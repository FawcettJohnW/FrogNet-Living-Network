# Homa on CroakOS: the design

Written 2026-11-16 by Claude for John Fawcett. This file stands on its own. Statements marked **[ruling]** are
John's decisions of 16 November 2026; everything about code names a file. Homa is John Ousterhout's transport
(`https://github.com/PlatformLab/HomaModule`, read at commit `07ce63ef`).

## 1. What is being built, and why

Two things, and they are different.

1. **A driver** that carries the FNW1 semantic wire on Homa as shipped, with the semantic engine unchanged. It is
   built: `core/homa_transport.py`, `core/homa_socket.py`, `proxy/transport_homa.py`,
   `daemon/engine/server_homa.py`, `core/homa_liveness.py`, and the simulator of Homa as shipped, `core/homa_sim.py`.
   The transport is the transport: Homa owns the wire, and the driver gives the engine the bus it has on TCP.
2. **Homa's model on shared memory** (this directory): his socket calls, kept exactly, with memory underneath in
   place of his protocol. **[ruling]** "I can implement his model entirely in user space, remove his timeouts, and
   it will work on the distributed datacenter."

The thesis the work tests **[ruling]**: Homa is sufficiently advanced, or can be made so, to stand on its own
outside the datacenter.

## 2. What Homa is, read from its code

- **A message transport.** A message is a length and bytes. Nothing in the protocol looks inside
  (`util/cp_node.cc`: a message is a small struct and filler; the workloads w1 to w5 are distributions of sizes).
- **Pairwise and coupled.** A sender may send `unsched_bytes` (40,000) of a message; the rest waits for the
  receiver's GRANT, and the receiver can grant only after the sender's packets tell it what is waiting.
- **Shortest first.** Senders and receivers rank messages by bytes remaining (SRPT), with switch priorities.
- **A crash detector per RPC.** RESEND after `resend_ticks` (5 ms) of silence, BUSY in answer, abort at
  `timeout_ticks` (100 ms).
- **A kernel module for microseconds.** `homa_qdisc.c` paces packets into the NIC so a short message is never behind
  a long one in the NIC's queue; `homa_offload.c` acts on packets in softirq (GRO) and uses NIC segmentation; the
  header is laid out as a TCP header for that (`homa_wire.h`). None of this is the model. It is how the model is
  made to answer in about 100 microseconds on datacenter NICs.

In what it delivers Homa is TCP: bytes from one end, reliably, to the other, blind to what they are, the two ends
coupled for the transfer. What it changed is messages in place of a stream, no connection, and the receiver
scheduling shortest first.

## 3. What CroakOS changes **[rulings]**

1. **Memory, not messages.** "There is no such thing as a small message or a large one because there are no
   messages." A sender writes into its own space and its part is over.
2. **Consumers decide how they consume.** "The consumers own those algorithms, not the OS." Shortest-first is one
   policy a consumer may choose. It is not a property of the wire.
3. **One triggered process, async.** The write fires one process; it sees everything addressed to it, decides, and
   hands the work to its own threads.
4. **No timeouts.** A waiting request is a single held read. The per-RPC timeout is "completely unnecessary".
   Liveness is a held read on the capability tuple; the driver removes the closed connection; `/etc/hosts` is
   watched with a file watcher, not read (`core/homa_liveness.py`).
5. **No copies. Write to your own space. No cross messaging.**
6. **The contract is not changed.** His API and his tools (`util/homa_test`, `util/cp_node`) run unchanged.
7. **The kernel module is used where it is present.** "It can be detected and used if present."
8. **Capability lives in the ControlRam**, the highest .1, reached "over whatever transport is loaded". The rule for
   which RAM holds what is in `AI_READ_FIRST.md`, `[WHICH_RAM_V1]`.

Three consequences that Homa cannot have:

- **The read is the grant.** Everything addressed to a consumer is already in memory with its length. The consumer
  reads what it wants first; the bytes of a large value move when it is read. There is no GRANT, no priority field
  and no cutoffs to keep in step, and the first form of "credit as state" (a sender asking how far the receiver has
  got, `rinab/STEP1-DATA-PLANE.md`) is not used either: the standing read is the receiver saying what it will take
  ("There is no ready flag. The presence of the data is the flag", `ribbit/docs/PERFORMANCE-LESSONS.md` 1.1).
- **Nobody waits a round trip they do not need** (the same document, 1.2). A send returns when the write is in
  memory. This is where the whole-run gain came from in the PyTorch work: 1,059 s against 1,480 s, with the best
  single steps equal.
- **Contents matter.** Above the driver the engine sends what changed. An unchanged answer is RESP_SAME, 21 bytes
  whatever its size; a repeated request is REQ_REPEAT, 21 bytes (`core/semcache_wire.py`). His protocol does not
  require contents to be opaque; it only never looked.

Because every transfer has a RAM host at one end (data travels through the shared space, never member to member),
the case his receiver-driven control cannot see, a bottleneck shared by flows to different receivers, does not
occur: the RAM host receives every write and sends every read.

## 4. His calls, on memory

His contract (`homa.h`, `man/homa.7`, `man/sendmsg.2`, `man/recvmsg.2`): a socket bound to a port; a request names
its destination and gets an RPC id; the receiver gets the request with that id; a reply names the id; the sender
gets the reply with its completion cookie; a request runs at most once; an RPC can be aborted; messages are up to
1,000,000 bytes; receive buffers are a region the application registers.

| His call | On memory |
|---|---|
| `socket`, `bind` / `SO_HOMA_SERVER` | A space the socket owns, named by its address and port. A server's capability is announced. |
| `sendmsg`, id 0 (a request) | One write into the sender's own space: `(to, id, cookie, length, bytes)`. The call returns when the write is in memory. The id is the sender's own count. |
| `recvmsg` at the server | The library holds ONE standing read on everything addressed to this socket. The write fires it. The library is the consumer: it reads shortest first, because that is what his API promises and `cp_node` measures, and hands each arrival to a thread from an adaptive pool. |
| `sendmsg`, id nonzero (a reply) | One write into the server's own space, under the request's id. |
| `recvmsg` at the client | The same standing read; the reply is matched to its request by id and returned with the cookie. |
| at most once | A request is one cell under one id. Reading it twice is reading the same cell; the server's "answered" is its own state. |
| `homa_abort` | The client retires its own cell. Nothing is sent to anyone. |
| a peer that leaves | The capability tuple and the hosts watcher (`core/homa_liveness.py`): everything outstanding with it ends at once, as ECONNRESET-class errors his API already has. No timer. |
| `SO_HOMA_RCVBUF`, bpages | The library copies into the application's registered region and returns offsets, as the module does. Copy-out first; anything else only after measuring. |

A value is retired after its reader has taken it (`PERFORMANCE-LESSONS.md` 1.6). Bulk goes in large records, not
20 ms slices: the short queue is right for the semantic socket and starved a bulk link (the same document, section
2: 271 s against 137 s).

## 5. Carriers: kernel Homa, soft Homa and TCP in one network

Nothing above the bearer may know which bearer is in use (`docs/spec/spec-18-implementation.md` section 2), and
the engine is held to one bus contract on all of them (`proxy/test_homa_ordering_oracle.py`). So the carrier is a
property of one hop between two ends, not of the network:

- Each end detects what it can open and writes it in its capability tuple in the ControlRam.
- A daemon listens on every carrier it has. TCP is the reference bearer and every daemon has it.
- A proxy opens each session on a carrier both ends have. Among those, the lowest measured round trip wins, with
  no preference by kind (`docs/spec/spec-05-routing.md` 4.1, `[PURE_RTT_V1]`). A peer whose tuple has not been read
  is known to have TCP and nothing more.
- A session is never moved to another carrier and nothing stands by as a spare: if a carrier fails, the session
  ends with the reason.

That is what lets fast and slow nodes, racks with the kernel module and Pis without it, and datacenters joined
across the Internet share one memory.

## 6. What is expected, and how it is judged

- Against Homa as shipped at defaults over a long round trip: on the simulator it completed 0 of 40 RPCs at 120 ms
  (two constants, `resend_ticks` and `timeout_ticks`); with the timeout removed, waits held and timers measured,
  40 of 40 at 180 ms with 384 packets against 200 needed.
- With many transfers in flight, the gain is the kind measured in the PyTorch work, and more, since `cp_node` does
  no compute. John's prediction: better than 1.4 times. Claude's estimate: 2 to 3 times on a LAN with many in
  flight; several times across a WAN against Homa tuned to finish.
- One short request at a time on a rack: the kernel module is expected to win, which is why it is used where
  present.
- Judged by his tools, unchanged, on the same machines in the same session, whole run
  (`PERFORMANCE-LESSONS.md` section 3). The engine's part is shown in CroakOS's own workloads, not by changing his.

## 7. State

Built and passing: the driver, the simulator, liveness (sections 1 and 3.4), and the per-hop carrier choice of
section 5 (`core/homa_transport.py`, gate `core/test_homa_carrier_oracle.py`; the two carriers it chooses between
today are kernel Homa and TCP). Not built: soft Homa in C++ on `ribbit/cpp` (section 4). The kernel module has not
run on any node, so the choice has only been exercised with the detection stood in.
