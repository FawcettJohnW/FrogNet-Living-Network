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
// fnwp_client.hpp -- S4a: the FNWP request a proxy puts on the wire, as proxy/transport_semantic.py
// _handle_semantic_request builds it (with core/codec.py, core/semcache_wire.py, proxy/origin.py):
//   encode_request_diff(opcode, url_vals, json_vals, reference) -> identical: REQ_REPEAT(req_hash);
//   no reference: REQ_FULL(req_hash, sem_req + origin trailer); else REQ_DIFF(req_hash, sem_req + origin trailer);
//   req_hash = sha256(json.dumps({"_dst": target, "_path": path, "_op": opcode, **new_reference}, sort_keys=True,
//   default=str))[:16]; origin trailer = origin bytes + dest bytes (UTF-8 'replace', each cut to 255) + [len o, len h, 1]
//   + FA CE.
#pragma once
#include "semcodec.hpp"
#include "semtpl.hpp"
#include "semwire.hpp"
#include <openssl/sha.h>
#include <algorithm>
#include <string>
namespace fnwp {
using pyv::Dict; using pyv::Obj; using semcodec::Fields;
struct Built { std::string req_type, frame, req_hash; Dict new_reference; };
namespace detail {
// [REQ_HASH_IN_PLACE_V1] The same text as json.dumps({"_dst", "_path", "_op", **reference}, sort_keys=True), byte for
// byte, written without building it: measured (MQTT, a batch of 37 writes, ~500 fields), a third of the client's CPU
// went to copying the reference twice (Dict h, then sorted_keys' sorted copy) and to Dict::set's linear search per key --
// quadratic in the fields. Now: one index sorted per dict, the values dumped where they lie.
inline void dumps_sorted(std::string& o, const Obj& v) {
    if (v.is<Dict>()) {
        const Dict& d = v.as<Dict>(); std::vector<size_t> ix(d.size()); for (size_t i = 0; i < ix.size(); ++i) ix[i] = i;
        std::sort(ix.begin(), ix.end(), [&](size_t x, size_t y) { return d.keys[x] < d.keys[y]; });
        o += '{';
        for (size_t k = 0; k < ix.size(); ++k) { if (k) o += ", "; semtpl::detail::dumps_ascii_str(o, d.keys[ix[k]]); o += ": "; dumps_sorted(o, d.vals[ix[k]]); }
        o += '}'; return;
    }
    if (v.is<pyv::List>()) { o += '['; bool f = true; for (const Obj& x : v.as<pyv::List>()) { if (!f) o += ", "; f = false; dumps_sorted(o, x); } o += ']'; return; }
    semtpl::detail::dumps_ascii(o, v, true);
}
inline std::string req_hash(const std::string& target, const std::string& path, uint64_t opcode, const Dict& ref) {
    // the top level: the three fixed keys unless the reference has them (its keys win), and every reference key, sorted
    const Obj dst{pyv::Str{target}}, pth{pyv::Str{path}}, op{pyv::Int::of(int64_t(opcode))};
    std::vector<std::pair<const std::string*, const Obj*>> top; top.reserve(ref.size() + 3);
    static const std::string K_DST = "_dst", K_PATH = "_path", K_OP = "_op";
    bool has_dst = false, has_path = false, has_op = false;
    for (size_t i = 0; i < ref.size(); ++i) { const std::string& k = ref.keys[i];
        if (k == K_DST) has_dst = true; else if (k == K_PATH) has_path = true; else if (k == K_OP) has_op = true;
        top.emplace_back(&k, &ref.vals[i]); }
    if (!has_dst) top.emplace_back(&K_DST, &dst);
    if (!has_path) top.emplace_back(&K_PATH, &pth);
    if (!has_op) top.emplace_back(&K_OP, &op);
    std::sort(top.begin(), top.end(), [](const auto& x, const auto& y) { return *x.first < *y.first; });
    thread_local std::string text; text.clear(); text += '{';
    for (size_t k = 0; k < top.size(); ++k) { if (k) text += ", "; semtpl::detail::dumps_ascii_str(text, *top[k].first); text += ": "; dumps_sorted(text, *top[k].second); }
    text += '}';
    unsigned char d[SHA256_DIGEST_LENGTH]; SHA256(reinterpret_cast<const unsigned char*>(text.data()), text.size(), d);
    return std::string(reinterpret_cast<const char*>(d), 16);
}
inline std::string origin_trailer(const std::string& origin_ip, const std::string& dest_host) {
    std::string ob = semcodec::detail::encode_replace(semtpl::u::strip(origin_ip)), hb = semcodec::detail::encode_replace(semtpl::u::strip(dest_host));
    if (ob.size() > 255) ob.resize(255);
    if (hb.size() > 255) hb.resize(255);
    return ob + hb + char(uint8_t(ob.size())) + char(uint8_t(hb.size())) + char(1) + "\xFA\xCE";
}
}  // namespace detail
// unanswered: [NO_WRITE_ACK_V1] a write nobody waits for is never coalesced with another (nothing answers it) and is not a
// REPEAT; its frame carries a zero request hash instead of the SHA-256 (measured: a tenth of a box's CPU). A later
// REQ_REPEAT naming its request is a REQ_MISS at the far end and is resent whole -- answered, as every REPEAT is.
inline Built build_request(const std::string& target_ip, const std::string& path, uint64_t opcode, const Fields& url_vals,
                           const Fields& json_vals, const Dict* reference, const std::string& origin_ip, const std::string& dest_host,
                           bool unanswered = false) {
    semcodec::DiffResult d = semcodec::encode_request_diff(opcode, url_vals, json_vals, reference, true);
    Built b; b.new_reference = d.reference;
    b.req_hash = (unanswered && !d.identical) ? std::string(16, '\0') : detail::req_hash(target_ip, path, opcode, d.reference);
    if (d.identical) { b.req_type = "REQ_REPEAT"; b.frame = semwire::wrap_req_repeat(b.req_hash); return b; }
    std::string sem = d.bytes + detail::origin_trailer(origin_ip, dest_host);
    if (!reference) { b.req_type = "REQ_FULL"; b.frame = semwire::wrap_req_full(b.req_hash, sem); }
    else { b.req_type = "REQ_DIFF"; b.frame = semwire::wrap_req_diff(b.req_hash, sem); }
    return b;
}
// S4b (pure part): RESP_DIFF on the client, as _handle_resp_diff: decode_reply(sem_blob, template, reference) -> fields;
// the fields become the new response reference; the body is the reply template's rebuild of their values.
struct Applied { std::string body; Dict new_reference; };
inline Applied apply_resp_diff(const std::string& sem_blob, const Dict& reply_fragment, const Dict* reference) {
    std::vector<std::string> fo = semtpl::str_list(semtpl::get(reply_fragment, "field_order"));
    Fields f = semcodec::decode_reply(sem_blob, fo, reference);
    Applied a; pyv::List vals; for (auto& kv : f) { a.new_reference.set(kv.first, kv.second); vals.push_back(kv.second); }
    a.body = semtpl::rebuild_reply(reply_fragment, vals);
    return a;
}
}  // namespace fnwp
