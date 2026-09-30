#include <functional>
// The Redis key layer: a key is a variable in service "db<N>". Its value is the instance "" cell;
// its expiry is a ttl cell beside it. Readers decide: an expired value reads as absent.
#include "server.hpp"
#include <unordered_set>
#include <climits>
#include <algorithm>
namespace rr {

namespace db {

const std::string VAL = "";
const std::string TTL = std::string("\x01ttl", 4);
const std::string ELEM = std::string("\x02", 1);

static std::vector<std::string> svc_names, claim_names;
const std::string& svc(int dbi) {
    if (svc_names.empty()) for (int d = 0; d < 1024; d++) svc_names.push_back("db" + std::to_string(d));
    return svc_names[(size_t)dbi];
}
const std::string& claims(int dbi) {
    if (claim_names.empty()) for (int d = 0; d < 1024; d++) claim_names.push_back("claims" + std::to_string(d));
    return claim_names[(size_t)dbi];
}

// one root load per lookup: the variable snapshot holds both the value and the ttl cell
static VarPtr check_live(VarPtr v, int dbi, const std::string& key) {
    if (!v) return v;
    CellPtr t = Memory::inst(v, TTL);
    if (t && t->bag->n <= Memory::now_ms()) { del(dbi, key); g.stats.expired_keys++; return nullptr; }   // readers decide, and reap
    return v;
}
VarPtr live_var(int dbi, const std::string& key) { return check_live(g.mem.var(svc(dbi), key), dbi, key); }
VarPtr live_var(const Memory::Snapshot& snap, int dbi, const std::string& key) { return check_live(snap.var(key), dbi, key); }

Key::Key(int dbi_, const std::string& name_) : dbi(dbi_), name(name_), R(g.mem.region(svc(dbi_))), h(Region::hash_of(name_)) {
    v = g.mem.var(R, name, h);
    if (v) { CellPtr t = Memory::inst(v, TTL); if (t && t->bag->n <= Memory::now_ms()) { del(dbi, name); g.stats.expired_keys++; v = nullptr; } }
    k = kind_of(v);
}
CellPtr Key::val() const { return v ? Memory::inst(v, VAL) : nullptr; }
uint64_t Key::write(const std::string& instance, BagPtr bag) { return g.mem.write(R, name, h, instance, std::move(bag)); }
long long Key::push_count() const { VarPtr cur = g.mem.var(R, name, h); return cur ? (long long)elem_count(cur) : 0; }
uint64_t Key::set_string(const std::string& val, bool keepttl) {
    int64_t delta = (int64_t)val.size();
    CellPtr old = this->val();
    if (old && old->bag->kind == Kind::String) delta -= (int64_t)old->bag->s.size();
    std::vector<std::string> leftovers;
    if (v && ((!old && elem_count(v)) || (old && old->bag->kind != Kind::String))) v->each([&](const std::string& i, const CellPtr&) { leftovers.push_back(i); return true; });
    if (!keepttl && v && Memory::inst(v, TTL)) g.mem.remove(svc(dbi), name, TTL);
    Bag b; b.kind = Kind::String; b.s = val;
    uint64_t id = g.mem.write(R, name, h, VAL, std::move(b));
    if (!leftovers.empty()) g.mem.remove_many(svc(dbi), name, leftovers);
    drop_pieces(dbi, name);                          // [PIECES_V1] a SET: the old edits are gone
    g.used_memory += delta;
    g.stats.dirty++;
    return id;
}
Kind kind_of(const VarPtr& v) {
    if (!v) return Kind::None;
    CellPtr c = Memory::inst(v, VAL);
    if (c) return c->bag->kind;
    Kind k = Kind::None;
    v->range(ELEM, "", true, [&](const std::string&, const CellPtr& cp) { k = cp->bag->kind; return false; });
    return k;
}
size_t elem_count(const VarPtr& v) {
    if (!v) return 0;
    return v->inst_count();
}
std::vector<Cell> elems(const VarPtr& v, int dbi, const std::string& key) {
    std::vector<Cell> out;
    if (!v) return out;
    v->range(ELEM, "", true, [&](const std::string& i, const CellPtr& cp) {
        Cell c; c.service = svc(dbi); c.variable = key; c.instance = i; c.node = cp; out.push_back(std::move(c)); return true; });
    return out;
}
// one element, straight through: no pair vector, no sort
size_t put_elem(int dbi, const std::string& key, Kind kind, const std::string& name, const std::string& value) {
    Region& R = g.mem.region(svc(dbi)); size_t h = Region::hash_of(key);
    VarPtr v = g.mem.var(R, key, h);
    std::string inst; inst.reserve(name.size() + 1); inst += ELEM; inst += name;
    CellPtr cur = Memory::inst(v, inst);
    size_t fresh = cur ? 0 : 1;
    if (cur && cur->bag->kind == kind && text(cur) == value) return 0;
    Bag b; b.kind = kind; b.s = value;
    g.mem.write(R, key, h, inst, std::move(b));
    g.stats.dirty++;
    return fresh;
}
size_t put_elems(int dbi, const std::string& key, Kind kind, const std::vector<std::pair<std::string, std::string>>& nv) {
    Region& R = g.mem.region(svc(dbi)); size_t h = Region::hash_of(key);
    VarPtr v = g.mem.var(R, key, h);
    if (nv.size() == 1) {                                               // one element: the addressed write, no batch
        std::string inst = ELEM + nv[0].first;
        CellPtr cur = Memory::inst(v, inst);
        size_t fresh = cur ? 0 : 1;
        if (cur && cur->bag->kind == kind && text(cur) == nv[0].second) return 0;   // the same truth again: nothing to publish
        Bag b; b.kind = kind; b.s = nv[0].second;
        g.mem.write(R, key, h, inst, std::move(b));
        g.stats.dirty++;
        return fresh;
    }
    std::vector<Memory::W> ws; size_t fresh = 0;
    for (auto& p : nv) {
        std::string inst = ELEM + p.first;
        if (!Memory::inst(v, inst)) fresh++;
        Bag b; b.kind = kind; b.s = p.second;
        ws.push_back(Memory::W{key, inst, std::move(b)});
    }
    if (!ws.empty()) g.mem.write_many(svc(dbi), ws);
    g.stats.dirty++;
    return fresh;
}
// [REMOVAL_IS_THE_ANSWER_V1] The count returned is what the MEMORY removed, not what was seen before removing: two
// concurrent takers of one element both see it, both remove, and only one removal takes effect -- the other must be
// told 0 (ZPOPMIN delivered 494 members twice when this returned the count it had read). The TTL goes only once the
// elements are gone, in its own removal, so it is never counted as an element.
// ---- [PIECES_V1] -----------------------------------------------------------------------------------------------
const std::string PIECE = std::string("\x08", 1);
void (*hll_fold)(std::string& v, const std::string& piece) = nullptr;
static std::atomic<uint64_t> piece_seq{1};
static std::string be64s(uint64_t x) { std::string s(8, '\0'); for (int i = 7; i >= 0; i--) { s[(size_t)i] = (char)(x & 0xff); x >>= 8; } return s; }
static uint64_t rd64s(const std::string& s, size_t at) { uint64_t x = 0; for (size_t i = 0; i < 8; i++) x = (x << 8) | (unsigned char)s[at + i]; return x; }
std::string float_text(long double v);
static void apply_piece(std::string& v, const std::string& p) {
    if (p.empty()) return;
    switch (p[0]) {
    case 'A': v.append(p, 1, std::string::npos); return;
    case 'R': if (p.size() >= 9) { uint64_t off = rd64s(p, 1); size_t len = p.size() - 9; if (v.size() < off + len) v.resize(off + len, '\0'); memcpy(&v[off], p.data() + 9, len); } return;
    case 'B': if (p.size() >= 10) { uint64_t bit = rd64s(p, 1); size_t byte = (size_t)(bit >> 3); if (v.size() < byte + 1) v.resize(byte + 1, '\0');
                                    int sh = 7 - (int)(bit & 7); unsigned char b = (unsigned char)v[byte]; b &= (unsigned char)~(1 << sh); if (p[9] == '1') b |= (unsigned char)(1 << sh); v[byte] = (char)b; } return;
    case 'M': if (hll_fold) hll_fold(v, p); return;
    case 'I': { long long b = 0; if (p.size() >= 9 && string2ll(v, b)) { long long d = (long long)rd64s(p, 1); v = ll2string(b + d); } return; }   // an integer add, in ticket order
    case 'F': { long double b = 0, d = 0; if (string2ld(v, b) && string2ld(p.substr(1), d)) v = float_text(b + d); return; }                  // a float add, in ticket order
    }
}
static void each_piece(const Var* v, const std::function<void(const std::string&, const CellPtr&)>& f) {
    if (!v || !v->inst_c()) return;
    v->range(PIECE, "", true, [&](const std::string& k, const CellPtr& c) {
        if (k.compare(0, PIECE.size(), PIECE) != 0) return false;
        f(k, c); return true;
    });
}
static std::string base_text(CellPtr n);
static const size_t FOLD_AT = 32;
// When a key's pieces pile up, they are folded into a new value cell: ONE replace_if on the value cell they were folded
// onto. If it loses, no fold happens this time -- folding is optional work, never a second attempt at anything. Pieces
// written after the fold read stay and apply over the new value.
static void maybe_fold(int dbi, const std::string& key) {
    ebr::Guard eg;
    Region& R = g.mem.region(svc(dbi)); size_t h = Region::hash_of(key);
    VarPtr v = g.mem.var(R, key, h); if (!v) return;
    CellPtr n = Memory::inst(v, VAL); if (!n || n->bag->kind != Kind::String) return;
    std::vector<std::string> keys; std::string folded = base_text(n);
    each_piece(v, [&](const std::string& k, const CellPtr& c) { keys.push_back(k); apply_piece(folded, c->bag->s); });
    if (keys.size() < FOLD_AT) return;
    Bag b = *n->bag; b.s = std::move(folded);
    if (!g.mem.replace_if(R, key, h, VAL, n, std::move(b))) return;
    std::vector<std::pair<std::string, std::string>> rs; for (auto& k : keys) rs.emplace_back(key, k);
    g.mem.remove_many(svc(dbi), rs);
}
void add_piece(int dbi, const std::string& key, std::string payload) {
    Bag b; b.kind = Kind::String; b.s = std::move(payload);
    g.mem.write(svc(dbi), key, PIECE + be64s(piece_seq.fetch_add(1, std::memory_order_relaxed)), std::move(b));
    maybe_fold(dbi, key);
}
bool has_pieces(int dbi, const std::string& key) {
    VarPtr v = g.mem.var(svc(dbi), key); bool any = false;
    each_piece(v, [&](const std::string&, const CellPtr&) { any = true; });
    return any;
}
void drop_pieces(int dbi, const std::string& key) {
    VarPtr v = g.mem.var(svc(dbi), key); if (!v || !v->inst_c()) return;
    std::vector<std::pair<std::string, std::string>> rs;
    each_piece(v, [&](const std::string& k, const CellPtr&) { rs.emplace_back(key, k); });
    if (!rs.empty()) g.mem.remove_many(svc(dbi), rs);
}
BagPtr copy_bag(CellPtr n) {
    uint32_t k = n->adds.load(std::memory_order_acquire);
    if (!k) return n->bag_ptr();
    Bag b = *n->bag;
    if (b.kind == Kind::StreamMeta) {                 // entries-added lives in bytes 16..23 (big-endian) plus the accumulator
        if (b.s.size() >= 24) {
            uint64_t added = 0; for (size_t i = 16; i < 24; i++) added = (added << 8) | (unsigned char)b.s[i];
            added += (uint64_t)n->acc.load(std::memory_order_acquire);
            for (int i = 23; i >= 16; i--) { b.s[(size_t)i] = (char)(added & 0xff); added >>= 8; }
        }
    } else b.s = base_text(n);                        // bytes plus adds, without pieces
    return std::make_shared<const Bag>(std::move(b));
}
std::string float_text(long double v) {
    if (std::isnan(v) || std::isinf(v)) return ld2string_human(v);
    char b[64]; snprintf(b, sizeof b, "%.15Lg", v);
    return ld2string_human(strtold(b, nullptr));
}
std::string text(CellPtr n) {
    if (!n) return {};
    std::string v = base_text(n);
    if (n->bag->kind == Kind::String) each_piece(n->owner.load(std::memory_order_acquire), [&](const std::string&, const CellPtr& c) { apply_piece(v, c->bag->s); });
    return v;
}
static std::string base_text(CellPtr n) {
    if (!n) return {};
    const std::string& s = n->bag->s;
    uint32_t k = n->adds.load(std::memory_order_acquire);
    if (!k) return s;
    if (k & 2) {                                     // float adds: the value is a float from here on
        long double b = 0;
        if (!string2ld(s, b)) return s;
        b += (long double)n->acc.load(std::memory_order_acquire) + (long double)n->facc.load(std::memory_order_acquire);
        return float_text(b);
    }
    long long b = 0;
    if (!string2ll(s, b)) return s;                  // a base that is not an integer never takes an add (the caller checks)
    return ll2string(b + n->acc.load(std::memory_order_acquire));
}
size_t drop_elems(int dbi, const std::string& key, const std::vector<std::string>& names) {
    VarPtr v = g.mem.var(svc(dbi), key);
    if (!v) return 0;
    std::vector<std::pair<std::string, std::string>> rs;
    for (auto& n : names) { std::string inst = ELEM + n; if (Memory::inst(v, inst)) rs.emplace_back(key, inst); }
    if (rs.empty()) return 0;
    size_t removed = g.mem.remove_many(svc(dbi), rs);
    if (removed) {
        g.stats.dirty++;
        VarPtr nv = g.mem.var(svc(dbi), key);
        if (nv && elem_count(nv) == 0 && Memory::inst(nv, TTL)) g.mem.remove(svc(dbi), key, TTL);   // the key is gone: so is its ttl
    }
    return removed;
}
void store_elems(int dbi, const std::string& key, Kind kind, const std::vector<std::pair<std::string, std::string>>& nv) {
    // [REPLACE_IN_ONE_STEP_V1] the destination's old contents go and the new ones land in ONE batch: no reader sees it empty
    std::vector<Memory::W> ws;
    ws.push_back(Memory::W(key, "", nullptr, false, true));
    for (auto& p : nv) { Bag b; b.kind = kind; b.s = p.second; ws.push_back(Memory::W(key, ELEM + p.first, std::make_shared<const Bag>(std::move(b)))); }
    g.mem.write_many(svc(dbi), ws);
    g.stats.dirty++;
}

bool get(int dbi, const std::string& key, Cell& out) {
    VarPtr v = live_var(dbi, key);
    CellPtr c = Memory::inst(v, VAL);
    if (!c) return false;
    out.service = svc(dbi); out.variable = key; out.instance = VAL; out.node = c;
    return true;
}

bool exists(int dbi, const std::string& key) { return kind_of(live_var(dbi, key)) != Kind::None; }

std::string type(int dbi, const std::string& key) {
    switch (kind_of(live_var(dbi, key))) {
        case Kind::String: return "string";
        case Kind::HashField: return "hash";
        case Kind::SetMember: return "set";
        case Kind::ListElem: return "list";
        case Kind::ZMember: return "zset";
        case Kind::StreamEntry: case Kind::StreamMeta: case Kind::StreamGroup: case Kind::StreamPEL: case Kind::StreamConsumer: return "stream";
        default: return "none";
    }
}

int64_t expire_at(int dbi, const std::string& key) {
    VarPtr v = live_var(dbi, key);
    if (kind_of(v) == Kind::None) return -2;
    CellPtr t = Memory::inst(v, TTL);
    return t ? t->bag->n : -1;
}

uint64_t set_string(int dbi, const std::string& key, const std::string& val, bool keepttl) {
    int64_t delta = (int64_t)val.size();
    VarPtr v = g.mem.var(svc(dbi), key);
    CellPtr old = Memory::inst(v, VAL);
    if (old && old->bag->kind == Kind::String) delta -= (int64_t)old->bag->s.size();
    // a container becomes a string: the string lands first, then the elements go. A reader that wakes in
    // between sees a string under the key (WRONGTYPE), never a missing key; the order is the batch's only tell.
    // [REPLACE_IN_ONE_STEP_V1] a key of another kind becoming a string: the old elements go, the string lands, and the
    // ttl goes (unless kept), in ONE batch -- no reader sees the string beside the old elements, or the key missing
    bool other = v && ((!old && elem_count(v)) || (old && old->bag->kind != Kind::String));
    uint64_t id;
    if (other) {
        std::vector<Memory::W> ws;
        v->each([&](const std::string& i, const CellPtr&) { ws.push_back(Memory::W(key, i, nullptr, true, false)); return true; });
        if (!keepttl && Memory::inst(v, TTL)) ws.push_back(Memory::W(key, TTL, nullptr, true, false));
        Bag b; b.kind = Kind::String; b.s = val;
        ws.push_back(Memory::W(key, VAL, std::make_shared<const Bag>(std::move(b))));
        id = g.mem.write_many(svc(dbi), ws);
    } else {
        if (!keepttl && Memory::inst(v, TTL)) g.mem.remove(svc(dbi), key, TTL);
        Bag b; b.kind = Kind::String; b.s = val;
        id = g.mem.write(svc(dbi), key, VAL, std::move(b));
    }
    drop_pieces(dbi, key);                           // [PIECES_V1] a SET: the old edits are gone
    g.used_memory += delta;
    g.stats.dirty++;
    return id;
}

void set_expire(int dbi, const std::string& key, int64_t at_ms) {
    Bag b; b.kind = Kind::Ttl; b.n = at_ms; b.indexed = true;      // indexed: liveness is derived from the numeric index
    g.mem.write(svc(dbi), key, TTL, std::move(b));
    g.stats.dirty++;
}

bool persist(int dbi, const std::string& key) {
    bool r = g.mem.remove(svc(dbi), key, TTL) != 0;
    if (r) g.stats.dirty++;
    return r;
}

bool del(int dbi, const std::string& key) {
    std::vector<BagPtr> gone;
    if (!g.mem.remove_variable(svc(dbi), key, &gone)) return false;
    for (auto& b : gone) if (b->kind == Kind::String) g.used_memory -= (int64_t)b->s.size();
    g.stats.dirty++;
    return true;
}

size_t dbsize(int dbi) { return g.mem.variable_count(svc(dbi)) - expired_count(dbi); }

std::vector<std::string> keys(int dbi) {
    // one pass over the ttl cells, one over the names: expired keys are hidden (readers decide), not reaped here
    std::unordered_set<std::string> expired;
    for (auto& c : g.mem.read_indexed(svc(dbi), INT64_MIN, Memory::now_ms())) expired.insert(c.variable);
    std::vector<std::string> out;
    for (auto& k : g.mem.variables(svc(dbi))) if (!expired.count(k)) out.push_back(k);
    return out;
}

bool randomkey(int dbi, std::string& out) {
    for (int tries = 0; tries < 100; tries++) {
        if (!g.mem.random_variable(svc(dbi), out)) return false;
        if (exists(dbi, out)) return true;
    }
    return false;
}

void flush(int dbi) {
    // region-wide clear is one write-order step; the byte accounting is recomputed from what remains
    g.mem.clear(svc(dbi));
    int64_t total = 0;
    for (int d = 0; d < g.databases; d++) g.mem.each_cell(svc(d), [&](const Cell& c) { if (c.bag().kind == Kind::String) total += (int64_t)c.bag().s.size(); return true; });
    g.used_memory = total;
    g.stats.dirty++;
}

void copy_key(int sdb, const std::string& skey, int ddb, const std::string& dkey) {
    std::vector<Memory::W> ws;
    for (auto& c : g.mem.read_all(svc(sdb), skey)) {
        if (c.instance == VAL && c.bag().kind == Kind::String) g.used_memory += (int64_t)c.bag().s.size();
        ws.push_back(Memory::W{dkey, c.instance, copy_bag(c.node)});
    }
    if (!ws.empty()) g.mem.write_many(svc(ddb), ws);      // every instance of the key appears in one step
    g.stats.dirty++;
}

// several string keys in one step (MSET)
void set_strings(int dbi, const std::vector<std::pair<std::string, std::string>>& kvs) {
    std::vector<Memory::W> ws;
    for (auto& kv : kvs) {
        VarPtr v = g.mem.var(svc(dbi), kv.first);
        CellPtr old = Memory::inst(v, VAL);
        int64_t delta = (int64_t)kv.second.size();
        if (old && old->bag->kind == Kind::String) delta -= (int64_t)old->bag->s.size();
        g.used_memory += delta;
        if (Memory::inst(v, TTL)) g.mem.remove(svc(dbi), kv.first, TTL);
        Bag b; b.kind = Kind::String; b.s = kv.second;
        ws.push_back(Memory::W{kv.first, VAL, std::move(b)});
    }
    g.mem.write_many(svc(dbi), ws);
    for (auto& kv : kvs) drop_pieces(dbi, kv.first);    // [PIECES_V1] a SET: the old edits are gone
    g.stats.dirty++;
}

// Liveness is derived, not maintained: a key with a ttl in the past is not there, whoever asks.
// Reclamation happens on the next touch of the key (live_var) -- nobody reaps in the background.
uint64_t version(int dbi, const std::string& key) {
    VarPtr v = live_var(dbi, key);
    return v ? v->last.load() : 0;
}

size_t expired_count(int dbi) { return g.mem.read_indexed(svc(dbi), INT64_MIN, Memory::now_ms()).size(); }

int64_t used_bytes() { return g.used_memory.load(); }
bool oom() { int64_t m = g.maxmemory.load(); return m > 0 && used_bytes() + 1024 * 1024 > m; }

} // namespace db


}  // namespace rr
