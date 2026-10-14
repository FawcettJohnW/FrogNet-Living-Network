FD&F bridge for Windows (fdf-kt-2026-10-11-a15, fdf-tun-2026-10-11-b11: BUILD.txt)
===================================================================================

What it is: this computer as a member of every pond in etc\ponds.txt at once -- a leaf of the forward pond (its
10.0.0.0/8, through its hub) and reaching every other pond that pond's own (pond, config, net) allows, through 1:1
aliases in 100.64.0.0/10, even when the ponds use the same 10.x addresses. The same member code as the Android app;
fdf-tun.exe carries the packets on one Wintun adapter ("FrogNet"). Replaces fdf-client.exe + the hand-made WireGuard
tunnel (stop both before starting this: the same key must not be in two tunnels).

Once:
  1. Unzip to C:\fdf (any folder).
  2. Get wintun-0.14.1.zip from https://www.wintun.net and copy bin\amd64\wintun.dll into C:\fdf.
     (The only file not included: it carries Wintun's signed driver and comes only from its authors.)
  3. Edit C:\fdf\etc\ponds.txt: replace the example line AREA=RAM-HOST:51888 with one <area>=<host:port> line
     per pond (the area and RAM manager fdf-region-create printed). fdf-bridge.cmd refuses while the example is there.

Run, in a terminal opened as Administrator:
  cd C:\fdf
  fdf-bridge.cmd

The first run makes var\wg.key and var\facts.json (name = this computer, guid made once, pubkey from the key); nothing
is typed. Every line says what happened: the ponds, each join, "bridge: forward via hub <hub> as 10.252.x.y",
"[<pond>] reach into <pond> (...) as <addr> there ...; aliased 100.64.a.0/24 -> 10.x.y.0/24", or
"REACH REFUSED into <pond>: <why>" (that pond's session ends; the others go on). Ctrl-C leaves every pond; the adapter,
its routes and every tunnel go with it.

From another terminal (no Administrator needed):
  fdf-bridge.cmd ask resolve w:<guid>:<port>     -> OK host:port   (AMBIGUOUS ... when two ponds answer for it)
  fdf-bridge.cmd ask comms                       -> OK --ram H:P --media H:P   (the forward pond's Communicator)
  fdf-bridge.cmd ask comms <pond>                -> the same for another pond, through its alias
  fdf-bridge.cmd ask status                      -> each tunnel's handshake and bytes
  fdf-bridge.cmd forward <pond>                  -> bring that pond forward

The Communicator: comms-app.exe --name John <the --ram/--media that "ask comms" printed> --camera ... --mic N

Layout: fdf-bridge.cmd, fdf-tun.exe, wintun.dll (yours), runtime\ (OpenJDK 21, only what the member uses), lib\ (the member), etc\ponds.txt,
var\ (this computer's key, facts, forward pond, alias ledger -- made here, never shipped).
