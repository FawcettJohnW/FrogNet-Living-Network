#include <optional>
// Hashes. A field is a cell (instance ELEM+field, kind HashField, bag.s = value); the hash is the
// partial index over them. HGET is one lookup, HGETALL one range read, HLEN a count, HSET one publish.
#include "server.hpp"
#include <random>
#include <climits>
#include <cmath>
#include <unordered_set>
#include <cerrno>
namespace rr {

static thread_local std::mt19937_64 rng{std::random_device{}()};

// -1 wrong type (error sent), 0 absent, 1 hash
static int hash_var(Client& c, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::HashField) { err_wrongtype(r); return -1; }
    return 1;
}
static std::optional<std::string> field(const VarPtr& v, const std::string& f) {   // the field's text: bytes plus adds
    CellPtr c = Memory::inst(v, db::ELEM + f);
    if (!c) return std::nullopt;
    return db::text(c);
}

static void cmd_hset(Client& c, const Argv& a, Reply& r) {
    if (a.size() % 2 != 0) { err_arity(r, lower(a[0])); return; }
    VarPtr v; if (hash_var(c, a[1], v, r) < 0) return;
    if (a.size() == 4) { size_t fresh = db::put_elem(c.db, a[1], Kind::HashField, a[2], a[3]); if (lower(a[0]) == "hmset") r.ok(); else r.integer((long long)fresh); return; }
    std::vector<std::pair<std::string, std::string>> nv;
    for (size_t i = 2; i < a.size(); i += 2) nv.emplace_back(a[i], a[i+1]);
    size_t fresh = db::put_elems(c.db, a[1], Kind::HashField, nv);
    if (lower(a[0]) == "hmset") r.ok(); else r.integer((long long)fresh);
}
static void cmd_hsetnx(Client& c, const Argv& a, Reply& r) {
    // [THE_MEMORY_DECIDES_V1] insert only if absent: exactly one caller creates the field
    VarPtr v; if (hash_var(c, a[1], v, r) < 0) return;
    if (field(v, a[2])) { r.integer(0); return; }
    db::Key k(c.db, a[1]);
    Bag b; b.kind = Kind::HashField; b.s = a[3];
    if (!g.mem.replace_if(k.R, k.name, k.h, db::ELEM + a[2], nullptr, std::move(b))) { r.integer(0); return; }
    g.stats.dirty++; r.integer(1);
}
static void cmd_hget(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    std::optional<std::string> f = st ? field(v, a[2]) : std::nullopt;
    if (f) r.bulk(*f); else r.null();
}
static void cmd_hmget(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) { std::optional<std::string> f = st ? field(v, a[i]) : std::nullopt; if (f) r.bulk(*f); else r.null(); }
}
static void cmd_hdel(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::string> names(a.begin() + 2, a.end());
    r.integer((long long)db::drop_elems(c.db, a[1], names));
}
static void cmd_hlen(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    r.integer(st ? (long long)db::elem_count(v) : 0);
}
static void cmd_hstrlen(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    std::optional<std::string> f = st ? field(v, a[2]) : std::nullopt;
    r.integer(f ? (long long)f->size() : 0);
}
static void cmd_hexists(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    r.integer(st && field(v, a[2]) ? 1 : 0);
}
static void hgetall_generic(Client& c, const Argv& a, Reply& r, bool keys, bool vals) {
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    auto es = st ? db::elems(v, c.db, a[1]) : std::vector<Cell>{};
    if (keys && vals) r.map(es.size()); else r.array(es.size());
    for (auto& e : es) {
        if (keys) r.bulk(e.instance.data() + 1, e.instance.size() - 1);
        if (vals) r.bulk(db::text(e.node));
    }
}
static void cmd_hgetall(Client& c, const Argv& a, Reply& r) { hgetall_generic(c, a, r, true, true); }
static void cmd_hkeys(Client& c, const Argv& a, Reply& r) { hgetall_generic(c, a, r, true, false); }
static void cmd_hvals(Client& c, const Argv& a, Reply& r) { hgetall_generic(c, a, r, false, true); }

static void cmd_hincrby(Client& c, const Argv& a, Reply& r) {
    // [THE_MEMORY_DECIDES_V1] the memory adds, on the field's own cell: every HINCRBY gets its own total
    long long incr; if (!string2ll(a[3], incr)) { err_notint(r); return; }
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    std::optional<std::string> f = st ? field(v, a[2]) : std::nullopt;
    long long v0 = 0; if (f && !string2ll(*f, v0)) { r.error("ERR hash value is not an integer"); return; }
    db::Key k(c.db, a[1]);
    ebr::Guard eg;
    Bag zero; zero.kind = Kind::HashField; zero.s = "0";
    auto ad = g.mem.add(k.R, k.name, k.h, db::ELEM + a[2], zero, incr, 0, false);
    long long base = 0;
    if (!string2ll(ad.node->bag->s, base)) { ad.node->acc.fetch_sub(incr, std::memory_order_acq_rel); r.error("ERR hash value is not an integer"); return; }
    __int128 total = (__int128)base + (__int128)ad.acc_after;
    if (total > LLONG_MAX || total < LLONG_MIN) { ad.node->acc.fetch_sub(incr, std::memory_order_acq_rel); r.error("ERR increment or decrement would overflow"); return; }
    g.stats.dirty++;
    r.integer((long long)total);
}
static void cmd_hincrbyfloat(Client& c, const Argv& a, Reply& r) {
    // [THE_MEMORY_DECIDES_V1] the memory adds, on the field's float accumulator: one fetch_add, its result final
    long double incr;
    if (!string2ld(a[3], incr)) { err_notfloat(r); return; }
    if (std::isnan(incr) || std::isinf(incr)) { r.error("ERR value is NaN or Infinity"); return; }
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    std::optional<std::string> f = st ? field(v, a[2]) : std::nullopt;
    long double v0 = 0; if (f && !string2ld(*f, v0)) { r.error("ERR hash value is not a float"); return; }
    db::Key k(c.db, a[1]);
    ebr::Guard eg;
    Bag zero; zero.kind = Kind::HashField; zero.s = "0";
    auto ad = g.mem.add(k.R, k.name, k.h, db::ELEM + a[2], zero, 0, (double)incr, true);
    long double base = 0;
    if (!string2ld(ad.node->bag->s, base)) { ad.node->facc.fetch_sub((double)incr, std::memory_order_acq_rel); r.error("ERR hash value is not a float"); return; }
    long double value = base + (long double)ad.node->acc.load(std::memory_order_acquire) + (long double)ad.facc_after;
    if (std::isnan(value) || std::isinf(value)) { ad.node->facc.fetch_sub((double)incr, std::memory_order_acq_rel); r.error("ERR increment would produce NaN or Infinity"); return; }
    g.stats.dirty++;
    r.bulk(db::float_text(value));
}

// random elements: count > 0 distinct (at most size), count < 0 with repetition, exactly |count|
std::vector<size_t> random_picks(size_t size, long long count) {
    std::vector<size_t> out;
    if (!size) return out;
    if (count < 0) { for (long long i = 0; i < -count; i++) out.push_back(rng() % size); return out; }
    size_t n = (size_t)count < size ? (size_t)count : size;
    if (n == size) { for (size_t i = 0; i < size; i++) out.push_back(i); return out; }
    std::unordered_set<size_t> seen;
    while (out.size() < n) { size_t i = rng() % size; if (seen.insert(i).second) out.push_back(i); }
    return out;
}
static void cmd_hrandfield(Client& c, const Argv& a, Reply& r) {
    bool withvalues = false; long long count = 0; bool has_count = a.size() >= 3;
    if (has_count) {
        if (!string2ll(a[2], count)) { err_notint(r); return; }
        if (count == LLONG_MIN) { r.error("ERR value is out of range"); return; }
        if (a.size() > 4 || (a.size() == 4 && !str_eq_ci(a[3], "withvalues"))) { err_syntax(r); return; }
        if (a.size() == 4) { withvalues = true; if (count < -LLONG_MAX / 2 || count > LLONG_MAX / 2) { r.error("ERR value is out of range"); return; } }
    }
    VarPtr v; int st = hash_var(c, a[1], v, r); if (st < 0) return;
    size_t size = st ? db::elem_count(v) : 0;
    auto nth = [&](size_t i) -> std::optional<Ent> {
        // elements are the instances >= ELEM: skip the VAL/TTL cells that sort before them
        size_t skip = v->rank(db::ELEM);
        return v->nth(skip + i);
    };
    if (!has_count) {
        if (!size) { r.null(); return; }
        auto x = nth(rng() % size); r.bulk(x->key.data() + 1, x->key.size() - 1); return;
    }
    if (!size || count == 0) { r.array(0); return; }
    auto picks = random_picks(size, count);
    if (withvalues && r.proto == 3) { r.array(picks.size()); for (size_t i : picks) { auto x = nth(i); r.array(2); r.bulk(x->key.data() + 1, x->key.size() - 1); r.bulk(db::text(x->val)); } }
    else if (withvalues) { r.array(picks.size() * 2); for (size_t i : picks) { auto x = nth(i); r.bulk(x->key.data() + 1, x->key.size() - 1); r.bulk(db::text(x->val)); } }
    else { r.array(picks.size()); for (size_t i : picks) { auto x = nth(i); r.bulk(x->key.data() + 1, x->key.size() - 1); } }
}

// SCAN over a container: the cursor is the rank of the next element in instance order
void elem_scan(Client& c, const Argv& a, Reply& r, Kind kind, bool with_values) {
    unsigned long cursor; char* ep = nullptr; errno = 0;
    cursor = strtoul(a[2].c_str(), &ep, 10);
    if (isspace((unsigned char)a[2][0]) || *ep != '\0' || errno == ERANGE) { r.error("ERR invalid cursor"); return; }
    long long count = 10; std::string pattern; bool use_pat = false;
    for (size_t i = 3; i < a.size(); i++) {
        size_t more = a.size() - i - 1; std::string o = lower(a[i]);
        if (o == "count" && more >= 1) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count < 1) { err_syntax(r); return; } i++; }
        else if (o == "match" && more >= 1) { pattern = a[i+1]; use_pat = pattern != "*"; i++; }
        else { err_syntax(r); return; }
    }
    VarPtr v = db::live_var(c.db, a[1]); Kind k = db::kind_of(v);
    if (k == Kind::None) { r.array(2); r.bulk("0"); r.array(0); return; }
    if (k != kind) { err_wrongtype(r); return; }
    auto es = db::elems(v, c.db, a[1]);
    // small containers come back whole, as Redis does for its packed encodings; large ones `count` at a time
    bool whole = es.size() <= 512;
    std::vector<const Cell*> out; size_t i = whole ? 0 : cursor, seen = 0;
    for (; i < es.size() && (whole || seen < (size_t)count); i++, seen++) {
        if (use_pat && !stringmatch(pattern, es[i].instance.substr(1))) continue;
        out.push_back(&es[i]);
    }
    unsigned long next = i >= es.size() ? 0 : i;
    r.array(2); r.bulk(std::to_string(next));
    r.array(out.size() * (with_values ? 2 : 1));
    for (auto* e : out) { r.bulk(e->instance.data() + 1, e->instance.size() - 1); if (with_values) r.bulk(db::text(e->node)); }
}
static void cmd_hscan(Client& c, const Argv& a, Reply& r) { elem_scan(c, a, r, Kind::HashField, true); }

void register_hash_commands() {
    register_cmd("hset", cmd_hset); register_cmd("hmset", cmd_hset); register_cmd("hsetnx", cmd_hsetnx);
    register_cmd("hget", cmd_hget); register_cmd("hmget", cmd_hmget); register_cmd("hdel", cmd_hdel); register_cmd("hlen", cmd_hlen);
    register_cmd("hstrlen", cmd_hstrlen); register_cmd("hexists", cmd_hexists); register_cmd("hgetall", cmd_hgetall);
    register_cmd("hkeys", cmd_hkeys); register_cmd("hvals", cmd_hvals); register_cmd("hincrby", cmd_hincrby); register_cmd("hincrbyfloat", cmd_hincrbyfloat);
    register_cmd("hrandfield", cmd_hrandfield); register_cmd("hscan", cmd_hscan);
}

}  // namespace rr
