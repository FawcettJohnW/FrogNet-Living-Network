# PyTorch on the transport drivers: where it stands and what to do next

Written 18 November 2026 for whoever picks this up, a person or an AI thread. It stands on its own. Read it, then
`core/TRANSPORT-DRIVER-INTERFACE.md`, then section 0 of `ribbit/examples/homa/DESIGN.md`, then the entries of
18 November in `ribbit/examples/homa/EXPLORATION-LOG.md`. Branch `homa-under-croakos-20261116`; never touch `main`.

## 1. What John asked for

17 November: run stock PyTorch and Psychedelic FrogTorch across the nodes with the transport swapped underneath and
nothing above it changed ("The transport should be transparent. If it is not, the job is not done"). 18 November:
"Do pytorch". His letter to John Ousterhout of the same day says "we are getting PyTorch numbers shortly" and asks
whether he wants them.

His measure (12 November): whole-run time, not the time of a step. His rule (12 and 17 November): start from the
code in the tree that worked and was measured; do not roll it back and do not rewrite it from nothing.

## 2. What the transport is now

A driver is six calls (`core/transport_driver.py`): connect, send, receive, pack, unpack, close, and listen on the
end that is connected to. "We own the wire": a permanently open connection of the driver's choosing; the data is
data; packing and unpacking are the driver's. Three drivers: `tcp`; `softhoma` (Homa's DATA frames on a TCP
connection the driver holds open, `core/homa_frames.py`, port 9131); `homa` (the kernel module). The word is a
setting per RAM connection in `/etc/frognet/semantic_transport`. Passing on 20 simulator topologies on perfect and
shaped links, and measured level with TCP between Seattle5 and BAMacBook on 18 November.

## 3. What FrogTorch puts on the network today, read from the code on 18 November

| traffic | code | how it travels | does the transport setting reach it |
|---|---|---|---|
| a rank's session with the FrogTorch RAM (membership, store, gate, bags, references: control cells) | `tuplespace/frogtorch_ram_client._Session`, `tuplespace/frogtorch_ram.RamServer` | its OWN TCP connection from the rank to `databasehost.frognet`, framed `<!Q length><payload>` | NO |
| tensor bytes: DDP gradients under Psychedelic, meshfeed's minibatches | `tuplespace/tensor_plane.py` (2,784 lines; `TensorServer` listens, readers `create_connection`) | direct sockets from rank to rank | NO |
| stock DDP on gloo | PyTorch's own | gloo's own sockets from rank to rank | no, and it should not: it is the control |
| anything a rank asks of `api.php` | the node's proxy | the proxy-to-daemon semantic wire | yes |

So if the setting is switched today and the benchmarks are run, the PyTorch numbers do not change with it, because
neither of FrogTorch's two connections uses a transport driver. Nothing has been measured that way and nothing
should be reported as "PyTorch on Homa" until section 4 is done.

Two of John's rulings are not met by the tree as it stands, and both are part of this work:
- 17 November: a rank must not listen for readers: "The tensor should not be listening at all -- it should be using
  held reads, and when another node needs a tensor it does not ask the other node for it. It gets it from tuple
  space"; "the bits are in tuple space ... Tuple space IS the RAM host".
- 17 November: "There should not be anything else talking between the nodes"; a connection to the RAM host "is
  expected and should be transport transparent".

The FrogTorch RAM already has what the first ruling needs: tensor cells written by their owner (`tput`) and read
with a held read (`tget`), used today for AIConnect's objects and not for PyTorch's tensors.

## 4. The work, in order (make it work, make it safe, make it fast)

1. **The rank's session with the FrogTorch RAM becomes a driver's connection.** `_Session` and `RamServer` stop
   opening sockets themselves and use the six calls. The `tcp` word keeps today's bytes on the wire (so the existing
   numbers stand as the control); `softhoma` is `core/homa_frames._Conn`. The word comes from the same per-RAM
   setting. Check: `agent_workload/tests/`, `simulation/collective_tier.py` (both need torch on the machine; the
   sandbox this was written in did not have it).
2. **PyTorch's tensors go into the RAM and are read from it by held read**, through that same session: the owner
   writes its cell once, a reader's held read on the family is answered by that write, and no rank listens.
   Start from `psychedelic_backend.py` as it is; change where a tensor is published and read, not the reduction.
   Acceptance is the existing one: every rank's `param_sha` agrees, shard still gives `df67ff9ce033941b` and
   held-out 0.4940 on the standard workload. Performance is not judged until that passes.
3. **Measure on the nodes, one session, the control first** (`usr/local/frognet-ai/`: `ddpbench.py`, `meshrun.sh`,
   `hookrun.sh`): gloo; Psychedelic shard with the setting absent (TCP); Psychedelic shard with `databasehost.frognet
   softhoma`; meshfeed k=1 batch 64 and batch 4096 on each. Report whole-run time, held-out loss and `param_sha` per
   rank, the three matrices John specified on 15 September (ranks down, variants across), ratios against that
   session's control only.
4. **Then kernel Homa** where the module is loaded (the Pi), same arms.

## 5. Things that will bite

- `ddpbench.py` runs from `/usr/local/frognet-ai`, not from this directory. Editing a copy elsewhere changes nothing.
- The FrogTorch RAM is one process on the SystemRam's machine; the role moves. No address goes in a setting.
- Moving the bytes through the RAM makes each tensor cross two links (owner to RAM, RAM to reader) where the
  September path crossed one. That is what John ruled; measure it, do not design around it.
- A session ends with its connection, at both ends (`[A_SESSION_ENDS_WITH_ITS_CONNECTION_V1]`).
- No timers, no fallbacks, no machine talking to anything but the RAM host.
- Soft Homa's port 9131 is not let through `usr/local/bin/ham_guardrail.sh`.
- John commits nothing to `main`; work goes on the branch as it is done, and the exploration log records each of
  his interventions, what was said that made him step in, and every rollback.
