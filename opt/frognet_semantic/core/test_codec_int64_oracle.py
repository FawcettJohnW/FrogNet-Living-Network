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
test_codec_int64_oracle.py - TYPE_INT must carry signed 64-bit values.

Regression for the int32 wire-overflow that silently killed remote telemetry:
a sensor field like local_bytes_resp=10809417555 (10.8B, > int32 and > uint32)
could not be encoded, the remote write failed, and the sensor row never landed
on the databasehost -> blank monitor panel.

Oracle contract (fail-on-old / pass-on-new):
  * FAILS on the int32 codec (`<i`): encoding 10809417555 raises / overflows.
  * PASSES on the int64 codec (`<q`): every value below round-trips exactly.
  * Fail-loud is preserved at the int64 boundary (> 2**63-1 still raises).

Exercises the real codec encode/decode pair (no toy reimplementation).
Exit 0 = pass, nonzero = fail.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.codec import SemanticCodec  # noqa: E402

# The value from the live Seattle5 Engine payload that started this, plus the
# boundaries that matter.
ROUND_TRIP_VALUES = [
    0,
    -5,
    2_147_483_647,          # int32 max  (old ceiling)
    2_147_483_648,          # int32 max + 1  (old code died here)
    -2_147_483_649,         # int32 min - 1
    4_294_967_296,          # uint32 + 1  (so it isn't just an unsigned widening)
    10_809_417_555,         # the real local_bytes_resp from the log
    -10_809_417_555,
    9_223_372_036_854_775_807,    # int64 max
    -9_223_372_036_854_775_808,   # int64 min
]

OVER_INT64 = 9_223_372_036_854_775_808   # int64 max + 1 -> must still raise


def _round_trip(codec, value):
    """Encode value as a single TYPE_INT field and decode it back."""
    fb = struct.pack("<HB", 0, codec.TYPE_INT) + codec._encode_value(codec.TYPE_INT, value)
    out = codec._decode_fieldblock(fb, 1)
    return out[0]


def main():
    codec = SemanticCodec()
    failures = []

    for v in ROUND_TRIP_VALUES:
        try:
            got = _round_trip(codec, v)
        except Exception as e:
            failures.append(f"value {v!r} failed to round-trip: {e!r}")
            continue
        if got != v:
            failures.append(f"value {v!r} round-tripped to {got!r}")

    # Fail-loud preserved beyond int64.
    try:
        codec._encode_value(codec.TYPE_INT, OVER_INT64)
        failures.append(f"value {OVER_INT64!r} (> int64) was encoded instead of raising")
    except (ValueError, struct.error):
        pass  # correct: still refuses what the wire genuinely cannot carry

    if failures:
        print("FAIL test_codec_int64_oracle:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(f"PASS test_codec_int64_oracle: {len(ROUND_TRIP_VALUES)} values round-trip, "
          f"int64 boundary still fails loud (incl. 10809417555)")
    sys.exit(0)


if __name__ == "__main__":
    main()
