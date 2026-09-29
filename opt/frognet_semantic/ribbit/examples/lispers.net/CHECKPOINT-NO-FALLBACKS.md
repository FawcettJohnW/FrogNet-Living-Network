# No fallbacks in John's Python (the files the semantic-engine port touches), and the port brought in line

Ruling (John, 2026-09-24): "There are not supposed to be swallows in the Python code either." A swallow is a defect to
fix; no carve-outs (a parse probe used as detection is "try both and use whichever works").

## Python changed (whole files in no-fallbacks-python-20260924-r2.tgz, paths from /; r2 adds discovery)
core/json_handler.py, core/template.py, daemon/engine/template.py (identical, imported by nothing), core/format_registry.py,
core/codec.py, core/store.py, proxy/templates.py, proxy/transport_semantic.py, discovery/hosts.py, and three tests:
simulation/spec_compliance/test_json_compliance.py, discovery/test_dbhost_rank_malformed_oracle.py,
discovery/test_direct_neighbors_oracle.py.
- json_handler: a body that does not parse, or that the template cannot hold, raises in learn and extract; an array field
  is a list or JSON text of one, else it raises (no ast.literal_eval, no [] default); a missing blob raises; a malformed
  FROGNET_JSON_MAX_ITEMS raises. An empty body is still no body (empty template, []).
- template: a fragment is a dict or JSON of one; it names a known mode (no raw default); tokens are a dict; a request
  template has an action URL and a list of dynamic keys.
- format_registry: detection decides from the bytes, with the rules detect_reply_handler already used for replies; no
  json.loads / ElementTree probe; no RAW default for an unknown mode. Changed behaviour: a request body opening with { or
  [ is JSON even when malformed (learning then raises); a body opening with a tag and no HTML marker is XML.
- codec: decode_error_reply returns None only for "not an error frame"; a malformed error frame raises.
- store: unreadable JSON columns raise; no retry in _with_conn (a failed connection is closed, never requeued); a failed
  migration step raises; no fallback INSERT without url_query_keys; commit/close failures are not swallowed; builders
  raise on a missing row or malformed params/tokens/keys; store_templates raises on an empty method/path.
- proxy/templates: a bad upsert body raises; a dynamic key the template declares but the path lacks raises; a client
  hang-up (BrokenPipe) in the fail-closed writer raises; the first-row cut runs only on JSON replies; no catch-all in
  learn_templates_from_real.
- transport_semantic: no catch around learning in _handle_resp_raw_and_learn (ruled "Abort").
- discovery/hosts.py (John's box log, 2026-09-24): select_database_host caught CapabilityReadFailed, logged "falling
  back" and elected the highest-.1 baseline -- which was 10.251.251.1, the host whose control read had just timed out
  after 20 s. A failed control read now raises and the merge stops (the previous /etc/hosts stands); a read that answers
  with nothing published still takes the baseline. role_barrier_ready no longer turns a failed read into "not ready".
  _num (RANK_NUM_GUARD_V1) raises on a malformed field, naming host and field; an unsent field (None) is still its
  default. test_dbhost_rank_malformed_oracle now requires the raise (red on the old hosts.py). test_direct_neighbors_oracle
  ran a merge over fake IPs without FROGNET_OFFLINE_TUPLES and passed only because the read failure was swallowed; it now
  sets it, as its sibling oracles do.
- test_json_compliance: a malformed body must RAISE (it required the empty-fragment swallow as "reject"). Red on the
  original handler (16 fail), green on the fixed one.

## Proof
- tools/test_python_no_fallbacks_oracle.py, 49 probes, each the input a site used to swallow, including the box log's
  timed-out control read: original tree 46 FAIL (the 3 PASS are controls that must pass on both); fixed tree 49 PASS.
- simulation/run_all.py: original tree and fixed tree fail the same 2 tiers, nothing more. Both are environmental and
  independent of these files: collectives/DDP (torch not installed in this container), discovery
  test_clean_install_oracle (the source tarball lacks usr/local/lib/frognet_log.sh and frognet_trace.sh).
  Container setup for the run: iproute2 installed, the tree's usr/local/bin/mapInterfaces copied to /usr/local/bin.

## Port
- S2: C++ decode_error_reply now raises (CpyValueError) on a malformed error frame. S2 oracle against the fixed Python:
  red before the change (dec_err 2,311 of 3,194 differ), then 721,496 cases 0 differ. Mutants and sanitizers were NOT
  re-run after this change; the next full qualify.sh run does.
- S3 red revised: the oracle compares raise against raise. Declined in the C++ (none a swallow): pair_heuristic,
  text_repr, not-ported handlers. 68,822 cases, all 31 categories FAIL against the throwing header.

## Not swept yet
xml_handler / html_handler (their compliance tests also demand a "graceful" reject) and the rest of the tree
(proxy_main.py has an except BrokenPipeError around the client write, among others). Not in the port's files; named here
so nobody assumes they were done.

## Hashes
- ba9558f29b7b678cbb62424327ba4283b32fc0a31e35ede35cb9beb9056ffcce  ribbit_cpp/semtpl.hpp
- 0e44c09185b1ca791310a119c2b3a070409c5408d0a9908dc4aa00c7c45ff775  ribbit_cpp/semcodec.hpp
- b170c23fbcb02a8971c58666894d9623d1b9ef387c692532f7d153b398dc8aed  tools/semtpl_driver.cpp
- 009741d518e4650522964cf94af8aa6c79fb6799e9d278171c072253407567c4  tools/test_semtpl_oracle.py
- 1d55b5e956ff2d00741e9c24b7574ad5822f63e89fbdd9062f3e431093d36684  tools/test_python_no_fallbacks_oracle.py
- 7978c7a5a89ffa05a3ed21d38a85fe737bc9762cb6beac536cb09dba8680b805  artifacts/semtpl-red-S3-r2.txt
- 5c22b775a4dc68fd9cb04a9c452401161345a62ff5bd43234a9a38a5a0172029  artifacts/semcodec-red-S2b-decode_error_reply.txt
- 86ee46116e24c3b0701b66ed374ce80964f97e32f203dd2bf7fd5df010c99a42  artifacts/semcodec-green-S2b-decode_error_reply.txt
- b9803de786832569b48c387717be8be813602ca0b1fe36b1e34b6a2b21f070b4  artifacts/python-no-fallbacks-original.txt
- c3954bf44ecd9ac12aa9c1ba57d631ec3288ddca64f9188f3499ca2c3e4ed780  artifacts/python-no-fallbacks-fixed.txt
- 3aa6b5a7554d9163cad7cf0d92fc21555fbc7ade35ae50f9d9d0fc3861a51464  artifacts/sim-run_all-original-python.txt
- 57223d219d41c76af9290b30b60f0596531edd285fea2c176cf87bb7c0b81102  artifacts/sim-run_all-fixed-python.txt
