history: superseded by docs/OPERATING.md

# r19-a12 — identities in tuples, a network's own config, reach into other ponds, every network on one screen

Build `fdf-2026-10-10-r19-a12` (on r19-a11). Member app `fdf-kt-2026-10-10-a10`. Communicator unchanged (comms-app r3,
comms-android r4). Tarballs: `fdf-install-x86_64.run`, `fdf-install-aarch64.run`, `fdf-droplet.tgz`, the source.

John, 2026-10-10 evening: other networks are not forbidden — reach them through their RAM manager, as far as **that
network's own configuration** allows; map them 1:1 when their addresses collide with yours, **only if the RAM is set
up that way**; **a failed reach closes the session**; no fallbacks; and make frognet_monitor and fln_dashboard
multi-network aware. Per-pond address indexes were considered and dropped (John: "No index; NETMAP only").

## What changed

| tag | where | what |
|---|---|---|
| `IDENTITY_IN_TUPLES_V1` | rina_region.cpp `resolve`; fdf_where.cpp; fdf-comms.sh; Member.kt `resolve`/`endpoint`; MemberService; member-cli | A where cell names a machine by identity, `w:<guid>:<port>` (`fdf-where comms ram=self:8800`), never by address. Each reader resolves it **in its own scope**: the region's new `resolve` op gives a node's .1, a leaf's (or a reacher's) /32; a member that is not alive is `ENOENT` (gone is gone). A cell written with an address is not taken (`not an identity endpoint`). |
| `NET_CONFIG_IS_A_TUPLE_V1` | rina_region.cpp `publish_net_config` | Every RAM manager publishes `(pond, config, net)` at start, from `/opt/fdf/etc/net.json` (`RINA_NET_CONFIG`): `reach` none / listed / all, `map` allowed / refused, plus its overlay ranges (`hub`, `edges`, `leaves` — the same in every pond). No file: reach none, map refused, and it says so. A bad value: nothing published, and it says why. |
| `NET_CONFIG_IS_THE_RAM_MANAGERS_V1` | tools/apply_net_config.py (build.sh) | The network API can read that cell but neither write nor remove it (403, said). Every network write goes through one `api()`, so FNW1 and HTTP are both covered. |
| `REACH_IS_A_LEAF_HERE_V1` | rina_region.cpp, fdf-hub, fdf_graph.py | A participant that asks to reach (`"reach":true`, its WireGuard pubkey) is given a /32 by that pond's region, peered by its hub and routed by its nodes exactly as a leaf is. A participant that only reads gets none of it. |
| `REACH_V1` | fdf/bin/fdf-reach (new); fdf_client.cpp; graph_apply.py; fdf-pond-add | `fdf-client@<area>` with `FDF_REACH=1` runs `fdf-reach apply` on every region event of that pond: config → what may be reached → what collides with this machine's own scope → aliases (only if map allowed) → applied on its OWN interface `rch-<h>`, table, rules and chains. Refused = everything taken down, the reason logged and written to `reach/<area>.refused`, **the session ends (exit 4) and systemd does not restart it**. Session ends for any reason → the reach goes with it (SIGTERM handler, every exit path, `ExecStopPost`). The daemon re-plans every reach when the forward pond's routes change. |
| `REACH_SELF_ALIAS_V1` | fdf-reach | This machine's address in the other network is never put on an interface (every pond's leaves are 10.252/16: the forward pond may give the same address to its own phone). The interface holds a private /32 (100.127.255.x); SNAT to the given address on the way out, DNAT back on the way in. |
| `PONDS_ARE_LISTED_V1` | getPonds.php (new); frognet_monitor_py/draw_ponds.py (new) + main_loop/discovery; fln_dashboard32.html; fdf-client, fdf-pond-add (`ponds/<area>.ram`) | Every network this machine is in, read live from each pond's RAM manager: forward or participant, this machine's role there, the pond's config, its members (alive, address there, its alias here), the reach or why it was refused. The monitor draws a **Networks** section under the peer table; the dashboard a **Networks** panel at the top of the sidebar. |
| `STATIC_CLIENTS_V1` | tools/build_static_clients.sh (new); build_static_host.sh | `build/static` (the node/droplet binaries and what the netns tests run) is now built by a script; until today it was compiled by hand, and the static rina-host there was stale. |
| — | fdf_client.cpp (Windows) | The Windows client had not compiled since PONDS_V1 (`is_forward` undeclared); a Windows member is in one pond, its forward. Builds again. |

## Using it

**On a network's RAM manager** (the droplet, or a Pi that hosts a pond), to let others reach it — `/opt/fdf/etc/net.json`:

```
{"reach":"all","map":"allowed"}
```

`reach`: `none` (others may read its RAM only — the default), `listed` (only the endpoints its where cells name), `all`
(every /24 its members serve and every /32 it gave). `map`: `allowed` lets a machine whose own addresses collide reach
it through a 1:1 alias; `refused` makes that machine's reach fail. Restart the RAM manager; its log line
`[NET_CONFIG_IS_A_TUPLE_V1] (pond, config, net) = {...}` says what it published.

**On a node**, to reach a pond it takes part in:

```
/opt/fdf/bin/fdf-pond-add <RAM host:port> <area> <site key> reach
/opt/fdf/bin/fdf-ponds
/opt/fdf/bin/fdf-reach resolve <area> w:<guid>:<port>
```

`fdf-ponds` shows each pond and its reach (`reach: all as 10.252.0.1 there; 10.7.7.0/24 10.8.8.0/24 100.64.0.0/24=10.7.7.0/24`)
or `reach REFUSED` (journalctl -u fdf-client@<area> says why). `fdf-reach resolve` gives an identity endpoint of that
pond as host:port usable from this machine — the alias when its network collides with this one.

What a reach puts on the node (all of it named from the area, all of it removed by `fdf-reach down`): interface
`rch-<h>` (not `wg*`: the daemon's orphan scan owns those), routing table 20000+, rules at priority 9000+ (`to` each
reached prefix, `fwmark` for aliases, `from` its private address), iptables chains `FDFR<h>` (mangle mark, nat NETMAP,
nat DNAT) and `FDFRS<h>` (nat SNAT), and `/opt/fdf/var/reach/<area>.json`. **The main routing table is never written**:
the forward pond's daemon owns it, so the two never fight over a route.

## Proof (netns, this build)

`tools/netns-reach-test.sh` — ponds A (no config), B (reach all, map allowed), C (reach all, map refused), D (no
config); A's node a and B's node c both serve 10.7.7.0/24; A's phone and n's address in B are both 10.252.0.1.
**RESULT PASS 35/35** (docs/run-reach-2026-10-10.log):

- n's reach into B applied **361–411 ms** after its fdf-client@pondB started; c's 10.7.7.0/24 aliased 100.64.0.0/24
- n → 10.8.8.1 answers **b** (B); n → 10.7.7.1 answers **a** (A keeps it); n → 100.64.0.1 answers **c** (B's 10.7.7.1), and c saw n as 10.252.0.1
- b → 10.252.0.1 (n's address in B) answers **n**; n holds no address 10.252.0.1, and A's phone at 10.252.0.1 still answers n
- `fdf-reach resolve`: w:g-b:8080 → 10.8.8.1:8080, w:g-c:8080 → 100.64.0.1:8080; n's main table unchanged
- A changes under the reach (e joins A serving b's 10.8.8.0/24): the daemon re-planned in **848–938 ms**; 10.8.8.1 now answers e, b answers at its new alias 100.64.1.1, c keeps 100.64.0.1
- getPonds.php on n and the monitor's Networks section show both ponds, B's members, c at its alias
- B brought forward while n reaches it: the reach goes (the daemon routes B now), fdf-client@pondB keeps its session, nothing refused; A forward again: the reach is applied again and c answers at its alias
- n's session to B ends: interface, rules, chains, state file all gone; the alias answers nothing
- C: `REACH REFUSED into pondC: 10.7.7.0/24 (d in pondC) collides with 10.7.7.0/24 (a in pondA), and pondC's config says map refused` — the session closed, n not in C's participants, nothing left on n
- D: `REACH REFUSED into pondD: pondD's config says reach 'none'`; a member's write of a config: 403

Regression, same build: region-loop (Communicator on a node, the phone's Kotlin member and Java core, by identity:
`z ram=w:g-z:8800 -> 10.3.3.1:8800`) 20/20; ponds-daemon 21/21; hub 25/25; graph 17/17; latency done; the Kotlin
member's leaf-tunnel 10/10, placement 18/18, two-ponds 14/14, member-death as before (19 ms clean, ~4 s silent);
oracles: fdf_graph (new 11, 11c), hub (new 9, 9c), graph_apply, region, region backends, gen — all pass; the net
config test over FNW1 (published, read-only to members) PASS; app sources type-check against Android API 34;
fdf-client.exe builds. Dashboard rendered in Chromium with getPonds.php's real answer, no page errors
(docs/dashboard-networks-2026-10-10.png).

## Reviewed

An independent review of fdf-reach, fdf-client's reach code, the region ops, the config patch and getPonds.php found
eight defects and two minor ones; a second pass on the fixes found one new and one older one. All closed in this
build:
- the address another network's region gives went into a shell command (popen) -- now execv with an argv; area names
  and addresses are validated
- the daemon's replan and an fdf-client's apply could interleave on one area and be taken for a refusal (session ended
  for good) -- one lock for every apply/down/replan on the machine (`REACH_ONE_AT_A_TIME_V1`), which also makes alias
  and private-address allocation race-free
- two participant ponds colliding with EACH OTHER were not detected -- the scope now includes every other reach
- "not yet" left a stale reach up -- taken down until it is
- a transient failure (a RAM manager's 4 s timeout, a failing command) counted as a refusal -- now exit 6: logged,
  tried again on the next event, never a refusal; replan carries on with the other areas
- the daemon's 30 s kill of replan could leave half-built state -- replan runs beside the daemon's loop, never killed
- after a "not now", fdf-client forgot a reach that was still up; and replan could refuse the FORWARD pond after a
  switch (ending its session for good) -- the forward pond is never reached and never refused

## Not done / limits

- **The phone does not reach.** Android's VPN interface does no NAT, so a phone can't hold a private address and map
  collisions; the app never asks for reach (a participant pond is read-only there). Its Communicator finds servers by
  identity as before.
- **No per-pond index** (John's call): every pond's leaves are 10.252/16 and its hub 10.253.253.254, so another pond's
  leaves always collide with yours — reaching them needs `map allowed`. Hubs are never reach destinations.
- **Aliases come from 100.64.0.0/10.** A node whose own uplink is carrier-grade NAT in that range: the blocks it
  routes are skipped; if none is free the reach is refused, saying so.
- **Not on hardware yet**: kernel WireGuard for `rch-*` (netns used wireguard-go, as wg-quick does there), NETMAP on
  the Pi kernels (iptables-nft's xt compat), the dashboard on a node's Apache.
- The intermittent region-loop non-convergence (2/59 earlier) happened once more today, in the first run (which also
  ran a stale static rina-host); the two runs on this build converged. Cause still not found; DIAG-CONVERGE stays in
  (that run's dump is in /tmp/claude-0/runs/loop1.log on the build machine, not kept).
