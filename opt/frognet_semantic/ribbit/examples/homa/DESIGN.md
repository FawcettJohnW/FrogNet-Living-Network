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

- **The read is the grant.** Everything addressed to a consumer is already in memory with its length. The consumer's
  one standing read fires for a message only when the whole message is there, so a reader never waits on part of
  one, and the consumer takes the shortest first from what has arrived (`Sock::inbox`). (As built, a message's bytes
  come with the read that announces it, not on a second read: a second read would cost a round trip per message.
  What is left of Homa's reason for holding a long message back is that a short one can be behind at most one
  240,000-byte piece in transit to the reader: by the two-machine tables in section 5a that is about 4 ms within
  Seattle and 8 ms to New York, inferred from them, not measured directly.) There is no GRANT, no priority field
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

**Soft Homa is a carrier of its own (`[SOFT_HOMA_CARRIER_V1]`, built 17 Nov).** Kernel Homa and soft Homa answer the
same calls and are not the same wire, so they have two names in the capability tuple, `homa` and `softhoma`. The
Homa driver is the same for both: `core/homa_socket.py` makes its calls through the process's own symbols, so
where `libsofthoma.so` is loaded into the proxy or the daemon (LD_PRELOAD, `SOFTHOMA_RAM`, `SOFTHOMA_IP`) they are
soft Homa's, and it says so in its start-up line. One process has one of the two, whichever answers IPPROTO_HOMA
in it. A kernel node and a soft node therefore find TCP in common. A node's word can be `softhoma` (Homa only, and
it must be soft; without it the process stops); `homa` with soft Homa loaded stops and says the word is
`softhoma`. Gate: `proxy/test_homa_ordering_oracle.py soft` runs the real engine at both ends through the driver
on soft Homa with no substitution (the fan-out workload exact, frames in order, several in flight), then closes the
daemon's socket with six requests outstanding and every one is failed in about 0.3 s by its capability cell, with
no timer.

**A frame larger than one message goes in pieces** (`[FRAMES_GO_IN_PIECES_V1]`; John, 17 Nov: "don't send more
than 240K in a single message and do message splitting and reconstruction like the Communicator does with the
video", in the driver). Before this the driver refused a frame over Homa's 1,000,000 bytes and the daemon answered
an oversized reply with ERROR 413. Now the driver asks its carrier what it takes as one message
(`homa_socket.max_message()`: 1,000,000 under the kernel module; under soft Homa the Homa API interface's record,
`softhoma_record_bytes()`, 240,000 unless `SOFTHOMA_RECORD` says otherwise) and cuts anything larger into numbered
pieces, each a whole Homa message, rebuilt at the far end before the engine sees it. Under soft Homa every piece is
therefore one cell, which is the path that was measured fast; the several-cell path inside the Homa API interface
(section 7) is no longer reached by FrogNet's own traffic. It is still reached by a program that calls the socket
itself with a larger message, his `cp_node` included.

| Message | Sent by | Meaning |
|---|---|---|
| `FNH1` session seq + frame | proxy | a whole frame, as before |
| `FNH2` session seq index count + bytes | proxy | one piece of a request; one RPC each, which the daemon keeps open |
| `FNH1` rseq + frame | daemon | a whole reply, as before |
| `FNH2` rseq seq index count sent + bytes | daemon | reply piece `index` of the reply to request `seq`; `sent` is how many of the `count` pieces have gone |
| `FNHS` session | proxy | a spare: an RPC of no request, left open at the daemon for a reply piece to go on |
| `FNHM` session seq index | proxy | an RPC for reply piece `index` to arrive on, sent only when the spares open were too few |
| `FNH0` | daemon | closes a request RPC that no reply piece needed |

**A large reply costs no extra round trip** (`[THE_RPCS_ARE_ALREADY_OPEN_V1]`; John, 17 Nov: "Allocate max size
before, take all that come, and if the max was reached, then look for the flag"). Homa lets the daemon speak only
in answer to an RPC, so a reply of m pieces needs m RPCs open. The request's own RPCs are used first. The rest go
on the session's spares, which the proxy keeps open at the daemon (each is one held read) and replaces as they are
used. `sent` is the flag: when it is under `count` the spares ran out, the proxy asks for the remaining pieces with
`FNHM` (one round trip, once), and from then on keeps that many more spares open. The number kept open is what the
session has needed; nothing is guessed and nothing is capped. A session that never has a large reply opens none.
Unlike the Communicator's video nothing is
shed: a piece that cannot be sent ends the session, as any lost frame does. Order is unchanged: a frame takes its
turn when its last piece is in. Gate: `proxy/test_homa_ordering_oracle.py pieces` runs the ordered storm on a
carrier limited to 64-byte messages (3,184 pieces; every frame cut at one end is rebuilt at the other; none over
the limit; 146 reply pieces went on spares already open and 35 had to be asked for, once each, after which the
proxy kept 35 spares open; the liveness checks pass with several RPCs per request). NOT measured: a large frame through the driver
on soft Homa on real machines, and a piece whose contents change from one frame to the next.

Not done: one process holding both kernel Homa and soft Homa at once (a node with the module that also talks to
soft-only peers reaches them on TCP today); which memory soft Homa's space lives in on the nodes (`SOFTHOMA_RAM`
is given by hand; by `[WHICH_RAM_V1]` it is the SystemRam); none of this has run on a node.

## 5a. Trying it on machines

`tools/try.sh` runs his `cp_node`, unchanged, on soft Homa across machines: `try.sh build` on each, `try.sh ram` on
the one that holds the memory (it prints the commands for the others with its address filled in), `try.sh server
RAM-ADDRESS` and `try.sh client RAM-ADDRESS SERVER-ADDRESS [WORKLOAD] [OUTSTANDING]`. A machine's own address is
FrogNet's `getEth0Address` where there is one. His `cp_node` looks its servers up as `node1`, `node2`, ...; the Homa API interface
answers those names from `SOFTHOMA_NODE<N>` and `/etc/hosts` is not written (FrogNet watches that file). As of
17 Nov this has run on one machine only.

His tools are written for x86. On any other machine `try.sh build` changes two places in the copy of his source it
builds from, and says so: `rdtsc()` in `util/test_utils.h` reads CLOCK_MONOTONIC in nanoseconds (his code calibrates
the counter against the clock, so his figures stand), and `cp_node.cc`'s crash handler prints that machine's program
counter. Found on the first node build (a Pi); checked by cross-compiling all three tools for aarch64 and by
running the whole gate on x86 with the clock change. His calls, his workloads and his reporting are not touched.

The first two-machine run (two Pis, 17 Nov) sent one request and stopped. The request and its reply both crossed
(the debug lines show the row written, received, answered and the answer received, 3 ms end to end); his client
never sent a second. With no `--gbps` his client computes a rate of 0, draws an interval of infinity and adds it
to a `uint64_t` (`cp_node.cc`, `next_start += interval_dist(rand_gen)*cycles_per_second`). That conversion is
undefined: x86 gives 0, so the next start is always in the past and it sends at once; ARM gives the largest
number, so it waits forever. Reproduced with his arithmetic alone, compiled for both (x86: 0; aarch64 under
emulation: 18446744073709551615). Off x86 `try.sh client` therefore gives his client `--gbps 1000000` through his
own option, which keeps the next start in the past; his source is not changed for it.

**First figures on two machines** (two Pis, 10.250.250.1 holding the memory and his server, 10.251.251.1 his
client; 100-byte messages, 8 outstanding; 17 Nov, Homa API interface v6): 3.3 to 3.7 thousand round trips a second, round trip
P50 2.1 ms, P99 4.2 ms, P99.9 19 ms. In the server's own log the time from a request's row arriving to its reply
being written is P50 0.10 ms, P99 0.32 ms (1,939 requests), so about 2.0 of the 2.1 ms is outside his server: the
two crossings of the link and the memory. No control was run in that session, so these say nothing yet about TCP
on the same machines; `try.sh tcpserver` and `try.sh tcpclient SERVER-ADDRESS` are that control (his cp_node on
TCP, straight between the two, no memory). On one x86 machine TCP is ahead at 100 bytes (P50 57 us against 340 us),
which is the case this is not built for.

**The control, same two Pis, 16 minutes later** (not the same session, so the link may have differed; one ping
between them read 1.78 ms):

| 100 bytes, 8 outstanding | soft Homa (Homa API interface v6) | his cp_node on TCP |
|---|---|---|
| round trips a second | 3.3 to 3.7 thousand (two seconds at 2.6 and 2.8) | 3.7 to 4.0 thousand |
| round trip P50 | 2.05 to 2.13 ms | 1.94 to 1.95 ms |
| round trip P99 | 4.1 to 4.5 ms | 2.8 to 3.1 ms |
| round trip P99.9 | 14 to 19 ms | 7 to 10 ms |

Predicted before it was run: TCP ahead at this size. It is, by about 0.15 ms at the median (7%) on a link whose own
round trip is about 1.8 ms, and by more in the tail. The memory was on the server's machine, so both carriers
crossed the link once each way; with the memory on a third machine soft Homa crosses two links each way and this
figure does not cover that.

**240,000-byte messages, one outstanding, same two Pis, the two runs a minute apart** (Homa API interface v7; the soft Homa
server was printing a debug line per request):

| 240,000 bytes, 1 outstanding | soft Homa | his cp_node on TCP |
|---|---|---|
| round trips a second | 150 to 180 | 50 to 60 |
| round trip P50 | 6.2 ms | 16.3 ms |
| round trip P99 | 8.7 to 9.8 ms | 20 to 22 ms |
| his own "Gbps out" | 0.30 to 0.34 | 0.11 to 0.12 |

Predicted before it was run: TCP ahead several times over. Wrong: soft Homa is ahead 2.6 times. The prediction
counted the encoding and forgot what the memory does with contents. His `cp_node` sends the same payload every
time, the cell it lands in holds the last one, and the engine sends what changed: measured on one machine, tens of
bytes cross to and from the memory per round trip where TCP carries 480,000. TCP's 16 ms is the link carrying
those bytes; soft Homa's 6.2 ms is the link's round trip plus processor time (in his server, request row in to
reply written is P50 2.1 ms at this size, over 2,957 requests). This is the engine's best case. A payload that is
different every time has to cross, a third larger for the base64, and has not been measured; his tool cannot send
one.

**A third node, far away (the node John calls New York, 10.123.123.1), as the client; memory and server still on
10.250.250.1; 240,000 bytes, one outstanding; 17 Nov.** Read from the server's side only (the client's own output
was not captured, and the link was not pinged):

| 240,000 bytes, 1 outstanding, far client | soft Homa | his cp_node on TCP |
|---|---|---|
| round trips a second | 10.0 | 4 (193 in 50 seconds, the first seconds slower) |
| time per round trip | 100 ms (P50 99.9, P90 100.6, P99 106.6, between 222 requests arriving) | about 250 ms |

The two runs were about a minute apart. Same reason as on the near pair: TCP carries 480,000 bytes over the long
link each round trip, and soft Homa carries what changed, which for his constant payload is almost nothing, so its
round trip is about the link's own.

**The same-session tables (17 Nov, Homa API interface v7), near pair and far pair, six sizes, are in
`REPORT-soft-homa-against-tcp-20261117.md`**, which stands on its own. In short: the same as TCP at 100 and 1,000
bytes on both links; 2.5 to 2.7 times faster from 10,000 to 240,000 bytes on the near pair and 1.3 to 2.5 times
from 100,000 to 240,000 on the far pair, on his constant payload; and at 1,000,000 bytes 10 times slower near
(649 ms against 62 ms) and 2.5 times slower far. The last is the several-cells path of item 2 below, now timed on
nodes: it is not only the one-machine loopback effect described there, because the server and the memory share a
node and meet over loopback there too, and because cells written as several records are not sent as differences.

`try.sh servers RAM` (both of his servers at once) and `try.sh compare RAM SERVER` (the client alternates soft
Homa and TCP at each of six sizes, each carrier once first and once second, a ping before and after, a table at the
end kept under `results/`) put the two in one session. It is run by hand from two terminals; it is not yet the
harness in which the participants announce themselves in the memory and the controller's tuple gives the test.

**Kernel Homa on the Pis (17 Nov, compiled only).** The nodes are Pi 5s on Linux 6.12.87; his module has no branch
for 6.12. His `linux_6.13.9` branch, cross-compiled for arm64 against the Raspberry Pi 6.12 kernel source
(`rpi-6.12.y`, 6.12.118, `bcm2712_defconfig`), compiles every file once it is given the ARM64 clock frequency
(`arch_timer_get_cntfrq()`, as his main branch does; that branch reads x86's `cpu_khz` and `tsc_khz`, 30 errors,
nothing else). It was not linked against the Pi kernel's own symbols, loaded or run. Its `homa.h` has the same
layouts as the one this work is pinned to. That branch is 424 commits behind his main.

Not yet measured on machines: payloads that change, his workloads w1 to w5, more than one client, the memory on a
third machine.

His `cp_node` server does not stop on a failed `recvmsg`: when the memory ends it calls it again at once, for ever,
printing each time (30 GB of log in a few minutes here). `SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1`, which `try.sh` and the
gate set, ends the process there with one line instead.

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
today are kernel Homa and TCP). The kernel module has not run on any node, so the choice has only been exercised
with the detection stood in.

**Soft Homa, first and second stages (make it work, make it safe): built.** `src/softhoma.cpp` is section 4 on `ribbit::Space`
(`[SPACE_V1]`), as a library loaded ahead of libc; `src/softhoma_ram.cpp` holds the memory; `make HOMA=...` builds
both incrementally. `tools/gate.sh /path/to/HomaModule` holds it to his tools, unchanged, on one machine:
`util/homa_test` (invoke, poll, shutdown, rtt, ioctl, read, private, set_buf, stream, wmem) against his
`util/server`, and `util/cp_node` as server and client on fixed 100-byte messages, his w2 and w4 distributions, and
500,000 and 1,000,000-byte messages; two `cp_node` clients at once on one server; then his server is killed with
requests outstanding and the client is told within tens of milliseconds, by the server's capability cell leaving
the memory, with no timer. 18 of 18 with the field-order comparison. His `homa_test close` ends in `std::terminate` by its own construction (a joinable
thread is destroyed) and is not in the gate.

Measured on one machine, through the memory as a separate process (this shows the contract is kept; a comparison
is multi-machine): one request at a time, 100 bytes, round trip P50 about 330 microseconds; `cp_node` 100 bytes with
8 outstanding, about 9 to 13 thousand a second; with 1 outstanding, 500,000 bytes about 380 ms a round trip and
1,000,000 bytes about 880 ms (finding 2 below is why).

Found, and what follows from each:

1. **A frame to the memory over 1 MiB ends the session, and the memory says nothing**
   (`ribbit/cpp/src/ram_host.cpp`, `MAX_FRAME`, `recv_frame`). Soft Homa keeps every cell under it (a message over
   240,000 bytes is several cells) and says so loudly if its session ever ends. The platform's silence is John's to
   rule on; it is not changed here.
2. **Where the time goes on large payloads (measured 17 Nov; this replaces an earlier diagnosis here that was
   wrong).** The earlier text blamed the memory's row differencing and proposed sending bytes past the engine.
   Neither is right: `[VENDOR_STREAM_V1]` was removed from the platform on 17 Oct, and the memory uses almost no
   processor on these runs (0.27 s in 9 s). His `cp_node` server was profiled by function (callgrind) on 240,000-byte
   messages, and the memory's sockets were traced (strace). There are two causes, both in the platform:
   - **A write classifies every string it carries, and throws the answer away.** `ribbit/cpp/include/cellwire.hpp`
     `field_order()` calls `semtpl::infer_schema()` to learn the order of a cell's fields. That reads each string
     several times, code point by code point, to ask whether it is an address, an interface, an enum or a host
     name (`leaf_type`), and only the field order is kept, which does not depend on the answer. It is 72% of the
     sender's instructions at 240,000 bytes. Walking the keys without classifying the leaves
     (`[FIELD_ORDER_WITHOUT_LEAF_TYPES_V1]`, 12 lines in `cellwire.hpp`, its own commit on this branch so that it
     can be dropped alone) gives, one request outstanding, P50 round trip:

     | message | before | with the change |
     |---|---|---|
     | 100 bytes | 167 us | 153 us |
     | 48,000 bytes | 2.98 ms | 0.92 ms |
     | 240,000 bytes | 13.6 ms | 2.85 ms |

     What it rests on: the two orders are compared on 300,000 generated bags (nested objects, lists of objects,
     unsafe keys, the keys `type` and `_array`, every kind of string `leaf_type` tells apart) and none differs;
     `tools/field_order_check.cpp` is that comparison and `tools/gate.sh` runs it. The platform's own
     `ribbit/cpp/qualify.sh` gives the same result lines with and without the change, but it does not pass in the
     sandbox either way, so it certifies nothing here: the semtpl oracle reports `canon_key` 84 of 220 differing
     from `core/json_handler.py` (the Python has carried a body's field set in a sensor upsert's key since
     befe497c, 1 Nov; `semtpl.hpp` was last changed 19 Oct), fnwp e2e and ramrows fail with it, and WRITE-WINDOW
     and ROW-SCAN-BOUND pass on one run and fail on the next in both copies. None of that is touched here.
     The other examples' gates (MQTT, Redis, FD&F) have not been run against the change.
   - **On one machine only: 44 ms for every 64 KB of a frame.** A session's sockets have a 32 KB send buffer
     (`frogram.cpp`, "the buffer is the latency"). The loopback interface's segment is 65,483 bytes, so the buffer
     holds one segment, the receiver has one segment and delays its acknowledgement 40 ms, and a 287 KB frame takes
     five of them: the 175 ms per cell recorded before. With the loopback MTU set to 1,500, as on a wire, a
     1,000,000-byte round trip goes from 750 ms to 74 ms (30 ms with the change above). This is the sandbox, not
     the design; large messages have to be timed on the nodes.

   What was the Homa API interface's own is fixed (`[SOFT_HOMA_FAST_V1]`): its base64 wrote a byte at a time and copied each piece
   twice; it now encodes and decodes in place. Payloads stay base64 fields in the cell's bag, on the semantic wire.
3. **Made safe (second stage).** A client socket's name is its address and a nonce, and its RPC ids start at a
   random even number, so two clients that pick the same port cannot take each other's answers. A request in flight
   has a slot from its sender's own free list; its cells and its reply's cells are named by the slot, so a cell is
   written over only after what it held was consumed, the memory holds as many cells as there are requests in
   flight, and nothing bounds how many that is (the first stage's ring of 4,096 and its EAGAIN are gone). `read()`
   and an unknown `ioctl` are answered EINVAL as the module answers them; a socket shut down gives nothing more.
4. **Two binds of one port at the same instant can both succeed**: the memory has no compare-and-set. A bind
   refuses (EADDRINUSE) a port whose capability cell is already there. A socket bound to one address is named by
   that address; bound to any, by `SOFTHOMA_IP`. A server that closes its socket says `closed` in its capability
   cell (the cell itself leaves only with the process), and those with requests outstanding to it are told at
   once; a request sent to a server known gone or closed ends at once (EHOSTUNREACH) and nothing is written. A
   request to a server nothing has been heard of asks the memory once whether its capability cell is there, says in
   one line what it found, and ends at once if it is not (found on the first two-machine run, where a client
   started with no server running sat with 8 requests outstanding and said nothing).
5. **IPv4 only.**
6. **The sandbox has two cores.** Two `cp_node` clients at once each see a round trip of almost exactly 8 ms (one
   client alone: 0.5 ms), which is the scheduler's time slice with more runnable threads than cores, not the
   memory. Timing with more than one client needs the nodes.

**The transport is not yet transparent, so the job is not done (John, 17 Nov: "The transport should be transparent.
If it is not, the job is not done").** Only the FNW1 semantic wire (proxy to daemon) asks `core/homa_transport.py`
which carrier to open. Everything else that talks between nodes opens its own TCP socket, read from the code:

| What | Where it dials or listens for itself |
|---|---|
| FrogTorch's tensor link (owner's memory to reader) | `agent_workload/tuplespace/tensor_plane.py` 1194, 1590, 2315; `memory_plane.py` 70 |
| FrogTorch's control (the frogtorch-ram) | `agent_workload/tuplespace/frogtorch_ram_client.py` 110; `frogtorch_ram.py` 288 |
| AIConnect | `agent_workload/aiconnect/producer.py` 191, 216, 249, 381 |
| Every C++ Ribbit session (three sockets each) and the RAM host's listener | `ribbit/cpp/src/frogram.cpp` `dial()` 443, 449, 452, 1058; `ram_client.cpp` 13; `ram_host.cpp` 1361 |

So a PyTorch or FrogTorch run today puts nothing on Homa whatever the node's carrier word says. Done means: one
place opens a link to a peer, for Python and for C++, every one of the above goes through it, and it hands back
the bus the engine is already held to (whole frames, in order each way, many in flight, the reply finds its
request, broken is broken) on whichever carrier the hop has. Then the carrier is a property of the hop and nothing
above it changes, which is the thesis. Soft Homa itself rides a Ribbit session to its memory, so under those
sessions the carriers are TCP and kernel Homa; soft Homa is Homa's interface on the memory, one layer up.

Not built: that one place and its callers; the kernel module on the nodes; drivers for FrogTorch's three wires; the passthrough handler; the matrix.
