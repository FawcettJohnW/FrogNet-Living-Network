# Semantic-engine port S3 GREEN -- templates (JSON, TEXT, RAW), detection, path keying, ids

Oracle root: John's RUNNING Python -- frognet-source-20260924.tgz as deployed (his revert restored it) plus
codec-exact-20260925.tgz (core/codec.py only; S3 does not touch the codec). NOT the withdrawn no-fallbacks overlay.

## What is implemented (ribbit_cpp/semtpl.hpp, 850 lines, header only; ribbit_cpp/pyuni_s3_tables.hpp generated)
urllib.parse as the template code calls it (urlparse/urlsplit, parse_qsl/parse_qs keep_blank_values, unquote_plus,
quote_plus, urlencode); proxy/templates.py path keying (normalize_path_for_semantics, extract_dynamic_query_vals,
_with_dynamic_shape, canonical_semantic_key incl. the upsert_by_name body key, _first_row_only, _empty_arrays_in);
core/store.py templateId / opcode (crc32); json_handler typing (_is_ipv4, _looks_like_interface, _looks_like_enum,
_is_plausible_host, _leaf_type with the path rules), _sanitize_nonfinite, json.dumps ensure_ascii (compact and default
separators), infer_schema_preserve_arrays, _merge_schema (Python == across types), _build_field_order_and_type_map,
template_utils.rebuild_json ("rows." rule); the json/text/raw handlers' learn, extract, rebuild through the template
fragment, with blob-backed arrays (sha256 blob store, FROGNET_BLOB_ROOT); RequestTemplate build_url / rebuild_body,
ReplyTemplate content_type; format_registry sniff / detect_request / detect_reply; learn_templates_from_real without its
store. Character behaviour (strip, lower, upper, \w, [a-z0-9] under IGNORECASE, \d) comes from tables generated from the
oracle's own Python (tools/gen_pyuni_s3.py; Python 3.12.3, Unicode 15.0.0).

## Contract where the running Python swallows (C++ never does; the oracle detects each case from the Python's execution)
- extract that cannot hold a body (not JSON; not the template's shape): the Python returns [] and the daemon answers
  RESP_RAW. C++ returns std::nullopt = "send it raw" -- the same outcome, made explicit ("ok H" in the oracle).
- Declined (C++ raises Declined naming it; the Python substitutes): array_literal, array_type, blob_missing,
  json_unparsable (learn), pair_heuristic, text_repr, unknown_mode, train_exception (ruled: abort).
- NotPorted: the xml / html / sotf_media handlers and detection that reaches an XML parse; a URL whose netloc is
  bracketed (ipaddress check) or non-ASCII (NFKC check) -- never the template paths (/api.php?..., /ram.php?...).
- Mirrored as the running Python behaves (flagged, not changed): _looks_like_json decides by parsing; the training cut
  to one row tries json.loads and learns the raw text otherwise; canonical_semantic_key widens the key for an upsert
  body that is not JSON, printing why.

## Proof
- Red (CHECKPOINT-S3-red.md, artifacts/semtpl-red-S3*.txt): the throwing header failed all 31 categories.
- Green: seed 20260924 scale 1 -- 68,822 cases, 31 categories, 0 differ (artifacts/semtpl-green-S3.txt);
  seed 7 scale 2 -- 136,122 cases, 0 differ (artifacts/semtpl-green-S3-seed7-scale2.txt). Blob stores written by learn
  and train into fresh roots are identical, names and bytes.
- Two harness bugs found on the way, both in my oracle/driver, not the port: the learn mode passed as a bare word, and
  an unbalanced-bracket netloc must stay a ValueError (urlsplit checks balance first).
- Mutants (tools/semtpl_mutants.sh, 14): 14 of 14 caught in qualify (artifacts/qualify-S3-batch1-SUMMARY.txt). Getting
  there: the first run caught 11. Two misses were corpus gaps, now closed -- schemas with unsorted keys (as a stored
  template can hold them) and numeric "type" values merged pairwise. The third was an EQUIVALENT mutant: FINDING --
  json_handler._force_host_for_path has no observable effect (its result always equals the unforced path's, because
  _is_plausible_host already excludes interfaces and enums); replaced by a mutant of the force-string suffix rule.

## Performance (artifacts/performance-semtpl.txt; same machine, same 8,691 B 50-row reply, N=2000, medians)
learn C++ ~640 us vs Python 1,289 us (2.0x); extract ~100 vs 126 (1.25x); rebuild ~115 vs 139 (1.2x). Python's json is C;
the C++ extract/rebuild are bound by the S2 JSON scanner (a faithful port of Python's scanner semantics) -- a faster
scanner is follow-up work. Two optimisations kept (both re-proved by the oracle): an ASCII fast path in the ensure_ascii
writer, and strip() on the bytes (only edge code points decoded).

## Qualification (three batches: background jobs do not outlive a turn here)
1 build + semtpl: oracle PASS 68,878; mutants 14/14.  2 all other stages: build, local, fnw1, 10 independent 3/3,
cross-process, Dino oracle 4/4, S1 (mutants 8/8), S2 715,366 cases (mutants 14/15: the miss was a SETUP failure -- the
"bool not numeric" mutant targeted the tolerant values_equal that EXACT_DIFF_V1 replaced; replaced by "bool equals int",
caught 23/23/16), handler, stress 0 partial, lock-freedom, perf.  3 every RAM-facing stage again on the urldec-fixed
ram-server: all PASS.

## RAM server fix shipped with this package (ram-server-urldec-fix-20260925.tgz; ribbit_cpp/ram-server is built from it)
[URLDEC_AS_RAM_PHP_V1]: ONE request with "%zz" in its query aborted the whole RAM server (std::stoi threw out of the
session thread; every client lost), and "%4z" decoded silently as byte 0x04. Now as ram.php's $_GET decodes (PHP
urldecode): "%"+two hex digits is the byte, any other "%" literal, "+" a space. test_urldec.cpp: red 3 FAIL (incl. the
server dying), green 4 PASS.

## Next: RAM-ANSWER-HANDLER-SPEC.md (the field list for RAM read answers; then S4 client engine, S5 server engine)
