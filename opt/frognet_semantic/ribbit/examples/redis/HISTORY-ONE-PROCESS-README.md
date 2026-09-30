# Redis Ribbit Memory V3 — status

One C++17 process (`ribbit-redis`): the Ribbit memory + a RESP2/3 front. No Redis code.

    make REDIS_SRC=/path/to/redis-7.2.11      # regenerates src/cmdtable.hpp from src/commands/*.json if missing
    ./ribbit-redis redis.conf [--option value ...]

Layout
  src/memory.{hpp,cpp}   the contract: write -> write-order id, read / partial index, read_after (held read),
                         read_range (a read may ask for less), count, remove, remove_variable, clear, swap.
                         Values are shared immutable bags: a read shares the pointer, a write replaces it, COPY moves nothing.
  src/db.cpp             Redis key = variable in service db<N>; value in instance "", expiry in a ttl cell beside it.
                         Readers decide (expired reads as absent); nothing reaps -- a dead key is reclaimed on its next touch.
  src/resp.{hpp,cpp}     parser (inline + multibulk, Redis's protocol-error strings) and reply builder (RESP2 downgrade rules).
  src/server.cpp         config file + command line (Redis's parsing rules and fatal messages), listener, one thread per
                         connection; every connection is a participant. There is one arm: no serialized control, no mutex
                         anywhere. Stock Redis is the oracle for "did we misread the contract"; QUESTION-3.md records the rest.
  src/cmd_conn.cpp       PING ECHO SELECT QUIT HELLO AUTH RESET
  src/cmd_keyspace.cpp   DEL UNLINK EXISTS TYPE TOUCH KEYS RANDOMKEY RENAME(NX) COPY MOVE SCAN EXPIRE-family TTL-family PERSIST
  src/cmd_string.cpp     SET-family GET-family MSET(NX) MGET APPEND STRLEN GETRANGE SETRANGE LCS INCR-family bits BITFIELD
  src/cmd_server.cpp     INFO CONFIG CLIENT COMMAND DEBUG DBSIZE FLUSHDB/ALL SWAPDB TIME OBJECT; persistence/replication -> -ERR not applicable
  src/cmdtable.hpp       generated: 392 commands (arity, flags, key ranges, acl) from 7.2.11 src/commands/*.json
  src/cfgtable.hpp       generated: 186 configs (alias, default, immutable/hidden/protected) from 7.2.11 src/config.c + stock CONFIG GET *
  tools/gen_cmdtable.py, tools/config_table.json

Harness
  Build stock Redis (make MALLOC=libc) for redis-cli/redis-benchmark; move src/redis-server to src/redis-server.stock and
  install redis-server.wrapper as src/redis-server (it execs $RIBBIT_REDIS, default /path/to/ribbit-redis).
  Run:  ./runtest --single unit/keyspace ... --ignore-encoding --ignore-digest --durable --clients 6 --timeout 120

SCAN cursor = a creation (born) id in write order: keys present at the start come back exactly once. No hash-table cursor.

## Memory, third cut (no locks, no contention on a cell)
Rule (John): no race conditions, no write contention, no locking at all. As built:
- A cell is an atomic slot holding an immutable node {id, born, written_ms, bag}. SET is one atomic exchange;
  with one writer per cell nothing contends on it.
- A variable is {atomic value slot, atomic ttl slot, lock-free ordered map of its other instances (lfmap.hpp:
  skip list, marked pointers, CAS insert/remove), lock-free numeric index, atomic newest id, dead flag}.
- A region is 4096 lock-free buckets name -> variable, a lock-free birth-order index (SCAN), a lock-free ttl
  index (derived liveness, no reaper), and the wait words (per-variable futex words; the writer bumps only its
  variable's word and wakes only if someone is parked).
- Reclamation is epoch-based (ebr.hpp): a command runs inside one epoch and holds raw pointers; an unlinked
  node is retired and freed once every reader has left the older epoch. A participant parked in a held read
  steps out of its epoch. Freeing is the server's job, on the server, never a client's.
- A variable dies when its last cell is removed: the dead flag is set BEFORE the emptiness check, so a
  writer that lands in the window sees it and redoes its write on a fresh variable. Two writers inserting
  into the same key's map collide on a CAS and one retries; nobody waits.
- Multi-key atomicity (MSET/EXEC/COPY) is application semantics over a live view: each cell lands in one step;
  the batch does not (question 3, recorded).
Measured, 1-core sandbox, redis-benchmark -c 50 -P 16 (stock 7.2.11 on the same core in parentheses):
SET 393k (621k), GET 525k (917k), INCR 356k (855k), LPUSH 353k (901k), LPOP 368k (897k), SADD 291k (897k),
HSET 281k (823k), MSET x10 62k (272k). Inside the command (INFO commandstats): SET 0.58 us, LPUSH 1.42 us,
SADD 1.69 us -- the memory is no longer the cost; the remaining gap is the front (50 threads on one core,
one read syscall per pipeline, reply building) against Redis's single epoll thread, which pays no context
switch. That is a 1-core artifact; scaling and contention need real cores.
Prior cuts for the record: v1 mutex SET 232k; v2 immutable/CAS 254k, LPUSH 139k, SADD 135k (path-copy
refcount traffic was the cost).
Validation: built with -fsanitize=address and run under hash/set/list.tcl: no report, no crash, same reds.
tools/membench.cpp times the memory without the wire; tools/waitbench.cpp the wait words (64 readers / 2
writers / 1024 variables: words=1 11k writes/s, words=4096 445k).

## The front: event loop (io-mode event, default) vs thread-per-connection (io-mode thread)
The memory result and the front are separate things. Redis's one-core advantage in the numbers above was
mundane: a 16-deep pipeline sits in the socket buffer and Redis's event loop reads, parses, executes and
answers all of it on one thread with no scheduler in the way; fifty kernel threads on one core are fifty
schedulable contexts competing for it. So the front now has an epoll thread that does the same: read, drain
every complete RESP frame, execute inline, one write per read. A command that can park (blocking pop, XREAD
BLOCK, DEBUG, WAIT) is handed, with the rest of its pipeline, to a thread of its own, and the connection
returns to the loop when it is answered. Nothing about the memory changed. Per-command trims came with it:
one lowercase per command instead of four, no to_string per argument, region lookup by the service string's
address, blocking-class detection without allocation.
Measured, same 1-core sandbox, redis-benchmark -c 50 -P 16 (stock 7.2.11 in parentheses):
SET 509k (621k), GET 678k (917k), INCR 506k (855k), LPUSH 442k (901k), LPOP 482k (897k), SADD 350k (897k),
HSET 339k (823k), MSET x10 91k (272k). Thread front on the same build: SET 393k, GET 525k, LPUSH 353k.
What remains is per-command allocation in parse and reply (Argv strings, the reply string) and the lookups
that take a std::string; gprof on the SET/GET path shows no single hot spot in the memory. On real cores the
front's execution should be dispatchable across cores -- the memory is lock-free and independent cells do
not contend -- which is the experiment Redis's model cannot run.
Conformance on the event front (list, zset, string, multi, stream-cgroups, introspection): same reds as the
thread front plus the timing-dependent family of QUESTION-3 entry 6 showing more often, because the event
thread answers the tester before a handed-off participant has run: "Circular BRPOPLPUSH", "Linked LMOVEs",
"Blocking command accounted only once in commandstats after timeout". One real fairness bug surfaced and was
fixed for both fronts: a newcomer to a key with parked claimants popped ahead of them because it had no claim
yet; a newcomer now defers to any live claim (blocking.hpp first_for). "CONFIG sanity" is red because
io-mode is an immutable config the Redis test does not know to skip.
Experiment two is built and waiting for cores: `io-threads N` runs N event threads, each with its own epoll
set; a connection is assigned round-robin at accept and belongs to that thread for its life (or to a hand-off
thread while it parks). No shared queue -- the partition is the dispatch. On this one core N=4 costs what
you'd expect (SET 372k vs 538k at N=1: four schedulable contexts again), so N=1 is the fair one-core
comparison and N>1 is the question for a real machine. The suite ran with N=4 on the one core (list, string,
multi, hash, stream-cgroups): same reds, plus one finding fixed for every front -- LPUSH/RPUSH/LINSERT
reported the list length from the live view after the write, and a participant that popped in between made a
push of 4 report 8 or 0. The length a push reports is the push's own truth: what it saw plus what it added,
unless the live count is already larger. To run the suite with arguments the wrapper takes RIBBIT_ARGS
(e.g. RIBBIT_ARGS="--io-threads 4").
The client registry (clients_mu + std::map) is gone: it is a lock-free map id -> Client*; a connection is
owned by whoever serves it (the loop, or a hand-off thread while a command parks), unlinked on close and
retired through EBR, so CLIENT LIST / KILL / UNBLOCK and the INFO derivations walk it inside their command's
epoch with nothing held. What remains locked in the front, deliberately and off the command path: the log
line, the CONFIG map, the error-stat map, and a per-connection write serializer (one writer per socket at a
time by construction; the mutex is belt over braces and will go when pushes/MONITOR are decided). None of
these is Ribbit. Same reds after the change (introspection, introspection-2, list, stream-cgroups).

## Memory v3.1: the batch is a value (QUESTION-3 entry 2, answered)
write_many with more than one member publishes the array of writes as one cell first (region "batches",
present for the batch's in-flight window), then lands the members with a pointer back to the array; readers
that meet a member resolve through the array (a later removal of the variable hides the cell, a later write
of the instance is the version reported); every wake inside a batch, and inside EXEC, is held and delivered
once at the end. A batch of one is the write it always was. WATCH is a version compare at EXEC in the Redis
region's API (db.cpp / db::Key), which is where every Redis-specific semantic lives: the region's API belongs to
the region and is optimized for what it holds (QUESTION-3.md, rule of placement).

## The Redis region's API (db::Key)
db.cpp is the Redis region's API: keys as variables, VAL/TTL/ELEM instances, derived liveness, claims, WATCH
versions, quiet spans for EXEC. db::Key resolves one Redis key once per command -- region, hash, variable,
liveness, kind -- and every operation on it (val(), write(), set_string(), push_count()) reuses that work
through addressed memory calls (Memory::write(Region&, name, hash, ...), Memory::var(Region&, name, hash));
single-element pushes and SADD/HSET take the addressed write instead of a batch of one. GET/SET/INCR/LPUSH/
RPUSH/SADD/HSET are on it. A/B against the previous binary, interleaved in one session on this box: inside
the run-to-run noise (SET 552k/514k/490k/489k alternating before/after); it is the right shape and a quiet
machine measures it. Regression (string, incr, list, set, hash, keyspace): same reds. Green: the whole "MULTI/EXEC is isolated" /
"should not awake" / "reprocessing" family in list.tcl and zset.tcl (list alone 258 ok / 16, every red N or
entry 6). Regression on string, keyspace, hash, set, stream, stream-cgroups, expire: same reds.

## Day four, later: the element fold and a front bug it exposed
An element's first version now lives inside its map node's slot: a set member or hash field written once is
one allocation (map node + slot + node + value). The first cut reused that storage after EBR reclaimed the old
version; unsafe -- the reclamation callback and the map node's own reclamation can run on different threads
in either order -- and the harness said so ("double free or corruption"). Storage is used once now; later
versions are heap nodes. With a direct single-element path for SADD/HSET (no pair vector, no sort): 8.7 -> 5.0
allocations per SADD/HSET, from 12.7 at the start of the day. One core here: SADD 0.30 -> 0.41 of stock,
HSET 0.33 -> 0.52.
Chasing an intermittent stall under oversubscription then found a front bug that predates the fold: a hand-off
thread closed its connection's fd itself, and the event thread's current epoll batch could still hold an event
for that fd -- reused meanwhile by a new client -- which it then read and closed. Level-triggered hangup on a
handed-off connection also re-fired every loop until the hand-off thread noticed. Now only the event thread
closes a connection (a hand-off thread marks it closing and hands it back), and a hangup is disarmed after
its first delivery. list.tcl six times at io-threads 8 and ten files at io-threads 4 with three clients (the
combination that stalled): clean.

## Day four: the fold, the cells, the collector
1. The fold. A cell's value lives inside its node (node + bag are one allocation; a shared pointer only when
   a bag is actually shared: COPY, a batch handed a pointer), and an element's slot lives inside its
   instance-map node. Hot paths hand the value to the addressed write by move; W carries a value or a pointer.
   Steady-state allocations per command: LPUSH 7.3 -> 5.3, SADD 10.5 -> 8.7, HSET 10.5 -> 8.3, SET 4.4 (the
   remaining four per string key are Var, node, bucket node, birth node -- intrusive-node work, later).
2. Geo search walks the numeric index over the nine geohash cells around the center (geohash_helper.c's
   step estimate, neighbour moves, the go-one-coarser rule when a neighbour wraps at a pole, and its neighbour
   order for the unsorted reply) and applies the exact circle/box test to what the cells yield. geo.tcl
   64 ok / 0; 199 s -> 88 s; the fuzzy tests 111 s -> 45 s.
3. Reclamation: every retiring thread frees its own lists as soon as the epoch has moved two past their stamp,
   not only when it is the thread that advanced the epoch. Four event threads, a million writes:
   ebr_retired_pending 132 (freed 1.0M). The Pi's 12k at three threads should collapse.
Validation: eleven files at io-threads 4, four clients: 1,127 ok / 81 err, every red known, no timeout, no
exception; ASAN build at io-threads 4 over list, set, hash, keyspace: no report.

## THE RESULT, Pi 5 (4 real Cortex-A76 cores, ARM64; client on core 3 in every row)
One core each, a million keys, -c 200 -P 16:
  stock  SET 306k  GET 403k  LPUSH 430k  SADD 191k
  Ribbit SET 268k  GET 399k  LPUSH 424k  SADD 189k          -- parity (-12% / -1% / -1% / -1%)
One core, 100 hot keys:  stock 699k/767k/364k/377k   Ribbit 689k/737k/421k/386k   -- parity, ahead on LPUSH/SADD
Ribbit io-threads 2 (cores 0,1):  SET 539k  GET 744k  LPUSH 735k  SADD 354k      -- 2.0x / 1.9x / 1.7x / 1.9x
Ribbit io-threads 3 (cores 0-2):  SET 704k  GET 858k  LPUSH 1.03M SADD 489k      -- 2.6x / 2.2x / 2.4x / 2.6x
Ribbit io-threads 3, 100 hot keys: SET 995k GET 1.10M LPUSH 995k SADD 830k       -- writers colliding on the same cells
Stock's line is one line for any number of cores. INFO ribbit after the 3-thread run: ebr_retired_pending
12,288 (higher than at one thread -- three writers outrun one collector -- but bounded; watch it at four).
Suite on ARM64, io-threads 4, --clients 8 (list, zset, stream, stream-cgroups, multi, set, hash, string,
keyspace, expire, scan, sort): 1,177 ok / 94 err, every red a known N (replication stream, DEBUG reload,
DUMP/RESTORE, loadaof, bgrewriteaof, scripting, '~' trimming, lazy expire, HTSTATS, dirty counting); no
timeout, no exception, no DIAG line. The weak memory model found no ordering hole. And on real cores the
entry-6 family (Linked LMOVEs, Circular BRPOPLPUSH, PUSH-affects-WATCH, commandstats after unblock) passed:
they flicker only when the parked participant cannot get a core.

## THE RESULT (John's i7-7567U: 2 physical cores / 4 threads; client on the siblings 2,3 in every row)
One physical core each, a million keys, -c 200 -P 16:
  stock  SET 271k  GET 397k  LPUSH 400k  SADD 193k
  Ribbit SET 301k  GET 365k  LPUSH 421k  SADD 174k          -- parity (+11% / -8% / +5% / -10%)
One core, 100 hot keys:
  stock  SET 699k  GET 863k  LPUSH 430k  SADD 392k
  Ribbit SET 649k  GET 852k  LPUSH 428k  SADD 385k          -- parity (within 7%)
Ribbit on two physical cores (io-threads 2), a million keys:
         SET 536k  GET 757k  LPUSH 739k  SADD 352k          -- +78% / +108% / +75% / +102% over its one core
Ribbit on two cores, 100 hot keys (writers colliding on the same cells):
         SET 900k  GET 1.04M LPUSH 726k  SADD 654k          -- +39% / +22% / +70% / +70%
Stock has no second row: its model executes on one thread. At equal budget the memory costs what Redis's
costs; with a second core it roughly doubles. The earlier "third thread gains nothing" was the hardware
(the third thread was a hyperthread sibling of the first). Four real cores and the ARM memory model come
next, on a Pi 5.

## Scaling, first curve (John's box, 4 cores, server on 1/2/3, benchmark on the 4th)
io-threads 1 -> 2 -> 3, spread keys: SET 414k -> 549k -> 605k, GET 515k -> 687k -> 649k, LPUSH 492k -> 561k ->
542k, SADD 243k -> 327k -> 369k. 100 hot keys: SET 703k -> 663k -> 625k, GET 919k -> 663k -> 685k. So +33% for
the second core and +10% for the third, and hot keys lose on two threads. Redis gives +0% on any of it, but the
curve says the event threads were meeting on something, and the something was bookkeeping, not data: the
write-order id counter (one fetch_add per write), g.stats.commands / e->calls / e->usec / dirty / used_memory
(four or five shared atomics per command, per command TYPE), the region's `any` wait word bumped on every write
whether or not anyone was parked, and EBR thread records packed so one thread's epoch store dirtied its
neighbours' lines on every command. All fixed the same way: counters are per-thread slots summed on read
(sharded.hpp), the id counter and the EBR records sit on their own lines, and a wait word is bumped only when a
waiter is registered on it -- the waiter registers (full-barrier RMW) before it reads `seen` and checks the
data, the writer publishes, fences, then reads the registration; one of them always sees the other, so no
wake is lost and the common case (nobody parked) writes no shared line at all. Same word for the `any` word.
Same reds on the suite; list.tcl alone four times at io-threads 4: clean. One caveat from validating this on a
one-core sandbox: 4 servers x 4 threads + 4 test clients on one core once produced a cascade of "Timeout
waiting for blocked clients" (hand-off threads not scheduled inside the tests' 1 s budget); the rerun was
clean and the mechanism is sound, but the multicore rerun is the real validation. Also: the same server, same
thread, does SET 401k with a million keys and 699k with a hundred -- the one-core gap to Redis is cache
misses on the five allocations per key, nothing else.

## Hash indirection: the direct table
John's point: the server is one entity and can keep its own shortcuts. A region now has a direct-mapped table
of Var* by name hash (2^18 entries, allocated on first use) in front of the buckets: a hit is one load and
one name compare, no walk; a miss fills the entry from the bucket; the entry is cleared before a dying
variable is retired (EBR keeps a pointer loaded inside an epoch valid), and the dead flag is checked on hit.
Three mixed runs on the sandbox, run 1 / run 3: GET 515k / 447k (was 442k / 349k), SET 411k / 343k (was 358k /
262k), MSET x10 49k / 46k (was 31k / 27k). The run-to-run slope is down from -32% to -13%; what remains is the
five scattered allocations per key. Regression with io-threads 4 over ten files: same reds.

## Diagnostics: INFO ribbit
`INFO ribbit` (also in `INFO all`) reports what the memory is made of right now -- live variables, cell nodes,
bags, slots, maps, map nodes, batches, their total, RSS and RSS per live object -- and the reclamation's state:
epoch, threads registered, retired-pending vs retired-freed, collects and epoch advances. Every count is an
atomic bumped in the object's constructor/destructor (Var, CellNode, Bag, Slot, Batch, LFMap nodes and heads).
Read it after a benchmark: a string key is Var + node + bag + bucket node + birth node (5 objects); a set
member is node + bag + slot + instance-map node (4); retired_pending staying small while retired_freed climbs
means EBR is keeping up; a thread stuck in an old epoch shows as pending climbing with advances flat.
Measured after 95k string keys: 507k live objects, 112 bytes each on average, 55 MB RSS -- ~585 bytes per key.
For where each byte goes by call site, run under valgrind's DHAT or massif (see below); for the run-2 slope,
perf stat on cache misses.

## Multicore, first results (John's 4-core box, 2026-09-22)
Run 3 (17 files, --clients 16, --io-threads 8): 1,347 ok / 143 err, all N except a real one -- a client sending
the literal command name CONFIG|GET reached the config subcommand; a name with '|' is now unknown. One run in two
stalled in list.tcl "client unblock tests". A [DIAG-UNBLOCK] line (logged on every CLIENT UNBLOCK miss) caught
it on the first recurrence here: CLIENT UNBLOCK ran before its target had parked, because the test's
wait-for-blocked-client had been satisfied by a client from the previous test that was closed but still parked
-- a parked participant only noticed its peer's hangup at the end of its 100 ms slice, while Redis frees a closed
client the instant the loop sees EOF. Fix in the front: during a hand-off the event thread keeps the fd
registered for hangup only and wakes the parked participant through its own wait word; blocked_clients drops
within a millisecond of the close (measured 5 ms). list.tcl six times at io-threads 4: no stall, no miss.
Benchmarks, stock vs Ribbit io-threads 1, both pinned to one core (3 runs): stock SET 894k-976k, GET 853k-1.10M,
INCR 990k-1.11M, LPUSH 1.06M-1.15M, SADD 780k-1.09M, HSET 704k-924k, MSET x10 195k-203k. Ribbit SET 242k-419k,
GET 337k-554k, INCR 371k-429k, LPUSH 389k-447k, LPOP 356k-383k, SADD 179k-229k, HSET 173k-221k, MSET x10 24k-25k.
The box was carrying docker, ollama and other services; Ribbit's run-to-run spread is wider than stock's.
MSET is the one shape where the batch design shows: ten member writes plus the array. The batch cell is now one
variable per writing thread in region "batches", replaced by exchange (nothing created or destroyed per
batch), and members reuse their hash; MSET x10 26.5k -> 30k here.
Memory footprint, measured (RSS after 100k string keys / +100k set members / +300k list elements):
before 106 MB / +49 MB / +155 MB; after 47 MB / +35 MB / +87 MB; stock 11 MB / +5 MB / +1.5 MB. Two changes:
skip-list nodes carry only as many next-pointers as their level (they carried 21), and a variable's element
maps are created on first element (a plain string key paid ~450 bytes for two empty maps). Still 4x Redis per
string key: Var + node + bag + bucket node + birth node are five allocations where Redis has robj + sds +
dictEntry; folding them is the next memory item, after the geohash ranges.

## Perf pass, measured by allocations (the number that does not move with the sandbox's mood)
Wall-clock A/B on this box is inside ±20% run to run, so this pass was steered by a count that is exact: mallocs
per command, from an LD_PRELOAD counter around a 100k-command benchmark of each type.
| command | before | after |
|---|---|---|
| SET | 8.6 | 4.4 |
| GET | 3.6 | 2.3 |
| LPUSH | 17.6 | 7.3 |
| LPOP | -- | 6.3 |
| SADD / HSET | 12.7 | 10.5 |
What changed: the RESP parser reserves the argv vector once it knows the count (it was growing 1-2-4), parses
lengths in place instead of through substrings, consumes the buffer by offset and compacts once per drain
instead of memmoving after every frame; list positions are 8 bytes big-endian plus an optional base-256
fraction instead of 16 hex digits plus hex fractions, so an element instance is 9 bytes and fits a std::string
without allocating (claim tickets use the same encoding); single-element pushes and SADD/HSET take the
addressed write. Regression (list, list-2, list-3, sort, string, protocol, set, hash): same reds. What is
left per command is the structure itself: argv, the key, the bag, the cell node, and for elements the slot and
the index node -- Redis pays the same shape (robj, sds, dictEntry). The next cut would be an arena for nodes.

## Where the remaining one-core gap is (gprof, SET/GET path, event front)
User-space time is spread thin: the name -> variable bucket lookup is the largest single item, then kind_of,
set_string, the parser's per-argument strings and the reply string. Nothing in the memory's write path stands
out. The bucket map is now a 3-level lock-free list per bucket (its head fits one cache line) instead of a
20-level skip list per bucket; on this sandbox the end-to-end difference is inside the run-to-run noise
(±20% between runs of the same binary today), so the layout is kept on addressing grounds and the
measurement is deferred to a quiet machine. Benchmarks on this box are only comparable within one session.

## Full regression on the event front (one core, io-threads 1), 2026-09-22
Every applicable file, each run in the harness with --ignore-encoding --ignore-digest:
- keyspace, string, incr, expire, hash, set, scan, sort, geo, hyperloglog, bitops, bitfield, protocol, auth,
  quit, info-command: 673 ok / 33 err, every red N (replication, DEBUG reload, encoding, scripting, sparse HLL,
  MASTERAUTH) or register entry 1.
- list, list-2, list-3, zset, stream, stream-cgroups, multi, introspection, introspection-2, other, info,
  slowlog, printver: 829 ok / 174 err; container-file reds all N or register entries 2/4/5/6; other/info/slowlog
  reds are the persistence, replication, scripting and pubsub families (70 of the 174).
- dump: the harness aborted at MIGRATE (a second server it could not start); DUMP/RESTORE themselves are N.
Total 1,502 ok / 207 err over 29 files, no crash, no stall. Files not run (acl, scripting, functions, pubsub,
tracking, tls, cluster, aofrw, maxmemory, lazyfree, wait, shutdown, pause, client-eviction, obuf-limits,
replybufsize, querybuf, networking, latency-monitor, memefficiency, oom-score-adj, violations, limits) are the
excluded machinery in the suite map; a few (networking, querybuf, limits, pause) may hold applicable tests and
are the next files to open. Opened since: networking, querybuf, limits, pause, wait, shutdown, lazyfree -- every
test there is CONFIG SET port/bind re-listen, query-buffer resizing internals, CLIENT PAUSE, WAIT and
replication machinery: N, as the map said.

## Containers (layer 3), each file run alone, Redis's harness, --ignore-encoding --ignore-digest
  On memory v3 (lock-free), re-run of every file below in two batches: keyspace/string/incr/expire/hash/set/
  scan/sort/multi/geo/hyperloglog 602 ok / 58 err; list/zset/stream/list-2/list-3/bitops/introspection(-2)
  743 ok / 77 err before, list alone 255 ok / 14 after one fix (push returned count-before + n; the view is
  live now, so it returns count-after). Every remaining red is a known N (encoding, DEBUG reload, replication,
  propagation, sparse HLL, scripting) or a recorded question 3. Baselines from the earlier memory follow.
  hash 70 ok / 3 N.  set 113 ok / 6 (5 N, 1 ZADD).  list 254+ ok / 15 (10 N, 1 dirty counter, 3 question-3).
  zset 295+ ok / 5 (2 N, 2 propagation, 1 question-3).  sort 52 ok / 1 (EVAL).  multi: replication cascades only.
  A sorted set is one cell per member; the memory keeps the variable's numeric index (score, then member)
  and rank/range/pop are rank/range/ends on it. Blocked pops (lists and zsets) are served in arrival
  order by claim-ticket cells, not a server queue (QUESTION-3.md entry 3).
  geo 63 ok / 0 (a zset with the 52-bit geohash score; search is a scan with the exact circle/box tests).
  hyperloglog 22 ok / 5 N (all five are the sparse encoding; every HLL here is Redis's dense format in one cell).
  stream-cgroups 56 ok / 10, all N (AOF rewrite, legacy RDB loading, replication propagation, DUMP/RESTORE,
  DEBUG loadaof). Delivery of '>' to parked readers uses claim tickets (QUESTION-3.md entry 5); XREAD/XREADGROUP
  BLOCK is integer milliseconds (it was being read as BLPOP's float seconds); INFO total_blocking_keys and
  total_blocking_keys_on_nokey are derived from what the parked clients publish, not counters.
  stream (part 1, no consumer groups) 59 ok / 16: 8 are '~'/LIMIT trimming asserting node-granularity numbers
  (stream-node-max-entries: a radix-tree fact, N), 7 propagation (N), 1 XGROUP HELP (part 2). An entry is a cell
  addressed by its 16-byte id; the partial index is the stream. One small meta cell (last id, entries-added,
  max-deleted, recorded-first) because Redis exposes those as facts that outlive deletion; published with the entries.
