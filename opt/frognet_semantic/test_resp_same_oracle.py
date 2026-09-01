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
"""test_resp_same_oracle.py -- [SAME_IS_BACK_V1] + [SAME_COMPARES_THE_BODY_V1]

RESP_SAME was removed on 2026-08-02 as collateral in [NO_CACHES_V1], a purge
aimed at unbounded memo dicts. It was not a decision anyone made. The proxy was
left 502ing every RESP_SAME with "peer is running pre-[NO_CACHES_V1] code",
blaming the nodes that were still behaving correctly.

And the comparison it depended on was wrong to begin with:

    new_raw_hash = hashlib.sha256(sem_resp).digest()

sem_resp is the SEMANTIC REPLY -- execute()'s own docstring says so. It is
diff-encoded against the reply reference, so an unchanged response encodes to
different bytes whenever the reference moves. Identical body, different hash,
SAME missed. The raw path in the same file always hashed the body correctly, and
both wrote the same `raw_hash` column.

Run: python3 test_resp_same_oracle.py
"""
import ast, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OLD = os.environ.get("FROGNET_OLD_TREE", "")
_results = []


def check(name, fn):
    try:
        fn(); _results.append(("PASS", name, ""))
    except AssertionError as e:
        _results.append(("FAIL", name, str(e)))
    except Exception as e:
        _results.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _src(rel, tree=None):
    return open(os.path.join(tree or HERE, rel)).read()


def _fn(rel, name, tree=None):
    for n in ast.walk(ast.parse(_src(rel, tree))):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found in {rel}")


def _rbh():
    """The hash function, executed standalone."""
    import hashlib
    src = _src("daemon/engine/session.py")
    i = src.index("def response_body_hash")
    j = src.index("\ndef ", i + 10)
    ns = {"hashlib": hashlib}
    exec(src[i:j], ns)
    return ns["response_body_hash"]


# ---- the comparison ---------------------------------------------------------

def t_hash_ignores_field_order():
    """field_order is a property of the template and shifts when one is
    re-learned. Order sensitivity would reintroduce the same false difference."""
    h = _rbh()
    a = [("host", "NY1"), ("ip", "10.102.60.1")]
    b = [("ip", "10.102.60.1"), ("host", "NY1")]
    assert h(a, 200) == h(b, 200), "same response, different field order, different hash"


def t_hash_separates_status():
    h = _rbh()
    a = [("msg", "ok")]
    assert h(a, 200) != h(a, 500), "a 200 and a 500 with the same text hashed alike"


def t_hash_separates_types():
    h = _rbh()
    n = len({h([("x", 1)], 200), h([("x", "1")], 200), h([("x", 1.0)], 200)})
    assert n == 3, f"1, '1' and 1.0 collapsed to {n} hash(es)"


def t_hash_detects_a_real_change():
    h = _rbh()
    assert h([("host", "NY1")], 200) != h([("host", "NY2")], 200)


def t_no_site_hashes_the_encoding():
    """The whole bug. sem_resp is the ENCODED reply."""
    # AST, not text: response_body_hash's own docstring quotes the bad line as
    # the thing it replaces, and a string search cannot tell an explanation from
    # an instruction. Look for a real call instead.
    src = _src("daemon/engine/session.py")
    bad = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        f = ast.unparse(n.func)
        if f.endswith("sha256") and n.args and ast.unparse(n.args[0]) == "sem_resp":
            bad.append(n.lineno)
    assert not bad, ("still hashing the encoded reply at line(s) %s" % bad)


def t_writer_and_comparison_agree():
    """REQ_FULL writes the entry a later REQ_REPEAT compares against. If the two
    ends hash different things, SAME can never match."""
    src = _src("daemon/engine/session.py")
    n = src.count("response_body_hash(dyn_vals, status)")
    assert n >= 3, ("only %d site(s) use the body hash; writers and comparison "
                    "must all use it" % n)


def t_old_tree_hashed_the_encoding():
    if not OLD:
        raise AssertionError("set FROGNET_OLD_TREE to confirm the before-state")
    src = _src("daemon/engine/session.py", OLD)
    assert "hashlib.sha256(sem_resp).digest()" in src, \
        "expected the old tree to hash the encoded reply"


# ---- the restoration --------------------------------------------------------

def t_proxy_resolves_same_not_502():
    src = _src("proxy/transport_semantic.py")
    assert "resp_same_unsupported" not in src, \
        "the proxy still 502s RESP_SAME and blames the peer"
    assert "_serve_from_same" in src, "the proxy cannot resolve a same_id"


def t_same_lru_is_bounded():
    src = _src("proxy/transport_semantic.py")
    assert "_SAME_LRU_MAX" in src, "the SAME body cache has no bound"
    body = ast.unparse(_fn("proxy/transport_semantic.py", "_same_lru_put"))
    assert "popitem" in body, "nothing evicts from the SAME cache"


def t_req_repeat_is_reachable():
    """REQ_REPEAT is what makes RESP_SAME reachable. Removing it and then asking
    why SAME never appeared was this whole detour."""
    src = _src("proxy/transport_semantic.py")
    assert "wrap_req_repeat" in src, "REQ_REPEAT is never sent"
    assert "is_req_seen" in src, "nothing decides when to send REQ_REPEAT"


def t_daemon_emits_same():
    src = _src("daemon/engine/session.py")
    assert "wrap_resp_same(old_same_id)" in src, "the daemon never emits RESP_SAME"


def t_semcache_is_consulted_again():
    for rel in ("daemon/engine/session.py", "proxy/transport_semantic.py"):
        src = _src(rel)
        assert "no longer consulted" not in src, f"{rel} still disowns semcache_db"
        assert "semcache_db" in src, f"{rel} does not use semcache_db"


for _n, _f in sorted(globals().items()):
    if _n.startswith("t_"):
        check(_n[2:], _f)
_w = max(len(r[1]) for r in _results)
for _st, _n, _m in _results:
    print(f"{_st:6} {_n:<{_w}}  {_m}")
_bad = sum(1 for r in _results if r[0] != "PASS")
print(f"\n{len(_results) - _bad}/{len(_results)} passed")
sys.exit(1 if _bad else 0)
