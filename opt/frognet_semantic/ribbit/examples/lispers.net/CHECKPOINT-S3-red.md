> SUPERSEDED IN PART (2026-09-24): John's Python no longer swallows; see CHECKPOINT-NO-FALLBACKS.md. The declined cases
> below that were swallows are now raise-vs-raise; artifacts/semtpl-red-S3-r2.txt is the current red.

# Semantic-engine port S3 RED -- templates (JSON, TEXT, RAW), detection, path keying

Base: S2 GREEN, package ribbit-lisp-v0.45.3-S2-r2.zip (73486f954bb600006000059616b9ff274dc8f2bc59a67153c0a09ec2bd532922).
Oracle source: frognet-source-20260924.tgz (c3ae0926...a3a7c7), files pinned in artifacts/oracle-source-20260924.sha256.
Characterization: S3-CHARACTERIZATION.md.

## What the red is
ribbit_cpp/semtpl.hpp declares the S3 interface; every entry point throws. tools/semtpl_driver.cpp drives it;
tools/test_semtpl_oracle.py imports John's Python and compares every case exactly (same canonical value form as S2).
31 categories, 68,822 cases at seed 20260924, scale 1:
- urllib.parse as called: urlparse, parse_qsl / parse_qs (keep_blank_values), urlencode (quote_plus, strict UTF-8);
- proxy/templates.py: normalize_path_for_semantics, extract_dynamic_query_vals, _with_dynamic_shape,
  canonical_semantic_key (upsert_by_name bodies, bad JSON, invalid UTF-8), _first_row_only, _empty_arrays_in;
- core/store.py ids: templateId (UTF-8 "replace") and opcode (strict; crc32 0 and 0xFFFFFFFF forged, 3 each);
- json_handler typing: _is_ipv4, _looks_like_interface, _looks_like_enum, _is_plausible_host, _leaf_type by path
  (Unicode digits, whitespace, case mapping, Kelvin sign / dotted I in paths), _sanitize_nonfinite, json.dumps with
  ensure_ascii (compact and default separators), infer_schema_preserve_arrays, _merge_schema,
  _build_field_order_and_type_map, template_utils.rebuild_json ("rows." paths, conflicting paths);
- handlers: learn (json/text/raw, request and reply), extract, rebuild through ReplyTemplate, blob-backed arrays;
- RequestTemplate build_url / rebuild_body, ReplyTemplate content_type;
- detection: sniff_body_mode, detect_request_handler, detect_reply_handler;
- learn_templates_from_real without the store (what it would store, or the empty-array refusal);
- blobs: learn and train run against fresh, empty blob roots on each side; the roots must match in names and bytes.

Result: all 31 categories FAIL (artifacts/semtpl-red-S3.txt). No existing source changed.

## Declined semantics (John's no-fallbacks rule; C++ raises Declined naming the case)
Detected from the Python's own execution: json_handler's json.loads, ast.literal_eval, BlobStore.load and
ElementTree.fromstring are wrapped and log each call. Branches that swallow without an exception are recomputed from
the inputs and cross-checked against the log; a disagreement is an oracle error (0 in this run).
Counts in this corpus, and what the Python did instead:
- json_unparsable (learn/extract/train: failed parse swallowed, empty template or []): 16 learn, 4 extract, 189 train
- json_shape (extract: a body the template cannot hold returns []): 305
- array_literal (ruled 2026-09-24: ast.literal_eval, else []): 470
- array_type (array field neither list, str nor None becomes []): 781
- blob_missing (BlobStore.load failure becomes []): 2
- pair_heuristic (first value a 2-element list is read as a (key, value) pair): 498 (293 returned output, 205 raised)
- unknown_mode (unknown mode handled as raw): 14
- text_repr (str() of a list/dict/bytes would need Python repr; the codec never yields one for a text field or URL
  value): 12 rebuild, 10 rebuild_body, 577 build_url
- train_exception (learn_templates_from_real catches and prints every exception): NOT EXERCISED. Nothing in the pure
  learning path raised past the handlers in this corpus; the likely route is RecursionError at Python's stack limit,
  which depends on the stack and is not tested (the S2 limit statement applies).
Not ported (C++ raises NotPorted): xml/html/sotf_media handlers, and any detection that reaches ElementTree.fromstring.

## Findings in the Python (reported, ported as they behave unless declined above)
- learn_templates_from_real passes the whole `upstream` dict to detect_reply_handler as its headers, so the reply's
  Content-Type is never seen; detection is by body only. The C++ ignores the reply headers the same way.
- A top-level scalar JSON reply learns a template (field_order ["value"]) that extract cannot use (json_shape), and
  whose rebuild would produce {"value": v}.
- The pair heuristic misreads a reply whose first field (sorted) is a 2-element array.

## Files
- e215d0a456e9f90cf6597c324a13ec6e7f152bb3c4d86388de109d5f7c087d85  ribbit_cpp/semtpl.hpp
- 4b0c09741f7603901e001579fd75cd36ea9463594002eb59aec9f04fa7c034f4  tools/semtpl_driver.cpp
- bf5e8f903d3fc33d648072bb7e8b2d826a22db0e6f482d9d6ad5fd04427aaa6c  tools/test_semtpl_oracle.py
- 870cec19e9bab0fc7c95a8a75bbf747e2e16f7d5a89beee11ccd1473f5f14ea0  artifacts/semtpl-red-S3.txt
