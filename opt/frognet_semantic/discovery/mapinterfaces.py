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
mapinterfaces.py - mapInterfaces ported. Classifies interfaces into the
FROGNET_* sets discovery/fixDefaultRoute consume. The sim injects these today;
the live path runs classify() over the real `ip addr` output.

classify() is pure over an interface fixture so it can be proven against the
setup_lillypad trace (an oracle: it logs the exact NY-1 classification).
RealInterfaceMap gathers the fixture from the system (ip link / ip addr /
operstate) and is the live entry's source.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_SKIP_PREFIXES = ("veth", "docker", "br-", "virbr")


def _is_physical(dev: str) -> bool:
    if dev == "lo":
        return False
    return not any(dev.startswith(p) for p in _SKIP_PREFIXES)


def _ip_of(addr: str) -> str:
    return addr.split("/")[0]


def _prefix_of(addr: str) -> str:
    return addr.split("/")[1] if "/" in addr else ""


@dataclass
class Iface:
    dev: str
    operstate: str
    addrs: list[str] = field(default_factory=list)   # "ip/prefix" in ip-emit order

    def ip4(self) -> str:
        return _ip_of(self.addrs[0]) if self.addrs else ""


def _has_transit30(ifc: Iface) -> bool:
    return any(_ip_of(a).startswith("10.253.253.") and _prefix_of(a) == "30" for a in ifc.addrs)


def _has_frognet_24(ifc: Iface) -> bool:
    for a in ifc.addrs:
        ip, pfx = _ip_of(a), _prefix_of(a)
        if pfx == "24" and ip.startswith("10.") and not ip.startswith("10.253.253."):
            return True
    return False


def _owns_served24(ifc: Iface) -> bool:
    for a in ifc.addrs:
        ip = _ip_of(a)
        o = ip.split(".")
        if len(o) == 4 and ip.startswith("10.") and o[3] == "1" and not ip.startswith("10.253.253."):
            return True
    return False


def _seed_from_ip4(ip: str, sim_underlay) -> str:
    if not ip.startswith("10.") or ip.startswith("10.253.253."):
        return ""
    if _in_sim_underlay(ip, sim_underlay):
        return ""
    o = ip.split(".")
    return f"{o[0]}.{o[1]}.{o[2]}.1"


def _in_sim_underlay(ip: str, sim_underlay) -> bool:
    o = ip.split(".")
    net = f"{o[0]}.{o[1]}.{o[2]}.0/24"   # convertToSubnetRange (/24)
    return net in sim_underlay


def _upstream_ips(ifc: Iface, sim_underlay) -> list[str]:
    out = []
    for a in ifc.addrs:
        ip = _ip_of(a)
        if not ip:
            continue
        if ip.startswith(("10.253.", "10.254.", "169.254.", "127.", "0.")):
            continue
        if ip.endswith(".1"):
            continue
        if ip == "255.255.255.255":
            continue
        try:
            if int(ip.split(".")[0]) >= 224:
                continue
        except ValueError:
            continue
        if _in_sim_underlay(ip, sim_underlay):
            continue
        out.append(ip)
    return out


def classify(interfaces: list[Iface], *, leases_nonempty: bool,
             sim_underlay=frozenset(), eth0_name: str = "eth0",
             transit_seeds=()) -> dict:
    """Reproduce the mapInterfaces FROGNET_* exports."""
    # _list_physical_interfaces: filter + sort -u; _list_up_interfaces: up/unknown
    phys = sorted({i.dev for i in interfaces if _is_physical(i.dev)})
    by_dev = {i.dev: i for i in interfaces}
    up = [d for d in phys if by_dev[d].operstate in ("up", "unknown")]

    INTERFACES, TRANSIT, FROGNET, DHCP, SEEDS, DEVIPS = [], [], [], [], [], []
    for dev in up:
        ifc = by_dev[dev]
        INTERFACES.append(dev)
        if _has_transit30(ifc):
            TRANSIT.append(dev)
        if _has_frognet_24(ifc):
            FROGNET.append(dev)
        if leases_nonempty and ifc.ip4().endswith(".1"):
            DHCP.append(dev)
        ip4 = ifc.ip4()
        if ip4:
            s = _seed_from_ip4(ip4, sim_underlay)
            if s:
                SEEDS.append(s)
        if dev != eth0_name and not _owns_served24(ifc) and not _has_transit30(ifc):
            for up_ip in _upstream_ips(ifc, sim_underlay):
                DEVIPS.append(f"{dev}={up_ip}")

    for s in transit_seeds:
        if s.startswith("10.") and not s.startswith("10.253.253."):
            SEEDS.append(s)

    return {
        "FROGNET_INTERFACES": " ".join(INTERFACES),
        "FROGNET_TRANSIT_DEVS": " ".join(TRANSIT),
        "FROGNET_FROGNET_DEVS": " ".join(FROGNET),
        "FROGNET_DHCP_DEVS": " ".join(DHCP),
        "FROGNET_UPSTREAM_SEEDS": " ".join(SEEDS),
        "FROGNET_UPSTREAM_DEVIPS": " ".join(DEVIPS),
    }


class RealInterfaceMap:
    """Live gatherer: build the Iface fixture from the system, then classify().
    Mirrors mapInterfaces' `ip -o link` / `ip -4 -o addr` / operstate reads.
    UNTESTED on-box here (no hardware in this environment)."""

    def __init__(self, ip_bin="/usr/sbin/ip"):
        self._ip = ip_bin

    def gather(self):
        import subprocess
        link = subprocess.run([self._ip, "-o", "link", "show"],
                              capture_output=True, text=True).stdout
        devs = []
        for line in link.splitlines():
            # "N: dev: <...>" -> dev (strip @parent)
            try:
                d = line.split(": ", 2)[1].split("@")[0].strip()
            except IndexError:
                continue
            devs.append(d)
        ifaces = []
        for d in devs:
            try:
                state = open(f"/sys/class/net/{d}/operstate").read().strip()
            except OSError:
                state = "unknown"
            addr = subprocess.run([self._ip, "-4", "-o", "addr", "show", "dev", d],
                                  capture_output=True, text=True).stdout
            addrs = []
            for ln in addr.splitlines():
                parts = ln.split()
                if "inet" in parts:
                    addrs.append(parts[parts.index("inet") + 1])
            ifaces.append(Iface(d, state, addrs))
        import os
        leases = "/var/lib/misc/dnsmasq.leases"
        leases_nonempty = os.path.isfile(leases) and os.path.getsize(leases) > 0
        return ifaces, leases_nonempty
