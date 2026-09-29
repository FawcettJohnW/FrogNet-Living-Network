# The C++ RAM host's memory: row-level multi-reader / single-writer locks, and no other lock
Ruling (John 2026-09-27): "The C++ engine needs row level locking and no more. The locks are multi-reader/single
writer." Also: the vendor-API executor must fan out like the rest of the host (no single Engine behind one mutex).

## Today (tools/ramsrv/ram_server.cpp, struct Memory)
One std::mutex for the whole memory, one condition_variable for every waiter. Every write, remove, read and held-read
wake takes it. Each hold is microseconds, but it is one lock for everything -- the opposite of the ruling.

## Target
- A row is a cell: (service, variable, instance). Each row carries a std::shared_mutex: readers share it, a writer
  holds it alone. Writing one row never blocks reading or writing any other row.
- No table, variable or global lock. The structures that say which rows exist are lock-free: immutable indexes
  published through the package's own epoch-based reclamation (ribbit_cpp/ebr.hpp, already proven by the lockfree
  stage in the participants) -- a reader takes a snapshot and never waits; creating or removing a row publishes a new
  index for that one variable with a compare-and-swap and retries on conflict. Writing an EXISTING row -- the common
  case -- touches no index at all, only that row's lock.
- Cell ids stay one global order (an atomic counter), so "everything in this variable written after id N" keeps its
  meaning; a read takes the variable's index snapshot, shared-locks each row it copies, keeps those with id > N,
  and returns them in id order.
- Waits (John 2026-09-27). a, b and c TOGETHER identify one storage location (addressed by the whole combination,
  not hierarchical, no root). A condition belongs to the wait, carries its comparison as JSON (C operators, custom
  comparisons allowed; data, never code), and nothing is built until a wait is requested.
  * A wait on one exact location a.b.c: its trigger hangs in a linked list off that location's envelope -- the
    tuple's metadata, where its write time is.
  * A wait with free coordinates (a.*.*, a.*.c, ...): it names no location, so it hangs on the shared space itself,
    under vendor control. Default: one table for the space, created when the first such condition is defined, keyed
    by the pattern's FIXED coordinates (a.*.c is keyed by a and c; b may be anything).
  * A write to a.b.c walks its own envelope's list, and -- only if the space's table exists -- looks up the patterns
    its coordinates could match (a.*.*, *.b.*, *.*.c, a.b.*, a.*.c, *.b.c): at most six lookups, never a scan.
  The lists and the table are lock-free: triggers pushed with a compare-and-swap, removals retired through EBR.
- Removal: the row leaves the variable's index, then is retired through EBR, so a reader still holding the old
  snapshot never touches freed memory.
- op=lisp: a pool of host Engines, each on its own thread; each operation goes to the Engine chosen by its sender, so
  one sender's operations stay in order and different senders run in parallel; site keys are given to every Engine.

## Gates
- The lockfree stage extended to the host: the source gate counts no std::mutex / lock_guard / unique_lock on the
  memory's paths; only std::shared_mutex on rows.
- A concurrency oracle: N writers on N different rows of one variable proceed in parallel (measured), readers of a
  variable never wait for writers of other rows, and every read returns exactly the cells a serial replay of the same
  history would (per-row linearizability, ids strictly increasing in write order).
- fnw1, independent, handler, stress (multi-record atomicity must still hold -- it is decided in the Engine, not by a
  memory lock), boundary, perf, fnwp e2e; and the Internet run, with the p99 tails compared.
