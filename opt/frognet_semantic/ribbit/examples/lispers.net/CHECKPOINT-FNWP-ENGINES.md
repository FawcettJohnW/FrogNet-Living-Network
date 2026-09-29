# FNWP engines on the RAM wire: C++ client (S4) <-> C++ server (S5), end to end GREEN

ribbit_cpp/fnwp_engine.hpp -- ClientEngine (proxy/transport_semantic.py per request) and ServerEngine
(daemon/engine/session.py per session, the RAM op as the engine), transport-agnostic, built only from pieces already
proven byte-identical to John's Python: S1 frames, S2 exact codec, S3 templates, ramrows, S4a request building, S4b
reply application, S5a reply decision. Reference rules and departures: S4-S5-SPEC.md and the header's comment.

## Proof (tools/test_fnwp_e2e.cpp; the RAM op is ram_server.cpp's own api(), compiled in)
Seeds 1/2/3 (artifacts/fnwp-e2e-green.txt):
- 2,000 answers per seed through the engines (writes, one-member rewrites, new shapes, removes, four reads each step);
  every read byte-identical to api()'s own answer at that moment. Frames seen: REQ_RAW / FULL / DIFF / REPEAT,
  RESP_SAME / DIFF / RAW, REQ_MISS -- the engines recover from REQ_MISS and from shape changes.
- Storm: 500 cells, one member of one cell rewritten, the same read 200 times: every answer exact; 14,800 B in for 200
  changed answers of ~71,576 B each (reduced wire 14.3 MB): about 74 B per changed answer, ~967x less.
Bugs of mine found on the way, fixed: WireMsg holds views into the frame, and the client parsed a temporary (dangling);
the server treated REQ_REPEAT as REQ_FULL, clearing its reply reference (FIX 15 is REQ_FULL only), so every repeat
carried every field (the first storm run: 13 KB per answer).

## Found, for John's decision (not changed)
- References and templates are keyed per (target, opcode); /ram.php's keys are all dynamic, so reads of different
  variables share one opcode. Mixed reads thrash the RAM-read template: in the workload 1,088 of 1,788 reads went RAW.
  Keying RAM-read references by the full request would hold them -- but it changes what FNWP keys on.
- The JSON handler's extract keeps only the template's fields: a request body with other keys loses them (the daemon
  rebuilds a different body). The C++ client proves every extraction by rebuilding it and sends RAW when it does not
  hold. CONFIRMED against the running Python (core/json_handler.py + core/template.py): a template learned from
  {"bag":{"n":1,"s":"x"}} extracts bag.n and bag.s from {"bag":{"n":2,"s":"y","extra":"LOST?"}}, and rebuild_body gives
  {"bag":{"n":2,"s":"y"},...} -- "extra" is silently dropped. Any POST whose body shape differs from the one learned
  loses data through the proxy/daemon today.

## Not yet
The engines are not yet inside frogram::Session / the ram-server socket loop; not yet run against the Python proxy /
daemon; the MySQL template store is not plugged in (in-memory); REQ_RAW's request hash is not yet matched to the proxy's.
