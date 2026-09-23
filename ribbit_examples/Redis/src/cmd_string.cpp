#include "server.hpp"
#include <climits>
#include <cmath>
#include <cstring>

// Read a string key. Returns: 0 absent, 1 string, -1 wrong type (error already sent).
static int read_str(const db::Key& k, std::string& out, Reply& r) {
    if (k.k == Kind::None) { g.stats.misses++; return 0; }
    g.stats.hits++;
    if (k.k != Kind::String) { err_wrongtype(r); return -1; }
    out = k.val()->bag->s; return 1;
}
static int read_str(Client& c, const std::string& key, std::string& out, Reply& r) { db::Key k(c.db, key); return read_str(k, out, r); }
static int64_t max_bulk() { return g.max_bulk.load(std::memory_order_relaxed); }
static bool check_len(long long size, long long append, Reply& r) {
    long long total = (long long)((uint64_t)size + (uint64_t)append);
    if (total > max_bulk() || total < size || total < append) { r.error("ERR string exceeds maximum allowed size (proto-max-bulk-len)"); return false; }
    return true;
}

// ---------------------------------------------------------------- SET / GET family
enum { F_NX = 1, F_XX = 2, F_GET = 4, F_KEEPTTL = 8, F_PERSIST = 16, F_EX = 32, F_PX = 64, F_EXAT = 128, F_PXAT = 256 };

static bool parse_ext(const Argv& a, size_t from, bool is_set, int& flags, std::string& expire, Reply& r) {
    for (size_t j = from; j < a.size(); j++) {
        std::string o = lower(a[j]); bool next = j + 1 < a.size();
        if (o == "nx" && !(flags & F_XX) && is_set) flags |= F_NX;
        else if (o == "xx" && !(flags & F_NX) && is_set) flags |= F_XX;
        else if (o == "get" && is_set) flags |= F_GET;
        else if (o == "keepttl" && !(flags & (F_PERSIST | F_EX | F_EXAT | F_PX | F_PXAT)) && is_set) flags |= F_KEEPTTL;
        else if (o == "persist" && !is_set && !(flags & (F_EX | F_EXAT | F_PX | F_PXAT | F_KEEPTTL))) flags |= F_PERSIST;
        else if (o == "ex" && !(flags & (F_KEEPTTL | F_PERSIST | F_EXAT | F_PX | F_PXAT)) && next) { flags |= F_EX; expire = a[++j]; }
        else if (o == "px" && !(flags & (F_KEEPTTL | F_PERSIST | F_EX | F_EXAT | F_PXAT)) && next) { flags |= F_PX; expire = a[++j]; }
        else if (o == "exat" && !(flags & (F_KEEPTTL | F_PERSIST | F_EX | F_PX | F_PXAT)) && next) { flags |= F_EXAT; expire = a[++j]; }
        else if (o == "pxat" && !(flags & (F_KEEPTTL | F_PERSIST | F_EX | F_EXAT | F_PX)) && next) { flags |= F_PXAT; expire = a[++j]; }
        else { err_syntax(r); return false; }
    }
    return true;
}
static bool expire_ms(const std::string& s, int flags, const std::string& cmd, long long& ms, Reply& r) {
    if (!string2ll(s, ms)) { err_notint(r); return false; }
    bool secs = flags & (F_EX | F_EXAT);
    if (ms <= 0 || (secs && ms > LLONG_MAX / 1000)) { r.error("ERR invalid expire time in '" + cmd + "' command"); return false; }
    if (secs) ms *= 1000;
    if (flags & (F_EX | F_PX)) ms += Memory::now_ms();
    if (ms <= 0) { r.error("ERR invalid expire time in '" + cmd + "' command"); return false; }
    return true;
}

static void set_generic(Client& c, const std::string& key, const std::string& val, int flags, long long ex_ms, Reply& r, const char* okreply) {
    db::Key kk(c.db, key); Kind k = kk.k; bool found = k != Kind::None;
    if ((flags & F_GET) && found && k != Kind::String) { err_wrongtype(r); return; }
    auto oldval = [&]() { if (found) r.bulk(kk.val()->bag->s); else r.null(); };
    if (((flags & F_NX) && found) || ((flags & F_XX) && !found)) {
        if (!(flags & F_GET)) r.null(); else oldval();
        return;
    }
    if (flags & F_GET) oldval();
    kk.set_string(val, (flags & F_KEEPTTL) != 0);
    if (ex_ms) db::set_expire(c.db, key, ex_ms);
    if (!(flags & F_GET)) r.status(okreply);
}

static void cmd_set(Client& c, const Argv& a, Reply& r) {
    int flags = 0; std::string exp; long long ms = 0;
    if (!parse_ext(a, 3, true, flags, exp, r)) return;
    if (!exp.empty() && !expire_ms(exp, flags, "set", ms, r)) return;
    set_generic(c, a[1], a[2], flags, ms, r, "OK");
}
static void cmd_setnx(Client& c, const Argv& a, Reply& r) {
    if (db::exists(c.db, a[1])) { r.integer(0); return; }
    db::set_string(c.db, a[1], a[2], false); r.integer(1);
}
static void cmd_setex(Client& c, const Argv& a, Reply& r) {
    long long ms; if (!expire_ms(a[2], F_EX, "setex", ms, r)) return;
    db::set_string(c.db, a[1], a[3], false); db::set_expire(c.db, a[1], ms); r.ok();
}
static void cmd_psetex(Client& c, const Argv& a, Reply& r) {
    long long ms; if (!expire_ms(a[2], F_PX, "psetex", ms, r)) return;
    db::set_string(c.db, a[1], a[3], false); db::set_expire(c.db, a[1], ms); r.ok();
}
static void cmd_get(Client& c, const Argv& a, Reply& r) {
    db::Key k(c.db, a[1]);
    if (k.k == Kind::None) { g.stats.misses++; r.null(); return; }
    g.stats.hits++;
    if (k.k != Kind::String) { err_wrongtype(r); return; }
    r.bulk(k.val()->bag->s);                       // straight from the cell into the reply
}
static void cmd_getex(Client& c, const Argv& a, Reply& r) {
    int flags = 0; std::string exp; long long ms = 0;
    if (!parse_ext(a, 2, false, flags, exp, r)) return;
    if (!exp.empty() && !expire_ms(exp, flags, "getex", ms, r)) return;
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) { r.null(); return; }
    r.bulk(v);
    if (ms) { if (ms <= Memory::now_ms()) db::del(c.db, a[1]); else db::set_expire(c.db, a[1], ms); }
    else if (flags & F_PERSIST) db::persist(c.db, a[1]);
}
static void cmd_getdel(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) { r.null(); return; }
    r.bulk(v); db::del(c.db, a[1]);
}
static void cmd_getset(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) r.null(); else r.bulk(v);
    db::set_string(c.db, a[1], a[2], false);
}
static void cmd_mget(Client& c, const Argv& a, Reply& r) {
    r.array(a.size() - 1);
    Memory::Snapshot snap = g.mem.snapshot(db::svc(c.db));          // one instant for every key
    for (size_t i = 1; i < a.size(); i++) {
        VarPtr v = db::live_var(snap, c.db, a[i]);
        if (db::kind_of(v) == Kind::String) { g.stats.hits++; r.bulk(Memory::inst(v, db::VAL)->bag->s); } else { g.stats.misses++; r.null(); }
    }
}
static void cmd_mset(Client& c, const Argv& a, Reply& r) {
    if (a.size() % 2 == 0) { err_arity(r, "mset"); return; }
    std::vector<std::pair<std::string, std::string>> kvs;
    for (size_t i = 1; i < a.size(); i += 2) kvs.emplace_back(a[i], a[i+1]);
    db::set_strings(c.db, kvs);            // one publish, not N
    r.ok();
}
static void cmd_msetnx(Client& c, const Argv& a, Reply& r) {
    if (a.size() % 2 == 0) { err_arity(r, "msetnx"); return; }
    for (size_t i = 1; i < a.size(); i += 2) if (db::exists(c.db, a[i])) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> kvs;
    for (size_t i = 1; i < a.size(); i += 2) kvs.emplace_back(a[i], a[i+1]);
    db::set_strings(c.db, kvs);            // the check and the write are still two steps: question-3 candidate
    r.integer(1);
}
static void cmd_strlen(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    r.integer(st == 0 ? 0 : (long long)v.size());
}
static void cmd_append(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) { db::set_string(c.db, a[1], a[2], true); r.integer((long long)a[2].size()); return; }
    if (!check_len((long long)v.size(), (long long)a[2].size(), r)) return;
    v += a[2]; db::set_string(c.db, a[1], v, true); r.integer((long long)v.size());
}
static void cmd_getrange(Client& c, const Argv& a, Reply& r) {
    long long start, end;
    if (!string2ll(a[2], start) || !string2ll(a[3], end)) { err_notint(r); return; }
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) { r.bulk(""); return; }
    long long len = (long long)v.size();
    if (start < 0 && end < 0 && start > end) { r.bulk(""); return; }
    if (start < 0) start = len + start;
    if (end < 0) end = len + end;
    if (start < 0) start = 0;
    if (end < 0) end = 0;
    if (end >= len) end = len - 1;
    if (start > end || len == 0) r.bulk(""); else r.bulk(v.substr((size_t)start, (size_t)(end - start + 1)));
}
static void cmd_setrange(Client& c, const Argv& a, Reply& r) {
    long long off;
    if (!string2ll(a[2], off)) { err_notint(r); return; }
    if (off < 0) { r.error("ERR offset is out of range"); return; }
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st == 0) {
        if (a[3].empty()) { r.integer(0); return; }
        if (!check_len(off, (long long)a[3].size(), r)) return;
        v.assign((size_t)off, '\0'); v += a[3];
        db::set_string(c.db, a[1], v, true); r.integer((long long)v.size()); return;
    }
    if (a[3].empty()) { r.integer((long long)v.size()); return; }
    if (!check_len(off, (long long)a[3].size(), r)) return;
    if (v.size() < (size_t)off + a[3].size()) v.resize((size_t)off + a[3].size(), '\0');
    memcpy(&v[(size_t)off], a[3].data(), a[3].size());
    db::set_string(c.db, a[1], v, true); r.integer((long long)v.size());
}

static void cmd_lcs(Client& c, const Argv& a, Reply& r) {
    long long minmatch = 0; bool getlen = false, getidx = false, withlen = false;
    for (size_t j = 3; j < a.size(); j++) {
        std::string o = lower(a[j]); bool more = j + 1 < a.size();
        if (o == "idx") getidx = true; else if (o == "len") getlen = true; else if (o == "withmatchlen") withlen = true;
        else if (o == "minmatchlen" && more) { if (!string2ll(a[j+1], minmatch)) { err_notint(r); return; } if (minmatch < 0) minmatch = 0; j++; }
        else { err_syntax(r); return; }
    }
    if (getlen && getidx) { r.error("ERR If you want both the length and indexes, please just use IDX."); return; }
    std::string A, B; int s1 = read_str(c, a[1], A, r); if (s1 < 0) return; int s2 = read_str(c, a[2], B, r); if (s2 < 0) return;
    uint32_t alen = (uint32_t)A.size(), blen = (uint32_t)B.size();
    if ((uint64_t)alen * blen > 0xFFFFFFFFULL / 4) { r.error("ERR String too long for LCS"); return; }
    std::vector<uint32_t> lcs((size_t)(alen + 1) * (blen + 1), 0);
    auto L = [&](uint32_t i, uint32_t j) -> uint32_t& { return lcs[(size_t)i * (blen + 1) + j]; };
    for (uint32_t i = 1; i <= alen; i++) for (uint32_t j = 1; j <= blen; j++) {
        if (A[i-1] == B[j-1]) L(i, j) = L(i-1, j-1) + 1;
        else L(i, j) = std::max(L(i-1, j), L(i, j-1));
    }
    uint32_t idx = L(alen, blen);
    std::string result; bool compute = getidx || !getlen;
    if (compute) result.assign(idx, '\0');
    Reply matches; matches.proto = r.proto; uint32_t arraylen = 0;
    uint32_t i = alen, j = blen, arange_start = alen, arange_end = 0, brange_start = 0, brange_end = 0;
    while (compute && i > 0 && j > 0) {
        bool emit = false;
        if (A[i-1] == B[j-1]) {
            result[idx-1] = A[i-1];
            if (arange_start == alen) { arange_start = i-1; arange_end = i-1; brange_start = j-1; brange_end = j-1; }
            else { if (arange_start == i && brange_start == j) { arange_start--; brange_start--; } else emit = true; }
            if (arange_start == 0 || brange_start == 0) emit = true;
            idx--; i--; j--;
        } else {
            if (L(i-1, j) > L(i, j-1)) i--; else j--;
            if (arange_start != alen) emit = true;
        }
        uint32_t mlen = arange_end - arange_start + 1;
        if (emit) {
            if ((minmatch == 0 || mlen >= (uint32_t)minmatch) && getidx) {
                matches.array(2 + (withlen ? 1 : 0));
                matches.array(2); matches.integer(arange_start); matches.integer(arange_end);
                matches.array(2); matches.integer(brange_start); matches.integer(brange_end);
                if (withlen) matches.integer(mlen);
                arraylen++;
            }
            arange_start = alen;
        }
    }
    if (getidx) { r.map(2); r.bulk("matches"); r.array(arraylen); r.out += matches.out; r.bulk("len"); r.integer(L(alen, blen)); }
    else if (getlen) r.integer(L(alen, blen));
    else r.bulk(result);
}

// ---------------------------------------------------------------- INCR family (read-modify-write: question-3 candidate)
static void incr_generic(Client& c, const std::string& key, long long incr, Reply& r) {
    db::Key k(c.db, key);
    std::string v; int st = read_str(k, v, r);
    if (st < 0) return;
    long long value = 0;
    if (st > 0 && !string2ll(v, value)) { err_notint(r); return; }
    if ((incr < 0 && value < 0 && incr < (LLONG_MIN - value)) || (incr > 0 && value > 0 && incr > (LLONG_MAX - value))) { r.error("ERR increment or decrement would overflow"); return; }
    value += incr;
    k.set_string(ll2string(value), true);
    r.integer(value);
}
static void cmd_incr(Client& c, const Argv& a, Reply& r) { incr_generic(c, a[1], 1, r); }
static void cmd_decr(Client& c, const Argv& a, Reply& r) { incr_generic(c, a[1], -1, r); }
static void cmd_incrby(Client& c, const Argv& a, Reply& r) { long long n; if (!string2ll(a[2], n)) { err_notint(r); return; } incr_generic(c, a[1], n, r); }
static void cmd_decrby(Client& c, const Argv& a, Reply& r) {
    long long n; if (!string2ll(a[2], n)) { err_notint(r); return; }
    if (n == LLONG_MIN) { r.error("ERR decrement would overflow"); return; }
    incr_generic(c, a[1], -n, r);
}
static void cmd_incrbyfloat(Client& c, const Argv& a, Reply& r) {
    long double incr, value = 0;
    if (!string2ld(a[2], incr)) { err_notfloat(r); return; }
    std::string v; int st = read_str(c, a[1], v, r);
    if (st < 0) return;
    if (st > 0 && !string2ld(v, value)) { err_notfloat(r); return; }
    value += incr;
    if (std::isnan(value) || std::isinf(value)) { r.error("ERR increment would produce NaN or Infinity"); return; }
    std::string s = ld2string_human(value);
    db::set_string(c.db, a[1], s, true);
    r.bulk(s);
}

// ---------------------------------------------------------------- bits
static bool bit_offset(const std::string& s, long long& off, Reply& r, bool hash = false, int bits = 0) {
    const char* err = "ERR bit offset is not an integer or out of range";
    bool usehash = hash && bits > 0 && !s.empty() && s[0] == '#';
    long long v;
    if (!string2ll(usehash ? s.substr(1) : s, v)) { r.error(err); return false; }
    if (usehash) v *= bits;
    if (v < 0 || (v >> 3) >= max_bulk()) { r.error(err); return false; }
    off = v; return true;
}
static void cmd_setbit(Client& c, const Argv& a, Reply& r) {
    long long off; if (!bit_offset(a[2], off, r)) return;
    long long on; if (!string2ll(a[3], on) || (on & ~1LL)) { r.error("ERR bit is not an integer or out of range"); return; }
    std::string v; int st = read_str(c, a[1], v, r); if (st < 0) return;
    size_t byte = (size_t)(off >> 3); size_t oldlen = v.size();
    if (v.size() < byte + 1) v.resize(byte + 1, '\0');
    int bit = 7 - (int)(off & 7);
    int old = ((unsigned char)v[byte] >> bit) & 1;
    unsigned char b = (unsigned char)v[byte]; b &= ~(1 << bit); b |= (unsigned char)((on & 1) << bit); v[byte] = (char)b;
    if (st == 0 || v.size() != oldlen || old != (on & 1)) db::set_string(c.db, a[1], v, true);   // nothing changed: nothing written
    r.integer(old);
}
static void cmd_getbit(Client& c, const Argv& a, Reply& r) {
    long long off; if (!bit_offset(a[2], off, r)) return;
    std::string v; int st = read_str(c, a[1], v, r); if (st < 0) return;
    size_t byte = (size_t)(off >> 3);
    if (byte >= v.size()) { r.integer(0); return; }
    r.integer(((unsigned char)v[byte] >> (7 - (off & 7))) & 1);
}
static int popcount8(unsigned char b) { return __builtin_popcount(b); }
static void cmd_bitcount(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = read_str(c, a[1], v, r); if (st < 0) return;
    if (st == 0) { if (a.size() != 2 && a.size() != 4 && a.size() != 5) { err_syntax(r); return; } r.integer(0); return; }
    long long start, end, len = (long long)v.size(); bool isbit = false;
    unsigned char firstmask = 0, lastmask = 0;
    if (a.size() == 4 || a.size() == 5) {
        long long tot = len;
        if (!string2ll(a[2], start) || !string2ll(a[3], end)) { err_notint(r); return; }
        if (start < 0 && end < 0 && start > end) { r.integer(0); return; }
        if (a.size() == 5) { std::string m = lower(a[4]); if (m == "bit") isbit = true; else if (m == "byte") isbit = false; else { err_syntax(r); return; } }
        if (isbit) tot <<= 3;
        if (start < 0) start = tot + start;
        if (end < 0) end = tot + end;
        if (start < 0) start = 0;
        if (end < 0) end = 0;
        if (end >= tot) end = tot - 1;
        if (isbit && start <= end) {
            firstmask = ~((1 << (8 - (start & 7))) - 1) & 0xFF;
            lastmask = (1 << (7 - (end & 7))) - 1;
            start >>= 3; end >>= 3;
        }
    } else if (a.size() == 2) { start = 0; end = len - 1; }
    else { err_syntax(r); return; }
    if (start > end) { r.integer(0); return; }
    long long count = 0;
    for (long long i = start; i <= end; i++) count += popcount8((unsigned char)v[(size_t)i]);
    if (firstmask) count -= popcount8((unsigned char)v[(size_t)start] & firstmask);
    if (lastmask) count -= popcount8((unsigned char)v[(size_t)end] & lastmask);
    r.integer(count);
}
static void cmd_bitpos(Client& c, const Argv& a, Reply& r) {
    long long bit;
    if (!string2ll(a[2], bit)) { err_notint(r); return; }
    if (bit != 0 && bit != 1) { r.error("ERR The bit argument must be 1 or 0."); return; }
    std::string v; int st = read_str(c, a[1], v, r); if (st < 0) return;
    if (st == 0) { r.integer(bit ? -1 : 0); return; }
    long long len = (long long)v.size(), start, end; bool isbit = false, end_given = false;
    if (a.size() >= 4 && a.size() <= 6) {
        long long tot = len;
        if (!string2ll(a[3], start)) { err_notint(r); return; }
        if (a.size() == 6) { std::string m = lower(a[5]); if (m == "bit") isbit = true; else if (m == "byte") isbit = false; else { err_syntax(r); return; } }
        if (a.size() >= 5) { if (!string2ll(a[4], end)) { err_notint(r); return; } end_given = true; }
        else end = isbit ? (tot << 3) + 7 : tot - 1;
        if (isbit) tot <<= 3;
        if (start < 0) start = tot + start;
        if (end < 0) end = tot + end;
        if (start < 0) start = 0;
        if (end < 0) end = 0;
        if (end >= tot) end = tot - 1;
        if (!isbit) { start <<= 3; end = (end << 3) + 7; }
    } else if (a.size() == 3) { start = 0; end = (len << 3) - 1; }
    else { err_syntax(r); return; }
    if (start > end) { r.integer(-1); return; }
    long long totbits = len << 3;
    for (long long p = start; p <= end && p < totbits; p++) {
        int b = ((unsigned char)v[(size_t)(p >> 3)] >> (7 - (p & 7))) & 1;
        if (b == bit) { r.integer(p); return; }
    }
    // not found in range
    if (bit == 0 && !end_given) { r.integer(end + 1 > totbits ? totbits : end + 1); return; }
    r.integer(-1);
}
static void cmd_bitop(Client& c, const Argv& a, Reply& r) {
    std::string op = lower(a[1]);
    int kind; if (op == "and") kind = 0; else if (op == "or") kind = 1; else if (op == "xor") kind = 2; else if (op == "not") kind = 3; else { err_syntax(r); return; }
    if (kind == 3 && a.size() != 4) { r.error("ERR BITOP NOT must be called with a single source key."); return; }
    std::vector<std::string> src; size_t maxlen = 0;
    for (size_t i = 3; i < a.size(); i++) { std::string v; int st = read_str(c, a[i], v, r); if (st < 0) return; src.push_back(v); maxlen = std::max(maxlen, v.size()); }
    std::string res(maxlen, '\0');
    for (size_t j = 0; j < maxlen; j++) {
        unsigned char out = kind == 0 ? 0xFF : 0;
        for (size_t s = 0; s < src.size(); s++) {
            unsigned char b = j < src[s].size() ? (unsigned char)src[s][j] : 0;
            if (kind == 0) out &= b; else if (kind == 1) out |= b; else if (kind == 2) out ^= b; else out = ~b;
        }
        res[j] = (char)out;
    }
    if (maxlen == 0) db::del(c.db, a[2]); else db::set_string(c.db, a[2], res, false);
    r.integer((long long)maxlen);
}

// BITFIELD
static uint64_t bf_get_unsigned(const std::string& v, uint64_t offset, uint64_t bits) {
    uint64_t value = 0;
    for (uint64_t j = 0; j < bits; j++) {
        uint64_t pos = offset + j; uint64_t byte = pos >> 3; int bit = 7 - (int)(pos & 7);
        uint64_t b = byte < v.size() ? (((unsigned char)v[byte] >> bit) & 1) : 0;
        value = (value << 1) | b;
    }
    return value;
}
static int64_t bf_get_signed(const std::string& v, uint64_t offset, uint64_t bits) {
    uint64_t u = bf_get_unsigned(v, offset, bits);
    if (bits < 64 && (u & ((uint64_t)1 << (bits - 1)))) u |= ~(((uint64_t)1 << bits) - 1);
    return (int64_t)u;
}
static void bf_set(std::string& v, uint64_t offset, uint64_t bits, uint64_t value) {
    size_t need = (size_t)((offset + bits + 7) >> 3);
    if (v.size() < need) v.resize(need, '\0');
    for (uint64_t j = 0; j < bits; j++) {
        uint64_t pos = offset + j; size_t byte = (size_t)(pos >> 3); int bit = 7 - (int)(pos & 7);
        uint64_t bv = (value >> (bits - 1 - j)) & 1;
        unsigned char b = (unsigned char)v[byte]; b &= ~(1 << bit); b |= (unsigned char)(bv << bit); v[byte] = (char)b;
    }
}
// overflow checks from bitops.c. Returns 0 no overflow, 1 overflow up, -1 down; *limit set for wrap/sat
static int bf_signed_overflow(int64_t value, int64_t incr, uint64_t bits, int owtype, int64_t* limit) {
    int64_t max = (bits == 64) ? INT64_MAX : (((int64_t)1 << (bits - 1)) - 1);
    int64_t min = -max - 1;
    int64_t maxincr = max - value, minincr = min - value;
    if (value > max || (bits != 64 && incr > maxincr) || (value >= 0 && incr > 0 && incr > maxincr)) {
        if (limit) {
            if (owtype == 0) { // wrap
                uint64_t msb = (uint64_t)1 << (bits - 1); uint64_t mask = ((uint64_t)-1) << bits; uint64_t cc = (uint64_t)value + (uint64_t)incr;
                if (bits < 64) { cc &= ~mask; if (cc & msb) cc |= mask; }
                *limit = (int64_t)cc;
            } else *limit = max;
        }
        return 1;
    } else if (value < min || (bits != 64 && incr < minincr) || (value < 0 && incr < 0 && incr < minincr)) {
        if (limit) {
            if (owtype == 0) {
                uint64_t msb = (uint64_t)1 << (bits - 1); uint64_t mask = ((uint64_t)-1) << bits; uint64_t cc = (uint64_t)value + (uint64_t)incr;
                if (bits < 64) { cc &= ~mask; if (cc & msb) cc |= mask; }
                *limit = (int64_t)cc;
            } else *limit = min;
        }
        return -1;
    }
    return 0;
}
static int bf_unsigned_overflow(uint64_t value, int64_t incr, uint64_t bits, int owtype, uint64_t* limit) {
    uint64_t max = (bits == 64) ? UINT64_MAX : (((uint64_t)1 << bits) - 1);
    int64_t maxincr = (int64_t)(max - value), minincr = -(int64_t)value;
    if (value > max || (incr > 0 && incr > maxincr)) {
        if (limit) { if (owtype == 0) { uint64_t mask = ((uint64_t)-1) << bits; uint64_t cc = value + (uint64_t)incr; if (bits < 64) cc &= ~mask; *limit = cc; } else *limit = max; }
        return 1;
    } else if (incr < 0 && incr < minincr) {
        if (limit) { if (owtype == 0) { uint64_t mask = ((uint64_t)-1) << bits; uint64_t cc = value + (uint64_t)incr; if (bits < 64) cc &= ~mask; *limit = cc; } else *limit = 0; }
        return -1;
    }
    return 0;
}
static bool bf_type(const std::string& s, int& sign, int& bits, Reply& r) {
    const char* err = "ERR Invalid bitfield type. Use something like i16 u8. Note that u64 is not supported but i64 is.";
    if (s.empty()) { r.error(err); return false; }
    if (s[0] == 'i') sign = 1; else if (s[0] == 'u') sign = 0; else { r.error(err); return false; }
    long long b; if (!string2ll(s.substr(1), b) || b < 1 || (sign && b > 64) || (!sign && b > 63)) { r.error(err); return false; }
    bits = (int)b; return true;
}
struct BfOp { int op; int sign; int bits; long long offset; long long value; int owtype; };
static void bitfield_generic(Client& c, const Argv& a, Reply& r, bool readonly) {
    std::vector<BfOp> ops; int owtype = 0; bool has_write = false;
    for (size_t j = 2; j < a.size(); j++) {
        std::string o = lower(a[j]); size_t rem = a.size() - j - 1;
        int op, need;
        if (o == "get" && rem >= 2) { op = 0; need = 2; }
        else if (o == "set" && rem >= 3) { op = 1; need = 3; }
        else if (o == "incrby" && rem >= 3) { op = 2; need = 3; }
        else if (o == "overflow" && rem >= 1) {
            std::string t = lower(a[j+1]);
            if (t == "wrap") owtype = 0; else if (t == "sat") owtype = 1; else if (t == "fail") owtype = 2; else { r.error("ERR Invalid OVERFLOW type specified"); return; }
            j++; continue;
        } else { err_syntax(r); return; }
        BfOp b; b.op = op; b.owtype = owtype; b.value = 0;
        if (!bf_type(a[j+1], b.sign, b.bits, r)) return;
        if (!bit_offset(a[j+2], b.offset, r, true, b.bits)) return;
        if (need == 3) { if (!string2ll(a[j+3], b.value)) { err_notint(r); return; } has_write = true; }
        ops.push_back(b); j += need;
    }
    if (readonly && has_write) { r.error("ERR BITFIELD_RO only supports the GET subcommand"); return; }
    std::string v; int st = read_str(c, a[1], v, r); if (st < 0) return;
    const std::string before = v;
    bool changed = false, force_write = false;
    r.array(ops.size());
    for (auto& b : ops) {
        if (b.op == 2) force_write = true;      // INCRBY is always a write, as in Redis
        if (b.op == 0) {
            if (b.sign) r.integer(bf_get_signed(v, (uint64_t)b.offset, (uint64_t)b.bits));
            else r.integer((long long)bf_get_unsigned(v, (uint64_t)b.offset, (uint64_t)b.bits));
            continue;
        }
        if (b.sign) {
            int64_t oldv = bf_get_signed(v, (uint64_t)b.offset, (uint64_t)b.bits);
            int64_t newv = b.op == 1 ? b.value : oldv + b.value;
            int64_t wrapped = 0; int ow;
            if (b.op == 1) ow = bf_signed_overflow(b.value, 0, (uint64_t)b.bits, b.owtype, &wrapped);
            else ow = bf_signed_overflow(oldv, b.value, (uint64_t)b.bits, b.owtype, &wrapped);
            if (ow && b.owtype == 2) { r.null(); continue; }
            if (ow) newv = wrapped;
            bf_set(v, (uint64_t)b.offset, (uint64_t)b.bits, (uint64_t)newv); changed = true;
            r.integer(b.op == 1 ? oldv : newv);
        } else {
            uint64_t oldv = bf_get_unsigned(v, (uint64_t)b.offset, (uint64_t)b.bits);
            uint64_t newv = b.op == 1 ? (uint64_t)b.value : oldv + (uint64_t)b.value;
            uint64_t wrapped = 0; int ow;
            if (b.op == 1) ow = bf_unsigned_overflow((uint64_t)b.value, 0, (uint64_t)b.bits, b.owtype, &wrapped);
            else ow = bf_unsigned_overflow(oldv, b.value, (uint64_t)b.bits, b.owtype, &wrapped);
            if (ow && b.owtype == 2) { r.null(); continue; }
            if (ow) newv = wrapped;
            bf_set(v, (uint64_t)b.offset, (uint64_t)b.bits, newv); changed = true;
            r.integer((long long)(b.op == 1 ? oldv : newv));
        }
    }
    if (changed && (st == 0 || force_write || v != before)) db::set_string(c.db, a[1], v, true);
}
static void cmd_bitfield(Client& c, const Argv& a, Reply& r) { bitfield_generic(c, a, r, false); }
static void cmd_bitfield_ro(Client& c, const Argv& a, Reply& r) { bitfield_generic(c, a, r, true); }

void register_string_commands() {
    register_cmd("set", cmd_set); register_cmd("setnx", cmd_setnx); register_cmd("setex", cmd_setex); register_cmd("psetex", cmd_psetex);
    register_cmd("get", cmd_get); register_cmd("getex", cmd_getex); register_cmd("getdel", cmd_getdel); register_cmd("getset", cmd_getset);
    register_cmd("mget", cmd_mget); register_cmd("mset", cmd_mset); register_cmd("msetnx", cmd_msetnx); register_cmd("strlen", cmd_strlen);
    register_cmd("append", cmd_append); register_cmd("getrange", cmd_getrange); register_cmd("substr", cmd_getrange); register_cmd("setrange", cmd_setrange);
    register_cmd("lcs", cmd_lcs);
    register_cmd("incr", cmd_incr); register_cmd("decr", cmd_decr); register_cmd("incrby", cmd_incrby); register_cmd("decrby", cmd_decrby); register_cmd("incrbyfloat", cmd_incrbyfloat);
    register_cmd("setbit", cmd_setbit); register_cmd("getbit", cmd_getbit); register_cmd("bitcount", cmd_bitcount); register_cmd("bitpos", cmd_bitpos);
    register_cmd("bitop", cmd_bitop); register_cmd("bitfield", cmd_bitfield); register_cmd("bitfield_ro", cmd_bitfield_ro);
}
