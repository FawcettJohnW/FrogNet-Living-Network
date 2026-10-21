#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""test_coalesce_per_peer_oracle - [COALESCE_PER_PEER_V1].

The daemon coalesces identical requests in flight (FROGNET_DAEMON_COALESCE_WAIT > 0): a request whose req_hash is
already executing waits for that execution and is handed its reply. A reply is a difference against the reference of
the peer it was made for (_diff_encode_response, keyed (peer_ip, opcode, instance)). Handed to a DIFFERENT peer, it is a
difference against a reference that peer does not hold: the peer fills the fields the difference left out from its own
reference and gets the wrong value -- and the daemon's reference for that peer never moves.

Drives the real SemanticSession._process_frame_inner from two peers at once, with a dispatch that takes a moment (so
the second request finds the first in flight), and decodes each peer's reply against that peer's own reference:
  1. each peer's reply decodes to the new value
  2. the daemon's reference for each peer is the new value
"""
import os, sys, threading, time, types
os.environ["FROGNET_DAEMON_COALESCE_WAIT"] = "5"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.semcache_wire import wrap_req_repeat
import daemon.engine.session as S

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

OPC, INST = 4242, "inst-1"
ORDER = ["f", "g", "h"]
TYPES = {"f": "str", "g": "int", "h": "str"}
NEW = [("f", "y"), ("g", 2), ("h", "kept")]
PEERS = {"10.0.0.1": {"f": "x", "g": 1, "h": "kept"},       # peer 1 holds h = the new h
         "10.0.0.2": {"f": "x", "g": 1, "h": "OLD"}}        # peer 2 holds an older h

def main():
    print("=== COALESCE PER PEER ORACLE ===")
    for peer, ref in PEERS.items():
        S._set_response_reference(peer, OPC, INST, dict(ref))
    started = threading.Event()

    def slow_dispatch(self, msg, emit=None, prepared=None):
        started.set(); time.sleep(0.5)                       # the second peer's request arrives meanwhile
        return S._diff_encode_response(self.peer_ip, OPC, INST, list(NEW), TYPES)
    S.SemanticSession._dispatch_fnw1 = slow_dispatch

    frame = wrap_req_repeat(b"\x11" * 16)
    replies = {}
    def run(peer, seq):
        s = object.__new__(S.SemanticSession); s.peer_ip = peer
        _seq, reply, _w = s._process_frame_inner(frame, seq, len(frame))
        replies[peer] = reply
    t1 = threading.Thread(target=run, args=("10.0.0.1", 1)); t1.start()
    started.wait(5)
    t2 = threading.Thread(target=run, args=("10.0.0.2", 2)); t2.start()
    t1.join(); t2.join()

    codec = S._get_codec()
    tpl = types.SimpleNamespace(fragment={"field_order": ORDER})
    for peer, ref in PEERS.items():
        got = dict(codec.decode_reply(replies[peer], tpl, None, reference=dict(ref)))
        check(f"{peer}: its reply decodes against its own reference to the new value (got {got})", got == dict(NEW))
        now = S._get_response_reference(peer, OPC, INST)
        check(f"{peer}: the daemon's reference for it is the new value (is {now})", now == dict(NEW))
    print(f"COALESCE-PER-PEER {'FAIL' if FAILS else 'PASS'}: {len(FAILS)} fail")
    return 1 if FAILS else 0

if __name__ == "__main__":
    sys.exit(main())
