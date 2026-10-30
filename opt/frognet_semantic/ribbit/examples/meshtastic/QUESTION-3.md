# QUESTION-3 — the register

Every red, and every place the contract is met differently, with the three questions in order: is the implementation
wrong; was the contract misread; does the incumbent expose something the memory declines to reproduce. A register entry
is a decision with its reason, not a bug.

## Decisions taken before Phase 1 (2026-10-30, John)

| # | incumbent | decision | reason |
|---|---|---|---|
| D1 | everything between the contract edges (2026-10-30 09:18, supersedes "services kept") | the goal is a node library: the Meshtastic client API contract and tests kept for existing apps, FrogNet inside, an extended API for multi-transport participation and richer services. Modules' messaging (periodic broadcasts, request/reply, airtime cooldowns, store-and-forward, MessageStore/WaypointStore, internal Observer pub/sub) is replaced: a module writes its owner's cell when the value changes, a request is a read of the owner's cell, and the FromRadio reply the client expects is built at the API edge. Kept: client API framing byte-exact, config surface, sensors, GPS, display, power | John: "Everything between the contract edges is yours"; the earlier "we don't replace existing services like traceroute" referred to FrogNet's IP traceroute |
| D2 | plumbing between machines: radio, TX queue, flood and next-hop relay, retransmission, implicit ACK, PacketHistory, to-phone queue | replaced: send = write to the node's own cells; receive = held read | no machine-to-machine communication |
| D3 | NodeDB (each node's copy of other nodes) | removed; every read of it is a read of the owner's cell | no copies, no aggregations |
| D4 | client want_ack ACK/NAK | built by the node serving the client: ACK when its write lands (NONE, request_id, from = destination); NAK MAX_RETRANSMIT, from = own node, when a trigger shows the destination's family gone | a DM is a write to the sender's own space and a read by the other; no routing packet goes. ACK half built in Phase 1 step 3b [MT_ACK_AT_THE_EDGE_V1]; NAK half is R1 |
| D5 | traceroute | not a design question; nothing special is built for it. smokemesh's chain assertions are judged when that suite runs | John, 2026-10-30 |
| D7 | channel encryption of NodeInfo/position/telemetry on the air | fact cells hold the plaintext protobuf; region membership is the trust boundary. Packets (mt, pkt) stay encrypted as stock | John's FD&F ruling: a region is invitation-only, connection = trust, no credentials (MQTT/bundles chat, Oct 28) |
| D8 | how a send reaches its destination | by destination type, read from the RAM at send time: (mtf, user, !N) present → N is a FrogNet node: write to memory, done; absent → N is stock: out through the stock path (MQTT edge when set, else the Meshtastic wire). No list or marker is kept: the held read on (mtf, user, *) is the set, and a node whose session ends leaves it. An on-air marker only for two FrogNet nodes that must find each other over LoRa before either reaches the RAM: a short beacon on a private PortNum (256–511), Phase 3 | John, 2026-10-30 09:38. Choosing a path by destination type, not a fallback |
| D9 | DM payload | the DM cell carries stock's encrypted payload, with the same key stock uses; to the memory it is text | John, 2026-10-30 11:40 |
| D6 | MQTT gateway, client MQTT proxy, Observer packet notification | gone inside; MQTT kept as an optional edge, off by default, reading cells and publishing Meshtastic's topics | no pub/sub inside the system |

## Open (to decide)

| # | incumbent | question | current phase-1 choice |
|---|---|---|---|
| O2 | XEdDSA-signed NodeInfo; updateUser refuses an unsigned identity change from a known signer | A fact cell's writer is the session that wrote it (gone-with-its-writer), not a signature. Who may write (mtf, user, !X)? | applied with signed=false through the same updateUser, so stock's refusal rules still run |

## Reds

| # | test | stock | Ribbit | the three questions | decision |
|---|---|---|---|---|---|
| R1 | step 4 S5: DM with want_ack to a node that has died | MAX_RETRANSMIT after 39.28 s (5 attempts), from own node | PKI_SEND_FAIL_PUBLIC_KEY at once: the dead node left the view, so there is no key to encrypt for | implementation: Phase 1 still encrypts per packet and has no want_ack at the edge. Contract: a failed delivery with a reason. D4 says MAX_RETRANSMIT from own node when the destination's family is gone | Phase 2 builds D4: the NAK the client gets is MAX_RETRANSMIT, at the gone trigger |
| R2 | step 4 S4: a node that dies | stays in every client's node list, last_heard ageing | leaves every client's list in 0.33 s (its family is gone) | not wrong, not misread: the incumbent keeps dead nodes because LoRa nodes drift in and out of range (last-heard, a stored key to DM them); on the memory a node's session is its presence | decided (John, 2026-10-30): gone is gone; the family GOES |
| R3 | smokemesh `test_smokemesh_traceroute_across_relay` asserts route == [B], routeBack == [B] on the harness's simulated A–B–C radio chain | relays append themselves to the route | nothing relays: every pair of nodes on the memory is direct, so the route is empty | not wrong, not misread: the test encodes a radio topology the memory does not have. meshtastic-mcp's own `test_traceroute_one_hop` expects route == [] for a direct pair, which is what the memory gives | registered (2026-10-30): traceroute is a user diagnostic; nothing else uses it once next-hop learning is gone |
