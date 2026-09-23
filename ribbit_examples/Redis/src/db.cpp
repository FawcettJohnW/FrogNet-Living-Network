// The Redis key layer: a key is a variable in service "db<N>". Its value is the instance "" cell;
// its expiry is a ttl cell beside it. Readers decide: an expired value reads as absent.
#include "server.hpp"
#include <unordered_set>
#include <climits>
#include <algorithm>

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
    if (cur && cur->bag->kind == kind && cur->bag->s == value) return 0;
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
        if (cur && cur->bag->kind == kind && cur->bag->s == nv[0].second) return 0;   // the same truth again: nothing to publish
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
size_t drop_elems(int dbi, const std::string& key, const std::vector<std::string>& names) {
    VarPtr v = g.mem.var(svc(dbi), key);
    if (!v) return 0;
    std::vector<std::pair<std::string, std::string>> rs; size_t hit = 0;
    for (auto& n : names) { std::string inst = ELEM + n; if (Memory::inst(v, inst)) { hit++; rs.emplace_back(key, inst); } }
    if (!hit) return 0;
    if (hit >= elem_count(v) && Memory::inst(v, TTL)) rs.emplace_back(key, TTL);     // the key is gone: so is its ttl
    g.mem.remove_many(svc(dbi), rs);
    g.stats.dirty++;
    return hit;
}
void store_elems(int dbi, const std::string& key, Kind kind, const std::vector<std::pair<std::string, std::string>>& nv) {
    g.mem.remove_variable(svc(dbi), key);
    if (!nv.empty()) put_elems(dbi, key, kind, nv);     // two steps (remove, then publish): question-3 candidate for STORE
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
    std::vector<std::string> leftovers;
    if (v && ((!old && elem_count(v)) || (old && old->bag->kind != Kind::String))) v->each([&](const std::string& i, const CellPtr&) { leftovers.push_back(i); return true; });
    if (!keepttl && Memory::inst(v, TTL)) g.mem.remove(svc(dbi), key, TTL);
    Bag b; b.kind = Kind::String; b.s = val;
    uint64_t id = g.mem.write(svc(dbi), key, VAL, std::move(b));
    if (!leftovers.empty()) g.mem.remove_many(svc(dbi), key, leftovers);
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
        ws.push_back(Memory::W{dkey, c.instance, c.node->bag_ptr()});
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

