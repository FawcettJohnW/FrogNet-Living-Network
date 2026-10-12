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
  one generalized loop         = emits the one role's line (databasehost) and no other.
  dnsmasq SIGHUP               = fires iff /etc/hosts actually changed.

Positive AND negative: off-mesh wins data; off-mesh without mysql excluded; a stale
(old-ts) top candidate still admitted; HUP gated on a real change. Uses the REAL
hosts.py / core.frognet_role_elect / handlers, fake tuples only.
"""
from __future__ import annotations
import os, sys, types, time

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
sys.path.insert(0, _ROOT)          # [CORE_IMPORTS_V1] core.*, as shipped; the old Communicator bundle dir is retired

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

_FAKE_T = None

def install_tuples(caps_by_role, my_ip="10.250.250.1"):
    """Fake transient that honors fresh_s (filters by value['ts']) so the staleness
    negative is real. capability rows carry per-candidate ts.

    The fake is a SINGLETON module bound once under the canonical core name. Each call
    only swaps its row set + my_ip - so a module that imported `core.frognet_tuples` in
    an earlier section keeps the SAME object and sees the new rows. (Re-creating the
    module per section let an earlier section's import go stale.)"""
    global _FAKE_T
    now = int(time.time())
    rows = {}
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
        sys.modules["core.frognet_tuples"] = T          # the name the shipped code imports
        import core as _core
        _core.frognet_tuples = T                          # `from core import frognet_tuples` -> fake
    _FAKE_T._rows = rows                                  # swap the section's data; same object
    _FAKE_T._my_ip = my_ip


def fresh_handlers():
    for m in ("core.frognet_role_elect", "core.frognet_service_hosts"):
        sys.modules.pop(m, None)
    import core.frognet_role_elect as RE
    from core.database_handler import DatabaseRoleHandler
    return RE, DatabaseRoleHandler

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
    install_tuples({"databasehost": {}})
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
    install_tuples({"databasehost": dict(db)})
    RE, DBH = fresh_handlers()

    probs = []
    hosts_list = RE.gather_candidates(DBH())
    ips = sorted(c["lan_ip"] for c in hosts_list)
    if OFFMESH not in ips:
        probs.append(f"off-mesh DB box must be ADMITTED as a candidate, got {ips}")
    win = RE.elect(DBH())
    if not (win and win["lan_ip"] == OFFMESH):
        probs.append(f"most-capable off-mesh box must win databasehost, got {win and win.get('lan_ip')}")
    check("[DATA+] off-mesh install_databasehost box admitted AND wins databasehost", probs)

    # ---- 2b. off-mesh WITHOUT mysql is EXCLUDED (negative) ----
    db_no = dict(db); db_no[OFFMESH] = dict(db_no[OFFMESH]); db_no[OFFMESH]["mysql_running"] = 0
    install_tuples({"databasehost": db_no})
    RE, DBH = fresh_handlers()
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
    # broke both ways: a live node was refused at ts_age=2040s while a node
    # that wrote a minute before dying was admitted.
    #
    # So a stale ballot is ADMITTED and, if it is the most capable, it WINS. A
    # dead node leaves the pool when the reaper removes its rows -- a shared fact
    # every node reads identically, unlike a locally-measured one.
    db_stale = dict(db); db_stale[OFFMESH] = dict(db_stale[OFFMESH]); db_stale[OFFMESH]["_ts"] = int(time.time()) - 9999
    install_tuples({"databasehost": db_stale})
    RE, DBH = fresh_handlers()
    probs = []
    win = RE.elect(DBH())
    if not (win and win["lan_ip"] == OFFMESH):
        probs.append(f"a 9999s-stale ballot must still be admissible and win on "
                     f"merit, got {win and win.get('lan_ip')}")
    check("[DATA] a stale ballot is ADMITTED and wins on merit "
          "[CAPABILITY_DOES_NOT_AGE_V1]", probs)

    # ---- 3. ONE generalized loop emits the one role's line (databasehost), nothing else ----
    install_tuples({"databasehost": dict(db)})
    fresh_handlers()
    probs = []
    try:
        from core.frognet_service_hosts import service_host_lines
        lines = service_host_lines(dbhost="databasehost_control.frognet")
        names = {l.split()[1] for l in lines}
        if names != {"databasehost.frognet"}:
            probs.append(f"generalized loop should emit exactly databasehost.frognet, got {lines}")
        dbline = next((l for l in lines if l.endswith("databasehost.frognet")), "")
        if dbline and dbline.split()[0] != OFFMESH:
            probs.append(f"databasehost.frognet should point at off-mesh winner, got {dbline}")
    except Exception as e:
        probs.append(f"service_host_lines raised/skipped: {e}")
    check("[GENERAL] the all-role loop emits databasehost.frognet and no other role line", probs)

    # ---- 4. dnsmasq RESTART fires iff dnsmasq's inputs CHANGED CONTENT ----
    # [DNSMASQ_RESTART_ON_CONTENT_V1 - John 2026-09-14] The rule is the CONTENTS
    # of /etc/hosts and the forwarders file, and the action is a restart: SIGHUP
    # does not re-read the conf-dir (verified, dnsmasq 2.91), and a byte compare
    # fires when a merge re-derives the same facts in a new order.
    #
    # [A_RECORD_OF_AN_ACTION_IS_NOT_THE_ACTION_V1 - John 2026-09-14] And the
    # sentinel records what dnsmasq was MADE TO SERVE, not what the merge
    # intended. The old order wrote the key first and discarded the restart's
    # result, so one failed restart latched the node into DNSMASQ_KEEP forever.
    probs = []
    import os as _os, tempfile as _tf
    from discovery import live
    calls = []
    _real_restart = live._restart_dnsmasq
    _real_inputs = live._DNSMASQ_INPUTS
    _sent_env = _os.environ.get("FROGNET_SENTINEL_DIR")
    _tmp = _tf.mkdtemp(prefix="dnsq_")
    _hosts = _os.path.join(_tmp, "hosts")
    _fwd = _os.path.join(_tmp, "frognet_forwarders_auto.conf")
    _ok = [True]
    try:
        def _fake_restart(logger=print):
            calls.append(1)
            return _ok[0]
        live._restart_dnsmasq = _fake_restart
        live._DNSMASQ_INPUTS = (_hosts, _fwd)
        _os.environ["FROGNET_SENTINEL_DIR"] = _os.path.join(_tmp, "sent")
        _os.makedirs(_os.environ["FROGNET_SENTINEL_DIR"], exist_ok=True)

        open(_hosts, "w").write("10.1.1.1 a.frognet\n10.2.2.2 b.frognet\n")
        open(_fwd, "w").write("server=/A/10.1.1.1\nbind-interfaces\n")
        # No sentinel: nothing is known about what dnsmasq holds, so act.
        live._apply_dnsmasq(lambda *a: None)
        if not calls:
            probs.append("no sentinel did NOT restart -- recording a key for a "
                         "dnsmasq nobody has spoken to assumes it is already "
                         "serving the file")
        calls.clear()

        # same facts, re-derived in a different order and respaced
        open(_hosts, "w").write("# regenerated\n10.2.2.2   b.frognet\n10.1.1.1\ta.frognet\n")
        open(_fwd, "w").write("# regenerated\nbind-interfaces\nserver=/A/10.1.1.1\n")
        live._apply_dnsmasq(lambda *a: None)
        if calls:
            probs.append("restart fired on a merge that only reordered the same facts")
        calls.clear()

        # a real change to /etc/hosts (a service float)
        open(_hosts, "w").write("10.9.9.9 a.frognet\n10.2.2.2 b.frognet\n")
        live._apply_dnsmasq(lambda *a: None)
        if not calls:
            probs.append("restart did NOT fire on a real /etc/hosts content change")
        calls.clear()

        # a real change to the forwarders file alone - SIGHUP could never do this
        open(_fwd, "w").write("server=/A/10.1.1.1\nserver=/B/10.2.2.2\nbind-interfaces\n")
        live._apply_dnsmasq(lambda *a: None)
        if not calls:
            probs.append("restart did NOT fire on a forwarders-only content change")
        calls.clear()

        live._apply_dnsmasq(lambda *a: None)
        if calls:
            probs.append("restart fired on a no-op pass (nothing touched)")
        calls.clear()

        # [A_RECORD_OF_AN_ACTION_IS_NOT_THE_ACTION_V1] The latch. A float whose
        # restart FAILS must leave the node dirty, and the next merge -- which
        # computes the identical key, because nothing about the files changed --
        # must try again rather than report the content as already handled.
        _ok[0] = False
        open(_hosts, "w").write("10.7.7.7 a.frognet\n10.2.2.2 b.frognet\n")
        live._apply_dnsmasq(lambda *a: None)
        if len(calls) != 1:
            probs.append(f"a failing restart was attempted {len(calls)} times, expected 1")
        calls.clear()
        _ok[0] = True
        live._apply_dnsmasq(lambda *a: None)
        if not calls:
            probs.append("fail-on-old: a FAILED restart recorded the key anyway, so "
                         "the next merge saw contents_unchanged and never retried -- "
                         "the node serves stale .frognet records until something "
                         "else edits /etc/hosts")
        calls.clear()

        # and once it has succeeded, the content IS recorded: no restart loop.
        live._apply_dnsmasq(lambda *a: None)
        if calls:
            probs.append("restarted again after a SUCCESSFUL restart of the same "
                         "content -- the key is not being recorded at all")
    finally:
        live._restart_dnsmasq = _real_restart
        live._DNSMASQ_INPUTS = _real_inputs
        if _sent_env is None:
            _os.environ.pop("FROGNET_SENTINEL_DIR", None)
        else:
            _os.environ["FROGNET_SENTINEL_DIR"] = _sent_env
    check("[DNSMASQ] restart fires on real content change (hosts OR forwarders), "
          "silent on reorder and no-op, and a FAILED restart is retried not recorded",
          probs)

def main():
    print("=== SERVICE-HOST SELECTION paradigm (control + data + off-mesh + HUP) ===")
    run()
    print("\n" + ("ALL SERVICE-ELECTION CHECKS PASS" if not FAILS
                  else f"SERVICE-ELECTION CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
