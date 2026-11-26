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
// bagtext.hpp -- [A_STORED_BAG_IS_ASCII_V1] (John 2026-11-26: "The stored bag should be ASCII. Convert the binary before
// storing it.")
//
// A bag written as text is stored in one form, whoever wrote it and however they spaced or escaped it: the form the
// wire rebuilds a bag in (semtpl::dumps_ascii, compact) and the form a bag written as fields already reads back in
// (bagarr.hpp json()). That form is ASCII: every character outside printable ASCII is a \uXXXX escape (a surrogate
// pair above U+FFFF), there is no space between tokens, an integer is its digits and a float is its shortest
// round-trip text (Python's repr). So what a reader is given is the same bytes whether it came whole or was rebuilt
// from a difference (ramrows.hpp), and nothing binary is in the memory's text.
//
// One pass over the text; no tree is built ([WRITE_BODY_SKIMMED_V1] stays true). For valid JSON without duplicate keys
// the result is byte for byte semtpl::dumps_ascii(semcodec::json_loads(text), false).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>
#include "semcodec.hpp"

namespace ribbit {
namespace bagtext {
inline void put(std::string& o, uint32_t c) {                 // one character, as semtpl::detail::dumps_ascii_str writes it
    char b[16];
    if (c == '"') o += "\\\""; else if (c == '\\') o += "\\\\"; else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r";
    else if (c == '\t') o += "\\t"; else if (c == '\b') o += "\\b"; else if (c == '\f') o += "\\f";
    else if (c >= 0x20 && c <= 0x7e) o += char(c);
    else if (c < 0x10000) { std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else { uint32_t v = c - 0x10000; std::snprintf(b, sizeof b, "\\u%04x\\u%04x", 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)); o += b; }
}
inline int hex4(std::string_view t, size_t i) {               // the four hex digits at i, or -1
    if (i + 4 > t.size()) return -1;
    int v = 0;
    for (size_t k = 0; k < 4; ++k) { char c = t[i + k]; v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0'; else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10; else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10; else return -1; }
    return v;
}
// the string that starts at t[i] == '"': written to o in the stored form; i moves past its closing quote
inline void string(std::string& o, std::string_view t, size_t& i) {
    o += '"'; ++i;
    while (i < t.size()) {
        size_t r = i;                                           // a run that is stored as it is
        while (r < t.size()) { uint8_t c = uint8_t(t[r]); if (c < 0x20 || c > 0x7e || c == '"' || c == '\\') break; ++r; }
        o.append(t.data() + i, r - i); i = r;
        if (i >= t.size()) break;
        uint8_t c = uint8_t(t[i]);
        if (c == '"') { ++i; break; }
        if (c == '\\') {
            char e = i + 1 < t.size() ? t[i + 1] : '\\'; i += 2;
            switch (e) {
                case 'n': put(o, '\n'); break; case 'r': put(o, '\r'); break; case 't': put(o, '\t'); break;
                case 'b': put(o, '\b'); break; case 'f': put(o, '\f'); break;
                case 'u': { int h = hex4(t, i);
                    if (h < 0) { put(o, 0xFFFD); break; }
                    i += 4; uint32_t cp = uint32_t(h);
                    if (cp >= 0xD800 && cp < 0xDC00 && i + 1 < t.size() && t[i] == '\\' && t[i + 1] == 'u') {   // a pair: one character
                        int lo = hex4(t, i + 2);
                        if (lo >= 0xDC00 && lo < 0xE000) { i += 6; cp = 0x10000 + ((cp - 0xD800) << 10) + uint32_t(lo - 0xDC00); }
                    }
                    put(o, cp); break; }
                default: put(o, uint8_t(e)); break;             // \" \\ \/
            }
            continue;
        }
        if (c < 0x80) { put(o, c); ++i; continue; }             // a control character
        // UTF-8: one character of 2, 3 or 4 bytes; a byte that is not one is U+FFFD
        int n = (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2 : (c & 0xF8) == 0xF0 ? 3 : -1;
        uint32_t cp = n == 1 ? c & 0x1F : n == 2 ? c & 0x0F : c & 0x07; bool ok = n > 0 && i + size_t(n) < t.size();
        if (ok) for (int k = 1; k <= n; ++k) { uint8_t x = uint8_t(t[i + size_t(k)]); if ((x & 0xC0) != 0x80) { ok = false; break; } cp = cp << 6 | (x & 0x3F); }
        if (!ok) { put(o, 0xFFFD); ++i; continue; }
        put(o, cp); i += size_t(n) + 1;
    }
    o += '"';
}
// the number that starts at t[i]: an integer as its digits, a float as its shortest round-trip text; i moves past it
inline void number(std::string& o, std::string_view t, size_t& i) {
    size_t r = i; bool is_float = false;
    while (r < t.size()) { char c = t[r];
        if (c == '.' || c == 'e' || c == 'E') is_float = true;
        else if (!((c >= '0' && c <= '9') || c == '-' || c == '+')) break;
        ++r; }
    if (r == i) { o += t[i++]; return; }                        // not a number: stored as it is
    if (!is_float) { if (r - i == 2 && t[i] == '-' && t[i + 1] == '0') o += '0'; else o.append(t.data() + i, r - i); i = r; return; }
    std::string tok(t.substr(i, r - i)); char* e = nullptr; double d = std::strtod(tok.c_str(), &e);
    if (e != tok.c_str() + tok.size()) o += tok;
    else if (std::isnan(d)) o += "NaN"; else if (std::isinf(d)) o += d > 0 ? "Infinity" : "-Infinity";   // as semtpl::dumps_ascii writes them
    else o += semcodec::detail::float_repr(d);
    i = r;
}
}  // namespace bagtext

// a bag's text in the form it is stored in
inline std::string stored_bag(std::string_view t) {
    std::string o; o.reserve(t.size());
    size_t i = 0;
    while (i < t.size()) {
        char c = t[i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { ++i; continue; }
        if (c == '"') { bagtext::string(o, t, i); continue; }
        if ((c >= '0' && c <= '9') || c == '-') { bagtext::number(o, t, i); continue; }
        o += c; ++i;                                            // { } [ ] , : and the letters of true, false, null, NaN, Infinity
    }
    return o;
}
}  // namespace ribbit
