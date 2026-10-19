# SPDX-License-Identifier: MIT
"""Cached stock-MEGAcmd metadata comparisons; never changes sync or file data."""

import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import subprocess
import threading
import time

from mega_process import run_cli

TTL = 24 * 60 * 60
FAILURE_TTL = 15
MAX_OUTPUT = 1024 * 1024
MAX_ENTRIES = 5000
HANDLE = re.compile(r"H:[A-Za-z0-9_-]{8}")
ROW = re.compile(r"([d-][e-][pt-][si-])\s+(?:\d+|>999|-)\s+(\d+|-)\s+(-?\d+)\s+(H:[A-Za-z0-9_-]{8}) (.+)")
MATCHED, DIFFERENT, MISSING, UNKNOWN, FOLDER = range(6, 11)


def valid_path(path):
    return (isinstance(path, str) and os.path.isabs(path)
            and len(path.encode("utf-8", errors="surrogatepass")) <= 16384
            and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path))


def listing(long_text, short_text):
    """Cross-check two renderings to reject multiline/ambiguous CLI filenames.

    Plain names+handles must exactly reconstruct the independent short listing.
    Duplicates or a changing directory are unknown, never a partial snapshot.
    """
    # Handle-addressed listings include a path heading in both renderings.
    # Accept only the exact same single-line heading; never guess past content.
    first, separator, rest = long_text.partition("\n")
    if first.endswith(": ") and separator:
        if not short_text.startswith(first + "\n"):
            raise ValueError("inconsistent-heading")
        long_text = rest
        short_text = short_text[len(first) + 1:]
    lines = long_text.split("\n")
    if not lines or lines[0].split() != ["FLAGS", "VERS", "SIZE", "DATE", "HANDLE", "NAME"]:
        raise ValueError("invalid-header")
    if lines[-1] != "" or len(lines) > MAX_ENTRIES + 2:
        raise ValueError("invalid-length")
    entries, expected, handles = {}, [], set()
    for line in lines[1:-1]:
        match = ROW.fullmatch(line)
        if not match:
            raise ValueError("invalid-row")
        flags, size, modified, handle, name = match.groups()
        if (name in entries or handle in handles or name in (".", "..") or "/" in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError("ambiguous-name")
        is_dir = flags[0] == "d"
        if (size == "-") != is_dir:
            raise ValueError("invalid-size")
        entries[name] = {"kind": "dir" if is_dir else "file", "size": None if is_dir else int(size),
                         "mtime": int(modified), "handle": handle}
        handles.add(handle)
        expected.append(f"{name} <{handle}>\n")
    if short_text != "".join(expected):
        raise ValueError("inconsistent-snapshot")
    return entries


class Cache:
    def __init__(self, database=None, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.persistent = False
        try:
            if database is None:
                base = os.environ.get("XDG_CACHE_HOME", "")
                base = Path(base) if os.path.isabs(base) else Path.home() / ".cache"
                directory = base / "strata-mega"
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                info = directory.lstat()
                if directory.is_symlink() or info.st_uid != os.getuid():
                    raise OSError("untrusted-cache-directory")
                directory.chmod(0o700)
                database = directory / "metadata.sqlite3"
                if database.is_symlink():
                    raise OSError("symlinked-cache")
            self.db = self.open(database)
            self.persistent = str(database) != ":memory:"
            if self.persistent:
                Path(database).chmod(0o600)
        except (OSError, sqlite3.Error):
            self.db = self.open(":memory:")

    @staticmethod
    def open(database):
        db = sqlite3.connect(str(database), timeout=0.2, check_same_thread=False)
        try:
            db.execute("PRAGMA max_page_count=32768")
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (handle TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT)")
            db.commit()
            return db
        except sqlite3.Error:
            db.close()
            raise

    def get(self, handle):
        with self.lock:
            try:
                row = self.db.execute("SELECT fetched,payload FROM snapshots WHERE handle=?", (handle,)).fetchone()
                if row is None:
                    return False, None
                fetched, payload = row
                age = self.clock() - fetched
                if age < 0 or age >= (TTL if payload is not None else FAILURE_TTL):
                    return False, None
                entries = None if payload is None else json.loads(payload)
                if entries is not None and (not isinstance(entries, dict) or len(entries) > MAX_ENTRIES
                        or any(not isinstance(name, str) or not isinstance(item, dict)
                               or item.get("kind") not in ("file", "dir")
                               or not isinstance(item.get("handle"), str)
                               or not HANDLE.fullmatch(item["handle"])
                               or type(item.get("mtime")) is not int
                               or (item["kind"] == "file" and (type(item.get("size")) is not int or item["size"] < 0))
                               for name, item in entries.items())):
                    return False, None
                return True, entries
            except (sqlite3.Error, ValueError):
                return False, None

    def put(self, handle, entries):
        data = None if entries is None else json.dumps(entries, ensure_ascii=True)
        if data is not None and len(data) > MAX_OUTPUT:
            data = None
        with self.lock:
            try:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (handle, self.clock(), data))
                    self.db.execute("DELETE FROM snapshots WHERE handle IN (SELECT handle FROM snapshots ORDER BY fetched DESC LIMIT -1 OFFSET ?)", (getattr(self, "max_snapshots", 128),))
                    while self.db.execute("SELECT coalesce(sum(length(payload)),0) FROM snapshots").fetchone()[0] > getattr(self, "max_payload", 8 * MAX_OUTPUT):
                        self.db.execute("DELETE FROM snapshots WHERE handle=(SELECT handle FROM snapshots ORDER BY fetched LIMIT 1)")
            except sqlite3.Error:
                self.db.rollback()

    def close(self):
        with self.lock:
            self.db.close()


class MetadataClient:
    """One bounded background worker; queries never wait on directory listings."""
    separator = "\x1f"

    def __init__(self, runner=None, cache=None, clock=time.monotonic, background=True):
        self.runner, self.cache, self.clock = runner if runner is not None else run_cli, cache if cache is not None else Cache(), clock
        self.roots = []
        self.roots_at = None
        self.tasks = queue.Queue(maxsize=32)
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        if background:
            self.thread = threading.Thread(target=self.work, daemon=True)
            self.thread.start()

    def command(self, args):
        result = self.runner(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=2,
                             env={**os.environ, "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"})
        if result.returncode or len(result.stdout) > MAX_OUTPUT:
            raise ValueError("cli-unavailable")
        return result.stdout.decode("utf-8")

    def sync_roots(self):
        now = self.clock()
        if self.roots_at is not None and 0 <= now - self.roots_at < getattr(self, "root_interval", 5):
            return self.roots
        self.roots_at, self.roots = now, []
        try:
            text = self.command(["/usr/bin/mega-sync", "--show-handles",
                                 "--output-cols=LOCALPATH,REMOTEHANDLE,RUN_STATE,STATUS,ERROR",
                                 "--col-separator=" + self.separator, "--path-display-size=16384"])
            lines = text.split("\n")
            if [x.strip() for x in lines[0].split(self.separator)] != ["LOCALPATH", "REMOTEHANDLE", "RUN_STATE", "STATUS", "ERROR"]:
                return []
            roots = []
            for line in lines[1:]:
                if not line:
                    continue
                fields = line.split(self.separator)
                # MEGAcmd sometimes appends human-readable notices. Never parse
                # those as folders or retain a partially parsed sync table.
                if len(fields) != 5:
                    return []
                path, handle, running, status, error = fields
                if not valid_path(path) or not re.fullmatch(r"<H:[A-Za-z0-9_-]{8}>", handle.strip()):
                    return []
                path = os.path.realpath(path)
                if error.strip() != "NO":
                    state = 5
                elif running.strip() in ("Suspended", "Disabled"):
                    state = 4
                elif running.strip() in ("Loading", "Pending"):
                    state = 1
                elif running.strip() == "Running":
                    state = {"Synced": 0, "Pending": 1, "Syncing": 2}.get(status.strip(), UNKNOWN)
                else:
                    state = UNKNOWN
                roots.append((path, handle.strip()[1:-1], state))
            self.roots = sorted(roots, key=lambda item: len(item[0]), reverse=True)
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            pass
        return self.roots

    def enqueue(self, task):
        with self.pending_lock:
            if task in self.pending or self.stop.is_set():
                return
            try:
                self.tasks.put_nowait(task)
            except queue.Full:
                return
            self.pending.add(task)

    def fetch(self, handle):
        if not HANDLE.fullmatch(handle):
            return None
        try:
            long_text = self.command(["/usr/bin/mega-ls", "-l", "--show-handles", "--time-format=%s", handle])
            short_text = self.command(["/usr/bin/mega-ls", "--show-handles", handle])
            entries = listing(long_text, short_text)
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            entries = None
        self.cache.put(handle, entries)
        return entries

    def populate(self, task):
        handle, parents = task
        deadline = time.monotonic() + 6
        for component in (*parents, None):
            if self.stop.is_set() or time.monotonic() >= deadline:
                return
            fresh, entries = self.cache.get(handle)
            if not fresh:
                entries = self.fetch(handle)
            if entries is None or component is None:
                return
            child = entries.get(component)
            if child is None or child.get("kind") != "dir":
                return
            handle = child["handle"]

    def work(self):
        while not self.stop.is_set():
            try:
                task = self.tasks.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.populate(task)
            finally:
                with self.pending_lock:
                    self.pending.discard(task)
                self.tasks.task_done()

    def lookup(self, root_handle, parts):
        handle = root_handle
        for index, component in enumerate(parts):
            fresh, entries = self.cache.get(handle)
            if not fresh:
                self.enqueue((root_handle, parts[:-1]))
                return UNKNOWN, None
            if entries is None:
                return UNKNOWN, None
            child = entries.get(component)
            if child is None:
                return MISSING, None
            if index < len(parts) - 1:
                if child.get("kind") != "dir":
                    return DIFFERENT, None
                handle = child["handle"]
            else:
                return None, child
        return UNKNOWN, None

    def folder_state(self, path, handle, budget, depth=0):
        if depth > 32 or budget[0] <= 0 or time.monotonic() >= budget[1]:
            return UNKNOWN
        fresh, entries = self.cache.get(handle)
        if not fresh:
            self.enqueue((handle, ()))
            return UNKNOWN
        if entries is None:
            return UNKNOWN
        children = {}
        try:
            with os.scandir(path) as scan:
                for child in scan:
                    budget[0] -= 1
                    if budget[0] < 0 or time.monotonic() >= budget[1]:
                        return UNKNOWN
                    children[child.name] = child
            if set(children) != set(entries):
                return DIFFERENT
            result = MATCHED
            for name, child in children.items():
                if time.monotonic() >= budget[1]:
                    return UNKNOWN
                if not valid_path(child.path) or child.is_symlink():
                    result = UNKNOWN
                    continue
                local = child.stat(follow_symlinks=False)
                remote = entries[name]
                if stat.S_ISDIR(local.st_mode) and remote["kind"] == "dir":
                    state = self.folder_state(child.path, remote["handle"], budget, depth + 1)
                elif stat.S_ISREG(local.st_mode) and remote["kind"] == "file":
                    state = MATCHED if (local.st_size == remote["size"] and local.st_mtime_ns // 1_000_000_000 == remote["mtime"]) else DIFFERENT
                elif stat.S_ISREG(local.st_mode) or stat.S_ISDIR(local.st_mode):
                    state = DIFFERENT
                else:
                    state = UNKNOWN
                if state == DIFFERENT:
                    return DIFFERENT
                if state != MATCHED:
                    result = UNKNOWN
            return result
        except OSError:
            return UNKNOWN

    def query(self, paths):
        roots, answer = self.sync_roots(), {}
        # Bound recursive filesystem work across the entire visible query batch.
        budget = [10000, time.monotonic() + 0.1]
        for path in paths:
            if not valid_path(path):
                continue
            canonical = os.path.realpath(path)
            for root, handle, state in roots:
                if not Path(canonical).is_relative_to(root):
                    continue
                # Symlinks (including directory aliases) are not metadata copies.
                if canonical != os.path.abspath(path):
                    answer[path] = UNKNOWN
                    break
                if canonical == root:
                    answer[path] = state
                    break
                parts = Path(canonical).relative_to(root).parts
                if len(parts) > 32:
                    answer[path] = UNKNOWN
                    break
                try:
                    local = os.lstat(path)
                except OSError:
                    answer[path] = UNKNOWN
                    break
                if not (stat.S_ISREG(local.st_mode) or stat.S_ISDIR(local.st_mode)):
                    answer[path] = UNKNOWN
                    break
                result, remote = self.lookup(handle, parts)
                if remote is not None:
                    if stat.S_ISDIR(local.st_mode):
                        result = self.folder_state(path, remote["handle"], budget) if remote["kind"] == "dir" else DIFFERENT
                    elif remote["kind"] != "file":
                        result = DIFFERENT
                    else:
                        result = MATCHED if (local.st_size == remote["size"] and local.st_mtime_ns // 1_000_000_000 == remote["mtime"]) else DIFFERENT
                answer[path] = result
                break
        return answer

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=4.5)
        if self.thread is None or not self.thread.is_alive():
            self.cache.close()
