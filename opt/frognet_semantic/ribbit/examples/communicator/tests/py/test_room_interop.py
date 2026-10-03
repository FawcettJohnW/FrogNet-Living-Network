#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_room_interop.py BUILD_DIR -- the Python room and the C++ room in one comms-ram: each sees the other in the
lobby, each reads the other's chat, and an invitation from C++ reaches Python intact."""
import os, subprocess, sys, time
B = sys.argv[1]
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
os.environ.setdefault("FROGCOMMS_LIB", B)
from frogcomms.room import Room  # noqa: E402

PORT = 21100
ram = subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % PORT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fail = 0
try:
    time.sleep(1)
    py = Room("127.0.0.1", PORT, "Pythonia")
    py.send_chat("S1", "hello from python")
    out = subprocess.run([os.path.join(B, "room_probe"), str(PORT), "Cplus", "S1", "hello from c++", "Pythonia"],
                         capture_output=True, text=True, timeout=20).stdout
    print(out.strip())
    if "Pythonia" not in out.split("\n")[0]: fail += 1; print("FAIL C++ does not see the Python participant")
    if "chat: Pythonia: hello from python" not in out: fail += 1; print("FAIL C++ did not read Python's chat")
    deadline = time.time() + 3
    while time.time() < deadline and not any(l.text == "hello from c++" for l in py.read_chat("S1")):
        py.wait_change(py.version, 0.5)
    names = [m.name for m in py.roster()]
    lines = ["%s: %s" % (l.from_name, l.text) for l in py.read_chat("S1")]
    inv = py.invitations()
    print("python sees roster:", names, "| chat:", lines, "| invitations:", inv)
    if "Pythonia" not in names: fail += 1; print("FAIL Python does not see itself")
    if "Cplus: hello from c++" not in lines: fail += 1; print("FAIL Python did not read C++'s chat")
    if not any(o.session == "S1" and o.offered_by == "Cplus" and o.media_port == 8994 for o in inv):
        fail += 1; print("FAIL the C++ invitation did not reach Python intact")
    t0 = time.time(); py.close(); closed = time.time() - t0
    print("python room closed in %.2f s" % closed)
    if closed > 1.0: fail += 1; print("FAIL closing waited on the held read")
finally:
    ram.terminate()
print("RESULT %s room: Python <-> C++ in one comms-ram: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
