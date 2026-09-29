# Matched audit -- lispers.net 0.643 and Ribbit-LISP: the same job, the same rules

Produced by `tools/matched_audit.py`. The rules were fixed in the script before measuring; they are
reproduced here. Every function on both sides, in or out and why, is in `lispers-functions.csv` and
`ribbit-functions.csv`.

```

 1. The job is the LISP map-server and map-resolver as the acceptance suite exercises it: Map-Register (with
    authentication and encryption), Map-Request (ECM and bare), Map-Notify and Map-Notify-Ack, Info-Request, the
    Map-Referral a map-server sends as DDT authority, sites and their options, policies, named locators (geo, ELP,
    RLE, JSON), registration state and its expiry, and the configuration commands that set these up.
 2. Out on both sides, with the reason recorded per function: other roles (ITR, ETR, RTR, the data plane, a
    map-resolver following referrals as a DDT client), features neither the tests nor Ribbit exercise (crypto-EID
    signatures, pub-sub, eid-crypto-hash, NAT-traversal proxying), and operator surfaces (show, debug, API, web UI,
    lig/rig, test waits and statistics).
 3. Ribbit's in-process backend -- the no-RAM branch of an `if(ram)` chain, which exists only for the `local` test
    stage -- is out. Where an Engine function mixes backends, only its no-RAM branches are removed and the rest is
    measured; where a chain tests only `resident`, nothing is removed (conservative: counted against Ribbit).
 4. Reachability decides what is in: lispers.net from its map-server, map-resolver and lisp-core packet and
    configuration entry points; Ribbit from its front, its RAM-server-hosted operation and the in-scope branches of
    Engine::dispatch(). Python calls are resolved per module, per class (self), and for other method calls by
    rapid type analysis (a method is reachable when its class is instantiated in reachable code and its name is
    called there). C++ calls are resolved by name within the application files.
 5. Ribbit's FrogNet machinery -- the client library, FNWP, the semantic codec and templates, the memory, EBR, the
    data plane, the RAM server outside its LISP operation -- is the PLATFORM, reported in a second row, whole
    (not filtered by reachability). lispers.net's Python runtime and standard library are not counted; its own
    infrastructure in lisp.py (IPC, sockets, timers) is its code and counts in its application row.
 6. One tool, one setting: lizard with defaults, functions only (module-level code is not a function on either
    side). Where Rule 3 removes branches from a Ribbit function, its NLOC and CCN are recounted with lizard's own
    counting rule (if for while case catch && || ?), which the script checks against lizard on every function it
    does not alter.
 7. The secondary classification (LISP semantics / coordination and state machinery / configuration) is by name
    rules listed in the report; it is judgment, published line by line, and not part of the primary result.
```

## Result

| | functions | NLOC | total CCN | average CCN | functions over CCN 15 |
|---|---:|---:|---:|---:|---:|
| lispers.net 0.643 -- application (Python) | 360 | 6463 | 2237 | 6.2 | 26 |
| Ribbit-LISP -- application (C++) | 175 | 1886 | 1175 | 6.7 | 12 |
| Ribbit-LISP -- application + FrogNet platform (C++) | 568 | 4972 | 3283 | 5.8 | 43 |
| (the FrogNet platform alone) | 393 | 3086 | 2108 | 5.4 | 31 |

Python and C++ lines are not the same unit (C++ carries braces and declarations); CCN counts decisions in
both and compares more directly.

## Ribbit's Engine::dispatch(): operations out of the job

| operation(s) | reason | NLOC | decisions |
|---|---|---:|---:|
| wire.auth_verify | test / diagnostic surface: not in the job | 3 | 2 |
| wire.etr_request4/wire.etr_request6 | other role (ETR/ITR): not in the job | 10 | 5 |
| etr_map_server.add | other role (ETR/ITR): not in the job | 14 | 12 |
| etr_map_server.delete | other role (ETR/ITR): not in the job | 5 | 2 |
| etr_map_server.get | other role (ETR/ITR): not in the job | 4 | 2 |
| etr_map_server.wait | test / diagnostic surface: not in the job | 9 | 9 |
| etr_liveness.start | other role (ETR/ITR): not in the job | 11 | 10 |
| etr_liveness.stop | other role (ETR/ITR): not in the job | 5 | 3 |
| etr.identity | other role (ETR/ITR): not in the job | 9 | 8 |
| ms_governor.start | native FrogNet registration governor: not the wire job | 9 | 5 |
| etr_decision.wait | test / diagnostic surface: not in the job | 17 | 12 |
| ram.count | test / diagnostic surface: not in the job | 4 | 2 |
| etr_registrar.start | other role (ETR/ITR): not in the job | 26 | 15 |
| etr_registrar.stop | other role (ETR/ITR): not in the job | 4 | 1 |
| etr_registrar.notified | test / diagnostic surface: not in the job | 7 | 3 |
| etr_registrar.notify_wait/etr_registrar.state_wait | test / diagnostic surface: not in the job | 18 | 17 |
| etr_registrar.sent | test / diagnostic surface: not in the job | 11 | 5 |
| etr_registrar.wait | test / diagnostic surface: not in the job | 15 | 13 |
| wire.etr_notify4 | other role (ETR/ITR): not in the job | 10 | 4 |
| wire.etr_register4 | other role (ETR/ITR): not in the job | 39 | 27 |
| transport.stats | test / diagnostic surface: not in the job | 8 | 2 |
| system.get | test / diagnostic surface: not in the job | 1 | 1 |
| map_resolver.add | other role (ETR/ITR): not in the job | 7 | 3 |
| map_resolver.get | other role (ETR/ITR): not in the job | 10 | 7 |
| map_resolver.delete | other role (ETR/ITR): not in the job | 6 | 2 |
| map_resolver.wait | test / diagnostic surface: not in the job | 13 | 7 |
| map_cache.add/database_mapping.add | other role (ETR/ITR): not in the job | 13 | 6 |
| ddt.add | map-resolver as DDT client (delegations): not in the job | 9 | 2 |
| ddt.delete | map-resolver as DDT client (delegations): not in the job | 11 | 2 |
| ddt.get | map-resolver as DDT client (delegations): not in the job | 28 | 11 |
| resolver.stats | test / diagnostic surface: not in the job | 16 | 1 |
| database_mapping.wait | test / diagnostic surface: not in the job | 19 | 14 |
| map_cache.wait | test / diagnostic surface: not in the job | 20 | 14 |
| resolver.wait_ddt | test / diagnostic surface: not in the job | 11 | 7 |
| resolver.wait_site | test / diagnostic surface: not in the job | 11 | 7 |
| resolver.wait | test / diagnostic surface: not in the job | 32 | 27 |
| map_cache.delete/database_mapping.delete | other role (ETR/ITR): not in the job | 18 | 7 |
| map_cache.list | other role (ETR/ITR): not in the job | 16 | 7 |
| database_mapping.get | other role (ETR/ITR): not in the job | 17 | 16 |
| map_cache.get | other role (ETR/ITR): not in the job | 28 | 13 |

## lispers.net: why functions are out

| reason | functions | NLOC | total CCN |
|---|---:|---:|---:|
| not reachable from the job's entry points | 563 | 7915 | 2858 |
| other role (ITR/ETR/RTR): not in the job | 49 | 1727 | 546 |
| operator show/display: not in the job | 69 | 1560 | 409 |
| xTR role (RLOC probing, telemetry, data-plane crypto): not in the job | 31 | 566 | 202 |
| web UI: not in the job | 7 | 318 | 64 |
| LISP-Decent mapping system: not in the job | 15 | 193 | 81 |
| xTR role: Map-Reply processing | 1 | 155 | 65 |
| map-resolver as DDT client (following referrals): not in the job | 1 | 130 | 35 |
| map-resolver as DDT client: not in the job | 4 | 113 | 40 |
| RTR / NAT-traversal proxying: not in the job | 14 | 85 | 47 |
| xTR role: Map-Notify processing | 1 | 84 | 18 |
| debug: not in the job | 8 | 75 | 31 |
| crypto-EID signatures: not in the job | 3 | 68 | 19 |
| lig/rig tools: not in the job | 3 | 65 | 23 |
| xTR role: Info-Reply processing | 1 | 65 | 27 |
| pub-sub (map subscriptions): not in the job | 4 | 62 | 12 |
| xTR role: multicast Map-Notify processing | 1 | 62 | 22 |
| NAT-traversal proxying (RTR): not in the job | 2 | 51 | 10 |
| operator API: not in the job | 1 | 30 | 11 |
| crypto-EIDs: not in the job | 1 | 13 | 7 |

## Secondary: what the in-scope code does (Rule 7 -- name rules, judgment)

coordination/state: names matching `ipc|socket|sock|receive|recv|segment|timer|thread|lock|mutex|queue|strand|worker|session|held|watch|await|applied|snapshot|ebr|retire|checkpoint|restart|fork|spawn|pipe|select|poll|futex|wait|startup|shutdown|process_command|dispatch_packet|parse_packet|loop|reject|log|memory|remote_call|call$|cache`; configuration: `_command$|kv_pair|config`; the rest: LISP semantics.

| | class | functions | NLOC | total CCN |
|---|---|---:|---:|---:|
| lispers.net | LISP semantics | 307 | 5177 | 1750 |
| lispers.net | coordination/state | 42 | 658 | 241 |
| lispers.net | configuration | 11 | 628 | 246 |
| Ribbit-LISP | LISP semantics | 146 | 1593 | 983 |
| Ribbit-LISP | coordination/state | 28 | 273 | 182 |
| Ribbit-LISP | configuration | 1 | 20 | 10 |

## Checks

- lizard and this script's counter agree on 597 of 612 Ribbit functions (the disagreements are in platform code with character literals, where lizard's own numbers are used).
- On the 5 Ribbit functions Rule 3 alters, the counter agrees with lizard on 5 before altering (NLOC and CCN): the recount is lizard's measure.
- lispers.net roots: 17, all found; functions stopped by name: see the CSV.
- The lispers.net source compiles to bytecode identical to the installed 0.643 release: 26 of 26 files.
