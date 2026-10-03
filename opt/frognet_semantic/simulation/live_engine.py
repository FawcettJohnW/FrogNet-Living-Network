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
live_engine.py - drive the REAL installed planner + committer from the
frognet_sim topology model.

frognet_sim.py Section 2a is a *model* of planner.py/committer.py. This module
replaces that model with the actual installed code: per node, per cycle, it

  1. reuses the harness observe step (sync_interfaces) to gather observations,
  2. translates them into real frognet_route.observation.Observation records,
  3. writes them to the real TSV and calls the real committer.commit_final()
     over an injected IPRoute backend,
  4. mirrors the committed table back into the FrogNode model so the harness's
     trace_packet / verify_reachability validate the REAL routes.

The backend is injected via `ipr_factory`:
  - FakeIPRoute (default) -> runs anywhere, no kernel. Validated in-container.
  - RealIPRoute bound to a netns -> same converge code, real `ip`/`wg`. Box tier
    (M2): pass ipr_factory=lambda name: RealIPRoute(netns=name).

So "test the installed planner/committer" and "M2 real-kernel" are the same code
path with a different factory.
"""
import os
import sys
import tempfile

# --- locate the live source tree (portable: box uses /opt + /usr/local/bin) ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)  # .../frognet_semantic
os.environ.setdefault("FROGNET_PROXY_ROOT", _PARENT)
for _b in ("/usr/local/bin", os.path.join(os.path.dirname(_PARENT), "usr/local/bin")):
    if os.path.isdir(os.path.join(_b, "frognet_monitor_py")):
        os.environ.setdefault("FROGNET_BIN_ROOT", _b)
        break
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# [PYCACHE_PURGE_V1] This box has shown stale-bytecode shadowing: a .pyc in
# __pycache__ gets served instead of updated .py after an overlay is applied, so
# OLD code runs silently (e.g. the netns ifname fix not taking effect even though
# the source was correct on disk). Purge project __pycache__ (NEVER the venv) and
# stop writing bytecode for this run, BEFORE importing any project module, so what
# executes is always the source on disk. The entry script is never cached by Python.
def _purge_pycache(root):
    import shutil
    for dp, dns, _fn in os.walk(root):
        parts = dp.split(os.sep)
        if "venv" in parts or "site-packages" in parts:
            dns[:] = []
            continue
        if "__pycache__" in dns:
            shutil.rmtree(os.path.join(dp, "__pycache__"), ignore_errors=True)
            dns.remove("__pycache__")
sys.dont_write_bytecode = True
_purge_pycache(_PARENT)

from frognet_log import get_logger
from frognet_route import committer as real_committer
from frognet_route import observation as real_obs
from frognet_route.iproute import FakeIPRoute
from frognet_route.planner import Route as RealRoute

# importing frognet_sim resolves the live parsers via the env set above
import frognet_sim as H

log = get_logger("simulation.live_engine")

_KIND_MAP = {"wg": "tunnel", "lan": "lan"}


def _dot1(cidr: str) -> str:
    return cidr.replace(".0/24", ".1")


def _to_real_obs(h, wave):
    """Harness Observation -> real frognet_route Observation.

    `source='sync_waveN'` is REQUIRED: the real planner's _obs_wave reads it to
    decide direct (wave 1: via=host_path onlink) vs transitive (wave 2+:
    via=relay .1). Without it the planner defaults to wave 0 == wave-1 shape and
    stamps the dest's own .1 as via for relayed routes, which never resolves."""
    return real_obs.Observation(
        dest=h.dest,
        host_path=h.host_path,
        dev=h.dev,
        via=h.via,
        rtt_ms=max(0, int(round(h.rtt_ms))),
        kind=_KIND_MAP.get(h.kind, "lan"),
        source=f"sync_wave{wave}",
        ch_name=h.ch_name or "",
    )


def _mirror_into_model(node, ipr, owned):
    """Mirror the real committed table back into FrogNode.routes so the harness
    trace model validates REAL routes. Connected /24s are re-seeded separately."""
    routes = []
    # connected routes (kernel-owned; trace delivers these via L2)
    for i in node.ifaces.values():
        routes.append(H.Route(i.subnet, dev=i.name, via=None, metric=100))
    for r in ipr.list_routes():
        if r.dest in owned:
            continue
        # Only /24s govern reachability. The committer also installs .2/32 admin
        # aliases (metric 5) with the SAME next hop as their /24 - they pin the
        # alias, they don't change reachability. Per verify_reachability's
        # contract, admin probes ride the /24. The /32s' correctness is asserted
        # via the committer call_log, not by trace_packet. Mirroring them here
        # would add a longest-prefix refinement that desyncs the trace model.
        if not r.dest.endswith("/24"):
            continue
        via = r.via
        if r.dev.startswith("wg"):
            # real tunnel route is dev-only (via=''); the harness trace model
            # needs a non-None via for the wg branch (value is ignored there,
            # next hop is the unique L2 peer on the dev).
            via = via or _dot1(r.dest)
        else:
            via = via or None
        routes.append(H.Route(r.dest, dev=r.dev, via=via, metric=r.metric))
    node.routes = routes


def _patch_sentinel_paths(tmpdir):
    """Point the committer's on-disk sentinel paths at a temp dir so off-box
    runs don't warn about /etc/sentinels. No behavioral change."""
    real_committer.OBSERVATIONS_PATH = os.path.join(tmpdir, "observations.tsv")
    real_committer.SNAPSHOT_PATH = os.path.join(tmpdir, "route_snapshot.tsv")
    real_obs.FAILURES_PATH = os.path.join(tmpdir, "discovery_failures.tsv")


def converge_real(topo, max_cycles=16, ipr_factory=None):
    """Run distributed merge cycles using the REAL planner+committer.

    Returns the number of cycles to convergence (or max_cycles).
    """
    prov = None  # set when WE own a real netns testbed; torn down in finally
    if ipr_factory is None:
        # Backend selection (fake-here / real-on-box). Default fake.
        mode = os.environ.get("FROGNET_SIM_BACKEND", "fake")
        if mode == "real":
            from netns_backend import NetnsProvisioner, ShellRunner, make_ipr_factory
            # [EXECUTE_INTERLOCK_V1] The real converge can't dry-run: its
            # RealIPRoute talks to per-node wrapper scripts that only get written
            # by a real runner. So "real backend" means "execute on a kernel".
            # Honor the documented two-flag interlock (SIM_STATUS Sec.9) instead of
            # silently shelling out on the backend flag alone.
            if os.environ.get("FROGNET_SIM_EXECUTE", "") not in ("1", "true", "yes"):
                raise RuntimeError(
                    "FROGNET_SIM_BACKEND=real requires FROGNET_SIM_EXECUTE=1 "
                    "(real netns converge executes `ip`/`wg` as root on a Linux box)")
            H.assign_identities(topo)
            prov = NetnsProvisioner(topo, runner=ShellRunner())
            # [NETNS_LIFECYCLE_V1] Node names repeat across the 20 builders, so
            # every topology provisions the SAME `fns_<node>` namespaces. Without
            # cleanup the 2nd topology's `ip netns add` collides and every later
            # `ip netns exec` runs inside the STALE namespace left by a prior
            # topology (old addrs/wg/routes) -> the committer reads a polluted
            # table -> meaningless, possibly false-green reachability. Pre-clean
            # (idempotent: `netns del` tolerates absent) clears anything an
            # aborted prior run left; the finally below tears down THIS run's
            # testbed so the next topology and the next invocation start clean.
            prov.teardown()
            prov.provision()
            prov.write_wrappers()
            ipr_factory = make_ipr_factory("real", prov)
        else:
            ipr_factory = lambda name: FakeIPRoute()
    H.assign_identities(topo)
    iprs = {n.name: ipr_factory(n.name) for n in topo.nodes.values()}
    owned = {n.name: {i.subnet for i in n.ifaces.values()}
             for n in topo.nodes.values()}
    name_by_one = {n.frognet_ip: n.name for n in topo.nodes.values() if n.frognet_ip}

    for node in topo.nodes.values():
        node.routes = [H.Route(i.subnet, dev=i.name, via=None, metric=100)
                       for i in node.ifaces.values()]
        node.known_hosts = {node.frognet_ip: node.name} if node.frognet_ip else {}

    tmp = tempfile.mkdtemp(prefix="live_engine_")
    _patch_sentinel_paths(tmp)
    obs_path = real_committer.OBSERVATIONS_PATH
    # [SENTINEL_ISOLATION_V1] commit_final's snapshot_path/failures_path default
    # to /etc/sentinels, bound at IMPORT time - patching the module globals does
    # not rebind those defaults. Pass them EXPLICITLY so a sim run (including on
    # the live box as root) never writes route_snapshot.tsv into, or truncates
    # discovery_failures.tsv in, the REAL /etc/sentinels the running services use.
    snap_path = os.path.join(tmp, "route_snapshot.tsv")
    fail_path = os.path.join(tmp, "discovery_failures.tsv")

    # direct-neighbor .1s per (node, dev): used to classify wave 1 vs 2+.
    direct_ones = {}
    for n in topo.nodes.values():
        for i in n.ifaces.values():
            ones = set()
            for (pn, _pi, _pip) in topo.l2_peers(n.name, i.name):
                if topo.nodes[pn].frognet_ip:
                    ones.add(topo.nodes[pn].frognet_ip)
            direct_ones[(n.name, i.name)] = ones

    up_channels = {n.name: set() for n in topo.nodes.values()}  # daemon _active_tunnels

    # [NETNS_LIFECYCLE_V1] finally guarantees teardown even if a converge cycle
    # raises, so one bad topology can't poison the next with leftover namespaces.
    result = max_cycles
    try:
        for cycle in range(1, max_cycles + 1):
            any_change = False
            for node in topo.nodes.values():
                old_routes = {str(r) for r in node.routes}
                old_hosts = dict(node.known_hosts)

                hobs = H.sync_interfaces(topo, node)
                robs = []
                for o in hobs:
                    # wave 1 iff the dest's .1 is a directly-adjacent peer on this dev
                    wave = 1 if o.host_path in direct_ones.get((node.name, o.dev), ()) else 2
                    robs.append(_to_real_obs(o, wave))
                real_obs.write_observations(obs_path, robs)

                ipr = iprs[node.name]
                ch2if = {o.ch_name: o.dev for o in robs
                         if o.kind == "tunnel" and o.ch_name}
                # active_tunnel_channels = what the daemon believes is UP (kernel
                # truth), NOT everything observed. Feeding all observed channels
                # makes the committer tear down any channel that loses to another
                # path, then it's re-observed and reinstalled next cycle - a flap
                # that never quiesces. Track the up set like the real daemon.
                active = sorted(up_channels[node.name])
                real_committer.commit_final(
                    ipr, obs_path,
                    active_tunnel_channels=active,
                    channel_to_iface=ch2if,
                    owned_subnets=sorted(owned[node.name]),
                    snapshot_path=snap_path,
                    failures_path=fail_path,
                )

                _mirror_into_model(node, ipr, owned[node.name])

                # refresh up set: a channel is up if its dev now carries a wg route
                wg_devs = {r.dev for r in ipr.list_routes() if r.dev.startswith("wg")}
                up_channels[node.name] = {ch for ch, dev in ch2if.items()
                                          if dev in wg_devs}

                # known_hosts: self + host_path of every dest we now hold a route to
                held = {r.dest for r in ipr.list_routes() if r.dest.endswith("/24")}
                new_hosts = {node.frognet_ip: node.name} if node.frognet_ip else {}
                for o in robs:
                    if o.dest in held and o.host_path in name_by_one:
                        new_hosts[o.host_path] = name_by_one[o.host_path]
                node.known_hosts = new_hosts

                if {str(r) for r in node.routes} != old_routes or new_hosts != old_hosts:
                    any_change = True
                    if os.environ.get("LE_DEBUG"):
                        rs={str(r) for r in node.routes}
                        print(f"  cyc{cycle} {node.name} CHANGED route+{sorted(rs-old_routes)} route-{sorted(old_routes-rs)} kh{'' if new_hosts==old_hosts else ' '+str(sorted(new_hosts.values()))}")
            if not any_change:
                result = cycle
                break
    finally:
        if prov is not None:
            prov.teardown()
    return result


def run_topo_real(topo, verbose=False, ipr_factory=None,
                  record_key=None, record_source="offline"):
    """Real-engine analogue of frognet_sim.run_topo: converge with the installed
    planner+committer, then validate all-pairs reachability on the REAL table.

    If record_key is set, bless the converged contract (committed routes +
    reachability) into a baseline tagged record_source. On a real backend the
    routes are kernel readback -> a HARDWARE baseline the offline gate then
    guards against, no box required for later regression checks."""
    cycles = converge_real(topo, ipr_factory=ipr_factory)
    ok, total, failures = H.verify_reachability(topo)
    label = topo.label
    if failures:
        # [RECORD_ON_SUCCESS_V1] NEVER bless a topology that didn't fully reach.
        # A provisioning failure (e.g. the v4 ifname bug, or a stale/misapplied
        # overlay) yields a truncated route table; recording it would label
        # broken state as a "hardware" baseline and poison the offline gate. If
        # this topology failed, leave its existing (good) baseline untouched.
        print(f"  [FAIL] {label}: {ok}/{total} pairs ({cycles}cyc) "
              f"- {len(failures)} failed"
              + (f"  (baseline NOT recorded - refusing to bless a failed run)"
                 if record_key else ""))
        for f in failures[:5]:
            print(f"           - {f}")
        return False
    if record_key:
        try:
            import regression_baselines as RB
            RB.write_baseline(RB.capture(record_key, topo, cycles, record_source))
        except Exception as e:
            print(f"  [WARN] baseline record failed for {record_key}: {e}")
    print(f"  [PASS] {label}: all {ok}/{total} pairs reach ({cycles}cyc) "
          f"[REAL planner+committer]")
    return True


# All harness topology builders, driven through the REAL engine.
_BUILDERS = [
    "topo_pair", "topo_chain_3", "topo_chain_5", "topo_star", "topo_ring_4",
    "topo_snowflake", "topo_lan_only_chain", "topo_pond_full_mesh",
    "topo_pond_with_workers", "topo_pond_hub_spoke", "topo_mixed",
    "topo_asym_lan2_wg_lan3", "topo_asym_chain_wg_star", "topo_asym_ring_wg_lan",
    "topo_asym_three_sites", "topo_multilan_shared_wg_transit",
    "topo_dual_wg_transit_bridge", "topo_solo_wlan0_ap",
    "topo_two_aps_ham_radio", "topo_mixed_iface_no_router",
]


def main():
    import regression_baselines as RB
    mode = os.environ.get("FROGNET_SIM_BACKEND", "fake")
    backend = "REAL kernel (netns)" if mode == "real" else "FakeIPRoute"
    print(f"=== REAL planner+committer over {backend} "
          f"[sim_build {RB.SIM_BUILD}], all harness topologies ===")
    # [FEEDBACK_LOOP_V1] When asked to record, bless each topology's contract into
    # a baseline. source=hardware iff this run drives a real kernel backend, so a
    # box run produces HARDWARE baselines the offline gate later guards against.
    record = os.environ.get("FROGNET_SIM_RECORD_BASELINES", "") in ("1", "true", "yes")
    source = "hardware" if mode == "real" else "offline"
    allok = True
    # [NETDEF_TOPOLOGIES_IN_THE_GATE_V1] A topology written as a netdef YAML
    # should be gated like any built-in. Point FROGNET_SIM_TOPO_DIR at a
    # directory of *.yaml and every file becomes a builder named topo_<file>,
    # attached to frognet_sim so the getattr below finds it unchanged.
    #
    # Opt-in by env: with the variable unset this loop is byte-for-byte what it
    # was, so an existing gate run cannot change behaviour. Import failures are
    # reported and skipped rather than taking the tier down -- a malformed YAML
    # is a definition bug, not a reason to lose the other twenty topologies.
    builders = list(_BUILDERS)
    _topo_dir = os.environ.get("FROGNET_SIM_TOPO_DIR", "").strip()
    if _topo_dir:
        try:
            import harness_topo as _HT
            added = _HT.register(_topo_dir)
            builders += added
            print(f"  [netdef] {len(added)} topology file(s) from {_topo_dir}")
        except Exception as e:
            print(f"  [netdef] SKIPPED {_topo_dir}: {type(e).__name__}: {e}")

    for bname in builders:
        builder = getattr(H, bname, None)
        if builder is None:
            print(f"  [SKIP] {bname} (not found in frognet_sim)")
            continue
        try:
            allok &= run_topo_real(builder(),
                                   record_key=(bname if record else None),
                                   record_source=source)
        except Exception as e:
            allok = False
            print(f"  [FAIL] {bname}: {type(e).__name__}: {e}")
    if record:
        try:
            import regression_baselines as RB
            RB.seed_scenarios()
        except Exception as e:
            print(f"  [WARN] scenario seed failed: {e}")
    print("\nALL REAL-ENGINE TOPOLOGIES PASS" if allok else "\nREAL-ENGINE FAILURES")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
