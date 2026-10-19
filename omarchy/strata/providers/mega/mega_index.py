# SPDX-License-Identifier: MIT
"""Persistent status index with filesystem-driven dirty tracking."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import threading
import time

from mega_metadata import Cache, MetadataClient, MATCHED, DIFFERENT, MISSING, UNKNOWN, valid_path
from mega_watch import Watcher

DIRTY = 11
AUDIT_SECONDS = 6 * 60 * 60
RESULT_SECONDS = 24 * 60 * 60
RETRY_SECONDS = (2, 8, 30, 120)


def fingerprint(info):
    return json.dumps((info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))


class Store:
    def __init__(self, cache):
        self.cache = cache
        with cache.lock, cache.db:
            cache.db.execute("CREATE TABLE IF NOT EXISTS statuses (scope TEXT,path TEXT,parent TEXT,kind TEXT,stamp TEXT,state INTEGER,dirty INTEGER,checked REAL,PRIMARY KEY(scope,path))")
            cache.db.execute("CREATE INDEX IF NOT EXISTS statuses_parent ON statuses(scope,parent)")
            cache.db.execute("CREATE TABLE IF NOT EXISTS directories (scope TEXT,path TEXT,handle TEXT,base INTEGER,PRIMARY KEY(scope,path))")
            cache.db.execute("CREATE TABLE IF NOT EXISTS audits (scope TEXT,root TEXT,checked REAL,PRIMARY KEY(scope,root))")

    def get(self, scope, path):
        with self.cache.lock:
            row = self.cache.db.execute("SELECT kind,stamp,state,dirty,checked FROM statuses WHERE scope=? AND path=?", (scope, str(path))).fetchone()
        if row is None or not 0 <= self.cache.clock() - row[4] < RESULT_SECONDS:
            return None
        return row

    def put(self, scope, path, kind, stamp, state):
        self.cache.db.execute("INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?,?,0,?)", (scope, str(path), str(Path(path).parent), kind, stamp, state, self.cache.clock()))

    def dirty(self, scope, root, paths):
        with self.cache.lock, self.cache.db:
            for path in paths:
                prefix = str(path) + os.sep
                self.cache.db.execute("UPDATE statuses SET dirty=1 WHERE scope=? AND (path=? OR substr(path,1,?)=?)", (scope, str(path), len(prefix), prefix))
                parent = Path(path).parent
                while parent.is_relative_to(root):
                    self.cache.db.execute("UPDATE statuses SET dirty=1 WHERE scope=? AND path=?", (scope, str(parent)))
                    if str(parent) == root:
                        break
                    parent = parent.parent

    def remove(self, scope, path):
        prefix = str(path) + os.sep
        self.cache.db.execute("DELETE FROM statuses WHERE scope=? AND (path=? OR substr(path,1,?)=?)", (scope, str(path), len(prefix), prefix))
        self.cache.db.execute("DELETE FROM directories WHERE scope=? AND (path=? OR substr(path,1,?)=?)", (scope, str(path), len(prefix), prefix))

    def aggregate(self, scope, path):
        base = self.cache.db.execute("SELECT base,handle FROM directories WHERE scope=? AND path=?", (scope, str(path))).fetchone()
        if base is None:
            return UNKNOWN
        base_state = base[0]
        if base[1]:
            snapshot = self.cache.db.execute("SELECT fetched,payload FROM snapshots WHERE handle=?", (base[1],)).fetchone()
            if snapshot is None or snapshot[1] is None or not 0 <= self.cache.clock() - snapshot[0] < RESULT_SECONDS:
                base_state = UNKNOWN
        children = self.cache.db.execute("SELECT state,dirty,checked FROM statuses WHERE scope=? AND parent=? AND path<>?", (scope, str(path), str(path))).fetchall()
        states = [DIRTY if dirty else (state if 0 <= self.cache.clock() - checked < RESULT_SECONDS else UNKNOWN)
                  for state, dirty, checked in children]
        if base_state == DIFFERENT or any(state in (DIFFERENT, MISSING) for state in states):
            return DIFFERENT
        if DIRTY in states:
            return DIRTY
        return MATCHED if base_state == MATCHED and all(state == MATCHED for state in states) else UNKNOWN

    def parents(self, scope, root, path):
        parent = Path(path).parent
        while parent.is_relative_to(root):
            row = self.cache.db.execute("SELECT stamp FROM statuses WHERE scope=? AND path=?", (scope, str(parent))).fetchone()
            if row:
                self.put(scope, parent, "dir", row[0], self.aggregate(scope, parent))
            if str(parent) == root:
                break
            parent = parent.parent


class IndexedClient(MetadataClient):
    def __init__(self, runner=None, cache=None, clock=time.monotonic, background=True, watch=True):
        super().__init__(runner, cache, clock, background=False)
        self.root_interval = 30
        self.cache.max_snapshots = 4096
        self.cache.max_payload = 32 * 1024 * 1024
        self.store = Store(self.cache)
        self.presentation_lock = threading.RLock()
        self.condition = threading.Condition()
        self.scheduled = {}
        self.active = False
        self.locations = {}
        self.registered = set()
        self.bootstrapped = set()
        self.completed = set()
        self.generation = 0
        self.sink = None
        self.watcher = Watcher(self.changed) if watch else None
        if background:
            self.thread = threading.Thread(target=self.index_work, daemon=True)
            self.thread.start()

    def set_event_sink(self, sink):
        self.sink = sink

    def notify(self, paths):
        if self.sink is None:
            return
        paths = sorted(set(map(str, paths)))
        event = {"version": 1, "event": "invalidate"}
        if len(paths) <= 200 and all(valid_path(path) for path in paths):
            event["paths"] = paths
        try:
            self.sink(event)
        except (OSError, BrokenPipeError):
            pass

    def schedule(self, root, scope, path, handle, recurse=False, force=False, delay=0, targets=(), attempt=0, audit=False):
        key = (scope, str(path), audit)
        task = dict(root=root, scope=scope, path=str(path), handle=handle, recurse=recurse, force=force,
                    due=self.clock() + delay, targets=set(targets), attempt=attempt, audit=audit)
        with self.condition:
            old = self.scheduled.get(key)
            if old:
                task["recurse"] |= old["recurse"]
                task["force"] |= old["force"]
                task["targets"].update(old["targets"])
                task["due"] = min(task["due"], old["due"])
            if len(self.scheduled) < 2048 or old:
                self.scheduled[key] = task
                self.condition.notify_all()

    def ensure_roots(self, roots):
        active = {(root, scope) for root, scope, state in roots}
        removed = self.registered - active
        if removed:
            self.generation += 1
            with self.condition:
                self.scheduled = {key: task for key, task in self.scheduled.items()
                                  if (task["root"], task["scope"]) in active}
            self.locations.clear()
            self.registered.clear()
            self.bootstrapped.clear()
            if self.watcher:
                self.watcher.reset()
        for root, scope, state in roots:
            key = (root, scope)
            if key in self.registered:
                continue
            self.registered.add(key)
            self.locations[root] = (root, scope, scope)
            if self.watcher:
                self.watcher.add(root)
            with self.cache.lock:
                audit = self.cache.db.execute("SELECT checked FROM audits WHERE scope=? AND root=?", (scope, root)).fetchone()
            delay = AUDIT_SECONDS if audit is None else max(0, min(AUDIT_SECONDS, audit[0] + AUDIT_SECONDS - self.cache.clock()))
            if delay == 0:
                self.schedule(root, scope, root, scope, recurse=True, force=True, audit=True)
            else:
                self.schedule(root, scope, root, scope, recurse=True)
                self.schedule(root, scope, root, scope, recurse=True, force=True, delay=delay, audit=True)

    def changed(self, paths):
        with self.presentation_lock:
            self.generation += 1
            if paths is None:
                if self.watcher:
                    self.watcher.reset()
                self.bootstrapped.clear()
                for root, scope in self.registered:
                    self.store.dirty(scope, root, [root])
                    self.schedule(root, scope, root, scope, recurse=True, force=True, delay=1)
                self.notify([root for root, scope in self.registered])
                return
            affected = set()
            for changed in paths:
                # SQLite/Strata require UTF-8 paths. An unsupported leaf still
                # invalidates its nearest supported containing directory.
                while not valid_path(changed) and str(Path(changed).parent) != changed:
                    changed = str(Path(changed).parent)
                for root, scope in self.registered:
                    if not Path(changed).is_relative_to(root):
                        continue
                    self.store.dirty(scope, root, [changed])
                    parent = Path(root) if changed == root else Path(changed).parent
                    while str(parent) not in self.locations and parent.is_relative_to(root):
                        parent = parent.parent
                    location = self.locations.get(str(parent), (root, scope, scope))
                    self.schedule(location[0], location[1], str(parent), location[2], force=True,
                                  delay=1, targets=[Path(changed).name])
                    affected.add(str(parent))
            self.notify(affected)

    def index_directory(self, root, scope, path, handle, recurse, force, depth=0):
        if self.stop.is_set() or depth > 32 or not valid_path(path) or not Path(os.path.abspath(path)).is_relative_to(root):
            return UNKNOWN, {}
        with self.presentation_lock:
            if (root, scope) not in self.registered:
                return UNKNOWN, {}
        self.locations[path] = (root, scope, handle)
        if self.watcher:
            self.watcher.add(path)
        with self.presentation_lock:
            generation = self.generation
        try:
            before = os.lstat(path)
            if not stat.S_ISDIR(before.st_mode) or os.path.realpath(path) != os.path.abspath(path):
                return UNKNOWN, {}
            with os.scandir(path) as scan:
                children = list(scan)
            if len(children) > 5000:
                return UNKNOWN, {}
            info = {child.name: child.stat(follow_symlinks=False) for child in children}
            # Catch changes made while the provider was stopped, before trusting
            # a long-lived cloud snapshot on this startup pass.
            changed_names = {child.name for child in children
                             if valid_path(child.path) and not stat.S_ISDIR(info[child.name].st_mode)
                             and (row := self.store.get(scope, child.path)) is not None
                             and row[1] != fingerprint(info[child.name])}
            previous_directory = self.store.get(scope, path)
            changed_while_stopped = bool(changed_names) or (previous_directory is not None and previous_directory[1] != fingerprint(before))
            fresh, entries = self.cache.get(handle) if handle else (True, {})
            if handle and (force or changed_while_stopped or not fresh):
                entries = self.fetch(handle)
            remote = entries if entries is not None else {}
            base = UNKNOWN if entries is None else (MATCHED if set(info) == set(remote) else DIFFERENT)
            rows, direct = [], {}
            for child in children:
                if self.stop.is_set():
                    return UNKNOWN, {}
                local, other = info[child.name], remote.get(child.name)
                kind = "dir" if stat.S_ISDIR(local.st_mode) else "file"
                if not valid_path(child.path):
                    direct[child.name] = UNKNOWN
                    base = UNKNOWN
                    continue
                if stat.S_ISLNK(local.st_mode):
                    state = UNKNOWN
                elif stat.S_ISDIR(local.st_mode):
                    child_handle = other["handle"] if other and other["kind"] == "dir" else ("?" if entries is None else None)
                    self.locations[child.path] = (root, scope, child_handle)
                    old = self.store.get(scope, child.path)
                    if recurse or old is None or old[3] or old[1] != fingerprint(local):
                        state, unused = self.index_directory(root, scope, child.path, child_handle, recurse, force, depth + 1)
                    else:
                        state = old[2]
                    if entries is None:
                        state = UNKNOWN
                    elif other is None:
                        state = MISSING
                    elif other["kind"] != "dir":
                        state = DIFFERENT
                elif not stat.S_ISREG(local.st_mode):
                    state = UNKNOWN
                elif entries is None:
                    state = UNKNOWN
                elif other is None:
                    state = MISSING
                elif other["kind"] != "file":
                    state = DIFFERENT
                else:
                    state = MATCHED if (local.st_size == other["size"] and local.st_mtime_ns // 1_000_000_000 == other["mtime"]) else DIFFERENT
                rows.append((child.path, kind, fingerprint(local), state))
                direct[child.name] = state
            after = os.lstat(path)
            with self.presentation_lock:
                if (root, scope) not in self.registered:
                    return UNKNOWN, direct
                if generation != self.generation or fingerprint(before) != fingerprint(after):
                    self.schedule(root, scope, path, handle, recurse=True, force=True, delay=1)
                    return DIRTY, direct
                with self.cache.lock, self.cache.db:
                    previous = self.cache.db.execute("SELECT path FROM statuses WHERE scope=? AND parent=? AND path<>?", (scope, path, path)).fetchall()
                    for (old_path,) in previous:
                        if Path(old_path).name not in info:
                            self.store.remove(scope, old_path)
                    for child_path, kind, stamp, state in rows:
                        self.store.put(scope, child_path, kind, stamp, state)
                    self.cache.db.execute("INSERT OR REPLACE INTO directories VALUES (?,?,?,?)", (scope, path, handle, base))
                    state = self.store.aggregate(scope, path)
                    self.store.put(scope, path, "dir", fingerprint(after), state)
                    self.store.parents(scope, root, path)
                    self.cache.db.execute("DELETE FROM statuses WHERE rowid IN (SELECT rowid FROM statuses ORDER BY checked DESC LIMIT -1 OFFSET 100000)")
                self.completed.add(path)
            retry_names = {name for name in changed_names if direct.get(name) != MATCHED}
            if retry_names:
                self.schedule(root, scope, path, handle, force=True, delay=2, targets=retry_names)
            # Deleted targets need a retry only while their cloud entries remain.
            for name in set(remote) - set(info):
                direct[name] = DIFFERENT
            return state, direct
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return UNKNOWN, {}

    def run_task(self, task):
        with self.presentation_lock:
            if (task["root"], task["scope"]) not in self.registered:
                return
        self.completed.clear()
        state, direct = self.index_directory(task["root"], task["scope"], task["path"], task["handle"], task["recurse"], task["force"])
        with self.presentation_lock:
            if task["path"] == task["root"] and task["root"] in self.completed:
                self.bootstrapped.add((task["root"], task["scope"]))
                with self.cache.lock, self.cache.db:
                    if task["audit"]:
                        self.cache.db.execute("INSERT OR REPLACE INTO audits VALUES (?,?,?)", (task["scope"], task["root"], self.cache.clock()))
                    else:
                        self.cache.db.execute("INSERT OR IGNORE INTO audits VALUES (?,?,?)", (task["scope"], task["root"], self.cache.clock()))
            self.notify([task["path"]])
        if (task["root"], task["scope"]) not in self.registered:
            return
        if task["audit"]:
            self.schedule(task["root"], task["scope"], task["root"], task["scope"], recurse=True, force=True, delay=AUDIT_SECONDS, audit=True)
        elif task["attempt"] < len(RETRY_SECONDS) and any(direct.get(name, MATCHED) != MATCHED for name in task["targets"]):
            self.schedule(task["root"], task["scope"], task["path"], task["handle"], force=True,
                          delay=RETRY_SECONDS[task["attempt"]], targets=task["targets"], attempt=task["attempt"] + 1)

    def index_work(self):
        while not self.stop.is_set():
            with self.condition:
                if not self.scheduled:
                    self.condition.wait(timeout=0.2)
                    continue
                key, task = min(self.scheduled.items(), key=lambda item: item[1]["due"])
                wait = task["due"] - self.clock()
                if wait > 0:
                    self.condition.wait(timeout=min(wait, 1))
                    continue
                self.scheduled.pop(key)
                self.active = True
            try:
                self.run_task(task)
            finally:
                self.active = False

    def query(self, paths):
        with self.presentation_lock:
            return self.indexed_query(paths)

    def indexed_query(self, paths):
        roots, answer = self.sync_roots(), {}
        self.ensure_roots(roots)
        for path in paths:
            if not valid_path(path):
                continue
            canonical = os.path.realpath(path)
            for root, scope, state in roots:
                if not Path(canonical).is_relative_to(root):
                    continue
                row = self.store.get(scope, path)
                if path == root:
                    answer[path] = DIRTY if row and row[3] else state
                    break
                if canonical != os.path.abspath(path):
                    answer[path] = UNKNOWN
                    break
                try:
                    local = os.lstat(path)
                except OSError:
                    answer[path] = UNKNOWN
                    break
                if row and row[3]:
                    answer[path] = DIRTY
                elif row and row[1] == fingerprint(local):
                    if stat.S_ISDIR(local.st_mode) and (root, scope) not in self.bootstrapped:
                        answer[path] = UNKNOWN
                    elif stat.S_ISDIR(local.st_mode) and (self.watcher is None or not self.watcher.healthy):
                        answer[path] = UNKNOWN
                        self.schedule(root, scope, path, self.locations.get(path, (root, scope, None))[2], recurse=True)
                    else:
                        answer[path] = row[2]
                else:
                    answer[path] = DIRTY if row else UNKNOWN
                    self.changed({path})
                break
        return answer

    def close(self):
        self.stop.set()
        if self.watcher:
            self.watcher.close()
        with self.condition:
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=4.5)
        if self.thread is None or not self.thread.is_alive():
            self.cache.close()
