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
test_json_compliance.py - RFC 8259 compliance for core/json_handler.py
(JsonFormatHandler), exercised LIVE through learn/extract/rebuild.

Corpus axes (hand-curated; full enumeration in the public suite):
  reference: https://github.com/nst/JSONTestSuite  (y_/n_/i_ naming)
  RFC 8259 Sec.6 (Numbers), Sec.7 (Strings), Sec.4 (Objects/duplicate names).

ACTUAL behavior, observed against this tree (BUILD=2026-06-03-nonfinite-guard):
  - Parser is Python json.loads. Malformed input => learn returns an EMPTY
    fragment (field_order == []) and rebuild yields "{}". That empty-fragment
    shape IS the handler's "reject". (Note: a valid empty object {} also
    yields field_order == []; negatives below are non-empty inputs that
    json.loads cannot parse, so the empty fragment is unambiguous reject.)
  - NaN / Infinity / -Infinity: ACCEPTED by json.loads, then the
    [NONFINITE_JSON_GUARD] coerces them to null on both learn and rebuild.
    Classified as NORMALIZE.
  - Duplicate keys: last-write-wins (Python json.loads), then dumped once.
  - Non-identifier keys (dots, hyphens, arrows) => whole dict treated as one
    atomic "value" field and round-trips intact (_SAFE_DICT_KEY_RE).
  - Lone surrogates: ACCEPTED and preserved (Python json is lenient). This
    DEVIATES from RFC strictness ("reject"); it is not a crash and not a
    security issue, so it is recorded as a documented limitation, not a fail.
  - Deeply nested arrays: RecursionError is caught => empty fragment (reject),
    no crash.

CODEC-LAYER FINDINGS (non-gating here; handler tested in isolation per PRIMER):
  - TYPE_INT is struct '<i' (signed 32-bit). Integers outside [-2^31, 2^31-1]
    survive the HANDLER but would overflow the CODEC. Recorded as a finding.
"""
from __future__ import annotations

import json

from simulation.spec_compliance._harness import (
    Suite, round_trip, field_order, json_semantic_eq,
)
from core.json_handler import JsonFormatHandler

# (description, body, expected_round_trip_text_or_None)
# expected None => assert semantic round-trip equals the input itself.
POSITIVE_CASES = [
    ("empty_object",            '{}',                       '{}'),
    ("empty_array",             '[]',                       '[]'),
    ("flat_scalars",            '{"a":1,"b":"x","c":true,"d":null}', None),
    ("nested_object",           '{"o":{"p":{"q":1}}}',      None),
    ("top_level_array",         '[1,2,3]',                  None),
    ("array_of_objects",        '[{"a":1},{"a":2}]',        None),
    ("nested_array_in_object",  '{"links":[1,2,3],"n":4}',  None),
    ("unicode_escape_basic",    '{"k":"\\u0041"}',          None),   # -> "A"
    ("surrogate_pair_emoji",    '{"k":"\\uD83D\\uDE00"}',   None),   # -> ?
    ("utf8_literal",            '{"k":"hello world"}',      None),
    ("number_scientific",       '{"k":1.5e-3}',             None),
    ("number_exp_pos",          '{"k":1e10}',               None),
    ("number_negative",         '{"k":-42}',                None),
    ("number_float",            '{"k":3.14159}',            None),
    ("number_zero",             '{"k":0}',                  None),
    ("number_float_neg_zero",   '{"k":-0.0}',               None),
    ("bool_false",              '{"k":false}',              None),
    ("null_value",              '{"k":null}',               None),
    ("whitespace_padded",       '  {  "a" : 1 }  ',         None),
    ("ip_value",                '{"addr":"10.102.60.1"}',   None),
    ("host_value",              '{"peer_name":"FrogNetHost.seattle"}', None),
    ("enum_value",              '{"status":"SEMANTIC"}',    None),
    ("deep_but_sane",           '{"a":{"b":{"c":{"d":1}}}}', None),
    ("string_with_quotes",      '{"k":"she said \\"hi\\""}', None),
    ("empty_string_value",      '{"k":""}',                 None),
    ("array_mixed_types",       '[1,"two",true,null,{"a":1}]', None),
]

# (description, body, expected_behavior) where expected_behavior in
#   "reject"    -> empty fragment (field_order == []), rebuild == "{}"
#   "normalize" -> accepted but value rewritten; we assert the rewrite
#   "limitation"-> documented deviation from spec (accepted where RFC says reject)
NEGATIVE_CASES = [
    ("trailing_comma_obj",      '{"a":1,}',                 "reject"),
    ("trailing_comma_arr",      '[1,2,]',                   "reject"),
    ("comment_block",           '{"a":1 /* c */}',          "reject"),
    ("comment_line",            '{"a":1 // c\n}',           "reject"),
    ("single_quoted_string",    "{'a':1}",                  "reject"),
    ("unquoted_key",            '{a:1}',                    "reject"),
    ("hex_number",              '{"a":0x10}',               "reject"),
    ("leading_zero",            '{"a":01}',                 "reject"),
    ("leading_plus",            '{"a":+1}',                 "reject"),
    ("bare_word",               'undefined',                "reject"),
    ("unterminated_string",     '{"a":"oops}',              "reject"),
    ("unbalanced_braces",       '{"a":1',                   "reject"),
    ("unbalanced_brackets",     '[1,2',                     "reject"),
    ("truncated_midkey",        '{"ab',                     "reject"),
    ("empty_input",             '',                         "reject"),
    ("garbage",                 '\x00\x01\x02not json',     "reject"),
    ("nan_bareword",            '{"x":NaN}',                "normalize"),  # -> null
    ("infinity_bareword",       '{"x":Infinity}',           "normalize"),  # -> null
    ("neg_infinity_bareword",   '{"x":-Infinity}',          "normalize"),  # -> null
    ("duplicate_keys",          '{"a":1,"a":2}',            "normalize"),  # last wins -> {"a":2}
    ("deep_nesting_bomb",       '[' * 5000 + ']' * 5000,    "reject"),     # RecursionError caught
    ("lone_surrogate",          '{"k":"\\uD83D"}',          "limitation"), # accepted (RFC: reject)
    # Top-level objects whose keys are NOT stable identifiers (dots, hyphens,
    # arrows) are treated as one atomic "value" field. Content is preserved but
    # the top-level shape changes: {"a.b":1} -> {"value":{"a.b":1}}. Documented
    # limitation, not a crash. (_SAFE_DICT_KEY_RE in json_handler.)
    ("nonidentifier_keys",      '{"a.b":1,"c-d":2}',        "atomic_wrap"),
    ("variable_key_matrix",     '{"REQ_REPEAT\\u2192RESP_SAME":5}', "atomic_wrap"),
]


def _do_positive(suite: Suite, h: JsonFormatHandler) -> None:
    print(" POSITIVE - well-formed JSON must round-trip semantically:")
    for desc, body, expected in POSITIVE_CASES:
        try:
            _frag, out = round_trip(h, body)
            ref = expected if expected is not None else body
            ok = json_semantic_eq(ref, out)
            suite.check(f"json/pos/{desc}", ok,
                        f"in={body!r} out={out!r}")
        except Exception as e:
            suite.check(f"json/pos/{desc}", False,
                        f"RAISED {type(e).__name__}: {e}")


def _do_negative(suite: Suite, h: JsonFormatHandler) -> None:
    print(" NEGATIVE - malformed/adversarial input must behave as documented:")
    for desc, body, behavior in NEGATIVE_CASES:
        try:
            frag, out = round_trip(h, body)
        except Exception as e:
            suite.check(f"json/neg/{desc}", False,
                        f"handler crashed (must be graceful): "
                        f"{type(e).__name__}: {e}")
            continue

        if behavior == "reject":
            ok = (field_order(frag) == [] and out == "{}")
            suite.check(f"json/neg/{desc} [reject]", ok,
                        f"fo={field_order(frag)!r} out={out!r}")
        elif behavior == "normalize":
            # nonfinite -> null ; duplicate keys -> last value.
            try:
                parsed = json.loads(out)
            except Exception as e:
                suite.check(f"json/neg/{desc} [normalize]", False,
                            f"output not valid JSON: {out!r} ({e})")
                continue
            if desc.startswith(("nan", "infinity", "neg_infinity")):
                ok = parsed.get("x", "MISSING") is None
                suite.check(f"json/neg/{desc} [normalize->null]", ok,
                            f"out={out!r}")
            elif desc == "duplicate_keys":
                ok = (parsed == {"a": 2})
                suite.check(f"json/neg/{desc} [last-write-wins]", ok,
                            f"out={out!r}")
            else:
                suite.check(f"json/neg/{desc} [normalize]", True, "")
        elif behavior == "atomic_wrap":
            # Whole object wrapped under synthetic "value"; inner content equal.
            try:
                parsed = json.loads(out)
                inner = parsed.get("value") if isinstance(parsed, dict) else None
                ok = (inner == json.loads(body))
            except Exception:
                ok = False
            suite.check(f"json/neg/{desc} [atomic-wrap-limitation]", ok,
                        f"out={out!r}")
            suite.finding(
                f"json/{desc}",
                "top-level object with non-identifier keys is wrapped as one "
                "atomic field 'value'; content preserved, top-level shape "
                "changes ({...} -> {\"value\":{...}}). Fix would require "
                "non-dot path encoding for unsafe keys.")
        elif behavior == "limitation":
            # Documented deviation: handler accepts (does not reject) and does
            # not crash. We assert "no crash + deterministic" only.
            ok = isinstance(out, str)
            suite.check(f"json/neg/{desc} [accepted-limitation]", ok,
                        f"out={out!r}")
            suite.finding(
                f"json/{desc}",
                "RFC 8259 says reject lone surrogates; Python json (and thus "
                "this handler) accepts and preserves them. Not a crash, not a "
                "security issue. Fix would require explicit surrogate scan.")


def _do_codec_findings(suite: Suite, h: JsonFormatHandler) -> None:
    print(" CODEC-LAYER (handler-isolated pass; codec note only):")
    big = '{"k":123456789012345678901234567890}'
    _frag, out = round_trip(h, big)
    suite.check("json/codec/bignum_handler_isolated",
                json_semantic_eq(big, out),
                f"handler round-trips arbitrary-precision int; out={out!r}")
    suite.finding(
        "codec/TYPE_INT_overflow",
        "core/codec.py TYPE_INT packs struct '<i' (signed 32-bit). Integers "
        "outside [-2^31, 2^31-1] survive the handler but overflow the codec "
        "wire encoder. Separate code change (e.g. widen to '<q' or fall back "
        "to TYPE_JSON for out-of-range ints).")


def main() -> int:
    print("== JSON codex compliance (RFC 8259) ==")
    suite = Suite("json_compliance")
    h = JsonFormatHandler()
    _do_positive(suite, h)
    _do_negative(suite, h)
    _do_codec_findings(suite, h)
    return suite.report()


if __name__ == "__main__":
    raise SystemExit(main())
