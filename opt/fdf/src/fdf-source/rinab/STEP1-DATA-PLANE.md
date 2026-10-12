# FD&F build step 1: the RINA data plane member to member (`[MEMBER_TO_MEMBER_DATA_V1]`)

**The region is now the DIF's management and control only.** It holds membership, the directory, flow allocation and
verdicts. SDUs and credit go directly between the two ends of a flow.

## How

- **Each program's endpoint (src/efcp_udp.hpp).** Each program owns one N-1 endpoint on the IP underlay: a UDP socket,
  as rlite's shim-udp4 uses.
  - Its locator ("ip:udpport") is the local address that reaches the RAM host. That is the loopback in the one-machine
    suite, and the machine's own IPv4 address (its LISP RLOC) on real machines.
  - Each flow end mints a small port id.
- **The allocation exchange carries the locators, as each end's own truth.**
  - The allocator's `cloc` ("ip:udpport/port") goes in its request; the region passes it into the request bag.
  - The acceptor's `sloc` goes in its verdict; the region passes it into the verdict event.
- **The PDU:** type (1) | receiving port (4) | seq (8) | payload. DATA carries an SDU, CREDIT the receiver's contiguous
  count handed to its program, CREDIT_REQ a sender asking for credit.
- **Flow control:** credit by quarters of the 4,096-SDU window, sent by the receiver. A sender a full window ahead
  sleeps on its credit word and asks after 100 ms. That is the data-transfer timer, RINA's own (DTCP): not liveness,
  not discovery.
- **Delivery:** one receiving thread per program. SDUs go to the program's socket in arrival order; a full socket
  hands them to the flow's own delivery thread.
- **`[ENDPOINT_ONCE_V1]`:** the endpoint is made exactly once per process, the first asking thread claiming it by
  compare-and-swap. Before this, two threads allocating at once (rinaperf -p 2) could each open a socket, and a flow
  could advertise the one nobody read: test 0011 lost replies in 1 run of 21. After the fix, 0011 passed 40 of 40.

## Measured

**Same session, sandbox (one core), quick-bench:**

| build | ping ms | rr tps | perf received | perf Mbps |
|---|---|---|---|---|
| 553f2f4c (SDUs through the region) | 0.301 / 0.303 | 3,562 / 3,254 | 20,000 | 155.4 / 152.6 |
| member to member | 0.022 / 0.021 / 0.021 | 52,123 / 51,658 / 52,005 | 20,000 | 1,875 / 1,964 / 1,991 |
| member to member, primed (regenerated store) | 0.023 / 0.022 | 51,640 / 44,736 | 20,000 | 1,994 / 2,053 |

**bench.sh's AI-Host topology (two namespaces, shim-eth):** this build in the sandbox beside rlite as measured on
AI-Host. The machines differ; a same-machine run is needed for a true ratio.

| measure | rlite (AI-Host, measured) | this build (sandbox) |
|---|---:|---:|
| ping avg rtt | 0.012 ms | 0.020 ms |
| rr | 85,598 tps | 51,459 tps |
| perf received | 2,940 Mbps | 1,951 Mbps (20,000 of 20,000) |
| 16 parallel pings | 0.181 ms | 0.298 ms |

**rlite's 30 tests:** 30/30, unprimed and primed. The primed store was regenerated: 1,008 exemplars → 10 templates.

## Not yet: these belong to later pieces of step 1 and to later steps

- **The endpoint uses the RAM host's network,** not the DIF's own shims. The suite's shim-eth/veth topology is
  configured, but SDUs travel the loopback of the RAM's namespace. A raw-Ethernet shim (shim-eth) is still to do.
- **No relaying.** A flow goes straight from end to end over the underlay; RMT relaying through intermediate members
  for multi-hop DIFs is still to do.
- **No reliability.** Flows deliver in arrival order with no retransmission, which matches rlite's default unreliable
  flow spec. Reliable flows (in-order, max_sdu_gap 0) need DTCP retransmission.
- **No re-linking.** A flow allocated before a fork cannot be used by the child; the child's locator differs from what
  the peer holds.

---

# Step 1, second piece: SDUs follow the DIF's own links, relayed hop by hop (`[RMT_OVER_DIF_LINKS_V1]`, src/rmt.hpp)

**Why.** The first piece sent SDUs over the loopback of the RAM host's network. That ignored the DIF's configured links:
rlite's 0028 passed only because the region refused allocation when it computed no path, while a flow's SDUs never
touched the veth links. FD&F's discovery claims (D3, experiment 3) need the data to follow the DIF's links.

## How

- **Addressing.** A flow end is addressed by DIF, machine (namespace), program and port:
  `rmt:<dif>:<node>:<pid>:<port>`. Each end states it in the allocation exchange as its own truth; the region writes
  the flow's DIF into the request bag and into the verdict. Routes are per DIF, so a flow never crosses into another
  DIF's links.
- **Each program's endpoint** is an abstract unix datagram socket `rina-ep-<pid>` on its own machine. A PDU to an end
  on the same machine goes straight there.
- **Every other PDU goes to the machine's agent (`rlite-uipcps`), now the machine's RMT** (abstract socket `rina-rmt`).
  The agent sends it out of the link its route gives:
  - on shim-eth links, an Ethernet frame (ethertype 0xD1F0) to the next machine's link address;
  - on shim-udp4 links, UDP to the next IPCP's address, resolved from its name as rlite's shim-udp4 does, port 0x0d1f.

  Every agent on the way does the same until the PDU reaches its machine.
- **Routes are derived by each agent itself,** from the DIF's records:
  - adjacencies (written by enrollment and lower-flow allocation);
  - IPCPs and their shims' devices;
  - each machine's own statement of its devices, up or down, with their link addresses (the agent now reports MACs).

  The agent runs a breadth-first search over links up at both ends. Routes are published whole, read without a lock,
  and re-derived whenever the region signals that the topology may have changed: a ctl command, a machine's devices
  changing, a machine's agent ending. The signal is an event pushed into each agent's own input space, the held read
  it already keeps.
- **`[HOP_LIMIT_V1]`:** every PDU carries a hop limit of 64, taken off by each agent that relays it to another
  machine. Agents derive routes independently, so two can briefly point at each other while a change converges.
- **Two fixes the tests forced:**
  - 0030: shim-udp4 links.
  - 0026: an agent relays only frames addressed to its own link (PACKET_HOST). A bridge floods unknown destinations to
    every port, and relaying those copies lost replies.

## Proof the data crossed the links (RINA_RMT_STATS=FILE: each agent's counts when it ends)

- **0028 resilient routing** (four namespaces, five veth links taken down one by one): the end agents each forwarded
  and delivered 12 PDUs; intermediate b relayed 9; c relayed 3 once ab and bd were down; no PDU lacked a route.
- **Tests 24–30 together, final build:** forwarded 229, delivered 155, no route 0, hop limit reached 0, send errors 0,
  flooded frames ignored 10.

## Measured

| case | ping ms | rr tps | perf received | perf Mbps | 16 parallel pings ms |
|---|---|---|---|---|---|
| same machine, one namespace (quick-bench) | 0.018 | 57,829 | 20,000 | 1,925 | — |
| relayed: two namespaces over shim-eth through both agents (bench.sh topology) | 0.030 | 33,449 | 20,000 | 635 | 0.490 |
| rlite on AI-Host (measured 2026-10-04, other machine) | 0.012 | 85,598 | — | 2,940 | 0.181 |

**rlite's 30 tests:** 30/30 on the final build (1–23, then 24–30 together).

## Open

- Reliable flows (retransmission).
- Flows across a fork.
- The relayed path's throughput: two user-space hops per PDU at each end; the agents could move PDUs with
  recvmmsg/sendmmsg.
- A same-machine bench.sh run on AI-Host against rlite.

---

# Credit is state, not messages (`[CREDIT_IS_STATE_V1]`), and the no-fallbacks audit (2026-10-06)

**Ruling.** No fallbacks: they mask real errors. John owns the contract, the tests, the API and the RAM server; the
schema is his to choose provided it stays tuple-shaped. Use the shared-RAM practices rather than copy a messaging
architecture.

## What was wrong

The data plane had copied EFCP's credit messages: CREDIT PDUs from receiver to sender, and a 100 ms timed CREDIT_REQ
for when one was lost. A timed re-ask is exactly what the rulings forbid, and it would have hidden a lost credit
rather than exposing it.

## Now

- **The receiver states its truth.** Each time the count it has handed to its program crosses a quarter of the
  window, it posts that count to the region (op `taken`, no reply) into the flow direction's record.
- **A sender a full window ahead does one held read of that truth** (op `credit`). It is answered the moment the count
  passes what it had, or told the receiver is gone, which is said and not retried.
- No credit message, no timer, nothing that can be lost and quietly retried. A PDU of unknown type is reported, never
  dropped silently.

## Measured

| case | ping ms | rr tps | perf received | perf Mbps | 16 parallel pings ms |
|---|---|---|---|---|---|
| same machine, credit as messages | 0.018 | 57,829 | 20,000 | 1,925 | — |
| same machine, credit as state | 0.018 / 0.018 | 36,170 / 58,022 | 20,000 | 2,082 / 2,073 | — |
| relayed (two namespaces over shim-eth), credit as messages | 0.030 | 33,449 | 20,000 | 635 | 0.490 |
| relayed, credit as state | 0.043 | 33,729 | 20,000 | **1,243** | 0.457 |

Credit no longer crosses the agents, so relayed throughput doubled.

## The audit: every catch-all, retry, sleep and timeout in the RINA source

| found | was | now |
|---|---|---|
| the library's fork announcement | `catch (...) {}`, swallowed | reported |
| a new agent finding an old one still running | retried every 50 ms for 10 s (also the source of the 2–10 s agent restart delays) | fails at once, saying so |
| an agent losing the region | caught and exited 0, as if terminate had been asked | reports the region gone and exits 2 |
| derived addresses: next candidate when one is taken | the E2 design, a deterministic claim sequence | kept (not a retry) |

**rlite's 30 tests:** 30/30 after both changes.

## Machine note

AI-Host is a quad-core i7 with 64 GB. Earlier extrapolations treated it as two physical cores.
