# FROGNET_PRIMER — Consolidated Identity + Health Payload (2026-06-19)

This payload carries EVERY code change from the 2026-06-19 session in one tree.
Three independent fixes, each proven on the simulator against real code. Extract
with `tar -xzf <payload> -C /`. Back up the broker DB before restarting the
broker (the GUID migration is destructive by design).

================================================================================
FIX 1 — GUID IDENTITY (broker server-half + client wiring)
================================================================================
Problem: identity was MAC-keyed; the node sent its install-time GUID only in the
"mac" field as a placeholder, and the broker had NO server-side logic to match
on it. Returning nodes collided on name and the broker RETIRED the incumbent,
minting Seattle5.retired.48/.49 and dropping the live node out of the mesh.

Fix: GUID is the SOLE identity key. No MAC, no name/pubkey heuristic, no
fallback.
  - broker/frognet_broker_v4.py: nodes.guid column + partial unique index;
    register_node GUID-only (empty->400, match->REVIVE same row, unknown->new
    node retiring nobody); all MAC/name/pubkey/collision/cross-pond guessing
    DELETED; POST /api/v4/retire-guid (explicit regen retirement); one-time
    migration reaps GUID-less active rows + cascades tunnels/chorus_members/
    node_ip_alloc.
  - internet_tunnels_v3/config.py: reads GUID from /etc/fnid (canonical source,
    same as frognet-node-guid.sh); reads POND from NETWORK_NAME in
    gateways.conf.
  - internet_tunnels_v3/poll.py: daemon gains register_with_broker() (sends
    `guid`); bring-up Phase 1 my-channels 401/404 -> self-register -> retry once
    -> only then teardown (no more "tear down and stop"). Real outage still
    tears down stale.
  - /usr/local/bin/frognet-node-guid.sh: REPLACES the deployed helper. Same
    generate-if-absent / 0444 / loud-failure behavior, PLUS a `regenerate`
    subcommand (POST retire-guid, then write fresh GUID).
  - discovery/sim/broker_world.py: sim register helpers send a deterministic
    GUID so the harness works under GUID-only identity.

MANUAL EDIT (node-side file NOT in repo): /usr/local/bin/frognet-tunnel-setup-v3.sh
register curl body must send `guid`. Add a line next to the existing `mac`:
    \"guid\":          \"${NODE_GUID}\",
    \"mac\":           \"${NODE_GUID}\",
This is what fixed the `400 guid required` on Seattle5. (NY-1 confirmed working
2026-06-19 19:05: Registration: registered, wg0->Seattle5 + wg1->BABox built.)

Oracle: broker/test_guid_identity_oracle.py (13/13, real broker via broker_world),
internet_tunnels_v3/test_register_on_404_oracle.py (10/10).

================================================================================
FIX 2 — PING-PONG HEALTH (HTTP echo must NEVER be a liveness gate)
================================================================================
Problem: tunnel/route liveness was gated on the L7 HTTP echo (frognet_echo.php).
A tunnel alive at L3/wg whose peer proxy 503s or is briefly down was reaped
(TUNNEL_DEAD ... reason=echo_failed_code=503). This is what reaps wg0/wg1 on
nodes still running the OLD discovery build (build=20260605-f-VOUCHGATE).

Fix: liveness gates on a :9009 ping-pong (RealVerify.measure_or_loop ->
float|"LOOP"|None) for ALL route kinds; HTTP echo demoted to identity-only.
  - discovery/healthcheck.py [PINGPONG_HEALTH_V1]: TUNNEL_DEAD now reports
    reason=loop_9009 / reason=no_pong, NEVER echo_failed_code. A tunnel whose
    daemon answers :9009 survives an echo 503.
  - discovery/live.py: reuses disc.verify (:9009) instead of echo.
  - discovery/real_backends.py [PINGPONG_RETURN_DEV_V1]: return-IP is the egress
    dev's local 10.x.
  - discovery/discovery.py [PINGPONG_GATE_V1/FASTPATH_V1]: walk gates
    reachability on :9009 for tunnel/LAN/relayed; existing-winner fast-path.

Oracles: test_healthcheck_oracle, test_pingpong_return_ip_oracle,
test_walk_pingpong_gate_oracle, test_fastpath_existing_route_oracle,
test_vouch_route_oracle, test_reflect_vouch_gate_oracle, test_onsegment_gate_oracle.

================================================================================
FIX 3 — _rank MALFORMED-CAPABILITY GUARD (DB-host election crash)
================================================================================
Problem: select_database_host._rank read numeric capability fields with bare
int()/float(); a malformed publish (mem_available_kb arriving as a dict) raised
TypeError and crashed the WHOLE merge AFTER discovery had already succeeded.

Fix: discovery/hosts.py [RANK_NUM_GUARD_V1]: every numeric field read through a
_num() coercion helper. A bad field degrades that candidate's score; it never
crashes the merge. These are already-mysql-capable .1 hosts, so coerce-not-drop.

Oracle: test_dbhost_rank_malformed_oracle.py.

================================================================================
DEPLOY ORDER (per node, then broker last)
================================================================================
1. BACK UP /var/lib/frognet_broker_v4/broker.db on the broker host.
2. Extract this tar `-C /` on every node AND the broker host (it carries the
   broker source too, so source control updates).
3. GUID already exists at /etc/fnid from install; nothing to generate.
4. Apply the one-line `guid` edit to /usr/local/bin/frognet-tunnel-setup-v3.sh
   on each node (or push a patched copy).
5. Restart frognet-tunnel-daemon-v3 on each node. Run runMerge.bash; confirm
   TUNNEL_DEAD reasons are now loop_9009/no_pong, never echo_failed_code.
6. Place + restart the broker -> migration adds guid column and REAPS all
   current GUID-less rows (clean cutover). Every node re-registers once and
   REVIVES/creates cleanly.

================================================================================
PROVENANCE / INVARIANTS (do not regress)
================================================================================
- GUID-only identity: no MAC, no fallback. Only /api/v4/retire-guid retires.
- HTTP echo is NEVER a liveness/reachability gate; :9009 ping-pong is. Applies
  to ALL route kinds, no safe-subset exceptions.
- Two-database discipline unchanged: databasehost_control.frognet (deterministic
  highest-.1, discovery/coordination) vs databasehost.frognet (elected data
  host). All election reads candidate data from _control.
- broker = coordination + cross-NAT transit + cross-site federation ONLY.
- Prove every change on the simulator with an oracle that FAILS on old code and
  PASSES on new. The 8 discovery oracles needing etc/frognet_bundles/communicator
  (boot_gate, elector, safeboot, service_election, host_reset, bundle_float,
  malformed_capability_guard, role_registry) are unrelated to these changes and
  pass in a complete tree; they are not in this payload's scope.

================================================================================
STILL OPEN (not identity, not in this payload)
================================================================================
- Seattle5 frognet-proxy returns 503 on its echo; BABox/BAMacBook offline (000).
  Fix 2 stops these from reaping a wg-live tunnel, but the proxies themselves
  need attention.
- GROUP_TOKEN missing on a node is a REAL 401 (not the standing red herring on
  token-bearing nodes). Set where absent.
- Deep chain hops (Seattle3/Seattle2/Seattle6 .2) FAIL_ECHO via the NY-2 relay:
  on-segment relay echo limitation, pre-existing.
