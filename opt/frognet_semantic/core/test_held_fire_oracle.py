#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1] oracle, under the frame: a role host's daemon puts a held read's fire on
the connection the holder's node opened; the holder's proxy hands it to the holder's socket.

Contracts:
  1. a node that has named only a Role's sequence on its connection (none for traffic of no Role): the fire rides
     that sequence, arrives on the holder's socket value for value, and the connection is still up
     (found between machines, 22 Nov 2026: handed to a sequence the node had not named, the daemon's writer stopped,
     the connection ended and every session on it with it)
  2. a request made on that connection after the fire is answered as before
  3. a node with a sequence for traffic of no Role: the fire rides that one
  4. a node with no connection here: nothing takes the fire (the caller says it has nowhere to go)
  5. a fire for a holder that is not there is said, and nothing is raised
Every wait is a blocking read."""
import json
import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
held = tempfile.mkdtemp(prefix="held-fire-oracle-")
os.environ["FROGNET_HELD_DIR"] = held
from core import channel_setting as CS  # noqa: E402
from core import channel_carrier as C  # noqa: E402
from core import chorus as _chorus  # noqa: E402,F401
from core import held_fire as HF  # noqa: E402

CS.connection_ended = lambda: None
CS.setting_of = lambda role: {}
CS.on_change = lambda fn: None
said = []
HF._say = lambda text: said.append(text)
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + str(detail)[:300]))


def holder(hid):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(os.path.join(held, hid + ".sock"))
    return s


def fire(n, hid):
    return json.dumps({"hold": n, "hid": hid, "SensorType": "channel", "SensorName": "SD:setting.units",
                       "data": {"number": 100 + n}, "gone": False}).encode()


def main():
    C.SHARED_PORT = False
    ls = socket.socket(); ls.bind(("127.0.0.1", 0)); port = ls.getsockname()[1]; ls.close()
    d = C.FrameCarrier(bind_ip="127.0.0.1", server_port=port)
    c = C.FrameCarrier()
    d._say = c._say = lambda text: said.append(text)
    h = holder("daemon_main.py.1")

    c.send_request(("127.0.0.1", port), b"ask", 1, ram="units")          # this node names a sequence for units only
    m = d.recv(); d.send_reply(m.peer, m.id, b"answer")
    check("the connection carries a Role's request and its answer", c.recv().payload == b"answer")
    f1 = fire(1, "daemon_main.py.1")
    went = d._fire_to("127.0.0.1", f1)
    check("1. the fire is handed to the connection from that node, on the sequence it named for units",
          went is not None and "units" in went, went)
    got = h.recv(65536)                                                  # blocks until the fire arrives
    check("1. it arrives on the holder's socket, value for value", json.loads(got) == json.loads(f1), got)
    c.send_request(("127.0.0.1", port), b"ask again", 2, ram="units")
    m = d.recv(); d.send_reply(m.peer, m.id, b"answer again")
    r = c.recv()
    check("2. the connection is still up: the next request on it is answered", getattr(r, "payload", None) == b"answer again", repr(r))
    check("1. no connection ended", not any("ended" in t for t in said), [t for t in said if "ended" in t][:1])

    c.send_request(("127.0.0.1", port), b"plain", 3)                     # now a sequence for traffic of no Role too
    m = d.recv(); d.send_reply(m.peer, m.id, b"plain answer"); c.recv()
    f2 = fire(2, "daemon_main.py.1")
    went = d._fire_to("127.0.0.1", f2)
    check("3. with a sequence for traffic of no Role, the fire rides that one", went is not None and "units" not in went, went)
    check("3. and arrives", json.loads(h.recv(65536)) == json.loads(f2))

    check("4. a node with no connection here: nothing takes the fire", d._fire_to("10.9.9.1", f1) is None)
    before = len(said)
    ok = HF.deliver_here(fire(3, "nobody.9"), "in this check")
    check("5. a fire for a holder that is not there: said, not raised", ok is False and any("no such holder here" in t for t in said[before:]), said[before:])
    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
