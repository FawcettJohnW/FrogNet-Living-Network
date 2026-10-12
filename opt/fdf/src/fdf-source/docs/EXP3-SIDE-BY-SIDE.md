# Experiment 3: FrogNet's 20 discovery topologies, FrogNet vs FD&F Mode B

FrogNet: its own simulation/frognet_sim.py, unchanged (FrogNet commit in experiments/FROGNET-COMMIT); `ALL TESTS PASS`.
Mode B: tools/topo-rig.py builds each topology as real machines (namespaces) on real shared Ethernet (bridges), one
shim-eth DIF per segment and one normal DIF over all; every ordered pair pings by name.

| # | topology | nodes | segments | FrogNet: converged (cycles) | Mode B: pairs reached | Mode B: routes per node | Mode B: build s |
|---|---|---|---|---|---|---|---|
| 0 | two-node LAN (each serves own /24, peering LAN between) | 2 | 1 | 2 | 2/2 | 1 (n-1 = 1) | 0.34 |
| 1 | 3-node chain via dedicated link /24s | 3 | 2 | 3 | 6/6 | 2 (n-1 = 2) | 0.69 |
| 2 | 5-node chain | 5 | 4 | 5 | 20/20 | 4 (n-1 = 4) | 1.42 |
| 3 | 4-spoke star | 5 | 4 | 2 | 20/20 | 4 (n-1 = 4) | 1.13 |
| 4 | 4-node ring | 4 | 4 | 3 | 12/12 | 3 (n-1 = 3) | 1.17 |
| 5 | snowflake (depth 2) | 7 | 6 | 3 | 42/42 | 6 (n-1 = 6) | 2.0 |
| 6 | LAN-only triangle (3 hosts on one /24) | 3 | 1 | 2 | 6/6 | 2 (n-1 = 2) | 0.56 |
| 7 | 3-site pond (full WG mesh) | 3 | 3 | 2 | 6/6 | 2 (n-1 = 2) | 0.91 |
| 8 | 3-site pond with workers | 6 | 6 | 2 | 30/30 | 5 (n-1 = 5) | 2.12 |
| 9 | 3-site pond hub-spoke (no NY<->Austin link) | 3 | 2 | 2 | 6/6 | 2 (n-1 = 2) | 0.65 |
| 10 | mixed LAN+WG | 3 | 2 | 3 | 6/6 | 2 (n-1 = 2) | 0.71 |
| 11 | asymmetric: A=2-host LAN <-> WG <-> B=3-host LAN | 5 | 3 | 4 | 20/20 | 4 (n-1 = 4) | 1.16 |
| 12 | asymmetric: A=3-chain <-> WG <-> B=3-spoke star | 7 | 6 | 5 | 42/42 | 6 (n-1 = 6) | 2.02 |
| 13 | asymmetric: A=4-node WG ring <-> WG <-> B=3-host LAN cluster | 7 | 6 | 4 | 42/42 | 6 (n-1 = 6) | 2.09 |
| 14 | asymmetric: 3 sites, each different shape, full WG mesh between gateways | 8 | 8 | 4 | 56/56 | 7 (n-1 = 7) | 2.52 |
| 15 | multi-LAN, /30 full mesh between 4 gateways (asymmetric shapes) | 11 | 12 | 4 | 110/110 | 10 (n-1 = 10) | 4.16 |
| 16 | two /30 admin mesh groups, bridged by BR_gw (asymmetric LANs) | 10 | 11 | 4 | 90/90 | 9 (n-1 = 9) | 3.54 |
| 17 | isolated wlan0 AP: served on wlan0, 2 DHCP clients, no internet | 3 | 1 | 2 | 6/6 | 2 (n-1 = 2) | 0.56 |
| 18 | two wlan0-AP FrogNets, peered by ham0 radio (no internet) | 4 | 3 | 2 | 12/12 | 3 (n-1 = 3) | 0.91 |
| 19 | mixed iface types (eth0/wlan0/wlan1/ham0), no internet, no broker | 5 | 4 | 3 | 20/20 | 4 (n-1 = 4) | 1.4 |

**Total: 554 of 554 ordered pairs reached under Mode B; FrogNet's own discovery passes all 20.**
