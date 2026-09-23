// Sorted sets. One cell per member (instance ELEM + member) whose bag carries the score, flagged
// indexed: the memory keeps the variable's numeric index (score, then member) itself. Rank is rank
// in that index, range-by-score is a range on it, pops read its ends. Nothing is maintained here.
#include "server.hpp"
#include "blocking.hpp"
#include <cmath>
#include <cstring>
#include <climits>
#include <algorithm>
#include <map>
#include <random>


// order-preserving key for a double: sorts as the double sorts, -0 and +0 the same
static int64_t score_key(double d) {
    if (d == 0) d = 0.0;
    uint64_t u; memcpy(&u, &d, 8);
    if (u >> 63) u = ~u; else u |= 1ULL << 63;
    return (int64_t)(u ^ (1ULL << 63));
}
static double score_of(const CellPtr& c) { return c->bag->d; }
static std::string member_of(const std::string& inst) { return inst.substr(1); }

static int zvar(Client& c, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::ZMember) { err_wrongtype(r); return -1; }
    return 1;
}
static size_t zcard(const VarPtr& v) { return v ? v->n_size() : 0; }
static bool zscore(const VarPtr& v, const std::string& m, double& d) {
    CellPtr cp = Memory::inst(v, db::ELEM + m);
    if (!cp) return false;
    d = score_of(cp); return true;
}
static BagPtr zbag(double d) { Bag b; b.kind = Kind::ZMember; b.d = d; b.n = score_key(d); b.indexed = true; return std::make_shared<const Bag>(std::move(b)); }
static void reply_score(Reply& r, double d) { r.double_(d); }
// parse "1.5", "(1.5", "-inf", "+inf"
static bool parse_range_item(const std::string& s, double& v, bool& ex) {
    ex = false; std::string t = s;
    if (!t.empty() && t[0] == '(') { ex = true; t = t.substr(1); }
    if (t.empty()) return false;
    char* e = nullptr; v = strtod(t.c_str(), &e);
    return *e == '\0' && !std::isnan(v);
}
struct LexItem { std::string s; bool ex = false; bool inf = false; bool neg = false; };
static bool parse_lex_item(const std::string& s, LexItem& out) {
    if (s.empty()) return false;
    switch (s[0]) { case '+': if (s.size() != 1) return false; out.inf = true; return true;
                    case '-': if (s.size() != 1) return false; out.inf = true; out.neg = true; return true;
                    case '(': out.ex = true; out.s = s.substr(1); return true;
                    case '[': out.s = s.substr(1); return true;
                    default: return false; }
}
static bool lex_ge(const std::string& m, const LexItem& lo) { if (lo.inf) return lo.neg; return lo.ex ? m > lo.s : m >= lo.s; }
static bool lex_le(const std::string& m, const LexItem& hi) { if (hi.inf) return !hi.neg; return hi.ex ? m < hi.s : m <= hi.s; }

// the members in index order (score, member); each as (member, score)
static std::vector<std::pair<std::string, double>> all_ordered(const VarPtr& v) {
    std::vector<std::pair<std::string, double>> out;
    if (!v) return out;
    v->n_each([&](const NumInst& k, const CellPtr& c) { out.emplace_back(member_of(k.instance), score_of(c)); return true; });
    return out;
}
// members with lo <= score <= hi (exclusive per flags), in index order; reverse walks from the top
static std::vector<std::pair<std::string, double>> by_score(const VarPtr& v, double lo, bool loex, double hi, bool hiex, bool reverse, long long offset, long long limit) {
    std::vector<std::pair<std::string, double>> out;
    if (!v || lo > hi) return out;
    NumInst a{score_key(lo), ""}, b{score_key(hi), std::string(1, '\xff') + std::string(255, '\xff')};
    auto take = [&](const NumInst& k, const CellPtr& c) {
        double d = score_of(c);
        if ((loex && d <= lo) || (hiex && d >= hi) || d < lo || d > hi) return true;
        if (offset > 0) { offset--; return true; }
        out.emplace_back(member_of(k.instance), d);
        return !(limit >= 0 && (long long)out.size() >= limit);
    };
    if (!reverse) v->n_range(a, b, take); else v->n_rrange(a, b, take);
    return out;
}
static std::vector<std::pair<std::string, double>> by_lex(const VarPtr& v, const LexItem& lo, const LexItem& hi, bool reverse, long long offset, long long limit) {
    std::vector<std::pair<std::string, double>> out;
    if (!v) return out;
    auto take = [&](const NumInst& k, const CellPtr& c) {
        std::string m = member_of(k.instance);
        if (!lex_ge(m, lo) || !lex_le(m, hi)) return true;
        if (offset > 0) { offset--; return true; }
        out.emplace_back(m, score_of(c));
        return !(limit >= 0 && (long long)out.size() >= limit);
    };
    if (!reverse) v->n_each(take);
    else { std::vector<std::pair<NumInst, CellPtr>> all; v->n_each([&](const NumInst& k, const CellPtr& c) { all.emplace_back(k, c); return true; }); for (auto it = all.rbegin(); it != all.rend(); ++it) if (!take(it->first, it->second)) break; }
    return out;
}
static std::vector<std::pair<std::string, double>> by_rank(const VarPtr& v, long long start, long long stop, bool reverse) {
    std::vector<std::pair<std::string, double>> out;
    long long n = (long long)zcard(v);
    if (start < 0) start += n;
    if (stop < 0) stop += n;
    if (start < 0) start = 0;
    if (start > stop || start >= n) return out;
    if (stop >= n) stop = n - 1;
    for (long long i = start; i <= stop; i++) {
        auto x = v->n_nth((size_t)(reverse ? n - 1 - i : i));
        if (!x) break;
        out.emplace_back(member_of(x->key.instance), x->val->bag->d);
    }
    return out;
}
static void reply_pairs(Reply& r, const std::vector<std::pair<std::string, double>>& ps, bool withscores) {
    if (withscores && r.proto == 3) { r.array(ps.size()); for (auto& p : ps) { r.array(2); r.bulk(p.first); r.double_(p.second); } return; }
    r.array(withscores ? ps.size() * 2 : ps.size());
    for (auto& p : ps) { r.bulk(p.first); if (withscores) reply_score(r, p.second); }
}

// write members: add/update in one publish. Returns (added, changed)
static std::pair<long long, long long> zput(int dbi, const std::string& key, const VarPtr& v, const std::vector<std::pair<std::string, double>>& ms) {
    std::vector<Memory::W> ws; long long added = 0, changed = 0;
    for (auto& m : ms) {
        double old; bool had = zscore(v, m.first, old);
        if (had && old == m.second) continue;
        if (!had) added++; else changed++;
        ws.push_back(Memory::W{key, db::ELEM + m.first, zbag(m.second)});
    }
    if (!ws.empty()) { if (v && Memory::inst(v, db::VAL)) g.mem.remove(db::svc(dbi), key, db::VAL); g.mem.write_many(db::svc(dbi), ws); g.stats.dirty++; }
    return {added, changed};
}
static long long zdrop(int dbi, const std::string& key, const std::vector<std::string>& ms) {
    std::vector<std::string> names; for (auto& m : ms) names.push_back(m);
    return (long long)db::drop_elems(dbi, key, names);
}

// ---------------------------------------------------------------- ZADD / ZINCRBY
static void cmd_zadd(Client& c, const Argv& a, Reply& r) {
    bool nx = false, xx = false, gt = false, lt = false, ch = false, incr = false;
    size_t j = 2;
    for (; j < a.size(); j++) {
        std::string o = lower(a[j]);
        if (o == "nx") nx = true; else if (o == "xx") xx = true; else if (o == "gt") gt = true; else if (o == "lt") lt = true;
        else if (o == "ch") ch = true; else if (o == "incr") incr = true; else break;
    }
    size_t elements = a.size() - j;
    if (elements == 0 || elements % 2) { err_syntax(r); return; }
    if (nx && xx) { r.error("ERR XX and NX options at the same time are not compatible"); return; }
    if ((gt && nx) || (lt && nx) || (gt && lt)) { r.error("ERR GT, LT, and/or NX options at the same time are not compatible"); return; }
    if (incr && elements > 2) { r.error("ERR INCR option supports a single increment-element pair"); return; }
    std::vector<std::pair<std::string, double>> in;
    for (size_t i = j; i < a.size(); i += 2) { long double d; if (!string2ld(a[i], d)) { err_notfloat(r); return; } in.emplace_back(a[i+1], (double)d); }
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    std::vector<std::pair<std::string, double>> put; long long processed = 0, added = 0, updated = 0; double incr_result = 0; bool incr_nil = false;
    for (auto& m : in) {
        double old; bool had = zscore(v, m.first, old);
        double ns = m.second;
        if (incr) { if (had) ns = old + m.second; if (std::isnan(ns)) { r.error("ERR resulting score is not a number (NaN)"); return; } }
        if (had && nx) { if (incr) incr_nil = true; continue; }
        if (!had && xx) { if (incr) incr_nil = true; continue; }
        if (had && ((gt && ns <= old) || (lt && ns >= old))) { if (incr) incr_nil = true; continue; }
        processed++;
        if (!had) added++; else if (ns != old) updated++;
        if (!had || ns != old) put.emplace_back(m.first, ns);
        incr_result = ns;
    }
    zput(c.db, a[1], v, put);
    if (incr) { if (incr_nil) r.null(); else reply_score(r, incr_result); return; }
    r.integer(ch ? added + updated : added);
}
static void cmd_zincrby(Client& c, const Argv& a, Reply& r) {
    Argv b{"zadd", a[1], "incr", a[2], a[3]}; cmd_zadd(c, b, r);
}
static void cmd_zcard(Client& c, const Argv& a, Reply& r) { VarPtr v; if (zvar(c, a[1], v, r) < 0) return; r.integer((long long)zcard(v)); }
static void cmd_zscore(Client& c, const Argv& a, Reply& r) {
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    double d; if (zscore(v, a[2], d)) reply_score(r, d); else r.null();
}
static void cmd_zmscore(Client& c, const Argv& a, Reply& r) {
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) { double d; if (zscore(v, a[i], d)) reply_score(r, d); else r.null(); }
}
static void cmd_zrem(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = zvar(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::string> ms(a.begin() + 2, a.end());
    r.integer(zdrop(c.db, a[1], ms));
}
static void cmd_zrank_generic(Client& c, const Argv& a, Reply& r, bool reverse) {
    bool withscore = false;
    if (a.size() == 4) { if (!str_eq_ci(a[3], "withscore")) { err_syntax(r); return; } withscore = true; }
    else if (a.size() > 4) { err_syntax(r); return; }
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    double d; if (!zscore(v, a[2], d)) { if (withscore) r.null_array(); else r.null(); return; }
    size_t rk = v->n_rank(NumInst{score_key(d), db::ELEM + a[2]});
    if (reverse) rk = zcard(v) - 1 - rk;
    if (withscore) { r.array(2); r.integer((long long)rk); reply_score(r, d); } else r.integer((long long)rk);
}
static void cmd_zrank(Client& c, const Argv& a, Reply& r) { cmd_zrank_generic(c, a, r, false); }
static void cmd_zrevrank(Client& c, const Argv& a, Reply& r) { cmd_zrank_generic(c, a, r, true); }

// ---------------------------------------------------------------- ranges
enum { BY_RANK, BY_SCORE, BY_LEX };
static void zrange_generic(Client& c, const Argv& a, Reply& r, int by, bool reverse, bool withscores, bool limit_ok, size_t first_opt, const std::string* store) {
    bool lim = false; long long offset = 0, count = -1;
    for (size_t j = first_opt; j < a.size(); j++) {
        std::string o = lower(a[j]); size_t left = a.size() - j - 1;
        if (o == "withscores" && !store) withscores = true;
        else if (o == "limit" && left >= 2) { if (!string2ll(a[j+1], offset) || !string2ll(a[j+2], count)) { err_notint(r); return; } lim = true; j += 2; }
        else if (o == "rev" && limit_ok) reverse = true;
        else if (o == "byscore" && limit_ok) by = BY_SCORE;
        else if (o == "bylex" && limit_ok) by = BY_LEX;
        else { err_syntax(r); return; }
    }
    if (lim && by == BY_RANK) { r.error("ERR syntax error, LIMIT is only supported in combination with either BYSCORE or BYLEX"); return; }
    if (withscores && by == BY_LEX) { r.error("ERR syntax error, WITHSCORES not supported in combination with BYLEX"); return; }
    if (store) offset = offset;   // ZRANGESTORE takes the same options
    const std::string& src = store ? a[2] : a[1];
    const std::string& s1 = store ? a[3] : a[2];
    const std::string& s2 = store ? a[4] : a[3];
    VarPtr v; int st = zvar(c, src, v, r); if (st < 0) return;
    std::vector<std::pair<std::string, double>> out;
    if (by == BY_RANK) {
        long long start, stop; if (!string2ll(s1, start) || !string2ll(s2, stop)) { err_notint(r); return; }
        out = by_rank(v, start, stop, reverse);
    } else if (by == BY_SCORE) {
        double lo, hi; bool loex, hiex;
        // in REV mode the arguments are (max, min)
        if (!parse_range_item(reverse ? s2 : s1, lo, loex) || !parse_range_item(reverse ? s1 : s2, hi, hiex)) { r.error("ERR min or max is not a float"); return; }
        if (!(lim && (offset < 0 || count == 0))) out = by_score(v, lo, loex, hi, hiex, reverse, offset, lim && count >= 0 ? count : -1);
    } else {
        LexItem lo, hi;
        if (!parse_lex_item(reverse ? s2 : s1, lo) || !parse_lex_item(reverse ? s1 : s2, hi)) { r.error("ERR min or max not valid string range item"); return; }
        if (!(lim && (offset < 0 || count == 0))) out = by_lex(v, lo, hi, reverse, offset, lim && count >= 0 ? count : -1);
    }
    if (store) {
        VarPtr dv; if (zvar(c, *store, dv, r) < 0) return;
        std::vector<std::pair<std::string, std::string>> nv;
        g.mem.remove_variable(db::svc(c.db), *store);
        if (!out.empty()) { std::vector<Memory::W> ws; for (auto& p : out) ws.push_back(Memory::W{*store, db::ELEM + p.first, zbag(p.second)}); g.mem.write_many(db::svc(c.db), ws); }
        g.stats.dirty++;
        r.integer((long long)out.size()); return;
    }
    reply_pairs(r, out, withscores);
}
static void cmd_zrange(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_RANK, false, false, true, 4, nullptr); }
static void cmd_zrevrange(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_RANK, true, false, false, 4, nullptr); }
static void cmd_zrangebyscore(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_SCORE, false, false, false, 4, nullptr); }
static void cmd_zrevrangebyscore(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_SCORE, true, false, false, 4, nullptr); }
static void cmd_zrangebylex(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_LEX, false, false, false, 4, nullptr); }
static void cmd_zrevrangebylex(Client& c, const Argv& a, Reply& r) { zrange_generic(c, a, r, BY_LEX, true, false, false, 4, nullptr); }
static void cmd_zrangestore(Client& c, const Argv& a, Reply& r) { std::string dst = a[1]; zrange_generic(c, a, r, BY_RANK, false, false, true, 5, &dst); }

static void cmd_zcount(Client& c, const Argv& a, Reply& r) {
    double lo, hi; bool loex, hiex;
    if (!parse_range_item(a[2], lo, loex) || !parse_range_item(a[3], hi, hiex)) { r.error("ERR min or max is not a float"); return; }
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    r.integer((long long)by_score(v, lo, loex, hi, hiex, false, 0, -1).size());
}
static void cmd_zlexcount(Client& c, const Argv& a, Reply& r) {
    LexItem lo, hi;
    if (!parse_lex_item(a[2], lo) || !parse_lex_item(a[3], hi)) { r.error("ERR min or max not valid string range item"); return; }
    VarPtr v; if (zvar(c, a[1], v, r) < 0) return;
    r.integer((long long)by_lex(v, lo, hi, false, 0, -1).size());
}
static void zremrange_generic(Client& c, const Argv& a, Reply& r, int by) {
    std::vector<std::pair<std::string, double>> out;
    long long start = 0, stop = 0; double lo = 0, hi = 0; bool loex = false, hiex = false; LexItem llo, lhi;
    if (by == BY_RANK) { if (!string2ll(a[2], start) || !string2ll(a[3], stop)) { err_notint(r); return; } }
    else if (by == BY_SCORE) { if (!parse_range_item(a[2], lo, loex) || !parse_range_item(a[3], hi, hiex)) { r.error("ERR min or max is not a float"); return; } }
    else { if (!parse_lex_item(a[2], llo) || !parse_lex_item(a[3], lhi)) { r.error("ERR min or max not valid string range item"); return; } }
    VarPtr v; int st = zvar(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    if (by == BY_RANK) out = by_rank(v, start, stop, false);
    else if (by == BY_SCORE) out = by_score(v, lo, loex, hi, hiex, false, 0, -1);
    else out = by_lex(v, llo, lhi, false, 0, -1);
    std::vector<std::string> ms; for (auto& p : out) ms.push_back(p.first);
    r.integer(zdrop(c.db, a[1], ms));
}
static void cmd_zremrangebyrank(Client& c, const Argv& a, Reply& r) { zremrange_generic(c, a, r, BY_RANK); }
static void cmd_zremrangebyscore(Client& c, const Argv& a, Reply& r) { zremrange_generic(c, a, r, BY_SCORE); }
static void cmd_zremrangebylex(Client& c, const Argv& a, Reply& r) { zremrange_generic(c, a, r, BY_LEX); }

// ---------------------------------------------------------------- algebra from one snapshot (reads N, writes one)
enum { Z_UNION, Z_INTER, Z_DIFF };
static void zalgebra(Client& c, const Argv& a, Reply& r, int op, bool store, bool card_only) {
    size_t ki = store ? 2 : 1; std::string dst = store ? a[1] : "";
    long long numkeys; if (!string2ll(a[ki], numkeys)) { err_notint(r); return; }
    if (numkeys < 1) { r.error("ERR at least 1 input key is needed for '" + lower(a[0]) + "' command"); return; }
    if (numkeys > (long long)(a.size() - ki - 1)) { err_syntax(r); return; }
    std::vector<std::string> keys(a.begin() + ki + 1, a.begin() + ki + 1 + numkeys);
    std::vector<double> weights(numkeys, 1.0); int aggregate = 0; bool withscores = false; long long limit = 0;
    for (size_t j = ki + 1 + numkeys; j < a.size(); j++) {
        std::string o = lower(a[j]); size_t left = a.size() - j - 1;
        if (o == "weights" && op != Z_DIFF && left >= (size_t)numkeys) { for (long long k = 0; k < numkeys; k++) { long double d; if (!string2ld(a[j+1+k], d)) { r.error("ERR weight value is not a float"); return; } weights[k] = (double)d; } j += numkeys; }
        else if (o == "aggregate" && op != Z_DIFF && left >= 1) { std::string ag = lower(a[j+1]); if (ag == "sum") aggregate = 0; else if (ag == "min") aggregate = 1; else if (ag == "max") aggregate = 2; else { err_syntax(r); return; } j++; }
        else if (o == "withscores" && !store && !card_only) withscores = true;
        else if (o == "limit" && card_only && left >= 1) { if (!string2ll(a[j+1], limit) || limit < 0) { r.error("ERR LIMIT can't be negative"); return; } j++; }
        else { err_syntax(r); return; }
    }
    Memory::Snapshot snap = g.mem.snapshot(db::svc(c.db));
    std::vector<std::map<std::string, double>> sets;
    for (auto& k : keys) {
        VarPtr v = db::live_var(snap, c.db, k); Kind kd = db::kind_of(v);
        std::map<std::string, double> s;
        if (kd == Kind::ZMember) for (auto& p : all_ordered(v)) s[p.first] = p.second;
        else if (kd == Kind::SetMember) for (auto& e : db::elems(v, c.db, k)) s[e.instance.substr(1)] = 1.0;
        else if (kd != Kind::None) { err_wrongtype(r); return; }
        sets.push_back(std::move(s));
    }
    auto agg = [&](double acc, double x) { if (aggregate == 1) return std::min(acc, x); if (aggregate == 2) return std::max(acc, x); double s = acc + x; if (std::isnan(s)) s = 0; return s; };
    std::map<std::string, double> res;
    if (op == Z_UNION) {
        for (size_t i = 0; i < sets.size(); i++) for (auto& p : sets[i]) { double sc = p.second * weights[i]; if (std::isnan(sc)) sc = 0; auto it = res.find(p.first); if (it == res.end()) res[p.first] = sc; else it->second = agg(it->second, sc); }
    } else if (op == Z_INTER) {
        for (auto& p : sets[0]) {
            double sc = p.second * weights[0]; if (std::isnan(sc)) sc = 0; bool all = true;
            for (size_t i = 1; i < sets.size(); i++) { auto it = sets[i].find(p.first); if (it == sets[i].end()) { all = false; break; } double x = it->second * weights[i]; if (std::isnan(x)) x = 0; sc = agg(sc, x); }
            if (all) { res[p.first] = sc; if (card_only && limit && (long long)res.size() >= limit) break; }
        }
    } else {
        for (auto& p : sets[0]) { bool in = false; for (size_t i = 1; i < sets.size(); i++) if (sets[i].count(p.first)) { in = true; break; } if (!in) res[p.first] = p.second; }
    }
    if (card_only) { r.integer((long long)res.size()); return; }
    std::vector<std::pair<std::string, double>> out(res.begin(), res.end());
    std::stable_sort(out.begin(), out.end(), [](auto& x, auto& y) { return x.second < y.second || (x.second == y.second && x.first < y.first); });
    if (store) {
        VarPtr dv; if (zvar(c, dst, dv, r) < 0) return;
        g.mem.remove_variable(db::svc(c.db), dst);
        if (!out.empty()) { std::vector<Memory::W> ws; for (auto& p : out) ws.push_back(Memory::W{dst, db::ELEM + p.first, zbag(p.second)}); g.mem.write_many(db::svc(c.db), ws); }
        g.stats.dirty++;
        r.integer((long long)out.size()); return;
    }
    reply_pairs(r, out, withscores);
}
static void cmd_zunion(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_UNION, false, false); }
static void cmd_zinter(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_INTER, false, false); }
static void cmd_zdiff(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_DIFF, false, false); }
static void cmd_zunionstore(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_UNION, true, false); }
static void cmd_zinterstore(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_INTER, true, false); }
static void cmd_zdiffstore(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_DIFF, true, false); }
static void cmd_zintercard(Client& c, const Argv& a, Reply& r) { zalgebra(c, a, r, Z_INTER, false, true); }

// ---------------------------------------------------------------- pops
// read an end of the index, remove(); 0 means someone else got it, read the new end
static std::vector<std::pair<std::string, double>> zpop_end(int dbi, const std::string& key, bool min, long long count) {
    std::vector<std::pair<std::string, double>> out;
    while ((long long)out.size() < count) {
        VarPtr v = db::live_var(dbi, key);
        if (db::kind_of(v) != Kind::ZMember) break;
        long long n = (long long)zcard(v); if (!n) break;
        auto x = v->n_nth((size_t)(min ? 0 : n - 1));
        if (!x) break;
        std::string m = member_of(x->key.instance); double d = score_of(x->val);
        if (!db::drop_elems(dbi, key, {m})) continue;
        out.emplace_back(m, d);
    }
    return out;
}
static void zpop_generic(Client& c, const Argv& a, Reply& r, bool min) {
    long long count = 1; bool has_count = a.size() == 3;
    if (a.size() > 3) { err_syntax(r); return; }
    if (has_count) { if (!string2ll(a[2], count)) { r.error("ERR value is out of range, must be positive"); return; } if (count < 0) { r.error("ERR value is out of range, must be positive"); return; } }
    VarPtr v; int st = zvar(c, a[1], v, r); if (st < 0) return;
    auto got = zpop_end(c.db, a[1], min, count);
    if (!has_count) { if (got.empty()) r.array(0); else { r.array(2); r.bulk(got[0].first); reply_score(r, got[0].second); } return; }
    reply_pairs(r, got, true);
}
static void cmd_zpopmin(Client& c, const Argv& a, Reply& r) { zpop_generic(c, a, r, true); }
static void cmd_zpopmax(Client& c, const Argv& a, Reply& r) { zpop_generic(c, a, r, false); }
static bool parse_mpop(const Argv& a, size_t from, std::vector<std::string>& keys, bool& min, long long& count, Reply& r) {
    long long numkeys; if (!string2ll(a[from], numkeys) || numkeys <= 0) { r.error("ERR numkeys should be greater than 0"); return false; }
    if ((long long)a.size() < (long long)from + 1 + numkeys + 1) { err_syntax(r); return false; }
    keys.assign(a.begin() + from + 1, a.begin() + from + 1 + numkeys);
    size_t j = from + 1 + numkeys; std::string w = lower(a[j]);
    if (w == "min") min = true; else if (w == "max") min = false; else { err_syntax(r); return false; }
    count = 1; bool seen = false;
    for (j++; j < a.size(); j++) { if (str_eq_ci(a[j], "count") && j + 1 < a.size() && !seen) { seen = true; if (!string2ll(a[++j], count) || count <= 0) { r.error("ERR count should be greater than 0"); return false; } } else { err_syntax(r); return false; } }
    return true;
}
static void cmd_zmpop(Client& c, const Argv& a, Reply& r) {
    std::vector<std::string> keys; bool min; long long count;
    if (!parse_mpop(a, 1, keys, min, count, r)) return;
    for (auto& k : keys) {
        VarPtr v; int st = zvar(c, k, v, r); if (st < 0) return;
        if (!st) continue;
        auto got = zpop_end(c.db, k, min, count);
        if (got.empty()) continue;
        r.array(2); r.bulk(k); r.array(got.size()); for (auto& p : got) { r.array(2); r.bulk(p.first); reply_score(r, p.second); }
        return;
    }
    r.null_array();
}
static bool zready(Client& c, const std::string& k, bool first, VarPtr& v, Reply& r, bool& err) {
    err = false;
    if (first) { int st = zvar(c, k, v, r); if (st < 0) { err = true; return false; } return st == 1; }
    v = db::live_var(c.db, k); return db::kind_of(v) == Kind::ZMember;
}
static void bzpop_generic(Client& c, const Argv& a, Reply& r, bool min) {
    int64_t timeout; if (!parse_timeout(a.back(), timeout, r)) return;
    std::vector<std::string> keys(a.begin() + 1, a.end() - 1);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w;
    Claim claim(c, keys); bool first = true;
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        for (auto& k : keys) {
            VarPtr v; bool err; if (!zready(c, k, first, v, r, err)) { if (err) return; continue; }
            if (!claim.first_for(k)) continue;
            auto got = zpop_end(c.db, k, min, 1);
            if (!got.empty()) { r.array(3); r.bulk(k); r.bulk(got[0].first); reply_score(r, got[0].second); return; }
        }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}
static void cmd_bzpopmin(Client& c, const Argv& a, Reply& r) { bzpop_generic(c, a, r, true); }
static void cmd_bzpopmax(Client& c, const Argv& a, Reply& r) { bzpop_generic(c, a, r, false); }
static void cmd_bzmpop(Client& c, const Argv& a, Reply& r) {
    int64_t timeout; if (!parse_timeout(a[1], timeout, r)) return;
    std::vector<std::string> keys; bool min; long long count;
    if (!parse_mpop(a, 2, keys, min, count, r)) return;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w;
    Claim claim(c, keys); bool first = true;
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        for (auto& k : keys) {
            VarPtr v; bool err; if (!zready(c, k, first, v, r, err)) { if (err) return; continue; }
            if (!claim.first_for(k)) continue;
            auto got = zpop_end(c.db, k, min, count);
            if (!got.empty()) { r.array(2); r.bulk(k); r.array(got.size()); for (auto& p : got) { r.array(2); r.bulk(p.first); reply_score(r, p.second); } return; }
        }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}

// ---------------------------------------------------------------- misc
static void cmd_zrandmember(Client& c, const Argv& a, Reply& r) {
    bool withscores = false; long long count = 1; bool has_count = a.size() >= 3;
    if (a.size() > 4) { err_syntax(r); return; }
    if (has_count) { if (!string2ll(a[2], count)) { err_notint(r); return; } if (a.size() == 4) { if (!str_eq_ci(a[3], "withscores")) { err_syntax(r); return; } withscores = true; } }
    if (count < -LLONG_MAX / 2 || count > LLONG_MAX / 2) { r.error("ERR value is out of range"); return; }
    VarPtr v; int st = zvar(c, a[1], v, r); if (st < 0) return;
    auto all = all_ordered(v);
    static thread_local std::mt19937_64 rng{std::random_device{}()};
    if (!has_count) { if (all.empty()) r.null(); else r.bulk(all[rng() % all.size()].first); return; }
    if (!st || count == 0) { r.array(0); return; }
    std::vector<std::pair<std::string, double>> out;
    if (count > 0) { std::shuffle(all.begin(), all.end(), rng); for (size_t i = 0; i < all.size() && (long long)i < count; i++) out.push_back(all[i]); }
    else { for (long long i = 0; i < -count; i++) out.push_back(all[rng() % all.size()]); }
    reply_pairs(r, out, withscores);
}
static void cmd_zscan(Client& c, const Argv& a, Reply& r) {
    errno = 0; char* ep = nullptr; unsigned long cursor = strtoul(a[2].c_str(), &ep, 10);
    if (isspace((unsigned char)a[2][0]) || *ep) { r.error("ERR invalid cursor"); return; }
    long long count = 10; std::string pat; bool use_pat = false;
    for (size_t i = 3; i < a.size(); i++) {
        size_t more = a.size() - i - 1; std::string o = lower(a[i]);
        if (o == "count" && more >= 1) { if (!string2ll(a[i+1], count) || count < 1) { err_syntax(r); return; } i++; }
        else if (o == "match" && more >= 1) { pat = a[i+1]; use_pat = pat != "*"; i++; }
        else { err_syntax(r); return; }
    }
    VarPtr v; int st = zvar(c, a[1], v, r); if (st < 0) return;
    (void)cursor; (void)count;
    std::vector<std::pair<std::string, double>> out;
    for (auto& p : all_ordered(v)) if (!use_pat || stringmatch(pat, p.first)) out.push_back(p);
    r.array(2); r.bulk("0"); r.array(out.size() * 2);
    for (auto& p : out) { r.bulk(p.first); r.bulk(Reply::fmt_double(p.second)); }
}

void register_zset_commands() {
    register_cmd("zadd", cmd_zadd); register_cmd("zincrby", cmd_zincrby); register_cmd("zcard", cmd_zcard); register_cmd("zscore", cmd_zscore); register_cmd("zmscore", cmd_zmscore);
    register_cmd("zrem", cmd_zrem); register_cmd("zrank", cmd_zrank); register_cmd("zrevrank", cmd_zrevrank);
    register_cmd("zrange", cmd_zrange); register_cmd("zrevrange", cmd_zrevrange); register_cmd("zrangebyscore", cmd_zrangebyscore); register_cmd("zrevrangebyscore", cmd_zrevrangebyscore);
    register_cmd("zrangebylex", cmd_zrangebylex); register_cmd("zrevrangebylex", cmd_zrevrangebylex); register_cmd("zrangestore", cmd_zrangestore);
    register_cmd("zcount", cmd_zcount); register_cmd("zlexcount", cmd_zlexcount);
    register_cmd("zremrangebyrank", cmd_zremrangebyrank); register_cmd("zremrangebyscore", cmd_zremrangebyscore); register_cmd("zremrangebylex", cmd_zremrangebylex);
    register_cmd("zunion", cmd_zunion); register_cmd("zinter", cmd_zinter); register_cmd("zdiff", cmd_zdiff); register_cmd("zunionstore", cmd_zunionstore); register_cmd("zinterstore", cmd_zinterstore); register_cmd("zdiffstore", cmd_zdiffstore); register_cmd("zintercard", cmd_zintercard);
    register_cmd("zpopmin", cmd_zpopmin); register_cmd("zpopmax", cmd_zpopmax); register_cmd("zmpop", cmd_zmpop); register_cmd("bzpopmin", cmd_bzpopmin); register_cmd("bzpopmax", cmd_bzpopmax); register_cmd("bzmpop", cmd_bzmpop);
    register_cmd("zrandmember", cmd_zrandmember); register_cmd("zscan", cmd_zscan);
}

// ---- shared with geo
int z_var(Client& c, const std::string& key, VarPtr& v, Reply& r) { return zvar(c, key, v, r); }
bool z_score(const VarPtr& v, const std::string& m, double& d) { return zscore(v, m, d); }
std::vector<std::pair<std::string, double>> z_all(const VarPtr& v) { return all_ordered(v); }
// members whose score lies in [lo, hi], through the numeric index (geo's cell ranges)
void z_range_scores(const VarPtr& v, double lo, double hi, const std::function<bool(const std::string&, double)>& f) {
    v->n_range(NumInst{score_key(lo), ""}, NumInst{score_key(hi), std::string(1, '\xff')}, [&](const NumInst& k, const CellPtr& c) { return f(member_of(k.instance), score_of(c)); });
}
std::pair<long long, long long> z_put(int dbi, const std::string& key, const VarPtr& v, const std::vector<std::pair<std::string, double>>& ms) { return zput(dbi, key, v, ms); }
void z_store(int dbi, const std::string& key, const std::vector<std::pair<std::string, double>>& ms) {
    g.mem.remove_variable(db::svc(dbi), key);
    if (!ms.empty()) { std::vector<Memory::W> ws; for (auto& p : ms) ws.push_back(Memory::W{key, db::ELEM + p.first, zbag(p.second)}); g.mem.write_many(db::svc(dbi), ws); }
    g.stats.dirty++;
}
