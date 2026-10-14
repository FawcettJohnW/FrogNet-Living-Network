# fdf-hub: the region's WireGuard concentrator

`/opt/fdf/bin/fdf-hub` runs on the region host. One WireGuard interface per region (`wg-<AREA>`, 10.253.253.254/16),
one peer per live member, a member's 10.A.B.0/24 behind its edge address 10.253.A.B, and prefixes only from verified
mappings that name this hub. One owner per /24 per region: the live claimant with the lowest announce id; a later
claimant is refused and logged (`REFUSED ... claimed by ...`). Nothing polls: `fdf-events` holds one session to the
RAM manager and reports changes; each change is one reconcile. Its WireGuard implementation is the one recorded in
/etc/frognet/wg.impl (`fdf-wg-impl --record`, run by the unit before it starts).

## On the region host

`fdf-region-create AREA PUBLIC_IP` makes everything the hub needs: the site key, the RAM manager unit `fdf-ram-AREA`,
`map_site`, /opt/fdf/hub/hub.env (the hub's settings; no package carries one), `fdf-hub` and the hub's own membership
`fdf-client-AREA`. The steps, the firewall lines and the update command are in `docs/OPERATING.md` (section 3) in the
FrogNet repository. Without hub.env the unit refuses to start and says
`fdf-hub: /opt/fdf/hub/hub.env does not exist: this machine holds no region yet. Run: /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP`.

    journalctl -u fdf-hub -f                       # "start (adopt): members=.. mappings=.. peers=.. build=..."
    wg show wg-AREA                                # the peers and their /24s
    /opt/fdf/bin/fdf-ls                            # the hub is a participant, role hub

Oracle (no WireGuard needed): `python3 /opt/fdf/hub/hub_oracle.py` on the region host (27 checks, with controls); from a
checkout, `FDF_PY=opt/fdf/lib/python python3 opt/fdf/hub/hub_oracle.py`.

## On a FrogNet node

The node's tunnel daemon is the hub's other end: in region mode it brings up one WireGuard link to each hub of its
region. A node joins with the `.run` (`docs/OPERATING.md`). /opt/fdf/var/facts.json is written by the daemon; fdf-client
announces it and registers its mappings, signed with the site key and bound to the session. Never edit the region
directly. `/var/lib/frognet/view.json` says the daemon's view of the region (phase, gen, dbhost, hosts_hash).

## The documents beside this file

`DEPLOY-REGION-2026-10-09.md`, `HANDOFF-REGION-2026-10-09.md` and `PLAN-FROGNET-ON-FDF.md` here are one-line pointers;
the documents are history, in `opt/fdf/src/fdf-source/docs/history/` in the FrogNet repository.
