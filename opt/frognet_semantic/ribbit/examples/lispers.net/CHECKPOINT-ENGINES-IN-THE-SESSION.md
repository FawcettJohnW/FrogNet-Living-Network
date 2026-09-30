# [ENGINES_IN_THE_SESSION_V1] + [HIGH_SPEED_DATA_SOCKET_V1] + [DATA_MARKER_V1] -- v0.52, 2026-09-26

frogram::Session runs fnwp::ClientEngine; ram_server.cpp runs fnwp::ServerEngine in its socket loop. Three outbound
connections per session: HELLO (requests), HELLO RETURN:<token> (replies), HELLO DATA:<token> (the high-speed data
socket). The RAM interface endpoint is the vendor's ([RAM_INTERFACE_IS_THE_VENDORS_V1]): Session(host, port, api),
ram_server --api.

## The rules, as John gave them
- References move in WIRE ORDER, as the Python proxy and daemon move them. The client commits a request reference as
  the frame leaves; the server commits it as the frame arrives; the server commits a response reference as the reply
  goes on the return socket (encode and send under one lock); the client commits it as the reply is read.
  The tree as found had fnwp::InOrder: replies released per (template, coordinates) strictly in request order. A
  parked held read held every later reply on its key, including the write that would have woken it -- fnw1 deadlocked
  at test_map_resolver_add_get_delete. Removed.
- [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]: a frame goes whole on the semantic socket only if it drains within 20 ms at
  the rate that socket is achieving (floor one segment, cap half the send buffer).
- [DATA_MARKER_V1]: a payload that cannot be differenced (REQ_RAW / RESP_RAW) and exceeds the ceiling leaves its
  MARKER on the control socket in wire order -- the same FNWP frame with no body: a REQ_RAW whose request JSON carries
  "data_bytes", a RESP_RAW whose headers JSON carries X-FrogNet-Data-Bytes -- and its body goes on the data socket
  under the same sequence, in 1368-byte segments, interleaved FAIRLY (round robin) with every other transfer in
  flight, reassembled by sequence at the far end. Not for speed: so every other message keeps getting through.
  A marker'd request executes on its own thread once its body lands; a marker'd reply is applied on its own thread
  once its body lands, and only LATER replies to that same instance wait for it (the instance's RAM-answer template
  is that body). Nothing else on the session waits.

## [BODY_FIELD_SET_IS_PART_OF_THE_IDENTITY_V1] -- core change, Python and C++ (John: "Fix both. Do the Python too")
Found with FROGRAM_TRACE on the deadlocking test: a semantic write of
  {"service":"lisp","variable":"map-resolver","instance":"192.0.2.254","bag":{"address":...,"active":false}}
was executed by the far end as
  {"bag":{"source_id":false},"instance":"192.0.2.254","service":"192.0.2.254","variable":"lisp"}, status 200.
Two bag shapes shared one template key (POST /ram.php?__dyn=op); the JSON handler flattens the bag, so each shape
is a different field order; both sides learn from RAW exchanges at different moments (client at the reply, server at
execution), so with writes in flight the encoder and decoder held different templates for one opcode. The Python has
the same race (proxy learns at reply, the far node's proxy learns as it serves). Fix, the rule already made for URL
keys ([DYNAMIC_KEY_SET_IS_PART_OF_THE_IDENTITY_V1]): a JSON object body's field paths are folded into the template
key as &__body=<crc32 of the paths>. proxy/templates.py canonical_semantic_key + semtpl::canonical_semantic_key.
S3: 68,878 cases, 0 differ. Opcodes of JSON-bodied POSTs change; the first request on a new opcode is one RAW.

## [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1] -- C++ server
A RESP_RAW answer to a semantic request is now cached under the request hash (as the daemon's _build_raw_reply does),
so a REQ_REPEAT of it is RESP_SAME when unchanged, not REQ_MISS + FULL. e2e seeds 1/2/3: REQ_MISS 6/7/20 -> 0/0/0.

## Also
- tools/qualify.sh builds ribbit_cpp/ram-server from tools/ramsrv/ram_server.cpp + frogram.cpp in the build stage
  (the 2026-09-25 prebuilt could not speak to the new session: 49 errors).
- FROGRAM_TRACE=1 (stderr) or FROGRAM_TRACE=<file>: every frame out and in, both ends.
- The e2e workload with body-shape templates: REQ_RAW 211 -> 8, REQ_MISS 6 -> 0, bytes out 100,396 -> 71,824 (seed 1).

## Proof (this container, results/ dated 2026-09-26 18:2x-18:4x)
build; fnw1 51 tests OK, four consecutive runs (the test that hung: 10/10); independent 10 x 3/3 + cross_process;
handler PASS; stress 3 x 600 rounds, 0 partial applications; lockfree PASS; dataplane: session over TCP on /ram.php
and /Fawcett.RibbitLISP.ram_interface.php PASS, fan-in over the shaped wire PASS (slowest small op 40 ms, mean 22 ms,
idle 20 ms; 250 KB completes at 234 ms while 6 MB takes 1,632 ms); fnwp e2e seeds 1/2/3 PASS on both endpoints;
semtpl S3 68,878 cases 0 differ against the changed Python. FrogSim on the Python tree: discovery 83/83, every tier
identical to baseline; instance-references and same-reference oracles PASS.
Not run: semcodec (its mutation stage exceeds the container's per-command limit), perf, bytes, oracle (needs
LISPERS_ROOT).
