# Every red in the ten suite files, traced (2026-09-29)
## Fixed in this pass
- list "BLPOP/BLMOVE should increase dirty": a move counted twice (its pop and its push); Redis counts a move once.
  move_one and bmove_generic now push without counting. Green.
- multi "EXEC with at least one use-memory command should fail": with maxmemory exceeded after queuing (another client
  set it), Redis discards the transaction at EXEC with EXECABORT ... OOM; we ran it. EXEC now checks. Green.
- incr (4 reds, found on the Pi): INCR on a key with pending edit pieces added under the pieces ("1"+APPEND "2"+INCR
  gave "22"); an add now goes in as a piece after them. Green.
## Traced to Redis machinery (the test exercises it; nothing here corresponds to it)
- replication stream / propagation to replica: string (GETDEL, GETEX), set (SPOP), list (BLMPOP, LMPOP, SWAPDB-expired,
  MULTI+LPUSH+EXPIRE, unblock execution order), zset (ZMPOP, BZMPOP), stream (7 "can propagate correctly"),
  stream-cgroups (propagation to slave x2, XCLAIM replication x2), multi (7 propagation, REPLICAOF, min-replicas
  NOREPLICAS, stale-replica).
- persistence: DEBUG RELOAD (set x3, list x5 incl. "plain nodes", zset x2), DEBUG LOADAOF (cgroups NOACK consumer),
  DUMP (cgroups seen/active-time: every seen/active assertion passes before the DUMP), AOF rewrite and legacy RDB
  loading (stream, cgroups), RDB loading (list), SAVE, BGREWRITEAOF, appendonly (multi).
- Redis's internal encodings: shared-object refcount (incr), ziplist/listpack/quicklist (hash x3, list x2), DEBUG
  HTSTATS (set long chain), stream radix-tree node granularity for '~' and LIMIT trimming (stream x6: '~' keeps at
  least N and LIMIT never exceeds its limit -- the contracts hold; only Redis's node boundaries differ).
- Lua scripting: multi (script timeout x4, EVAL, SCRIPT LOAD/FLUSH propagation). SHUTDOWN (multi).
## Cascades (never run their own logic in the full file)
- multi: EXEC OOM (read-only), "Blocking commands ignores the timeout", "MULTI with config error", "Flushall while
  watching several keys" -- the replication tests before them fail with a transaction left open. Run on their own,
  all four pass.
## Question-3 register (timing across connections)
- list "BRPOPLPUSH does not affect WATCH while still blocked": depends on EXEC on one connection reaching the region
  before an LPUSH sent later on another; separate connections through fronts do not guarantee arrival order.
- hash "HINCRBYFLOAT for correct float representation": Redis runs it on x86_64 only (72 tests on ARM is complete).
