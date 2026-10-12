# PLAN-BRIDGE — an intelligent bridge between networks that share nothing in advance

2026-10-11, Android thread, on fdf-2026-10-10-r19-a13. John: "multiple external islands can all have the same internal
IP addresses and the client will handle the translation properly, using the w: scheme"; "it removes any requirement for
pre-configured network addresses"; "an actual network bridge with intelligence"; "LISP is the glue".

## 1. The problem

A FrogNet member can already reach another network's shared memory: its RAM manager has a reachable address, and a
session to it gives presence, tuples and notices. What it could not do is route **packets** into that network. The
networks behind other RAM managers are private (10.x like ours), assigned by whoever set them up. Two islands that
never planned together will collide: the same /24, the same leaf /32 (every pond's first phone is 10.252.0.1), the
same hub address. Routing one would break the other.

Three things were coupled that should not be:

| | what it is | where it lives |
|---|---|---|
| **identity** | which machine or service you want | the tuple: `w:<guid>:<port>` |
| **locator** | how that machine is reached from *your* scope | resolved by the reader, per scope |
| **translation** | how an overlapping address space is accommodated | at the boundary, on the reaching machine |

## 2. The design

### 2.1 Identities in tuples, never addresses — `[IDENTITY_IN_TUPLES_V1]` (built, a12)

A service says where it runs by identity: `(pond, where, w:<guid>)` `{"comms":{"ram":"w:<guid>:8800","media":"w:<guid>:8994"}}`
(`fdf-where comms ram=self:8800`). The reader resolves the identity **in the pond it came from** (the region's
`resolve` op: a node's .1, a leaf's or reacher's /32; ENOENT when gone), then **through its own alias table** when that
pond's addresses collide with its own. The application asks for `w:<guid>:<port>` and gets something it can connect to.
An address in a tuple means one machine only in one scope; an identity means the same machine everywhere.

### 2.2 Each network says what it allows — `[NET_CONFIG_IS_A_TUPLE_V1]` (built, a12)

`(pond, config, net)`, written by the RAM manager from `/opt/fdf/etc/net.json`, read-only to members:
`reach` none | listed | all, `map` allowed | refused, plus its overlay ranges. No file: reach none. Nothing is
negotiated out of band; the far network's own memory decides.

### 2.3 The boundary on the reaching machine — `[REACH_V1]` (built for Linux nodes, a12–a13)

For each island it reaches, the machine:
- is given **an address there** by that island's region (as a leaf is), peered by its hub, routed by its nodes;
- holds a **private address here** instead (100.127.255.x) — the given one is never local, so it can't shadow a
  forward-pond address that happens to be equal (`[REACH_SELF_ALIAS_V1]`);
- maps every destination that **collides** with what it already routes or reaches onto a **1:1 alias block** in
  100.64.0.0/10 (`.N` stays `.N`), keyed per island, stable across re-plans; non-colliding destinations are used as they are;
- does all of it on **its own** interface, table, rules and chains — the forward pond's routing is never touched;
- re-plans on every event of that island and whenever its own routes change; one lock serialises all of it.

Rules (John): only what the island's config allows; map only if it says so; **a failed reach closes that session**;
no fallbacks; gone is gone (session ends → reach, aliases, routes go).

### 2.4 Where LISP is the glue

- **Finding networks:** the mapping region holds each area's name → its RAM manager's locator (signed MAP_REGISTER;
  the map-resolver's address derived from the name; members waiting on a mapping are told when it moves).
- **What hubs carry:** every served /24 is a signed EID-prefix mapping with a locator set; a hub installs only verified
  mappings; a second live claimant is refused.
- **The model:** the identity/locator split itself. Members are resolved by their own pond's region, not the mapping
  region — each pond stays authoritative for its own membership and addressing.

Not LISP: the translation (netfilter + policy routing on nodes; a userspace rewrite on the phone, §3.1).

## 3. What is next

### 3.1 The phone as a bridge — **BUILT (r19-a14 / fdf-kt a11, 2026-10-11), netns-proven; emulator and handset next**

As built (details in MERGE-r19-a14.md and §6):
- **fdf-tun** (`frognet-android/tun/`, Go, wireguard-go vendored): one TUN, one WireGuard device per scope, the 1:1
  rewrite with incremental checksums, ICMP errors' quoted headers mapped too. Not gomobile/NDK: a **static linux
  executable** shipped as `jniLibs/<abi>/libfdftun.so` and run from the app's native library dir; the VpnService TUN fd
  is passed to it over an abstract unix socket (SCM_RIGHTS, this app's uid only), and every socket its WireGuard opens
  comes back to be `protect()`ed before it binds. Controlled by one JSON `set` with the whole state.
- **The phone's TUN address never changes** (100.127.255.1); every pond's given address is put on the packets by
  fdf-tun. **On the phone all of 10.0.0.0/8 is the forward pond's** (`PHONE_SCOPE_IS_10_8_V1`), so every island
  destination is aliased and the VPN routes only 100.64.0.0/10 (+ 10/8 while the forward tunnel is up).
- **Kotlin** (`member/Bridge.kt`): `ReachPlanner` = fdf-reach's rules; `AliasLedger` (quarantine); `Bridge` (the whole
  state, decided and committed under one lock); `Reach` per participant pond; `BridgeVpn`/`AndroidBridgeHost` in the
  app; `fdf-member-kt bridge` runs the same code on a JVM for the netns proof.

Original plan, kept for the record:

Android gives no root, so no iptables; but the VPN interface hands every packet to the app in userspace. Plan:
- **Own tunnel library** in place of stock GoBackend: one Android TUN (Android allows one VPN at a time), **one
  wireguard-go device per island** (two islands with the same 10.7.7.0/24 cannot be two peers of one WireGuard device:
  allowed-ips would overlap), and a demux in Go: a packet to an alias block → that island's device, its destination
  rewritten alias→real and its source rewritten private→given; the reverse on receive; IP/TCP/UDP checksums fixed.
  The same 1:1 rule as NETMAP, in Go. Built with gomobile/NDK into the app.
- **Kotlin:** the member asks an island for reach (`"reach":true` + pubkey; the region gives the /32, the hub peers it),
  plans as fdf-reach does (config, collisions with the forward pond and other reaches, aliases), feeds the tunnel
  library, and resolves `w:` endpoints through the alias table (`Member.endpoint` gains the island).
- **Same rules:** refused = that pond's session ends; gone is gone.
- **Proof:** the Kotlin member against the same netns islands as `netns-reach-test.sh` (A, B and F all 10.7.7.0/24;
  equal 10.252.0.1s), then the emulator app, then a handset.
- **What it gives:** concurrent traffic into several private networks from the device that moves between them — e.g. a
  call through the home pond's media server while watching a feed from a node in another pond — with no Bring forward
  and nothing configured on either side.

### 3.2 Transparent resolution for applications — **BUILT (r19-a14)**: `fdf-reach resolve w:<guid>:<port>` (node), `Bridge.resolve` / `MemberService.resolve` (phone). Every network the machine is in is asked; **exactly one may answer**, otherwise refused, naming each (`RESOLVE_AMBIGUOUS_REFUSED_V1`, exit 7).

Original plan:

Today an application on a node names the island: `fdf-reach resolve <area> w:<guid>:<port>`. Next: resolve an identity
without the area — ask the ponds this machine is in (forward first, then each reach) for the identity, use the one
that answers; a library call (C++, Python, Kotlin) and the CLI. The application then never knows whether it was reached
directly, through an alias, or through another transport.

### 3.3 Smaller

- `listed` reach exercised end to end in netns (planned and coded; not yet in a test).
- A dashboard tile per reach showing its aliases live (getPonds.php already carries them).
- Hardware: kernel WireGuard for `rch-*`, NETMAP on the Pi kernels, the dashboard on a node's Apache.

## 4. Not in scope / decided

- **Data-only access needs none of this:** a pond you only read is a RAM session to its RAM manager — no routes, no
  address there, no collision possible. The bridge is for packets to services inside the network.
- **No per-pond address index** (John 2026-10-10): every pond keeps 10.252/16 leaves and 10.253.253.254 hubs;
  collisions are mapped.
- The RAM manager itself must be reachable; everything behind it follows from what it publishes.

## 6. Rules added while building it (review 2026-10-11 and the tests)

- **Ambiguity is refused, never chosen.** An identity answered by two networks — reached here or not — is ambiguous:
  `resolve` refuses and names both; the caller names the network (`fdf-reach resolve <area> w:…`). A guid is meant to
  be unique; two answers mean a duplicate, a stale member or a spoof, and picking one would hide it.
- **Alias stability and quarantine** (`ALIAS_QUARANTINE_V1`). A block keeps its mapping across re-plans. Released (session
  lost, refused, removed, or dropped by a re-plan), it is remembered with its (pond, real prefix): the same pond reaching
  the same prefix gets it back whenever it returns; **any other use waits 600 s**, so a connection or a cached answer
  made through it cannot land in another network. Kept on disk (node `<var>/reach/.released`, phone
  `files/fdf-aliases.json`), so a restart does not forget it.
- **Covered is not a destination** (`COVERED_IS_NOT_A_DESTINATION_V1`): a /32 inside a served /24 is reached through
  the /24's alias (an alias of its own would answer from another address than the one connected to).
- **Planned and committed under one lock** (`PLAN_UNDER_THE_BRIDGE_LOCK_V1`): found in the netns log — B and F, planned
  at the same moment, both took 100.64.0.0/24. fdf-tun also refuses any set in which two islands overlap.
- **Carrier-grade NAT** (`CGNAT_IS_NOT_AN_ALIAS_V1`): a carrier's own 100.64.0.0/10 addresses (the device's, its
  gateway's, its DNS) are never an alias and, on Android 13+, are excluded from the VPN.
- **fdf-tun gone = everything gone**: a dead or broken fdf-tun drops every reach and the forward tunnel, and every
  session ends; the next ones decide again (`GONE_IS_GONE_V1`). No restart behind the member's back.

### 6.1 Limitation: addresses carried inside payloads

The rewrite is at the IP header (and an ICMP error's quoted header). **Addresses written inside application data are not
rewritten**: FTP `PORT`/`PASV`, SIP/SDP, RTSP, H.323, a web page or JSON body that embeds `http://10.7.7.5/…`, a peer
that tells another its own address. Through an alias such an address points into **this** machine's scope, not the
island's. This is by design, not an omission to fill with protocol helpers: FrogNet's own applications name endpoints by
identity (`w:<guid>:<port>`) and resolve them per scope, so they never carry a scope-bound address. Other applications
reached through an alias must not embed addresses — or be reached without aliasing (a non-colliding network, on a node).
The Communicator is safe: it gets its memory and media servers by identity, and its media flows over the session it opened.

## 5. Proven so far (netns, r19-a13)

`netns-reach-test.sh` 47/47: reach applied in ~400 ms; three networks with one /24 each reached (direct, alias B, alias
F) with replies to the right place; equal 10.252.0.1s in two ponds; re-plan when the forward pond changes (~850–940 ms);
B forward and back; two islands reached at once, each with its own alias and private side, one re-planned or removed
without touching the other; refusals (map refused, no config) close the session and leave nothing. Found and fixed on
the way: hub routes lost after a node's own session dropped (`APPLIED_IS_THE_KERNEL_V1`), the phone elected control
host (`ELECT_NODES_ONLY_V1`), and ten defects from an independent review (MERGE-r19-a12.md, MERGE-r19-a13.md).
