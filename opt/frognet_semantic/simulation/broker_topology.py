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
broker_topology.py (M3) - run the REAL broker to decide the pond, then drive the
REAL planner+committer over the topology it produced.

The broker (frognet_broker_v4.py) is straight Python + sqlite; only its web
transport (fastapi/pydantic/uvicorn) and host namespace ops (wg/netns) are
environment-coupled. We stub ONLY those, then call the actual handlers -
create_pond, register_node - and the actual channel assembler _compute_aggregate
against a temp DB. Everything that decides topology (membership, _ensure_tunnel
pairing, IP/port allocation, channel naming) runs for real. Its output (each
node's channels) becomes a frognet_sim Topology, converged + validated through
live_engine's real planner+committer.

Container vs hardware (per the fake-here / real-on-box rule):
  - here: web/ns/subprocess stubbed, temp sqlite, FakeIPRoute in live_engine.
  - box:  point FROGNET_BROKER_DIR at the live broker; the SAME flow runs against
          the real DB, and live_engine can use RealIPRoute (M2). The broker's
          *decision* code is identical in both.
"""
import os
import sys
import types
import tempfile
import asyncio
import secrets

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from frognet_log import get_logger
import live_engine as LE
import frognet_sim as H

log = get_logger("simulation.broker_topology")

BROKER_DIR = os.environ.get("FROGNET_BROKER_DIR", os.path.join(_PARENT, "broker"))


def _install_web_stubs():
    if "fastapi" not in sys.modules:
        fa = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        class FastAPI:
            def __init__(self, *a, **k): pass
            def _deco(self, *a, **k):
                def wrap(fn): return fn
                return wrap
            post = get = put = delete = patch = _deco
            on_event = middleware = _deco
            def add_middleware(self, *a, **k): pass

        def Header(default=None, *a, **k): return default
        fa.FastAPI = FastAPI; fa.HTTPException = HTTPException; fa.Header = Header
        resp = types.ModuleType("fastapi.responses")

        class JSONResponse:
            def __init__(self, content=None, status_code=200, **k):
                self.content, self.status_code = content, status_code
        resp.JSONResponse = JSONResponse
        fa.responses = resp
        sys.modules["fastapi"] = fa
        sys.modules["fastapi.responses"] = resp

    if "pydantic" not in sys.modules:
        pyd = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kw):
                for k in getattr(type(self), "__annotations__", {}):
                    if hasattr(type(self), k):
                        setattr(self, k, getattr(type(self), k))
                for k, v in kw.items():
                    setattr(self, k, v)
        pyd.BaseModel = BaseModel
        sys.modules["pydantic"] = pyd

    if "uvicorn" not in sys.modules:
        uv = types.ModuleType("uvicorn")
        uv.run = lambda *a, **k: None
        sys.modules["uvicorn"] = uv


def _stub_ns(broker):
    ns = types.SimpleNamespace()
    for m in ("ensure_namespace", "create_wg_interface", "add_wg_peer", "add_route",
              "setup_port_forward", "teardown_port_forward", "delete_wg_interface"):
        setattr(ns, m, lambda *a, **k: None)
    ns._run = lambda *a, **k: ""
    ns._veth_ips = lambda *a, **k: ("10.255.255.1", "10.255.255.2")
    ns.get_public_ip = lambda: "203.0.113.9"
    ns._wg_genkey = lambda: (secrets.token_hex(16), secrets.token_hex(16))
    broker.ns = ns


def load_broker():
    if not os.path.isfile(os.path.join(BROKER_DIR, "frognet_broker_v4.py")):
        raise FileNotFoundError(
            f"frognet_broker_v4.py not found under FROGNET_BROKER_DIR={BROKER_DIR}")
    _install_web_stubs()
    import logging
    logging.getLogger("broker").setLevel(logging.ERROR)
    if BROKER_DIR not in sys.path:
        sys.path.insert(0, BROKER_DIR)
    import importlib
    broker = importlib.import_module("frognet_broker_v4")
    importlib.reload(broker)
    from pathlib import Path
    broker.DB_PATH = Path(tempfile.mkdtemp()) / "broker_sim.db"
    _stub_ns(broker)
    broker._check_admin_token = lambda *a, **k: None
    fake_sp = types.SimpleNamespace()
    fake_sp.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    fake_sp.PIPE = fake_sp.DEVNULL = None
    fake_sp.CalledProcessError = Exception
    broker.subprocess = fake_sp
    broker.init_db()
    return broker


def broker_edges(node_specs, pond="simpond"):
    """Run the real broker for one pond; return (sorted tunnel edges, subnets)."""
    broker = load_broker()
    asyncio.run(broker.create_pond(broker.PondCreate(name=pond, node_base="10.0.0.1"),
                                   authorization="admin"))
    reg, subnets = {}, {}
    for (name, s3) in node_specs:
        subnets[name] = s3
        pk = f"PUB-{pond}-{name}-{secrets.token_hex(4)}"
        # [GUID_IDENTITY] a real node mints /etc/fnid at install; a sim node
        # mints its GUID here. The broker refuses empty guid (400) by decided
        # contract - the sim was registering pre-identity nodes.
        asyncio.run(broker.register_node(broker.RegisterRequest(
            pond=pond, pubkey=pk, subnet=f"{s3}.0/24", node_name=name,
            mac=f"MAC-{pond}-{name}", guid=f"SIMGUID-{pond}-{name}")))
        reg[name] = pk
    db = broker.get_db()
    for name, pk in reg.items():
        broker._bump_last_seen(db, broker._get_node_by_pubkey(db, pk)["id"])
    edges = set()
    for name, pk in reg.items():
        for c in broker._compute_aggregate(db, broker._get_node_by_pubkey(db, pk)):
            edges.add(tuple(sorted((name, c["node_name"]))))
    db.close()
    return sorted(edges), subnets


def build_topology(node_specs, label="broker pond", pond="simpond"):
    """Real broker decides edges; assemble a frognet_sim Topology from them
    (eth0 served /24 per node, a /30 wgN pair per broker tunnel edge)."""
    edges, subnets = broker_edges(node_specs, pond)
    t = H.Topology(f"{label} [broker-decided: {len(edges)} tunnels]")
    nodes = {}
    for name, s3 in subnets.items():
        nodes[name] = H.FrogNode(name).add_iface("eth0", f"{s3}.1/24")
        t.add(nodes[name])
    wg_idx = {name: 0 for name in subnets}
    for e, (a, b) in enumerate(edges, start=1):
        link = f"10.252.{e}"
        da, db_ = f"wg{wg_idx[a]}", f"wg{wg_idx[b]}"
        wg_idx[a] += 1; wg_idx[b] += 1
        nodes[a].add_iface(da, f"{link}.1/30")
        nodes[b].add_iface(db_, f"{link}.2/30")
        t.link((a, da), (b, db_))
    return t, edges


def main():
    print("=== M3: REAL broker decides pond -> REAL planner+committer validates ===")
    try:
        load_broker()
    except FileNotFoundError as e:
        print(f"  [SKIP] {e}")
        print("        set FROGNET_BROKER_DIR to the broker source dir to enable.")
        return 0

    scenarios = [
        ("5-member pond (entire-pond chorus)",
         [("M1", "10.11.11"), ("M4", "10.14.14"), ("M7", "10.17.17"),
          ("M10", "10.20.20"), ("GW", "10.9.9")]),
        ("3-site pond",
         [("Seattle", "10.241.241"), ("NewYork", "10.122.221"),
          ("Austin", "10.44.44")]),
        ("4-member pond",
         [("A", "10.61.1"), ("B", "10.61.2"), ("C", "10.61.3"), ("D", "10.61.4")]),
    ]
    allok = True
    for label, specs in scenarios:
        try:
            topo, edges = build_topology(specs, label=label)
            ok = LE.run_topo_real(topo)
            allok &= ok
        except Exception as e:
            allok = False
            print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
    print("\nALL M3 BROKER-IN-THE-LOOP PASS" if allok else "\nM3 FAILURES")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
