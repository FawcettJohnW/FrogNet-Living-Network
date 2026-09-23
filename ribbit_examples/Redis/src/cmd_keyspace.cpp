#include "server.hpp"
#include <climits>
#include <cerrno>

static void cmd_del(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i]) && db::del(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_exists(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_type(Client& c, const Argv& a, Reply& r) { r.status(db::type(c.db, a[1])); }
static void cmd_touch(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_keys(Client& c, const Argv& a, Reply& r) {
    bool all = a[1] == "*";
    std::vector<std::string> out;
    for (auto& k : db::keys(c.db)) if (all || stringmatch(a[1], k)) out.push_back(k);
    r.array(out.size()); for (auto& k : out) r.bulk(k);
}
static void cmd_randomkey(Client& c, const Argv&, Reply& r) {
    std::string k;
    if (db::randomkey(c.db, k)) r.bulk(k); else r.null();
}

static void rename_generic(Client& c, const Argv& a, Reply& r, bool nx) {
    bool same = a[1] == a[2];
    if (!db::exists(c.db, a[1])) { r.error("ERR no such key"); return; }
    if (same) { if (nx) r.integer(0); else r.ok(); return; }
    if (db::exists(c.db, a[2])) {
        if (nx) { r.integer(0); return; }
        db::del(c.db, a[2]);
    }
    // two-cell atomic move with one writer per cell: three memory steps. (question-3 candidate)
    db::copy_key(c.db, a[1], c.db, a[2]);
    db::del(c.db, a[1]);
    if (nx) r.integer(1); else r.ok();
}
static void cmd_rename(Client& c, const Argv& a, Reply& r) { rename_generic(c, a, r, false); }
static void cmd_renamenx(Client& c, const Argv& a, Reply& r) { rename_generic(c, a, r, true); }

static void cmd_copy(Client& c, const Argv& a, Reply& r) {
    int dst = c.db; bool replace = false;
    for (size_t j = 3; j < a.size(); j++) {
        size_t more = a.size() - j - 1;
        std::string o = lower(a[j]);
        if (o == "replace") replace = true;
        else if (o == "db" && more >= 1) {
            long long d; if (!string2ll(a[j+1], d)) { err_notint(r); return; }
            if (d < 0 || d >= g.databases) { r.error("ERR DB index is out of range"); return; }
            dst = (int)d; j++;
        } else { err_syntax(r); return; }
    }
    if (dst == c.db && a[1] == a[2]) { r.error("ERR source and destination objects are the same"); return; }
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    if (db::exists(dst, a[2])) { if (!replace) { r.integer(0); return; } db::del(dst, a[2]); }
    db::copy_key(c.db, a[1], dst, a[2]);
    r.integer(1);
}

static void cmd_move(Client& c, const Argv& a, Reply& r) {
    long long d;
    if (!string2ll(a[2], d)) { err_notint(r); return; }
    if (d < 0 || d >= g.databases) { r.error("ERR DB index is out of range"); return; }
    if ((int)d == c.db) { r.error("ERR source and destination objects are the same"); return; }
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    if (db::exists((int)d, a[1])) { r.integer(0); return; }
    db::copy_key(c.db, a[1], (int)d, a[1]);
    db::del(c.db, a[1]);
    r.integer(1);
}

// SCAN: the cursor is a creation (born) id in the memory's write order; keys that existed when the
// scan started are returned exactly once as long as they are not deleted or renamed.
static void cmd_scan(Client& c, const Argv& a, Reply& r) {
    errno = 0; char* ep = nullptr;
    unsigned long cursor = strtoul(a[1].c_str(), &ep, 10);
    if (isspace((unsigned char)a[1][0]) || *ep != '\0' || errno == ERANGE) { r.error("ERR invalid cursor"); return; }
    long long count = 10; std::string pattern; bool use_pat = false; std::string type;
    for (size_t i = 2; i < a.size(); i++) {
        size_t more = a.size() - i - 1; std::string o = lower(a[i]);
        if (o == "count" && more >= 1) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count < 1) { err_syntax(r); return; } i++; }
        else if (o == "match" && more >= 1) { pattern = a[i+1]; use_pat = !(pattern == "*"); i++; }
        else if (o == "type" && more >= 1) {
            type = lower(a[i+1]); i++;
            // 7.2 accepts any type name and matches nothing for an unknown one (the error arrives in 8.0)
        }
        else { err_syntax(r); return; }
    }
    uint64_t next = 0;
    std::vector<std::string> out;
    uint64_t cur = cursor;
    // walk in chunks until we have something to say or the keyspace is exhausted
    for (int rounds = 0; rounds < 1000; rounds++) {
        auto vars = g.mem.variables_after(db::svc(c.db), cur, (size_t)count, next);
        for (auto& k : vars) {
            if (!db::exists(c.db, k)) continue;
            if (use_pat && !stringmatch(pattern, k)) continue;
            if (!type.empty() && db::type(c.db, k) != type) continue;
            out.push_back(k);
        }
        if (next == 0 || !out.empty()) break;
        cur = next;
    }
    r.array(2);
    r.bulk(std::to_string(next));
    r.array(out.size()); for (auto& k : out) r.bulk(k);
}

// ---------------------------------------------------------------- expiry
static void expire_generic(Client& c, const Argv& a, Reply& r, long long basetime, bool seconds) {
    int nx = 0, xx = 0, gt = 0, lt = 0;
    for (size_t j = 3; j < a.size(); j++) {
        std::string o = lower(a[j]);
        if (o == "nx") nx = 1; else if (o == "xx") xx = 1; else if (o == "gt") gt = 1; else if (o == "lt") lt = 1;
        else { r.error("ERR Unsupported option " + a[j]); return; }
    }
    if ((nx && xx) || (nx && gt) || (nx && lt)) { r.error("ERR NX and XX, GT or LT options at the same time are not compatible"); return; }
    if (gt && lt) { r.error("ERR GT and LT options at the same time are not compatible"); return; }
    long long when;
    if (!string2ll(a[2], when)) { err_notint(r); return; }
    std::string cmdname = lower(a[0]);
    if (seconds) { if (when > LLONG_MAX / 1000 || when < LLONG_MIN / 1000) { r.error("ERR invalid expire time in '" + cmdname + "' command"); return; } when *= 1000; }
    if (when > LLONG_MAX - basetime) { r.error("ERR invalid expire time in '" + cmdname + "' command"); return; }
    when += basetime;
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    if (nx || xx || gt || lt) {
        int64_t cur = db::expire_at(c.db, a[1]);
        if (nx && cur != -1) { r.integer(0); return; }
        if (xx && cur == -1) { r.integer(0); return; }
        if (gt && (when <= cur || cur == -1)) { r.integer(0); return; }
        if (lt && cur != -1 && when >= cur) { r.integer(0); return; }
    }
    if (when <= Memory::now_ms()) { db::del(c.db, a[1]); r.integer(1); return; }
    db::set_expire(c.db, a[1], when);
    r.integer(1);
}
static void cmd_expire(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, Memory::now_ms(), true); }
static void cmd_pexpire(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, Memory::now_ms(), false); }
static void cmd_expireat(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, 0, true); }
static void cmd_pexpireat(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, 0, false); }

static void ttl_generic(Client& c, const Argv& a, Reply& r, bool ms, bool abs) {
    int64_t e = db::expire_at(c.db, a[1]);
    if (e == -2) { r.integer(-2); return; }
    if (e == -1) { r.integer(-1); return; }
    long long ttl = abs ? e : e - Memory::now_ms();
    if (ttl < 0) ttl = 0;
    r.integer(ms ? ttl : (ttl + 500) / 1000);
}
static void cmd_ttl(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, false, false); }
static void cmd_pttl(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, true, false); }
static void cmd_expiretime(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, false, true); }
static void cmd_pexpiretime(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, true, true); }
static void cmd_persist(Client& c, const Argv& a, Reply& r) {
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    r.integer(db::persist(c.db, a[1]) ? 1 : 0);
}

void register_keyspace_commands() {
    register_cmd("del", cmd_del); register_cmd("unlink", cmd_del); register_cmd("exists", cmd_exists); register_cmd("type", cmd_type);
    register_cmd("touch", cmd_touch); register_cmd("keys", cmd_keys); register_cmd("randomkey", cmd_randomkey);
    register_cmd("rename", cmd_rename); register_cmd("renamenx", cmd_renamenx); register_cmd("copy", cmd_copy); register_cmd("move", cmd_move);
    register_cmd("scan", cmd_scan);
    register_cmd("expire", cmd_expire); register_cmd("pexpire", cmd_pexpire); register_cmd("expireat", cmd_expireat); register_cmd("pexpireat", cmd_pexpireat);
    register_cmd("ttl", cmd_ttl); register_cmd("pttl", cmd_pttl); register_cmd("expiretime", cmd_expiretime); register_cmd("pexpiretime", cmd_pexpiretime);
    register_cmd("persist", cmd_persist);
}
