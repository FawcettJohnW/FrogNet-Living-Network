# RAM-answer handler GREEN (ribbit_cpp/ramrows.hpp) -- FNWP field diffs for RAM read answers

Spec: RAM-ANSWER-HANDLER-SPEC.md. The template is the reference answer both ends hold: learn(answer) -> per row its
address and bag shape, and a field order ( <n>/id, <n>/updated, <n>/bag/<path>... ); extract(answer, template) -> fields
in that order, or std::nullopt when the shape differs (address set, or a bag's object structure) -> RESP_RAW;
rebuild(template, values) -> the answer text byte for byte, rows re-sorted by id, rendered with copies of ram_server.cpp's
row_json() and dump(). Numbers travel as float64 (the RAM stores doubles), updated_epoch as its "%.3f" text, arrays as
typed JSON; a bag with duplicate keys cannot be a template (every answer to that read goes RAW).

## Proof (tools/test_ramrows.py against the REAL ram-server; FNW1 client reads exact answer text)
Random workload: writes, one-member rewrites, new shapes at an address, removes and re-adds; nested bags, arrays, ints,
floats (0.1, 1e-12, 2^53+1, 1e16), unicode, quotes, backslashes, bools, nulls. For every consecutive answer pair to each
of four reads: held -> rebuild(learn(ref), decode_reply(encode_reply_diff(extract(new), ref))) == new, byte for byte;
not held -> the shape really changed.
- Red (artifacts/ramrows-red.txt): every pair failed (throwing header).
- Green (artifacts/ramrows-green.txt), seeds 1/2/3, 300 steps, 1,196 pairs each: held 889/891/921, ALL exact;
  RAW 307/305/275, ALL real shape changes; 0 errors.
- Effect, answers that changed and were held: 3.3% / 3.2% / 3.1% of the answer bytes (30-32x less), with small answers
  (~6 rows). Unchanged answers are RESP_SAME. The storm case (one member of N cells) is measured in S4.
