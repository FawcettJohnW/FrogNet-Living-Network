"""apply_alloc_once.py RIBBIT_CPP_DIR -- [ALLOC_ONLY_WHEN_NEEDED_V1] the codec allocates only when it must: buffers that
keep their high-water mark, reserve() to the expected size plus a little, and no copy of a value that is not changed.
Each change gives exactly the bytes the long way gives. Measured by a malloc call-site sampler (rr 2,000: 1.71 M mallocs
in the host, 854 per transaction, before). One change per marker:

 [NONFINITE_COPY_ONLY_IF_NEEDED_V1] semtpl::sanitize_nonfinite rebuilt every parsed JSON tree (every dict, every list,
     key by key) to replace NaN/Infinity with null. A tree is now walked once to see whether it holds a non-finite number,
     and copied only then; otherwise the parsed tree itself is used (moved, not copied)."""
import sys, os
d = sys.argv[1]
def patch(fn, pairs):
    p = os.path.join(d, fn); s = open(p).read()
    for a, b in pairs:
        assert s.count(a) >= 1, (fn, a[:80], s.count(a)); s = s.replace(a, b)
    open(p, 'w').write(s)

patch('semtpl.hpp', [
 ('''inline Obj sanitize_nonfinite(const Obj& o);''', '''inline Obj sanitize_nonfinite(const Obj& o);
inline bool has_nonfinite(const Obj& o);'''),
 ('''inline Obj sanitize_nonfinite(const Obj& o) {''',
  '''// [NONFINITE_COPY_ONLY_IF_NEEDED_V1] does the tree hold a NaN or an infinity anywhere? (a walk, no copy)
inline bool has_nonfinite(const Obj& o) {
    if (o.is<double>()) return !std::isfinite(o.as<double>());
    if (o.is<Dict>()) { for (const Obj& v : o.as<Dict>().vals) if (has_nonfinite(v)) return true; return false; }
    if (o.is<List>()) { for (const Obj& v : o.as<List>()) if (has_nonfinite(v)) return true; return false; }
    return false;
}
inline Obj sanitize_nonfinite(const Obj& o) {'''),
 ('''    Obj obj = sanitize_nonfinite(*j);''', '''    Obj obj = has_nonfinite(*j) ? sanitize_nonfinite(*j) : std::move(*j);   // [NONFINITE_COPY_ONLY_IF_NEEDED_V1]'''),
 ('''    Obj obj = sanitize_nonfinite(*parsed);''', '''    Obj obj = has_nonfinite(*parsed) ? sanitize_nonfinite(*parsed) : std::move(*parsed);   // [NONFINITE_COPY_ONLY_IF_NEEDED_V1]'''),
 ('''        return dumps_ascii(sanitize_nonfinite(Obj(detail::coerce_array(arr))), false);''',
  '''        { Obj ca(detail::coerce_array(arr)); return dumps_ascii(has_nonfinite(ca) ? sanitize_nonfinite(ca) : ca, false); }   // [NONFINITE_COPY_ONLY_IF_NEEDED_V1]'''),
 ('''    return dumps_ascii(sanitize_nonfinite(obj), false);''',
  '''    return has_nonfinite(obj) ? dumps_ascii(sanitize_nonfinite(obj), false) : dumps_ascii(obj, false);   // [NONFINITE_COPY_ONLY_IF_NEEDED_V1]'''),
])
print("alloc once applied")
