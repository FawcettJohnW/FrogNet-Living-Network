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
reap_ghost_tunnels.py  —  run ON THE DROPLET (the broker host).

A "ghost" tunnel is a row in `tunnels` with active=1 whose WireGuard interface(s)
no longer exist in the pond namespace. The broker's _ensure_tunnel returns an
active=1 row WITHOUT verifying its interface still exists, and the stale-endpoint
reaper won't touch it while both endpoints keep polling — so the ghost is permanent.
my-channels then hands the channel to the node, which dials a droplet port nothing
listens on -> "no handshake" -> the node islands. (This is BABox's case: it has
active=1 rows for BABox<->New-York-1 and BABox<->Seattle5 with no interfaces.)

This script flips ghost rows to active=0. On the affected node's NEXT my-channels
poll the broker's own active=0 path runs _destroy_tunnel (proper port-forward + iface
cleanup) and then recreates the tunnel fresh -> real interfaces -> handshake -> mesh.

Default is DRY-RUN (reports only). Pass --apply to actually flip the rows.

  python3 reap_ghost_tunnels.py                 # report ghosts
  python3 reap_ghost_tunnels.py --apply         # flip ghosts to active=0
  python3 reap_ghost_tunnels.py --node BABox    # restrict to one node's tunnels

Only stdlib. Does NOT delete rows, touch ports, or modify the namespace itself —
it only sets active=0 and lets the broker's tested self-heal path do the rest.
"""
import argparse, sqlite3, subprocess, sys

DEFAULT_DB = "/var/lib/frognet_broker_v4/broker.db"


def iface_exists(ns_name: str, iface: str) -> bool:
    """True iff `iface` exists in netns `ns_name`. Uses wg show (these are wg ifaces)."""
    if not iface:
        return False
    r = subprocess.run(["ip", "netns", "exec", ns_name, "wg", "show", iface],
                       capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true",
                    help="flip ghost rows to active=0 (default: report only)")
    ap.add_argument("--node", default=None,
                    help="restrict to tunnels with this node as host or join")
    ap.add_argument("--dedup", action="store_true",
                    help="deactivate duplicate node-pair tunnels (keeps newest per pair) "
                         "to free WG ports; combine with --apply to actually write")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    ponds = {p["id"]: p for p in db.execute("SELECT * FROM ponds")}

    q = ("SELECT t.*, hn.name AS host_name, jn.name AS join_name "
         "FROM tunnels t "
         "JOIN nodes hn ON hn.id=t.host_node_id "
         "JOIN nodes jn ON jn.id=t.join_node_id "
         "WHERE t.active=1")
    params: list = []
    if args.node:
        q += " AND (hn.name=? OR jn.name=?)"
        params += [args.node, args.node]
    q += " ORDER BY t.tunnel_index"
    rows = db.execute(q, params).fetchall()

    ghosts = []
    pairs: dict = {}   # frozenset({host_name,join_name}) -> [rows], oldest first
    ports: list = []
    print(f"{'idx':>4}  {'host':<14} {'join':<14} {'h_port':>7} {'j_port':>7}  status")
    for t in rows:
        pond = ponds.get(t["pond_id"])
        ns = pond["ns_name"] if pond else None
        h_ok = iface_exists(ns, t["host_wg_iface"]) if ns else False
        j_ok = iface_exists(ns, t["join_wg_iface"]) if ns else False
        live = h_ok and j_ok
        status = "live" if live else (
            "GHOST (" + ",".join(
                x for x, ok in (("host_iface", h_ok), ("join_iface", j_ok)) if not ok)
            + " missing)")
        if not live:
            ghosts.append(t)
        pairs.setdefault(frozenset((t["host_name"], t["join_name"])), []).append(t)
        for p in (t["host_wg_port"], t["join_wg_port"]):
            if p is not None:
                ports.append(p)
        print(f"{t['tunnel_index']:>4}  {t['host_name']:<14} {t['join_name']:<14} "
              f"{t['host_wg_port']:>7} {t['join_wg_port']:>7}  {status}")

    dups = {k: v for k, v in pairs.items() if len(v) > 1}
    print(f"\n{len(rows)} active tunnel(s), {len(ghosts)} ghost(s), "
          f"{len(dups)} duplicated node-pair(s).")
    if ports:
        print(f"WG ports in use: {len(ports)} ({min(ports)}-{max(ports)}). "
              "Compare against the broker's FROGNET_WG_PORT_BASE/MAX — if max is at "
              "the top of the range, the pool is exhausted and new tunnels can't allocate.")
    if dups:
        print("Duplicate node-pairs (each wastes a port pair):")
        for k, v in dups.items():
            a, b = tuple(k)
            idxs = ", ".join(f"idx{r['tunnel_index']}(id{r['id']})" for r in v)
            print(f"  {a} <-> {b}: {idxs}")
        if args.dedup:
            # Keep the highest tunnel id (most recent) per pair, deactivate the rest.
            for v in dups.values():
                keep = max(v, key=lambda r: r["id"])
                for r in v:
                    if r["id"] != keep["id"]:
                        if args.apply:
                            db.execute("UPDATE tunnels SET active=0 WHERE id=?", (r["id"],))
                        print(f"  {'deactivated' if args.apply else 'WOULD deactivate'} "
                              f"duplicate id={r['id']} idx={r['tunnel_index']} "
                              f"(kept id={keep['id']})")
            if args.apply:
                db.commit()
                print("Duplicates deactivated. On the next poll the broker frees those "
                      "ports; BABox's next poll can then allocate its NY1/Seattle5 tunnels.")
            else:
                print("DRY-RUN: add --apply to actually deactivate the duplicates.")

    if not ghosts and not (dups and args.dedup):
        return 0
    if not ghosts:
        return 0

    if not args.apply:
        print("DRY-RUN: re-run with --apply to flip the ghost rows to active=0 "
              "(the broker recreates them with real interfaces on the next poll).")
        return 0

    for t in ghosts:
        db.execute("UPDATE tunnels SET active=0 WHERE id=?", (t["id"],))
        print(f"  flipped active=0: id={t['id']} idx={t['tunnel_index']} "
              f"{t['host_name']}<->{t['join_name']}")
    db.commit()
    print(f"\nDone. {len(ghosts)} ghost(s) deactivated. On each affected node's next "
          "my-channels poll the broker will _destroy_tunnel (clean) and recreate fresh "
          "interfaces. Watch the broker log for 'Tunnel ... created' / WG CREATE lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
