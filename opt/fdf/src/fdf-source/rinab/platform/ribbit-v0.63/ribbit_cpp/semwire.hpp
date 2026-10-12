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
// semwire.hpp -- FNW1 v4.1 wire framing: C++ port of John's core/semcache_wire.py (semantic-engine port, slice S1).
//
// The Python module is the reference. tools/test_semwire_oracle.py imports it and holds this file to it byte for byte:
// every wrap_* frame, every try_parse result field for field (None versus empty included), every rejection's kind,
// and every ValueError's text.
//
// Frames: [MAGIC "FNW1"][op:1][payload...]; every multi-byte integer is big-endian.
//   REQ_FULL   [0x01][req_hash:16][len:4][semantic_packet]     REQ_REPEAT [0x02][req_hash:16]
//   REQ_RAW    [0x03][req_hash:16][len:4][http_request]        REQ_DIFF   [0x04][req_hash:16][len:4][semantic_diff]
//   RESP_DIFF  [0x11][same_id:16][len:4][sem_blob]             RESP_SAME  [0x13][same_id:16]
//   RESP_RAW   [0x14][same_id:16][len:4][status:2][hdrs_len:2][hdrs][body]
//   REQ_MISS   [0x21][req_hash:16]                             ERROR      [0x30][status:2][msg_len:2][msg]
//   SEQ_RESET  [0x40]                                          HELLO      [0x50][ip_len:1][ip]
//   RTT_PING   [0x60][ping_id:4][proxy_t_send_ns:8][pad_len:4][pad: pad_len zero bytes]
//   RTT_PONG   [0x61][ping_id:4][proxy_t_send_ns_echo:8][daemon_t_recv_ns:8][daemon_t_reply_ns:8]
//   RTT_LOOP   [0x62]
//
// Rejections carry Python's exception kinds:
//   ValueError  -- the checks John's module makes itself (hash / same_id length, HELLO ip length, RTT_PING ranges,
//                  every short-frame case in try_parse). Message text is the Python text.
//   StructError -- Python's struct.error: a value that does not fit its packed field (status or length over 16 bits,
//                  a length over 32 bits, RTT_PONG ping_id over 32 bits). Message is ours; the oracle checks the kind.
//   EncodeError -- Python's UnicodeEncodeError: a HELLO return_ip with a non-ASCII byte.
//
// Differences of representation, not behaviour:
//   - Python takes str for wrap_error's message and wrap_hello's return_ip; here they are bytes. wrap_error's bytes are
//     the message's UTF-8 (Python encodes with "replace", which only alters lone surrogates, which UTF-8 cannot hold).
//   - Integers are unsigned 64-bit, so Python's negative-value and >= 2^64 rejections have no input that reaches them.
//   - try_parse returns views into the frame, not copies: a WireMsg is valid while the frame it was parsed from lives.
#pragma once
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace semwire {

constexpr char MAGIC[4] = {'F', 'N', 'W', '1'};
constexpr size_t REQ_HASH_LEN = 16;  // core/codec.py REQ_HASH_LEN
constexpr size_t SAME_ID_LEN = 16;
enum : uint8_t {
    OP_LEGACY = 0x00, OP_REQ_FULL = 0x01, OP_REQ_REPEAT = 0x02, OP_REQ_RAW = 0x03, OP_REQ_DIFF = 0x04,
    OP_RESP_DIFF = 0x11, OP_RESP_SAME = 0x13, OP_RESP_RAW = 0x14, OP_REQ_MISS = 0x21, OP_ERROR = 0x30,
    OP_SEQ_RESET = 0x40, OP_HELLO = 0x50, OP_RTT_PING = 0x60, OP_RTT_PONG = 0x61, OP_RTT_LOOP = 0x62
};

struct ValueError : std::invalid_argument { using std::invalid_argument::invalid_argument; };
struct StructError : std::range_error { using std::range_error::range_error; };
struct EncodeError : std::invalid_argument { using std::invalid_argument::invalid_argument; };

// Parsed frame. Unset optionals are Python's None; views point into the parsed frame.
struct WireMsg {
    uint8_t op = 0;
    std::optional<std::string_view> req_hash, same_id, payload;
    std::optional<uint32_t> status;
    std::optional<std::string_view> headers, body;
    std::optional<uint64_t> ping_id, proxy_t_send_ns, daemon_t_recv_ns, daemon_t_reply_ns;
};

namespace detail {
inline void put(std::string& s, uint64_t v, int n) { for (int i = n - 1; i >= 0; --i) s.push_back(char(uint8_t(v >> (8 * i)))); }
inline uint64_t get(std::string_view f, size_t off, int n) {
    uint64_t v = 0;
    for (int i = 0; i < n; ++i) v = v << 8 | uint8_t(f[off + i]);
    return v;
}
inline std::string head(uint8_t op, size_t rest) {
    std::string s;
    s.reserve(5 + rest);
    s.append(MAGIC, 4);
    s.push_back(char(op));
    return s;
}
inline void need_hash(std::string_view h) {
    if (h.size() != REQ_HASH_LEN)
        throw ValueError("req_hash must be " + std::to_string(REQ_HASH_LEN) + " bytes, got " + std::to_string(h.size()));
}
inline void need_same_id(std::string_view s) {
    if (s.size() != SAME_ID_LEN)
        throw ValueError("same_id must be " + std::to_string(SAME_ID_LEN) + " bytes, got " + std::to_string(s.size()));
}
inline void fits(uint64_t v, uint64_t max, const char* what) {
    if (v > max) throw StructError(std::string("semwire: ") + what + " " + std::to_string(v) + " does not fit its field");
}
inline std::string hash_len_payload(uint8_t op, std::string_view h, std::string_view p) {
    need_hash(h);
    fits(p.size(), 0xFFFFFFFFu, "payload length");
    std::string s = head(op, REQ_HASH_LEN + 4 + p.size());
    s.append(h); put(s, p.size(), 4); s.append(p);
    return s;
}
inline std::string hash_only(uint8_t op, std::string_view h) {
    need_hash(h);
    std::string s = head(op, REQ_HASH_LEN);
    s.append(h);
    return s;
}
inline std::string short_payload(const char* op, uint64_t expected, size_t got) {
    return std::string("short ") + op + " payload: expected " + std::to_string(expected) + ", got " + std::to_string(got);
}
// REQ_FULL / REQ_RAW / REQ_DIFF / RESP_DIFF share one layout: [id:16][len:4][payload:len]
inline WireMsg id_len_payload(std::string_view f, uint8_t op, const char* name, bool is_hash) {
    constexpr size_t off0 = 5;
    if (f.size() < off0 + 16 + 4) throw ValueError(std::string("short ") + name + " header");
    std::string_view id = f.substr(off0, 16);
    uint64_t n = get(f, off0 + 16, 4);
    size_t off = off0 + 20;
    if (f.size() < off + n) throw ValueError(short_payload(name, n, f.size() - off));
    WireMsg m;
    m.op = op;
    (is_hash ? m.req_hash : m.same_id) = id;
    m.payload = f.substr(off, size_t(n));
    return m;
}
}  // namespace detail

inline bool is_fnw1(std::string_view frame) { return frame.size() >= 5 && frame.compare(0, 4, std::string_view(MAGIC, 4)) == 0; }

inline std::string wrap_req_full(std::string_view req_hash, std::string_view payload) {
    return detail::hash_len_payload(OP_REQ_FULL, req_hash, payload);
}
inline std::string wrap_req_repeat(std::string_view req_hash) { return detail::hash_only(OP_REQ_REPEAT, req_hash); }
inline std::string wrap_req_raw(std::string_view req_hash, std::string_view http_request) {
    return detail::hash_len_payload(OP_REQ_RAW, req_hash, http_request);
}
inline std::string wrap_req_diff(std::string_view req_hash, std::string_view diff_payload) {
    return detail::hash_len_payload(OP_REQ_DIFF, req_hash, diff_payload);
}
inline std::string wrap_resp_same(std::string_view same_id) {
    detail::need_same_id(same_id);
    std::string s = detail::head(OP_RESP_SAME, SAME_ID_LEN);
    s.append(same_id);
    return s;
}
inline std::string wrap_resp_diff(std::string_view same_id, std::string_view sem_blob) {
    detail::need_same_id(same_id);
    detail::fits(sem_blob.size(), 0xFFFFFFFFu, "payload length");
    std::string s = detail::head(OP_RESP_DIFF, SAME_ID_LEN + 4 + sem_blob.size());
    s.append(same_id); detail::put(s, sem_blob.size(), 4); s.append(sem_blob);
    return s;
}
inline std::string wrap_resp_raw(std::string_view same_id, uint64_t status, std::string_view headers, std::string_view body) {
    detail::need_same_id(same_id);
    detail::fits(status, 0xFFFF, "status");
    detail::fits(headers.size(), 0xFFFF, "headers length");
    uint64_t plen = 4 + uint64_t(headers.size()) + body.size();
    detail::fits(plen, 0xFFFFFFFFu, "payload length");
    std::string s = detail::head(OP_RESP_RAW, SAME_ID_LEN + 4 + plen);
    s.append(same_id); detail::put(s, plen, 4); detail::put(s, status, 2); detail::put(s, headers.size(), 2);
    s.append(headers); s.append(body);
    return s;
}
inline std::string wrap_req_miss(std::string_view req_hash) { return detail::hash_only(OP_REQ_MISS, req_hash); }
// message: the UTF-8 bytes of the error text.
inline std::string wrap_error(uint64_t status, std::string_view message) {
    detail::fits(status, 0xFFFF, "status");
    detail::fits(message.size(), 0xFFFF, "message length");
    std::string s = detail::head(OP_ERROR, 4 + message.size());
    detail::put(s, status, 2); detail::put(s, message.size(), 2); s.append(message);
    return s;
}
inline std::string wrap_seq_reset() { return detail::head(OP_SEQ_RESET, 0); }
inline std::string wrap_hello(std::string_view return_ip) {
    for (size_t i = 0; i < return_ip.size(); ++i)
        if (uint8_t(return_ip[i]) >= 0x80)
            throw EncodeError("semwire: return_ip byte " + std::to_string(i) + " is not ASCII");
    if (return_ip.size() > 255) throw ValueError("return_ip too long: " + std::to_string(return_ip.size()));
    std::string s = detail::head(OP_HELLO, 1 + return_ip.size());
    s.push_back(char(uint8_t(return_ip.size()))); s.append(return_ip);
    return s;
}
inline std::string wrap_rtt_ping(uint64_t ping_id, uint64_t proxy_t_send_ns, uint64_t pad_len) {
    if (ping_id > 0xFFFFFFFFu) throw ValueError("ping_id out of range: " + std::to_string(ping_id));
    if (pad_len > 0xFFFFFFFFu) throw ValueError("pad_len out of range: " + std::to_string(pad_len));
    std::string s = detail::head(OP_RTT_PING, 16 + pad_len);
    detail::put(s, ping_id, 4); detail::put(s, proxy_t_send_ns, 8); detail::put(s, pad_len, 4);
    s.append(size_t(pad_len), '\0');
    return s;
}
inline std::string wrap_rtt_pong(uint64_t ping_id, uint64_t proxy_t_send_ns_echo, uint64_t daemon_t_recv_ns,
                                 uint64_t daemon_t_reply_ns) {
    detail::fits(ping_id, 0xFFFFFFFFu, "ping_id");
    std::string s = detail::head(OP_RTT_PONG, 28);
    detail::put(s, ping_id, 4); detail::put(s, proxy_t_send_ns_echo, 8);
    detail::put(s, daemon_t_recv_ns, 8); detail::put(s, daemon_t_reply_ns, 8);
    return s;
}
inline std::string wrap_rtt_loop() { return detail::head(OP_RTT_LOOP, 0); }

// None (nullopt) if the frame is not FNW1; ValueError if it is FNW1 but malformed. Trailing bytes are ignored, and an
// unknown op yields a WireMsg carrying only the op, as in the Python.
inline std::optional<WireMsg> try_parse(std::string_view f) {
    using detail::get;
    if (!is_fnw1(f)) return std::nullopt;
    const uint8_t op = uint8_t(f[4]);
    const size_t off = 5, H = REQ_HASH_LEN;
    WireMsg m;
    m.op = op;
    switch (op) {
    case OP_REQ_REPEAT:
    case OP_REQ_MISS:
        if (f.size() < off + H) throw ValueError(op == OP_REQ_REPEAT ? "short REQ_REPEAT frame" : "short REQ_MISS frame");
        m.req_hash = f.substr(off, H);
        return m;
    case OP_REQ_FULL: return detail::id_len_payload(f, op, "REQ_FULL", true);
    case OP_REQ_RAW: return detail::id_len_payload(f, op, "REQ_RAW", true);
    case OP_REQ_DIFF: return detail::id_len_payload(f, op, "REQ_DIFF", true);
    case OP_RESP_DIFF: return detail::id_len_payload(f, op, "RESP_DIFF", false);
    case OP_RESP_SAME:
        if (f.size() < off + SAME_ID_LEN) throw ValueError("short RESP_SAME frame");
        m.same_id = f.substr(off, SAME_ID_LEN);
        return m;
    case OP_RESP_RAW: {
        WireMsg w = detail::id_len_payload(f, op, "RESP_RAW", false);
        std::string_view p = *w.payload;
        if (p.size() < 4) throw ValueError("short RESP_RAW payload content");
        size_t hl = size_t(get(p, 2, 2));
        if (p.size() < 4 + hl) throw ValueError("short RESP_RAW headers");
        m.same_id = w.same_id;
        m.status = uint32_t(get(p, 0, 2));
        m.headers = p.substr(4, hl);
        m.body = p.substr(4 + hl);
        return m;
    }
    case OP_ERROR: {
        if (f.size() < off + 4) throw ValueError("short ERROR header");
        size_t n = size_t(get(f, off + 2, 2));
        if (f.size() < off + 4 + n)
            throw ValueError("short ERROR message: expected " + std::to_string(n) + ", got " + std::to_string(f.size() - off - 4));
        m.status = uint32_t(get(f, off, 2));
        m.payload = f.substr(off + 4, n);
        return m;
    }
    case OP_SEQ_RESET: return m;
    case OP_HELLO: {
        if (f.size() < off + 1) throw ValueError("short HELLO frame");
        size_t n = uint8_t(f[off]);
        if (f.size() < off + 1 + n)
            throw ValueError("short HELLO ip: expected " + std::to_string(n) + ", got " + std::to_string(f.size() - off - 1));
        m.body = f.substr(off + 1, n);
        return m;
    }
    case OP_RTT_PING: {
        if (f.size() < off + 16) throw ValueError("short RTT_PING header");
        uint64_t pad = get(f, off + 12, 4);
        if (f.size() < off + 16 + pad)
            throw ValueError("short RTT_PING pad: expected " + std::to_string(pad) + ", got " + std::to_string(f.size() - off - 16));
        m.ping_id = get(f, off, 4);
        m.proxy_t_send_ns = get(f, off + 4, 8);
        m.payload = std::string_view();  // b"": the pad is not kept
        return m;
    }
    case OP_RTT_PONG:
        if (f.size() < off + 28) throw ValueError("short RTT_PONG frame");
        m.ping_id = get(f, off, 4);
        m.proxy_t_send_ns = get(f, off + 4, 8);
        m.daemon_t_recv_ns = get(f, off + 12, 8);
        m.daemon_t_reply_ns = get(f, off + 20, 8);
        return m;
    case OP_RTT_LOOP: return m;
    default: return m;  // unknown op, valid magic (OP_LEGACY included)
    }
}

inline std::string op_name(uint8_t op) {
    switch (op) {
    case OP_REQ_FULL: return "REQ_FULL";
    case OP_REQ_REPEAT: return "REQ_REPEAT";
    case OP_REQ_RAW: return "REQ_RAW";
    case OP_REQ_DIFF: return "REQ_DIFF";
    case OP_RESP_SAME: return "RESP_SAME";
    case OP_RESP_DIFF: return "RESP_DIFF";
    case OP_RESP_RAW: return "RESP_RAW";
    case OP_REQ_MISS: return "REQ_MISS";
    case OP_ERROR: return "ERROR";
    case OP_SEQ_RESET: return "SEQ_RESET";
    case OP_HELLO: return "HELLO";
    case OP_RTT_PING: return "RTT_PING";
    case OP_RTT_PONG: return "RTT_PONG";
    case OP_RTT_LOOP: return "RTT_LOOP";
    case OP_LEGACY: return "LEGACY";
    }
    static const char* d = "0123456789abcdef";
    std::string s = "UNKNOWN(0x";
    if (op >= 16) s.push_back(d[op >> 4]);
    s.push_back(d[op & 15]);
    return s + ")";
}

}  // namespace semwire
