# S4 (engine in frogram) and S5 (engine in the RAM server) -- FNWP on the RAM wire

Ruling (2026-09-25): FNWP as it exists, both ends; no invented frames. Built from: S1 semwire.hpp (frames), S2 semcodec.hpp
(exact codec), S3 semtpl.hpp (templates, path keying, ids), ramrows.hpp (field list for RAM read answers).
Read from proxy/transport_semantic.py (running tree); the daemon side (daemon/engine/session.py, execution.py) still to be
read line by line before S5 is written.

## Client (S4), per request -- as _handle_semantic_request / _handle_raw_and_learn do it
1. Key: semantic_key = canonical_semantic_key(method, path, body) (S3); template = lookup (method, semantic_key);
   opcode = opcode_for(semantic_key); templateId = template_id(method, semantic_key).
2. No template: REQ_RAW (wrap_req_raw(req_hash, http_req)), or REQ_REPEAT if the raw request is known -> RESP_RAW ->
   learn_templates (S3) from the real exchange, store (MySQL, both sides, ruled) -> a learning failure aborts (ruled).
3. Template: body fields = extract (S3; nullopt -> RAW), url_vals = extract_dynamic_query_vals(path, url_query_keys).
   encode_request_diff(opcode, url_vals, json_vals, reference = request reference for (target, opcode)):
   identical -> REQ_REPEAT(req_hash); no reference -> REQ_FULL(req_hash, sem_req); else REQ_DIFF(req_hash, sem_req).
   Non-identical requests get the origin trailer appended: origin_ip bytes + dest_host bytes (UTF-8 'replace', each cut
   to 255) + [len(o), len(h), 1] + b"\xFA\xCE" (proxy/origin.py inject_origin_into_semantic_request).
4. req_hash = sha256(json.dumps({"_dst": target, "_path": path, "_op": opcode, **new_reference}, sort_keys=True,
   default=str))[:16] -- needs S3's ensure_ascii writer with sort_keys (default separators ", " ": ").
5. Every RPC goes through coalescing keyed by req_hash (_coalesced_rpc): duplicate in-flight requests share one reply.
6. Replies: RESP_SAME(same_id) -> the SAME LRU (same_id -> body, ctype, is_raw, status, headers; bounded, oldest first);
   RESP_DIFF(same_id, sem_blob) -> decode_reply against the response reference for (target, opcode), rebuild with the
   reply template (for RAM read answers: ramrows), put in the SAME LRU; RESP_RAW -> the body as is; REQ_MISS -> the far
   end lost the reference: resend as REQ_FULL (attempt loop, bounded; never silent).
7. References: request reference and response reference per (target, opcode), replaced whole after each exchange.

## Server (S5): mirrors daemon/engine -- decode REQ_FULL/DIFF against its request reference, execute the RAM op, answer
SAME / DIFF (encode_reply_diff against its response reference) / RAW; REQ_MISS when it holds no reference; RAW when the
reply cannot be held (ramrows nullopt, S3 nullopt). To be read from session.py / execution.py before writing.

## RAM specifics
- /ram.php query keys are all DYNAMIC under S3's keying (none is a static template key): semantic_key is
  "/ram.php?__dyn=<sorted keys>" and the key values travel as url_vals -- a held read that moves `after` is a REQ_DIFF
  of one field.
- Read answers: reply template = ramrows over the response reference (no MySQL row). Writes / removes answer small JSON
  ({"ok":true,"id":N}) -> S3 JSON handler.

## Proof plan
Red first against a real RAM server: every frame byte-identical to what the Python proxy / daemon would produce for the
same inputs (oracle: the Python modules imported, as S1-S3), then end-to-end: the Ribbit-LISP qualification with the
engine on, answers identical to the reduced wire, bytes measured (storm case: one member of 500 cells).

## Reference rules, read from the running code (2026-09-25) -- both ends must follow them exactly
Proxy (proxy/transport_semantic.py):
- The REQUEST reference for (target, opcode) is replaced by the new reference after any RESP_SAME / RESP_DIFF / RESP_RAW;
  cleared on REQ_MISS, and the next attempt goes REQ_FULL (bounded attempts, then 503 "exhausted retries").
- The RESPONSE reference for (target, opcode) is set ONLY after a RESP_DIFF (_handle_resp_diff: decode_reply against it,
  replace it with the decoded fields). Never after RESP_RAW. Cleared, with the request reference, when a RESP_SAME
  misses the SAME cache (then the request re-bootstraps RAW).
- RESP_SAME serves from the SAME LRU (same_id -> body, ctype, is_raw, status, headers), then semcache_db; RESP_DIFF and
  RESP_RAW bodies are put in the LRU. OP_ERROR "no templates for opcode" -> re-bootstrap RAW; other OP_ERROR relayed.
Daemon (daemon/engine/session.py):
- REQ_FULL clears the daemon's RESPONSE reference for that opcode first (FIX 15), so its reply carries ALL fields.
- SAME is decided on the response itself, response_body_hash(dyn_vals, status), never on the diff-encoded reply
  ([SAME_COMPARES_THE_BODY_V1]); on SAME the daemon still sets its response reference to dyn_vals.
- same_id = compute_same_id(req_hash, raw_hash). REQ_REPEAT looks up (sem_req, raw_hash, same_id) by req_hash in a
  MEMORY-ONLY LRU ([SAME_CACHE_IS_MEMORY_ONLY_V1]); a miss is REQ_MISS.
- A merged request reference missing a declared url_query_key -> REQ_MISS ([REF_INCOMPLETE_IS_A_REQ_MISS_V1]).
- Engine cannot hold the reply -> RESP_RAW ([RAW_SIGNAL_V1]); engine error -> OP_ERROR with the engine's own reason
  ([ENGINE_ERROR_PRESERVE_REASON_V1]).
Consequence for the RAM server (S5): the per-session state is exactly the daemon's -- a response reference per
(session, opcode), a request reference per (session, opcode), and the memory-only REPEAT cache by req_hash.

## Status
- S4a GREEN: fnwp::build_request (ribbit_cpp/fnwp_client.hpp) -- the frame, req_hash and new reference byte-identical to
  the proxy's (tools/test_fnwp_oracle.py: seed 20260925 24,854 cases, seed 11 37,276 cases, 0 differ; red: all failed).
- S5a GREEN: fnwp_server.hpp -- Python repr() (exact: quoting, escapes by Unicode printability via a generated
  isprintable table, float / bytes / list / dict repr), response_body_hash, same_id (HMAC-SHA256, secret as a parameter),
  _diff_encode_response (full encode_reply when identical or empty; its except-branch mirrored as the running code has
  it), and the SAME-or-DIFF reply with the reference and REPEAT-cache entry it leaves (tools/test_fnwp_server_oracle.py:
  seed 20260925 6,000 cases, seed 13 20,000 cases, 0 differ; red: all failed).
- S4b reply side GREEN: fnwp::apply_resp_diff -- RESP_DIFF payloads from the daemon's own _diff_encode_response,
  decoded and rebuilt by the C++ client exactly as _handle_resp_diff does, the S3 declines included
  (tools/test_fnwp_reply_oracle.py: 4,000 + 6,000 cases, 0 differ).
- Next: the stateful engines -- frogram::Session (request / response references per (target, opcode), SAME LRU,
  REQ_MISS attempt loop, coalescing by req_hash) and the RAM server session (references per (session, opcode), the
  memory-only REPEAT cache, REQ_FULL clears the response reference, the RAM op as the engine) -- then end to end: C++
  client <-> C++ server, and each against the Python proxy / daemon, answers identical to the reduced wire.
