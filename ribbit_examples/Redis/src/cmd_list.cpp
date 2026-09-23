// Lists. An element is a cell whose instance is ELEM + a position: 16 hex digits (a signed sequence
// biased by 2^63, so lexicographic order is numeric order) plus an optional hex fraction for LINSERT.
// The list is the partial index in instance order. Index access is rank in the tree (O(log n)),
// so gaps from LREM/LPOP cost nothing and LPUSH never renumbers: it writes one position before the head.
// A pop is read-the-end then remove(id): if the remove returns 0 somebody else got it, try the next.
#include "server.hpp"
#include <climits>
#include <algorithm>
#include <chrono>
#include <sys/socket.h>
#include <cerrno>


static int list_var(Client& c, const std::string& key, VarPtr& v, Reply& r) {
    v = db::live_var(c.db, key);
    Kind k = db::kind_of(v);
    if (k == Kind::None) return 0;
    if (k != Kind::ListElem) { err_wrongtype(r); return -1; }
    return 1;
}
static size_t skip_of(const VarPtr& v) { return v->rank(db::ELEM); }
static std::optional<Ent> at(const VarPtr& v, long long i) {     // i already normalised, may be out of range
    if (!v || i < 0) return std::nullopt;
    return v->nth(skip_of(v) + (size_t)i);
}
// A list position is 8 bytes big-endian plus an optional fraction (bytes, base 256), compared bytewise: with
// the element prefix it is 9 bytes for a whole-number position, which fits a std::string with no allocation.
static std::string bin8(uint64_t x) { std::string s(8, '\0'); for (int i = 7; i >= 0; i--) { s[(size_t)i] = (char)(x & 0xff); x >>= 8; } return s; }
static const uint64_t BIAS = 1ULL << 63;
static std::string first_pos(const VarPtr& v) { auto n = at(v, 0); return n ? n->key.substr(1) : ""; }
static std::string last_pos(const VarPtr& v) { long long len = (long long)db::elem_count(v); auto n = at(v, len - 1); return n ? n->key.substr(1) : ""; }
static uint64_t int_part(const std::string& pos) { uint64_t x = 0; for (size_t i = 0; i < 8 && i < pos.size(); i++) x = (x << 8) | (unsigned char)pos[i]; return x; }
// a position strictly before `head` (head may carry a fraction)
static std::string before_pos(const std::string& head) {
    std::string p = head.substr(0, 8);
    if (p < head) return p;                    // the bare integer part is unused and sorts before its fractions
    return bin8(int_part(head) - 1);
}
static std::string after_pos(const std::string& tail) { return bin8(int_part(tail) + 1); }
// a position strictly between a and b (a < b): the midpoint of their fractions, one byte longer at most
static std::string between(const std::string& a, const std::string& b) {
    std::string fa = a.substr(8), fb = b.substr(8);
    uint64_t ia = int_part(a), ib = int_part(b);
    if (ia == ib) {
        size_t L = std::max(fa.size(), fb.size()) + 1; fa.resize(L, '\0'); fb.resize(L, '\0');
        std::string m(L, '\0'); int carry = 0;
        for (size_t i = L; i-- > 0;) { int sum = (unsigned char)fa[i] + (unsigned char)fb[i] + carry; m[i] = (char)(sum & 0xff); carry = sum >> 8; }
        int rem = carry;                       // halve, carry being the unit above the point
        for (size_t i = 0; i < L; i++) { int d = (unsigned char)m[i] + rem * 256; m[i] = (char)(d >> 1); rem = d & 1; }
        if (rem) m += (char)0x80;
        while (!m.empty() && m.back() == '\0') m.pop_back();
        std::string out = a.substr(0, 8) + m;
        if (!(a < out && out < b)) out = a + (char)0x80;    // cannot happen for well-formed positions; keep the order invariant anyway
        return out;
    }
    std::string cand = bin8(ia) + fa + (char)0x80;
    if (a < cand && cand < b) return cand;
    return a + (char)0x80;
}
static std::vector<std::string> values_of(const VarPtr& v, long long start, long long stop) {   // inclusive, normalised
    std::vector<std::string> out;
    auto n = at(v, start); if (!n) return out;
    size_t want = (size_t)(stop - start + 1);
    v->range(n->key, "", true, [&](const std::string&, const CellPtr& cp) { out.push_back(cp->bag->s); return out.size() < want; });
    return out;
}
static bool norm_range(long long len, long long& start, long long& stop) {
    if (start < 0) start += len;
    if (stop < 0) stop += len;
    if (start < 0) start = 0;
    if (start > stop || start >= len) return false;
    if (stop >= len) stop = len - 1;
    return true;
}
static void push_generic(Client& c, const Argv& a, Reply& r, bool left, bool xx) {
    db::Key k(c.db, a[1]); VarPtr v = k.v; int st = k.k == Kind::None ? 0 : 1;
    if (st && k.k != Kind::ListElem) { err_wrongtype(r); return; }
    if (xx && !st) { r.integer(0); return; }
    std::vector<Memory::W> ws;
    std::string pos = st ? (left ? first_pos(v) : last_pos(v)) : bin8(BIAS);
    if (!st) { pos = left ? bin8(BIAS + 1) : bin8(BIAS - 1); }
    for (size_t i = 2; i < a.size(); i++) {
        pos = left ? before_pos(pos) : after_pos(pos);
        Bag b; b.kind = Kind::ListElem; b.s = a[i];
        ws.push_back(Memory::W{a[1], db::ELEM + pos, std::move(b)});
    }
    // the length this push produced: what was there when it looked plus what it added. The view is live, so a
    // participant that popped between the write and the read after is not a reason to report fewer than we added.
    long long before = (long long)(st ? db::elem_count(v) : 0);
    if (ws.size() == 1) g.mem.write(k.R, k.name, k.h, ws[0].instance, std::move(ws[0].own));   // one element: the addressed write, no batch
    else g.mem.write_many(db::svc(c.db), ws);                           // every pushed element appears in one step
    g.stats.dirty++;
    long long after = k.push_count();
    r.integer(after >= before + (long long)ws.size() ? after : before + (long long)ws.size());
}
static void cmd_lpush(Client& c, const Argv& a, Reply& r) { push_generic(c, a, r, true, false); }
static void cmd_rpush(Client& c, const Argv& a, Reply& r) { push_generic(c, a, r, false, false); }
static void cmd_lpushx(Client& c, const Argv& a, Reply& r) { push_generic(c, a, r, true, true); }
static void cmd_rpushx(Client& c, const Argv& a, Reply& r) { push_generic(c, a, r, false, true); }

// pop up to `count` elements from one end; returns what was actually taken (the race is settled by remove)
static std::vector<std::string> pop_end(int dbi, const std::string& key, bool left, long long count) {
    std::vector<std::string> out;
    while ((long long)out.size() < count) {
        VarPtr v = db::live_var(dbi, key);
        if (db::kind_of(v) != Kind::ListElem) break;
        long long len = (long long)db::elem_count(v);
        auto n = at(v, left ? 0 : len - 1);
        if (!n) break;
        std::string val = n->val->bag->s;
        std::vector<std::pair<std::string, std::string>> rs{{key, n->key}};
        if (len == 1 && Memory::inst(v, db::TTL)) rs.emplace_back(key, db::TTL);
        if (g.mem.remove_many(db::svc(dbi), rs) == 0) continue;   // someone else took it: read the new end
        g.stats.dirty++;
        out.push_back(std::move(val));
    }
    return out;
}
static void pop_generic(Client& c, const Argv& a, Reply& r, bool left) {
    if (a.size() > 3) { err_arity(r, lower(a[0])); return; }
    long long count = 1; bool with_count = a.size() == 3;
    if (with_count) { if (!string2ll(a[2], count)) { err_notint(r); return; } if (count < 0) { r.error("ERR value is out of range, must be positive"); return; } }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { if (with_count) r.null_array(); else r.null(); return; }
    if (with_count && count == 0) { r.array(0); return; }
    auto got = pop_end(c.db, a[1], left, count);
    if (!with_count) { if (got.empty()) r.null(); else r.bulk(got[0]); return; }
    r.array(got.size()); for (auto& s : got) r.bulk(s);
}
static void cmd_lpop(Client& c, const Argv& a, Reply& r) { pop_generic(c, a, r, true); }
static void cmd_rpop(Client& c, const Argv& a, Reply& r) { pop_generic(c, a, r, false); }

static void cmd_llen(Client& c, const Argv& a, Reply& r) { VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return; r.integer(st ? (long long)db::elem_count(v) : 0); }
static void cmd_lindex(Client& c, const Argv& a, Reply& r) {
    long long i; if (!string2ll(a[2], i)) { err_notint(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    long long len = st ? (long long)db::elem_count(v) : 0;
    if (i < 0) i += len;
    auto n = (st && i < len) ? at(v, i) : std::nullopt;
    if (n) r.bulk(n->val->bag->s); else r.null();
}
static void cmd_lrange(Client& c, const Argv& a, Reply& r) {
    long long start, stop; if (!string2ll(a[2], start) || !string2ll(a[3], stop)) { err_notint(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    long long len = st ? (long long)db::elem_count(v) : 0;
    if (!st || !norm_range(len, start, stop)) { r.array(0); return; }
    auto vals = values_of(v, start, stop);
    r.array(vals.size()); for (auto& s : vals) r.bulk(s);
}
static void cmd_lset(Client& c, const Argv& a, Reply& r) {
    long long i; if (!string2ll(a[2], i)) { err_notint(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.error("ERR no such key"); return; }
    long long len = (long long)db::elem_count(v); if (i < 0) i += len;
    auto n = i < len ? at(v, i) : std::nullopt;
    if (!n) { r.error("ERR index out of range"); return; }
    Bag b; b.kind = Kind::ListElem; b.s = a[3];
    g.mem.write(db::svc(c.db), a[1], n->key, std::move(b)); g.stats.dirty++;
    r.ok();
}
static void cmd_linsert(Client& c, const Argv& a, Reply& r) {
    bool before; std::string w = lower(a[2]);
    if (w == "before") before = true; else if (w == "after") before = false; else { err_syntax(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::optional<Ent> pivot; std::string prev_key, next_key; bool found = false;
    // one walk: find the pivot and its neighbours
    v->range(db::ELEM, "", true, [&](const std::string& k, const CellPtr& cp) {
        if (found) { next_key = k; return false; }
        if (cp->bag->s == a[3]) { found = true; pivot = Ent{k, cp}; return true; }
        prev_key = k; return true;
    });
    if (!found) { r.integer(-1); return; }
    std::string ppos = pivot->key.substr(1), pos;
    if (before) pos = prev_key.empty() ? before_pos(ppos) : between(prev_key.substr(1), ppos);
    else pos = next_key.empty() ? after_pos(ppos) : between(ppos, next_key.substr(1));
    Bag b; b.kind = Kind::ListElem; b.s = a[4];
    long long n0 = (long long)db::elem_count(v);
    g.mem.write(db::svc(c.db), a[1], db::ELEM + pos, std::move(b)); g.stats.dirty++;
    long long after = (long long)db::elem_count(g.mem.var(db::svc(c.db), a[1]));
    r.integer(after >= n0 + 1 ? after : n0 + 1);
}
static void cmd_lrem(Client& c, const Argv& a, Reply& r) {
    long long count; if (!string2ll(a[2], count)) { err_notint(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::string> keys;
    long long want = count == 0 ? LLONG_MAX : (count < 0 ? -count : count);
    auto es = db::elems(v, c.db, a[1]);
    if (count >= 0) { for (auto& e : es) { if ((long long)keys.size() >= want) break; if (e.bag().s == a[3]) keys.push_back(e.instance); } }
    else { for (size_t i = es.size(); i-- > 0;) { if ((long long)keys.size() >= want) break; if (es[i].bag().s == a[3]) keys.push_back(es[i].instance); } }
    if (keys.empty()) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> rs; for (auto& k : keys) rs.emplace_back(a[1], k);
    if (keys.size() >= es.size() && Memory::inst(v, db::TTL)) rs.emplace_back(a[1], db::TTL);
    g.mem.remove_many(db::svc(c.db), rs); g.stats.dirty++;
    r.integer((long long)keys.size());
}
static void cmd_ltrim(Client& c, const Argv& a, Reply& r) {
    long long start, stop; if (!string2ll(a[2], start) || !string2ll(a[3], stop)) { err_notint(r); return; }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.ok(); return; }
    long long len = (long long)db::elem_count(v);
    bool keep = norm_range(len, start, stop);
    auto es = db::elems(v, c.db, a[1]);
    std::vector<std::pair<std::string, std::string>> rs;
    for (long long i = 0; i < len; i++) if (!keep || i < start || i > stop) rs.emplace_back(a[1], es[(size_t)i].instance);
    if ((long long)rs.size() >= len && Memory::inst(v, db::TTL)) rs.emplace_back(a[1], db::TTL);
    if (!rs.empty()) { g.mem.remove_many(db::svc(c.db), rs); g.stats.dirty++; }
    r.ok();
}
static void cmd_lpos(Client& c, const Argv& a, Reply& r) {
    long long rank = 1, count = -1, maxlen = 0;
    for (size_t j = 3; j < a.size(); j++) {
        bool more = j + 1 < a.size(); std::string o = lower(a[j]);
        if (o == "rank" && more) { if (!string2ll(a[++j], rank)) { err_notint(r); return; } if (rank == 0) { r.error("ERR RANK can't be zero: use 1 to start from the first match, 2 from the second ... or use negative to start from the end of the list"); return; } if (rank == LLONG_MIN) { r.error("ERR value is out of range"); return; } }
        else if (o == "count" && more) { if (!string2ll(a[++j], count) || count < 0) { r.error("ERR COUNT can't be negative"); return; } }
        else if (o == "maxlen" && more) { if (!string2ll(a[++j], maxlen) || maxlen < 0) { r.error("ERR MAXLEN can't be negative"); return; } }
        else { err_syntax(r); return; }
    }
    VarPtr v; int st = list_var(c, a[1], v, r); if (st < 0) return;
    std::vector<long long> hits;
    if (st) {
        auto es = db::elems(v, c.db, a[1]); long long n = (long long)es.size();
        long long skip = (rank > 0 ? rank : -rank) - 1, seen = 0;
        for (long long k = 0; k < n; k++) {
            if (maxlen && k >= maxlen) break;
            long long i = rank > 0 ? k : n - 1 - k;
            if (es[(size_t)i].bag().s != a[2]) continue;
            if (seen++ < skip) continue;
            hits.push_back(i);
            if (count == 0) continue;
            if (count > 0 && (long long)hits.size() >= count) break;
            if (count < 0) break;
        }
    }
    if (count < 0) { if (hits.empty()) r.null(); else r.integer(hits[0]); return; }
    r.array(hits.size()); for (auto h : hits) r.integer(h);
}
// pop from src, push to dst: two steps (question-3 candidate: two-list atomic move)
static bool move_one(Client& c, const std::string& src, const std::string& dst, bool from_left, bool to_left, Reply& r) {
    VarPtr sv, dv;
    if (list_var(c, src, sv, r) < 0) return false;
    if (list_var(c, dst, dv, r) < 0) return false;
    auto got = pop_end(c.db, src, from_left, 1);
    if (got.empty()) { r.null(); return false; }
    Argv pa{ to_left ? "lpush" : "rpush", dst, got[0] };
    Reply tmp; tmp.proto = r.proto; push_generic(c, pa, tmp, to_left, false);
    r.bulk(got[0]);
    return true;
}
static void cmd_rpoplpush(Client& c, const Argv& a, Reply& r) { move_one(c, a[1], a[2], false, true, r); }
static void cmd_lmove(Client& c, const Argv& a, Reply& r) {
    std::string f = lower(a[3]), t = lower(a[4]);
    if ((f != "left" && f != "right") || (t != "left" && t != "right")) { err_syntax(r); return; }
    move_one(c, a[1], a[2], f == "left", t == "left", r);
}
static void cmd_lmpop(Client& c, const Argv& a, Reply& r) {
    long long numkeys; if (!string2ll(a[1], numkeys) || numkeys <= 0) { r.error("ERR numkeys should be greater than 0"); return; }
    if ((long long)a.size() < 2 + numkeys + 1) { err_syntax(r); return; }
    size_t j = 2 + (size_t)numkeys; std::string w = lower(a[j]); bool left;
    if (w == "left") left = true; else if (w == "right") left = false; else { err_syntax(r); return; }
    long long count = 1; bool seen_count = false;
    for (j++; j < a.size(); j++) { if (str_eq_ci(a[j], "count") && j + 1 < a.size() && !seen_count) { seen_count = true; if (!string2ll(a[++j], count) || count <= 0) { r.error("ERR count should be greater than 0"); return; } } else { err_syntax(r); return; } }
    for (size_t i = 2; i < 2 + (size_t)numkeys; i++) {
        VarPtr v; int st = list_var(c, a[i], v, r); if (st < 0) return;
        if (!st) continue;
        auto got = pop_end(c.db, a[i], left, count);
        if (got.empty()) continue;
        r.array(2); r.bulk(a[i]); r.array(got.size()); for (auto& s : got) r.bulk(s);
        return;
    }
    r.null_array();
}

// ---- blocking pops.
// Who is served first is state, not a queue in the server: a parked participant writes a claim ticket
// under the key in the db's claims region (instance = its ticket, so the partial index is the line in
// arrival order). On wake-up it is entitled to pop only if its ticket is the lowest live claim for that
// key; otherwise it waits again. Leaving (served, timed out, unblocked, hung up) removes the ticket and
// touches the key so the next in line looks. remove() settling the pop is what makes it race-free.
bool parse_timeout(const std::string& s, int64_t& ms, Reply& r) {
    long double t; if (!string2ld(s, t)) { r.error("ERR timeout is not a float or out of range"); return false; }
    if (t < 0) { r.error("ERR timeout is negative"); return false; }
    if (t * 1000 > (long double)LLONG_MAX) { r.error("ERR timeout is out of range"); return false; }
    ms = (int64_t)(t * 1000); if (t > 0 && ms == 0) ms = 1;
    return true;
}
static std::atomic<uint64_t> next_ticket{1};
std::string claim_ticket() { return bin8(next_ticket.fetch_add(1)); }
#include "blocking.hpp"

WaitWord& wait_word_for(Client& c, const std::vector<std::string>& keys) {
    Region& R = g.mem.region(db::svc(c.db));
    c.block_keys.keys = keys; c.block_keys.nokey = false;
    return keys.size() == 1 ? R.word(keys[0]) : R.any;
}
bool peer_gone(int fd) { char b; ssize_t n = recv(fd, &b, 1, MSG_PEEK | MSG_DONTWAIT); return n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR); }
// park until the word moves past `seen` (read BEFORE the pop attempt, so nothing between is missed),
// the timeout passes, CLIENT UNBLOCK, or the peer hangs up. Sliced so the client watches its own connection.
bool block_on(Client& c, WaitWord& w, uint32_t seen, int64_t timeout_ms, std::chrono::steady_clock::time_point deadline) {
    if (c.in_exec) return false;                    // inside EXEC a blocking command is its non-blocking self
    c.waiting_on = &w; g.blocked_clients++;
    auto* bk = new Client::BlockKeys(c.block_keys); c.blocked_keys.store(bk, std::memory_order_release);
    bool ok = true;
    for (;;) {
        if (c.closing) break;                          // the peer hung up (seen by the event thread): leave now
        int64_t left = -1;
        if (timeout_ms > 0) { left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count(); if (left <= 0) { ok = false; break; } }
        int64_t slice = left < 0 ? 100 : std::min<int64_t>(left, 100);
        ebr::exit(); Region::wait(w, seen, slice); ebr::enter();     // parked: hold nobody's garbage
        if (w.gen.load(std::memory_order_acquire) != seen || c.unblock.load()) break;
        if (c.closing || peer_gone(c.fd)) { c.closing = true; break; }
    }
    g.blocked_clients--; c.waiting_on = nullptr;
    c.blocked_keys.store(nullptr, std::memory_order_release); ebr::retire(bk);
    return ok;
}
static bool list_ready(Client& c, const std::string& k, bool first, VarPtr& v, Reply& r, bool& err) {
    err = false;
    if (first) { int st = list_var(c, k, v, r); if (st < 0) { err = true; return false; } return st == 1; }
    v = db::live_var(c.db, k);                      // once parked, a key of another type is simply not ready
    return db::kind_of(v) == Kind::ListElem;
}
static void bpop_generic(Client& c, const Argv& a, Reply& r, bool left) {
    int64_t timeout; if (!parse_timeout(a.back(), timeout, r)) return;
    std::vector<std::string> keys(a.begin() + 1, a.end() - 1);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w;
    Claim claim(c, keys);
    bool first = true;
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        for (auto& k : keys) {
            VarPtr v; bool err; if (!list_ready(c, k, first, v, r, err)) { if (err) return; continue; }
            if (!claim.first_for(k)) continue;
            auto got = pop_end(c.db, k, left, 1);
            if (!got.empty()) { r.array(2); r.bulk(k); r.bulk(got[0]); return; }
        }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}
static void cmd_blpop(Client& c, const Argv& a, Reply& r) { bpop_generic(c, a, r, true); }
static void cmd_brpop(Client& c, const Argv& a, Reply& r) { bpop_generic(c, a, r, false); }
static void bmove_generic(Client& c, const std::string& src, const std::string& dst, bool from_left, bool to_left, const std::string& tstr, Reply& r) {
    int64_t timeout; if (!parse_timeout(tstr, timeout, r)) return;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    WaitReg wreg(wait_word_for(c, {src})); WaitWord& w = wreg.w;
    Claim claim(c, {src});
    bool first = true;
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        VarPtr sv, dv; bool err;
        bool ready = list_ready(c, src, first, sv, r, err); if (err) return;
        if (ready && claim.first_for(src)) {
            if (list_var(c, dst, dv, r) < 0) return;      // the destination's type matters only once there is something to move
            auto got = pop_end(c.db, src, from_left, 1);
            if (!got.empty()) { Argv pa{ to_left ? "lpush" : "rpush", dst, got[0] }; Reply tmp; tmp.proto = r.proto; push_generic(c, pa, tmp, to_left, false); r.bulk(got[0]); return; }
        }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}
static void cmd_brpoplpush(Client& c, const Argv& a, Reply& r) { bmove_generic(c, a[1], a[2], false, true, a[3], r); }
static void cmd_blmove(Client& c, const Argv& a, Reply& r) {
    std::string f = lower(a[3]), t = lower(a[4]);
    if ((f != "left" && f != "right") || (t != "left" && t != "right")) { err_syntax(r); return; }
    bmove_generic(c, a[1], a[2], f == "left", t == "left", a[5], r);
}
static void cmd_blmpop(Client& c, const Argv& a, Reply& r) {
    int64_t timeout; if (!parse_timeout(a[1], timeout, r)) return;
    long long numkeys; if (!string2ll(a[2], numkeys) || numkeys <= 0) { r.error("ERR numkeys should be greater than 0"); return; }
    if ((long long)a.size() < 3 + numkeys + 1) { err_syntax(r); return; }
    size_t j = 3 + (size_t)numkeys; std::string dir = lower(a[j]); bool left;
    if (dir == "left") left = true; else if (dir == "right") left = false; else { err_syntax(r); return; }
    long long count = 1; bool seen_count = false;
    for (j++; j < a.size(); j++) { if (str_eq_ci(a[j], "count") && j + 1 < a.size() && !seen_count) { seen_count = true; if (!string2ll(a[++j], count) || count <= 0) { r.error("ERR count should be greater than 0"); return; } } else { err_syntax(r); return; } }
    std::vector<std::string> keys(a.begin() + 3, a.begin() + 3 + numkeys);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w;
    Claim claim(c, keys);
    bool first = true;
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        for (auto& k : keys) {
            VarPtr v; bool err; if (!list_ready(c, k, first, v, r, err)) { if (err) return; continue; }
            if (!claim.first_for(k)) continue;
            auto got = pop_end(c.db, k, left, count);
            if (!got.empty()) { r.array(2); r.bulk(k); r.array(got.size()); for (auto& s : got) r.bulk(s); return; }
        }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}

void register_list_commands() {
    register_cmd("blpop", cmd_blpop); register_cmd("brpop", cmd_brpop); register_cmd("brpoplpush", cmd_brpoplpush); register_cmd("blmove", cmd_blmove); register_cmd("blmpop", cmd_blmpop);
    register_cmd("lpush", cmd_lpush); register_cmd("rpush", cmd_rpush); register_cmd("lpushx", cmd_lpushx); register_cmd("rpushx", cmd_rpushx);
    register_cmd("lpop", cmd_lpop); register_cmd("rpop", cmd_rpop); register_cmd("llen", cmd_llen); register_cmd("lindex", cmd_lindex);
    register_cmd("lrange", cmd_lrange); register_cmd("lset", cmd_lset); register_cmd("linsert", cmd_linsert); register_cmd("lrem", cmd_lrem);
    register_cmd("ltrim", cmd_ltrim); register_cmd("lpos", cmd_lpos); register_cmd("rpoplpush", cmd_rpoplpush); register_cmd("lmove", cmd_lmove);
    register_cmd("lmpop", cmd_lmpop);
}
