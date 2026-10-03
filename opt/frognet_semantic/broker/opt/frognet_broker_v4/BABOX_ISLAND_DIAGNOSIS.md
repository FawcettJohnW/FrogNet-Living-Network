# BABox island — ROOT CAUSE (confirmed by data): stale-name tunnel rows

_Status doc. Survives compaction. 2026-06-23. Broker is the correct GUID build.
Earlier wrong calls — pre-GUID broker, ghost rows, port exhaustion, public-IP
conflation — are all retracted; see bottom._

## Cause (CONFIRMED — `diagnose_mesh.py --tunnels` flagged it)
Two active tunnel rows are named for BABox but bound to a different node-id:
- idx1 (id 1018): stored name `BABox-to-Seattle5`, bound to **BAMacBook(56)**↔Seattle5(54)
- idx2 (id 1019): stored name `New-York-1-to-BABox`, bound to NY1(55)↔**BAMacBook(56)**

Node 56 was named "BABox" when those rows were built, then renamed to "BAMacBook".
`_deterministic_tunnel_name` is `"{lowerSubnetName}-to-{higherSubnetName}"` — names only.
`_ensure_tunnel` pre-flights by `(pond_id, name)` and, on an active=1 hit, returns that
row **without checking its node-ids match the current pair**. So when the real BABox
(id 57) polls, `_ensure_tunnel(BABox, Seattle5)` and `(BABox, NY1)` match idx1/idx2 by
name and return them. Those rows' droplet interfaces carry **BAMacBook's** pubkey
(ports 51003 / 51005), so BABox's handshake (its own key) is rejected → no handshake →
island. The poll returns 200 because a row *was* returned; no CREATE is logged because
nothing is created.

The real BAMacBook tunnels are idx3 (`BAMacBook-to-Seattle5`) and idx4
(`New-York-1-to-BAMacBook`), both correctly bound — so removing idx1/idx2 does not
affect BAMacBook.

## FIX

### Immediate (closes the island)
Deactivate the two mismatched rows:
```
python3 diagnose_mesh.py --tunnels --apply
# or: sqlite3 /var/lib/frognet_broker_v4/broker.db \
#       "UPDATE tunnels SET active=0 WHERE id IN (1018,1019);"
```
On the next poll `_ensure_tunnel` finds no active row by those names and rebuilds them
bound to the CURRENT nodes (BABox 57 with BABox's pubkey). Verify:
- `python3 diagnose_mesh.py --tunnels` → 0 mismatches; new `BABox-to-Seattle5` and
  `New-York-1-to-BABox` rows bound to BABox(57).
- `python3 diagnose_mesh.py --node BABox` → NY1/Seattle5 show a tunnel idx, not NONE.
- BABox `runMerge` → wg1/wg2 handshake instead of `no handshake after 30s`; `ip r`
  gains 10.102.60/24 and 10.250.250/24.

### Permanent (stops recurrence) — broker patch, not yet written
`_ensure_tunnel`'s active=1 early-return must verify the matched row's
`host_node_id`/`join_node_id` equal the current `(node, peer)` ids. If not, the row is a
stale-identity match → `_destroy_tunnel` + recreate, logged loudly. Name is not a safe
identity key across renames. Ship with an oracle: build a row named for a node whose id
later changed, assert old code returns the stale row / new code rebuilds bound to the
current ids.

Also worth fixing upstream: whatever renamed node 56 (BABox→BAMacBook) without retiring
or re-binding its tunnels is what minted these rows. Same-site boxes sharing a public IP
(68.132.214.34) make this easy to trigger.

## Ruled out (history, do not re-walk)
- AllowedIPs (always 10/8); BABox WireGuard (broker tunnel healthy).
- Chorus membership (all four in `entire_pond`).
- Pre-GUID broker — WRONG; empty `mac=` logging exists in the GUID build.
- Ghost rows — WRONG; `reap_ghost_tunnels.py` found 0 ghosts, all ifaces live.
- Port exhaustion — WRONG; BABox polls return 200, `_alloc_wg_port` raise would 500.
- Public-IP conflation — WRONG; broker resolves nodes by pubkey only.
