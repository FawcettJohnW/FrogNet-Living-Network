# Homa under CroakOS: the record of the work

This file is the account of how the Homa work got to where it is: every ruling, every design that was tried and
dropped and why, every measurement, every test that failed and what it turned out to be, and everything that is
still unverified. `DESIGN.md` says what the design IS. This file says how it came to be that, including the wrong
turns, so that nobody pays for them a second time.

**Why it exists.** John, 17 Nov 2026: "Update your branch as appropriate so you dont drop anything, and i mean
anything. I have invested a lot in this chat and I dont want the knowledge lost, including the process of
exploration." The conversations in which this was worked out are not kept. The branch is.

**How to keep it.** Add to it as the work is done, not at the end. A new entry goes at the bottom of section 3 with
its date and commit. When something recorded here turns out wrong, do not delete it: add the correction beside it.
A mistake goes in section 4 with the rule that came out of it. Anything claimed without having been run goes in
section 7 until it has been run. All times are Pacific. "John" is John Fawcett, FrogNet's author. "His" tools, module
and code, unless it says otherwise, are John Ousterhout's Homa (PlatformLab/HomaModule at commit `07ce63e`).

Branch: `homa-under-croakos-20261116`, cut from `948b435c`. Never merged, no pull request, `main` not touched.

---

## 1. What is being tested, and the order of work

**The thesis** (John, 16 Nov): Homa's protocol "is sufficiently advanced, or can be made so, to stand on its own
outside the datacenter". That is the claim under test. It is not "the transport does not matter".

**The order** (John, 16 Nov): make it work, make it safe, make it fast.

**The frame.** Convert Homa to run under CroakOS as its transport, so the application above stays constant and only
the transport varies. Two deliverables: a **driver** (FrogNet's semantic wire on a Homa socket) and a **simulator**
(of Homa's own packets, for when there is no kernel module). Then, 16 Nov late: **soft Homa**, Homa's socket
interface on CroakOS shared memory, entirely in user space, with his API and his load generator as the contract.

## 2. The rulings, in the order they were given

Each is John's, quoted where the words matter. They are the constraints everything else is built to.

### 16 November

1. "When the transport is Homa, Homa owns the wire and how it works." The Communicator and the PyTorch work were
   shown for ideas, not as where Homa goes.
2. "Honor the contract." The transport is the bus and nothing above it changes when it is swapped. The Homa driver
   supplies what the semantic engine already requires of its transport (whole frames; in order each way; many in
   flight; a reply finds its request; broken is broken). The engine is not changed to suit Homa.
3. With no kernel module to run against: "Well, create a simulator." A simulator is a simulator; it is not to be
   delivered wrapped in qualifications that it is not the real thing.
4. Homa's keep-alive for a waiting request (a RESEND and a BUSY every 5 ms): "That is a single held read."
5. The conversion methods apply inside the driver and simulator: "no copies. Write to your own space. No cross
   messaging."
6. Homa's per-RPC timeout (its crash detector): "Completely unnecessary. Remove it."
7. "Liveness is a function of a held read on the capability tuple." A peer leaving is learned from that read, not
   from a timeout.
8. "The driver is the thing that must remove the closed connection."
9. "You don't need to read hosts; you put a file watcher on it."
10. "Highest static ip is control ram, where the capability tuples live. Reach it over whatever transport is
    loaded." (`[CAPABILITY_IN_THE_CONTROL_RAM_V1]`.) Which tuples go in the SystemRam and which in the ControlRam
    is the implementer's call (`[WHICH_RAM_V1]`, `AI_READ_FIRST.md`).
11. Of Homa pacing the NIC queue so a short message is never behind a long one: "That is sequential messaging and is
    no longer required." "There is no such thing as a small message or a large one because there are no messages."
12. "Memory, not messages. Consumers decide how they want to consume messages. The consumers own those algorithms,
    not the OS." No connection is "already done"; scheduling is "up to the consumer".
13. "We can't mess with the contract." His API and his load generator (`cp_node`) run unchanged. No variant of his
    workload is added.
14. "So I can implement his model entirely in user space, remove his timeouts, and it will work on the distributed
    datacenter."
15. Of the kernel module's short-message latency: "I don't have to give that up. It can be detected and used if
    present." Hence carriers per hop.
16. "With this design, I should be able to mix both kernel and soft Homa with tcp, linking datacenters across the
    internet, combining high and low speed nodes of all protocols."
17. The Homa code's name is `core/homa_transport`; `core/sem_transport` is left as it was.
18. The work is committed to the branch as it is done.

### 17 November

19. "The transport should be transparent. If it is not, the job is not done." "The fact that it is sending Homa vs
    TCP should be transparent to the Torch."
20. Of the proxy-to-daemon semantic wire: "There should not be anything else talking between the nodes." "The
    connection to the RAM host is expected and should be transport transparent."
21. Of what a rank's session with FrogTorch's RAM carries (membership, the store, the gate, the bags, references):
    "Those are all tuples." Of a C++ Ribbit client's session with its RAM host: "That should be using the FNWP
    wire."
22. Of a rank listening on a port for readers: "That is completely wrong. The tensor should not be listening at all
    -- it should be using held reads, and when another node needs a tensor it does not ask the other node for it.
    It gets it from tuple space."
23. "The bits stay where they are means that the bits are in tuple space and it is not copied between nodes. If
    node B needs a tensor from node A, it uses a reference to say this data is Here instead of copying it."
24. "Tuple space IS the RAM host."
25. "There is not necessarily one RAM for all PyTorch work, if it doesn't make sense. There may be only one, and
    there probably is only one, but if it makes sense to do something else, we can." (11 Nov: "using whatever ram
    regions make sense".)
26. "Don't send more than 240K in a single message and do message splitting and reconstruction like the
    Communicator does with the video", in the driver.
27. Of a large reply costing an extra round trip: "Allocate max size before, take all that come, and if the max was
    reached, then look for the flag."
28. Grants: "Grants became reads, didn't they?" (Yes: section 5.) "The reader does not see the held read until the
    message is ready." "A short message can be interleaved with the long ones, so the short read priority still
    holds."
29. "The driver is the thing that decides what goes on the wire. The transport just places it there. The driver
    should be able to accept multiple incoming messages simultaneously, as through fan-in, giving each thread a
    separate version of the driver to handle that message. The drivers should stick the wire bytes on a queue that
    the wire manager reads off. If you wish, use two queues." The queues are inside the Homa driver, between the
    engine's threads and the Homa socket: "That is the right place."
30. "The driver should be independent and able to run alongside the existing drivers. There should be no risk to
    existing systems at all."
31. Of an address kept in a setting: "You know it can change address during a run. Right?"
32. "The Homa tests in the simulator need to activate the driver between machines on the topology and use that as
    the transport for a full battery of tests. That is passing."
33. "You need to have the selection criteria set so that the calculation of the ram host ip address is
    deterministic. One option is to just set it to databasehost.frognet."
34. Names: `libsofthoma.so` is "the Homa API interface" (not "the shim", and not "the RAM manager", which was a
    wrong guess at what he meant). `softhoma-ram` is the RAM host its cells live in.

## 3. What was done, in order

### 16 Nov, evening: the driver, the simulator, liveness (`8c67920f` to `d2645d32`)

- The Homa driver for the semantic wire: `core/homa_socket.py` (the socket, by ctypes, layouts pinned to his
  `homa.h`), `proxy/transport_homa.py` (`_ClientMux`, `_MsgWorker`), `daemon/engine/server_homa.py`
  (`MsgDaemonServer`, `_MsgSession`). One Homa socket per process serves every peer; the driver numbers frames and
  holds early ones so the engine sees them in order (`[DRIVER_KEEPS_WIRE_ORDER_V1]`).
- `core/homa_sim.py`: Homa's own packets (DATA, GRANT, RESEND, BUSY, ACK), loss and recovery, in user space.
- `core/homa_liveness.py`: each end writes a capability tuple `(homa, can, <ip>:<role>)` in the ControlRam and holds
  a read on the family; a file watcher on the hosts file says when a machine left (`[HOSTS_IS_WATCHED_V1]`).
- Carriers per hop (`[CARRIER_PER_HOP_V1]`, `core/homa_transport.py`): each end says what it can open; a daemon
  listens on every carrier it has; a proxy opens each session on a carrier both have, lowest measured round trip
  wins. Words: `auto`, `tcp`, `homa`, `softhoma`, `homasim`.
- The seam in existing code is two places, 24 lines: `daemon/daemon_main.py` (which listener) and
  `proxy/transport_semantic.py` `_get_worker` (which worker). Nothing else of the TCP driver was changed.

### 16 Nov night to 17 Nov 01:00: soft Homa (`67362f12`, `51a6b684`, `fb3c7009`)

- `ribbit/examples/homa/src/softhoma.cpp`: Homa's socket calls (`socket`, `bind`, `setsockopt`, `sendmsg`,
  `recvmsg`, `ioctl`, `shutdown`, `close`) answered on a `ribbit::Space`. A request and a reply are cells; a socket
  has one standing read on its own name; a server has a capability cell `(homa, can, <ip>:<port>)`; liveness is that
  cell leaving. For a program that cannot be changed (his `cp_node`) it is put under the program with `LD_PRELOAD`.
- Second stage, "made safe": slots, nonce names, a closed marker, the `there` / `not_there` sets.
- Where large payloads spend their time was MEASURED after an earlier diagnosis of mine (row differencing; sending
  bytes past the engine) proved wrong. With callgrind and strace: 72% of sender CPU at 240 KB was the platform's
  `cellwire.hpp field_order()` classifying every string leaf; and a 32 KB `SO_SNDBUF` against loopback's 65,483-byte
  MSS with delayed ACK cost 44 ms per 64 KB on one machine.

### 17 Nov 06:30: the one platform change (`cb4423c9`, `76482166`)

- `ribbit/cpp/include/cellwire.hpp`: `field_order()` computes the order from the bag's shape without classifying its
  leaves (`[FIELD_ORDER_WITHOUT_LEAF_TYPES_V1]`). John's "Continue" was taken as the go-ahead. It is its own commit so
  it can be reverted alone. Verified equal to the old order on 300,000 generated bags (`tools/field_order_check.cpp`,
  run by the gate). **The MQTT, Redis and FD&F gates have not been run against it.**

### 17 Nov 07:00 to 08:50: soft Homa on real machines, against TCP (`a0c91529` to `f10c5cf6`)

- `tools/try.sh`: his `cp_node` across machines without writing `/etc/hosts` (his tools look servers up as `node1`,
  `node2`; the interface answers `getaddrinfo` for those names from `SOFTHOMA_NODE<N>`).
- **His tools are written for x86.** On the Pi 5: `rdtsc` (given a clock, in the tarball's copy of his source), the
  crash handler's `uc_mcontext.gregs[REG_RIP]`, and a real one: with no `--gbps` his client computes a rate of 0, an
  interval of infinity, and adds it to a `uint64_t` (`next_start += interval_dist(rand_gen)*cycles_per_second`). That
  conversion is undefined. x86 gives 0, so it sends at once; ARM gives the largest number, so it sends ONE request
  and waits forever. Off x86 `try.sh` passes his own `--gbps 1000000`; his source is not changed for it.
- A request to a server that is not there waited 60 s; now it ends at once and says so (`1a3fc17a`). His server
  spins on a failed `recvmsg` when the memory ends and wrote a 30 GB log in the sandbox; hence
  `SOFTHOMA_EXIT_WHEN_MEMORY_GONE` (`8b748038`).
- The machines: Seattle5 (10.250.250.1, memory and servers), Seattle3B (10.251.251.1, near client), New-York-1
  (10.123.123.1, far client). Raspberry Pi 5, kernel 6.12.87. The numbers are in section 6 and in
  `REPORT-soft-homa-against-tcp-20261117.md`, which stands alone.
- Kernel Homa for the Pi: his `linux_6.13.9` branch cross-compiles for arm64 against rpi-6.12.y with a forced
  include giving `cpu_khz` / `tsc_khz` from `arch_timer_get_cntfrq()`. Compiled only: not linked against real
  symbols, not loaded, not run. Loading a module on a node needs John's say-so, which has not been given.

### 17 Nov 08:50 to 09:15: the transport is not transparent; FrogTorch's tensors (`7079d01a`)

- Asked to run PyTorch and Psychedelic FrogTorch across the nodes with the driver in place, an inventory of every
  place that dials for itself was made from the code (the table is in `DESIGN.md`, "The transport is not yet
  transparent, so the job is not done"). Rulings 19 to 25 came out of it.
- **What the FrogTorch code does today, and why it is wrong.** `agent_workload/tuplespace/frogtorch.py:116` gives
  each rank a `MemoryPlane`, which binds and listens. A reader takes the owner's `{ip, port}` from its member cell
  and connects to it (`psychedelic_backend.py:1670`, `ddp_hook.py:176`, `TensorClient._link`). That was committed on
  12 Nov (`5b79b445`) as a reading of "You do not copy bits from one machine to another. You leave them where they
  were and use references." It was a misreading. The design John means is the one of 11 Nov
  (`[ALL_IN_THE_REGION_V1]`, `frogtorch_ram.py`): a rank writes its tensor cells into the RAM host and readers hold
  reads there; no rank listens; the cell outlives its writer. `5b79b445` also deleted the placement of regions by
  capability tuple (188 lines of `frogtorch_ram_client.py`), which ruling 25 wants back.
- **Not yet changed.** The work to do: revive the 11 Nov code (start from the code that worked), put the rank's
  session with the RAM host on the FNWP wire in place of its private JSON socket, and then run both Torches with the
  carrier swapped underneath. Acceptance: the collectives and DDP hashes (`df67ff9ce033941b`, held-out 0.4940).
- **One arm has no design.** Stock PyTorch's gloo opens its own TCP connections rank to rank. Under ruling 20 it
  cannot ride on Homa as it is, and a socket interposer was proposed and rejected (section 4). Until ruled on, stock
  PyTorch runs only as the TCP control.

### 17 Nov 09:15 to 10:15: what his code says (for John's note to Homa's author)

Checked in his repository, because each was about to be stated to him:

- **TCP hijacking is real and is used.** `homa_hijack.h`: "Homa packets are encapsulated as TCP frames and
  transmitted with an IP protocol of IPPROTO_TCP"; the socket's `sk_protocol` is set to `IPPROTO_TCP` when
  `hijack_tcp` is set; the packets carry flag bits real TCP never uses (SYN and RST together). The reason given is
  NIC segmentation (TSO): most cards only segment TCP. It is off in the code until set, and it is set by his cluster
  script for Intel `ice` cards (`cloudlab/bin/config`), recommended in `INSTALL.md`, and present in his best
  measured settings for a 100 Gb cluster (`perf.txt` item 57, `hijack_tcp=1`). He warns that some datacenter
  switches drop TCP packets that belong to no connection, and then it does not work.
- **Two builds.** `Makefile` compiles `homa_hijack.o`, `homa_grant.o`, `homa_offload.o`, `homa_qdisc.o` and
  `homa_metrics.o` into his own module. The version submitted to the Linux kernel (`Makefile.upstream`, the
  `net-next` target, `__STRIP__`) leaves all five out, and his `Kconfig` says of it: "this version provides
  bare-bones functionality but is not performant". So the upstream Homa has no grants and no hijacking; the fast
  Homa is the out-of-tree module.
- **Homa has an IANA protocol number**, 146, since October 2024 (his README).
- **Soft Homa does not disguise anything.** Its bytes travel in ordinary TCP connections between each program and
  the RAM host. What is Homa is the INTERFACE, not the protocol on the wire: there are no Homa packets. A sentence
  true of both: "Like your module with TCP hijacking on, it rides in TCP on the wire."
- **Size**, code lines with blanks and comments removed (`/home/claude/scripts/loc.py`, now `tools/sandbox/loc.py`):

  | | Files | Code lines | Characters |
  |---|---|---|---|
  | His kernel module (`homa*.c`, `homa*.h`, `timetrace.*`) | 34 | 12,111 | 322,733 |
  | His unit tests (`test/`) | 30 | 22,941 | 711,506 |
  | Soft Homa (`softhoma.cpp`, `softhoma_ram.cpp`), at `8b748038` | 2 | 380 | 23,522 |
  | The FrogNet driver (five Python files), same commit | 5 | 1,092 | 46,466 |
  | The simulator (`homa_sim.py`) | 1 | 405 | 14,746 |
  | The driver's oracles | 6 | 1,121 | 57,260 |
  | `gate.sh`, `try.sh` | 2 | 234 | 18,033 |

  32 times fewer lines than his module; 14 times by characters, because these lines are longer. 14 is the figure to
  quote. It is not like for like: soft Homa stands on the Ribbit library, which is not counted, and has no packet
  handling or retransmission of its own.
- The text of John's note itself is his correspondence and is not kept in this repository.

### 17 Nov 09:20 to 10:20: the driver, large frames and priority (`e7037514`, `ef1947a2`, `f3bcb3a9`)

- **Pieces** (`[FRAMES_GO_IN_PIECES_V1]`, ruling 26). The driver refused a frame over 1,000,000 bytes and the daemon
  answered an oversized reply with ERROR 413. Now it asks the carrier what one message may be
  (`homa_socket.max_message()`: 1,000,000 on kernel Homa; soft Homa's record, 240,000) and cuts anything larger into
  numbered pieces. The split at 240,000 ALREADY existed inside the Homa API interface (`RECORD`) and was the slow
  path: a 1,000,000-byte message is five records and took 649 ms, about 130 ms a record, against 6 ms for one
  240,000-byte message. The cost was not the size or the splitting: a record of a multi-record message goes in a
  cell named per record and is not sent as a difference. With the driver cutting, each piece is a whole message.
- **Spares** (`[THE_RPCS_ARE_ALREADY_OPEN_V1]`, ruling 27). Homa lets the daemon speak only in answer to a request
  the proxy has open, so a reply of m pieces needs m open requests. First version: the proxy learned the count from
  the first piece and then opened the rest, one extra round trip per large reply (about 92 ms to New York). John's
  fix: the proxy keeps spare requests open at the daemon, each a held read, and replaces them as they are used. A
  reply piece carries `sent`, the flag; under `count` means the spares ran out, the proxy asks for the rest once and
  keeps that many more open. The number kept open is what the session has needed: no guess, no cap.
- **The wire manager** (`[THE_WIRE_MANAGER_SENDS_V1]`, ruling 29). The thread that had a frame used to send it, and
  all the pieces of a large frame went to the socket at once, so a short frame from the same sender went out behind
  them. Now that thread makes the wire bytes and queues them; one wire manager thread per end is the only caller of
  the carrier's send; two queues, whole messages and pieces; a whole message always goes before a waiting piece.
- A proposal of mine in between was withdrawn: to make the bytes of a message come on a second read, so a long
  message's bytes were held back. John: "The reader does not see the held read until the message is ready. This
  point is moot." A second read would have cost a round trip per message. What is left of Homa's reason for holding
  a long message back is that a short one can be behind at most one piece in transit: about 4 ms within Seattle and
  8 ms to New York, inferred from the tables, not measured. `DESIGN.md` had claimed "the bytes of a large value move
  when it is read", which the code does not do; the document was corrected, not the code (`fb5af1e0`).

### 17 Nov 10:35 to 11:30: independence, and the memory that moves (`54f4c3bb`, `9fe9591d`)

- **Nothing is preloaded into a service** (`[THE_DRIVER_LOADS_ITS_OWN_HOMA_V1]`, ruling 30). The driver opens
  `libsofthoma.so` itself (`FROGNET_SOFTHOMA_LIB`, or the one line of `/etc/frognet/softhoma_library`) and makes
  every call of its socket through that handle; the socket is a bare descriptor. With neither setting the library is
  never loaded.
- **The memory is where the role is** (`[THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1]`, ruling 31). The Homa API interface
  used to read `SOFTHOMA_RAM` and `SOFTHOMA_IP` once and connect once. Now: each socket keeps the memory it was
  opened on; `softhoma_memory(ram, ip)` says where a NEW socket opens and puts the old ones down; the driver asks
  where the memory and the node are when a socket opens and at every hosts change; the proxy opens a new socket when
  it next has something to send; the daemon's listener opens again at once or at the next network event. Every node
  on soft Homa runs the memory process as a child of the daemon's Homa listener; the one on the SystemRam's machine
  is the one used. What the repository says about roles that settled this: `AI_READ_FIRST.md` `[ROLES_ON_API_V1]` (a
  role is a host; the SystemRam is that host's api.php on port 80; a service keeps its own port).
- **A peer can be behind** (`[A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1]`). Each node arrives in the new memory when its
  own hosts file changes. Found by the test: the first request after a move asked for the daemon before it had
  bound, was told it was not there, and three such failures would have marked the peer "not FrogNet" for the session
  (the proxy's existing three-strikes rule, errno 113). Now a peer is in the memory when its capability cell is; on
  `auto` it does not have soft Homa in common until then; told `softhoma` only, the session is not opened and the
  caller is told why at once, with no errno and so no strike.
- Bugs found on the way: `HomaSocket.close()` could close a descriptor twice (the number may belong to something
  else by then); a closing server whose memory had gone tried to write there; the first fix for that used the wrong
  flag (`shutdown` sets `down` before `close` runs) and broke "a closing server says so", caught by the soft gate.

### 17 Nov 11:30 to 12:30: the real simulator (`8ffbddc3`)

- `simulation/run_all.py` with nothing set (the wire on the TCP driver): every tier passed except two. Ribbit-LISP
  fails because lispers.net 0.643 and its Python are not on the sandbox. The discovery suite failed its license
  check, and that was this work's: `tools/gate.sh` and `tools/field_order_check.cpp` had no license header. Fixed.
- Added: `door_semantic_tier.py` can take a real driver as its socket hop (`SOCKET_HOP`, `TARGET_IP`; default
  unchanged); `homa_door_tier.py` runs that tier through the Homa driver on soft Homa (4 scenarios pass, every
  answer equal to the door's); the driver's oracles are listed as tiers; `MsgDaemonServer.stop()`.
- **This is NOT passing** by ruling 32. `homa_door_tier` is one proxy and one daemon in one process with stand-ins
  for liveness and for where the memory is. Passing is the driver activated between machines on the simulator's
  topology and used as the transport for the full battery.
- One full `run_all.py` reported the ordering-oracle tier FAIL. Alone, run the same way, it passed all eight modes
  before and after. The simulator throws a tier's output away, so which mode failed was not seen; the oracle now
  keeps every mode's output (`$TMPDIR/homa_ordering_oracle.last.txt`). The run made to find out was interrupted.
  **Not explained.** Earlier the same morning `daemon/test_writer_gone_oracle.py` (the TCP driver's) failed once in
  four runs with "Connection refused" and passed otherwise, including on the commit before the change. **Also not
  explained.**
- **The role's address is calculated one way** (`[ONE_INPUT_ONE_RULE_V1]`, ruling 33). `soft_memory_now()` had asked
  the system resolver for `databasehost.frognet`, which can answer from DNS or a cache, and in the simulator would
  give every machine the same answer. It now reads the line for that name from the hosts file the election writes
  and the watcher watches (`frognet_held.HOSTS`). No line, or two addresses, is said and no memory is used.

## 4. Mistakes, who caught them, and the rule that came out

| What I did | Caught by | The rule |
|---|---|---|
| Diagnosed large-payload slowness from reading code (row differencing; send bytes past the engine) | measuring it | Profile before saying where time goes. |
| Said the 1,000,000-byte slowness was only a one-machine loopback effect | the Pis: 649 ms near, 2,091 ms far | The multi-record path is a real defect. |
| Said kernel Homa "needs x86" | John | It is kernel-version specific and has an ARM64 path. |
| Python driver called `ctypes.CDLL("libc.so.6")`, going past soft Homa | the soft test | (Superseded: the driver now opens the library itself.) |
| Built FrogTorch so each rank listens and readers connect to it (12 Nov) | John, twice: "why is there machine to machine traffic?", then "completely wrong" | A tensor is a tuple in the RAM host. No rank listens. Nobody asks another node for it. |
| Proposed "pick wires by hand" for the Torch runs, then a socket interposer | John | The transport is transparent or the job is not done. Nothing else talks between nodes. |
| Read "the bits stay where they are" as "in the writing node's own memory" and then as "not in a RAM host" | John | Tuple space IS the RAM host. The bits are there and are referenced there. |
| Called `libsofthoma.so` "the shim", then guessed "the RAM manager" | John | It is the Homa API interface. Ask what a name refers to before renaming. |
| First piece design cost an extra round trip per large reply | John | Have the requests open already; a flag says when they ran out. |
| Proposed moving a message's bytes to a second read | John | The read fires when the whole message is there; the point was moot. |
| Listed grants as something soft Homa lacks | John: "Grants became reads, didn't they?" | The read is the grant (`DESIGN.md` section 3 already said so). |
| Said his TCP hijack was there "but I see no default that turns it on", implying unused | John: "Look at the makefiles" | Read the build and the scripts, not only the source, before saying how code is used. |
| Put soft Homa under the driver with `LD_PRELOAD` in the tests | John: "WHAT???" | Never preload into a proxy or daemon. The driver opens its own library. |
| Planned to put the RAM host's address and the node's address in settings | John: "You know it can change address during a run. Right?" | No address is kept anywhere. Ask at each event. |
| Resolved the role's name through the system resolver | John (ruling 33) | One input, the hosts file; one rule. |
| Presented one proxy and one daemon in one process as "the simulator" | John (ruling 32) | Passing is the driver between machines on the topology, full battery. |
| Two new files without the license header | the simulator's license check | Every source file carries it; run the simulator before saying a change is clean. |
| Spent an hour on a note's wording while saying the Torch work was under way | John: "Where is the torch work" | Say "not started" when it is not started. |

## 5. What Homa's mechanisms became

| In his module | Under CroakOS |
|---|---|
| A connectionless, reliable message | A cell written to memory; a reply finds its request by id |
| GRANT packets, the receiver saying what may come | The read. One standing held read per socket; it fires when a whole message is there |
| SRPT, shortest first, in the kernel | The consumer's choice among what has arrived (`Sock::inbox`, by length); and in the driver, whole messages ahead of waiting pieces |
| The per-RPC timeout, his crash detector | Removed. A held read on the peer's capability cell, and the hosts file watcher |
| RESEND and BUSY every 5 ms for a waiting request | One held read |
| Pacing the NIC queue | Not needed; "sequential messaging" |
| TCP hijacking, to get cards to segment | Not needed: the bytes are in real TCP to the RAM host |
| 1,000,000-byte message limit | The driver cuts a frame into pieces of what the carrier takes |

## 6. Measurements

**His `cp_node`, unchanged, soft Homa against TCP run in the same session.** Median response time, milliseconds.
Raspberry Pi 5s. Ping 1.9 ms within Seattle, 91.6 ms to New York. 17 Nov, Homa API interface v7.

| Message bytes | Seattle-Seattle soft Homa | Seattle-Seattle TCP | Seattle-New York soft Homa | Seattle-New York TCP |
|---|---|---|---|---|
| 100 | 1.87 | 1.97 | 91.9 | 92.0 |
| 1,000 | 1.82 | 1.95 | 91.9 | 92.9 |
| 10,000 | 1.97 | 5.01 | 92.0 | 93.9 |
| 100,000 | 3.58 | 9.13 | 95.9 | 128.1 |
| 240,000 | 6.03 | 16.19 | 100.0 | 245.6 |
| 1,000,000 | 649 | 62 | 2,091 | 852 |

- His payload is constant, so the engine sends only what changed. That is why 240,000 bytes is 2.7 times faster.
  **A payload that changes has not been measured.** A tensor is that case; in September the tensor plane got no
  benefit from differences (`same_replies: 0`).
- **Two predictions I made were wrong**: at 100 bytes TCP was predicted ahead (equal); at 240,000 bytes TCP was
  predicted several times ahead (soft Homa 2.7 times faster). Both are recorded in the report.
- Soft Homa's P99.9 on the far link is twice the round trip. Probably the first request's capability lookup;
  unconfirmed. Two clients at once gave 8 ms; a scheduling hypothesis, unconfirmed.

**The driver's tests, in the sandbox** (one machine):

| What | Result |
|---|---|
| Ordering oracle, 8 modes (free, ordered, pieces, gone, sim, simheld, soft, softmoved) | 167 checkpoints pass (`9fe9591d`) |
| Pieces mode, carrier limited to 64-byte messages | 3,184 pieces; 146 reply pieces on spares, 35 asked for once each; 39 whole messages sent ahead of waiting pieces |
| Memory moved with 6 requests outstanding | all 6 ended in 2 ms; a request to a daemon behind the move refused in 5 ms; next answered in 4 ms |
| Operations a second in the oracle before and after the wire manager, one run each | ordered 517/475, pieces 467/477, sim 294/340, soft 348/458 (noise) |
| Soft Homa's own gate (his `server`, `homa_test`, `cp_node`) | 18 checks pass |
| Simulator `run_all.py`, wire on TCP | all pass except Ribbit-LISP (prerequisites absent) |
| `homa_door_tier` | 4 scenarios pass on soft Homa, 0 TCP workers |

## 7. Not done, not run, not explained

Nothing here may be reported as working.

1. **The simulator, by ruling 32.** The driver activated between machines on the topology, the full battery over it.
2. **Any of the driver on a node.** Pieces, spares, the wire manager, the library opened by the driver, the memory
   following the role, `softhoma-ram` started by the daemon: none has run outside the sandbox. Only his `cp_node`
   on soft Homa has run on nodes.
3. **FrogTorch.** Still as built on 12 Nov (ranks listen). Not reverted to the 11 Nov design; its RAM host session
   is not on the FNWP wire. No Torch run has been made on Homa.
4. **Stock PyTorch on Homa.** No design under ruling 20.
5. **A payload that changes**, at any size, on soft Homa or through the driver.
6. **The 1,000,000-byte row.** `cp_node` calls the socket itself, so the driver's pieces do not help it. The fix is
   inside the Homa API interface: make a record of a large message take the path a whole message takes. Not done.
7. **The platform patch** (`cellwire.hpp`) against the MQTT, Redis and FD&F gates. The platform's `qualify.sh` fails
   in the sandbox without the patch too (C++ `semtpl` canon_key behind Python since `befe497c`, 1 Nov).
8. **Kernel Homa on the Pi.** Compiles; never loaded. Needs John's go-ahead and `linux-headers` on the node. One
   process holding both kernel Homa and soft Homa is not built.
9. **Two unexplained test failures** (section 3, 11:30): the ordering tier once inside `run_all.py`; the TCP
   driver's writer-gone oracle once in four.
10. **The old memory's session.** The platform's `Space` has no close, so after a move the session with the old
    memory ends only with the process, and this process's cells stay in the old memory until then.
11. **A node tarball for the driver.** Not built. The install changes two existing files; it must compare them with
    the version the branch was cut from and stop if they differ.
12. Memory on a third node; several clients; IPv6; a RAM-coordinated test harness (participants advertise, a
    controller tuple, held reads).

## 8. Where things are

- Design: `DESIGN.md` (this directory). Report on the cp_node runs: `REPORT-soft-homa-against-tcp-20261117.md`.
- Rules for AIs: `AI_READ_FIRST.md` (root), `core/AI_README.md`, `proxy/AI_README.md`, `daemon/AI_README.md`.
- The driver: `core/homa_socket.py`, `core/homa_transport.py`, `core/homa_liveness.py`,
  `proxy/transport_homa.py`, `daemon/engine/server_homa.py`. The simulator of Homa's packets: `core/homa_sim.py`.
- Soft Homa: `src/softhoma.cpp` (the Homa API interface), `src/softhoma_ram.cpp` (its RAM host).
- Tests: `proxy/test_homa_ordering_oracle.py` (modes above), `core/test_homa_*_oracle.py`, `tools/gate.sh`,
  `simulation/homa_door_tier.py`; listed in `simulation/run_all.py` under `[HOMA_DRIVER_TIERS_V1]`.
- The scripts used in the sandbox to run all of this are kept as they were in `tools/sandbox/`, with their sandbox
  paths, so the process can be repeated or read.
- Markers to search for: `[HOMA_TRANSPORT_V1]`, `[CARRIER_PER_HOP_V1]`, `[SOFT_HOMA_V1]`, `[SOFT_HOMA_CARRIER_V1]`,
  `[SOFT_HOMA_FAST_V1]`, `[FIELD_ORDER_WITHOUT_LEAF_TYPES_V1]`, `[FRAMES_GO_IN_PIECES_V1]`,
  `[THE_RPCS_ARE_ALREADY_OPEN_V1]`, `[THE_WIRE_MANAGER_SENDS_V1]`, `[THE_DRIVER_LOADS_ITS_OWN_HOMA_V1]`,
  `[THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1]`, `[A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1]`, `[ONE_INPUT_ONE_RULE_V1]`,
  `[HOMA_DRIVER_TIERS_V1]`, `[THE_SOCKET_HOP_CAN_BE_A_REAL_DRIVER_V1]`.

## 9. Commits

| Commit | When | What |
|---|---|---|
| `8c67920f` | 16 Nov 22:11 | Driver, simulator, liveness (build 20261116j) |
| `1fb77740` | 16 Nov 23:00 | `core/sem_transport.py` renamed `core/homa_transport.py` |
| `4af78f82` | 16 Nov 23:12 | Liveness: capability tuples live in the ControlRam |
| `29f2c988` | 16 Nov 23:16 | `AI_READ_FIRST`: which RAM a tuple belongs in |
| `37706dc2` | 16 Nov 23:25 | `DESIGN.md` written |
| `d2645d32` | 16 Nov 23:31 | The carrier is chosen per hop |
| `67362f12` | 16 Nov 23:59 | Soft Homa, first stage |
| `614383dd` | 17 Nov 00:01 | Liveness oracle on the real memory |
| `51a6b684` | 17 Nov 00:36 | Soft Homa, second stage: made safe |
| `fb3c7009` | 17 Nov 00:59 | Where large payloads spend their time, measured |
| `cb4423c9` | 17 Nov 06:32 | Platform: `cellwire.hpp` field order (revertable alone) |
| `76482166` | 17 Nov 06:32 | The gate checks the field order |
| `b2cbafe8` | 17 Nov 06:57 | Soft Homa is a carrier of the semantic wire |
| `a0c91529` to `765fb1b9` | 17 Nov 07:04 to 07:30 | `try.sh`; his tools on ARM |
| `1a3fc17a` | 17 Nov 07:45 | A request to a server that is not there ends at once |
| `8b748038` to `94349915` | 17 Nov 07:59 to 08:28 | Two-machine figures, the TCP control, `compare`, kernel compile note |
| `e1d1fe4a` to `f10c5cf6` | 17 Nov 08:31 to 08:47 | New York; the report |
| `7079d01a` | 17 Nov 08:50 | The transparency inventory |
| `3c86f731`, `e022a9ac` | 17 Nov 09:20, 09:23 | Names: the Homa API interface |
| `e7037514` | 17 Nov 09:47 | A frame larger than one message goes in pieces |
| `ef1947a2` | 17 Nov 10:04 | A large reply goes on requests the proxy already has open |
| `fb5af1e0` | 17 Nov 10:10 | "The read is the grant" says what the code does |
| `f3bcb3a9` | 17 Nov 10:20 | The wire manager sends |
| `54f4c3bb` | 17 Nov 10:51 | The driver opens soft Homa itself; nothing preloaded |
| `9fe9591d` | 17 Nov 11:28 | Soft Homa's memory is where the role is, and the driver follows it |
| `8ffbddc3` | 17 Nov 12:28 | The role's address by one rule; simulator tiers; license headers |
