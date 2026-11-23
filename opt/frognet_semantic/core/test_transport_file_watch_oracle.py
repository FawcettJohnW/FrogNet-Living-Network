#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/test_transport_file_watch_oracle.py -- [THE_TRANSPORT_FILE_IS_WATCHED_V1] what the watcher does for each change
to the node's transport file.

Real: core/transport_file_watch.py, core/homa_transport.py (a real process start reads the file and writes its record).
Substituted: the restart command (it appends a line to a file here), and the file's place. systemd's own part, waking
the watcher when the file changes, is not run here: there is no systemd in this sandbox.

    python3 core/test_transport_file_watch_oracle.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = []


def check(ok, what):
    print("  [%s] %s" % ("PASS" if ok else "FAIL", what), flush=True)
    if not ok:
        FAILED.append(what)


def main():
    d = tempfile.mkdtemp(prefix="transport-watch-")
    f, rec, did = os.path.join(d, "semantic_transport"), os.path.join(d, "run", "semantic_transport.read"), os.path.join(d, "restarts")
    env = dict(os.environ, FROGNET_SEM_TRANSPORT_FILE=f, FROGNET_SEM_TRANSPORT_READ=rec, PYTHONPATH=HERE)
    env.pop("FROGNET_SEM_TRANSPORT", None)

    def start():                                   # a daemon or proxy starting: it reads the file
        return subprocess.run([sys.executable, "-c", "from core import homa_transport as H; H._chosen=None; print(H.semantic_transport())"],
                              env=env, cwd=HERE, capture_output=True, text=True)

    def watch():                                   # the watcher, as the path unit runs it
        code = ("from core import transport_file_watch as W\nW.RESTART=['sh','-c','echo restarted >> %s']\nimport sys\nsys.exit(W.main())" % did)
        r = subprocess.run([sys.executable, "-c", code], env=env, cwd=HERE, capture_output=True, text=True)
        return r.returncode, r.stdout

    def restarts():
        return len(open(did).read().split()) if os.path.exists(did) else 0

    def write(text):
        with open(f, "w") as h:
            h.write(text)

    write("auto\n")
    r = start()
    check(r.returncode == 0 and open(rec).read() == "auto\n", "a process starting writes what it read from the file")
    rc, out = watch()
    check(rc == 0 and restarts() == 0 and "nothing to do" in out, "the file says what was read: nothing is restarted")
    write("auto\ndatabasehost_control.frognet chorus\n")
    rc, out = watch()
    check(rc == 0 and restarts() == 1 and "restarting frognet-daemon and frognet-proxy" in out and "databasehost_control.frognet chorus" in out,
          "a Chorus line is added: the daemon and the proxy are restarted, once, and the line says what the file says now")
    start()
    rc, out = watch()
    check(restarts() == 1 and "nothing to do" in out, "they have read it: a second wake for the same change restarts nothing")
    write("AUTO\n  databasehost_control.frognet   chorus  \n\n")
    rc, out = watch()
    check(restarts() == 1, "saved again with other spacing and case, the same in meaning to a process: nothing is restarted")
    write("auto\ndatabasehost_control.frognet choras\n")
    rc, out = watch()
    check(rc == 0 and restarts() == 1 and "NOT restarted" in out and "choras" in out,
          "a file no process could start on: nothing is restarted, and the line says what is wrong with it")
    write("auto\ndatabasehost_control.frognet tcp\n")
    rc, out = watch()
    check(restarts() == 2, "corrected: restarted")
    start()
    os.remove(f)
    rc, out = watch()
    check(restarts() == 3 and "It says now: auto" in out, "the file is removed (the default): restarted")
    start()
    rc, out = watch()
    check(restarts() == 3, "and not again")
    os.remove(rec)
    rc, out = watch()
    check(restarts() == 4 and "no record of what they read" in out, "no record of what the running processes read (they predate this): restarted once")
    print("RESULT %s" % ("FAIL" if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
