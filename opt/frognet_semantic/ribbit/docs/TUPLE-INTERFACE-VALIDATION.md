# Tuple interface validation — 2026-10-28

The additions are in the API++ library (`ribbit_pp.hpp`, `ribbit_pp.cpp`).
The old Space, memory, host, client and wire implementation files are unchanged.
The existing `announce`, `capable`, publisher/subscriber and ask/answer APIs remain.

Contract: `TupleName {std::string indexA,indexB,indexC}`, a Tuple base class,
a derived Capability class, and the existing native JSON value model as JsonBag.
Set, Get and HeldRead all accept the same TupleName. Results and watch callbacks
are JSON bags, with rows, their names/values/write IDs/gone state, and a cursor.

| Gate | Observed result |
|---|---|
| Red checkpoint `8b65d28` (tests without new interface) | Compile fails: JsonBag and tuple interface absent |
| New C++ tests, local memory and actual TCP sessions | 42 positive/negative assertions PASS |
| Existing `test_ribbit_pp.cpp`, including 4,000 publishes and 600 ask/answer exchanges | RESULT PASS API++ (0 failed) |
| Mutant 1: ignore explicit withdrawal | Detected by 4 failed assertions |
| Mutant 2: ignore exact instance on watch | Detected by 2 failed assertions |
| Mutant 3: accept malformed capability state | Detected by 2 failed assertions |
| Added standard `STAGES=capabilities` gate | RESULT PASS capability API (0 failed) |
| Git whitespace check | PASS |

Built C++20, -O2, -Wall -Wextra -Werror (existing platform false-positive warning
suppression retained), linked the current platform's own sources and libraries.
This environment supplied the missing LZ4 headers through CPATH/LIBRARY_PATH;
normal builds use the repository's existing dependency helper.

Tests cover absence versus a present empty bag, exact and family addressing,
independent B/C selectors, names containing delimiters, metadata updates,
withdrawal without affecting another tuple, directory isolation, malformed JSON,
non-object values, nonfinite numbers, binary values, family writes, empty space,
NUL and oversized coordinates, forged reserved state, malformed foreign state,
held timeout and invalid wait arguments, integer precision, existing-state watch,
updates, reassertion, exact watch isolation, and writer session death.

An attempted unbounded integer exposed an existing wire limitation: TYPE_INT must
fit signed 64-bit. API++ now rejects unsupported integers before asynchronous
handoff, rather than letting an exception terminate the sender thread. The test
also proves 9007199254740993 (within int64 but beyond double's exact range) survives
set/get. The core wire was not changed. Larger quantities can be JSON strings.

The three mutants must reach RESULT FAIL with failed assertions. A timeout or
crash is not considered a successful negative control. The old implementation
was checked against the new tests using its original header, not guessed from
compilation of the new code.

Run the complete focused gate: `cpp/tools/check_capabilities.sh`.
Machine outputs: the gate's CAPABILITY_BUILD_DIR, default `cpp/bin/capabilities/`.
Standard platform qualification builds and runs the positive gate too.

No new claims are made about automatic RAM election, live WAN performance,
concurrent merges, permissions, or atomic tuple compare-and-swap. Base access
policy and session/linger semantics still apply. Tuple names and JSON metadata
are not proof of authenticated ownership. CPU compare/exchange inside the
implementation is not a remotely exposed atomic tuple CAS operation.

## Exists / WaitUntil / SetMany extension

Red checkpoint `0520072`: the additional tests fail to compile against the
previous header because the three methods are absent. The implementation adds
only API++ methods; no core files changed.

Current results: **64/64 assertions PASS**, local and actual TCP sessions;
**existing API++ regression PASS**; **6/6 semantic mutants detected**, each by
assertion with a RESULT FAIL summary (no crash/timeout counted as detection).

The new controls produce 4 failed assertions for Exists always false, 2 for
WaitUntil not holding, and 3 for SetMany writing before all validation completes.
The existing controls were rebuilt against the expanded tests as well: 6, 2 and 2
failed assertions for withdrawal, exact-instance watch, and malformed state.

WaitUntil's predicate receives the complete current family; the test changes a
value inside the snapshot-to-hold window and requires that change to be seen.
The test also verifies immediate matches at zero timeout, explicit timeout flags,
invalid predicate/time arguments, and propagated predicate exceptions. The base
wire's millisecond wait precision is respected rather than busy-polling the final
fraction of a millisecond.

SetMany tests require rejection of an invalid later name/bag before the first
write, allow empty input, and verify repeated-name writes in input order. This
validates before side effects; it does not make the independent base writes an
atomic transaction and does not add rollback after transport failure.

## Explicit connection helpers

`RibbitConnection::ConnectRibbit` and `DisconnectRibbit` wrap existing Space
construction/destruction. `GetSpace` rejects disconnected access, and
`IsConnected` reports ownership rather than reachability. Core files unchanged.

The expanded tuple/capability test passes **75 assertions**: 11 new lifecycle
checks plus the existing 64. New checks cover initial/closed access, repeated
disconnect, empty arguments, failed construction, real TCP connection, duplicate
connect preserving the session, tuple operations, session-owned data removal,
reconnection and local mode. The existing API++ regression was rebuilt and passed.
The previously recorded six mutation results concern tuple/capability/helper
behavior; no new mutation result is claimed for connection lifetime.

Active watches/held reads are not canceled by this wrapper. Existing base
lifetime rules still apply; callers must not disconnect while borrowers or
operations need the session. This restriction is documented in the API contract.
