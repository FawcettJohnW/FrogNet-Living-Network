// semtpl.hpp -- FrogNet semantic templates (S3 of the semantic-engine port), header only.
//
// Reference: John's RUNNING Python (frognet-source-20260924 + codec-exact-20260925), imported and run as the oracle by
// tools/test_semtpl_oracle.py: core/json_handler.py, core/text_handler.py, core/template.py, core/template_utils.py,
// core/format_registry.py, core/blob_store.py, core/store.py (ids), proxy/templates.py, and urllib.parse (3.12.3) as
// they call it. Python str is carried as UTF-8 (WTF-8 for lone surrogates), as in pyval.hpp; character behaviour comes
// from pyuni_tables.hpp and pyuni_s3_tables.hpp, generated from the oracle's own Python.
//
// Where the running Python swallows a failure, this code does not: it raises Declined naming the case, and the oracle
// detects each case from the Python's own execution and reports what the Python did instead:
//   array_literal   rebuild of an array field: text that is not a JSON list goes to ast.literal_eval, else [] (ruled)
//   array_type      rebuild of an array field: a value that is neither list, str nor None becomes []
//   blob_missing    rebuild: BlobStore.load failure becomes []
//   json_unparsable learn: a body that does not parse becomes an empty JSON template
//   pair_heuristic  rebuild_reply: a first value that is a 2-element list is read as a (key, value) pair
//   text_repr       str() of a list / dict / bytes would need Python repr(); the codec never yields one there
//   unknown_mode    FORMAT_HANDLERS.get(mode, RAW): an unknown mode is handled as raw
//   train_exception learn_templates_from_real catches every exception and prints it (ruled 2026-09-24: abort)
// One swallow is a SIGNAL, not a failure, and is carried explicitly: when extract cannot hold a body (it does not parse,
// or its shape is not the template's) the Python returns [] and the daemon answers RESP_RAW. extract() here returns
// std::nullopt for exactly that -- "send it raw" -- so no caller can mistake it for an empty field list.
// Not ported (raise NotPorted): the xml, html and sotf_media handlers; detection that reaches an XML parse; a URL whose
// netloc is non-ASCII (NFKC check) or bracketed (ipaddress check). The template paths (/api.php?..., /ram.php?...) never
// have either.
#pragma once

#include "pyval.hpp"
#include "pyuni_s3_tables.hpp"
#include "semcodec.hpp"

#include <openssl/sha.h>
#include <sys/stat.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace semtpl {

using pyv::Bytes;
using pyv::Dict;
using pyv::Int;
using pyv::List;
using pyv::None;
using pyv::Obj;
using pyv::Str;
using semcodec::Fields;
using Pairs = std::vector<std::pair<std::string, std::string>>;

struct Declined : std::runtime_error { using std::runtime_error::runtime_error; };   // what() = declined case name
struct NotPorted : std::runtime_error { using std::runtime_error::runtime_error; };  // what() = handler / reason
struct NotFound : std::runtime_error { using std::runtime_error::runtime_error; };   // FileNotFoundError (blob)
inline const Obj* get(const Dict& d, const std::string& k);                         // dict.get, defined below

namespace u {   // ---------------------------------------------------------------- Python str behaviour on WTF-8
inline std::vector<uint32_t> cps(std::string_view s) {
    std::vector<uint32_t> o; o.reserve(s.size());
    if (!semcodec::detail::wtf8_walk(s, [&](uint32_t c, size_t, size_t) { o.push_back(c); }))
        throw std::logic_error("semtpl: string is not WTF-8");
    return o;
}
inline std::string enc(const std::vector<uint32_t>& v, size_t a = 0, size_t b = size_t(-1)) {
    std::string o; b = std::min(b, v.size()); for (size_t i = a; i < b; ++i) semcodec::detail::utf8_put(o, v[i]); return o;
}
inline bool is_space(uint32_t c) { for (uint32_t s : pyuni::SPACE) if (s == c) return true; return false; }
inline int digit(uint32_t c) {   // Unicode decimal digit value (re \d, int()), else -1
    for (const auto& r : pyuni::DIGITS) if (c >= r.first && c < r.first + r.count) return int(c - r.first);
    return -1;
}
inline bool is_alnum(uint32_t c) {
    size_t lo = 0, hi = sizeof pyuni_s3::ALNUM / sizeof pyuni_s3::ALNUM[0];
    while (lo < hi) { size_t m = (lo + hi) / 2; if (pyuni_s3::ALNUM[m].hi < c) lo = m + 1; else hi = m; }
    return lo < sizeof pyuni_s3::ALNUM / sizeof pyuni_s3::ALNUM[0] && pyuni_s3::ALNUM[lo].lo <= c;
}
inline bool is_word(uint32_t c) { return c == '_' || is_alnum(c); }                  // re \w on str
inline bool icase_az09(uint32_t c) {                                                   // [a-z0-9] under re.IGNORECASE
    for (uint32_t x : pyuni_s3::ICASE_AZ09) if (x == c) return true;
    return false; }
template <size_t N> inline const pyuni_s3::Lower* find_map(const pyuni_s3::Lower (&t)[N], uint32_t c) {
    size_t lo = 0, hi = N; while (lo < hi) { size_t m = (lo + hi) / 2; if (t[m].cp < c) lo = m + 1; else hi = m; }
    return lo < N && t[lo].cp == c ? &t[lo] : nullptr;
}
inline std::vector<uint32_t> lower(const std::vector<uint32_t>& v) {
    std::vector<uint32_t> o; o.reserve(v.size());
    for (uint32_t c : v) { const auto* m = find_map(pyuni_s3::LOWER, c); if (!m) o.push_back(c); else for (int k = 0; k < m->n; ++k) o.push_back(m->to[k]); }
    return o;
}
inline std::string lower(std::string_view s) { return enc(lower(cps(s))); }
inline std::string upper(std::string_view s) {
    std::vector<uint32_t> o; for (uint32_t c : cps(s)) { const auto* m = find_map(pyuni_s3::UPPER, c);
        if (!m) o.push_back(c); else for (int k = 0; k < m->n; ++k) o.push_back(m->to[k]); }
    return enc(o);
}
inline std::vector<uint32_t> strip(const std::vector<uint32_t>& v, bool left = true, bool right = true) {
    size_t a = 0, b = v.size();
    if (left) while (a < b && is_space(v[a])) ++a;
    if (right) while (b > a && is_space(v[b - 1])) --b;
    return std::vector<uint32_t>(v.begin() + long(a), v.begin() + long(b));
}
// str.strip() on the bytes: only the code points at each edge are decoded (whitespace is 1-3 bytes of UTF-8)
inline uint32_t cp_at(std::string_view s, size_t i, size_t& len) {
    uint32_t c = 0; len = 0;
    semcodec::detail::wtf8_walk(s.substr(i, std::min<size_t>(4, s.size() - i)), [&](uint32_t x, size_t st, size_t n) { if (st == 0) { c = x; len = n; } });
    if (!len) throw std::logic_error("semtpl: string is not WTF-8");
    return c;
}
inline std::string strip(std::string_view s) {
    size_t a = 0, b = s.size(), n;
    while (a < b) { uint32_t c = cp_at(s, a, n); if (!is_space(c)) break; a += n; }
    while (b > a) { size_t k = b - 1; while (k > a && (uint8_t(s[k]) & 0xC0) == 0x80) --k;
                    uint32_t c = cp_at(s, k, n); if (!is_space(c)) break; b = k; }
    return std::string(s.substr(a, b - a));
}
inline bool starts(std::string_view s, std::string_view p) { return s.size() >= p.size() && s.compare(0, p.size(), p) == 0; }
inline bool ends(std::string_view s, std::string_view p) { return s.size() >= p.size() && s.compare(s.size() - p.size(), p.size(), p) == 0; }
inline bool has(std::string_view s, std::string_view p) { return s.find(p) != std::string_view::npos; }
inline std::string replace_all(std::string s, std::string_view a, std::string_view b) {   // str.replace
    std::string o; size_t i = 0, j;
    while ((j = s.find(a, i)) != std::string::npos) { o.append(s, i, j - i); o += b; i = j + a.size(); }
    return o + s.substr(i);
}
inline std::vector<std::string> split(std::string_view s, char d) {   // str.split(d)
    std::vector<std::string> o; size_t i = 0, j;
    while ((j = s.find(d, i)) != std::string_view::npos) { o.emplace_back(s.substr(i, j - i)); i = j + 1; }
    o.emplace_back(s.substr(i)); return o;
}
inline std::string decode_replace(std::string_view bytes) { return semcodec::detail::utf8_replace(bytes); }   // .decode('utf-8','replace')
}  // namespace u

// ---------------------------------------------------------------------------------------------- urllib.parse (3.12.3)
struct UrlParts { std::string scheme, netloc, path, params, query, fragment; };
inline UrlParts urlparse(std::string_view in) {
    std::string url(in);
    size_t a = 0; while (a < url.size() && uint8_t(url[a]) <= 0x20) ++a;                      // lstrip(C0 control or space)
    url = url.substr(a);
    std::string t; for (char c : url) if (c != '\t' && c != '\r' && c != '\n') t += c; url = t; // _UNSAFE_URL_BYTES_TO_REMOVE
    UrlParts r;
    size_t i = url.find(':');
    if (i != std::string::npos && i > 0 && std::isalpha(uint8_t(url[0])) && uint8_t(url[0]) < 0x80) {
        static const std::string sc = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.";
        bool all = true; for (size_t k = 0; k < i; ++k) if (sc.find(url[k]) == std::string::npos) { all = false; break; }
        if (all) { r.scheme = url.substr(0, i); for (char& c : r.scheme) c = char(std::tolower(uint8_t(c))); url = url.substr(i + 1); }
    }
    if (url.compare(0, 2, "//") == 0) {
        size_t delim = url.size();
        for (char c : std::string("/?#")) { size_t w = url.find(c, 2); if (w != std::string::npos) delim = std::min(delim, w); }
        r.netloc = url.substr(2, delim - 2); url = url.substr(delim);
        bool ob = u::has(r.netloc, "["), cb = u::has(r.netloc, "]");
        if (ob != cb) throw pyv::CpyValueError("Invalid IPv6 URL");
        if (ob && cb) throw NotPorted("urlsplit: bracketed netloc (ipaddress check)");
    }
    size_t h = url.find('#'); if (h != std::string::npos) { r.fragment = url.substr(h + 1); url = url.substr(0, h); }
    size_t q = url.find('?'); if (q != std::string::npos) { r.query = url.substr(q + 1); url = url.substr(0, q); }
    for (char c : r.netloc) if (uint8_t(c) >= 0x80) throw NotPorted("urlsplit: non-ASCII netloc (NFKC check)");
    static const char* uses_params[] = {"", "ftp", "hdl", "prospero", "http", "imap", "https", "shttp", "rtsp", "rtspu",
                                        "sip", "sips", "mms", "sftp", "tel"};
    bool up = false; for (const char* p : uses_params) if (r.scheme == p) up = true;
    if (up && u::has(url, ";")) {
        size_t k;
        if (u::has(url, "/")) { k = url.find(';', url.rfind('/')); if (k == std::string::npos) { r.path = url; return r; } }
        else k = url.find(';');
        r.params = url.substr(k + 1); url = url.substr(0, k);
    }
    r.path = url; return r;
}
namespace detail {
inline int hexv(char c) { if (c >= '0' && c <= '9') return c - '0'; if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                          if (c >= 'A' && c <= 'F') return c - 'A' + 10;
                          return -1; }
inline std::string unquote(const std::string& s) {                 // unquote(str, 'utf-8', 'replace')
    if (!u::has(s, "%")) return s;
    std::string o; size_t i = 0;
    while (i < s.size()) {
        if (uint8_t(s[i]) >= 0x80) { size_t j = i; while (j < s.size() && uint8_t(s[j]) >= 0x80) ++j; o.append(s, i, j - i); i = j; continue; }
        size_t j = i; while (j < s.size() && uint8_t(s[j]) < 0x80) ++j;
        std::string run = s.substr(i, j - i), bytes; auto bits = u::split(run, '%');
        bytes = bits[0];
        for (size_t k = 1; k < bits.size(); ++k) {
            const std::string& it = bits[k];
            if (it.size() >= 2 && hexv(it[0]) >= 0 && hexv(it[1]) >= 0) { bytes += char(hexv(it[0]) * 16 + hexv(it[1])); bytes += it.substr(2); }
            else { bytes += '%'; bytes += it; }
        }
        o += u::decode_replace(bytes); i = j;
    }
    return o;
}
inline std::string unquote_plus(std::string s) { for (char& c : s) if (c == '+') c = ' '; return unquote(s); }
inline std::string quote(const std::string& s, const std::string& safe) {   // quote(str, safe), utf-8 strict
    if (s.empty()) return s;
    semcodec::detail::encode_strict(s);                                        // a lone surrogate raises
    static const std::string always = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~";
    std::string o; char buf[4];
    for (unsigned char c : s) {
        if (c < 0x80 && (always.find(char(c)) != std::string::npos || safe.find(char(c)) != std::string::npos)) o += char(c);
        else { std::snprintf(buf, sizeof buf, "%%%02X", c); o += buf; }
    }
    return o;
}
inline std::string quote_plus(const std::string& s) {
    if (!u::has(s, " ")) return quote(s, "");
    return u::replace_all(quote(s, " "), " ", "+");
}
}  // namespace detail
inline Pairs parse_qsl(std::string_view qs) {                            // keep_blank_values=True, separator '&'
    Pairs r; if (qs.empty()) return r;
    for (const std::string& nv : u::split(qs, '&')) {
        if (nv.empty()) continue;
        size_t e = nv.find('=');
        std::string name = e == std::string::npos ? nv : nv.substr(0, e), value = e == std::string::npos ? "" : nv.substr(e + 1);
        r.emplace_back(detail::unquote_plus(name), detail::unquote_plus(value));
    }
    return r;
}
inline std::vector<std::pair<std::string, std::vector<std::string>>> parse_qs(std::string_view qs) {
    std::vector<std::pair<std::string, std::vector<std::string>>> r;
    for (auto& kv : parse_qsl(qs)) {
        bool f = false; for (auto& x : r) if (x.first == kv.first) { x.second.push_back(kv.second); f = true; break; }
        if (!f) r.push_back({kv.first, {kv.second}});
    }
    return r;
}
inline std::string urlencode(const Pairs& q) {
    std::string o; for (size_t i = 0; i < q.size(); ++i) o += (i ? "&" : "") + detail::quote_plus(q[i].first) + "=" + detail::quote_plus(q[i].second);
    return o;
}

// ---------------------------------------------------------------------------------------------- proxy/templates.py
inline const std::vector<std::string>& static_query_keys() {
    static const std::vector<std::string> k{"entity", "action", "SensorType", "NetworkName", "FrogID", "Mode",
                                            "SensorName__like", "SensorName", "SensorID", "CallSign"};
    return k;
}
// ---------------------------------------------------------------------------------------------- core/tuple_key.py
// [INSTANCE_REFERENCES_V1] location_query_keys: the query keys of a tuple call that are its location, and so not part
// of its template's identity. Same definition as the Python: api.php, entity sensors/sensor_data; a read's location is
// every query key that is not a control; a write's is SensorName when the query carries it. Anything else: none.
inline std::vector<std::string> tuple_location_query_keys(std::string_view path) {
    std::vector<std::string> out;
    if (!u::has(path, "api.php")) return out;
    const auto qs = parse_qs(urlparse(path).query);
    auto first = [&](const std::string& k) -> std::optional<std::string> {
        for (auto& x : qs) if (x.first == k) return x.second.empty() ? std::string() : x.second.front();
        return std::nullopt; };
    auto in = [](const std::optional<std::string>& v, std::initializer_list<const char*> set) {
        if (!v) return false;
        for (auto s : set) if (*v == s) return true;
        return false; };
    if (!in(first("entity"), {"sensors", "sensor_data"})) return out;
    if (in(first("action"), {"values", "list", "get"})) {
        for (auto& x : qs) {
            bool control = false; for (auto c : {"entity", "action", "order", "limit", "parse", "fresh_s"}) if (x.first == c) control = true;
            if (!control) out.push_back(x.first);
        }
    } else if (in(first("action"), {"upsert", "upsert_by_name", "upsert_batch", "update", "create", "delete"})) {
        if (first("SensorName")) out.push_back("SensorName");
    }
    return out;
}
inline std::pair<std::string, std::vector<std::string>> normalize_path_for_semantics(std::string_view in) {
    if (in.empty()) return {"/", {}};
    std::string path(in); while (u::starts(path, "//")) path = path.substr(1);
    UrlParts p = urlparse(path);
    std::string raw = p.path.empty() ? "/" : p.path;
    while (u::has(raw, "//")) raw = u::replace_all(raw, "//", "/");
    if (!u::starts(raw, "/")) raw = "/" + raw;
    const auto location = tuple_location_query_keys(path);     // [INSTANCE_REFERENCES_V1] travels as dynamic values
    Pairs stable; std::vector<std::string> dyn;
    for (auto& kv : parse_qsl(p.query)) {
        const auto& sk = static_query_keys();
        if (std::find(sk.begin(), sk.end(), kv.first) != sk.end() &&
            std::find(location.begin(), location.end(), kv.first) == location.end()) {
            bool f = false; for (auto& s : stable) if (s.first == kv.first) { s.second = kv.second; f = true; }
            if (!f) stable.push_back(kv);
        } else if (std::find(dyn.begin(), dyn.end(), kv.first) == dyn.end()) dyn.push_back(kv.first);
    }
    return {stable.empty() ? raw : raw + "?" + urlencode(stable), dyn};
}
inline Pairs extract_dynamic_query_vals(std::string_view raw_path, const std::vector<std::string>& keys) {
    Pairs r; if (keys.empty() || raw_path.empty()) return r;
    UrlParts p = urlparse(raw_path);
    if (p.query.empty()) { for (auto& k : keys) r.emplace_back(k, ""); return r; }
    auto qs = parse_qs(p.query);
    for (auto& k : keys) { std::string v; for (auto& x : qs) if (x.first == k) { v = x.second.front(); break; } r.emplace_back(k, v); }
    return r;
}
inline std::string with_dynamic_shape(std::string_view base, std::vector<std::string> keys) {
    if (keys.empty()) return std::string(base);
    std::sort(keys.begin(), keys.end());                                     // code point order == byte order (UTF-8)
    std::string shape; for (size_t i = 0; i < keys.size(); ++i) shape += (i ? "," : "") + keys[i];
    return std::string(base) + (u::has(base, "?") ? "&" : "?") + "__dyn=" + shape;
}

namespace detail { inline uint32_t crc32(std::string_view d); }
namespace detail {
inline std::string extract_metric_name(const Obj* sensor_name) {
    if (!sensor_name || !sensor_name->is<Str>()) return "";
    std::vector<std::string> parts; for (auto& p : u::split(u::strip(sensor_name->as<Str>().s), '.')) if (!p.empty()) parts.push_back(p);
    return parts.size() >= 4 ? parts.back() : "";
}
}  // namespace detail
// defined below (json_handler)
inline Obj sanitize_nonfinite(const Obj& o);
inline Obj infer_schema(const Obj& v, std::string_view path);
inline std::pair<std::vector<std::string>, Dict> field_order_and_type_map(const Obj& schema);
// [BODY_FIELD_SET_IS_PART_OF_THE_IDENTITY_V1] proxy/templates.py _with_body_shape: the crc32 of a JSON object body's
// field paths, folded into the key as &__body=%08x. Two field sets are two wire layouts, so two templates. Not a JSON
// object (empty, unparsable, a list, a scalar): the key is unchanged.
inline std::string with_body_shape(const std::string& key, std::string_view body) {
    size_t a = 0, b = body.size();                      // bytes.strip(): ASCII whitespace, on the bytes, before decoding
    auto ws = [](char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\x0b' || c == '\x0c'; };
    while (a < b && ws(body[a])) ++a;
    while (b > a && ws(body[b - 1])) --b;
    if (a == b) return key;
    std::optional<Obj> j;
    try { j = semcodec::json_loads(u::decode_replace(body.substr(a, b - a))); } catch (const pyv::JsonError&) { return key; }
    Obj obj = sanitize_nonfinite(*j);
    if (!obj.is<Dict>()) return key;
    auto fo = field_order_and_type_map(infer_schema(obj, "")).first;
    std::string joined; for (size_t i = 0; i < fo.size(); ++i) joined += (i ? "," : "") + fo[i];
    char hex[16]; std::snprintf(hex, sizeof hex, "%08x", detail::crc32(joined));
    return key + (u::has(key, "?") ? "&" : "?") + "__body=" + hex;
}
inline std::string canonical_semantic_key(std::string_view method, std::string_view raw_path, std::string_view body) {
    auto [base, dyn] = normalize_path_for_semantics(raw_path);
    if (u::upper(method) != "POST" || !u::starts(base, "/api.php") || !u::has(base, "entity=sensor_data") || !u::has(base, "action=upsert_by_name"))
        return with_body_shape(with_dynamic_shape(base, dyn), body);
    const std::string coarse = "/api.php?entity=sensor_data&action=upsert_by_name";
    std::optional<Obj> j;
    if (!body.empty()) {                                // _parse_json_body: a ValueError widens the key, printed; nothing else is caught
        try { j = semcodec::json_loads(u::decode_replace(body)); }
        catch (const pyv::JsonError& e) {
            std::cerr << "[TEMPLATES] body present (" << body.size() << "B) but not JSON: " << e.what()
                      << " - semantic key widened to the un-keyed form\n" << std::flush; }
    }
    if (!j || !j->is<Dict>()) return coarse;
    const Obj* st = get(j->as<Dict>(), "SensorType"); std::string metric = detail::extract_metric_name(get(j->as<Dict>(), "SensorName"));
    if (!st || !st->is<Str>() || u::strip(st->as<Str>().s).empty()) return coarse;
    std::string sensor_type = u::strip(st->as<Str>().s);
    if (metric.empty()) return coarse + "&SensorType=" + sensor_type;
    return coarse + "&SensorType=" + sensor_type + "&MetricName=" + metric;
}

// ---------------------------------------------------------------------------------------------- core/store.py ids
namespace detail {
inline uint32_t crc32(std::string_view d) {
    static uint32_t t[256]; static bool init = false;
    if (!init) { for (uint32_t i = 0; i < 256; ++i) { uint32_t c = i; for (int k = 0; k < 8; ++k) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1; t[i] = c; } init = true; }
    uint32_t c = 0xFFFFFFFFu; for (unsigned char x : d) c = t[(c ^ x) & 0xFF] ^ (c >> 8); return c ^ 0xFFFFFFFFu;
}
}  // namespace detail
inline std::string template_id(std::string_view method, std::string_view semantic_path) {
    std::string key = semcodec::detail::encode_replace(u::upper(method) + " " + std::string(semantic_path));
    char b[16]; std::snprintf(b, sizeof b, "tpl_%08x", detail::crc32(key)); return b;
}
inline uint32_t opcode_for(std::string_view semantic_key) {
    semcodec::detail::encode_strict(semantic_key);
    uint32_t c = detail::crc32(semantic_key);
    return (c == 0 || c == 0xFFFFFFFFu) ? 0xFFFFFFFEu : c;
}

// ---------------------------------------------------------------------------------------------- Python value semantics
namespace detail {
inline std::optional<std::string> integral_text(double d) {          // exact decimal of an integral double, else none
    if (!std::isfinite(d) || d != std::floor(d)) return std::nullopt;
    char b[400]; std::snprintf(b, sizeof b, "%.0f", d); std::string s = b; if (s == "-0") s = "0"; return s;
}
inline std::string num_key(const Obj& x) {                            // bool/int as exact decimal text
    if (x.is<bool>()) return x.as<bool>() ? "1" : "0";
    return x.as<Int>().str();
}
}  // namespace detail
inline bool python_eq(const Obj& a, const Obj& b) {                   // Python ==, on the values json produces
    auto num = [](const Obj& x) { return x.is<bool>() || x.is<Int>() || x.is<double>(); };
    if (num(a) && num(b)) {
        if (a.is<double>() && b.is<double>()) return a.as<double>() == b.as<double>();
        if (a.is<double>() || b.is<double>()) {
            double d = a.is<double>() ? a.as<double>() : b.as<double>(); const Obj& n = a.is<double>() ? b : a;
            auto t = detail::integral_text(d); return t && *t == detail::num_key(n);
        }
        return detail::num_key(a) == detail::num_key(b);
    }
    if (a.v.index() != b.v.index()) return false;
    if (a.is<None>()) return true;
    if (a.is<Str>()) return a.as<Str>().s == b.as<Str>().s;
    if (a.is<Bytes>()) return a.as<Bytes>().b == b.as<Bytes>().b;
    if (a.is<List>()) { const List& x = a.as<List>(); const List& y = b.as<List>(); if (x.size() != y.size()) return false;
        for (size_t i = 0; i < x.size(); ++i) if (!python_eq(x[i], y[i])) return false;
        return true; }
    const Dict& x = a.as<Dict>(); const Dict& y = b.as<Dict>(); if (x.size() != y.size()) return false;
    for (size_t i = 0; i < x.size(); ++i) { long j = y.find(x.keys[i]); if (j < 0 || !python_eq(x.vals[i], y.vals[size_t(j)])) return false; }
    return true;
}
inline bool is_str(const Obj& o, const char* s) { return o.is<Str>() && o.as<Str>().s == s; }
inline const Obj* get(const Dict& d, const std::string& k) { long i = d.find(k); return i < 0 ? nullptr : &d.vals[size_t(i)]; }
inline bool truthy(const Obj* o) {                                    // Python truthiness of what .get() returned
    if (!o || o->is<None>()) return false;
    if (o->is<bool>()) return o->as<bool>();
    if (o->is<Int>()) return o->as<Int>().big || o->as<Int>().v != 0;
    if (o->is<double>()) return o->as<double>() != 0.0;
    if (o->is<Str>()) return !o->as<Str>().s.empty();
    if (o->is<Bytes>()) return !o->as<Bytes>().b.empty();
    if (o->is<List>()) return !o->as<List>().empty();
    return o->as<Dict>().size() != 0;
}
inline std::vector<std::string> str_list(const Obj* o) {              // a list of str (a field_order)
    std::vector<std::string> r; if (!truthy(o)) return r;
    for (const Obj& x : o->as<List>()) r.push_back(x.as<Str>().s);
    return r;
}
inline Obj sanitize_nonfinite(const Obj& o) {
    if (o.is<double>()) return std::isfinite(o.as<double>()) ? o : Obj(None{});
    if (o.is<Dict>()) { Dict d; const Dict& s = o.as<Dict>(); for (size_t i = 0; i < s.size(); ++i) d.set(s.keys[i], sanitize_nonfinite(s.vals[i])); return d; }
    if (o.is<List>()) { List l; for (const Obj& x : o.as<List>()) l.push_back(sanitize_nonfinite(x)); return l; }
    return o;
}

// json.dumps(obj, ensure_ascii=True, allow_nan=True); default_seps: (', ', ': '), else (',', ':')
namespace detail {
inline void dumps_ascii_str(std::string& o, std::string_view s) {
    bool plain = true;                                   // printable ASCII with no quote or backslash: copied as is
    for (char ch : s) { uint8_t c = uint8_t(ch); if (c < 0x20 || c > 0x7e || c == '"' || c == '\\') { plain = false; break; } }
    if (plain) { o += '"'; o.append(s.data(), s.size()); o += '"'; return; }
    o += '"'; char b[16];
    for (uint32_t c : u::cps(s)) {
        if (c == '"') o += "\\\""; else if (c == '\\') o += "\\\\"; else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r";
        else if (c == '\t') o += "\\t"; else if (c == '\b') o += "\\b"; else if (c == '\f') o += "\\f";
        else if (c >= 0x20 && c <= 0x7e) o += char(c);
        else if (c < 0x10000) { std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else { uint32_t v = c - 0x10000; std::snprintf(b, sizeof b, "\\u%04x", 0xD800 + (v >> 10)); o += b;
               std::snprintf(b, sizeof b, "\\u%04x", 0xDC00 + (v & 0x3FF)); o += b; }
    }
    o += '"';
}
inline void dumps_ascii(std::string& o, const Obj& v, bool dflt) {
    const char* is = dflt ? ", " : ","; const char* ks = dflt ? ": " : ":";
    if (v.is<None>()) o += "null";
    else if (v.is<bool>()) o += v.as<bool>() ? "true" : "false";
    else if (v.is<Int>()) o += v.as<Int>().str();
    else if (v.is<double>()) { double d = v.as<double>();
        if (std::isnan(d)) o += "NaN"; else if (std::isinf(d)) o += d > 0 ? "Infinity" : "-Infinity"; else o += semcodec::detail::float_repr(d); }
    else if (v.is<Str>()) dumps_ascii_str(o, v.as<Str>().s);
    else if (v.is<List>()) { o += '['; bool f = true; for (const Obj& x : v.as<List>()) { if (!f) o += is; f = false; dumps_ascii(o, x, dflt); } o += ']'; }
    else if (v.is<Dict>()) { const Dict& d = v.as<Dict>(); o += '{';
        for (size_t i = 0; i < d.size(); ++i) { if (i) o += is; dumps_ascii_str(o, d.keys[i]); o += ks; dumps_ascii(o, d.vals[i], dflt); } o += '}'; }
    else throw pyv::TypeError("Object of type bytes is not JSON serializable");
}
}  // namespace detail
inline std::string dumps_ascii(const Obj& v, bool default_seps) { std::string o; detail::dumps_ascii(o, v, default_seps); return o; }

// ---------------------------------------------------------------------------------------------- core/json_handler.py typing
inline bool is_ipv4(std::string_view s0) {                           // ^\d{1,3}(\.\d{1,3}){3}$ on strip(), each 0..255
    std::vector<uint32_t> s = u::strip(u::cps(s0)); std::vector<int> parts; size_t i = 0;
    for (int g = 0; g < 4; ++g) {
        if (g) { if (i >= s.size() || s[i] != '.') return false; ++i; }
        int n = 0, v = 0; while (i < s.size() && n < 3 && u::digit(s[i]) >= 0) { v = v * 10 + u::digit(s[i]); ++i; ++n; }
        if (n == 0) return false;
        parts.push_back(v);
    }
    if (i != s.size()) return false;
    for (int p : parts) if (p > 255) return false;
    return true;
}
inline bool looks_like_interface(std::string_view s0) {
    std::vector<uint32_t> s = u::strip(u::cps(s0)); if (s.empty()) return false;
    std::string t = u::enc(s); if (t == "lo") return true;
    for (const char* p : {"eth", "wlan", "wl", "enp", "wlp", "br", "docker", "usb", "veth"}) {
        size_t n = std::strlen(p); if (t.compare(0, n, p) != 0) continue;
        bool ok = true; for (size_t k = n; k < s.size(); ++k) if (!u::is_word(s[k])) { ok = false; break; }
        if (ok) return true;
    }
    return false;
}
inline bool looks_like_enum(std::string_view s0) {
    std::vector<uint32_t> s = u::strip(u::cps(s0)); if (s.empty()) return false;
    std::string up; bool ascii_up = true;                                  // s.upper() if it is all A-Z, else no match
    for (uint32_t c : s) { const char* m = nullptr; for (const auto& e : pyuni_s3::UPPER_AZ) if (e.cp == c) { m = e.to; break; }
        if (!m) { ascii_up = false; break; } up += m; }
    if (ascii_up) for (const char* w : {"FAST", "SEMANTIC", "LOCAL", "DOWN", "UNKNOWN", "GOOD", "OK", "WARN", "BAD"}) if (up == w) return true;
    if (s.size() < 2) return false;
    for (uint32_t c : s) if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
    return true;
}
inline bool is_plausible_host(std::string_view s0) {
    std::string s = u::strip(s0);
    if (s.empty() || u::has(s, " ")) return false;
    if (is_ipv4(s) || looks_like_interface(s) || looks_like_enum(s)) return false;
    bool alpha = false; for (char c : s) if (std::isalpha(uint8_t(c)) && uint8_t(c) < 0x80) alpha = true;
    if (!alpha) return false;
    if (!u::has(s, ".") && !u::starts(s, "FrogNetHost")) return false;
    auto an = [](char c) { return uint8_t(c) < 0x80 && std::isalnum(uint8_t(c)); };
    if (s.size() < 2 || !an(s.front()) || !an(s.back())) return false;
    for (char c : s) if (!(an(c) || c == '.' || c == '-')) return false;
    return true;
}
namespace detail {
inline bool force_string_for_path(std::string_view path) {
    if (path.empty()) return false;
    std::string p = u::lower(path);
    for (const char* n : {"dev", "kind", "run_id", "sensortype", "metricname", "method", "action", "tags", "status", "reason"})
        if (p == n || u::ends(p, std::string(".") + n)) return true;
    return false;
}
inline bool force_host_for_path(std::string_view path) {
    if (path.empty()) return false;
    std::string p = u::lower(path);
    for (const char* n : {"sensorname", "networkname", "peer_name", "host_name"}) if (u::ends(p, n)) return true;
    return false;
}
}  // namespace detail
inline std::string leaf_type(const Obj& v, std::string_view path) {
    if (v.is<None>()) return "any";
    if (v.is<bool>()) return "bool";
    if (v.is<Int>()) return "int";
    if (v.is<double>()) return "float";
    if (v.is<Str>()) { const std::string& s = v.as<Str>().s;
        if (is_ipv4(s)) return "ip";
        if (detail::force_string_for_path(path)) return "string";
        if (detail::force_host_for_path(path)) return is_plausible_host(s) ? "host" : "string";
        if (looks_like_interface(s) || looks_like_enum(s)) return "string";
        return is_plausible_host(s) ? "host" : "string"; }
    return "any";
}

// ---------------------------------------------------------------------------------------------- schema
namespace detail {
inline Dict type1(const std::string& t) { Dict d; d.set("type", Str{t}); return d; }
inline bool safe_key(const std::string& k) {                          // ^[A-Za-z_][A-Za-z0-9_]*$ ($ also before a final \n)
    std::string s = u::ends(k, "\n") ? k.substr(0, k.size() - 1) : k;
    if (s.empty()) return false;
    auto first = [](char c) { return uint8_t(c) < 0x80 && (std::isalpha(uint8_t(c)) || c == '_'); };
    auto rest = [](char c) { return uint8_t(c) < 0x80 && (std::isalnum(uint8_t(c)) || c == '_'); };
    if (!first(s[0])) return false;
    for (char c : s.substr(1)) if (!rest(c)) return false;
    return true;
}
inline std::vector<std::string> sorted_keys(const Dict& d) { std::vector<std::string> k = d.keys; std::sort(k.begin(), k.end()); return k; }
}  // namespace detail
inline Obj merge_schema(const Obj* a, const Obj* b);
inline Obj infer_schema(const Obj& v, std::string_view path) {
    if (v.is<None>()) return detail::type1("any");
    if (v.is<bool>() || v.is<Int>() || v.is<double>() || v.is<Str>()) return detail::type1(leaf_type(v, path));
    if (v.is<Dict>()) { const Dict& d = v.as<Dict>();
        for (const auto& k : d.keys) if (!detail::safe_key(k)) return detail::type1("any");
        Dict out; for (const auto& k : detail::sorted_keys(d)) out.set(k, infer_schema(*get(d, k), path.empty() ? k : std::string(path) + "." + k));
        return out; }
    if (v.is<List>()) { const List& l = v.as<List>(); Dict out;
        if (l.empty()) { out.set("_array", detail::type1("any")); return out; }
        std::optional<Obj> item; for (const Obj& it : l) { Obj s = infer_schema(it, path); item = item ? merge_schema(&*item, &s) : s; }
        out.set("_array", *item); return out; }
    return detail::type1("any");
}
inline Obj merge_schema(const Obj* a, const Obj* b) {                 // nullptr and None are both Python None
    if (!a || a->is<None>()) return b ? *b : Obj(None{});
    if (!b || b->is<None>()) return *a;
    auto one_type = [](const Obj* x) { return x->is<Dict>() && get(x->as<Dict>(), "type") && x->as<Dict>().size() == 1; };
    if (one_type(a)) {
        if (!one_type(b)) return detail::type1("any");
        const Obj& ta = *get(a->as<Dict>(), "type"); const Obj& tb = *get(b->as<Dict>(), "type");
        Dict r;
        if (is_str(ta, "any")) { r.set("type", tb); return r; }
        if (is_str(tb, "any")) { r.set("type", ta); return r; }
        if (python_eq(ta, tb)) { r.set("type", ta); return r; }
        if ((is_str(ta, "int") && is_str(tb, "float")) || (is_str(ta, "float") && is_str(tb, "int"))) return detail::type1("float");
        return detail::type1("any");
    }
    if (a->is<Dict>() && get(a->as<Dict>(), "_array")) {
        if (b->is<Dict>() && get(b->as<Dict>(), "_array")) { Dict r; r.set("_array", merge_schema(get(a->as<Dict>(), "_array"), get(b->as<Dict>(), "_array"))); return r; }
        return detail::type1("any");
    }
    if (a->is<Dict>() && b->is<Dict>()) {
        std::vector<std::string> keys = a->as<Dict>().keys; for (const auto& k : b->as<Dict>().keys) keys.push_back(k);
        std::sort(keys.begin(), keys.end()); keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
        Dict out; for (const auto& k : keys) out.set(k, merge_schema(get(a->as<Dict>(), k), get(b->as<Dict>(), k))); return out;
    }
    return detail::type1("any");
}
namespace detail {
inline void field_order(const Obj& schema, const std::string& prefix, std::vector<std::string>& fo, Dict& tm) {
    auto add = [&](const std::string& p, const Obj& t) { fo.push_back(p); tm.set(p, t); };
    static const char* allowed[] = {"ip", "host", "string", "int", "float", "bool", "raw", "any", "array"};
    auto clean = [&](const Obj& t) -> Obj { for (const char* a : allowed) if (is_str(t, a)) return t; return Str{"any"}; };
    const std::string here = prefix.empty() ? "value" : prefix;
    if (schema.is<Dict>() && get(schema.as<Dict>(), "_array")) { add(here, Str{"array"}); return; }
    if (schema.is<Dict>() && get(schema.as<Dict>(), "type") && schema.as<Dict>().size() == 1) { add(here, clean(*get(schema.as<Dict>(), "type"))); return; }
    if (schema.is<Dict>()) {
        const Dict& d = schema.as<Dict>();
        for (const auto& k : sorted_keys(d)) {
            const Obj& child = *get(d, k); std::string cp = prefix.empty() ? k : prefix + "." + k;
            if (child.is<Dict>() && get(child.as<Dict>(), "_array")) { add(cp, Str{"array"}); continue; }
            if (child.is<Dict>() && get(child.as<Dict>(), "type") && child.as<Dict>().size() == 1) { add(cp, clean(*get(child.as<Dict>(), "type"))); continue; }
            field_order(child, cp, fo, tm);
        }
        return;
    }
    add(here, Str{"any"});
}
}  // namespace detail
inline std::pair<std::vector<std::string>, Dict> field_order_and_type_map(const Obj& schema) {
    std::vector<std::string> fo; Dict tm; detail::field_order(schema, "", fo, tm); return {fo, tm};
}
inline Obj get_by_path(const Obj& obj, const std::string& path) {
    if (path.empty()) return None{};
    const Obj* cur = &obj;
    for (const auto& part : u::split(path, '.')) { if (!cur->is<Dict>()) return None{}; const Obj* n = get(cur->as<Dict>(), part); if (!n) return None{}; cur = n; }
    return *cur;
}

// ---------------------------------------------------------------------------------------------- template_utils.rebuild_json
namespace detail {
inline Dict& child_dict(Dict& cur, const std::string& p) {
    long i = cur.find(p); if (i < 0 || !cur.vals[size_t(i)].is<Dict>()) { cur.set(p, Dict{}); i = cur.find(p); }
    return cur.vals[size_t(i)].as<Dict>();
}
}  // namespace detail
inline Obj rebuild_json(const List& values, const std::vector<std::string>& field_order) {
    Dict root;
    for (size_t n = 0; n < std::min(values.size(), field_order.size()); ++n) {
        const Obj& val = values[n]; auto parts = u::split(field_order[n], '.');
        if (parts[0] == "rows") {
            if (root.find("rows") < 0) root.set("rows", List{});
            List& rows = root.vals[size_t(root.find("rows"))].as<List>();
            if (rows.empty()) rows.push_back(Dict{});
            Dict* cur = &rows[0].as<Dict>();
            for (size_t k = 1; k + 1 < parts.size(); ++k) cur = &detail::child_dict(*cur, parts[k]);
            cur->set(parts.back(), val); continue;
        }
        Dict* cur = &root; for (size_t k = 0; k + 1 < parts.size(); ++k) cur = &detail::child_dict(*cur, parts[k]);
        cur->set(parts.back(), val);
    }
    return root;
}
inline Obj first_row_only(const Obj& o) {
    if (o.is<List>()) { List r; if (!o.as<List>().empty()) r.push_back(first_row_only(o.as<List>()[0])); return r; }
    if (o.is<Dict>()) { Dict d; const Dict& s = o.as<Dict>(); for (size_t i = 0; i < s.size(); ++i) d.set(s.keys[i], first_row_only(s.vals[i])); return d; }
    return o;
}
namespace detail {
inline void empty_arrays(const Obj& o, const std::string& path, std::vector<std::string>& out) {
    if (o.is<List>()) { const List& l = o.as<List>(); if (l.empty()) out.push_back(path.empty() ? "<root>" : path);
        else for (size_t i = 0; i < l.size(); ++i) empty_arrays(l[i], path + "[" + std::to_string(i) + "]", out); }
    else if (o.is<Dict>()) { const Dict& d = o.as<Dict>(); for (size_t i = 0; i < d.size(); ++i) empty_arrays(d.vals[i], path.empty() ? d.keys[i] : path + "." + d.keys[i], out); }
}
}  // namespace detail
inline std::vector<std::string> empty_arrays_in(const Obj& o) { std::vector<std::string> r; detail::empty_arrays(o, "", r); return r; }

// ---------------------------------------------------------------------------------------------- core/blob_store.py
namespace blob {
inline std::string root() { const char* r = std::getenv("FROGNET_BLOB_ROOT"); return r ? r : "/opt/frognet_semantic/blob_cache"; }
inline std::string id_for(std::string_view data) {
    unsigned char h[SHA256_DIGEST_LENGTH]; SHA256(reinterpret_cast<const unsigned char*>(data.data()), data.size(), h);
    static const char* x = "0123456789abcdef"; std::string o = "sha256-";
    for (unsigned char c : h) { o += x[c >> 4]; o += x[c & 15]; } return o;
}
inline std::string store(std::string_view data) {
    std::string id = id_for(data), dir = root(), path = dir + "/" + id; struct stat st {};
    if (::stat(path.c_str(), &st) == 0) return id;
    if (::mkdir(dir.c_str(), 0755) != 0 && errno != EEXIST) throw std::runtime_error("blob store: mkdir " + dir + ": " + std::strerror(errno));
    std::string tmp = path + ".tmp";
    { std::ofstream f(tmp, std::ios::binary); if (!f) throw std::runtime_error("blob store: cannot write " + tmp);
      f.write(data.data(), std::streamsize(data.size())); if (!f) throw std::runtime_error("blob store: short write " + tmp); }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) throw std::runtime_error("blob store: rename " + tmp + ": " + std::strerror(errno));
    return id;
}
inline std::string load(const std::string& id) {
    std::string path = root() + "/" + id; std::ifstream f(path, std::ios::binary);
    if (!f) throw NotFound("blob " + id + " not found under " + root());
    std::ostringstream s; s << f.rdbuf(); return s.str();
}
}  // namespace blob

// ---------------------------------------------------------------------------------------------- handlers
namespace detail {
inline Dict tokens() { Dict t; for (const char* k : {"ip", "host", "str", "enum"}) t.set(k, Dict{}); return t; }
inline Dict json_frag(Obj schema, List fo, Dict tm, Dict baseline, Dict blobs) {
    Dict d; d.set("mode", Str{"json"}); d.set("schema", std::move(schema)); d.set("field_order", std::move(fo)); d.set("type_map", std::move(tm));
    d.set("baseline", std::move(baseline)); d.set("baseline_blobs", std::move(blobs)); d.set("tokens", tokens()); return d;
}
inline Dict text_frag(const char* mode, const std::string& body) {
    Dict d, tm, base; d.set("mode", Str{mode}); d.set("fields", List{Str{"raw"}}); d.set("field_order", List{Str{"raw"}});
    tm.set("raw", Str{"raw"}); d.set("type_map", tm); base.set("raw", Str{body}); d.set("baseline", base); d.set("baseline_blobs", Dict{});
    d.set("tokens", tokens()); return d;
}
inline std::optional<Obj> json_try(std::string_view text) {           // json.loads, or none where the Python's except fires
    try { return semcodec::json_loads(text); }
    catch (const pyv::JsonError&) { return std::nullopt; }
    catch (const pyv::RecursionError&) { return std::nullopt; }
}
inline const char* ported_mode(const std::string& m) {
    if (m == "json" || m == "text" || m == "raw") return m.c_str();
    if (m == "xml" || m == "html" || m == "sotf_media") throw NotPorted("handler " + m);
    throw Declined("unknown_mode");
}
inline std::string mode_of(const Dict& frag) { const Obj* m = get(frag, "mode"); if (!m) return "raw"; if (!m->is<Str>()) throw Declined("unknown_mode"); return m->as<Str>().s; }
inline std::string py_str(const Obj& v) {                               // str(v) for what the codec yields
    if (v.is<Str>()) return v.as<Str>().s;
    if (v.is<Int>()) return v.as<Int>().str();
    if (v.is<bool>()) return v.as<bool>() ? "True" : "False";
    if (v.is<double>()) { double d = v.as<double>(); if (std::isnan(d)) return "nan"; if (std::isinf(d)) return d > 0 ? "inf" : "-inf";
                          return semcodec::detail::float_repr(d); }
    if (v.is<None>()) return "None";
    throw Declined("text_repr");
}
}  // namespace detail
inline Dict learn(std::string_view mode, std::string_view body, bool /*reply: the handlers learn requests and replies alike*/) {
    const std::string m(mode); detail::ported_mode(m);
    if (m != "json") return detail::text_frag(m == "text" ? "text" : "raw", std::string(body));
    std::string text = u::strip(body);
    if (text.empty()) return detail::json_frag(Dict{}, List{}, Dict{}, Dict{}, Dict{});
    auto parsed = detail::json_try(text);
    if (!parsed) throw Declined("json_unparsable");
    Obj obj = sanitize_nonfinite(*parsed);
    if (obj.is<List>()) { Dict tm, base; tm.set("value", Str{"array"}); base.set("value", obj);
        return detail::json_frag(infer_schema(obj, ""), List{Str{"value"}}, tm, base, Dict{}); }
    if (obj.is<Dict>()) {
        Obj schema = infer_schema(obj, ""); auto [fo, tm] = field_order_and_type_map(schema);
        Dict base, blobs; List fol;
        for (const auto& p : fo) {
            fol.push_back(Str{p}); Obj v = p == "value" ? obj : get_by_path(obj, p);
            if (v.is<List>()) {
                std::string ser = semcodec::json_dumps(v); semcodec::detail::encode_strict(ser);   // .encode('utf-8') is strict
                if (ser.size() > 8192) { blobs.set(p, Str{blob::store(ser)}); base.set(p, None{}); continue; }
            }
            base.set(p, v);
        }
        return detail::json_frag(schema, fol, tm, base, blobs);
    }
    std::string t = leaf_type(obj, "value"); Dict tm, base; tm.set("value", Str{t}); base.set("value", obj);
    return detail::json_frag(detail::type1(t), List{Str{"value"}}, tm, base, Dict{});
}
// extract: the fields, or std::nullopt = this template cannot hold that body (the caller sends it RAW)
inline std::optional<Fields> extract(const Dict& frag, std::string_view body, bool /*reply*/) {
    if (body.empty()) return Fields{};
    const std::string m = detail::mode_of(frag); detail::ported_mode(m);
    if (m != "json") return Fields{{"raw", Str{std::string(body)}}};
    std::string text = u::strip(body); std::vector<std::string> fo = str_list(get(frag, "field_order"));
    if (text.empty() || fo.empty()) return Fields{};
    auto parsed = detail::json_try(text); if (!parsed) return std::nullopt;
    Obj obj = sanitize_nonfinite(*parsed);
    if (obj.is<List>() && fo == std::vector<std::string>{"value"}) return Fields{{"value", obj}};
    if (!obj.is<Dict>()) return std::nullopt;
    Fields f; for (const auto& p : fo) f.emplace_back(p, p == "value" ? obj : get_by_path(obj, p)); return f;
}
namespace detail {
inline List coerce_array(const Obj& v) {                                // a list, or JSON text of one; anything else is declined
    if (v.is<List>()) return v.as<List>();
    if (v.is<Str>()) { std::string s = u::strip(v.as<Str>().s); if (s.empty()) return List{};
        auto j = json_try(s); if (j && j->is<List>()) return j->as<List>(); throw Declined("array_literal"); }
    throw Declined("array_type");
}
}  // namespace detail
inline std::string rebuild_reply(const Dict& frag, const List& values) {
    const std::string m = detail::mode_of(frag); detail::ported_mode(m);
    if (m != "json") {
        if (values.empty()) { const Obj* b = get(frag, "baseline"); if (!truthy(b)) return ""; const Obj* r = get(b->as<Dict>(), "raw");
                              return r ? r->as<Str>().s : ""; }
        const Obj& v0 = values[0];
        if (v0.is<List>() && v0.as<List>().size() == 2) throw Declined("pair_heuristic");
        return v0.is<None>() ? "" : detail::py_str(v0);
    }
    std::vector<std::string> fo = str_list(get(frag, "field_order"));
    if (fo.empty()) return "{}";
    if (!values.empty() && values[0].is<List>() && values[0].as<List>().size() == 2) throw Declined("pair_heuristic");
    Dict valmap; for (size_t i = 0; i < std::min(fo.size(), values.size()); ++i) valmap.set(fo[i], values[i]);
    const Obj* tmo = get(frag, "type_map"); Dict tm = truthy(tmo) ? tmo->as<Dict>() : Dict{};
    if (fo == std::vector<std::string>{"value"} && get(tm, "value") && is_str(*get(tm, "value"), "array")) {
        const Obj* a = get(valmap, "value"); Obj arr = a ? *a : Obj(None{});
        if (arr.is<None>()) { const Obj* b = get(frag, "baseline"); const Obj* bv = truthy(b) ? get(b->as<Dict>(), "value") : nullptr; arr = bv ? *bv : Obj(List{}); }
        return dumps_ascii(sanitize_nonfinite(Obj(detail::coerce_array(arr))), false);
    }
    const Obj* bbo = get(frag, "baseline_blobs"); Dict blobs = truthy(bbo) ? bbo->as<Dict>() : Dict{};
    Dict arrays; std::vector<std::string> sfo;
    for (const auto& p : fo) {
        const Obj* t = get(tm, p);
        if (t && is_str(*t, "array")) {
            const Obj* v = get(valmap, p); std::optional<List> arr;
            if (v && !v->is<None>()) arr = detail::coerce_array(*v);
            if ((!arr || arr->empty()) && get(blobs, p)) {
                std::string data;
                try { data = blob::load(get(blobs, p)->as<Str>().s); } catch (const NotFound&) { throw Declined("blob_missing"); }
                auto j = semcodec::json_loads(data); arr = j.as<List>();
            }
            arrays.set(p, arr ? Obj(*arr) : Obj(None{}));
        } else sfo.push_back(p);
    }
    List sv; for (const auto& p : sfo) { const Obj* v = get(valmap, p); sv.push_back(v ? *v : Obj(None{})); }
    Obj obj = rebuild_json(sv, sfo);
    for (size_t i = 0; i < arrays.size(); ++i) {
        if (arrays.vals[i].is<None>()) continue;
        auto parts = u::split(arrays.keys[i], '.'); Dict* cur = &obj.as<Dict>();
        for (size_t k = 0; k + 1 < parts.size(); ++k) cur = &detail::child_dict(*cur, parts[k]);
        cur->set(parts.back(), arrays.vals[i]);
    }
    return dumps_ascii(sanitize_nonfinite(obj), false);
}

// ---------------------------------------------------------------------------------------------- core/template.py
inline std::string build_url(std::string_view url_static, const Fields& url_vals) {
    std::string us = url_static.empty() ? "/" : std::string(url_static);
    UrlParts p = urlparse(us); Pairs q = parse_qsl(p.query);
    for (const auto& kv : url_vals) {
        if (kv.second.is<None>()) throw pyv::ValueError("build_url: " + kv.first + " is None for " + us + " - refusing to drop the parameter");
        q.emplace_back(kv.first, detail::py_str(kv.second));
    }
    std::string qs = q.empty() ? "" : urlencode(q), path = p.path.empty() ? "/" : p.path;
    return qs.empty() ? path : path + "?" + qs;
}
inline std::string rebuild_body(const Dict& frag, const Fields& json_vals) {
    Dict mapping; for (const auto& kv : json_vals) mapping.set(kv.first, kv.second);
    const Obj* mo = get(frag, "mode");                  // not handler-based: a non-str mode is just not one of the names
    const std::string m = !mo ? "raw" : mo->is<Str>() ? mo->as<Str>().s : std::string("\x01not-a-name");
    if (m == "json") {
        std::vector<std::string> fo = str_list(get(frag, "field_order")); List vals;
        for (const auto& f : fo) { const Obj* v = get(mapping, f); vals.push_back(v ? *v : Obj(None{})); }
        return dumps_ascii(rebuild_json(vals, fo), false);
    }
    if (m == "raw" || m == "text" || m == "html" || m == "xml") {
        const Obj* r = get(mapping, "raw"); if (!r) return ""; return r->is<None>() ? "" : detail::py_str(*r);
    }
    if (mapping.size()) { const Obj& v0 = mapping.vals[0]; return v0.is<None>() ? "" : detail::py_str(v0); }
    return "";
}
inline std::string content_type(const Dict& frag) {
    const Obj* mo = get(frag, "mode"); if (mo && !mo->is<Str>()) return "application/octet-stream";
    std::string m = mo ? mo->as<Str>().s : "raw";
    if (m == "json") return "application/json";
    if (m == "xml") return "application/xml";
    if (m == "html") return "text/html; charset=utf-8";
    if (m == "raw" || m == "text") return "text/plain; charset=utf-8";
    return "application/octet-stream";
}

// ---------------------------------------------------------------------------------------------- core/format_registry.py
namespace detail {
inline bool looks_like_json(const std::vector<uint32_t>& text) {
    std::vector<uint32_t> t = u::strip(text); if (t.empty() || (t[0] != '{' && t[0] != '[')) return false;
    return json_try(u::enc(t)).has_value();      // the running Python decides by parsing; this makes the same decision
}
inline bool xml_gate(const std::vector<uint32_t>& text) {       // _looks_like_xml up to its ElementTree call
    std::vector<uint32_t> t = u::strip(text, true, false); if (t.empty() || t[0] != '<') return false;
    std::vector<uint32_t> head(t.begin(), t.begin() + long(std::min<size_t>(t.size(), 9)));
    std::string lh = u::enc(u::lower(head)); if (u::starts(lh, "<!doctype")) return false;
    size_t n = std::min<size_t>(t.size(), 200);
    for (size_t i = 0; i + 1 < n; ++i) if (t[i] == '<') { uint32_t c = t[i + 1];
        if (c < 0x80 && (std::isalnum(int(c)) || c == '-' || c == '_')) return true; }
    return false;
}
inline bool looks_like_html(const std::vector<uint32_t>& text) {
    std::vector<uint32_t> t = u::lower(text); std::string s = u::enc(t);
    if (u::starts(s, "<!doctype html") || u::starts(s, "<html")) return true;
    std::string h500 = u::enc(t, 0, 500); if (u::has(h500, "<html") || u::has(h500, "<body")) return true;
    size_t n = std::min<size_t>(t.size(), 400), tags = 0, i = 0;
    while (i < n) {
        if (t[i] == '<') { size_t j = i + 1; if (j < n && t[j] == '/' && j + 1 < n && u::icase_az09(t[j + 1])) ++j;
            if (j < n && u::icase_az09(t[j])) { while (j < n && u::icase_az09(t[j])) ++j; ++tags; i = j; continue; } }
        ++i;
    }
    return tags >= 8;
}
inline bool looks_like_text(const std::vector<uint32_t>& t) {
    if (t.empty()) return false;
    size_t bad = 0;
    for (uint32_t c : t) if (c < 9 || (c > 126 && c < 160)) ++bad;
    return double(bad) <= double(t.size()) * 0.15;
}
inline std::string header(const Pairs& h, const char* k) { for (const auto& kv : h) if (kv.first == k) return kv.second; return ""; }
inline std::string ctype(const Pairs& h) { std::string v = header(h, "Content-Type"); if (v.empty()) v = header(h, "content-type"); return u::lower(v); }
inline std::string notported(const std::string& m) { if (m == "xml" || m == "html") throw NotPorted("detected " + m); return m; }
}  // namespace detail
inline std::string sniff_body_mode(std::string_view body) {
    if (body.empty()) return "raw";
    std::vector<uint32_t> t = u::cps(u::decode_replace(body));
    if (detail::looks_like_json(t)) return "json";
    if (detail::xml_gate(t)) throw NotPorted("detection reaches an XML parse");
    if (detail::looks_like_html(t)) return detail::notported("html");
    if (detail::looks_like_text(t)) return "text";
    return "raw";
}
inline std::string detect_request_mode(const Pairs& headers, std::string_view body) {
    std::string ct = detail::ctype(headers);
    if (u::has(ct, "json")) return "json";
    if (u::has(ct, "xml")) return detail::notported("xml");
    if (u::has(ct, "html")) {
        std::string s = u::lower(u::strip(u::decode_replace(body.substr(0, std::min<size_t>(body.size(), 512)))));
        if (u::has(s, "<html") || u::has(s, "<!doctype html") || u::starts(s, "<")) return detail::notported("html");
    }
    return sniff_body_mode(body);
}
inline std::string detect_reply_mode(const Pairs& headers, std::string_view body) {
    std::string ct = detail::ctype(headers);
    std::string s = u::lower(u::strip(u::decode_replace(body.substr(0, std::min<size_t>(body.size(), 1024)))));
    if (u::has(ct, "application/json") || u::starts(s, "{") || u::starts(s, "[")) return "json";
    bool tag = s.size() >= 2 && s[0] == '<' && uint8_t(s[1]) < 0x80 && (std::islower(uint8_t(s[1])) || std::isdigit(uint8_t(s[1])) || s[1] == '-' || s[1] == '_');
    if ((u::has(ct, "xml") || u::starts(s, "<?xml") || tag) && !u::has(s, "<html") && !u::has(s, "<!doctype html")) return detail::notported("xml");
    if (u::has(ct, "html") || u::starts(s, "<"))
        if (u::has(s, "<!doctype html") || u::has(s, "<html") || u::has(s, "<body") || detail::looks_like_html(u::cps(s))) return detail::notported("html");
    if (u::starts(ct, "text/")) return "text";
    if (detail::looks_like_text(u::cps(s))) return "text";
    return "raw";
}

// ---------------------------------------------------------------------------------------------- learn_templates_from_real
struct Learned { bool refused_empty = false; std::vector<std::string> empty_at; Dict req, resp; };
inline Learned learn_templates(std::string_view /*method*/, std::string_view /*semantic_path*/, const Pairs& req_headers,
                               std::string_view req_body, const Pairs& /*resp_headers: unseen by the Python, see below*/,
                               std::string_view resp_body, std::string_view raw_path) {
    try {
        Learned out;
        std::string req_mode = detect_request_mode(req_headers, req_body);
        out.req = learn(req_mode, req_body.empty() ? "" : u::decode_replace(req_body), false);
        // The Python passes the whole upstream dict as the reply "headers", so the reply's Content-Type is never read.
        std::string resp_mode = detect_reply_mode(Pairs{}, resp_body);
        std::string resp_text = resp_body.empty() ? "" : u::decode_replace(resp_body), learn_text = resp_text;
        if (auto j = detail::json_try(resp_text)) learn_text = dumps_ascii(first_row_only(*j), true);
        out.resp = learn(resp_mode, learn_text, true);
        if (!raw_path.empty()) { auto dyn = normalize_path_for_semantics(raw_path).second;
            if (!dyn.empty()) { List l; for (auto& k : dyn) l.push_back(Str{k}); out.req.set("url_query_keys", l); } }
        const Obj* b = get(out.resp, "baseline"); out.empty_at = empty_arrays_in(b ? *b : Obj(None{}));
        out.refused_empty = !out.empty_at.empty();
        return out;
    } catch (const Declined&) { throw; } catch (const NotPorted&) { throw; }
    catch (const std::exception& e) {                  // the Python prints and returns; ruled: abort, loudly
        std::cerr << "[SEMTPL-TRAIN] learn FAILED: " << e.what() << "\n" << std::flush;
        throw Declined("train_exception");
    }
}

}  // namespace semtpl
