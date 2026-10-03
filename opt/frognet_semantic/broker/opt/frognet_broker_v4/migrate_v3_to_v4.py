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
migrate_v3_to_v4.py — Optional one-shot import of v3 broker state into a
fresh v4 broker DB.

Usage:
    migrate_v3_to_v4.py [--v3-db PATH] [--v4-db PATH] [--dry-run]

Defaults:
    --v3-db /var/lib/frognet_broker_v3/broker.db
    --v4-db /var/lib/frognet_broker_v4/broker.db

What is copied:
    ponds                — name, indices, max_users, node_base/increment,
                           password_hash.  allow_user_choruses defaults
                           to 0; admin can flip per pond afterwards.
    choruses             — name, subnet, visible, password_hash.
                           creator_node_id is NULL because nodes are not
                           migrated.
    subnet_pool          — chorus subnet allocations, so a chorus that
                           had 10.254.5.0/24 in v3 keeps 10.254.5.0/24 in v4.

What is NOT copied (deliberately):
    nodes                — pubkeys re-register through setup_frognet on
                           the host; v4 binds them to its own state then.
    chorus_members       — comes back as nodes re-register and re-join.
    tunnels              — different transit /16 (10.252.x vs 10.253.x)
                           and different WG port range (51901-51980 vs
                           51820-51900); rebuilt on first /my-channels poll.
    node_ip_alloc        — IP assignment algorithm is deterministic for
                           a given pond config, so re-registering nodes
                           land on the same IPs.
    admin_config         — v4 bootstraps its own admin token.
    node_blocklist       — v4-only table; starts empty.

This script does NOT modify the v3 DB.  It also does NOT modify v4 ponds
that already exist (the import is skipped for any name collision so
re-running is safe).

After migration, point a node's broker URL at v4 (port 18257) and run
setup_frognet.  The node will re-register, auto-join entire_pond, and
its tunnels will rebuild on the next poll.
"""

import os
import sys
import sqlite3
import argparse
import logging
from pathlib import Path

V3_DB_DEFAULT = Path("/var/lib/frognet_broker_v3/broker.db")
V4_DB_DEFAULT = Path("/var/lib/frognet_broker_v4/broker.db")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO)
log = logging.getLogger("migrate")


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if not path.exists():
        log.error("DB not found: %s", path)
        sys.exit(2)
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _ensure_v4_schema(v4: sqlite3.Connection):
    """Make sure the v4 DB has the v4 schema.  We do not run init_db
    here — instead we verify the tables exist and bail if not, to
    avoid masking a broker that hasn't started yet."""
    needed = ["ponds", "choruses", "subnet_pool", "node_blocklist", "nodes",
              "chorus_members", "tunnels", "admin_config", "node_ip_alloc"]
    rows = v4.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    have = {r["name"] for r in rows}
    missing = [t for t in needed if t not in have]
    if missing:
        log.error("v4 DB is missing tables: %s — start frognet-broker-v4 "
                  "once to initialise its schema, then re-run this script.",
                  missing)
        sys.exit(3)


def _migrate_ponds(v3, v4, dry: bool) -> dict:
    """Returns {v3_pond_id: v4_pond_id}."""
    mapping = {}
    rows = v3.execute("SELECT * FROM ponds WHERE active=1").fetchall()
    log.info("ponds: %d active in v3", len(rows))

    for p in rows:
        existing = v4.execute(
            "SELECT id FROM ponds WHERE name=?", (p["name"],)).fetchone()
        if existing:
            log.info("  pond '%s' already in v4 (id=%d) — skipping",
                     p["name"], existing["id"])
            mapping[p["id"]] = existing["id"]
            continue

        # pond_index in v3 has a unique constraint in v4 too; re-use the
        # same index so transit IPs stay numerically aligned with admin
        # mental models.
        if v4.execute("SELECT id FROM ponds WHERE pond_index=?",
                      (p["pond_index"],)).fetchone():
            log.warning("  pond '%s' v3 index=%d collides in v4 — skipping",
                        p["name"], p["pond_index"])
            continue

        # ns_name must be the v4 prefix (pondv4_) so namespaces don't
        # collide with v3.  Source v3 stored "pond_<name>".
        new_ns = f"pondv4_{p['name']}"

        if dry:
            log.info("  [DRY] would insert pond '%s' index=%d ns=%s",
                     p["name"], p["pond_index"], new_ns)
            continue

        v4.execute(
            "INSERT INTO ponds "
            "(name,pond_index,ns_name,max_users,node_base,node_increment,"
            " password_hash,allow_user_choruses,created_at,active) "
            "VALUES (?,?,?,?,?,?,?,?,?,1)",
            (p["name"], p["pond_index"], new_ns, p["max_users"],
             p["node_base"], p["node_increment"], p["password_hash"],
             0, p["created_at"]))
        v4.commit()
        new_id = v4.execute(
            "SELECT id FROM ponds WHERE name=?", (p["name"],)).fetchone()["id"]
        mapping[p["id"]] = new_id
        log.info("  imported pond '%s' v3=%d -> v4=%d ns=%s",
                 p["name"], p["id"], new_id, new_ns)
    return mapping


def _migrate_choruses(v3, v4, pond_map: dict, dry: bool):
    """Copy choruses + claim their subnets in v4.subnet_pool."""
    if not pond_map:
        log.info("choruses: no pond mappings — nothing to copy")
        return

    placeholders = ",".join("?" * len(pond_map))
    rows = v3.execute(
        f"SELECT * FROM choruses WHERE active=1 AND pond_id IN ({placeholders})",
        tuple(pond_map.keys())).fetchall()
    log.info("choruses: %d active in mapped v3 ponds", len(rows))

    for c in rows:
        v4_pid = pond_map[c["pond_id"]]
        existing = v4.execute(
            "SELECT id FROM choruses WHERE pond_id=? AND name=?",
            (v4_pid, c["name"])).fetchone()
        if existing:
            log.info("  chorus '%s' already in v4 pond_id=%d — skipping",
                     c["name"], v4_pid)
            continue

        if dry:
            log.info("  [DRY] would insert chorus '%s' subnet=%s "
                     "visible=%d into v4 pond_id=%d",
                     c["name"], c["subnet"], c["visible"], v4_pid)
            continue

        v4.execute(
            "INSERT INTO choruses "
            "(pond_id,name,subnet,visible,password_hash,creator_node_id,"
            " created_at,active) VALUES (?,?,?,?,?,NULL,?,1)",
            (v4_pid, c["name"], c["subnet"], c["visible"],
             c["password_hash"], c["created_at"]))
        v4.commit()

        # Mark the v4 subnet_pool slot as claimed by this new chorus.
        new_id = v4.execute(
            "SELECT id FROM choruses WHERE pond_id=? AND name=?",
            (v4_pid, c["name"])).fetchone()["id"]
        v4.execute(
            "UPDATE subnet_pool SET chorus_id=? WHERE subnet=?",
            (new_id, c["subnet"]))
        v4.commit()
        log.info("  imported chorus '%s' (subnet %s) into v4 pond_id=%d "
                 "as v4 chorus_id=%d",
                 c["name"], c["subnet"], v4_pid, new_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v3-db", type=Path, default=V3_DB_DEFAULT)
    ap.add_argument("--v4-db", type=Path, default=V4_DB_DEFAULT)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would happen, change nothing.")
    args = ap.parse_args()

    log.info("v3 DB: %s", args.v3_db)
    log.info("v4 DB: %s", args.v4_db)
    log.info("dry run: %s", args.dry_run)

    v3 = _connect(args.v3_db, readonly=True)
    v4 = _connect(args.v4_db, readonly=False)
    _ensure_v4_schema(v4)

    pond_map = _migrate_ponds(v3, v4, args.dry_run)
    _migrate_choruses(v3, v4, pond_map, args.dry_run)

    v3.close()
    v4.close()
    log.info("Migration %s.", "preview complete (no writes)" if args.dry_run
             else "complete")


if __name__ == "__main__":
    main()
