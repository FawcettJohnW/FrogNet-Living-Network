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
"""test_engine_error_reason_oracle - [ENGINE_ERROR_PRESERVE_REASON_V1].

A template-less opcode makes the engine return encode_error_reply(503, "no templates for
opcode=..."). The session layer wraps that into an OP_ERROR for the proxy. The proxy only
re-bootstraps (relearns the template) when the OP_ERROR message contains "no templates for
opcode" (transport_semantic.py). The old session code flattened the reason to
"engine error status=503", so the proxy never re-bootstrapped and the opcode 503'd forever.
This drives the REAL codec + wire wrap + the proxy's exact trigger string.
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.codec import SemanticCodec
from core.semcache_wire import wrap_error, MAGIC, OP_ERROR

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def _proxy_parse_op_error(frame):
    # mirror the proxy's OP_ERROR parse: MAGIC + [OP_ERROR] + !HH(status,len) + msg
    assert frame[:4] == MAGIC and frame[4] == OP_ERROR, "not an OP_ERROR frame"
    status, n = struct.unpack("!HH", frame[5:9])
    return status, frame[9:9 + n].decode("utf-8", "replace")

def _triggers_rebootstrap(error_msg):
    # transport_semantic.py: if "no templates for opcode" in error_msg -> raw re-bootstrap
    return "no templates for opcode" in error_msg

def main():
    print("=== ENGINE ERROR REASON ORACLE ===")
    codec = SemanticCodec()

    # (round-trip) the codec helper recovers the exact (status, msg)
    blob = codec.encode_error_reply(503, "no templates for opcode=1234567")
    check("decode_error_reply round-trips (status, msg)",
          codec.decode_error_reply(blob) == (503, "no templates for opcode=1234567"))
    check("non-error frame decodes to None",
          codec.decode_error_reply(struct.pack("<BBIH", 5, 0, 42, 0)) is None)

    # OLD session behavior: flatten -> generic. proxy CANNOT recognize template miss.
    _, old_msg = _proxy_parse_op_error(wrap_error(503, "engine error status=503"))
    check("OLD generic message does NOT trigger re-bootstrap (the bug)",
          not _triggers_rebootstrap(old_msg))

    # NEW session behavior: decode engine reason, forward it. proxy re-bootstraps.
    er = codec.decode_error_reply(blob)
    new_frame = wrap_error(503, er[1] if er else "engine error status=503")
    status, new_msg = _proxy_parse_op_error(new_frame)
    check("NEW preserves 'no templates for opcode' -> proxy re-bootstraps",
          _triggers_rebootstrap(new_msg))
    check("NEW keeps the 503 status intact", status == 503)

    # a genuinely-transient engine error (not template miss) still does NOT re-bootstrap
    trans = codec.encode_error_reply(502, "resolve error: NXDOMAIN")
    er2 = codec.decode_error_reply(trans)
    _, tmsg = _proxy_parse_op_error(wrap_error(502, er2[1]))
    check("transient 502 reason preserved but does NOT trigger re-bootstrap",
          (tmsg == "resolve error: NXDOMAIN") and not _triggers_rebootstrap(tmsg))

    print("\n" + ("ALL ENGINE-ERROR-REASON CHECKS PASS" if not FAILS
                  else f"ENGINE-ERROR-REASON ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
