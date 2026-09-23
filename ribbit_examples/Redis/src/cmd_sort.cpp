// SORT / SORT_RO: read N cells, write one key. The reads come from one region snapshot; the STORE is a
// separate publish (question-3 candidate: snapshot consistency of the read side vs the write).
#include "server.hpp"
#include <algorithm>
#include <cmath>

struct SortItem { std::string val; double score = 0; std::string cmp; };

static bool lookup_pattern(const Memory::Snapshot& snap, Client& c, const std::string& pat, const std::string& subst, std::string& out) {
    size_t star = pat.find('*'); if (star == std::string::npos) return false;
    std::string p = pat; p.replace(star, 1, subst);
    size_t arrow = p.find("->");
    if (arrow != std::string::npos && arrow + 2 == p.size()) arrow = std::string::npos;   // a trailing "->" is part of the key name
    std::string key = arrow == std::string::npos ? p : p.substr(0, arrow);
    VarPtr v = db::live_var(snap, c.db, key);
    if (arrow == std::string::npos) {
        CellPtr cp = Memory::inst(v, db::VAL);
        if (!cp || cp->bag->kind != Kind::String) return false;
        out = cp->bag->s; return true;
    }
    if (db::kind_of(v) != Kind::HashField) return false;
    CellPtr cp = Memory::inst(v, db::ELEM + p.substr(arrow + 2));
    if (!cp) return false;
    out = cp->bag->s; return true;
}

static void sort_generic(Client& c, const Argv& a, Reply& r, bool readonly) {
    std::string by, store; std::vector<std::string> gets; bool desc = false, alpha = false, dontsort = false;
    long long limit_start = 0, limit_count = -1;
    for (size_t j = 2; j < a.size(); j++) {
        size_t left = a.size() - j - 1; std::string o = lower(a[j]);
        if (o == "asc") desc = false;
        else if (o == "desc") desc = true;
        else if (o == "alpha") alpha = true;
        else if (o == "limit" && left >= 2) { if (!string2ll(a[j+1], limit_start) || !string2ll(a[j+2], limit_count)) { err_notint(r); return; } j += 2; }
        else if (o == "store" && left >= 1 && !readonly) { store = a[j+1]; j++; }
        else if (o == "by" && left >= 1) { by = a[j+1]; j++; if (by.find('*') == std::string::npos) dontsort = true; else if (by.find("->") != std::string::npos && by.find('*') > by.find("->")) dontsort = true; }
        else if (o == "get" && left >= 1) { gets.push_back(a[j+1]); j++; }
        else { err_syntax(r); return; }
    }
    Memory::Snapshot snap = g.mem.snapshot(db::svc(c.db));
    VarPtr v = db::live_var(snap, c.db, a[1]);
    Kind k = db::kind_of(v);
    std::vector<SortItem> items;
    if (k == Kind::ListElem || k == Kind::SetMember) {
        for (auto& e : db::elems(v, c.db, a[1])) { SortItem it; it.val = k == Kind::ListElem ? e.bag().s : e.instance.substr(1); items.push_back(std::move(it)); }
    } else if (k == Kind::ZMember) {
        v->n_each([&](const NumInst& ni, const CellPtr&) { SortItem it; it.val = ni.instance.substr(1); items.push_back(std::move(it)); return true; });   // score order
    } else if (k == Kind::String) { err_wrongtype(r); return; }
    else if (k != Kind::None) { err_wrongtype(r); return; }
    // by-pattern lookups for every element
    if (!dontsort) {
        for (auto& it : items) {
            std::string w = it.val;
            if (!by.empty()) { std::string got; if (lookup_pattern(snap, c, by, it.val, got)) w = got; else w = ""; }
            if (alpha) it.cmp = w;
            else if (w.empty()) it.score = 0;
            else { long double d; if (!string2ld(w, d)) { r.error("ERR One or more scores can't be converted into double"); return; } it.score = (double)d; }
        }
        std::stable_sort(items.begin(), items.end(), [&](const SortItem& x, const SortItem& y) {
            int cmp;
            if (alpha) cmp = x.cmp < y.cmp ? -1 : (y.cmp < x.cmp ? 1 : 0);
            else cmp = x.score < y.score ? -1 : (x.score > y.score ? 1 : 0);
            if (cmp == 0 && !by.empty()) cmp = x.val < y.val ? -1 : (y.val < x.val ? 1 : 0);   // BY sub-sorts lexicographically on ties
            return desc ? cmp > 0 : cmp < 0;
        });
    } else if (k == Kind::SetMember) {
        // a set has no order; nosort on a set is still sorted lexicographically for the client's sake when stored or scripted -- Redis sorts here too
        std::stable_sort(items.begin(), items.end(), [](const SortItem& x, const SortItem& y) { return x.val < y.val; });
        if (desc) std::reverse(items.begin(), items.end());
    } else if (k == Kind::ZMember && desc) std::reverse(items.begin(), items.end());
    // limit
    long long n = (long long)items.size();
    long long start = limit_start, count = limit_count;
    if (start < 0) { start = 0; }
    if (count < 0) count = n;
    if (start > n) start = n;
    long long end = std::min(n, start + count);
    std::vector<std::string> out;
    for (long long i = start; i < end; i++) {
        if (gets.empty()) { out.push_back(items[i].val); continue; }
        for (auto& gp : gets) {
            if (gp == "#") { out.push_back(items[i].val); continue; }
            std::string got; if (lookup_pattern(snap, c, gp, items[i].val, got)) out.push_back(got); else out.push_back(std::string("\x00", 1) + "\x01null");
        }
    }
    if (!store.empty()) {
        std::vector<std::pair<std::string, std::string>> nv;
        for (size_t i = 0; i < out.size(); i++) { uint64_t x = (1ULL << 63) + i; std::string b(8, '\0'); for (int j = 7; j >= 0; j--) { b[(size_t)j] = (char)(x & 0xff); x >>= 8; } nv.emplace_back(b, out[i] == std::string("\x00", 1) + "\x01null" ? "" : out[i]); }
        db::store_elems(c.db, store, Kind::ListElem, nv);
        r.integer((long long)out.size()); return;
    }
    r.array(out.size());
    for (auto& s : out) { if (s == std::string("\x00", 1) + "\x01null") r.null(); else r.bulk(s); }
}
static void cmd_sort(Client& c, const Argv& a, Reply& r) { sort_generic(c, a, r, false); }
static void cmd_sort_ro(Client& c, const Argv& a, Reply& r) { sort_generic(c, a, r, true); }
void register_sort_commands() { register_cmd("sort", cmd_sort); register_cmd("sort_ro", cmd_sort_ro); }
