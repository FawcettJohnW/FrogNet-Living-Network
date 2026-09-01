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
sim/service_election_check.py - proves the service-host selection paradigm that sets
a well-known .frognet name from candidate capability on the CONTROL host:

  databasehost_control.frognet = highest .1 GATEWAY, deterministic, capability-free
                                 (off-mesh non-.1 boxes can NEVER be control).
  databasehost.frognet         = most-capable mysql candidate on control - INCLUDING
                                 off-mesh non-FrogNet boxes registered via
                                 install_databasehost (a non-.1 specialist can win data).
  mediahost.frognet            = same machinery, LAN-only, ffmpeg+libvpx gate - proving
                                 ONE generalized loop covers every role.
  dnsmasq SIGHUP               = fires iff /etc/hosts actually changed.

Positive AND negative: off-mesh wins data; off-mesh without mysql excluded; a stale
(old-ts) top candidate dropped by fresh_s; media bars WAN and non-libvpx; HUP gated on
a real change. Uses the REAL hosts.py / frognet_role_elect / handlers, fake tuples only.
"""
from __future__ import annotations
import os, sys, types, time

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_ROOT))    # .../work
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles", "communicator"))

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

_FAKE_T = None

def install_tuples(caps_by_role, reach_wan, my_ip="10.250.250.1"):
    """Fake transient that honors fresh_s (filters by value['ts']) so the staleness
    negative is real. capability rows carry per-candidate ts.

    The fake is a SINGLETON module bound once under both the flat and the canonical
    core name. Each call only swaps its row set + my_ip - so a module that imported
    `frognet_tuples` in an earlier section keeps the SAME object and sees the new rows.
    (Re-creating the module per section let an earlier section's import go stale.)"""
    global _FAKE_T
    now = int(time.time())
    rows = {("discovery", "reach_plane"): [
        {"addr": my_ip, "var": "reach_plane",
         "value": {"wan_subnets": reach_wan, "self_ip": my_ip, "ts": now}}]}
    for role, caps in caps_by_role.items():
        rr = []
        for ip, cap in caps.items():
            ts = cap.pop("_ts", now)
            rr.append({"addr": ip, "var": "capability",
                       "value": {"capability": cap, "loadavg": {"1": 0.1},
                                 "temps_c": [], "ts": ts}})
        rows[(role, "capability")] = rr
    if _FAKE_T is None:
        T = types.ModuleType("frognet_tuples")

        def _get(service, var, dbhost=None, fresh_s=0, timeout=4.0):
            out = T._rows.get((service, var), [])
            if fresh_s:
                t = int(time.time())
                out = [r for r in out if t - int(r["value"].get("ts", 0)) <= fresh_s]
            return out

        T.my_ip = lambda: T._my_ip
        T.get = _get
        T.put = lambda *a, **k: True
        T.DEFAULT_DBHOST = "databasehost_control.frognet"
        _FAKE_T = T
        sys.modules["frognet_tuples"] = T
        sys.modules["core.frognet_tuples"] = T          # canonical name the moved code imports
        import core as _core
        _core.frognet_tuples = T                          # `from core import frognet_tuples` -> fake
    _FAKE_T._rows = rows                                  # swap the section's data; same object
    _FAKE_T._my_ip = my_ip
    global _LAN_ARGS
    _LAN_ARGS = (reach_wan, my_ip)
    _install_lan_split(reach_wan, my_ip)


# Last LAN/WAN split installed, so fresh_handlers() can re-apply it. Empty until
# the first install_tuples: _install_lan_split(None, ...) means "no WAN subnets",
# which is the right reading for the cold-start section that runs before any
# tuples exist.
_LAN_ARGS = (None, None)


def _install_lan_split(reach_wan, my_ip):
    """[LAN_IS_UNTUNNELLED_V1] gather_candidates asks is_on_lan() per candidate,
    which TRACEROUTES. The fixture's LAN/WAN intent lives in the reach_plane
    tuple's wan_subnets, which used to be read by _wan_subnets(); stubbing that
    retired function left every candidate but this node off the LAN list -- the
    media election then picked self instead of the best LAN box, and the
    generalized loop emitted "#No database servers on this LAN".

    So derive is_on_lan from the SAME wan_subnets the fixture already declares:
    a candidate in a WAN /24 is not on the LAN, everything else is. No trace, no
    dependence on whether the container has a traceroute binary.

    fresh_handlers() re-applies this. Installing it only from install_tuples was
    order-dependent: fresh_handlers pops frognet_role_elect from sys.modules and
    re-imports, and whether the stub survived that came down to whether the
    interpreter handed back the same module object -- it did in one container
    and did not on a live node, so the same fixture gave two answers."""
    wan = {s.rsplit(".", 1)[0] for s in (reach_wan or ())}
    mine = my_ip or (_FAKE_T._my_ip if _FAKE_T is not None else None)

    def _is_on_lan(ip, local_ips=None, hops=None):
        if ip == mine:
            return True                      # [SELF_IS_ALWAYS_LAN_V1]
        return ip.rsplit(".", 1)[0] not in wan

    import frognet_role_elect as _RE
    _RE.is_on_lan = _is_on_lan
    try:
        import core.frognet_role_elect as _CRE
        _CRE.is_on_lan = _is_on_lan
    except Exception:
        pass


def fresh_handlers():
    for m in ("frognet_role_elect", "frognet_service_hosts",
              "core.frognet_role_elect", "core.frognet_service_hosts"):
        sys.modules.pop(m, None)
    import frognet_role_elect as RE
    _install_lan_split(*_LAN_ARGS)   # the pop above may have replaced the
                                     # module object; re-apply onto whatever
                                     # came back rather than hoping it survived
    from core.database_handler import DatabaseRoleHandler
    from core.sotf_handler import SotFMediaHandler
    return RE, DatabaseRoleHandler, SotFMediaHandler

# off-mesh non-FrogNet DB box registered via install_databasehost: 10/8, NOT a .1
OFFMESH = "10.77.77.50"

def run():
    from discovery.hosts import control_host_ip, set_control_host, CONTROL_NAME, select_database_host

    # ---- 1. CONTROL is the highest .1 gateway; off-mesh non-.1 can't be control ----
    block = ["10.120.120.1 FrogNetHost.x", "10.250.250.1 FrogNetHost.x",
             "10.160.160.1 FrogNetHost.x", f"{OFFMESH} FrogNetHost.x"]   # off-mesh has a line too
    ctl = control_host_ip(block)
    probs = []
    if ctl != "10.250.250.1":
        probs.append(f"control should be highest .1 (10.250.250.1), got {ctl}")
    if ctl == OFFMESH:
        probs.append("off-mesh non-.1 became control - must never happen")
    out = set_control_host(block)
    if f"10.250.250.1 {CONTROL_NAME}" not in out:
        probs.append(f"control line not written: {[l for l in out if CONTROL_NAME in l]}")
    check("[CONTROL] databasehost_control = highest .1 gateway, off-mesh barred", probs)

    # cold-start floor: no candidates -> select_database_host floors databasehost.frognet
    # at highest .1, i.e. == control, so a cold node still has a data host.
    #
    # The fake transient MUST be installed before this. Until section 2's
    # install_tuples, frognet_tuples is still the REAL module, so
    # _capability_index inside select_database_host reads the LIVE store: on a
    # running node 10.120.120.1 has a genuine capability row, `eligible` is
    # non-empty, the ranked branch answers, and the cold floor is never reached.
    # The check then passed or failed on whether this box's store happened to
    # reply -- green in a container with no store, red on a live node.
    #
    # An explicitly EMPTY capability set is what "no capability" means here.
    install_tuples({"databasehost": {}, "mediahost": {}}, [])
    fresh_handlers()

    probs = []
    cold = select_database_host(["10.120.120.1 FrogNetHost.x", "10.250.250.1 FrogNetHost.x"])
    if "10.250.250.1 databasehost.frognet" not in cold:
        probs.append(f"cold floor should be highest .1 == control, got "
                     f"{[l for l in cold if 'databasehost.frognet' in l]}")
    check("[COLD] no capability -> databasehost.frognet floors to control (highest .1)", probs)

    # ---- 2. DATA election ADMITS off-mesh non-.1 and lets it WIN (positive) ----
    # [DBHOST_STATIC_RANK_V1] disk_free_gb is a HARD GATE, not a multiplier --
    # "out of space is genuinely fatal and stays fatal". These candidates carried
    # no disk_free_gb at all, so score() returned -1.0 for every one of them and
    # elect() had no winner: three checks below reported "got None" and read as
    # an election failure when the fixture simply predated the gate.
    #
    # mem_total_kb likewise: the score reads INSTALLED RAM, never free, so that a
    # transient free-RAM reading cannot move a durable role. A candidate with
    # only mem_available_kb still scores, but it is scored on the wrong axis and
    # the relative ranking here is meant to be the hardware's.
    db = {
        "10.250.250.1": dict(lan_ip="10.250.250.1", mysql_running=1,
                             mem_total_kb=4*1024*1024, mem_available_kb=2*1024*1024,
                             cores=2, cpu_mhz=1500, cpu_bench_total=1500,
                             disk_class="sdcard", disk_free_gb=20,
                             disk_write_mbps=40, disk_fsync_ms=5),
        "10.120.120.1": dict(lan_ip="10.120.120.1", mysql_running=1,
                             mem_total_kb=2*1024*1024, mem_available_kb=1*1024*1024,
                             cores=2, cpu_mhz=1200, cpu_bench_total=1200,
                             disk_class="sdcard", disk_free_gb=12,
                             disk_write_mbps=30, disk_fsync_ms=8),
        OFFMESH:        dict(lan_ip=OFFMESH, mysql_running=1,
                             mem_total_kb=64*1024*1024, mem_available_kb=64*1024*1024,
                             cores=16, cpu_mhz=3600, cpu_bench_total=14000,
                             disk_class="nvme", disk_free_gb=900,
                             disk_write_mbps=900, disk_fsync_ms=1),
    }
    media = {
        "10.250.250.1": dict(lan_ip="10.250.250.1", ffmpeg=1, libvpx=1, cores=2, cpu_bench_total=1500),
        "10.160.160.1": dict(lan_ip="10.160.160.1", ffmpeg=1, libvpx=1, cores=4, cpu_bench_total=3000),
        "10.102.60.1":  dict(lan_ip="10.102.60.1",  ffmpeg=1, libvpx=1, cores=16, cpu_bench_total=14000),  # WAN
        "10.130.130.1": dict(lan_ip="10.130.130.1", ffmpeg=1, libvpx=0, cores=8, cpu_bench_total=9000),    # no libvpx
    }
    install_tuples({"databasehost": dict(db), "mediahost": dict(media)}, ["10.102.60.0/24"])
    RE, DBH, MH = fresh_handlers()

    probs = []
    hosts_list, _lan = RE.gather_candidates(DBH())
    ips = sorted(c["lan_ip"] for c in hosts_list)
    if OFFMESH not in ips:
        probs.append(f"off-mesh DB box must be ADMITTED as a candidate, got {ips}")
    win = RE.elect(DBH())
    if not (win and win["lan_ip"] == OFFMESH):
        probs.append(f"most-capable off-mesh box must win databasehost, got {win and win.get('lan_ip')}")
    check("[DATA+] off-mesh install_databasehost box admitted AND wins databasehost", probs)

    # ---- 2b. off-mesh WITHOUT mysql is EXCLUDED (negative) ----
    db_no = dict(db); db_no[OFFMESH] = dict(db_no[OFFMESH]); db_no[OFFMESH]["mysql_running"] = 0
    install_tuples({"databasehost": db_no, "mediahost": dict(media)}, ["10.102.60.0/24"])
    RE, DBH, MH = fresh_handlers()
    probs = []
    win = RE.elect(DBH())
    if win and win["lan_ip"] == OFFMESH:
        probs.append("off-mesh box with mysql_running=0 won - mysql gate FAILED")
    if not (win and win["lan_ip"] == "10.250.250.1"):
        probs.append(f"a mysql .1 should win when off-mesh ungated, got {win and win.get('lan_ip')}")
    check("[DATA-] off-mesh without mysql excluded; a mysql .1 wins instead", probs)

    # ---- 2c. [CAPABILITY_DOES_NOT_AGE_V1, supersedes BALLOT_ADMISSIBILITY_V1]
    # This case asserted the opposite until now: a ballot unrefreshed past
    # FROGNET_BALLOT_MAX_AGE_S was refused by the consumer. gather_candidates
    # supersedes that by name -- "The age gate is GONE" -- because the 8.1-day
    # fossil that won this role mesh-wide was an UNREACHABLE box, not a stale
    # description; it still had its cores. Age only correlated with liveness and
    # broke both ways: a live mediahost was refused at ts_age=2040s while a node
    # that wrote a minute before dying was admitted.
    #
    # So a stale ballot is ADMITTED and, if it is the most capable, it WINS. A
    # dead node leaves the pool when the reaper removes its rows -- a shared fact
    # every node reads identically, unlike a locally-measured one.
    db_stale = dict(db); db_stale[OFFMESH] = dict(db_stale[OFFMESH]); db_stale[OFFMESH]["_ts"] = int(time.time()) - 9999
    install_tuples({"databasehost": db_stale, "mediahost": dict(media)}, ["10.102.60.0/24"])
    RE, DBH, MH = fresh_handlers()
    probs = []
    win = RE.elect(DBH())
    if not (win and win["lan_ip"] == OFFMESH):
        probs.append(f"a 9999s-stale ballot must still be admissible and win on "
                     f"merit, got {win and win.get('lan_ip')}")
    check("[DATA] a stale ballot is ADMITTED and wins on merit "
          "[CAPABILITY_DOES_NOT_AGE_V1]", probs)

    # ---- 3. MEDIAHOST generalization: best LAN libvpx wins; WAN + non-libvpx barred ----
    install_tuples({"databasehost": dict(db), "mediahost": dict(media)}, ["10.102.60.0/24"])
    RE, DBH, MH = fresh_handlers()
    probs = []
    _h, lan_list = RE.gather_candidates(MH())
    lan_ips = sorted(c["lan_ip"] for c in lan_list)
    if "10.102.60.1" in lan_ips:
        probs.append("WAN box in media lan_list - LAN/WAN split FAILED")
    mw = RE.elect(MH())
    if not (mw and mw["lan_ip"] == "10.160.160.1"):
        probs.append(f"media should elect best LAN libvpx (10.160.160.1), got {mw and mw.get('lan_ip')}")
    if mw and mw["lan_ip"] in ("10.102.60.1", "10.130.130.1"):
        probs.append("media elected a WAN or non-libvpx host")
    check("[MEDIA] best LAN libvpx wins; WAN box + non-libvpx host barred", probs)

    # ---- 4. ONE generalized loop emits BOTH role lines (databasehost + mediahost) ----
    probs = []
    try:
        from frognet_service_hosts import service_host_lines
        lines = service_host_lines(dbhost="databasehost_control.frognet")
        names = {l.split()[1] for l in lines}
        if "databasehost.frognet" not in names or "mediahost.frognet" not in names:
            probs.append(f"generalized loop should emit both role lines, got {lines}")
        dbline = next((l for l in lines if l.endswith("databasehost.frognet")), "")
        if dbline and dbline.split()[0] != OFFMESH:
            probs.append(f"databasehost.frognet should point at off-mesh winner, got {dbline}")
    except Exception as e:
        probs.append(f"service_host_lines raised/skipped: {e}")
    check("[GENERAL] one all-role loop emits databasehost.frognet AND mediahost.frognet", probs)

    # ---- 5. dnsmasq SIGHUP fires iff /etc/hosts changed ----
    probs = []
    from discovery import live
    calls = []
    live._reload_dnsmasq = lambda logger=print: calls.append(1)
    orig = live._write_if_changed
    try:
        live._write_if_changed = lambda path, content, logger=print: ("hosts" in path)  # changed
        live._commit_hosts({"frognet_hosts": ["x"], "etc_hosts": ["y"], "route_table_mutated": False})
        if not calls:
            probs.append("HUP did NOT fire on a real /etc/hosts change")
        calls.clear()
        live._write_if_changed = lambda path, content, logger=print: False               # unchanged
        live._commit_hosts({"frognet_hosts": ["x"], "etc_hosts": ["y"], "route_table_mutated": False})
        if calls:
            probs.append("HUP fired on a no-op merge (should be gated on change)")
    finally:
        live._write_if_changed = orig
    check("[HUP] dnsmasq SIGHUP fires on hosts change, silent on no-op", probs)

def main():
    print("=== SERVICE-HOST SELECTION paradigm (control + data + media + off-mesh + HUP) ===")
    run()
    print("\n" + ("ALL SERVICE-ELECTION CHECKS PASS" if not FAILS
                  else f"SERVICE-ELECTION CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
