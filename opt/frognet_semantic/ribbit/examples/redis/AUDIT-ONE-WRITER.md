# The one-writer audit -- every write in the Redis region, checked against the memory's rule (2026-09-29)

The rule (MEMORY-V3, John): a cell has ONE writer; a write is one atomic exchange; what can be derived is read, never
stored; contention is designed out, not arbitrated with locks or retries. Redis's contract assumes one command at a
time. On the RAM host commands from different fronts run truly in parallel (one host thread per front session), so
every place the Redis code leans on "nobody else is running" is a defect. Found by listing every write primitive's
call site (76 functions, all the writes there are -- tools: grep of g.mem.* and db:: write helpers) and reading each.

Evidence so far: coordinated test across the Internet, INCR 473 of 34,362 lost (two machines); tests/run_concurrency.py
on the 1-CPU sandbox (races only where the scheduler preempts mid-command, so PASS there proves nothing): XREADGROUP 48
double deliveries, XADD 3 lost and duplicate IDs, RPUSH 2 lost, LMOVE 1 destroyed, RPOP 1 false empty.

## P1  Contended read-modify-write of one value
Every writer reads the value, computes, writes it back: concurrent writers overwrite each other.
Commands: INCR DECR INCRBY DECRBY INCRBYFLOAT; HINCRBY HINCRBYFLOAT; ZINCRBY, ZADD INCR; BITFIELD INCRBY.
Fix (reduce-by-read, John 2026-09-29): each operation writes its OWN cell (a delta, instance unique to the op); the
value is the fold of the key's cells in write order; the reply is the fold up to the op's own write-order id (a unique,
serially consistent answer). Cells fold into a base as they age. No writer waits on another.

## P2  Whole-value edits
The whole value is read, edited, written back; concurrent edits to different parts overwrite each other.
Commands: APPEND SETRANGE SETBIT BITFIELD SET; PFADD (the register string).
Fix: each edit is its own cell (append / range / bit / register-max), folded in write order on read (PFADD folds by
max: order-free). Same machinery as P1; the reply is the fold up to the op's own id.

## P3  Positions and IDs computed from a read
The new element's place is "one past what I read": concurrent writers compute the same place; one overwrites the other.
Commands: LPUSH RPUSH LPUSHX RPUSHX (and the push half of LMOVE RPOPLPUSH BLMOVE); LINSERT (the gap between neighbours);
XADD auto-ID (from the stored last ID).
Fix: a writer's place carries its write-order id as the tiebreak, so no two writers can compute one place; order is
the position then the write order. XADD's ID comes from its own write, not from a stored "last".

## P4  Takes that do not arbitrate on their outcome
A take must be decided by the take itself (the removal, or the lowest write-order claim) -- as LPOP/ZPOPMIN/BLPOP
already do -- and the reply must come from that outcome, not from the read that chose it.
Commands: SPOP (replies with the member it chose, whether or not its removal won); SMOVE (checks, removes, adds:
a member can land in two destinations); GETDEL, GETSET, SET ... GET (the old value handed to two callers); LMOVE,
RPOPLPUSH, BLMOVE (element destroyed in transit); RPOP (gives up on a stale index: false empty); XREADGROUP,
XCLAIM, XAUTOCLAIM (the group's last-delivered id and the pending entries rewritten from a read: double delivery).
Fix: the removal (or claim ticket) is the arbitration; a loser re-reads; the reply reports what the winner took.
A move is one batch whose removal half is the claim.

## P5  Check-then-act conditionals
The condition is read, then the write happens: concurrent writers all pass the check.
Commands: SETNX, SET NX/XX, MSETNX, HSETNX, ZADD NX/XX/GT/LT, EXPIRE/PEXPIRE(AT) NX/XX/GT/LT, RENAMENX, COPY (without
REPLACE), MOVE, LSET (writes a position that may have been popped: resurrects it), XGROUP CREATE (BUSYGROUP),
XGROUP CREATECONSUMER, SETNX-shaped paths inside SORT/GEO STORE.
Fix: claims decided by write order -- every contender writes its claim; the lowest write-order claim wins; losers
see they lost and answer as Redis would (0 / nil / BUSYGROUP). The same shape the blocking claims and the DHCPv6
region's address claims already use.

## P6  Derived state stored as truth
Something computable from the cells is kept in a cell (or a global) and rewritten from a read.
- Stream metadata (last ID, entries added, first ID, max deleted ID): rewritten by XADD, XDEL, XTRIM, XSETID.
- Group state (last-delivered ID, entries read) and consumer state (seen, active): rewritten by every reader.
- List/set length used as an index (elem_count in RPOP/LSET/LTRIM/SPOP): stale under concurrent change.
- used_memory: adjusted by deltas computed from reads (set_string, RENAME).
- PFCOUNT writes its cached cardinality back (a read that writes).
- Replies computed from the read, not the outcome: SADD/HSET "newly added" counts, LREM's removed count.
Fix: compute on read from the cells (last ID = the greatest entry instance; added = count of writes; lengths from the
index); replies from the operation's own outcome. Where a value must be kept (a group's position), it becomes P1/P4:
one cell per writer, reduced on read -- or a claim.

## P7  Replacements visible in steps
Commands: ZUNIONSTORE ZINTERSTORE ZDIFFSTORE ZRANGESTORE, SINTERSTORE SUNIONSTORE SDIFFSTORE, SORT ... STORE,
GEOSEARCHSTORE / GEORADIUS STORE (remove the destination, then fill it); set_string on a key of another type (TTL
removed, value written, old elements removed: three steps); MOVE (copy, then delete); COPY REPLACE (delete, then copy).
Fix: one batch (the memory's existing shape, as [RENAME_ONE_BATCH_V1] did for RENAME).

## P8  Multi-key reads that are not one moment
The algebra "snapshot" is a live view (MEMORY-V3 says so): SINTER SUNION SDIFF ZUNION ZINTER ZDIFF (and their STOREs),
BITOP, SORT with BY/GET, PFCOUNT and PFMERGE over several keys, MGET, RENAME's read of the source, WATCH's check.
Question-3: a read at a write-order id (every cell with id <= N) would make these one moment without a lock. Register.

## P9  Transactions
MULTI/EXEC runs its queue without isolation (other fronts' commands interleave); WATCH checks versions and THEN
executes -- a write between the check and the execution breaks the optimistic lock Redis promises.
Question-3 (the publication unit, John's batch answer): EXEC as one batch, its WATCH versions verified by that batch's
publication. Register.

## Clean (no defect found)
Blind overwrites decided by write order: SET (plain), MSET (one batch), HSET/HMSET, SADD/SREM/HDEL/ZREM membership,
DEL/UNLINK, EXPIRE (plain), PERSIST; takes already arbitrated by removal: LPOP, ZPOPMIN/ZPOPMAX, BLPOP/BRPOP/BZPOP*
(claims); RENAME (one batch, [RENAME_ONE_BATCH_V1]); every read-only command.

## Plan
P1 and P2 share one machinery (per-op cells folded in write order); P3 and P4 are small, local fixes in the command
code; P5 is the claim shape already in the code; P6 removes stored state; P7 is batches. P8 and P9 go to the
question-3 register. tests/run_concurrency.py is the test that fails first for each, run on a multi-core machine
(the sandbox has one CPU); tests/coordinated.py across machines is the final word.

## Status 2026-09-29 -- [THE_MEMORY_DECIDES_V1]: the decision moved into the memory (the database), not the command code
Memory operations, each atomic in the memory (no lock, no command-side read-then-write):
- replace_if(cell, expected, new): lands only if the cell still holds what was read; expected = none -> insert only if absent
- exchange(value, new): hands back the node it replaced
- add(cell, delta): one fetch_add on the cell's accumulator; every adder gets its own total (value = bytes + adds)
- take_variable(key): removes the key and hands its value to the one caller that removed it
- remove_if(value, expected): takes back only what the caller wrote (MSETNX losing a later key)
Commands on them: INCR DECR INCRBY DECRBY HINCRBY (add); INCRBYFLOAT HINCRBYFLOAT APPEND SETRANGE SETBIT BITFIELD
ZADD INCR/NX/XX/GT/LT ZINCRBY SET XX (replace_if on what was read); SETNX SET NX HSETNX MSETNX XADD ids (insert only if
absent); GETSET SET GET (exchange); GETDEL (take_variable); XREADGROUP '>' (each entry taken by advancing the group
cell it read); pops/takes/moves (removal reports what was removed [REMOVAL_IS_THE_ANSWER_V1]); pushes (unique
positions [PUSH_POSITIONS_ARE_UNIQUE_V1], ends walked [ENDS_ARE_WALKED_V1]).
Gates here: run_concurrency 27/27; Redis suites = control for incr, string, hash, zset, stream, stream-cgroups, bitops,
bitfield, list; oracles two-fronts, trust, reconnect, same-miss GREEN.
Still open: P7 (the *STORE commands, set_string's type change, MOVE, COPY REPLACE: batches), P8 and P9 (question-3),
LINSERT's in-between position (P3), XDEL/XTRIM/XSETID meta and XCLAIM/XAUTOCLAIM/XACK pending entries (P6/P4),
PFADD registers (P2), EXPIRE NX/XX/GT/LT and RENAMENX/COPY/MOVE (P5). Edges noted in the code: an INCR racing an
APPEND/SET-derived rewrite of the same key can have its add land on a node being replaced (the add is taken back and
made again when the replacement is seen, but a rewrite computed before the add and landing after it drops it).

## Status 2026-09-29, second pass -- the rest of the audit list
[REPLACE_IN_ONE_STEP_V1] one batch each: the *STORE commands (ZUNIONSTORE ZINTERSTORE ZDIFFSTORE ZRANGESTORE,
SINTERSTORE SUNIONSTORE SDIFFSTORE, SORT STORE, GEO STORE), set_string turning a key of another kind into a string
(old elements, the value and the ttl in one step), COPY REPLACE (in the target database).
[THE_MEMORY_DECIDES_V1] further: replace_if on the ttl cell (EXPIRE/PEXPIRE(AT) NX XX GT LT); RENAMENX and COPY claim
the destination (insert only if absent) and the batch writes over the claim; MOVE takes the source (one mover wins),
writes the target only if the name is still free there, and otherwise puts the source back; the stream meta cell
(XDEL XTRIM XSETID, and XADD) changes only on the version read; XCLAIM and XAUTOCLAIM decide each pending entry on its
cell as read; PFADD and PFMERGE update registers on the cell as read; PFCOUNT writes its cache only onto the
registers it was computed from.
[REMOVAL_IS_THE_ANSWER_V1] further: XACK, XDEL, XTRIM reply what THIS call removed; LREM chooses again after losing
a match, until it has removed as many as asked or none is left.
[PUSH_POSITIONS_ARE_UNIQUE_V1] further: LINSERT's position is strictly inside the gap, not a prefix of the upper
neighbour, and carries the insert's own suffix.
Gates here: run_concurrency 38/38 (the 27 families plus EXPIRE NX, RENAMENX, COPY, MOVE, LINSERT, LREM, XDEL, XACK,
XCLAIM (min idle), PFADD, GETSET chain); Redis suites = control for keyspace, expire, string, incr, hash, set, list,
zset, stream, stream-cgroups, hyperloglog, sort, geo, bitops, bitfield; oracles GREEN.
Still open: P8 and P9 (question-3: multi-key reads at one moment; MULTI/EXEC isolation and WATCH). Edges recorded:
an INCR racing an APPEND-style rewrite of one key; SADD/HSET "newly added" counts when racing on the same member
(the member is right, the count can be 1 on both); a RENAMENX/COPY of a CONTAINER source leaves a moment where the
destination's claim cell is gone before the elements are the only content.

## P9 CLOSED by ruling -- John 2026-09-29: "P9 needs to go away. What possible reason could you have for stopping
*everything* for one query"
Nothing in this system stops for one transaction. MULTI/EXEC is a queue: its commands run in order when EXEC arrives,
each deciding in the memory like any other command; other participants' commands run between them. WATCH is checked
when EXEC arrives. Redis's isolation (no other client runs during EXEC) comes from serializing everyone; that is not
reproduced, by design. Recorded in QUESTION-3.md.

## P8 CLOSED by ruling -- John 2026-09-29: "Same thing. We will do a best-effort, but if the store underneath is changed
by another thread, then the store is changed."
Multi-key reads (SINTER SUNION SDIFF, ZUNION ZINTER ZDIFF and their STOREs, BITOP, SORT BY/GET, PFCOUNT/PFMERGE over
several keys, MGET) read each key as it is when it is read. A change another participant makes meanwhile is the store
changing, and the read reflects it. Nothing stops to give a read one moment. Recorded in QUESTION-3.md.
With P8 and P9 ruled, the audit has no open patterns; the three edges recorded above remain.
