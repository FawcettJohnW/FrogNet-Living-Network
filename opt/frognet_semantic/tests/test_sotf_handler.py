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
# tests/test_sotf_handler.py - Smoke test for the SotF codex.
#
# Run from anywhere AFTER copying core/sotf_handler.py into place:
#
#   cd /opt/frognet_semantic
#   python3 tests/test_sotf_handler.py
#
# Verifies:
#   1. Handler instantiates and exposes the production FormatHandler
#      interface (learn_*_template, extract_*_dynamic, rebuild_reply).
#   2. Round-trip through the REAL SemanticCodec (encode_request +
#      decode_request) preserves the session-scoped fields and the
#      base64-encoded media payload.
#   3. encode_request_diff against a per-session reference shrinks
#      subsequent frames (REQ_DIFF case).
#   4. Identical-content frames produce the is_identical flag set
#      (REQ_REPEAT case - handler-level confirmation; wire wrapping
#      is the proxy/daemon's job).
#   5. The looks_like_sotf sniffer hook returns True for SotF bodies
#      and False for plain JSON / text / non-JSON inputs.
#
# Exit code 0 on all green; 1 on any FAIL.

from __future__ import annotations

import base64
import json
import os
import sys
import types

# Make the core/ package importable from wherever we're invoked.
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
for p in (PARENT, HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)


# Production codec imports lz4.frame.  On a working FrogNet install
# this is real; standalone test boxes may not have it.  Install a stub
# only if missing.
def _install_lz4_stub_if_missing():
    if "lz4" in sys.modules:
        return
    try:
        import lz4.frame  # noqa: F401
        return
    except ImportError:
        pass
    import zlib
    lz4 = types.ModuleType("lz4")
    frame = types.ModuleType("lz4.frame")
    frame.compress = lambda d: zlib.compress(d, 1)
    frame.decompress = lambda d: zlib.decompress(d)
    lz4.frame = frame
    sys.modules["lz4"] = lz4
    sys.modules["lz4.frame"] = frame


_install_lz4_stub_if_missing()

from core.sotf_handler import SotFMediaHandler, looks_like_sotf
from core.codec import SemanticCodec


FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {detail}"))
    if not ok:
        FAILS.append(name)


def _make_envelope(seq: int, payload_bytes: bytes,
                    session_id: str = "sotf-smoke-1",
                    codec: str = "opus",
                    sr: int = 48000,
                    layout: str = "stereo",
                    level_idx: int = 4) -> str:
    return json.dumps({
        "_sotf": 1,
        "session_id": session_id,
        "codec": codec,
        "sr": sr,
        "layout": layout,
        "level_idx": level_idx,
        "seq": seq,
        "payload": base64.b64encode(payload_bytes).decode("ascii"),
    }, separators=(",", ":"))


def main() -> int:
    print("=== SotF codex smoke test ===")
    handler = SotFMediaHandler()
    codec = SemanticCodec()

    # ----- 1. Interface --------------------------------------------------
    print("\n[1] handler exposes production FormatHandler interface")
    for name in ("learn_request_template", "learn_reply_template",
                 "extract_request_dynamic", "extract_reply_dynamic",
                 "rebuild_reply"):
        check(f"has method {name}", callable(getattr(handler, name, None)))

    # ----- 2. Template + round-trip via SemanticCodec --------------------
    print("\n[2] template learning + codec round-trip")
    payload0 = bytes(range(60))  # deterministic binary
    body0 = _make_envelope(seq=0, payload_bytes=payload0)

    fragment = handler.learn_request_template(body0)
    check("fragment.mode is sotf_media", fragment.get("mode") == "sotf_media",
          f"got {fragment.get('mode')!r}")
    check("baseline.session_id captured",
          fragment["baseline"]["session_id"] == "sotf-smoke-1")
    check("payload mapped as 'string' (FINDING-1 workaround)",
          fragment["type_map"]["payload"] == "string")

    dyn = handler.extract_request_dynamic(body0, fragment)
    check("extract returns list-of-tuples", isinstance(dyn, list) and
          all(isinstance(x, tuple) and len(x) == 2 for x in dyn))
    check("field_order matches", [k for k, _ in dyn] == fragment["field_order"])

    # Real SemanticCodec round-trip
    encoded = codec.encode_request(
        opcode=0xC0DEC701,
        url_vals=[], json_vals=dyn,
        type_map=fragment["type_map"],
        tokens=fragment["tokens"],
        compress=True,
    )
    check("encoded payload non-empty", encoded and len(encoded) > 0)

    req_tpl = types.SimpleNamespace(
        url_query_keys=[], fragment=fragment,
    )
    _, decoded = codec.decode_request(
        encoded, req_tpl, tokens=fragment["tokens"],
    )
    decoded_dict = {k: v for (k, v) in decoded}
    check("session_id round-tripped",
          decoded_dict.get("session_id") == "sotf-smoke-1")
    check("codec field round-tripped",
          decoded_dict.get("codec") == "opus")
    check("sr round-tripped as int",
          decoded_dict.get("sr") == 48000)
    check("seq round-tripped",
          decoded_dict.get("seq") == 0)

    rebuilt = handler.rebuild_reply(fragment, decoded)
    check("rebuilt body parses as JSON",
          isinstance(json.loads(rebuilt), dict))
    rebuilt_payload = handler.decode_payload(rebuilt)
    check("payload bytes round-tripped byte-identical",
          rebuilt_payload == payload0,
          f"got {len(rebuilt_payload)}B, expected {len(payload0)}B")

    # ----- 3. encode_request_diff shrinks subsequent frames --------------
    print("\n[3] diff against per-session reference")
    payload1 = bytes(b for b in range(1, 61))
    body1 = _make_envelope(seq=1, payload_bytes=payload1)
    dyn1 = handler.extract_request_dynamic(body1, fragment)

    full1 = codec.encode_request(
        opcode=0xC0DEC701,
        url_vals=[], json_vals=dyn1,
        type_map=fragment["type_map"],
        tokens=fragment["tokens"],
        compress=True,
    )
    reference = {k: v for (k, v) in dyn}  # reference = the previous frame's values
    diff1, _new_ref, is_identical = codec.encode_request_diff(
        opcode=0xC0DEC701,
        url_vals=[], json_vals=dyn1,
        type_map=fragment["type_map"],
        reference=reference,
        tokens=fragment["tokens"],
        compress=True,
    )
    check("diff frame smaller than full frame",
          len(diff1) < len(full1),
          f"diff={len(diff1)} full={len(full1)}")
    check("is_identical False when payload changed",
          is_identical is False)

    # ----- 4. Identical content collapses to is_identical ----------------
    print("\n[4] identical content -> is_identical True (REQ_REPEAT case)")
    dyn_same = handler.extract_request_dynamic(body0, fragment)  # same as frame 0
    reference_same = {k: v for (k, v) in dyn_same}
    _, _, is_identical_same = codec.encode_request_diff(
        opcode=0xC0DEC701,
        url_vals=[], json_vals=dyn_same,
        type_map=fragment["type_map"],
        reference=reference_same,
        tokens=fragment["tokens"],
        compress=True,
    )
    check("identical content sets is_identical True",
          is_identical_same is True)

    # ----- 5. Sniffer hook ----------------------------------------------
    print("\n[5] looks_like_sotf sniffer hook")
    check("looks_like_sotf accepts SotF envelope", looks_like_sotf(body0))
    check("looks_like_sotf rejects plain JSON",
          not looks_like_sotf('{"a":1,"b":2}'))
    check("looks_like_sotf rejects non-JSON text",
          not looks_like_sotf("not json"))
    check("looks_like_sotf rejects empty",
          not looks_like_sotf(""))
    check("looks_like_sotf rejects JSON without marker",
          not looks_like_sotf('{"session_id":"x","seq":0}'))
    check("looks_like_sotf rejects wrong marker value",
          not looks_like_sotf('{"_sotf":0,"seq":0}'))

    print()
    if FAILS:
        print(f"FAILED: {FAILS}")
        return 1
    print("ALL SMOKE-TEST CHECKS PASS - SotF codex round-trips through real SemanticCodec.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
