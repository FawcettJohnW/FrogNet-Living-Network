# T0 baseline — before any cleanup (2026-10-12)

Tree: repo e889208 + frognet-2026-10-11 overlay + Croakus 10-11 + the README fixes (git commit 42465dd here).
Run in the sandbox with /opt/frognet_semantic pointing at this tree. Logs: t0-logs/ beside this file.

| gate | result | notes |
|---|---|---|
| installer C3: `python3 -m discovery selfcheck` | **PASS** 14/14 suites | real-broker and lifecycle scenarios SKIP (no broker source; dev gate) |
| `discovery/run_discovery_oracles.py` | **FAIL** 80 pass, 6 regression | all six are imports of modules that are no longer in the tree: test_host_reset (working_memory), test_license (relative import), test_malformed_capability_guard (frognet_tuples), test_role_registry_lxmlfree, test_safeboot, test_service_election (frognet_service_hosts / frognet_role_elect, which live in core/ but are imported as top-level modules from the old Communicator bundle dir) |
| `simulation/run_all.py` (FROGNET_PROXY_ROOT, FROGNET_BIN_ROOT set) | **FAIL** 6 tiers | TIERs 1, 2, 2b, 3 (broker skipped, 0 checks), R, 4, C, D-pathway pass. Fail: collectives/DDP hooks (agent_workload not in tree), aiconnect ramp (same), SotF media planes (old Python Communicator), the discovery suite (the six above). Needs /usr/local/bin/mapInterfaces present. |
| rinab `netns-region-loop-test.sh` | **PASS** (13 PASS) | Communicator-on-a-node stage SKIP (COMMS_BUILD not given) |
| rinab `netns-ponds-daemon-test.sh` | **PASS** | one node in two ponds, real daemon, forward pond switched 6 times |
| rinab `netns-reach-test.sh` with BRIDGE_KT + FDF_TUN | **PASS** 70/70 | |
| `fdf-member-kt selftest` | **PASS** | |

The rule for every later tranche: everything green here stays green; the red ones are either removed with the code they
test (old Communicator, agent_workload) or fixed (the six import regressions point at the bundle dir that T2 retires).
