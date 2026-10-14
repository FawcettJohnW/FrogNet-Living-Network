history: superseded by docs/OPERATING.md

# Android member — progress record (Android thread, 2026-10-10)

## Decision taken: route 2 (Kotlin), raw-only FNWP subset

Read from the source, not the brief:

- `ServerEngine::finish` (fnwp_engine.hpp): a `REQ_RAW` is always answered `RESP_RAW` (or `OP_ERROR`). A client that never
  learns templates is a correct subset of the contract. No semcodec, semtpl, ramrows, SAME cache, pyuni tables.
- The data socket cannot be skipped: the server's ceiling starts at one segment (1,368 B) and only grows with measured
  rate, so any answer over ~1.3 KB (participants, world with a few members) comes as a marker on the return socket
  (`X-FrogNet-Data-Bytes` header, empty body) plus `[seq][total][offset][bytes]` records on the data socket.
- Requests may always go whole on the request socket (the server reads any frame up to MAX_FRAME); the client-side
  ceiling is the client's choice, so the Kotlin member never segments requests.
- Return-socket frames are `[seq:4][FNW1 reply]`; seq = the request's position on the request socket (server counts from 0).
- Session liveness = the request socket (ram_server.cpp `serve`: `session down` → `rina_session_down`).
- Route 1 (NDK) would carry fnwp_engine + semcodec + semtpl + pyuni tables + OpenSSL + lz4 cross-built for Android, behind
  JNI, to send a handful of ops. Not worth it.

Code: `member/` (pure Kotlin, no Android types, ~450 lines: Wire.kt, Json.kt, Session.kt, Member.kt), `member-cli/`
(the same code on a JVM, driven like fdf-client: RINA_RAM, FDF_FACTS watched, RINA_AREA, RINA_SITE_KEY).

## Proven against a real rina-host (built here from fdf-source-r16, x86, build fdf-2026-10-09-r16)

| check | result |
|---|---|
| session: HELLO / RETURN / DATA, server's HELLO back | `session up` + `data socket up` in host.log |
| node + announce as a leaf | participants: `w:kt-leaf-1 kt-leaf alive=True client=fdf-member-kt facts={role leaf, addr 10.252.0.8/32, pubkey}` — same shape as the C++ leaf beside it |
| version | `region at 127.0.0.1:8840 is build fdf-2026-10-09-r16` |
| data socket (participants/world 3.8 KB) | 600/600 answers via marker + segments; body == HTTP front's answer; server: `601 replies (2308434 B) on the data socket` |
| RTT, 300 calls, loopback | version p50 0.36 ms; participants (3.8 KB, data socket) p50 0.43 ms; world p50 0.38 ms |
| map_register signed (HMAC-SHA256, site key) | `mapping 10.61.61.0/24 -> hubA:1:50 registered`; map_list shows it |
| second claimant (C++ fdf-client) | `EEXIST (10.61.61.0/24 is held by a live session (node:w:kt-node-1); not transferred)` |
| wrong site key | `EPERM (authentication failed) = the site key is not the key map_site holds` |
| facts change → re-announce | `facts changed, re-announced`; participants shows the new pubkey |
| served /24 overlap | `JOIN REFUSED by the region: 10.70.70.0/24 is served by holder (alive)`; waits; joins 14 ms after the holder's session ended |

## Liveness, netns, side by side (`tools/netns-member-death-test.sh`, same method as rinab's netns-latency-test.sh)

| event | fdf-client (C++) | fdf-member-kt (Kotlin) |
|---|---|---|
| (1) clean death (kill -9) → first event on a watcher | `p` after 12 ms | `p` after 13 ms |
| (2) silent death (member's link down) → first event on a watcher | `p` after 19,306 ms | `p` after 19,162 ms |
| (3) silent region (member drops the region's packets) → member says its session ended | 19,318 ms | 19,288 ms (`Connection timed out`: its own keepalive) |

A leaf registers no mapping, so its death is a `p` (participant set), not an `m`. The brief's "`m` within 20 ms" is
the case of a member with mappings.

## The first experiment (TRANSITION-ANDROID §6), in namespaces: `tools/netns-leaf-tunnel-test.sh` — RESULT PASS 8/8

hub ns: rina-host + fdf-hub (wireguard-go: the same userspace WireGuard wireguard-android's GoBackend embeds) + the
hub's own fdf-client. kt ns: the Kotlin member as a leaf `{role leaf, addr 10.252.0.8/32, pubkey}`, its WireGuard peer
taken FROM THE REGION (the hub's announced facts: pubkey, endpoint) — nothing about the hub configured by hand
(`[LEAF_TUNNEL_FROM_REGION_V1]`). cpp ns: fdf-client as a second leaf 10.252.0.7.

| stage (from the Kotlin process start, JVM start included) | ms |
|---|---|
| joined the region | +315 |
| tunnel peer set from the hub's facts | +352 |
| hub has its peer (`wg show`: allowed-ips 10.252.0.8/32; route 10.252.0.8 dev wg-frognet) | +417 |
| first WireGuard handshake | +426 |
| kt killed → hub peer deleted (`deleted peer … (not a member now)`) | 43 |

Pings: kt → hub 10.253.253.254, hub → kt, kt → cpp leaf (through the hub), cpp → kt: all pass; kt → cpp rtt avg 1.2 ms.
After kt left: no peer on the hub, kt cannot reach cpp (gone is gone).

Harness note: wireguard-go's control socket is /var/run/wireguard/<iface>.sock on the shared filesystem; two namespaces
must not both name their interface wg0 (the second silently takes over the first's socket).

## [KEEPALIVE_5S_V1] (John 2026-10-10 03:55: "let's try taking the keepalive to 5 seconds")

Both ends: idle 2 s + 3 probes 1 s apart, TCP_USER_TIMEOUT 5,000 ms. C++: `tools/apply_keepalive_5s.py` on frogram.cpp
`dial` and ram_server.cpp `serve` (replaces the [KEEPALIVE_20S_V1] blocks). Kotlin: jvmTuner, AndroidTuner (the JVM has
no TCP_USER_TIMEOUT option; Android sets it, value 18).

Death, netns, side by side (`tools/netns-member-death-test.sh`):

| event | C++ 20 s | Kotlin 20 s | C++ 5 s | Kotlin 5 s |
|---|---|---|---|---|
| clean death → event | 12 ms | 13 ms | 21 ms | 20 ms |
| silent death → event | 19,306 ms | 19,162 ms | 3,889 ms | 3,860 ms |
| silent region → member says ended | 19,318 ms | 19,288 ms | 4,075 ms | 3,990 ms |

False deaths of idle sessions, random loss each way at the member (iptables statistic; this kernel has no netem, so loss
without delay), 3 C++ members × 300 s = 15 member-minutes per cell, both builds run at the same time:

| loss each way | 20 s build | 5 s build |
|---|---|---|
| 5% | 1 | 1 |
| 10% | 5 | 14 |
| 20% | 19 | 40 |

Each false death is a real leave: `p` everywhere, the hub deletes the peer, the member rejoins as a new member seconds later.

## a3 — the leaf decides from the region on every event (`[LEAF_FROM_THE_REGION_V1]`, `[REGION_EVENTS_ON_MY_SESSION_V1]`)

The other thread's r18 rule — nothing re-derives what the region holds, everything follows its events — applied to the
member. a2 read the region once per session (placement before the announce, the hub at join) and never again. a3: the
member's hold wait is also its event reader (watch_participants on its own `agent:` proc, one session, no second
reader); on every `p` it reads `world` once, re-places itself (re-announcing if the placement moved) and sets the tunnel
the placement and the hubs call for. `member/Leaf.kt` holds that logic; the app (GoBackend driver) and the JVM runner
(`wg` driver) run the same code.

`tools/netns-placement-test.sh`, one session throughout — RESULT PASS 15/15, three runs:

| event | run 1 | run 2 | run 3 |
|---|---|---|---|
| BlackBox leaves → hub has the phone's peer | 81 ms | 85 ms | 85 ms |
| BlackBox leaves → first WireGuard handshake | 138 ms | 129 ms | **5,129 ms** |
| BlackBox returns → hub has dropped the phone's peer (its fdf-client start included) | 102 ms | 93 ms | 96 ms |

Run 3 is a race, not noise: the phone sets its peer (persistent-keepalive sends a handshake at once) a few ms before
fdf-hub has added the phone's peer from the re-announcement; the hub drops that initiation, WireGuard retries at its 5 s
rekey. Nodes have the same race at bring-up (poll.py waits up to 30 s for the handshake). Not fixed here: the hub would
have to say which peers it holds (see the handoff).

## a4 — the RAM manager gives the leaf its /32 (`[LEAF_ADDR_FROM_THE_REGION_V1]`; John: "Nobody types shit. That's what the ram manager is for")

rina_region.cpp `announce`: a leaf off the mesh (role leaf, pubkey, no `lan`) gets the lowest 10.252.X.Y no live session
holds (10.252.254.0/24 never), bound to the session (`laddr` records, as `mown` binds mappings), written into the stored
facts (fdf-hub, fdf_graph read facts.addr) and returned (`{"ok":true,"addr":"10.252.0.1/32"}`); the same on every
re-announcement; freed at session down and when the member is on a LAN. A leaf that names its own addr: `EINVAL a leaf's
address is the region's to give`. fdf-client prints `the region gave this leaf 10.252.0.1/32 (its WireGuard Address)`.
The app has no address field; the member logs `the region gave this leaf …` and the tunnel uses it.

- netns-leaf-tunnel-test.sh RESULT PASS 10/10: fdf-client leaf given 10.252.0.1, Kotlin leaf 10.252.0.2 (different by
  construction); hub peers/routes on those; pings both ways and leaf↔leaf; a leaf naming its own addr refused EINVAL.
  Kotlin leaf: joined +433 ms, address from the region and peer set +488, hub peer +550, handshake +560.
- netns-placement-test.sh RESULT PASS 18/18, one session: on BlackBox's LAN no address held; BlackBox leaves → given
  10.252.0.1/32, hub peer 63 ms, handshake 167 ms; BlackBox back → on the LAN, tunnel down, the region frees it (facts
  carry no addr; the member logs `the region holds no leaf address for this session`).
