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
diagnose_mesh.py  —  run ON THE DROPLET (broker host). Read-only, stdlib only.

Reproduces the broker's EXACT _compute_aggregate peer selection for one node and
shows, for every chorus-mate, whether it lands in the peer set and whether a tunnel
row actually exists. Answers "why does node X have no tunnel to Y" without guessing.

The broker builds tunnels for peer = same-chorus AND active=1 AND
last_seen_at > now - _NODE_STALE_AFTER_SEC (default 630). If a chorus-mate is active
but its last_seen age exceeds the window AT POLL TIME, it silently drops out of the
set and no tunnel is created for that pair — the poll still returns 200.

  python3 diagnose_mesh.py --node BABox
  python3 diagnose_mesh.py                 # every active node
  python3 diagnose_mesh.py --stale-after 630
"""
import argparse, sqlite3, time

DEFAULT_DB = "/var/lib/frognet_broker_v4/broker.db"
NODE_STALE_AFTER_SEC = 630   # broker default; override with --stale-after


def tunnel_between(db, a_id, b_id):
    """Return active tunnel row id(s) between two node_ids (either orientation)."""
    rows = db.execute(
        "SELECT id, name, tunnel_index, host_node_id, join_node_id, active "
        "FROM tunnels WHERE active=1 AND "
        "((host_node_id=? AND join_node_id=?) OR (host_node_id=? AND join_node_id=?))",
        (a_id, b_id, b_id, a_id)).fetchall()
    return rows


def diagnose_node(db, node, now, stale_after):
    print(f"\n=== {node['name']} (id={node['id']} subnet={node['subnet']} "
          f"active={node['active']} last_seen_age="
          f"{now - (node['last_seen_at'] or 0)}s) ===")

    chorus_ids = [r["chorus_id"] for r in db.execute(
        "SELECT chorus_id FROM chorus_members WHERE node_id=?", (node["id"],)).fetchall()]
    if not chorus_ids:
        print("  NOT in any chorus -> no peers, no tunnels.")
        return
    print(f"  chorus_ids={chorus_ids}")

    ph = ",".join("?" * len(chorus_ids))
    mates = db.execute(
        f"""SELECT DISTINCT n.* FROM nodes n
            JOIN chorus_members cm ON cm.node_id=n.id
            WHERE cm.chorus_id IN ({ph}) AND n.id != ? AND n.active=1
            ORDER BY n.name""",
        (*chorus_ids, node["id"])).fetchall()

    cutoff = now - stale_after
    print(f"  {'chorus-mate':<16} {'last_seen_age':>13} {'in_peer_set':>12} "
          f"{'tunnel_rows':>12}")
    missing = []
    for m in mates:
        age = now - (m["last_seen_at"] or 0)
        in_set = (m["last_seen_at"] or 0) > cutoff
        tuns = tunnel_between(db, node["id"], m["id"])
        tdesc = ",".join(f"idx{t['tunnel_index']}" for t in tuns) if tuns else "NONE"
        if len(tuns) > 1:
            tdesc += " (DUP)"
        flag = ""
        if in_set and not tuns:
            flag = "  <-- peer but NO tunnel"
            missing.append(m["name"])
        elif not in_set and not tuns:
            flag = "  <-- dropped by staleness"
        print(f"  {m['name']:<16} {age:>12}s {str(in_set):>12} {tdesc:>12}{flag}")

    if missing:
        print(f"  ** {node['name']} has peers with no tunnel: {missing} "
              "-> _ensure_tunnel should have created these; if absent, look for a "
              "create failure / rollback in the broker log for this node's poll.")


def dump_tunnels(db, apply=False):
    """Show each active tunnel's STORED name vs the CURRENT names of its bound
    node-ids. A mismatch means the row was built when a node had a different name
    (identity swap/rename) and _ensure_tunnel is matching it by the stale name."""
    rows = db.execute(
        """SELECT t.id AS tid, t.tunnel_index AS idx, t.name AS stored_name,
                  t.host_node_id AS h_id, hn.name AS host_now,
                  t.join_node_id AS j_id, jn.name AS join_now
           FROM tunnels t
           JOIN nodes hn ON hn.id=t.host_node_id
           JOIN nodes jn ON jn.id=t.join_node_id
           WHERE t.active=1 ORDER BY t.tunnel_index""").fetchall()
    print(f"{'idx':>4} {'id':>5}  {'stored_name':<26} {'host_now(id)':<18} "
          f"{'join_now(id)':<18} flag")
    mismatched = []
    for r in rows:
        # Expected stored name from CURRENT bound names (order-independent check).
        expect = {f"{r['host_now']}-to-{r['join_now']}", f"{r['join_now']}-to-{r['host_now']}"}
        bad = r["stored_name"] not in expect
        if bad:
            mismatched.append(r["tid"])
        flag = "<-- NAME/BINDING MISMATCH" if bad else ""
        print(f"{r['idx']:>4} {r['tid']:>5}  {r['stored_name']:<26} "
              f"{r['host_now']+'('+str(r['h_id'])+')':<18} "
              f"{r['join_now']+'('+str(r['j_id'])+')':<18} {flag}")
    print(f"\n{len(mismatched)} mismatched row(s): {mismatched}")
    if not mismatched:
        return
    if apply:
        db.execute(
            f"UPDATE tunnels SET active=0 WHERE id IN ({','.join('?'*len(mismatched))})",
            mismatched)
        db.commit()
        print("Deactivated. On the next poll _ensure_tunnel finds no row by those "
              "names and rebuilds them bound to the CURRENT nodes (with the right "
              "pubkeys). Watch the broker log for 'Tunnel '...' created'.")
    else:
        print("A MISMATCH row is named for a node identity that no longer holds that "
              "name. _ensure_tunnel matches by the stored name, so it returns this "
              "stale row instead of building the real one. Re-run with --apply to set "
              "these active=0; the broker rebuilds them correctly on the next poll.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--node", default=None, help="node name (default: all active)")
    ap.add_argument("--stale-after", type=int, default=NODE_STALE_AFTER_SEC)
    ap.add_argument("--tunnels", action="store_true",
                    help="dump stored tunnel name vs current node bindings, flag mismatches")
    ap.add_argument("--apply", action="store_true",
                    help="with --tunnels: deactivate the flagged mismatch rows")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    now = int(time.time())

    if args.tunnels:
        dump_tunnels(db, apply=args.apply)
        return

    if args.node:
        rows = db.execute("SELECT * FROM nodes WHERE name=? AND active=1",
                          (args.node,)).fetchall()
        if not rows:
            print(f"No active node named {args.node!r}.")
            return
    else:
        rows = db.execute("SELECT * FROM nodes WHERE active=1 ORDER BY name").fetchall()

    print(f"now={now}  stale_after={args.stale_after}s  "
          f"(peer must have last_seen_age < {args.stale_after}s)")
    for node in rows:
        diagnose_node(db, node, now, args.stale_after)


if __name__ == "__main__":
    main()
