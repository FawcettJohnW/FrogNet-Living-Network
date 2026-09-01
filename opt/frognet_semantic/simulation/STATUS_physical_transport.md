# STATUS — PRIMER 1 Physical transport — HANDOFF / RECOVERY

## STANDING RULES — carry into EVERY session (John's front-of-chat block)

**User:** senior programmer. Runs FrogNet (mesh of nodes, WireGuard tunnels,
broker, Python proxy/daemon). Direct. Impatient. Calls out bullshit.

**Posture.** Answer asked. No preamble, no restatement, no summary. Brevity.
"I don't know" / "I need X" on turn one, not after speculation. Prose over
bullets on mobile.

**Don't.**
- Invent paths, APIs, fields, configs. If I don't have it, ask.
- Assume I have the codebase. Each chat starts empty.
- Rank possibilities when the honest answer is "insufficient evidence."
- Apologize for being an AI. Soften. "Great question." Say "I apologize for the confusion."
- Propose tools that don't fit the constraint (e.g. realtime-only when stalls aren't noticed live).
- Course-correct on only the specific point pushed back on — the pushback is a pattern.

**Root cause means find the cause, wherever it lives. Not dig deeper where the light is.**
1. Enumerate layers and hosts before theorizing. Distributed system = app, threads,
   systemd, tunnel daemon, kernel (netfilter/conntrack/routing/ifaces), overlay (WG),
   LAN, broker, every peer, scheduled jobs/hooks on all of the above.
2. State what I have and what's missing. One log from one host during one incident is
   almost never enough.
3. Don't narrow until cross-layer/cross-host evidence rules out the rest. Visible process
   = victim until proven otherwise. "What happened *to* it" before "what's wrong *with* it."
4. Symptom ≠ cause. Name which one I'm looking at.

**When the log is insufficient: instrument for one-shot root cause.** Propose specific
file/function/line, matching existing tag conventions (`[DIAG-WRITER]` etc.). Log
everything potentially relevant, not just the suspected cause — all in-scope variables,
all conditions checked, all state inspected, precise timestamps, thread/task IDs, inputs
and outputs. Err toward too much. A second instrumented run costs hours and may not
reproduce; a noisy log costs nothing. If I'm selecting variables based on my current
hypothesis, log all of them instead — the hypothesis is what might be wrong.

**Use external tools.** py-spy (dump/record/speedscope), strace (`-e network,poll,futex`),
tcpdump (ring buffer for post-hoc), ss, `/proc/$pid/task/*/stack`, conntrack,
`/proc/net/softnet_stat`, full unfiltered journalctl (not one unit), dmesg,
`systemctl list-timers`, cross-host correlation. Match tool to the layer cause could live
in. Check constraint fit before recommending.

**Prior chats via `conversation_search`** have real context: file paths, service names
(`frognet-tunnel-daemon-v3`), concepts (runMerge, lillypad, chorus). Search before asking
user to re-explain.

**Code is wrong / primer is right** for FrogNet behavior unless John says the primer is
stale. Never describe what code does from memory — read the source first. Sim before box.

**Every core component ships with BOTH unit and integration tests, exercised on the
simulator.** Unit = logic/lifecycle, no live infra. Integration = the property the
component exists for, demonstrated through the sim transport (e.g. measured HOL isolation,
not an asserted comment). Deliver the tests with the component, not later.

---

Self-sufficient resume doc. If context was compressed, read this top to bottom;
it records what was built, what's proven, what's pending, and the load-bearing
facts that are easy to get wrong. Source of truth is the code; this points at it.

## Task
Transition the WireMedium simulator from in-process socketpair to real TCP,
optionally with kernel traffic shaping via tc-netem. Three modes behind one
factory: `sim` (current), `loopback` (real TCP, same box), `remote` (real TCP
to a remote daemon). Add `--transport sim|loopback|remote` to the two SotF
test scripts. Primer: `PRIMER_1_physical_transport.md`.

## Files (relative to /opt/frognet_semantic)
- NEW    `simulation/transport_factories.py`   — the factory abstraction
- NEW    `simulation/remote_test_daemon.py`    — mode-2 far end (FNW1 server +
            origin forwarder); run on the remote box
- NEW    `simulation/echo.php`                 — mode-2 origin (PHP echo);
            `php -S 0.0.0.0:8080 echo.php`
- NEW    `simulation/mode2_capture_remote.sh`  — REMOTE-box capture (daemon end)
- NEW    `simulation/mode2_capture_near.sh`    — NEAR-box capture (proxy end)
- EDIT   `simulation/sotf_video_stream_test.py`— `--transport` + `--mode 0|1|2`
            + mode-2 REQ_RAW echo + shaper
- EDIT   `simulation/sotf_degradation_ladder.py`— `--transport` + shaper
- EDIT   `simulation/transport_sim_tier.py`    — ADDITIVE: `set_origin_handler`
            on the daemon session + REQ_RAW routing through it (baseline
            unchanged — nothing sets a handler; `create_connected_pair` still
            byte-identical). Imports `wrap_resp_raw`.
- DOC    `simulation/RUNBOOK_physical_transport.md` — on-box fill-in steps

## Mode vocabulary (user's 0/1/2 == --transport)
- mode 0 = `sim`      — in-process socketpair + Python shaper.
- mode 1 = `loopback` — real TCP to an in-process daemon on 127.0.0.1.
- mode 2 = `remote`   — real proxy over a TRUE interface to `remote_test_daemon.py`
            on another box, which forwards REQ_RAW to an origin (echo.php) and
            returns RESP_RAW. This is the production shape: app->proxy->wire->
            daemon->origin->back. The SotF *receiver* can't run cross-process,
            so mode 2 verifies a REQ_RAW echo round-trips with body fidelity.

## What `transport_factories.py` contains
- `SocketWireEndpoint` — real-TCP twin of `transport_sim_tier.WireEndpoint`.
  IDENTICAL surface (settimeout/sendall/send/recv/shutdown/close/setsockopt/
  fileno) so SimulatedProxyWorker / SimulatedDaemonSession do NOT change. The
  one difference: `setsockopt` actually applies (real TCP wants TCP_NODELAY;
  the sim endpoint no-ops it). Matches production: transport_semantic.py:811,
  session.py:429/456.
- `NetemShaper` — tc-netem on the tree's EXISTING Runner pattern from
  `netns_backend.py`: `DryRunner` records argv (container, no privilege),
  `ShellRunner` executes (box, root). Switch: `FROGNET_SIM_EXECUTE=1`.
  `apply()` -> `tc qdisc add dev <iface> root netem delay…ms […jitter…ms]
  rate <bytes*8>bit limit 1000`. `remove()` -> `tc qdisc del`. `cleanup_all()`.
  Bidirectional => IFB mirror plan (`modprobe ifb numifbs=2`).
- `SimTransportFactory` / `LoopbackTransportFactory` / `RemoteTransportFactory`
  (Protocol `TransportFactory.connect_proxy`), plus:
- `TestDaemonProcess` — a SimulatedDaemonSession behind a real TCP listener
  (for loopback verification). Reuses `SimulatedDaemonServer` HELLO/RETURN
  pairing. Defaults to a NON-9009 port.
- `connect_pair(local_gw, mode, …)` — ONE entry the SotF tests call; returns the
  same 4-tuple `(proxy, daemon, handle1, handle2)` as create_connected_pair for
  `sim` (delegates verbatim) and `loopback` (real TCP + in-process daemon so the
  SotF receiver still drives it). `remote` is NOT here.
- `round_trip_remote(...)` — primer remote criterion: HELLO + one REQ_REPEAT to
  a real daemon. Used by `--transport remote` on the video test.
- `_selftest()` (run the module directly) — 12 checks.

## Verification — PROVEN IN-CONTAINER (real, not dry-run)
Run env: `FROGNET_PROXY_ROOT=<tree>/opt/frognet_semantic
FROGNET_BIN_ROOT=<tree>/usr/local/bin`.
- `python3 simulation/transport_sim_tier.py`         -> 24/24 PASS (sim, no regression)
- `python3 simulation/transport_factories.py`        -> 12/12 PASS (sim+loopback parity,
   20-way pipelined seq-match over real TCP, netem argv, outage honesty)
- `python3 simulation/sotf_video_stream_test.py --transport sim`       -> 100%, byte-exact
- `… --transport loopback`                            -> 100%, byte-exact (10692/11440/11730)
- MODE 2 (proven on loopback as the true-interface stand-in; egress blocked in
  container so 127.0.0.1 substitutes for a remote box): start
  `remote_test_daemon.py --listen 127.0.0.1:19009 --origin http://127.0.0.1:8099/`
  (a stdlib echo stood in for echo.php — identical HTTP contract, no php in the
  container), then `sotf_video_stream_test.py --mode 2 --remote-addr 127.0.0.1
  --remote-port 19009` -> "MODE-2 ECHO OK: 392 bytes round-tripped intact
  (status=200)". Self-echo path (no --origin) also OK.
- `python3 simulation/sotf_degradation_ladder.py --transport sim`      -> contested collapses
- `… --transport loopback`                            -> 100% clean-wire, byte-exact per-msg
- `python3 simulation/run_all.py`                     -> FULL GATE PASS (all in-container tiers
   green; discovery 35/0/0). My changes are not imported by run_all; ran it to confirm anyway.

Why loopback RTT/results differ from sim and that's CORRECT: real `lo` has no
synthetic 1ms delay (sim's latency is artificial), and in-container loopback has
no kernel netem so CONTESTED_RF outages can't fire -> clean-wire delivery is the
expected, faithful result. The load-bearing invariant proven is byte-exact wire
sizes, not wall-clock latency.

## run_all gate (TIER T = this feature)
`python3 simulation/run_all.py` now runs the feature: faithful transport sim (24),
factory self-test (12), channel_sets UNIT + INTEGRATION, SotF mode 0 + mode 1. Each
is an isolated subprocess (the test files sys.exit). Default leaves the box-tier
mode-2/shaped/cross-box legs as printed notes.
`python3 simulation/run_all.py --transport real --remote-daemon <addr>[ --remote-daemon-port N]`
points the real mode-2 leg at a daemon you launched on another box
(`remote_test_daemon.py --listen 0.0.0.0:19009`). --remote-daemon implies real.
Without it, real mode-2 self-hosts a daemon on 127.0.0.1 over lo. (Tolerates the
--remote-deamon misspelling.) FROGNET_TEST_REMOTE_ADDR/PORT are the env equivalents.

Add `--shape-iface` to apply REAL tc-netem: run_all builds a scratch netns +
veth pair, applies `tc qdisc ... netem delay 50ms` on the root-side veth (a netns
is required — a same-host pair without one is short-circuited by the loopback fast
path and the qdisc never bites), runs mode-2 across it, and ASSERTS measured p50
rose to ~the applied delay (lo baseline is <1ms). Whitelist guard: only the scratch
veth (auto / frsh*) is shaped, NEVER wg/eth/wlan/lo. Container prints the plan and
SKIPs; box (root + FROGNET_SIM_EXECUTE=1) executes and tears down in finally.

NOTE/FIX: connect_pair(daemon_port=0) now means EPHEMERAL (was coerced to 19009 by a
`0 or DEFAULT` bug), so the in-process loopback tiers can't collide with a real test
daemon on 19009. _run_proc retries once on failure with a VISIBLE flag — a genuine
break fails twice and reports FAIL; it never silently masks a failure.

## Physical cross-box run (validated from pasted artifacts)
mode 2's only unproven-in-container piece is a real hop over a physical/WG NIC
(egress blocked here). The two capture scripts produce machine-checkable
evidence I validate post-hoc:
  - NEAR driver emits `[DIAG-MODE2] {json}`: per-iteration in_sha256/out_sha256,
    match bool, rtt_ms ladder, connect_open_ms, near kernel.
  - REMOTE daemon emits `[DIAG-DAEMON] … in_sha256=… out_sha256=…` per request.
  VALIDATION = the near in_sha256 for iteration i must equal the far in_sha256
  AND both out_sha256 — proves bytes survived proxy->wire->daemon->origin->back
  with no corruption/truncation across the real bearer. Large --echo-bytes
  (e.g. 60000) forces multi-segment TCP, exercising length-prefix reassembly
  past the WG ~1420 MTU. pcap (wire.pcap) + ping.txt (independent latency) + tc
  readback corroborate the layer below. Dry-run proven in-container on loopback:
  5x60000B all sha256-identical, both ends agree.

## PENDING — box fill-ins (cannot run in container; no net, no tc/ip, no root)
See RUNBOOK_physical_transport.md for exact commands.
1. tc-netem EXECUTING: `FROGNET_SIM_EXECUTE=1 … --shape-iface veth-d`; ping pre-
   flight; shaped loopback must match sim within scheduler noise.
2. remote round-trip to a live frognet-daemon-v3: `--transport remote
   --remote-addr <ip>`; expect "REMOTE ROUND-TRIP OK".
3. independent shaping measurement (ping/iperf3) before trusting shaped-tier
   numbers — primer success criterion.

## Load-bearing facts / corrections (don't re-derive)
- PRIMER ENV VAR IS WRONG: primer says `FROGNET_SEM_PORT`; live code reads
  `FROGNET_DAEMON_PORT` (proxy/transport_semantic.py:141) and
  `FROGNET_DAEMON_LISTEN` (daemon/daemon_main.py:42, default 0.0.0.0:9009). Used
  the real names; added `FROGNET_TEST_DAEMON_PORT` (default 19009) for the test
  daemon to dodge a same-box conntrack collision with the live daemon (gotcha).
- `create_connected_pair` UNCHANGED on purpose. All new behavior routes through
  `connect_pair`. This is what structurally protects the 24-check baseline.
- WireEndpoint surface to preserve EXACTLY (transport_sim_tier.py:403): settimeout,
  sendall, send, recv, shutdown, close, setsockopt, fileno.
- Two-socket model: send channel = HELLO; RETURN channel = HELLO RETURN:<gw>.
  Production opens both (transport_semantic.py _open_send_sock/_open_return_sock);
  daemon pairs them (session.py set_send_sock / server.py _handle_connection).
- NetworkParams.bandwidth_bps is BYTES/sec; tc `rate` wants BITS -> *8.
- Outage honesty: tc-netem can't model P/sec-of-Tms-blackout. DEFAULT (b):
  outages NOT pushed to kernel (`NetemShaper.skipped_outage=True`); keep outage
  modeling in the sim path. OPT-IN (a): `NetemShaper(outage_thread=True)` toggles
  the qdisc. The ladder's on-box shaper sets outage_thread=True.
- `remote` mode on the SotF tests does a REQ_REPEAT preflight and exits — the
  in-process receiver can't attach to a daemon in another process. Full remote
  streaming is out of scope (primer's remote criterion is just the round-trip).
- Standing rule respected: never executed/claimed network behavior the container
  can't do; tc layer is dry-run-only here (SIM_STATUS: "Loopback sockets work
  in-container; egress does not"). Loopback TCP on 127.0.0.1 DOES work here and
  was exercised for real.

## Re-package
Full tree + this STATUS:
  tar czf frognet_FULLTREE_v25_4_physical_transport_FULLSRC.tar.gz -C <treeroot> .
Apply over `/` on the box (paths are ./opt/frognet_semantic/…).
DO NOT delete the venv (SIM_STATUS §8).
