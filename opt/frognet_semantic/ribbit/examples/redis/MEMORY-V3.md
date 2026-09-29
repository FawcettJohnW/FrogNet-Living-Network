# Memory v3 — mutable, addressable RAM (running record; pick up here if the work stalls)

Decision (John, 2026-09-22): immutable-region publication was imported snapshot thinking. Ribbit says a cell has a
current value and a write replaces it. Nothing requires the index to be immutable, a region root, or a path copy per
write. Region snapshots were solving a problem the contract does not pose; multi-key atomicity is an application
semantic (write_many, question 3). Target: Ribbit at least as fast as Redis locally, or a damned good reason.

## Design (first principles)
- write-order id: one atomic counter (unchanged).
- Cell = a slot holding an atomically replaceable node {id, born, written_ms, bag}. SET/HSET-existing = one atomic exchange.
  (std::atomic<std::shared_ptr<const CellNode>>: readers load, writers store; the old node dies with its last reader.)
- Variable = { name, born, atomic last, its instances }. Instances: an ordered map instance -> slot, plus the numeric
  index (score, instance) -> slot, both guarded by the variable's own shared_mutex: element add/remove is exclusive
  per KEY (only writers of the same key meet), lookups/ranges are shared. A single-instance read (GET) never takes
  the variable lock: the value slot is a dedicated atomic.
- Region = { stripes: 4096 x (mutex + unordered_map name -> shared_ptr<Var>), by_born under a region shared_mutex
  (touched only when a key is born or dies), ttl index likewise, wait words unchanged }.
- Held reads: unchanged (wait word per variable hash; writer bumps, wakes only if someone waits).
- Snapshot: gone as a memory guarantee. Memory::Snapshot stays as a type for the algebra/sort code but reads live.
- API kept as much as possible; the PMap-on-VarPtr accesses in command code become accessors on Var.

## Steps
1. [ ] memory.hpp/.cpp v3, membench green (write ~100-200 ns expected; measure)
2. [ ] db.cpp on v3
3. [ ] cmd_string/keyspace/server (layer 0/1 files green)
4. [ ] hash, set, list, zset, geo, hll, stream, groups, sort, multi
5. [ ] full regression, benchmark table vs stock, README/QUESTION-3 updated

## Revision (John): no locks either
"There should not be any race conditions. There should not be any write contention. There should not be any
locking at all." The shared_mutex-per-variable draft is gone. v3 as built:
- ebr.hpp/.cpp: epoch-based reclamation. A command runs inside one epoch (Guard in dispatch); readers hold raw
  pointers; unlinked things are retired and freed after every reader has left the older epoch. A parked
  participant steps out of its epoch while it waits.
- lfmap.hpp: lock-free ordered map (skip list, marked pointers, CAS insert/remove, retry never wait).
- memory: cell = atomic slot (SET is one exchange, no contention by construction: one writer per cell);
  variable = {atomic val, atomic ttl, LFMap inst, LFMap by_n, atomic last, dead flag}; region = 4096 LFMap
  buckets name->Var*, LFMap by_born, LFMap by_ttl; wait words as before. A dying variable sets `dead` before
  its emptiness check; a writer that lands in the window sees `dead` and redoes its write on a fresh one.
- Snapshot type kept for the algebra/sort code but is a live view: multi-key atomicity is an application semantic.
- Rank/nth on a skip list are walks (O(n)); Redis's LINDEX is O(n) too. Widths can come later if a test or a
  measurement asks for them.
Command-code sweep: CellPtr/VarPtr are raw; `(*cp)->` becomes `cp->`; PMap<...>::op(v->by_inst, ...) becomes
v->op(...); `v->last` becomes `v->last.load()`.

## v3.1: the batch is a value
John's question, "why isn't a batch just an array?", replaced a pending/commit state word I had sketched.
The array of writes is one cell; its own single exchange is the commit; members land pointing at it; readers
resolve through it; wakes are held for the batch. Batch lifetime = its live member nodes (a counter, retired
through EBR at zero) so a resolving reader never loses the array. Entries 2/4 of the register went green with it. write_if (a conditional write for WATCH) was
added and removed the same day: WATCH is Redis's, and Redis-specific things live in the Redis API layer.
