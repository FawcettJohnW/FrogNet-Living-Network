// Streams. An entry is a cell whose instance is ELEM + the 16-byte big-endian id (ms, seq), so the
// partial index IS the stream in id order: XRANGE is a range read, XLEN a count, XREAD a held read on
// the key. The only maintained state is a small meta cell at the key's value instance (last id,
// entries added, max deleted id, recorded first id) because Redis exposes those as facts that survive
// deletion; it is published in the same step as the entries it describes.
#include "stream.hpp"
namespace rr {
void stream_groups_count(const VarPtr& v, Reply& r);
void stream_groups_full(const VarPtr& v, Reply& r, long long count);

// ---------------------------------------------------------------- trimming (shared by XADD and XTRIM)
struct TrimArgs { int strategy = 0; bool approx = false; long long maxlen = 0; SID minid; long long limit = 0; bool limit_given = false; };   // 1 maxlen 2 minid
static bool parse_trim(const Argv& a, size_t& i, TrimArgs& t, bool xadd, Reply& r) {
    for (; i < a.size(); i++) {
        std::string opt = lower(a[i]); size_t more = a.size() - i - 1;
        if (opt == "maxlen" && more) {
            if (t.strategy) { r.error("ERR syntax error, MAXLEN and MINID options at the same time are not compatible"); return false; }
            t.approx = false; if (more >= 2 && (a[i+1] == "~" || a[i+1] == "=")) { t.approx = a[i+1] == "~"; i++; }
            if (!string2ll(a[i+1], t.maxlen)) { err_notint(r); return false; }
            if (t.maxlen < 0) { r.error("ERR The MAXLEN argument must be >= 0."); return false; }
            i++; t.strategy = 1;
        } else if (opt == "minid" && more) {
            if (t.strategy) { r.error("ERR syntax error, MAXLEN and MINID options at the same time are not compatible"); return false; }
            t.approx = false; if (more >= 2 && (a[i+1] == "~" || a[i+1] == "=")) { t.approx = a[i+1] == "~"; i++; }
            if (!parse_id(a[i+1], t.minid, 0, true)) { err_id(r); return false; }
            i++; t.strategy = 2;
        } else if (opt == "limit" && more) {
            if (!string2ll(a[i+1], t.limit)) { err_notint(r); return false; }
            if (t.limit < 0) { r.error("ERR The LIMIT argument must be >= 0."); return false; }
            t.limit_given = true; i++;
        } else if (xadd && opt == "nomkstream") { /* handled by caller */ }
        else if (xadd) break;
        else { err_syntax(r); return false; }
    }
    if (t.limit && !t.strategy) { r.error("ERR syntax error, LIMIT cannot be used without specifying a trimming strategy"); return false; }
    if (!xadd && !t.strategy) { r.error("ERR syntax error, XTRIM must be called with a trimming strategy"); return false; }
    if (t.limit_given && !t.approx) { r.error("ERR syntax error, LIMIT cannot be used without the special ~ option"); return false; }
    return true;
}
// which entries a trim removes, given the current snapshot
static std::vector<SID> trim_victims(const VarPtr& v, const TrimArgs& t) {
    std::vector<SID> out;
    if (!t.strategy) return out;
    auto all = range(v, SID{0, 0}, SID_MAX, false, 0);
    long long n = (long long)all.size();
    if (t.strategy == 1) { for (long long i = 0; i < n - t.maxlen; i++) out.push_back(all[(size_t)i].first); }
    else { for (auto& e : all) { if (e.first < t.minid) out.push_back(e.first); else break; } }
    if (t.limit && (long long)out.size() > t.limit) out.resize((size_t)t.limit);
    return out;
}

// ---------------------------------------------------------------- XADD

static void cmd_xadd(Client& c, const Argv& a, Reply& r) {
    size_t i = 2; TrimArgs t; bool nomk = false;
    for (size_t j = 2; j < a.size(); j++) { std::string o = lower(a[j]); if (o == "nomkstream") { nomk = true; } else if (o == "maxlen" || o == "minid" || o == "limit") { break; } else break; }
    // parse trim options and NOMKSTREAM in order until the id
    for (; i < a.size(); i++) {
        std::string opt = lower(a[i]);
        if (opt == "nomkstream") continue;
        if (opt == "maxlen" || opt == "minid" || opt == "limit") { size_t k = i; if (!parse_trim(a, k, t, true, r)) return; i = k; if (i < a.size()) { std::string o2 = lower(a[i]); if (o2 == "nomkstream" || o2 == "maxlen" || o2 == "minid" || o2 == "limit") continue; } break; }
        break;
    }
    if (i >= a.size()) { err_arity(r, "xadd"); return; }
    std::string ids = a[i]; size_t fields_from = i + 1;
    if (fields_from >= a.size() || (a.size() - fields_from) % 2) { r.error("ERR wrong number of arguments for 'xadd' command"); return; }
    SID want; bool seq_given = true, auto_id = ids == "*";
    if (!auto_id && !parse_id(ids, want, 0, true, &seq_given)) { err_id(r); return; }
    if (!auto_id && seq_given && want.ms == 0 && want.seq == 0) { r.error("ERR The ID specified in XADD must be greater than 0-0"); return; }
    VarPtr v; int st = svar(c, a[1], v, r); if (st < 0) return;
    if (!st && nomk) { r.null(); return; }
    // [THE_MEMORY_DECIDES_V1] The entry's id is decided by the memory: the entry lands only if its instance is free
    // (replace_if on nothing), and an XADD that loses takes the next id. The last id is the greater of the one stored
    // and the greatest entry present, so a stored last that another XADD has not yet advanced cannot hand out an id twice.
    db::Key k(c.db, a[1]);
    if (!st) { Meta z; g.mem.replace_if(k.R, k.name, k.h, db::VAL, nullptr, Bag(*meta_bag(z))); }   // the stream's meta cell, created once
    Bag e; e.kind = Kind::StreamEntry; e.s = pack_fields(a, fields_from);
    // [THE_MEMORY_DECIDES_V1] The id is decided once. An auto id is (time, this XADD's own number): the number is this
    // XADD's add on the meta cell's accumulator -- one fetch_add, so no two XADDs can hold the same id and none needs a
    // second attempt. An explicit id lands only if free (insert if absent); a loser gets Redis's error, final.
    VarPtr cv = g.mem.var(k.R, k.name, k.h);
    Meta m = meta_of(cv);
    SID id;
    ebr::Guard eg;
    if (auto_id) {
        // Unique by construction, one decision, in this order:
        //  1. count this XADD in flight (one fetch_add on its own cell) -- BEFORE anything else, so any XADD that overlaps
        //     this one at any point sees a count of 2 or more;
        //  2. take this XADD's number (one fetch_add on the meta cell's accumulator; each entry keeps it);
        //  3. read the stream (the last id, the entry it belongs to) -- AFTER counting, so an XADD that counted 1 reads
        //     a stream every earlier XADD has already published to;
        //  4. alone (the only XADD in flight, and the last entry carries number n-1): Redis's exact id -- sequence 0 in a
        //     new millisecond, the last sequence + 1 in the same one. Otherwise sequence 2^63 + n: a range the exact
        //     form never reaches, and numbers are distinct, so no two XADDs can produce one id.
        //  The count is taken back after the insert, landed or not. (Measured: counting after taking the number let
        //  ten whole XADDs run between the two steps, and one of them held the id the stale XADD then chose.)
        Bag zero_if; zero_if.kind = Kind::StreamMeta;
        const int64_t in_flight = g.mem.add(k.R, k.name, k.h, INFLIGHT_INST, zero_if, 1, 0, false).acc_after;
        struct Out { Region& R; const std::string& nm; size_t h; ~Out() { Bag z; z.kind = Kind::StreamMeta; g.mem.add(R, nm, h, INFLIGHT_INST, z, -1, 0, false); } } out_{k.R, k.name, k.h};
        auto ad = g.mem.add(k.R, k.name, k.h, db::VAL, *meta_bag(Meta()), 1, 0, false);
        uint64_t n = (uint64_t)ad.acc_after + unpack_meta(ad.node->bag->s).added;
        m = meta_of(g.mem.var(k.R, k.name, k.h));
        CellPtr lc = g.mem.cell(k.R, k.name, k.h, inst_of(m.last));
        bool pred = (lc && lc->bag->kind == Kind::StreamEntry) ? (uint64_t)lc->bag->n == n - 1 : (m.last == SID{0, 0} && n == 1);
        bool alone = in_flight == 1 && pred;
        uint64_t now = (uint64_t)Memory::now_ms();
        const uint64_t HIGH = 1ULL << 63;
        if (alone) {
            if (now > m.last.ms) id = SID{now, 0};
            else if (m.last.seq < HIGH - 1) id = SID{m.last.ms, m.last.seq + 1};
            else if (m.last.ms < UINT64_MAX) id = SID{m.last.ms + 1, 0};
            else { r.error("ERR The stream has exhausted the last possible ID, unable to add more items"); return; }
        } else {
            id = SID{now > m.last.ms ? now : m.last.ms, HIGH + n};
            if (!(m.last < id)) {
                if (m.last.ms == UINT64_MAX) { r.error("ERR The stream has exhausted the last possible ID, unable to add more items"); return; }
                id = SID{m.last.ms + 1, HIGH + n};
            }
        }
        e.n = (int64_t)n;
        if (!g.mem.replace_if(k.R, k.name, k.h, inst_of(id), nullptr, Bag(e))) {
            if (getenv("RIBBIT_XADD_DIAG")) {   // [DIAG-XADD] the collision, in full: what this XADD saw and what holds the id
                CellPtr held = g.mem.cell(k.R, k.name, k.h, inst_of(id));
                fprintf(stderr, "[DIAG-XADD] lost id=%llu-%llu n=%llu in_flight=%lld pred=%d alone=%d now=%llu last=%llu-%llu lc_n=%lld held_n=%lld\n",
                        (unsigned long long)id.ms, (unsigned long long)id.seq, (unsigned long long)n, (long long)in_flight, (int)pred, (int)alone,
                        (unsigned long long)now, (unsigned long long)m.last.ms, (unsigned long long)m.last.seq,
                        lc ? (long long)lc->bag->n : -1LL, held ? (long long)held->bag->n : -1LL);
            }
            r.error("ERR The ID specified in XADD is equal or smaller than the target stream top item"); return;
        }
    } else {
        if (!seq_given) { if (want.ms > m.last.ms) id = SID{want.ms, 0}; else if (want.ms == m.last.ms) { if (m.last.seq == UINT64_MAX) { r.error("ERR The ID specified in XADD is equal or smaller than the target stream top item"); return; } id = SID{want.ms, m.last.seq + 1}; } else { r.error("ERR The ID specified in XADD is equal or smaller than the target stream top item"); return; } }
        else { if (!(m.last < want)) { r.error("ERR The ID specified in XADD is equal or smaller than the target stream top item"); return; } id = want; }
        auto ad = g.mem.add(k.R, k.name, k.h, db::VAL, *meta_bag(Meta()), 1, 0, false);   // entries added: one fetch_add
        e.n = (int64_t)((uint64_t)ad.acc_after + unpack_meta(ad.node->bag->s).added);    // this XADD's number, kept in the entry
        if (!g.mem.replace_if(k.R, k.name, k.h, inst_of(id), nullptr, Bag(e))) {
            ad.node->acc.fetch_sub(1, std::memory_order_acq_rel);                        // no entry was added: withdraw the count
            r.error("ERR The ID specified in XADD is equal or smaller than the target stream top item"); return;
        }
    }
    // trimming: the victims are decided by their removal (a removed entry is gone for every trimmer)
    if (t.strategy) {
        VarPtr tv = g.mem.var(k.R, k.name, k.h);
        auto all = range(tv, SID{0, 0}, SID_MAX, false, 0);
        std::vector<SID> victims;
        if (t.strategy == 1) { for (long long q = 0; q < (long long)all.size() - t.maxlen; q++) victims.push_back(all[(size_t)q].first); }
        else for (auto& x : all) { if (x.first < t.minid) victims.push_back(x.first); else break; }
        if (t.limit && (long long)victims.size() > t.limit) victims.resize((size_t)t.limit);
        std::vector<std::pair<std::string, std::string>> rs; for (auto& vid : victims) rs.emplace_back(a[1], inst_of(vid));
        if (!rs.empty()) g.mem.remove_many(db::svc(c.db), rs);
    }
    g.stats.dirty++;
    r.bulk(sid_str(id));
}
static void cmd_xlen(Client& c, const Argv& a, Reply& r) { VarPtr v; if (svar(c, a[1], v, r) < 0) return; r.integer((long long)slen(v)); }
static void xrange_generic(Client& c, const Argv& a, Reply& r, bool reverse) {
    long long count = 0;
    if (a.size() > 4) { if (a.size() != 6 || !str_eq_ci(a[4], "count")) { err_syntax(r); return; } if (!string2ll(a[5], count)) { err_notint(r); return; } if (count < 0) count = 0; }
    const std::string& s1 = reverse ? a[3] : a[2]; const std::string& s2 = reverse ? a[2] : a[3];
    SID lo, hi; bool loex = false, hiex = false;
    std::string t1 = s1, t2 = s2;
    if (!t1.empty() && t1[0] == '(') { loex = true; t1 = t1.substr(1); }
    if (!t2.empty() && t2[0] == '(') { hiex = true; t2 = t2.substr(1); }
    if (!parse_id(t1, lo, 0, loex)) { r.error("ERR Invalid stream ID specified as stream command argument"); return; }
    if (!parse_id(t2, hi, UINT64_MAX, hiex)) { r.error("ERR Invalid stream ID specified as stream command argument"); return; }
    if (loex) { if (lo.seq == UINT64_MAX) { if (lo.ms == UINT64_MAX) { r.error("ERR invalid start ID for the interval"); return; } lo.ms++; lo.seq = 0; } else lo.seq++; }
    if (hiex) { if (hi.seq == 0) { if (hi.ms == 0) { r.error("ERR invalid end ID for the interval"); return; } hi.ms--; hi.seq = UINT64_MAX; } else hi.seq--; }
    VarPtr v; if (svar(c, a[1], v, r) < 0) return;
    if (a.size() == 6 && count == 0) { r.array(0); return; }
    auto es = range(v, lo, hi, reverse, count);
    r.array(es.size()); for (auto& e : es) reply_entry(r, e.first, e.second);
}
static void cmd_xrange(Client& c, const Argv& a, Reply& r) { xrange_generic(c, a, r, false); }
static void cmd_xrevrange(Client& c, const Argv& a, Reply& r) { xrange_generic(c, a, r, true); }
static void cmd_xdel(Client& c, const Argv& a, Reply& r) {
    std::vector<SID> ids;
    for (size_t i = 2; i < a.size(); i++) { SID id; if (!parse_id(a[i], id, 0, true)) { err_id(r); return; } ids.push_back(id); }
    VarPtr v; int st = svar(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    // the entries each removed on their own: the count is what THIS call removed [REMOVAL_IS_THE_ANSWER_V1]
    long long deleted = 0; SID maxdel{0, 0};
    for (auto& id : ids) if (g.mem.remove_many(db::svc(c.db), {{a[1], inst_of(id)}}) == 1) { deleted++; if (maxdel < id) maxdel = id; }
    if (deleted) {
        // this XDEL's own cell holds the greatest id it deleted: no two XDELs write one cell (the meta derives the max)
        Meta d; d.maxdel = maxdel;
        g.mem.write(db::svc(c.db), a[1], MAXDEL_PFX + inst_of(maxdel).substr(1), meta_bag(d));
        g.stats.dirty++;
    }
    r.integer(deleted);
}
static void cmd_xtrim(Client& c, const Argv& a, Reply& r) {
    size_t i = 2; TrimArgs t; if (!parse_trim(a, i, t, false, r)) return;
    VarPtr v; int st = svar(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    auto victims = trim_victims(v, t);
    if (victims.empty()) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> rs;
    for (auto& vid : victims) rs.emplace_back(a[1], inst_of(vid));
    size_t removed = g.mem.remove_many(db::svc(c.db), rs);                   // [REMOVAL_IS_THE_ANSWER_V1]
    g.stats.dirty++;
    r.integer((long long)removed);
}
static void cmd_xsetid(Client& c, const Argv& a, Reply& r) {
    SID id; if (!parse_id(a[2], id, 0, true)) { err_id(r); return; }
    long long added = -1; SID maxdel; bool have_maxdel = false;
    for (size_t i = 3; i < a.size(); i++) {
        std::string o = lower(a[i]); size_t more = a.size() - i - 1;
        if (o == "entriesadded" && more) { if (!string2ll(a[i+1], added)) { err_notint(r); return; } if (added < 0) { r.error("ERR entries_added must be positive"); return; } i++; }
        else if (o == "maxdeletedid" && more) { if (!parse_id(a[i+1], maxdel, 0, true)) { err_id(r); return; } have_maxdel = true; i++; }
        else { err_syntax(r); return; }
    }
    VarPtr v; int st = svar(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.error("ERR no such key"); return; }
    Meta m = meta_of(v); SID f, l; bool nonempty = first_last(v, f, l);
    if (nonempty && id < l) { r.error("ERR The ID specified in XSETID is smaller than the target stream top item"); return; }
    if (added >= 0 && (uint64_t)added < slen(v)) { r.error("ERR The entries_added specified in XSETID is smaller than the target stream length"); return; }
    if (have_maxdel && id < maxdel) { r.error("ERR The ID specified in XSETID is smaller than the provided max_deleted_entry_id"); return; }
    if (!have_maxdel && id < m.maxdel) { r.error("ERR The ID specified in XSETID is smaller than current max_deleted_entry_id"); return; }
    {   // one exchange of the meta cell: the stored values are set (the accumulator starts again from the new cell)
        Meta mm = m; mm.last = id; if (added >= 0) mm.added = (uint64_t)added; else mm.added = m.added; if (have_maxdel) mm.maxdel = maxdel;
        db::Key k(c.db, a[1]); ebr::Guard eg; CellPtr old = nullptr;
        g.mem.exchange(k.R, k.name, k.h, Bag(*meta_bag(mm)), &old);
    }
    g.stats.dirty++;
    r.ok();
}

// ---------------------------------------------------------------- XREAD (non-consuming; every blocked reader gets the entry)
static void cmd_xread(Client& c, const Argv& a, Reply& r) {
    long long count = 0; long long timeout = -1; bool block = false; size_t i = 1;
    for (; i < a.size(); i++) {
        std::string o = lower(a[i]); size_t more = a.size() - i - 1;
        if (o == "count" && more) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count < 0) count = 0; i++; }
        else if (o == "block" && more) { if (!string2ll(a[i+1], timeout)) { r.error("ERR timeout is not an integer or out of range"); return; } if (timeout < 0) { r.error("ERR timeout is negative"); return; } block = true; i++; }   // BLOCK is integer milliseconds
        else if (o == "streams") { i++; break; }
        else if (o == "group" || o == "noack") { r.error("ERR The " + std::string(o == "group" ? "GROUP" : "NOACK") + " option is only supported by XREADGROUP. You called XREAD instead."); return; }
        else { err_syntax(r); return; }
    }
    if (i >= a.size()) { err_syntax(r); return; }
    size_t rest = a.size() - i;
    if (rest % 2) { r.error("ERR Unbalanced 'xread' list of streams: for each stream key an ID or '$' must be specified."); return; }
    size_t n = rest / 2;
    std::vector<std::string> keys(a.begin() + i, a.begin() + i + n);
    std::vector<SID> ids(n); std::vector<bool> dollar(n, false);
    for (size_t k = 0; k < n; k++) {
        const std::string& s = a[i + n + k];
        if (s == "$") { dollar[k] = true; continue; }
        if (s == ">") { r.error("ERR The > ID can be specified only when calling XREADGROUP using the GROUP <group> <consumer> option."); return; }
        if (!parse_id(s, ids[k], UINT64_MAX, true)) { err_id(r); return; }
    }
    // resolve $ to the current last id; exclusive lower bounds
    for (size_t k = 0; k < n; k++) {
        VarPtr v; if (svar(c, keys[k], v, r) < 0) return;
        if (dollar[k]) ids[k] = meta_of(v).last;
    }
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout > 0 ? timeout : 0);
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        std::vector<std::pair<std::string, std::vector<std::pair<SID, std::string>>>> found;
        for (size_t k = 0; k < n; k++) {
            VarPtr v = db::live_var(c.db, keys[k]); Kind kd = db::kind_of(v);
            if (kd != Kind::StreamEntry && kd != Kind::StreamMeta) continue;
            SID lo = ids[k]; if (lo.seq == UINT64_MAX) { if (lo.ms == UINT64_MAX) continue; lo.ms++; lo.seq = 0; } else lo.seq++;
            auto es = range(v, lo, SID_MAX, false, count);
            if (!es.empty()) found.emplace_back(keys[k], std::move(es));
        }
        if (!found.empty()) {
            if (r.proto == 3) r.map(found.size()); else r.array(found.size());
            for (auto& f : found) { if (r.proto != 3) r.array(2); r.bulk(f.first); r.array(f.second.size()); for (auto& e : f.second) reply_entry(r, e.first, e.second); }
            return;
        }
        if (!block) { r.null_array(); return; }
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}

// ---------------------------------------------------------------- XINFO STREAM
static void cmd_xinfo_stream(Client& c, const Argv& a, Reply& r) {
    bool full = false; long long count = 10;
    for (size_t i = 3; i < a.size(); i++) { std::string o = lower(a[i]); if (o == "full") full = true; else if (o == "count" && i + 1 < a.size() && full) { if (!string2ll(a[++i], count)) { err_notint(r); return; } if (count < 0) count = 10; } else { err_syntax(r); return; } }
    VarPtr v; int st = svar(c, a[2], v, r); if (st < 0) return;
    if (!st) { r.error("ERR no such key"); return; }
    Meta m = meta_of(v); SID f, l; bool ne = first_last(v, f, l);
    if (!full) {
        r.map(10);
        r.bulk("length"); r.integer((long long)slen(v));
        r.bulk("radix-tree-keys"); r.integer((long long)(slen(v) / 100 + 1));     // no radix tree here; a number that grows with the stream so loops keyed on it terminate
        r.bulk("radix-tree-nodes"); r.integer((long long)(slen(v) / 100 + 2));
        r.bulk("last-generated-id"); r.bulk(sid_str(m.last));
        r.bulk("max-deleted-entry-id"); r.bulk(sid_str(m.maxdel));
        r.bulk("entries-added"); r.integer((long long)m.added);
        r.bulk("recorded-first-entry-id"); r.bulk(sid_str(m.first));
        r.bulk("groups"); stream_groups_count(v, r);
        r.bulk("first-entry"); if (ne) { auto e = range(v, f, f, false, 1); reply_entry(r, f, e[0].second); } else r.null();
        r.bulk("last-entry"); if (ne) { auto e = range(v, l, l, false, 1); reply_entry(r, l, e[0].second); } else r.null();
        return;
    }
    auto es = range(v, SID{0, 0}, SID_MAX, false, count);
    r.map(9);
    r.bulk("length"); r.integer((long long)slen(v));
    r.bulk("radix-tree-keys"); r.integer((long long)(slen(v) / 100 + 1));
    r.bulk("radix-tree-nodes"); r.integer((long long)(slen(v) / 100 + 2));
    r.bulk("last-generated-id"); r.bulk(sid_str(m.last));
    r.bulk("max-deleted-entry-id"); r.bulk(sid_str(m.maxdel));
    r.bulk("entries-added"); r.integer((long long)m.added);
    r.bulk("recorded-first-entry-id"); r.bulk(sid_str(m.first));
    r.bulk("entries"); r.array(es.size()); for (auto& e : es) reply_entry(r, e.first, e.second);
    r.bulk("groups"); stream_groups_full(v, r, count);
}
static void cmd_xinfo_help(Client&, const Argv&, Reply& r) {
    const char* lines[] = {"XINFO <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","CONSUMERS <key> <groupname>","    Show consumers of <groupname>.","GROUPS <key>","    Show the stream consumer groups.","STREAM <key> [FULL [COUNT <count>]","    Show information about the stream.","HELP","    Print this help."};
    r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
}
void register_stream_commands() {
    register_cmd("xadd", cmd_xadd); register_cmd("xlen", cmd_xlen); register_cmd("xrange", cmd_xrange); register_cmd("xrevrange", cmd_xrevrange);
    register_cmd("xdel", cmd_xdel); register_cmd("xtrim", cmd_xtrim); register_cmd("xsetid", cmd_xsetid); register_cmd("xread", cmd_xread);
    register_cmd("xinfo|stream", cmd_xinfo_stream); register_cmd("xinfo|help", cmd_xinfo_help);
}

}  // namespace rr
