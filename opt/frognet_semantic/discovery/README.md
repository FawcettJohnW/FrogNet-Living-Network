# FrogNet discovery — Python port

Faithful port of the bash `runMerge` discovery chain to integrated python3, with
a simulator that proves it against a real successful-merge log (the ORACLE,
New-York-1, 2026-06-03). Same-module/different-backend injection (Real* for the
box, Fake* for the sim), mirroring `frognet_route/iproute.py`.

## Module map (python <- bash)
- kernel.py        <- RTMUT readback + `ip route` FIB semantics (Real/FakeKernel)
- routes.py        <- RTMUT, probe_install/delete, route_matches, install_if_changed,
                      _sweep_probe_routes  (SWEEP_METRIC=5, per John)
- sources.py       <- echo_probe / frognet_alive / getHosts / broker / addHost edges
- discovery.py     <- walk() (4 forms, depth-2, broker authority) + promote() + seeds
- hosts.py         <- addHost host-entry + stage_hosts + selectNewDatabaseHost
- propagate.py     <- addHostAndPropogate + propogateNotificationInternal
- fixdefault.py    <- fixDefaultRoute (Mode A; Mode B raises NotImplementedError)
- resolv.py        <- manageResolv
- snapshot.py      <- emit_routes_snapshot classifier + commit_only no-op wiring
- orchestrate.py   <- mergeHostsAndResolv stage ordering
- runmerge.py      <- runMerge controller + converge decision
- sim/topology.py  <- the 9-node NY-1 oracle scenario

## Proof coverage (run: python3 frognet_discovery/test_*.py)
ORACLE-EXACT (byte/line vs log):
- kernel: promote mutations + sweep -> post_promote (20) + final ip r (14)
- routes: 3 KEEP / 11 INSTALL decisions, sweep=6, final table
- walk: 46 descend_downstream decision lines + final table + host set
- hosts: /etc/hosts verbatim (21 lines incl dbhost) + final table
- fixdefault: 11 decision lines, default route preserved
- runmerge: converge_decision line + both artifacts
- propagate: 8 fire / 2 skip addHostAndPropogate, 7/1 peer fan-out
STRUCTURAL (no artifact in log):
- snapshot: route classifier, payload shape, commit_only no-op

## Standing facts honored
.2 proves / .1 carries; echo self-describing (never compute net from addr);
WG AllowedIPs always 10/8; code-wrong/primer-right; read source before describing.

## Done since: real_backends.py (exact contract), mapinterfaces.py (oracle-proven),
live.py (deployable wiring). 10 suites green.

## Not yet done (gap to fully-complete)
1. ON-BOX validation + differential bash-vs-python run (biggest item).
2. run_tunnel_health_check (sim injects DEAD_IFACES; the probe that produces it
   isn't ported).
3. fixDefaultRoute Mode B (mesh exit synth).
4. live.py: fixDefaultRoute wiring + descend_upstream seeds.
5. helpers: discovery_pending, frognet_discovery_cache, setup_iptables, makeHostJson.
6. multi-node frogsim scenarios + property/fuzz tier.
