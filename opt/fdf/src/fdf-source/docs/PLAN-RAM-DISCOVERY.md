# PLAN-RAM-DISCOVERY — discovery and routing from the shared RAM, not from the probe walk

2026-10-09. Replaces Stage 4's "discovery gathers from the region" with the design agreed today: every node
writes what it can see into the RAM as address tuples, every node builds the graph from the whole table, and
routes, default routes, /etc/hosts and the WireGuard mappings fall out of that graph. Split and merge are the
same operation: "attach to the first RAM manager on my list, write my rows, rebuild."

## 1. The table

One tuple per address a host can see:

    <host>:<iface>:<ip>  ->  value

| row | value | who writes it |
|---|---|---|
| `Seattle7:wlan0:10.170.170.1` | `FrogNetHost.Seattle7` | the host, for each address it holds on its own /24 |
| `Seattle7:wlan1:10.250.250.63` | `Seattle7` | the host, for a lease it holds on someone else's /24 |
| `Seattle5:wlan0:10.250.250.63` | `Seattle7` | the host that handed out the lease (its dnsmasq lease table; value = client name, or the mac when the client has no FrogNet name) |
| `Seattle3B:wlan1:192.168.0.33` | `uplink eth 1000` / `uplink wifi 72` / `uplink wifi 72 dead` | the host, for an interface with a non-10.x address; link type and rate measured by the writer, `dead` if its one ping out that interface failed |
| `Seattle7:wg0:10.253.170.170` | `StreamingFrog` | the host, for its region tunnel edge (value = the hub) |
| `Laptop:wg:10.252.0.7` | `Laptop` | a leaf (no /24): one row, a /32 |

Carried as `facts.addrs` on the host's announce row — a list of `{"iface","ip","value"}` — so it is written by the
thing that already writes facts (the daemon's `write_facts`), announced by the thing that already announces
(fdf-client's inotify re-announce), owned by the host's session (gone when the session is gone), and delivered to
readers by the thing that already delivers facts (`participants`, and `p` events). No new RAM op for the table.

Capability tuples (`cap:<host>:<role>`) stay as they are (SD:capability.*). Perf stats stay in databasehost.

## 2. The graph (every reader, from the whole table)

- **Link**: the same address under two hosts (`10.250.250.63` under Seattle7/wlan1 and Seattle5/wlan0). The side
  whose row sits on its own /24 (holds the .1 of that /24) is the parent; the other is the child.
- **Uplink**: a host with an `uplink … ` row that is not `dead`.
- **Hub link**: a `wg*` row whose value is a hub.
- **Subtree(h)**: h and everything below h through child links, cut at the first host that has its own uplink.
- **Default route** of a host with no uplink of its own: toward the nearest host on its upward path that has a
  live uplink; among several at the same distance, highest measured rate wins (eth 1000 beats wifi 72), ties →
  lower .1. No 8.8.8.8 probing by readers; only the writer of an uplink row pings, once, out that interface.
- **Routes** from host h: for every other /24 in the table, next hop = the address on h's side of the first link
  on the path; for a /24 with no LAN path, `dev wg0` (the hub). Own /24s and leases: none.
- **Names**: every `.1` row's value is a hosts line (`<ip> <value>`), plus the /32 leaves.
- **Mappings** registered by h (what the hub may forward to h): h's edge /32 implicitly, plus the /24s of
  Subtree(h), only if h has a hub link. A host inside someone else's subtree registers nothing.
- **Duplicate .1**: refused at the RAM manager (§4); a reader that still sees two live `.1` rows for one address
  routes neither and logs which two hosts collide.

## 3. The reader's discipline

- Rebuild on every `p` or `m` event (the held fdf-events session), never on a timer.
- Keep the last applied set (routes, hosts lines, mappings). Apply the diff: install what is new at once;
  remove what disappeared only after a hold-down (default 60 s; a session blip is 20 s) during which the row has
  not come back. Nothing is removed on a transient.
- `/etc/hosts` and the dnsmasq forwarders are rewritten only when the generated content differs; dnsmasq is
  restarted only then.
- runMerge is forked only when the applied set changed (routes, hosts, or default route), never per event.
  This is what stops the loop seen today (every facts write was an event, every event a merge).
- `view.json`: `steady` with the graph's generation after each apply; `recalc` the moment the region is lost.
- One WARNING line per rebuild says it all: `GRAPH gen=N hosts=… links=… uplinks=… default=… routes=+a/-b
  hosts=+c/-d maps=… held=[…]`.

## 4. The RAM manager's two checks (rina-host)

1. **Duplicate .1** [DUP_DOT1_REFUSED_V1]: an announce whose `facts.addrs` claims a `.1` already present in a
   live participant's `addrs` under another host is refused: `{"ok":false,"error":"EEXIST","why":"10.170.170.1 is
   held by <host>"}`. fdf-client logs it at ERROR, stays out, and retries only when the region reports a `p`
   event (the holder may have left), not every 2 seconds.
2. **Version** already exists; the `participants` answer also carries `addrs` (it carries facts, so it does).

## 5. Who owns what (division of responsibility, 2026-10-09 12:12)

FD&F is the authority for membership and topology; FrogNet is the local configuration and forwarding-policy
engine. No second discovery system: the crawl does not run in region mode.

- **RINA** defines the DIFs, membership, enrollment, recursion (the RAM manager, sessions, announce/participants).
- **LISP** maps identities to locations (map_register/map_list: the prefixes each hub forwards for which peer).
- **Ribbit** holds the published facts and delivers every change to every member (facts on the announce row, p/m events).
- **FrogNet** configures the local machine, publishes its facts, measures candidate paths, installs what passed.

Operating sequence: installer gives local connectivity → the node enrolls in its DIF (fdf-join) → FD&F discovers
the node (its own fdf-facts writer publishes interfaces, addresses, leases, capabilities) → the RAM has it →
each node computes its plan from the shared table and validates candidates → FD&F handles joins, leaves,
reattachments, splits and merges; FrogNet applies the resulting local configuration.

Two kinds of truth: structural state lives in the RAM (FD&F); measured reachability and installed routes are the
node's own (FrogNet) and never go to the RAM.

| piece | owner | file | tag |
|---|---|---|---|
| facts writer: interfaces, leases held/given (with macs), uplink measure, wg edges, capabilities → facts.json | FD&F | `/opt/fdf/bin/fdf-facts` (+ `fdf-facts.service`; inotify on the lease file and `ip monitor`) | `[FDF_FACTS_V1]` |
| the graph: links, forest, exit rank, routes/hosts/maps per host, conflicts | FD&F | `/opt/fdf/lib/python/fdf_graph.py`, CLI `/opt/fdf/bin/fdf-graph` | `[GRAPH_V1]` |
| membership checks: overlapping served prefixes refused (EEXIST, holder named) | FD&F | `src/rina_region.cpp` announce | `[PREFIX_OVERLAP_REFUSED_V1]` |
| a refused join waits for a `p` event, says why at ERROR | FD&F | `src/fdf_client.cpp` | `[JOIN_REFUSED_WAITS_V1]` |
| apply: admission probe, handshake gate, hold-down, diff, routes, hosts sentinel, view.json; runMerge only on change | FrogNet | `internet_tunnels_v3/graph_apply.py`, run_region_loop | `[GRAPH_APPLY_V1]` |
| region mode: no crawl; hosts are the plan's; dbhost/mediahost election unchanged | FrogNet | `discovery/live.py` (DISCOVERY_SOURCE=region) | `[NO_CRAWL_IN_REGION_V1]` |
| mappings = the plan's `maps` | FrogNet publishes, LISP holds | `internet_tunnels_v3/region.py` publish_maps | `[MAPS_FROM_GRAPH_V1]` |

## 6. Tests (before hardware)

- `tests/graph_oracle.py`: tables for the agreed topologies, exact expected routes/hosts/maps per host:
  1. `x -> y -> z -> RAM -> a -> b -> c`: x's route to c's /24 is via y's address on x's wire; z registers x,y,z;
     a registers a,b,c; the hub's allowed-ips per peer are exactly those.
  2. two uplinks in one LAN (eth 1000 vs wifi 72): every host in that LAN picks the eth one; equal rates → lower .1.
  3. duplicate .1 from two hosts: no route to it, both named in the log line.
  4. hold-down: a row vanishes and returns within 60 s → no removal; stays gone → removed once.
  5. no changes → no hosts write, no dnsmasq restart, no runMerge.
  6. a leaf (/32) appears → hosts line and a /32 route via wg0, nothing else changes.
- rina-host: announce with a duplicate .1 → EEXIST with the holder's name; after the holder leaves → accepted.
- netns run (tools/netns-hub-test.sh, extended): two sites behind NAT, a child node behind one of them; ping
  child → far site through two tunnels and one LAN hop; the hub's `wg show` allowed-ips as in test 1.

## 7. Deployment

Same three tarballs (fdf-node-arm64.tgz, fdf-node.tgz, fdf-droplet.tgz), build tag bumped; `fdf-region-join`
unchanged. On a node nothing new to configure: the writer derives everything from the kernel and dnsmasq.
