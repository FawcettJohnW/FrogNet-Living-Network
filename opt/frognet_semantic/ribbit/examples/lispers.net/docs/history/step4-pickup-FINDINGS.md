# Step 4 pick-up, 2026-09-26 17:45 UTC -- findings on the tree as found

The tree carries [ENGINES_IN_THE_SESSION_V1] + [HIGH_SPEED_DATA_SOCKET_V1] (frogram.hpp/.cpp, fnwp_engine.hpp,
dataplane.hpp, tools/ramsrv/ram_server.cpp, tools/test_session_tcp.cpp, tools/test_fanin_data.cpp, tools/shaped_relay.py).
Its own qualify run (results/20260926-171500) ran ONLY the stages build, fnwp, session: all green. It did not run fnw1.

1. ribbit_cpp/ram-server was the prebuilt binary of 2026-09-25. The build stage never rebuilt it, so every stage that
   starts a server (fnw1, independent, handler, stress) ran the OLD server against the NEW session: 49 errors,
   "the far end closed the connection". Fixed in tools/qualify.sh: the build stage now builds ribbit_cpp/ram-server from
   tools/ramsrv/ram_server.cpp + ribbit_cpp/frogram.cpp.

2. With the rebuilt server, fnw1 (the LISP conformance suite over the real wire) HANGS in a clean run, deterministically,
   at tests/test_roundtrip_contract test_map_resolver_add_get_delete (fnw1-run-HANG/). One earlier run got past it and
   failed 3 tests instead (2 x "HTTP 400: missing coordinate: variable" on a write, 1 x record count 3 != 2).
   Stacks (gdb): the client main thread is in Engine::wait_applied -> Memory::read -> Session::call; the client's
   watcher threads are parked in held reads on the server; the server holds two parked api() threads.

   Cause, from the code: fnwp::InOrder. The server enters every request under an order key (template + the cell's
   coordinates) and releases replies for one key strictly in request order. A parked HELD read holds its ticket until
   something is written; every later reply on the same key -- a plain read of the same cell, a write to it -- waits
   behind it. The client applies the same rule. A held read that waits for a write whose reply is queued behind it can
   never be woken: deadlock by construction. The Python daemon has no such rule: references move in wire order.

3. Stale processes: qualify's fresh_server starts ram-server with setsid nohup; a killed run leaves it listening on 8788,
   and the next run's server logs "Address already in use" while the client talks to the stale one
   (fnw1-run-port-in-use/). Cleaned by hand here.

Not changed: none of the step-4 code. This is the state the next turn starts from.
