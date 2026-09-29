# ribbit/defaults -- the default API and schema

What a vendor uses when its region is plain tuples: the API FrogNet discovery uses, and its schema, as code.

| | |
|---|---|
| [`api/`](api/README.md) | `ribbit::TuplesClient` and its library `libtuples.so` (C interface `tuples.h`), and `frognet_tuples.py`: the functions a FrogNet node's code calls, with the node's signatures, over that library |
| [`schema/`](schema/SCHEMA.md) | the tuple schema: how a tuple is laid out in the memory |

Two of the examples are regions of this kind -- chat (`chat-ram`) and the Communicator
(`comms-ram`): each is a `RamHost` with no operations of its own, and each client is `TuplesClient` with its own
`vendor_id()`, so each speaks only to its own host.
