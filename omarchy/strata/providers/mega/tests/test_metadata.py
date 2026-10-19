import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mega_metadata as metadata
import provider

HEADER = "FLAGS VERS SIZE DATE HANDLE NAME\n"


def render(entries):
    long, short = HEADER, ""
    for name, item in entries.items():
        folder = item["kind"] == "dir"
        size = "-" if folder else str(item["size"])
        long += f"{'d--- -' if folder else '---- 1'} {size} {item['mtime']} {item['handle']} {name}\n"
        short += f"{name} <{item['handle']}>\n"
    return long, short


def entry(handle="H:FILE0001", size=4, modified=100, kind="file"):
    return {"kind": kind, "size": None if kind == "dir" else size, "mtime": modified, "handle": handle}


class Runner:
    def __init__(self, root, entries):
        self.root, self.entries = root, entries
        self.calls = []
        self.root_handle = "H:ROOT0001"
        self.unavailable = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.unavailable:
            return SimpleNamespace(returncode=57, stdout=b"")
        if args[0] == "/usr/bin/mega-sync":
            sep = metadata.MetadataClient.separator
            text = sep.join(("LOCALPATH", "REMOTEHANDLE", "RUN_STATE", "STATUS", "ERROR")) + "\n"
            text += sep.join((str(self.root), f"<{self.root_handle}>", "Running", "Synced", "NO")) + "\n"
        else:
            records = self.entries.get(args[-1])
            if records is None:
                return SimpleNamespace(returncode=1, stdout=b"")
            text = render(records)[0 if "-l" in args else 1]
        return SimpleNamespace(returncode=0, stdout=text.encode())


class MetadataTests(unittest.TestCase):
    def test_listing_preserves_spaces_unicode_quotes_and_literal_shell_characters(self):
        entries = {" leading café '$()' trailing ": entry(), "folder": entry("H:DIR00001", kind="dir")}
        self.assertEqual(metadata.listing(*render(entries)), entries)
        self.assertEqual(metadata.listing(HEADER, ""), {})
        long, short = render(entries)
        self.assertEqual(metadata.listing("/notes: \n" + long, "/notes: \n" + short), entries)
        with self.assertRaises(ValueError):
            metadata.listing("/notes: \n" + long, "/different: \n" + short)

    def test_listing_rejects_injected_lines_duplicates_partial_or_racing_snapshots(self):
        good = "---- 1 4 100 H:FILE0001 note\n"
        cases = [(HEADER + good, "different <H:FILE0001>\n"),
                 (HEADER + good + good, "note <H:FILE0001>\n" * 2),
                 (HEADER + good[:-1], "note <H:FILE0001>\n"),
                 (HEADER + "broken\n", ""),
                 (HEADER + good + "---- 1 4 100 H:FILE0002 forged\n",
                  "note\n---- 1 4 100 H:FILE0002 forged <H:FILE0001>\n"),
                 (HEADER + "---- 1 4 100 H:FILE0001 ../escape\n", "../escape <H:FILE0001>\n")]
        for long, short in cases:
            with self.subTest(long=long):
                with self.assertRaises(ValueError):
                    metadata.listing(long, short)

    def test_sqlite_survives_restart_expires_and_rejects_backwards_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite3"
            now = [1000]
            cache = metadata.Cache(database, clock=lambda: now[0])
            records = {"note": entry()}
            cache.put("H:ROOT0001", records)
            cache.close()
            cache = metadata.Cache(database, clock=lambda: now[0])
            self.assertEqual(cache.get("H:ROOT0001"), (True, records))
            self.assertFalse(database.stat().st_mode & 0o077)
            now[0] += metadata.TTL
            self.assertEqual(cache.get("H:ROOT0001"), (False, None))
            now[0] = 999
            self.assertEqual(cache.get("H:ROOT0001"), (False, None))
            cache.close()

    def test_failure_backoff_cache_bounds_and_corruption(self):
        now = [1000]
        cache = metadata.Cache(":memory:", clock=lambda: now[0])
        try:
            cache.put("H:ROOT0001", None)
            self.assertEqual(cache.get("H:ROOT0001"), (True, None))
            now[0] += metadata.FAILURE_TTL
            self.assertEqual(cache.get("H:ROOT0001"), (False, None))
            for i in range(140):
                cache.put(str(i), {})
            self.assertLessEqual(cache.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 128)
            cache.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", ("bad", now[0], '[1,2]'))
            cache.db.commit()
            self.assertEqual(cache.get("bad"), (False, None))
        finally:
            cache.close()

    def test_default_cache_location_permissions_and_no_cache_when_unwritable(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CACHE_HOME": directory}):
                cache = metadata.Cache()
                self.assertTrue(cache.persistent)
                cache.close()
                root = Path(directory) / "strata-mega"
                self.assertFalse(root.stat().st_mode & 0o077)
                self.assertTrue((root / "metadata.sqlite3").is_file())
            with patch.dict(os.environ, {"XDG_CACHE_HOME": "/proc/strata-test-unwritable"}):
                cache = metadata.Cache()
                self.assertFalse(cache.persistent)
                cache.put("H:ROOT0001", {})
                self.assertEqual(cache.get("H:ROOT0001"), (True, {}))
                cache.close()

    def test_files_compare_stat_only_and_unchecked_folders_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note, changed, missing, folder = [root / name for name in ("note", "changed", "missing", "folder")]
            for p in (note, changed, missing):
                p.write_bytes(b"test")
                os.utime(p, (100, 100))
            folder.mkdir()
            records = {"note": entry(), "changed": entry("H:FILE0002", size=3),
                       "folder": entry("H:DIR00001", kind="dir")}
            runner = Runner(root, {"H:ROOT0001": records})
            cache = metadata.Cache(":memory:")
            client = metadata.MetadataClient(runner, cache, background=False)
            try:
                paths = [str(p) for p in (note, changed, missing, folder)]
                first = client.query(paths)
                self.assertEqual(set(first.values()), {metadata.UNKNOWN})
                self.assertEqual(client.tasks.qsize(), 1)
                client.populate(("H:ROOT0001", ()))
                with patch("builtins.open", side_effect=AssertionError("must not read file contents")):
                    states = client.query(paths)
                self.assertEqual([states[p] for p in paths], [metadata.MATCHED, metadata.DIFFERENT, metadata.MISSING, metadata.UNKNOWN])
                self.assertEqual(len([args for args in runner.calls if args[0].endswith("mega-ls")]), 2)
                matched_reply = provider.handle({"version": 1, "id": 2, "method": "query", "paths": [str(note)]}, client)
                self.assertEqual(matched_reply["decorations"][0]["badge"], "synced")
                self.assertEqual(matched_reply["decorations"][0]["description"], "MEGA: Synced")
                status_reply = provider.handle({"version": 1, "id": 3, "method": "activate", "action": "status", "paths": [str(root), str(note)]}, client)
                self.assertTrue(status_reply["message"].startswith("Synced: 2"))
                self.assertIn("inferred", status_reply["message"])
                note.write_bytes(b"changed locally")
                self.assertEqual(client.query([str(note)])[str(note)], metadata.DIFFERENT)
                self.assertEqual(len(runner.calls), 3)
                self.assertNotIn(str(root / "../outside"), client.query([str(root / "../outside")]))
                reply = provider.handle({"version": 1, "id": 1, "method": "query", "paths": paths}, client)
                self.assertEqual([d["badge"] for d in reply["decorations"]], ["different", "different", "missing", "unknown"])
            finally:
                client.close()

    def test_expired_or_failed_snapshots_never_keep_green_badges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "note"
            note.write_bytes(b"test"); os.utime(note, (100, 100))
            now = [1000]
            cache = metadata.Cache(":memory:", clock=lambda: now[0])
            cache.put("H:ROOT0001", {"note": entry()})
            runner = Runner(root, {"H:ROOT0001": {"note": entry()}})
            client = metadata.MetadataClient(runner, cache, clock=lambda: now[0], background=False)
            try:
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                now[0] += metadata.TTL
                self.assertEqual(client.query([str(note)])[str(note)], metadata.UNKNOWN)
                # A failed listing stores negative backoff, not an empty folder.
                runner.entries = {}
                client.populate(("H:ROOT0001", ()))
                self.assertEqual(client.query([str(note)])[str(note)], metadata.UNKNOWN)
                runner.unavailable = True
                now[0] += 5
                self.assertEqual(client.query([str(note)]), {})
            finally:
                client.close()

    def test_nested_repeated_folder_names_missing_parents_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "same" / "same"
            folder.mkdir(parents=True)
            note = folder / "note"
            note.write_bytes(b"test"); os.utime(note, (100, 100))
            runner = Runner(root, {"H:ROOT0001": {"same": entry("H:DIR00001", kind="dir")},
                                   "H:DIR00001": {"same": entry("H:DIR00002", kind="dir")},
                                   "H:DIR00002": {"note": entry()}})
            cache = metadata.Cache(":memory:")
            client = metadata.MetadataClient(runner, cache, background=False)
            try:
                client.populate(("H:ROOT0001", ("same", "same")))
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                alias = root / "alias"; alias.symlink_to(note)
                self.assertEqual(client.query([str(alias)])[str(alias)], metadata.UNKNOWN)
                absent_parent = root / "local-only"; absent_parent.mkdir()
                child = absent_parent / "child"; child.touch()
                self.assertEqual(client.query([str(child)])[str(child)], metadata.MISSING)
                runner.root_handle = "H:ROOT0002"
                client.roots_at = None
                self.assertEqual(client.query([str(note)])[str(note)], metadata.UNKNOWN)
                self.assertNotIn(str(root / "bad\nname"), client.query([str(root / "bad\nname")]))
            finally:
                client.close()

    def test_folder_synced_requires_complete_recursive_match_on_both_sides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "folder"; folder.mkdir()
            nested = folder / "nested"; nested.mkdir()
            note = nested / "note"; note.write_bytes(b"test"); os.utime(note, (100, 100))
            cache = metadata.Cache(":memory:")
            cache.put("H:ROOT0001", {"folder": entry("H:DIR00001", kind="dir")})
            cache.put("H:DIR00001", {"nested": entry("H:DIR00002", kind="dir")})
            runner = Runner(root, {})
            client = metadata.MetadataClient(runner, cache, background=False)
            try:
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.UNKNOWN)
                self.assertIn(("H:DIR00002", ()), client.pending)
                cache.put("H:DIR00002", {"note": entry()})
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.MATCHED)
                reply = provider.handle({"version": 1, "id": 1, "method": "query", "paths": [str(folder)]}, client)
                self.assertEqual(reply["decorations"][0]["badge"], "synced")
                note.write_bytes(b"local changes")
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.DIFFERENT)
                note.write_bytes(b"test"); os.utime(note, (100, 100))
                cache.put("H:DIR00002", {"note": entry(), "cloud-only": entry("H:FILE0002")})
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.DIFFERENT)
                cache.put("H:DIR00002", {"note": entry()})
                (nested / "local-only").touch()
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.DIFFERENT)
                (nested / "local-only").unlink()
                cache.put("H:DIR00002", None)
                self.assertEqual(client.query([str(folder)])[str(folder)], metadata.UNKNOWN)
            finally:
                client.close()

    def test_folder_empty_match_expiry_symlinks_and_scan_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            now = [1000]
            cache = metadata.Cache(":memory:", clock=lambda: now[0])
            client = metadata.MetadataClient(cache=cache, background=False)
            try:
                cache.put("H:DIR00001", {})
                self.assertEqual(client.folder_state(folder, "H:DIR00001", [10, time.monotonic()+1]), metadata.MATCHED)
                now[0] += metadata.TTL
                self.assertEqual(client.folder_state(folder, "H:DIR00001", [10, time.monotonic()+1]), metadata.UNKNOWN)
                cache.put("H:DIR00001", {"alias": entry()})
                (folder / "alias").symlink_to("/tmp/elsewhere")
                self.assertEqual(client.folder_state(folder, "H:DIR00001", [10, time.monotonic()+1]), metadata.UNKNOWN)
                self.assertEqual(client.folder_state(folder, "H:DIR00001", [0, time.monotonic()+1]), metadata.UNKNOWN)
                self.assertEqual(client.folder_state(folder, "H:DIR00001", [10, time.monotonic()-1]), metadata.UNKNOWN)
                with patch.object(metadata.os, "scandir", side_effect=PermissionError):
                    self.assertEqual(client.folder_state(folder, "H:DIR00001", [10, time.monotonic()+1]), metadata.UNKNOWN)
            finally:
                client.close()

    def test_background_worker_populates_cache_without_blocking_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "note"; note.write_bytes(b"test"); os.utime(note, (100, 100))
            runner = Runner(root, {"H:ROOT0001": {"note": entry()}})
            client = metadata.MetadataClient(runner, metadata.Cache(":memory:"))
            try:
                client.query([str(note)])
                deadline = time.monotonic() + 2
                while client.tasks.unfinished_tasks and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(client.query([str(note)])[str(note)], metadata.MATCHED)
                self.assertEqual(client.tasks.unfinished_tasks, 0)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
