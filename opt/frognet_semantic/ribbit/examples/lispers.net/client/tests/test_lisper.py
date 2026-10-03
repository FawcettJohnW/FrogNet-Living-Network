#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_lisper.py PORT -- the LISP client library from Python (ctypes) against a real lisper-ram: a site, a real
authenticated Map-Register (built by the acceptance suite's own encoder) answered with a valid Map-Notify, the lookup."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..")); sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import lisper
import acceptance as A
fails = 0
def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what); fails += 0 if ok else 1
c = lisper.Client("127.0.0.1", int(sys.argv[1]))
c.add_site("0", "198.18.0.0/16", "", True, A.KEY_ID, A.PASSWORD)
pkt = A.register([("198.18.9.0", 24, 3, ["192.0.2.9"])], 0x1234567812345678)
notify = c.register(pkt.hex(), "192.0.2.9")
check(len(notify) > 0 and A.notify_valid(bytes.fromhex(notify)), "P1 an authenticated Map-Register through the library: a Map-Notify with a valid HMAC")
r = c.resolve("0", "198.18.9.1")
check(any("198.18.9.0/24" in str(x) for x in (r if isinstance(r, list) else [r])), "P2 the lookup resolves to the registration")
bad = A.register([("198.18.10.0", 24, 3, ["192.0.2.9"])], 0x1111, password="wrong")
check(c.register(bad.hex(), "192.0.2.9") == "", "P3 the wrong key: no Map-Notify, nothing applied")
check(c.resolve("0", "198.18.10.1") == [], "P3 ... and nothing resolves")
c.delete_site("0", "198.18.0.0/16"); c.close()
print("RESULT %s lisper client (Python, ctypes): %d fail" % ("FAIL" if fails else "PASS", fails))
sys.exit(1 if fails else 0)
