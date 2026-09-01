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
"""[LAN_IS_ATTACHED_V1] Oracle - off-LAN candidate must NOT win a LAN role.

Models Seattle2: no wg tunnels, so the wg-WAN plane is EMPTY. The pond is reached via
a relay (NY1). Attached segments are 10.102.60 and 10.28.28 ONLY. 10.160.160.1 is an
off-LAN box reached through the relay.

FAILS on the old code: LAN test = "/24 not in wan"; wan=[] -> 10.160.160.1 is LAN
                       (and would win mediahost).
PASSES on the new code: LAN test = "/24 in attached set" -> 10.160.160.1 is NOT LAN;
                       only 10.102.60.1 (attached) is.

Run with PATCHED=1 for the new module, unset for the pristine _orig.
"""
import importlib.util, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHED = os.environ.get("PATCHED") == "1"
def _find(rel):
    for base in (os.path.join(HERE, ".."), os.path.join(HERE, "..", "opt", "frognet_semantic"),
                 HERE, os.path.join(HERE, "opt", "frognet_semantic")):
        p = os.path.join(base, rel)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(rel)
SRC = (_find("core/frognet_role_elect.py") if PATCHED
       else os.path.join(HERE, "frognet_role_elect_orig.py"))

# --- stub `core.frognet_tuples as T` with capability + reach_plane rows ----------
CAPS = {
    "10.102.60.1":  dict(lan_ip="10.102.60.1",  cores=4, cpu_mhz=2000, cpu_bench_total=100,
                         mem_total_kb=8000000, mem_available_kb=4000000,
                         mysql_innodb_pool_bytes=0, disk_write_mbps=100, disk_fsync_ms=1,
                         disk_free_gb=50, ffmpeg=True, libvpx=True, av_port=9100),
    "10.160.160.1": dict(lan_ip="10.160.160.1", cores=8, cpu_mhz=3000, cpu_bench_total=200,
                         mem_total_kb=16000000, mem_available_kb=8000000,
                         mysql_innodb_pool_bytes=0, disk_write_mbps=500, disk_fsync_ms=1,
                         disk_free_gb=500, ffmpeg=True, libvpx=True, av_port=9100),
}

class _T:
    @staticmethod
    def get(ns, key, dbhost=None, fresh_s=0):
        if key == "capability":
            return [{"addr": ip, "value": {"capability": cap, "ts": 1000.0,
                                           "loadavg": {}, "temps_c": []}}
                    for ip, cap in CAPS.items()]
        if key == "reach_plane":
            return [{"value": {"wan_subnets": [], "ts": 1000.0}}]   # island: no wg winners
        return []

core_pkg = types.ModuleType("core"); core_pkg.__path__ = []
sys.modules["core"] = core_pkg
ft = types.ModuleType("core.frognet_tuples")
for _n in ("get", ):
    setattr(ft, _n, getattr(_T, _n))
ft.get = _T.get
sys.modules["core.frognet_tuples"] = ft

spec = importlib.util.spec_from_file_location("rel_under_test", SRC)
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)

class H:  # minimal role handler
    ROLE_NAME = "mediahost"

ATTACHED = {"10.102.60.0/24", "10.28.28.0/24"}

if PATCHED:
    hosts, lan = rel.gather_candidates(H(), lan_subnets=ATTACHED)
else:
    hosts, lan = rel.gather_candidates(H())          # old signature: no attached set

lan_ips = {c["lan_ip"] for c in lan}
host_ips = {c["lan_ip"] for c in hosts}
print(f"PATCHED={PATCHED} hosts={sorted(host_ips)} lan={sorted(lan_ips)}")

# databasehost set (hosts_list) is pond-wide either way - both nodes present
assert host_ips == {"10.102.60.1", "10.160.160.1"}, host_ips

offlan_in_lan = "10.160.160.1" in lan_ips
attached_in_lan = "10.102.60.1" in lan_ips

if PATCHED:
    assert not offlan_in_lan, "FAIL(new): off-LAN 10.160.160.1 leaked into LAN list"
    assert attached_in_lan,   "FAIL(new): attached 10.102.60.1 missing from LAN list"
    print("PASS(new): off-LAN excluded from LAN role; attached kept")
else:
    assert offlan_in_lan, "expected old code to (wrongly) bucket off-LAN as LAN"
    print("CONFIRM(old): off-LAN 10.160.160.1 WRONGLY in LAN list (this is the bug)")
