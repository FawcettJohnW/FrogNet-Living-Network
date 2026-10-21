#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_chat.py PORT -- the chat client library from Python (ctypes, client/chat.py) against a real chat-ram."""
import os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
import chat
fails = 0
def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what)
    fails += 0 if ok else 1
port = int(sys.argv[1]); tag = str(os.getpid())
dave, bob = "PyDave" + tag, "PyBob" + tag
d, b = chat.Client("127.0.0.1", port), chat.Client("127.0.0.1", port)
at = b.position(bob)
d.send(bob, dave, "hello from python")
m, at = b.receive(bob, at, 5)
check(len(m) == 1 and m[0]["from"] == dave and m[0]["text"] == "hello from python", "P1 send and receive through the library")
got = []
def waiter():
    global at
    r, at2 = b.receive(bob, at, 10); got.extend(r); at = at2
t0 = time.time(); th = threading.Thread(target=waiter); th.start(); time.sleep(0.4); d.send(bob, dave, "late"); th.join()
check(len(got) == 1 and got[0]["text"] == "late" and 0.35 <= time.time() - t0 < 3, "P2 a held receive wakes on the send")
try:
    chat.Client("127.0.0.1", 1); check(False, "P3 unreachable chat-ram raises")
except chat.ChatError as e:
    check("unreachable" in str(e).lower() or "refused" in str(e).lower() or "connect" in str(e).lower(), "P3 unreachable chat-ram raises ChatError (%s)" % e)
d.close(); b.close()
print("RESULT %s chat client (Python, ctypes): %d fail" % ("FAIL" if fails else "PASS", fails))
sys.exit(1 if fails else 0)
