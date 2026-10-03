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
reap_stale_tunnels.py — one-shot cleanup for broker v4

For each active tunnel, ask the kernel what peer pubkey each wg iface is
configured to accept.  Compare against the current pubkey in the nodes
table for the corresponding node_id.  If they don't match, the iface
holds a stale peer config from a prior pubkey rotation — the joiner
sends with the new pubkey, the iface silently drops (WG by design),
handshake never completes.

Destroy any such tunnel via the broker's _destroy_tunnel: that tears
down the wg ifaces, flushes the iptables port-forwards (via the patched
broker_namespace._flush_dport_rules), and deletes the DB row.

Run as root.  Does NOT need the broker to be running, but must not run
concurrently with it.

Usage:
    sudo systemctl stop frognet-broker-v4
    sudo python3 reap_stale_tunnels.py [--dry-run]
    sudo systemctl start frognet-broker-v4
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import subprocess
import sys


DB_PATH = "/var/lib/frognet_broker_v4/broker.db"


def wg_show_peer_pubkey(ns_name: str, iface: str) -> str | None:
    """Return the (single) peer pubkey configured on the wg iface, or None
    if the iface doesn't exist or has no peer.  The broker creates each
    iface with exactly one peer (add_wg_peer), so a single line is
    expected; we take the first line just in case."""
    r = subprocess.run(
        ["ip", "netns", "exec", ns_name, "wg", "show", iface, "peers"],
        capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    line = r.stdout.strip().splitlines()[:1]
    return line[0].strip() if line else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be destroyed without doing it")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("Must run as root (needs ip netns exec).", file=sys.stderr)
        return 2

    # Import the broker's own modules so we use the same _destroy_tunnel
    # path the runtime uses — keeps DB writes / kernel state consistent.
    sys.path.insert(0, "/opt/frognet_broker_v4")
    import broker_namespace as ns  # noqa: E402

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    # Pull every active tunnel along with its pond's ns_name and the
    # current pubkey of each side's node.  Sentinel <NO_NODE> covers
    # rows whose node was hard-deleted (shouldn't happen but defend).
    rows = db.execute("""
        SELECT
            t.id          AS t_id,
            t.name        AS t_name,
            t.host_node_id, t.join_node_id,
            t.host_wg_iface, t.join_wg_iface,
            t.host_wg_port, t.join_wg_port,
            p.ns_name     AS ns_name,
            p.pond_index  AS pond_index,
            p.id          AS pond_id,
            p.name        AS pond_name,
            COALESCE(hn.pubkey, '<NO_NODE>') AS host_current_pubkey,
            COALESCE(hn.name,   '<NO_NODE>') AS host_current_name,
            hn.active     AS host_active,
            COALESCE(jn.pubkey, '<NO_NODE>') AS join_current_pubkey,
            COALESCE(jn.name,   '<NO_NODE>') AS join_current_name,
            jn.active     AS join_active
        FROM tunnels t
        JOIN ponds p ON p.id = t.pond_id
        LEFT JOIN nodes hn ON hn.id = t.host_node_id
        LEFT JOIN nodes jn ON jn.id = t.join_node_id
        WHERE t.active = 1
        ORDER BY t.name
    """).fetchall()

    print(f"Scanning {len(rows)} active tunnel(s) ...")
    to_destroy: list[sqlite3.Row] = []

    for r in rows:
        reasons: list[str] = []

        # Either side's node row inactive or missing → can't possibly
        # be carrying the current pubkey.
        if r["host_current_pubkey"] == "<NO_NODE>" or not r["host_active"]:
            reasons.append(f"host_node_id={r['host_node_id']} not active "
                           f"(name={r['host_current_name']!r})")
        if r["join_current_pubkey"] == "<NO_NODE>" or not r["join_active"]:
            reasons.append(f"join_node_id={r['join_node_id']} not active "
                           f"(name={r['join_current_name']!r})")

        # Kernel-configured peer pubkey on each broker-side wg iface
        host_peer = wg_show_peer_pubkey(r["ns_name"], r["host_wg_iface"])
        join_peer = wg_show_peer_pubkey(r["ns_name"], r["join_wg_iface"])

        if host_peer is None:
            reasons.append(f"host_wg_iface={r['host_wg_iface']} missing in "
                           f"ns={r['ns_name']}")
        elif r["host_current_pubkey"] not in ("<NO_NODE>",) \
                and host_peer != r["host_current_pubkey"]:
            reasons.append(
                f"host iface peer={host_peer[:16]}... != "
                f"node {r['host_current_name']} current pubkey="
                f"{r['host_current_pubkey'][:16]}...")

        if join_peer is None:
            reasons.append(f"join_wg_iface={r['join_wg_iface']} missing in "
                           f"ns={r['ns_name']}")
        elif r["join_current_pubkey"] not in ("<NO_NODE>",) \
                and join_peer != r["join_current_pubkey"]:
            reasons.append(
                f"join iface peer={join_peer[:16]}... != "
                f"node {r['join_current_name']} current pubkey="
                f"{r['join_current_pubkey'][:16]}...")

        if reasons:
            print(f"  STALE  id={r['t_id']:4d}  name={r['t_name']}")
            for reason in reasons:
                print(f"           {reason}")
            to_destroy.append(r)
        else:
            print(f"  ok     id={r['t_id']:4d}  name={r['t_name']}")

    if not to_destroy:
        print("\nNo stale tunnels found.")
        return 0

    print(f"\nFound {len(to_destroy)} stale tunnel(s).")
    if args.dry_run:
        print("Dry run — no changes made.")
        return 0

    # Destroy via the broker's own path so iptables port-forwards get
    # flushed, wg ifaces get deleted, and the DB row gets removed —
    # all in the same transaction the broker would have used.
    print("\nDestroying ...")
    for r in to_destroy:
        # _destroy_tunnel expects sqlite3.Row-like access on both
        # tunnel and pond.  We have those.
        try:
            # Reach into the broker module so we don't reimplement.
            # _destroy_tunnel signature: (db, tunnel, pond)
            from frognet_broker_v4 import _destroy_tunnel  # noqa: E402
            pond_row = db.execute(
                "SELECT * FROM ponds WHERE id=?", (r["pond_id"],)).fetchone()
            tunnel_row = db.execute(
                "SELECT * FROM tunnels WHERE id=?", (r["t_id"],)).fetchone()
            if tunnel_row is None:
                print(f"  id={r['t_id']} already gone, skipping")
                continue
            _destroy_tunnel(db, tunnel_row, pond_row)
            print(f"  destroyed id={r['t_id']} name={r['t_name']}")
        except Exception as e:
            print(f"  FAILED id={r['t_id']} name={r['t_name']}: {e}",
                  file=sys.stderr)

    db.commit()
    db.close()
    print("\nDone.  Restart frognet-broker-v4, then trigger merges on "
          "the affected nodes — they'll create fresh tunnels with "
          "current pubkeys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
