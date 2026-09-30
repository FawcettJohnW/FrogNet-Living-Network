#!/bin/bash
# Oracle sensitivity for S2: each mutant is one deliberate bug in a copy of include/semcodec.hpp; the S2 oracle must
# FAIL on every one. The real header is never modified. usage: tests/semcodec_mutants.sh FROGNET_SEMANTIC_ROOT
set -u
cd "$(dirname "$0")/.."
ROOT=$1; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PY=$(command -v python3 || command -v python)
caught=0; total=0
mutant(){
  total=$((total+1)); rm -rf "$T/m"; mkdir -p "$T/m"; cp include/semcodec.hpp include/pyval.hpp include/pyuni_tables.hpp "$T/m/"
  $PY - "$T/m/semcodec.hpp" "$2" "$3" <<'PYEOF' || { echo "MUTANT-SETUP-FAIL $1"; return; }
import sys; p, a, b = sys.argv[1:]; s = open(p).read()
if s.count(a) != 1: sys.exit('pattern not unique (%d): %s' % (s.count(a), a))
open(p, 'w').write(s.replace(a, b))
PYEOF
  g++ -std=c++17 -O2 -I"$T/m" tests/semcodec_driver.cpp -llz4 -o "$T/drv" 2> "$T/cc.txt" || { echo "MUTANT-BUILD-FAIL $1"; return; }
  r=$($PY tests/test_semcodec_oracle.py "$ROOT" --driver "$T/drv" 2>/dev/null | grep '^FAIL' | tr '\n' ';')
  if [ -n "$r" ]; then caught=$((caught+1)); echo "CAUGHT $1: $r"; else echo "MISSED $1"; fi
}
mutant "float equality by value, not bits (0.0 == -0.0)" 'return std::memcmp(&x, &y, sizeof x) == 0; }' 'return x == y; }'
mutant "dict key order ignored" 'if (x.keys != y.keys) return false;' 'if (x.size() != y.size()) return false;'
mutant "bool equals int (True == 1)" '    if (a.v.index() != b.v.index()) return false;' '    if (a.is<bool>() && b.is<Int>()) return !b.as<Int>().big && b.as<Int>().v == (a.as<bool>() ? 1 : 0);
    if (a.v.index() != b.v.index()) return false;'
mutant "UTF-8 E0 lower bound" 'if (b == 0xE0) lo = 0xA0; if (b == 0xED) hi = 0x9F;' 'if (b == 0xED) hi = 0x9F;'
mutant "repr exponent threshold" 'if (decpt <= -4 || decpt > 16) {' 'if (decpt <= -4 || decpt > 15) {'
mutant "JSON \\u00XX upper case" 'static const char* hx = "0123456789abcdef";' 'static const char* hx = "0123456789ABCDEF";'
mutant "high-surrogate range to DFFF" 'if (cp >= 0xD800 && cp <= 0xDBFF && end + 6' 'if (cp >= 0xD800 && cp <= 0xDFFF && end + 6'
mutant "lz4_smart keeps equal size" 'if (c.size() < payload.size()) return {std::move(c), true};' 'if (c.size() <= payload.size()) return {std::move(c), true};'
mutant "STR slice not clamped" 'vset(vals, idx, Str{utf8_replace(slice(fb, p, p + L))});' 'if (p + L > fb.size()) throw StructError("short"); vset(vals, idx, Str{utf8_replace(slice(fb, p, p + L))});'
mutant "int() allows trailing underscore" "else if (a[i] == '_' && i + 1 < n && a[i + 1] >= '0' && a[i + 1] <= '9') ++i;" "else if (a[i] == '_') ++i;"
mutant "JSON leading zero takes digits" "else if (s[idx] == '0') ++idx;" "else if (s[idx] == '0') { ++idx; while (idx <= end_idx && dig(idx)) ++idx; }"
A='const Obj& ref = at >= 0 ? reference->vals[size_t(at)] : none;'
B='if (at < 0) { changed.emplace_back(idx, &f.second); return; } const Obj& ref = reference->vals[size_t(at)];'
mutant "absent reference field counts as changed" "$A" "$B"
mutant "JSON duplicate key appended" 'if (at < hashes.size()) d.vals[at] = std::move(v);' 'if (false) d.vals[at] = std::move(v);'
mutant "diff ignores empty reference" 'if (changed.empty() && reference) { r.identical = true; return r; }' 'if (changed.empty() && reference && reference->size()) { r.identical = true; return r; }'
mutant "int digit limit off by one" 'if (i != n || digits.size() > MAX_INT_DIGITS) return std::nullopt;' 'if (i != n || digits.size() >= MAX_INT_DIGITS) return std::nullopt;'
echo "mutants caught: $caught of $total"
[ "$caught" = "$total" ]
