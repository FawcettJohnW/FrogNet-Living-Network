# HISTORY -- Redis on the RAM host (to 2026-10-17; replaced by boxes on the memory, DESIGN.md)

Stock Redis clients (redis-cli, redis-py, Jedis, redis-benchmark...) talk to a **front** on their own machine or any
reachable one. Every front connects UP to one **RAM host** on a neutral machine (streamingfrog), which holds the Redis
**region**: the keyspace, resident in memory. Fronts hold no data -- start one, stop one, start ten; they all serve the
same keyspace. Nothing connects into another machine.

    stock client --RESP--> front --FNW1 (semantic wire)--> RAM host: the Redis region, in memory

Build: `bash tools/build.sh` (one job per core; rebuilds what changed by CONTENT, not timestamps; `CLEAN=1` for from
scratch). Needs g++ 11 or newer, liblz4-dev, libssl-dev, python3. Run: RUN-ACROSS-THE-INTERNET.md.
Tests: tests/run_concurrency.py (every read-modify-write family under contention), tests/coordinated.py (machines
across the Internet, Go signal in Redis), tools/qualify.sh (oracles, Redis 7.2.11's own suite, deploy).

## The server does the work: we extended the bases the platform provides

The design principle of this system: **data is consolidated and worked on directly in the server, where it lives** --
not fetched to a client, decided on there, and written back. We get that by extending the bases the platform gives us,
not by building around them.

**The API is a base, and the Redis host inherits it.** The RAM host's interface -- the endpoint
`/Fawcett.Redis.ram_interface.php` -- is the platform's RAM interface taken whole: every base operation it offers
(read, write, remove, held reads, the semantic wire, sessions, data sockets) is inherited unchanged, and the Redis
region adds its own operations on top: `op=redis` (a pipeline of Redis commands, executed in the region, inline --
it never waits) and `op=redis_block` (a blocking command, parked on its own request thread in the region).
`tools/make_host.sh` builds the host from the platform's unmodified ram_server.cpp plus one include and one dispatch
line: the base is inherited, not copied or forked. This is the same pattern as Discovery's API.php, which is itself a
base class: an application inherits it and adds operations that work on the data in the server.

**The memory is a base, and we extended it with operations that decide in the server.** The platform's memory
contract (write -> write-order id, read, partial indexes, held reads, remove, batches) is inherited; we added
operations that consolidate and decide inside the memory itself, each one atomic there -- no lock, no read-then-write
in the command code ([THE_MEMORY_DECIDES_V1], src/memory.{hpp,cpp}):

| memory operation | what the server decides | Redis commands built on it |
|---|---|---|
| `add(cell, delta)` | the total: one fetch_add on the cell's accumulator; every adder gets its own total | INCR DECR INCRBY DECRBY HINCRBY |
| `replace_if(cell, expected, new)` | whether this write lands: only if the cell still holds what was read (nothing expected = insert only if absent) | SETNX, SET NX/XX, HSETNX, MSETNX, XADD ids, INCRBYFLOAT, HINCRBYFLOAT, APPEND, SETRANGE, SETBIT, BITFIELD, ZADD INCR/NX/XX/GT/LT, ZINCRBY, XREADGROUP delivery |
| `exchange(value, new)` | which value was replaced, handed back | GETSET, SET ... GET |
| `take_variable(key)` | who removed the key, and its value, handed to that one caller | GETDEL |
| `remove_if(value, expected)` | taking back only what this caller wrote | MSETNX losing a later key |
| removal counts (existing) | who took an element: the removal is the answer | LPOP RPOP SPOP ZPOPMIN SMOVE LMOVE LREM, HDEL SREM ZREM, XACK XDEL XTRIM |
| `replace_if` on the ttl cell | whether an expiry lands on the ttl that was compared | EXPIRE/PEXPIRE(AT) NX XX GT LT |
| batches (existing), used whole | a replacement appears in one step | the *STORE commands, SET over another kind, COPY REPLACE, RENAME |
| pieces: each edit its own cell, folded in ticket order on read (reduce-by-read) | nothing -- no edit reads-then-writes, so none can lose | APPEND SETRANGE SETBIT BITFIELD, PFADD PFMERGE (register maxima) |

A command takes the memory's answer as final; where a claim is lost it takes the NEXT item (pops, SPOP, LREM,
XREADGROUP), never the same one again. Where a command's new value depends on the old one and no accumulator can carry
it (ZADD INCR/NX/XX/GT/LT and ZINCRBY, whose score is indexed; APPEND, whose reply is the length after it), the memory
decides it with `update`: the value is decided from the live cell and lands only on that cell, and if another writer
replaced the cell first it is decided again on the new one -- the compare-and-swap loop of every lock-free structure
in the memory, serialising the command with that writer as Redis serialises commands ([ONE_CELL_SERIALISED_V1]). The
same mechanism, never a second one.
PROGRESS-NO-FALLBACKS.md records how each read-modify-write became a memory operation.

Why it matters: the region runs commands from every front truly in parallel (one host thread per front session).
When the command code read a value, decided, and wrote back, concurrent commands overwrote each other: across two
machines on the Internet, 473 of 34,362 INCRs were lost; on one multi-core host, 22 of 27 command families broke their
Redis guarantee (lost increments, lost pushes, entries delivered twice, several SETNX winners). With the server
deciding, tests/run_concurrency.py holds all 38 families it checks (AUDIT-ONE-WRITER.md has the audit, pattern by pattern, and what is
still open).

**The client is equivalent to the platform's Python client.** The C++ client coalesces identical in-flight requests
and applies each instance's replies in arrival order in the caller's turn, exactly as the Python proxy does
([CLIENT_COALESCES_V1], [WIRE_ORDER_TURNS_V1]); an answer the far end named as SAME but the near end no longer holds is
fetched from the far end's cache, never re-run ([SAME_MISS_FROM_CACHE_V1]). CLIENT-EQUIVALENCE.md.

## On FD&F's platform (2026-10-17) [PLATFORM_FROM_FDF_V1]

The platform under the region is FD&F's: tools/build.sh copies platform/ribbit-v0.63 and applies FD&F's generic patch
chain (tools/fdf/, vendored unchanged from opt/fdf/src/fdf-source/rinab/tools; the RINA-only host_announce, net_config
and region_session_key are not applied), then Redis's own patches rewritten for it:

- apply_shape_fastpath.py [SHAPE_NEEDS_AN_OBJECT_V1] (unchanged)
- apply_row_scan_bound_fdf.py [ROW_SCAN_BOUND_V1]: the same bound, on the anchors after [PUBLISH_IN_ID_ORDER_V1]
- apply_call_bytes_lockless.py [RESP_ON_THE_WIRE_V1]: Session::call_bytes is FD&F's call_text ([CALL_TEXT_V1])
- apply_call_async_lockless.py [NOBODY_WAITS_V1]: call_async on the lock-free session, coalesced like call; the same
  code as ribbit/cpp's platform
- make_host.sh: the region's dispatch on FD&F's host -- pool ([HOST_FANOUT_V1]), per-session order
  ([SESSION_ORDER_V1]), coalescing ([HOST_COALESCE_V1]), deferred answers ([DEFERRED_ANSWER_V1]). op=redis_raw takes its
  continuation and the region's pool answers it; no thread is made. The host's session hooks are empty: the region keeps
  nothing per session.

Replaced and no longer used: apply_reply_proof, apply_same_miss, apply_coalesce, apply_wire_order (FD&F's own copies in
tools/fdf/), apply_row_scan_bound, apply_call_bytes, apply_call_async.

Two front fixes found by the port:
- [FEED_OPEN_IS_THIS_FRONTS_V1] two fronts opening their invalidation feeds at once sent the same bytes; identical
  requests in flight are one request on the fabric, so both got ONE feed and one front's copy went stale (coherence:
  "convergence ... still stale"). The request now names its front.
- [BLOCKING_CHANGES_LEAVE_THE_COPY_V1] a blocking command's changes (BZPOPMIN, BLMOVE, ...) reached the front's kept
  copy only through the feed, after the reply had gone out: another connection on that front could read the old value
  (Redis suite, zset "BZPOPMIN/BZPOPMAX with multiple existing sorted sets", 1 run in 14; the v0.63 build had it too:
  193 of 2000 rounds stale). Now every argument of a blocking command leaves the copy before its reply is written.
  tests/run_coherence.py checks it ("blocking", 2000 rounds).

Two platform fixes on top of FD&F's chain:
- apply_same_last_held.py [SAME_NAMES_A_HELD_ANSWER_V1]: at 200 clients every run failed with "REQ_SAME_MISS: the
  answer RESP_SAME named is no longer held" and the front dropped its session. [DIAG-SAME] on both ends: 3138 client
  misses in one run, each for an answer the client had held and evicted (256 entries, by insertion); the host answered
  3137 from its 4096-entry cache and then had evicted one too. A SAME names the answer held for a request however
  old, and neither cache moved an entry it kept naming. Now the client holds 4096 like the host, both move an entry to
  the back each time it is answered or named (O(1)), and a REQ_SAME_MISS for an instance's last answer is answered from
  the writer's own copy of it. 200 clients: 3 of 3 runs clean.
- apply_adaptive_pool.py [ADAPTIVE_POOL_V1] [WAIT_TIME_TRIGGER_V1]: the host's pool starts empty, has no ceiling, starts
  a worker when the oldest queued task has waited past RIBBIT_POOL_WAIT_US (1 ms) with no worker free (checked by
  every push, every take, and a 1 ms monitor while work is queued), one start at a time, and retires a worker idle for
  RIBBIT_POOL_IDLE_S (60 s). Parked reads and landed bodies run on it instead of a thread each. The region's helpers
  (src/redis_region.cpp) follow the same rule. 2 cores, 200 clients, SET / GET per s and host threads: trigger 100 us
  46.6k / 92.1k, 222; 1 ms 55.9k / 85.7k, 24; 10 ms 59.4k / 96.1k, 19. 300 clients parked in BLPOP: 13 -> 313 threads,
  all 300 released in 314 ms by one RPUSH each.

Measured in the sandbox (2 cores), against the v0.63-platform build: Redis's suite the same per file; concurrency
39/39; coherence PASS; endpoints and loss PASS. redis-benchmark SET/GET -P 1 is slower: one client 7.2k -> 5.2k/s,
100 clients SET 68k -> 51k, GET 79k -> 72k. Per request the host makes 3 context switches instead of 1 (socket reader
-> pool -> the session's writer) and the front 4 instead of 2 (the lock-free session's sender and applier threads);
system time per request host 22 -> 47 us, front 39 -> 61 us. The Pi numbers decide.

## Across the Internet
- Trust: the region issues every connection an id and a random token; authentication is held in the region, never
  taken from a call ([REGION_HELD_AUTH_V1]); configuration comes from the host's operator ([REGION_CONFIG_IS_THE_HOSTS_V1]).
- A front survives losing the host: in-flight calls fail loudly, the next call opens a new session ([FRONT_RECONNECTS_V1]).
- Measured AI-Host -> streamingfrog (~23 ms round trip): 1 client ~44/s, 50 clients ~1,900/s, 50 clients pipelined
  x16 ~31,500/s -- 85-90% of what the round trip itself allows.
- Not here: encryption on the front-host link (run it inside WireGuard or FrogNet's transport); persistence (a host
  restart is a new, empty region).

## Layout
    src/memory.{hpp,cpp}     the memory, extended with the deciding operations
    src/redis_region.cpp     the door: the Redis region's operations added to the inherited RAM interface
    src/front.cpp            the RESP front (a participant connecting up)
    src/db.cpp, src/cmd_*.cpp  Redis's data model and commands, in the region
    tools/make_host.sh       the host = the platform's ram_server + one include + one dispatch line
    tools/apply_*.py         platform patches applied at build (vendored v0.63 untouched)
    tests/                   run_concurrency.py (the one-writer audit), coordinated.py (multi-machine test, Go signal
                             in Redis), run_two_fronts / run_trust / run_reconnect / run_same_miss, internet_wrapper
    tools/qualify.sh         build, oracles, Redis's own suite (local and across a simulated 24 ms path), deploy

## Documents
AUDIT-ONE-WRITER.md (concurrency, by pattern) - PROGRESS-NO-FALLBACKS.md - TRIAGE-SUITE-REDS.md (every red in
Redis's suite, traced) - RUN-ACROSS-THE-INTERNET.md - MEMORY-V3.md -
QUESTION-3.md (what Redis's contract assumes that a shared region answers differently) - CHECKPOINT-STEP*.md (history)
- HISTORY-ONE-PROCESS-README.md (the earlier one-process build).
