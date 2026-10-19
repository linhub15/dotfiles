import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_metadata import Runner, entry
import mega_metadata as metadata
from mega_index import IndexedClient, DIRTY, AUDIT_SECONDS
from mega_watch import Watcher, EVENT, OVERFLOW
import provider


class FakeWatcher:
    healthy = True
    def add(self, path):
        return True
    def reset(self):
        pass
    def close(self):
        pass


def pump(client):
    with client.condition:
        due = [(key, task) for key, task in client.scheduled.items() if task["due"] <= client.clock()]
        if not due:
            return False
        key, task = min(due, key=lambda item: item[1]["due"])
        client.scheduled.pop(key)
    client.run_task(task)
    return True


def setup(directory, database=":memory:", now=None, watch=False, background=False):
    root = Path(directory) / "notes"
    folder = root / "folder"
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / "note"
    if not note.exists():
        note.write_bytes(b"test"); os.utime(note, (100, 100))
    records = {"H:ROOT0001": {"folder": entry("H:DIR00001", kind="dir")},
               "H:DIR00001": {"note": entry()}}
    runner = Runner(root, records)
    cache = metadata.Cache(database)
    client = IndexedClient(runner, cache, clock=(lambda: now[0]) if now else time.monotonic,
                           background=background, watch=watch)
    if not watch:
        client.watcher = FakeWatcher()
    return root, folder, note, runner, client


class IndexTests(unittest.TestCase):
    def test_bootstrap_indexes_all_descendants_and_browsing_uses_no_listings_or_scans(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            root, folder, note, runner, client = setup(directory, now=now)
            try:
                client.query([str(root)])
                self.assertTrue(pump(client))
                self.assertIn((str(root), "H:ROOT0001"), client.bootstrapped)
                self.assertEqual(client.cache.db.execute("SELECT count(*) FROM statuses").fetchone()[0], 3)
                calls = len(runner.calls)
                with patch.object(os, "scandir", side_effect=AssertionError("cached folder must not be rescanned")):
                    for i in range(30):
                        states = client.query([str(folder), str(note)])
                        self.assertEqual(set(states.values()), {metadata.MATCHED})
                self.assertEqual(len(runner.calls), calls)
                now[0] += 31
                client.query([str(folder), str(note)])
                self.assertEqual(len(runner.calls), calls + 1)
                self.assertTrue(runner.calls[-1][0].endswith("mega-sync"))
            finally:
                client.close()

    def test_edit_dirties_ancestors_then_refreshes_only_parent_and_catches_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            root, folder, note, runner, client = setup(directory, now=now)
            events = []
            client.set_event_sink(events.append)
            try:
                client.query([str(root)]); pump(client)
                before = len(runner.calls)
                note.write_bytes(b"longer edit")
                client.changed({str(note)})
                client.changed({str(note)})
                self.assertEqual(client.query([str(note)])[str(note)], DIRTY)
                self.assertEqual(client.query([str(folder)])[str(folder)], DIRTY)
                self.assertEqual(client.query([str(root)])[str(root)], DIRTY)
                ordinary = [t for t in client.scheduled.values() if not t["audit"]]
                self.assertEqual(len(ordinary), 1)
                self.assertTrue(any(e.get("paths") == [str(folder)] for e in events))
                now[0] += 2; pump(client)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.DIFFERENT)
                self.assertEqual(len(runner.calls) - before, 2)
                self.assertTrue(all(args[-1] == "H:DIR00001" for args in runner.calls[before:]))
                local = note.stat()
                runner.entries["H:DIR00001"]["note"] = entry(size=local.st_size, modified=local.st_mtime_ns // 1_000_000_000)
                now[0] += 3; pump(client)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.MATCHED)
            finally:
                client.close()

    def test_restart_reuses_cloud_cache_and_detects_edits_made_while_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite3"
            root, folder, note, runner, client = setup(directory, database)
            client.query([str(root)]); pump(client); client.close()
            root, folder, note, runner, client = setup(directory, database)
            try:
                client.query([str(root)]); pump(client)
                self.assertFalse(any(args[0].endswith("mega-ls") for args in runner.calls))
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
            finally:
                client.close()
            note.write_bytes(b"edit while closed")
            root, folder, note, runner, client = setup(directory, database)
            try:
                client.query([str(root)]); pump(client)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.DIFFERENT)
                listings = [args for args in runner.calls if args[0].endswith("mega-ls")]
                self.assertEqual(len(listings), 2)
                self.assertTrue(all(args[-1] == "H:DIR00001" for args in listings))
                self.assertTrue(any(t["targets"] == {"note"} for t in client.scheduled.values() if not t["audit"]))
            finally:
                client.close()

    def test_offline_creates_and_deletes_refresh_the_changed_directory(self):
        for operation in ("create", "delete"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "cache.sqlite3"
                root, folder, note, runner, client = setup(directory, database)
                client.query([str(root)]); pump(client); client.close()
                if operation == "create":
                    new = folder / "new"
                    new.write_bytes(b"test"); os.utime(new, (100, 100))
                else:
                    note.unlink()
                # Do not call setup again: it intentionally creates a missing fixture file.
                records = {"H:ROOT0001": {"folder": entry("H:DIR00001", kind="dir")},
                           "H:DIR00001": {"note": entry(), "new": entry("H:FILE0002")} if operation == "create" else {}}
                runner = Runner(root, records)
                client = IndexedClient(runner, metadata.Cache(database), background=False, watch=False)
                client.watcher = FakeWatcher()
                try:
                    client.query([str(root)]); pump(client)
                    self.assertEqual(client.query([str(folder)])[str(folder)], metadata.MATCHED)
                    if operation == "create":
                        self.assertEqual(client.query([str(new)])[str(new)], metadata.MATCHED)
                    else:
                        self.assertIsNone(client.store.get("H:ROOT0001", str(note)))
                    listings = [args for args in runner.calls if args[0].endswith("mega-ls")]
                    self.assertEqual(len(listings), 2)
                    self.assertTrue(all(args[-1] == "H:DIR00001" for args in listings))
                finally:
                    client.close()

    def test_expired_children_and_cloud_snapshots_cannot_refresh_parent_green(self):
        with tempfile.TemporaryDirectory() as directory:
            root, folder, note, runner, client = setup(directory)
            try:
                client.query([str(root)]); pump(client)
                checked = client.cache.clock()
                client.cache.clock = lambda: checked + 24 * 60 * 60 + 1
                with client.cache.lock, client.cache.db:
                    client.cache.db.execute("UPDATE snapshots SET fetched=?", (client.cache.clock(),))
                    self.assertEqual(client.store.aggregate("H:ROOT0001", str(folder)), metadata.UNKNOWN)
                    client.cache.db.execute("UPDATE statuses SET checked=?", (client.cache.clock(),))
                    client.cache.db.execute("UPDATE snapshots SET fetched=?", (checked,))
                    self.assertEqual(client.store.aggregate("H:ROOT0001", str(folder)), metadata.UNKNOWN)
            finally:
                client.close()

    def test_root_events_and_indexer_never_escape_the_configured_root(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            root, folder, note, runner, client = setup(directory, now=now)
            try:
                client.query([str(root)]); pump(client)
                client.changed({str(root)})
                ordinary = [t for t in client.scheduled.values() if not t["audit"]]
                self.assertEqual([t["path"] for t in ordinary], [str(root)])
                with patch.object(os, "scandir", side_effect=AssertionError("outside root must not be scanned")):
                    for outside in (str(root.parent), str(root / "..")):
                        state, direct = client.index_directory(str(root), "H:ROOT0001", outside, "H:ROOT0001", True, True)
                        self.assertEqual(state, metadata.UNKNOWN)
            finally:
                client.close()

    def test_non_utf8_leaf_does_not_break_watcher_or_valid_file_indexing(self):
        with tempfile.TemporaryDirectory() as directory:
            root, folder, note, runner, client = setup(directory)
            bad_bytes = os.fsencode(folder) + b"/bad-\xff"
            fd = os.open(bad_bytes, os.O_CREAT | os.O_WRONLY, 0o600); os.close(fd)
            try:
                client.query([str(root)]); pump(client)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.UNKNOWN)
                client.changed({os.fsdecode(bad_bytes)})
                self.assertTrue(all(Path(t["path"]).is_relative_to(root) for t in client.scheduled.values()))
                self.assertTrue(client.watcher.healthy)
            finally:
                client.close()

    def test_removed_sync_cancels_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root, folder, note, runner, client = setup(directory)
            try:
                client.query([str(root)])
                task = next(t for t in client.scheduled.values() if not t["audit"])
                client.ensure_roots([])
                before = len(runner.calls)
                client.run_task(task)
                self.assertEqual(len(runner.calls), before)
                self.assertFalse(client.scheduled)
                self.assertFalse(client.registered)
            finally:
                client.close()

    def test_audit_deadline_survives_provider_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite3"
            root, folder, note, runner, client = setup(directory, database)
            client.query([str(root)]); pump(client)
            with client.cache.lock, client.cache.db:
                client.cache.db.execute("UPDATE audits SET checked=checked-?", (AUDIT_SECONDS + 1,))
            client.close()
            root, folder, note, runner, client = setup(directory, database)
            try:
                client.query([str(root)]); pump(client)
                self.assertEqual(len([args for args in runner.calls if args[0].endswith("mega-ls")]), 4)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
            finally:
                client.close()

    def test_rename_deletion_and_periodic_cloud_only_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            root, folder, note, runner, client = setup(directory, now=now)
            try:
                client.query([str(root)]); pump(client)
                renamed = folder / "renamed"
                note.rename(renamed)
                client.changed({str(note), str(renamed)})
                now[0] += 2; pump(client)
                self.assertEqual(client.query([str(renamed)])[str(renamed)], metadata.MISSING)
                self.assertIsNone(client.store.get("H:ROOT0001", str(note)))
                runner.entries["H:DIR00001"] = {"renamed": entry()}
                now[0] += 3; pump(client)
                self.assertEqual(client.query([str(renamed)])[str(renamed)], metadata.MATCHED)
                # No local event for a cloud-only addition: the scheduled audit catches it.
                runner.entries["H:DIR00001"]["cloud-only"] = entry("H:FILE0002")
                now[0] = 1000 + AUDIT_SECONDS + 1
                for i in range(5):
                    if not pump(client):
                        break
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.DIFFERENT)
            finally:
                client.close()

    def test_lost_watch_history_invalidates_all_and_requires_fresh_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000]
            root, folder, note, runner, client = setup(directory, now=now)
            try:
                client.query([str(root)]); pump(client)
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.MATCHED)
                client.changed(None)
                self.assertEqual(client.query([str(folder)])[str(folder)], DIRTY)
                self.assertFalse(client.bootstrapped)
                now[0] += 2; pump(client)
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.MATCHED)
            finally:
                client.close()

    def test_failed_cloud_lookup_does_not_turn_unchecked_descendants_into_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root, folder, note, runner, client = setup(directory)
            try:
                runner.entries = {}
                client.query([str(root)]); pump(client)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.UNKNOWN)
            finally:
                client.close()

    def test_real_inotify_detects_write_and_new_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root, folder, note, runner, client = setup(directory, watch=True, background=True)
            try:
                self.assertTrue(client.watcher.healthy)
                client.query([str(root)])
                deadline = time.monotonic() + 3
                while (str(root), "H:ROOT0001") not in client.bootstrapped and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                note.write_bytes(b"real event")
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline:
                    row = client.store.get("H:ROOT0001", str(note))
                    if row and row[3]:
                        break
                    time.sleep(0.01)
                self.assertTrue(row[3], "inotify must mark the record dirty without a provider query")
                self.assertEqual(client.query([str(note)])[str(note)], DIRTY)
                created = folder / "created"; created.mkdir()
                deadline = time.monotonic() + 4
                while str(created) not in client.watcher.descriptors and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIn(str(created), client.watcher.descriptors)
            finally:
                client.close()

    def test_directory_deletion_is_scoped_instead_of_resetting_all_watches(self):
        with tempfile.TemporaryDirectory() as directory:
            watcher = Watcher(lambda paths: None)
            try:
                self.assertTrue(watcher.add(directory))
                wd = watcher.descriptors[directory]
                self.assertEqual(watcher.decode(EVENT.pack(wd, 0x400, 0, 0)), {directory})
                self.assertEqual(watcher.decode(EVENT.pack(wd, 0x8000, 0, 0)), set())
            finally:
                watcher.close()

    def test_overflow_frame_is_treated_as_lost_history(self):
        watcher = Watcher(lambda paths: None)
        try:
            self.assertIsNone(watcher.decode(EVENT.pack(-1, OVERFLOW, 0, 0)))
        finally:
            watcher.close()

    def test_protocol_serializes_invalidation_and_reply_frames(self):
        class Client:
            presentation_lock = threading.RLock()
            def set_event_sink(self, sink):
                self.sink = sink
            def query(self, paths):
                self.sink({"version": 1, "event": "invalidate", "paths": paths})
                return {paths[0]: DIRTY}
        request = {"version": 1, "id": 3, "method": "query", "paths": ["/tmp/note"]}
        output = io.StringIO()
        provider.serve(io.BytesIO(json.dumps(request).encode() + b"\n"), output, Client())
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(frames[0]["event"], "invalidate")
        self.assertEqual(frames[1]["id"], 3)
        self.assertEqual(frames[1]["decorations"][0]["description"], "MEGA: Local changes")


if __name__ == "__main__":
    unittest.main()
