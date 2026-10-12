"""apply_engine_exact_shortcuts.py RIBBIT_CPP_DIR -- two shortcuts that give EXACTLY the bytes the long way gives:
  [PROOF_BYTES_FIRST_V1] fnwp_engine.hpp: the reply proof (REPLY_EXTRACTION_PROVEN_V1) rebuilt the answer from the
      template and parsed both into JSON to compare them -- 33 k instructions per answer (callgrind, rr). When the
      rebuilt text is byte-identical to the answer it is proven without parsing; otherwise the JSON comparison runs as
      before. Byte-equal texts are JSON-equal, so the decision is the same.
  [LZ4_NEVER_SMALLER_SKIPPED_V1] semcodec.hpp: _lz4_smart compresses, then keeps the result only if it is smaller.
      An LZ4 frame as encoded here (python-lz4's defaults, content size in the header) is never shorter than 24 bytes
      (measured over every payload of 1-200 bytes, uniform and varied: the smallest frame is 24 B, for a 1-byte input),
      so a payload of 24 bytes or fewer can never be kept compressed: it is not compressed. The bytes on the wire are the
      same.
Applied at build after apply_reply_proof.py; the vendored v0.63 source stays untouched."""
import sys, os
d = sys.argv[1]
def patch(fn, a, b):
    p = os.path.join(d, fn); s = open(p).read()
    assert s.count(a) == 1, (fn, a[:70], s.count(a)); open(p, 'w').write(s.replace(a, b))
patch('fnwp_engine.hpp',
      'auto x = semtpl::detail::json_try(a.body), y = semtpl::detail::json_try(rebuilt);\n',
      'if (rebuilt == a.body) { same = true; } else {                     // [PROOF_BYTES_FIRST_V1]\n'
      '                      auto x = semtpl::detail::json_try(a.body), y = semtpl::detail::json_try(rebuilt);\n')
patch('fnwp_engine.hpp',
      'same = (x && y) ? semtpl::python_eq(*x, *y) : rebuilt == a.body; }\n',
      'same = (x && y) ? semtpl::python_eq(*x, *y) : rebuilt == a.body; } }\n')
patch('semcodec.hpp',
      '    if (payload.empty()) return {std::string(payload), false};\n    std::string c = lz4_compress(payload);',
      '    if (payload.size() <= 24) return {std::string(payload), false};   // [LZ4_NEVER_SMALLER_SKIPPED_V1] a frame is >= 24 B\n'
      '    std::string c = lz4_compress(payload);')
print("engine exact shortcuts applied")
