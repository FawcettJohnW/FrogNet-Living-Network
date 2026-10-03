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
oracle_tunnel_idkey.py  —  prove the tunnel re-key.

Scenario (the real BABox/BAMacBook incident, minimized):
  1. Seattle5 and a box named "BABox" (node id A) register; build the
     BABox<->Seattle5 tunnel via _ensure_tunnel -> bound to id A.
  2. The "BABox" box is renamed to "BAMacBook" in place (same id A) — exactly
     what the register guid-revive path does, and the mistake a human makes.
  3. A DIFFERENT box registers as "BABox" (node id B). The real BABox now wants
     its BABox<->Seattle5 tunnel.

Assertion: _ensure_tunnel(newBABox, Seattle5) must return a tunnel bound to id B
(the current BABox) and NOT id A (the renamed box).

  - name-keyed broker (orig): matches the stale "BABox-to-Seattle5" row bound to
    id A and returns it -> peer carries id-A's pubkey -> FAIL.
  - id-keyed broker (fixed): no tunnel for the (B,Seattle5) pair -> builds it
    bound to id B -> PASS.

Usage: python3 oracle_tunnel_idkey.py <path-to-broker-module.py>
Exit 0 = PASS (binding follows identity), 1 = FAIL (binding follows stale name).
"""
import os
import sys
import time
import types
import tempfile
import importlib.util


def load_broker(path):
    # Inject the namespace stub BEFORE importing the broker.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import broker_namespace_v4  # the stub in this dir
    sys.modules["broker_namespace_v4"] = broker_namespace_v4

    # Fresh temp DB dir for this run.
    tmp = tempfile.mkdtemp(prefix="frognet_oracle_")
    os.environ["FROGNET_BROKER_DIR"] = tmp
    os.environ.setdefault("FROGNET_WG_PORT_BASE", "51901")
    os.environ.setdefault("FROGNET_WG_PORT_MAX", "51999")

    spec = importlib.util.spec_from_file_location("broker_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp


def _insert_node(db, pubkey, name, subnet, guid, pond_id):
    db.execute(
        "INSERT INTO nodes (pubkey,name,label,subnet,mac,guid,pond_id,"
        "created_at,active,transit_subnets) VALUES (?,?,?,?,?,?,?,?,1,'[]')",
        (pubkey, name, "", subnet, "", guid, pond_id, time.time()))
    db.commit()
    return db.execute("SELECT id FROM nodes WHERE pubkey=?", (pubkey,)).fetchone()["id"]


def run(path):
    B, tmp = load_broker(path)
    B.init_db()

    # The real _used_wg_ports shells out to `ss`/`ip netns` (absent in the
    # sandbox and irrelevant to identity). Keep DB-based port dedup only.
    def _db_only_used_ports(db):
        ports = set()
        for col in ("host_wg_port", "join_wg_port"):
            for r in db.execute(f"SELECT {col} FROM tunnels WHERE active=1"):
                ports.add(r[0])
        return ports
    B._used_wg_ports = _db_only_used_ports

    db = B.get_db()

    # Minimal pond.
    db.execute(
        "INSERT INTO ponds (name,pond_index,ns_name,created_at,active) "
        "VALUES ('p',1,'pondv4_p',?,1)", (time.time(),))
    db.commit()
    pond = db.execute("SELECT * FROM ponds WHERE name='p'").fetchone()

    sea = _insert_node(db, "KEY_SEA", "Seattle5", "10.250.250.0/24", "G_SEA", pond["id"])
    a   = _insert_node(db, "KEY_A",   "BABox",    "10.111.11.0/24",  "G_A",   pond["id"])

    # 1. build BABox(idA) <-> Seattle5
    sea_row = db.execute("SELECT * FROM nodes WHERE id=?", (sea,)).fetchone()
    a_row   = db.execute("SELECT * FROM nodes WHERE id=?", (a,)).fetchone()
    t1 = B._ensure_tunnel(db, pond, a_row, sea_row)
    assert a in (t1["host_node_id"], t1["join_node_id"]), "setup: t1 not bound to A"
    print(f"  built BABox<->Seattle5: name={t1['name']!r} bound=({t1['host_node_id']},{t1['join_node_id']}) (A={a})")

    # 2. rename the box: id A keeps its id, name -> BAMacBook, subnet moves.
    db.execute("UPDATE nodes SET name='BAMacBook', subnet='10.179.178.0/24' WHERE id=?", (a,))
    db.commit()
    print(f"  renamed id={a}: BABox -> BAMacBook (stored tunnel name is now stale)")

    # 3. a different box becomes BABox (id B).
    b = _insert_node(db, "KEY_B", "BABox", "10.111.11.0/24", "G_B", pond["id"])
    b_row = db.execute("SELECT * FROM nodes WHERE id=?", (b,)).fetchone()
    sea_row = db.execute("SELECT * FROM nodes WHERE id=?", (sea,)).fetchone()

    # The real BABox asks for its tunnel to Seattle5.
    t2 = B._ensure_tunnel(db, pond, b_row, sea_row)
    bound = (t2["host_node_id"], t2["join_node_id"])
    print(f"  _ensure_tunnel(newBABox={b}, Seattle5={sea}) -> name={t2['name']!r} bound={bound}")

    db.close()

    ok = (b in bound) and (a not in bound)
    if ok:
        print("RESULT: PASS — tunnel bound to the CURRENT BABox (id %d); rename did not orphan it." % b)
        return 0
    print("RESULT: FAIL — tunnel bound to the RENAMED box (id %d), not the current BABox (id %d)." % (a, b))
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(run(sys.argv[1]))
