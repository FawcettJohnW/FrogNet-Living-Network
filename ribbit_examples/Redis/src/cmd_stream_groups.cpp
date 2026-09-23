// Consumer groups. Everything is a cell under the stream's key, addressed so the relationships are
// the addresses:
//   "\x04" + group                       the group's truth: last-delivered id, entries-read
//   "\x05" + group + "\0" + id16         a pending entry: who holds it, since when, how many deliveries
//   "\x06" + group + "\0" + consumer     the consumer's truth: seen-time, active-time
// The PEL of a group is a range read under its prefix; a consumer's PEL is the same range filtered by
// owner; XACK is remove(); XCLAIM rewrites the owner; XAUTOCLAIM walks the range. Nothing here keeps
// a count -- pending, lag and idle are derived at read time.
#include "stream.hpp"

static const char G_PFX = '\x04', P_PFX = '\x05', C_PFX = '\x06';
static std::string g_inst(const std::string& g) { return std::string(1, G_PFX) + g; }
static std::string p_pfx(const std::string& g) { return std::string(1, P_PFX) + g + std::string(1, '\0'); }
static std::string p_inst(const std::string& g, const SID& id) { return p_pfx(g) + be64(id.ms) + be64(id.seq); }
static std::string c_pfx(const std::string& g) { return std::string(1, C_PFX) + g + std::string(1, '\0'); }
static std::string c_inst(const std::string& g, const std::string& c) { return c_pfx(g) + c; }
static const long long INVALID_READ = -1;

struct Group { SID last; long long entries_read = INVALID_READ; };
static std::string pack_group(const Group& grp) { return be64(grp.last.ms) + be64(grp.last.seq) + be64((uint64_t)grp.entries_read); }
static Group unpack_group(const std::string& s) { Group grp; if (s.size() >= 24) { grp.last.ms = rd64(s, 0); grp.last.seq = rd64(s, 8); grp.entries_read = (long long)rd64(s, 16); } return grp; }
static BagPtr group_bag(const Group& g) { Bag b; b.kind = Kind::StreamGroup; b.s = pack_group(g); return std::make_shared<const Bag>(std::move(b)); }
struct Nack { std::string consumer; int64_t delivery_ms = 0; uint64_t count = 0; };
static std::string pack_nack(const Nack& n) { return be64((uint64_t)n.delivery_ms) + be64(n.count) + n.consumer; }
static Nack unpack_nack(const std::string& s) { Nack n; if (s.size() >= 16) { n.delivery_ms = (int64_t)rd64(s, 0); n.count = rd64(s, 8); n.consumer = s.substr(16); } return n; }
static BagPtr nack_bag(const Nack& n) { Bag b; b.kind = Kind::StreamPEL; b.s = pack_nack(n); return std::make_shared<const Bag>(std::move(b)); }
struct Consumer { int64_t seen = 0, active = -1; };
static std::string pack_consumer(const Consumer& c) { return be64((uint64_t)c.seen) + be64((uint64_t)c.active); }
static Consumer unpack_consumer(const std::string& s) { Consumer c; if (s.size() >= 16) { c.seen = (int64_t)rd64(s, 0); c.active = (int64_t)rd64(s, 8); } return c; }
static BagPtr consumer_bag(const Consumer& c) { Bag b; b.kind = Kind::StreamConsumer; b.s = pack_consumer(c); return std::make_shared<const Bag>(std::move(b)); }

static bool group_of(const VarPtr& v, const std::string& g, Group& out) { CellPtr cp = Memory::inst(v, g_inst(g)); if (!cp) return false; out = unpack_group(cp->bag->s); return true; }
static bool consumer_of(const VarPtr& v, const std::string& g, const std::string& c, Consumer& out) { CellPtr cp = Memory::inst(v, c_inst(g, c)); if (!cp) return false; out = unpack_consumer(cp->bag->s); return true; }
static bool nack_of(const VarPtr& v, const std::string& g, const SID& id, Nack& out) { CellPtr cp = Memory::inst(v, p_inst(g, id)); if (!cp) return false; out = unpack_nack(cp->bag->s); return true; }
// the group's PEL in id order (optionally one consumer's, optionally within [lo, hi])
static std::vector<std::pair<SID, Nack>> pel_of(const VarPtr& v, const std::string& g, const std::string* consumer = nullptr, const SID* lo = nullptr, const SID* hi = nullptr) {
    std::vector<std::pair<SID, Nack>> out; if (!v) return out;
    std::string pf = p_pfx(g);
    std::string from = lo ? p_inst(g, *lo) : pf, to = hi ? p_inst(g, *hi) : pf + std::string(16, '\xff');
    v->range(from, to, false, [&](const std::string& i, const CellPtr& c) {
        if (i.compare(0, pf.size(), pf) != 0) return false;
        Nack n = unpack_nack(c->bag->s);
        if (consumer && n.consumer != *consumer) return true;
        SID id; id.ms = rd64(i, pf.size()); id.seq = rd64(i, pf.size() + 8);
        out.emplace_back(id, n); return true;
    });
    return out;
}
static std::vector<std::pair<std::string, Consumer>> consumers_of(const VarPtr& v, const std::string& g) {
    std::vector<std::pair<std::string, Consumer>> out; if (!v) return out;
    std::string pf = c_pfx(g);
    v->range(pf, pf + std::string(255, '\xff'), false, [&](const std::string& i, const CellPtr& c) { if (i.compare(0, pf.size(), pf) != 0) return false; out.emplace_back(i.substr(pf.size()), unpack_consumer(c->bag->s)); return true; });
    return out;
}
static std::vector<std::pair<std::string, Group>> groups_of(const VarPtr& v) {
    std::vector<std::pair<std::string, Group>> out; if (!v) return out;
    std::string pf(1, G_PFX);
    v->range(pf, pf + std::string(255, '\xff'), false, [&](const std::string& i, const CellPtr& c) { if (i[0] != G_PFX) return false; out.emplace_back(i.substr(1), unpack_group(c->bag->s)); return true; });
    return out;
}
static bool entry_exists(const VarPtr& v, const SID& id) { return Memory::inst(v, inst_of(id)) != nullptr; }
static bool has_tombstones(const VarPtr& v, const Meta& m, const SID& start) {
    if (slen(v) == 0 || (m.maxdel.ms == 0 && m.maxdel.seq == 0)) return false;
    return start <= m.maxdel;
}
static long long estimate_read(const VarPtr& v, const Meta& m, const SID& id) {
    size_t len = slen(v);
    if (!m.added) return 0;
    if (!len && id <= m.last) return (long long)m.added;
    if (!(id.ms == 0 && id.seq == 0) && id < m.maxdel) return INVALID_READ;
    if (id == m.last) return (long long)m.added;
    if (m.last < id) return INVALID_READ;
    bool xdel_zero = m.maxdel.ms == 0 && m.maxdel.seq == 0;
    if (xdel_zero || m.maxdel < m.first) {
        if (id < m.first) return (long long)m.added - (long long)len;
        if (id == m.first) return (long long)m.added - (long long)len + 1;
    }
    return INVALID_READ;
}
static bool lag_of(const VarPtr& v, const Meta& m, const Group& grp, long long& lag) {
    size_t len = slen(v);
    if (!m.added) { lag = 0; return true; }
    if (!len) { lag = 0; return true; }
    if (grp.last < m.first && m.maxdel < m.first) { lag = (long long)len; return true; }
    if (grp.entries_read != INVALID_READ && !has_tombstones(v, m, grp.last)) { lag = (long long)m.added - grp.entries_read; return true; }
    long long er = estimate_read(v, m, grp.last);
    if (er != INVALID_READ) { lag = (long long)m.added - er; return true; }
    return false;
}
static int require_group(Client& c, const std::string& key, const std::string& g, VarPtr& v, Group& grp, Reply& r, bool nogroup_err = true) {
    int st = svar(c, key, v, r); if (st < 0) return -1;
    if (!st || !group_of(v, g, grp)) { if (nogroup_err) r.error("NOGROUP No such key '" + key + "' or consumer group '" + g + "'"); return 0; }
    return 1;
}

// ---------------------------------------------------------------- XGROUP
static void cmd_xgroup_create(Client& c, const Argv& a, Reply& r) {
    bool mkstream = false; long long entries_read = INVALID_READ;
    for (size_t i = 5; i < a.size(); i++) {
        std::string o = lower(a[i]);
        if (o == "mkstream") mkstream = true;
        else if (o == "entriesread" && i + 1 < a.size()) { if (!string2ll(a[i+1], entries_read)) { err_notint(r); return; } if (entries_read < 0 && entries_read != INVALID_READ) { r.error("ERR value for ENTRIESREAD must be positive or -1"); return; } i++; }
        else { err_syntax(r); return; }
    }
    VarPtr v; int st = svar(c, a[2], v, r); if (st < 0) return;
    if (!st && !mkstream) { r.error("ERR The XGROUP subcommand requires the key to exist. Note that for CREATE you may want to use the MKSTREAM option to create an empty stream automatically."); return; }
    Meta m = meta_of(v);
    SID id; if (a[4] == "$") id = m.last; else if (!parse_id(a[4], id, 0, true)) { err_id(r); return; }
    Group grp; if (group_of(v, a[3], grp)) { r.error("BUSYGROUP Consumer Group name already exists"); return; }
    grp.last = id; grp.entries_read = entries_read;
    std::vector<Memory::W> ws;
    if (!st) ws.push_back(Memory::W{a[2], db::VAL, meta_bag(Meta())});
    ws.push_back(Memory::W{a[2], g_inst(a[3]), group_bag(grp)});
    g.mem.write_many(db::svc(c.db), ws); g.stats.dirty++;
    r.ok();
}
static void cmd_xgroup_setid(Client& c, const Argv& a, Reply& r) {
    long long entries_read = INVALID_READ;
    if (a.size() != 5 && a.size() != 7) { err_syntax(r); return; }
    if (a.size() == 7) { if (!str_eq_ci(a[5], "entriesread")) { err_syntax(r); return; } if (!string2ll(a[6], entries_read)) { err_notint(r); return; } if (entries_read < 0 && entries_read != INVALID_READ) { r.error("ERR value for ENTRIESREAD must be positive or -1"); return; } }
    VarPtr v; Group grp; int st = require_group(c, a[2], a[3], v, grp, r); if (st <= 0) return;
    SID id; if (a[4] == "$") id = meta_of(v).last; else if (!parse_id(a[4], id, 0, false)) { err_id(r); return; }   // Redis: any valid id, '-' included
    grp.last = id; grp.entries_read = entries_read;
    g.mem.write(db::svc(c.db), a[2], g_inst(a[3]), group_bag(grp)); g.stats.dirty++;
    r.ok();
}
static void cmd_xgroup_destroy(Client& c, const Argv& a, Reply& r) {
    VarPtr v; Group grp; int st = require_group(c, a[2], a[3], v, grp, r, false); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> rs{{a[2], g_inst(a[3])}};
    for (auto& p : pel_of(v, a[3])) rs.emplace_back(a[2], p_inst(a[3], p.first));
    for (auto& cn : consumers_of(v, a[3])) rs.emplace_back(a[2], c_inst(a[3], cn.first));
    g.mem.remove_many(db::svc(c.db), rs); g.stats.dirty++;
    r.integer(1);
}
static void cmd_xgroup_createconsumer(Client& c, const Argv& a, Reply& r) {
    VarPtr v; Group grp; int st = require_group(c, a[2], a[3], v, grp, r); if (st <= 0) return;
    Consumer cn; if (consumer_of(v, a[3], a[4], cn)) { r.integer(0); return; }
    cn.seen = Memory::now_ms(); cn.active = -1;
    g.mem.write(db::svc(c.db), a[2], c_inst(a[3], a[4]), consumer_bag(cn)); g.stats.dirty++;
    r.integer(1);
}
static void cmd_xgroup_delconsumer(Client& c, const Argv& a, Reply& r) {
    VarPtr v; Group grp; int st = require_group(c, a[2], a[3], v, grp, r); if (st <= 0) return;
    Consumer cn; if (!consumer_of(v, a[3], a[4], cn)) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> rs{{a[2], c_inst(a[3], a[4])}};
    long long pending = 0;
    for (auto& p : pel_of(v, a[3], &a[4])) { rs.emplace_back(a[2], p_inst(a[3], p.first)); pending++; }
    g.mem.remove_many(db::svc(c.db), rs); g.stats.dirty++;
    r.integer(pending);
}
static void cmd_xgroup_help(Client&, const Argv&, Reply& r) {
    const char* lines[] = {"XGROUP <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","CREATE <key> <groupname> <id|$> [option]","    Create a new consumer group. Options are:","    * MKSTREAM","      Create the empty stream if it does not exist.","    * ENTRIESREAD entries_read","      Set the group's entries_read counter (internal use).","CREATECONSUMER <key> <groupname> <consumer>","    Create a new consumer in the specified group.","DELCONSUMER <key> <groupname> <consumer>","    Remove the specified consumer.","DESTROY <key> <groupname>","    Remove the specified group.","SETID <key> <groupname> <id|$> [ENTRIESREAD entries_read]","    Set the current group ID and entries_read counter.","HELP","    Print this help."};
    r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
}

// ---------------------------------------------------------------- XREADGROUP
static void cmd_xreadgroup(Client& c, const Argv& a, Reply& r) {
    long long count = 0; long long timeout = -1; bool block = false, noack = false; std::string group, consumer; bool have_group = false; size_t i = 1;
    for (; i < a.size(); i++) {
        std::string o = lower(a[i]); size_t more = a.size() - i - 1;
        if (o == "count" && more) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count < 0) count = 0; i++; }
        else if (o == "block" && more) { if (!string2ll(a[i+1], timeout)) { r.error("ERR timeout is not an integer or out of range"); return; } if (timeout < 0) { r.error("ERR timeout is negative"); return; } block = true; i++; }   // BLOCK is integer milliseconds
        else if (o == "noack") noack = true;
        else if (o == "group" && more >= 2) { group = a[i+1]; consumer = a[i+2]; have_group = true; i += 2; }
        else if (o == "streams") { i++; break; }
        else { err_syntax(r); return; }
    }
    if (!have_group) { r.error("ERR Missing GROUP option for XREADGROUP"); return; }
    if (i >= a.size()) { err_syntax(r); return; }
    size_t rest = a.size() - i;
    if (rest % 2) { r.error("ERR Unbalanced 'xreadgroup' list of streams: for each stream key an ID or '>' must be specified."); return; }
    size_t n = rest / 2;
    std::vector<std::string> keys(a.begin() + i, a.begin() + i + n);
    std::vector<SID> ids(n); std::vector<bool> fresh(n, false);
    for (size_t k = 0; k < n; k++) {
        const std::string& s = a[i + n + k];
        if (s == ">") { fresh[k] = true; continue; }
        if (s == "$") { r.error("ERR The $ ID is meaningless in the context of XREADGROUP: you want to read the history of this consumer by specifying a proper ID, or use the > ID to get new messages. The $ ID would just return an empty result set."); return; }
        if (!parse_id(s, ids[k], 0, true)) { err_id(r); return; }
    }
    // every key must exist with the group
    for (size_t k = 0; k < n; k++) { VarPtr v; Group grp; int st = svar(c, keys[k], v, r); if (st < 0) return; if (!st || !group_of(v, keys[k] == keys[k] ? group : group, grp)) { r.error("NOGROUP No such key '" + keys[k] + "' or consumer group '" + group + "' in XREADGROUP with GROUP option"); return; } }
    WaitReg wreg(wait_word_for(c, keys)); WaitWord& w = wreg.w; c.block_keys.nokey = true;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout > 0 ? timeout : 0);
    Claim claim(c, keys);                 // delivery of '>' to parked readers is served in arrival order: a claim, not a queue
    bool first = true;                    // the consumer's seen-time is written on the first pass only when blocking: a rewrite per wake would wake the others
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        std::vector<std::pair<std::string, std::vector<std::pair<SID, std::string>>>> found;
        bool any_history = false;
        for (size_t k = 0; k < n; k++) {
            VarPtr v = db::live_var(c.db, keys[k]); Kind kd = db::kind_of(v);
            if (v && kd != Kind::None && kd != Kind::StreamEntry && kd != Kind::StreamMeta) { r.error("WRONGTYPE Operation against a key holding the wrong kind of value"); return; }
            if (kd != Kind::StreamEntry && kd != Kind::StreamMeta) { r.error("NOGROUP No such key '" + keys[k] + "' or consumer group '" + group + "' in XREADGROUP with GROUP option"); return; }
            Group grp; if (!group_of(v, group, grp)) { r.error("NOGROUP No such key '" + keys[k] + "' or consumer group '" + group + "' in XREADGROUP with GROUP option"); return; }
            int64_t now = Memory::now_ms();
            std::vector<Memory::W> ws;
            Consumer cn; bool newc = !consumer_of(v, group, consumer, cn);
            cn.seen = now; if (newc) cn.active = -1;
            std::vector<std::pair<SID, std::string>> es;
            if (!fresh[k]) {
                any_history = true;
                // the consumer's history: its PEL from the id on, with the entries that still exist
                for (auto& p : pel_of(v, group, &consumer, &ids[k])) {
                    if (!(ids[k] < p.first)) continue;                       // history is ids strictly greater than the one given
                    auto e = range(v, p.first, p.first, false, 1);
                    if (e.empty()) es.emplace_back(p.first, std::string(1, '\0'));      // deleted: reply nil
                    else es.push_back(e[0]);
                    if (count && (long long)es.size() >= count) break;
                }
                ws.push_back(Memory::W{keys[k], c_inst(group, consumer), consumer_bag(cn)});
                g.mem.write_many(db::svc(c.db), ws);
                if (!es.empty()) found.emplace_back(keys[k], std::move(es)); else found.emplace_back(keys[k], std::vector<std::pair<SID, std::string>>());
                continue;
            }
            if (block && !claim.first_for(keys[k])) { if (newc || !block || first) { ws.push_back(Memory::W{keys[k], c_inst(group, consumer), consumer_bag(cn)}); g.mem.write_many(db::svc(c.db), ws); } continue; }   // not my turn yet
            SID lo = grp.last; if (lo.seq == UINT64_MAX) { if (lo.ms == UINT64_MAX) { if (newc || !block || first) { ws.push_back(Memory::W{keys[k], c_inst(group, consumer), consumer_bag(cn)}); g.mem.write_many(db::svc(c.db), ws); } continue; } lo.ms++; lo.seq = 0; } else lo.seq++;
            es = range(v, lo, SID_MAX, false, count);
            // no data: create the consumer if it is new, but do not rewrite an existing one -- that write would move
            // the key's wait word and this reader would wake itself
            if (es.empty()) { if (newc || !block || first) { ws.push_back(Memory::W{keys[k], c_inst(group, consumer), consumer_bag(cn)}); g.mem.write_many(db::svc(c.db), ws); } continue; }
            Meta m = meta_of(v);
            for (auto& e : es) {
                if (grp.last < e.first) {
                    if (grp.entries_read != INVALID_READ && m.first <= grp.last && !has_tombstones(v, m, grp.last)) grp.entries_read++;
                    else if (m.added) grp.entries_read = estimate_read(v, m, e.first);
                    grp.last = e.first;
                }
                if (!noack) { Nack nk; nk.consumer = consumer; nk.delivery_ms = now; nk.count = 1; ws.push_back(Memory::W{keys[k], p_inst(group, e.first), nack_bag(nk)}); }
            }
            cn.active = now;
            ws.push_back(Memory::W{keys[k], c_inst(group, consumer), consumer_bag(cn)});
            ws.push_back(Memory::W{keys[k], g_inst(group), group_bag(grp)});
            g.mem.write_many(db::svc(c.db), ws); g.stats.dirty++;
            found.emplace_back(keys[k], std::move(es));
        }
        bool have_data = false; for (auto& f : found) if (!f.second.empty()) have_data = true;
        if (have_data || any_history) {
            if (r.proto == 3) r.map(found.size()); else r.array(found.size());
            for (auto& f : found) {
                if (r.proto != 3) r.array(2);
                r.bulk(f.first); r.array(f.second.size());
                for (auto& e : f.second) { if (e.second.size() == 1 && e.second[0] == '\0') { r.array(2); r.bulk(sid_str(e.first)); r.null_array(); } else reply_entry(r, e.first, e.second); }
            }
            return;
        }
        if (!block) { r.null_array(); return; }
        first = false; claim.place();
        if (!block_on(c, w, seen, timeout, deadline)) { r.null_array(); return; }
        int u = c.unblock.exchange(0);
        if (u == 1) { r.null_array(); return; }
        if (u == 2) { r.error("UNBLOCKED client unblocked via CLIENT UNBLOCK"); return; }
        if (c.closing) return;
    }
}

// ---------------------------------------------------------------- XACK / XPENDING / XCLAIM / XAUTOCLAIM
static void cmd_xack(Client& c, const Argv& a, Reply& r) {
    std::vector<SID> ids; for (size_t i = 3; i < a.size(); i++) { SID id; if (!parse_id(a[i], id, 0, true)) { err_id(r); return; } ids.push_back(id); }
    VarPtr v; Group grp; int st = require_group(c, a[1], a[2], v, grp, r, false); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    std::vector<std::pair<std::string, std::string>> rs; long long acked = 0;
    for (auto& id : ids) { Nack nk; if (nack_of(v, a[2], id, nk)) { rs.emplace_back(a[1], p_inst(a[2], id)); acked++; } }
    if (acked) { g.mem.remove_many(db::svc(c.db), rs); g.stats.dirty++; }   // XACK is remove()
    r.integer(acked);
}
static void cmd_xpending(Client& c, const Argv& a, Reply& r) {
    long long minidle = 0, count = 0; bool extended = false; std::string consumer; bool have_consumer = false; SID lo{0, 0}, hi = SID_MAX;
    if (a.size() != 3 && a.size() < 6) { err_syntax(r); return; }
    if (a.size() >= 6) {
        size_t s = 3;
        if (str_eq_ci(a[3], "idle")) { if (!string2ll(a[4], minidle)) { err_notint(r); return; } if (minidle < 0) minidle = 0; s = 5; if (a.size() < 8) { err_syntax(r); return; } }
        if (!string2ll(a[s+2], count)) { err_notint(r); return; } if (count < 0) count = 0;
        std::string t1 = a[s], t2 = a[s+1]; bool loex = false, hiex = false;
        if (!t1.empty() && t1[0] == '(') { loex = true; t1 = t1.substr(1); }
        if (!t2.empty() && t2[0] == '(') { hiex = true; t2 = t2.substr(1); }
        if (!parse_id(t1, lo, 0, loex)) { err_id(r); return; }
        if (!parse_id(t2, hi, UINT64_MAX, hiex)) { err_id(r); return; }
        if (loex) { if (lo.seq == UINT64_MAX) { if (lo.ms == UINT64_MAX) { r.error("ERR invalid start ID for the interval"); return; } lo.ms++; lo.seq = 0; } else lo.seq++; }
        if (hiex) { if (hi.seq == 0) { if (hi.ms == 0) { r.error("ERR invalid end ID for the interval"); return; } hi.ms--; hi.seq = UINT64_MAX; } else hi.seq--; }
        if (a.size() > s + 3) { consumer = a[s+3]; have_consumer = true; }
        if (a.size() > s + 4) { err_syntax(r); return; }
        extended = true;
    }
    VarPtr v; Group grp; int st = require_group(c, a[1], a[2], v, grp, r); if (st <= 0) return;
    int64_t now = Memory::now_ms();
    if (!extended) {
        auto pel = pel_of(v, a[2]);
        r.array(4); r.integer((long long)pel.size());
        if (pel.empty()) { r.null(); r.null(); r.null_array(); return; }
        r.bulk(sid_str(pel.front().first)); r.bulk(sid_str(pel.back().first));
        std::map<std::string, long long> per; for (auto& p : pel) per[p.second.consumer]++;
        r.array(per.size()); for (auto& kv : per) { r.array(2); r.bulk(kv.first); r.bulk(std::to_string(kv.second)); }
        return;
    }
    if (have_consumer) { Consumer cn; if (!consumer_of(v, a[2], consumer, cn)) { r.array(0); return; } }
    std::vector<std::pair<SID, Nack>> out;
    for (auto& p : pel_of(v, a[2], have_consumer ? &consumer : nullptr, &lo, &hi)) {
        if (minidle && now - p.second.delivery_ms < minidle) continue;
        out.push_back(p); if (count && (long long)out.size() >= count) break;
    }
    r.array(out.size());
    for (auto& p : out) { r.array(4); r.bulk(sid_str(p.first)); r.bulk(p.second.consumer); r.integer(now - p.second.delivery_ms); r.integer((long long)p.second.count); }
}
static void cmd_xclaim(Client& c, const Argv& a, Reply& r) {
    VarPtr v; Group grp; int st = require_group(c, a[1], a[2], v, grp, r); if (st <= 0) return;
    long long minidle; if (!string2ll(a[4], minidle)) { r.error("ERR Invalid min-idle-time argument for XCLAIM"); return; } if (minidle < 0) minidle = 0;
    std::vector<SID> ids; size_t j = 5;
    for (; j < a.size(); j++) { SID id; if (!parse_id(a[j], id, 0, true)) break; ids.push_back(id); }
    if (ids.empty()) { err_id(r); return; }
    int64_t now = Memory::now_ms(), deliverytime = now; long long retrycount = -1; bool force = false, justid = false; SID last_id{0, 0};
    for (; j < a.size(); j++) {
        std::string o = lower(a[j]); bool more = j + 1 < a.size();
        if (o == "force") force = true; else if (o == "justid") justid = true;
        else if (o == "idle" && more) { long long idle; if (!string2ll(a[++j], idle)) { r.error("ERR Invalid IDLE option argument for XCLAIM"); return; } deliverytime = now - idle; }
        else if (o == "time" && more) { long long t; if (!string2ll(a[++j], t)) { r.error("ERR Invalid TIME option argument for XCLAIM"); return; } deliverytime = t; }
        else if (o == "retrycount" && more) { if (!string2ll(a[++j], retrycount)) { r.error("ERR Invalid RETRYCOUNT option argument for XCLAIM"); return; } }
        else if (o == "lastid" && more) { if (!parse_id(a[++j], last_id, 0, true)) { err_id(r); return; } }
        else { r.error("ERR Unrecognized XCLAIM option '" + a[j] + "'"); return; }
    }
    if (deliverytime < 0 || deliverytime > now) deliverytime = now;
    std::vector<Memory::W> ws; std::vector<std::pair<std::string, std::string>> rs;
    if (grp.last < last_id) { grp.last = last_id; ws.push_back(Memory::W{a[1], g_inst(a[2]), group_bag(grp)}); }
    Consumer cn; bool newc = !consumer_of(v, a[2], a[3], cn); if (newc) { cn.seen = now; cn.active = -1; }
    std::vector<std::pair<SID, std::string>> claimed; bool touched = false;
    for (auto& id : ids) {
        Nack nk; bool have = nack_of(v, a[2], id, nk);
        if (!entry_exists(v, id)) { if (have) rs.emplace_back(a[1], p_inst(a[2], id)); continue; }
        if (!have) { if (!force) continue; nk = Nack(); }
        if (have && !nk.consumer.empty() && minidle && now - nk.delivery_ms < minidle) continue;
        nk.consumer = a[3]; nk.delivery_ms = deliverytime;
        if (retrycount >= 0) nk.count = (uint64_t)retrycount; else if (!justid) nk.count++;
        ws.push_back(Memory::W{a[1], p_inst(a[2], id), nack_bag(nk)});
        auto e = range(v, id, id, false, 1); claimed.emplace_back(id, e.empty() ? "" : e[0].second);
        cn.active = now; touched = true;
    }
    if (touched || newc) ws.push_back(Memory::W{a[1], c_inst(a[2], a[3]), consumer_bag(cn)});
    if (!rs.empty()) g.mem.remove_many(db::svc(c.db), rs);
    if (!ws.empty()) g.mem.write_many(db::svc(c.db), ws);
    if (!rs.empty() || !ws.empty()) g.stats.dirty++;
    r.array(claimed.size());
    for (auto& cl : claimed) { if (justid) r.bulk(sid_str(cl.first)); else reply_entry(r, cl.first, cl.second); }
}
static void cmd_xautoclaim(Client& c, const Argv& a, Reply& r) {
    long long minidle; if (!string2ll(a[4], minidle)) { r.error("ERR Invalid min-idle-time argument for XAUTOCLAIM"); return; } if (minidle < 0) minidle = 0;
    SID start; std::string t = a[5]; bool ex = false; if (!t.empty() && t[0] == '(') { ex = true; t = t.substr(1); }
    if (!parse_id(t, start, 0, ex)) { err_id(r); return; }
    if (ex) { if (start.seq == UINT64_MAX) { if (start.ms == UINT64_MAX) { r.error("ERR invalid start ID for the interval"); return; } start.ms++; start.seq = 0; } else start.seq++; }
    long long count = 100; bool justid = false;
    for (size_t j = 6; j < a.size(); j++) {
        std::string o = lower(a[j]); bool more = j + 1 < a.size();
        if (o == "count" && more) { if (!string2ll(a[++j], count) || count < 1 || count > LONG_MAX / 16) { r.error("ERR COUNT must be > 0"); return; } }
        else if (o == "justid") justid = true;
        else { err_syntax(r); return; }
    }
    VarPtr v; Group grp; int st = require_group(c, a[1], a[2], v, grp, r); if (st <= 0) return;
    int64_t now = Memory::now_ms();
    long long attempts = count * 10;
    std::vector<Memory::W> ws; std::vector<std::pair<std::string, std::string>> rs;
    std::vector<std::pair<SID, std::string>> claimed; std::vector<SID> deleted; SID endid{0, 0}; bool more = false;
    Consumer cn; bool newc = !consumer_of(v, a[2], a[3], cn); if (newc) { cn.seen = now; cn.active = -1; }
    auto pel = pel_of(v, a[2], nullptr, &start);
    size_t idx = 0;
    for (; idx < pel.size() && attempts > 0 && count > 0; idx++, attempts--) {
        auto& p = pel[idx];
        if (!entry_exists(v, p.first)) { rs.emplace_back(a[1], p_inst(a[2], p.first)); deleted.push_back(p.first); count--; continue; }
        if (minidle && now - p.second.delivery_ms < minidle) continue;
        Nack nk = p.second; nk.consumer = a[3]; nk.delivery_ms = now; if (!justid) nk.count++;
        ws.push_back(Memory::W{a[1], p_inst(a[2], p.first), nack_bag(nk)});
        auto e = range(v, p.first, p.first, false, 1); claimed.emplace_back(p.first, e.empty() ? "" : e[0].second);
        cn.active = now; count--;
    }
    if (idx < pel.size()) { endid = pel[idx].first; more = true; }
    (void)more;
    ws.push_back(Memory::W{a[1], c_inst(a[2], a[3]), consumer_bag(cn)});
    if (!rs.empty()) g.mem.remove_many(db::svc(c.db), rs);
    g.mem.write_many(db::svc(c.db), ws); g.stats.dirty++;
    r.array(3); r.bulk(sid_str(endid));
    r.array(claimed.size()); for (auto& cl : claimed) { if (justid) r.bulk(sid_str(cl.first)); else reply_entry(r, cl.first, cl.second); }
    r.array(deleted.size()); for (auto& d : deleted) r.bulk(sid_str(d));
}

// ---------------------------------------------------------------- XINFO GROUPS / CONSUMERS, and the groups part of XINFO STREAM
static void cmd_xinfo_groups(Client& c, const Argv& a, Reply& r) {
    VarPtr v; int st = svar(c, a[2], v, r); if (st < 0) return;
    if (!st) { r.error("ERR no such key"); return; }
    Meta m = meta_of(v); auto gs = groups_of(v);
    r.array(gs.size());
    for (auto& gp : gs) {
        r.map(6);
        r.bulk("name"); r.bulk(gp.first);
        r.bulk("consumers"); r.integer((long long)consumers_of(v, gp.first).size());
        r.bulk("pending"); r.integer((long long)pel_of(v, gp.first).size());
        r.bulk("last-delivered-id"); r.bulk(sid_str(gp.second.last));
        r.bulk("entries-read"); if (gp.second.entries_read != INVALID_READ) r.integer(gp.second.entries_read); else r.null();
        r.bulk("lag"); long long lag; if (lag_of(v, m, gp.second, lag)) r.integer(lag); else r.null();
    }
}
static void cmd_xinfo_consumers(Client& c, const Argv& a, Reply& r) {
    VarPtr v; Group grp; int st = require_group(c, a[2], a[3], v, grp, r); if (st < 0) return;
    if (!st) { r.error("NOGROUP No such consumer group '" + a[3] + "' for key name '" + a[2] + "'"); return; }
    int64_t now = Memory::now_ms(); auto cs = consumers_of(v, a[3]);
    r.array(cs.size());
    for (auto& cn : cs) {
        r.map(4);
        r.bulk("name"); r.bulk(cn.first);
        r.bulk("pending"); r.integer((long long)pel_of(v, a[3], &cn.first).size());
        r.bulk("idle"); r.integer(now - cn.second.seen);
        r.bulk("inactive"); r.integer(cn.second.active != -1 ? now - cn.second.active : cn.second.active);
    }
}
// called by XINFO STREAM: the groups section (count or full)
void stream_groups_count(const VarPtr& v, Reply& r) { r.integer((long long)groups_of(v).size()); }
void stream_groups_full(const VarPtr& v, Reply& r, long long count) {
    Meta m = meta_of(v); auto gs = groups_of(v);
    r.array(gs.size());
    for (auto& gp : gs) {
        r.map(7);
        r.bulk("name"); r.bulk(gp.first);
        r.bulk("last-delivered-id"); r.bulk(sid_str(gp.second.last));
        r.bulk("entries-read"); if (gp.second.entries_read != INVALID_READ) r.integer(gp.second.entries_read); else r.null();
        r.bulk("lag"); long long lag; if (lag_of(v, m, gp.second, lag)) r.integer(lag); else r.null();
        auto pel = pel_of(v, gp.first);
        r.bulk("pel-count"); r.integer((long long)pel.size());
        r.bulk("pending"); size_t np = count ? std::min<size_t>(pel.size(), (size_t)count) : pel.size(); r.array(np);
        for (size_t i = 0; i < np; i++) { r.array(4); r.bulk(sid_str(pel[i].first)); r.bulk(pel[i].second.consumer); r.integer(pel[i].second.delivery_ms); r.integer((long long)pel[i].second.count); }
        auto cs = consumers_of(v, gp.first);
        r.bulk("consumers"); r.array(cs.size());
        for (auto& cn : cs) {
            r.map(5);
            r.bulk("name"); r.bulk(cn.first);
            r.bulk("seen-time"); r.integer(cn.second.seen);
            r.bulk("active-time"); r.integer(cn.second.active);
            auto cp = pel_of(v, gp.first, &cn.first);
            r.bulk("pel-count"); r.integer((long long)cp.size());
            r.bulk("pending"); size_t nc = count ? std::min<size_t>(cp.size(), (size_t)count) : cp.size(); r.array(nc);
            for (size_t i = 0; i < nc; i++) { r.array(3); r.bulk(sid_str(cp[i].first)); r.integer(cp[i].second.delivery_ms); r.integer((long long)cp[i].second.count); }
        }
    }
}
void register_stream_group_commands() {
    register_cmd("xgroup|create", cmd_xgroup_create); register_cmd("xgroup|setid", cmd_xgroup_setid); register_cmd("xgroup|destroy", cmd_xgroup_destroy);
    register_cmd("xgroup|createconsumer", cmd_xgroup_createconsumer); register_cmd("xgroup|delconsumer", cmd_xgroup_delconsumer); register_cmd("xgroup|help", cmd_xgroup_help);
    register_cmd("xreadgroup", cmd_xreadgroup); register_cmd("xack", cmd_xack); register_cmd("xpending", cmd_xpending); register_cmd("xclaim", cmd_xclaim); register_cmd("xautoclaim", cmd_xautoclaim);
    register_cmd("xinfo|groups", cmd_xinfo_groups); register_cmd("xinfo|consumers", cmd_xinfo_consumers);
}
