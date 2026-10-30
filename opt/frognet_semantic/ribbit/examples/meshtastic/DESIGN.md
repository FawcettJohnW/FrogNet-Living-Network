# Meshtastic on Ribbit — DESIGN v2

**Goal (John, 2026-10-30):** a node library that satisfies the Meshtastic client API contract and tests for existing
apps, runs on FrogNet internally, and exposes an extended API for multi-transport participation and richer services.
Everything between the contract edges is replaced. This supersedes v1's "services kept unchanged" (QUESTION-3 D1): the
modules' messaging goes too, and every reply a client expects is built at the API edge from reads of owner cells.
The sections below that say a module or its request/reply is kept are Phase 1 (mechanical) scope only.

Supersedes v0, which replaced the services themselves (replies synthesized at the serving node). That was wrong:
the services are kept; only the plumbing that makes nodes talk to each other is replaced.

## Split

**Kept unchanged — the services.** Meshtastic's modules and their application semantics: traceroute, position,
telemetry, nodeinfo, admin (incl. session passkey), key verification, waypoints, store-and-forward, range test,
neighbor info, serial, remote hardware, and the client API (PhoneAPI: config dump, heartbeat, QueueStatus, client
rate limits). A module's request and its reply are ordinary packets.

**Replaced — the plumbing.** Everything that moves packets between machines or repairs that movement:
RadioInterface and SimRadio, the TX queue, FloodingRouter / NextHopRouter relay, ReliableRouter retransmission and
implicit ACK, next-hop learning and route health, PacketHistory and every duplicate filter, the to-phone queue.

| truth | owner | indices | latest / distinct | readers | trigger (held read) |
|---|---|---|---|---|---|
| a MeshPacket a node sends (any port, as the module built it) | the sending node | `(mt, pkt.<channel>, <from>/<id>)` | distinct; retention declared per family | every participant | `(mt, pkt.*, *)` after the last id taken → into the stock receive path (decode, modules) |
| User incl. public key | the node | `(mt, user, <node>)` | latest | every participant (PKI key lookups, config dump node list) | `(mt, user, *)` |
| position | the node | `(mt, pos, <node>)` | latest | every participant | `(mt, pos, *)` |
| telemetry, one cell per variant | the node | `(mt, tel.<variant>, <node>)` | latest | every participant | `(mt, tel.*, *)` |
| status message | the node | `(mt, status, <node>)` | latest | every participant | `(mt, status, *)` |
| favourite / ignored / muted mark on a peer | the marking node | `(mt, mark, <me>/<peer>)` | latest | the marking node's services and clients | — |
| liveness of a node | — (session-bound family) | — | — | — | the family disappears when the node's session ends |
| last_heard | — | — | — | — | derived: the owner cell's updated time |

Sending = one write. Receiving = the held read firing. No packet goes from machine to machine; nothing is
acknowledged between participants.

**MQTT (decided 2026-10-30).** Meshtastic's MQTT gateway, the client MQTT proxy and the in-process Observer
packet notifications are gone inside the system; held reads replace them. MQTT stays as an **optional edge**: one
participant, off by default, that holds reads on the cells and publishes them as Meshtastic's MQTT topics for
outside integrations. MQTT module config stays owner config cells, readable and writable as stock.

## Open

- **want_ack: closed (2026-10-30).** Stock's ACK is a packet from the destination's router (plumbing), removed.
  The node serving the client builds the client's ROUTING_APP packet itself: ACK when the client's write lands
  (error NONE, request_id = message id, from = destination); NAK only when a trigger shows the destination's family
  gone (error MAX_RETRANSMIT, from = own node). Same fields as stock.
- **NodeDB: removed (decided 2026-10-30: no copies, no aggregations).** Each node writes its own facts once, in its
  own cells: User with public key `(mt, user, <node>)`, position `(mt, pos, <node>)`, each telemetry variant
  `(mt, tel.<variant>, <node>)`, status `(mt, status, <node>)`. Every NodeDB read a service makes becomes a read of
  the owner's cell: a PKI key is one read of `(mt, user, <peer>)`, and the config dump's node list is a read of the
  `(mt, user, *)` family when the dump runs. Nothing is stored about another node: no node list, no last_heard
  table (derived from the cell's updated time), no satellites, warm tier, eviction or persistence of others' data.
  Favourite / ignored / muted are the local node's own facts about a peer, written in its own cells
  `(mt, mark, <me>/<peer>)`.
- **Traceroute: closed (2026-10-30).** A service of the network underneath; FrogNet keeps real hops (IP traceroute
  across a FrogNet path shows the intermediate node). Unchanged by the conversion. smokemesh's chain tests
  (`route == [B]`, hopLimit=0 reach) run on a real A–B–C path in FrogNet's simulator, and the test decides.
- **Channel encryption.** Stock encrypts per channel / PKI before the router sends. It decides which nodes can read a
  packet, so it stays with the packet as written.

## Phase 1 steps (mechanical, CONVERTING §3.4: same shape as the incumbent, one at a time, the suites after each)

| step | tag | what changes | status |
|---|---|---|---|
| 1 | `[MT_PACKETS_ON_THE_RAM_V1]` | send = write into the node's own ring (mt, pkt, <node>.<n>); receive = held read on (mt, pkt) into the UDP handler's ingress path | done: mcp mesh e2e PASS on RAM only, smokevirt 40/40 |
| 2a | `[MT_OWN_FACTS_V1]` | each node writes its own facts to its own cells: (mt, user, <node>) with public key, (mt, pos, <node>), (mt, tel.<variant>, <node>), (mt, status, <node>), whenever its own entry changes | done |
| 2b | `[MT_NODEDB_IS_A_HELD_VIEW_V1]` | NodeDB stops building other nodes from received packets; its tables are a held view of those families (read, then hold; best practices §3e), so its ~50 methods and 70 calling files are untouched. Persistence, eviction and the warm tier of other nodes' data go | done |
| 3a | `[MT_ONLY_OWN_PACKETS_V1]` | a node writes only its own packets into the memory; a relay would be a second copy of another node's truth (measured in 2b: every packet landed twice). Other transports still relay | done: mesh e2e PASS, each packet once |
| 3b | `[MT_ACK_AT_THE_EDGE_V1]` | QUESTION-3 D4, ACK half: a DM with want_ack to a FrogNet node (its user cell is in the view, D8) is written once and not retransmitted; the sender's node hands its client the ACK (NONE, request_id, from = destination) when the write lands, through the same MeshService path a received ACK takes. The destination sends no ACK to a FrogNet sender. A stock destination keeps stock retransmission and ACK. NAK half (R1) not built yet | done: step 4 S3 ACK 0.04 s from the destination, 0 ACK packets in the memory; smokevirt 40/40; native 88/88 |
| 4 | | bad data (CONVERTING §3.5): kills mid-flight, a node's session ending with a want_ack open, adversarial ordering, many nodes, malformed cells | done (harness/step4-bad-data.sh), rerun at every step |

A held view is the memory's truth kept current by a held read, never a second truth: nothing in it is written by this
node, nothing is persisted, and when the owner's family goes the view's row goes with it.

## Phase 2 — the truth table (greenfield; written before any code, CONVERTING §4a)

The library replaces everything between the contract edges. The client API (ToRadio/FromRadio framing, byte-exact),
the config surface and the incumbent's tests are kept; sensors, GPS, display and power drivers are kept and write
through it. Addresses are (space, variable, instance); `!N` is a node number.

| truth | owner (one writer) | address | latest / distinct | readers | trigger (held read) → action | derived instead |
|---|---|---|---|---|---|---|
| a node's identity: User incl. public key | the node | (mt, user, !N) | latest; written at start and when the owner changes | every edge (node list in the config dump, PKI keys, D8 path choice), extended API | (mt, user, *) → NODEINFO packet / node_info to its clients; gone → the node leaves every list (R2) | FrogNet membership (D8): the cell exists |
| position | the node (its GPS or its client) | (mt, pos, !N) | latest; written when it changes, never on a timer | every edge, extended API | (mt, pos, *) → POSITION packet to clients, truncated to the channel's precision at read | — |
| telemetry, one cell per variant | the node (its sensors) | (mt, tel.<variant>, !N) | latest, on change | every edge | (mt, tel.*, *) → TELEMETRY packet | — |
| status | the node | (mt, status, !N) | latest | every edge | (mt, status, *) | — |
| a message a client sends to a channel | the sending node | (mt, msg\|<channel>, !S.<n mod ring>) | distinct; ring per sender; retention ≥ what stock holds for a reconnecting client | the edges of nodes holding that channel | (mt, msg\|<channel>) past the edge's mark → packet to clients | — |
| a direct message | the sending node | (mt, dm\|!R, !S.<n mod ring>) | distinct; recipient in the variable, so the recipient reads only its own; payload encrypted as stock, same key (D9) | node R's edge | (mt, dm\|!R) past its mark → packet to R's clients | — |
| want_ack on a sent message | — | — | — | — | the sender's edge: ACK NONE when its write lands and R's user cell exists (from = R); NAK MAX_RETRANSMIT when R's user cell goes (R1) | yes |
| where a client of node N has read to | node N's edge | (mt, mark, !N.<client>) | latest | node N's edge (reconnect resumes from it) | — | replaces the to-phone queue |
| config, module config, channels, owner settings | the node | (mt, cfg.<type>, !N), (mt, chan.<i>, !N) | latest, permanent (`[PERMANENT_CELL_V1]`: filed and written back on restart) | the node's edge (config dump), remote admin readers | — | replaces NodeDB load/save and preference files |
| a remote admin request (set_*, commands) from node A's client to node B | node A | (mt, admin\|!B, !A.<n>) | distinct | node B | (mt, admin\|!B) → B validates (session passkey, admin keys) and rewrites its own cfg cells | — |
| a remote admin refusal | node B | (mt, admin.err\|!A, !B.<n>) | distinct | node A's edge | (mt, admin.err\|!A) → Routing error to A's client (exceptions only come back) | — |
| remote admin get_* | — | — | — | — | node A's edge reads B's cfg cells and B's passkey cell; builds get_*_response | yes |
| admin session passkey | node B | (mt, passkey, !B) | latest | requesters' edges | — | — |
| waypoint | the creating node | (mt, wp, !N.<id>) | latest per id; delete = a withdrawn value, not a removal | every edge | (mt, wp, *) | — |
| key verification step | each of the two nodes | (mt, kv\|!peer, !me) | latest | the peer | (mt, kv\|!me) → next step, ClientNotification | — |
| marks on a peer (favourite, ignored, muted) | the marking node | (mt, peer, !me.!N) | latest, permanent | the marking node's edge | — | — |
| liveness, last_heard | — | — | — | — | — | the owner's family exists (session-bound); last_heard = the cell's write time |
| request replies (position, telemetry, nodeinfo) | — | — | — | — | the requesting edge reads the owner's cell and builds the reply (request_id, from = owner); no cell → NO_RESPONSE | yes |

Participants, each one job:
- **edge** (per node): serves the client API; answers each request from a write or a read; holds reads on the families
  its clients receive; builds every ACK, NAK and reply.
- **own-facts writer** (per node): GPS, sensors, owner changes → the node's own cells, on change.
- **admin applier** (per node): holds (mt, admin|!me), applies intents to its own cells.
- **MQTT edge** (optional, D6) and **over-the-air edge** (Phase 3, D8: destination's user cell absent → stock path).

Gone entirely: radio TX queue, flood/next-hop relay, retransmission, implicit ACK, PacketHistory and the other
duplicate filters, NodeDB and its persistence, to-phone queue, store-and-forward, MessageStore/WaypointStore, periodic
re-broadcasts, airtime cooldowns, Traffic Management, the internal Observer pub/sub, session passkey timers beyond
the passkey cell's own freshness (read-time arithmetic).

Decided for this table (2026-10-30): a node whose session ends is gone from every list (R2); a DM cell carries stock's
encrypted payload with the same key (D9); nothing special is built for traceroute (D5).
