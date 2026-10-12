# WINDOWS-BRIDGE — the bridge on Windows (fdf-kt-2026-10-11-a12, fdf-tun-2026-10-11-b8; region r19-a14)

John 2026-10-11: "Needs to work with windows, too."

## What it is

A Windows computer runs the same bridge as the phone: a member of every pond in `etc\ponds.txt` at once — a leaf of the
forward pond and reaching every other pond its own `(pond, config, net)` allows, through 1:1 aliases in 100.64.0.0/10.
It replaces `fdf-client.exe` plus the hand-made WireGuard tunnel of DEPLOY-REGION §3: no key, address or peer is typed.

| piece | on the phone | on Windows |
|---|---|---|
| the member (sessions, planner, ledger, resolve) | Kotlin in the app | **the same Kotlin** (`fdf-member-kt bridge`) on a bundled Java 21 runtime |
| the TUN | VpnService | a **Wintun** adapter "FrogNet" (wintun.dll, Administrator) |
| fdf-tun | libfdftun.so (linux/arm64…) | **fdf-tun.exe** (windows/amd64), the same engine and rewrite |
| address and routes | VpnService.Builder | `netsh interface ipv4 … store=active` (nothing persists) |
| WireGuard sockets kept out of the tunnel | `protect()` | `[NO_LOOP_ON_WINDOWS_V1]`: when Windows would route a hub's endpoint into the TUN (a hub inside 10/8 reached through a gateway), that device's socket is held to the default-route interface (IP_UNICAST_IF); said in the log |
| key and facts | made by the app | made on first run (`var\wg.key`, `var\facts.json`): X25519, identical to `wg genkey`/`wg pubkey` |
| resolve, the Communicator's servers | MemberService | `fdf-bridge.cmd ask resolve w:…` / `ask comms [pond]` → `--ram H:P --media H:P` for comms-app.exe |

## Package: `fdf-windows.zip` (one, for Windows x64)

`fdf-bridge.cmd`, `fdf-tun.exe`, `runtime\`, `lib\` (the member), `etc\ponds.txt`, `README.txt`, `BUILD.txt` — 22 MB.
`runtime\` is OpenJDK 21.0.8 for Windows x64 (pypi's jdk4py; no JDK site is reachable here) relinked to the three
modules the member uses (java.base, jdk.net, jdk.crypto.ec): its module image exploded with `jimage`, java.base's
ModuleHashes attribute dropped, relinked by `jlink`, the Windows natives of only those modules kept
(`windows/make_package.sh` does all of it; 12 MB of modules instead of 64). Its extnet.dll has the Windows TCP
keepalive options the member's 2+3×1 s keepalive needs. **Not included: `wintun.dll`** — it carries Wintun's Microsoft-signed driver and is published
only at https://www.wintun.net (wintun-0.14.1.zip, `bin\amd64\wintun.dll`), which this workspace cannot reach. fdf-tun
loads it only from its own folder or System32 and says so when it is missing.

## New code

- `tun/third_party/wintun` `[WINTUN_BINDING_V1]`: the Go API wireguard-go's `tun_windows.go` uses, written onto wintun.dll's
  exports from `api/wintun.h` (github.com/WireGuard/wintun); the upstream Go binding could not be fetched.
- `tun/main_windows.go` `[BRIDGE_WINDOWS_V1]`, `tun/main_linux.go` (the Linux/Android TUN and protect, moved from main.go).
- `tun/engine.go` `[UP_IS_CONFIGURES_V1]`: the device TUN no longer announces EventUp. wireguard-go's event reader
  brought the device up on its own, racing configure's Up: the loser saw "already up" and a failed bind (an unprotected
  socket on Android) was answered ok. Found by netns-fdftun-test's `--fd-socket` stage when the timing changed.
- member-cli: `ensureIdentity`, `publicKey`, `TunNet` (ip / netsh), `FDF_PONDS_FILE`, the forward pond defaulting to the
  first listed on the first run, `ask` (resolve / comms / status), the ctl `comms` query; capability says the OS.
- `windows/`: fdf-bridge.cmd (Administrator and wintun.dll checked first, each with what to do), README.txt, ponds.txt.

## Proven

- Linux, netns, the same code: reach stage (7) — the bridge, `ask resolve`, `ask comms pondB` (c's where cell → its alias,
  answering); fdf-tun suite on b8 20/20; `fdf-member-kt selftest` (keys equal `wg pubkey` for 20 `wg genkey` keys and the
  RFC 7748 vector; first-run identity made once and kept).
- The Windows package, under Wine (the zip as shipped): fdf-tun.exe starts and, with no wintun.dll, refuses saying where to
  get it; `fdf-bridge.cmd` refuses the same way before starting anything, `ask status` says the bridge is not running,
  `forward <pond>` writes var\forward; the bundled runtime derives public keys identical to `wg pubkey` (10 keys) and
  runs the member's planner, ledger and concurrency selftests (22 checks) — then stops inside Wine's own network-adapter
  API (NetworkInterface, while Java seeds SecureRandom), a Wine gap: Wine cannot carry a live member.

## Proven on hardware (2026-10-11, DESKTOP-1UJHGLF, Windows 11, Core i7)

- On Seattle5's LAN (10.250.250.20): placed on the mesh, no tunnel, announced with facts (a13); listed by the dashboard
  and the monitor.
- Moved to another network (192.168.0.2): the session ended on the change, rejoined off the mesh, the region gave it
  10.252.0.1, fdf-tun b8 brought up the Wintun adapter "FrogNet" (driver 0.14), netsh set 100.127.255.1/32 and the routes
  (rc=0), forward via hub StreamingFrog. Ping to a node behind the hub answered; ssh from the PC into a node through the
  bridge, and frognet_monitor run there showed every row correct, the PC included.
- Found on the way: on-mesh facts were empty (fixed a13); a wrong-architecture wintun.dll was reported as "not found"
  and the bridge carried on without a TUN (fixed a14/b9); Windows' multicast chatter logged as drops (silenced b10).

## Not yet proven on hardware

The Wintun adapter, netsh, the keepalive options on Windows sockets, the no-loop binding, a live session. The log says
each: `bridge: ponds …; Windows 11, Java 25.0.2`, `fdf-tun: Wintun driver 0.14`, `bridge: FrogNet 100.127.255.1/32 ->
rc=0`, `route 100.64.0.0/10 -> rc=0`, `forward via hub …`, `reach into …`, `… its socket is held to "Wi-Fi"`.
