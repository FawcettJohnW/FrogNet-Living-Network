"""[ELECTION_FROM_FACTS_V1] the service elections' input in region mode: every member's capability is a FACT in the
region (facts.capability, written by its daemon from frognet_capability_probe.sh), and the election reads the alive
members' facts -- not capability tuples in a store that may live on the very host that just died.

What was: each node advertised <role>/capability tuples to the control store (databasehost_control = highest .1) and
the election read them back from there. The databasehost IS the highest .1, so when it died the control moved, the
advertisers kept writing to the dead name, the new control's store had nothing or stale rows, and every election
ABSTAINed ("the store did not answer ... the standing line is kept") -- Seattle5 2026-10-09 19:00: both roles ABSTAIN,
databasehost.frognet left pointing at a dead host. The switchover was not automatic because its input was not.

Here: `set_snapshot(participants)` is called by the elector with the region's alive members (the daemon's view at
this event); `rows(role)` then answers in the shape the readers expect from the store (value/addr/ts_env/name/age_s),
so gather_candidates, _capability_index and _live_roles work unchanged over the region. Region mode always sets the
snapshot; broker mode (no region) never does and reads the store as before. That is the mode, not a fallback.
"""
from __future__ import annotations
import threading
from typing import Dict, List, Optional

_snap = threading.local()


def _dot1_of(facts: dict) -> str:
    for r in facts.get("addrs") or []:
        ip = r.get("ip", "")
        if r.get("kind") == "own" and ip.endswith(".1"):
            return ip
    sub = facts.get("subnet") or ""
    return sub.rsplit(".", 1)[0] + ".1" if sub.endswith(".0/24") else ""


def lan_of(participants: list, self_ip: str) -> set:
    """[LAN_IS_THE_GRAPH_V1] the .1s fdf_graph links to the member whose .1 is self_ip (a lease held/given between them)."""
    import os, sys
    fp = os.environ.get("FDF_PY", "/opt/fdf/lib/python")
    if fp not in sys.path:
        sys.path.insert(0, fp)
    from fdf_graph import Graph
    g = Graph(participants)
    dot1 = {p.get("name"): _dot1_of(p.get("facts") or {}) for p in participants if p.get("alive")}
    me = next((n for n, ip in dot1.items() if ip == self_ip), None)
    out = {dot1.get(parent if child == me else child) for child, parent, _a, _d in g.links if me in (child, parent)}
    out.discard(None); out.discard("")
    return out


def set_snapshot(participants: Optional[list], self_ip: str = "") -> int:
    """participants: the region's list (name, alive, facts). None clears (broker mode). Returns how many rows.
    self_ip: this node's .1 -> the LAN bucket is set from the graph too."""
    if participants is None:
        _snap.rows = None; _snap.lan = None
        return 0
    _snap.lan = lan_of(participants, self_ip) if self_ip else set()
    rows: List[Dict] = []
    for p in participants:
        f = p.get("facts") or {}
        if not p.get("alive") or f.get("role") != "node":
            continue
        cap = f.get("capability")
        ip = _dot1_of(f)
        if not isinstance(cap, dict) or not ip:
            continue
        cap = dict(cap)
        cap["lan_ip"] = ip                              # the .1 the region knows, not whatever the probe guessed
        rows.append({"value": {"capability": cap, "loadavg": cap.get("loadavg") or {}, "temps_c": cap.get("temps_c") or []},
                     "addr": ip, "ts_env": float(f.get("capability_ts") or 0), "age_s": 0,
                     "name": "region:%s" % p.get("name", ip), "member": p.get("name", "")})
    _snap.rows = rows
    return len(rows)


def set_lan(ips) -> None:
    """[LAN_IS_THE_GRAPH_V1] the .1s on this node's LAN, from fdf_graph's links (a held lease whose giver wrote the same
    ip+mac): the mediahost bucket. Replaces a traceroute per candidate per election."""
    _snap.lan = set(ips or ())


def lan() -> Optional[set]:
    return getattr(_snap, "lan", None) if active() else None


def active() -> bool:
    return getattr(_snap, "rows", None) is not None


def rows(role: str) -> Optional[List[Dict]]:
    """The capability rows for a role, from the snapshot; None when no snapshot is set (broker mode)."""
    r = getattr(_snap, "rows", None)
    return None if r is None else [dict(x, name="%s:%s" % (x["name"], role)) for x in r]
