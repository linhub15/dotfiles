#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""MEGAcmd sync-root status and cached cloud-metadata badges for Strata."""

import json
from contextlib import nullcontext
import threading
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

MAX_FRAME = 1024 * 1024
MAX_PATHS = 200
BATCH_SECONDS = 4.0
SOCKET_SECONDS = 0.3
STATES = {
    0: ("synced", "Synced"),
    1: ("pending", "Pending"),
    2: ("syncing", "Syncing"),
    3: ("ignored", "Ignored"),
    4: ("paused", "Paused"),
    5: ("error", "Sync issue"),
    6: ("synced", "Synced"),
    7: ("different", "Metadata differs"),
    8: ("missing", "Missing in cloud"),
    9: ("unknown", "Unknown"),
    10: ("cloud-folder", "Folder exists in cloud"),
    11: ("pending", "Local changes"),
}


class MegaCmdClient:
    """MEGAcmd exposes whole-sync status, not individual file status.

    Only decorate exact sync roots. Inheriting a root's status onto every child
    would wrongly label excluded files and conceal individual pending changes.
    """
    separator = "\x1f"

    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def query(self, paths):
        try:
            result = self.runner(
                ["/usr/bin/mega-sync", "--output-cols=LOCALPATH,RUN_STATE,STATUS,ERROR",
                 "--col-separator=" + self.separator, "--path-display-size=16384"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=4,
                env={**os.environ, "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            )
            if result.returncode or len(result.stdout) > MAX_FRAME:
                return {}
            roots = self.parse(result.stdout.decode("utf-8"))
            states = {}
            for path in paths:
                if wire_path(path) is None:
                    continue
                state = roots.get(os.path.realpath(path))
                if state is not None:
                    states[path] = state
            return states
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            return {}

    @classmethod
    def parse(cls, text):
        lines = text.split("\n")
        expected = ["LOCALPATH", "RUN_STATE", "STATUS", "ERROR"]
        if not lines or [x.strip() for x in lines[0].split(cls.separator)] != expected:
            return {}
        roots = {}
        for line in lines[1:]:
            if not line:
                continue
            fields = line.split(cls.separator)
            if len(fields) != 4:
                return {}
            path, running, status, error = fields
            running, status, error = running.strip(), status.strip(), error.strip()
            if not os.path.isabs(path) or wire_path(path) is None:
                return {}
            if error != "NO":
                state = 5
            elif running in ("Suspended", "Disabled"):
                state = 4
            elif running in ("Pending", "Loading"):
                state = 1
            elif running == "Running":
                state = {"Synced": 0, "Syncing": 2, "Pending": 1}.get(status)
            else:
                state = None
            if state is not None:
                key = os.path.realpath(path)
                if key in roots:
                    return {}
                roots[key] = state
        return roots


def socket_path():
    base = os.environ.get("XDG_DATA_HOME", "")
    if not os.path.isabs(base):
        base = str(Path.home() / ".local/share")
    return Path(base) / "data/Mega Limited/MEGAsync/mega.socket"


def wire_path(path):
    """MEGA's protocol is delimiter-based with a 1024-byte server buffer.

    Never truncate or escape a path: either would ask about a different file.
    Resolve symlinks to match MEGA's own file-manager extensions.
    """
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    if any(c in path for c in ("\0", "\n", "\r", "\x1c")):
        return None
    try:
        canonical = os.path.realpath(path)
        if any(c in canonical for c in ("\0", "\n", "\r", "\x1c")):
            return None
        payload = b"P:" + canonical.encode("utf-8") + b"\x1c0"
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if len(payload) <= 1023 else None


class MegaClient:
    def __init__(self, endpoint=None, roots=None):
        self.endpoint = endpoint if endpoint is not None else socket_path()
        self.roots = roots

    def sync_roots(self):
        """Each notify connection starts with A<root> records, or A. for none.

        There is no snapshot terminator. Briefly drain the initial burst, with
        a hard bound; incomplete data fails closed rather than decorating files
        outside known syncs. MEGA reports many unrelated paths as ignored.
        """
        if self.roots is not None:
            return self.roots
        deadline = time.monotonic() + 0.5
        roots = set()
        data = bytearray()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(SOCKET_SECONDS)
                connection.connect(str(Path(self.endpoint).with_name("notify.socket")))
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return ()
                    connection.settimeout(min(0.03 if data else SOCKET_SECONDS, remaining))
                    try:
                        part = connection.recv(65536)
                    except TimeoutError:
                        break
                    if not part or len(data) + len(part) > 262144:
                        return ()
                    data.extend(part)
            if not data or not data.endswith(b"\n"):
                return ()
            for line in bytes(data).split(b"\n")[:-1]:
                if line == b"A.":
                    continue
                if line[:1] not in (b"A", b"D", b"P"):
                    return ()
                path = line[1:].decode("utf-8")
                if not os.path.isabs(path) or any(c in path for c in ("\0", "\r", "\x1c")):
                    return ()
                if line[:1] == b"A":
                    roots.add(path)
                elif line[:1] == b"D":
                    roots.discard(path)
        except (OSError, UnicodeError, ValueError):
            return ()
        return tuple(roots)

    def query(self, paths):
        roots = self.sync_roots()
        if not roots:
            return {}
        requests = [(path, wire_path(path)) for path in paths]
        requests = [(path, payload) for path, payload in requests
                    if payload and any(Path(payload[2:-2].decode("utf-8")).is_relative_to(root) for root in roots)]
        if not requests:
            return {}
        deadline = time.monotonic() + BATCH_SECONDS
        states = {}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(SOCKET_SECONDS)
                connection.connect(str(self.endpoint))
                for path, payload in requests:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {}
                    connection.settimeout(min(SOCKET_SECONDS, remaining))
                    # MEGA's official extensions send an un-terminated request,
                    # then wait for a newline-terminated numeric response.
                    connection.sendall(payload)
                    reply = bytearray()
                    while not reply.endswith(b"\n"):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return {}
                        connection.settimeout(min(SOCKET_SECONDS, remaining))
                        part = connection.recv(1)
                        if not part or len(reply) >= 3:
                            return {}
                        reply.extend(part)
                    if bytes(reply) not in (b"0\n", b"1\n", b"2\n", b"3\n", b"4\n", b"9\n", b"10\n"):
                        return {}
                    state = int(reply)
                    if state in (0, 1, 2, 3, 4):
                        states[path] = state
        except (OSError, ValueError):
            # Withdraw all badges on disconnect, malformed response or timeout;
            # retaining earlier results could falsely claim a file is synced.
            return {}
        return states


def handle(request, client):
    request_id = request.get("id", 0)
    reply = {"version": 1, "id": request_id}
    paths = request.get("paths")
    if (request.get("version") != 1 or type(request_id) is not int
            or not isinstance(paths, list) or len(paths) > MAX_PATHS
            or any(not isinstance(p, str) or not os.path.isabs(p) for p in paths)):
        return {**reply, "error": "invalid-request"}
    method = request.get("method")
    if method not in ("query", "menu", "activate"):
        return {**reply, "error": "unsupported-method"}
    if method == "activate" and request.get("action") != "status":
        return {**reply, "error": "unsupported-action"}
    states = client.query(paths)
    if method == "query":
        return {**reply, "decorations": [
            {"path": p, "badge": STATES[state][0],
             "description": "MEGA: " + STATES[state][1],
             "priority": 2 if state == 5 else (1 if state in (4, 7, 11) else 0)}
            for p, state in states.items()
        ]}
    if method == "menu":
        # The action applies to the entire selection, never a filtered subset.
        actions = [{"id": "status", "label": "Check MEGA status"}] if paths and len(states) == len(set(paths)) else []
        return {**reply, "actions": actions}
    # Re-query on activation: the file or sync configuration may have changed
    # since opening the menu. This adapter never mutates local or cloud data.
    if not paths or len(states) != len(set(paths)):
        return {**reply, "message": "MEGA status is unavailable for this selection. Check that MEGAcmd is signed in. Select files or folders within a configured sync folder.",
                "outcome": {"status": "rejected"}}
    counts = {}
    for state in states.values():
        label = STATES[state][1]
        counts[label] = counts.get(label, 0) + 1
    message = "\n".join(f"{label}: {count}" for label, count in counts.items())
    if any(state >= 6 for state in states.values()):
        message += "\n\nFile status is inferred from size and modification time compared with cached cloud metadata. Folders marked Synced have matching metadata throughout the checked subtree."
    return {**reply, "message": message, "outcome": {"status": "accepted"}}


def serve(source, destination, client):
    output_lock = threading.Lock()
    def send(payload):
        with output_lock:
            destination.write(json.dumps(payload, ensure_ascii=True) + "\n")
            destination.flush()
    if hasattr(client, "set_event_sink"):
        client.set_event_sink(send)
    while True:
        line = source.readline(MAX_FRAME + 2)
        if not line:
            return
        if len(line) > MAX_FRAME + 1 or not line.endswith(b"\n"):
            return
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                return
            # Serialize replies with watcher/index invalidations, so a newer
            # dirty event cannot be followed by an older successful reply.
            with getattr(client, "presentation_lock", nullcontext()):
                response = handle(request, client)
                send(response)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            return


if __name__ == "__main__":
    from mega_index import IndexedClient
    client = IndexedClient()
    try:
        serve(sys.stdin.buffer, sys.stdout, client)
    except BrokenPipeError:
        pass
    finally:
        client.close()
