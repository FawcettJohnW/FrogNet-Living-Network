# AI_README — the client half of the semantic path

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model (the holistic invariant, the checksum,
and what to do before reporting a defect); this file lists only the hazards in this directory.

## What is here

The proxy: intercepts HTTP, speaks FNWP-1 to the daemon, holds the caches.

| | |
|---|---|
| `proxy_main.py` | `ThreadingMixIn` HTTP server; thread per request |
| `transport_semantic.py` | the FNWP-1 socket to the daemon |
| `decision.py` | when to go semantic — EWMA RTT with hysteresis |
| `channel_sets.py` | up to 3 protocol sets per peer |
| `cache/` | `semcache_db.py` — templates, references, response bodies |

## Traps

- **Never probe `:9009` from anywhere but the proxy.** The daemon has one caller.
  This is stated in `decision.py` and it is a hard rule.
- **Three storage layers, three different retention rules.** Templates age out on
  disuse. References are corrected by `REQ_MISS`, not expiry. Response bodies
  MUST be evicted, least-recently-*read* — the clock advances on a read hit, not
  a write, because on a slow link the hot rows are the ones worth keeping.
- **The hot path takes no subprocess.** The one `subprocess` call in
  `proxy_main.py` is a startup alias hook. Keep it that way.
- **Two sockets, not one, and the proxy opens both** (`[RETURN_ONLY_V1]`). The proxy dials `daemon:9009` for
  requests and dials `daemon:9009` again for the RETURN channel; the daemon never dials back and 9010 is gone
  (`transport_semantic.py`, `_open_send_sock` and `_open_return_channel`). Replies do not arrive on the socket the
  request left by, and each reply is preceded on the wire by a 4-byte sequence number that both ends count in stream
  order. That number is the TCP transport's, not FNW1's.

## The semantic wire on Homa (`[HOMA_TRANSPORT_V1]`, 2026-11-16)

`core/sem_transport.py` says which transport the FNW1 wire runs on: `tcp` (the default, everything above) or `homa`,
one word in `/etc/frognet/semantic_transport`. It is configured, never probed, and there is no fallback from one to
the other. With `homa`, `_get_worker` hands the engine `transport_homa._MsgWorker` instead of `_DaemonWorker`.

**The transport is the transport.** Homa owns the wire and how it works. `transport_homa.py` (with
`daemon/engine/server_homa.py`) is a driver: it gives the engine the bus the engine was written against and the
engine does not change. The bus contract is written at the top of `transport_homa.py`: whole frames; in order each
way; many in flight; a reply finds its request; broken is broken.

- **Do not solve Homa's lack of order in the engine.** Homa keeps no order between messages and sends short ones
  ahead of long ones on purpose. The engine moves references in wire order and detects nothing when that order is
  broken (`same_id` names the result, not the base). The answer is not frames that name their bases, a rule about how
  many frames of an instance may be in flight, or any other change to what the engine says. Two such designs were
  drafted on 2026-11-16 and withdrawn. The driver keeps the order: `[DRIVER_KEEPS_WIRE_ORDER_V1]`, its own header
  (`FNH1` + session + seq on requests, `FNH1` + rseq on replies) and a hold for what arrives early.
- **`test_homa_ordering_oracle.py` is the gate.** `free` shows the engine on an unordered wire: 127 to 823 wrong
  answers of 1,800, status 200. `ordered` must pass with the daemon's engine handed every frame in submit order,
  and holds both ends to contract 5 by liveness on a carrier that never reports a failure. `sim` and `simheld` run
  the same on the Homa simulator, as shipped and with its per-RPC timeout removed.
- **A frame is sent once.** `_DaemonWorker` re-sends an `_Rpc` up to three times and the daemon executes each. That
  retry is the TCP driver's, not the contract. Homa retransmits by itself and runs a request at most once.
- **Contract 5 is kept by liveness, not by Homa's timeout** (`core/homa_liveness.py`,
  `[LIVENESS_IS_A_HELD_READ_V1]`). When the peer's daemon end stops or is replaced, or its machine leaves the
  network, every session with it ends at once with `PeerGone` and its requests are aborted in Homa. This works with
  Homa's per-RPC timeout removed (the oracle's `simheld` mode). The peer is named by the .1 in its daemon's HELLO
  reply, not by the address the session was opened to.
- **One failed RPC still ends the session**, if the carrier reports one (Homa as shipped does, after
  `timeout_ticks`). Its seq can never arrive, so nothing after it could be handed up.
- **`FNHE` + why, in place of a reply, is the daemon's driver ending the session** (it judged this proxy end gone,
  or has no such session). The session here ends with that reason.
- **The carrier is told an address is gone only when the machine left.** A daemon that stopped starts again at the
  same address; `peer_gone` then would drop what the socket has with the new one.
- **Nothing in the driver's construction waits on the SystemRam.** On a node the way to the SystemRam can be this
  wire. `PeerLiveness` makes its first write on its own thread.
- **Neither driver reads a frame**, beyond what the TCP server already reads to bring a session up (HELLO, RTT_PING).
- **This has not run on a Homa kernel.** The socket under the oracle is a stand-in that keeps Homa's calls and
  reorders. `core/test_homa_layout_oracle.py` checks the system-call layouts against `homa.h`.
