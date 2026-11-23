#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""
test_reply_order_oracle.py - [ONE_WRITER_OWNS_THE_WIRE_V1] what a session's reader does with replies, in order.

The proxy's references move on the two threads that own a session's wire: the writer commits a request reference as it
builds the frame, the reader applies each reply as it arrives (proxy/transport_semantic.py, _ReplyOrder). There is no
lock and no gate. One reply cannot be applied on arrival: a RESP_SAME naming an answer the proxy no longer holds. The
reader fetches that answer and holds every later reply TO THAT INSTANCE, in order, until it is in.

Real: TS._ReplyOrder, TS._Rpc, TS._InLine, the request-clear queue. Substituted: the wire (replies are handed to
arrived() by this file, in the order it chooses) and the reply step (on_reply records what it was given).

  A  replies to different instances are applied as they arrive, each exactly once
  B  after a reply that needs a fetch: the fetch is sent once; later replies to that instance are held, replies to
     other instances are not; when the fetch's answer arrives the first reply is applied with it, then the held
     replies in the order they arrived
  C  a held reply that itself needs a fetch holds the ones behind it again, in order
  D  the fetch ends without an answer: the first reply is handed up saying so, the held replies are applied in order
  E  the reader ends: everything held fails with the reason, nothing waits
  F  a request reference cleared from another thread is gone before the writer's next build for that target
  G  _InLine (the oracles' stand-in session) builds on submit, applies the reply, and fetches in line

Run:  /root/.frogsim/venv/bin/python3 proxy/test_reply_order_oracle.py
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("FROGNET_PROXY_ROOT", os.path.dirname(HERE))

from proxy import transport_semantic as TS      # noqa: E402

FAILS = []


def check(ok, what):
    print("  [%s] %s" % ("PASS" if ok else "FAIL", what), flush=True)
    if not ok:
        FAILS.append(what)


class Reader:
    """One session's reader, with the wire in this file's hands."""

    def __init__(self):
        self.applied, self.sent, self.handed = [], [], []
        self.order = TS._ReplyOrder(self.send_after, self.finish)

    def send_after(self, packet, after):
        rpc = TS._Rpc(packet, max_attempts=1)
        rpc.after = after
        self.sent.append(rpc)
        return rpc

    def finish(self, rpc, frame):
        rpc.reply = frame
        rpc.done.set()
        self.handed.append(rpc)

    def request(self, name, key, fetch_on=()):
        def on_reply(frame, second=None):
            self.applied.append((name, frame, second))
            if second is None and frame in fetch_on:
                return {"msg": None, "fetch": b"i" * 16}
            return {"msg": None, "name": name, "second": second}
        rpc = TS._Rpc(b"req-" + name.encode(), max_attempts=1, on_reply=on_reply, key=key)
        return rpc


def main():
    K1, K2 = ("10.1.1.1", 7, "a"), ("10.1.1.1", 7, "b")

    print("A  replies to different instances")
    r = Reader()
    a, b = r.request("a", K1), r.request("b", K2)
    r.order.arrived(b, b"B1"); r.order.arrived(a, b"A1")
    check(r.applied == [("b", b"B1", None), ("a", b"A1", None)] and a.done.is_set() and b.done.is_set()
          and a.prepared["name"] == "a" and r.sent == [], "each applied once, as it arrived, and handed up")

    print("B  a reply that needs a fetch holds its instance, and only its instance")
    r = Reader()
    a1, a2, a3, b1 = r.request("a1", K1, fetch_on=(b"SAME",)), r.request("a2", K1), r.request("a3", K1), r.request("b1", K2)
    r.order.arrived(a1, b"SAME")
    check(len(r.sent) == 1 and r.sent[0].after is a1 and not a1.done.is_set(), "the fetch was sent once and the reply is not handed up yet")
    r.order.arrived(a2, b"A2"); r.order.arrived(b1, b"B1"); r.order.arrived(a3, b"A3")
    check(not a2.done.is_set() and not a3.done.is_set() and b1.done.is_set(), "later replies to that instance are held; another instance's is not")
    r.order.arrived(r.sent[0], b"ANSWER")
    check(r.applied == [("a1", b"SAME", None), ("b1", b"B1", None), ("a1", b"SAME", b"ANSWER"), ("a2", b"A2", None), ("a3", b"A3", None)],
          "the first reply was applied with the fetched answer, then the held replies in arrival order")
    check(all(x.done.is_set() for x in (a1, a2, a3)) and a1.prepared["second"] == b"ANSWER" and r.handed.index(a1) < r.handed.index(a2) < r.handed.index(a3),
          "and they were handed up in that order")

    print("C  a held reply that needs a fetch holds the ones behind it")
    r = Reader()
    a1, a2, a3 = r.request("a1", K1, fetch_on=(b"SAME",)), r.request("a2", K1, fetch_on=(b"SAME2",)), r.request("a3", K1)
    r.order.arrived(a1, b"SAME"); r.order.arrived(a2, b"SAME2"); r.order.arrived(a3, b"A3")
    r.order.arrived(r.sent[0], b"ANS1")
    check(a1.done.is_set() and not a2.done.is_set() and not a3.done.is_set() and len(r.sent) == 2 and r.sent[1].after is a2,
          "the second fetch went out and a3 is still held")
    r.order.arrived(r.sent[1], b"ANS2")
    check([x[0] for x in r.applied] == ["a1", "a1", "a2", "a2", "a3"] and a3.done.is_set(), "then all were applied in order")

    print("D  the fetch ends without an answer")
    r = Reader()
    a1, a2 = r.request("a1", K1, fetch_on=(b"SAME",)), r.request("a2", K1)
    r.order.arrived(a1, b"SAME"); r.order.arrived(a2, b"A2")
    r.order.look()
    check(not a1.done.is_set(), "nothing is given up while the fetch is outstanding")
    r.sent[0].err = TimeoutError("no answer"); r.sent[0].done.set()
    r.order.look()
    check(a1.done.is_set() and isinstance(a1.prepared.get("fetch_failed"), TimeoutError) and a2.done.is_set() and r.applied[-1] == ("a2", b"A2", None),
          "the first reply is handed up saying the fetch failed and the held reply is applied")

    print("E  the reader ends")
    r = Reader()
    a1, a2 = r.request("a1", K1, fetch_on=(b"SAME",)), r.request("a2", K1)
    r.order.arrived(a1, b"SAME"); r.order.arrived(a2, b"A2")
    r.order.ended(ConnectionError("gone"))
    check(a1.done.is_set() and a2.done.is_set() and isinstance(a1.err, ConnectionError) and isinstance(a2.err, ConnectionError),
          "everything held fails with the reason")

    print("F  a request reference cleared from another thread")
    TS._set_request_reference("10.9.9.9", 5, "x", {"f": 1})
    t = threading.Thread(target=TS._clear_request_reference, args=("10.9.9.9", 5, "x")); t.start(); t.join()
    check(TS._get_request_reference("10.9.9.9", 5, "x") == {"f": 1}, "is still there until the writer takes the clears (the writer owns it)")
    TS._take_request_clears("10.9.9.9")
    check(TS._get_request_reference("10.9.9.9", 5, "x") is None, "and is gone once it has")

    print("G  the stand-in session")
    seen = []
    def answer(packet):
        seen.append(packet)
        return b"ANSWER" if packet == TS.wrap_req_same_miss(b"i" * 16) else b"SAME"
    w = TS._InLine(answer)
    got = []
    def on_reply(frame, second=None):
        got.append((frame, second))
        return {"msg": None, "fetch": b"i" * 16} if second is None else {"msg": None, "second": second}
    built = []
    def build():
        built.append(1)
        return b"built-frame"
    rpc = w.submit(build, on_reply, K1)
    check(built == [1] and seen[0] == b"built-frame" and got == [(b"SAME", None), (b"SAME", b"ANSWER")] and w.wait(rpc) == b"SAME"
          and rpc.prepared["second"] == b"ANSWER", "built the frame once, applied the reply and fetched the named answer in line")

    print("\nREPLY-ORDER ORACLE: %s" % ("ALL CHECKPOINTS PASS" if not FAILS else "FAILED: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
