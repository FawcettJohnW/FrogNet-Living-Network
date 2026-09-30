# Semantic-engine port S2 RED — codec v5 (core/codec.py)

Base: S1 GREEN (ribbit-lisp-v0.45.2-S1.zip 8a9a2b71a8bd79a1bb26535560fd570c78cb545940bcaa861cb5936f7b605073).
Oracle: John's `core/codec.py` (SemanticCodec, WIRE_VERSION 5), imported and run, and python-lz4 4.4.5 (liblz4 1.9.4)
as the codec calls it.

`tools/test_semcodec_oracle.py ROOT` drives `tools/semcodec-driver` (tools/semcodec_driver.cpp, ribbit_cpp/semcodec.hpp,
ribbit_cpp/pyval.hpp). 767,519 cases at seed 20260924, scale 1, compared exactly (bytes; every decoded value's type and
IEEE bits; None versus absent; reference dicts in insertion order; rejection kind, and ValueError text where it is John's):
- encode_request / encode_reply / encode_request_diff / encode_reply_diff: random field lists (all seven types, nested
  JSON, int64 edges and unbounded ints, NaN/inf/-0.0/subnormals, lone surrogates, 64 KiB edges), references None / {} /
  equal / near-equal (1 vs 1.0 vs True, +1e-10, +1e-8) / different, compressible and incompressible payloads 0-65530 B,
  65535-65537 fields;
- encode_error_reply, decode_error_reply (Python int() status parsing: whitespace, signs, underscores, Unicode digits,
  the 4,300-digit limit);
- decode_request / decode_reply: every encoded packet, truncations, byte corruptions, every type id 0-11, clamped
  slices, versions 0/4/5/6/255, all flag combinations, compressed blocks valid/truncated/trailing;
- JSON through TYPE_JSON: ~6,000 texts (number grammar, NaN/Infinity, escapes, surrogate pairs and lone surrogates,
  control characters, BOM, duplicate keys, big ints, invalid UTF-8) and 3,000 json.dumps outputs;
- _values_equal (20,058 pairs), _lz4_smart (170), lz4.frame.decompress (419: truncated, bit-flipped, trailing, doubled).

The codec interface throws on every call. Result: all 11 categories FAIL (artifacts/semcodec-red-S2.txt).
No existing source changed.
- 808c4cbd26504b65fb75e5ff8fd94398a7905612d217ab645f411697a9fbc053  ribbit_cpp/pyval.hpp
- 5588d8f24c72a60887880ad351cd9fb62daf42fe2853ce80bd1c3c435f333110  ribbit_cpp/semcodec.hpp
- 5c63a21e1e7ff6bd078fc6895ac515a5ea5907c9ca59e8640db338fe26060f61  tools/semcodec_driver.cpp
- 116634d84d917f54b1e0db25d8456809066898b420451eda45fb6b667e6ea973  tools/test_semcodec_oracle.py
- b725c2e11cc624871fa7712a76f42513d091aacb9a48a32cc305564b81b27eb0  artifacts/semcodec-red-S2.txt
