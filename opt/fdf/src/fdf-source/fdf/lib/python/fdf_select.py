################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdf_select.py -- [APP_SELECTION_V1] the app's own selection of the member that serves each of its RAMs.

John, 2026-11-03: "That's up to the algorithm. All services work like this -- EOC is just the example. For a media
service, the requirements are different than for the EOC. It's up to the app." The platform gives the mechanism (the
capability tuples, the held read, the rerun on every event, connect-or-fail at the door); the app gives the ranking.

An app's selection, in its configuration as "select", per RAM ("*" for every RAM it does not name):
    {"*":       {"rank": ["mem_total_kb", "cores"]},
     "general": {"need": ["mysql_running"], "rank": ["cpu_bench_total", "-disk_fsync_ms", "mem_total_kb"]}}
  need  keys of the capability tuple that must be true (non-zero, non-empty) for a member to be a candidate at all
  rank  keys compared in order, higher first; "-key": lower first. A member whose tuple lacks a key, or has a value
        that is not a number, ranks below every member that has it: nothing is defaulted ([NO_FALLBACK_V1]).
  The guid breaks the last tie, highest first, so the answer is total: every member that sees the same tuples computes
  the same member. No "select" (or a RAM it does not name, with no "*"): the default, the most installed memory, then
  the most cores, then the highest guid (fdf-app's selection before this).
A member offers only the RAMs it was installed to serve: its capability tuple's "serve" (all the app's RAMs when absent).
"""

DEFAULT = {"rank": ["mem_total_kb", "cores"]}


def _key_ok(k):
    if not isinstance(k, str):
        return False
    n = k[1:] if k.startswith("-") else k
    return 0 < len(n) <= 40 and all(ch.isalnum() or ch == "_" for ch in n)


def check(sel):
    """the "select" of a configuration, checked: {ram: {"need": [...], "rank": [...]}}; ValueError says what is wrong"""
    if sel in (None, {}):
        return {}
    if not isinstance(sel, dict):
        raise ValueError("select: {RAM or *: {need: [keys], rank: [keys or -keys]}}")
    out = {}
    for r, s in sel.items():
        if not isinstance(r, str) or not r or not isinstance(s, dict) or set(s) - {"need", "rank"}:
            raise ValueError("select %r: only need and rank" % r)
        need, rank = s.get("need") or [], s.get("rank") or []
        if not isinstance(need, list) or not isinstance(rank, list) or not all(_key_ok(k) and k[0] != "-" for k in need) \
                or not all(_key_ok(k) for k in rank):
            raise ValueError("select %r: need is a list of capability keys, rank a list of keys or -keys" % r)
        out[r] = {"need": list(need), "rank": list(rank)}
    return out


def spec(sel, ram):
    sel = sel or {}
    return sel.get(ram) or sel.get("*") or DEFAULT


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def rank_of(s, guid, bag):
    """the sort key of one candidate under spec s (larger is better)"""
    out = []
    for k in s.get("rank") or []:
        down = k.startswith("-")
        v = _num(bag.get(k.lstrip("-")))
        out.append((0, 0.0) if v is None else (1, -v if down else v))
    out.append((1, guid))
    return tuple(out)


def candidates(s, ram, cands):
    """the members that can serve ram: they offer it, and have every need"""
    out = []
    for g, b in cands:
        serve = b.get("serve")
        if isinstance(serve, list) and ram not in serve:
            continue
        if not all(b.get(k) not in (None, 0, 0.0, False, "", "0", "false") for k in s.get("need") or []):
            continue
        out.append((g, b))
    return out


def select(sel, ram, cands):
    """cands: [(guid, capability bag)] of the alive members that have the app. The guid that serves ram, or None."""
    s = spec(sel, ram)
    c = candidates(s, ram, cands)
    return max(c, key=lambda gb: rank_of(s, gb[0], gb[1]))[0] if c else None


def explain(sel, ram, cands, top=3):
    """why: the ranked candidates, for the log"""
    s = spec(sel, ram)
    c = sorted(candidates(s, ram, cands), key=lambda gb: rank_of(s, gb[0], gb[1]), reverse=True)
    def vals(b):
        return ",".join("%s=%s" % (k.lstrip("-"), b.get(k.lstrip("-"), "absent")) for k in s.get("rank") or [])
    left = len(cands) - len(c)
    return "; ".join("%s(%s)" % (g, vals(b)) for g, b in c[:top]) + (" [%d not candidates: need %s or not offered]" % (left, s.get("need") or []) if left else "")
