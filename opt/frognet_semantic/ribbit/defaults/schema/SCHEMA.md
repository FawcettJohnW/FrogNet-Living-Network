# The default schema: tuples

A tuple is one cell of the memory:

| tuple | cell |
|---|---|
| service (the application's; `SensorType` on a FrogNet node) | service |
| var -- what the tuple is | variable |
| scope -- whose it is: `host:<ip>`, `host:<ip>:<pid>`, `host:<ip>:<role>`, `session:<id>`, or any name | instance |
| value, the writer's address | bag: `{"value": {...}, "addr": "<ip>"}` |

- **The name.** `SD:<var>.<scope>`, as discovery names it; a pattern `SD:<var>.%` reads one var in every scope.
- **Writing replaces.** A coordinate holds one value; a put replaces it, as writing to a memory location does. `put`
  stamps `value.ts` (seconds) if the value has none.
- **Freshness is the memory's.** `fresh_s` filters on when the memory last wrote the cell, on the memory's own clock
  (`ts_env`), never on a writer's `ts`, so clock skew between writers cannot matter.
- **Held reads.** `wait_s` holds a read until `min_rows` tuples exist, or until something newer than a given write
  id exists (`after`).
- **Ownership.** A tuple put with `own=True` is removed when its writer closes (ephemeral coordination: presence,
  call signalling); `own=False` outlives the writer and ages out by freshness.
