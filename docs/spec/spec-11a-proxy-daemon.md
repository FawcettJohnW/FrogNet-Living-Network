<!-- Generated from the site page spec-11a-proxy-daemon.html (https://fawcettinnovations.com/spec-11a-proxy-daemon.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §11a

# The proxy and the daemon.

§11 specifies what crosses the wire; this specifies what puts it there. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

**Prerequisite of §4.** Discovery's echo and getHosts go direct to the neighbour's port 80, marked past this layer, and its route check opens its own probe to `:9009` (§4.1b). The numbering follows the layered exposition, not the dependency.

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

The intercept, the permanent socket, the byte accounting

## Current implementation clauses: October 12, 2026

## Legacy intercept

The FrogNet proxy/daemon path captures HTTP, learns semantic structure and reconstructs requests for the local application server. It remains part of the node tree.

## Stand-alone Ribbit

Applications use the client library’s permanent-session transport directly to a RAM host. An application does not need the node’s port-80 capture path to mount an Internet region.

## Region integration

Region mode serves hosts and echo information from the node’s published facts and derived region hosts data. r19 prevents the proxy’s negative host cache from marking an alive region member as not-FrogNet.

## Implementation gaps

The October 11 optimization record lists semantic template re-learn and Python wrapper heartbeat/reset integration as open. Existing code must be examined before examples rely on those features.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the local path remains separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Scope](#p1)
2.  [1aConcurrency](#pd1a)
3.  [2The proxy](#p2)
4.  [3Socket sets](#p3)
5.  [4The daemon](#p4)
6.  [5The worker pool](#p5)
7.  [6Two caches, two rules](#p6)
8.  [7Non-goals](#p7)
9.  [8References](#p8)

## 1. Scope

**Programming-model note.** Transparent HTTP handling exists so existing software can receive FrogNet’s transport benefits without being rewritten. It is an adoption path, not the intended final programming model. **An application that continues to use REST remains a REST application running efficiently over FrogNet; an application using FrogNet Memory has changed the programming surface** (§10.3b).

Croakus: [§27](https://fawcettinnovations.com/magnum-croakus.html#27-fnwp-1-the-wire), Appendix F

§11 specifies what crosses the wire. This specifies the two components that put it there. Both are named at every step of the pathway in §11.2 and neither is defined elsewhere in this document.

| Component | Sits                                      | Owns                                                                                      |
|-----------|-------------------------------------------|-------------------------------------------------------------------------------------------|
| Proxy     | On port 80 of every node                  | Interception, the semantic-mode decision (§11.5), socket sets, the local read cache (§2a) |
| Daemon    | On the FrogNet service port of every node | The codec, the worker pool, execution against the origin                                  |

They are separate processes and MUST be. The caches they hold are therefore separate and behave differently (§11a.6); a design that merges them collapses that distinction.

## 1a. Concurrency, and the two directions of it

Both sides are threaded, and the two are not the same shape. The terms are used loosely elsewhere; this section fixes them.

### 1a.1 Proxy side — fan-in

The proxy accepts a request and hands it to a thread \[ThreadingMixIn\], so the accept loop returns immediately. That thread conducts the whole exchange with the remote daemon and then terminates. Threads are created in response to arriving traffic; the count follows the load offered to this node.

### 1a.2 Daemon side — fan-out

The daemon reads request frames and dispatches each to a worker from one of two fixed pools: echo to `FROGNET_DAEMON_POOL_SIZE` threads, 256 by default; everything else to a database pool of four per CPU core (`FROGNET_DAEMON_DB_POOL_SIZE`). A worker executes the request and posts a `(seq, reply)` tuple onto the reply queue. A worker MAY block on a request already in flight; when it completes it returns to the proxy like any other.

A separate writer thread drains the reply queue **in arrival order**. There is no shared send lock and no flush-polling loop.

### 1a.3 Two sockets, not one

The pair is not a single bidirectional channel.

| Socket                    | Opened by                                                                | Carries                                                                         |
|---------------------------|--------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| `proxy → daemon:9009`     | the proxy                                                                | request frames. A dedicated reader thread on the daemon reads this socket only. |
| `daemon → proxy` (return) | the proxy, a second connection to `daemon:9009` carrying HELLO `RETURN:` | reply frames. A dedicated writer thread drains onto this socket only.           |

An implementation MUST NOT assume replies arrive on the socket the request was sent on. The return connection is a second connection the proxy opens; it can fail independently. It used to be opened by the daemon back to `proxy:9010`, which failed under Wi-Fi client isolation, where the outbound leg succeeds and the return leg does not.

## 2. The proxy

The proxy takes port 80. An application on the node makes an ordinary HTTP call and MUST require no modification: interception is the whole of the integration.

**Every request to a FrogNet peer goes through the daemon**, not only the templated ones. A request that cannot be templated is forwarded raw and cached raw; it is not diverted around the semantic path. An implementation MUST NOT make raw traffic a bypass, because a bypass means two code paths with different failure modes for the same request.

Concurrency is bounded: the proxy caps active requests. An implementation MUST bound it, because the collapse mechanisms in §11.12 bound duplicate work but not arrival rate.

**No subprocess on the hot path.** Earlier versions forked a shell per request to emit topology and link-quality telemetry. Telemetry MUST instead be emitted periodically from in-process state, on its own schedule. A per-request fork makes observability cost proportional to traffic, which is exactly backwards.

## 3. Socket sets

A worker for a peer is a socket set: one outbound socket, one return socket, its own sequence space, its own pending map, its own writer and cleanup threads. One set per peer is the default (§11.11).

A caller — a game, a media stream, a bulk transfer — MAY request its **own independent set** for a peer, keyed by protocol. The caller receives an opaque token and MUST NOT touch a socket: the set is an artifact of the driver, and the driver owns its lifecycle.

### 3.1 Why independence helps

Each set is a separate flow inside the one tunnel, so it has its own congestion window and its own head-of-line domain. A lost segment on the game's set stalls the game's set and not the sensor stream sharing the peer. **That isolation is the entire purpose**; a set that does not get its own flow provides nothing.

### 3.2 Overlay is not free

Independent sets are capped at three per peer. Beyond the cap, additional protocols MUST share an existing set — and the moment two protocols share a set they share one congestion window and one head-of-line domain, so a loss on one stalls the other.

Overlay MUST therefore collapse the **least** latency-sensitive protocols together, and MUST NOT overlay onto a latency-critical set where any alternative exists. Sensitivity is declared by the caller when the set is opened; a set's sensitivity is the maximum of the protocols on it.

Sets are transient. A caller opens one at start and releases it at end, and release MUST reap the underlying worker when its last protocol leaves. Because the application is blind to the set, an implementation MUST NOT make the application responsible for the lifecycle.

## 4. The daemon

The daemon runs the codec and executes against the origin.

**Local execution MUST be gated on local resolution.** A request is executed locally only where its stated destination resolves to an interface on this machine. The destination MUST be explicit and the daemon MUST fail closed where it is absent — a daemon that infers a missing destination executes somebody else's request against its own origin.

Upstream connections to the local origin MUST be marked before connect, so that the node's own translation rules do not re-capture them. Without the mark, the proxy's interception applies to the daemon's own upstream call and the request recurses into the node.

End-to-end timeout policy MUST accommodate slow bearers — the reference implementation gives each request a budget of 15 to 30 seconds, adapted to the peer's round-trip time, under a 60-second cap — and MUST be overridable, because the bearer under FNWP-1 ranges from a wired LAN to a long-range radio (§18).

## 5. The worker pool

Database concurrency is sized at startup from three ceilings and MUST respect all three:

``` code
cpu_count() * 4            diminishing returns beyond this
max_connections / 4        leave headroom for the origin and others
RLIMIT_NOFILE / 8          file descriptors are not free
```

\[NO_FALLBACK_V1\] **Where the hardware cannot be measured, sizing MUST fail loudly rather than assume a value.** A node that cannot read its own CPU count and quietly sizes for a four-core box answers every later capacity question from a number nobody measured — disk thrash on an undersized machine, idle capacity on an oversized one, and the log says nothing either way. An operator override is the documented answer when the count genuinely cannot be taken (§18).

The executor and the pool MUST derive their size from one source. Where they drift, half the executor's workers race for connections that do not exist and the pool churns connect and close under load.

The pool MAY resize at runtime. The reference heuristics, evaluated over the window since the previous evaluation:

``` code
p95 acquire wait      > 100 ms   ->  grow 25%
p95 in-flight use     <  50%     ->  shrink 25%
otherwise                        ->  hold
```

Resizing MUST stay within the startup bounds, MUST be triggerable out of band by a sentinel as well as on its interval, and MUST log one line per evaluation **regardless of the action taken**. A resizer that logs only when it acts is indistinguishable from a resizer that is not running.

**Reference implementation, October 2026:** not wired. The daemon's database executor is sized four per CPU core (`daemon/engine/session.py`), the template-store pool is a fixed 8 (`core/store.py`), `core/sizing.py`'s three-ceiling figure has no caller, and `daemon/pool_resizer.py` is not started.

## 6. Two caches, two rules

Because the proxy and the daemon are separate processes, they hold separate caches. Both answer reads from RAM and both are conformant, for different reasons (§2a).

**Neither is the semantic store**, and neither is FrogNet Memory. The node's database holds templates and per-peer references at all times, and the tuple space only while that node is the elected host (§11.14). The two caches below sit above those, in memory, and are discarded freely.

|          | Daemon                                                                                                            | Proxy                                                                                   |
|----------|-------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| Where    | Database host only                                                                                                | Every node                                                                              |
| Covers   | The store's own tables                                                                                            | Only that proxy's own local forwards — nothing global, nothing cross-host               |
| Standing | **Proven current**: served only where a store-maintained generation counter shows RAM equals disk on this request | **Bounded stale**: scoped invalidation on writes it observes, plus a short TTL backstop |
| On doubt | Disables itself for the life of the process — fail closed                                                         | Falls through to the origin                                                             |
| Writes   | Never intercepted                                                                                                 | Never intercepted                                                                       |

Neither MUST ever acknowledge a write it has not durably placed. An earlier daemon cache acknowledged an upsert before the row reached the store, which is a false answer about durability rather than a fast one.

Invalidation on the proxy cache MUST be by name membership, not a flush on any write: writes run at hundreds per minute, and a blunt flush thrashes the cache to uselessness. A write busts every cached read whose *result* contained the written name, which covers narrow and broad queries alike because a broad query's result lists its members. The TTL exists solely to cover what name membership cannot see: a newly created item that should newly appear in a broad query whose cached result predates it.

## 7. Non-goals

The proxy is **not** a load balancer, a reverse proxy for third-party services, or a TLS terminator. The daemon is **not** an application server: it executes against the origin and holds no application logic. Neither holds the tuple space (both publish the node's role capability into it), and neither MUST be required for a node to serve its own store — a node with no reachable peer is a conforming pond of one (§3).

## 8. References

Specification [§2a](spec-02-invariants.md#b2), [§3](spec-03-node.md), [§9](spec-09-memory.md), [§10](spec-10-unrest.md), [§11](spec-11-wire.md), [§12 (Security)](README.md#s12), [§18](README.md#s18). Source: `proxy/proxy_main.py`, `proxy/decision.py`, `proxy/channel_sets.py`, `proxy/local_read_cache.py`, `daemon/daemon_main.py`, `daemon/pool_resizer.py`, `daemon/engine/data_cache.py`, `core/sizing.py`. Build manual: Croakus [§27](https://fawcettinnovations.com/magnum-croakus.html#27-fnwp-1-the-wire), Appendix F.

[← §11 FNWP-1 and BLDC-1](spec-11-wire.md) [Index](README.md) [§12 Security Architecture →](spec-12-security.md)
