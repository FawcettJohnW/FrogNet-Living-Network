# r19-a14 — the phone as a bridge; resolution by identity alone; alias quarantine

Build `fdf-2026-10-11-r19-a14` (on r19-a13). Member app `fdf-kt-2026-10-11-a11` with `fdf-tun-2026-10-11-b6`.
Communicator unchanged (comms-app r3, comms-android r4). Tarballs: `fdf-install-x86_64.run`, `fdf-install-aarch64.run`,
`fdf-droplet.tgz`, `frognet-android-a11.tgz`, the source.

PLAN-BRIDGE §3.1 (the phone reaches several private networks at once, all free to use the same addresses) and §3.2
(an application names an identity, never a network), plus the three points of the 2026-10-11 review: ambiguity must be
detectable, alias stability across session loss and reuse needs a quarantine and tests, payload-embedded addresses are
a documented limitation (PLAN-BRIDGE §6.1).

## What changed

| tag | where | what |
|---|---|---|
| `BRIDGE_V1` | frognet-android/tun (new: fdf-tun, Go) | One TUN, one wireguard-go device per scope (the forward pond, each island), the 1:1 rewrite (destination alias→real, source → the address that pond gave, and back), incremental IPv4/TCP/UDP checksums. Static linux executable: `jniLibs/<abi>/libfdftun.so` in the app (arm64-v8a, armeabi-v7a, x86_64), `out/fdf-tun-*` for netns. Control: JSON lines on stdin (`set` with the whole state, `status`, `version`); stdin closed = everything closed (gone is gone). wireguard-go, x/crypto, x/net, x/sys vendored in `tun/third_party` (pruned to linux, 3.3 MB); `tun/build.sh` builds every target with no network. |
| `FDF_PROTECT_HOOK_V1` | tun/third_party/wireguard-go/conn, tun/main.go, BridgeVpn.kt | Android: the app passes its VpnService TUN fd over an abstract SOCK_SEQPACKET socket (SCM_RIGHTS; fdf-tun takes it only from the app's own uid) and closes its copy; every UDP socket WireGuard opens is sent back to be `protect()`ed before it binds. Requests are numbered (`protect <n>` / `ok <n>`): a late answer is dropped, never taken for the next socket's. |
| `PHONE_SCOPE_IS_10_8_V1` | member/Bridge.kt | On the phone all of 10.0.0.0/8 is the forward pond's (through its hub, or over the LAN on the mesh), so every island destination is aliased from 100.64.0.0/10 and the VPN routes only that (+10/8 while the forward tunnel is up). The TUN's address is fixed (100.127.255.1); each pond's given address is put on the packets by fdf-tun, never on an interface. |
| `BRIDGE_V1` | member/Bridge.kt (new), Member.kt `netConfig`/`where`, MemberService, Identity, BridgeVpn.kt (new) | `ReachPlanner` = fdf-reach's rules (config reach/map, collisions, Pending/Refused/NotNow); `Bridge` = the whole state sent whole; `Reach` per participant pond; participant facts carry `reach:true` + the pubkey; a refused reach ends that pond's session and it is not rejoined. GoBackend replaced; wireguard-android kept for its key type. `fdf-member-kt bridge` runs the same code on a JVM (fdf-tun `--tun`) for the netns proof. |
| `RESOLVE_AMBIGUOUS_REFUSED_V1` | fdf-reach `resolve w:…`; Bridge.resolve; MemberService.resolve | Resolve an identity with no network named: every network this machine is in is asked; **exactly one may answer** (reached here or not). Two = refused (exit 7 on a node), naming each with its address and how it is reached here. |
| `ALIAS_QUARANTINE_V1` | fdf-reach `pick_alias` + `<var>/reach/.released`; Bridge.kt `AliasLedger` (+ `files/fdf-aliases.json`) | A released block (session lost, refused, removed, dropped by a re-plan) comes back to the same (pond, prefix) whenever it returns; no other use for 600 s (`FDF_ALIAS_QUARANTINE_S`). Kept on disk on both. A /32 alias is never an .0 or .255 address. |
| `PLAN_UNDER_THE_BRIDGE_LOCK_V1` | Bridge.decide; fdf-tun `set` | **Found by the netns log:** B and F planned at the same moment both took 100.64.0.0/24. Planning and commit are now one step under the bridge's lock; fdf-tun also refuses a set in which two islands' aliases or routes overlap. Selftest: 200 concurrent decides never overlap (and 200/200 overlapped with the lock removed). |
| `COVERED_IS_NOT_A_DESTINATION_V1` | both planners | A /32 inside a served /24 is reached through the /24's alias (its own alias would answer from another address than the one connected to). |
| `SET_OUTSIDE_THE_PACKET_LOCK_V1` | tun/engine.go | **Review:** `set` configured devices while holding the engine lock; replacing a peer waits for its receive routine, which may be waiting for that lock → deadlock under traffic. Devices are configured with no engine lock; only the table swap takes it. Same hub key = in-place update (the session survives a re-plan). A device's config is recorded only after it came up (a failed Up is retried by the next set); a failure part-way gives re-configured devices their old config back and closes new ones (all or nothing). |
| `ICMP_INNER_REWRITTEN_V1` | tun/rewrite.go | **Review:** an ICMP error (unreachable, time exceeded, parameter problem) quotes the packet it is about; its addresses are mapped too, with both checksums, so path-MTU discovery and port-unreachable work through an alias. |
| `CGNAT_IS_NOT_AN_ALIAS_V1` | MemberService, BridgeVpn | **Review:** a carrier using 100.64.0.0/10 itself (address, gateway, DNS): those addresses are never an alias and, on Android 13+, excluded from the VPN (older: said in the log). |
| `GONE_IS_GONE_V1` | Bridge.lost | **Review:** a dead or broken fdf-tun dropped nothing and every later change failed. Now: every reach and the forward tunnel dropped (aliases quarantined), the TUN closed, every session ends; the next sessions decide again. Errors taking a tunnel down no longer escape a worker thread (on Android that killed the app). Bring forward takes the old pond's tunnel down before the new one's comes up (the driver is shared). |

## Proof (this build)

All on this build, in network namespaces (logs: docs/run-fdftun-2026-10-11.log, run-reach-2026-10-11.log,
run-region-loop-2026-10-11.log): **fdftun 20/20, reach 68/68** (stage 7, the Kotlin bridge: 14), **region-loop 20/20**
(Communicator and the phone's code included), ponds-daemon 21/21, graph 17/17, hub 25/25, leaf-tunnel 10/10, placement
18/18, two-ponds 14/14. Oracles: graph, graph_apply, hub, region, region backends, gen, election sim, reach_alias_test 12/12;
`fdf-member-kt selftest` 24/24; fdf-tun `go test`.

- **netns-fdftun-test** (fdf-tun alone, three hubs that ALL serve 10.7.7.0/24 and ALL gave the phone 10.252.0.1):
  forward + two islands through aliases, both directions, 600 concurrent requests each answered by its own network,
  3 MB byte for byte, handshakes, removal, bad and overlapping sets refused with nothing changed, stdin close; and the
  app's way in (`--fd-socket`, the TUN fd passed, protect answered — the first one late: refused, the late answer
  dropped, the same set again works).
- **netns-reach-test** stage (7), the Kotlin member + fdf-tun on a phone namespace: forward in A as a leaf, B and F reached
  at once (A, B and F all serve 10.7.7.0/24; B and F both gave it 10.252.0.1), C refused and its session closed; by
  identity w:g-a / w:g-c / w:g-g → three different 10.7.7.1s; into the phone from c, g and a; 300 concurrent requests;
  the same identity in B and F refused as ambiguous; B's session lost and back with the same aliases; the member ended →
  fdf-tun and its TUN gone. Plus node stages (2e) the same alias after a session loss, (2f) resolve by identity alone and
  its ambiguity.
- `fdf-member-kt selftest` (planner, ledger, quarantine, concurrency, covered destinations, ledger on disk, lost bridge);
  `rinab/tests/reach_alias_test.py` (fdf-reach's alias choice); fdf-tun `go test` (20,000 random packets' checksums
  against a full recompute, zero UDP checksum, later fragments, ICMP quoted headers).
- The app type-checks against Android 34 (no APK built here: no SDK in this workspace).

## Not proven here

- **The app on the emulator and a handset**: fdf-tun as libfdftun.so under Android's seccomp and SELinux, the fd
  passing and `protect()` on a real VpnService. The app log says each step (`bridge: fdf-tun <build> on the VPN's TUN`,
  every `protect` failure, `BRIDGE LOST: …` with fdf-tun's exit code).
- A pond with `reach listed` end to end (planner unit-tested both sides; not in a netns stage).

---

## Addendum (2026-10-11, later): Windows — fdf-kt a12, fdf-tun b8, `fdf-windows.zip`

The bridge on Windows: the same Kotlin member on a bundled Java runtime, fdf-tun.exe on a Wintun adapter, netsh for its
address and routes, sockets held off the TUN when a hub would route into it, key and facts made on first run, `ask
resolve` / `ask comms` for applications and comms-app.exe. Full account: **WINDOWS-BRIDGE.md**. Also fixed for every
platform: `[UP_IS_CONFIGURES_V1]` (a race that could answer ok over a failed bind). Node and droplet packages unchanged
(r19-a14). Runs: reach 70/70 (stage 7 now with `ask resolve` and `ask comms`), fdftun 20/20 on b8, two-ponds 14/14,
selftest with 20 `wg genkey` keys; the Windows package under Wine as far as Wine goes (WINDOWS-BRIDGE.md "Proven").
