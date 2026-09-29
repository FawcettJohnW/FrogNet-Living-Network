#!/bin/bash
# Oracle sensitivity for S3: each mutant is one deliberate bug in a copy of include/semtpl.hpp; the S3 oracle must FAIL
# on every one. The real header is never modified. usage: tests/semtpl_mutants.sh FROGNET_SEMANTIC_ROOT
set -u
cd "$(dirname "$0")/.."
ROOT=$1; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PY=$(command -v python3 || command -v python)
caught=0; total=0
mutant(){
  total=$((total+1)); rm -rf "$T/m"; mkdir -p "$T/m"; cp include/*.hpp "$T/m/"
  $PY - "$T/m/semtpl.hpp" "$2" "$3" <<'PYEOF' || { echo "MUTANT-SETUP-FAIL $1"; return; }
import sys; p, a, b = sys.argv[1:]; s = open(p).read()
if s.count(a) != 1: sys.exit('pattern not unique (%d): %s' % (s.count(a), a))
open(p, 'w').write(s.replace(a, b))
PYEOF
  g++ -std=c++17 -O2 -I"$T/m" tests/semtpl_driver.cpp -llz4 -lcrypto -o "$T/drv" 2> "$T/cc.txt" || { echo "MUTANT-BUILD-FAIL $1"; return; }
  r=$($PY tests/test_semtpl_oracle.py "$ROOT" --driver "$T/drv" 2>/dev/null | grep '^FAIL' | tr '\n' ';')
  if [ -n "$r" ]; then caught=$((caught+1)); echo "CAUGHT $1: $r"; else echo "MISSED $1"; fi
}
mutant "strip: ASCII whitespace only" 'inline bool is_space(uint32_t c) { for (uint32_t s : pyuni::SPACE) if (s == c) return true; return false; }' 'inline bool is_space(uint32_t c) { return c == 32 || (c >= 9 && c <= 13); }'
mutant "lower: ASCII only" 'for (uint32_t c : v) { const auto* m = find_map(pyuni_s3::LOWER, c); if (!m) o.push_back(c);' 'for (uint32_t c : v) { const pyuni_s3::Lower* m = nullptr; if (!m) o.push_back(c >= 65 && c <= 90 ? c + 32 : c);'
mutant "IGNORECASE [a-z0-9]: ASCII only" '    for (uint32_t x : pyuni_s3::ICASE_AZ09) if (x == c) return true;' '    if (c < 128) for (uint32_t x : pyuni_s3::ICASE_AZ09) if (x == c) return true;'
mutant "ensure_ascii escapes in upper-case hex" 'else if (c < 0x10000) { std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }' 'else if (c < 0x10000) { std::snprintf(b, sizeof b, "\\u%04X", c); o += b; }'
mutant "quote: lower-case percent hex" 'std::snprintf(buf, sizeof buf, "%%%02X", c);' 'std::snprintf(buf, sizeof buf, "%%%02x", c);'
mutant "parse_qsl drops blank values" '        if (nv.empty()) continue;' '        if (nv.empty() || nv.back() == 0x3d) continue;'
mutant "dict key: no \$-before-newline" '    std::string s = u::ends(k, "\n") ? k.substr(0, k.size() - 1) : k;' '    std::string s = k;'
mutant "field order not sorted" '        for (const auto& k : sorted_keys(d)) {' '        for (const auto& k : d.keys) {'
mutant "rebuild_json: no rows special case" '        if (parts[0] == "rows") {' '        if (false) {'
mutant "blob threshold 4096" 'if (ser.size() > 8192) {' 'if (ser.size() > 4096) {'
mutant "text sniff threshold 0.2" 'return double(bad) <= double(t.size()) * 0.15;' 'return double(bad) <= double(t.size()) * 0.2;'
mutant "cannot-hold returned as empty fields" '    if (!obj.is<Dict>()) return std::nullopt;' '    if (!obj.is<Dict>()) return Fields{};'
mutant "string path: equality, not suffix" '        if (p == n || u::ends(p, std::string(".") + n)) return true;' '        if (p == n) return true;'
mutant "merge: types must match to be equal" '        if (python_eq(ta, tb)) { r.set("type", ta); return r; }' '        if (ta.v.index() == tb.v.index() && python_eq(ta, tb)) { r.set("type", ta); return r; }'
echo "semtpl mutants caught: $caught of $total"
