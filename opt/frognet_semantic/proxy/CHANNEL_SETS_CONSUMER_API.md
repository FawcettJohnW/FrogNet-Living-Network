# FrogNet socket-sets + transport modes — CONSUMER / API doc

Hand this to a chat that will WRITE CODE USING this feature. It is self-contained:
it does not assume the reader has the codebase. It describes the public API and the
guarantees; it does not require reading the transport internals.

Producer side (the proxy/daemon implementing this) is already built and verified.
Consumers only need the API below.

---

## What this gives you

A way for an app to get its OWN independent transport channel to a FrogNet peer —
own TCP socket pair, own sequence space, own worker threads — so one protocol's
packet loss or stall does not block another's. Think: a game, a media stream, and a
control channel to the same peer, each isolated from the others' head-of-line blocking.

The app never touches a socket. It asks for a set by protocol name, gets an opaque
token, and sends through the token. The driver owns the sockets and their lifecycle.

## The API (proxy/channel_sets.py)

```python
from proxy.channel_sets import (
    open_channel_set, release_channel_set, worker_for_token, ChannelToken,
)

# Open (or join) an independent socket set toward host:port for a protocol.
#   sensitivity: 0 = bulk/latency-tolerant .. higher = latency-critical.
#   Returns an opaque ChannelToken. The underlying socket set is created
#   lazily on the first RPC through the token.
token = open_channel_set(host: str, port: int, protocol: str,
                         sensitivity: int = 0) -> ChannelToken

# Send a request on this set's worker and block for the reply, exactly like the
# default path. `packet` is a wire frame (REQ_RAW / REQ_FULL / REQ_DIFF / etc.,
# built with core.semcache_wire helpers). Returns the reply bytes.
reply = worker_for_token(token).call(packet)

# Release the set when done (e.g. game over). When the set's LAST protocol is
# released, its sockets close and its worker threads exit (transient lifetime).
# Returns True if the underlying set was torn down.
release_channel_set(token) -> bool
```

`ChannelToken` is opaque: it carries `host`, `port`, `set_id`, `protocol`. Do not
construct it yourself — only `open_channel_set` returns valid tokens. Do not depend
on `set_id` values; treat the token as a handle.

## Guarantees and limits (READ THESE — they shape correct use)

- **Up to 3 INDEPENDENT sets per peer.** The first three distinct protocols toward a
  peer each get a truly independent set (own sockets, own seq, own threads).
- **Beyond 3 → OVERLAY, and overlay is not free.** A 4th+ protocol shares an existing
  set. Two protocols sharing a set share one TCP congestion window and one
  head-of-line domain — a loss on one stalls the other. Overlay deliberately lands on
  the LEAST `sensitivity` set. So: **pass honest sensitivity values**, or you lose the
  isolation you opened the set for. game=9, media=5, control=1, bulk=0 is a sane scale.
- **Per peer, not global.** The 3-set budget is per (host, port). Total sockets across
  many peers is bounded only by how many peers you talk to — size your fd/conntrack
  budget accordingly.
- **Set 0 is the shared default.** Any code path that does not use this API at all keeps
  using the single shared set (set 0) with no change. Opening sets is purely additive.
- **Idempotent open.** Asking for the same protocol toward the same peer twice returns
  the same set.
- **Transient lifetime.** Release when the activity ends. The driver reaps the set
  (sockets + threads) on the last release. Forgetting to release leaks a set until
  process exit — within the 3/peer cap, but it won't free early.

## What a consumer does NOT need to do

- Does not open/close sockets, do the HELLO/HELLO RETURN/SEQ_RESET handshake, or manage
  reconnects — the worker does all of that on first `.call()`.
- Does not pick `set_id` — the allocator assigns it.
- Does not know the transport mode (sim/loopback/remote); that is a deployment concern,
  not an app concern.

## Producer-side prerequisite (already applied in the shipped changeset)

This API depends on the proxy keying workers by set:
`_WORKERS: Dict[(host, port, set_id), _DaemonWorker]` and
`_get_worker(host, port, set_id=0)`. It is backward compatible — `set_id=0` is the
shared default. If you are integrating into a tree that lacks this, apply that two-line
change first (see STATUS_physical_transport.md). Consumers do not apply it; producers do.

## Transport modes (context only — not a consumer concern)

The same transport runs in three modes, selected at the harness/deployment level, not by
the app: 0=sim (in-process), 1=loopback (real TCP, local daemon), 2=remote (real TCP over
a true interface to a remote daemon + origin). A consumer's code is identical across all
three — it always goes through `worker_for_token(token).call(packet)`.

## Status of the feature

Logic + lifecycle verified in-container: 3 sets → 3 distinct workers (set ids 0/1/2),
intentional overlay away from the sensitive set, reap-on-last-release with all per-set
threads exiting (no leak). Modes 0/1/2 verified on hardware (Pi 5). NOT yet measured:
socket-set churn under real WireGuard crypto on the box, and the cross-box physical hop.
Treat throughput/HOL claims as designed-and-unit-proven, not yet load-measured.
