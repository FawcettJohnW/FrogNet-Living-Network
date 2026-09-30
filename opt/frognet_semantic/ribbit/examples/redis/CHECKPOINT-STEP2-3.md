# Redis region on the RAM host -- steps 2 and 3: containers, blocking, MULTI, and the platform fixes they exposed (2026-09-29)
## Wire shape [REDIS_WIRE_FLAT_V1]
The first request shape (a list of lists) could not be templated by the semantic wire: every call RAW plus a failed
learning attempt on both ends (0.42 ms/command on loopback, 60x in-process). Now flat: the context's fields plus "p",
the pipeline's RESP bytes; reply {"r": RESP bytes, "ctx": JSON text}. Templated after one RAW: 0.18 ms/command,
pipelined ~80k/s on the 1-CPU sandbox (not the benchmark). Door 2 us; the rest is the semantic wire (~145 us/call).
## Platform fixes (patches applied at build; vendored v0.63 untouched)
- [REPLY_EXTRACTION_PROVEN_V1] (from the DHCPv6 work; not in v0.63).
- [SAME_MISS_FROM_CACHE_V1] (John's design): a RESP_SAME the client cannot resolve is answered from the host's cache
  (REQ_SAME_MISS 0x22); the request is never run again. Before: resent RAW and re-executed -- writes applied twice
  (list.tcl: 743 re-runs, wrong data). tests/run_same_miss.py: 303 -> 302 executions. Python reference fixed too:
  same-miss-from-cache.tgz.
## Blocking commands
op=redis_block: the host parks the command on its own request thread, in the Redis memory, with the unchanged claim
and wait code; registered in the region's client registry while parked (CLIENT UNBLOCK from any front finds it).
Client ids from the region. Fixes found by tests: a sticky cancel (write-then-read handshake; 18/20 immediate
hang-ups leaked, now 0/200), no call timeout on blocking calls (BLPOP 0 is forever; a dead host still ends the call),
replies before a blocking command flushed first (pipelined LPUSH+BLPOP), blocked_clients leaves only after
accounting (INFO never ahead of commandstats).
## Design changes
- [RENAME_ONE_BATCH_V1] RENAME is one batch (the memory's existing shape): it was three visible steps; a reader
  parked on the destination saw the key gone (XREADGROUP -> NOGROUP).
- [MULTI_QUEUE_IN_THE_REGION_V1] the MULTI queue stays in the region per connection; it travelled both ways in the
  context on every call (O(N^2); 10,000 queued XADDs stalled). "gone" on a connection that ends mid-MULTI.
## Suite (Redis 7.2.11's own), front + host vs the one-process control
Same reds as the control: string 79/2, incr 31/1, hash 70/3, set 114/5, stream-cgroups 56/10 (final build);
zset 316/4, keyspace 56/0, stream 60/15, multi 35/28, list 260/14 (builds earlier in the session).
list, final build: one extra red, "BRPOPLPUSH does not affect WATCH while still blocked": it depends on EXEC on one
connection reaching the region before an LPUSH sent later on another -- socket-arrival order across connections,
which separate connections through a front do not guarantee. Question-3 register, not forced.
