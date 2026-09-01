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
hardware.py - named hardware profiles for network definitions.

A profile fills in the capability fields the REAL election scores
(core/database_handler.py) and the probe's schema requires
(frognet_capability_probe.sh _AVCAP_NUMERIC_REQ). A config names a profile and
overrides what it cares about:

    hardware: {profile: pi5, memory_gb: 64, disk: {class: sdcard, size_gb: 1000}}

Profiles are SIM VALUES, not datasheet claims. A Pi 5 does not ship with 64 GB;
if a config asks for one it gets one, because the point is to rank candidates,
not to model Broadcom's parts list. `warnings()` flags physically-implausible
combinations so a definition can be read for what it is.
"""
from __future__ import annotations

from typing import Dict, List

# disk_class values database_handler.score() recognises. Anything else scores at
# the 0.9 "unknown" fallback.
DISK_CLASSES = ("nvme", "ssd", "hdd", "sdcard", "unknown")

# "class 3" / "class 10" style SD speed grades -> the class the scorer knows.
DISK_ALIASES = {
    "class3": "sdcard", "class10": "sdcard", "uhs-i": "sdcard", "uhs-ii": "sdcard",
    "microsd": "sdcard", "sd": "sdcard",
    "nvme-gen3": "nvme", "nvme-gen4": "nvme", "nvme-gen5": "nvme",
    "sata-ssd": "ssd", "spinning": "hdd", "hdd": "hdd",
}

# hw_encoder per profile: what the box can actually offload to.
PROFILES: Dict[str, Dict] = {
    # Raspberry Pi 5. Onboard WiFi can project an SSID (ap_capable).
    "pi5": dict(hw_encoder="v4l2m2m", arch="aarch64", cores=4, cpu_mhz=2400, cpu_bench_total=520.0,
                memory_gb=8, disk_class="sdcard", disk_write_mbps=90.0,
                disk_fsync_ms=6.0, disk_size_gb=64, ap_capable=True,
                link_mbps=1000),
    "pi4": dict(hw_encoder="v4l2m2m", arch="aarch64", cores=4, cpu_mhz=1500, cpu_bench_total=300.0,
                memory_gb=4, disk_class="sdcard", disk_write_mbps=40.0,
                disk_fsync_ms=9.0, disk_size_gb=32, ap_capable=True,
                link_mbps=1000),
    "macbook_pro": dict(hw_encoder="videotoolbox", arch="arm64", cores=12, cpu_mhz=4000,
                        cpu_bench_total=4800.0, memory_gb=36,
                        disk_class="nvme", disk_write_mbps=6000.0,
                        disk_fsync_ms=0.2, disk_size_gb=1000, ap_capable=False,
                        link_mbps=10000),
    "workstation": dict(hw_encoder="nvenc", arch="x86_64", cores=16, cpu_mhz=3600,
                        cpu_bench_total=5200.0, memory_gb=64,
                        disk_class="nvme", disk_write_mbps=3500.0,
                        disk_fsync_ms=0.3, disk_size_gb=2000, ap_capable=False,
                        link_mbps=10000),
    # A box built to BE the database host.
    "db_appliance": dict(arch="x86_64", cores=32, cpu_mhz=3800,
                         cpu_bench_total=9000.0, memory_gb=256,
                         disk_class="nvme", disk_write_mbps=7000.0,
                         disk_fsync_ms=0.15, disk_size_gb=4000,
                         ap_capable=False, link_mbps=25000),
    # An access-point-class box: many interfaces, modest compute.
    "ap_class": dict(arch="aarch64", cores=8, cpu_mhz=2200,
                     cpu_bench_total=900.0, memory_gb=16, disk_class="ssd",
                     disk_write_mbps=450.0, disk_fsync_ms=1.5,
                     disk_size_gb=512, ap_capable=True, link_mbps=2500),
    # The default: an ordinary node.
    "generic": dict(arch="aarch64", cores=4, cpu_mhz=1500,
                    cpu_bench_total=400.0, memory_gb=4, disk_class="sdcard",
                    disk_write_mbps=40.0, disk_fsync_ms=9.0, disk_size_gb=32,
                    ap_capable=True, link_mbps=1000),
}

# Plausibility ceilings, for warnings() only. Never enforced.
_PLAUSIBLE_MAX_RAM_GB = {"pi5": 16, "pi4": 8, "macbook_pro": 128,
                         "ap_class": 32, "generic": 16}


def normalize_disk_class(v: str) -> str:
    if not v:
        return "unknown"
    v = str(v).strip().lower().replace(" ", "").replace("_", "-")
    if v in DISK_CLASSES:
        return v
    return DISK_ALIASES.get(v, "unknown")


def resolve(profile: str, **over) -> Dict:
    """Return a flat hardware dict: profile defaults with overrides applied."""
    base = PROFILES.get(profile)
    if base is None:
        raise KeyError(f"unknown hardware profile {profile!r}; "
                       f"known: {sorted(PROFILES)}")
    hw = dict(base)
    hw["profile"] = profile
    disk = over.pop("disk", None)
    if isinstance(disk, dict):
        if "class" in disk:
            hw["disk_class"] = normalize_disk_class(disk["class"])
        if "size_gb" in disk:
            hw["disk_size_gb"] = float(disk["size_gb"])
        if "write_mbps" in disk:
            hw["disk_write_mbps"] = float(disk["write_mbps"])
        if "fsync_ms" in disk:
            hw["disk_fsync_ms"] = float(disk["fsync_ms"])
    if "disk_class" in over:
        over["disk_class"] = normalize_disk_class(over["disk_class"])
    hw.update(over)
    return hw


def warnings(name: str, hw: Dict) -> List[str]:
    """Physically-implausible combinations. Reported, never enforced -- the sim
    does what the config says."""
    out = []
    prof = hw.get("profile", "generic")
    cap = _PLAUSIBLE_MAX_RAM_GB.get(prof)
    if cap and float(hw.get("memory_gb", 0)) > cap:
        out.append(f"{name}: {prof} with {hw['memory_gb']} GB RAM exceeds the "
                   f"real part's {cap} GB ceiling (simulated as asked)")
    if hw.get("disk_class") == "unknown":
        out.append(f"{name}: disk class did not map to a class the scorer knows "
                   f"(nvme/ssd/hdd/sdcard); it will score at the 0.9 fallback")
    if hw.get("ap_capable") is False and hw.get("projects_ssid"):
        out.append(f"{name}: projects an SSID but the {prof} profile is not "
                   f"AP-capable")
    return out
