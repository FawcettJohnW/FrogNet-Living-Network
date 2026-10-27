# Simulator TIER 4 on the real kernel, with bearer delay and real packets (2026-10-23, netns sandbox)

All simulated (network namespaces on one kernel, wireguard-go), not hardware. Code: `opt/frognet_semantic/simulation/`
`tier4_transport.py`, `tier4_egress.py`, `wg_bearer_relay.py`; hooks `[WG_BEARER_HOOK_V1]` (netns_backend) and
`[CONVERGE_CONTINUES_V1]` (live_engine, opt-in, default unchanged).

| run | what | result |
|---|---|---|
| `tier4-as-documented.txt` | TIER 4 as SIM_STATUS documents it: `FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1`, live_engine over the 20 topologies | 20/20 PASS (model trace over kernel readback; no packets, model RTTs) |
| `run1/` | 20 topologies; tunnels point-to-point with per-site WAN delays; measured RTTs into the real planner; every pair pinged by name | all pairs answered in 20/20; by name in 17/20 (see below); lowest-delay route in all but 4 topologies |
| `run2-hub/` | the 10 tunnel topologies; every tunnel relayed by the hub (as hardware: "ALL endpoints = broker"), legs Seattle 10 / NY 35 / Austin 20 / 5 / 15 ms | every pair answered, every pair on its lowest-delay route, measured within 0.6-3.4 ms of the floor |
| `egress-warm/` | second egress point for a LAN (derived from topo_asym_lan2_wg_lan3), re-converge from learned state | below |
| `egress-cold/` | same, each re-converge a cold restart of discovery (live_engine default) | superseded: cold restart drops held routes, not live behaviour |

**By-name failures** (run1: solo_wlan0_ap, two_aps_ham_radio, mixed_iface_no_router; run2: pond_with_workers): every
packet answered; the names that fail are hosts on the same /24 as the asker (DHCP clients, workers). The names come
from the harness's known_hosts model, which only names a host once it holds a route to its /24. Harness limit, not a
FrogNet result; the real hosts writer is not in this tier.

**Not lowest-delay (run1, point-to-point only):** asym_three_sites 6/56, multilan 15/110, dual_wg 6/90. All the same
cause: the planner keeps an installed route unless another is >=100 ms AND >=20% faster (planner.py
SWITCH_MIN_ABS_MS/REL). A route found first (direct tunnel, cycle 1) is kept against a detour 10-20 ms faster. With
hub-relayed tunnels (run2) a detour can never beat the direct tunnel, and every pair was lowest-delay.

## Second egress point (egress-warm)

Site A: A1, A2 on one LAN; A2's tunnel to B1 (leg 10 ms), plus A1's own uplink and tunnel to B1. Site B leg 35 ms.

| A1's own leg | converged | A1's exit cut | restored |
|---|---|---|---|
| 5 ms (better by 10 ms RTT) | own exit both ways, 80.8 ms (lowest 80) | via A2, 90.9 | **stays via A2**, 91.0 (lowest 80) |
| 30 ms (worse by 40 ms RTT) | **out own exit, back via A2**, 110.7 (lowest 90) | via A2, 90.8 | via A2, 90.7 |
| 120 ms (worse by 220 ms RTT) | via A2, 91.0 | own exit, 311.0 | back to A2, 90.8 |

Every phase: 20/20 pairs answered by name; failover 2-3 merge cycles. The switch threshold decides the rest: a
better exit that is less than 100 ms better is not taken once another route is installed, and a worse exit installed
first (A1's own tunnel is direct, so it is found in cycle 1) is kept.
