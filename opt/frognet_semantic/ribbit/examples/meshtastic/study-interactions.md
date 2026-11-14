# Meshtastic request/response, acknowledgement, retry and cache inventory

Source read: firmware `/home/claude/mesh/firmware` (commit 332b5b0, version 2.8.2), protobufs `/home/claude/mesh/pb/meshtastic`, Python client `/home/claude/mesh/mpy/meshtastic`.

Path abbreviations used in citations:

- `fw/` = `/home/claude/mesh/firmware/src/`
- `pb/` = `/home/claude/mesh/pb/meshtastic/`
- `py/` = `/home/claude/mesh/mpy/meshtastic/`

All line numbers are for this checkout. "Client" means a program on the client API (phone app, Python library, web) connected over BLE, serial, TCP, HTTP or the packet API. "Node" means a firmware instance. "Local node" is the node a client is attached to.

This checkout contains code that is not part of every Meshtastic build: route health ("M1".."M4") in `NextHopRouter`, ACK proofs (`AckProof`), the Traffic Management module, a warm node tier, lockdown/access control, DMShell. Sections below describe this code as it is, not any other version.

---

## 0. Shared machinery (used by every request/response below)

### 0.1 Wire fields

- `Data.want_response` (`pb/mesh.proto:1281`), `Data.request_id` (`pb/mesh.proto:1303`), `Data.reply_id` (`pb/mesh.proto:1308`).
- `MeshPacket.hop_limit` (`pb/mesh.proto:1978`), `MeshPacket.want_ack` (`pb/mesh.proto:1991`), `MeshPacket.priority` (`pb/mesh.proto:1997`), `MeshPacket.ack_proof_status` (`pb/mesh.proto:2077`).
- Priority values: UNSET 0, MIN 1, BACKGROUND 10, DEFAULT 64, RELIABLE 70, RESPONSE 80, HIGH 100, ALERT 110, ACK 120, MAX 127 (`pb/mesh.proto:1690` enum).
- Routing.Error values: NONE 0, NO_ROUTE 1, GOT_NAK 2, TIMEOUT 3, NO_INTERFACE 4, MAX_RETRANSMIT 5, NO_CHANNEL 6, TOO_LARGE 7, NO_RESPONSE 8, DUTY_CYCLE_LIMIT 9, BAD_REQUEST 32, NOT_AUTHORIZED 33, PKI_FAILED 34, PKI_UNKNOWN_PUBKEY 35, ADMIN_BAD_SESSION_KEY 36, ADMIN_PUBLIC_KEY_UNAUTHORIZED 37, RATE_LIMIT_EXCEEDED 38, PKI_SEND_FAIL_PUBLIC_KEY 39 (`pb/mesh.proto:1121-1196`).

### 0.2 Module dispatch and the reply rule (`MeshModule::callModules`, `fw/mesh/MeshModule.cpp:116-234`)

- Every received or locally delivered packet is passed to each registered module in construction order. `RoutingModule` is constructed last on purpose (`fw/modules/Modules.cpp:307-309`).
- A module is called if it wants the packet (`wantPacket`, default: port matches, `fw/mesh/SinglePortModule.h:21`) and the packet is decoded (or the module sets `encryptedOk`), and the packet is to us/broadcast or the module is promiscuous (`fw/mesh/MeshModule.cpp:139`). Packets with source `RX_SRC_LOCAL` are skipped unless the module sets `loopbackOk` (`fw/mesh/MeshModule.cpp:141-144`).
- Bound channel: if a module has `boundChannel` and the packet arrived on another channel (and `from != 0`), a `want_response` request gets `NOT_AUTHORIZED` (`fw/mesh/MeshModule.cpp:160-170`).
- Reply: if `want_response` is set, the packet is to us or broadcast, it is not from us unless it is also to us, and nobody has replied yet, the module whose port matches gets `sendResponse()` (`fw/mesh/MeshModule.cpp:184-192`). `sendResponse` calls the module's `allocReply()`; a non-null reply is passed to `setReplyTo` and becomes `currentReply` (`fw/mesh/MeshModule.cpp:247-257`). Only one reply per request.
- `setReplyTo` (`fw/mesh/MeshModule.cpp:262-274`): `to = getFrom(request)`, same channel, `hop_limit = getHopLimitForResponse(request)`, `want_ack = request.want_ack` (forced false when the request came from a local client, `from == 0`), priority RELIABLE if unset, `request_id = request.id`.
- After all modules: if `want_response` and to us/broadcast, `currentReply` is sent with `service->sendToMesh` (`fw/mesh/MeshModule.cpp:211-215`). If no module replied, the request is not from us, and no module set `ignoreRequest`, the node sends a Routing `NO_RESPONSE` NAK to the requester (`fw/mesh/MeshModule.cpp:216-228`). `ignoreRequest` is how a module declines without a NAK (cooldowns, busy, etc.).
- A reply prepared by a module when no response was requested is discarded (`fw/mesh/MeshModule.cpp:194-199`).
- `isMultiHopBroadcastRequest()` = broadcast and `hop_limit < hop_start` (already relayed) (`fw/mesh/MeshModule.h:116-122`). Telemetry and traceroute use it to refuse replies to relayed broadcast requests.
- `getHopLimitForResponse` (`fw/modules/RoutingModule.cpp:64-81`): if hops used > configured hop limit, reply uses hops used (not in event mode); if request `hop_start == 0`, reply hop limit 0; if `hopsUsed + 2 < limit`, `hopsUsed + 2`; else configured limit.
- Default priority if unset at send (`fixPriority`, `fw/mesh/MeshPacketQueue.cpp:43-67`): RELIABLE if `want_ack` else DEFAULT; ROUTING_APP -> ACK; TEXT or ADMIN -> HIGH; `request_id != 0` -> RESPONSE; `want_response` -> RELIABLE.

### 0.3 Delivery to the client

- `RoutingModule` wants every packet (`fw/modules/RoutingModule.h:39`). For each packet it calls `router->sniffReceived` (ACK/NAK/relay logic) and, if the packet is broadcast or to us and `from != 0`, `service->handleFromRadio` which queues it for the client (`fw/modules/RoutingModule.cpp:30-38`).
- `MeshService::handleFromRadio` updates NodeDB (`fw/mesh/MeshService.cpp:93`), drops echoes of our own packets that are not addressed to us (`fw/mesh/MeshService.cpp:120-123`), stamps `ack_proof_status`, and queues a copy (`fw/mesh/MeshService.cpp:126-131`).
- A reply the local node generates for a local client is addressed to our own node number; `sendToMesh` forces `ccToPhone` for local deliveries from `RX_SRC_LOCAL` (`fw/mesh/MeshService.cpp:391-395`), so the client receives it as a FromRadio `packet` with `decoded.request_id` set.

---

## 1. Routing ACK / NAK and retransmission

### 1.1 Participants

Originating node (O), relays (R), destination (D), the client of O. Classes: `ReliableRouter` -> `NextHopRouter` -> `FloodingRouter` -> `Router` (`fw/mesh/ReliableRouter.h:9`, `fw/mesh/NextHopRouter.h:82`).

### 1.2 Who sends an ACK

All ACK/NAK packets are built by `MeshModule::allocAckNak` (`fw/mesh/MeshModule.cpp:55-100`): port ROUTING_APP, payload `Routing{error_reason}`, priority ACK (120), `request_id = original id`, `to = original sender`, hop limit given by caller, optional `relay_node`/RSSI/SNR copied from an overheard relay, optional ACK proof appended (`ackProofAttach`, `fw/mesh/MeshModule.cpp:89-94`). `RoutingModule::sendAckNak` sets `want_ack` per caller and sends with `router->sendLocal` (`fw/modules/RoutingModule.cpp:50-62`).

Generation sites:

1. Final recipient, explicit ACK (`ReliableRouter::sniffReceived`, `fw/mesh/ReliableRouter.cpp:120-195`), only when the packet is to us (`:123`) and no module already replied (`MeshModule::currentReply`, `:124`; RoutingModule runs last so a module's reply is known at this point):
   - Decoded, `want_ack`, and a DM text (TEXT_MESSAGE_APP / TEXT_MESSAGE_COMPRESSED_APP) not from us: ACK NONE with `want_ack = true` (the ACK itself is sent reliably), hop limit from `getHopLimitForResponse` (`:131-135`, rule at `:310-337`).
   - Decoded, `want_ack`, not a reply (`request_id == 0 && reply_id == 0`): ACK NONE, hop limit from `getHopLimitForResponse` (`:136-139`).
   - Decoded, `want_ack`, is a reply, and it arrived directly (0 hops) or via a next hop: 0-hop ACK (`:140-145`). Otherwise no ACK; the reply's sender relies on overhearing a relay.
   - Encrypted (not decodable), `want_ack`, channel 0, and we hold no public key for the sender: NAK `PKI_UNKNOWN_PUBKEY` on the primary channel (`:146-150`).
   - Encrypted, `want_ack`, otherwise: NAK `NO_CHANNEL` on the primary channel (`:151-155`).
   - Not `want_ack`, but `next_hop` equals our last byte and `hop_limit > 0`: 0-hop ACK so the last relay stops its hop-level retries (`:156-159`).
2. Final recipient, repeated copy: a duplicate with 0 hops away that is to us and `want_ack`, if not still in the TX queue and not rebroadcast, gets a 0-hop ACK again (`fw/mesh/NextHopRouter.cpp:163-170`).
3. Implicit ACK (originator overhears a relay of its own packet): `perhapsGenerateImplicitAckForOwnOverheard` (`fw/mesh/ReliableRouter.cpp:58-90`). If `p.from` is our node number and a pending retransmission exists for `(from,id)`, it sends a local ACK NONE (to ourselves, hop limit 0, carrying the relay's `relay_node`, RSSI, SNR) and, only if the copy arrived over LoRa, stops retransmission (`:81-86`). This works from the header only, before decode.
4. MQTT echo: when our own packet comes back from the broker with our gateway id, the MQTT client builds a local ACK NONE with `transport_mechanism = TRANSPORT_MQTT` (`fw/mqtt/MQTT.cpp:120-133`). `ReliableRouter::sniffReceived` does not stop retransmissions for an ACK that is from us over MQTT (`fw/mesh/ReliableRouter.cpp:177-179`), but the ACK is still delivered to the client.
5. NO_RESPONSE: see 0.2 (`fw/mesh/MeshModule.cpp:216-228`).

### 1.3 NAK reasons and where each is produced

| Error | Producer | Citation |
|---|---|---|
| MAX_RETRANSMIT | originator after last retry (local only) | `fw/mesh/NextHopRouter.cpp:473-481` |
| NO_CHANNEL | destination cannot decrypt a want_ack packet; also `perhapsEncode` failure returned to sender | `fw/mesh/ReliableRouter.cpp:151-155`; `fw/mesh/Router.cpp:1342`, `:588-593` |
| PKI_UNKNOWN_PUBKEY | destination lacks sender's key for an undecodable channel-0 want_ack packet | `fw/mesh/ReliableRouter.cpp:146-150` |
| NO_RESPONSE | node with no replying module | `fw/mesh/MeshModule.cpp:226` |
| NOT_AUTHORIZED | wrong bound channel; admin unauthorized; DMShell unauthorized | `fw/mesh/MeshModule.cpp:168`; `fw/modules/AdminModule.cpp:187,227`; `fw/modules/DMShell.cpp:200` |
| ADMIN_PUBLIC_KEY_UNAUTHORIZED | PKC admin with unknown key | `fw/modules/AdminModule.cpp:221` |
| ADMIN_BAD_SESSION_KEY | remote admin setter without valid passkey | `fw/modules/AdminModule.cpp:236-239` |
| BAD_REQUEST | admin validation failures | `fw/modules/AdminModule.cpp:280,299,309,342,349,356,365,656,669` |
| DUTY_CYCLE_LIMIT | local send refused by duty cycle (NAK only for our own packets) | `fw/mesh/Router.cpp:484-511` |
| NO_INTERFACE | no radio interface | `fw/mesh/Router.cpp:434-438` |
| TOO_LARGE, PKI_FAILED, PKI_SEND_FAIL_PUBLIC_KEY, BAD_REQUEST | `perhapsEncode` failure, NAK to sender (our client) | `fw/mesh/Router.cpp:1274-1349`, `:588-593` |
| RATE_LIMIT_EXCEEDED | client text sent within 2 s of previous | `fw/mesh/PhoneAPI.cpp:1896-1900` |
| NO_ROUTE, GOT_NAK, TIMEOUT | not produced anywhere in `fw/` (grep finds no producer) | -- |

`abortSendAndNak` sends the NAK to `getFrom(p)` (our node when the packet is ours) and drops the packet (`fw/mesh/Router.cpp:398-403`).

### 1.4 Retransmission

- Attempt budgets, each including the first send (`fw/mesh/NextHopRouter.h:111-115`):
  - Originator, unicast, `want_ack`: `NUM_RELIABLE_UNICAST_ATTEMPTS = 5`.
  - Originator, broadcast, `want_ack`: `NUM_RELIABLE_RETX = 3`.
  - Hop-level (relay with a `next_hop` set, or our own non-`want_ack` unicast with a `next_hop`): `NUM_INTERMEDIATE_RETX = 3`.
- Start: `ReliableRouter::send` copies every `want_ack` packet into `pending` with the budget above (`fw/mesh/ReliableRouter.cpp:30-39`). `NextHopRouter::sendWithNextHop` tracks a copy when the packet is not ours-with-want_ack, `next_hop` is set, and `hop_limit > 0 || want_ack` (`fw/mesh/NextHopRouter.cpp:124-128`). A send that fails (other than DUTY_CYCLE_LIMIT) stops tracking (`fw/mesh/ReliableRouter.cpp:50-53`).
- `PendingPacket` stores `numRetransmissions = attempts - 1` (`fw/mesh/NextHopRouter.cpp:98-103`).
- Timer: `nextTxMsec = now + iface->getRetransmissionMsec(p)` (`fw/mesh/NextHopRouter.cpp:564-572`). `getRetransmissionMsec = 2 * airtime(packet) + (2^CWsize + 2*CWmax + 2^((CWmax+CWmin)/2)) * slotTimeMsec + PROCESSING_TIME_MSEC` (`fw/mesh/RadioInterface.cpp:813-826`), with `CWsize` mapped from channel utilisation 0..100 % onto `CWmin = 3`..`CWmax = 8` and `PROCESSING_TIME_MSEC = 4500` (`fw/mesh/RadioInterface.h:117-120`). `slotTimeMsec` is computed from SF/BW (`fw/mesh/RadioInterface.cpp:1473`).
- Timer extension: every packet we send extends all other pending timers by that packet's airtime (`fw/mesh/ReliableRouter.cpp:41-48`); every packet we receive extends all pending timers by its airtime (`fw/mesh/ReliableRouter.cpp:96-103`).
- On each expiry (`doRetransmissions`, `fw/mesh/NextHopRouter.cpp:455-562`):
  - Retries left 0: if the packet is ours, local NAK `MAX_RETRANSMIT`; record removed (`:473-481`).
  - Unicast, retries left 1 (last retry): count a route failure, clear `next_hop` in the copy, in NodeDB and in the TMM cache, and send by flooding (`:487-508`).
  - Unicast, otherwise: resend via `sendWithNextHop(copy, false)` (`:532-536`); with `NEXTHOP_EARLY_FLOOD_ON_UNVERIFIED` (default 0, `fw/mesh/NextHopRouter.h:68-70`) an unverified route floods one retry earlier (`:509-530`).
  - Broadcast: resend by flooding (`:538-545`).
  - Decrement and reschedule (`:548-549`).
- Stop conditions:
  - ACK/NAK addressed to us whose `request_id` matches a pending `(to,request_id)`; a non-routing reply with `request_id` also counts as an ACK (`fw/mesh/ReliableRouter.cpp:170-190`). A success ACK also refreshes route health (`:183-186`).
  - Implicit ACK over LoRa (1.2 item 3).
  - Overheard duplicate of the packet over LoRa (`fw/mesh/NextHopRouter.cpp:149-152`).
  - Sniffed ACK/reply not addressed to us for a DM: cancel our queued rebroadcast and stop our hop-level retries for the original (`fw/mesh/NextHopRouter.cpp:229-233`; flooding path `fw/mesh/FloodingRouter.cpp:160-168`).
  - `stopRetransmission` also cancels the packet from the TX queue if at least one retry has gone out and we are the originator or our role permits cancelling (ROUTER and ROUTER_LATE never cancel) (`fw/mesh/NextHopRouter.cpp:407-434`, `fw/mesh/FloodingRouter.cpp:117-135`).
- Broadcast and `want_ack`: `Router::send` clears `want_ack` on every broadcast before it goes on air (`fw/mesh/Router.cpp:518-520`); the originator's pending copy is retired by implicit ACKs (overheard rebroadcasts). Over-the-air broadcasts therefore never request an explicit ACK.
- Client sends with `want_ack` and `hop_limit == 0`: hop limit is raised to the configured default (`fw/mesh/Router.cpp:461-466`).
- Relay repeat handling: a duplicate whose `hop_start == hop_limit` (originator retransmission) is reprocessed and relayed again if not in the TX queue, so the originator hears an implicit ACK (`fw/mesh/FloodingRouter.cpp:47-56`).

### 1.5 ACK proof

- The ACK sender appends a proof when it holds a key for the recipient (`fw/mesh/MeshModule.cpp:89-94`).
- The originator verifies only if the ACK is for an id still pending and comes from the node it addressed; result is recorded as VALID / INVALID / NO_KEY / ABSENT (`fw/mesh/ReliableRouter.cpp:221-305`). Enforcement is controlled by `ACK_PROOF_ENFORCE`; in the shown code paths an invalid proof only blocks when it is set (`:272-273`, `:288`).
- The verdict is copied into `MeshPacket.ack_proof_status` of the packet given to the client (`fw/mesh/MeshService.cpp:126-130`, `fw/mesh/ReliableRouter.cpp:214-219`).
- `ACK_PROOF_ENFORCE = false` (`fw/mesh/AckProof.h:73`): verification is advisory; no ACK is rejected on proof grounds in this build.

### 1.6 What the client sees

- ToRadio: `packet` with `want_ack` (`pb/mesh.proto:2799` ToRadio).
- FromRadio `queueStatus` after every `sendToMesh` (res, free, maxlen, mesh_packet_id) (`fw/mesh/MeshService.cpp:400-407`, `pb/mesh.proto:2501-2520`). `free`/`maxlen` come from the radio TX queue, `MAX_TX_QUEUE = 16` (`fw/mesh/RadioInterface.h:19`, `fw/mesh/RadioLibInterface.cpp:304-313`).
- FromRadio `packet`, port ROUTING_APP, `request_id = id`, `error_reason`:
  - Real ACK: `from` = destination.
  - Implicit ACK: `from` = our own node, `relay_node` = relayer byte, RSSI/SNR of the overheard relay.
  - NAKs listed in 1.3; MAX_RETRANSMIT `from` = our own node.
  - `ack_proof_status` set.
- ROUTING_APP packets displace the oldest entry when the client queue is full (`fw/mesh/MeshService.cpp:512-521`).
- Python client: response handlers are keyed by request id; plain ACKs (error NONE) are passed only to callbacks named `onAckNak` or registered `ackPermitted`; NAKs and data replies are passed to any handler, which is then removed (`py/mesh_interface.py:1643-1666`). `onAckNak` marks an ACK whose `from` is the local node as an implicit ACK (`py/node.py:1056-1071`).

### 1.7 State cached about another participant

- `pending` map keyed by `(from,id)` with a packet copy (`fw/mesh/NextHopRouter.h:128`).
- Last ACK-proof verdict `(from,id,status)` (`fw/mesh/ReliableRouter.h:57-65`).

---

## 2. Next-hop learning and route health (cache of other nodes' routes)

- Learn: on a sniffed ACK or reply (`request_id` or `reply_id` set) whose relayer also relayed the original, or which came directly from the destination when we were the sole relayer, and whose relay byte resolves to a unique node, set `NodeInfoLite.next_hop` for the ACK's sender, record route health, and copy into the TMM next-hop cache (`fw/mesh/NextHopRouter.cpp:182-235`).
- Traceroute replies also set next hops when the route's next hop equals the actual relayer (`fw/modules/TraceRouteModule.cpp:276-348`).
- Use: `getNextHop` returns `node->next_hop` only if it differs from the relay and resolves to a unique currently known direct neighbour; else falls back to TMM cache, then flooding (`fw/mesh/NextHopRouter.cpp:315-378`).
- Route health (RAM only): 32 slots, reuse oldest; route stale after 30 min without confirmation or 3 consecutive un-ACKed directed deliveries; a stale route is cleared and the packet floods (`fw/mesh/NextHopRouter.h:118-120`, `fw/mesh/NextHopRouter.cpp:327-334`, `:654-659`).
- Last retry of a unicast clears the stored next hop (1.4).
- Persistence: `next_hop` is a NodeInfoLite field (`pb/deviceonly.proto:153`) and is saved with the node database; route health is not saved (`fw/mesh/NextHopRouter.h:50-56`).
- Client visibility: none. `fw/mesh/TypeConversions.cpp` contains no reference to `next_hop`, so NodeInfo records sent to the client do not carry it.

---

## 3. Duplicate suppression (PacketHistory and others)

### 3.1 PacketHistory (`fw/mesh/PacketHistory.h`, `fw/mesh/PacketHistory.cpp`)

- Record, 20 bytes: sender, id, `rxTimeMsec`, `next_hop` requested, highest hop limit seen (bits 0-2), our TX hop limit (bits 3-5), up to 6 relayer bytes (`fw/mesh/PacketHistory.h:6-28`).
- Capacity `PACKETHISTORY_MAX` = `MAX_NUM_NODES * 2`, minimum 100 (STM32WL: 20) (`fw/mesh/mesh-pb-constants.h:128-137`). `MAX_NUM_NODES` = 120 default, 10 on STM32WL, 100/200/250 on ESP32-S3 by flash size (`fw/mesh/mesh-pb-constants.h:102-126`).
- Lookup by `(sender,id)` through a hash index or linear scan (`fw/mesh/PacketHistory.cpp:289-333`). Packets with id 0 are never recorded (`:74-79`).
- No time-based expiry: insert takes an empty slot, the matching slot, or the oldest slot (`fw/mesh/PacketHistory.cpp:336-370`). `RECENT_WARN_AGE` (10 min) is only a log warning (`:13`).
- Updated on every send (`fw/mesh/FloodingRouter.cpp:21-23`, `fw/mesh/NextHopRouter.cpp:115-117`) and receive (`fw/mesh/FloodingRouter.cpp:31-32`, `fw/mesh/NextHopRouter.cpp:137-139`).
- Uses: duplicate detection, hop-limit upgrade detection (`fw/mesh/FloodingRouter.cpp:67-95`), fallback-to-flooding detection, "we were next hop", relayer lists for next-hop learning (`fw/mesh/PacketHistory.cpp:66-222`).
- RAM only.

### 3.2 Other duplicate filters

- Opaque (undecryptable) relay seen-set: 32 `(from,id)` slots, FIFO, never feeds routing/ACK (`fw/mesh/NextHopRouter.h:122`, `:143-148`; `fw/mesh/NextHopRouter.cpp:84-96`).
- Text message id ring: last 50 text packet ids (`fw/modules/TextMessageModule.h:4`, `fw/modules/TextMessageModule.cpp:21-23`, `:56-64`), consulted as a second duplicate check in `FloodingRouter::shouldFilterReceived` (`fw/mesh/FloodingRouter.cpp:39-41`).
- Client-to-radio packet ids: last 20 ids; a repeat is ignored (`fw/mesh/PhoneAPI.cpp:1766-1781`, `:1867-1870`).
- Opaque PKI MQTT uplink seen-set, 8 entries, round robin (`fw/mesh/Router.h:222-223`, `fw/mesh/Router.cpp:1661-1666`).

---

## 4. NodeInfo exchange (NODEINFO_APP, protobuf `User`)

- Participants: any node (sender) -> broadcast or one node; answering node; Traffic Management on a relay may answer on behalf of the target (section 21).
- Triggers for sending our NodeInfo (`NodeInfoModule::sendOurNodeInfo`, `fw/modules/NodeInfoModule.cpp:88-135`):
  1. Periodic broadcast: first at `30 s + 15 s * index` after boot (`fw/mesh/MeshModule.h:12-13`, `fw/mesh/MeshModule.cpp:47-53`, `fw/modules/NodeInfoModule.cpp:240`), then every `node_info_broadcast_secs` (default 3 h, `fw/mesh/Default.h:34`; `fw/modules/NodeInfoModule.cpp:244-254`). Minimum when set by admin: 1 h (`fw/mesh/Default.h:37`, `fw/modules/AdminModule.cpp:916-918`). Ham mode sets 600 s (`fw/modules/AdminModule.cpp:1916`). Some roles default to 24 h (`fw/mesh/NodeDB.cpp:1766`, `:1781`). Skipped for CLIENT_HIDDEN or when air utilisation is not allowed (`:246`). `want_response` is set on the periodic broadcast only if the radio configuration generation changed (`:248`).
  2. First packet from a node without a User entry: `sendOurNodeInfo(from, wantReplies=true, channel)` if NodeDB is less than half full, role is not ROUTER/ROUTER_LATE/CLIENT_BASE, channel utilisation is below the polite 25 % limit, and the sender is not more than `hop_limit + 2` hops away; not for telemetry responses (`fw/mesh/MeshService.cpp:94-115`; limits `fw/airtime.h:230-232`).
  3. Routing error `PKI_UNKNOWN_PUBKEY` received: unicast our NodeInfo to that node, short timeout (`fw/mesh/ReliableRouter.cpp:163-169`).
  4. Client heartbeat with `nonce == 1`: broadcast, `want_response = true`, short timeout (`fw/mesh/PhoneAPI.cpp:512-516`; packet API `fw/mesh/api/PacketAPI.cpp:88-93`).
  5. Owner changed: `reloadOwner` broadcast (`fw/mesh/MeshService.cpp:179-189`).
  6. Key verification commit: unicast to verified node, short timeout (`fw/modules/KeyVerificationModule.cpp:475-476`).
  7. Position ping fallback (`trySendPosition`) when no position can be sent (`fw/mesh/MeshService.cpp:426-450`).
- What is sent: `User` (owner) with `id` = `!%08x` of our node (`fw/modules/NodeInfoModule.cpp:187-201`); priority BACKGROUND, or DEFAULT with short timeout (`:107-110`); TRACKER and SENSOR never set `want_response` (`:102-106`); a still-queued previous NodeInfo is cancelled (`:91-92`).
- Throttles (in `allocReply`, used for both own sends and replies; `fw/modules/NodeInfoModule.cpp:143-203`):
  - Reply to an external request suppressed if we already heard a NodeInfo request from that sender within 12 h (`USERPREFS_NODEINFO_REPLY_SUPPRESS_SECS`, default 43 200 s) (`:15-21`, `:34-46`, `:151-156`). The per-sender map is pruned to the NodeDB size and to entries within the window (`:205-233`).
  - Channel utilisation above 40 %: skip (`:158-162`).
  - Normal: skip if our last NodeInfo was sent less than `30 min * congestionCoefficient` ago (coefficient 1.0 for up to 40 online nodes; ROUTER/ROUTER_LATE and SENSOR/TRACKER/TAK_TRACKER are not scaled) (`:164-179`; `fw/mesh/Default.h:119-123`; `fw/mesh/Default.cpp:41-61`). A licensed station is never held past its own broadcast interval (`:169-174`).
  - Short timeout: skip if sent less than 60 s ago (`:180-184`).
  - Skips set `ignoreRequest`, so no NO_RESPONSE NAK (`:153`, `:159`, `:178`, `:183`).
  - Last-sent time is kept by `TransmitHistory`, which persists per-port last broadcast times to the filesystem (`fw/mesh/TransmitHistory.h:4-14`; `fw/modules/NodeInfoModule.cpp:126-127`, `:199-200`).
- What comes back: a NodeInfo reply (`request_id` = request id) from the target, or nothing when throttled.
- Receiving (`fw/modules/NodeInfoModule.cpp:23-86`): `updateUser` (section 19); unsigned broadcasts from a known signer are refused (`:55-58`); `is_licensed` mismatch is dropped (`:48-51`). If the User changed and the packet was a unicast not to us, a copy is sent to the client (`:71-80`).
- Client: ToRadio heartbeat `nonce=1` triggers a broadcast. Client receives NodeInfo packets (broadcast/to us) as FromRadio `packet`.

---

## 5. Position (POSITION_APP, protobuf `Position`)

- Periodic broadcast: interval `position_broadcast_secs` (default 1 h, routers 12 h) scaled by congestion and region throttle (`fw/mesh/Default.h:20`; `fw/modules/PositionModule.cpp:574-575`); held to a stationary floor of 6 h + 6 min when fixed or unmoved (`fw/mesh/Default.h:26`, `:46`; `fw/modules/PositionModule.cpp:586-597`); smart broadcast on movement, minimum interval 5 min default (`fw/mesh/Default.h:21`; `fw/modules/PositionModule.cpp:615`, `:706-711`). Sent only when channel utilisation is below 25 % (40 % for trackers) (`:578-581`). `want_response` on the broadcast only after a radio configuration change (`:407-414`).
- Request: any packet with `want_response` (`sendOurPosition(dest, wantReplies, channel)`, `fw/modules/PositionModule.cpp:444-500`); TRACKER never sets `want_response` (`:465`). Priority RELIABLE for TRACKER/TAK_TRACKER, else BACKGROUND (`:466-470`). Also `trySendPosition` position "ping" (`fw/mesh/MeshService.cpp:426-450`).
- Reply (`allocReply`, `fw/modules/PositionModule.cpp:291-305`): not more than one reply per 3 min except LOST_AND_FOUND (`:293-298`, `ignoreRequest` set, no NAK). Returns null without `ignoreRequest` when channel precision is 0 (`:186-190`), which produces a NO_RESPONSE NAK through 0.2. Event-channel position requests are answered on the position channel (`fw/modules/PositionModule.cpp:307-324`, `fw/mesh/Router.cpp:1589-1598`).
- Cache: `nodeDB->updatePosition(sender, p)` (`fw/modules/PositionModule.cpp:120`; `fw/mesh/NodeDB.cpp:3697-3755`), see 19.
- Client: client positions go out with a 10 s per-port rate limit (`fw/mesh/PhoneAPI.cpp:1885-1894`) and are truncated to channel precision (`fw/modules/PositionModule.cpp:133-140`). Python `sendPosition(wantResponse=True)` waits for a POSITION reply or NO_RESPONSE (`py/mesh_interface.py:585-668`).

---

## 6. Telemetry (TELEMETRY_APP, protobuf `Telemetry`)

- Modules (each on the same port; each `allocReply` answers only its own variant and returns null for others):
  - Device: device_metrics and local_stats (`fw/modules/Telemetry/DeviceTelemetry.cpp:65-92`).
  - Environment: environment_metrics (`fw/modules/Telemetry/EnvironmentTelemetry.cpp:765-795`).
  - Air quality (`fw/modules/Telemetry/AirQualityTelemetry.cpp:448-478`), power (`fw/modules/Telemetry/PowerTelemetry.cpp:236-267`), health (`fw/modules/Telemetry/HealthTelemetry.cpp:212-242`).
  - Host metrics: `allocReply` is commented out, so no reply for host_metrics (`fw/modules/Telemetry/HostMetrics.cpp:42-64`).
- Request: packet with a `Telemetry` whose variant names the wanted metrics, `want_response`. If no constructed module answers that variant, the requester gets NO_RESPONSE (0.2).
- Reply refusal: a relayed broadcast request (`isMultiHopBroadcastRequest`) is ignored without NAK unless our role is SENSOR, ROUTER or ROUTER_LATE (`fw/modules/Telemetry/DeviceTelemetry.cpp:68-71`, `fw/modules/Telemetry/BaseTelemetryModule.h:11-16`; same pattern in each module).
- Periodic device telemetry: every `device_update_interval` (default 1 h, routers 12 h) scaled; gated by channel utilisation, air utilisation, not CLIENT_HIDDEN, `device_telemetry_enabled` (`fw/modules/Telemetry/DeviceTelemetry.cpp:23-46`; `fw/mesh/Default.h:19`). Otherwise sent to the client only, when the client queue is empty (`:37-45`). `want_response = false` on all periodic telemetry (`:181`, `:202`).
- Cache: device_metrics and environment_metrics of other nodes are stored in NodeDB satellite maps; air_quality, power, local_stats, health are not stored per node (`fw/mesh/NodeDB.cpp:3757-3797`; callers `fw/modules/Telemetry/DeviceTelemetry.cpp:60`, `fw/modules/Telemetry/EnvironmentTelemetry.cpp:694`).
- A telemetry response (`request_id > 0`) does not trigger the "new node -> send our NodeInfo" path (`fw/mesh/MeshService.cpp:97-100`).
- Client: 10 s per-port rate limit on client-sent telemetry (`fw/mesh/PhoneAPI.cpp:1885-1894`). Python `sendTelemetry(wantResponse=True)` (`py/mesh_interface.py:740-797`).
- UNRESOLVED: which telemetry modules are constructed depends on detected sensors and build flags (`fw/modules/Modules.cpp:230-260`); resolve per target build.

---

## 7. Traceroute (TRACEROUTE_APP, protobuf `RouteDiscovery`)

- Participants: originator, every relay (promiscuous module, `fw/modules/TraceRouteModule.cpp:511-515`), destination.
- Request: `want_response = true`, `want_ack = true` (on-device UI, `fw/modules/TraceRouteModule.cpp:618-626`, `:740-746`); client unicast traceroutes are forced to `want_ack = true` (`fw/mesh/PhoneAPI.cpp:1905-1909`).
- Relays and endpoints rewrite the payload in `alterReceivedProtobuf`: insert unknown hops, append own id and SNR (toward destination into `route`/`snr_towards`, back into `route_back`/`snr_back`), re-encode (`fw/modules/TraceRouteModule.cpp:133-154`, `:364-440`). `route` arrays hold 8 entries (`fw/mesh/generated/meshtastic/mesh.pb.h:908-909`).
- Reply: destination copies the (updated) request payload into the reply (`fw/modules/TraceRouteModule.cpp:486-506`); relayed broadcast requests are ignored without NAK (`:490-494`). Reply inherits `want_ack` (0.2).
- Next-hop learning from reply: section 2 (`fw/modules/TraceRouteModule.cpp:276-348`).
- Throttles: client may send one traceroute per 30 s; client multi-hop traceroute to broadcast is refused (`fw/mesh/PhoneAPI.cpp:1872-1884`), each refusal produces a ClientNotification and a QueueStatus. On-device: 30 s cooldown, 10 s response timeout, 10 s result display (`fw/modules/TraceRouteModule.h:78-80`; `fw/modules/TraceRouteModule.cpp:848-864`).
- Client: Python `sendTraceRoute` waits `timeout * waitFactor`, `waitFactor = max(1, min(nodes-1, hopLimit+1))` (`py/mesh_interface.py:669-686`).

---

## 8. NeighborInfo (NEIGHBORINFO_APP, protobuf `NeighborInfo`)

- Enabled by `moduleConfig.neighbor_info.enabled`; when enabled the module is promiscuous (`fw/modules/NeighborInfoModule.cpp:40-55`). When disabled, received NEIGHBORINFO packets are dropped and their relay cancelled (`fw/mesh/Router.cpp:1546-1553`).
- Periodic: every `update_interval` (default 6 h, minimum 4 h) (`fw/mesh/Default.h:35`, `:38`; `fw/modules/NeighborInfoModule.cpp:129-139`). Over LoRa only if `transmit_over_lora`, the primary channel or slot is not default, and utilisation permits; otherwise to `NODENUM_BROADCAST_NO_LORA` (local only) (`:131-137`). `want_response = false`.
- Request/reply: `allocReply` returns our neighbour list; at most one reply per 3 min (`ignoreRequest`) (`fw/modules/NeighborInfoModule.cpp:141-159`).
- Cache: up to `MAX_NUM_NEIGHBORS = 10` neighbours `{node_id, snr, last_rx_time, node_broadcast_interval_secs}` (`fw/modules/NeighborInfoModule.h:3`, `:13`); added from 0-hop packets of any port and from received NeighborInfo (`fw/modules/NeighborInfoModule.cpp:165-222`); full list replaces the oldest (`:258-266`); entries older than twice their broadcast interval are removed (`:89-106`). RAM only.

---

## 9. Paxcounter (PAXCOUNTER_APP, protobuf `Paxcount`, ESP32)

- Periodic send when new data, `want_response = false`, BACKGROUND (`fw/modules/esp32/PaxcounterModule.cpp:69-93`).
- Reply: `allocReply` always returns current counts; no throttle (`fw/modules/esp32/PaxcounterModule.cpp:102-109`).
- Received Paxcount packets are not stored (`:96-99`).

---

## 10. Range test (RANGE_TEST_APP, text payload)

- Sender sends `"seq N"` every `range_test.sender` seconds, `hop_limit = 0`, `want_ack = false`, `want_response = false` by default (`fw/modules/RangeTestModule.cpp:115-135`; default argument `fw/modules/RangeTestModule.h:40`), only when channel utilisation is below 25 %, and disables itself after 8 h (`fw/modules/RangeTestModule.cpp:80-96`).
- Receiver appends to a file if `save` is set (`:166-172`). No reply module; a `want_response` request would get NO_RESPONSE through 0.2.
- Client queue: RANGE_TEST packets displace the oldest when the client queue is full (`fw/mesh/MeshService.cpp:515-521`).

---

## 11. Store & Forward (STORE_FORWARD_APP, protobuf `StoreAndForward`; history stored from TEXT_MESSAGE_APP)

- Roles: server if ROUTER/ROUTER_LATE or `is_server`, PSRAM present and at least 1 MiB free; otherwise client (`fw/modules/StoreForwardModule.cpp:603-647`). Runs on ESP32 and Portduino only (`:35`, `:410`).
- Server cache: every TEXT_MESSAGE_APP packet seen (promiscuous) stored in a ring: time, has_rx_time, to, channel, from, id, reply_id, emoji, payload, RSSI, SNR, hop_start, hop_limit, via_mqtt, transport (`fw/modules/StoreForwardModule.cpp:184-217`). Capacity `records` or 3/4 of free PSRAM (`:80-82`). When full, overwrite from index 0 and reset every client's read cursor (`:188-194`). Per-client cursor `lastRequest[client]` (`:135`, `:299`). RAM/PSRAM only.
- Requests and responses (`fw/modules/StoreForwardModule.cpp:457-583`):
  - CLIENT_HISTORY (optional window in minutes) -> ROUTER_HISTORY `{history_messages, window ms, last_request}` then the messages, unless busy or on the default channel, in which case a TEXT "S&F - Busy. Try again shortly." or "S&F not permitted on the public channel." (`:479-495`, `:101-123`, `:352-375`). Legacy text `"SF"` request does the same with the default window (`:415-423`).
  - History playback: one ROUTER_TEXT_DIRECT/ROUTER_TEXT_BROADCAST packet per `packetTimeMax = 5000 ms` while channel utilisation < 25 %, up to `historyReturnMax` (default 25) (`:36-45`, `fw/modules/StoreForwardModule.h:50`, `:103`). Default window 240 min (`fw/modules/StoreForwardModule.h:104`). Only messages not from the requester and broadcast or to the requester (`:132-146`).
  - CLIENT_PING -> ROUTER_PONG; CLIENT_STATS -> ROUTER_STATS or ROUTER_BUSY; CLIENT_ABORT/CLIENT_ERROR stop playback; ROUTER_PING -> CLIENT_PONG (client side) (`:497-551`).
  - Server heartbeat: ROUTER_HEARTBEAT broadcast every `heartbeatInterval` (default 900 s) when `heartbeat` is enabled and utilisation < 25 % (`:46-57`; `fw/modules/StoreForwardModule.h:62`).
  - Client side stores heartbeat period and last heartbeat time, stats, history window; on ROUTER_BUSY/ROUTER_ERROR it computes `retry_delay = now + available * packetTimeMax * (2 for ERROR)` (`:522-577`).
  - All S&F protocol packets: `want_ack = false`, `want_response = false`, BACKGROUND (`:314-330`).
- Client API: when this node is an S&F server on ESP32, received TEXT packets are not put in the client queue; instead the client is fed from the S&F history (all stored messages for the local node, no time limit) as TEXT_MESSAGE_APP packets (`fw/mesh/MeshService.cpp:501-510`; `fw/mesh/PhoneAPI.cpp:1726-1733`; `fw/modules/StoreForwardModule.cpp:153-177`, `:278-282`).
- No firmware code sends CLIENT_HISTORY, CLIENT_PING or CLIENT_STATS; these requests come from clients or other implementations. `retry_delay` is declared (`fw/modules/StoreForwardModule.h:112`) and written (`fw/modules/StoreForwardModule.cpp:527`) but never read anywhere in `fw/` (grep).

---

## 12. Key verification (KEY_VERIFICATION_APP, protobuf `KeyVerification`)

- Participants: initiator node A (started by its local client through admin `key_verification`), responder node B, both users.
- Constants: idle timeout 60 s, session cap 3 min, remote-initiated cooldown 60 s (`fw/modules/KeyVerificationModule.cpp:17-21`).
- Flow:
  1. A's client sends AdminMessage `key_verification` INITIATE_VERIFICATION (local only, `from == 0`) (`fw/modules/KeyVerificationModule.cpp:58-64`).
  2. A sends M1: nonce from hardware RNG, our public key in `hash1`, channel 0, `pki_encrypted` only if A holds B's key, `want_response = true`, priority HIGH (`:165-216`).
  3. B's `allocReply` (M2): refused without NAK if busy, within cooldown, or licensed mode (`:219-239`); computes security number and hashes; reply carries B's public key in `hash1` and `hash2`; PKI-encrypted only if B already held A's key (`:241-333`). B's client receives a ClientNotification `key_verification_number_inform` with the security number (`:318-330`).
  4. A receives M2 (`:96-122`): stores hash2, stashes B's key as pending if unknown, client gets ClientNotification `key_verification_number_request`.
  5. A's client sends admin PROVIDE_SECURITY_NUMBER with nonce (`:65-69`); A verifies hash2, sends M3 (`hash1`, PKI-encrypted, `want_response = true`, HIGH) (`:335-405`), client gets `key_verification_final`.
  6. B receives M3 (must be PKI) and compares hash1 (`:124-160`); client gets `key_verification_final`.
  7. Each client sends DO_VERIFY (commit) or DO_NOT_VERIFY (`:71-76`). Commit marks the node `IS_KEY_MANUALLY_VERIFIED`, stores the key, unicasts our NodeInfo, saves the node database (`:439-478`).
- M3 also carries `want_response`; B is no longer IDLE so `allocReply` sets `ignoreRequest` and no NAK is sent (`:228-232`).
- Cache: pending public key of the peer (in `crypto`), cleared on reset (`:420-437`).

---

## 13. Reply module and ReplyBot

- `ReplyModule` (REPLY_APP) replies "Message Received" to `want_response` (`fw/modules/ReplyModule.cpp:8-26`), but it is not constructed (`fw/modules/Modules.cpp:218` commented out).
- `ReplyBotModule` (text port), built unless `MESHTASTIC_EXCLUDE_REPLYBOT` (`fw/modules/Modules.cpp:142-144`): on a DM or a broadcast on the primary channel whose text starts with `/ping`, `/hello` or `/test`, sends a DM "Mic Check : hops | RSSI | SNR", `want_ack = false`, `want_response = false` (`fw/modules/ReplyBotModule.cpp:89-185`). Per-sender cooldown 15 s for DMs, 60 s for broadcasts, in an 8-entry ring (`:40-66`, `:120-123`). The reply does not set `request_id`.

---

## 14. Node status message (NODE_STATUS_APP, protobuf `StatusMessage`)

- Broadcast every 12 h if `statusmessage.node_status` is set, `want_response = false`, BACKGROUND, channel 0 (`fw/modules/StatusMessageModule.cpp:10-30`).
- Received statuses are cached per node in the NodeDB status satellite map (`fw/modules/StatusMessageModule.cpp:32-47`; `fw/mesh/NodeDB.cpp:2038-2048`), persisted with the node database and replayed to clients (section 18).
- No reply module.

---

## 15. Audio (AUDIO_APP, ESP32)

- Codec2 frames, `want_ack = false`, priority MAX, `want_response` default false (`fw/modules/esp32/AudioModule.cpp:258-273`; default `fw/modules/esp32/AudioModule.h:60`).
- `allocReply` returns an empty data packet (`fw/modules/esp32/AudioModule.cpp:244-248`), so a `want_response` request gets an empty AUDIO reply.

---

## 16. Serial module (SERIAL_APP or TEXT, depending on mode)

- Mesh send from serial bytes: `want_ack = ACK` (`#define ACK 1`), `want_response` default false, bound to the "serial" channel in default/simple modes (`fw/modules/SerialModule.cpp:83`, `:114-130`, `:341-359`; `fw/modules/SerialModule.h:57`).
- Sensor telemetry from serial: broadcast, `want_ack = true` and HIGH for SENSOR role, else RELIABLE (`fw/modules/SerialModule.cpp:316-333`).
- No reply module; received packets are written to the serial port (`:367-420`). A `want_response` on another channel gets NOT_AUTHORIZED (0.2 bound channel); otherwise NO_RESPONSE.
- Serial API connection timeout 15 min since last contact (`fw/modules/SerialModule.cpp:86`, `:141`; console `fw/SerialConsole.cpp:42`, `:240-243`).

---

## 17. Waypoint (WAYPOINT_APP, protobuf `Waypoint`)

- Received waypoints stored in `WaypointStore` (`fw/modules/WaypointModule.cpp:169-200`); history limit 10, persisted to flash when `ENABLE_WAYPOINT_PERSISTENCE` (`fw/WaypointStore.h:8`, `:12`, `:50-51`).
- No `allocReply`: a `want_response` waypoint gets NO_RESPONSE. Python treats NO_RESPONSE as a failure message (`py/mesh_interface.py:837-848`); Python waypoint defaults are `wantAck=True`, `wantResponse=False` (`py/mesh_interface.py:850-861`).
- Delete: a copy with `expire = 1` is broadcast, unless locked to another node (`fw/modules/WaypointModule.cpp:203-247`).
- Client rate limit 10 s per port (`fw/mesh/PhoneAPI.cpp:1885-1894`).

---

## 17a. Remote hardware (REMOTE_HARDWARE_APP, protobuf `HardwareMessage`), bound to channel "gpio"

- READ_GPIOS -> READ_GPIOS_REPLY via `setReplyTo` (`fw/modules/RemoteHardwareModule.cpp:96-109`); WRITE_GPIOS no reply; WATCH_GPIOS starts a watch, GPIOS_CHANGED broadcast when values change, checked every 30 s (`:16`, `:111-155`). Bound channel `Channels::gpioChannel` (`:70`).

## 17b. Canned messages

- Sent with `want_ack = true` (`fw/modules/CannedMessageModule.cpp:1072`); the module watches ROUTING_APP packets whose `request_id` equals its last sent id to show delivery state on the device (`fw/modules/CannedMessageModule.cpp:2197-2199`).

## 17c. DMShell (REMOTE_SHELL_APP, protobuf `RemoteShell`, Linux Portduino only)

- Opcodes OPEN 1, INPUT 2, RESIZE 3, CLOSE 4, PING 5, ACK 6, OPEN_OK 64, OUTPUT 65, CLOSED 66, ERROR 67, PONG 68 (`pb/mesh.proto:1448-1470`).
- Own sequence/ACK protocol: TX window default 4 frames, history 50; bare ACK after 2 in-order received frames; replay-request interval `2*frameMsec + 250 ms` clamped to 250..10 000 ms; peer declared gone after 25 consecutive retransmits; session idle timeout 5 min (`fw/modules/DMShell.cpp:35-79`, `:162-163`; `fw/modules/DMShell.h:71`, `:88`).
- Authorization as for admin: local (`from == 0`, unless `is_managed`), admin channel when enabled, or PKC with an admin key; unauthorized -> NOT_AUTHORIZED (`fw/modules/DMShell.cpp:198-202`, `:476-492`).
- Only built when `ARCH_PORTDUINO && __linux__` (`fw/modules/DMShell.h:15-18`).

---

## 18. Client API session (ToRadio / FromRadio)

### 18.1 Handshake

- ToRadio `want_config_id = nonce` (`pb/mesh.proto:2819`) -> `handleStartConfig` (`fw/mesh/PhoneAPI.cpp:467-471`, `:268-356`). Special nonces: 69420 `SPECIAL_NONCE_ONLY_CONFIG` (no node database, no satellite replay), 69421 `SPECIAL_NONCE_ONLY_NODES` (own node then other nodes, no config) (`fw/mesh/PhoneAPI.h:28-29`). The Python library uses 69420 as `NODELESS_WANT_CONFIG_ID` (`py/__init__.py:127`) and otherwise a random 32-bit id, never 69420 (`py/mesh_interface.py:1197-1212`).
- FromRadio sequence (`fw/mesh/PhoneAPI.cpp:538-551`, state machine `:594-1113`): `my_info` -> `deviceuiConfig` -> own `node_info` -> `metadata` -> `region_presets` -> one `channel` per channel slot -> one `config` per config type -> one `moduleConfig` per module config type -> other `node_info` records (thin: no bundled position or metrics, prefetch depth 4, `fw/mesh/PhoneAPI.cpp:1207-1240`, `fw/mesh/PhoneAPI.h:107`) -> `fileInfo` manifest entries -> `config_complete_id = nonce` (`:1129-1189`).
- After `config_complete_id` the node is in STATE_SEND_PACKETS and, unless ONLY_CONFIG, replays cached positions, then device telemetry, then environment, then status of other nodes as ordinary `packet` messages, at lower priority than live traffic (`fw/mesh/PhoneAPI.cpp:1132-1144`, `:1090-1100`, `:1357-1656`; prefetch depth 4, `fw/mesh/PhoneAPI.h:113`).
- With access control active (lockdown), an unauthorized connection gets redacted config, empty channels and no node database (`fw/mesh/PhoneAPI.cpp:562-570`, `:637-640`, `:686-694`, `:943-947`), and a `lockdown_status` message (`:1159-1180`).
- Python treats `config_complete_id == configId` as connected and starts its heartbeat (`py/mesh_interface.py:1364-1368`, `:1183-1195`, `:1284-1294`).

### 18.2 Heartbeat and queue status

- ToRadio `heartbeat` (`pb/mesh.proto:2842`, `Heartbeat.nonce` `:3162-3166`): nonce 0 sets a flag; the next `getFromRadio` returns FromRadio `queueStatus` built from the radio TX queue before anything else (`fw/mesh/PhoneAPI.cpp:503-521`, `:576-585`). The flag is a single global, not per connection (`fw/mesh/PhoneAPI.cpp:56`). Nonce 1 triggers a NodeInfo broadcast (section 4). On the packet API heartbeat nonce 0 only logs (`fw/mesh/api/PacketAPI.cpp:88-96`).
- Python sends a heartbeat every 300 s after connection (`py/mesh_interface.py:1170-1181`).
- QueueStatus is also queued after every `sendToMesh` (section 1.6) and when the TX queue regains free space after having been full (`fw/mesh/MeshService.cpp:139-143`). The QueueStatus queue holds 2 entries, oldest discarded (`fw/mesh/mesh-pb-constants.h:45-48`; `fw/mesh/MeshService.cpp:360-384`).
- Python client flow control: packets wait in an ordered queue until the last QueueStatus shows `free > 0`, polling every 0.5 s; a QueueStatus with `res == 0` removes the packet with `mesh_packet_id` from the queue; otherwise the queue is resent (`py/mesh_interface.py:1220-1314`).

### 18.3 Notification of new data, disconnect, timeout

- `fromNum` counter is incremented for every queued item; `MeshService::loop` notifies observers (`fw/mesh/MeshService.cpp:144-154`); PhoneAPI `onNotify` checks the connection timeout and tells the transport there is data (`fw/mesh/PhoneAPI.cpp:1922-1945`).
- Disconnect: ToRadio `disconnect` -> `close()` (`fw/mesh/PhoneAPI.cpp:472-475`); `close()` releases the packet currently held for the client and clears handshake/replay state but does not empty the MeshService queues (`fw/mesh/PhoneAPI.cpp:358-419`).
- Connection timeout: serial 15 min since last contact (`fw/SerialConsole.cpp:42`, `:240-243`); TCP while the socket is connected (`fw/mesh/api/ServerAPI.cpp:33-36`); BLE while the link exists (`fw/nimble/NimbleBluetooth.cpp:418`, `fw/platform/nrf52/NRF52Bluetooth.cpp:153`); packet API after first contact while the server queue is available (`fw/mesh/api/PacketAPI.cpp:136-140`). Checked from `onNotify` (`fw/mesh/PhoneAPI.cpp:421-432`).
- Node number change after handshake: client is sent a fresh `my_info` (STATE_RESEND_MY_INFO), or the config dump restarts if mid-sync (`fw/mesh/PhoneAPI.cpp:1926-1940`, `:1103-1109`).

### 18.4 Client-to-node packet handling

- `from`, `next_hop`, `relay_node`, `ack_proof_status` are overwritten; id 0 is replaced with a generated id (`fw/mesh/MeshService.cpp:312-318`).
- Repeat ids (last 20) dropped silently (`fw/mesh/PhoneAPI.cpp:1867-1870`).
- Per-port client rate limits: TRACEROUTE 30 s, POSITION/WAYPOINT/ALERT/TELEMETRY 10 s, TEXT 2 s (TEXT also gets a RATE_LIMIT_EXCEEDED NAK) (`fw/mesh/PhoneAPI.cpp:1872-1903`). Each refusal sends QueueStatus `res = 0, mesh_packet_id = p.id` (`:1876-1902`).
- UNRESOLVED: the RATE_LIMIT_EXCEEDED NAK is addressed to `mp->from` as the client supplied it (`fw/mesh/MeshService.cpp:557-570`), before `from` is normalised (`fw/mesh/MeshService.cpp:312`). With `from = 0` the NAK has `to = 0`; `Router::sendLocal` logs "to=0" and, since 0 is not our node number, passes it to `send()` (`fw/mesh/Router.cpp:424-438`, `:468`). Whether a client ever sees this NAK needs a test on hardware or the native simulator.
- Outgoing remote admin getter requests are recorded for response matching (section 20.5) (`fw/mesh/MeshService.cpp:330-336`).

---

## 19. Buffering for the client (MeshService / PhoneAPI)

- Queues filled regardless of whether a client is connected:
  - Mesh packets: `toPhoneQueue`, `MAX_RX_TOPHONE` = 8 (STM32WL, classic ESP32), 16 (nRF52840, small parts), 32 (RP2040, ESP32-C3, medium/large memory) (`fw/mesh/mesh-pb-constants.h:25-43`). When full, TEXT, RANGE_TEST and ROUTING packets displace the oldest entry; other packets are dropped (`fw/mesh/MeshService.cpp:512-528`).
  - QueueStatus: 2 entries, oldest discarded (`fw/mesh/mesh-pb-constants.h:45-48`, `fw/mesh/MeshService.cpp:369-374`).
  - MqttClientProxyMessage: 8 entries, oldest discarded (`fw/mesh/mesh-pb-constants.h:50-53`, `fw/mesh/MeshService.cpp:539-555`); backing pool 16 (`fw/mesh/MeshService.cpp:54-55`).
  - ClientNotification: 2 entries, oldest discarded (`fw/mesh/mesh-pb-constants.h:55-58`, `fw/mesh/MeshService.cpp:572-588`); pool 4 (`:60-61`).
  - XModem: one pending control/data slot (section 23).
- Delivery order in STATE_SEND_PACKETS: queueStatus, mqttClientProxyMessage, xmodem, lockdown_status, clientNotification, mesh packet, then satellite replay (`fw/mesh/PhoneAPI.cpp:1029-1101`; `available()` `:1705-1744`).
- On reconnect the client receives: the full config dump (18.1), then whatever is still in the queues above, then the satellite replay. The queues are RAM only. Queued packets carrying an uptime placeholder time are re-dated when the clock becomes valid (`fw/mesh/MeshService.cpp:207-237`).
- Undecodable NODEINFO/WAYPOINT payloads are not queued for the client (`fw/mesh/MeshService.cpp:467-499`).
- Local positions and device telemetry are sent to the client only when `toPhoneQueue` is empty (`fw/modules/PositionModule.cpp:556-566`; `fw/modules/Telemetry/DeviceTelemetry.cpp:37-45`).

---

## 20. Admin (ADMIN_APP, protobuf `AdminMessage`)

### 20.1 Who may send (`AdminModule::handleReceivedProtobuf`, `fw/modules/AdminModule.cpp:109-715`)

- Storage locked (lockdown active, not unlocked): every admin payload dropped (`:115-132`).
- Response variants (`messageIsResponse`, `:1986-2000`) from a remote node are accepted only if they match an outstanding request (20.5) (`:151-161`).
- Local client (`from == 0`): accepted; under the legacy model refused when `is_managed` (`:162-183`). With `MESHTASTIC_PHONEAPI_ACCESS_CONTROL` the gate is in PhoneAPI: an unauthorized connection may only send `lockdown_auth` to its own node (`fw/mesh/PhoneAPI.cpp:445-465`, `:1803-1848`).
- Remote on the channel named "admin": only if `admin_channel_enabled`, else NOT_AUTHORIZED (`:184-189`).
- Remote PKC (`pki_encrypted`) with an authorized admin key: accepted; the sender is auto-favourited except on CLIENT_BASE (`:190-219`). Unknown key: ADMIN_PUBLIC_KEY_UNAUTHORIZED (`:220-224`).
- Anything else: NOT_AUTHORIZED (`:225-229`).

### 20.2 Session passkey

- Every getter response carries `session_passkey` (8 bytes) from `setPassKey` (`fw/modules/AdminModule.cpp:1952-1975`). A new key is drawn from the hardware RNG (CSPRNG fallback) when none exists or the current key is older than 150 s.
- Remote non-getter, non-response messages (setters, commands) must carry a passkey matching the current key, issued less than 300 s ago; otherwise ADMIN_BAD_SESSION_KEY (`fw/modules/AdminModule.cpp:233-241`, `:1977-1984`). Local (`from == 0`) messages are not checked (`:235`).
- One passkey per node, shared by all requesters (single `session_passkey`/`session_time`, `fw/modules/AdminModule.h:54-55`).
- Python stores the passkey from any received admin packet per node (`py/__init__.py:258-263`), sends it on every admin message (`py/node.py:1115-1116`), and requests SESSIONKEY_CONFIG first if it has none (`py/node.py:1128-1137`). The node answers `get_config_request` SESSIONKEY_CONFIG with a `get_config_response` carrying a passkey (`fw/modules/AdminModule.cpp:1457-1543`; config tag handling `fw/mesh/PhoneAPI.cpp:804-807`).

### 20.3 Getter/response pairs (each requires `want_response`; response built with `allocDataProtobuf`, PKI-encrypted if the request was)

| Request | Response | Citation |
|---|---|---|
| get_owner_request | get_owner_response (owner User) | `fw/modules/AdminModule.cpp:261-264`, `:1438-1455` |
| get_config_request(type) | get_config_response | `:266-269`, `:1457-1543` |
| get_module_config_request(type) | get_module_config_response | `:271-274`, `:1544-1663` |
| get_channel_request(index+1) | get_channel_response, BAD_REQUEST if out of range | `:276-284`, `:1794-1810` |
| get_device_metadata_request | get_device_metadata_response (see note) | `:438-442`, `:1693-1713` |
| get_device_connection_status_request | get_device_connection_status_response | `:492-496`, `:1715-1792` |
| get_ui_config_request | get_ui_config_response (no passkey set) | `:367-372`, `:1812-1824` |
| get_canned_message_module_messages_request, get_ringtone_request, get_node_remote_hardware_pins_request | answered by other modules through `handleAdminMessageForAllModules`; reply gets a passkey | `:699-701`, `:717-731`; `fw/mesh/MeshModule.cpp:309-330` |

Note: `handleGetDeviceMetadata` and `handleGetDeviceUIConfig` build `myReply` without checking `want_response`; MeshModule discards a reply when `want_response` is not set (`fw/mesh/MeshModule.cpp:194-199`), so a reply leaves the node only when it was requested.

### 20.4 Setters and commands

- If `want_response` is set and nothing else produced a reply, the node replies with Routing error NONE (an application-level ACK) (`fw/modules/AdminModule.cpp:707-710`).
- `begin_edit_settings`: opens a transaction; saves are deferred and accumulated; a reboot requested by a setter is deferred (`:477-482`, `:1851-1867`). `commit_edit_settings`: saves config, module config, device state, channels, node database (`:483-491`). An open transaction idle for 60 s is committed without reboot on the next admin message (`fw/modules/AdminModule.h:45`; `fw/modules/AdminModule.cpp:1836-1849`, called at `:243`). Single transaction per node (`hasOpenEditTransaction`, `fw/modules/AdminModule.h:42`).
- Reboots (after `DEFAULT_REBOOT_SECONDS = 7`, `fw/configuration.h:439-440`, unless deferred by an open transaction):
  - set_config: `requiresReboot` defaults true (`fw/modules/AdminModule.cpp:883`), with exceptions inside each config case (device `:900`, power `:965`, display `:990`, LoRa `:1120`, `:1125`); reboot at `:1244-1248`.
  - set_module_config: reboots except statusmessage and mesh_beacon (`:1258`, `:1368`, `:1389`, `:1397`).
  - set_owner: `saveChanges` with default `shouldReboot = true` (`:835-836`; default `fw/modules/AdminModule.h:58`).
  - set_channel: no reboot (`:1427`).
  - reboot_seconds, factory_reset_config, factory_reset_device, nodedb_reset, restore_preferences: reboot (`:377-379`, `:443-470`, `:660-668`). ota_request: 1 s (`:427-429`). shutdown_seconds: shutdown (`:432-436`).
- Node database edits by admin: remove_by_nodenum, add_contact, set/remove favorite, set/remove ignored, toggle muted, set/remove fixed position (`:506-611`).

### 20.5 Remote admin response matching (requester side)

- Outgoing getter requests from the client to a remote node are recorded: `to`, packet id, expected response variant, module config subtype, and the destination public key if the request will be PKC-encrypted. 8 slots, oldest evicted (`fw/modules/AdminModule.cpp:2032-2086`; `fw/modules/AdminModule.h:104-121`).
- A response is accepted once, only from that node, with `request_id` equal to the recorded id, within 300 s, over PKC with the same key when pinned (`fw/modules/AdminModule.cpp:2088-2116`).

### 20.6 Client visibility

- Local admin replies are delivered to the client as FromRadio `packet` (0.3). Python `_sendAdmin` sets `wantAck = True`, `wantResponse` as given (default true), `pkiEncrypted = True`, channel = admin channel index (`py/node.py:1095-1126`).
- UNRESOLVED: for a local admin message with `want_ack` and no reply, `ReliableRouter::sniffReceived` would generate ACK NONE addressed to our own node (`fw/mesh/ReliableRouter.cpp:123-139`). Whether this ACK reaches the client depends on `RoutingModule` seeing the local `RX_SRC_USER` packet (`fw/mesh/MeshModule.cpp:141-144` only excludes `RX_SRC_LOCAL`). Confirm with a test.

---

## 21. Traffic Management module (cache of other nodes' data, throttles, answering on behalf)

- Built when `HAS_TRAFFIC_MANAGEMENT` and `moduleConfig.has_traffic_management` (`fw/modules/Modules.cpp:146-150`; `fw/mesh/mesh-pb-constants.h:184-192`). Constructed before RoutingModule, so a `STOP` from this module also prevents relay, ACK generation and client delivery for that packet (module order `fw/modules/Modules.cpp:148`, `:309`; `fw/mesh/MeshModule.cpp:201-204`). Promiscuous, sees all packets (`fw/modules/TrafficManagementModule.h:115`).
- Caches:
  - Unified per-node cache, 10 bytes per entry: position fingerprint and tick, packet-rate count and tick, undecodable-packet count and tick, cached role (4 bits), next-hop byte (`fw/modules/TrafficManagementModule.h:151-185`). Size `TRAFFIC_MANAGEMENT_CACHE_SIZE`: 250 nRF52840, 2048 large memory, 500 medium, 400 small (`fw/mesh/mesh-pb-constants.h:206-223`). Full cache evicts the stalest entry, preferring entries without a next hop (`fw/modules/TrafficManagementModule.h:187-189`).
  - NodeInfo payload cache, 2000 entries: full `User`, observation tick, source channel, decoded bitfield, key provenance (`fw/modules/TrafficManagementModule.h:191-197`, `:236-260`). Used for direct NodeInfo replies, name rehydration of re-admitted NodeDB nodes (`fw/mesh/NodeDB.cpp:4696-4710`), and as last-resort public key source (`fw/modules/TrafficManagementModule.h:63-71`). Written through from `NodeDB::updateUser` (`fw/mesh/NodeDB.cpp:3996-4008`).
- Throttles/filters (`TrafficManagementModule::handleReceived`, `fw/modules/TrafficManagementModule.cpp:1073-1187`), each sets `ignoreRequest` and returns STOP:
  - Undecodable packets above `unknown_packet_threshold` (capped 60) per 5-minute window (1-min ticks) (`:1093-1103`, `:1670-1708`).
  - Position dedup (not to/from us, well-known channel): drop when the truncated position fingerprint is unchanged within `position_min_interval_secs` (default 6 h; trackers capped 1 h, lost-and-found 15 min) (`:1155-1166`, `:1303-1384`; `fw/mesh/Default.h:44-51`; tick 6 min `fw/modules/TrafficManagementModule.h:201`).
  - Rate limit (not to/from us, not ROUTING/ADMIN): more than `rate_limit_max_packets` (capped 60) per `rate_limit_window_secs` (5-min ticks, at most 15 ticks) (`:1174-1183`, `:1622-1668`).
- NodeInfo direct response (`:1127-1146`, `:1386-1545`): a unicast, unsigned NodeInfo request with `want_response`, not to or from us, when `nodeinfo_direct_response_max_hops > 0` and the requester is within the role limit (routers/CLIENT_BASE up to 3 hops, clients 0) (`fw/modules/TrafficManagementModule.cpp:39-40`, `:1597-1620`). If the target is in the NodeInfo cache and was heard within 6 h (and, when the signed gate is compiled in, its key is proven) (`:1427-1480`; `fw/modules/TrafficManagementModule.h:216-217`), the relay sends the cached `User` with `from = target`, `to = requester`, `request_id = request id`, `hop_limit = 0`, `hop_start = 0`, DEFAULT priority, `want_response = false`, and does not forward the request (`:1494-1541`). Throttles: 60 s per requester, 60 s per target, 1 s global, 8-slot tables (`fw/modules/TrafficManagementModule.h:375-385`; `fw/modules/TrafficManagementModule.cpp:1568-1595`).
- Hop exhaustion request: `shouldExhaustHops` makes a relay send with `hop_limit = 0` (`fw/modules/TrafficManagementModule.h:102-106`; `fw/mesh/NextHopRouter.cpp:253-283`).
- Maintenance sweep every 60 s; reconcile with NodeDB every 60 sweeps (1 h) (`fw/modules/TrafficManagementModule.h:116`, `:311`).
- Client visibility: config via module config. `getStats()` (`fw/modules/TrafficManagementModule.h:48-53`) has no caller outside `TrafficManagementModule.cpp` (grep), so no client path for the counters exists in this checkout.

---

## 22. NodeDB: what each node stores about other nodes

### 22.1 Hot store (`NodeInfoLite`, `pb/deviceonly.proto:109-203`)

Per node: `num`, `snr` (persisted as `snr_q4`), `last_heard`, `channel` heard on, `hops_away`, `next_hop`, `bitfield` (has_user, favourite, ignored, muted, via_mqtt, key manually verified, XEdDSA signed, has_SNR, has RF hear, LoRa slot fingerprint), `long_name`, `short_name`, `hw_model`, `role`, `public_key`. Capacity `MAX_NUM_NODES` (section 3.1).

### 22.2 Satellite maps (`pb/deviceonly.proto:291-308`)

- position (`PositionLite`), device telemetry (`DeviceMetrics`), environment (`EnvironmentMetrics`), status (`StatusMessage`).
- Each capped at `MAX_SATELLITE_NODES` = 250 (large memory) or 40 (`fw/mesh/mesh-pb-constants.h:139-150`). Insert of a new node into a full map evicts the entry whose node has the oldest `last_heard` (`fw/mesh/NodeDB.cpp:814-841`). Compiled out on STM32WL (`fw/mesh/mesh-pb-constants.h:64-96`).

### 22.3 Updates and the packets that cause them

- Any decoded packet to us or broadcast (via RoutingModule -> `handleFromRadio`), any packet we send (`sendToMesh`), and reprocessed duplicates: `NodeDB::updateFrom` sets `last_heard` (or an uptime sidecar when the clock is untrusted), SNR (LoRa with RSSI only), heard slot, via_mqtt, `hops_away` (`fw/mesh/MeshService.cpp:93`, `:389`; `fw/mesh/FloodingRouter.cpp:105-106`; `fw/mesh/NodeDB.cpp:4015-4099`). Unicast packets between other nodes do not reach `handleFromRadio` (`fw/modules/RoutingModule.cpp:35`).
- NODEINFO_APP: `updateUser`, which keeps the first 32-byte public key and drops a NodeInfo whose key differs, refuses a remote node advertising our own key (client gets a ClientNotification), saves at most once a minute (`fw/mesh/NodeDB.cpp:3907-4011`).
- POSITION_APP: `updatePosition`; a remote position without time keeps the previous time (`fw/mesh/NodeDB.cpp:3697-3755`).
- TELEMETRY_APP device/environment: `updateTelemetry` (`fw/mesh/NodeDB.cpp:3762-3797`).
- NODE_STATUS_APP: `setNodeStatus` (`fw/mesh/NodeDB.cpp:2038-2048`).
- Admin add_contact (client-supplied contact; never erases a stored key; auto-favourite except CLIENT_BASE) (`fw/mesh/NodeDB.cpp:3802-3876`).
- Next hop: section 2.

### 22.4 Eviction

- New node with a full store: evict the oldest "boring" node (not favourite, not ignored, no public key), else the oldest node that is not favourite, ignored or verified; index 0 (self) never (`fw/mesh/NodeDB.cpp:4612-4669`).
- Admission when full of a node never seen before is rate-limited to one per 2 s (`fw/mesh/NodeDB.cpp:3677`, `:4024-4036`).
- Evicted nodes go to the warm tier (`WarmNodeEntry`: num, last_heard with role/protection bits, public key, 40 bytes) so DMs still decrypt; re-admission restores key, role, XEdDSA flag (`fw/mesh/WarmNodeStore.h:35-40`; `fw/mesh/NodeDB.cpp:4654-4694`). Warm capacity 0..2000 by platform (`fw/mesh/mesh-pb-constants.h:152-179`); persisted to `/prefs/warm.dat` (`fw/mesh/WarmNodeStore.cpp:45`).
- On load, nodes without user info and not ignored are purged (keys go to warm) (`fw/mesh/NodeDB.cpp:2179-2218`); over-cap stores demote the oldest unprotected nodes to warm (`:2371-2407`).

### 22.5 Persistence

- `/prefs/nodes.proto` holds nodes and all four satellite maps (`fw/mesh/NodeDB.h:129`; `fw/mesh/NodeDB.cpp:3267-3365`).
- Written: on user change (at most once per minute, `fw/mesh/NodeDB.cpp:3981-3994`), by the main loop when dirty and a minute has passed (`fw/main.cpp:1504`, `fw/mesh/NodeDB.cpp:3888-3895`), on graceful reboot when dirty (`:3897-3903`), on admin edits (`saveChanges(SEGMENT_NODEDATABASE)`), on key verification commit (`fw/modules/KeyVerificationModule.cpp:478`).
- `nodeDatabaseDirty` is set only in `updateUser` (`fw/mesh/NodeDB.cpp:3987`); position/telemetry/status updates alone do not mark the database dirty, so they are persisted only when another save occurs.

### 22.6 Client view

- Config dump: own node full, others thin (18.1); satellite data replayed afterwards (18.1); live updates as received packets.
- Python keeps its own node table updated from `node_info` and from received packets (`py/mesh_interface.py:1343-1363`; `py/__init__.py:173-263`).

---

## 23. XModem file transfer (ToRadio/FromRadio `xmodemPacket`, protobuf `XModem`)

- Client <-> local node only; never on the mesh. Blocked for unauthorized connections under access control (`fw/mesh/PhoneAPI.cpp:476-487`, `:1048-1058`).
- Buffer 128 bytes, `seq` and `crc16` 16-bit (`pb/xmodem.options:4-6`). CRC-16/CCITT (`fw/xmodem.cpp:89-117`).
- Upload (client -> node): SOH seq 0 with filename -> node ACK (file opened) or NAK (unsafe name or open failed); data SOH seq n with correct CRC -> ACK, else NAK; EOT -> ACK, file closed; CAN -> ACK, file removed (`fw/xmodem.cpp:137-250`).
- Download (node -> client): STX seq 0 with filename -> node sends SOH seq 1; client ACK -> next block; after a short block the next ACK yields EOT; client NAK -> resend same block, cancel (CAN) after `MAXRETRANS = 25` NAKs (`fw/xmodem.cpp:173-196`, `:251-315`; `fw/xmodem.h:39`, `:68`).
- No timers on the node; the client drives all retries.
- Output is a single slot overwritten by the next control (`fw/xmodem.cpp:119-135`), copied by PhoneAPI into `xmodemPacketForPhone` (`fw/mesh/PhoneAPI.cpp:1719-1725`).

---

## 24. MQTT client proxy (ToRadio/FromRadio `mqttClientProxyMessage`)

- Enabled by `mqtt.proxy_to_client_enabled` (with MQTT enabled and an uplink/downlink channel or map reporting) (`fw/mesh/PhoneAPI.cpp:489-501`; `fw/mqtt/MQTT.cpp:340-346`).
- Node -> client: every MQTT publish becomes a `MqttClientProxyMessage{topic, data|text, retained}` in the 8-entry proxy queue (`fw/mqtt/MQTT.cpp:478-524`; `fw/mesh/MeshService.cpp:539-555`). Not sent to unauthorized connections (`fw/mesh/PhoneAPI.cpp:1037-1047`).
- Client -> node: ToRadio `mqttClientProxyMessage` is processed as a broker message (`MQTT::onClientProxyReceive` -> `onReceive` -> `onReceiveProto`) (`fw/mqtt/MQTT.cpp:354-381`). Ignored during the config handshake (`fw/mesh/PhoneAPI.cpp:491-494`).
- No request/response pairing and no acknowledgement in either direction.
- MQTT queue when not connected (direct mode): 16 entries, oldest discarded (`fw/mqtt/MQTT.h:24`; `fw/mqtt/MQTT.cpp:814-832`). Direct broker reconnect: every 30 s, after 5 failures triggers a network reconnect (`fw/mqtt/MQTT.cpp:55`, `:526-575`, `:626-635`).
- MQTT echo of our own packet produces a local implicit ACK (1.2 item 4).

---

## 25. Python client: request tracking summary

- `sendData(..., wantAck, wantResponse, onResponse, onResponseAckPermitted)` registers a handler under the packet id (`py/mesh_interface.py:507-583`, `:950-958`).
- Packet ids: 10-bit counter in the low bits plus 22 random bits (`py/mesh_interface.py:1144-1155`).
- Timeouts: `Timeout(maxSecs=timeout)`, default 300 s for `MeshInterface` (`py/mesh_interface.py:92-123`); `Timeout` default 20 s (`py/util.py:199-206`); polling every 0.1 s (`py/util.py:208-247`).
- No client-side retransmission of mesh packets; resends from the local queue happen only while waiting for TX queue space (`py/mesh_interface.py:1231-1279`).

---

## 26. UNRESOLVED items

1. Telemetry modules present on a given device: depends on build flags and detected sensors (`fw/modules/Modules.cpp:230-260`). Resolve per target build.
2. Fate of the RATE_LIMIT_EXCEEDED NAK when the client leaves `from = 0` (18.4). Resolve with a native-simulator or hardware test that sends two texts within 2 s and logs FromRadio.
3. Whether a local `want_ack` admin packet without a reply produces an ACK the client sees (20.6). Resolve with the same kind of test (admin setter, `want_ack`, no `want_response`).

Resolved during the study: `ACK_PROOF_ENFORCE = false` (1.5); `next_hop` not sent to clients (2); opaque uplink set = 8 (3.2); no firmware sender of S&F client requests, `retry_delay` unused (11); Audio `wantReplies` default false (15); TMM stats have no client path (21).

---

## 27. Summary table

| Interaction | Asker | Answerer | ACK / retry? | Cache of another's data? | Client-visible effect |
|---|---|---|---|---|---|
| Routing ACK (explicit) | any node sending `want_ack` unicast | destination (ReliableRouter) | ACK NONE; DM text ACK itself `want_ack` | pending map, ACK-proof verdict | FromRadio ROUTING packet, `request_id`, `from` = destination |
| Implicit ACK | originator | any relay (overheard) | local ACK NONE; stops retries if heard over LoRa | relay byte, RSSI/SNR | ROUTING NONE with `from` = own node |
| Retransmission | originator / relay | -- | 5 attempts unicast, 3 broadcast, 3 hop-level; last unicast retry floods | PendingPacket copy | MAX_RETRANSMIT NAK to client |
| NAKs (NO_CHANNEL, PKI_UNKNOWN_PUBKEY, NO_RESPONSE, etc.) | requester | destination / local node | no retry of NAK | -- | ROUTING packet with error |
| Next-hop learning | -- | ACK/reply relays, traceroute | route health 30 min / 3 failures | `next_hop`, route health, TMM next hop | none directly |
| Duplicate suppression | -- | every node | causes 0-hop re-ACK / relay of originator repeats | PacketHistory, opaque ring, text id ring, client id list | duplicates not shown |
| NodeInfo | node (periodic, new node, PKI error, heartbeat nonce 1) | target node, or TMM relay on its behalf | reply throttled 30 min scaled / 60 s short, 12 h per requester; no NAK when throttled | NodeDB user + key; TMM NodeInfo cache | node_info / packets; heartbeat nonce 1 |
| Position | node / client | target | 3 min reply throttle; NO_RESPONSE at precision 0 | NodeDB position satellite | packets; 10 s client rate limit |
| Telemetry | node / client | matching telemetry module | relayed broadcast requests ignored (non-router); NO_RESPONSE if no module | NodeDB device + environment satellites | packets; 10 s client rate limit |
| Traceroute | node / client | destination; relays edit payload | request `want_ack`, reply inherits; 30 s client limit; 10 s device timeout | next hops learned | reply packet with route |
| NeighborInfo | node | target | 3 min reply throttle | 10-neighbour table | packets |
| Paxcounter | node | target | no throttle | none | packets |
| Range test | sender | -- | none (hop_limit 0) | file log on receiver | packets |
| Store & Forward | client node | S&F server | busy/public-channel text replies; 25 msgs / 5 s pacing; heartbeat 900 s | server text history, per-client cursor | history as TEXT packets |
| Key verification | node A (via admin) | node B | 60 s idle, 3 min cap, 60 s cooldown; no NAK when busy | pending peer key; verified flag | ClientNotifications, admin key_verification |
| ReplyBot | any node (/ping etc.) | node with ReplyBot | 15 s DM / 60 s broadcast cooldown | 8-entry cooldown ring | DM text |
| Status message | -- | -- | none | NodeDB status satellite | packets, replay |
| Audio | node | target (empty reply) | none | none | packets |
| Serial | serial bridge | -- | `want_ack` true | none | -- |
| Waypoint | node / client | -- (NO_RESPONSE if asked) | client default `want_ack` | WaypointStore (10, flash) | packets |
| Remote hardware | node | target (gpio channel) | none | watch mask | reply packet |
| DMShell | admin-authorized peer | Portduino node | own seq/ACK, window 4, 25 retransmits | session state | -- |
| Admin get/response | client / remote admin | target node | Python `want_ack`; responses need outstanding request (300 s) | passkey, outstanding table | admin reply packets with passkey |
| Admin setters | client / remote admin | target node | passkey 300 s; ACK NONE if `want_response`; transaction 60 s idle | -- | Routing NONE reply; reboot 7 s |
| Client handshake | client | local node | -- | -- | config dump, config_complete_id, replay |
| Heartbeat | client | local node | -- | -- | queueStatus (nonce 0) |
| Client queues | -- | local node | oldest-drop rules | queued packets (8/16/32) | delivered after reconnect |
| Traffic management | -- | relay node | position dedup 6 h, rate limit, unknown limit; direct NodeInfo 60 s/60 s/1 s | unified cache, 2000 NodeInfo cache | none directly |
| MQTT proxy | local node <-> client | -- | none | 8-entry proxy queue | mqttClientProxyMessage both ways |
| MQTT echo ACK | broker echo | local node | does not stop retries | -- | ROUTING NONE to client |
| XModem | client | local node | ACK/NAK per block, 25 NAKs -> CAN | -- | xmodemPacket |
