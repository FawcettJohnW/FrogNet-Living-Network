# FROGNET_NOW — state as of 2026-07-20 (loop + discovery hardening session)
Discovery gate: PASS (72/72, 0 regression). Back-vouch loops: 0 across 6 sim
topologies (chain depth 2/3/4, tunnel-remote, multihomed). Full-mesh reachability
preserved. All fixes proven fail-on-old/pass-on-new; tags grep-able.

PROVEN — code (runs on-box):
- [PROXY_IDENTITY_TOKEN_V1] proxy_metrics._get_frognet_local_ip matched
  "FrogNetHost" as a SUBSTRING, so it hit FrogNetHost.New-York-1 (first host line)
  and stamped every telemetry sensor with NY1's 10.102.60.1 instead of the node's
  own .1. Now matches the bare "FrogNetHost" self-alias token (parts[1:]). Also
  hardened daemon_metrics identity to prefer the served .1 over a guest lease that
  may enumerate first. Network then derives x.y.z.0/24 correctly from the right .1.
- [SENSOR_META_BACKFILL_V1] web/api.php sensors `create`: on conflict, besides
  resolving SensorID, backfill SensorAddress/SensorNetwork/SensorLocation/SensorType
  the caller sent (IF(VALUES(col)='',col,VALUES(col)) — fill blanks, no wipe). The
  prior [IDEMPOTENT_CREATE_V1] only set SensorID on conflict, so daemon/proxy
  telemetry sensors (SemanticDaemon.*, SemanticCache.Peer.*) sat with EMPTY
  address/network once their row existed. Also: added SensorLocation to the sensors
  field whitelist (was stripped on create), and fixed daemon_metrics/proxy_metrics
  SensorNetwork from x.y.z.1 to x.y.z.0/24. NOT PHP-lintable in this env — verify on
  a box. SensorLocation lands only for writers that send it (daemon/proxy don't yet).
- [UPSTREAM_LEASE_DOT1_V1] discovery.py descend_downstream: the .1 of a subnet this
  node holds a NON-.1 lease on is added as an UPSTREAM immediate, deterministically
  from the lease — not from ARP. Root of a field split-brain: Seattle3 (client
  10.250.250.191 on Seattle5's LAN) transited 10.250.250.1 but never listed Seattle5,
  because the uplink was derived from arp_neigh alone and drops when ARP ages (the
  route via it survives under incumbency-hold; the HOST vanishes). With the gateway
  gone, databasehost_control (highest .1) collapsed to BAMacBook and three nodes
  elected three different databasehosts -> sensors written to one DB, read from
  another -> "no sensor data." Oracle: test_upstream_lease_dot1_oracle.
- [LOOP_MEMO_V1] discovery.py + descend.py: the reflect counter's LOOP verdict
  memoizes (dest,via) for the run; promote refuses a memoized (dest,via) so the
  poisoned vouch cannot re-form the loop; cleared next run. Kills the back-vouch
  (a node routing a remote via a downstream child) without touching reachability
  — a looping avenue is just rejected in favor of one that reaches.
- fail-cache key: descend.py fkey = (x_ip, via or dev). Tunnels share via="", so
  the old (x_ip, via) collapsed all tunnels into one; the first tunnel's failure
  poisoned the rest and a node reachable only over a non-first tunnel was never
  probed. Oracle: test_multitunnel_failcache_oracle.
- [TUNNEL_PEER_HEALTH_EMIT_V1] descend.py: a tunnel peer whose .2 discovery plane
  is silent is emitted from the srcless .1 health signal (healthcheck's proven
  probe), so reap cannot delete a health-proven peer. Oracle:
  test_tunnel_peer_health_emit_oracle.
- prune_dest_extras proto-kernel guard: routes.py never prunes a "proto kernel"
  connected route (NM stamps them metric 100/600, so the old metric-None
  heuristic missed them and deleted a client node off its own LAN). Oracle:
  test_prune_kernel_route_oracle.
- [NEIGHBOR_CLIENT_DOT1_V1] neighbors.py: a node that is a client on a subnet
  (holds a non-.1 addr) derives that subnet's .1 as a direct gossip neighbor, so
  propagation reaches the upstream even with no /24 via-route. Oracle:
  test_neighbor_client_dot1_oracle.

PROVEN — sim fidelity (so the sim can TEST the above; no on-box effect):
- route_egress (sim/fabric.py): a bare host address (no /CIDR) is treated as /32.
  The kernel stores /32 probe routes bare, so route_egress skipped them and the
  loop counter could never resolve its first hop — the sim silently modeled every
  loop as fine. This single line is why the sim now catches loops at all.
- converge_by_notification (sim/system.py): the sim now also settles the PRODUCTION
  way — a changed node fires a notification to its derived neighbors (neighbors.py),
  who merge and propagate onward until a run completes with no changes. The loop fix
  holds under it: all 6 topologies QUIESCE with 0 loops (brute-force converge() alone
  hid whether propagation actually reaches every affected node). loop_scenarios now
  audits under BOTH models.
- _loop_counter + SimReflect (sim/system.py): the o+c counter wired as the sim's
  reflect backend so REFLECT_VOUCH_GATE_V2 / the LOOP_MEMO path actually fire in
  simulation. Traces the live /32 candidate route, not the converged table.

OPEN (your call, not shipped):
- .2 metric-5 alias churn: sweep deletes live aliases every merge and promote
  re-lays them (unconditional replace of an unchanged route). /24 traffic routes
  are NOT churned. Fix = keep a metric-5 alias whose /24 has a winner; requires
  rewriting 5 oracles that assert the sweep-everything design. Design decision.

--------------------------------------------------------------------------------
# FROGNET_NOW — state as of 2026-07-05 (fail-fast sweep session)
Baseline all_5.tgz gated 14/17 with 7 discovery-oracle regressions. This tree
gates GATE: PASS, all 17 in-container tiers, DISCOVERY GATE 58/58.

FIXES (each root-caused; tags grep-able):
- [FAILFAST_V1] discovery.py: core.not_frognet import stub removed (hard
  import); runmerge.py flush swallow removed (unguarded, fail loud).
- [NF_CONTRACT_V1] real_backends._ping_pong now separates verdicts: timeout
  -> None and NEVER marks not_frognet; definitive errnos (REFUSED/HOST-/NET-
  UNREACH) -> "REFUSED", the only marker; protocol garbage logged loud.
  discovery.py:296 mark site matches. FakeVerify grew `refused` to mirror.
  BINDTODEVICE failure now warns (was silent pass).
- [SENTINEL_DIR_V1] not_frognet SENTINEL dir env-overridable
  (FROGNET_SENTINEL_DIR); default /etc/sentinels unchanged for boxes.
- [SENTINEL_ISOLATION_V1] run_all.py exports a per-run tempdir (fail-hard);
  run_discovery_oracles gives EACH oracle a fresh dir. Root cause of the
  walk/etc regressions was cross-run/cross-oracle mark leakage through the
  real /etc/sentinels — proven by clearing the file (oracle went green) and
  by the marks' content (sim IPs, incl. relay 10.102.60.230).
- [ALIVE_9009_RETRY_V1] implemented per its shipped oracle (was oracle-only):
  Discovery._alive_measure/_alive_ok retry a NEGATIVE :9009 verdict up to
  $FROGNET_ALIVE_9009_TRIES (default 3); float/"LOOP"/"REFUSED"/True are
  decisive, never retried. All 6 verify call sites wired.
- [LOCAL_BY_DEFINITION_V1] core/frognet_service_hosts: EMPTY candidate pool +
  self_ip -> the asking node is the host (standalone completeness). Scoped so
  [NO_FLOOR] still governs every case where candidates existed.
- Oracles reconciled to DECIDED behavior (tarball had pre-decision copies):
  service_election_check 2c -> the June-23 NO-AGE replacement (verbatim from
  the record); safeboot stubs -> current service_host_lines signature
  (self_ip/lan_subnets); winner_hysteresis_nexthop B1 -> the decided >=50%
  bar (June 24), with new B2/B3 proving a 30% challenger is HELD;
  not_frognet_skip + onsegment_gate -> refused/timeout split, with new
  timeout-never-marks cases (fail on old code).
- etc/systemd/system/frognet-discovered.target reconstructed from the boot
  gate oracle's explicit contract (Requires+After the gate service); four
  shipped units and live.py already referenced it.
- simulation/broker_topology.py registers sim nodes WITH a guid
  ([GUID_IDENTITY]: broker 400s empty guid by decided contract) — fixed both
  failing tiers (broker ponds, failure modes).
- 8 __pycache__ dirs shipped in the tarball: cleared (ship-no-bytecode).

QUEUE: systematic static sweep of remaining used files for the violation
class (bare except / silent defaults / subprocess unchecked) — the gate-
driven pass above fixed what the suite exposed; the file-by-file sweep is
NOT yet done. See /home/claude/FINDINGS_failfast_sweep.md.

## 2026-07-06 — Seattle5 merge latch, root-caused from the field log
Log (merge 20260706T045104): 10.102.60.0/24 winner dev flipped wg2->wg0
between passes, slash24_mutated=1 each pass, runAgain latched, 4-6 min/pass.
Mechanism proven: NY-1's .2 echo alternates per pass while .1 pongs, so one
pass has a measured candidate on one tunnel and the next has a vouch-only
pool — and the vouch fallback promote broke ties by WALK ORDER (lottery).
Fix: [VOUCH_INCUMBENCY_V1] — when no measured candidate survived, a vouch
matching the installed winner's (via,dev) goes first. Measurement still
beats hearsay; the alive gate still evicts a dark incumbent. Oracle case D
(fails on old, passes on new) grown in test_winner_hysteresis_nexthop_oracle.
DISCOVERY GATE 58/58 after.
OPEN (far side, not this tree): WHY 10.102.60.2's echo alternates while
10.102.60.1 answers — instrument on New-York-1. Also note [ALIVE_9009_RETRY]
triples worst-case dead-probe cost (~15s vs ~5s): visible in the 4-6 min
passes on a mesh with several dead .2s; tunable via FROGNET_ALIVE_9009_TRIES.

## 2026-07-06b — both-machine merge wedge: unbounded `ip` shell-out
Both NY-1 and peer stopped at the identical instruction: inside
_health_probe_tunnel for wg0->10.111.11.1, between the probe /32 replace and
its del. Eliminated from the tree: RealVerify timeouts (hard 2.0/3.0 floats),
the codec-banner import chain (banner is codec.py's last line), frognet_log
(plain stderr). Remaining unbounded step on that exact path: kernel._run's
subprocess.run had NO timeout — an `ip route` blocked on the rtnetlink lock
(concurrent wg reconcile) hangs forever, silently. [KERNEL_CMD_DEADLINE_V1]:
20s deadline, loud kill line, rc=124; OSError path now loud too. Build banner
finally bumped (20260706-a-FAILFAST) so deploy state is grep-able — the old
banner could not distinguish deployed trees. ON-BOX proof still owed: the
D-state `ip` child + its rtnl wchan, and WHO holds the lock (likely the
reconcile's wg ops) — that ordering collision is its own root once confirmed.
Gate: DISCOVERY 58/58 after (kernel_parity caught a missing `import sys` in
the new failure path — fixed; the oracle earned its keep).

## 2026-07-06c — stop-guessing instrumentation for the wg0 dark windows
PROVEN so far: on the new build, wg0->BABox misses ALL retries for entire
passes, contributes nothing to the promote pool, winner moves, runAgain
re-arms. Cause location UNKNOWN (NY-1 wg/kernel, wire, BABox wg/kernel/
daemon) — one host's log cannot discriminate. Shipped:
- [NF_FAIL_DETAIL_V1] real_backends records per-call failure step/reason
  (timeout / ECONNREFUSED / errno / oserror) in _last_fail.
- [DIAG_TUNNEL_DARK_V1] discovery._alive_measure: when every retry misses on
  a wg* dev, emits ONE line with per-try fail reasons + `wg show DEV
  latest-handshakes` + `transfer` + `ip route get target`, all bounded by
  KERNEL_CMD_DEADLINE, verdict-neutral.
Next dark pass self-documents. BABox-side correlation still required for the
same minutes: full journalctl (not one unit), `wg show`, ss on :9009.

## 2026-07-06d — the missing setup_iptables, resolved
etc/setup_iptables IS in the release manifest (frognet_build_release.sh
WORLD_PATHS) — first-class by design. The uploaded all_5 lacked the member
because the builder's "skip missing paths" loop silently dropped manifest
entries absent on the build host. [RELEASE_MANIFEST_FAILFAST_V1]: a missing
manifest path is now FATAL — the build refuses to ship an incomplete world,
naming every missing path. Boxes are fine (every merge log shows
setup_iptables rc=0); the deployed bytes still need one `cat
/etc/setup_iptables` from a box to be diffed against the designed content
(record: idempotent ensure — forwarding, rp_filter=0 incl per-wg, port-80
OUTPUT DNAT; no 9009 NAT). Also noted: usr/local/bin/' (quote-named file) is
a stale runMerge copy shipping in the world — packaging debris for cleanup.

## 2026-07-06e — THE ROUTING LAW (John, verbatim, supersedes all prior bars)
    If you have a candidate path for a remote machine
      If there is already a .1 path for that candidate
        If that .1 path is alive and healthy
          THEN LEAVE THE FUCKING ROUTE ALONE
[ROUTE_INCUMBENCY_HOLD_V1] at the top of promote: an installed winner whose
dest .1 answers over its own path (reflect chain for relayed, :9009 pong
bound to the incumbent dev for direct/tunnel) is untouchable — no measured
challenger at ANY margin (the 2026-06-24 50% bar is superseded), no vouch
reshuffle, no mutation, no runAgain re-arm on that dest. Candidates matter
only when the incumbent is absent or its .1 is dead (PROMOTE_HOLD_RELEASED).
Alias /32 refreshed on hold; winners list still fed for transit derivation.
No verify backend wired => no hold (no evidence, old path). Oracle:
winner_hysteresis_nexthop B1 (healthy incumbent vs 60% challenger HELD —
fails on old code) + B1c (dead incumbent releases). Gate 58/58.
Effect on the field latch: the flapping dest's winner stops moving while its
.1 stays alive; slash24_mutated stops firing; chains converge. The .2
probe-contract debt (session-manufacturing HELLOs) remains open and now
matters only for probe load, not routing stability.

## 2026-07-06f — [VOUCH_TS_GATE_V1] host-propagation death, John's ruling
"The tuple has a timestamp. Using the timestamp is the responsibility of the
consumer." No tombstones, no counters, no host reaper: the ts-bearing
assertion of a host's existence already exists — its own SD:capability
tuples on the control plane (re-asserted ~5 min, reaped when dead). The
consumer (the walk's vouch path) now checks the vouched .1's own freshest
self-assertion BEFORE spending anything: absent-or-ancient in a POPULATED
store -> VOUCH_STALE, no candidate, no propagation, no child walk, no probe
ladder. Empty/unreachable store (cold bootstrap) or caps unwired -> gate off
(loud when a store was expected). FROGNET_VOUCH_MAX_AGE_S default 3600.
Downstream, the existing machinery finishes the death: no vouch -> no
candidate -> no route -> HOSTS_GATE drops the host line -> getHosts stops
serving it -> the vouchers themselves go quiet a merge later. Seattle6 dies
mesh-wide within two merges of fleet deploy without any manual purge.
Oracle: test_vouch_ts_gate_oracle (fresh passes / absent refused / ancient
refused with age / empty-store legacy / unwired legacy) — fails on pre-gate
code. Discovery tier 59/59. Build banner bumped to 20260706-b-VOUCHTS.

## 2026-07-06g — [BALLOT_ADMISSIBILITY_V1] — the databasehost fix, PROVEN
John, verbatim: "The tuple has a timestamp. Using the timestamp is the
responsibility of the consumer." The election is a consumer: a capability
ballot unrefreshed past FROGNET_BALLOT_MAX_AGE_S (default 1800s; heartbeat
~300s) is INADMISSIBLE, refused loudly with its age. Supersession chain
documented in test_capability_no_age_oracle: Jun-12 fresh_s=180 (caused the
Jun-21 chronic split-brain race) -> Jun-23 no-age read (cured the race,
delegated liveness to a reaper that field-proved absent) -> Jul-6 consumer
admissibility (horizon >> heartbeat, so the Jun-21 race stays dead by
arithmetic; a dying node's ballot crosses the boundary once, transiently).
PROOF WITHOUT A FIELD RUN (test_ballot_admissibility_oracle): the EIGHT REAL
ballots from NY-1's instrumented log replayed through the real election.
Pre-fix behavior (horizon disabled): all 8 admitted incl. the 8.1-day
10.160.160.1 fossil — reproduces the field failure. Fixed: admitted =
{10.102.60.1, 10.120.120.1, 10.130.130.1} (the nodes actually speaking),
fossil refused at 701292s, winner a living node; a RETURNING Seattle6 with a
fresh ballot is admissible again (ages, not blacklists); an all-stale store
elects nobody and falls to local-by-definition. DISCOVERY 60/60; FULL M6
GATE: PASS. The election is now correct regardless of store view, reaper
state, or any lifecycle failure anywhere.
ALSO: etc/apache2 absent from shipped all_5 despite manifest (second
manifest hole; builder failfast already guards future builds). The
three-views store question (reaper/curl/election saw different row sets on
one box) remains OPEN but is now harmless to elections by construction.
Ballot ages also exposed: BABox/BAMacBook capability publishers dead 8-10
days, Seattle5's 5.5h, NY-2's 70min — communicator republish path broken on
most of the fleet; NY-1's was fixed today, same check applies per box.
