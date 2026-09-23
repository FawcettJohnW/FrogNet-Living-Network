// Sets. A member is a cell (instance ELEM+member, kind SetMember); the set is the partial index.
// SISMEMBER is one lookup, SMEMBERS one range read, SCARD a count, SADD one publish.
// SINTER/SUNION/SDIFF read every key from ONE region snapshot: the N reads are one instant.
#include "server.hpp"
#include <random>
#include <climits>
#include <cerrno>
#include <set>
#include <algorithm>

std::vector<size_t> random_picks(size_t size, long long count);
void elem_scan(Client& c, const Argv& a, Reply& r, Kind kind, bool with_values);
static thread_local std::mt19937_64 rng{std::random_device{}()};

static int set_var(Client& c, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::SetMember) { err_wrongtype(r); return -1; }
    return 1;
}
static int set_var(Client& c, const Memory::Snapshot& snap, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(snap, c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::SetMember) { err_wrongtype(r); return -1; }
    return 1;
}
static bool has(const VarPtr& v, const std::string& m) { return Memory::inst(v, db::ELEM + m) != nullptr; }
static std::vector<std::string> members(const VarPtr& v) {
    std::vector<std::string> out;
    if (!v) return out;
    v->range(db::ELEM, "", true, [&](const std::string& i, const CellPtr&) { out.push_back(i.substr(1)); return true; });
    return out;
}
static std::optional<Ent> nth_member(const VarPtr& v, size_t i) {
    size_t skip = v->rank(db::ELEM);
    return v->nth(skip + i);
}
static void reply_members(Reply& r, const std::vector<std::string>& ms) { r.set(ms.size()); for (auto& m : ms) r.bulk(m); }

static void cmd_sadd(Client& c, const Argv& a, Reply& r) {
    VarPtr v; if (set_var(c, a[1], v, r) < 0) return;
    if (a.size() == 3) { r.integer((long long)db::put_elem(c.db, a[1], Kind::SetMember, a[2], "")); return; }
    std::vector<std::pair<std::string, std::string>> nv;
    for (size_t i = 2; i < a.size(); i++) nv.emplace_back(a[i], "");
    std::sort(nv.begin(), nv.end()); nv.erase(std::unique(nv.begin(), nv.end()), nv.end());
    r.integer((long long)db::put_elems(c.db, a[1], Kind::SetMember, nv));
}
static void cmd_srem(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::string> names(a.begin() + 2, a.end());
    std::sort(names.begin(), names.end()); names.erase(std::unique(names.begin(), names.end()), names.end());
    r.integer((long long)db::drop_elems(c.db, a[1], names));
}
static void cmd_sismember(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    r.integer(st && has(v, a[2]) ? 1 : 0);
}
static void cmd_smismember(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) r.integer(st && has(v, a[i]) ? 1 : 0);
}
static void cmd_smembers(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    reply_members(r, st ? members(v) : std::vector<std::string>{});
}
static void cmd_scard(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    r.integer(st ? (long long)db::elem_count(v) : 0);
}
static void cmd_spop(Client& c, const Argv& a, Reply& r) {
    if (a.size() > 3) { err_syntax(r); return; }
    long long count = 1; bool with_count = a.size() == 3;
    if (with_count) { if (!string2ll(a[2], count)) { err_notint(r); return; } if (count < 0) { r.error("ERR value is out of range, must be positive"); return; } }
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    size_t size = st ? db::elem_count(v) : 0;
    if (!with_count) {
        if (!size) { r.null(); return; }
        auto x = nth_member(v, rng() % size); std::string m = x->key.substr(1);
        db::drop_elems(c.db, a[1], {m}); r.bulk(m); return;
    }
    if (!size || count == 0) { r.set(0); return; }
    auto picks = random_picks(size, count);
    std::vector<std::string> ms; for (size_t i : picks) ms.push_back(nth_member(v, i)->key.substr(1));
    db::drop_elems(c.db, a[1], ms);
    reply_members(r, ms);
}
static void cmd_srandmember(Client& c, const Argv& a, Reply& r) {
    if (a.size() > 3) { err_syntax(r); return; }
    long long count = 0; bool with_count = a.size() == 3;
    if (with_count) { if (!string2ll(a[2], count)) { err_notint(r); return; } if (count == LLONG_MIN) { r.error("ERR value is out of range"); return; } }
    VarPtr v; int st = set_var(c, a[1], v, r); if (st < 0) return;
    size_t size = st ? db::elem_count(v) : 0;
    if (!with_count) { if (!size) { r.null(); return; } r.bulk(nth_member(v, rng() % size)->key.substr(1)); return; }
    if (!size || count == 0) { r.array(0); return; }
    auto picks = random_picks(size, count);
    r.array(picks.size()); for (size_t i : picks) r.bulk(nth_member(v, i)->key.substr(1));
}
static void cmd_smove(Client& c, const Argv& a, Reply& r) {
    VarPtr src, dst;
    int ss = set_var(c, a[1], src, r); if (ss < 0) return;
    int ds = set_var(c, a[2], dst, r); if (ds < 0) return;
    if (!ss || !has(src, a[3])) { r.integer(0); return; }
    if (a[1] == a[2]) { r.integer(1); return; }
    // two cells in two keys: remove then add, two steps (question-3 candidate: cross-key atomic move)
    db::drop_elems(c.db, a[1], {a[3]});
    if (!has(dst, a[3])) db::put_elems(c.db, a[2], Kind::SetMember, {{a[3], ""}});   // already there: nothing written, nothing touched
    r.integer(1);
}

// ---- algebra from one snapshot
enum { OP_INTER, OP_UNION, OP_DIFF };
static bool algebra(Client& c, const Argv& a, size_t from, size_t nkeys, int op, std::vector<std::string>& out, Reply& r, long long limit = 0) {
    Memory::Snapshot snap = g.mem.snapshot(db::svc(c.db));
    std::vector<VarPtr> vs;
    for (size_t i = from; i < from + nkeys; i++) { VarPtr v; if (set_var(c, snap, a[i], v, r) < 0) return false; vs.push_back(v); }
    if (op == OP_INTER) {
        for (auto& v : vs) if (!v || !db::elem_count(v)) return true;    // an empty set intersects to nothing
        size_t small = 0; for (size_t i = 1; i < vs.size(); i++) if (db::elem_count(vs[i]) < db::elem_count(vs[small])) small = i;
        for (auto& m : members(vs[small])) {
            bool all = true; for (size_t i = 0; i < vs.size() && all; i++) if (i != small && !has(vs[i], m)) all = false;
            if (all) { out.push_back(m); if (limit && (long long)out.size() >= limit) break; }
        }
    } else if (op == OP_UNION) {
        std::set<std::string> u; for (auto& v : vs) for (auto& m : members(v)) u.insert(m);
        out.assign(u.begin(), u.end());
    } else {
        for (auto& m : members(vs[0])) { bool in = false; for (size_t i = 1; i < vs.size() && !in; i++) if (has(vs[i], m)) in = true; if (!in) out.push_back(m); }
    }
    return true;
}
static void algebra_cmd(Client& c, const Argv& a, Reply& r, int op, bool store) {
    std::vector<std::string> out;
    if (!algebra(c, a, store ? 2 : 1, a.size() - (store ? 2 : 1), op, out, r)) return;
    if (!store) { reply_members(r, out); return; }
    // STORE: the destination's old elements go and the result is published (two steps)
    VarPtr dst = db::live_var(c.db, a[1]);
    if (out.empty()) { if (db::kind_of(dst) != Kind::None) db::del(c.db, a[1]); r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> nv; for (auto& m : out) nv.emplace_back(m, "");
    db::store_elems(c.db, a[1], Kind::SetMember, nv);
    r.integer((long long)out.size());
}
static void cmd_sinter(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_INTER, false); }
static void cmd_sinterstore(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_INTER, true); }
static void cmd_sunion(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_UNION, false); }
static void cmd_sunionstore(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_UNION, true); }
static void cmd_sdiff(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_DIFF, false); }
static void cmd_sdiffstore(Client& c, const Argv& a, Reply& r) { algebra_cmd(c, a, r, OP_DIFF, true); }
static void cmd_sintercard(Client& c, const Argv& a, Reply& r) {
    long long numkeys, limit = 0;
    if (!string2ll(a[1], numkeys) || numkeys < 1) { r.error("ERR numkeys should be greater than 0"); return; }
    if (numkeys > (long long)a.size() - 2) { r.error("ERR Number of keys can't be greater than number of args"); return; }
    for (size_t j = 2 + (size_t)numkeys; j < a.size(); j++) {
        bool more = j + 1 < a.size();
        if (str_eq_ci(a[j], "limit") && more) { j++; if (!string2ll(a[j], limit) || limit < 0) { r.error("ERR LIMIT can't be negative"); return; } }
        else { err_syntax(r); return; }
    }
    std::vector<std::string> out;
    if (!algebra(c, a, 2, (size_t)numkeys, OP_INTER, out, r, limit)) return;
    r.integer((long long)out.size());
}
static void cmd_sscan(Client& c, const Argv& a, Reply& r) { elem_scan(c, a, r, Kind::SetMember, false); }

void register_set_commands() {
    register_cmd("sadd", cmd_sadd); register_cmd("srem", cmd_srem); register_cmd("sismember", cmd_sismember); register_cmd("smismember", cmd_smismember);
    register_cmd("smembers", cmd_smembers); register_cmd("scard", cmd_scard); register_cmd("spop", cmd_spop); register_cmd("srandmember", cmd_srandmember);
    register_cmd("smove", cmd_smove); register_cmd("sinter", cmd_sinter); register_cmd("sinterstore", cmd_sinterstore); register_cmd("sunion", cmd_sunion);
    register_cmd("sunionstore", cmd_sunionstore); register_cmd("sdiff", cmd_sdiff); register_cmd("sdiffstore", cmd_sdiffstore); register_cmd("sintercard", cmd_sintercard);
    register_cmd("sscan", cmd_sscan);
}
