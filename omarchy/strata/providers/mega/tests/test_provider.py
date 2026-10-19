import io
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import provider
import install


class MegaServer:
    def __init__(self, endpoint, responses):
        self.requests = []
        self.error = None
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(endpoint))
        self.server.listen()
        self.server.settimeout(2)
        self.responses = responses
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            with self.server.accept()[0] as connection:
                connection.settimeout(2)
                for response in self.responses:
                    data = b""
                    while not data.endswith(b"\x1c0"):
                        part = connection.recv(1024)
                        if not part:
                            return
                        data += part
                    self.requests.append(data)
                    if response is None:
                        return
                    if isinstance(response, float):
                        time.sleep(response)
                        return
                    # Deliberately fragment every byte to test stream framing.
                    for byte in response:
                        connection.sendall(bytes([byte]))
        except (OSError, TimeoutError) as error:
            self.error = error
        finally:
            self.server.close()

    def finish(self):
        self.thread.join(3)
        if self.thread.is_alive():
            raise AssertionError("Mock MEGA server did not terminate")


def request(method="query", paths=None, **extra):
    return {"version": 1, "id": 7, "method": method,
            "paths": paths if paths is not None else ["/tmp/report.txt"],
            "background": False, **extra}


class ProviderTests(unittest.TestCase):
    def test_all_states_and_literal_unicode_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "s"
            paths = [f'/tmp/quotes " $() café {i}' for i in range(7)]
            server = MegaServer(endpoint, [f"{state}\n".encode() for state in (0, 1, 2, 3, 4, 9, 10)])
            reply = provider.handle(request(paths=paths), provider.MegaClient(endpoint, roots=("/tmp",)))
            server.finish()
            self.assertEqual([d["badge"] for d in reply["decorations"]],
                             ["synced", "pending", "syncing", "ignored", "paused"])
            self.assertEqual([d["path"] for d in reply["decorations"]], paths[:5])
            self.assertEqual(server.requests, [b"P:" + p.encode() + b"\x1c0" for p in paths])
            self.assertIsNone(server.error)

    def test_failure_withdraws_entire_batch_and_next_query_recovers(self):
        for response in (None, b"not-a-status\n", b"5\n", b"\n", b"0000\n"):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                endpoint = Path(directory) / "s"
                server = MegaServer(endpoint, [b"0\n", response])
                client = provider.MegaClient(endpoint, roots=("/tmp",))
                self.assertEqual(client.query(["/tmp/a", "/tmp/b"]), {})
                server.finish()
                endpoint.unlink()
                server = MegaServer(endpoint, [b"2\n"])
                self.assertEqual(client.query(["/tmp/a"]), {"/tmp/a": 2})
                server.finish()

    def test_timeout_and_absent_service_never_report_synced(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "s"
            client = provider.MegaClient(endpoint, roots=("/tmp",))
            self.assertEqual(client.query(["/tmp/a"]), {})
            server = MegaServer(endpoint, [0.15])
            with patch.object(provider, "SOCKET_SECONDS", 0.03):
                before = time.monotonic()
                self.assertEqual(client.query(["/tmp/a"]), {})
                self.assertLess(time.monotonic() - before, 0.5)
            server.finish()

    def test_unencodable_paths_are_not_sent(self):
        for path in ("relative", "/a\nb", "/a\rb", "/a\x1cb", "/a\0b", "/" + "é" * 600, "/a\udcff"):
            with self.subTest(path=repr(path)):
                self.assertIsNone(provider.wire_path(path))
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "link"
            link.symlink_to("/tmp/bad\nname")
            self.assertIsNone(provider.wire_path(str(link)))
            link.unlink()
            link.symlink_to("/tmp/real")
            self.assertEqual(provider.wire_path(str(link)), b"P:/tmp/real\x1c0")

    def test_query_budget_withdraws_incomplete_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "s"
            server = MegaServer(endpoint, [0.15])
            with patch.object(provider, "BATCH_SECONDS", 0.02):
                self.assertEqual(provider.MegaClient(endpoint, roots=("/tmp",)).query(["/tmp/a"]), {})
            server.finish()

    def test_menu_requires_entire_selection_and_activation_rechecks(self):
        class Client:
            values = {"/tmp/a": 0}
            def query(self, paths):
                return self.values.copy()
        client = Client()
        self.assertEqual(provider.handle(request("menu", ["/tmp/a", "/tmp/b"]), client)["actions"], [])
        menu = provider.handle(request("menu", ["/tmp/a"]), client)
        self.assertEqual(menu["actions"][0]["id"], "status")
        client.values = {"/tmp/a": 4}
        status = provider.handle(request("activate", ["/tmp/a"], action="status"), client)
        self.assertEqual(status["message"], "Paused: 1")
        client.values = {}
        status = provider.handle(request("activate", ["/tmp/a"], action="status"), client)
        self.assertEqual(status["outcome"]["status"], "rejected")

    def test_subprocess_protocol_and_xdg_socket_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "data/Mega Limited/MEGAsync/mega.socket"
            endpoint.parent.mkdir(parents=True)
            server = MegaServer(endpoint, [b"0\n"])
            notify = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            notify.bind(str(endpoint.with_name("notify.socket")))
            notify.listen()
            def announce():
                with notify.accept()[0] as connection:
                    connection.sendall(b"A/tmp\n")
                    time.sleep(0.1)
                notify.close()
            thread = threading.Thread(target=announce, daemon=True)
            thread.start()
            result = subprocess.run([sys.executable, "-c", "import provider,sys; provider.serve(sys.stdin.buffer,sys.stdout,provider.MegaClient())"],
                                    input=json.dumps(request()).encode() + b"\n",
                                    capture_output=True, timeout=3,
                                    cwd=ROOT,
                                    env={**os.environ, "XDG_DATA_HOME": directory})
            server.finish()
            thread.join(2)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(json.loads(result.stdout)["decorations"][0]["badge"], "synced")

    def test_outside_sync_paths_are_not_queried_even_if_mega_would_say_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "s"
            server = MegaServer(endpoint, [b"3\n"])
            client = provider.MegaClient(endpoint, roots=("/tmp/cloud",))
            self.assertEqual(client.query(["/tmp/cloud-other/a", "/usr/share", "/tmp/cloud/a"]),
                             {"/tmp/cloud/a": 3})
            server.finish()
            self.assertEqual(server.requests, [b"P:/tmp/cloud/a\x1c0"])
            self.assertEqual(provider.MegaClient(endpoint, roots=()).query(["/tmp/cloud/a"]), {})

    def test_notification_snapshots_empty_deleted_and_malformed(self):
        cases = [(b"A/tmp/cloud\n", ("/tmp/cloud",)),
                 (b"A.\n", ()), (b"A/tmp/cloud\nD/tmp/cloud\n", ()),
                 (b"A/tmp/cloud", ()), (b"Abad-relative\n", ()),
                 (b"A/tmp/cloud\nunknown\n", ()), (b"A/tmp/\xff\n", ())]
        for payload, expected in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                endpoint = Path(directory) / "mega.socket"
                notify = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                notify.bind(str(endpoint.with_name("notify.socket")))
                notify.listen()
                def announce():
                    with notify.accept()[0] as connection:
                        connection.sendall(payload)
                        time.sleep(0.06)
                    notify.close()
                thread = threading.Thread(target=announce, daemon=True)
                thread.start()
                self.assertEqual(provider.MegaClient(endpoint).sync_roots(), expected)
                thread.join(2)

    def test_megacmd_states_only_decorate_sync_roots(self):
        sep = provider.MegaCmdClient.separator
        records = [("/tmp/cloud", "Running", "Synced", "NO"),
                   ("/tmp/upload", "Running", "Syncing", "NO"),
                   ("/tmp/wait", "Loading", "NONE", "NO"),
                   ("/tmp/paused", "Suspended", "NONE", "NO"),
                   ("/tmp/broken", "Running", "Synced", "Sync Issues (1)"),
                   ("/tmp/unknown", "Running", "Processing", "NO")]
        output = sep.join(("LOCALPATH", "RUN_STATE", "STATUS", "ERROR")) + "\n"
        output += "\n".join(sep.join(record) for record in records) + "\n"
        calls = []
        def runner(argv, **options):
            calls.append((argv, options))
            return SimpleNamespace(returncode=0, stdout=output.encode())
        client = provider.MegaCmdClient(runner)
        paths = [record[0] for record in records] + ["/tmp/cloud/file.txt", "/tmp/cloud-other"]
        self.assertEqual(client.query(paths), {"/tmp/cloud": 0, "/tmp/upload": 2,
                                              "/tmp/wait": 1, "/tmp/paused": 4, "/tmp/broken": 5})
        self.assertEqual(calls[0][0][0], "/usr/bin/mega-sync")
        self.assertFalse(any(path in calls[0][0] for path in paths))
        self.assertEqual(calls[0][1]["env"]["PATH"], "/usr/bin:/bin")

    def test_megacmd_failure_and_malformed_output_withdraw_badges(self):
        sep = provider.MegaCmdClient.separator
        header = sep.join(("LOCALPATH", "RUN_STATE", "STATUS", "ERROR")) + "\n"
        for code, output in ((57, b"Not logged in"), (0, b"unexpected"),
                             (0, (header + "/tmp/cloud\n").encode()),
                             (0, b"\xff"), (0, b"x" * (provider.MAX_FRAME + 1))):
            with self.subTest(code=code, output_size=len(output)):
                client = provider.MegaCmdClient(lambda *a, **kw: SimpleNamespace(returncode=code, stdout=output))
                self.assertEqual(client.query(["/tmp/cloud"]), {})
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("mega-sync", 4)
        self.assertEqual(provider.MegaCmdClient(timeout).query(["/tmp/cloud"]), {})

    def test_multiple_requests_and_invalid_frames(self):
        client = provider.MegaClient("/nonexistent/mega.socket")
        destination = io.StringIO()
        data = b"\n".join(json.dumps(request(method)).encode() for method in ("query", "menu")) + b"\n"
        provider.serve(io.BytesIO(data), destination, client)
        replies = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual(replies[0]["decorations"], [])
        self.assertEqual(replies[1]["actions"], [])
        for invalid in (b"[]\n", b"{}", b"{\xff}\n", b"x" * (provider.MAX_FRAME + 2) + b"\n"):
            destination = io.StringIO()
            provider.serve(io.BytesIO(invalid), destination, client)
            self.assertEqual(destination.getvalue(), "")
        self.assertEqual(provider.handle(request(paths=["relative"]), client)["error"], "invalid-request")
        self.assertEqual(provider.handle(request(paths=["/a"] * 201), client)["error"], "invalid-request")
        self.assertEqual(provider.handle(request("other"), client)["error"], "unsupported-method")

    def test_installer_creates_trusted_manifest_and_real_icons(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = install.install(Path(directory))
            manifest = json.loads((destination / "provider.json").read_text())
            self.assertEqual(manifest["command"][1], str(destination / "provider.py"))
            self.assertTrue(Path(manifest["command"][0]).is_absolute())
            self.assertEqual(manifest["id"], destination.name)
            for file in destination.iterdir():
                self.assertFalse(file.stat().st_mode & 0o022)
            for file in manifest["icons"].values():
                data = (destination / file).read_bytes()
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(int.from_bytes(data[16:20], "big"), 64)
                self.assertLess(len(data), 65536)


if __name__ == "__main__":
    unittest.main()
