# RESULTS — measured, tied to a version

Machine: 2-core x86-64 cloud workspace, Linux 6.18. Stock = meshtasticd 2.8.2.332b5b0 (STOCK). Platform = ribbit 0.69.

## Phase 0 — stock baseline (results/phase0-stock-2.8.2.332b5b0/)

| oracle | stock |
|---|---|
| firmware native suites (88, coverage env, ASan) | GREEN 88/88 |
| Python CLI smokevirt | 40/40 |
| Python CLI smokemesh (3-node chain) | 7/7 |
| meshtastic-mcp `ci_device_mesh_e2e.py` (2 nodes, UDP multicast) | PASS |
| RepeaterTastic vectors recaptured | 12/12 verify |

Baseline numbers (over the air, client API session, source size): PHASE0-CHECKPOINT.md §4.

## Phase 1, step 1 — packets on the shared RAM [MT_PACKETS_ON_THE_RAM_V1]

Mechanical: the node's send is a write into its own ring of cells (mt, pkt, <node>.<n>); receive is a held read on
(mt, pkt) into the same ingress path the UDP handler feeds. Nothing else changed.

| oracle | front, MESHTASTIC_RIBBIT set | notes |
|---|---|---|
| meshtastic-mcp `ci_device_mesh_e2e.py`, unmodified, multicast blackholed | PASS | 0 UDP datagrams on the capture; node0 4 writes / 3 reads, node1 4 writes / 2 reads (results/step1-20261030-084233) |
| Python CLI smokevirt (one node) | 40/40 | 253.7 s (stock 281.5 s) |
| firmware native suites (88, coverage env, ASan) | GREEN 88/88, all clean | 38 m 52 s; transport off in the suites (no MESHTASTIC_RIBBIT), so this shows the change leaves stock behaviour intact. libmtribbit was rebuilt mid-run with functions added only (0.2 facts API) |

## Phase 1, step 2a — each node writes its own facts [MT_OWN_FACTS_V1]

A packet the node sends that carries one of its own facts also writes it into the node's own latest-value cell
(mtf, user|pos|tel.<n>|status, !<node>); at start the node writes its own user cell.

| oracle | result | notes |
|---|---|---|
| meshtastic-mcp mesh e2e, RAM only | PASS | each node wrote (mtf, user, !self) |

## Phase 1, step 2b — other nodes come from their owners' cells [MT_NODEDB_IS_A_HELD_VIEW_V1]

One held read on the fact space (mtf, *); the held read's thread wakes a main-loop thread (the router's own wake), which
applies the rows through the same NodeDB functions; received packets no longer write other nodes into the NodeDB. A gone
user row (the owner's session ended) removes the node.

| oracle | result | notes |
|---|---|---|
| meshtastic-mcp mesh e2e, RAM only | PASS | each node applied the other's user cell (changed=1); the DUT's client resolves the sender (from !49d3a7cf); after the config reboot each node has the other again at once from its start write; a node's exit removes it from the other's view (gone) |
| Python CLI smokevirt (one node) | 40/40 | 281.6 s |
| firmware native suites (88, coverage env, ASan) | GREEN 88/88, all clean | 38 m 56 s; transport off in the suites |

Found on the way: the drain thread had been `disable()`d, which clears `enabled`, and the wake only moved its due
time, so it never ran; and the library had not been rebuilt after the wake was added. `harness/mtr_probe.cpp`
(fact write and hold between two processes, no firmware) showed the library correct and the wake absent. The sandbox
sync now builds the library before the firmware and forces the relink.

## Phase 1, step 3a — a node writes only its own packets [MT_ONLY_OWN_PACKETS_V1]

| oracle | result | notes |
|---|---|---|
| meshtastic-mcp mesh e2e, RAM only | PASS | each packet in the memory once, from its owner; node0 refused 2 relays, node1 1 |

## Phase 1, step 4 — good and bad data, side by side (harness/step4-bad-data.sh, 4 nodes)

Same harness, same 4-node lab (meshtastic-mcp's), the stock client library over TCP; stock on UDP multicast, Ribbit on
the shared RAM only (multicast blackholed). One run each: step4-20261030-104050 (Ribbit), step4-20261030-104210 (stock).

| scenario | stock 2.8.2 | Ribbit 0.4 (step 3a) |
|---|---|---|
| S1 every client lists every other node | 21.12 s | 0.44 s |
| S2 a broadcast from each node reaches every other client | PASS | PASS |
| S3 DM want_ack to a live node | ACK NONE, 0.04 s, from = destination | ACK NONE, 0.05 s, from = destination |
| S4 SIGKILL one node: it leaves every other client's list | never (stock keeps it) | 0.33 s (gone row) |
| S5 DM want_ack to the dead node | MAX_RETRANSMIT after 39.28 s, from own node | PKI_SEND_FAIL_PUBLIC_KEY at 0.0 s, from own node |
| S6 restart it (same state): back in every list | 0.33 s (it never left) | 0.33 s |
| S7 malformed cells (garbage user bytes, bad instance) | n/a (no memory) | logged, dropped; every node still answers |

S4 and S5 are client-visible differences: QUESTION-3 R1, R2.
Harness faults found and fixed on the way (not firmware): meshtasticd serves one TCP client at a time, so the view checks
displaced the sender's open connection; the restart used the lab's first-launch erase and came back as a new node.

## Phase 1, step 3b — the ACK is built at the edge [MT_ACK_AT_THE_EDGE_V1]

ReliableRouter::send: a DM with want_ack from our client to a FrogNet node (its user cell in the view) is written once,
with no retransmission entry; when the write lands, the node hands its client ACK NONE (request_id = the DM, from = the
destination) through MeshService::handleFromRadio, the path RoutingModule gives a received ACK. sniffReceived: no ACK is
sent back to a FrogNet sender.

| oracle | result | notes |
|---|---|---|
| step 4, 4 nodes, RAM only (step4-20261030-115924) | 7/7 PASS | S3 ACK NONE 0.04 s from = destination; sender logged "ack at the edge", destination "no ACK sent"; the DM is the only packet the sender wrote to the destination, the destination wrote none to the sender |
| Python CLI smokevirt (one node, on the RAM) | 40/40 | 279.4 s |
| firmware native suites (88, coverage env, ASan) | GREEN 88/88, all clean | 30 m 16 s; transport off in the suites |

Found on the way: the first build fed the edge ACK to the router's ingress (enqueueReceivedMessage); stock ingress
dropped it ("hop_start invalid/missing") because it never crossed a wire. It now goes straight to the client.
S5 (DM to a dead node) is unchanged: PKI_SEND_FAIL_PUBLIC_KEY at once; R1 stays open.

## Phase 2, step 1 — the client edge reads the cells [MT_DUMP_READS_CELLS_V1] [MT_OWN_FACTS_AT_START_V1]

The config dump's other nodes and the satellite replay after it are reads of the owners' cells, taken when each part
starts; the NodeDB is not consulted for other nodes on that path. Step 4 gained S8: a node shares a fixed position
(primary channel given a position precision, 30 s interval) and a fresh client of another node must show it, then again
20 s later after the config reboot.

| scenario | stock 2.8.2 (step4-20261030-130717) | Ribbit 0.6 (step4-20261030-130558) |
|---|---|---|
| S1 views | 21.17 s | 0.44 s |
| S3 DM ACK | NONE 0.04 s, from destination | NONE 0.00 s, from destination |
| S4 kill | never (R2) | 0.33 s |
| S5 dead DM | MAX_RETRANSMIT 39.78 s | PKI_SEND_FAIL_PUBLIC_KEY 0.0 s (R1) |
| S8 position at a fresh client | 1.62 s; 47.4677248, -122.3360512, alt 120, 15 bits | 1.61 s; 47.4677248, -122.3360512, alt 120, 15 bits |
| S8 again 20 s later | same | same (from the cell the node wrote at restart) |

| oracle | result |
|---|---|
| Python CLI smokevirt (one node, on the RAM) | 40/40, 250.3 s |
| firmware native suites (88, coverage env, ASan) | GREEN 88/88, all clean, 34 m 45 s |

Found on the way: the node's own cells go when its session ends, so after the config reboot its position was gone
until its next broadcast; the node now writes its position cell at start. S8's first form never passed on stock
either: the lab's primary channel shares no position ("position sharing disabled on all channels"), which is the
contract, and the scenario now enables it.

## Phase 2, step 2 — fact changes reach clients from the cells; requests are reads [MT_FACT_CHANGES_TO_CLIENTS_V1] [MT_REPLIES_FROM_CELLS_V1]

Own-fact packets (NodeInfo, position, telemetry, status) no longer go into the memory; the only packets each node wrote
in step 4 were its text broadcast and its DM. Step 4 gained S9 (another node's client asks this node for node info,
position and device telemetry) and a live half of S8 (a client already connected to the other node gets the position
when it changes).

| scenario | stock 2.8.2 (step4-20261030-155158) | Ribbit 0.7 (step4-20261030-160450) |
|---|---|---|
| S8 live client gets the position | (not measured on stock) | 0.01 s |
| S8 fresh client, and again 20 s later | same coordinates both times | same coordinates both times |
| S9 node info request | no answer in 30 s | NODEINFO 0.01 s, from the owner |
| S9 position request | POSITION 0.04 s | POSITION 0.00 s |
| S9 device telemetry request | TELEMETRY 0.05 s | TELEMETRY 0.05 s |

| oracle | result |
|---|---|
| Python CLI smokevirt (one node, on the RAM) | 40/40, 342.8 s |
| firmware native suites (88, coverage env, ASan) | GREEN 88/88, all clean, 37 m 26 s |

Found on the way: the device metrics cell did not exist until the module's first broadcast (30 s after start, and only
when telemetry broadcasting is on), so a request got NO_RESPONSE where stock answers with a fresh reading; the module now
writes its cell on its first run, keeping stock's start delay for the broadcast. Writing it from the module's
constructor crashed the node (not every global is up yet). mtr-probe is now built by build-ribbit.sh, so it cannot lag
the library.
