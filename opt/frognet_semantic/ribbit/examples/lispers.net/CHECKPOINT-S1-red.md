# Semantic-engine port S1 RED — FNW1 v4.1 wire framing

Base: v0.45.1-realmachine (5e7d24eaa3dd4d02b95851ebdd07a6c213b0befb654bf7cf8283c6e64053a89e).
Oracle: John's `core/semcache_wire.py` from frognet-source-20260914_happydog.tgz
(fc49efc3bf7803a03bced78726138f5ae493c9fbdbaf1396bd51f795033084d3), imported and run.

`tools/test_semwire_oracle.py ROOT` drives `tools/semwire-driver` (built from `tools/semwire_driver.cpp` and
`ribbit_cpp/semwire.hpp`) and compares, per case:
- every `wrap_*` on the same inputs: frame bytes identical, or the same rejection kind (ValueError / struct.error /
  UnicodeEncodeError) with ValueError text identical;
- `try_parse` on every frame, every prefix of every frame up to 400 bytes, trailing-byte extensions, one-byte
  corruptions of bytes 5-59, all 256 op bytes, RESP_RAW inner-length edges and a seeded fuzz corpus (200,000):
  None / ValueError text / every WireMsg field, None distinguished from empty;
- `is_fnw1` on 5,000 frames, `op_name` on 0-255;
- round trips both ways: parse(wrap(x)) gives back x, C++ frames through Python and Python frames through C++.

Coverage of the corpus, from the oracle's own outcomes: 4,768 wrap cases (4,273 ok, 394 ValueError, 99 struct.error,
2 UnicodeEncodeError); 538,276 parse cases reaching every op's success path, unknown ops, None, and all 22 ValueError
branches of try_parse.

`ribbit_cpp/semwire.hpp` holds the interface only; every function throws. Result: every category FAIL
(artifacts/semwire-red-S1.txt). No existing source changed.
- 477388fb3025a5203ca5a43dff7b30f25ed55b29a370f7c0fdd2fe1eb0b698e3  ribbit_cpp/semwire.hpp
- ddb43fced4dbc88351388fef49c99f04beef00598031a5f6595a8272d8d22cff  tools/semwire_driver.cpp
- e6a0935067028ffdd6c80a510e8e5d463bf35118a7673858e475761c1c100531  tools/test_semwire_oracle.py
- 0ecb2d482df7495f5adc6f83b9294d3b567525491f692674c45220087429267d  artifacts/semwire-red-S1.txt
