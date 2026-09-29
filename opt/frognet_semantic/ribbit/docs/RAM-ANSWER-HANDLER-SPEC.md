# RAM-answer handler -- FNWP field diffs for RAM read answers

John, 2026-09-25: the semantic mechanism belongs on the RAM wire, both ends; use FNWP as it exists (S1 frames, S2 codec);
"diff is more than delete". No invented frames. This is the handler that gives a RAM read answer a field list, so the
codec's RESP_DIFF carries field-level changes instead of the whole rows array.

## Why the generic JSON handler is not enough here
A read answer is {"ok":true,"rows":[cell, ...]}. Learned by the JSON handler (training cuts to one row), its field order
is ["ok", "rows"] with rows an ATOMIC array: any change to any cell re-sends every cell. Correct, and useless for the
storm case (many readers re-reading the same cells).

## The handler (mode "ram_rows", a C++ UnRESTHandler codec slot; S4 client and S5 server both use it)
Template = the reference answer both ends hold for that request (the answer last sent / received on that session). No
learning, no MySQL row: both ends derive the same field order from the same reference, deterministically.
- learn(answer) -> fragment: the rows in answer order, each keyed by its address (service, variable, instance); per row
  the fields  <n>.id (int), <n>.updated_epoch (the server's "%.3f" text, str), and <n>.bag.<path> for every member of the
  bag, nested objects flattened with dots as json_handler does, arrays atomic (their JSON text as the server wrote it).
  n is the row's position in the reference.
- extract(answer, fragment) -> fields, or "cannot hold" (std::nullopt, as S3) when the answer's SHAPE differs: a different
  address set, or any bag whose member paths differ (added, removed, reordered). Cannot-hold is the engine's existing
  path: RESP_RAW, and the RAW answer becomes the new reference.
- rebuild(fragment, values) -> the answer text byte for byte: rows re-sorted by their (possibly new) ids, each row
  rendered exactly as ram_server.cpp row_json() does, each bag exactly as ram_server.cpp dump() does.

## Values, chosen so the rebuild is exact
- Bag numbers travel as TYPE_FLOAT (float64): the RAM stores every number as a double and prints it with its own dump()
  ("%.0f" when integral and |x| < 9e15, else "%.17g"); the rebuild prints the same double the same way.
- Strings TYPE_STR, true/false TYPE_BOOL, null TYPE_NULL, arrays TYPE_JSON (the server's dump text), ids TYPE_INT,
  updated_epoch TYPE_STR (the "%.3f" text).
- The codec's exact _values_equal (EXACT_DIFF_V1) decides what changed: a field is sent iff it differs.

## On the wire (FNWP, unchanged)
REQ_FULL / REQ_REPEAT / REQ_DIFF from the client (url_vals: the read's query keys as S3 keys them -- all dynamic, since
none of /ram.php's keys is a static template key); RESP_SAME / RESP_DIFF (semcodec encode_reply_diff over the handler's
fields against the reference) / RESP_RAW (first answer, or cannot-hold) / REQ_MISS from the server.

## Proof it must pass (red first)
1. Exactness against the real RAM server: random workloads (writes, rewrites, removes, nested bags, arrays, numbers of
   every kind); for every consecutive pair of answers (ref, new) to the same read: extract(new, learn(ref)) is either
   cannot-hold -- and then the shapes really differ -- or fields whose rebuild equals the new answer's text byte for byte.
2. Effect: the storm case (N cells, one member of one cell changes) is one field plus that cell's id and updated_epoch;
   bytes measured against RESP_RAW on the same workload.
3. Round trip through semcodec: encode_reply_diff(fields, reference) -> decode_reply -> rebuild == the answer.

## Findings in the RAM server this depends on (reported, not changed here)
- Bag numbers are stored as doubles: an integer above 2^53 is stored changed (9007199254740993 reads 9007199254740992);
  a non-integral number is re-printed "%.17g" (a writer's 0.1 reads 0.10000000000000001).
- urldec() calls std::stoi(..., 16) on whatever follows '%': a query with "%zz" throws there; whether anything catches it
  is not yet verified.
