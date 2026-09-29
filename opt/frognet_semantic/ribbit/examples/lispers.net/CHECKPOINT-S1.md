# Semantic-engine port S1 — FNW1 v4.1 wire framing — GREEN

Base: v0.45.1-realmachine. Red: CHECKPOINT-S1-red.md. Reference: John's `core/semcache_wire.py` (465 lines) in
frognet-source-20260914_happydog.tgz (fc49efc3bf7803a03bced78726138f5ae493c9fbdbaf1396bd51f795033084d3).

## What S1 is
`ribbit_cpp/semwire.hpp`, header only: every opcode of FNW1 v4.1 — REQ_FULL, REQ_REPEAT, REQ_RAW, REQ_DIFF, RESP_DIFF,
RESP_SAME, RESP_RAW, REQ_MISS, ERROR, SEQ_RESET, HELLO, RTT_PING, RTT_PONG, RTT_LOOP — as `wrap_*`, `try_parse`,
`is_fnw1` and `op_name`, with the Python's semantics: None for non-FNW1, ValueError (same text) for malformed FNW1,
trailing bytes ignored, unknown ops returned carrying only the op, RTT_PING's pad dropped with an empty payload,
RESP_RAW's payload field left unset. `try_parse` returns views into the frame; nothing is copied.

Scope is the inner frame only. The outer 4-byte length prefix and the socket belong to the transport (S4). Nothing
uses semwire yet: `frogram.cpp` and the RAM server are unchanged and still speak the reduced FNW1.

## Qualification (artifacts/qualify-S1-SUMMARY.txt, one tools/qualify.sh run, every stage)
- S1 oracle: wrap 4,768 cases, parse 538,276, is_fnw1 5,000, op_name 256, round trip C++ -> Python 4,273 and
  Python -> C++ 4,273: 0 differ (artifacts/semwire-green-S1.txt).
- Oracle sensitivity: 8 of 8 deliberate one-line bugs caught (tools/semwire_mutants.sh; artifacts/semwire-mutants-S1.txt).
- ASan+UBSan driver on the full corpus: PASS. Seeds 1-5 at 500,000 fuzz frames each: PASS
  (artifacts/semwire-green-S1-sanitizers-seeds.txt).
- Existing suite, unchanged code: build PASS (-Wall -Wextra -Werror for the driver); local 50 PASS / 1 SKIP of 51;
  clean FNW1 50 PASS / 1 SKIP of 51; all ten independent-process tests 3/3; cross_process_ram PASS; Dino byte oracle
  4/4; atomicity stress 3 x 600, 0 partial; pointer_atomic_lock_free=YES; born=100001 dead=100001; source gates 0/0;
  no line over 200 in ribbit_lisp.cpp or any S1 source; package_check PASS.
- The handoff reported 49 PASS / 1 SKIP; this run's unittest counts are 51 run, 1 skipped. No test file changed
  against v0.45.1.

## Performance (artifacts/performance-S1.txt; measured, single thread, in memory, this 1-core container, not AI-Host)
Median ns per operation, C++ / Python reference:
- wrap_req_full 64 B 47.9 / 367; 4 KiB 84.8 / 509; wrap_req_repeat 36.3 / 243; wrap_resp_same 36.0 / 243;
  wrap_resp_raw 40 B headers + 1 KiB body 81.2 / 729; wrap_rtt_pong 62.0 / 365.
- try_parse REQ_FULL 64 B 31.7 / 1,056; 4 KiB 32.4 / 1,242; REQ_REPEAT 16.8 / 791; RESP_SAME 17.0 / 837;
  RESP_RAW 34.2 / 1,739; RTT_PONG 28.0 / 1,150.
What these are not: end-to-end latency, socket cost, or anything about bytes on the wire (S1 does not change a byte
any participant sends). C++ parse is size-independent because it does not copy; Python's slices copy.

## Differences of representation (not behaviour), stated in the header
- Python takes str for wrap_error's message and wrap_hello's return_ip; C++ takes bytes (the UTF-8 / ASCII).
- C++ integers are unsigned 64-bit, so Python's rejections of negative or >= 2^64 values have no reachable input.
- struct.error and UnicodeEncodeError are matched by kind; their text is CPython's, not John's, and is not compared.

## Files
New: ribbit_cpp/semwire.hpp, tools/test_semwire_oracle.py, tools/semwire_driver.cpp, tools/semwire_mutants.sh,
tools/bench_semwire.cpp, tools/bench_semwire_python.py, artifacts listed above. Changed: tools/qualify.sh (build lines
for the driver and bench; `semwire` stage behind FROGNET_SEMANTIC_ROOT; S1 timings in `perf`),
RUN-ON-A-REAL-MACHINE.md (the new stage and variable).

## Next: S2, codec v5 (`core/codec.py`, 487 lines) against the same oracle method; needs liblz4.
- bec9d3bd7d799947e47e0f56dc04b22e72e0f175cb7269ddbd99d95907c81361  ribbit_cpp/semwire.hpp
- e6a0935067028ffdd6c80a510e8e5d463bf35118a7673858e475761c1c100531  tools/test_semwire_oracle.py
- ddb43fced4dbc88351388fef49c99f04beef00598031a5f6595a8272d8d22cff  tools/semwire_driver.cpp
- e58f9855972c19f85d1fceda8db805287e6edaf454f27e703628ff391c149a2d  tools/semwire_mutants.sh
- fb1fc1ba1138436df3cef66da16de6c29bfacf12953bf53206dbe0ea16c7a4ca  tools/bench_semwire.cpp
- 17a155d6d58af377cc28773406e5dd13de669d10a1f5c5d9161ef5c8c2dc7955  tools/bench_semwire_python.py
- 43a5bbdbfacf7638c32b6ff5bb4665a6d1af348184ad0ad23cf4c87c5b37f9f6  tools/qualify.sh
