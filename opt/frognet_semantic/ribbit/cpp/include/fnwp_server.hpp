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
// fnwp_server.hpp -- S5a: how the daemon decides and encodes a reply, as daemon/engine/session.py does it:
//   raw_hash = response_body_hash(dyn_vals, status)  sha256("status:%d\n" + "%s=%r\n" per field sorted by str(key));
//   the REPEAT cache (memory LRU by req_hash) holds (raw_hash, same_id): equal raw_hash and a 16-byte same_id -> RESP_SAME
//   (the response reference is still set to dyn_vals); else same_id = HMAC-SHA256(secret, req_hash + raw_hash)[:16] and
//   RESP_DIFF(same_id, _diff_encode_response(...)), cached when status < 400.
//   _diff_encode_response: encode_reply_diff against the response reference, which becomes the new reference; identical or
//   empty -> the FULL encode_reply instead. (It also answers encode_reply when encode_reply_diff raises -- mirrored as the
//   running code does it; the same values make encode_reply raise too.)
// Python repr() is ported exactly (str quoting and escapes by Unicode printability, float repr, list/dict/bytes repr).
#pragma once
#include "pyuni_s3_tables.hpp"
#include "semcodec.hpp"
#include "semtpl.hpp"
#include "semwire.hpp"
#include <openssl/hmac.h>
#include <openssl/sha.h>
#include <optional>
#include <string>
namespace fnwp {
using pyv::Dict; using pyv::List; using pyv::Obj; using semcodec::Fields;
#ifdef FNWP_S5A_RED
#define S5A_RED(f) throw std::logic_error("fnwp S5a red: " f)
#else
#define S5A_RED(f) (void)0
#endif
namespace detail {
inline bool printable(uint32_t c) {
    size_t lo = 0, hi = sizeof pyuni_s3::PRINTABLE / sizeof pyuni_s3::PRINTABLE[0];
    while (lo < hi) { size_t m = (lo + hi) / 2; if (pyuni_s3::PRINTABLE[m].hi < c) lo = m + 1; else hi = m; }
    return lo < sizeof pyuni_s3::PRINTABLE / sizeof pyuni_s3::PRINTABLE[0] && pyuni_s3::PRINTABLE[lo].lo <= c;
}
inline void repr_str(std::string& o, const std::string& s) {
    bool sq = s.find('\'') != std::string::npos, dq = s.find('"') != std::string::npos;
    char q = (sq && !dq) ? '"' : '\''; char b[16]; o += q;
    for (uint32_t c : semtpl::u::cps(s)) {
        if (c == uint32_t(q) || c == '\\') { o += '\\'; o += char(c); }
        else if (c == '\t') o += "\\t"; else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r";
        else if (c < 0x20 || c == 0x7f) { std::snprintf(b, sizeof b, "\\x%02x", c); o += b; }
        else if (c < 0x80 || printable(c)) semcodec::detail::utf8_put(o, c);
        else if (c < 0x100) { std::snprintf(b, sizeof b, "\\x%02x", c); o += b; }
        else if (c < 0x10000) { std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else { std::snprintf(b, sizeof b, "\\U%08x", c); o += b; }
    }
    o += q;
}
inline void repr_bytes(std::string& o, const std::string& s) {
    bool sq = s.find('\'') != std::string::npos, dq = s.find('"') != std::string::npos;
    char q = (sq && !dq) ? '"' : '\''; char b[8]; o += 'b'; o += q;
    for (unsigned char c : s) {
        if (c == uint8_t(q) || c == '\\') { o += '\\'; o += char(c); }
        else if (c == '\t') o += "\\t"; else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r";
        else if (c < 0x20 || c >= 0x7f) { std::snprintf(b, sizeof b, "\\x%02x", c); o += b; }
        else o += char(c);
    }
    o += q;
}
inline void repr(std::string& o, const Obj& v) {
    if (v.is<pyv::None>()) o += "None";
    else if (v.is<bool>()) o += v.as<bool>() ? "True" : "False";
    else if (v.is<pyv::Int>()) o += v.as<pyv::Int>().str();
    else if (v.is<double>()) { double d = v.as<double>(); if (std::isnan(d)) o += "nan"; else if (std::isinf(d)) o += d > 0 ? "inf" : "-inf";
                               else o += semcodec::detail::float_repr(d); }
    else if (v.is<pyv::Str>()) repr_str(o, v.as<pyv::Str>().s);
    else if (v.is<pyv::Bytes>()) repr_bytes(o, v.as<pyv::Bytes>().b);
    else if (v.is<List>()) { o += '['; bool f = true; for (const Obj& x : v.as<List>()) { if (!f) o += ", "; f = false; repr(o, x); } o += ']'; }
    else { const Dict& d = v.as<Dict>(); o += '{';
        for (size_t i = 0; i < d.size(); ++i) { if (i) o += ", "; repr_str(o, d.keys[i]); o += ": "; repr(o, d.vals[i]); } o += '}'; }
}
inline std::string surrogateescape(const std::string& s) {      // .encode("utf-8", "surrogateescape")
    std::string o;
    for (uint32_t c : semtpl::u::cps(s)) {
        if (c >= 0xDC80 && c <= 0xDCFF) o += char(uint8_t(c - 0xDC00));
        else if (c >= 0xD800 && c <= 0xDFFF) throw pyv::EncodeError("surrogates not allowed");
        else semcodec::detail::utf8_put(o, c);
    }
    return o;
}
}  // namespace detail
inline std::string pyrepr(const Obj& v) { S5A_RED("repr"); std::string o; detail::repr(o, v); return o; }
inline std::string response_body_hash(const Fields& dyn_vals, int64_t status) {
    S5A_RED("response_body_hash");
    std::vector<size_t> ix(dyn_vals.size()); for (size_t i = 0; i < ix.size(); ++i) ix[i] = i;
    std::stable_sort(ix.begin(), ix.end(), [&](size_t a, size_t b) { return dyn_vals[a].first < dyn_vals[b].first; });
    std::string text = "status:" + std::to_string(status) + "\n";
    for (size_t i : ix) text += detail::surrogateescape(dyn_vals[i].first + "=") + detail::surrogateescape(pyrepr(dyn_vals[i].second) + "\n");
    unsigned char d[SHA256_DIGEST_LENGTH]; SHA256(reinterpret_cast<const unsigned char*>(text.data()), text.size(), d);
    return std::string(reinterpret_cast<const char*>(d), 32);
}
inline std::string same_id(const std::string& secret, const std::string& req_hash, const std::string& raw_hash) {
    S5A_RED("same_id");
    if (req_hash.size() != 16 && req_hash.size() != 32) throw pyv::ValueError("req_hash must be 16 or 32 bytes");
    if (raw_hash.size() != 32) throw pyv::ValueError("raw_hash must be 32 bytes");
    std::string m = req_hash + raw_hash; unsigned char out[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), secret.data(), int(secret.size()), reinterpret_cast<const unsigned char*>(m.data()), m.size(), out, &n);
    return std::string(reinterpret_cast<const char*>(out), 16);
}
// _diff_encode_response: the payload, and the response reference it leaves behind
inline std::pair<std::string, Dict> diff_encode_response(uint64_t opcode, const Fields& dyn_vals, const Dict* reference) {
    S5A_RED("diff_encode_response");
    try {
        semcodec::DiffResult d = semcodec::encode_reply_diff(opcode, dyn_vals, reference, true);
        if (d.identical || d.bytes.empty()) return {semcodec::encode_reply(opcode, dyn_vals, true), d.reference};
        return {d.bytes, d.reference};
    } catch (const std::exception&) {
        Dict r; for (auto& kv : dyn_vals) r.set(kv.first, kv.second);
        return {semcodec::encode_reply(opcode, dyn_vals, true), r};
    }
}
struct Cached { std::string raw_hash, same_id; };
struct Reply { std::string frame; std::optional<Dict> new_reference; std::optional<Cached> cache; };
// consider_same: REQ_REPEAT / REQ_DIFF compare against the REPEAT cache; REQ_FULL does not (the proxy starts fresh).
inline Reply daemon_reply(const std::string& secret, const std::string& req_hash, const Fields& dyn_vals, int64_t status,
                          const Dict* reference, const std::optional<Cached>& cached, uint64_t resp_opcode, bool consider_same) {
    S5A_RED("daemon_reply");
    Reply r; std::string raw = response_body_hash(dyn_vals, status);
    if (consider_same && cached && !cached->raw_hash.empty() && cached->raw_hash == raw && cached->same_id.size() == 16) {
        Dict ref; for (auto& kv : dyn_vals) ref.set(kv.first, kv.second);
        r.new_reference = ref; r.frame = semwire::wrap_resp_same(cached->same_id); return r;
    }
    std::string sid = same_id(secret, req_hash, raw);
    auto enc = diff_encode_response(resp_opcode, dyn_vals, reference);
    r.new_reference = enc.second; r.frame = semwire::wrap_resp_diff(sid, enc.first);
    if (status < 400) r.cache = Cached{raw, sid};
    return r;
}
}  // namespace fnwp
