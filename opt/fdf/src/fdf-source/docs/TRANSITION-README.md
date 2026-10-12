# FD&F / FrogNet RAM-driven discovery — transition package (build fdf-2026-10-10-r19, 2026-10-10)

Everything a new chat needs, in one download: `fdf-source-r19.tgz`. Two transition briefs sit beside it:

- `TRANSITION-ANDROID.md` — for the chat that explores implementing the member protocol on Android: what a member
  *is* on the wire, what it must do, what it may skip, and where the reference implementation lives in the tarball.
- `TRANSITION-OPTIMIZATION.md` — for the chat that continues optimization and completeness: current state, every
  measurement, every open item, the cleanup list, how to build, how to test, the rules John has set.

Both should also read `HANDOFF-RAM-DISCOVERY-2026-10-09.md` (the day's build-by-build record, r5–r16) and
`PLAN-RAM-DISCOVERY.md` (the design the builds implement). The older docs (`PLAN-FROGNET-ON-FDF.md`,
`HANDOFF-REGION-2026-10-09.md`, `HANDOFF-FDF-2026-10-08.md`, `DEPLOY-REGION-2026-10-09.md`) are history, kept for
the record.

## What the system is, in one paragraph

Three programs make a mesh of FrogNet nodes find each other and route without a crawl, a broker, or a merge storm.
**rina-host** (C++) is the RAM manager: one process on the droplet (165.232.153.144:51888) holding the *region* —
the DIF's shared memory: every member's announcement and facts, the signed EID→RLOC mappings, liveness as the
member's held session. **fdf-client** (C++, static, one per machine) holds that session, announces the machine's
`facts.json` and re-announces it on every change (inotify), registers the machine's /24s as mappings signed with the
site key, and asserts them every 30 s. **fdf-facts** (python, one per node) writes the machine's addresses, leases held
and given (with MACs), uplink and tunnel edge into `facts.json`, waking on `ip monitor` and inotify. From those facts
every member builds the same graph (`fdf_graph.py`): adjacency = a held lease and a given lease agreeing on (ip, mac);
one upward link per host toward its ranked exit; a host with a hub tunnel carries its own /24. The FrogNet tunnel
daemon's region loop (`poll.py` + `graph_apply.py`) applies its own slice in-process on every region event: routes,
default, hosts, names, mappings, hub channel, and the databasehost/mediahost elections — from the facts, never from a
store. **fdf-hub** (python, on the droplet) applies the hub's slice: one WireGuard interface, one peer per member,
allowed-ips and routes from the plan and the verified mappings; no memory (leave = delete, return = new peer). The
old path — the walk, runMerge's propagation chain, the merge-time election, the broker — is bypassed on every region
event; runMerge remains for boot and interface events on the LAN.

## Files in the package

| file | what |
|---|---|
| `fdf-source-r19.tgz` | all source (layout below), the FrogNet-side diff, every doc, the tests and simulators |
| `fdf-install-aarch64.run`, `fdf-install-x86_64.run`, `fdf-droplet.tgz` | the three r19 deliverables (one per machine type) |
| `HANDOFF-RAM-DISCOVERY-2026-10-09.md` | the build record r5–r16, log vocabulary, rules, not-done |
| `PLAN-RAM-DISCOVERY.md` | the design (§1–§7) the builds implement |
| `frognet-ram-discovery.diff` | the FrogNet-side change against John's tree (27 files, +2,488/−24) |

### Tarball layout

```
fdf-source-r16/
  rinab/                      FD&F proper (John's rinab tree as it stands)
    src/                      rina_region.cpp (the region: ops, mappings, sessions), fdf_client.cpp, fdf_events.cpp,
                              fdf_participants.cpp, fdf_version.hpp, ctl_host.cpp, librina_region.cpp, rina_lisp.cpp, ...
    tools/                    build.sh, build_static_host.sh, build_arm64.sh, build_windows_client.sh,
                              fdf-hub, apply_once.py, apply_*.py (platform patches applied at build),
                              netns-graph-test.sh, netns-latency-test.sh, make_host.sh, ...
    tests/                    hub_oracle.py, mapown_oracle.sh, ...
    platform/                 ribbit_cpp/ (frogram.cpp = the FNWP client, fnwp_*.hpp, ram_memory.hpp, lisp_*.hpp),
                              ramsrv/ (ram_server.cpp = the RAM manager's server; patched by tools/apply_*.py at build)
    templates/ www/ kart/ lisp-mapping/ *.md
  fdf/                        /opt/fdf as installed on a node or the droplet (scripts and python; binaries are in the
                              .run/.tgz deliverables, built from rinab/)
    bin/                      fdf-facts, fdf-hub, fdf-join, fdf-region-join, fdf-leave, fdf-ls, fdf-member.sh, ...
    lib/python/               fdf_graph.py (THE graph), fdf_graph_oracle.py, fdfram.py
    hub/ www/ templates/ README.md fdf.env
  frognet/                    the FrogNet-side files this work changed (full copies) + the diff
    opt/frognet_semantic/internet_tunnels_v3/   poll.py (region loop), graph_apply.py (applier), region.py (region
                              client + facts), gen.py (generation counter), gen_oracle.py, tests/graph_apply_oracle.py,
                              test_region_oracle.py, config.py
    opt/frognet_semantic/discovery/             elect.py (in-process election), live.py, hosts.py, descend.py,
                              orchestrate.py, real_backends.py, dbhost_switch_sim.py (the switchover simulator)
    opt/frognet_semantic/core/                  region_capability.py (election input from facts), frognet_role_elect.py,
                              frognet_service_hosts.py
    usr/local/bin/            propogateNotificationInternal, runMerge.bash, frognet-merge-watcher.sh,
                              frognet_dbhost_elector.py
    etc/NetworkManager/dispatcher.d/90-frognet-merge
    frognet-ram-discovery.diff
  docs/                       every document listed above, this README, the two briefs
  deliverables/               run/header.sh (the installer header), BUILD files
```

## Where things run

| machine | what runs | unit |
|---|---|---|
| droplet 165.232.153.144 | rina-host (RAM manager) on :51888 | `fdf-ram-frognet` |
| | fdf-client (the hub's own membership) | `fdf-client-frognet` |
| | fdf-hub: WireGuard `wg-frognet` on UDP 50889, one peer per member | `fdf-hub` |
| each node (Pi aarch64 or x86) | fdf-facts --watch | `fdf-facts` |
| | fdf-client (holds the session; announces facts; registers mappings) | `fdf-client` |
| | frognet-tunnel-daemon-v3 (poll.py region loop + applier + elections) | `frognet-tunnel-daemon-v3` |
| | fdf-events (spawned by the daemon: the region's p/m events, held long-poll) | child of the daemon |

Site key `<the frognet site key: on the droplet, not in the tree>`, area `frognet`. Install on a node:
`sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>`; droplet: `cd / && tar xzf - < /tmp/fdf-droplet.tgz &&
systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub`.

## John's standing rules for this work (both chats)

1. One tarball per machine type, every build ships all three, nothing "optional"; a new build is a new download
   with a new tag, and the tag is on every log line (`build=fdf-YYYY-MM-DD-rN`).
2. Never explain a failure as "old version"; never make him run a command to learn what is deployed — the software
   says it in its logs.
3. No fallbacks. A failed step is logged and retried on the next event, never replaced by a second mechanism.
   Gone is gone: when the region is gone, its routes, names and tunnels are withdrawn and nothing is recomputed.
4. The hub has no memory. Addresses and connections are dynamic.
5. Root cause means find the cause wherever it lives; look at the logs and the prior sessions before theorizing;
   state only what the rows show.
6. No pollution of /usr/local/bin; node commands as `cd / && tar xzf - < /tmp/<file>`; no output redirection.
7. Lead with the answer; brevity; measured numbers side by side when asked about performance.
