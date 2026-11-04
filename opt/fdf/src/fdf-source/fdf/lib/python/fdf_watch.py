################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdf_watch -- [EVENTS_NOT_TIMERS_V1] (John 2026-11-03: "There should be no walks, no locks, no notifications, and no
waiting." "Fix them all.") What a program waits for is an event, never a timer: a file written, or a RAM manager
saying it is up.

A RAM manager says it is up by writing its own file, $FDF_VAR/ram/<host>_<port>.up, when it listens (fdf-ram), and
this machine's member says the region's RAM is reachable again by writing the same name for the address it reached
when its session comes up (fdf-client). A program whose held read failed because its RAM went away blocks on that
file -- not on a sleep and a retry. A RAM that never comes back is never asked again: gone is gone; the program said
so when the read failed.

Inotify is libc's (no module); the watch is set BEFORE the file is looked at, so a write between the failure and the
watch is not missed."""
import ctypes
import ctypes.util
import os
import select
import struct
import time

VAR = os.environ.get("FDF_VAR", "/opt/fdf/var")


class Inotify:
    """inotify on one directory: read() blocks until something in it is written, moved in or removed."""
    IN_CLOSE_WRITE, IN_MOVED_TO, IN_DELETE, IN_MOVED_FROM, IN_CREATE = 0x8, 0x80, 0x200, 0x40, 0x100

    def __init__(self, d):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = self.libc.inotify_init1(0o2000000)                      # IN_CLOEXEC; reads block
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.add(d)

    def add(self, d):
        mask = self.IN_CLOSE_WRITE | self.IN_MOVED_TO | self.IN_DELETE | self.IN_MOVED_FROM | self.IN_CREATE
        if self.libc.inotify_add_watch(self.fd, d.encode(), mask) < 0:
            raise OSError(ctypes.get_errno(), "inotify_add_watch " + d)

    def read(self):
        data = os.read(self.fd, 65536)
        names, i = [], 0
        while i + 16 <= len(data):
            wd, mask, cookie, ln = struct.unpack_from("iIII", data, i)
            names.append(data[i + 16:i + 16 + ln].rstrip(b"\0").decode(errors="replace"))
            i += 16 + ln
        return sorted(set(n for n in names if n))

    def close(self):
        os.close(self.fd)


def _newer(path, since):
    try:
        return os.stat(path).st_mtime >= since
    except OSError:
        return False


def wait_written(path, since=None):
    """Block until `path` is written (moved in, closed after writing) at or after `since` (default: now)."""
    since = time.time() if since is None else since
    d, name = os.path.dirname(path) or ".", os.path.basename(path)
    os.makedirs(d, exist_ok=True)
    w = Inotify(d)
    try:
        if _newer(path, since):
            return
        while True:
            if name in w.read() and _newer(path, since):
                return
    finally:
        w.close()


def up_names(hostport):
    host, _, port = hostport.rpartition(":")
    return ["%s_%s.up" % (host, port), "0.0.0.0_%s.up" % port]


def wait_up(hostport, since):
    """Block until the RAM manager at hostport says it is up after `since` (the time the read on it failed): its own
    file, or this machine's member's for the address it reached."""
    d = os.path.join(VAR, "ram")
    os.makedirs(d, exist_ok=True)
    names = up_names(hostport)
    w = Inotify(d)
    try:
        if any(_newer(os.path.join(d, n), since) for n in names):
            return
        while True:
            got = w.read()
            if any(n in got and _newer(os.path.join(d, n), since) for n in names):
                return
    finally:
        w.close()


def up_or_moved(hostport, since, now):
    """[SYSTEM_RAM_FOLLOWED_V1] block until the RAM manager at hostport says it is up after `since`, or now() -- where
    the RAM is now (fdfram.where) -- is another one: the tunnel daemon moved the system space (a split, a merge).
    One wait on both directories. Returns now()'s answer."""
    d = os.path.join(VAR, "ram")
    os.makedirs(d, exist_ok=True)
    names = up_names(hostport)
    w = Inotify(d)
    w.add(VAR)
    try:
        while True:
            at = now()
            if at != hostport or any(_newer(os.path.join(d, n), since) for n in names):
                return at
            w.read()
    finally:
        w.close()


def up_or_exit(hostport, since, pid):
    """Block until the RAM manager at hostport says it is up after `since` ("up"), or process `pid` exits ("exit"):
    one wait on both (inotify and a pidfd), whichever comes first."""
    d = os.path.join(VAR, "ram")
    os.makedirs(d, exist_ok=True)
    names = up_names(hostport)
    w = Inotify(d)
    pfd = os.pidfd_open(pid)
    try:
        if any(_newer(os.path.join(d, n), since) for n in names):
            return "up"
        while True:
            r, _, _ = select.select([w.fd, pfd], [], [])
            if pfd in r:
                return "exit"
            got = w.read()
            if any(n in got and _newer(os.path.join(d, n), since) for n in names):
                return "up"
    finally:
        w.close(); os.close(pfd)


def after_failure(hostport, err, since):
    """[NOT_THIS_MEMORY_V1] a read on hostport failed with `err` at `since`: a RAM manager that answered ESTALE is up (it
    is another memory than the one read: read it again from the start, now); any other failure waits for it to say it
    is up (wait_up)."""
    if "ESTALE" in str(err):
        return
    wait_up(hostport, since)


def until(path, ok, timeout, also=()):
    """Block until ok(path) is true -- checked when anything is written, created or moved into path's directory (or
    any directory in `also`) -- or `timeout` seconds pass (a bound on the wait, not a poll). True: it is; False: the
    time ran out."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    w = Inotify(d)
    for x in also:
        os.makedirs(x, exist_ok=True); w.add(x)
    end = time.monotonic() + timeout
    try:
        while True:
            if ok(path):
                return True
            left = end - time.monotonic()
            if left <= 0:
                return False
            r, _, _ = select.select([w.fd], [], [], left)
            if r:
                w.read()
    finally:
        w.close()


def _main(argv):
    """For scripts (installers): one wait on a file, never a sleep loop.
      fdf_watch.py has PATH TEXT TIMEOUT_S        until PATH holds TEXT (exit 0), or the time runs out (exit 1)
      fdf_watch.py written PATH SINCE TIMEOUT_S   until PATH is written at or after SINCE (epoch seconds)"""
    if len(argv) == 4 and argv[0] == "has":
        def ok(p):
            try:
                return argv[2] in open(p).read()
            except OSError:
                return False
        return 0 if until(argv[1], ok, float(argv[3])) else 1
    if len(argv) == 4 and argv[0] == "written":
        since = float(argv[2])
        return 0 if until(argv[1], lambda p: _newer(p, since), float(argv[3])) else 1
    print(_main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
