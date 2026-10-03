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
frognet_broker.py — FrogNet Tunnel Broker (v3.1) — Pond/Chorus Architecture

No websockets. Pure request/response. Sits behind Apache on 8080,
optionally fronted by the FrogNet semantic proxy on 9009.

Data model:
  pond              → top-level container; admin-created; has IP pool
  node_ip_alloc     → tentative or confirmed node IP allocations
  chorus            → visibility group within a pond; auto-gets 10.254.x.0/24
  chorus_members    → node <-> chorus membership with assigned chorus IP
  subnet_pool       → 256 rows covering 10.254.0.0/24 - 10.254.255.0/24
  node              → a FrogNet edge device (identified by pubkey)
  tunnel            → WireGuard pipe between two nodes through the droplet

Reserved ranges:
  10.253.x.x  — WireGuard transit (broker-internal)
  10.254.x.x  — Chorus subnets (broker-assigned)
  .1           — Always reserved for FrogNetHost within any subnet
"""

import os
import sys
import json
import time
import secrets
import sqlite3
import logging
import hashlib
import ipaddress
import subprocess
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

import broker_namespace_v4 as ns

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BROKER_DIR  = Path(os.environ.get("FROGNET_BROKER_DIR", "/var/lib/frognet_broker_v3"))
DB_PATH     = BROKER_DIR / "broker.db"
KEYS_DIR    = BROKER_DIR / "keys"
LOG_LEVEL   = os.environ.get("FROGNET_BROKER_LOG", "INFO")

WG_PORT_BASE = int(os.environ.get("FROGNET_WG_PORT_BASE", "51901"))
WG_PORT_MAX  = int(os.environ.get("FROGNET_WG_PORT_MAX",  "51999"))

ADMIN_TOKEN = os.environ.get("FROGNET_ADMIN_TOKEN", "")

TUNNEL_ALLOWED_IPS     = "10.0.0.0/8"
NODE_BASE_DEFAULT      = "10.101.100.1"
NODE_INCREMENT_DEFAULT = 10

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("broker")

app = FastAPI(title="FrogNet Tunnel Broker", version="3.1.0")




# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ponds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            pond_index      INTEGER NOT NULL UNIQUE,
            ns_name         TEXT    NOT NULL UNIQUE,
            max_users       INTEGER NOT NULL DEFAULT 50,
            node_base       TEXT    NOT NULL DEFAULT '10.101.100.1',
            node_increment  INTEGER NOT NULL DEFAULT 10,
            password_hash       TEXT,
            allow_user_choruses INTEGER NOT NULL DEFAULT 0,
            created_at          REAL    NOT NULL,
            active              INTEGER NOT NULL DEFAULT 1
        );

        -- v3.2: nodes blocked from registering.  Match is by pubkey;
        -- node_name is recorded for admin display only.
        CREATE TABLE IF NOT EXISTS node_blocklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pubkey      TEXT    NOT NULL UNIQUE,
            node_name   TEXT    NOT NULL DEFAULT '',
            reason      TEXT    NOT NULL DEFAULT '',
            added_at    REAL    NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS node_ip_alloc (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            pond_id      INTEGER NOT NULL REFERENCES ponds(id),
            ip           TEXT    NOT NULL,
            pubkey       TEXT,
            allocated_at REAL    NOT NULL,
            confirmed    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pond_id, ip)
        );

        CREATE TABLE IF NOT EXISTS choruses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pond_id         INTEGER NOT NULL REFERENCES ponds(id),
            name            TEXT    NOT NULL,
            subnet          TEXT    NOT NULL DEFAULT '',
            visible         INTEGER NOT NULL DEFAULT 1,
            password_hash   TEXT,
            creator_node_id INTEGER REFERENCES nodes(id),
            created_at      REAL    NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1,
            UNIQUE(pond_id, name)
        );

        CREATE TABLE IF NOT EXISTS subnet_pool (
            id           INTEGER PRIMARY KEY,
            subnet       TEXT    NOT NULL UNIQUE,
            chorus_id    INTEGER,
            rotation_seq INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS nodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pubkey      TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL DEFAULT '',
            label       TEXT    NOT NULL DEFAULT '',
            subnet      TEXT    NOT NULL DEFAULT '',
            mac         TEXT    NOT NULL DEFAULT '',
            guid        TEXT    NOT NULL DEFAULT '',
            pond_id     INTEGER NOT NULL REFERENCES ponds(id),
            created_at  REAL    NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS chorus_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chorus_id   INTEGER NOT NULL REFERENCES choruses(id),
            node_id     INTEGER NOT NULL REFERENCES nodes(id),
            chorus_ip   TEXT    NOT NULL DEFAULT '',
            joined_at   REAL    NOT NULL,
            UNIQUE(chorus_id, node_id)
        );

        CREATE TABLE IF NOT EXISTS tunnels (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pond_id         INTEGER NOT NULL REFERENCES ponds(id),
            name            TEXT    NOT NULL,
            host_node_id    INTEGER NOT NULL REFERENCES nodes(id),
            join_node_id    INTEGER NOT NULL REFERENCES nodes(id),
            tunnel_index    INTEGER NOT NULL,
            host_wg_iface   TEXT    NOT NULL,
            host_wg_port    INTEGER NOT NULL,
            host_transit_ip TEXT    NOT NULL,
            host_edge_ip    TEXT    NOT NULL,
            host_privkey    TEXT    NOT NULL,
            host_pubkey     TEXT    NOT NULL,
            join_wg_iface   TEXT    NOT NULL,
            join_wg_port    INTEGER NOT NULL,
            join_transit_ip TEXT    NOT NULL,
            join_edge_ip    TEXT    NOT NULL,
            join_privkey    TEXT    NOT NULL,
            join_pubkey     TEXT    NOT NULL,
            transit_mask    INTEGER NOT NULL DEFAULT 30,
            created_at      REAL    NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1
            -- [TUNNEL_ID_KEY_V1] identity is the node-id pair, not the name.
            -- Uniqueness is enforced by the partial index ux_tunnels_pond_pair_active
            -- (created in init_db). The old UNIQUE(pond_id,name) is gone: the name
            -- derives from mutable node names and a rename would orphan the tunnel.
        );

        CREATE TABLE IF NOT EXISTS admin_config (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            created_at  REAL NOT NULL
        );

        -- [RECONFIG_ARCHIVE_V1] When a node migrates between ponds
        -- (cross-pond MAC match in the register handler), the node's
        -- prior pond-scoped state is archived here so that a future
        -- return to the same pond restores the same chorus memberships.
        -- entire_pond is NOT archived: it auto-rejoins via the standard
        -- register flow.  One archive entry per (pond_id, mac).
        CREATE TABLE IF NOT EXISTS reconfigured_nodes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pond_id         INTEGER NOT NULL REFERENCES ponds(id),
            mac             TEXT    NOT NULL,
            name            TEXT    NOT NULL DEFAULT '',
            label           TEXT    NOT NULL DEFAULT '',
            subnet          TEXT    NOT NULL DEFAULT '',
            transit_subnets TEXT    NOT NULL DEFAULT '[]',
            pubkey_at_leave TEXT    NOT NULL DEFAULT '',
            orig_node_id    INTEGER NOT NULL,
            archived_at     REAL    NOT NULL,
            UNIQUE(pond_id, mac)
        );

        CREATE TABLE IF NOT EXISTS reconfigured_chorus_members (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            reconfigured_node_id INTEGER NOT NULL
                                 REFERENCES reconfigured_nodes(id)
                                 ON DELETE CASCADE,
            chorus_id       INTEGER NOT NULL REFERENCES choruses(id),
            chorus_ip       TEXT    NOT NULL DEFAULT '',
            archived_at     REAL    NOT NULL,
            UNIQUE(reconfigured_node_id, chorus_id)
        );
    """)
    db.commit()

    # --- TRANSIT_SUBNETS_V1 migration [FIX1] ----------------------------
    # Add nodes.transit_subnets if missing.  A transit subnet is a /24
    # reachable THROUGH this node but not owned by it (e.g. a LAN peer
    # behind a gateway node).  Stored as a JSON array of CIDR strings.
    #
    # NOTE: the `db` here has no row_factory set, so PRAGMA rows come
    # back as tuples.  PRAGMA table_info returns
    # (cid, name, type, notnull, dflt_value, pk) — name is index 1.
    cols = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
    if "transit_subnets" not in cols:
        log.info("DB MIGRATION [TRANSIT_SUBNETS_V1]: "
                 "adding nodes.transit_subnets column")
        db.execute(
            "ALTER TABLE nodes ADD COLUMN transit_subnets TEXT NOT NULL "
            "DEFAULT '[]'")
        db.commit()
    # --- end migration [TRANSIT_SUBNETS_V1_FIX1] -------------------------

    # --- migration [GUID_IDENTITY_V1] -----------------------------------
    # Add nodes.guid (the install-time identity that replaces MAC entirely),
    # then ONE-TIME reap every GUID-less active node.  Rationale: GUID is now
    # the sole identity key with no fallback, so a row with guid='' can never
    # be matched on a return — it would orphan forever and pollute the mesh.
    # The fleet is small; a clean cutover (every node re-registers once with a
    # freshly generated GUID) is correct and cheaper than carrying a heuristic.
    # Cascades to the row's tunnels and chorus_members so no dangling FK or
    # ghost wg-iface bookkeeping survives.
    cols = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
    if "guid" not in cols:
        log.info("DB MIGRATION [GUID_IDENTITY_V1]: adding nodes.guid column")
        db.execute("ALTER TABLE nodes ADD COLUMN guid TEXT NOT NULL DEFAULT ''")
        db.commit()
        orphans = db.execute(
            "SELECT id, name FROM nodes WHERE active=1 AND "
            "(guid IS NULL OR guid='')").fetchall()
        if orphans:
            ids = [r[0] for r in orphans]
            log.info("DB MIGRATION [GUID_IDENTITY_V1]: reaping %d GUID-less "
                     "active node(s): %s", len(ids),
                     ", ".join(f"{r[1]}(id={r[0]})" for r in orphans))
            qmarks = ",".join("?" * len(ids))
            # tunnels touching any reaped node
            db.execute(
                f"DELETE FROM tunnels WHERE host_node_id IN ({qmarks}) "
                f"OR join_node_id IN ({qmarks})", (*ids, *ids))
            db.execute(
                f"DELETE FROM chorus_members WHERE node_id IN ({qmarks})", ids)
            db.execute(
                f"DELETE FROM node_ip_alloc WHERE pubkey IN "
                f"(SELECT pubkey FROM nodes WHERE id IN ({qmarks}))", ids)
            db.execute(f"DELETE FROM nodes WHERE id IN ({qmarks})", ids)
            db.commit()
    # Partial unique index: a GUID is unique among active rows in a pond.
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_nodes_pond_guid_active "
        "ON nodes(pond_id, guid) WHERE guid != '' AND active = 1")
    db.commit()
    # --- end migration [GUID_IDENTITY_V1] -------------------------------

    # --- migration: ponds.allow_user_choruses ----------------------------
    pcols = {row[1] for row in db.execute("PRAGMA table_info(ponds)")}
    if "allow_user_choruses" not in pcols:
        log.info("DB MIGRATION [USER_CHORUSES]: "
                 "adding ponds.allow_user_choruses column")
        db.execute(
            "ALTER TABLE ponds ADD COLUMN allow_user_choruses "
            "INTEGER NOT NULL DEFAULT 0")
        db.commit()
    # --- end migration ---------------------------------------------------

    # --- migration [NAME_UNIQUE_V1] --------------------------------------
    # Before today's fix, register_node could insert a second row with the
    # same (pond_id, name) when a node re-registered with a new pubkey.
    # That produced orphan rows whose tunnels never got cleaned up.
    # 1. Deduplicate: for any (pond_id, name) with multiple active rows,
    #    keep the most recent (highest id) and deactivate the rest.
    # 2. Create UNIQUE INDEX so future duplicates are rejected at the
    #    schema layer.
    have_idx = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_nodes_pond_name_active'"
    ).fetchone()
    if not have_idx:
        log.info("DB MIGRATION [NAME_UNIQUE_V1]: deduplicating active rows")
        # Find dup groups
        dup_groups = db.execute(
            "SELECT pond_id, name, COUNT(*) AS n "
            "FROM nodes WHERE active=1 "
            "GROUP BY pond_id, name HAVING n > 1"
        ).fetchall()
        for g in dup_groups:
            keep = db.execute(
                "SELECT id FROM nodes "
                "WHERE active=1 AND pond_id=? AND name=? "
                "ORDER BY id DESC LIMIT 1",
                (g[0], g[1])).fetchone()
            keep_id = keep[0]
            log.info("DB MIGRATION [NAME_UNIQUE_V1]: pond=%s name=%s "
                     "keeping id=%s, deactivating older duplicates",
                     g[0], g[1], keep_id)
            db.execute(
                "UPDATE nodes SET active=0 "
                "WHERE active=1 AND pond_id=? AND name=? AND id<>?",
                (g[0], g[1], keep_id))
        db.commit()
        log.info("DB MIGRATION [NAME_UNIQUE_V1]: creating unique index")
        # Partial index: uniqueness only enforced for active rows.
        # Inactive rows can have duplicate (pond_id, name).
        db.execute(
            "CREATE UNIQUE INDEX idx_nodes_pond_name_active "
            "ON nodes(pond_id, name) WHERE active=1")
        db.commit()
    # --- end migration [NAME_UNIQUE_V1] ----------------------------------

    # --- migration [MAC_IDENTITY_V1] -------------------------------------
    # Add mac column for the MAC-keyed identity model.  MAC is the
    # canonical primary identity for a node, since name/IP/pubkey can all
    # change while the hardware stays the same.  Empty string = unknown
    # (grandfathered row from before this migration); the partial UNIQUE
    # index excludes empty MACs so legacy rows don't collide.  Once a row
    # has a non-empty MAC, it must be unique within its pond.
    have_mac_col = any(
        r[1] == "mac"
        for r in db.execute("PRAGMA table_info(nodes)").fetchall()
    )
    if not have_mac_col:
        log.info("DB MIGRATION [MAC_IDENTITY_V1]: adding nodes.mac column")
        db.execute("ALTER TABLE nodes ADD COLUMN mac TEXT NOT NULL DEFAULT ''")
        db.commit()
    have_mac_idx = db.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='index' AND name='idx_nodes_pond_mac_active'"
    ).fetchone()
    if not have_mac_idx:
        log.info("DB MIGRATION [MAC_IDENTITY_V1]: creating partial UNIQUE "
                 "index on (pond_id, mac) WHERE active=1 AND mac != ''")
        db.execute(
            "CREATE UNIQUE INDEX idx_nodes_pond_mac_active "
            "ON nodes(pond_id, mac) WHERE active=1 AND mac != ''")
        db.commit()
    # --- end migration [MAC_IDENTITY_V1] ---------------------------------

    # --- migration [NODE_LAST_SEEN_V1] -----------------------------------
    # Per-node "last contacted broker" timestamp, bumped on every
    # authenticated hit (register, my_channels, update_subnets).  Used
    # by _compute_aggregate to filter chorus-mates the broker hasn't
    # heard from in > _NODE_STALE_AFTER_SEC, and by the node-staleness
    # reaper to destroy tunnels touching evicted nodes.  Default 0
    # means "never seen since this column existed"; the first poll
    # bumps it to a real time, after which steady-state operation
    # carries it forward.  Nodes that have never polled (no WAN, never
    # configured, etc.) stay at 0 and never appear in any chorus-mate
    # peer set.
    if "last_seen_at" not in cols:
        log.info("DB MIGRATION [NODE_LAST_SEEN_V1]: "
                 "adding nodes.last_seen_at column")
        db.execute(
            "ALTER TABLE nodes ADD COLUMN last_seen_at INTEGER "
            "NOT NULL DEFAULT 0")
        db.commit()
    # --- end migration [NODE_LAST_SEEN_V1] -------------------------------

#

    _init_subnet_pool(db)

    # [RECONFIG_ARCHIVE_V1] One-time backfill: any active rows that share
    # MAC with active rows in another pond are same-hardware-multiple-pond
    # ghosts.  Keep the newest in each cluster, archive the rest.  Safe
    # on every startup (no-op if no duplicates exist).
    db.row_factory = sqlite3.Row
    try:
        _backfill_reconfigured_nodes(db)
    except Exception as e:
        log.error("[RECONFIG_ARCHIVE_V1] startup backfill failed: %s", e)

    # [RECONFIG_ARCHIVE_V1_1] Tag any active=0 rows whose pubkey doesn't
    # already start with 'RETIRED-' so the global UNIQUE(pubkey) slot is
    # freed.  Handles pre-V1.1 retired rows (those left by the original
    # register handler's UPDATE active=0 calls, or by hand) that would
    # otherwise block future registers with matching pubkeys.
    try:
        _backfill_tag_retired_pubkeys(db)
    except Exception as e:
        log.error("[RECONFIG_ARCHIVE_V1_1] startup pubkey tag backfill "
                  "failed: %s", e)

    # [TUNNEL_ID_KEY_V1] Re-key tunnels on the stable node-id pair (drops the
    # legacy UNIQUE(pond_id,name) and collapses any rename-orphaned duplicates).
    _migrate_tunnel_id_key(db)

    db.close()
    log.info("Database initialized at %s", DB_PATH)


def _migrate_tunnel_id_key(db):
    """[TUNNEL_ID_KEY_V1] Make the tunnel's identity the unordered node-id pair
    instead of its name. Idempotent; safe every startup.

    Why: _deterministic_tunnel_name derives from node NAMES, which are mutable
    (a node is revived by guid across a rename). The old schema keyed tunnels on
    UNIQUE(pond_id,name) and _ensure_tunnel matched by name, so renaming a node
    orphaned its tunnels — the match returned a stale-named row bound to a
    different node-id, whose wg pubkey then never matched the real owner.

    Steps:
      1. Collapse active duplicates that share (pond_id, node-id pair). Keep the
         row whose stored name already matches its current binding (else the
         lowest id); delete the rest. (DB-only; _restore_state reaps the orphan
         wg ifaces on the same startup.)
      2. Drop the legacy UNIQUE(pond_id,name) by rebuilding the table (only when
         the constraint is still present). Ids are preserved so bindings hold.
      3. Enforce uniqueness on the active node-id pair.
    """
    # 1. collapse active duplicates per (pond_id, unordered node-id pair)
    dups = db.execute(
        "SELECT pond_id, MIN(host_node_id,join_node_id) AS lo, "
        "       MAX(host_node_id,join_node_id) AS hi, COUNT(*) AS n "
        "FROM tunnels WHERE active=1 "
        "GROUP BY pond_id, lo, hi HAVING n>1").fetchall()
    for g in dups:
        rows = db.execute(
            "SELECT t.*, hn.name AS hnow, jn.name AS jnow FROM tunnels t "
            "JOIN nodes hn ON hn.id=t.host_node_id "
            "JOIN nodes jn ON jn.id=t.join_node_id "
            "WHERE t.active=1 AND t.pond_id=? "
            "AND MIN(t.host_node_id,t.join_node_id)=? "
            "AND MAX(t.host_node_id,t.join_node_id)=?",
            (g["pond_id"], g["lo"], g["hi"])).fetchall()

        def _matches(r):
            return r["name"] in (f"{r['hnow']}-to-{r['jnow']}",
                                 f"{r['jnow']}-to-{r['hnow']}")

        keep = next((r for r in rows if _matches(r)), None) \
            or min(rows, key=lambda r: r["id"])
        drop = [r["id"] for r in rows if r["id"] != keep["id"]]
        if drop:
            log.info("DB MIGRATION [TUNNEL_ID_KEY_V1]: pond=%s pair=(%s,%s) "
                     "keep id=%s drop %s", g["pond_id"], g["lo"], g["hi"],
                     keep["id"], drop)
            qm = ",".join("?" * len(drop))
            db.execute(f"DELETE FROM tunnels WHERE id IN ({qm})", drop)
    db.commit()

    # 2. drop legacy UNIQUE(pond_id,name) by rebuilding the table, if present
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tunnels'"
    ).fetchone()
    if row and "UNIQUE(pond_id, name)" in (row["sql"] or ""):
        log.info("DB MIGRATION [TUNNEL_ID_KEY_V1]: rebuilding tunnels to drop "
                 "legacy UNIQUE(pond_id,name)")
        db.commit()                       # close any open txn before PRAGMA
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
            CREATE TABLE tunnels_idkey (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pond_id         INTEGER NOT NULL REFERENCES ponds(id),
                name            TEXT    NOT NULL,
                host_node_id    INTEGER NOT NULL REFERENCES nodes(id),
                join_node_id    INTEGER NOT NULL REFERENCES nodes(id),
                tunnel_index    INTEGER NOT NULL,
                host_wg_iface   TEXT    NOT NULL,
                host_wg_port    INTEGER NOT NULL,
                host_transit_ip TEXT    NOT NULL,
                host_edge_ip    TEXT    NOT NULL,
                host_privkey    TEXT    NOT NULL,
                host_pubkey     TEXT    NOT NULL,
                join_wg_iface   TEXT    NOT NULL,
                join_wg_port    INTEGER NOT NULL,
                join_transit_ip TEXT    NOT NULL,
                join_edge_ip    TEXT    NOT NULL,
                join_privkey    TEXT    NOT NULL,
                join_pubkey     TEXT    NOT NULL,
                transit_mask    INTEGER NOT NULL DEFAULT 30,
                created_at      REAL    NOT NULL,
                active          INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO tunnels_idkey SELECT
                id, pond_id, name, host_node_id, join_node_id, tunnel_index,
                host_wg_iface, host_wg_port, host_transit_ip, host_edge_ip,
                host_privkey, host_pubkey, join_wg_iface, join_wg_port,
                join_transit_ip, join_edge_ip, join_privkey, join_pubkey,
                transit_mask, created_at, active
            FROM tunnels;
            DROP TABLE tunnels;
            ALTER TABLE tunnels_idkey RENAME TO tunnels;
        """)
        db.execute("PRAGMA foreign_keys=ON")
        db.commit()

    # 3. enforce uniqueness on the active node-id pair
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tunnels_pond_pair_active "
        "ON tunnels(pond_id, MIN(host_node_id,join_node_id), "
        "MAX(host_node_id,join_node_id)) WHERE active=1")
    db.commit()


# ---------------------------------------------------------------------------
# Subnet pool  10.254.0.0/24 through 10.254.255.0/24
# ---------------------------------------------------------------------------

def _init_subnet_pool(db):
    """Populate subnet_pool on first run. Idempotent."""
    db.row_factory = sqlite3.Row
    count = db.execute("SELECT COUNT(*) as n FROM subnet_pool").fetchone()["n"]
    if count >= 256:
        return
    for octet in range(256):
        db.execute(
            "INSERT OR IGNORE INTO subnet_pool (id, subnet, rotation_seq) VALUES (?,?,?)",
            (octet, f"10.254.{octet}.0/24", octet))
    db.commit()
    log.info("Subnet pool initialised (256 x /24 from 10.254.0.0/16)")


def _alloc_chorus_subnet(db) -> dict:
    """Take the next free /24 (lowest rotation_seq). Does NOT claim it."""
    row = db.execute(
        "SELECT * FROM subnet_pool WHERE chorus_id IS NULL "
        "ORDER BY rotation_seq ASC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("Chorus subnet pool exhausted")
    return dict(row)


def _claim_chorus_subnet(db, pool_id: int, chorus_id: int):
    db.execute("UPDATE subnet_pool SET chorus_id=? WHERE id=?",
               (chorus_id, pool_id))
    db.commit()


def _free_chorus_subnet(db, chorus_id: int):
    """Release subnet to end of the rotation queue."""
    mx = db.execute(
        "SELECT MAX(rotation_seq) as mx FROM subnet_pool"
    ).fetchone()["mx"] or 0
    db.execute(
        "UPDATE subnet_pool SET chorus_id=NULL, rotation_seq=? WHERE chorus_id=?",
        (mx + 1, chorus_id))
    db.commit()


def _alloc_chorus_ip(db, chorus_id: int, subnet: str) -> str:
    """
    Allocate the next host address in a chorus /24.
    .1 is always reserved for FrogNetHost.
    IPs are never reused within a chorus lifetime.
    """
    net  = ipaddress.ip_network(subnet, strict=False)
    used = {
        row["chorus_ip"]
        for row in db.execute(
            "SELECT chorus_ip FROM chorus_members WHERE chorus_id=?",
            (chorus_id,)).fetchall()
        if row["chorus_ip"]
    }
    for host in net.hosts():
        ip_str = str(host)
        if ip_str.endswith(".1"):
            continue
        if ip_str not in used:
            return ip_str
    raise RuntimeError(f"No free IPs in chorus subnet {subnet}")


# ---------------------------------------------------------------------------
# Node IP allocation
# ---------------------------------------------------------------------------

def _nth_node_ip(base_ip: str, increment: int, n: int) -> str:
    """
    Compute nth address from base using third-octet stepping.
    base=10.101.100.1, increment=10, n=1 -> 10.101.110.1
    Carry propagates into second octet automatically.
    """
    base_int = int(ipaddress.ip_address(base_ip))
    return str(ipaddress.ip_address(base_int + n * increment * 256))


def _next_node_ip(db, pond: sqlite3.Row) -> str:
    """Return next unallocated node IP for this pond."""
    count = db.execute(
        "SELECT COUNT(*) as n FROM node_ip_alloc WHERE pond_id=?",
        (pond["id"],)).fetchone()["n"]
    if count >= pond["max_users"]:
        raise RuntimeError(f"Pond '{pond['name']}' is full ({pond['max_users']} users)")
    idx = count
    while True:
        ip = _nth_node_ip(pond["node_base"], pond["node_increment"], idx)
        if not db.execute(
                "SELECT id FROM node_ip_alloc WHERE pond_id=? AND ip=?",
                (pond["id"], ip)).fetchone():
            return ip
        idx += 1
        if idx > pond["max_users"] + 256:
            raise RuntimeError("Cannot find free node IP in pond address space")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h    = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{h}:{salt}"


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return True
    try:
        h, salt = stored.split(":", 1)
    except ValueError:
        return False
    return secrets.compare_digest(
        h, hashlib.sha256((password + salt).encode()).hexdigest())


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _generate_token():
    return secrets.token_urlsafe(32)


def _normalise(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _check_admin_token(authorization: Optional[str]):
    if not ADMIN_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(403, "Missing Authorization header")
    if not secrets.compare_digest(authorization[7:], ADMIN_TOKEN):
        raise HTTPException(403, "Invalid admin token")


def _get_node_by_pubkey(db, pubkey: str) -> sqlite3.Row:
    node = db.execute(
        "SELECT * FROM nodes WHERE pubkey=? AND active=1", (pubkey,)
    ).fetchone()
    if not node:
        raise HTTPException(404, "Node not found — run frognet_tunnel_setup.sh first")
    return node


def _get_pond(db, pond_id: int) -> sqlite3.Row:
    pond = db.execute(
        "SELECT * FROM ponds WHERE id=? AND active=1", (pond_id,)
    ).fetchone()
    if not pond:
        raise HTTPException(404, "Pond not found")
    return pond


def _next_pond_index(db) -> int:
    row = db.execute("SELECT MAX(pond_index) as mx FROM ponds").fetchone()
    nxt = 0 if row["mx"] is None else row["mx"] + 1
    if (nxt * 4) + 200 > 253:
        raise RuntimeError("Maximum pond count reached")
    return nxt

def _next_tunnel_index(db, pond_id) -> int:
    row = db.execute(
        "SELECT MAX(tunnel_index) as mx FROM tunnels WHERE pond_id=?",
        (pond_id,)).fetchone()
    return 0 if row["mx"] is None else row["mx"] + 1


def _compute_tunnel_ips(pond_index: int, tunnel_index: int):
    tunnels_per_block = 31
    block_offset = tunnel_index // tunnels_per_block
    local_index  = tunnel_index %  tunnels_per_block

    third = 200 + (pond_index * 4) + block_offset
    if third > 253:
        raise ValueError(f"tunnel_index {tunnel_index} exceeds transit address space")

    base = local_index * 8
    return (
        f"10.253.{third}.{base + 1}",
        f"10.253.{third}.{base + 2}",
        f"10.253.{third}.{base + 5}",
        f"10.253.{third}.{base + 6}",
        30,
    )

def _wg_iface_name(pond_name: str, suffix: str) -> str:
    h = hashlib.sha256(pond_name.encode()).hexdigest()[:7]
    return f"wg_{h}_{suffix}"


def _used_wg_ports(db) -> set:
    ports = set()
    for col in ("host_wg_port", "join_wg_port"):
        for row in db.execute(
                f"SELECT {col} FROM tunnels WHERE active=1").fetchall():
            ports.add(row[0])

    def _scan(netns=None):
        cmd = ["ss", "-ulnH"]
        if netns:
            cmd = ["ip", "netns", "exec", netns] + cmd
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        found = set()
        for line in r.stdout.split("\n"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    found.add(int(parts[3].rsplit(":", 1)[-1]))
                except (ValueError, IndexError):
                    pass
        return found

    ports |= _scan()
    nr = subprocess.run(
        ["ip", "netns", "list"], capture_output=True, text=True, check=False)
    for line in nr.stdout.split("\n"):
        nm = line.split()[0] if line.strip() else ""
        if nm.startswith("pond_") or nm.startswith("pondv4_") or nm.startswith("grp_"):
            ports |= _scan(nm)
    return ports


def _alloc_wg_port(db, exclude=None) -> int:
    used = _used_wg_ports(db)
    if exclude:
        used |= exclude
    for port in range(WG_PORT_BASE, WG_PORT_MAX + 1):
        if port not in used:
            return port
    raise RuntimeError(f"WireGuard port range exhausted ({WG_PORT_BASE}-{WG_PORT_MAX})")


def _subnet_int(subnet: str) -> int:
    try:
        return int(ipaddress.ip_network(subnet, strict=False).network_address)
    except ValueError:
        return 0


def _deterministic_tunnel_name(a_name, a_int, b_name, b_int) -> str:
    if a_int < b_int:
        return f"{a_name}-to-{b_name}"
    return f"{b_name}-to-{a_name}"


def _validate_subnet(subnet: str) -> str:
    try:
        return str(ipaddress.ip_network(subnet, strict=False))
    except ValueError as e:
        raise ValueError(f"Invalid subnet: {e}")


# ---------------------------------------------------------------------------
# Transit-subnets helpers [TRANSIT_SUBNETS_V1]
#
# A node's "transit_subnets" is a JSON list of /24 CIDRs this node can
# forward to on its LAN but does not own.  Every broker code path that
# currently handles node["subnet"] is extended to also handle the
# transit list via _node_subnets_for_side().
# ---------------------------------------------------------------------------

# Hard cap to prevent a malicious or misbehaving node from flooding the
# broker with route installs.
_TRANSIT_MAX_ENTRIES = 64


def _parse_transit(node_row) -> list:
    """Extract transit_subnets from a nodes row.  Tolerates pre-migration
    rows that lack the column and malformed JSON (logged, treated as
    empty)."""
    try:
        raw = node_row["transit_subnets"]
    except (IndexError, KeyError):
        return []
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [s for s in val if isinstance(s, str) and s]
        log.warning("_parse_transit: non-list value for node id=%s: %r",
                    node_row["id"] if "id" in node_row.keys() else "?", val)
        return []
    except (TypeError, ValueError) as e:
        log.warning("_parse_transit: malformed JSON for node id=%s: %s",
                    node_row["id"] if "id" in node_row.keys() else "?", e)
        return []


def _validate_transit_list(candidates, own_subnet: str) -> list:
    """Validate, normalise, dedupe.  Raises HTTPException on bad input.
    Returns a sorted list of canonical CIDR strings."""
    if not isinstance(candidates, list):
        raise HTTPException(400, "transit_subnets must be a list")
    if len(candidates) > _TRANSIT_MAX_ENTRIES:
        raise HTTPException(
            400, f"transit_subnets exceeds max {_TRANSIT_MAX_ENTRIES}")
    out = set()
    for s in candidates:
        if not isinstance(s, str):
            raise HTTPException(400, f"transit_subnets entry not a string: {s!r}")
        try:
            normalised = _validate_subnet(s)
        except ValueError as e:
            raise HTTPException(400, f"transit_subnets: {e}")
        net = ipaddress.ip_network(normalised, strict=False)
        if not str(net.network_address).startswith("10."):
            raise HTTPException(
                400, f"transit_subnets: {normalised} not in 10.0.0.0/8")
        if str(net.network_address).startswith("10.253.") or \
           str(net.network_address).startswith("10.254."):
            raise HTTPException(
                400, f"transit_subnets: {normalised} uses a reserved range")
        if own_subnet and normalised == own_subnet:
            # Silently drop — asking to transit your own subnet is a no-op.
            continue
        out.add(normalised)
    return sorted(out)


def _transit_conflict(db, pond_id: int, excl_node_id: int,
                      subnets: list) -> list:
    """Return list of (subnet, conflicting_node_name, kind) for any
    subnet already owned or declared transit by a DIFFERENT active node
    in the same pond.  Empty list = no conflicts."""
    if not subnets:
        return []
    conflicts = []
    others = db.execute(
        "SELECT id, name, subnet, transit_subnets FROM nodes "
        "WHERE pond_id=? AND id!=? AND active=1",
        (pond_id, excl_node_id)).fetchall()
    want = set(subnets)
    # [TRANSIT_OWNED_OK_V1] A gateway legitimately claims a LAN-peer's /24
    # as transit even when that /24 is another node's owned subnet
    # (e.g. Seattle1 announcing Seattle3/Seattle4 as transit).  Only
    # transit-vs-transit collisions are real conflicts.
    for other in others:
        for ts in _parse_transit(other):
            if ts in want:
                conflicts.append((ts, other["name"], "transit"))
    return conflicts


def _node_subnets_for_side(node_row) -> list:
    """Return [owned] + transit_subnets, deduped, omitting falsey
    values.  Used by every route-install site so a single helper
    governs what gets programmed per tunnel side."""
    seen = set()
    out = []
    owned = node_row["subnet"] if "subnet" in node_row.keys() else ""
    if owned:
        seen.add(owned)
        out.append(owned)
    for ts in _parse_transit(node_row):
        if ts and ts not in seen:
            seen.add(ts)
            out.append(ts)
    return out


# ---------------------------------------------------------------------------
# [RECONFIG_ARCHIVE_V1] Cross-pond node migration support
#
# When the register handler sees an active node row in some OTHER pond
# with the same MAC as the incoming request, it treats it as a node
# migrating between ponds.  The old pond's tunnels for that node get
# destroyed, the node's pond-scoped state (label, subnet, transit, and
# non-entire_pond chorus memberships) is archived in
# reconfigured_nodes / reconfigured_chorus_members, and the old node
# row is retired.
#
# When the same node later returns to a pond it was previously in,
# _restore_reconfigured_state finds the archive by (pond_id, mac) and
# re-runs _join_chorus for each archived chorus (fresh chorus_ip
# allocation per design call #2).  Archive entries are then deleted.
#
# Archive-fill (label / transit_subnets): if the request body omits
# transit_subnets (request_provided_transit=False) and the archive has
# a non-empty value, the archive fills the gap.  Same for label when
# req.label is empty.  Request always wins when it provides a value,
# even an empty list/string (design call #1).
# ---------------------------------------------------------------------------

def _archive_node_for_reconfig(db, old_node, old_pond) -> int:
    """Archive a node's pond-scoped state and non-entire_pond chorus
    memberships before retiring the row.  Idempotent: re-archiving for
    the same (pond_id, mac) updates the existing entry and rewrites the
    chorus-member archive.  Returns the reconfigured_nodes row id."""
    mac = old_node["mac"] or ""
    if not mac:
        log.warning("[RECONFIG_ARCHIVE_V1] cannot archive node id=%d "
                    "(name=%s) — empty mac",
                    old_node["id"], old_node["name"])
        return 0
    db.execute(
        "INSERT INTO reconfigured_nodes "
        "(pond_id, mac, name, label, subnet, transit_subnets, "
        " pubkey_at_leave, orig_node_id, archived_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(pond_id, mac) DO UPDATE SET "
        "  name=excluded.name, "
        "  label=excluded.label, "
        "  subnet=excluded.subnet, "
        "  transit_subnets=excluded.transit_subnets, "
        "  pubkey_at_leave=excluded.pubkey_at_leave, "
        "  orig_node_id=excluded.orig_node_id, "
        "  archived_at=excluded.archived_at",
        (old_pond["id"], mac, old_node["name"], old_node["label"] or "",
         old_node["subnet"] or "",
         old_node["transit_subnets"] or "[]",
         old_node["pubkey"] or "", old_node["id"], time.time()))
    arch_id = db.execute(
        "SELECT id FROM reconfigured_nodes WHERE pond_id=? AND mac=?",
        (old_pond["id"], mac)).fetchone()["id"]
    # Wipe any prior chorus archive for this entry, then re-archive
    db.execute(
        "DELETE FROM reconfigured_chorus_members "
        "WHERE reconfigured_node_id=?", (arch_id,))
    now = time.time()
    archived = 0
    for cm in db.execute(
        "SELECT cm.chorus_id, cm.chorus_ip, c.name AS cname "
        "FROM chorus_members cm "
        "JOIN choruses c ON c.id = cm.chorus_id "
        "WHERE cm.node_id=? AND c.name != 'entire_pond'",
        (old_node["id"],)).fetchall():
        db.execute(
            "INSERT INTO reconfigured_chorus_members "
            "(reconfigured_node_id, chorus_id, chorus_ip, archived_at) "
            "VALUES (?,?,?,?)",
            (arch_id, cm["chorus_id"], cm["chorus_ip"] or "", now))
        archived += 1
    log.info("[RECONFIG_ARCHIVE_V1] archived node id=%d mac=%s "
             "pond=%s (name=%s, subnet=%s, transit=%s, label=%r) "
             "with %d chorus(es)",
             old_node["id"], mac, old_pond["name"], old_node["name"],
             old_node["subnet"], old_node["transit_subnets"] or "[]",
             old_node["label"] or "", archived)
    return arch_id


def _restore_reconfigured_state(db, node, pond) -> list:
    """If this node has an archive for (pond_id, mac), restore the
    archived non-entire_pond chorus memberships via _join_chorus.
    Choruses that no longer exist (deleted while the node was away)
    are skipped with a log line.  Archive entries are deleted after a
    successful restore pass.  Returns the list of restored chorus
    names."""
    mac = node["mac"] or ""
    if not mac:
        return []
    arch = db.execute(
        "SELECT * FROM reconfigured_nodes WHERE pond_id=? AND mac=?",
        (pond["id"], mac)).fetchone()
    if not arch:
        return []
    restored = []
    skipped = []
    archived_rows = db.execute(
        "SELECT * FROM reconfigured_chorus_members "
        "WHERE reconfigured_node_id=?", (arch["id"],)).fetchall()
    for am in archived_rows:
        chorus = db.execute(
            "SELECT * FROM choruses WHERE id=? AND active=1",
            (am["chorus_id"],)).fetchone()
        if not chorus:
            skipped.append(am["chorus_id"])
            log.info("[RECONFIG_RESTORE_V1] node id=%d (name=%s) "
                     "skipping archived chorus_id=%d — no longer exists",
                     node["id"], node["name"], am["chorus_id"])
            continue
        try:
            _join_chorus(db, chorus, node)
            restored.append(chorus["name"])
            log.info("[RECONFIG_RESTORE_V1] node id=%d (name=%s) rejoined "
                     "chorus '%s' (orig chorus_ip=%s, fresh-allocated)",
                     node["id"], node["name"], chorus["name"],
                     am["chorus_ip"] or "<empty>")
        except Exception as e:
            log.warning("[RECONFIG_RESTORE_V1] node id=%d (name=%s) "
                        "failed to rejoin chorus '%s': %s",
                        node["id"], node["name"], chorus["name"], e)
    db.execute(
        "DELETE FROM reconfigured_chorus_members "
        "WHERE reconfigured_node_id=?", (arch["id"],))
    db.execute(
        "DELETE FROM reconfigured_nodes WHERE id=?", (arch["id"],))
    log.info("[RECONFIG_RESTORE_V1] node id=%d (name=%s) restored from "
             "archive (was archived %ds ago); rejoined %d chorus(es), "
             "skipped %d gone; archive deleted",
             node["id"], node["name"],
             int(time.time() - arch["archived_at"]),
             len(restored), len(skipped))
    return restored


def _archive_fill_defaults(db, pond_id, mac, label, transit_json_new,
                           request_provided_transit):
    """Archive-fill for INSERT path: when the request body omits transit
    or label, fall back to the archived value.  Returns the (possibly
    overridden) (label, transit_json_new, request_provided_transit).

    Per design (call #1): request wins when provided (even if empty);
    archive fills the gap when request omits the field.
    For label, "omits" is operationalised as empty/whitespace string."""
    if not mac:
        return label, transit_json_new, request_provided_transit
    arch = db.execute(
        "SELECT label, transit_subnets FROM reconfigured_nodes "
        "WHERE pond_id=? AND mac=?", (pond_id, mac)).fetchone()
    if not arch:
        return label, transit_json_new, request_provided_transit
    if not request_provided_transit and (arch["transit_subnets"] or "[]") != "[]":
        log.info("[RECONFIG_ARCHIVE_V1] request omitted transit_subnets; "
                 "filling from archive: %s", arch["transit_subnets"])
        transit_json_new = arch["transit_subnets"]
        request_provided_transit = True
    if not (label or "").strip() and (arch["label"] or "").strip():
        log.info("[RECONFIG_ARCHIVE_V1] request label empty; filling "
                 "from archive: %r", arch["label"])
        label = arch["label"]
    return label, transit_json_new, request_provided_transit


def _backfill_reconfigured_nodes(db):
    """One-time pass at startup: find active node rows that share MAC
    with active rows in OTHER ponds — same hardware split across
    multiple pond_ids, only one of which is current.  Keep the most
    recently created row in each MAC-cluster; archive the rest.

    Safe to run repeatedly: only matches currently-active duplicate
    clusters.  Skips rows with empty MAC (legacy / MAC-less)."""
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT mac, COUNT(DISTINCT pond_id) AS pondcount "
        "FROM nodes WHERE active=1 AND mac != '' "
        "GROUP BY mac HAVING pondcount > 1").fetchall()
    if not rows:
        return
    log.info("[RECONFIG_ARCHIVE_V1] startup backfill: %d mac(s) span "
             "multiple ponds", len(rows))
    for r in rows:
        mac = r["mac"]
        cluster = db.execute(
            "SELECT * FROM nodes WHERE mac=? AND active=1 "
            "ORDER BY created_at DESC", (mac,)).fetchall()
        keep = cluster[0]
        log.info("[RECONFIG_ARCHIVE_V1] backfill mac=%s: keeping id=%d "
                 "(pond_id=%d, created_at=%.0f); archiving %d older row(s)",
                 mac, keep["id"], keep["pond_id"], keep["created_at"],
                 len(cluster) - 1)
        for old in cluster[1:]:
            try:
                old_pond = _get_pond(db, old["pond_id"])
            except Exception as e:
                log.error("[RECONFIG_ARCHIVE_V1] backfill: cannot resolve "
                          "pond_id=%d for node id=%d: %s — skipping",
                          old["pond_id"], old["id"], e)
                continue
            stale_tunnels = db.execute(
                "SELECT * FROM tunnels WHERE active=1 AND "
                "(host_node_id=? OR join_node_id=?)",
                (old["id"], old["id"])).fetchall()
            for t in stale_tunnels:
                try:
                    _destroy_tunnel(db, t, old_pond)
                    log.info("[RECONFIG_ARCHIVE_V1] backfill: destroyed "
                             "old-pond tunnel id=%d name=%s",
                             t["id"], t["name"])
                except Exception as e:
                    log.error("[RECONFIG_ARCHIVE_V1] backfill: destroy "
                              "tunnel id=%d failed: %s", t["id"], e)
            _archive_node_for_reconfig(db, old, old_pond)
            db.execute(
                "DELETE FROM chorus_members WHERE node_id=?",
                (old["id"],))
            db.execute(
                "UPDATE nodes SET active=0, "
                "name = name || '.retired.' || id WHERE id=? AND active=1",
                (old["id"],))
            _tag_retired_pubkey(db, old["id"])
        db.commit()


def _tag_retired_pubkey(db, node_id: int) -> None:
    """[RECONFIG_ARCHIVE_V1_1] Free the global UNIQUE(pubkey) slot held by
    a retired row.  The schema has `pubkey TEXT NOT NULL UNIQUE` with no
    `WHERE active=1` partial-index — so an active=0 row still locks its
    pubkey.  When a node returns to the broker with the same pubkey (or
    when a fresh node generates a key that collides with a retired row's
    pubkey), the register handler's UPDATE/INSERT fails with
    sqlite3.IntegrityError.

    Tag the pubkey by prepending 'RETIRED-<id>-' and truncating, which
    is guaranteed unique per id and keeps the original prefix for
    forensics.  Idempotent: a re-call on an already-tagged row is a
    no-op.

    Called from every retire site in the register handler, and from
    the startup backfill _backfill_tag_retired_pubkeys."""
    row = db.execute(
        "SELECT pubkey FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        return
    pk = row["pubkey"] or ""
    if pk.startswith("RETIRED-"):
        return
    tagged = f"RETIRED-{node_id}-{pk[:32]}"
    db.execute("UPDATE nodes SET pubkey=? WHERE id=?", (tagged, node_id))
    log.info("[RECONFIG_ARCHIVE_V1_1] tagged retired pubkey for "
             "node id=%d (original prefix=%s...)", node_id, pk[:16])


def _backfill_tag_retired_pubkeys(db) -> None:
    """[RECONFIG_ARCHIVE_V1_1] Startup backfill: tag the pubkey on every
    active=0 row that still holds an untagged pubkey.  These are
    leftovers from before V1.1: rows retired by the original register
    handler's `UPDATE nodes SET active=0` calls (or by hand) that left
    their original pubkey in place, occupying the UNIQUE slot.

    Idempotent: only matches active=0 rows whose pubkey does NOT yet
    have the RETIRED- prefix."""
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, pubkey FROM nodes "
        "WHERE active=0 AND pubkey NOT LIKE 'RETIRED-%'"
    ).fetchall()
    if not rows:
        return
    log.info("[RECONFIG_ARCHIVE_V1_1] startup backfill: tagging %d "
             "retired-but-untagged pubkey(s)", len(rows))
    for r in rows:
        _tag_retired_pubkey(db, r["id"])
    db.commit()


# ---------------------------------------------------------------------------
# Tunnel lifecycle
# ---------------------------------------------------------------------------

def _destroy_tunnel(db, tunnel, pond):
    # [DESTROY_BY_IFACE_V1]
    # Route cleanup by interface, not by node lookup. Previous code
    # walked host_node_id/join_node_id → _node_subnets_for_side and
    # silently skipped when the node row was missing, leaving stale
    # routes on soon-to-be-dead ifaces. That broke Seattle4 when
    # Seattle3L (same /24) was deleted: j34's route survived and
    # ROUTE ADD SKIP blocked the replacement install.
    ns_name = pond["ns_name"]
    for port in (tunnel["host_wg_port"], tunnel["join_wg_port"]):
        try:
            ns.teardown_port_forward(port, pond["pond_index"])
        except Exception as e:
            log.warning("Port forward teardown %d: %s", port, e)
    # Flush routes BEFORE deleting the iface — more robust than relying
    # on kernel auto-removal on iface delete, and works if the iface
    # is already gone (no-op in that case due to check=False).
    for iface in (tunnel["host_wg_iface"], tunnel["join_wg_iface"]):
        ns._run(["ip", "route", "flush", "dev", iface],
                netns=ns_name, check=False)
    for iface in (tunnel["host_wg_iface"], tunnel["join_wg_iface"]):
        try:
            ns.delete_wg_interface(ns_name, iface)
        except RuntimeError as e:
            log.warning("Failed to delete %s: %s", iface, e)
    db.execute("DELETE FROM tunnels WHERE id=?", (tunnel["id"],))
    db.commit()
    log.info("Destroyed tunnel '%s'", tunnel["name"])


def _ensure_tunnel(db, pond, node_a, node_b) -> dict:
    ia = _subnet_int(node_a["subnet"])
    ib = _subnet_int(node_b["subnet"])
    host, joiner = (node_a, node_b) if ia < ib else (node_b, node_a)

    tname = _deterministic_tunnel_name(
        host["name"],   _subnet_int(host["subnet"]),
        joiner["name"], _subnet_int(joiner["subnet"]))

    # [TUNNEL_ID_KEY_V1] Identity is the UNORDERED NODE-ID PAIR, not the name.
    # _deterministic_tunnel_name derives from node NAMES, which are mutable — a
    # node is revived by guid across a rename, keeping its id but changing its
    # name. Keying the match on the name meant a renamed node's tunnels became
    # unreachable by their real owner: _ensure_tunnel matched a stale-named row
    # bound to a DIFFERENT node-id and returned it, so the broker-side wg peer
    # carried the wrong pubkey and the real node could never handshake. node-id
    # is the stable key; the name is only a label, kept current below.
    lo_id, hi_id = sorted((host["id"], joiner["id"]))

    # [ENSURE_TUNNEL_SELFHEAL_V1] Pre-flight covers active=1 AND active=0 rows
    # for this pair; an inactive row would otherwise explode the INSERT after we
    # built ifaces/peers/routes/port-forwards, orphaning them.
    existing = db.execute(
        "SELECT * FROM tunnels WHERE pond_id=? "
        "AND MIN(host_node_id, join_node_id)=? "
        "AND MAX(host_node_id, join_node_id)=?",
        (pond["id"], lo_id, hi_id)).fetchone()
    if existing:
        if existing["active"] == 1:
            # [TUNNEL_ID_KEY_V1] Keep the label current so a prior rename shows
            # on the wire and no stale name lingers in the DB. The binding (the
            # node-id pair) is unchanged; only the cosmetic name moves.
            if existing["name"] != tname:
                log.info("ENSURE_TUNNEL: relabel tunnel id=%s '%s' -> '%s' "
                         "(node rename; binding unchanged)",
                         existing["id"], existing["name"], tname)
                try:
                    db.execute("UPDATE tunnels SET name=? WHERE id=?",
                               (tname, existing["id"]))
                    db.commit()
                    existing = db.execute(
                        "SELECT * FROM tunnels WHERE id=?",
                        (existing["id"],)).fetchone()
                except sqlite3.IntegrityError as e:
                    # A legacy DB may still carry UNIQUE(pond_id,name) and a
                    # stale row could hold this label. The binding is already
                    # correct, so the label is cosmetic — keep the old one.
                    log.warning("ENSURE_TUNNEL: relabel id=%s -> '%s' blocked "
                                "(%s); binding correct, keeping old label",
                                existing["id"], tname, e)
            return dict(existing)
        # Inactive row for this pair blocks our INSERT. Destroy it (cleans up any
        # leftover namespace state from the prior incarnation) so we can recreate
        # fresh with current ports/keys.
        log.info("ENSURE_TUNNEL: inactive row id=%s name=%s exists — "
                 "destroying before re-creating",
                 existing["id"], existing["name"])
        try:
            _destroy_tunnel(db, dict(existing), pond)
        except Exception as e:
            log.warning("ENSURE_TUNNEL: _destroy_tunnel of inactive "
                        "id=%s failed: %s — forcing DB removal",
                        existing["id"], e)
            db.execute("DELETE FROM tunnels WHERE id=?",
                       (existing["id"],))
            db.commit()

    tidx      = _next_tunnel_index(db, pond["id"])
    host_port = _alloc_wg_port(db)
    join_port = _alloc_wg_port(db, exclude={host_port})

    hdrop, hedge, jdrop, jedge, mask = \
        _compute_tunnel_ips(pond["pond_index"], tidx)

    hpriv, hdpub = ns._wg_genkey()
    jpriv, jdpub = ns._wg_genkey()
    ns_name = pond["ns_name"]
    hiface  = _wg_iface_name(pond["name"], f"h{tidx}")
    jiface  = _wg_iface_name(pond["name"], f"j{tidx}")

    # [ENSURE_TUNNEL_SELFHEAL_V1] Track everything we install so we
    # can roll back if the INSERT below fails for any reason. Without
    # this, a failure here leaves wg ifaces + port forwards orphaned
    # in the namespace, and the NEXT call computes the same idx,
    # finds the orphans, deletes-and-recreates them as "stale" — a
    # cycle that masks the underlying DB inconsistency and silently
    # rotates keys on every poll.
    _installed_ifaces: list = []
    _installed_ports:  list = []
    try:
        ns.create_wg_interface(ns_name, hiface, host_port, hpriv, hdrop, mask)
        _installed_ifaces.append(hiface)
        ns.add_wg_peer(ns_name, hiface, host["pubkey"], [TUNNEL_ALLOWED_IPS])
        ns.create_wg_interface(ns_name, jiface, join_port, jpriv, jdrop, mask)
        _installed_ifaces.append(jiface)
        ns.add_wg_peer(ns_name, jiface, joiner["pubkey"], [TUNNEL_ALLOWED_IPS])

        # [TRANSIT_SUBNETS_V1] install owned + transit for each side
        # [OWNED_SUBNET_PRECEDENCE_V1] mark the side's OWN subnet owned so it
        # beats any transit claim another node made for the same /24.
        host_owned = host["subnet"] if "subnet" in host.keys() else ""
        join_owned = joiner["subnet"] if "subnet" in joiner.keys() else ""
        for s in _node_subnets_for_side(host):
            ns.add_route(ns_name, s, hedge, hiface, owned=(s == host_owned))
        for s in _node_subnets_for_side(joiner):
            ns.add_route(ns_name, s, jedge, jiface, owned=(s == join_owned))

        ns.setup_port_forward(host_port, ns_name, pond["pond_index"])
        _installed_ports.append(host_port)
        ns.setup_port_forward(join_port, ns_name, pond["pond_index"])
        _installed_ports.append(join_port)

        try:
            pub_ip = ns.get_public_ip()
        except RuntimeError:
            pub_ip = "<DROPLET_PUBLIC_IP>"

        db.execute(
            """INSERT INTO tunnels
               (pond_id, name, host_node_id, join_node_id, tunnel_index,
                host_wg_iface, host_wg_port, host_transit_ip, host_edge_ip,
                host_privkey, host_pubkey,
                join_wg_iface, join_wg_port, join_transit_ip, join_edge_ip,
                join_privkey, join_pubkey, transit_mask, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            # [IN_MEMORY_KEYS] Private keys are NEVER persisted. hpriv/jpriv are
            # generated and installed into the kernel WG iface above, but the
            # *_privkey columns are written empty. A DB dump yields no keys. On a
            # host reboot the iface is gone; _restore_state re-keys and republishes
            # the pubkey (see [IN_MEMORY_KEYS] there).
            (pond["id"], tname, host["id"], joiner["id"], tidx,
             hiface, host_port, hdrop, hedge, "", hdpub,
             jiface, join_port, jdrop, jedge, "", jdpub,
             mask, time.time()))
        db.commit()
    except sqlite3.IntegrityError as e:
        # UNIQUE constraint slipped past our pre-flight. Roll back
        # everything we installed in the namespace and re-SELECT —
        # whoever wrote the colliding row wins, return their state.
        log.error("ENSURE_TUNNEL: INSERT failed on (pond_id=%s, name=%s): "
                  "%s — rolling back namespace work and returning "
                  "winning row",
                  pond["id"], tname, e)
        _ensure_tunnel_rollback(ns_name, pond, _installed_ifaces,
                                _installed_ports)
        winning = db.execute(
            "SELECT * FROM tunnels WHERE pond_id=? "
            "AND MIN(host_node_id, join_node_id)=? "
            "AND MAX(host_node_id, join_node_id)=?",
            (pond["id"], lo_id, hi_id)).fetchone()
        if winning:
            return dict(winning)
        # No row found despite UNIQUE failure: schema invariant broken.
        raise
    except Exception as e:
        log.error("ENSURE_TUNNEL: setup failed for (pond_id=%s, name=%s): "
                  "%s — rolling back namespace work",
                  pond["id"], tname, e)
        _ensure_tunnel_rollback(ns_name, pond, _installed_ifaces,
                                _installed_ports)
        raise

    log.info("Tunnel '%s' created in %s", tname, ns_name)
    return dict(db.execute(
        "SELECT * FROM tunnels WHERE pond_id=? "
        "AND MIN(host_node_id, join_node_id)=? "
        "AND MAX(host_node_id, join_node_id)=?",
        (pond["id"], lo_id, hi_id)).fetchone())


def _ensure_tunnel_rollback(ns_name, pond, ifaces, ports):
    """[ENSURE_TUNNEL_SELFHEAL_V1] Undo partial namespace setup from a
    failed _ensure_tunnel. Best-effort: log warnings on individual
    failures but do not raise — caller is already handling an error."""
    for port in ports:
        try:
            ns.teardown_port_forward(port, pond["pond_index"])
        except Exception as e:
            log.warning("ENSURE_TUNNEL rollback: port_forward %d: %s",
                        port, e)
    for iface in ifaces:
        try:
            ns._run(["ip", "route", "flush", "dev", iface],
                    netns=ns_name, check=False)
        except Exception as e:
            log.warning("ENSURE_TUNNEL rollback: route flush %s: %s",
                        iface, e)
        try:
            ns.delete_wg_interface(ns_name, iface)
        except Exception as e:
            log.warning("ENSURE_TUNNEL rollback: delete_wg_interface "
                        "%s: %s", iface, e)


# ---------------------------------------------------------------------------
# Aggregate computation
# ---------------------------------------------------------------------------

def _compute_aggregate(db, node) -> List[dict]:
    chorus_ids = [r["chorus_id"] for r in db.execute(
        "SELECT chorus_id FROM chorus_members WHERE node_id=?",
        (node["id"],)).fetchall()]
    if not chorus_ids:
        return []

    ph   = ",".join("?" * len(chorus_ids))
    # [NODE_LAST_SEEN_V1] Only consider chorus-mates the broker has
    # heard from within the staleness window.  A node that has never
    # polled (legacy WAN host now LAN-only, decommissioned, never
    # configured) has last_seen_at=0 and is filtered out here, which
    # prevents _ensure_tunnel from re-creating tunnels for it on every
    # poll.  When the node polls again it re-enters this set
    # automatically.
    peer_cutoff = int(time.time()) - _NODE_STALE_AFTER_SEC
    peers = db.execute(
        f"""SELECT DISTINCT n.* FROM nodes n
            JOIN chorus_members cm ON cm.node_id = n.id
            WHERE cm.chorus_id IN ({ph}) AND n.id != ? AND n.active = 1
                  AND n.last_seen_at > ?""",
        (*chorus_ids, node["id"], peer_cutoff)).fetchall()
    if not peers:
        return []

    pond = _get_pond(db, node["pond_id"])
    try:
        pub_ip = ns.get_public_ip()
    except RuntimeError:
        pub_ip = "<DROPLET_PUBLIC_IP>"

    channels = []
    for peer in peers:
        t = _ensure_tunnel(db, pond, node, peer)
        if node["id"] == t["host_node_id"]:
            edge, drop, dpub, dport = (
                t["host_edge_ip"], t["host_transit_ip"],
                t["host_pubkey"],  t["host_wg_port"])
        else:
            edge, drop, dpub, dport = (
                t["join_edge_ip"], t["join_transit_ip"],
                t["join_pubkey"],  t["join_wg_port"])

        # [TRANSIT_SUBNETS_V1] remote_subnets = peer's owned + transit.
        # Backward compatible: clients ignoring extras still see the
        # owned subnet first in the list.
        remote_subnets_for_peer = _node_subnets_for_side(peer)
        channels.append({
            "channel_name": f"{peer['name']}-{peer['subnet'].replace('.0/24','')}",
            "node_name":    peer["name"],
            "label":        peer["label"],
            "subnet":       peer["subnet"],
            "transit_subnets": _parse_transit(peer),
            "tunnel_name":  t["name"],
            "wg_config": {
                "edge_ip":          edge,
                "edge_mask":        t["transit_mask"],
                "droplet_ip":       drop,
                "droplet_pubkey":   dpub,
                "droplet_endpoint": f"{pub_ip}:{dport}",
                "allowed_ips":      TUNNEL_ALLOWED_IPS,
                "remote_subnets":   remote_subnets_for_peer,
            },
        })
    return channels


def _compute_chorus_assignments(db, node) -> List[dict]:
    """Return chorus IP assignments for frognet0 virtual interface management."""
    rows = db.execute(
        """SELECT c.name, c.subnet, cm.chorus_ip
           FROM chorus_members cm
           JOIN choruses c ON c.id = cm.chorus_id
           WHERE cm.node_id = ? AND c.active = 1""",
        (node["id"],)).fetchall()
    return [
        {"chorus_name": r["name"],
         "subnet":      r["subnet"],
         "chorus_ip":   r["chorus_ip"]}
        for r in rows if r["chorus_ip"]
    ]


# ---------------------------------------------------------------------------
# Chorus join / leave helpers
# ---------------------------------------------------------------------------

def _join_chorus(db, chorus, node) -> str:
    """Idempotent. Returns assigned chorus_ip."""
    existing = db.execute(
        "SELECT chorus_ip FROM chorus_members WHERE chorus_id=? AND node_id=?",
        (chorus["id"], node["id"])).fetchone()
    if existing:
        return existing["chorus_ip"]
    ip = _alloc_chorus_ip(db, chorus["id"], chorus["subnet"])
    db.execute(
        "INSERT INTO chorus_members (chorus_id, node_id, chorus_ip, joined_at) "
        "VALUES (?,?,?,?)",
        (chorus["id"], node["id"], ip, time.time()))
    db.commit()
    log.info("Node '%s' joined chorus '%s' -> %s", node["name"], chorus["name"], ip)
    return ip


def _leave_chorus(db, chorus, node, pond):
    db.execute(
        "DELETE FROM chorus_members WHERE chorus_id=? AND node_id=?",
        (chorus["id"], node["id"]))
    db.commit()
    log.info("Node '%s' left chorus '%s'", node["name"], chorus["name"])

    # Tear down tunnels to peers no longer sharing any chorus
    tunnels = db.execute(
        "SELECT * FROM tunnels WHERE pond_id=? AND active=1 "
        "AND (host_node_id=? OR join_node_id=?)",
        (pond["id"], node["id"], node["id"])).fetchall()
    for t in tunnels:
        peer_id = (t["join_node_id"] if t["host_node_id"] == node["id"]
                   else t["host_node_id"])
        shared = db.execute(
            """SELECT COUNT(*) as n FROM chorus_members cm1
               JOIN chorus_members cm2 ON cm1.chorus_id = cm2.chorus_id
               WHERE cm1.node_id=? AND cm2.node_id=?""",
            (node["id"], peer_id)).fetchone()["n"]
        if shared == 0:
            _destroy_tunnel(db, t, pond)

    # Auto-delete chorus if empty
    remaining = db.execute(
        "SELECT COUNT(*) as n FROM chorus_members WHERE chorus_id=?",
        (chorus["id"],)).fetchone()["n"]
    if remaining == 0:
        _free_chorus_subnet(db, chorus["id"])
        db.execute("UPDATE choruses SET active=0 WHERE id=?", (chorus["id"],))
        db.commit()
        log.info("Chorus '%s' auto-deleted (no members)", chorus["name"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PondCreate(BaseModel):
    name:           str
    max_users:      int = 50
    node_base:      str = NODE_BASE_DEFAULT
    node_increment: int = NODE_INCREMENT_DEFAULT
    password:       str = ""


class AllocateIPRequest(BaseModel):
    pond_password: str = ""


class RegisterRequest(BaseModel):
    pond:            str
    pubkey:          str
    subnet:          str
    node_name:       str
    label:           str = ""
    pond_password:   str = ""
    # [GUID_IDENTITY_V1] Install-time GUID — the SOLE identity key. The broker
    # matches a returning node on (pond, guid) and revives its row. Empty guid
    # is rejected (400): a node with no identity must run `frognet-guid
    # generate`. MAC is retained on the row for diagnostics only and is no
    # longer consulted for identity.
    guid:            str = ""
    # [MAC_IDENTITY_V1 — VESTIGIAL] No longer an identity key. Stored on the
    # row if sent, ignored for matching. Kept so existing clients don't break.
    mac:             str = ""
    # [TRANSIT_SUBNETS_V1] LAN /24s this node can forward to but does
    # not own.  Optional; empty list preserves legacy behavior.
    # [TRANSIT_PRESERVE_V1] Default None means "not provided" — register
    # handler preserves DB value.  Empty list [] explicitly clears.
    transit_subnets: Optional[list] = None


class RetireGuidRequest(BaseModel):
    # [GUID_IDENTITY_V1] The regenerate path: a node rotating its identity
    # tells the broker to retire its CURRENT guid before it writes a fresh one.
    # This is the ONLY caller that retires a row by identity. Explicit and
    # precise — it names the exact guid to retire, so no name/pubkey heuristic
    # is ever involved.
    pond:   str
    guid:   str


class ChorusCreate(BaseModel):
    pubkey:   str
    name:     str
    visible:  bool = True
    password: str  = ""


class ChorusJoin(BaseModel):
    pubkey:   str
    name:     str
    password: str = ""


class ChorusLeave(BaseModel):
    pubkey: str
    name:   str


class ChorusRemoveMember(BaseModel):
    pond:      str
    chorus:    str
    node_name: str


class LeaveRequest(BaseModel):
    pubkey:      str
    tunnel_name: str


# [TRANSIT_SUBNETS_V1]
class UpdateSubnetsRequest(BaseModel):
    pubkey:          str
    transit_subnets: list = []


class DeregisterRequest(BaseModel):
    pubkey: str


# ---------------------------------------------------------------------------
# API: Bootstrap
# ---------------------------------------------------------------------------

@app.post("/api/v4/bootstrap")
async def bootstrap():
    db = get_db()
    try:
        existing = db.execute(
            "SELECT value FROM admin_config WHERE key='admin_token'"
        ).fetchone()
        if existing:
            if db.execute(
                    "SELECT value FROM admin_config "
                    "WHERE key='admin_token_claimed'").fetchone():
                raise HTTPException(403, "Broker already bootstrapped")
            db.execute(
                "INSERT INTO admin_config (key,value,created_at) "
                "VALUES ('admin_token_claimed','1',?)", (time.time(),))
            db.commit()
            return JSONResponse(
                {"admin_token": existing["value"], "status": "claimed"})

        token = _generate_token()
        for k, v in [("admin_token", token), ("admin_token_claimed", "1")]:
            db.execute(
                "INSERT INTO admin_config (key,value,created_at) VALUES (?,?,?)",
                (k, v, time.time()))
        db.commit()
        global ADMIN_TOKEN
        ADMIN_TOKEN = token
        os.environ["FROGNET_ADMIN_TOKEN"] = token
        return JSONResponse({"admin_token": token, "status": "created"},
                            status_code=201)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Admin — create pond
# ---------------------------------------------------------------------------

@app.post("/api/v4/ponds")
async def create_pond(req: PondCreate,
                      authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    name = _normalise(req.name)
    if len(name) < 2:
        raise HTTPException(400, "Pond name must be at least 2 characters")
    try:
        ipaddress.ip_address(req.node_base)
    except ValueError:
        raise HTTPException(400, f"Invalid node_base: {req.node_base}")
    if not req.node_base.endswith(".1"):
        raise HTTPException(400, "node_base must end in .1")
    if req.node_increment < 1:
        raise HTTPException(400, "node_increment must be >= 1")

    db = get_db()
    try:
        if db.execute("SELECT id FROM ponds WHERE name=?", (name,)).fetchone():
            raise HTTPException(400, f"Pond '{name}' already exists")

        pidx    = _next_pond_index(db)
        ns_name = f"pondv4_{name}"
        ns.ensure_namespace(ns_name, pidx)
        pw_hash = _hash_password(req.password) if req.password else None

        db.execute(
            "INSERT INTO ponds "
            "(name,pond_index,ns_name,max_users,node_base,node_increment,"
            " password_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, pidx, ns_name, req.max_users, req.node_base,
             req.node_increment, pw_hash, time.time()))
        db.commit()
        pond = db.execute("SELECT * FROM ponds WHERE name=?", (name,)).fetchone()

        # Auto-create entire_pond chorus with first free /24
        pool_row = _alloc_chorus_subnet(db)
        db.execute(
            "INSERT INTO choruses "
            "(pond_id,name,subnet,visible,password_hash,creator_node_id,created_at) "
            "VALUES (?,?,?,1,NULL,NULL,?)",
            (pond["id"], "entire_pond", pool_row["subnet"], time.time()))
        db.commit()
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name='entire_pond'",
            (pond["id"],)).fetchone()
        _claim_chorus_subnet(db, pool_row["id"], chorus["id"])

        log.info("Created pond '%s' (ns=%s idx=%d) entire_pond=%s",
                 name, ns_name, pidx, pool_row["subnet"])
        return JSONResponse({
            "pond":               name,
            "ns_name":            ns_name,
            "max_users":          req.max_users,
            "node_base":          req.node_base,
            "node_increment":     req.node_increment,
            "entire_pond_subnet": pool_row["subnet"],
        }, status_code=201)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Allocate node IP
# ---------------------------------------------------------------------------

@app.post("/api/v4/ponds/{pond_name}/allocate-ip")
async def allocate_ip(pond_name: str, req: AllocateIPRequest):
    """
    Anyone who knows the pond (and password if set) can request a node IP.
    Allocation is tentative (pubkey=NULL) until confirmed by /register.
    """
    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (_normalise(pond_name),)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{pond_name}' not found")
        if pond["password_hash"] and not _verify_password(
                req.pond_password, pond["password_hash"]):
            raise HTTPException(403, "Invalid pond password")

        ip = _next_node_ip(db, pond)
        db.execute(
            "INSERT INTO node_ip_alloc "
            "(pond_id,ip,pubkey,allocated_at,confirmed) VALUES (?,?,NULL,?,0)",
            (pond["id"], ip, time.time()))
        db.commit()

        subnet = ip.rsplit(".", 1)[0] + ".0/24"
        log.info("Allocated %s in pond '%s' (tentative)", ip, pond["name"])
        return JSONResponse({
            "ip":     ip,
            "subnet": subnet,
            "status": "tentative",
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Register node
# ---------------------------------------------------------------------------

@app.post("/api/v4/register")
async def register_node(req: RegisterRequest):
    pond_name = _normalise(req.pond)
    if not pond_name or not req.pubkey or not req.subnet:
        raise HTTPException(400, "pond, pubkey, and subnet are required")

    try:
        subnet = _validate_subnet(req.subnet)
    except ValueError as e:
        raise HTTPException(400, str(e))

    net = ipaddress.ip_network(subnet, strict=False)
    if str(net.network_address).startswith("10.253.") or \
       str(net.network_address).startswith("10.254."):
        raise HTTPException(400, "Subnet uses a reserved range")

    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (pond_name,)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{pond_name}' not found")
        if pond["password_hash"] and not _verify_password(
                req.pond_password, pond["password_hash"]):
            raise HTTPException(403, "Invalid pond password")

        # Gateway IP implied by subnet (e.g. 10.101.130.0/24 -> 10.101.130.1)
        node_ip = str(net.network_address).rsplit(".", 1)[0] + ".1"

        alloc = db.execute(
            "SELECT * FROM node_ip_alloc WHERE pond_id=? AND ip=?",
            (pond["id"], node_ip)).fetchone()

        if alloc:
            db.execute(
                "UPDATE node_ip_alloc SET pubkey=?, confirmed=1 WHERE id=?",
                (req.pubkey, alloc["id"]))
        else:
            db.execute(
                "REPLACE INTO node_ip_alloc "
                "(pond_id,ip,pubkey,allocated_at,confirmed) VALUES (?,?,?,?,1)",
                (pond["id"], node_ip, req.pubkey, time.time()))
        db.commit()

        # [TRANSIT_SUBNETS_V1] validate transit list if provided.  We
        # only enforce conflicts against nodes OTHER than this one —
        # re-registering with the same pubkey must be able to shrink
        # its own list without triggering self-conflicts.
        # [TRANSIT_PRESERVE_V1] If the request omits transit_subnets
        # entirely (None) OR sends an empty list, preserve whatever's in
        # the DB. The tunnel daemon's register/advertise path ALWAYS pushes
        # transit_subnets (often [] when discover_transit_subnets momentarily
        # finds nothing), so treating [] as "clear" lets a register call wipe
        # subnets that update-subnets had set — the exact failure this guard
        # exists to prevent. Clearing is the job of /api/v4/update-subnets,
        # which computes real add/remove deltas. So: only a NON-EMPTY list in
        # a register call updates transit; empty/omitted preserves.
        request_provided_transit = bool(req.transit_subnets)
        validated_transit = _validate_transit_list(
            list(req.transit_subnets or []), subnet)
        transit_json_new = json.dumps(validated_transit)

        # [GUID_IDENTITY_V1] GUID is the SOLE identity key — no MAC, no
        # name/pubkey heuristic, no fallback.  Rationale: every identity-
        # guessing path (MAC match, name/pubkey adoption, REGISTER_COLLISION
        # retire, cross-pond MAC/legacy sweeps) existed only because there was
        # no stable key, and each one could retire the wrong row — that is the
        # source of the Seattle5.retired.NN proliferation.  With a durable
        # install-time GUID the rule collapses to:
        #   - empty guid            -> 400, no identity (user runs frognet-guid)
        #   - matching active guid  -> REVIVE that row (same id/tunnels/choruses)
        #   - unknown guid          -> brand-new node, retire NOBODY
        # The ONLY thing that ever retires a row is an explicit
        # POST /api/v4/retire-guid (the regenerate path).  A normal return
        # never retires anything.
        if not req.guid:
            raise HTTPException(
                400, "guid required — run `frognet-guid generate` on the node")

        node = db.execute(
            "SELECT * FROM nodes WHERE pond_id=? AND guid=? AND active=1 "
            "LIMIT 1",
            (pond["id"], req.guid)).fetchone()

        if node:
            old_pubkey = node["pubkey"]
            old_name   = node["name"]
            old_active = node["active"]
            if request_provided_transit:
                db.execute(
                    "UPDATE nodes SET pubkey=?, name=?, label=?, subnet=?, "
                    "mac=COALESCE(NULLIF(?, ''), mac), "
                    "transit_subnets=?, active=1 WHERE id=?",
                    (req.pubkey, req.node_name, req.label, subnet,
                     req.mac, transit_json_new, node["id"]))
            else:
                db.execute(
                    "UPDATE nodes SET pubkey=?, name=?, label=?, subnet=?, "
                    "mac=COALESCE(NULLIF(?, ''), mac), "
                    "active=1 WHERE id=?",
                    (req.pubkey, req.node_name, req.label, subnet,
                     req.mac, node["id"]))
            if not old_active:
                log.info("REGISTER REACTIVATE: node=%s id=%s "
                         "previously inactive — now active",
                         req.node_name, node["id"])
            if old_name != req.node_name:
                log.info("REGISTER RENAME: id=%s old_name=%s new_name=%s",
                         node["id"], old_name, req.node_name)
            # --- Pubkey change: destroy all stale tunnels for this node.
            # Tunnels carry the OLD pubkey on broker-side wg ifaces; new
            # peer can never handshake until those are torn down.
            if old_pubkey != req.pubkey:
                stale = db.execute(
                    "SELECT * FROM tunnels WHERE active=1 AND "
                    "(host_node_id=? OR join_node_id=?)",
                    (node["id"], node["id"])).fetchall()
                if stale:
                    log.info("REGISTER PUBKEY CHANGE: node=%s old=%s... "
                             "new=%s... — destroying %d stale tunnel(s)",
                             req.node_name, old_pubkey[:16],
                             req.pubkey[:16], len(stale))
                    for t in stale:
                        try:
                            _destroy_tunnel(db, t, pond)
                            log.info("REGISTER PUBKEY CHANGE: destroyed "
                                     "tunnel id=%d name=%s",
                                     t["id"], t["name"])
                        except Exception as e:
                            log.error("REGISTER PUBKEY CHANGE: failed to "
                                      "destroy tunnel id=%d name=%s: %s",
                                      t["id"], t["name"], e)
                    db.commit()
        else:
            # [RECONFIG_ARCHIVE_V1] Brand-new row for this (pubkey).
            # If this is a returning-to-old-pond migration we may have
            # an archive holding the prior transit/label.  Request wins
            # when it provides values; archive fills the gap when it
            # doesn't.  See _archive_fill_defaults for the precise
            # rule.
            # [GUID_IDENTITY_V1] Unknown guid => brand-new node. Retire nobody.
            # No MAC archive lookup (mac is no longer an identity key); keep the
            # label/transit archive-fill keyed on '' which is a no-op when there
            # is no archive, preserving request-provided values.
            label_eff, transit_eff, _rpt = _archive_fill_defaults(
                db, pond["id"], "", req.label, transit_json_new,
                request_provided_transit)
            db.execute(
                "INSERT INTO nodes "
                "(pubkey,name,label,subnet,mac,guid,pond_id,created_at,"
                "active,transit_subnets) "
                "VALUES (?,?,?,?,?,?,?,?,1,?)",
                (req.pubkey, req.node_name, label_eff,
                 subnet, req.mac, req.guid, pond["id"], time.time(),
                 transit_eff))
        db.commit()
        node = db.execute(
            "SELECT * FROM nodes WHERE pubkey=?", (req.pubkey,)).fetchone()
        # [NODE_LAST_SEEN_V1] register itself is proof-of-life.  Bump
        # here so that this node is immediately visible to chorus-mate
        # _compute_aggregate calls (otherwise it'd be stuck with
        # last_seen_at=0 from the INSERT default until its first poll).
        _bump_last_seen(db, node["id"])
        db.commit()

#

        # Auto-join entire_pond
        entire = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name='entire_pond' AND active=1",
            (pond["id"],)).fetchone()
        if not entire:
            raise HTTPException(500, "entire_pond missing — contact admin")
        chorus_ip = _join_chorus(db, entire, node)

        # [RECONFIG_ARCHIVE_V1] If the node was previously in this pond
        # (under this MAC) and was archived during a cross-pond
        # migration, restore the archived non-entire_pond chorus
        # memberships now.  No-op when no archive exists.  Failures on
        # individual chorus rejoin are logged but do not fail the
        # register call.
        try:
            _restore_reconfigured_state(db, node, pond)
            db.commit()
        except Exception as e:
            log.error("[RECONFIG_RESTORE_V1] restore failed for "
                      "node id=%d (name=%s): %s",
                      node["id"], node["name"], e)

        memberships = [r["name"] for r in db.execute(
            """SELECT c.name FROM choruses c
               JOIN chorus_members cm ON cm.chorus_id = c.id
               WHERE cm.node_id=?""", (node["id"],)).fetchall()]

        return JSONResponse({
            "status":             "registered",
            "pond":               pond_name,
            "node_id":            node["id"],
            "node_name":          node["name"],
            "subnet":             subnet,
            "transit_subnets":    validated_transit,
            "choruses":           memberships,
            "entire_pond_ip":     chorus_ip,
            "entire_pond_subnet": entire["subnet"],
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# [NODE_LAST_SEEN_V1] Node-recency-driven tunnel liveness.
#
# Replaces the WG-handshake-driven reaper (OPPORTUNISTIC_REAP_V1.x).
#
# Model: the broker remembers only what its clients have advertised
# recently.  Every authenticated hit (register, my_channels,
# update_subnets) bumps `nodes.last_seen_at`.  A node not heard from
# in > _NODE_STALE_AFTER_SEC is considered gone:
#
#   - _compute_aggregate filters it out of every other node's peer
#     set, so no new tunnels involving it are generated.
#   - _reap_tunnels_with_stale_endpoint destroys every existing
#     tunnel touching it, so wg ifaces and port_forward rules
#     disappear from the broker namespace.
#
# Daemon polls /my-channels on a fixed cadence (5 min).  "> 2
# iterations out" → threshold = 2 * 300s = 600s.  A node that misses
# two consecutive polls is gone; when it polls again it gets fresh
# tunnels with fresh keys via the normal _ensure_tunnel path.
#
# This handles the long-standing case of a node that *used to* be a
# WAN-reachable tunnel host but no longer is (no internet, LAN-only,
# decommissioned).  The old handshake-based reaper would destroy and
# recreate such tunnels in a churn loop because the chorus-mate
# query still returned the absent node.  Filtering at the source
# (_compute_aggregate) ends the loop.
# ---------------------------------------------------------------------------

# Threshold for "stale node."  Two daemon poll intervals + a small
# margin to absorb clock skew and HTTP latency.  Daemon polls every
# 300s; 2 * 300 = 600s strict, plus 30s buffer = 630.  A node that
# polls on schedule stays well clear; one that misses two consecutive
# polls trips this.
_NODE_STALE_AFTER_SEC = 2 * 300 + 30  # 630s


def _lastseen_diag_machine_rows(db, pond_id, mac):
    """[LASTSEEN-DIAG] All node rows for one physical machine = (pond_id, mac),
    across active states and pubkeys.  MAC is the stable identity (schema has
    UNIQUE(pond_id,mac) WHERE active=1); a re-registration changes the pubkey
    but not the MAC, so grouping by MAC surfaces an identity orphan that the
    pubkey-keyed poll path cannot see.  Logging-only; never raises."""
    try:
        if not mac:
            return [("no-mac",)]
        now_i = int(time.time())
        rows = db.execute(
            "SELECT id,name,pubkey,active,last_seen_at FROM nodes "
            "WHERE pond_id=? AND mac=?", (pond_id, mac)).fetchall()
        return [(r["id"], r["name"], (r["pubkey"] or "")[:12], r["active"],
                 r["last_seen_at"], now_i - (r["last_seen_at"] or 0))
                for r in rows]
    except Exception as e:
        return [("ERR", repr(e))]


def _bump_last_seen(db, node_id: int) -> None:
    """Record that we just heard from `node_id`.  Idempotent; called
    once per authenticated request.  Commits immediately because the
    polling handlers (notably my_channels) do not otherwise commit,
    so without this the bump is dropped on connection close and
    STALE_NODE_REAP fires against nodes that are actually polling.
    """
    ts = int(time.time())
    cur = db.execute(
        "UPDATE nodes SET last_seen_at=? WHERE id=?",
        (ts, node_id))
    rc = cur.rowcount
    db.commit()
    # [LASTSEEN-DIAG] Confirm the bump matched a row and persisted, and show
    # the full machine (pond_id,mac) so we see which node_id the bump landed
    # on vs which one the tunnels reference.  rowcount=0 => node_id matched
    # nothing; readback != set => not committed.
    try:
        row = db.execute(
            "SELECT id,name,pubkey,mac,pond_id,active,last_seen_at "
            "FROM nodes WHERE id=?", (node_id,)).fetchone()
        log.info("[LASTSEEN-DIAG] bump node_id=%s rowcount=%s set=%s "
                 "readback=%s name=%s mac=%s pubkey=%.12s active=%s "
                 "machine_rows=%s",
                 node_id, rc, ts,
                 (row["last_seen_at"] if row else None),
                 (row["name"] if row else "?"),
                 (row["mac"] if row else "?"),
                 (row["pubkey"] if row else "?"),
                 (row["active"] if row else "?"),
                 _lastseen_diag_machine_rows(db, row["pond_id"], row["mac"])
                 if row else [])
    except Exception as e:
        log.warning("[LASTSEEN-DIAG] bump readback failed node_id=%s: %r",
                    node_id, e)


def _reap_tunnels_with_stale_endpoint(db) -> None:
    """Destroy every active tunnel where at least one endpoint node
    has `last_seen_at` older than _NODE_STALE_AFTER_SEC.  Runs from
    my_channels (so a live poll is what drives the sweep, no
    background thread) and is scoped broker-wide, not just to the
    polling node — one live caller is enough to garbage-collect any
    other node's orphaned tunnels.  Cheap: a single indexed SELECT
    plus _destroy_tunnel per stale row.

    Note: never touches `nodes.active`.  Identity reconciliation is
    a separate concern (handled by the register path); this reaper
    only manages the wg/iface/port_forward side effects of tunnels
    whose endpoints have gone quiet.  If a stale node comes back
    and polls, last_seen_at bumps, and _compute_aggregate re-issues
    fresh tunnels via _ensure_tunnel.
    """
    cutoff = int(time.time()) - _NODE_STALE_AFTER_SEC
    stale = db.execute(
        "SELECT t.* FROM tunnels t "
        "WHERE t.active=1 AND ("
        "  t.host_node_id IN ("
        "    SELECT id FROM nodes WHERE last_seen_at <= ?) OR "
        "  t.join_node_id IN ("
        "    SELECT id FROM nodes WHERE last_seen_at <= ?))",
        (cutoff, cutoff)).fetchall()
    # [LASTSEEN-DIAG] Dump EVERY node row that tripped the cutoff this sweep
    # with its active flag and ALL rows for the same machine (pond_id,mac).
    # This is the probe that names the cause:
    #   - identity orphan: the stale row is active=0 (or a different id) while
    #     the same (pond_id,mac) has a fresher active row -> the tunnels point
    #     at a retired/superseded node_id; broker is reaping a LIVE machine.
    #   - genuinely quiet: stale row is active=1 and is the only row for that
    #     machine -> the node really stopped polling for >cutoff (node side).
    # The reaper SELECT above does NOT filter active=1, so retired rows trip it.
    try:
        snodes = db.execute(
            "SELECT id,name,pubkey,mac,pond_id,active,last_seen_at "
            "FROM nodes WHERE last_seen_at <= ?", (cutoff,)).fetchall()
        now_d = int(time.time())
        for sn in snodes:
            log.info("[LASTSEEN-DIAG] stale-node id=%s name=%s active=%s "
                     "mac=%s last_seen=%s age=%ss pubkey=%.12s machine_rows=%s",
                     sn["id"], sn["name"], sn["active"], sn["mac"],
                     sn["last_seen_at"], now_d - (sn["last_seen_at"] or 0),
                     sn["pubkey"],
                     _lastseen_diag_machine_rows(db, sn["pond_id"], sn["mac"]))
    except Exception as e:
        log.warning("[LASTSEEN-DIAG] stale-node dump failed: %r", e)
    if not stale:
        return

    # Group by pond so we look up the pond row once per pond.
    by_pond: dict = {}
    for t in stale:
        by_pond.setdefault(t["pond_id"], []).append(t)

    for pond_id, tunnels in by_pond.items():
        pond = db.execute(
            "SELECT * FROM ponds WHERE id=?", (pond_id,)).fetchone()
        if pond is None:
            log.warning("STALE_NODE_REAP: tunnels reference missing "
                        "pond_id=%d", pond_id)
            continue
        for t in tunnels:
            # Look up which endpoint(s) are stale for the log line —
            # cheap, one row each, and the diagnostic value when
            # reading broker logs is high.
            host = db.execute(
                "SELECT name, last_seen_at FROM nodes WHERE id=?",
                (t["host_node_id"],)).fetchone()
            join = db.execute(
                "SELECT name, last_seen_at FROM nodes WHERE id=?",
                (t["join_node_id"],)).fetchone()
            now_i = int(time.time())
            log.info(
                "STALE_NODE_REAP: tunnel id=%d name=%s "
                "host=%s(last_seen=%ss ago) "
                "join=%s(last_seen=%ss ago) "
                "cutoff=%ds — destroying",
                t["id"], t["name"],
                host["name"] if host else "?",
                (now_i - host["last_seen_at"]) if host else "?",
                join["name"] if join else "?",
                (now_i - join["last_seen_at"]) if join else "?",
                _NODE_STALE_AFTER_SEC)
            try:
                _destroy_tunnel(db, t, pond)
            except Exception as e:
                log.warning(
                    "STALE_NODE_REAP: _destroy_tunnel failed for "
                    "id=%d name=%s: %r", t["id"], t["name"], e)



# ---------------------------------------------------------------------------
# API: My Channels (daemon polls this every 5 min)
# ---------------------------------------------------------------------------

@app.get("/api/v4/my-channels")
async def my_channels(pubkey: str):
    if not pubkey:
        raise HTTPException(400, "pubkey is required")
    db = get_db()
    try:
        node               = _get_node_by_pubkey(db, pubkey)
        # [LASTSEEN-DIAG] Which row did this live poll resolve to (pubkey-keyed,
        # the bug), and what are ALL rows for that physical machine (pond_id,
        # mac)?  If a sibling row with a different pubkey is the one the tunnels
        # reference, this poll bumps the wrong row and the reaper kills the
        # machine's tunnels.  pre_bump_last_seen shows staleness on arrival.
        # (Unknown pubkeys raise 404 above -> visible in the access log.)
        try:
            log.info("[LASTSEEN-DIAG] poll pubkey=%.12s -> node_id=%s name=%s "
                     "mac=%s active=%s pre_bump_last_seen=%s machine_rows=%s",
                     pubkey, node["id"], node["name"], node["mac"],
                     node["active"], node["last_seen_at"],
                     _lastseen_diag_machine_rows(db, node["pond_id"],
                                                 node["mac"]))
        except Exception as e:
            log.warning("[LASTSEEN-DIAG] poll diag failed pubkey=%.12s: %r",
                        pubkey, e)
        # [NODE_LAST_SEEN_V1] Record this poll as proof-of-life
        # *before* the reap sweep so this node can't be reaped by
        # its own poll (would require last_seen_at to already be
        # stale, but defensively still right).
        _bump_last_seen(db, node["id"])
        # [NODE_LAST_SEEN_V1] Destroy every tunnel broker-wide whose
        # endpoints include a node not heard from in
        # _NODE_STALE_AFTER_SEC.  Replaces handshake-based
        # OPPORTUNISTIC_REAP_V1.x.  Runs BEFORE _compute_aggregate so
        # the response reflects post-reap topology — caller's "list
        # of active tunnels" is authoritative and the node prunes
        # to match.
        _reap_tunnels_with_stale_endpoint(db)
        channels           = _compute_aggregate(db, node)
        chorus_assignments = _compute_chorus_assignments(db, node)

        payload = json.dumps(
            {"channels": channels, "chorus_assignments": chorus_assignments},
            sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]

        return JSONResponse({
            "node_name":          node["name"],
            "node_id":            node["id"],
            "digest":             digest,
            "channels":           channels,
            "chorus_assignments": chorus_assignments,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Retire GUID  (the regenerate path — explicit identity retirement)
# ---------------------------------------------------------------------------

@app.post("/api/v4/retire-guid")
async def retire_guid(req: RetireGuidRequest):
    """[GUID_IDENTITY_V1] Retire the active node row matching (pond, guid).

    The node-side `frognet-guid regenerate` calls this with its CURRENT guid
    before rotating to a fresh one, so the old identity becomes a ghost
    deliberately — never as a side effect of a confused return. Destroys the
    row's tunnels and chorus memberships so no stale wg-iface / FK survives,
    then flips active=0 with the standard .retired.<id> suffix. Idempotent: an
    unknown or already-retired guid is a no-op success, so a retried regen does
    not error."""
    pond_name = _normalise(req.pond)
    if not pond_name or not req.guid:
        raise HTTPException(400, "pond and guid are required")
    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (pond_name,)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{pond_name}' not found")
        node = db.execute(
            "SELECT * FROM nodes WHERE pond_id=? AND guid=? AND active=1 "
            "LIMIT 1", (pond["id"], req.guid)).fetchone()
        if not node:
            log.info("RETIRE_GUID: no active row for pond=%s guid=%s — no-op",
                     pond_name, req.guid)
            return JSONResponse({"status": "noop", "retired_node_id": None})
        stale = db.execute(
            "SELECT * FROM tunnels WHERE active=1 AND "
            "(host_node_id=? OR join_node_id=?)",
            (node["id"], node["id"])).fetchall()
        for t in stale:
            try:
                _destroy_tunnel(db, t, pond)
                log.info("RETIRE_GUID: destroyed tunnel id=%d name=%s",
                         t["id"], t["name"])
            except Exception as e:
                log.error("RETIRE_GUID: failed to destroy tunnel id=%d: %s",
                          t["id"], e)
        db.execute("DELETE FROM chorus_members WHERE node_id=?", (node["id"],))
        db.execute(
            "UPDATE nodes SET active=0, "
            "name = name || '.retired.' || id WHERE id=? AND active=1",
            (node["id"],))
        _tag_retired_pubkey(db, node["id"])
        db.commit()
        log.info("RETIRE_GUID: retired node id=%d name=%s pond=%s guid=%s",
                 node["id"], node["name"], pond_name, req.guid)
        return JSONResponse({"status": "retired", "retired_node_id": node["id"]})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Create chorus
# ---------------------------------------------------------------------------

@app.post("/api/v4/choruses")
async def create_chorus(req: ChorusCreate):
    cname = _normalise(req.name)
    if len(cname) < 2:
        raise HTTPException(400, "Chorus name must be at least 2 characters")
    if cname == "entire_pond":
        raise HTTPException(400, "'entire_pond' is reserved")

    db = get_db()
    try:
        node = _get_node_by_pubkey(db, req.pubkey)
        pond = _get_pond(db, node["pond_id"])


        # v3.2: only honour user-initiated chorus creation when the pond's
        # admin has enabled it.  Admin-initiated creation goes through
        # /api/v1/admin/choruses and bypasses this gate.
        if not pond["allow_user_choruses"]:
            raise HTTPException(
                403,
                f"Pond '{pond['name']}' does not allow user-created choruses")

        if db.execute(
            "SELECT id FROM choruses WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], cname)).fetchone():
            raise HTTPException(400, f"Chorus '{cname}' already exists")

        pool_row = _alloc_chorus_subnet(db)
        pw_hash  = _hash_password(req.password) if req.password else None

        db.execute(
            "INSERT INTO choruses "
            "(pond_id,name,subnet,visible,password_hash,creator_node_id,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (pond["id"], cname, pool_row["subnet"],
             1 if req.visible else 0, pw_hash, node["id"], time.time()))
        db.commit()
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name=?",
            (pond["id"], cname)).fetchone()
        _claim_chorus_subnet(db, pool_row["id"], chorus["id"])

        # Creator auto-joins (no password required)
        chorus_ip = _join_chorus(db, chorus, node)

        log.info("Chorus '%s' created pond='%s' subnet=%s visible=%s",
                 cname, pond["name"], pool_row["subnet"], req.visible)
        return JSONResponse({
            "chorus":    cname,
            "subnet":    pool_row["subnet"],
            "chorus_ip": chorus_ip,
            "visible":   req.visible,
        }, status_code=201)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Join chorus
# ---------------------------------------------------------------------------

@app.post("/api/v4/choruses/join")
async def join_chorus(req: ChorusJoin):
    cname = _normalise(req.name)
    db = get_db()
    try:
        node   = _get_node_by_pubkey(db, req.pubkey)
        pond   = _get_pond(db, node["pond_id"])
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], cname)).fetchone()
        # Return same 404 for invisible chorus caller is not in
        # (don't confirm existence of invisible choruses to non-members)
        if not chorus:
            raise HTTPException(404, f"Chorus '{cname}' not found")
        already = db.execute(
            "SELECT id FROM chorus_members WHERE chorus_id=? AND node_id=?",
            (chorus["id"], node["id"])).fetchone()
        if not already:
            if chorus["password_hash"] and \
               chorus["creator_node_id"] != node["id"]:
                if not _verify_password(req.password, chorus["password_hash"]):
                    raise HTTPException(403, "Invalid chorus password")
        ip = _join_chorus(db, chorus, node)
        return JSONResponse({
            "chorus":    cname,
            "subnet":    chorus["subnet"],
            "chorus_ip": ip,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Leave chorus
# ---------------------------------------------------------------------------

@app.post("/api/v4/choruses/leave")
async def leave_chorus(req: ChorusLeave):
    cname = _normalise(req.name)
    if cname == "entire_pond":
        raise HTTPException(400, "Cannot leave entire_pond")
    db = get_db()
    try:
        node   = _get_node_by_pubkey(db, req.pubkey)
        pond   = _get_pond(db, node["pond_id"])
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], cname)).fetchone()
        if not chorus:
            raise HTTPException(404, f"Chorus '{cname}' not found")
        if not db.execute(
            "SELECT id FROM chorus_members WHERE chorus_id=? AND node_id=?",
            (chorus["id"], node["id"])).fetchone():
            raise HTTPException(404, "Not a member of this chorus")
        _leave_chorus(db, chorus, node, pond)
        return JSONResponse({"left": cname})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: List choruses
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API: GET /api/v4/transit-map   [VOUCH_TRANSIT_GATE_V1]
#   Node-facing (pond-scoped via pubkey). Returns, for every active node in the
#   caller's pond, the /24-prefix of its owned subnet -> the list of /24 CIDRs
#   it can relay (owned + transit_subnets). The tunnel daemon caches this to
#   /var/lib/frognet-tunnel/node_transit.json so the discovery engine can reject
#   vouches through a relay that does not actually transit the destination
#   (an unregistered LAN leaf is simply absent from the map). Read-only; no
#   admin token — it exposes only routing topology the node already learns via
#   getHosts/route propagation.
# ---------------------------------------------------------------------------
@app.get("/api/v4/transit-map")
async def transit_map(pubkey: str):
    if not pubkey:
        raise HTTPException(400, "pubkey is required")
    db = get_db()
    try:
        node = _get_node_by_pubkey(db, pubkey)
        rows = db.execute(
            "SELECT name, subnet, transit_subnets FROM nodes "
            "WHERE pond_id=? AND active=1 AND subnet != ''",
            (node["pond_id"],)).fetchall()
        out = {}
        for r in rows:
            prefix = r["subnet"].split("/")[0].rsplit(".", 1)[0]  # 10.x.y.0/24 -> 10.x.y
            if prefix:
                out[prefix] = _node_subnets_for_side(r)
        return JSONResponse({"map": out})
    finally:
        db.close()


@app.get("/api/v4/choruses")
async def list_choruses(pubkey: str):
    if not pubkey:
        raise HTTPException(400, "pubkey is required")
    db = get_db()
    try:
        node = _get_node_by_pubkey(db, pubkey)
        pond = _get_pond(db, node["pond_id"])

        my_ids = {r["chorus_id"] for r in db.execute(
            "SELECT chorus_id FROM chorus_members WHERE node_id=?",
            (node["id"],)).fetchall()}

        result = []
        for c in db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND active=1",
                (pond["id"],)).fetchall():
            if not c["visible"] and c["id"] not in my_ids:
                continue
            mc = db.execute(
                "SELECT COUNT(*) as n FROM chorus_members WHERE chorus_id=?",
                (c["id"],)).fetchone()["n"]
            result.append({
                "name":          c["name"],
                "subnet":        c["subnet"],
                "visible":       bool(c["visible"]),
                "has_password":  bool(c["password_hash"]),
                "member_count":  mc,
                "you_are_member": c["id"] in my_ids,
            })
        return JSONResponse({"choruses": result})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: List / status ponds (admin)
# ---------------------------------------------------------------------------

@app.get("/api/v4/ponds")
async def list_ponds(authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        result = []
        for p in db.execute("SELECT * FROM ponds WHERE active=1").fetchall():
            choruses = [r["name"] for r in db.execute(
                "SELECT name FROM choruses WHERE pond_id=? AND active=1",
                (p["id"],)).fetchall()]
            nc = db.execute(
                "SELECT COUNT(*) as n FROM nodes WHERE pond_id=? AND active=1",
                (p["id"],)).fetchone()["n"]
            result.append({
                "name": p["name"], "max_users": p["max_users"],
                "node_base": p["node_base"],
                "node_increment": p["node_increment"],
                "allow_user_choruses": bool(p["allow_user_choruses"]),
                "has_password": bool(p["password_hash"]),
                "choruses": choruses, "node_count": nc,
            })
        return JSONResponse({"ponds": result})
    finally:
        db.close()


@app.get("/api/v4/ponds/{pond_name}")
async def pond_status(pond_name: str,
                      authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (_normalise(pond_name),)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{pond_name}' not found")

        choruses = []
        for c in db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND active=1",
                (pond["id"],)).fetchall():
            members = db.execute(
                """SELECT n.name, n.subnet, cm.chorus_ip FROM nodes n
                   JOIN chorus_members cm ON cm.node_id = n.id
                   WHERE cm.chorus_id=?""", (c["id"],)).fetchall()
            choruses.append({
                "name": c["name"], "subnet": c["subnet"],
                "visible": bool(c["visible"]),
                "members": [{"name": m["name"], "subnet": m["subnet"],
                             "chorus_ip": m["chorus_ip"]} for m in members],
            })

        allocs = db.execute(
            "SELECT ip, confirmed, pubkey FROM node_ip_alloc "
            "WHERE pond_id=? ORDER BY ip", (pond["id"],)).fetchall()

        return JSONResponse({
            "pond": pond_name, "max_users": pond["max_users"],
            "choruses": choruses,
            "allocations": [{"ip": a["ip"],
                             "confirmed": bool(a["confirmed"]),
                             "has_node":  bool(a["pubkey"])} for a in allocs],
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API: Leave tunnel / admin remove from chorus
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API: Update transit subnets  [TRANSIT_SUBNETS_V1]
#
# Allows a registered node to declare (or change) the list of /24 LAN
# subnets it can forward to.  Idempotent.  Propagates changes to all
# active tunnels this node is part of, installing routes for newly
# added subnets and deleting routes for removed ones in the broker's
# namespace.  Peers learn the new set on their next /api/v1/my-channels
# poll (remote_subnets in each channel reflects the current DB state).
# ---------------------------------------------------------------------------
# API: Admin endpoints  [v3.2 admin surface]
# ---------------------------------------------------------------------------
# These endpoints all require the admin bearer token.  They expose
# operations the user-facing API doesn't: per-pond settings, admin-driven
# chorus creation, node listing/removal, pubkey blocklist management.
# Algorithms below match the v3 broker exactly — admin endpoints only add
# CRUD on top of existing tables and do not alter the routing/transit
# logic that the steady-state poll handlers depend on.

class AdminPondPatch(BaseModel):
    allow_user_choruses: Optional[bool] = None
    max_users:           Optional[int]  = None
    password:            Optional[str]  = None  # "" to clear, None = leave


@app.patch("/api/v4/admin/ponds/{pond_name}")
async def admin_patch_pond(pond_name: str, req: AdminPondPatch,
                           authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (_normalise(pond_name),)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{pond_name}' not found")

        sets, vals = [], []
        if req.allow_user_choruses is not None:
            sets.append("allow_user_choruses=?")
            vals.append(1 if req.allow_user_choruses else 0)
        if req.max_users is not None:
            if req.max_users < 1:
                raise HTTPException(400, "max_users must be >= 1")
            sets.append("max_users=?")
            vals.append(req.max_users)
        if req.password is not None:
            if req.password == "":
                sets.append("password_hash=NULL")
            else:
                sets.append("password_hash=?")
                vals.append(_hash_password(req.password))

        if not sets:
            raise HTTPException(400, "No fields to update")

        # password_hash=NULL has no placeholder; build SQL accordingly.
        sql = "UPDATE ponds SET " + ", ".join(sets) + " WHERE id=?"
        vals.append(pond["id"])
        db.execute(sql, tuple(vals))
        db.commit()
        log.info("ADMIN PATCH pond '%s': %s", pond["name"], sets)

        updated = db.execute(
            "SELECT * FROM ponds WHERE id=?", (pond["id"],)).fetchone()
        return JSONResponse({
            "name":                updated["name"],
            "max_users":           updated["max_users"],
            "allow_user_choruses": bool(updated["allow_user_choruses"]),
            "has_password":        bool(updated["password_hash"]),
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# v4 Admin: POST /api/v1/admin/choruses  (no creator-node required)
# ---------------------------------------------------------------------------

class AdminChorusCreate(BaseModel):
    pond:     str
    name:     str
    visible:  bool = True
    password: str  = ""


@app.post("/api/v4/admin/choruses")
async def admin_create_chorus(req: AdminChorusCreate,
                              authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    cname = _normalise(req.name)
    if len(cname) < 2:
        raise HTTPException(400, "Chorus name must be at least 2 characters")
    if cname == "entire_pond":
        raise HTTPException(400, "'entire_pond' is reserved")

    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (_normalise(req.pond),)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{req.pond}' not found")
        if db.execute(
            "SELECT id FROM choruses WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], cname)).fetchone():
            raise HTTPException(400, f"Chorus '{cname}' already exists")

        pool_row = _alloc_chorus_subnet(db)
        pw_hash  = _hash_password(req.password) if req.password else None

        db.execute(
            "INSERT INTO choruses "
            "(pond_id,name,subnet,visible,password_hash,creator_node_id,"
            " created_at) VALUES (?,?,?,?,?,NULL,?)",
            (pond["id"], cname, pool_row["subnet"],
             1 if req.visible else 0, pw_hash, time.time()))
        db.commit()
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name=?",
            (pond["id"], cname)).fetchone()
        _claim_chorus_subnet(db, pool_row["id"], chorus["id"])

        log.info("ADMIN chorus '%s' created pond='%s' subnet=%s visible=%s",
                 cname, pond["name"], pool_row["subnet"], req.visible)
        return JSONResponse({
            "chorus":  cname,
            "pond":    pond["name"],
            "subnet":  pool_row["subnet"],
            "visible": req.visible,
        }, status_code=201)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# v4 Admin: GET /api/v1/admin/nodes
# ---------------------------------------------------------------------------

@app.get("/api/v4/admin/nodes")
async def admin_list_nodes(pond: Optional[str] = None,
                           authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        if pond:
            rows = db.execute(
                """SELECT n.id, n.pubkey, n.name, n.label, n.subnet,
                          n.created_at, n.active, p.name as pond_name
                     FROM nodes n
                     JOIN ponds p ON p.id = n.pond_id
                    WHERE p.name = ?
                    ORDER BY n.name""", (_normalise(pond),)).fetchall()
        else:
            rows = db.execute(
                """SELECT n.id, n.pubkey, n.name, n.label, n.subnet,
                          n.created_at, n.active, p.name as pond_name
                     FROM nodes n
                     JOIN ponds p ON p.id = n.pond_id
                    ORDER BY p.name, n.name""").fetchall()
        blocked = {r["pubkey"] for r in db.execute(
            "SELECT pubkey FROM node_blocklist WHERE active=1").fetchall()}
        out = []
        for r in rows:
            out.append({
                "id":         r["id"],
                "pubkey":     r["pubkey"],
                "name":       r["name"],
                "label":      r["label"],
                "subnet":     r["subnet"],
                "pond":       r["pond_name"],
                "active":     bool(r["active"]),
                "blocked":    r["pubkey"] in blocked,
                "created_at": r["created_at"],
            })
        return JSONResponse({"nodes": out})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# v4 Admin: DELETE /api/v1/admin/nodes/{pubkey}
#   Force-deregister.  Tunnels destroyed, node marked inactive, but the
#   pubkey is NOT added to blocklist — node may re-register.
# ---------------------------------------------------------------------------

@app.delete("/api/v4/admin/nodes/{pubkey:path}")
async def admin_force_deregister(pubkey: str,
                                 authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        node = db.execute(
            "SELECT * FROM nodes WHERE pubkey=?", (pubkey,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        pond = _get_pond(db, node["pond_id"])
        stale = db.execute(
            "SELECT * FROM tunnels WHERE active=1 AND "
            "(host_node_id=? OR join_node_id=?)",
            (node["id"], node["id"])).fetchall()
        destroyed = 0
        for t in stale:
            try:
                _destroy_tunnel(db, t, pond)
                destroyed += 1
            except Exception as e:
                log.error("ADMIN force-dereg: tunnel id=%d: %s", t["id"], e)
        db.execute("UPDATE nodes SET active=0 WHERE id=?", (node["id"],))
        # Free the IP allocation so the next register can pick it up cleanly.
        db.execute(
            "UPDATE node_ip_alloc SET pubkey=NULL, confirmed=0 "
            "WHERE pond_id=? AND pubkey=?", (pond["id"], pubkey))
        db.commit()
        log.info("ADMIN force-dereg pubkey=%s... node='%s' tunnels=%d",
                 pubkey[:16], node["name"], destroyed)
        return JSONResponse({
            "pubkey":            pubkey,
            "node_name":         node["name"],
            "tunnels_destroyed": destroyed,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# v4 Admin: blocklist  GET / POST / DELETE
# ---------------------------------------------------------------------------

class BlocklistAdd(BaseModel):
    pubkey:    str
    node_name: str = ""
    reason:    str = ""


@app.get("/api/v4/admin/blocklist")
async def admin_list_blocklist(authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        # Left-join nodes/ponds so the admin sees which pond the blocked
        # pubkey was last registered with.  Blocked pubkeys with no node
        # row (blocked before they ever registered) get pond_name=NULL.
        rows = db.execute(
            """SELECT b.id, b.pubkey, b.node_name, b.reason, b.added_at,
                      p.name AS pond_name
                 FROM node_blocklist b
                 LEFT JOIN nodes n ON n.pubkey = b.pubkey
                 LEFT JOIN ponds p ON p.id    = n.pond_id
                WHERE b.active=1
                ORDER BY b.added_at DESC""").fetchall()
        return JSONResponse({"blocklist": [
            {"id":        r["id"],
             "pubkey":    r["pubkey"],
             "node_name": r["node_name"],
             "pond_name": r["pond_name"],
             "reason":    r["reason"],
             "added_at":  r["added_at"]} for r in rows]})
    finally:
        db.close()


@app.post("/api/v4/admin/blocklist")
async def admin_add_blocklist(req: BlocklistAdd,
                              authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    if not req.pubkey:
        raise HTTPException(400, "pubkey is required")
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM node_blocklist WHERE pubkey=?",
            (req.pubkey,)).fetchone()
        if existing:
            db.execute(
                "UPDATE node_blocklist SET node_name=?, reason=?, "
                "active=1, added_at=? WHERE id=?",
                (req.node_name, req.reason, time.time(), existing["id"]))
        else:
            db.execute(
                "INSERT INTO node_blocklist "
                "(pubkey,node_name,reason,added_at,active) "
                "VALUES (?,?,?,?,1)",
                (req.pubkey, req.node_name, req.reason, time.time()))
        db.commit()
        log.info("ADMIN BLOCKLIST ADD pubkey=%s... node_name=%s reason=%s",
                 req.pubkey[:16], req.node_name, req.reason)

        # Tear down active tunnels on either side of this pubkey.
        node = db.execute(
            "SELECT * FROM nodes WHERE pubkey=?",
            (req.pubkey,)).fetchone()
        if node:
            pond = _get_pond(db, node["pond_id"])
            stale = db.execute(
                "SELECT * FROM tunnels WHERE active=1 AND "
                "(host_node_id=? OR join_node_id=?)",
                (node["id"], node["id"])).fetchall()
            for t in stale:
                try:
                    _destroy_tunnel(db, t, pond)
                    log.info("ADMIN BLOCKLIST: destroyed tunnel id=%d name=%s",
                             t["id"], t["name"])
                except Exception as e:
                    log.error("ADMIN BLOCKLIST: failed to destroy tunnel "
                              "id=%d: %s", t["id"], e)
            db.execute("UPDATE nodes SET active=0 WHERE id=?", (node["id"],))
            db.commit()

        return JSONResponse({"pubkey": req.pubkey, "blocked": True})
    finally:
        db.close()


@app.delete("/api/v4/admin/blocklist/{pubkey:path}")
async def admin_remove_blocklist(pubkey: str,
                                 authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        row = db.execute(
            "SELECT id FROM node_blocklist WHERE pubkey=? AND active=1",
            (pubkey,)).fetchone()
        if not row:
            raise HTTPException(404, "Pubkey not in blocklist")
        db.execute("UPDATE node_blocklist SET active=0 WHERE id=?",
                   (row["id"],))
        db.commit()
        log.info("ADMIN BLOCKLIST REMOVE pubkey=%s...", pubkey[:16])
        return JSONResponse({"pubkey": pubkey, "blocked": False})
    finally:
        db.close()


# ---------------------------------------------------------------------------

@app.post("/api/v4/update-subnets")
async def update_subnets(req: UpdateSubnetsRequest):
    if not req.pubkey:
        raise HTTPException(400, "pubkey is required")

    db = get_db()
    try:
        node = _get_node_by_pubkey(db, req.pubkey)
        # [NODE_LAST_SEEN_V1] proof-of-life from any authenticated hit.
        _bump_last_seen(db, node["id"])

        validated = _validate_transit_list(
            list(req.transit_subnets or []), node["subnet"])

        current = _parse_transit(node)
        cur_set, new_set = set(current), set(validated)
        added   = sorted(new_set - cur_set)
        removed = sorted(cur_set - new_set)
        kept    = sorted(cur_set & new_set)

        if not added and not removed:
            return JSONResponse({
                "status":           "unchanged",
                "added":            [],
                "removed":          [],
                "kept":             kept,
                "tunnels_updated":  0,
            })

        # [TRANSIT_SHARED_OK_V1] Transit is consumed PER-PEER: every tunnel
        # tells a node "peer P reaches [P.owned] + P.transit", and the
        # node-side route engine resolves multiple paths to the same /24 by
        # metric/echo. So two nodes legitimately transiting the same subnet
        # (e.g. Seattle5 as Seattle6's downstream-server and SeattleThree as
        # Seattle6's upstream-client both reaching 10.160.160) is mesh
        # redundancy, NOT a conflict. The old code raised 409 on ANY shared
        # subnet, which _sync_transit_subnets treats as "retry next cycle" —
        # so a node with even one shared-path subnet never persisted ANY of
        # its transit, including the non-shared entries. That is why most
        # nodes' transit_subnets stayed []. Detection is kept for logging
        # only; it no longer rejects.
        if added:
            shared = _transit_conflict(db, node["pond_id"],
                                       node["id"], added)
            if shared:
                detail = "; ".join(
                    f"{sn} also {kind} by {who}"
                    for sn, who, kind in shared)
                log.info("update_subnets [TRANSIT_SHARED_OK_V1]: node=%s "
                         "shared transit (allowed): %s",
                         node["name"], detail)

        # Find all active tunnels this node is part of so we know which
        # (iface, edge) pairs to program.
        tunnels = db.execute(
            "SELECT * FROM tunnels WHERE active=1 AND "
            "(host_node_id=? OR join_node_id=?)",
            (node["id"], node["id"])).fetchall()

        pond = _get_pond(db, node["pond_id"])
        ns_name = pond["ns_name"]

        # Apply route additions FIRST so that if something breaks the
        # DB state stays old and retries remain sound.  Track what we
        # successfully added so we can unwind on failure.
        installed = []  # list of (destination, iface) we added
        try:
            for t in tunnels:
                if t["host_node_id"] == node["id"]:
                    iface, edge = t["host_wg_iface"], t["host_edge_ip"]
                else:
                    iface, edge = t["join_wg_iface"], t["join_edge_ip"]
                for s in added:
                    ns.add_route(ns_name, s, edge, iface,
                                 owned=(s == (node["subnet"] if "subnet"
                                              in node.keys() else "")))
                    installed.append((s, iface))
        except Exception as e:
            log.error("update_subnets: add_route failed for node=%s "
                      "subnets=%s: %s — unwinding %d partial adds",
                      node["name"], added, e, len(installed))
            for dest, iface in installed:
                try:
                    ns._run(["ip", "route", "del", dest, "dev", iface],
                            netns=ns_name, check=False)
                except Exception as e2:
                    log.warning("update_subnets: unwind del %s dev %s: %s",
                                dest, iface, e2)
            raise HTTPException(500, f"route install failed: {e}")

        # Route deletions — best-effort.  If a del fails we log and move
        # on; the DB still reflects the intended state and _restore_state
        # on next startup will not re-add the now-orphan route.
        for t in tunnels:
            if t["host_node_id"] == node["id"]:
                iface = t["host_wg_iface"]
            else:
                iface = t["join_wg_iface"]
            for s in removed:
                try:
                    ns._run(["ip", "route", "del", s, "dev", iface],
                            netns=ns_name, check=False)
                except Exception as e:
                    log.warning("update_subnets: del %s dev %s: %s",
                                s, iface, e)

        # Commit the DB last so state reflects reality.
        db.execute(
            "UPDATE nodes SET transit_subnets=? WHERE id=?",
            (json.dumps(validated), node["id"]))
        db.commit()

        log.info("update_subnets [TRANSIT_SUBNETS_V1]: node=%s "
                 "added=%s removed=%s kept=%s tunnels=%d",
                 node["name"], added, removed, kept, len(tunnels))

        return JSONResponse({
            "status":           "updated",
            "added":            added,
            "removed":          removed,
            "kept":             kept,
            "tunnels_updated":  len(tunnels),
        })
    finally:
        db.close()


@app.post("/api/v4/leave")
async def leave_tunnel(req: LeaveRequest):
    db = get_db()
    try:
        node   = _get_node_by_pubkey(db, req.pubkey)
        pond   = _get_pond(db, node["pond_id"])
        tunnel = db.execute(
            "SELECT * FROM tunnels WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], req.tunnel_name)).fetchone()
        if not tunnel:
            raise HTTPException(404, f"Tunnel '{req.tunnel_name}' not found")
        _destroy_tunnel(db, tunnel, pond)
        return JSONResponse({"destroyed": req.tunnel_name})
    finally:
        db.close()


@app.post("/api/v4/deregister")
async def deregister_node(req: DeregisterRequest):
    """Remove a node from the broker entirely.

    Destroys all active tunnels, removes chorus memberships,
    frees IP allocations, and deactivates the node record.
    Called by frognet_tunnel_setup_v3.sh on LAN-only nodes
    that have no direct internet and should not be in the pond.
    Idempotent — returns 200 even if the node is not registered.
    """
    if not req.pubkey:
        raise HTTPException(400, "pubkey is required")

    db = get_db()
    try:
        node = db.execute(
            "SELECT * FROM nodes WHERE pubkey=? AND active=1",
            (req.pubkey,)).fetchone()
        if not node:
            return JSONResponse({
                "status": "not_registered",
                "message": "No active node found for this pubkey"
            })

        pond = _get_pond(db, node["pond_id"])
        node_name = node["name"]
        node_id = node["id"]

        # 1. Destroy all active tunnels involving this node
        tunnels = db.execute(
            "SELECT * FROM tunnels WHERE pond_id=? AND active=1 "
            "AND (host_node_id=? OR join_node_id=?)",
            (pond["id"], node_id, node_id)).fetchall()
        destroyed = 0
        for t in tunnels:
            try:
                _destroy_tunnel(db, t, pond)
                destroyed += 1
                log.info("DEREGISTER: destroyed tunnel id=%d name=%s",
                         t["id"], t["name"])
            except Exception as e:
                log.error("DEREGISTER: failed to destroy tunnel id=%d: %s",
                          t["id"], e)

        # 2. Remove all chorus memberships
        memberships = db.execute(
            "SELECT cm.*, c.name as chorus_name FROM chorus_members cm "
            "JOIN choruses c ON c.id = cm.chorus_id "
            "WHERE cm.node_id=?", (node_id,)).fetchall()
        for m in memberships:
            db.execute(
                "DELETE FROM chorus_members WHERE chorus_id=? AND node_id=?",
                (m["chorus_id"], node_id))
            log.info("DEREGISTER: removed %s from chorus %s",
                     node_name, m["chorus_name"])
            remaining = db.execute(
                "SELECT COUNT(*) as n FROM chorus_members WHERE chorus_id=?",
                (m["chorus_id"],)).fetchone()["n"]
            if remaining == 0:
                chorus = db.execute(
                    "SELECT * FROM choruses WHERE id=?",
                    (m["chorus_id"],)).fetchone()
                if chorus and chorus["name"] != "entire_pond":
                    _free_chorus_subnet(db, chorus["id"])
                    db.execute("UPDATE choruses SET active=0 WHERE id=?",
                               (chorus["id"],))
                    log.info("DEREGISTER: auto-deleted empty chorus %s",
                             chorus["name"])
        db.commit()

        # 3. Free IP allocations
        db.execute(
            "DELETE FROM node_ip_alloc WHERE pond_id=? AND pubkey=?",
            (pond["id"], req.pubkey))
        db.commit()

        # 4. Deactivate node
        db.execute("UPDATE nodes SET active=0 WHERE id=?", (node_id,))
        db.commit()

        log.info("DEREGISTER: node '%s' (id=%d) fully deregistered — "
                 "%d tunnel(s) destroyed, %d chorus(es) left",
                 node_name, node_id, destroyed, len(memberships))

        return JSONResponse({
            "status": "deregistered",
            "node_name": node_name,
            "tunnels_destroyed": destroyed,
            "choruses_removed": len(memberships),
        })
    finally:
        db.close()


@app.delete("/api/v4/chorus-member")
async def remove_from_chorus(req: ChorusRemoveMember,
                             authorization: Optional[str] = Header(None)):
    _check_admin_token(authorization)
    db = get_db()
    try:
        pond = db.execute(
            "SELECT * FROM ponds WHERE name=? AND active=1",
            (_normalise(req.pond),)).fetchone()
        if not pond:
            raise HTTPException(404, f"Pond '{req.pond}' not found")
        chorus = db.execute(
            "SELECT * FROM choruses WHERE pond_id=? AND name=? AND active=1",
            (pond["id"], _normalise(req.chorus))).fetchone()
        if not chorus:
            raise HTTPException(404, f"Chorus '{req.chorus}' not found")
        node = db.execute(
            "SELECT * FROM nodes WHERE name=? AND pond_id=? AND active=1",
            (req.node_name, pond["id"])).fetchone()
        if not node:
            raise HTTPException(404, f"Node '{req.node_name}' not found")
        if not db.execute(
            "SELECT id FROM chorus_members WHERE chorus_id=? AND node_id=?",
                (chorus["id"], node["id"])).fetchone():
            raise HTTPException(404, "Node is not in this chorus")
        _leave_chorus(db, chorus, node, pond)
        return JSONResponse({"removed": req.node_name, "from": req.chorus})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _restore_state():
    db = get_db()

    # ── Flush stale v2 iptables rules ────────────────────────────────────
    # The v2 broker (grp_ namespaces) left DNAT and FORWARD rules pointing
    # to veth IPs that no longer host WG interfaces.  These rules sit AHEAD
    # of v3 rules in the chain and silently intercept traffic.
    # Strategy: collect all veth IPs that belong to active v3 ponds, then
    # delete any PREROUTING DNAT / FORWARD rules targeting 10.253.254.x
    # addresses that are NOT in the active set.  The rest of _restore_state
    # will re-add correct rules via setup_port_forward.
    active_ns_ips = set()
    for pond in db.execute("SELECT pond_index FROM ponds WHERE active=1").fetchall():
        _, ns_ip, _ = ns._veth_ips(pond["pond_index"])
        active_ns_ips.add(ns_ip)

    # Scan PREROUTING for stale DNAT rules
    r = subprocess.run(["iptables", "-t", "nat", "-S", "PREROUTING"],
                       capture_output=True, text=True, check=False)
    for line in r.stdout.splitlines():
        if "--to-destination 10.253.254." not in line:
            continue
        # Extract destination IP from --to-destination IP:PORT
        dest = line.split("--to-destination ")[1].split()[0]
        dest_ip = dest.split(":")[0]
        if dest_ip not in active_ns_ips:
            # Build the delete command from the -A rule
            del_cmd = line.replace("-A PREROUTING", "-D PREROUTING", 1)
            subprocess.run(["iptables", "-t", "nat"] + del_cmd.split(),
                           capture_output=True, check=False)
            log.info("restore_state: flushed stale DNAT rule: %s", line.strip())

    # Same for FORWARD rules
    r = subprocess.run(["iptables", "-S", "FORWARD"],
                       capture_output=True, text=True, check=False)
    for line in r.stdout.splitlines():
        if "-d 10.253.254." not in line:
            continue
        for part in line.split():
            if part.startswith("10.253.254."):
                dest_ip = part.split("/")[0]
                if dest_ip not in active_ns_ips:
                    del_cmd = line.replace("-A FORWARD", "-D FORWARD", 1)
                    subprocess.run(["iptables"] + del_cmd.split(),
                                   capture_output=True, check=False)
                    log.info("restore_state: flushed stale FORWARD rule: %s",
                             line.strip())
                break

    ponds = db.execute("SELECT * FROM ponds WHERE active=1").fetchall()
    log.info("restore_state: %d pond(s)", len(ponds))
    for pond in ponds:
        ns_name = pond["ns_name"]
        ns.ensure_namespace(ns_name, pond["pond_index"])
        try:
            r = ns._run(["ip", "link", "show", "type", "wireguard"],
                        netns=ns_name, check=False)
            live = {line.split(":")[1].strip()
                    for line in r.stdout.splitlines()
                    if ":" in line and line.split(":")[1].strip().startswith("wg_")}
        except Exception as e:
            log.warning("restore_state ns=%s: %s", ns_name, e)
            live = set()

        tunnels  = db.execute(
            "SELECT * FROM tunnels WHERE pond_id=? AND active=1",
            (pond["id"],)).fetchall()
        db_ifaces = {t["host_wg_iface"] for t in tunnels} | \
                    {t["join_wg_iface"]  for t in tunnels}

        for iface in live - db_ifaces:
            try:
                ns.delete_wg_interface(ns_name, iface)
            except Exception as e:
                log.warning("orphan %s: %s", iface, e)

        for t in tunnels:
            # Look up edge node pubkeys from the nodes table —
            # the tunnel record only stores the BROKER's keypairs.
            # [TRANSIT_SUBNETS_V1] also fetch transit_subnets for route restore
            host_node = db.execute(
                "SELECT id, pubkey, subnet, transit_subnets FROM nodes WHERE id=?",
                (t["host_node_id"],)).fetchone()
            join_node = db.execute(
                "SELECT id, pubkey, subnet, transit_subnets FROM nodes WHERE id=?",
                (t["join_node_id"],)).fetchone()
            if not host_node or not join_node:
                log.warning("restore_state: tunnel '%s' references missing "
                            "node(s) — deleting", t["name"])
                db.execute("DELETE FROM tunnels WHERE id=?", (t["id"],))
                db.commit()
                continue

            # [TRANSIT_SUBNETS_V1] restore owned + transit for each side.
            # peer_node is the node whose subnets this side's wg iface routes to.
            for side, iface, port, priv, transit, mask, ppub, peer_node, edge in (
                ("host", t["host_wg_iface"], t["host_wg_port"],
                 t["host_privkey"], t["host_transit_ip"], t["transit_mask"],
                 host_node["pubkey"], host_node, t["host_edge_ip"]),
                ("join", t["join_wg_iface"], t["join_wg_port"],
                 t["join_privkey"], t["join_transit_ip"], t["transit_mask"],
                 join_node["pubkey"], join_node, t["join_edge_ip"]),
            ):
                try:
                    ns.setup_port_forward(port, ns_name, pond["pond_index"])
                except Exception as e:
                    log.warning("port_fwd %d: %s", port, e)

                if iface not in live:
                    try:
                        # [IN_MEMORY_KEYS] The stored `priv` is now empty (keys are
                        # never persisted). The kernel iface holds the key while it
                        # exists; if it's gone (host reboot / ns rebuild) we re-key
                        # and republish the new pubkey so the node relearns it and
                        # re-handshakes on its next poll (node reconciles by
                        # droplet_pubkey — poll.py _rebuild_active_tunnels_*).
                        new_priv, new_pub = ns._wg_genkey()
                        ns.create_wg_interface(ns_name, iface, port, new_priv,
                                               transit, mask)
                        ns.add_wg_peer(ns_name, iface, ppub, ["10.0.0.0/8"])
                        _pubcol = "host_pubkey" if side == "host" else "join_pubkey"
                        db.execute(
                            f"UPDATE tunnels SET {_pubcol}=? WHERE id=?",
                            (new_pub, t["id"]))
                        db.commit()
                    except Exception as e:
                        log.error("restore %s %s: %s — deleting tunnel '%s'",
                                  side, iface, e, t["name"])
                        db.execute("UPDATE tunnels SET active=0 WHERE id=?",
                                   (t["id"],))
                        db.execute("DELETE FROM tunnels WHERE id=?",
                                   (t["id"],))
                        db.commit()
                        break

                # Always (re)install owned + transit routes — the WG iface
                # may persist across broker restarts but routes do not.
                # [OWNED_SUBNET_PRECEDENCE_V1] re-tag ownership on restore so
                # the owner map is rebuilt and owned subnets keep precedence.
                peer_owned = (peer_node["subnet"] if "subnet"
                              in peer_node.keys() else "")
                for s in _node_subnets_for_side(peer_node):
                    try:
                        ns.add_route(ns_name, s, edge, iface,
                                     owned=(s == peer_owned))
                    except Exception as e:
                        log.warning("restore %s route %s via %s dev %s: %s",
                                    side, s, edge, iface, e)
    db.close()
    log.info("restore_state: done")


@app.on_event("startup")
async def startup():
    global ADMIN_TOKEN
    BROKER_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    if not ADMIN_TOKEN:
        db = get_db()
        row = db.execute(
            "SELECT value FROM admin_config WHERE key='admin_token'"
        ).fetchone()
        db.close()
        if row:
            ADMIN_TOKEN = row["value"]
            os.environ["FROGNET_ADMIN_TOKEN"] = ADMIN_TOKEN
    _restore_state()
    log.info("FrogNet Tunnel Broker v3.1 ready")


if __name__ == "__main__":
    host = os.environ.get("FROGNET_BROKER_HOST", "0.0.0.0")
    port = int(os.environ.get("FROGNET_BROKER_PORT", "18427"))
    log.info("Starting broker on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level=LOG_LEVEL.lower())
