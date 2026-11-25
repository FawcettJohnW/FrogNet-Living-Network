#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[HELD_SOCKETS_SURVIVE_THE_WIPE_V1] The merge's wipe of /run/frognet, run as the script has it, on a directory made
here: what a merge must leave, and what it must still remove.

Found on John's network, 23 Nov 2026: runMerge.bash removed /run/frognet/held at every merge, so every process that
held a read was left bound to a socket with no name, and no fire reached it until it was restarted. John: "A merge
should delete the held reads only if it changes /etc/hosts." The holds end with /etc/hosts in the holder itself
(core/frognet_held.py); the sockets are never the merge's to remove.

The loop is taken from usr/local/bin/runMerge.bash itself, not copied here: if the script changes, this runs what it
says now.
"""
import os
import re
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
passed = failed = 0


def check(ok, text):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    print("  [%s] %s" % ("PASS" if ok else "FAIL", text))


def main() -> int:
    script = open(os.path.join(ROOT, "usr", "local", "bin", "runMerge.bash")).read()
    m = re.search(r"^for _f in /var/run/frognet/\* /var/run/frognet/\.\[!\.\]\*; do\n.*?^done\n", script, re.S | re.M)
    check(m is not None, "runMerge.bash still has its wipe of /var/run/frognet as one loop")
    if m is None:
        return 1
    d = tempfile.mkdtemp(prefix="run-frognet-")
    held = os.path.join(d, "held")
    os.makedirs(held)
    names = ["daemon_main.py.101.sock", "proxy_main.py.102.sock", "role-host-fires.sock"]
    socks = []
    for n in names:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(os.path.join(held, n))
        socks.append(s)
    for keep in ("merge_requests", "seen_notifications"):
        os.makedirs(os.path.join(d, keep))
    open(os.path.join(d, "tunnel-daemon.pid"), "w").write("1\n")
    open(os.path.join(d, "nm_dispatch_sig"), "w").write("x\n")
    os.makedirs(os.path.join(d, "debug", "old-run"))
    open(os.path.join(d, "semantic_hosts"), "w").write("x\n")
    open(os.path.join(d, ".hidden_state"), "w").write("x\n")
    r = subprocess.run(["bash", "-c", m.group(0).replace("/var/run/frognet", d)], capture_output=True, text=True)
    check(r.returncode == 0, "the wipe runs (%s)" % (r.stderr.strip()[:120] or "no error"))
    check(all(os.path.exists(os.path.join(held, n)) for n in names),
          "every holder's socket and the daemon's role-host-fires.sock are still there after the wipe")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        probe.sendto(b'{"hold": 1}', os.path.join(held, names[0]))
        socks[0].settimeout(2)
        got = socks[0].recv(100)
    except OSError as e:
        got = b""
        print("     (%s)" % e)
    check(got == b'{"hold": 1}', "a fire sent to a holder's socket after the wipe reaches the process that was already running")
    check(all(os.path.exists(os.path.join(d, k)) for k in ("merge_requests", "seen_notifications", "tunnel-daemon.pid", "nm_dispatch_sig")),
          "what the merge kept before, it still keeps")
    check(not any(os.path.exists(os.path.join(d, k)) for k in ("debug", "semantic_hosts", ".hidden_state")),
          "what the merge wiped before, it still wipes (debug, semantic_hosts, a hidden file)")
    for s in socks + [probe]:
        s.close()
    subprocess.run(["rm", "-rf", d])
    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
