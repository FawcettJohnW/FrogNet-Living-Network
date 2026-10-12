# PLAN-PONDS — one forward network, any number of participant ponds, and a notice that says "come here"

2026-10-10, Android thread, on fdf-2026-10-10-r16-a4. John: "we need the concept of a joined network vs. a participant
pond"; "we might be able to alt-tab the networks as they are needed ... bring the game network forward"; "some kind of
notification system so something happening in a pond that needs attention can say so. This is just a tuple."

## 1. The two roles

| | forward (exactly one) | participant (zero or more) |
|---|---|---|
| session to that pond's RAM manager | yes | yes, held open all the time |
| facts announced | the full node facts (role node, subnet, addrs, pubkey, maps, gen, view, capability) — today's facts.json | `{"role":"participant","name","guid"}` |
| routes, default, /etc/hosts, names | the daemon applies that pond's graph | none |
| mapping of this node's /24, hub peer, hub tunnel | yes | none (fdf-hub peers only role node/leaf) |
| databasehost / mediahost elections | candidate and voter | neither (no capability fact) |
| shared memory (apps, chat, presence) | yes | yes |
| attention notices | not needed (already here) | held read; shown when one arrives |

Every network's /24 is assigned once system-wide, so nothing collides when a pond is brought forward. Only one pond
is forward, so only one hub tunnel (10.253/16) exists at a time.

## 2. Files on a node (/opt/fdf/var)

- `ponds/<area>.env` — one per pond: `RINA_RAM=host:port RINA_AREA=<area> RINA_SITE_KEY=<key>` (each pond's site key
  is its own; only the forward pond's is used, to sign the mapping).
- `forward` — one line: the forward pond's area. The only thing a switch writes.
- `join.env` / `site.env` — kept, written from the forward pond's `ponds/<area>.env` (everything that reads them today
  keeps working: the daemon at start, fdf-facts, the installers).
- `facts.json` — unchanged: the node's full facts, written by fdf-facts and the daemon as today.

## 3. Pieces

1. **fdf-client, one per pond** — systemd template `fdf-client@<area>` (EnvironmentFile=ponds/<area>.env). Each
   watches the var directory (it already does, for facts.json) and also reads `forward`:
   `forward == my area` → it announces facts.json and registers facts.maps (today's behaviour);
   otherwise → it announces `{"role":"participant",...}` and withdraws any mapping it holds.
   A switch is therefore one re-announcement on each of two open sessions; nobody reconnects. `[FORWARD_IS_A_FACT_V1]`
2. **The daemon follows `forward`** — inotify on the file; on a change: set REGION_RAM/REGION_AREA from
   `ponds/<area>.env`, end the fdf-events child. The existing loss path runs (withdraw routes and names, tear down the
   tunnel, `REGION: lost`), then the loop rejoins the new pond AT ONCE: a switch is not a retry, so the 5 s
   `REJOIN_EVERY_5S_V1` sleep is skipped when the reason is `forward changed`. Logged as
   `REGION: forward <old> -> <new>`. No daemon restart. `[FORWARD_SWITCH_IN_PLACE_V1]`
3. **Commands** (in /opt/fdf/bin, nothing in /usr/local/bin):
   `fdf-pond-add <RAM> <area> <key>` (writes ponds/<area>.env, enables fdf-client@<area>: a participant at once),
   `fdf-pond-remove <area>` (refused for the forward pond), `fdf-forward <area>` (writes `forward`, join.env, site.env).
   `fdf-region-join` becomes `fdf-pond-add` + `fdf-forward` for a first pond.
4. **Attention** — a plain tuple in the pond's RAM: `(pond, attention, <writer ns>)`, bag
   `{"why":"match starting","from":"GameHost","ts":…}`. Written with the RAM's `write` (any member, on its own session);
   read by every participant with the RAM's held `read` (`after=<last id>`, waiting) on that pond's session — verified
   on the r16-a4 rina-host: the held read returned the moment the tuple was written. Cleared by the writer
   (`remove id`) or by its next notice. Writer: `fdf-attention <area> "<why>"` (C++, through fdf-client's library).
   Proposed: the region sweeps a writer's `(pond, attention, <its ns>)` cells at its session down, as it sweeps its
   mappings and its leaf /32 (gone is gone: a notice from a member that left is not a notice). `[ATTENTION_IS_A_TUPLE_V1]`
5. **Who shows it** — fdf-client@<area> (participant) logs `ATTENTION <area>: <why> (from <who>)` and writes
   `/opt/fdf/var/attention/<area>.json` for a desktop/dashboard; the phone app posts an Android notification with a
   `Bring forward` action (= `fdf-forward`). The forward pond's notices are not shown (you are there).
6. **The hub says which peers it holds** — fdf-hub adds `"peers":[<pubkeys>]` to its own facts whenever the set
   changes (one re-announce, one `p`); a member brings its tunnel up on the event that shows its key there, not before.
   Removes the first-handshake race (5,129 ms one run in three, a3) on switch, join and leaf bring-up alike.
   `[HUB_SAYS_ITS_PEERS_V1]`
7. **The phone** — one Member (session) per pond; Leaf (placement, tunnel) only on the forward one; the others hold the
   attention read. Bring forward = the same two re-announcements; the tunnel follows the forward pond's hub.

## 4. Tests (netns, before any hardware)

Two RAM managers + two hubs (`ponds A`, `B`), a node n in both, a member of each pond beside it:
1. n forward in A: A's routes/names on n, A's hub peer, n's /24 mapped in A; in B: a participant row, no peer, no
   mapping, no route from B on n.
2. a member of B writes an attention tuple → n's fdf-client@B logs it; ms from write to log.
3. `fdf-forward B`: stamps at forward written → A re-announce (participant) → A hub peer deleted → B re-announce
   (node) → B hub peer added → B routes on n → first handshake; A's routes and names gone from n; one session per pond
   throughout (no reconnect); the daemon not restarted. Target: total under a few hundred ms on the LAN.
4. switch back; ten switches in a row: no leftovers (routes, peers, mappings, attention cells).
5. the phone runner: the same with two Member sessions.
6. regression: r16's netns-graph-test, mapown, oracles, the three Android netns tests.

## 5. Order

6 (hub peers) → 1 (fdf-client forward/participant) → 2 (daemon follows forward) → 3 (commands) → 4 + 5 (attention)
→ 7 (phone). One build, all three tarballs, tag after r16-a4; merged with r18 as the a4 notes say.

## 6. Yours to say

- The attention sweep at session down (4): yes or no.
- The command names (3).
