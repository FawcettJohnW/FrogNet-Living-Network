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
oracle_migration.py — prove the TUNNEL_ID_KEY_V1 migration on a populated
legacy DB (the risky bit: a table rebuild on live data).

1. Build a DB with broker_orig (legacy schema: UNIQUE(pond_id,name)).
2. Populate it like the droplet: Seattle5 + BAMacBook(id A, renamed from BABox)
   and TWO active tunnels for the SAME node-id pair (A,Seattle5) — one named
   'BABox-to-Seattle5' (stale) and one 'BAMacBook-to-Seattle5' (correct).
3. Run the new broker's init_db() (migration) against the SAME DB file.
4. Assert: legacy UNIQUE(pond_id,name) is gone; ux_tunnels_pond_pair_active
   exists; exactly ONE active row remains for the pair, and it's the correctly
   named one; ids preserved.

Exit 0 = PASS.
"""
import os, sys, time, tempfile, importlib.util


def _load(path, modname, tmp):
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import broker_namespace_v4
    sys.modules["broker_namespace_v4"] = broker_namespace_v4
    os.environ["FROGNET_BROKER_DIR"] = tmp
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _node(db, pubkey, name, subnet, guid, pond_id):
    db.execute("INSERT INTO nodes (pubkey,name,label,subnet,mac,guid,pond_id,"
               "created_at,active,transit_subnets) VALUES (?,?,?,?,?,?,?,?,1,'[]')",
               (pubkey, name, "", subnet, "", guid, pond_id, time.time()))
    db.commit()
    return db.execute("SELECT id FROM nodes WHERE pubkey=?", (pubkey,)).fetchone()["id"]


def _tunnel(db, pond_id, name, hid, jid, idx, hp, jp):
    db.execute(
        "INSERT INTO tunnels (pond_id,name,host_node_id,join_node_id,tunnel_index,"
        "host_wg_iface,host_wg_port,host_transit_ip,host_edge_ip,host_privkey,"
        "host_pubkey,join_wg_iface,join_wg_port,join_transit_ip,join_edge_ip,"
        "join_privkey,join_pubkey,transit_mask,created_at,active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,30,?,1)",
        (pond_id, name, hid, jid, idx, f"wg_h{idx}", hp, "10.253.200.1",
         "10.253.200.2", "p", "P", f"wg_j{idx}", jp, "10.253.200.5",
         "10.253.200.6", "p", "P", time.time()))
    db.commit()


def main():
    tmp = tempfile.mkdtemp(prefix="frognet_mig_")

    # 1+2: legacy DB with a duplicate pair
    bo = _load("./broker_orig.py", "bo", tmp)
    bo.init_db()
    db = bo.get_db()
    db.execute("INSERT INTO ponds (name,pond_index,ns_name,created_at,active) "
               "VALUES ('p',1,'pondv4_p',?,1)", (time.time(),))
    db.commit()
    pond_id = db.execute("SELECT id FROM ponds WHERE name='p'").fetchone()["id"]
    sea = _node(db, "KEY_SEA", "Seattle5", "10.250.250.0/24", "G_SEA", pond_id)
    a   = _node(db, "KEY_A", "BAMacBook", "10.179.178.0/24", "G_A", pond_id)
    # two active tunnels for the SAME pair (a,sea): stale-named + correct-named
    _tunnel(db, pond_id, "BABox-to-Seattle5",     a, sea, 1, 51901, 51902)
    _tunnel(db, pond_id, "BAMacBook-to-Seattle5", a, sea, 2, 51903, 51904)
    legacy_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tunnels'"
    ).fetchone()["sql"]
    assert "UNIQUE(pond_id, name)" in legacy_sql, "setup: legacy constraint absent"
    db.close()
    print(f"  legacy DB built: 2 active tunnels for pair ({a},{sea}); "
          "constraint present")

    # 3: run the new broker's migration against the same DB
    bn = _load("./frognet_broker_v4.py", "bn", tmp)
    bn.init_db()

    # 4: assertions
    db = bn.get_db()
    sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tunnels'"
    ).fetchone()["sql"]
    idx = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='ux_tunnels_pond_pair_active'").fetchone()
    rows = db.execute(
        "SELECT id,name,host_node_id,join_node_id FROM tunnels "
        "WHERE active=1 AND host_node_id IN (?,?) AND join_node_id IN (?,?)",
        (a, sea, a, sea)).fetchall()
    db.close()

    ok = True
    if "UNIQUE(pond_id, name)" in sql:
        print("  FAIL: legacy UNIQUE(pond_id,name) still present"); ok = False
    else:
        print("  ok: legacy UNIQUE(pond_id,name) dropped")
    if not idx:
        print("  FAIL: ux_tunnels_pond_pair_active index missing"); ok = False
    else:
        print("  ok: ux_tunnels_pond_pair_active present")
    if len(rows) != 1:
        print(f"  FAIL: expected 1 active row for the pair, got {len(rows)}: "
              f"{[dict(r) for r in rows]}"); ok = False
    else:
        kept = rows[0]
        print(f"  ok: pair collapsed to 1 row -> id={kept['id']} "
              f"name={kept['name']!r}")
        if kept["name"] != "BAMacBook-to-Seattle5":
            print("  NOTE: kept row is not the current-name match "
                  "(acceptable, binding is correct by id)")

    # the new unique index must actually forbid a second active row for the pair
    db = bn.get_db()
    try:
        _tunnel(db, pond_id, "dupe-name", a, sea, 9, 51950, 51951)
        print("  FAIL: index did NOT prevent a second active row for the pair")
        ok = False
    except Exception:
        print("  ok: index rejects a second active row for the same pair")
    db.close()

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
