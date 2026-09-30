# Engine::dispatch() dissected -- v0.63-bounded (lizard: 833 NLOC, CCN 548)

Method: dispatch() split into its 53 top-level branches by brace depth; decision points counted as lizard counts them
(`if for while case catch && || ?`, comments and strings removed) -- the count reproduces lizard's exactly: 523
decisions + 1 = CCN 548, and 833 NLOC. Each decision is then attributed, first match wins:

- **select** -- the `op=="..."` comparisons that pick the branch (pure routing);
- **validate** -- conditions whose statement is a `throw` (argument and contract checks);
- **json** -- tests of a JSON argument's type to take it or default it (argument decoding);
- **local** -- the in-process backend: the `if(ram)` tests and the decisions in their no-RAM branches (a second,
  map-based implementation the `local` qualification stage runs without a RAM server);
- **rest** -- everything else: the operation's own logic.

The validate/json/local attributions are pattern-based (regular expressions over the source), so the rest column is an
upper bound on the operations' own logic, not an exact figure.

## By kind of operation

| kind | branches | NLOC | decisions | select | validate | json | local | rest |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| wire protocol (LISP packets in/out) | 8 | 94 | 74 | 12 | 6 | 4 | 4 | 48 |
| registration state (put / delete / resolve) | 3 | 173 | 119 | 3 | 1 | 4 | 12 | 99 |
| region configuration | 20 | 296 | 179 | 22 | 27 | 8 | 27 | 95 |
| native participant roles (ETR liveness, governor, registrar) | 5 | 55 | 34 | 5 | 14 | 1 | 2 | 12 |
| test / diagnostic surface (waits, counters, stats) | 16 | 212 | 141 | 17 | 4 | 19 | 18 | 83 |
| fall-through (unknown operation) | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| **total** | 53 | 831 | 547 | 59 | 52 | 36 | 63 | 337 |

## Every branch

| # | operation(s) | kind | NLOC | decisions | select | validate | json | local | rest |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | ms.named_locator | region configuration | 34 | 26 | 1 | 4 | 2 | 1 | 18 |
| 1 | ms.policy | region configuration | 17 | 15 | 1 | 4 | 0 | 1 | 9 |
| 2 | ms.encryption_key | region configuration | 7 | 6 | 1 | 2 | 1 | 0 | 2 |
| 3 | wire.auth_verify | wire protocol | 3 | 2 | 1 | 0 | 0 | 0 | 1 |
| 4 | wire.register6 | wire protocol | 8 | 9 | 1 | 0 | 0 | 0 | 8 |
| 5 | wire.request6 / wire.request4 / wire.request6 | wire protocol | 1 | 2 | 3 | 0 | 0 | 0 | -1 |
| 6 | wire.register4_notify / wire.register_notify | wire protocol | 15 | 16 | 2 | 0 | 0 | 0 | 14 |
| 7 | wire.register4 | wire protocol | 8 | 9 | 1 | 0 | 0 | 0 | 8 |
| 8 | wire.etr_request4 / wire.etr_request6 | wire protocol | 10 | 5 | 2 | 1 | 0 | 0 | 2 |
| 9 | etr_map_server.add | region configuration | 14 | 12 | 1 | 4 | 3 | 1 | 3 |
| 10 | etr_map_server.delete | region configuration | 5 | 2 | 1 | 0 | 0 | 1 | 0 |
| 11 | etr_map_server.get | region configuration | 4 | 2 | 1 | 0 | 0 | 0 | 1 |
| 12 | etr_map_server.wait | test / diagnostic surface | 9 | 9 | 1 | 0 | 2 | 2 | 4 |
| 13 | etr_liveness.start | native participant roles | 11 | 10 | 1 | 5 | 0 | 0 | 4 |
| 14 | etr_liveness.stop | native participant roles | 5 | 3 | 1 | 0 | 0 | 0 | 2 |
| 15 | etr.identity | region configuration | 9 | 8 | 1 | 7 | 0 | 1 | -1 |
| 16 | ms_governor.start | native participant roles | 9 | 5 | 1 | 4 | 0 | 1 | -1 |
| 17 | etr_decision.wait | test / diagnostic surface | 17 | 12 | 1 | 2 | 1 | 1 | 7 |
| 18 | ram.count | test / diagnostic surface | 4 | 2 | 1 | 0 | 0 | 1 | 0 |
| 19 | etr_registrar.start | native participant roles | 26 | 15 | 1 | 5 | 1 | 1 | 7 |
| 20 | etr_registrar.stop | native participant roles | 4 | 1 | 1 | 0 | 0 | 0 | 0 |
| 21 | etr_registrar.notified | test / diagnostic surface | 7 | 3 | 1 | 0 | 0 | 1 | 1 |
| 22 | etr_registrar.notify_wait / etr_registrar.state_wait | test / diagnostic surface | 18 | 17 | 2 | 1 | 3 | 1 | 10 |
| 23 | etr_registrar.sent | test / diagnostic surface | 11 | 5 | 1 | 0 | 0 | 1 | 3 |
| 24 | etr_registrar.wait | test / diagnostic surface | 15 | 13 | 1 | 1 | 3 | 1 | 7 |
| 25 | wire.etr_notify4 | wire protocol | 10 | 4 | 1 | 0 | 0 | 0 | 3 |
| 26 | wire.etr_register4 | wire protocol | 39 | 27 | 1 | 5 | 4 | 4 | 13 |
| 27 | transport.stats | test / diagnostic surface | 8 | 2 | 1 | 0 | 0 | 0 | 1 |
| 28 | system.get | test / diagnostic surface | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| 29 | map_resolver.add | region configuration | 7 | 3 | 1 | 1 | 0 | 1 | 0 |
| 30 | map_resolver.get | region configuration | 10 | 7 | 1 | 0 | 0 | 1 | 5 |
| 31 | map_resolver.delete | region configuration | 6 | 2 | 1 | 0 | 0 | 1 | 0 |
| 32 | map_resolver.wait | test / diagnostic surface | 13 | 7 | 1 | 0 | 1 | 1 | 4 |
| 33 | map_cache.add / database_mapping.add | region configuration | 13 | 6 | 2 | 0 | 0 | 2 | 2 |
| 34 | ddt.add | region configuration | 9 | 2 | 1 | 0 | 0 | 1 | 0 |
| 35 | ddt.delete | region configuration | 11 | 2 | 1 | 0 | 0 | 1 | 0 |
| 36 | ddt.get | region configuration | 28 | 11 | 1 | 0 | 0 | 4 | 6 |
| 37 | site.add | region configuration | 25 | 24 | 1 | 3 | 2 | 1 | 17 |
| 38 | site.delete | region configuration | 18 | 8 | 1 | 0 | 0 | 1 | 6 |
| 39 | registration.put | registration state | 105 | 70 | 1 | 0 | 4 | 6 | 59 |
| 40 | registration.delete | registration state | 14 | 6 | 1 | 0 | 0 | 5 | 0 |
| 41 | resolver.stats | test / diagnostic surface | 16 | 1 | 1 | 0 | 0 | 0 | 0 |
| 42 | database_mapping.wait | test / diagnostic surface | 19 | 14 | 1 | 0 | 2 | 3 | 8 |
| 43 | map_cache.wait | test / diagnostic surface | 20 | 14 | 1 | 0 | 2 | 3 | 8 |
| 44 | resolver.wait_ddt | test / diagnostic surface | 11 | 7 | 1 | 0 | 1 | 1 | 4 |
| 45 | resolver.wait_site | test / diagnostic surface | 11 | 7 | 1 | 0 | 1 | 1 | 4 |
| 46 | resolver.wait | test / diagnostic surface | 32 | 27 | 1 | 0 | 3 | 1 | 22 |
| 47 | resolution.get | registration state | 54 | 43 | 1 | 1 | 0 | 1 | 40 |
| 48 | map_cache.delete / database_mapping.delete | region configuration | 18 | 7 | 2 | 0 | 0 | 2 | 3 |
| 49 | map_cache.list | region configuration | 16 | 7 | 1 | 0 | 0 | 3 | 3 |
| 50 | database_mapping.get | region configuration | 17 | 16 | 1 | 1 | 0 | 2 | 12 |
| 51 | map_cache.get | region configuration | 28 | 13 | 1 | 1 | 0 | 2 | 9 |
| 52 | (unknown operation) | fall-through | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
