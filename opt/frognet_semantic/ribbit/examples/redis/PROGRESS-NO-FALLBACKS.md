# Removing every fallback from the Redis region -- progress (2026-09-29)
Ruling: "fail, then recompute" is a FALLBACK (NO FALLBACKS, EVER). Taking the NEXT item after a lost claim is the
accepted pattern (pops, LREM choosing among remaining matches, XREADGROUP taking the next entry, XCLAIM/XAUTOCLAIM
skipping an entry another claimer took): the claim's answer is final for that item. Recomputing the SAME operation after
losing is what goes. The memory performs operations itself (add = one fetch_add); reduce-by-read (each operation its own
cell, folded in write order) is the direction for contended read-modify-write.
## Done
- Memory::add: one pass, no take-back/add-again; an add landing on a value just replaced happened before that SET.
- INCRBYFLOAT, HINCRBYFLOAT: the float accumulator (one fetch_add); db::text renders float adds (long double base).
- INCR/HINCRBY error paths (base not a number, overflow) withdraw their add and raise the error: error handling, kept.
- SET XX: one exchange when present. EXPIRE NX/XX/GT/LT: one replace_if on the ttl cell (a loser answers 0).
  ZADD NX/XX/GT/LT: one replace_if per member (a loser is not applied).
- Streams: meta derived on read (last = stored or greatest entry, first = first entry, added = stored + the meta
  cell's accumulator, maxdel = stored or every XDEL's own cell); XADD writes no meta; an auto id = distance of this
  XADD's number (one fetch_add) from the number kept in the entry it builds on -- Redis's id one at a time, distinct
  when overlapping; explicit ids decided once; XDEL writes its own maxdel cell; XTRIM writes nothing; XSETID one
  exchange. stream.tcl and stream-cgroups.tcl = control.
- Float adds render through db::float_text: the accumulator is a double (lock-free), rounded to 15 significant digits
  then Redis's human format (hash.tcl "correct float representation" test green again).
Gates at this point: incr, string, hash, expire, stream, stream-cgroups suites = control; concurrency families
INCR INCRBY DECR INCRBYFLOAT HINCRBY HINCRBYFLOAT XADD XREADGROUP EXPIRE-NX XDEL XACK GETSET green.
- String edits as pieces [PIECES_V1] (reduce-by-read): APPEND, SETRANGE, SETBIT, BITFIELD each write their edit as its
  own cell under a ticket; the value is the value cell folded with its pieces in ticket order. edit_string (the
  recompute loop) is gone. A writer that sees 32+ pieces folds them into a new value cell with ONE replace_if; losing
  means no fold this time (optional work, not a second attempt). SET-class writes (SET, MSET, GETSET, SET GET/XX)
  drop the old pieces.
- HyperLogLog as register pieces folded by max: PFADD writes only the registers it raises; PFMERGE the maxima above
  the destination; creation is insert-if-absent and "created" is answered only by the call whose insert won.
- COPY, MOVE, RENAME, RENAMENX and copy_key copy a cell's value with its adds folded in (db::copy_bag): the accumulator
  is part of the cell, not of its bytes (a copied stream's entries-added was lost without it).
- Every remaining loop accounted for: taking the next item after a lost claim (LREM, SPOP), blocking waits, the
  memory's dying-variable rule (as v3's Memory::write), the front's event loops. None recomputes after losing.
Gates: run_concurrency 38/38 (1 CPU); Redis suites = control for incr, string, hash, expire, keyspace, zset, stream,
stream-cgroups, bitops, bitfield, hyperloglog.

## Remaining
- ZINCRBY / ZADD INCR: currently one decision (a loser under contention answers nil and its increment is lost). The
  score is also the member's position in the memory's ordered score index; the memory has to move the index entry as
  part of the add. Next.

## Later fixes (2026-09-29)
- [SAME_MISS_FROM_CACHE_V1], C++ host: an answer over 64 KB erased the request's whole REPEAT-cache entry, which is also
  the base the next answer is decided against; every repeat of a large answer then cost a REQ_MISS and a full resend
  (Ribbit platform fnwp storm test: 2.6 MB instead of 14,800 B). Now as the Python daemon: the entry always stays, only
  the answer is not kept above 64 KB, and SAME is said only for an answer held here.
- XADD auto ids: the distance-from-base rule was not unique when XADDs overlapped (an XADD that saw an older last entry
  measured from another base; one in ~4 audit runs lost an insert). Then "the entry built on carries n-1" was taken
  as "alone", which it is not (one in ~5 runs). Now, in this order: count the XADD in flight (a fetch_add on its own
  cell), take its number, read the stream, decide. Alone = the only XADD in flight AND the last entry carries n-1 ->
  Redis's exact id; otherwise sequence 2^63 + n, a range the exact form never reaches. Counting after the number was
  still one in ~16: [DIAG-XADD] (RIBBIT_XADD_DIAG=1) caught ten whole XADDs running between the two steps. Counting
  first: 26 of 26 instrumented runs, 0 collisions; stream suites = control; audit 38/38.
