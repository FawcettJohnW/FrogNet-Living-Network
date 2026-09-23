// Stream helpers shared by cmd_stream.cpp (entries) and cmd_stream_groups.cpp (consumer groups).
#pragma once
#include "server.hpp"
#include "blocking.hpp"
#include <climits>
#include <cstring>
#include <cerrno>
#include <algorithm>
struct SID { uint64_t ms = 0, seq = 0; bool operator<(const SID& o) const { return ms < o.ms || (ms == o.ms && seq < o.seq); } bool operator==(const SID& o) const { return ms == o.ms && seq == o.seq; } bool operator<=(const SID& o) const { return !(o < *this); } };
static std::string sid_str(const SID& id) { return std::to_string(id.ms) + "-" + std::to_string(id.seq); }
static std::string be64(uint64_t x) { std::string s(8, '\0'); for (int i = 7; i >= 0; i--) { s[i] = (char)(x & 0xff); x >>= 8; } return s; }
static uint64_t rd64(const std::string& s, size_t off) { uint64_t x = 0; for (int i = 0; i < 8; i++) x = (x << 8) | (uint8_t)s[off + i]; return x; }
static std::string inst_of(const SID& id) { return db::ELEM + be64(id.ms) + be64(id.seq); }
static SID sid_of_inst(const std::string& inst) { SID id; id.ms = rd64(inst, 1); id.seq = rd64(inst, 9); return id; }
static const SID SID_MAX{UINT64_MAX, UINT64_MAX};

// meta cell: last id, entries_added, max_deleted, first id (recorded)
struct Meta { SID last; uint64_t added = 0; SID maxdel; SID first; };
static std::string pack_meta(const Meta& m) { return be64(m.last.ms) + be64(m.last.seq) + be64(m.added) + be64(m.maxdel.ms) + be64(m.maxdel.seq) + be64(m.first.ms) + be64(m.first.seq); }
static Meta unpack_meta(const std::string& s) { Meta m; if (s.size() < 56) return m; m.last.ms = rd64(s, 0); m.last.seq = rd64(s, 8); m.added = rd64(s, 16); m.maxdel.ms = rd64(s, 24); m.maxdel.seq = rd64(s, 32); m.first.ms = rd64(s, 40); m.first.seq = rd64(s, 48); return m; }
static Meta meta_of(const VarPtr& v) { CellPtr cp = Memory::inst(v, db::VAL); return cp ? unpack_meta(cp->bag->s) : Meta(); }
static BagPtr meta_bag(const Meta& m) { Bag b; b.kind = Kind::StreamMeta; b.s = pack_meta(m); return std::make_shared<const Bag>(std::move(b)); }
// fields: [u32 len][bytes]...
[[maybe_unused]] [[maybe_unused]] static std::string pack_fields(const Argv& a, size_t from) { std::string s; for (size_t i = from; i < a.size(); i++) { uint32_t n = (uint32_t)a[i].size(); s.append((const char*)&n, 4); s += a[i]; } return s; }
static std::vector<std::string> unpack_fields(const std::string& s) { std::vector<std::string> out; size_t i = 0; while (i + 4 <= s.size()) { uint32_t n; memcpy(&n, s.data() + i, 4); i += 4; out.push_back(s.substr(i, n)); i += n; } return out; }

static int svar(Client& c, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::StreamEntry && k != Kind::StreamMeta) { err_wrongtype(r); return -1; }
    return 1;
}
// entries only: instances under the ELEM prefix (groups, PEL and consumers live under other prefixes)
static size_t slen(const VarPtr& v) {
    if (!v) return 0;
    return v->rank(std::string(1, '\x03')) - v->rank(db::ELEM);
}
// parse an id; missing_seq fills a bare ms; strict forbids - and +
static bool parse_id(const std::string& s, SID& id, uint64_t missing_seq, bool strict, bool* seq_given = nullptr) {
    if (seq_given) *seq_given = true;
    if (s == "-") { if (strict) return false; id = SID{0, 0}; return true; }
    if (s == "+") { if (strict) return false; id = SID_MAX; return true; }
    size_t dash = s.find('-');
    std::string a = dash == std::string::npos ? s : s.substr(0, dash), b = dash == std::string::npos ? "" : s.substr(dash + 1);
    if (a.empty() || a.find_first_not_of("0123456789") != std::string::npos) return false;
    if (dash != std::string::npos && b != "*" && (b.empty() || b.find_first_not_of("0123456789") != std::string::npos)) return false;
    errno = 0; id.ms = strtoull(a.c_str(), nullptr, 10); if (errno) return false;
    if (dash == std::string::npos) id.seq = missing_seq;
    else if (b == "*") { id.seq = 0; if (seq_given) *seq_given = false; }
    else { errno = 0; id.seq = strtoull(b.c_str(), nullptr, 10); if (errno) return false; }
    return true;
}
static void err_id(Reply& r) { r.error("ERR Invalid stream ID specified as stream command argument"); }
static void reply_entry(Reply& r, const SID& id, const std::string& fields) {
    auto f = unpack_fields(fields);
    r.array(2); r.bulk(sid_str(id)); r.array(f.size()); for (auto& x : f) r.bulk(x);
}
// entries in [lo, hi], optionally reversed, at most count (0 = all)
static std::vector<std::pair<SID, std::string>> range(const VarPtr& v, const SID& lo, const SID& hi, bool reverse, long long count) {
    std::vector<std::pair<SID, std::string>> out;
    if (!v || hi < lo) return out;
    auto take = [&](const std::string& i, const CellPtr& c) { if (c->bag->kind != Kind::StreamEntry) return true; out.emplace_back(sid_of_inst(i), c->bag->s); return !(count > 0 && (long long)out.size() >= count); };
    if (!reverse) v->range(inst_of(lo), inst_of(hi), false, take); else v->rrange(inst_of(lo), inst_of(hi), false, take);
    return out;
}
[[maybe_unused]] [[maybe_unused]] static bool first_last(const VarPtr& v, SID& first, SID& last) {
    auto f = range(v, SID{0, 0}, SID_MAX, false, 1); if (f.empty()) return false;
    auto l = range(v, SID{0, 0}, SID_MAX, true, 1);
    first = f[0].first; last = l[0].first; return true;
}

