# FROGNET_NOW — GUID Identity (clean break, no fallback)

> **SUPERSEDED, 2026-08-31. Historical record; do not follow the deploy order below.**
> This note describes the migration that introduced `frognet-guid` and a
> `NODE_GUID=` line in `/etc/frognet/tunnel.conf`. That approach was replaced:
> identity now lives at `/etc/fnid`, allocated by `frognet-node-guid.sh --ensure`,
> and read directly by `internet_tunnels_v3/config.py` and
> `frognet-tunnel-setup-v3.sh`. `frognet-guid` has been removed.
> Step 2 of the deploy order below would put identity in the directory a
> reinstall wipes, which is the failure the current design exists to prevent.

## What changed and why
Node identity moves off MAC entirely onto an **install-time GUID**. GUID is the
SOLE key the broker matches on: no MAC, no name/pubkey heuristic, no fallback.
This removes the machinery that minted `Seattle5.retired.48/.49` — the broker
could not match a returning node to its row, so it retired the incumbent as a
name-collision and the live node aged out of the chorus+freshness mesh, leaving
NY-1 unable to pair to it.

Root cause chain (all confirmed from source this session):
- `register_node` keyed identity on `req.mac`; the tunnel daemon **never** sent
  one (and in fact never called `/api/v4/register` at all — registration was a
  one-shot install-time action). No GUID was ever generated or wired.
- A returning node 404'd on `/api/v4/my-channels`, the daemon bucketed 404 as
  `broker_unreachable`, tore down all wg ifaces, and **stopped** — never
  re-registering.

## Files (all proven on the simulator / real broker handlers)
- `frognet_broker_v4.py` — `nodes.guid` column + partial unique index
  `(pond_id,guid) WHERE guid!='' AND active=1`; `register_node` rewritten
  GUID-only (empty→400, match→REVIVE same row/id/tunnels/choruses with pubkey
  rotated in place, unknown→new node retiring nobody); the entire MAC /
  name+pubkey / REGISTER_COLLISION / cross-pond identity-guessing block DELETED;
  new `POST /api/v4/retire-guid` (explicit regen retirement, idempotent);
  one-time migration reaps GUID-less active rows + cascades tunnels /
  chorus_members / node_ip_alloc.
- `frognet_guid.py` — node helper. `generate` (idempotent, never overwrites an
  existing GUID — that is what makes identity durable), `regenerate` (POST
  retire-guid for current GUID, then write a fresh one), `show`. Install calls
  `generate`. Reads/writes `NODE_GUID=` in `/etc/frognet/tunnel.conf`.
- `config.py` — reads `NODE_GUID` (empty + broker-enabled => hard CRITICAL log,
  node won't register) and `POND` (from `NETWORK_NAME=` in
  `/etc/frognet/gateways.conf`, already on disk from install).
- `poll.py` — daemon GAINS `register_with_broker()` (it had no register call);
  bring-up Phase 1 now branches on HTTP status: `my-channels` 401/404 →
  self-register with GUID → retry once → only then teardown. A genuine
  connection failure (non-HTTPError) still tears down stale (unreachable path
  preserved).
- `broker_world.py` — sim register helpers now send a deterministic
  `GUID-{pond}-{name}` so the harness works under GUID-only identity.

## Oracles
- `test_guid_identity_oracle.py` (real broker via broker_world): revive on
  same-GUID return (no new row, no retired.NN), unknown GUID = new node retiring
  nobody, empty GUID rejected, explicit regen retires exactly the prior row then
  registers clean, migration reaps GUID-less rows and spares GUID-bearing ones.
  13/13.
- `test_register_on_404_oracle.py`: register payload carries GUID+pond,
  no-identity refuses (no blind guess), 404→register→retry proceeds without
  teardown, real outage still tears down. 10/10. NOTE: the 404-branch assertion
  uses a faithful transcription of the Phase-1 control flow (the real
  `register_with_broker` is exercised directly); `_reconcile_bringup_phase` is
  too setup-heavy to call standalone.

## Deploy order (DESTRUCTIVE migration — back up broker.db first)
1. Back up `/var/lib/frognet_broker_v4/broker.db`.
2. Deploy `frognet_guid.py` + `config.py` + `poll.py` to nodes; install
   `frognet-guid` and run `frognet-guid generate` on every node.
3. Deploy `frognet_broker_v4.py`; restart broker → migration adds the guid
   column and REAPS all current GUID-less rows (the approved clean cutover).
4. Every node re-registers once with its GUID (daemon does this on its next
   poll via the register path / 404 self-register). Roster rebuilds clean.

## Still open (separate from identity)
- GROUP_TOKEN: Seattle5 had none; that 401 is NOT the standing red herring on
  token-less nodes. Set GROUP_TOKEN on any node missing it.
- BAMacBook proxy 503/000 = that peer offline; benign.
- NY-1 tunnel mesh fills in once live nodes (esp. Seattle5) are registered
  active and share NY-1's chorus.
