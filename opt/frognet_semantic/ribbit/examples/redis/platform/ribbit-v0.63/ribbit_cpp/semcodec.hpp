/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// semcodec.hpp -- C++ port of John's core/codec.py: SemanticCodec, WIRE_VERSION 5 (semantic-engine port, slice S2).
//
// The Python module is the reference; tools/test_semcodec_oracle.py imports it and holds this file to it case for case.
//
// Semantic packet: [version:u8 = 5][flags:u8][opcode:u32][n:u16] then a field block, little-endian throughout.
//   flags: FLAG_COMPRESSED 0x01 (field block is one LZ4 frame, set only when that is smaller), FLAG_DIFF 0x02.
//   field: [idx:u16][type:u8][value]; NULL 0 (no bytes), RAW 1 ([len:u16][bytes]), STR 2 ([len:u16][UTF-8]),
//   INT 3 (i64), FLOAT 4 (f64), BOOL 5 (u8), JSON 7 ([len:u32][compact JSON, ensure_ascii=False]).
// Diff encoding compares each field against a per-(target, opcode) reference: no reference means send everything; all
// fields equal means identical (the caller sends REPEAT or SAME); otherwise only changed fields, by index.
//
// Python behaviour carried over deliberately, because the reference has it:
//   - _values_equal is EXACT since [EXACT_DIFF_V1] (2026-09-25): same type, floats bit for bit, dict keys in order.
//   - decode reads are clamped like Python slices: a STR/RAW/JSON length past the end yields the bytes that are there.
//   - STR is decoded with UTF-8 "replace" (one U+FFFD per maximal ill-formed subpart); JSON is json.loads of that text.
//   - LZ4: python-lz4 4.4.5's compress()/decompress() call for call on liblz4 (see PORT-LOG.md). Byte identity of the
//     compressed form needs the same liblz4 version on both sides (1.9.4 here); decompression does not.
// Representation limits, not behaviour: JSON and value nesting deeper than 900 raises RecursionError (Python's limit
// depends on its stack, about 1000); C++ strings that are not UTF-8/WTF-8 at all have no Python form and raise
// EncodeError where Python would encode.
#pragma once
#include "pyval.hpp"
#include "pyuni_tables.hpp"
#include <lz4frame.h>
#include <charconv>
#include <cmath>
#include <cstring>
#include <functional>
#include <memory>
#include <optional>
#include <string_view>

namespace semcodec {
using namespace pyv;
constexpr uint8_t WIRE_VERSION = 5, FLAG_COMPRESSED = 0x01, FLAG_DIFF = 0x02;
constexpr size_t REQ_HASH_LEN = 16, MAX_WIRE_FIELD_BYTES = 0xFFFF, MAX_INT_DIGITS = 4300, MAX_DEPTH = 900;
enum : uint8_t { TYPE_NULL = 0, TYPE_RAW = 1, TYPE_STR = 2, TYPE_INT = 3, TYPE_FLOAT = 4, TYPE_BOOL = 5, TYPE_JSON = 7 };
constexpr uint32_t ERROR_OPCODE = 0xFFFFFFFFu;
using Fields = std::vector<std::pair<std::string, Obj>>;
struct DiffResult { std::string bytes; Dict reference; bool identical = false; };

namespace detail {
inline void put(std::string& s, uint64_t v, int n) { for (int i = 0; i < n; ++i) s.push_back(char(uint8_t(v >> (8 * i)))); }
inline uint64_t get(std::string_view f, size_t off, int n) {
    uint64_t v = 0;
    for (int i = n - 1; i >= 0; --i) v = v << 8 | uint8_t(f[off + i]);
    return v;
}
// Python slice f[a:b] with clamping
inline std::string_view slice(std::string_view f, size_t a, size_t b) {
    if (a >= f.size() || b <= a) return {};
    return f.substr(a, std::min(b, f.size()) - a);
}
// struct.unpack of n bytes at p: struct.error unless exactly n are there
inline uint64_t unpack(std::string_view f, size_t p, int n) {
    if (p > f.size() || f.size() - p < size_t(n)) throw StructError("semcodec: unpack requires a buffer of " + std::to_string(n) + " bytes");
    return get(f, p, n);
}

inline void utf8_put(std::string& o, uint32_t c) {  // code point -> UTF-8 (surrogates as 3 bytes: WTF-8)
    if (c < 0x80) o.push_back(char(c));
    else if (c < 0x800) { o.push_back(char(0xC0 | c >> 6)); o.push_back(char(0x80 | (c & 0x3F))); }
    else if (c < 0x10000) { o.push_back(char(0xE0 | c >> 12)); o.push_back(char(0x80 | (c >> 6 & 0x3F))); o.push_back(char(0x80 | (c & 0x3F))); }
    else { o.push_back(char(0xF0 | c >> 18)); o.push_back(char(0x80 | (c >> 12 & 0x3F)));
           o.push_back(char(0x80 | (c >> 6 & 0x3F))); o.push_back(char(0x80 | (c & 0x3F))); }
}
// length of the run of ASCII bytes starting at i, eight at a time
inline size_t ascii_run(std::string_view s, size_t i) {
    size_t j = i, n = s.size();
    while (j + 8 <= n) { uint64_t w; std::memcpy(&w, s.data() + j, 8); if (w & 0x8080808080808080ull) break; j += 8; }
    while (j < n && uint8_t(s[j]) < 0x80) ++j;
    return j - i;
}
// bytes.decode("utf-8", "replace"): one U+FFFD per maximal subpart of an ill-formed sequence.
inline std::string utf8_replace(std::string_view s) {
    std::string o;
    o.reserve(s.size());
    size_t i = 0, n = s.size();
    while (i < n) {
        size_t a = ascii_run(s, i);
        if (a) { o.append(s.data() + i, a); i += a; continue; }
        uint8_t b = uint8_t(s[i]);
        int need; uint8_t lo = 0x80, hi = 0xBF;
        if (b >= 0xC2 && b <= 0xDF) need = 1;
        else if (b >= 0xE0 && b <= 0xEF) { need = 2; if (b == 0xE0) lo = 0xA0; if (b == 0xED) hi = 0x9F; }
        else if (b >= 0xF0 && b <= 0xF4) { need = 3; if (b == 0xF0) lo = 0x90; if (b == 0xF4) hi = 0x8F; }
        else { o += "\xEF\xBF\xBD"; ++i; continue; }
        size_t j = i + 1; bool ok = true;
        for (int k = 0; k < need; ++k, ++j) {
            if (j >= n || uint8_t(s[j]) < lo || uint8_t(s[j]) > hi) { ok = false; break; }
            lo = 0x80; hi = 0xBF;
        }
        if (ok) o.append(s.data() + i, j - i); else o += "\xEF\xBF\xBD";
        i = j;
    }
    return o;
}
// Walk a WTF-8 string (UTF-8 in which surrogate code points may appear as 3-byte sequences). f(cp, start, len) per
// code point; returns false if the bytes are not WTF-8.
template <class F> inline bool wtf8_walk(std::string_view s, F f) {
    size_t i = 0, n = s.size();
    while (i < n) {
        uint8_t b = uint8_t(s[i]);
        if (b < 0x80) { f(uint32_t(b), i, size_t(1)); ++i; continue; }
        int need; uint32_t c; uint8_t lo = 0x80, hi = 0xBF;
        if (b >= 0xC2 && b <= 0xDF) { need = 1; c = b & 0x1F; }
        else if (b >= 0xE0 && b <= 0xEF) { need = 2; c = b & 0x0F; if (b == 0xE0) lo = 0xA0; }
        else if (b >= 0xF0 && b <= 0xF4) { need = 3; c = b & 0x07; if (b == 0xF0) lo = 0x90; if (b == 0xF4) hi = 0x8F; }
        else return false;
        for (int k = 1; k <= need; ++k) {
            if (i + size_t(k) >= n) return false;
            uint8_t x = uint8_t(s[i + size_t(k)]);
            if (x < lo || x > hi) return false;
            lo = 0x80; hi = 0xBF;
            c = c << 6 | (x & 0x3F);
        }
        f(c, i, size_t(need + 1));
        i += size_t(need) + 1;
    }
    return true;
}
inline bool is_surrogate(uint32_t c) { return c >= 0xD800 && c <= 0xDFFF; }
// str.encode("utf-8") (strict): a lone surrogate raises UnicodeEncodeError.
inline void encode_strict(std::string_view s) {
    if (ascii_run(s, 0) == s.size()) return;
    bool sur = false;
    if (!wtf8_walk(s, [&](uint32_t c, size_t, size_t) { if (is_surrogate(c)) sur = true; }) || sur)
        throw EncodeError("semcodec: 'utf-8' codec can't encode a surrogate");
}
// str.encode("utf-8", "replace"): each lone surrogate becomes '?'.
inline std::string encode_replace(std::string_view s) {
    std::string o;
    o.reserve(s.size());
    if (!wtf8_walk(s, [&](uint32_t c, size_t at, size_t len) { if (is_surrogate(c)) o.push_back('?'); else o.append(s.data() + at, len); }))
        throw EncodeError("semcodec: not a UTF-8/WTF-8 string");
    return o;
}

inline std::string int_repr(const Int& i) {  // str(int); CPython refuses above 4300 digits
    std::string d = i.str();
    if (d.size() - (d[0] == '-') > MAX_INT_DIGITS) throw CpyValueError("Exceeds the limit (4300 digits) for integer string conversion");
    return d;
}
// int from decimal digits (ASCII, optional sign already split off): canonical Int.
inline Int int_from_digits(bool neg, std::string_view digits) {
    size_t z = 0;
    while (z + 1 < digits.size() && digits[z] == '0') ++z;
    std::string d(digits.substr(z));
    if (d == "0") neg = false;
    std::string full = (neg ? "-" : "") + d;
    int64_t v;
    auto r = std::from_chars(full.data(), full.data() + full.size(), v);
    if (r.ec == std::errc() && r.ptr == full.data() + full.size()) return Int::of(v);
    Int big; big.big = true; big.dec = full; return big;
}
// float(int): OverflowError past the double range, else correctly rounded
inline double int_to_float(const Int& i) {
    if (!i.big) return double(i.v);
    double d = std::strtod(i.dec.c_str(), nullptr);
    if (std::isinf(d)) throw OverflowError("int too large to convert to float");
    return d;
}

// repr(float)
inline std::string float_repr(double x) {
    char buf[64];
    auto r = std::to_chars(buf, buf + sizeof buf, x, std::chars_format::scientific);
    std::string s(buf, r.ptr), out;
    size_t p = 0;
    if (s[0] == '-') { out = "-"; p = 1; }
    size_t e = s.find('e');
    std::string digits;
    for (size_t k = p; k < e; ++k) if (s[k] != '.') digits.push_back(s[k]);
    int exp10 = std::stoi(s.substr(e + 1));
    int decpt = exp10 + 1, n = int(digits.size());
    if (decpt <= -4 || decpt > 16) {
        out += digits[0];
        if (n > 1) { out += '.'; out += digits.substr(1); }
        int ae = exp10 < 0 ? -exp10 : exp10;
        out += exp10 < 0 ? "e-" : "e+";
        if (ae < 10) out += '0';
        out += std::to_string(ae);
    } else if (decpt <= 0) {
        out += "0." + std::string(size_t(-decpt), '0') + digits;
    } else if (decpt >= n) {
        out += digits + std::string(size_t(decpt - n), '0') + ".0";
    } else {
        out += digits.substr(0, size_t(decpt)) + "." + digits.substr(size_t(decpt));
    }
    return out;
}

// json.dumps(v, separators=(",", ":"), ensure_ascii=False)
inline void dumps_str(std::string& o, std::string_view s) {
    static const char* hx = "0123456789abcdef";
    o.push_back('"');
    size_t i = 0;
    while (i < s.size()) {
        size_t j = i;
        while (j < s.size() && uint8_t(s[j]) >= 0x20 && s[j] != '"' && s[j] != '\\') ++j;
        o.append(s.data() + i, j - i);
        if (j == s.size()) break;
        char ch = s[j];
        uint8_t c = uint8_t(ch);
        i = j + 1;
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        case '\b': o += "\\b"; break;
        case '\f': o += "\\f"; break;
        default:
            if (c < 0x20) { o += "\\u00"; o.push_back(hx[c >> 4]); o.push_back(hx[c & 15]); }
            else o.push_back(ch);
        }
    }
    o.push_back('"');
}
inline void dumps(std::string& o, const Obj& v, size_t depth) {
    if (depth > MAX_DEPTH) throw RecursionError("semcodec: maximum recursion depth exceeded while encoding JSON");
    if (v.is<None>()) o += "null";
    else if (v.is<bool>()) o += v.as<bool>() ? "true" : "false";
    else if (v.is<Int>()) o += int_repr(v.as<Int>());
    else if (v.is<double>()) {
        double d = v.as<double>();
        if (std::isnan(d)) o += "NaN";
        else if (std::isinf(d)) o += d > 0 ? "Infinity" : "-Infinity";
        else o += float_repr(d);
    } else if (v.is<Str>()) dumps_str(o, v.as<Str>().s);
    else if (v.is<Bytes>()) throw TypeError("Object of type bytes is not JSON serializable");
    else if (v.is<List>()) {
        o.push_back('[');
        bool first = true;
        for (const Obj& x : v.as<List>()) { if (!first) o.push_back(','); first = false; dumps(o, x, depth + 1); }
        o.push_back(']');
    } else {
        const Dict& d = v.as<Dict>();
        o.push_back('{');
        for (size_t k = 0; k < d.size(); ++k) {
            if (k) o.push_back(',');
            dumps_str(o, d.keys[k]); o.push_back(':'); dumps(o, d.vals[k], depth + 1);
        }
        o.push_back('}');
    }
}

// json.loads(text) with Python's C scanner (_json.c) semantics; text is valid UTF-8 (it comes from utf8_replace).
struct JsonScan {
    std::string_view s;
    [[noreturn]] void fail(const char* why) { throw JsonError(std::string("semcodec: JSON: ") + why); }
    size_t ws(size_t i) const { while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) ++i; return i; }
    bool lit(size_t i, const char* w) const { size_t n = std::strlen(w); return s.size() - i >= n && s.compare(i, n, w) == 0; }
    static int hexv(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }
    // four hex digits at i; the scanner requires at least one more character after them
    uint32_t hex4(size_t i) {
        if (i + 4 >= s.size()) fail("Invalid \\uXXXX escape");
        uint32_t c = 0;
        for (size_t k = 0; k < 4; ++k) { int h = hexv(s[i + k]); if (h < 0) fail("Invalid \\uXXXX escape"); c = c << 4 | uint32_t(h); }
        return c;
    }
    std::string str(size_t& i) {  // i is just after the opening quote
        std::string o;
        for (;;) {
            size_t j = i;
            while (j < s.size() && s[j] != '"' && s[j] != '\\') {
                if (uint8_t(s[j]) <= 0x1F) fail("Invalid control character");
                ++j;
            }
            if (j >= s.size()) fail("Unterminated string");
            o.append(s.data() + i, j - i);
            if (s[j] == '"') { i = j + 1; return o; }
            ++j;
            if (j >= s.size()) fail("Unterminated string");
            char c = s[j];
            if (c != 'u') {
                switch (c) {
                case '"': o.push_back('"'); break;
                case '\\': o.push_back('\\'); break;
                case '/': o.push_back('/'); break;
                case 'b': o.push_back('\b'); break;
                case 'f': o.push_back('\f'); break;
                case 'n': o.push_back('\n'); break;
                case 'r': o.push_back('\r'); break;
                case 't': o.push_back('\t'); break;
                default: fail("Invalid \\escape");
                }
                i = j + 1;
                continue;
            }
            size_t next = j + 1;
            uint32_t cp = hex4(next);
            size_t end = next + 4;
            if (cp >= 0xD800 && cp <= 0xDBFF && end + 6 < s.size() && s[end] == '\\' && s[end + 1] == 'u') {
                uint32_t c2 = hex4(end + 2);
                if (c2 >= 0xDC00 && c2 <= 0xDFFF) { cp = 0x10000 + (((cp - 0xD800) << 10) | (c2 - 0xDC00)); end += 6; }
            }
            utf8_put(o, cp);
            i = end;
        }
    }
    Obj number(size_t& i) {
        size_t start = i, n = s.size();
        if (i >= n) fail("Expecting value");
        size_t end_idx = n - 1, idx = i;
        auto dig = [&](size_t k) { return k < n && s[k] >= '0' && s[k] <= '9'; };
        if (s[idx] == '-') { ++idx; if (idx > end_idx) fail("Expecting value"); }
        if (s[idx] >= '1' && s[idx] <= '9') { ++idx; while (idx <= end_idx && dig(idx)) ++idx; }
        else if (s[idx] == '0') ++idx;
        else fail("Expecting value");
        bool is_float = false;
        if (idx < end_idx && s[idx] == '.' && dig(idx + 1)) { is_float = true; idx += 2; while (idx <= end_idx && dig(idx)) ++idx; }
        if (idx < end_idx && (s[idx] == 'e' || s[idx] == 'E')) {
            size_t e_start = idx; ++idx;
            if (idx < end_idx && (s[idx] == '-' || s[idx] == '+')) ++idx;
            while (idx <= end_idx && dig(idx)) ++idx;
            if (dig(idx - 1)) is_float = true; else idx = e_start;
        }
        std::string_view t = s.substr(start, idx - start);
        i = idx;
        if (is_float) return std::strtod(std::string(t).c_str(), nullptr);
        bool neg = t[0] == '-';
        std::string_view d = t.substr(neg ? 1 : 0);
        if (d.size() > MAX_INT_DIGITS) throw CpyValueError("Exceeds the limit (4300 digits) for integer string conversion");
        return int_from_digits(neg, d);
    }
    Obj value(size_t& i, size_t depth) {
        if (i >= s.size()) fail("Expecting value");
        switch (s[i]) {
        case '"': ++i; return Str{str(i)};
        case '{': return object(i, depth);
        case '[': return array(i, depth);
        case 'n': if (lit(i, "null")) { i += 4; return None{}; } break;
        case 't': if (lit(i, "true")) { i += 4; return true; } break;
        case 'f': if (lit(i, "false")) { i += 5; return false; } break;
        case 'N': if (lit(i, "NaN")) { i += 3; return std::nan(""); } break;
        case 'I': if (lit(i, "Infinity")) { i += 8; return HUGE_VAL; } break;
        case '-': if (lit(i, "-Infinity")) { i += 9; return -HUGE_VAL; } break;
        }
        return number(i);
    }
    Obj object(size_t& i, size_t depth) {
        if (depth + 1 > MAX_DEPTH) throw RecursionError("semcodec: maximum recursion depth exceeded while decoding JSON");
        Dict d;
        std::vector<size_t> hashes;   // parallel to d.keys: a repeated key replaces its value in place (dict semantics)
        size_t idx = ws(i + 1);
        if (idx >= s.size() || s[idx] != '}') {
            for (;;) {
                if (idx >= s.size() || s[idx] != '"') fail("Expecting property name enclosed in double quotes");
                ++idx;
                std::string k = str(idx);
                idx = ws(idx);
                if (idx >= s.size() || s[idx] != ':') fail("Expecting ':' delimiter");
                idx = ws(idx + 1);
                Obj v = value(idx, depth + 1);
                size_t h = std::hash<std::string>()(k), at = 0;
                while (at < hashes.size() && !(hashes[at] == h && d.keys[at] == k)) ++at;
                if (at < hashes.size()) d.vals[at] = std::move(v);
                else { hashes.push_back(h); d.keys.push_back(std::move(k)); d.vals.push_back(std::move(v)); }
                idx = ws(idx);
                if (idx < s.size() && s[idx] == '}') break;
                if (idx >= s.size() || s[idx] != ',') fail("Expecting ',' delimiter");
                idx = ws(idx + 1);
            }
        }
        i = idx + 1;
        return d;
    }
    Obj array(size_t& i, size_t depth) {
        if (depth + 1 > MAX_DEPTH) throw RecursionError("semcodec: maximum recursion depth exceeded while decoding JSON");
        List l;
        size_t idx = ws(i + 1);
        if (idx >= s.size() || s[idx] != ']') {
            for (;;) {
                l.push_back(value(idx, depth + 1));
                idx = ws(idx);
                if (idx < s.size() && s[idx] == ']') break;
                if (idx >= s.size() || s[idx] != ',') fail("Expecting ',' delimiter");
                idx = ws(idx + 1);
            }
        }
        i = idx + 1;
        return l;
    }
};

inline void lz4_check(size_t r, const char* what) {
    if (LZ4F_isError(r)) throw Lz4Error(std::string("semcodec: ") + what + " failed with code: " + LZ4F_getErrorName(r));
}
struct DctxFree { void operator()(LZ4F_dctx* c) const { LZ4F_freeDecompressionContext(c); } };
}  // namespace detail

inline std::string json_dumps(const Obj& v) { std::string o; detail::dumps(o, v, 0); return o; }
// json.loads(text); text is the UTF-8 the codec produced with "replace" decoding
inline Obj json_loads(std::string_view text) {
    detail::JsonScan sc{text};
    if (text.size() >= 3 && text.compare(0, 3, "\xEF\xBB\xBF") == 0) sc.fail("Unexpected UTF-8 BOM");
    size_t i = sc.ws(0);
    Obj v = sc.value(i, 0);
    i = sc.ws(i);
    if (i != text.size()) sc.fail("Extra data");
    return v;
}

// lz4.frame.compress(data) with python-lz4's defaults
inline std::string lz4_compress(std::string_view data) {
    LZ4F_preferences_t p;
    std::memset(&p, 0, sizeof p);
    p.frameInfo.contentChecksumFlag = LZ4F_noContentChecksum;
    p.frameInfo.blockMode = LZ4F_blockLinked;
    p.frameInfo.blockChecksumFlag = LZ4F_noBlockChecksum;
    p.autoFlush = 0;
    p.frameInfo.contentSize = data.size();
    size_t bound = LZ4F_compressFrameBound(data.size(), &p);
    std::string out(bound, '\0');
    size_t n = LZ4F_compressFrame(out.data(), out.size(), data.data(), data.size(), &p);
    detail::lz4_check(n, "LZ4F_compressFrame");
    out.resize(n);
    return out;
}
// lz4.frame.decompress(data): python-lz4's loop -- to the end of the first frame; trailing bytes are ignored.
// The context is per thread and reset on every call (LZ4F_resetDecompressionContext, liblz4 >= 1.8.0): the same
// fresh state python-lz4 gets from creating one per call, without re-allocating its 64 KiB block buffers each time.
inline std::string lz4_decompress(std::string_view data) {
    thread_local std::unique_ptr<LZ4F_dctx, detail::DctxFree> ctx;
    if (!ctx) {
        LZ4F_dctx* raw = nullptr;
        detail::lz4_check(LZ4F_createDecompressionContext(&raw, LZ4F_VERSION), "LZ4F_createDecompressionContext");
        ctx.reset(raw);
    }
    LZ4F_resetDecompressionContext(ctx.get());
    const char* cur = data.data();
    const char* end = data.data() + data.size();
    size_t remain = data.size(), rd = data.size();
    LZ4F_frameInfo_t fi;
    detail::lz4_check(LZ4F_getFrameInfo(ctx.get(), &fi, cur, &rd), "LZ4F_getFrameInfo");
    cur += rd; remain -= rd;
    size_t dsize = fi.contentSize > 0 ? size_t(fi.contentSize) : 2 * remain;
    std::string dst(dsize + 1, '\0');  // +1: never hand liblz4 a null pointer; the logical size is dsize
    LZ4F_decompressOptions_t opt;
    std::memset(&opt, 0, sizeof opt);
    size_t src_read = remain, dw = dsize, written = 0, r = 0, factor = 1;
    for (;;) {
        r = LZ4F_decompress(ctx.get(), dst.data() + written, &dw, cur, &src_read, &opt);
        detail::lz4_check(r, "LZ4F_decompress");
        written += dw; cur += src_read; src_read = size_t(end - cur);
        if (r == 0) break;
        if (cur == end) break;
        if (written == dsize) { factor *= 2; dsize *= factor; dst.resize(dsize + 1); }
        dw = dsize - written;
    }
    if (r > 0) throw Lz4Error("semcodec: Frame incomplete. LZ4F_decompress returned: " + std::to_string(r));
    dst.resize(written);
    return dst;
}
// _lz4_smart: compress only when the frame is smaller than the input
inline std::pair<std::string, bool> lz4_smart(std::string_view payload) {
    if (payload.empty()) return {std::string(payload), false};
    std::string c = lz4_compress(payload);
    if (c.size() < payload.size()) return {std::move(c), true};
    return {std::string(payload), false};
}

// _values_equal -- [EXACT_DIFF_V1], as core/codec.py after its fix: equal means the receiver would rebuild the same value.
// Same type (bool, int and float are different types), floats bit for bit (0.0 != -0.0; a NaN equals the same NaN),
// dict keys in the same order, lists element by element. No tolerance: a change judged equal is never sent.
inline bool values_equal(const Obj& a, const Obj& b) {
    if (a.v.index() != b.v.index()) return false;
    if (a.is<None>()) return true;
    if (a.is<double>()) { double x = a.as<double>(), y = b.as<double>(); return std::memcmp(&x, &y, sizeof x) == 0; }
    if (a.is<Dict>()) {
        const Dict& x = a.as<Dict>(); const Dict& y = b.as<Dict>();
        if (x.keys != y.keys) return false;
        for (size_t k = 0; k < x.size(); ++k) if (!values_equal(x.vals[k], y.vals[k])) return false;
        return true;
    }
    if (a.is<List>()) {
        const List& x = a.as<List>(); const List& y = b.as<List>();
        if (x.size() != y.size()) return false;
        for (size_t k = 0; k < x.size(); ++k) if (!values_equal(x[k], y[k])) return false;
        return true;
    }
    if (a.is<bool>()) return a.as<bool>() == b.as<bool>();
    if (a.is<Int>()) return a.as<Int>() == b.as<Int>();
    if (a.is<Str>()) return a.as<Str>().s == b.as<Str>().s;
    return a.as<Bytes>().b == b.as<Bytes>().b;
}

inline uint8_t type_id(const Obj& v) {
    if (v.is<None>()) return TYPE_NULL;
    if (v.is<List>() || v.is<Dict>()) return TYPE_JSON;
    if (v.is<bool>()) return TYPE_BOOL;
    if (v.is<Int>()) return TYPE_INT;
    if (v.is<double>()) return TYPE_FLOAT;
    if (v.is<Bytes>()) return TYPE_RAW;
    return TYPE_STR;
}

namespace detail {
inline void encode_value(std::string& fb, uint8_t t, const Obj& v) {
    switch (t) {
    case TYPE_NULL: return;
    case TYPE_JSON: {
        std::string data = json_dumps(v);
        encode_strict(data);
        if (data.size() > 0xFFFFFFFFu) throw StructError("semcodec: JSON length does not fit u32");
        put(fb, data.size(), 4); fb += data; return;
    }
    case TYPE_RAW: {
        const std::string& b = v.as<Bytes>().b;
        if (b.size() > MAX_WIRE_FIELD_BYTES)
            throw ValueError("TYPE_RAW over 65535-byte wire field: " + std::to_string(b.size()) + " bytes");
        put(fb, b.size(), 2); fb += b; return;
    }
    case TYPE_STR: {
        const std::string& s = v.as<Str>().s;
        encode_strict(s);
        if (s.size() > MAX_WIRE_FIELD_BYTES)
            throw ValueError("TYPE_STR over 65535-byte wire field: " + std::to_string(s.size()) + " bytes");
        put(fb, s.size(), 2); fb += s; return;
    }
    case TYPE_INT: {
        const Int& i = v.as<Int>();
        if (i.big) throw ValueError("TYPE_INT does not fit signed int64 wire field: " + int_repr(i));
        put(fb, uint64_t(i.v), 8); return;
    }
    case TYPE_FLOAT: { double d = v.as<double>(); uint64_t u; std::memcpy(&u, &d, 8); put(fb, u, 8); return; }
    case TYPE_BOOL: fb.push_back(char(v.as<bool>() ? 1 : 0)); return;
    }
}
}  // namespace detail
// [HOLDABLE_V1] Whether a value can be carried in a wire field at all: a str or raw over the 65535-byte field limit
// cannot, and the engines answer such a message RAW instead of failing mid-encode.
inline bool fits_wire(const Obj& v) {
    if (v.is<Str>()) return v.as<Str>().s.size() <= MAX_WIRE_FIELD_BYTES;
    if (v.is<Bytes>()) return v.as<Bytes>().b.size() <= MAX_WIRE_FIELD_BYTES;
    return true;
}
inline bool fits_wire(const Fields& f) { for (auto& kv : f) if (!fits_wire(kv.second)) return false; return true; }
namespace detail {
inline void encode_field(std::string& fb, size_t idx, const Obj& v) {
    if (idx > 0xFFFF) throw StructError("semcodec: field index does not fit u16");
    uint8_t t = type_id(v);
    put(fb, idx, 2); fb.push_back(char(t));
    encode_value(fb, t, v);
}
inline std::string finish(uint64_t opcode, uint8_t flags, size_t n, std::string payload, bool compress) {
    if (compress && !payload.empty()) {
        auto c = lz4_smart(payload);
        if (c.second) { payload = std::move(c.first); flags |= FLAG_COMPRESSED; }
    }
    if (opcode > 0xFFFFFFFFu) throw StructError("semcodec: opcode does not fit u32");
    if (n > 0xFFFF) throw StructError("semcodec: field count does not fit u16");
    std::string out;
    out.reserve(8 + payload.size());
    out.push_back(char(WIRE_VERSION)); out.push_back(char(flags)); put(out, opcode, 4); put(out, n, 2);
    out += payload;
    return out;
}
template <class Each> inline void merged(const Fields& a, const Fields* b, Each each) {
    size_t idx = 0;
    for (const auto& f : a) each(idx++, f);
    if (b) for (const auto& f : *b) each(idx++, f);
}
inline std::string encode_full(uint64_t opcode, const Fields& a, const Fields* b, bool compress) {
    std::string fb;
    size_t n = 0;
    merged(a, b, [&](size_t idx, const std::pair<std::string, Obj>& f) { encode_field(fb, idx, f.second); n = idx + 1; });
    return finish(opcode, 0, n, std::move(fb), compress);
}
inline DiffResult encode_diff(uint64_t opcode, const Fields& a, const Fields* b, const Dict* reference, bool compress) {
    DiffResult r;
    std::vector<std::pair<size_t, const Obj*>> changed;
    static const Obj none{None{}};
    merged(a, b, [&](size_t idx, const std::pair<std::string, Obj>& f) {
        r.reference.set(f.first, f.second);
        if (reference) {
            long at = reference->find(f.first);
            const Obj& ref = at >= 0 ? reference->vals[size_t(at)] : none;
            if (!values_equal(f.second, ref)) changed.emplace_back(idx, &f.second);
        } else {
            changed.emplace_back(idx, &f.second);
        }
    });
    if (changed.empty() && reference) { r.identical = true; return r; }
    std::string fb;
    for (auto& c : changed) encode_field(fb, c.first, *c.second);
    r.bytes = finish(opcode, FLAG_DIFF, changed.size(), std::move(fb), compress);
    return r;
}

using Vals = std::vector<std::pair<size_t, Obj>>;   // Python dict idx -> value; a repeated idx overwrites
inline void vset(Vals& vals, size_t idx, Obj v) {
    for (auto& p : vals) if (p.first == idx) { p.second = std::move(v); return; }
    vals.emplace_back(idx, std::move(v));
}
inline const Obj* vget(const Vals& vals, size_t idx) {
    for (auto& p : vals) if (p.first == idx) return &p.second;
    return nullptr;
}
inline Vals decode_fieldblock(std::string_view fb, size_t n) {
    Vals vals;
    size_t p = 0;
    for (size_t k = 0; k < n; ++k) {
        if (p > fb.size() || fb.size() - p < 3) throw StructError("semcodec: unpack requires a buffer of 3 bytes");
        size_t idx = size_t(get(fb, p, 2));
        uint8_t t = uint8_t(fb[p + 2]);
        p += 3;
        switch (t) {
        case TYPE_NULL: vset(vals, idx, None{}); break;
        case TYPE_JSON: {
            size_t L = size_t(unpack(fb, p, 4)); p += 4;
            vset(vals, idx, json_loads(utf8_replace(slice(fb, p, p + L)))); p += L; break;
        }
        case TYPE_RAW: { size_t L = size_t(unpack(fb, p, 2)); p += 2; vset(vals, idx, Bytes{std::string(slice(fb, p, p + L))}); p += L; break; }
        case TYPE_STR: { size_t L = size_t(unpack(fb, p, 2)); p += 2; vset(vals, idx, Str{utf8_replace(slice(fb, p, p + L))}); p += L; break; }
        case TYPE_INT: vset(vals, idx, Int::of(int64_t(unpack(fb, p, 8)))); p += 8; break;
        case TYPE_FLOAT: { uint64_t u = unpack(fb, p, 8); double d; std::memcpy(&d, &u, 8); vset(vals, idx, d); p += 8; break; }
        case TYPE_BOOL:
            if (p >= fb.size()) throw IndexError("semcodec: index out of range");
            vset(vals, idx, bool(fb[p] != 0)); p += 1; break;
        default: throw ValueError("Unknown type_id " + std::to_string(t));
        }
    }
    return vals;
}
struct Decoded { bool diff; size_t n; Vals vals; };
inline Decoded decode_packet(std::string_view packet) {
    if (packet.size() < 8) throw StructError("semcodec: unpack requires a buffer of 8 bytes");
    uint8_t version = uint8_t(packet[0]), flags = uint8_t(packet[1]);
    size_t n = size_t(get(packet, 6, 2));
    if (version != WIRE_VERSION) throw ValueError("Unsupported semantic wire version " + std::to_string(version));
    std::string_view fb = packet.substr(8);
    std::string plain;
    if (flags & FLAG_COMPRESSED) { plain = lz4_decompress(fb); fb = plain; }
    return Decoded{(flags & FLAG_DIFF) != 0, n, decode_fieldblock(fb, n)};
}
inline void merge_reference(Decoded& d, const std::vector<std::string>& fields, const Dict* reference) {
    if (!(d.diff && reference && reference->size())) return;
    for (size_t idx = 0; idx < fields.size(); ++idx) {
        long at = reference->find(fields[idx]);
        if (!vget(d.vals, idx) && at >= 0) d.vals.emplace_back(idx, reference->vals[size_t(at)]);
    }
}
// the value for idx, moved out: every template position is a distinct index, so each is read exactly once
inline Obj take_or_none(Decoded& d, size_t idx) {
    for (auto& p : d.vals) if (p.first == idx) return std::move(p.second);
    return None{};
}

// int(text) as Python parses a str: non-ASCII whitespace -> ' ', Unicode decimal digits -> ASCII, then base 10.
inline std::optional<Int> py_int(std::string_view text) {
    std::string a;
    bool bad = false;
    wtf8_walk(text, [&](uint32_t c, size_t, size_t) {
        if (c < 128) { a.push_back(char(c)); return; }
        for (uint32_t sp : pyuni::SPACE) if (sp == c) { a.push_back(' '); return; }
        for (const auto& r : pyuni::DIGITS) if (c >= r.first && c < r.first + r.count) { a.push_back(char('0' + (c - r.first))); return; }
        bad = true;
    });
    if (bad) return std::nullopt;
    auto sp = [](char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r'; };
    size_t i = 0, n = a.size();
    while (i < n && sp(a[i])) ++i;
    bool neg = false;
    if (i < n && (a[i] == '+' || a[i] == '-')) { neg = a[i] == '-'; ++i; }
    std::string digits;
    if (i >= n || !(a[i] >= '0' && a[i] <= '9')) return std::nullopt;
    while (i < n) {
        if (a[i] >= '0' && a[i] <= '9') { digits.push_back(a[i]); ++i; }
        else if (a[i] == '_' && i + 1 < n && a[i + 1] >= '0' && a[i + 1] <= '9') ++i;
        else break;
    }
    while (i < n && sp(a[i])) ++i;
    if (i != n || digits.size() > MAX_INT_DIGITS) return std::nullopt;
    return int_from_digits(neg, digits);
}
}  // namespace detail

inline std::string encode_request(uint64_t opcode, const Fields& url_vals, const Fields& json_vals, bool compress = true) {
    return detail::encode_full(opcode, url_vals, &json_vals, compress);
}
inline std::string encode_reply(uint64_t opcode, const Fields& dynamic_vals, bool compress = true) {
    return detail::encode_full(opcode, dynamic_vals, nullptr, compress);
}
// reference: nullptr = never sent (send everything); otherwise the values last sent for this (target, opcode)
inline DiffResult encode_request_diff(uint64_t opcode, const Fields& url_vals, const Fields& json_vals, const Dict* reference,
                                      bool compress = true) {
    return detail::encode_diff(opcode, url_vals, &json_vals, reference, compress);
}
inline DiffResult encode_reply_diff(uint64_t opcode, const Fields& dynamic_vals, const Dict* reference, bool compress = true) {
    return detail::encode_diff(opcode, dynamic_vals, nullptr, reference, compress);
}
// msg: UTF-8 (lone surrogates as WTF-8 become '?', as Python's "replace" does)
inline std::string encode_error_reply(const Int& status, std::string_view msg) {
    std::string payload = detail::encode_replace(detail::int_repr(status) + ":" + std::string(msg));
    if (payload.size() > 0xFFFF) throw StructError("semcodec: error message does not fit u16");
    std::string out;
    out.push_back(char(WIRE_VERSION)); out.push_back(0); detail::put(out, ERROR_OPCODE, 4); detail::put(out, 1, 2);
    detail::put(out, 0, 2); out.push_back(char(TYPE_STR)); detail::put(out, payload.size(), 2); out += payload;
    return out;
}
inline std::optional<std::pair<Int, std::string>> decode_error_reply(std::string_view blob) {
    if (blob.size() < 13) return std::nullopt;
    if (detail::get(blob, 2, 4) != ERROR_OPCODE) return std::nullopt;
    size_t n = size_t(detail::get(blob, 11, 2));
    std::string text = detail::utf8_replace(detail::slice(blob, 13, 13 + n));
    size_t colon = text.find(':');
    std::string status = text.substr(0, colon), msg = colon == std::string::npos ? "" : text.substr(colon + 1);
    // [NO_FALLBACK_ERROR_REPLY_V1] nullopt answers only "not an error frame". An error frame whose status is not an
    // integer raises, as int() does in core/codec.py; it used to read as "not an error" and lose the reason.
    auto st = detail::py_int(status);
    if (!st) throw CpyValueError("decode_error_reply: error frame status " + status + " is not an integer");
    return std::make_pair(*st, msg);
}
inline std::pair<Fields, Fields> decode_request(std::string_view packet, const std::vector<std::string>& url_query_keys,
                                                const std::vector<std::string>& json_field_order, const Dict* reference) {
    detail::Decoded d = detail::decode_packet(packet);
    std::vector<std::string> all = url_query_keys;
    all.insert(all.end(), json_field_order.begin(), json_field_order.end());
    detail::merge_reference(d, all, reference);
    std::pair<Fields, Fields> out;
    for (size_t i = 0; i < url_query_keys.size(); ++i) out.first.emplace_back(url_query_keys[i], detail::take_or_none(d, i));
    size_t base = url_query_keys.size();
    for (size_t j = 0; j < json_field_order.size(); ++j) out.second.emplace_back(json_field_order[j], detail::take_or_none(d, base + j));
    return out;
}
inline Fields decode_reply(std::string_view packet, const std::vector<std::string>& field_order, const Dict* reference) {
    detail::Decoded d = detail::decode_packet(packet);
    detail::merge_reference(d, field_order, reference);
    Fields out;
    if (field_order.empty()) {
        for (size_t i = 0; i < d.n; ++i) out.emplace_back("field" + std::to_string(i), detail::take_or_none(d, i));
        return out;
    }
    for (size_t i = 0; i < field_order.size(); ++i) out.emplace_back(field_order[i], detail::take_or_none(d, i));
    return out;
}

}  // namespace semcodec
