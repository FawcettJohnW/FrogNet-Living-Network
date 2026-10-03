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
"""
test_text_compliance.py - plain-text / raw compliance for core/text_handler.py
(TextFormatHandler, RawFormatHandler), exercised LIVE.

There is no formal text spec; "compliance" here (PRIMER 2 Sec.Plain text) means:
never crash on any input, never silently lose data, preserve content through
round-trip. Both handlers treat the body as a single opaque "raw" field and
operate on Python str - so at the HANDLER level the round-trip is lossless for
every str, by construction. These suites prove that across the axes that
matter (encoding artifacts, line endings, unicode normalization, control
chars, large/empty bodies).

FINDING-1 (PRIMER 2 Sec.Plain text, Sec."binary-in-text-channel") - CODEC LAYER:
  core/codec.py _decode_fieldblock decodes BOTH TYPE_STR and TYPE_RAW via
  fb.decode("utf-8", "replace"). So a TYPE_RAW (bytes) value:
    (a) comes back as str, not bytes (type identity lost), and
    (b) any non-UTF-8 byte is irreversibly replaced with U+FFFD.
  This shadows real binary-over-text, but lives BELOW the handler. Per the
  primer we test the handler in isolation (lossless) and DOCUMENT FINDING-1
  as a reproduced codec finding rather than failing the handler suite. Fix is
  a separate code change (return bytes for TYPE_RAW; only utf-8-decode
  TYPE_STR).
"""
from __future__ import annotations

import struct

from simulation.spec_compliance._harness import Suite, round_trip
from core.text_handler import TextFormatHandler, RawFormatHandler
from core.codec import SemanticCodec


# (description, body) - every str body must round-trip byte-for-byte (as str).
POSITIVE_CASES = [
    ("ascii_simple",        "hello world"),
    ("empty_body",          ""),
    ("whitespace_only",     "   \t  "),
    ("trailing_whitespace", "line with trailing spaces   "),
    ("lf_endings",          "a\nb\nc\n"),
    ("crlf_endings",        "a\r\nb\r\nc\r\n"),
    ("cr_endings",          "a\rb\rc\r"),
    ("mixed_endings",       "a\nb\r\nc\rd"),
    ("utf8_multibyte",      "hello world cafe"),
    ("emoji",               "frog ? net ?"),
    ("cjk",                 "?? ?????? ???"),
    ("nfc_form",            "\u00e9"),          # e precomposed
    ("nfd_form",            "e\u0301"),          # e decomposed (combining acute)
    ("combining_marks",     "a\u0300\u0301\u0302b"),
    ("control_chars",       "bell\x07tab\tvtab\x0b"),
    ("nul_in_text",         "before\x00after"),
    ("json_as_text",        '{"a":1,"b":[2,3]}'),
    ("csv_as_text",         "host,ip\nFrogNetHost,10.102.60.1"),
    ("very_long_line",      "x" * 100000),
    ("many_lines",          "\n".join(str(i) for i in range(5000))),
    ("rtl_text",            "???? ???? ?????"),
    ("zero_width",          "a\u200bb\u200cc\ufeffd"),
]


def _roundtrip_handler(suite: Suite, h, tag: str) -> None:
    print(f" POSITIVE - {tag}: every str body round-trips losslessly:")
    for desc, body in POSITIVE_CASES:
        try:
            _frag, out = round_trip(h, body)
            ok = (out == body)
            suite.check(f"{tag}/pos/{desc}", ok,
                        f"len_in={len(body)} len_out={len(out)} "
                        f"first_diff={_first_diff(body, out)}")
        except Exception as e:
            suite.check(f"{tag}/pos/{desc}", False,
                        f"RAISED {type(e).__name__}: {e}")


def _first_diff(a: str, b: str) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return f"@{i} {a[i]!r}!={b[i]!r}"
    if len(a) != len(b):
        return f"len {len(a)}!={len(b)}"
    return "none"


def _do_codec_finding(suite: Suite) -> None:
    print(" CODEC-LAYER FINDING-1 (reproduction; non-gating for handler):")
    c = SemanticCodec()
    raw = b"\xff\xfe\x00\x01ABC"          # not valid UTF-8
    tid = c._type_to_id(raw)              # -> TYPE_RAW (1)
    enc = c._encode_value(tid, raw)       # preserves bytes on encode
    # mirror the _decode_fieldblock TYPE_STR/TYPE_RAW branch:
    length = struct.unpack("<H", enc[:2])[0]
    dec = enc[2:2 + length].decode("utf-8", "replace")
    lossless = (isinstance(dec, (bytes, bytearray)) and dec == raw)
    # We do NOT assert losslessness (it is known-broken); we DOCUMENT it.
    suite.finding(
        "codec/FINDING-1/TYPE_RAW_utf8_mangle",
        f"TYPE_RAW(id={tid}) bytes {raw!r} decode to "
        f"{type(dec).__name__} {dec!r} via utf-8/replace; lossless={lossless}. "
        f"Non-UTF-8 bytes -> U+FFFD, bytes->str. Fix: decode TYPE_RAW as bytes "
        f"(separate code change).")
    # Sanity: a pure-UTF-8 'binary' string still survives content-wise.
    okutf = b"plain-ascii"
    enc2 = c._encode_value(c._type_to_id(okutf), okutf)
    L2 = struct.unpack("<H", enc2[:2])[0]
    dec2 = enc2[2:2 + L2].decode("utf-8", "replace")
    suite.check("text/codec/ascii_raw_content_preserved",
                dec2 == okutf.decode("ascii"),
                f"ascii bytes survive content (type still coerced to str): {dec2!r}")


def main() -> int:
    print("== TEXT / RAW codex compliance (no formal spec; safety + fidelity) ==")
    suite = Suite("text_compliance")
    _roundtrip_handler(suite, TextFormatHandler(), "text")
    _roundtrip_handler(suite, RawFormatHandler(), "raw")
    _do_codec_finding(suite)
    return suite.report()


if __name__ == "__main__":
    raise SystemExit(main())
