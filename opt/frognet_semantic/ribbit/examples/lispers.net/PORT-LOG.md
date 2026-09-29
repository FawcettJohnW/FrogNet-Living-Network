# Semantic-engine port — running log (newest last)

Written as the work goes so an interrupted session can resume from the last line.

- S1 GREEN: FNW1 v4.1 framing, ribbit_cpp/semwire.hpp. CHECKPOINT-S1.md. Package ribbit-lisp-v0.45.2-S1.zip
  8a9a2b71a8bd79a1bb26535560fd570c78cb545940bcaa861cb5936f7b605073.
- S2 characterization: core/codec.py read in full (487 lines). python-lz4 4.4.5 source read: compress() =
  LZ4F_compressFrame with zeroed preferences, contentSize = input length, blockLinked, no checksums, level 0;
  decompress() = LZ4F_getFrameInfo then an LZ4F_decompress loop to end of frame, trailing bytes ignored,
  "Frame incomplete" if the frame does not end. System liblz4 is 1.9.4, the same as python-lz4's bundled copy.
  Python json is the C scanner/encoder (_json); its edge behaviour is taken from the oracle, not assumed.
- S2 RED: CHECKPOINT-S2-red.md.
- S2 GREEN in progress: ribbit_cpp/semcodec.hpp + pyuni_tables.hpp (tools/gen_pyuni_tables.py) pass the oracle, 0 differ.
  First mutant run 11/14 (artifacts/semcodec-mutants-S2-first-run.txt): uppercase \u00XX missed = corpus gap (fixed:
  control chars in JSON-nested strings); surrogate lookahead and fraction-at-end were equivalent mutants (replaced).
  Oracle harness changed after red: empty-bytes formatting, corpus-build crash, float batch, corpus fix.
- S2 verified on final code: oracle PASS 721,496 cases; seeds 1-3 scale 2 PASS; ASan+UBSan PASS; mutants 14/14.
  Performance fixes after first measurement (C++ was slower on LZ4 decode): ASCII-run UTF-8, per-thread reset LZ4
  context, move-out decode, hashed duplicate-key check, bulk JSON escaping. C++ now faster on all 9 benchmarks.
- John approved (2026-09-24): ast.literal_eval in json_handler.rebuild_reply is a declined semantic -- C++ raises loudly
  when a str-typed array value is not JSON; the S3 oracle must exercise and report that category.
- Full qualify.sh run with S2 started; then CHECKPOINT-S2.md, package, clean extract, SHA-256.
- S2 GREEN verified (see CHECKPOINT-S2.md once written): oracle PASS 721,496 cases; seeds 1-3 scale 2 PASS; ASan+UBSan
  PASS; mutants 14/14 on final code; performance fixed (UTF-8 fast path, per-thread LZ4 context reset, hashed
  duplicate-key check, bulk JSON escapes) and re-measured.
- S3 decision (John, 2026-09-24, "OK" to the proposal): ast.literal_eval in json_handler.rebuild_reply is DECLINED.
  The C++ raises loudly when a str-typed array value is not JSON; the oracle exercises and reports that category.
- S2 GREEN checkpointed: CHECKPOINT-S2.md; package ribbit-lisp-v0.45.3-S2.zip.
- Template storage ruling (John, 2026-09-24): templates go in the local MySQL instance on both sides (client and
  server), as the Python does in core/store.py. The C++ uses the same tables and row encoding; no alternative store.
- SEMANTIC-ENGINE-PORT-PLAN.md was missing from the S2 package; restored verbatim from the session that wrote it.
- New oracle source frognet-source-20260924.tgz (c3ae0926...a3a7c7). S1 and S2 were never pinned to source-file
  hashes; re-ran both oracles against it: S1 0 differ, S2 721,496 cases 0 differ
  (artifacts/s1-s2-oracle-vs-source-20260924.txt). Oracle files now pinned: artifacts/oracle-source-20260924.sha256.
- S3 characterization completed (loader.py, text_handler.py, client and server call sites). TEXT/RAW join S3.
  Raised for S4: learn_templates_from_real failure is swallowed at debug in transport_semantic.py.
- S3 RED: CHECKPOINT-S3-red.md. 31 categories, 68,822 cases, all FAIL against the throwing header; declined cases
  detected by hooks on the Python's swallowing sites, 0 oracle errors. train_exception not exercised (stated).
- Ruling (John, 2026-09-24, "Abort"): a template-learning failure after a RAW bootstrap aborts loudly in the C++ client.
  Neither Python swallow is ported: transport_semantic.py line 2896 (except Exception, debug "non-fatal") and
  learn_templates_from_real's own except Exception (declined case train_exception). Applies in S4.
- NO FALLBACKS in John's Python (2026-09-24 ruling): the swallows in the port's files fixed in his Python, simulator back
  to baseline, S2 decode_error_reply raises in C++, S3 red revised to raise-vs-raise. CHECKPOINT-NO-FALLBACKS.md.
- LispHandler (John, 2026-09-25, "Make that a C++ derived class"): ribbit_cpp/unrest_handler.hpp + lisp_handler.hpp;
  Engine moved verbatim to lisp_engine.hpp (identical disassembly). Red 12 FAIL, green 31 PASS, full qualify.sh rc=0
  (artifacts/qualify-LISP-HANDLER-SUMMARY.txt). Two vacuous source gates fixed. CHECKPOINT-LISP-HANDLER.md.
- CODEC EXACT (2026-09-25): _values_equal exact in core/codec.py and semcodec.hpp; S2 oracle root = running tree +
  codec-exact-20260925.tgz. CHECKPOINT-CODEC-EXACT.md. The 0x05/0x15 cells packages are withdrawn.
- S3 GREEN (2026-09-25): semtpl.hpp vs the running Python + codec-exact: 68,878 / 136,122 cases, 0 differ; mutants 14/14;
  learn 2.0x, extract 1.25x, rebuild 1.2x vs Python. RAM server urldec crash fixed ([URLDEC_AS_RAM_PHP_V1]).
  CHECKPOINT-S3-green.md; RAM-ANSWER-HANDLER-SPEC.md is next.
- RAM-answer handler GREEN (2026-09-25): ramrows.hpp; 3 seeds x 1,196 answer pairs from the real ram-server, every held
  answer rebuilt byte for byte through semcodec; changed answers 30-32x fewer bytes. CHECKPOINT-RAMROWS-green.md.
- S4a GREEN (2026-09-25): fnwp_client.hpp build_request byte-identical to the proxy's request (frame, req_hash, new
  reference): 24,854 + 37,276 cases, 0 differ. Proxy/daemon reference rules read and recorded in S4-S5-SPEC.md.
- S5a GREEN (daemon reply decision, repr, body hash, same_id): 6,000 + 20,000 cases. S4b reply side GREEN
  (apply_resp_diff vs _handle_resp_diff): 4,000 + 6,000 cases. Next: the stateful engines, then end to end.
- FNWP ENGINES GREEN end to end (2026-09-25): fnwp_engine.hpp ClientEngine <-> ServerEngine over ram_server's api():
  3 seeds x 2,000 answers exact; storm 74 B per changed 500-cell answer (~967x). CHECKPOINT-FNWP-ENGINES.md.
- INSTANCE REFERENCES GREEN (2026-09-26): one template per format, references per (template, location) in each side's
  local cache; location travels with every difference. Reads RAW exactly = new location or real shape change (was ~2.4x
  that); seed 1 bytes in -35%. Client moves its response reference on RESP_SAME. CHECKPOINT-INSTANCE-REFERENCES.md.
