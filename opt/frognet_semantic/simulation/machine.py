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
machine.py - the MACHINE DEFINITION a simulated node brings up from.

A definition declares the hardware and the attachments; the node runtime turns
it into (a) the capability blob the real election scores, and (b) the sensor set
that publishes over time.

The capability field names here are NOT invented. They are exactly the fields
/usr/local/bin/frognet_capability_probe.sh emits and
core/database_handler.py:score() reads:

    REQUIRED numeric (probe's _AVCAP_NUMERIC_REQ):
        cores cpu_mhz cpu_bench_total mem_total_kb mem_available_kb
        disk_write_mbps disk_fsync_ms disk_free_gb av_port
    OPTIONAL numeric (_AVCAP_NUMERIC_OPT):
        mysql_innodb_pool_bytes
    REQUIRED string:
        lan_ip   (non-empty)
    scored by DatabaseRoleHandler:
        mysql_running (hard gate), mem_total_kb, mysql_innodb_pool_bytes,
        disk_class, disk_free_gb (gate at 1.0 GB), cores, cpu_mhz

A definition that fails the probe's own schema check is a definition bug, and
validate() below applies that same check so the sim catches it at build time
rather than as a mysteriously unelectable node.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# probe schema, verbatim from frognet_capability_probe.sh
NUMERIC_REQ = ("cores", "cpu_mhz", "cpu_bench_total", "mem_total_kb",
               "mem_available_kb", "disk_write_mbps", "disk_fsync_ms",
               "disk_free_gb", "av_port")
NUMERIC_OPT = ("mysql_innodb_pool_bytes",)

DISK_CLASSES = ("nvme", "ssd", "hdd", "sdcard", "unknown")


@dataclass
class Iface:
    """One network interface. `kind` drives what the node treats it as."""
    name: str                        # eth0, wlan0, wlan1, wg0, ...
    addr: str                        # dotted quad on this iface
    prefixlen: int = 24
    kind: str = "lan"                # lan | ap | uplink | wg
    up: bool = True

    @property
    def subnet(self) -> str:
        o = self.addr.split(".")
        return f"{o[0]}.{o[1]}.{o[2]}.0/{self.prefixlen}"


@dataclass
class Device:
    """An attached device. Sensor devices publish metrics over time.

    kind: dht22 | gps | thermal | camera | mic | disk | linkstate
    Anything with `sensor_type` set gets a metric_upsert publisher.
    """
    name: str
    kind: str
    sensor_type: str = ""            # SensorType for metric_upsert (e.g. "DHT")
    params: Dict = field(default_factory=dict)


@dataclass
class Machine:
    """A whole simulated box."""
    name: str                        # sim label, e.g. "Seattle5"
    domain: str                      # FrogNet domain, e.g. "Seattle5"
    ifaces: List[Iface] = field(default_factory=list)
    devices: List[Device] = field(default_factory=list)
    roles: List[str] = field(default_factory=lambda: ["databasehost", "mediahost"])

    # hardware facts -> capability blob
    cores: int = 4
    cpu_mhz: int = 1500
    cpu_bench_total: float = 400.0
    mem_total_kb: int = 4 * 1024 * 1024
    mem_available_kb: int = 3 * 1024 * 1024
    disk_class: str = "ssd"
    disk_write_mbps: float = 120.0
    disk_fsync_ms: float = 3.0
    disk_free_gb: float = 40.0
    # media capability. [MEDIAHOST_STATIC_RANK_V1] ffmpeg+libvpx is the HARD
    # GATE in sotf_handler.score -- a candidate without both scores -1.0 and can
    # never win, which is why mediahost elected NONE while databasehost worked:
    # the blob simply never carried the fields the handler reads.
    ffmpeg: bool = True
    libvpx: bool = True
    hw_encoder: str = "none"          # none | vaapi | nvenc | v4l2m2m
    gpu_render_node: bool = False     # vaapi is only credited with a real node
    mysql: bool = False
    mysql_running: bool = False
    mysql_innodb_pool_bytes: int = 0
    av_port: int = 7011
    arch: str = "x86_64"

    # ---- derived --------------------------------------------------------
    @property
    def served_iface(self) -> Iface:
        """The node's served interface: the first one carrying a .1 on a 10/8,
        else simply the first. This is the FrogNetHost address."""
        for i in self.ifaces:
            if i.addr.startswith("10.") and i.addr.endswith(".1"):
                return i
        if not self.ifaces:
            raise ValueError(f"machine {self.name}: no interfaces defined")
        return self.ifaces[0]

    @property
    def lan_ip(self) -> str:
        return self.served_iface.addr

    @property
    def fqdn(self) -> str:
        # metric_upsert.sh: FQDN comes from getFrogNet.bash; every FrogNet node's
        # hostname is "FrogNetHost", qualified by domain.
        return f"FrogNetHost.{self.domain}"

    @property
    def subnet24(self) -> str:
        o = self.lan_ip.split(".")
        return f"{o[0]}.{o[1]}.{o[2]}.0/24"

    def capability(self) -> Dict:
        """The blob UnRESTHandler.advertise(blob=...) publishes as
        <role>/capability. Deterministic: same machine -> same blob, every time.
        [DBHOST_STATIC_RANK_V1] every scored term is a property of the hardware."""
        return {
            "lan_ip": self.lan_ip,
            "arch": self.arch,
            "cores": int(self.cores),
            "cpu_mhz": int(self.cpu_mhz),
            "cpu_bench_total": float(self.cpu_bench_total),
            "mem_total_kb": int(self.mem_total_kb),
            "mem_available_kb": int(self.mem_available_kb),
            "disk_class": self.disk_class,
            "disk_write_mbps": float(self.disk_write_mbps),
            "disk_fsync_ms": float(self.disk_fsync_ms),
            "disk_free_gb": float(self.disk_free_gb),
            "mysql": bool(self.mysql),
            "mysql_running": bool(self.mysql_running),
            "mysql_innodb_pool_bytes": int(self.mysql_innodb_pool_bytes),
            "av_port": int(self.av_port),
            "ffmpeg": bool(self.ffmpeg),
            "libvpx": bool(self.libvpx),
            "hw_encoder": self.hw_encoder,
            "gpu_render_node": bool(self.gpu_render_node),
            "no_cam": not any(d.kind == "camera" for d in self.devices),
            "no_mic": not any(d.kind == "mic" for d in self.devices),
        }

    def validate(self) -> List[str]:
        """Apply the probe's OWN schema check (_avcap_cache_valid) plus structure.
        Returns a list of problems; empty means the definition would publish."""
        bad = []
        cap = self.capability()
        for k in NUMERIC_REQ:
            v = cap.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                bad.append(f"{self.name}: {k} not a scalar number ({v!r})")
        for k in NUMERIC_OPT:
            if k in cap:
                v = cap[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    bad.append(f"{self.name}: {k} present but not scalar ({v!r})")
        if not isinstance(cap.get("lan_ip"), str) or not cap.get("lan_ip"):
            bad.append(f"{self.name}: lan_ip empty")
        if not self.lan_ip.startswith("10."):
            bad.append(f"{self.name}: lan_ip {self.lan_ip} is not on the 10/8 plane")
        if self.disk_class not in DISK_CLASSES:
            bad.append(f"{self.name}: disk_class {self.disk_class!r} unknown "
                       f"(scores as 0.9 fallback)")
        if self.mysql_running and not self.mysql:
            bad.append(f"{self.name}: mysql_running=True but mysql=False")
        names = [i.name for i in self.ifaces]
        if len(names) != len(set(names)):
            bad.append(f"{self.name}: duplicate interface names {names}")
        return bad


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def generic(name: str, subnet3: str, *, mysql: bool = False, **over) -> Machine:
    """A GENERIC node: one served eth0 at <subnet3>.1, standard sensor set,
    standard roles. Modest hardware so a purpose-built node out-scores it.

    'Generic' means: brings up and handles the standard services. It advertises
    capability for every standard role and publishes the standard sensors.
    """
    m = Machine(
        name=name,
        domain=name,
        ifaces=[Iface("eth0", f"{subnet3}.1", 24, "lan")],
        devices=[
            Device("cpu_thermal", "thermal", "System", {"base_c": 41.0}),
            Device("root_fs", "disk", "System", {}),
            Device("eth0_link", "linkstate", "LinkState", {}),
        ],
        roles=["databasehost", "mediahost"],
        cores=4, cpu_mhz=1500, cpu_bench_total=400.0,
        mem_total_kb=4 * 1024 * 1024, mem_available_kb=3 * 1024 * 1024,
        disk_class="sdcard", disk_write_mbps=40.0, disk_fsync_ms=9.0,
        disk_free_gb=20.0,
        mysql=mysql, mysql_running=mysql,
        mysql_innodb_pool_bytes=(128 << 20) if mysql else 0,
    )
    for k, v in over.items():
        setattr(m, k, v)
    return m


def db_class(name: str, subnet3: str, **over) -> Machine:
    """A purpose-built database box: lots of RAM, NVMe, tuned innodb pool."""
    m = generic(name, subnet3, mysql=True)
    m.cores = 8
    m.cpu_mhz = 3200
    m.cpu_bench_total = 2400.0
    m.mem_total_kb = 32 * 1024 * 1024
    m.mem_available_kb = 26 * 1024 * 1024
    m.disk_class = "nvme"
    m.disk_write_mbps = 1800.0
    m.disk_fsync_ms = 0.4
    m.disk_free_gb = 900.0
    m.mysql_innodb_pool_bytes = 8 << 30
    m.arch = "x86_64"
    for k, v in over.items():
        setattr(m, k, v)
    return m
