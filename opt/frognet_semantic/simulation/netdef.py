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
netdef.py - a YAML network definition, compiled into the two models FrogSim runs.

    netdef.yaml  --compile-->  [Machine]      -> capability / tuple space / election
                               TopologySpec   -> discovery merge / convergence

Both come from ONE definition, so a network is described once.

VOCABULARY (deliberately the vocabulary that already exists in the codebase --
discovery/sim/shapes.py defines snake, ring, star, snowflake as functions):

    networks:
      net1:
        subnet: 10.101.101          # served /24; auto-assigned if omitted
        hardware: {profile: pi5, memory_gb: 64, disk: {class: class3, size_gb: 1000}}
        projects_ssid: true         # onboard radio projects this network's SSID
        external_antenna: true      # documentation only; no RF model exists
        roles: [mediahost, databasehost]
        hosts:                      # machines ON net1's projected LAN
          - {name: net1-db, hardware: {profile: db_appliance, memory_gb: 256},
             roles: [databasehost], addr: 10.101.101.50}
        in_range: [net3, net4]      # associates to those networks' APs
        tunnel_to: [net5, net6]     # Internet tunnels (WireGuard)

      net3: {generic: true}
      net4: {generic: true}

GENERATIVE FORM (for "build me N access points with K attached LANs each"):

    generate:
      access_points: 3
      attached_per_ap: 15
      ap_mesh: full                 # full | ring | star  (tunnels between APs)
      lan_shape: random             # star | snake | snowflake | ring | random
      seed: 1234
      ap_hardware: {profile: ap_class}
      attached_hardware: {profile: generic}

`in_range` IS A CANDIDATE SET, NOT SIMULTANEOUS ASSOCIATION. A station
associates to ONE access point at a time; the rest are what it can fail over to
when the current one goes away. That maps exactly onto NodeSpec's single
`guest_on` uplink -- no representation is lost. The list drives ROAMING:

    associate: net4      # optional; defaults to the first in_range entry

`CompiledNet.roam` carries {node: [candidates]} so a run can take the active AP
down, re-associate to the next candidate in range, re-converge, and measure the
recovery. That is the behaviour the list exists to describe, and it is tested
(objective `roam` in reported_run.py).
"""
from __future__ import annotations

import os
import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hardware
from machine import Device, Iface, Machine

SEM = os.environ.get("FROGNET_SEM_ROOT", "/home/claude/src/opt/frognet_semantic")
if SEM not in sys.path:
    sys.path.insert(0, SEM)
from discovery.sim.system import NodeSpec, TopologySpec   # noqa: E402

SHAPES = ("star", "snake", "snowflake", "ring")

# Standard device set for a node that has no explicit `devices:`.
_STD_DEVICES = [("cpu_thermal", "thermal", "System"),
                ("root_fs", "disk", "System"),
                ("link", "linkstate", "LinkState")]


@dataclass
class CompiledNet:
    machines: List[Machine] = field(default_factory=list)
    spec: TopologySpec = None
    unmodelled: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # net name -> the Machine that serves it (for "run X between net1 and net4")
    by_net: Dict[str, Machine] = field(default_factory=dict)
    # station -> ordered AP candidates it is in range of. The station is
    # associated to exactly one at a time (the first that is still present);
    # the rest are failover targets. Drives the roam scenario.
    roam: Dict[str, List[str]] = field(default_factory=dict)
    subnets: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _auto_subnet(i: int) -> str:
    """Deterministic /24 allocation avoiding the reserved planes: 10.253.x is
    cross-NAT transit and 10.254.x is chorus, and discovery excludes both from
    node identity everywhere (see DBHOST_EXCLUDE_RESERVED_V1)."""
    o2 = 10 + (i // 240)
    o3 = 10 + (i % 240)
    if o2 in (253, 254):
        o2 += 2
    return f"10.{o2}.{o3}"


def _mk_machine(name: str, subnet3: str, hw: Dict, roles: List[str],
                *, addr: Optional[str] = None, devices=None,
                projects_ssid: bool = False) -> Machine:
    """Turn a resolved hardware dict into a Machine (which IS the probe blob)."""
    ip = addr or f"{subnet3}.1"
    mem_kb = int(float(hw["memory_gb"]) * 1024 * 1024)
    is_db = "databasehost" in roles
    ifname = "wlan0" if projects_ssid else "eth0"
    m = Machine(
        name=name, domain=name,
        ifaces=[Iface(ifname, ip, 24, "ap" if projects_ssid else "lan")],
        devices=[], roles=list(roles),
        cores=int(hw["cores"]), cpu_mhz=int(hw["cpu_mhz"]),
        cpu_bench_total=float(hw["cpu_bench_total"]),
        mem_total_kb=mem_kb,
        # available RAM is not scored ([DBHOST_STATIC_RANK_V1] ranks INSTALLED
        # RAM) but the probe schema requires the field, so derive it.
        mem_available_kb=int(mem_kb * 0.8),
        disk_class=hardware.normalize_disk_class(hw["disk_class"]),
        disk_write_mbps=float(hw["disk_write_mbps"]),
        disk_fsync_ms=float(hw["disk_fsync_ms"]),
        disk_free_gb=float(hw["disk_size_gb"]) * 0.6,
        mysql=is_db, mysql_running=is_db,
        # innodb pool: a DB box is configured with a real fraction of RAM.
        mysql_innodb_pool_bytes=int(mem_kb * 1024 * 0.5) if is_db else 0,
        arch=hw.get("arch", "aarch64"),
    )
    for dn, dk, st in (devices or _STD_DEVICES):
        m.devices.append(Device(dn, dk, st, {}))
    return m


def _shape_edges(members: List[str], shape: str, rng: random.Random
                 ) -> List[Tuple[str, str]]:
    """LAN-internal arrangement among `members` (the AP is members[0]).
    Mirrors discovery/sim/shapes.py semantics."""
    if len(members) < 2:
        return []
    hub, rest = members[0], members[1:]
    if shape == "star":
        return [(hub, m) for m in rest]
    if shape == "snake":
        chain = [hub] + rest
        return [(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    if shape == "ring":
        chain = [hub] + rest
        e = [(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
        e.append((chain[-1], chain[0]))
        return e
    if shape == "snowflake":
        # star of stars: sub-hubs every ~sqrt(n)
        import math
        k = max(1, int(math.sqrt(len(rest))))
        subs, edges, leaves = rest[:k], [], rest[k:]
        edges += [(hub, s) for s in subs]
        for i, leaf in enumerate(leaves):
            edges.append((subs[i % len(subs)], leaf))
        return edges
    raise ValueError(f"unknown shape {shape!r}; known: {SHAPES}")


# ---------------------------------------------------------------------------
# declarative form
# ---------------------------------------------------------------------------

def _compile_declarative(cfg: Dict) -> CompiledNet:
    out = CompiledNet()
    nets = cfg.get("networks") or {}
    order = list(nets)
    subnets: Dict[str, str] = {}
    for i, nm in enumerate(order):
        d = nets[nm] or {}
        subnets[nm] = str(d.get("subnet") or _auto_subnet(i))

    specs: Dict[str, NodeSpec] = {}
    tunnels: List[Tuple[str, str]] = []

    for nm in order:
        d = nets[nm] or {}
        generic = bool(d.get("generic"))
        hw_cfg = dict(d.get("hardware") or {})
        profile = hw_cfg.pop("profile", "generic" if generic else "generic")
        hw = hardware.resolve(profile, **hw_cfg)
        projects = bool(d.get("projects_ssid", hw.get("ap_capable", False)))
        hw["projects_ssid"] = projects
        out.warnings += hardware.warnings(nm, hw)
        if d.get("external_antenna"):
            out.unmodelled.append(
                f"{nm}: external_antenna declared -- there is no RF/range model "
                f"in this simulator, so it affects nothing")
        roles = list(d.get("roles") or ["databasehost", "mediahost"])
        s3 = subnets[nm]
        out.machines.append(_mk_machine(nm, s3, hw, roles, projects_ssid=projects))
        specs[nm] = NodeSpec(name=nm, subnet3=s3)

        # hosts on this network's projected LAN -> guests
        for h in d.get("hosts") or []:
            hn = h.get("name") or f"{nm}-host"
            hhw_cfg = dict(h.get("hardware") or {})
            hprof = hhw_cfg.pop("profile", "generic")
            hhw = hardware.resolve(hprof, **hhw_cfg)
            out.warnings += hardware.warnings(hn, hhw)
            hroles = list(h.get("roles") or ["databasehost", "mediahost"])
            # A guest still SERVES its own /24 (it is a FrogNetHost), and holds a
            # client lease on its host's LAN. That is the proven guest pattern.
            gi = len(specs)
            gs3 = str(h.get("subnet") or _auto_subnet(200 + gi))
            addr = h.get("addr") or f"{s3}.{50 + len(specs)}"
            out.machines.append(_mk_machine(hn, gs3, hhw, hroles))
            specs[hn] = NodeSpec(name=hn, subnet3=gs3, guest_on=(nm, addr))

        for t in d.get("tunnel_to") or []:
            if t in nets and (t, nm) not in tunnels and (nm, t) not in tunnels:
                tunnels.append((nm, t))

    # in_range -> ONE association at a time; the rest are failover candidates.
    for nm in order:
        d = nets[nm] or {}
        cands = [x for x in (d.get("in_range") or [])]
        if not cands:
            continue
        unknown = [x for x in cands if x not in specs]
        for u in unknown:
            out.unmodelled.append(f"{nm}: in_range names unknown network {u!r}")
        cands = [x for x in cands if x in specs]
        if not cands:
            continue
        active = d.get("associate") or cands[0]
        if active not in cands:
            out.warnings.append(
                f"{nm}: associate={active!r} is not in in_range {cands}; "
                f"using {cands[0]!r}")
            active = cands[0]
        if specs[nm].guest_on:
            out.warnings.append(
                f"{nm}: already a guest on {specs[nm].guest_on[0]}; "
                f"in_range association skipped")
            continue
        specs[nm].guest_on = (active, f"{subnets[active]}.{80 + order.index(nm)}")
        out.roam[nm] = [active] + [x for x in cands if x != active]

    out.spec = TopologySpec(nodes=list(specs.values()), tunnels=tunnels)
    out.by_net = {m.name: m for m in out.machines}
    out.subnets = subnets
    return out


# ---------------------------------------------------------------------------
# generative form
# ---------------------------------------------------------------------------

def _compile_generated(cfg: Dict) -> CompiledNet:
    g = cfg["generate"]
    n_ap = int(g.get("access_points", 3))
    per_ap = int(g.get("attached_per_ap", 15))
    ap_mesh = str(g.get("ap_mesh", "full"))
    shape_cfg = str(g.get("lan_shape", "random"))
    rng = random.Random(g.get("seed", 0))

    ap_hw_cfg = dict(g.get("ap_hardware") or {})
    ap_prof = ap_hw_cfg.pop("profile", "ap_class")
    at_hw_cfg = dict(g.get("attached_hardware") or {})
    at_prof = at_hw_cfg.pop("profile", "generic")
    ap_hw = hardware.resolve(ap_prof, **ap_hw_cfg)
    at_hw = hardware.resolve(at_prof, **at_hw_cfg)

    out = CompiledNet()
    specs: List[NodeSpec] = []
    tunnels: List[Tuple[str, str]] = []
    idx = 0
    ap_names = []
    shapes_used = {}

    for a in range(n_ap):
        ap = f"AP{a}"
        s3 = _auto_subnet(idx); idx += 1
        ap_names.append(ap)
        out.machines.append(_mk_machine(ap, s3, ap_hw,
                                        ["databasehost", "mediahost"],
                                        projects_ssid=True))
        specs.append(NodeSpec(name=ap, subnet3=s3))
        shape = rng.choice(SHAPES) if shape_cfg == "random" else shape_cfg
        shapes_used[ap] = shape

        members = [ap]
        for k in range(per_ap):
            nn = f"{ap}-N{k}"
            ns3 = _auto_subnet(idx); idx += 1
            members.append(nn)
            out.machines.append(_mk_machine(nn, ns3, at_hw,
                                            ["databasehost", "mediahost"]))
            specs.append(NodeSpec(name=nn, subnet3=ns3))

        # The LAN arrangement. A node attached to the AP's projected SSID is a
        # guest on the AP. A shape edge that is NOT AP->leaf (snake/ring/
        # snowflake) is a node hanging off ANOTHER node's LAN, which is the same
        # guest relation one level down.
        by_name = {s.name: s for s in specs}
        addr_n = 50
        ap_names_set = set(ap_names)
        for (parent, child) in _shape_edges(members, shape, rng):
            # [RING_CANNOT_REPARENT_THE_AP] the ring shape closes with an edge
            # from the last leaf BACK to the hub. Treating that as a parent-child
            # uplink makes the access point a guest of one of its own stations,
            # which is not a thing: the AP is the root of its LAN. Observed as
            # AP0 vanishing from the topology diagram into its own leaf ring.
            # The closing edge is peer adjacency, not an uplink; skip it.
            if child in ap_names_set:
                out.warnings.append(
                    f"{shape}: closing edge {parent}->{child} would make the "
                    f"access point a guest of its own station; treated as peer "
                    f"adjacency, not an uplink")
                continue
            cs = by_name[child]
            if cs.guest_on is None:
                ps3 = by_name[parent].subnet3
                cs.guest_on = (parent, f"{ps3}.{addr_n}")
                addr_n += 1
            else:
                out.unmodelled.append(
                    f"{child}: shape {shape!r} gives it a second parent "
                    f"({parent}); NodeSpec carries one uplink, kept "
                    f"{cs.guest_on[0]}")

    # AP-to-AP Internet tunnels
    if ap_mesh == "full":
        for i in range(len(ap_names)):
            for j in range(i + 1, len(ap_names)):
                tunnels.append((ap_names[i], ap_names[j]))
    elif ap_mesh == "ring":
        for i in range(len(ap_names)):
            tunnels.append((ap_names[i], ap_names[(i + 1) % len(ap_names)]))
    elif ap_mesh == "star":
        for i in range(1, len(ap_names)):
            tunnels.append((ap_names[0], ap_names[i]))
    else:
        raise ValueError(f"unknown ap_mesh {ap_mesh!r}; known: full, ring, star")

    out.warnings += hardware.warnings("AP", ap_hw)
    out.warnings += hardware.warnings("attached", at_hw)
    out.warnings.append(f"generated shapes: " +
                        ", ".join(f"{k}={v}" for k, v in shapes_used.items()))
    out.spec = TopologySpec(nodes=specs, tunnels=tunnels)
    out.by_net = {m.name: m for m in out.machines}
    return out


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def compile_config(cfg: Dict) -> CompiledNet:
    if "generate" in cfg:
        return _compile_generated(cfg)
    if "networks" in cfg:
        return _compile_declarative(cfg)
    raise ValueError("config must contain either 'networks:' or 'generate:'")


def load(path: str) -> CompiledNet:
    import yaml
    with open(path) as f:
        return compile_config(yaml.safe_load(f))
