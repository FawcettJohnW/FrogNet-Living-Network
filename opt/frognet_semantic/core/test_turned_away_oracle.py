#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[A_NODE_TURNED_AWAY_DOES_NOT_KEEP_DIALLING_V1] oracle.

Found on John's network, 22 Nov 2026. Seattle7's file said to reach the ControlRam on chorus; the node the ControlRam
was on had been set back to tcp, so its daemon reset every framed connection. Seattle7 dialled it ten times a second.
The ten a second were this process's own reads: a connection's end was an event at which the settings were read, the
read went to the node that had just reset the connection, and its end was the next event.

Contracts:
  1. a destination that resets the connection before saying anything: the request fails, and its reason names the
     destination and says what to look at
  2. that end is NOT an event: the settings are not read because of it
  3. the destination is dialled once for that request, and once more for the next request: no dial of its own
  4. a destination that cannot be connected to at all fails with "no connection", not as a node refusing frames
  5. a connection the far end has spoken on IS an event when it ends, as before

No clock is read and nothing is polled: every wait is a blocking read.
"""
import os
import queue
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import channel_setting as CS  # noqa: E402
from core import channel_carrier as C  # noqa: E402
from core import chorus as _chorus  # noqa: E402,F401  (registers the protocol "chorus")
from core.homa_socket import RpcFailed, PeerConnectionEnded  # noqa: E402

read_at_an_end = []
CS.connection_ended = lambda: read_at_an_end.append(threading.current_thread().name)
CS.setting_of = lambda role: {}
CS.on_change = lambda fn: None

said = []
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        print("  FAIL  %s  %s" % (name, detail))


class Resets:
    """A daemon that was not told of the frame carrier: it takes the connection and resets it."""

    def __init__(self):
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(16)
        self.port = self.ls.getsockname()[1]
        self.dialled: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                s, _ = self.ls.accept()
            except OSError:
                return
            s.recv(len(C.WORD), socket.MSG_WAITALL)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()                                  # a reset
            self.dialled.put(1)


def take(c):
    """The carrier's next item; a failed request is raised by recv(), and is the item."""
    try:
        return c.recv()
    except Exception as e:
        return e


def ask(c, dest, payload, cookie):
    """One request and how it ended. A connection that was reset before the request was handed to it refuses the
    request (send_request raises); one reset after fails it (recv). Either way it is the request's one end. The
    notice to the sessions on that connection (PeerConnectionEnded) is not it."""
    try:
        c.send_request(dest, payload, cookie)
    except Exception as e:
        return e
    while True:                                        # each turn is a blocking read; it ends at the request's end
        got = take(c)
        if not isinstance(got, PeerConnectionEnded):
            return got


def main():
    far = Resets()
    c = C.FrameCarrier()
    c._say = lambda text: said.append(text)
    dest = ("127.0.0.1", far.port)

    got = ask(c, dest, b"one", 7)
    far.dialled.get()
    check("1. the request fails", isinstance(got, Exception), repr(got))
    why = str(got)
    check("1. its reason names the destination", "127.0.0.1:%d" % far.port in why, why)
    check("1. its reason says the far node does not take a framed connection, and what to look at",
          "does not take this node's framed connection" in why and "semantic_transport" in why, why)
    check("1. it does not say 'No route to host'", "No route to host" not in why, why)
    check("2. the settings were not read because of that end", read_at_an_end == [], repr(read_at_an_end))
    check("3. dialled once for one request", far.dialled.empty())
    check("3. said once, the 1st time", sum("1 time(s) running" in t for t in said) == 1, repr(said))

    got = ask(c, dest, b"two", 8)
    far.dialled.get()
    check("3. the next request dials again, and fails with the reason", isinstance(got, Exception)
          and "does not take" in str(got), repr(got))
    check("3. dialled once more, no dial of its own", far.dialled.empty())
    check("2. still no read made at an end", read_at_an_end == [])
    check("3. said the 2nd time", any("2 time(s) running" in t for t in said), repr(said))
    ask(c, dest, b"three", 9); far.dialled.get()
    check("3. not said the 3rd time (1st, 2nd, 4th, 8th...)", not any("3 time(s) running" in t for t in said))

    far.ls.shutdown(socket.SHUT_RDWR); far.ls.close()        # wakes its accept: the port is closed for good
    e = ask(c, dest, b"four", 10)
    check("4. nothing listening: the request fails, and is NOT said to be a node that does not take frames",
          "no connection to" in str(e) and "does not take" not in str(e), repr(e))
    check("4. and that is not an event either", read_at_an_end == [])

    # 5. a real daemon end: it answers, then its connection ends
    C.SHARED_PORT = False
    ls = socket.socket(); ls.bind(("127.0.0.1", 0)); port = ls.getsockname()[1]; ls.close()
    d = C.FrameCarrier(bind_ip="127.0.0.1", server_port=port)
    d._say = lambda text: said.append("D " + text)
    c2 = C.FrameCarrier()
    c2._say = lambda text: said.append(text)
    rid = c2.send_request(("127.0.0.1", port), b"ask", 11)
    m = take(d)
    d.send_reply(m.peer, m.id, b"answer")
    r = take(c2)
    check("5. a daemon that takes frames answers", getattr(r, "payload", None) == b"answer", repr(r))
    before = read_at_an_end.count("MainThread")      # (the daemon end's own end is its event, on its own thread)
    link = list(c2._links.values())[0]
    c2._ended(link, "the far end closed the connection")
    check("5. a connection the far end spoke on: its end is an event", read_at_an_end.count("MainThread") == before + 1, repr(read_at_an_end))
    check("5. and it is not counted as turned away", c2._turned_away == {}, repr(c2._turned_away))

    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
