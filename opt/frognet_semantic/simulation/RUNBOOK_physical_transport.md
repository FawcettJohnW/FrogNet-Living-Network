# RUNBOOK — Physical transport (PRIMER 1) on-box verification

Everything below the line "proven in-container" is done and gate-green. This
runbook covers the three fill-ins that need a real Linux box (root + `tc`/`ip`,
or egress to a remote daemon) and cannot run in the container.

## What's already proven in-container (no box needed)
- `transport_sim_tier.py` — 24/24 tier checks PASS (sim, unchanged path).
- `transport_factories.py` self-test — 12/12 PASS:
  `python3 simulation/transport_factories.py`
  (sim + REAL-TCP loopback parity, 20-way pipelined seq-matching, netem argv
  correctness under DryRunner, outage-honesty).
- `sotf_video_stream_test.py --transport {sim,loopback}` — both 100% delivery,
  byte-exact (raw/codex/wire identical).
- `sotf_degradation_ladder.py --transport {sim,loopback}` — sim shows the
  contested-RF collapses; loopback (clean wire in-container) 100% with
  byte-exact per-msg wire sizes.
- `run_all.py` — full gate PASS.

## Apply over the live tree
    tar xzf frognet_FULLTREE_v25_4_physical_transport.tar.gz -C /
    # files land under /opt/frognet_semantic/simulation/
Changed/new files only: `simulation/transport_factories.py` (new),
`simulation/sotf_video_stream_test.py`, `simulation/sotf_degradation_ladder.py`.
`create_connected_pair` in `transport_sim_tier.py` is UNCHANGED — sim path is
structurally identical.

---

## Fill-in 1 — tc-netem actually executing (loopback + shaping)

Container proves the argv; the box proves the kernel applies it. Needs root and
`CAP_NET_ADMIN`. The clean topology (primer): a netns + veth pair so shaping
does not touch the rest of the box (`lo` silently no-ops most qdisc rules).

Pre-flight that the qdisc is real (primer step 3):
    # inside the daemon netns, after the shaper applies a 50ms delay:
    ip netns exec ns-daemon ping -c 5 127.0.0.1   # RTT should reflect ~100ms

Run the video stream shaped, on-box:
    FROGNET_SIM_EXECUTE=1 \
    python3 simulation/sotf_video_stream_test.py \
        --video real.ivf --transport loopback \
        --shape-iface veth-d --latency-ms 50 --jitter-ms 10 --bandwidth-bps 32000

Expected: measured per-frame RTT >= ~100ms (2x one-way) and throughput capped
near the configured rate. Compare against `--transport sim` with the same
NetworkParams — must match within thread-scheduler noise (primer success
criterion). NOTE: outages are NOT applied by netem in the default path
(`skipped_outage=True`); pass them only in the `sim` path, or use the opt-in
`NetemShaper(outage_thread=True)` (the ladder's on-box shaper already sets it).

Cleanup is in a finally, but if a run is killed mid-shape:
    tc qdisc del dev veth-d root        # NetemShaper.cleanup_all() does this

## Fill-in 2 — mode 2: real proxy over a true interface to a remote daemon

This is the production shape: proxy -> FNW1 over eth0/wg0 -> remote daemon ->
HTTP origin (echo.php) -> back. Two processes on the REMOTE box, one command on
the near box.

On the REMOTE box:
    # origin (the echo handler the daemon forwards to)
    php -S 0.0.0.0:8080 echo.php
    # daemon, bound to a routable interface, forwarding to that origin
    python3 simulation/remote_test_daemon.py \
        --listen 0.0.0.0:19009 --origin http://127.0.0.1:8080/echo.php
(omit --origin to self-echo without a php process — pure transport check.)

On the NEAR box (drives the proxy):
    python3 simulation/sotf_video_stream_test.py \
        --mode 2 --remote-addr <remote-box-ip> --remote-port 19009
Expected: "MODE-2 ECHO OK: <N> bytes round-tripped intact (status=200)" — the
probe bytes went app->proxy->wire->daemon->origin->back with body fidelity.
A dead daemon/origin fails the TCP connect, the SEQ_RESET wait, or the origin
fetch (502) with a clear error.

Port note: 19009 default keeps the test daemon OFF the live 9009
frognet-daemon-v3 (FROGNET_TEST_DAEMON_PORT). Don't point it at 9009.

To shape that true interface while testing (mode 2 + on-box netem):
    FROGNET_SIM_EXECUTE=1 python3 simulation/sotf_video_stream_test.py \
        --mode 2 --remote-addr <ip> --remote-port 19009 \
        --shape-iface wg0 --latency-ms 50
DANGER on a live box: --shape-iface wg0 applies a REAL qdisc to the production
WireGuard bearer and degrades live mesh traffic until remove()/cleanup_all().
Shape a veth in a netns, not a production bearer, unless you mean to.

## Fill-in 3 — independent shaping measurement before tier conclusions

Primer success criterion: verify shaping by an INDEPENDENT tool before trusting
any shaped-tier numbers. Do this once per interface/profile:
    ip netns exec ns-daemon ping -c 20 127.0.0.1          # latency/jitter
    iperf3 -c <daemon-ns-addr> -t 10                       # bandwidth ceiling
Confirm ping RTT ~= 2x configured one-way latency and iperf3 throughput ~=
configured rate. Only then read the SotF tier results as bearer-faithful. At LAN
scale tc-netem's tbf-style rate limiter may not match the Python shaper exactly
(primer gotcha) — the independent measurement is the arbiter, not the sim.

## WireGuard between boxes
After WG encap MTU ~1420; tc-netem default queue can drop bursts. NetemShaper
already pre-tunes `limit 1000` on every qdisc add.
