# PyTorch on the transport drivers: where it stands and what to do next

Written 18 November 2026, rewritten 19 November 2026 after steps 1 and 2 were built, for whoever picks this up, a
person or an AI thread. It stands on its own. Read it, then `core/TRANSPORT-DRIVER-INTERFACE.md`, then section 0 of
`ribbit/examples/homa/DESIGN.md`, then the entries of 18 and 19 November in
`ribbit/examples/homa/EXPLORATION-LOG.md`. Branch `homa-under-croakos-20261116`; never touch `main`.

## 1. What John asked for

17 November: run stock PyTorch and Psychedelic FrogTorch across the nodes with the transport swapped underneath and
nothing above it changed ("The transport should be transparent. If it is not, the job is not done"). 18 November:
"Do pytorch". His letter to John Ousterhout of the same day says "we are getting PyTorch numbers shortly" and asks
whether he wants them.

His measure (12 November): whole-run time, not the time of a step. His rule (12 and 17 November): start from the
code in the tree that worked and was measured; do not roll it back and do not rewrite it from nothing.

## 2. What the transport is

A driver is six calls (`core/transport_driver.py`): connect, send, receive, pack, unpack, close, and listen on the
end that is connected to. "We own the wire": a permanently open connection of the driver's choosing; the data is
data; packing and unpacking are the driver's. Three drivers: `tcp`; `softhoma` (Homa's DATA frames on a TCP
connection the driver holds open, `core/homa_frames.py`); `homa` (the kernel module). The word is a setting per RAM
connection in `/etc/frognet/semantic_transport`.

## 3. What FrogTorch puts on the network now (read from the code, and from the kernel, on 19 November)

| traffic | code | how it travels | does the transport setting reach it |
|---|---|---|---|
| a rank's session with the FrogTorch RAM (membership, store, gate, bags: control cells) | `tuplespace/frogtorch_ram_client._Session`, `tuplespace/frogtorch_ram.RamServer` | a transport driver's connection from the rank to the RAM (`tuplespace/frogtorch_ram_drivers.py`) | YES: `tcp` or `softhoma` |
| tensor bytes: DDP gradients under Psychedelic, the hooks' buckets, meshfeed's minibatches | `tuplespace/ram_plane.py` (`RamPlane`, `RamStream`, `RamTensorClient`) | cells in the FrogTorch RAM: the owner writes each once on its session, a reader's held read is answered by that write; each session is a driver's connection | YES: `tcp` or `softhoma` |
| stock DDP on gloo | PyTorch's own | gloo's own sockets from rank to rank | no, and it should not: it is the control |
| anything a rank asks of `api.php` | the node's proxy | the proxy-to-daemon semantic wire | yes |

No rank listens, and a rank has no connection to another rank. That is checked from the kernel's list of each rank's
sockets, on three real ranks, on each driver (`simulation/collective_tier.py`, plane 4b); on the tree before
19 November the same check shows every rank listening and connected to every other.

Both of John's rulings of 17 November that the tree did not meet are met for PyTorch:
- "The tensor should not be listening at all -- it should be using held reads, and when another node needs a tensor
  it does not ask the other node for it. It gets it from tuple space"; "the bits are in tuple space ... Tuple space
  IS the RAM host".
- "There should not be anything else talking between the nodes"; a connection to the RAM host "is expected and should
  be transport transparent".

How it was done, so it is not redone:
- **The session** (`[THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1]`). `_Session` and `RamServer` call the six calls
  and open no socket. `tcp` puts on the wire exactly the bytes that session always sent (`<!Q length><payload>`;
  compared byte for byte in the tier), so numbers measured before stand as the control. `softhoma` is
  `core/homa_frames._Conn`. A RAM has one port P (the one it is started with and announces): tcp listens on P,
  softhoma on P + 1; the RAM listens with every driver a word of its own node's setting names. `auto` is tcp here.
- **The tensors** (`[THE_TENSORS_ARE_IN_THE_RAM_V1]`). The three classes in `ram_plane.py` have the calls
  `TensorServer`, `StreamSubscription` and `TensorClient` had, so `psychedelic_backend.py`, `ddp_hook.py` and
  `meshfeed.py` changed only in which class they make. The reduction, its order, the cell names, the generations and
  the read-ahead are untouched. In the RAM: `tsub` (a read held on a family of names; every cell that matches
  answers it as its owner writes it), `twant` (one named cell, once), `tdel`, and `readers=k` on a write.
- **A rank's cells** are named `<host>:<number>/<name>` (the host and number are what the rank writes in its
  rendezvous cell; the number is its process id, since no rank has a port), are held by its session, and go when
  its process goes, as they did when they were in the process.
- **A wait is one held read** (`[A_WAIT_IS_ONE_HELD_READ_V1]`): the September backends' waits (collectives'
  gathers, broadcast, scatter, recv, the hook's linger) no longer sleep, re-arm on a cap or ask again.
- **One thing found on the way** (`[A_READ_IS_HELD_UNTIL_THE_WRITE_V1]`): a cell and the descriptor that names it
  reach the RAM on different sessions, so a reader can have the descriptor before the bytes have landed. DDP's
  first broadcast failed on it ("r0 does not publish 'bc.3' at all"). A named read is now held for the write.

## 4. The work, in order (make it work, make it safe, make it fast)

1. **DONE 19 Nov. The rank's session with the FrogTorch RAM is a driver's connection.**
2. **DONE 19 Nov, in the sandbox. PyTorch's tensors are in the RAM and are read by held read.** What was run, on
   `tcp` and on `softhoma`, four ranks on one machine:
   - `agent_workload/tests/test_regions_collectives.py`: every collective passes.
   - `agent_workload/tests/test_regions_ddp.py`: shard `1299476b225f2d9a`, faithful `8293546b6b4e8c48`, held-out
     0.487819 on every rank: the same as the tree before the work.
   - The standard workload (`ddpbench.py`, 4 ranks, hidden 1024, 40 steps, warmup 8, shard, bucket-view 0):
     held-out 0.4940 and one `param_sha` on every rank, `6d84cfed6441a54f`, the same as the tree before the work
     gives on that machine (gloo `3541e1807be7f016`, faithful `d1d97066cbba6e15`, also unchanged). The sandbox's
     torch is 2.15 on x86-64; **on John's nodes the acceptance is still `df67ff9ce033941b` and 0.4940, and that
     has not been run.**
   - `meshfeed.py`, 3 nodes, k=1: `e815acd8ca31c698` before and after, and equal to its oracle.
   - `simulation/collective_tier.py`: all 99 pass.
   - `simulation/test_aiconnect_ramp_oracle.py`: passes on tcp.
3. **NEXT, on the nodes: measure, one session, the control first.** Install (section 5), then on every node:

       sh /usr/local/frognet-ai/transportrun.sh <this node's mesh address> <a run id number never used before>

   It runs the tier on the node, prints what the one FrogTorch RAM is and what it listens with, then seven arms in
   step across the nodes: gloo; Psychedelic shard on tcp; Psychedelic shard on softhoma; meshfeed k=1 batch 64 on
   each; meshfeed k=1 batch 4096 on each. Each rank writes its result of each arm into the RAM and every node
   prints the same report from it: whole-run seconds, held-out loss and `param_sha` as three matrices (ranks down,
   arms across, as John specified on 15 September), then the arms with their ratio against that session's arm 0
   only. Its node set is `meshrun.sh`'s five; `FROGNET_RUN_NODES` names another.
   Performance is not judged until the shard arms give `df67ff9ce033941b` and 0.4940 there.
4. **Then kernel Homa** where the module is loaded (the Pi), same arms. Not written: a session told `homa` says so
   and stops. Two things to know before writing it: Homa is request and reply, and a held read on a family is
   answered many times, so the RAM's end needs the open-RPC machinery the proxy's Homa driver has
   (`[THE_RPCS_ARE_ALREADY_OPEN_V1]`); and a tensor write is two frames (the request, then the bytes) that the
   RAM takes in order, which a stream gives and Homa's messages do not, so on kernel Homa they must be one frame.

## 5. Installing it on nodes, and things that will bite

- **Every node gets the same build, and `frogtorch-ram` is restarted on every node after it.** Ranks of different
  builds refuse to form a group and say which rank runs what; a RAM of the old build does not know `tsub`.
- **For the softhoma arms the RAM must be listening with soft Homa**: `/etc/frognet/semantic_transport` on its node
  has the line `databasehost.frognet softhoma` (every node has the same file, since the role moves), and
  `frogtorch-ram` was started after that. `transportrun.sh` gives each arm's word to that arm's processes alone and
  changes nothing on the node; it prints what the RAM listens with before the first arm, and a session that finds
  nothing listening says which driver it was on and why that happens.
- **Soft Homa's port for the FrogTorch RAM is the RAM's port plus one.** A link that filters by port must let it
  through. (`usr/local/bin/ham_guardrail.sh` lets only 9009 through, so neither FrogTorch port crosses a ham link.)
- `ddpbench.py` runs from `/usr/local/frognet-ai`, not from this directory. Editing a copy elsewhere changes nothing.
- The FrogTorch RAM is one process on the SystemRam's machine; the role moves. No address goes in a setting.
- Moving the bytes through the RAM makes each tensor cross two links (owner to RAM, RAM to reader) where the
  September path crossed one. That is what John ruled; measure it, do not design around it. Where a cell's time
  went is in each rank's result file: the `link` block's `r<peer>|held` entries (`owner_to_ram_ms`, `in_ram_ms`,
  `ram_to_reader_ms`, per cell, each on one machine's clock) and the `plane` block's `write_ms_mean`.
- The September path's wire work between ranks (LZ4 when it shrinks, block DIFF/SAME per instance, records sized to
  the measured rate, channel sets) was on the rank-to-rank link and is not on the path through the RAM. Nothing has
  been put in its place: that is the "fast" stage, after the numbers say where the time is.
- A session ends with its connection, at both ends (`[A_SESSION_ENDS_WITH_ITS_CONNECTION_V1]`). A rank's cells go
  with its session.
- No timers, no fallbacks, no machine talking to anything but the RAM host. The session's old socket timeout is
  gone: a held read returns at the deadline the request names, or when its connection ends.
- The first Psychedelic run after `frognet_work.cpp` or the tree's path changes rebuilds the native shim inside
  `init_process_group` and says so ("native Work shim BUILT in ..."). On a node the path and that file have not
  changed. In a sandbox that runs two trees in turn it adds about 22 s to whichever arm pays it.
- The gate for this code is not in `run_all.py` (the PyTorch tiers were pulled from it on 16 October because the
  platform tree does not carry `agent_workload`). Run `simulation/collective_tier.py`, the two tests in
  `agent_workload/tests/`, and `simulation/test_aiconnect_ramp_oracle.py`. They need torch and lz4.
- John commits nothing to `main`; work goes on the branch as it is done, and the exploration log records each of
  his interventions, what was said that made him step in, and every rollback.

## 6. Not done. Nothing here may be reported as working

1. (Closed 19 Nov.) Nine wait loops in the September backends slept between re-asks. John: "Why is a held read
   causing the tests to fail? There should not need to be anything else". Each is one held read now
   (`[A_WAIT_IS_ONE_HELD_READ_V1]`), `BLOCK_CAP` is gone, and the tier passes.
2. **Nothing has run on a node.** Every number in section 4 is one machine with four ranks.
3. **Kernel Homa** under the RAM session (step 4).
4. (Closed 19 Nov.) `frogtorch.py` and `frogtrain.py` kept their tensors in `memory_plane.MemoryPlane`, a process
   listening. They make `RamPlane` and `RamTensorClient` now and `memory_plane.py` is deleted.
5. (Closed 19 Nov.) AIConnect's producer opened its own TCP sockets to a RAM's port. Its bulk link and probes are
   the transport driver's connection now (`[AICONNECT_ON_THE_DRIVERS_V1]`); `--link-timeout` and the TTL hop probe
   are gone with the sockets. Its oracle passes on `tcp` and on `softhoma`.
6. **The path through the RAM has had no performance work** (section 5).
