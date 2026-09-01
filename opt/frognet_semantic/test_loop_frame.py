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
"""[LOOP_DETECT_9009_V1] Standalone proof of the OP_RTT_LOOP round-trip and the
daemon-decision / client-decode logic. No socket, no sim - just the wire + the
two ends' decision rules. Run: PYTHONPATH=<frognet_semantic> python3 test_loop_frame.py
"""
from core.semcache_wire import wrap_rtt_loop, try_parse, OP_RTT_LOOP, op_name

def test_wire_roundtrip():
    m = try_parse(wrap_rtt_loop())
    assert m is not None and m.op == OP_RTT_LOOP
    assert op_name(OP_RTT_LOOP) == "RTT_LOOP"

def _daemon_hello(return_ip, local_ips):
    # mirrors daemon/engine/server.py normal-HELLO short-circuit
    if return_ip and return_ip in local_ips:
        return wrap_rtt_loop()
    return b"\x00PONG-placeholder"

def _client_decode(frame):
    # mirrors discovery/real_backends.py RealVerify._ping_pong
    m = try_parse(frame)
    if m is not None and m.op == OP_RTT_LOOP:
        return "LOOP"
    return "rtt-or-None"

def test_hairpin_vs_remote():
    local = {"10.111.11.1"}                      # this node (BABox)
    # candidate route hairpins back to us -> our daemon sees our own IP -> LOOP
    assert _client_decode(_daemon_hello("10.111.11.1", local)) == "LOOP"
    # clean remote dest -> our IP is not local there -> normal reply
    assert _client_decode(_daemon_hello("10.250.250.1", local)) == "rtt-or-None"

if __name__ == "__main__":
    test_wire_roundtrip()
    test_hairpin_vs_remote()
    print("LOOP_DETECT_9009_V1: wire round-trip + hairpin/remote decode PASS")
