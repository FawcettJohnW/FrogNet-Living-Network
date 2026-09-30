# S3 (templates) — characterization from the source, before any code

Read from frognet-source-20260914_happydog.tgz /opt/frognet_semantic; completed against frognet-source-20260924.tgz
(c3ae0926...a3a7c7; file hashes in artifacts/oracle-source-20260924.sha256). Nothing here is implemented yet.

## What actually runs
- Proxy (client): proxy/transport_semantic.py imports proxy/templates.py (get_template_store, learn_templates_from_real,
  extract_dynamic_query_vals) -> core/store.py (TemplateStore) -> core/template.py (RequestTemplate, ReplyTemplate)
  -> core/format_registry.py (FORMAT_HANDLERS, detect_request_handler, detect_reply_handler).
- Daemon (server): daemon/engine/execution.py imports daemon/templates/loader.py -> core/template.py.
- daemon/engine/template.py is imported by nothing outside itself (grep); not ported unless a caller appears.
- core/template_utils.py: rebuild_json is used (json_handler, RequestTemplate.rebuild_body); learn_json_schema and
  flatten_json are not used by the JSON handler.

## JSON handler (core/json_handler.py) — Python semantics a C++ port must carry
- _sanitize_nonfinite: NaN/inf floats become None, recursively, before learning, extraction and _dumps.
- _dumps(obj) = json.dumps(sanitized, separators=(",", ":")) with ensure_ascii TRUE unless passed: escapes every char
  outside ' '..'~' (so DEL 0x7f too) as \uXXXX lowercase; > U+FFFF as a surrogate pair; lone surrogates as \udXXX.
  RequestTemplate.rebuild_body also uses json.dumps(obj, separators) with ensure_ascii True.
- Learn: infer_schema_preserve_arrays sorts dict keys by str (code point order = UTF-8/WTF-8 byte order); a dict with
  any key failing ^[A-Za-z_][A-Za-z0-9_]*$ becomes {"type":"any"} -- NB Python re '$' also matches before a trailing
  "\n", so key "abc\n" passes. Arrays: item schemas merged (_merge_schema); empty array -> {"_array":{"type":"any"}}.
- _leaf_type: bool/int/float by type; str: _is_ipv4 (after str.strip(); ^\d{1,3}(\.\d{1,3}){3}$ where \d is Unicode
  Nd, then int() of each part 0..255), path rules (_force_string_for_path, _force_host_for_path, lower() of the path),
  _looks_like_interface (^(eth|wlan|wl|enp|wlp|br|docker|usb|veth)\w*$, \w = Unicode alnum or '_'),
  _looks_like_enum (s.upper() in a word set -- Unicode case mapping: 'ſ'->'S', 'ﬆ'->'ST', 'ı'->'I' -- or
  ^[A-Z0-9_]{2,}$ without '.'), _is_plausible_host (no space, has [A-Za-z], '.' or FrogNetHost prefix,
  ^[A-Za-z0-9][A-Za-z0-9\.\-]*[A-Za-z0-9]$). str.strip() strips Unicode whitespace (str.isspace set).
- _build_field_order_and_type_map: sorted keys, "array" for _array children, leaf types validated against a set.
- Baseline: list values whose ensure_ascii=False compact JSON exceeds 8192 bytes go to BlobStore (content-addressed,
  "sha256-<hex>", files under FROGNET_BLOB_ROOT default /opt/frognet_semantic/blob_cache) and baseline[path] = None.
- Top-level list body: field_order ["value"], type_map {"value":"array"}, baseline {"value": obj}.
- Extract: strip, json.loads, sanitize; list + ["value"] -> [("value", obj)]; dict -> flatten_json_preserve_arrays
  (dotted paths through dicts only; "value" is the whole object); anything else -> [].
- rebuild_reply: array fields arriving as str are re-parsed with json.loads, then ast.literal_eval (Python literal
  syntax), else [] -- and an array value of [] or None with a baseline blob loads the blob. Scalars through
  template_utils.rebuild_json (the "rows." prefix makes a one-element rows[] list). Output is _dumps (ensure_ascii).
- Decision pending (to raise with John, not assumed): ast.literal_eval is a full Python-literal evaluator; porting it
  faithfully is large. Candidate: a declined-semantics entry with a loud C++ error if a str-typed array value is not
  JSON, and an oracle category that exercises and reports it.

## Detection (core/format_registry.py)
- detect_request_handler: Content-Type contains json/xml/html (html only if the body looks like HTML), else sniff:
  json (starts {/[ and parses) -> xml (ElementTree parses) -> html -> text (<= 15% of chars < 9 or 127..159) -> raw.
- detect_reply_handler: application/json or body starts {/[ -> JSON; xml rules; html rules; text/*; text sniff; raw.
- JSON is the first handler (Ribbit bags are JSON). XML/HTML/text/raw/sotf_media handlers are separate work; until
  ported, the C++ must refuse loudly when detection selects one, never substitute.

## proxy/templates.py (client learning)
- normalize_path_for_semantics: strip leading '//' runs, urlparse, collapse '//' in path, prefix '/', parse_qsl
  keep_blank_values, static key set (entity, action, SensorType, NetworkName, FrogID, Mode, SensorName__like,
  SensorName, SensorID, CallSign) kept (last value wins, first position) and urlencoded; dynamic keys in first-seen
  order. _with_dynamic_shape appends "__dyn=<sorted names>". canonical_semantic_key special-cases sensor_data upserts.
- extract_dynamic_query_vals: parse_qs keep_blank_values, first value, missing -> "".
- learn_templates_from_real: reply sample cut to first row of every array (_first_row_only) via json.loads/json.dumps
  (default separators ", " ": " and ensure_ascii True!) before learning; refuses to store if the learned baseline has
  an empty array anywhere (_empty_arrays_in); url_query_keys injected from the raw path.
- urllib.parse (urlparse, parse_qsl, urlencode quote_plus) semantics must be ported exactly for the keys to match.

## core/store.py (TemplateStore)
- templateId = "tpl_%08x" % crc32(("METHOD " + semantic_path) as UTF-8 "replace"); opcode = crc32 of the same key
  (strict UTF-8), with 0 and 0xFFFFFFFF both mapped to 0xFFFFFFFE (0xFFFFFFFF is the codec's error opcode).
- Persistence: local MySQL (DB_CONFIG.json), tables frognet_request_templates (params = request fragment JSON,
  url_query_keys JSON) and frognet_response_templates (templateJSON, responseMode); both json.dumps compact,
  ensure_ascii=False. Lookups by (method, actionUrl) and by opcode, cached 60 s positive / 1 s negative.
- build_request_template / build_reply_template coerce stored JSON back (_loads_json_maybe); an absent reply row
  yields a raw-mode ReplyTemplate "none"; a non-dict templateJSON raises.
- Ruled (John, 2026-09-24): templates go in the local MySQL instance on both sides, same tables and row encoding.

## Remaining reads, done against the 20260924 source
- daemon/templates/loader.py (44 lines): TemplateLoader.lookup_by_opcode -> TemplateStore.lookup_by_opcode; returns
  (None, None) unless BOTH rows exist, else build_request_template / build_reply_template. The daemon has no other path.
- core/text_handler.py (140): RawFormatHandler and TextFormatHandler are identical except mode "raw" / "text": one field
  "raw", type_map raw->raw (TYPE_RAW), baseline {"raw": body}; extract returns [("raw", body or "")]; rebuild_reply
  returns baseline["raw"] when values is empty, else the first value (or the second element of a 2-item tuple/list),
  "" for None, else str(). Small enough to port with JSON in S3, which removes one refuse-loudly case.
- Client call sites (proxy/transport_semantic.py): handle_request looks up by (method, semantic_path); with both rows,
  _handle_semantic_request: extract_dynamic(body utf-8 "replace"), extract_dynamic_query_vals(path, url_query_keys),
  encode_request_diff(url_vals, json_vals, type_map, reference, tokens, compress=True). RESP_DIFF: decode_reply against
  the per-(target, opcode) response reference, then resp_tpl.rebuild(values), utf-8 "replace". No template: REQ_RAW
  bootstrap, then learn_templates_from_real on status < 400 with a body.
- Server call sites (daemon/engine/execution.py): lookup_by_opcode, no templates -> encode_error_reply(503); decode_request
  against the per-(peer, opcode) request reference; [REQUEST_REF_COMPLETE_V1] gate; build_url(url_vals),
  rebuild_body(json_vals); resp_tpl.extract_dynamic on the executed (or data-cached) reply; session.py encode_reply_diff.
- daemon/engine/template.py is still imported by nothing (grep of the 20260924 tree).
- For S4, not S3: transport_semantic.py line 2896 wraps learn_templates_from_real in `except Exception` logged at debug
  as "non-fatal". That is a swallowed failure under the no-fallbacks rule. Ruled (John, 2026-09-24): abort loudly; not ported.

## S3 scope (from the reads)
Template learn / extract / rebuild for JSON, TEXT and RAW; request templates (url_query_keys, build_url, rebuild_body);
normalize_path_for_semantics, extract_dynamic_query_vals and the urllib.parse pieces they use; templateId/opcode crc32;
format detection, with XML/HTML/sotf_media refused loudly. Oracle: the Python modules above, imported and run.
Storage (MySQL) is S4.
