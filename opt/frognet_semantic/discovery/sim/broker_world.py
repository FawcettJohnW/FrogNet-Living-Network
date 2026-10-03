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
sim/broker_world.py - run the REAL broker against the simulator.

The broker (frognet_broker_v4.py) is straight Python + sqlite; only its web
transport (fastapi/pydantic/uvicorn) and its namespace ops (ns.* = wg/netns on
the broker host) are environment-coupled. We stub ONLY those, then call the
actual handlers - create_pond, register_node - and the actual channel assembler
_compute_aggregate, against a temp DB. Everything that decides topology (chorus
membership, _ensure_tunnel pairing, IP/port allocation, channel naming, transit)
runs for real. Its output (each node's channels) is then fed to the real ported
discovery, and the whole thing is converged + validated.

This is the LAN short-circuit case: two LAN-local nodes that are pond members get
a direct broker tunnel, so the shortest path between them is that 2-hop tunnel,
not a relay through the gateway.

Locate the broker via $FROGNET_BROKER_DIR (default: the uploaded tree).
"""
from __future__ import annotations

import os, sys, types, tempfile, asyncio, secrets
from pathlib import Path

BROKER_DIR = os.environ.get(
    "FROGNET_BROKER_DIR",
    "/opt/frognet_semantic/broker")


# ---- stub ONLY the web transport + namespace system ops --------------------

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
    ns.ensure_namespace = lambda *a, **k: None
    ns.create_wg_interface = lambda *a, **k: None
    ns.add_wg_peer = lambda *a, **k: None
    ns.add_route = lambda *a, **k: None
    ns.setup_port_forward = lambda *a, **k: None
    ns.teardown_port_forward = lambda *a, **k: None
    ns.delete_wg_interface = lambda *a, **k: None
    ns._run = lambda *a, **k: ""
    ns._veth_ips = lambda *a, **k: ("10.255.255.1", "10.255.255.2")
    ns.get_public_ip = lambda: "203.0.113.9"
    ns._wg_genkey = lambda: (secrets.token_hex(16), secrets.token_hex(16))
    broker.ns = ns


def _load_broker():
    _install_web_stubs()
    import logging
    logging.getLogger("broker").setLevel(logging.ERROR)
    if BROKER_DIR not in sys.path:
        sys.path.insert(0, BROKER_DIR)
    import importlib
    broker = importlib.import_module("frognet_broker_v4")
    importlib.reload(broker)               # ensure stubs take effect
    broker.DB_PATH = Path(tempfile.mkdtemp()) / "broker_sim.db"
    _stub_ns(broker)
    broker._check_admin_token = lambda *a, **k: None   # bypass admin auth
    # live port scan shells out to `ss` (absent here); return empty so only the
    # broker's DB-tracked ports count. All allocation logic stays real.
    fake_sp = types.SimpleNamespace()
    fake_sp.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    fake_sp.PIPE = fake_sp.DEVNULL = None
    fake_sp.CalledProcessError = Exception
    broker.subprocess = fake_sp
    broker.init_db()
    return broker


# ---- run the real broker for a set of nodes --------------------------------

def _register_pond(broker, pond, node_specs, node_base="10.0.0.1"):
    """Create one pond on an already-loaded broker, register its nodes, bump
    them fresh, and return {name: (pubkey, node_row)}. Real broker code."""
    asyncio.run(broker.create_pond(broker.PondCreate(name=pond, node_base=node_base),
                                   authorization="admin"))
    reg = {}
    for (name, s3) in node_specs:
        pk = f"PUB-{pond}-{name}-{secrets.token_hex(4)}"
        asyncio.run(broker.register_node(broker.RegisterRequest(
            pond=pond, pubkey=pk, subnet=f"{s3}.0/24",
            node_name=name, mac=f"MAC-{pond}-{name}")))
        reg[name] = pk
    db = broker.get_db()
    for name, pk in reg.items():
        broker._bump_last_seen(db, broker._get_node_by_pubkey(db, pk)["id"])
    db.close()
    return reg


def _channels_for(broker, pond, reg):
    db = broker.get_db()
    out = {}
    for name, pk in reg.items():
        out[name] = broker._compute_aggregate(db, broker._get_node_by_pubkey(db, pk))
    db.close()
    return out


def run_broker_pond(node_specs, pond="simpond"):
    """One pond. Returns {name: [channel,...]} and the derived tunnel edge set."""
    broker = _load_broker()
    reg = _register_pond(broker, pond, node_specs)
    channels = _channels_for(broker, pond, reg)
    edges = set()
    for name, chans in channels.items():
        for c in chans:
            edges.add(tuple(sorted((name, c["node_name"]))))
    return channels, sorted(edges)


def run_multi_lan_wan(lans, wan_pond="wan_broker"):
    """Correct broker roles - LAN and WAN brokers do NOT mix.

    lans: {lan_name: {"members": [(name, subnet3), ...], "gateway": name}}

    Each LAN broker is its own pond supporting a SINGLE LAN: it pairs that LAN's
    members pairwise (short-circuit tunnels). The WAN broker is ONE pond whose
    members are the gateways ONLY; it tunnels gateways to each other, which is
    how LAN-to-LAN networking happens. A non-gateway node never enters the WAN
    pond, so it has no direct tunnel off its LAN - it transits its gateway.
    Returns (union_edges, {name: subnet3}). Real broker decides everything."""
    broker = _load_broker()
    subnets, lan_regs = {}, {}
    for lan_name, spec in lans.items():
        for (n, s3) in spec["members"]:
            subnets[n] = s3
        lan_regs[lan_name] = _register_pond(
            broker, f"lanbroker_{lan_name}", spec["members"])
    # WAN broker pond: gateways only
    gws = [(spec["gateway"], subnets[spec["gateway"]]) for spec in lans.values()]
    wan_reg = _register_pond(broker, wan_pond, gws)

    edges = set()
    for lan_name, spec in lans.items():
        for name, chans in _channels_for(
                broker, f"lanbroker_{lan_name}", lan_regs[lan_name]).items():
            for c in chans:
                edges.add(tuple(sorted((name, c["node_name"]))))
    for name, chans in _channels_for(broker, wan_pond, wan_reg).items():
        for c in chans:
            edges.add(tuple(sorted((name, c["node_name"]))))
    return sorted(edges), subnets


def multi_lan_wan_system(lans):
    """Run all LAN brokers + the WAN broker, union the tunnels, run the real
    ported discovery on the combined graph. Returns (System, edges, subnets)."""
    from discovery.sim.system import System, TopologySpec, NodeSpec
    edges, subnets = run_multi_lan_wan(lans)
    nodes = [NodeSpec(name, s3) for name, s3 in subnets.items()]
    spec = TopologySpec(nodes=nodes, tunnels=[list(e) for e in edges])
    return System(spec), edges, subnets


# ---- incremental ops for the lifecycle harness (real broker, one pond) ------

def load_broker():
    """Load the real broker with web/ns/subprocess stubbed and a fresh temp DB."""
    return _load_broker()


def pond_create(broker, pond, node_base="10.0.0.1"):
    asyncio.run(broker.create_pond(broker.PondCreate(name=pond, node_base=node_base),
                                   authorization="admin"))


def node_register(broker, pond, name, s3):
    """Real register_node + bump last_seen. Returns the node's pubkey."""
    pk = f"PUB-{pond}-{name}-{secrets.token_hex(4)}"
    asyncio.run(broker.register_node(broker.RegisterRequest(
        pond=pond, pubkey=pk, subnet=f"{s3}.0/24", node_name=name,
        mac=f"MAC-{pond}-{name}")))
    db = broker.get_db()
    broker._bump_last_seen(db, broker._get_node_by_pubkey(db, pk)["id"])
    db.close()
    return pk


def node_retire(broker, pubkey):
    """Take a node down: clear the broker's own active flag, which is exactly
    what _compute_aggregate filters on (active=1), so the node drops out of all
    pairings. Mirrors the broker's membership gate, no invented behaviour."""
    db = broker.get_db()
    db.execute("UPDATE nodes SET active=0 WHERE pubkey=?", (pubkey,))
    db.commit()
    db.close()


def edges_for(broker, pond, reg):
    """reg = {name: pubkey} of CURRENTLY-ACTIVE members. Returns
    (channels, sorted_edges) from the real _compute_aggregate."""
    ch = _channels_for(broker, pond, reg)
    edges = set()
    for name, chans in ch.items():
        for c in chans:
            edges.add(tuple(sorted((name, c["node_name"]))))
    return ch, sorted(edges)


# ---- drive the real discovery on the broker's real pairings -----------------

def broker_driven_system(node_specs, pond="simpond"):
    """Run the real broker to decide tunnels, then build a System (real ported
    discovery) on those tunnels. Returns (System, channels, edges)."""
    from discovery.sim.system import System, TopologySpec, NodeSpec
    channels, edges = run_broker_pond(node_specs, pond)
    nodes = [NodeSpec(name, s3) for (name, s3) in node_specs]
    spec = TopologySpec(nodes=nodes, tunnels=[list(e) for e in edges])
    return System(spec), channels, edges


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # 5 pond members; the broker pairs all of them (entire_pond chorus).
    specs = [("M1", "10.11.11"), ("M4", "10.14.14"), ("M7", "10.17.17"),
             ("M10", "10.20.20"), ("GW", "10.9.9")]
    s, channels, edges = broker_driven_system(specs)
    print("REAL broker pairings (tunnel edges):")
    for e in edges:
        print("   ", e)
    print(f"\nbroker channels for M4: "
          f"{[c['channel_name'] for c in channels['M4']]}")
    lan_pair = ("M10", "M4")
    print(f"\nLAN short-circuit M4<->M10 tunnel exists? "
          f"{tuple(sorted(lan_pair)) in edges}")
    cyc = s.converge()
    print(f"\nreal discovery converged in {cyc} cycles on broker-decided tunnels")
    probs = s.validate()
    print("VALIDATION:", "PASS" if not probs else f"FAIL {probs[:3]}")
