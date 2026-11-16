# AI_README — Ribbit, the programming surface

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model. This file lists only the hazards here.

**Ribbit is the programming model, not a messaging system with a different API.** Determine your truth, write your
truth, read the other truths, go.

## What is here

| | |
|---|---|
| `RIBBIT.md` | the model and the method (specification §19) |
| `docs/PROGRAMMING-RIBBIT-BEST-PRACTICES.md` | the programming rules (§20) |
| `docs/CONVERTING-TO-RIBBIT.md` | the conversion method, three phases ending in the rewrite of the core algorithms (§21) |
| `docs/API-PLUS-PLUS.md` | the convenience layer (§22) |
| `cpp/` | the platform every example builds from: the RAM host, the client library, the wire |
| `examples/` | chat, the Communicator, lispers.net (Ribbit-LISP), Redis, MQTT, PyTorch (code elsewhere; see its README), OpenEOC, FrogTastic-RAM |

## Traps

- **Operations are put, get and get_all, and a read can be held.** No take, no queue, no history, no subscription. A
  held read is a program waiting for information it needs; the write that supplies it wakes it.
- **One owner per fact.** A participant writes its own cells; two observations are two facts in two cells, never one
  contested value. If two writers want one cell, the address is wrong.
- **No copies.** Do not mirror another participant's data locally or stage bytes in a host; read a reference to where
  they are (`[THE_BITS_STAY_WHERE_THEY_ARE_V1]`).
- **A conversion keeps the incumbent's contract and its own tests as the judge.** Never modify the incumbent's tests to
  pass. Parity is where Phase 3 starts, not a result.
- **The results have their evidence beside them**: `examples/pytorch/evidence/`, `examples/lispers.net/evidence/`,
  `examples/openeoc/RESULTS.md`. Keep the two PyTorch implementations (the backend, and meshfeed) and the two
  Ribbit-LISP runs (2026-09-28, 2026-11-14) apart.
