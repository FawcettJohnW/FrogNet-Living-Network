# PLAN: FrogNet discovery and tunnels on FD&F (2026-10-09)

STATUS 2026-10-09: Stages 0-3 built and proven in the sandbox (see HANDOFF-REGION-2026-10-09.md). Hardware next.

Goal: a FrogNet site joins an FD&F region and gets everything the broker and the
getHosts crawl give it today from the region instead. Data still rides WireGuard,
hub-and-spoke through the RAM manager's host. Control plane = tuples, held reads.
Nothing polls, nothing heartbeats, nothing goes node to node that isn't 10/8 data.

Repo paths (FrogNet-Living-Network, commit 783671f):
- discovery: `opt/frognet_semantic/discovery/` (descend.py, discovery.py, real_backends.py, propagate.py, routes.py)
- tunnel daemon: `opt/frognet_semantic/internet_tunnels_v3/` (poll.py, peer.py, wg.py, broker.py)
- broker: `opt/frognet_semantic/broker/opt/frognet_broker_v4/` (frognet_broker_v4.py, broker_namespace_v4.py)
- FD&F: `opt/fdf/`

## Stage 0: prerequisites and the tuple layout

0.1 ARM build of `fdf-client` / `rina-host` (FrogNetHost and the Pi nodes are arm64).
    Without it no FrogNet node can be a region member. Cross-compile from
    `opt/fdf/src/fdf-rinab-src.tgz` the way `tools/build_static_host.sh` does for x86.
0.2 Region = pond. Area password = pond password. One region per FrogNet network.
0.3 Records. Each of the three does one job, and the schema says which:
    RINA names and structures (who is in which DIF, what sits on what), LISP maps
    (which locator reaches which prefix), Ribbit holds it and wakes readers.
    One row per writer, `own=true`, so the session end removes it.

    RINA (existing FD&F record keys, not new ones):
    - region = a DIF (the pond). A FrogNet site's LAN is a LOWER DIF (its own
      databasehost RAM, FrogNet unchanged). The region DIF is supported by the LAN DIFs
      plus the Internet. "Generation" = that nesting: site = gen 0, region = gen 1,
      a region of regions = gen 2 (that is the cross-region attach; see Open).
    - `ipcp|<machine>|<ipcp>` -> the node's IPCP in the region DIF, extended with the
      FrogNet facts: {name, guid, subnet, pubkey, transit:[/24...], chorus:[...],
      hosts:[[ip,name]...] (ONLY non-member hosts behind this node), echo:{id1,upstream},
      role: node|hub|leaf, since}. Replaces /register, /update-subnets, getHosts.php,
      frognet_echo.php. Written by the node's fdf-client (0.6). A hub is an IPCP with
      role=hub {pubkey, endpoint, addr 10.253.253.254}; a leaf is role=leaf
      {addr /32, pubkey}.
    - `nreg|<LAN DIF>|<ipcp>` -> the site's region IPCP registered in its own LAN DIF.
      This is how a LAN client (gen 0) knows which node carries it into the region.
    - `adj|<region>|<node>|<hub>` -> a live tunnel (written by the node when the
      handshake is up, removed when it is not). Adjacency = peering, nothing else.
    - enrollment = the session. Join/leave is RINA enrollment, not a FrogNet event.

    LISP (mapping system in Ribbit, the Ribbit-LISP conversion's rows):
    - `map|<eid-prefix>` -> {rlocs:[{hub, prio, weight}], ttl, gen} for each node /24
      and transit /24. EID = the FrogNet /24; RLOC = a hub that reaches it. The node
      map-registers its prefix with the locator set = every hub it has an adj with,
      prio from its measured RTT to that hub. Peers map-request (one read) and get
      the ordered set; routes.py still ranks what it measures.
    - map-notify: on readdress or hub change the node re-registers and holders of
      the old mapping are told (held read on the row). This is the fix for exp4.
    - the hub is an RTR (re-encapsulating tunnel router) in LISP terms; WireGuard is
      the encapsulation. That is the one-line answer to "what is LISP for".

    FrogNet-specific (neither RINA nor LISP has it):
    - `fn.view.<guid>` -> {gen, dbhost, hosts_hash, phase: steady|recalc, reason}
      Each node's own computed view of the world after a merge. Mirrored locally in
      /var/lib/frognet/view.json (the split side has no region to write to).
      "Merge with connectivity issues in progress" is DEFINED, not declared: any two
      live rows disagree on (dbhost, hosts_hash), or any row says recalc. Anyone can
      compute it; nobody owns it. Applications hold a read on it (local or region):
      that is the failover doctrine's "your datastore changed, rediscover".

    Ribbit: all of the above are rows; every "tell everyone" is a write and every
    "wait for" is a held read. No new mechanism anywhere in this plan.
0.4 Addresses, deterministic (no allocator): node subnet 10.A.B.0/24 -> tunnel edge
    address 10.253.A.B/16 on the region's wg iface. Hub = 10.253.253.254/16 (A=253 is
    reserved, so no node maps onto it; 10.253.255.254 would collide with a node at
    10.255.254.0/24). 10.253/16 and 10.254/16 stay reserved as in register_node().
    Chorus subnets (10.254) stay as they are for now.
0.5 Ownership invariant: one owner per /24 per region. The hub agent accepts the first
    ipcp row that claims a subnet (lowest row id) and refuses later claimants,
    logged with both guids. Same rule for the edge address, which follows from it.
    (Today: _validate_subnet / _transit_conflict in the broker.)
0.6 The node's fdf-client owns ipcp|<machine>|<ipcp> (own=true: the session is membership
    and withdrawal). Because the row dies with its writer's session, runMerge cannot
    write it directly: the client gets a local control path (a file it watches under
    /opt/fdf/var, or a unix socket) and rewrites the row through its own session when
    hosts/transit change after commit.

## Stage 1: hub agent (`fdf-hub`), on the droplet

Replaces frognet_broker_v4 for the region. A few hundred lines of Python.
1.1 One wg iface per region: `wg-<area>` with the hub keypair, addr 10.253.253.254/16,
    ListenPort fixed, in a netns per region if isolation is wanted (keep
    `broker_namespace_v4.ensure_namespace/create_wg_interface/add_wg_peer/add_route`;
    drop everything pairwise and the port-forward-per-tunnel code).
1.2 Hold a read on ipcp|* rows (role node, later leaf) and map|*. On a new/changed row:
    `wg set wg-<area> peer <pubkey> allowed-ips <subnet>,<transit...>,10.253.A.B/32`
    and `ip route add <subnet> via 10.253.A.B dev wg-<area>` (per transit /24 too).
    On row removal: PARK the peer (allowed-ips cleared, session kept) and delete its routes; delete a
    parked peer only when WireGuard's last-handshake age exceeds 180 s ([HUB_PARK_V1], 2026-10-09: deleting
    and re-adding gave a fresh peer with no session and a blackhole until rekey). ip_forward on.
    AllowedIPs policy (WireGuard moves an overlapping prefix to the LAST peer that
    claims it, silently): the hub never lets last-write decide. Owned subnet always
    wins and is never displaced by a transit claim; a transit /24 goes to the first
    claimant and only while nobody owns it; a later conflicting claim is logged and
    ignored; when the holder's row goes, the next claimant (lowest row id) gets it.
    The hub keeps its own prefix->peer table and applies `wg set` from that, never
    from a row in isolation.
1.3 Publish the hub's ipcp row (role=hub) once at start (own=true).
1.4 Test: two x86 boxes with hand-written ipcp rows and hand-configured wg;
    ping 10.A.B.1 across. Record: time from row write to peer present, from session
    end to peer gone.

1.5 Adopt on start: the agent reads existing wg peers/routes from the kernel and
    reconciles them against the rows (like today's
    _rebuild_active_tunnels_from_kernel_and_broker); it never tears down and
    rebuilds. A RAM-manager restart is still a region-wide event (every session ends,
    every site splits to LAN and rejoins); the hub side just doesn't add to it.
    Plan droplet upgrades; say so in public.
## Stage 2: node side, tunnel daemon on the region

Keep peer.py / wg.py / iproute.py. Replace the broker.
2.1 New `region.py` in internet_tunnels_v3 implementing the three calls the rest of
    the daemon consumes, from tuples:
    - register        -> write ipcp|<machine>|<ipcp> (or confirm fdf-client wrote it)
    - my-channels     -> for each hub ipcp row: ONE channel {wg_config: edge_ip=10.253.A.B,
                         edge_mask=16, droplet_pubkey=hub.pubkey,
                         droplet_endpoint=hub.endpoint, allowed_ips=10.0.0.0/8,
                         remote_subnets = the EID prefixes whose map|* locator set
                         includes this hub}
    - transit-map     -> map|* rows whose prefix is a transit /24
    - map-register    -> after each tunnel comes up: write/refresh map|<my /24> and
                         map|<each transit /24> with rlocs = my live adj hubs, prio
                         from measured RTT to each hub
2.2 `poll_once()` -> held read on ipcp|* / hub ipcp rows after last id; fire runMerge on
    return. POLL_INTERVAL_SEC goes away. `[BROKER_AUTHORITATIVE_V1]` becomes: no
    session = tear down the region's tunnel.
2.3 `_bring_up_tunnel` unchanged except edge_mask 16 and one tunnel per hub.
2.4 Oracle: `test_register_on_404_oracle.py` has the shape; add a fake region backend
    and port it. Keep `test_transit_*`.
2.5 Test: Stage-1 hub + two real nodes running the daemon; `wg show`, routes to each
    other's /24 via the hub, runMerge fires on join/leave without polling.
2.6 Loss handling (gain needs nothing: the incumbent dbhost stays valid while the
    union is computed; loss is different because the dbhost may be what left).
    - Member leaves region: its rows go on session end; everyone's held read returns;
      runMerge. No timer.
    - I lose the region: my FNWP socket dies -> write view.json phase=recalc
      reason=lost:region IMMEDIATELY (before runMerge) -> runMerge computes host set
      and dbhost from what is still reachable, which is the LAN ONLY: the region's
      tunnel is torn down on session loss (no session, no authority to hold it), and
      it is dead on the far end anyway, because every hub agent saw my row vanish
      and removed my peer at the same moment. The small side's world is its LAN.
      -> view.json phase=steady gen+1 -> apps rediscover.
    - Silent loss (idle socket, path gone): RULED 2026-10-09, 20 seconds. Kernel TCP
      keepalive on the FNWP socket, both ends: SO_KEEPALIVE, TCP_KEEPIDLE=5,
      TCP_KEEPINTVL=5, TCP_KEEPCNT=3 => dead at 5 + 3*5 = 20 s. Transport liveness,
      same class as WireGuard's keepalive, not an application heartbeat. Set in
      rina-host's accept path and in fdf-client / the daemon's connect path.
    - Election unchanged: highest .1 of the live host set, computed per side; on
      re-join both sides see the union and agree.
    - Keepalive probes only when the socket is silent (TCP_KEEPIDLE is idle time;
      traffic resets it). A socket carrying data never probes. 20 s bounds the
      silent case only.
    - Application notification is on CHANGE of dbhost in the view, not on the loss
      event: the app holds a read on fn.view.<guid> (region) or view.json (local,
      inotify) and is notified on every write where dbhost differs from the last it
      saw, whenever that happens (socket death, the 20 s verdict, end of a merge
      minutes later, a rejoin). The app never elects and never watches a clock.
    - Test: pull the uplink on one node; measure socket-death -> recalc flag -> new
      dbhost -> steady, on both sides; then restore and measure the merge. Record
      every timestamp. Compare with today's poll + _NODE_STALE_AFTER_SEC + 180 s
      handshake age.

## Stage 3: discovery gather from the region

Backend swap only; descend.py's probe/rank/commit untouched.
3.1 `real_backends.py`: add `RegionGetHosts.get_hosts(host_path, pip)` -> that node's
    the ipcp row's hosts; `RegionEcho.echo_probe(pip)` -> "name,id1,upstream" from the ipcp row;
    `RegionPropagator.add_host_and_propagate()` -> update own ipcp row's hosts (one write;
    everyone's held read sees it). Select with `FROGNET_DISCOVERY_SOURCE=region`.
3.2 Seeds: with get_hosts answered from the region, `_crawl_to_fixpoint` reaches the
    whole member set in one level. Leave `max_levels=2`; it just stops early.
    `tunnel_immediates` now yields one immediate per remote /24 on the one wg dev;
    descend handles that already (tunnel root, via "").
3.3 Keep: `_measure`, `_probe_through`, RealReflect (:18432), RealVerify (:9009),
    routes.py, kernel.py, live.py. These are path measurements and the planner.
3.4 Oracles: fake region backend for the harness; rerun selfcheck. The ones that
    exist to prove crawl depth / runAgain convergence (`test_descend_depth_oracle`,
    `test_runagain_convergence_oracle`, `test_walk_oracle`, `test_propagate_reach_oracle`)
    get a region-mode control or are marked not-applicable in region mode.
3.5 Test on hardware: three nodes, one hub. /etc/hosts identical on all three after
    one merge; routes via hub; LAN path still wins where one exists (RTT).
    Record: merge wall time region vs. crawl, candidates, packets on join.
3.6 Acceptance: published != reachable. A node whose row is present but whose tunnel
    is down (hub peer removed by hand, or wg stopped on the node) is a candidate whose
    probe FAILS (PROBE_FAIL said), gets NO route, and -- HOSTS_GATE, the engine's own
    rule -- no name either: a name without a route is a lie to dnsmasq's clients.
    Restore the tunnel; the next merge commits it and names it. (Amended 2026-10-09
    after the region oracle: the engine already had the right rule.)

## Stage 4: leaf (non-FrogNet machine with IP reachability)

4.1 `fdf-leaf <region> <hub>`: generate wg key, pick addr (10.A.B.z/32 from a leaf
    block, say 10.252.x.y), write leaf ipcp row (role=leaf), bring up wg to the hub.
    Windows: same with wg.exe.
4.2 Hub agent adds leaf peers like nodes (Stage 1.2).
4.3 Node side: a leaf has no proxy, so no reflect/:9009 answer. Emit its route from
    the hub fact as `kind=vouch` (promote doesn't verify-backout vouch), via the hub
    tunnel. One emit in descend from leaf ipcp rows.
4.4 Names on the leaf: fdf-leaf holds a read on ipcp|* and writes the same lines
    hosts.py writes (`<.1> <name> FrogNetHost.<name>`, `<.2> FrogNetAdmin.<name>`)
    into the leaf's hosts file (Linux /etc/hosts block; Windows
    System32\drivers\etc\hosts), refreshed on every change. The ipcp row carries `name`
    (the FrogNet node name / domain); FrogNetHost.<name> is derived, as in
    derive_domain(). The constant hostname "FrogNetHost" is never carried.
4.5 Test: laptop behind NAT ssh's to `FrogNetHost.<name>` of a Pi.

## Stage 5: several hubs = LISP locator sets

Each hub writes its ipcp row (role=hub); a node brings up one tunnel per hub and
writes one adj row per live tunnel; its map|<prefix> rows carry every live hub as an
RLOC with prio from measured RTT. A peer's map-request (one read of map|<prefix>)
returns the ordered locator set; routes.py measures each and ranks with incumbency
hold as today. Each hub's reachability is its own: a member may be peered on one hub
and not the other, and its locator set says exactly which.
map-notify: when a node's locator set changes (hub lost, readdress, rejoin) it
re-registers; every holder's held read on that map row returns; they re-merge.
This is the exp4 fix and its acceptance test: readdress a node; no peer keeps a
stale locator past one merge.
Code: the lispers map-register / map-request / map-notify logic from the Ribbit-LISP
conversion, on the node (register, request) and nowhere else; the hub only reads.
Test, three nodes, two hubs: (a) hub loss: stop fdf-hub A; routes move to B within
one merge, packets flow; restart A, incumbency hold keeps B unless A measures at or
below half its RTT. (b) member loss: node X ends its session; X's peer gone on both
hubs, X's routes pruned on the other two. (c) recovery: X rejoins; routes back.
(d) readdress: X changes its /24; every peer's route follows within one merge.
Check `ip route` AND delivery (ping-pong :9009 over the installed route) at each step.

## Stage 6: cut-over

Stop frognet-broker-v4 on the droplet for the region's pond; leave LAN FrogNet
untouched. Keep broker v4 only for ponds not on a region.

## Where each of the three does its job (check)

- RINA: the region is a DIF; a site LAN is a lower DIF; a node is an IPCP in both
  (nreg); a tunnel is an adjacency; join/leave is enrollment (the session);
  nesting is how regions attach to regions. Nothing in FrogNet's structure is
  described by anything else.
- LISP: every /24 is an EID prefix with a locator set of hubs; nodes map-register,
  peers map-request, changes map-notify; the hub is an RTR. Nothing about "which
  hub reaches which prefix" is described by anything else.
- Ribbit: every record above is a row owned by its writer; every notification is a
  write; every wait is a held read; no polling, no heartbeats, no second channel.
- FrogNet keeps what neither names nor maps: path measurement (reflect, :9009),
  route ranking (routes.py), kernel commit, the LAN, the election, and fn.view.
- Not used: LISP's own data plane (WireGuard encapsulates), RINA's flow allocator
  (10/8 is the data plane here, not DIF flows). Both are deliberate; say so.

## Security of the mapping system; restricted DIFs

Rule kept: the RAM manager does no authorization. Security lives in enrollment (RINA),
in signed registration (LISP), and in which memory a record lives in (Ribbit). Six
distinct contracts, kept distinct:

1. DIF enrollment and RAM visibility. A RESTRICTED DIF is its own RAM manager with its
   own area password ("each DIF is a shared RAM"). Its ipcp/adj/map rows live there.
   Enrollment = the HMAC-signed registration the RAM manager already checks. You see
   a DIF's records only if you are enrolled. This bounds what can be SEEN, not what
   can be reached.
2. Authenticated Map-Register, verified Map-Notify. HMAC-SHA-256-128 with the DIF's
   site key, as lisp_area.hpp does (alg 2, auth zeroed, verified on receipt). This
   proves "an enrolled member registered this", NOT "this member owns this prefix":
   the site key is shared by every enrolled member.
   IMPLEMENTATION CONTRACT: map|<prefix> rows are produced ONLY by the region's
   map_register op (src/rina_region.cpp:447, which verifies the HMAC before it
   writes), extended to carry a prefix and a locator set. A map|* row written any
   other way (ram tool, HTTP write) is not a mapping; the hub and region.py ignore
   rows that did not come through that op (the op stamps them; a raw write cannot).
3. Prefix ownership and AllowedIPs. The one-owner-per-/24 rule (0.5) is the
   authorization; 2 only authenticates. The hub installs AllowedIPs from verified
   mappings, in DIFs it is enrolled in, subject to 0.5 and the 1.2 policy.
4. Selective nreg publication. A restricted DIF nreg's into a higher DIF only what it
   chooses: its hub as an IPCP and the prefixes it accepts traffic for. Nothing else
   of its state is copied upward; the higher DIF holds registrations, not a mirror.
5. Forwarding limited to what was exposed. Visibility (1) and reachability are
   different boundaries. The hub that advertises a restricted prefix upward must
   forward ONLY the prefixes it exposed: AllowedIPs for the upward peer, and the
   hub's route table for that DIF, contain nothing else. A sibling region must not
   be able to reach more than the restricted DIF published. Test it.
6. Bootstrap in the clear (area name, where the RAM manager is): deferred, ruling
   stands.

## Relationships between DIFs (correction, 2026-10-09)

Two different relationships, both allowed, never conflated:
- Nesting: a higher DIF supported by lower ones (site LAN -> region -> region of
  regions). Reachability flows through the hierarchy: a node enrolled in its region
  reaches a sibling region's services THROUGH the higher DIF. Access through, not
  membership in.
- Multiple membership: a machine or a REGION enrolled in several DIFs that are not
  nested. A region may sit under more than one higher DIF; hierarchies overlap, there
  is no single tree. nreg is many-to-many; a map|<prefix> RLOC may be another
  region's hub. Nothing in the schema or the implementation may assume a tree.
Enrollment sets a machine's initial scope (its primary region); nesting and the
mappings decide what else is reachable from there. A node enrolls only in its primary
region. Cross-region reachability is a Stage 7, after Stage 5 holds; not demonstrated.

## Tests and validation that change

- Discovery oracles (81): engine unchanged; harness gets a fake region backend next
  to the fake getHosts/echo. Crawl-mechanics oracles get a region-mode control or are
  marked crawl-only: test_descend_depth, test_runagain_convergence, test_walk,
  test_propagate_reach. New: row present + tunnel down => candidate, not committed
  (3.6). Every new oracle gets the case that makes it fail.
- Tunnel daemon: test_register_on_404_oracle is replaced (no 404, no session) by:
  session lost => tunnel down + view.json recalc; session back => row rewritten =>
  tunnel up. test_transit_* keep their logic, transit read from rows.
- Broker oracles go with the broker. fdf-hub gets: ownership invariant (second
  claimant refused; holder leaves => next lowest row gets it), AllowedIPs never moved
  by last-write, peer gone on session end, adopt-on-start, two hubs with different
  member sets (Stage 5 test).
- FD&F experiments: exp1 gains "FrogNet node join incl. hub peer + routes on others";
  exp4 (stale locator) untouched and still failing; add the 2.6 loss experiment and
  the 3.5 crawl-vs-region merge comparison.
- Unchanged: routes.py oracles, kernel parity, hosts/resolv, FrogSim, rlite 30/30,
  Communicator and kart runs. The LAN half of FrogNet is not in this plan.

## Operational notes

- fdf-client is now what says a node is alive. A client crash on a healthy network
  is a false leave (everyone re-merges twice). Supervise it like the tunnel daemon;
  count its restarts in the measurements; a restart that shows up as churn is a
  client bug, never a reason to add a timer.
- Idle members cost one kernel TCP keepalive per 5 s each way. Say it next to
  "nothing polls".
- Per-pair isolation on the droplet is gone (one interface per region). Linux
  handles it; it is no longer separated by construction.
- First-claim-wins on a /24 is an ownership invariant, not a security property
  (weapon-and-foot ruling; same exposure as the broker today).

## Measurements to publish

- join: packets/bytes/ms from fdf-client connect to route installed on peers
- leave: ms from session end to peer removed on hub and route pruned on peers
- droplet: wg interfaces and ports with N nodes (broker: 2*N(N-1)/2; hub: 1)
- merge: wall time, gather calls (crawl: O(hosts) HTTP; region: 1 read)
- lines: broker v4 3,375 + broker.py 55 + crawl/propagate paths vs. fdf-hub + region.py
  (region.py includes the lispers map-register/request/notify it reuses; count it)

## Rules this plan keeps

- No polling, no heartbeats: held reads; liveness is the session. (WireGuard's
  PersistentKeepalive=25 is the tunnel's own NAT keepalive, not FrogNet's.)
- No data member to member through FD&F; 10/8 data goes over wg to the hub's host.
  FrogNet LAN paths stay direct; that's FrogNet's transport, say so in public.
- No fallbacks: a .2 that doesn't reflect is a PROBE_FAIL, not a retry.
- Nothing in /usr/local/bin that isn't already there; node commands untar from /tmp.
- Security deferred (bootstrap in the clear) per the demo ruling.

## Open

- (Decided, 0.6) fdf-client owns the ipcp row; runMerge updates it through the client's
  local control path.
- Chorus addressing (10.254 pools) untouched here; revisit when Chorus moves to tuples.
- Cross-region attach: see "Relationships between DIFs". One region for now.
