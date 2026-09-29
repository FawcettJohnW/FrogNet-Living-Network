# [INSTANCE_REFERENCES_V1] FNWP engines: one template per format, many instances in each side's local cache

John, 2026-09-26: the template defines the generally recognized format; each side's cache (local only, nothing shared)
recognizes multiple individual instances of the template. An instance is the template plus the location in the message's
data -- on the RAM wire the (service, variable, instance) the call names. A message is differenced against the last
message for the SAME location. The location travels with every difference so the receiver picks the matching entry from
its own local cache before applying the differences.

## What changed (ribbit_cpp/fnwp_engine.hpp only)
- Template gains loc_fields: service / variable / instance, whichever the /ram.php template carries (URL for reads, body
  for writes). A template with none (remove, every non-RAM template) has one instance: v0.50 behaviour exactly.
- Request references, response references and the RAM-answer template are keyed (opcode, location), both engines.
- The client sends the location fields in every REQ_DIFF even when unchanged (re-encodes against the reference without
  them; build_request itself is untouched, so S4a stays byte-identical to the proxy). REQ_REPEAT and REQ_FULL unchanged.
- The server decodes the explicit fields first, takes the location, then that instance's reference. A frame without a
  location field raises (protocol error), never guessed.
- [SAME_MOVES_REFERENCE_V1] On RESP_SAME the client now sets its response reference to the answer the SAME names, as
  the server's reply decision (daemon_reply, same as the Python daemon) does on its side. Without it the next RESP_DIFF
  was decoded against a different reference than it was encoded against: seed 1 crashed on it once instances were kept
  apart. The Python proxy has the same omission -- see Findings.
- [RAM_READ_REQUEST_TEMPLATE_V1] A RAM read learns its request template even when the answer has no rows yet. S3 refuses
  to learn from an empty array; for a RAM read the answer is held by ramrows, not S3's reply template, so the refusal was
  blocking the request template and every read of a not-yet-written tuple went REQ_RAW (58 of 59 excess RAWs, seed 2).
- Client RESP_RAW takes the answer as the instance's template only when status < 400, as the server does.

## Proof (tools/test_fnwp_e2e.cpp, now a qualify.sh stage "fnwp")
Red on v0.50 (artifacts/instance-refs-RED.txt): reads answered RAW 1,093 / 1,142 / 1,113 against 466 / 462 / 460 justified.
Green (artifacts/instance-refs-GREEN.txt, artifacts/qualify-fnwp-stage-INSTANCE-REFS.txt), seeds 1 / 2 / 3:
- every answer byte-identical to api()'s own, 2,000 per seed;
- reads answered RAW 466 / 462 / 460 = reads whose location was new or whose answer changed shape against that same
  location's previous answer, exactly; plus 1 per seed named separately: a RESP_SAME naming a body the client's
  256-entry SAME cache had evicted, recovered by REQ_RAW exactly as the proxy's [SAME_MISS_AUTORETRY_V1];
- seed 1 bytes: in 1,401,599 -> 917,439 (-35%), out 131,084 -> 100,396 (-23%); RESP_SAME 356 -> 769;
- storm unchanged: 14,800 B for 200 changed 500-cell answers (74 B each).

## Findings for John
1. The Python proxy does not move its response reference on RESP_SAME; the daemon does (it sets it to the SAME'd
   answer). They agree only while the SAME'd body is also the proxy's last decoded answer for that (peer, opcode).
   Under per-opcode keying a SAME can name an older answer of another request sharing the opcode, and the next
   RESP_DIFF is then applied to a different reference than it was encoded against. Found by reading
   transport_semantic.py's RESP_SAME branch against daemon_reply; not yet reproduced against the running Python.
2. The SAME cache is an LRU of 256 bodies keyed by same_id. Keeping each instance's current answer as its cache entry
   (your description: "the current response becomes the cache entry") would remove the eviction recovery, but the
   server's REPEAT cache is not refreshed on RESP_RAW, so a SAME can name an answer older than the instance's current
   one. Not changed; needs your decision.

## Not run here
qualify.sh's full run was killed by the container inside the semcodec mutation stage (untouched code). Before that:
build, local, fnw1, independent, semwire, semcodec (715,366 cases) all PASS. semtpl, handler, stress, lockfree, perf and
bytes were not rerun; none of them compiles fnwp_engine.hpp, the only source changed.

## Next
Engines inside frogram::Session and the RAM server's socket loop; the Python proxy/daemon on the same instance rule;
the MySQL template store; REQ_RAW's request hash matched to the proxy's.
