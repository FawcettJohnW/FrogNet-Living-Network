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
"""test_capability_no_age_oracle.py -- [CAPABILITY_DOES_NOT_AGE_V1]

Capability describes the MACHINE. Hardware does not expire, so age is not a
consideration and a capability row may live forever. The one thing the election
must not do is pick a candidate that is not reachable.

Run:  python3 test_capability_no_age_oracle.py
"""
import ast, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "frognet_role_elect.py")).read()
_results = []


def check(name, fn):
    try:
        fn(); _results.append(("PASS", name, ""))
    except AssertionError as e:
        _results.append(("FAIL", name, str(e)))
    except Exception as e:
        _results.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _mod(rows, reachable):
    """Load the module with frognet_tuples and the reach probe stubbed."""
    pkg = types.ModuleType("core"); pkg.__path__ = []
    T = types.ModuleType("core.frognet_tuples")
    T.get = lambda role, var, dbhost=None, fresh_s=0, **k: rows
    T.role_scope = lambda r: "host:x:" + r
    sys.modules["core"] = pkg
    sys.modules["core.frognet_tuples"] = T
    m = types.ModuleType("elect_probe")
    exec(compile(SRC, "elect_probe", "exec"), m.__dict__)
    return m


def _row(ip, age_s):
    return {"name": "SD:capability.host:%s:mediahost" % ip, "addr": ip,
            "age_s": age_s,
            "value": {"lan_ip": ip, "cores": 4, "camera": True, "mic": True}}


class _H:
    ROLE_NAME = "mediahost"


def _gather(m, **kw):
    return m.gather_candidates(_H(), local_ips=kw.get("local_ips", []),
                               lan_subnets=kw.get("lan_subnets"))


# ---- age is not a consideration -------------------------------------------

def t_ancient_row_is_still_a_candidate():
    """The 8.1-day fossil case -- but REACHABLE. It still has its cores."""
    m = _mod([_row("10.160.160.1", 8 * 86400)], reachable={"10.160.160.1"})
    hosts, _lan = _gather(m)
    assert [c["lan_ip"] for c in hosts] == ["10.160.160.1"], (
        "an 8-day-old capability row was refused -- hardware does not expire")


def t_the_2040s_ballot_is_admitted():
    """Measured: mediahost 10.28.28.1 refused at ts_age=2040s while healthy."""
    m = _mod([_row("10.28.28.1", 2040)], reachable={"10.28.28.1"})
    hosts, _lan = _gather(m)
    assert len(hosts) == 1, "a 2040s-old row was refused"


def t_no_age_gate_left_in_the_source():
    assert "FROGNET_BALLOT_MAX_AGE_S" not in SRC, (
        "the max-age env gate is still present")
    assert "BALLOT_REFUSED" not in SRC, (
        "ballots are still refused on age")


def t_missing_envelope_no_longer_disqualifies():
    """age_s None was 'not admissible'. Age is not a consideration."""
    r = _row("10.120.120.1", None)
    m = _mod([r], reachable={"10.120.120.1"})
    hosts, _lan = _gather(m)
    assert len(hosts) == 1, (
        "a row with no envelope timestamp was dropped -- that is an age "
        "judgement by another name")


# ---- the determination must be IDENTICAL ON EVERY NODE ---------------------

def t_no_per_node_reachability_cull():
    """apply_to_etc_hosts: "NO per-node reachability cull: the determination is
    identical on every node by construction." A local probe is a per-node input
    and cannot produce a pond-wide agreement, however correct each probe is."""
    for bad in ("is_reachable", "_reach_memo", "REACH_PORT", "connect("):
        assert bad not in SRC, (
            "gather_candidates does a per-node reachability measurement (%s) -- "
            "every node then reads a different pool and elects a different "
            "winner, which is the split measured 2026-08-08" % bad)


def t_same_rows_same_answer():
    """Two nodes, same rows, different local_ips -> identical candidate list."""
    rows = [_row("10.1.1.1", 9999999), _row("10.2.2.2", 5), _row("10.3.3.3", 0)]
    a = _mod(rows, reachable=set())
    ha, _ = a.gather_candidates(_H(), local_ips=["10.1.1.1"], lan_subnets=None)
    b = _mod(rows, reachable=set())
    hb, _ = b.gather_candidates(_H(), local_ips=["10.9.9.9"], lan_subnets=None)
    assert [c["lan_ip"] for c in ha] == [c["lan_ip"] for c in hb], (
        "two nodes reading the SAME rows produced different candidate lists: "
        "%s vs %s" % ([c["lan_ip"] for c in ha], [c["lan_ip"] for c in hb]))


def t_pool_is_every_row_regardless_of_age():
    rows = [_row("10.1.1.1", 8 * 86400), _row("10.2.2.2", 2040), _row("10.3.3.3", 1)]
    m = _mod(rows, reachable=set())
    hosts, _ = m.gather_candidates(_H(), local_ips=[], lan_subnets=None)
    assert len(hosts) == 3, (
        "%d of 3 rows survived -- age is not a consideration and nothing else "
        "may cull the pool per node" % len(hosts))


for _n, _f in sorted(globals().items()):
    if _n.startswith("t_"):
        check(_n[2:], _f)
_w = max(len(r[1]) for r in _results)
for _st, _n, _m in _results:
    print(f"{_st:6} {_n:<{_w}}  {_m}")
_bad = sum(1 for r in _results if r[0] != "PASS")
print(f"\n{len(_results) - _bad}/{len(_results)} passed")
sys.exit(1 if _bad else 0)
