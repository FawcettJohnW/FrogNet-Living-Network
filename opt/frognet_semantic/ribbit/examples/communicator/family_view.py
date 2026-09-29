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
family_view.py - the multi-network family home model (headless core of family_app.py).

A family is not one network. Members live across ponds (sites/namespaces) and choruses
(the family sub-group within a pond), federated. The old hub showed only the bundle grid
on one network; this presents the WHOLE family as one thing, regardless of how many
networks it actually spans - the fabric stays invisible.

It is pure logic over the Communicator spine, so it is testable headless; family_app.py
is the thin Tk shell that renders this model. Built on what already exists:
  - presence + the one-byte beacon  -> comm.presence.view_of(peer)/.local()  (converged)
  - bundle discovery by beacon       -> launcher.grouped(source)

Doctrine honored:
  - Awareness = reachability: a network is "lit" iff a member on it is currently
    converged in our local view; nothing is configured as *the* network.
  - The one-byte beacon is the load-bearing human signal: a NEED_HELP anywhere, on any
    pond or chorus, surfaces to the TOP as an alert - that is the whole point of the byte.
  - The family is grouped by chorus (the family unit); the pond is just the network tag.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from presence_codex import OK, NEED_HELP
from launcher import grouped, BeaconSource


def _member_view(comm: Any, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve one roster entry against our converged presence view of that peer.
    No converged view yet => unreachable (awareness = reachability)."""
    v = comm.presence.view_of(entry["peer_id"]) if comm.presence else None
    reachable = v is not None
    return {
        "peer_id": entry["peer_id"],
        "name": (v.get("name") if v else None) or entry.get("person", entry["peer_id"]),
        "pond": entry.get("pond", "?"),
        "chorus": entry.get("chorus", "family"),
        "status": (v.get("status") if v else "unreachable"),
        "beacon": int(v.get("beacon", OK)) if v else OK,
        "reachable": reachable,
    }


def _rank(m: Dict[str, Any]) -> tuple:
    # NEED_HELP first, then online, then reachable, then name
    return (0 if m["beacon"] == NEED_HELP else 1,
            0 if m["status"] == "online" else 1,
            0 if m["reachable"] else 1,
            m["name"].lower())


def home_model(comm: Any, roster: List[Dict[str, Any]],
               bundle_source: Optional[BeaconSource] = None) -> Dict[str, Any]:
    """Build the family home screen model from the converged spine state.

    roster: [{peer_id, person, pond, chorus}, ...] - who is in the family and where.
    Returns: { self, alerts, networks, family, bundles }.
    """
    members = [_member_view(comm, e) for e in roster]

    # alerts: any NEED_HELP, across every network, surfaced first - the byte that matters
    alerts = sorted([m for m in members if m["beacon"] == NEED_HELP],
                    key=lambda m: m["name"].lower())

    # networks: one row per (pond, chorus); lit iff any member is converged
    nets: Dict[tuple, Dict[str, Any]] = {}
    for m in members:
        k = (m["pond"], m["chorus"])
        n = nets.setdefault(k, {"pond": m["pond"], "chorus": m["chorus"],
                                "members": 0, "online": 0, "reachable": False,
                                "needs_help": 0})
        n["members"] += 1
        n["online"] += 1 if m["status"] == "online" else 0
        n["reachable"] = n["reachable"] or m["reachable"]
        n["needs_help"] += 1 if m["beacon"] == NEED_HELP else 0
    networks = sorted(nets.values(), key=lambda n: (n["pond"], n["chorus"]))

    # family: grouped by chorus (the family unit), members ranked (help first)
    fam: Dict[str, Dict[str, Any]] = {}
    for m in members:
        g = fam.setdefault(m["chorus"], {"chorus": m["chorus"], "ponds": set(), "members": []})
        g["ponds"].add(m["pond"])
        g["members"].append(m)
    family = []
    for chorus in sorted(fam):
        g = fam[chorus]
        family.append({
            "chorus": chorus,
            "ponds": sorted(g["ponds"]),                 # a family may span >1 pond
            "members": sorted(g["members"], key=_rank),
        })

    bundles = grouped(bundle_source) if bundle_source is not None else {}
    me = comm.presence.local() if comm.presence else {}
    return {"self": {"name": me.get("name"), "status": me.get("status"),
                     "beacon": int(me.get("beacon", OK))},
            "alerts": alerts, "networks": networks, "family": family, "bundles": bundles}
