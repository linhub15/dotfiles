# SPDX-License-Identifier: MIT
"""Recursive directory watches using Linux inotify (no third-party module)."""
import ctypes
import os
import select
import struct
import threading

EVENT = struct.Struct("iIII")
OVERFLOW, IGNORED, SELF = 0x4000, 0x8000, 0x400 | 0x800
MASK = 0x2 | 0x4 | 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | SELF


class Watcher:
    def __init__(self, callback):
        self.callback = callback
        self.lock = threading.RLock()
        self.paths, self.descriptors = {}, {}
        self.stopping = threading.Event()
        self.failed = False
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_init1.restype = ctypes.c_int
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_add_watch.restype = ctypes.c_int
        self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        self.broken = self.fd < 0
        self.healthy = not self.broken
        self.thread = None
        if self.healthy:
            self.thread = threading.Thread(target=self.read, daemon=True)
            self.thread.start()

    def add(self, path):
        path = os.path.abspath(path)
        with self.lock:
            if path in self.descriptors:
                return True
            if self.fd < 0 or len(self.paths) >= 8192:
                self.failed, self.healthy = True, False
                return False
            wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), MASK | 0x1000000 | 0x2000000)
            if wd < 0:
                self.failed, self.healthy = True, False
                return False
            self.paths[wd], self.descriptors[path] = path, wd
            return True

    def reset(self):
        with self.lock:
            for wd in self.paths:
                self.libc.inotify_rm_watch(self.fd, wd)
            self.paths.clear()
            self.descriptors.clear()
            self.failed = False
            self.healthy = self.fd >= 0 and not self.broken

    def decode(self, data):
        paths, lost, offset = set(), False, 0
        with self.lock:
            while offset + EVENT.size <= len(data):
                wd, mask, cookie, length = EVENT.unpack_from(data, offset)
                offset += EVENT.size
                raw = data[offset:offset + length].split(b"\0", 1)[0]
                offset += length
                if mask & OVERFLOW:
                    lost = True
                    continue
                directory = self.paths.get(wd)
                if directory is None:
                    continue
                if mask & (SELF | IGNORED):
                    self.paths.pop(wd, None)
                    self.descriptors.pop(directory, None)
                    if mask & 0x400:  # Deleted directory: only its parent needs reindexing.
                        paths.add(directory)
                    else:
                        lost = True
                else:
                    paths.add(os.path.join(directory, os.fsdecode(raw)) if raw else directory)
        return None if lost else paths

    def read(self):
        while not self.stopping.is_set():
            try:
                if not select.select([self.fd], [], [], 0.2)[0]:
                    continue
                paths = self.decode(os.read(self.fd, 65536))
                if paths is None or paths:
                    self.callback(paths)
            except Exception:
                # Any permanent reader/callback failure withdraws confidence in
                # cached folder statuses. Never leave an unwatched cache healthy.
                self.broken, self.healthy = True, False
                if not self.stopping.is_set():
                    try:
                        self.callback(None)
                    except Exception:
                        pass
                return

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=0.5)
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
