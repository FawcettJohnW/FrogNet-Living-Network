# ribbit/defaults/api -- the default API

```
./build.sh      # build/libtuples.so, build/named-ram (a test host)
./test.sh       # the API's test against a real RAM host
```

- **C++:** `tuples.hpp` -- `ribbit::TuplesClient`, a `RamClient`: `put(service, var, scope, value_json, own)`,
  `get_all(service, fresh_s, wait_s, min_rows, var, after)`, `get(service, var, ...)`, `remove_tuple`,
  `cleanup_owned`, `my_ip` (this machine's address toward the host). A vendor derives from it and says `vendor_id()`.
- **C:** `tuples.h` -- the same calls on an opaque handle, opened with a vendor id: `tuples_open("torch", host, port, &err)`.
- **Python:** `frognet_tuples.py` -- `put`, `get`, `get_all`, `_values_raw`, `_delete_by_id`, `my_ip`, the scope
  helpers, `prune_self_stale_rows`/`_capability` and the `Store*` exceptions, with a FrogNet node's exact signatures,
  so code written for a node runs unchanged. `dbhost` is the vendor's RAM host as `HOST:PORT`; the default is
  `FROGNET_TUPLES_RAM`, and a node's names for its memory (`databasehost.frognet`, `databasehost_control.frognet`)
  mean it. `FROGNET_TUPLES_VENDOR` names the region. Any other `.frognet` name is refused.

The test (`tests/test_tuples.py`) checks put, replace, the writer's address and envelope age, a held read waiting for
`min_rows`, exact and pattern names, the node's raw row shape, delete by id, freshness on the memory's clock, owned
tuples removed when their writer exits and unowned ones outliving it, the node's memory names, and refusal of any
other node name.
