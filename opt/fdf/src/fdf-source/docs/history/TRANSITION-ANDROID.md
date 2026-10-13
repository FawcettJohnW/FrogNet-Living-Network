history: superseded by docs/OPERATING.md

# Transition brief — implementing an FD&F member on Android (as of build fdf-2026-10-09-r16)

Read `TRANSITION-README.md` first (what the system is, where things run, John's rules). This brief is the protocol
view: what a *member* of the region is on the wire, so the question "can an Android device be one, and how" can be
answered from the actual contract rather than from the Linux implementation.

## 1. What a member is

A member is a machine that (a) holds one FNWP session to the region's RAM manager, (b) has written its `node` record
on that session, (c) has announced its name, addresses and facts on that session, and (d) optionally registers the
EID prefixes it serves as signed mappings bound to that session. Liveness *is* the session: the moment the session
ends, the RAM manager drops the node record, the announcement goes dead, the member's mappings go with it, and every
other member sees one `m` (membership) event. Nothing is polled, nothing times out at the application layer, nothing
is remembered about a member that left. A silent death is detected by TCP keepalive on the session socket
(5 s idle, 3 probes 5 s apart = 20 s; `[KEEPALIVE_20S_V1]`, John's number); a clean close is detected in ~14 ms.

Everything else in the system (the graph, the routes, the hub's peers, the elections) is *derived* by every member
from what the region holds. A member that only wants to *exist* and be routed to needs (a)–(d) and a WireGuard
tunnel to the hub. A member that wants to *route for others* (a FrogNet node) also runs the graph and applies it —
on Android that is not the first question.

## 2. The wire: FNWP (frogram)

Reference: `rinab/platform/ribbit_cpp/frogram.cpp` (client, 720 lines) and `fnwp_client.hpp`/`fnwp_server.hpp`;
server side `rinab/platform/ramsrv/ram_server.cpp` (730 lines) with the region's ops in `rinab/src/rina_region.cpp`.
This is John's own protocol, not HTTP. The facts that matter for a port:

- Transport: TCP to the RAM manager (`RINA_RAM=host:port`; the droplet is `165.232.153.144:51888`).
- A session is **three TCP connections** from the client, each opened with a HELLO frame:
  request socket `HELLO <token>`, return socket `HELLO RETURN:<token>`, data socket `HELLO DATA:<token>`.
  Frames are `4-byte big-endian length + body`. MAGIC is `"FNW1"`; HELLO is op `0x50`, body `MAGIC + op + len(token)
  + token`. The server answers the RETURN and DATA hellos with an acceptance frame; refusal means the far end does not
  speak the full contract (`frogram.cpp` ~line 340).
- Requests are posted on the request socket with a sequence number; replies arrive on the return socket; large
  payloads are segmented on the data socket under the same sequence (`[HIGH_SPEED_DATA_SOCKET_V1]`). A held
  (blocking) read — `?op=rina_block` with `{"op":"wait",...}` — is a request whose reply comes when the region has
  something to say; the client waits with an effectively infinite timeout (`3.0e8` s).
- The application layer on top is "HTTP-shaped": `call("POST", "/FawcettInnovations.RINA.ram_interface.php?op=rina",
  <json>)`. The path and the `op=rina` / `op=rina_block` query are literal strings the server routes on; the body is
  the JSON op. There is also a real HTTP front on the same port (`tools/apply_http_front.py` patches it in) that
  python uses for *reads* (`region.py` `_call`: `urllib` POST of the same JSON to the same path) — but an HTTP request
  is its own one-shot session, so **membership cannot be held over HTTP**; a monitor that announced over HTTP would be
  "alive" for the duration of one request and produce an `m` event when it closed.
- Keepalive: both ends set SO_KEEPALIVE/TCP_KEEPIDLE=5/INTVL=5/CNT=3 on the session sockets (`tools/apply_keepalive.py`
  shows the exact calls). An Android implementation must do the same on its side or a silent death of the *server*
  side will not be noticed by the member for the OS default (2 h).

## 3. The ops a member uses (JSON bodies, exactly as fdf-client sends them)

All on the member's own session unless noted. `ns` is the member's identity key: `facts.guid` when there is one,
else the name (`[MEMBER_IDENTITY_V1]`; every FrogNet node's hostname is "FrogNetHost", so the guid is the key).

| op | body | meaning |
|---|---|---|
| `node` | `{"op":"node","ns":<ns>,"devs":""}` | this session is a live machine; the node record lives while the session does |
| `announce` | `{"op":"announce","ns":<ns>,"name":<shown name>,"ips":[<IPv4 strings>],"cap":<capability JSON as a string>,"facts":<facts JSON as a string>}` | the member's row; re-sent on the same session whenever facts change. Refused `EEXIST` (holder named) if a served /24 in the facts overlaps a live member's (`[PREFIX_OVERLAP_REFUSED_V1]`); the client then waits for the next region change and tries again |
| `map_register` | `{"op":"map_register","site":<area>,"name":<eid "10.A.B.0/24">,"rloc":<"hub:prio:weight,...">,"ttl":"86400","ts":<"sec.nsec">,"auth":<hex HMAC-SHA256(site key, name\|rloc\|ttl\|ts)>,"own":true}` | a signed LISP Map-Register bound to this session (`own:true`). Refused `EPERM` (HMAC mismatch), `EEXIST` with `why` naming the live holder (`[MAP_OWNER_IS_NOT_TRANSFERRED_V1]`), `EALREADY` |
| `map_remove` | `{"op":"map_remove","name":<eid>}` | withdraw a mapping this session registered |
| `map_list` | `{"op":"map_list","site":<area>}` | every live mapping under the site (the client asserts its own every 30 s against this: `[MAPS_ASSERTED_V1]`) |
| `world` | `{"op":"world","site":<area>}` | participants + mappings in one read (r15, `[WORLD_IN_ONE_READ_V1]`); a region older than r15 answers an error |
| `participants` | `{"op":"participants"}` | every announcement with `alive`, `facts`, `id` |
| `watch_participants` | `{"op":"watch_participants","proc":<watcher name>}` | register this process for `p` events |
| `map_watch` | `{"op":"map_watch","name":"*","proc":<watcher name>}` | and for `m` (mapping) events |
| `wait` (held, `?op=rina_block`) | `{"op":"wait","proc":<watcher name>,"after":<n>}` | blocks until an event after `n`; the reply text carries event lines `<n>\t<bag>` after a 2-byte header; `fdf_events.cpp` (36 lines) is the whole reader — it prints `p` / `m` / `gone` |
| `version` | `{"op":"version"}` | `{"ok":true,"server":"fdf-2026-10-09-r16"}` |
| `hello`, `fork` | | program-level session ops; not needed by a member |

The site key for the area `frognet` is kept on the droplet, not in the tree. The key is held by the region (`map_site`) and used only to verify
`auth`; a member never sends it.

## 4. The facts a member announces

`facts.json` is an opaque JSON object to the region; its *consumers* are every member's `fdf_graph.py` and the hub.
What a non-routing member (a phone) would publish:

```json
{"role": "node", "name": "<shown name>", "guid": "<stable machine id>", "subnet": "10.A.B.0/24",
 "pubkey": "<WireGuard public key>",
 "addrs": [ {"iface": "wg0", "ip": "10.253.A.B", "value": "", "kind": "hub", "mac": ""},
            {"iface": "wlan0", "ip": "10.A.B.1", "value": "FrogNetHost.<name>", "kind": "own", "mac": "..."} ],
 "maps": [ {"eid": "10.A.B.0/24", "rlocs": "hubA:1:50"} ],
 "capability": { ... frognet_capability_probe.sh output ... },  "capability_ts": 1760000000.0,
 "gen": 3, "gen_why": "routes_changed", "view": {...}}
```

Row kinds `fdf_graph.py` understands: `own` (an address this machine serves; `.1` = it serves that /24), `held` (a DHCP
lease this machine holds — ip, iface, mac), `given` (a lease it gave — ip, name, mac), `uplink` (value `"uplink
<eth|wifi> <rate>[ dead]"`), `hub` (its tunnel edge address `10.253.A.B`), `leaf` (a /32 member with no /24). A
**leaf** is the simplest member: `{"role":"leaf","addr":"10.253.x.y/32","name":...,"pubkey":...}` — the hub gives it
a peer with that /32 as allowed-ips and an on-link route (`hub_oracle.py` case 7). A phone that only needs to be
reachable and to reach the mesh is a leaf. A phone that serves a /24 (tethering) is a node and publishes `own`/`given`
rows; the graph then places the hosts behind it.

`gen`, `view`, `capability` are written by the FrogNet daemon on a node; a leaf need not write them. The region
refuses an announcement whose served /24s overlap a live member's.

## 5. The tunnel

The hub (`fdf-hub`) builds one WireGuard interface on the droplet (`wg-frognet`, UDP 50889) with one peer per member
whose facts carry a `pubkey`: allowed-ips = the member's edge `/32` plus the /24s the plan says it carries (its own
if it has a tunnel; its tunnel-less children's if it is their exit); a route per prefix to the member's edge. The hub
remembers nothing: a member that leaves is deleted, one that returns is a new peer from its facts. The member side is
a plain WireGuard interface with the hub as its one peer (`fdf-region-join` writes it on a node;
`internet_tunnels_v3/region.py` `my_channels()` shows the config derived from the hub's facts: endpoint, pubkey,
the member's `10.253.A.B/32` edge from its subnet). Liveness of the tunnel is the WireGuard handshake
(`[HUB_HEALTH_IS_THE_HANDSHAKE_V1]`); the member's own region traffic is sourced from its `.1`
(`[REGION_SRC_IS_MY_DOT1_V1]`) — a leaf has only the edge address and sources from it.

On Android that is `VpnService` + the wireguard-android backend (userspace wireguard-go is what the netns tests use,
`FDF_WG_USERSPACE=1`, so a userspace implementation is already proven against this hub).

## 6. Two ways to build it

1. **Port fdf-client to the NDK.** `fdf_client.cpp` (244 lines) + `frogram.cpp` (720) + `fnwp_*.hpp` build static for
   aarch64 today (`tools/build_arm64.sh`: g++ -static with lz4 and OpenSSL's libcrypto for the HMAC). The Linux-isms
   are: inotify on the facts file (replace with the app writing facts and calling re-announce directly), `sysinfo`
   for memory, `getifaddrs` for `ips`, futex for the lockless client ring (NDK has `syscall(SYS_futex)`), TCP
   keepalive setsockopts (all available). The Windows client (`tools/build_windows_client.sh`, `fdf-client.exe`) is
   the precedent for a non-Linux member: it skips facts/maps and announces name+ips+cap only.
2. **Reimplement the member in Kotlin/Java.** The contract in §2–§3 is small: three sockets, length-prefixed frames,
   HELLO, JSON ops, one held `wait`, HMAC-SHA256 for map_register. The parts of FNWP a member needs are the subset
   fdf-client uses; the engine/coalescing/ring machinery in frogram.cpp is for high-rate RAM programs, not for a
   member that sends a handful of ops. The exact framing of requests and replies (sequence numbers, the return and
   data sockets, the 2-byte header on `wait` replies) must be read from `frogram.cpp` and `fnwp_server.hpp`; nothing
   in this brief replaces reading those two files.

What a first experiment would prove: a phone holds a session, announces as a leaf with a pubkey, the hub (`fdf-hub`
log: `event p: ... peers=N`) adds its peer, the phone's WireGuard handshakes, `ping 10.253.x.y` from a node answers,
and killing the app produces `m` on the nodes within 20 ms (clean) or 20 s (airplane mode). The netns tests
(`tools/netns-graph-test.sh`, `tools/netns-latency-test.sh`) are the model for proving each step with timestamps.

## 7. Things to know before designing

- The region is one process on one droplet. A member on a mobile network reaches it over the Internet; the RTT is
  paid once per event (`world` is one read) and once per facts change (announce).
- NAT: the RAM manager records the address a session arrives from (`[SESSION_PEER_V1]`); a member whose announced
  `ips` do not include that address is behind NAT (DESIGN-NAT.md step 1 in the rinab tree) — measured, never
  configured. WireGuard through NAT needs the member to initiate and keep alive (persistent-keepalive on the
  member's peer entry).
- Address change (wifi → cellular): the session survives if TCP survives; if not, the session ends, the member is
  gone (`m` everywhere), and the new session is a new member with the same `ns` — the region has no memory of the old
  one and that is by design ("hub has no memory"). Reconnect = re-run (a)–(d).
- Every refusal says why (`[REFUSAL_SAYS_WHY_V1]`): `{"ok":false,"error":"EEXIST","why":"10.7.7.0/24 is served by a"}`.
  Log the `why`.
- No fallbacks (John's rule): a refused announcement waits for the next region change and announces again; it does
  not try another path.
