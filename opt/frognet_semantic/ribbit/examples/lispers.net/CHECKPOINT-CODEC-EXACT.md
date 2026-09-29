# Exact diff: core/codec.py _values_equal, and the S2 C++ codec with it

John, 2026-09-25: exact comparison, both ends. Oracle root for S2 from here: John's RUNNING tree (frognet-source-20260924,
restored by his revert) + codec-exact-20260925.tgz. The no-fallbacks overlay is NOT the oracle.

## Python (codec-exact-20260925.tgz: core/codec.py, core/test_codec_exact_diff_oracle.py -- whole files)
- _values_equal: same type, floats bit for bit, dict keys in order, lists element-wise; no tolerance. It compared floats
  within an ABSOLUTE 1e-9 and int/float of equal value as equal; a change judged equal is never sent, so 1e-12 -> 2e-12
  never arrived. Encoder-only: a fixed sender and an unfixed receiver interoperate; no frame changes.
- decode_error_reply: None only for "not an error frame"; an error frame whose status does not parse raises. Its only
  production caller (daemon/engine/session.py x3) decodes its own engine's frames, which always carry an int status.
- Oracle: 8 FAIL on the running codec.py (float below 1e-9, 1e-12 -> 2e-12, 0.0 -> -0.0, int 1 <-> float 1.0 both ways,
  a nested float in a JSON field, reordered keys, malformed error status); 11/11 PASS fixed. Existing oracles
  test_engine_error_reason_oracle and test_codec_int64_oracle still pass.
- simulation/run_all.py: identical to the unmodified-tree baseline -- the same two environmental failures (collectives:
  torch not installed; test_clean_install_oracle: the tarball lacks usr/local/lib/frognet_log.sh, frognet_trace.sh).

## C++ (ribbit_cpp/semcodec.hpp values_equal)
- Exact, as the Python. Red: the previous header against the fixed codec.py differs in enc_rep_diff 647, enc_req_diff
  647, veq 257. Green: 715,366 cases, 0 differ (artifacts/semcodec-S2-exact-green.txt).
- Oracle vocabulary: MemoryError (Python) and std::bad_alloc (C++) are one kind, "memory": a fuzzed LZ4 frame declaring a
  content size neither side can allocate. FINDING: both sides try to allocate a size the frame declares.
- Mutants: the dead "tolerance 1e-6" mutant replaced by "float equality by value" (caught: 112/112/31) and "dict key order
  ignored" (caught, thinly: 1/1/2 cases). The full mutant set was not re-run.

## Superseded, not to be deployed
ram-server-cells-20260925.tgz and ribbit-lisp-v0.46-cells.zip (opcodes 0x05/0x15): rejected -- a set difference, not a
diff. The RAM wire will carry FNWP as it exists: semwire (S1) frames, semcodec (S2) diffs.
