# TUNNEL_ID_KEY_V1 — re-key broker tunnels on the node-id pair

## What this fixes
A tunnel's identity was its **name** (`{lowerSubnetNode}-to-{higherSubnetNode}`),
derived from node names. Node names are mutable — a node is revived by guid across a
rename, keeping its id but changing its name. `_ensure_tunnel` matched on that name, so
renaming a node orphaned its tunnels: the match returned a stale-named row bound to a
**different node-id**, whose broker-side WireGuard pubkey then never matched the real
owner. That is the BABox island — node 56 was named "BABox" when its tunnels were built,
got renamed to "BAMacBook", and the real BABox (id 57) kept being handed node 56's
tunnels and could never handshake.

The fix makes the tunnel's identity the **unordered node-id pair**, which is stable across
renames. The name becomes a label that `_ensure_tunnel` keeps current. This removes the
whole bug class — no name can ever bind a tunnel to the wrong node again.

## What changed (see TUNNEL_ID_KEY_V1.diff — 5 hunks, all in the broker)
1. **Schema**: dropped `UNIQUE(pond_id, name)` from the `tunnels` CREATE TABLE.
2. **`_ensure_tunnel`**: pre-flight, IntegrityError re-select, and final select now match on
   `MIN(host_node_id,join_node_id)/MAX(...)` — the canonical node-id pair — not the name.
   On an active match it refreshes a stale name label (best-effort; cosmetic).
3. **Migration `_migrate_tunnel_id_key` (runs in `init_db`, idempotent)**:
   - collapses active duplicates sharing a `(pond_id, node-id pair)` — keeps the row whose
     stored name matches its current binding, deletes the rest (DB-only; `_restore_state`
     reaps the orphan wg ifaces on the same startup);
   - rebuilds the table to drop the legacy `UNIQUE(pond_id, name)` (ids preserved);
   - creates partial unique index `ux_tunnels_pond_pair_active` on the active node-id pair.

Everything else already used node-ids: `_restore_state` rebinds each wg peer from the
current `nodes` pubkey by `host_node_id`/`join_node_id`; destroy, reaper, and the
pubkey-change cascade are all node-id based. Only `_ensure_tunnel` + the schema were wrong.

## Prove-don't-assert (both oracles included; ns stubbed, temp DB)
Run them yourself:
```
pip install --break-system-packages fastapi pydantic uvicorn   # oracle deps only
python3 oracle_tunnel_idkey.py ./broker_orig.py        # FAIL / exit 1  (name-keyed)
python3 oracle_tunnel_idkey.py ./frognet_broker_v4.py  # PASS / exit 0  (id-keyed)
python3 oracle_migration.py                            # PASS / exit 0
```
- `oracle_tunnel_idkey.py` reproduces the incident: build BABox(idA)↔Seattle5, rename idA to
  BAMacBook, register a new BABox(idB), then `_ensure_tunnel(newBABox, Seattle5)`. Old code
  returns the tunnel bound to idA (the renamed box) → FAIL. New code builds it bound to idB
  → PASS.
- `oracle_migration.py` builds a legacy DB with `UNIQUE(pond_id,name)` and a duplicate pair,
  runs the migration, and asserts: constraint dropped, pair index present and enforcing,
  duplicate collapsed to the correctly-named row, ids preserved.
- (Confirmed separately: `_ensure_tunnel` is idempotent — both call orders return the one row.)
You need `broker_orig.py` (a pristine copy of the current droplet broker) next to these to run
the old-vs-new comparison.

## Deploy (droplet)
1. Back up first:
   `cp /var/lib/frognet_broker_v4/broker.db /var/lib/frognet_broker_v4/broker.db.bak-$(date +%s)`
   and back up the current broker .py.
2. Drop in `frognet_broker_v4.py` (replace the running broker file).
3. Restart the broker service. On startup `init_db()` runs the migration (collapses the
   BAMacBook duplicates, drops the legacy constraint, creates the pair index) and
   `_restore_state()` reconciles the namespace.
4. Verify with the diagnostic from earlier:
   - `python3 diagnose_mesh.py --tunnels` → 0 mismatches.
   - `python3 diagnose_mesh.py --node BABox` → NY1/Seattle5 show a tunnel idx, not NONE.
   - BABox `runMerge` → wg1/wg2 handshake; `ip r` gains 10.102.60/24 and 10.250.250/24.

This deploy supersedes the manual `diagnose_mesh.py --tunnels --apply` stopgap; the migration
does the same collapse and then prevents recurrence. If you already ran the stopgap, the
migration is idempotent and safe.

## Rollback
Restore both the broker `.py` and the `broker.db` backup. The migration's table rebuild is a
forward step on the DB (the legacy constraint is gone); restoring the DB backup returns to the
pre-migration state cleanly.

## Note on the rename path (separate, optional follow-up)
The register guid-revive path already detects the rename (`REGISTER RENAME` log). With
id-keying it no longer needs to do anything about tunnels — they follow the id. If you ever
want renames to also refresh tunnel labels immediately (rather than on the next poll), that's
a one-line relabel in that branch; not required for correctness.
