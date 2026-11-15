<!-- Generated from the site page spec-07-broker.html (https://fawcettinnovations.com/spec-07-broker.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §7

# Internet extension: the region.

A single node is a network. Two nodes on the same wire find each other with no help. A region exists for the third case: nodes that cannot see each other directly. The broker that did this before was removed on 12 October 2026; this section describes what replaced it, from the code. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary (broker era)

This recording is from the broker era. The mechanism it shows was replaced by the region on 12 October 2026 (`docs/cleanup-2026-10/T3b.md`); the document below governs.

The broker joins two independent FrogNets

FrogNet Specification — §7 rewritten 2026-10-31 against build fdf-2026-10-25-r31. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1What the region is](#b1)
2.  [2Where it lives](#b2)
3.  [3Admission is an invitation](#b3)
4.  [4The hub is derived from the records](#b4)
5.  [5Loss of the region](#b5)
6.  [6Tunnel liveness](#b6)
7.  [7Trust](#b7)
8.  [8What the broker was](#b8)
9.  [9References](#b9)

## 1. What the region is

The region host runs three units. `fdf-ram-<AREA>` is the RAM manager, `rina-host` on TCP 51888 at `10.253.253.254`, **inside the hub's tunnel**: the region's memory, where every member holds a session. `fdf-hub` is the WireGuard hub, UDP 50889, on the interface `wg-<AREA>`. `fdf-client-<AREA>` is the hub's own membership. `fdf-region-create AREA PUBLIC_IP` makes all three (`opt/fdf/bin/fdf-region-create`: the RAM manager's unit is `ExecStart=rina-host --listen $HUB_ADDR:$RAM_PORT`, after `fdf-hub --iface-only` has brought the interface up; `[RAM_IN_THE_TUNNEL_V1]`).

The region keeps one record of its own per area: the area's **site key**, written by `map_site` and kept permanently. Everything else lives as long as a member's session. The session is the enrolment and the GUID in `/etc/fnid` is the identity. A member announces its facts through its session — name, GUID, /24, WireGuard public key — and it is alive while its session is up. The mappings that tell the hub which /24s a member serves are registered over the same session, signed with the site key, and they go when the session ends. A member that stops leaves the region with its session.

A node learns its region from one file, `/opt/fdf/var/join.env` (`RINA_RAM=10.253.253.254:51888`). With it the tunnel daemon runs in region mode; without it the node is LAN-only. There is no third mode (`internet_tunnels_v3/config.py`; a leftover `tunnel.conf` is named in one log line and ignored).

## 2. Where it lives

The region host has one hard requirement: every member MUST reach it on UDP 50889, the hub's WireGuard port. The RAM manager listens only inside the hub's tunnel. Members open the tunnel; the region host never connects to a member. A member's first packet to the Internet is a WireGuard handshake to the hub's UDP port; the region's records, facts, mappings and held reads all travel inside the tunnel.

For members at different sites, behind home routers and NAT, that means a public address. For a region with no internet, any LAN address every member can reach will do. The region host is not a FrogNet node: it needs `/opt/fdf` (from `fdf-droplet.tgz`), `python3`, `wireguard-tools`, and `wireguard-go` where its kernel has no WireGuard module. One machine holds one region (a second `fdf-region-create` stops; `--replace` re-makes one an earlier build made).

`fdf-region-create` prints the firewall lines, once: `ufw default deny incoming`, `ufw allow 50889/udp`, `ufw route allow in on wg-AREA out on wg-AREA`, `ufw allow in on wg-AREA to 10.253.253.254 port 51888 proto tcp` (lines 160–167). With them, the region host answers the Internet on the hub's UDP port and nothing else of the region's (`[NOTHING_LISTENS_OUTSIDE_V1]`; `fdf-hub`'s journal names whatever else the machine listens on). Allow ssh first if the machine is administered over the Internet.

## 3. Admission is an invitation

Members are admitted one at a time, on the region host: `fdf-invite NAME` makes NAME's WireGuard key, gives it an edge address on the tunnel (the first free `10.253.X.Y`, X 1–252, Y 1–254: 64,008 per region, `fdf-invite` `free_edge`), records it in `/opt/fdf/hub/admitted/NAME.json` (the hub adds the peer at once) and prints the invitation: one line, `fdfinv1.<...>`, shown once. It is a secret: it carries NAME's private key and the site key (`[ADMITTED_BY_INVITE_V1]`).

The node joins with that line: `frognet_install.sh --from-repo ... --invite fdfinv1.<...>` at install, or `fdf-install-$(uname -m).run fdfinv1.<...>` on a node already installed LAN-only. `--region`, `--site-key` and `--area` are refused (`frognet_install.sh:199-200`). `fdf-region-join` writes the invitation's key to `/var/lib/frognet-tunnel/node_private.key`, `site.env`, `hub.json` and `join.env`, starts `fdf-client` and `fdf-facts`, and restarts the tunnel daemon into region mode. A new invitation brings a new key; `fdf-invite NAME --revoke` removes a member's peer now; `fdf-invite --list` shows who is admitted; `--leaf` admits a phone or PC.

There is no pond password, no passcode and no group token. The node's `fdf-client` signs each mapping it registers with the site key; the RAM manager refuses one that does not verify.

## 4. The hub is derived from the records

The hub is derived from the region's records and its invitations alone. It has one peer per member admitted with an invitation, and it gives a peer a /24 only from a verified mapping that names this hub (`fdf-hub:192-207`: a member whose key was never invited gets no peer, and the hub logs `NOT ADMITTED`). A /24 has one owner in a region: the first live member to claim it; a later claimant is refused and the hub logs `REFUSED <subnet> claimed by ...`. Nothing is configured per member but its invitation, and the hub polls nothing: each change in the region is one event (`fdf-events`, one held session) and each event is one reconcile.

The hub stores none of the traffic it carries. Traffic between members at different sites crosses the hub: each member's tunnel ends there, the hub decrypts each packet, forwards it in the kernel and encrypts it onto the far member's tunnel. Inter-site traffic is therefore in the clear inside the hub host. On one LAN, members talk directly and the hub never sees a byte.

## 5. Loss of the region

When the region cannot be reached, a member does not wait on it. Its tunnel daemon withdraws every route and name the region gave it, tears its tunnel down, and asks again every 5 seconds; when the region answers, the daemon reads it again and brings the tunnel back. The node keeps serving its own /24 the whole time. If the region host goes down, every cross-site link goes with it, because they all end there. Recovery after a region-host network outage is a gate (`docs/cleanup-2026-10/`, region-loop test); permanent destruction and replacement of the region host is not recorded as a test.

## 6. Tunnel liveness

These rules came from the broker era and survive unchanged in the region tunnel daemon (`internet_tunnels_v3/poll.py`): a handshake is live for 180 s (`HANDSHAKE_LIVE_SEC`, line 284); a control-plane blip does not tear down a recently handshaken tunnel (`[TRANSIENT_GUARD_V1]`, 287–300); `PersistentKeepalive` is 25 s (550); a stale handshake is refreshed in place before it is torn down (`[STALE_HANDSHAKE_PURGE_V2]`, 766, 929).

## 7. Trust

The region host is a trust boundary. It holds the site key, every member's public key and edge address, and decrypts inter-site traffic in transit. It holds no member's private key after the invitation is handed over, and no application data: the RAM manager's records are the region's membership, facts and mappings. The RAM manager does no authorization beyond the site-key check on mappings; what keeps others out is that only the hub's tunnel reaches it, and only an invited key gets a peer. See §12.

## 8. What the broker was

Until 12 October 2026 the inter-site path was a broker: a service members enrolled with by passcode, which built a full mesh of pairwise tunnels and allocated a carrier block per tunnel, which is where the old ceiling of about 58 nodes per pond (16 on a shared broker) came from. The broker, its enrolment scripts, `pond.conf`, choruses and `MAX_TUNNELS` are gone (`usr/local/bin/frognet_prune_removed.sh:88-213`; `docs/cleanup-2026-10/static-no-broker.sh`, 0 violations). The region allocates per member, not per pair.

## 9. References

Specification [§7](README.md#s7), [§12](spec-12-security.md), [§13](spec-13-failure.md), [§17.4](spec-17-limits.md#k4). Source: `opt/fdf/bin/fdf-region-create`, `fdf-invite`, `fdf-region-join`, `fdf-hub`; `opt/frognet_semantic/internet_tunnels_v3/` — `region.py`, `poll.py`, `config.py`. Operator guide: `docs/OPERATING.md` §3. Build manual: Croakus [§19](https://fawcettinnovations.com/magnum-croakus.html#19-the-region), [§20](https://fawcettinnovations.com/magnum-croakus.html#20-standing-up-a-region), [§21](https://fawcettinnovations.com/magnum-croakus.html#21-the-tunnel), [§22](https://fawcettinnovations.com/magnum-croakus.html#22-one-overlay-many-sites), Appendix K (Known Limits).

[← §6 Pond Formation](spec-06-pond.md) [Index](README.md) [§8 Service Discovery and Election →](spec-08-election.md)
