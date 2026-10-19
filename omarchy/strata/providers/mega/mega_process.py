# SPDX-License-Identifier: MIT
"""Bound CLI stdout while it is produced, including descendants on cancellation."""
import os
import selectors
import signal
import subprocess
import time

MAX_OUTPUT = 1024 * 1024


def run_cli(args, *, stdin=subprocess.DEVNULL, capture_output=True, timeout=2, env=None):
    process = subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            if not selector.select(min(remaining, 0.1)):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            if len(output) + len(chunk) > MAX_OUTPUT:
                raise ValueError("cli-output-limit")
            output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        code = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(args, code, bytes(output), b"")
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
