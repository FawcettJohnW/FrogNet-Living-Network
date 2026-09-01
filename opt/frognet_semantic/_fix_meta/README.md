# FrogNet election fixes — LAN_IS_ATTACHED_V1 + SERVICE_HOSTS_NO_SYNC_V1

Diagnosed from a Seattle2 (eth1=10.102.60 / eth0=10.28.28) runMerge: the merge looped
across the mesh and elected the wrong mediahost/databasehost.

## Bug 1 — service-host change triggered re-sync/propagation (the loop)
`discovery/live.py:_commit_hosts` set `sync_required` on `changed or routes_changed`,
where `changed` = ANY `/etc/hosts` write. `/etc/hosts` carries the `<role>.frognet`
service lines, so every mediahost/databasehost election flip set `hosts_changed` ->
`SYNC_REQUIRED` -> `chain_dirty=1` -> `propogateNotification`. Each node re-derives the
same role line, the byte differs, it propagates, the neighbor re-derives — forever.

Fix: the host-table trigger now keys on `_topology_changed()`, which strips `.frognet`
service lines before comparing. Service-line changes no longer trip sync. dnsmasq still
reloads on any change (so resolvers see the new mapping); routes remain the primary
trigger; a real node-identity/topology line change still syncs.

## Bug 2 — off-LAN node won the LAN (mediahost) role
`core/frognet_role_elect.py:gather_candidates` bucketed a candidate as LAN when its /24
was NOT in the wg-WAN plane (`reach_plane.wan_subnets`). Seattle2 has no wg tunnels, so
`wan=[]`, so EVERY candidate — including 10.160.160.1 reached via the NY1 relay — was
called LAN, and an off-LAN box won the LAN-only mediahost election.

Fix: LAN now means a candidate whose /24 is one of THIS node's directly-attached
segments. `discovery/live.py` passes `disc.local_subnets` (rendered `10.x.y.0/24`) down
through `apply_to_etc_hosts` -> `service_host_lines` -> `gather_candidates`. The bucket
test is now membership in that attached set. databasehost still elects over the
pond-wide `hosts_list`; only the LAN list (mediahost) is constrained.

NOTE: the databasehost in that run was ALSO wrong for a separate, already-fixed reason —
NY1 (10.102.60.1) publishes a malformed capability blob (dict where a scalar belongs) and
is `MALFORMED excluded`. Deploy the capability-probe fix to NY1 as well; that is a
different package.

## Files (overlay onto each node)
- opt/frognet_semantic/core/frognet_role_elect.py
- opt/frognet_semantic/core/frognet_service_hosts.py
- opt/frognet_semantic/discovery/live.py

## Oracles (fail on old, pass on new)
- _fix_meta/oracle_lan_bucket.py        : off-LAN excluded from LAN list (PATCHED=1 passes; unset confirms bug)
- _fix_meta/oracle_service_no_sync.py   : service-line change -> no sync; node change -> sync

## Deploy
Overlay the three files, restart the discovery/merge service, re-pull a Seattle2 merge.
Expect: mediahost no longer 10.160.160.1; merge ends HOSTS_KEEP (no `SYNC_REQUIRED set
reason=hosts_changed` from a service flip) and stops propagating.
