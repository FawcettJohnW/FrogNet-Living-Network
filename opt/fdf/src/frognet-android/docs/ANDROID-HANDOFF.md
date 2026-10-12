# HANDOFF — an FD&F member on Android (fdf-kt-2026-10-11-a11 + fdf-tun b6, with the Communicator r4; against region fdf-2026-10-11-r19-a14 = r19 + this thread)

## The one-line version

An Android device can be a region member as a **leaf**, in plain Kotlin, without porting the C++: the member speaks only
`REQ_RAW` (the RAM manager answers every one with `RESP_RAW`), holds the three-socket session with the same 2+3×1 s
keepalive (+ TCP_USER_TIMEOUT 5 s), takes its WireGuard peer from the hub's facts in the region, and runs the tunnel on wireguard-android's
GoBackend (wireguard-go). Proven here against the real rina-host and fdf-hub (r16-a4, then r19-a11), side by side
with fdf-client: same rows, same refusals, same liveness numbers, tunnel up and pinging both ways. The APK itself was not
built here (no Android SDK reachable); its sources type-check against the Android API 34 framework.

## a11 on r19-a14 (2026-10-11): the phone as a bridge (PLAN-BRIDGE §3.1/§3.2)

- **GoBackend is gone.** The app has one VPN TUN (`BridgeVpn`, address 100.127.255.1, never changes) and runs
  **fdf-tun** on it (`libfdftun.so`, a static Go executable in `jniLibs/` for arm64-v8a, armeabi-v7a and x86_64; source in
  `tun/`, `tun/build.sh`). The TUN fd goes to it over an abstract unix socket; every WireGuard socket comes back to be
  `protect()`ed. One WireGuard device per pond: the forward pond's (the leaf tunnel, as before) and one per pond it reaches.
- **Every participant pond is reached as its own config allows**: its facts say `reach:true` + the pubkey; its hub peers
  it, its RAM manager gives it an address, the planner (fdf-reach's rules, in Kotlin) aliases its networks 1:1 from
  100.64.0.0/10 (on the phone 10/8 is the forward pond's, so every island is aliased). A pond that refuses (map refused
  and a collision, reach none, no config) ends that session and is not rejoined until Start; the log says why.
- `MemberService.resolve("w:<guid>:<port>")` answers host:port from whichever pond has that identity; two ponds answering
  is refused as ambiguous (the log names both).
- Proven on a JVM in netns (the same Kotlin, `fdf-member-kt bridge`): see MERGE-r19-a14.md. **Not yet on the emulator or a
  handset** — the first run there proves fdf-tun under Android's seccomp/SELinux and the protect path.
- What the log says: `bridge: fdf-tun fdf-tun-2026-10-11-b6 on the VPN's TUN (routes 100.64.0.0/10 and 10.0.0.0/8)`,
  `bridge: forward via hub <hub> as 10.252.0.x`, `[pondB] reach into pondB (all, map allowed) as 10.252.0.1 there via hub
  hub-pondB: 10.7.7.0/24 …; aliased 100.64.0.0/24 -> 10.7.7.0/24`, `[pondC] REACH REFUSED into pondC: …`,
  `BRIDGE LOST: …` (fdf-tun died: every session ends and starts again), `bridge: a socket fdf-tun opened could NOT be protected`.

## a10 on r19-a12 (2026-10-10 night): servers found by identity

- `[IDENTITY_IN_TUPLES_V1]` the where cell now says `{"comms":{"ram":"w:<guid>:8800","media":"w:<guid>:8994"}}`;
  `Member.endpoint` resolves each through the region's `resolve` op to host:port in this pond (`Member.resolve` for
  several). The Communicator button opens with those; a cell written with an address (an older node) is not taken and
  the log says why. Proven in region-loop: `z ram=w:g-z:8800 -> 10.3.3.1:8800 media=w:g-z:8994 -> 10.3.3.1:8994`.
- The phone does **not** reach other ponds (REACH_V1 is a node's: it needs NAT, which Android's VPN interface does not
  do). A participant pond on the phone is presence, shared memory and notices, as before.
- Build: a10 member + app; the app sources type-check against Android API 34; leaf-tunnel 10/10, placement 18/18,
  two-ponds 14/14, member-death as before.

## a8 + Communicator r3 (2026-10-10 afternoon): on John's hardware

- **Physical phone:** the member app's Communicator "was responsive and worked properly" (John). The emulator ran
  before it: tunnel up, routed by the nodes once `[LEAF_ROW_FROM_ADDR_V1]` was in (r19-a9), shown in frognet_monitor as
  `<name> DEVICE ONLINE|DOWN` (`[DEVICE_ROW_V1]`, r19-a10).
- **Phantom call on Windows** fixed in both apps: `[RINGS_ONLY_WHILE_OFFERED_V1]` -- a call rings only while the
  invitation is <= 60 s old and the caller still offers that session, re-checked every tick; `[LEAVE_WITHDRAWS_THE_OFFER_V1]`
  -- the desktop's hang-up stops offering the session (it never did). Communicator STATUS.md "r3" has the detail and proof.
- **Stamped picture:** desktop senders burn `#frame HH:MM:SS.mmm` bottom-left; the phone shows a "here" clock under the
  caller's name, the window in its overlay. Lag = here - stamped.
- **Open:** the emulator call's start-up lag (cause not found; the stamp measures it if it recurs); the emulator's video
  "reversed" (the phone un-mirrors only a camera reporting LENS_FACING_FRONT). a9 / comms-android r4 logs it:
  `[camera] opened camera <id>: facing front|back|external, sensor N deg, WxH; un-mirrored for the far end | sent as
  the camera gives it` (`[CAMERA_SAYS_WHICH_V1]`).
- Windows: rebuild from the whole Communicator tree (WINDOWS.md "Updating"); never an overlay.

## a5 on r19-a11 (2026-10-10 07:00): ponds, notices, and the merge onto r19

PLAN-PONDS (John: "a joined network vs. a participant pond"; "alt-tab the networks"; "a notification system ... just a
tuple"). The device holds one session per pond, all the time; one pond is **forward** (placement, leaf facts, the
tunnel), the rest are **participants** (`{"role":"participant"}`: presence, shared memory, notices; no peer, no route).
A notice is a tuple, `(pond, attention, w:<guid>) {why, from, ts}`, written by `fdf-attention "why"`; a participant
pond's member holds a read on it; on the phone it is an Android notification with **Bring forward**. Bring forward =
two re-announcements on two open sessions; the tunnel follows the hub's event. Nobody reconnects.
Phone logic (`netns-two-ponds-test.sh`, the same `member/` code): 14/14, notice 25 ms, a switch to the first ping
through the new hub 107–232 ms. A FrogNet node does the same with the real daemon (`fdf-forward <area>`, no restart):
`netns-ponds-daemon-test.sh` 24/24, a switch to the first answer 356–636 ms.
Merged onto r19 as region build `fdf-2026-10-10-r19-a11`; `MERGE-ANDROID-r19-a11.md` lists every change and test. Testing
on r19 found an r19 bug: two facts.json writers sharing one tmp name could wipe the node's identity (fixed,
`[FACTS_ONE_WRITER_AT_A_TIME_V1]`). Full record: `docs/WORKLOG-PONDS.md`, plan: `docs/PLAN-PONDS.md`.

## a4 (2026-10-10 05:00): the RAM manager gives the leaf its /32; three tarballs

`[LEAF_ADDR_FROM_THE_REGION_V1]` (John: "Nobody types shit. That's what the ram manager is for"): rina-host gives a leaf
off the mesh its 10.252.X.Y/32 with the announcement, bound to the session; the app has no address field; fdf-client
prints the one it got. Region build `fdf-2026-10-10-r16-a4` carries it and `[KEEPALIVE_5S_V1]`, all three tarballs;
`MERGE-ANDROID-r16-a4.md` says what goes onto r18. Tests: tunnel 10/10, placement 18/18, death 3.9 s, r16's graph test
17/17, mapown 4/4, oracles and switchover sim PASS, arm64 under qemu.

## a3 (2026-10-10 05:00): every decision follows the region's events

The member's held wait is its event reader; on every `p`: one `world` read, re-place, re-announce if moved, the tunnel the
placement and the hubs call for (`member/Leaf.kt`, shared by app and runner). `tools/netns-placement-test.sh` 15/15 ×3 in
ONE session: BlackBox leaves → the phone goes off the mesh, re-announces, hub peer at 81–85 ms, handshake 129–138 ms;
BlackBox returns → back on its LAN, tunnel down, hub drops the peer at 93–102 ms.

One run in three: handshake at 5,129 ms. The phone's first initiation reaches the hub before fdf-hub has added the
phone's peer; WireGuard retries at its 5 s rekey. Nodes have the same race at bring-up. Exact fix, hub side: fdf-hub
puts the peers it holds in its own facts; a member brings its peer up on the event that shows its key there. Cost: one
hub re-announcement (one `p`) per peer change. That is a hub change for the r19 thread.

## a2 (2026-10-10 04:25): the address it already has, and a 5 s keepalive

- `[ADDRESS_IS_THE_ONE_IT_HAS_V1]` (John: "The machine already has an ip"). Before every announcement the member reads
  the region and places itself (`member/Placement.kt`): if one of its IPv4s is inside a /24 a live member serves (`own`
  .1 row or facts.subnet), it is a host on that member's LAN, routed everywhere by that /24 — it announces
  `lan{ip,subnet,served_by}` and no addr, no pubkey, so the hub gives it no peer and the graph no /32, and no tunnel
  comes up. Off the mesh it is a leaf through the hub as in a1; the typed /32 is now used only there.
  `[ADDRESS_CHANGE_ENDS_SESSION_V1]`: the default network's IPv4 set changing ends the session at once; the next one
  places itself again. `tools/netns-placement-test.sh`: RESULT PASS 9/9 (on BlackBox's LAN: on the mesh, no tunnel, no
  hub peer, BlackBox pings it on its /24; address taken away, new session: off the mesh, hub peer, ping the hub).
- `[KEEPALIVE_5S_V1]` (John: "let's try taking the keepalive to 5 seconds"): idle 2 s + 3 probes 1 s apart +
  TCP_USER_TIMEOUT 5,000 ms, both ends. C++: `tools/apply_keepalive_5s.py` (frogram.cpp `dial`, ram_server.cpp
  `serve`) — for r18, all three tarballs. Kotlin: both tuners.

| | 20 s (r16) | 5 s |
|---|---|---|
| silent death → event, C++ / Kotlin | 19,306 / 19,162 ms | 3,889 / 3,860 ms |
| silent region → member says ended, C++ / Kotlin | 19,318 / 19,288 ms | 4,075 / 3,990 ms |
| false deaths, 15 member-minutes, 5% loss each way | 1 | 1 |
| same, 10% loss | 5 | 14 |
| same, 20% loss | 19 | 40 |

(loss by iptables random drop, no delay: this kernel has no netem.)

## Files (one tarball: `frognet-android-a11.tgz`)

```
frognet-android/
  member/       the member protocol, pure Kotlin, no Android types (the app and the JVM run the same code)
    Wire.kt       FNW1 frames: HELLO, REQ_RAW out; RESP_RAW, OP_ERROR, markers in                          92 lines
    Json.kt       JSON quote + parse (frogram::Json's subset)                                               71
    Session.kt    3 sockets, ordered writer, return/data readers, marker+segment pairing, death            183
    Member.kt     fdf_client.cpp main(): node, watch, announce/EEXIST-wait, addr, version, maps, notices,
                  hold; reannounce/world/leave                                                            214
    Hubs.kt       the hub(s) from participants, with the peers each holds                                   27
    Placement.kt  OnMesh (in a live member's /24) / OffMesh, from my IPv4s and the region                   47
    Leaf.kt       per event: world -> placement -> re-announce -> tunnel, forward pond only                 68
    Bridge.kt     [BRIDGE_V1] ReachPlanner, AliasLedger (quarantine), Bridge (the whole state -> fdf-tun),
                  Reach (one participant pond), ProcessBridgeLink, resolve with ambiguity refused            ~400
  member-cli/   fdf-member-kt: the member on a JVM, driven like fdf-client; `probe`; `ponds`; `bridge` (fdf-tun --tun,
                FDF_PONDS, FDF_FORWARD_FILE, FDF_KEY_FILE, FDF_TUN_BIN, FDF_TUN_NAME, FDF_CTL_PORT); `selftest`
  tun/          fdf-tun (Go): engine.go (devices, demux, set), rewrite.go (1:1 + checksums + ICMP), main.go (--tun /
                --fd-socket, protect, JSON control), third_party/ (wireguard-go + x/*, vendored), build.sh
  app/          the Android app (Android Studio project)
    MemberService.kt   one session per pond; forward pond's tunnel; notices -> notifications, Bring forward    163
    BridgeVpn.kt       the VpnService TUN, libfdftun.so started on it, the fd passed, protect() answered
  app/src/main/jniLibs/<abi>/libfdftun.so   fdf-tun for arm64-v8a, armeabi-v7a, x86_64 (tun/build.sh)
    AndroidTuner.kt    keepalive 2/1/3 + TCP_USER_TIMEOUT 5000 via Os.setsockoptInt                             36
    Identity.kt        guid and WireGuard key made once; name; ponds (area|ram); forward                         56
    MainActivity.kt    the pond list (Forward / Remove / Add), Start/Stop, the live log                          93
    AppLog.kt          every line tagged with the build; logcat + screen + files/fdf-member.log                 29
  tools/netns-member-death-test.sh   liveness, C++ vs Kotlin side by side
  tools/netns-leaf-tunnel-test.sh    hub <-> leaf, end to end (10 checks)
  tools/netns-placement-test.sh      on the LAN vs off the mesh, one session (18 checks)
  tools/netns-two-ponds-test.sh      the phone in two ponds: notices, Bring forward, ten switches (14 checks)
  tools/netns-fdftun-test.sh         fdf-tun alone: three hubs all 10.7.7.0/24, aliases, concurrency, the --fd-socket path
  (the phone's bridge against real ponds is stage (7) of rinab/tools/netns-reach-test.sh: BRIDGE_KT=… FDF_TUN=…)
  tools/apply_keepalive_5s.py        [KEEPALIVE_5S_V1] + [RECV_SAYS_WHY_V1] for rinab (same file as in r19-a11)
  app/src/main/java/net/frognet/communicator/  the Communicator (Java): CommunicatorActivity, camera/codec/audio, core/
  communicator-test/  its tests (Probe, Robolectric AppFlowTest opened as the member opens it, test_app_flow.py)
  docs/ANDROID-HANDOFF.md (this), ANDROID-PROGRESS.md, PLAN-PONDS.md, WORKLOG-PONDS.md, COMMUNICATOR-ANDROID-r1.md
```

Build the app: open `frognet-android/` in Android Studio (AGP 8.5.2, Kotlin 2.0.21, compileSdk 34, minSdk 29), Run.
`jniLibs/` already holds fdf-tun; rebuild it with `tun/build.sh` (Go 1.23+, no network needed).
Build the JVM runner only: `gradle -Pfdf.jvmOnly=true :member-cli:installDist`.

## What the logs say

Every app line: `<time> [fdf-kt-2026-10-10-a7] …`, on the screen, in logcat (tag `FDF`), in `files/fdf-member.log`.

- `member: <name> guid=android-… leaf 10.252.0.x/32 pubkey … region 165.232.153.144:51888 area frognet`
- `placement: on the mesh: 10.101.10.170 is in 10.101.10.0/24, served by BlackBox; routed by that /24, no tunnel` / `placement: off the mesh (…)`
- `network: IPv4 [...] -> [...]; ending the session to place the member again`
- `session up to H:P token=frogram-kt-… (request, return, data sockets; keepalive 2+3x1 s)`
- `build fdf-kt-…; region at H:P is build fdf-2026-10-10-r19-a11`
- `<name> joined the region at H:P as w:<guid>, announced [ips] with facts`
- `tunnel: up, 10.252.0.x/32, peer <hub> <key> endpoint … allowed 10.0.0.0/8 keepalive 25 s (wireguard-go …)`
- `JOIN REFUSED by the region: <why>. … waiting for the region to change (no retry loop).`
- `session ended: return socket: Connection timed out` (its own keepalive) / `… closed by this member`
- `tunnel: down (the session ended: …)`, `member: not a member (…); a new session in 5 s`

The hub and the nodes need nothing new: the hub's `event p: members=… peers=…` and `deleted peer … (not a member now)`
are the same lines a laptop leaf produces.

## Proven (every number from a run on this build)

Against rina-host built here from fdf-source-r16 (x86), on loopback and in network namespaces.

| check | C++ fdf-client | Kotlin member |
|---|---|---|
| joins; participants row | `w:cpp-leaf-1 alive facts{role leaf, addr, pubkey}` | `w:kt-leaf-1 alive facts{…}`, cap.client=fdf-member-kt |
| clean death (kill -9) → first event on a watcher | `p` 12 ms | `p` 13–14 ms |
| silent death (member's link down) → event on a watcher | `p` 19,176–19,306 ms | `p` 19,141–19,162 ms |
| silent region → the member itself says the session ended | 19,318–19,402 ms | 19,288–19,350 ms (`Connection timed out`) |
| map_register signed with the site key | registered | registered; HMAC accepted |
| second claimant | `EEXIST (… held by a live session (node:w:kt-node-1); not transferred)` — the C++ client refused, Kotlin named as holder | |
| wrong site key | | `EPERM (authentication failed)` |
| served /24 overlap | | `JOIN REFUSED … 10.70.70.0/24 is served by holder (alive)`; joins 14 ms after the holder left |
| facts change | | `facts changed, re-announced`; region shows the new value |
| data socket (participants/world 3.8 KB, 300 calls each) | | 600/600 via marker + segments, bytes == HTTP front's answer; p50 0.43 / 0.38 ms |

`tools/netns-leaf-tunnel-test.sh` — RESULT PASS 8/8 (hub: rina-host + fdf-hub on wireguard-go + its fdf-client; leaves:
the Kotlin member, and fdf-client beside it):

| stage, from the Kotlin process start (JVM start included) | ms |
|---|---|
| joined the region | +334 |
| tunnel peer set from the hub's facts (no hub key typed anywhere) | +373 |
| hub has its peer: allowed-ips 10.252.0.8/32, route 10.252.0.8 dev wg-frognet | +426 |
| first WireGuard handshake | +437 |
| leaf ↔ hub, leaf ↔ other leaf through the hub (ping both ways) | pass; rtt avg 1.17 ms |
| leaf killed → hub peer deleted | 17–46 ms |
| after it left: no peer, no path (gone is gone) | pass |

App sources: compile clean against Android API 34 (Robolectric's android-all-14) + the wireguard-android tunnel
1.0.20250531 classes. One real error was caught and fixed that way (`id` inside `View.apply {}` is `View.id`).

## Not proven here (needs a phone)

2026-10-10: a physical phone has run the app with the Communicator (above). The numbers below have still not been taken.

- The APK on a device: VPN consent, GoBackend bring-up, the foreground service, Os.setsockoptInt on Android's sockets.
- A FrogNet node reaching the phone: fdf_graph already turns a leaf's `addr` into a /32 route via wg0 and a hosts line;
  not exercised in this session (only hub ↔ leaf and leaf ↔ leaf).
- The numbers to take on hardware, in the app's log and the hub's journal: join → `event p … peers=N` → handshake;
  Stop → `deleted peer`; airplane mode → `deleted peer` (expect ~4 s); wifi → cellular → new session; a notice
  from another pond → the notification → Bring forward → the tunnel on the new hub.

## Open (yours)

1. **5 s on lossy links** costs 2–3× the false deaths at 10–20% loss, equal at 5%. Battery on a phone is still to measure.
2. `netns-region-loop-test.sh` (r19's) did not converge in 2 of its first 4 runs on r19-a11, then passed every run after;
   cause not found (see MERGE-ANDROID-r19-a11.md "Open"); the next failure dumps everything it needs.

## Next, in order

1. Take the hardware numbers above on the physical phone (the app already runs there).
2. Two ponds on hardware: the droplet's pond forward, a second pond (a Pi) as participant; `fdf-attention` there.
3. Names on the phone (the hosts lines the graph already produces), then the phone as a tethering **node** (`own` and
   `given` rows; fdf-facts' job on a Pi, the app's on a phone).
