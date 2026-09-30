# Redis region on the RAM host -- step 1: host, door, minimal front (2026-09-29)
John 2026-09-29: the one-process ribbit-redis "is not mirroring the real redis at all". Now: a RAM host (v0.63's
ram_server.cpp) holds the Redis region; RESP fronts are participants that connect up to it.

## What was done
- Day-4b sources (redis-ribbit-checkpoint-day4b.tgz, sha256 in SOURCE-day4b.sha256) moved into namespace rr: the
  host also contains the platform's Reply/Memory/Cell/Engine, and two global definitions of one name in one binary is
  a silent ODR break. One late #include moved to the top of cmd_list.cpp. server.cpp split: engine (config, stats,
  registry, dispatch) stays; the front is new (src/front.cpp). log_fp made reachable. No command code changed.
- The door (src/redis_region.cpp, [REDIS_REGION_V1]): the host's op=redis runs one connection's pipeline in the
  Redis memory resident in the host [RESIDENT_REGION_V1] -- the Redis region's implementation is the lock-free Redis
  memory; the context (db, proto, auth, name, reply mode, MULTI queue, WATCH) travels with the call and comes back.
  Inline on the connection's thread: a Redis region operation never waits. One symbol crosses: redis_region_call.
- The host: tools/make_host.sh adds one include and one dispatch line to v0.63's unmodified ram_server.cpp.
  build/ is built by tools/build.sh with [REPLY_EXTRACTION_PROVEN_V1] applied (not in v0.63).
- The front (src/front.cpp): RESP to stock clients, one thread per connection, each read's pipeline as one call cut
  into frame-sized calls; RIBBIT_RAM=host:port connects up to a named host; unset, it starts a host of its own on a
  loopback port (a fresh region per server, as Redis's suite expects) and stops it on exit.
## Results (Redis 7.2.11's own suite, --clients 1)
| file | one-process control | front + host |
|---|---|---|
| unit/type/string | 79 ok / 2 err | 78 ok / 3 err |
| unit/type/incr | 31 / 1 | 31 / 1 |
| unit/keyspace | 56 / 0 | 56 / 0 |
Every red but one is the control's (propagation to a replica; shared integer objects). The new one: "Very big
payload in GET/SET" -- a 4 MB value, refused by name (see limits).
tests/run_two_fronts.py (cannot run on the one-process build: RED by construction) T1-T5 GREEN: a write on front A
read on B; 100 INCRs across both = 100; databases are the region's; A killed, B serves everything, A restarted
serves at once.
## Found on the way
1. FROGNET_BLOB_ROOT: the semantic wire keeps large fields in a blob store (semtpl.hpp, default
   /opt/frognet_semantic/blob_cache). Absent, every request over ~8 KB failed, reported only as "fnwp client: REQ_RAW
   answered with something other than RESP_RAW" (host: "ENCODE FAILED train_exception"). The front now creates a
   private blob root per run for itself and the host it starts. A host run on its own needs FROGNET_BLOB_ROOT or that
   directory. Platform note: the error names the wire, not the cause.
2. CORRECTED 2026-09-29 (step 2): I first reported a 1 MiB value limit from ram_server.cpp's MAX_FRAME and made the
   front refuse larger commands. That was wrong: I never checked the path large bodies take. They cross on the data
   socket in segments ([DATA_MARKER_V1]); a 20 MB value and a 100,000-member SADD round-trip exactly. The refusal and
   the frame budget are removed. "Very big payload in GET/SET" passes (see CHECKPOINT-STEP2.md). There is no such
   platform limit to decide on.
3. My own mistake, recorded: piping the compiler through `head` killed g++ by SIGPIPE mid-compile (an object went
   missing silently). tools/build.sh never pipes the compiler.
## Not yet (next steps)
Blocking commands (answered with a named error; the front will hold the read), MULTI across calls, pub/sub, CLIENT
LIST/KILL and INFO clients across fronts (presence cells), the rest of the suite, the event front, the
streamingfrog benchmark.
