# ribbit/docs -- the programming model and the platform's design

| document | what it is |
|---|---|
| `PROGRAMMING-RIBBIT-BEST-PRACTICES.md` | how to write Ribbit: what is yours to change, addressing, holding truth instead of fetching it, writing your own truth once, coordination as state, participants, the region's API, testing with the incumbent's own tests as oracle, measuring honestly, packaging, and what first implementers get wrong |
| `RAM-HOST-LOCKING-DESIGN.md` | the memory: row-level multi-reader/single-writer locks and no other lock |
| `TRANSPORT-ARCHITECTURE.md` | how participants reach a RAM host: one session, its connections, the dedicated data socket |
| `SEMANTIC-ENGINE-PORT-PLAN.md`, `S3-CHARACTERIZATION.md`, `S4-S5-SPEC.md`, `RAM-ANSWER-HANDLER-SPEC.md` | FNW1, FrogNet's semantic wire, and its port from Python to C++ |

These were written while building the lispers.net example; they describe the platform every example now shares.
