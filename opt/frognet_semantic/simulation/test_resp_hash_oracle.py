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
test_resp_hash_oracle.py   [RESP_HASH_DISCRIMINATES] [NO_RESP_SAME_V1]

Settles the recorded "open bug" at daemon/engine/session.py:833 and 948, and
covers the one thing that IS wrong, at 715.

    python3 test_resp_hash_oracle.py

THE RECORDED CLAIM
------------------
    new_raw_hash = hashlib.sha256(sem_resp).digest()

    "hashes the semantic encoding rather than the actual response body, so a
     3-row reply and a 0-row reply produce identical hashes and the daemon
     answers RESP_SAME for a body that changed."

Two earlier sessions disagree about this: one recorded it as the open bug, the
other recorded that it was disproved by running the codec.  Part A runs the
REAL codec and settles it.  This is not a matter of reading the code and
reasoning about it -- encode_reply is a compression codec and the only way to
know what it emits is to make it emit.

WHAT PART B COVERS
------------------
Even a discriminating hash is only interesting if something consumes it.  Under
[NO_CACHES_V1] the RESP_SAME branch was deleted from the daemon and the proxy
502s on any RESP_SAME it receives.  So the hash feeds compute_same_id, the
token rides along on a RESP_DIFF, and _handle_resp_diff checks it is 16 bytes
and then never looks at it again.  Part B holds that invariant in place: if
anyone reintroduces a RESP_SAME emitter, the proxy on the other end cannot
handle it, and this fails.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.join(_HERE, "..")):
    if p not in sys.path:
        sys.path.insert(0, p)

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


def part_a_codec_discriminates():
    """Run the real codec. Does sem_resp distinguish response bodies?"""
    from core.codec import SemanticCodec
    from core.tokens import TokenStore

    codec = SemanticCodec()
    tokens = TokenStore({})
    OPCODE = 1233457664                       # getHosts.php, from a live log

    # The REAL learned shape for this endpoint, as learn_templates_from_real
    # produces it -- field_order ['ok','rows'], rows captured as an atomic
    # array. Not an invented [('value', rows)]: the whole question is what the
    # codec does with the actual template, so the actual template is used.
    TYPE_MAP = {"ok": "bool", "rows": "array"}

    def enc(rows):
        return codec.encode_reply(opcode=OPCODE,
                                  dynamic_vals=[("ok", True), ("rows", rows)],
                                  type_map=TYPE_MAP, tokens=tokens)

    def h(rows):
        return hashlib.sha256(enc(rows)).digest()

    three = [{"ip": "10.1.1.1"}, {"ip": "10.2.2.1"}, {"ip": "10.3.3.1"}]
    one = [{"ip": "10.1.1.1"}]
    zero = []
    changed = [{"ip": "10.1.1.1"}, {"ip": "10.2.2.1"}, {"ip": "10.9.9.9"}]

    print("\n  real codec output:")
    for name, rows in (("3 rows", three), ("1 row", one), ("0 rows", zero),
                       ("3 rows, one changed", changed)):
        b = enc(rows)
        print(f"    {name:22} {len(b):4}B  sha256={hashlib.sha256(b).hexdigest()[:24]}")
    print()

    check(h(three) != h(zero),
          "A  3-row and 0-row replies hash DIFFERENTLY "
          "(the recorded bug does not reproduce)")
    check(h(three) != h(one), "A2 3-row and 1-row hash differently")
    check(h(three) != h(changed),
          "A3 same row COUNT, different content -> different hash")
    check(h(three) == h(three), "A4 identical input -> identical hash (stable)")
    check(len(enc(zero)) < len(enc(one)) < len(enc(three)),
          "A5 encoded size tracks row count (the count is IN the encoding)")


def part_b_no_resp_same_emitters():
    """[NO_CACHES_V1] left the proxy unable to handle RESP_SAME. Any daemon
    that still emits one produces a 502 at the far end."""
    root = os.path.join(_HERE, "..")
    session = os.path.join(root, "daemon", "engine", "session.py")
    if not os.path.exists(session):
        session = os.path.join(_HERE, "daemon", "engine", "session.py")
    src = open(session, encoding="utf-8").read()

    # strip comments and docstrings so a mention is not an emit
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(l.split("#")[0] for l in code.splitlines())

    emitters = [l.strip() for l in code.splitlines() if "wrap_resp_same(" in l]
    for e in emitters:
        print(f"        emitter: {e}")
    check(not emitters,
          f"B  the daemon emits NO RESP_SAME -- the [NO_CACHES_V1] proxy "
          f"rejects it with 502 sem_resp_same_unsupported "
          f"({len(emitters)} emitter(s) found)")

    proxy = os.path.join(root, "proxy", "transport_semantic.py")
    if not os.path.exists(proxy):
        proxy = os.path.join(_HERE, "proxy", "transport_semantic.py")
    psrc = open(proxy, encoding="utf-8").read()
    check("sem_resp_same_unsupported" in psrc,
          "B2 the proxy does reject RESP_SAME (invariant is load-bearing)")

    # the token rides along but is not a decision input on a DIFF
    body = psrc[psrc.index("def _handle_resp_diff"):]
    body = body[:body.index("\ndef ")] if "\ndef " in body[10:] else body
    uses = [l.strip() for l in body.splitlines()
            if "same_id" in l
            and "len(same_id)" not in l                 # the length guard
            and "Invalid same_id" not in l              # its own error string
            and "def _handle_resp_diff" not in l
            and "trace_enter" not in l]
    check(not uses,
          f"B3 _handle_resp_diff checks same_id's LENGTH and nothing else -- "
          f"the hash is not a decision input ({uses})")


def part_c_no_empty_substitution():
    """[NO_EMPTY_SUBSTITUTE_V1] The daemon must never manufacture a value.

    History: extract_dynamic() returns empty for some bodies, and a guard
    substituted `dyn = [("value", [])]`. Every caller then received a
    17-byte encoding of nothing -- `curl .../getHosts.php` through the
    semantic path returned `{}` while port 8080 straight to Apache returned
    the real array. [RAW_SIGNAL_V1] fixed the 200 path by sending the real
    body. The non-200 path kept the substitution under the comment
    "Non-200 (error responses): fallback is acceptable".

    It is not acceptable. An empty list is a WELL-FORMED ANSWER: substituting
    one turns "the upstream failed and I could not parse it" into "the query
    succeeded and matched nothing". It also disarms the error path one layer
    up -- session.py emits OP_ERROR only when `status >= 400 and dyn_vals is
    None`, so setting dyn makes dyn_vals non-None, the error branch is
    skipped, and a failed read leaves as RESP_DIFF. The proxy rebuilds it and
    hands the client HTTP 200 with an empty rows array.
    """
    root = os.path.join(_HERE, "..")
    ex = os.path.join(root, "daemon", "engine", "execution.py")
    if not os.path.exists(ex):
        ex = os.path.join(_HERE, "daemon", "engine", "execution.py")
    src = open(ex, encoding="utf-8").read()
    code = "\n".join(l.split("#")[0] for l in src.splitlines())

    subs = [l.strip() for l in code.splitlines()
            if re.search(r'dyn\s*=\s*\[\s*\(\s*["\']\w+["\']\s*,\s*\[\s*\]\s*\)\s*\]', l)]
    for x in subs:
        print(f"        substitution: {x}")
    check(not subs,
          f"C  the daemon NEVER substitutes an empty value for a failed "
          f"extraction ({len(subs)} found)")

    # and the raw path must not be gated on status
    gated = re.search(r'if not dyn:\s*\n\s*if status == 200:', src)
    check(gated is None,
          "C2 the raw-body path is not gated on status==200 -- a non-200 "
          "with no extractable fields still sends the REAL body")


def main():
    print("\n[RESP_HASH_DISCRIMINATES] daemon response hashing")
    part_a_codec_discriminates()
    print("\n[NO_RESP_SAME_V1] no emitter the far end cannot handle")
    part_b_no_resp_same_emitters()
    print("\n[NO_EMPTY_SUBSTITUTE_V1] no manufactured answers")
    part_c_no_empty_substitution()
    print()
    if _fails:
        print(f"ORACLE FAIL ({len(_fails)}):")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print("ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
