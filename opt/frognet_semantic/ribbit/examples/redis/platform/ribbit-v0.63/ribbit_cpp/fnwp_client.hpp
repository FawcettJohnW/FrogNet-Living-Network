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
inline Obj sorted_keys(const Obj& o) {                  // json.dumps(sort_keys=True) sorts every dict, recursively
    if (o.is<Dict>()) { const Dict& d = o.as<Dict>(); std::vector<size_t> ix(d.size()); for (size_t i = 0; i < ix.size(); ++i) ix[i] = i;
        std::sort(ix.begin(), ix.end(), [&](size_t x, size_t y) { return d.keys[x] < d.keys[y]; });
        Dict r; for (size_t i : ix) r.set(d.keys[i], sorted_keys(d.vals[i])); return r; }
    if (o.is<pyv::List>()) { pyv::List l; for (const Obj& x : o.as<pyv::List>()) l.push_back(sorted_keys(x)); return l; }
    return o;
}
inline std::string req_hash(const std::string& target, const std::string& path, uint64_t opcode, const Dict& ref) {
    Dict h; h.set("_dst", pyv::Str{target}); h.set("_path", pyv::Str{path}); h.set("_op", pyv::Int::of(int64_t(opcode)));
    for (size_t i = 0; i < ref.size(); ++i) h.set(ref.keys[i], ref.vals[i]);          // **reference: its keys win
    std::string text = semtpl::dumps_ascii(sorted_keys(Obj(h)), true);                  // default separators, ensure_ascii
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
inline Built build_request(const std::string& target_ip, const std::string& path, uint64_t opcode, const Fields& url_vals,
                           const Fields& json_vals, const Dict* reference, const std::string& origin_ip, const std::string& dest_host) {
    semcodec::DiffResult d = semcodec::encode_request_diff(opcode, url_vals, json_vals, reference, true);
    Built b; b.new_reference = d.reference; b.req_hash = detail::req_hash(target_ip, path, opcode, d.reference);
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
