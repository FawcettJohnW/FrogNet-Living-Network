#include "memory.hpp"
#include <chrono>
#include <random>
#include <stdexcept>
#include <algorithm>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <ctime>

MemStats& memstats() { static MemStats* s = new MemStats(); return *s; }
void memstats_mapnode(int d) { memstats().mapnodes.fetch_add(d, std::memory_order_relaxed); }
void memstats_map(int d) { memstats().maps.fetch_add(d, std::memory_order_relaxed); }
static const std::string VAL_INST = "";
static const std::string TTL_INST = std::string("\x01ttl", 4);

int64_t Memory::now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

// ---------------------------------------------------------------- wait words
static void futex_wait(std::atomic<uint32_t>& w, uint32_t seen, int64_t timeout_ms) {
    if (timeout_ms == 0) return;
    struct timespec ts, *tp = nullptr;
    if (timeout_ms > 0) { ts.tv_sec = timeout_ms / 1000; ts.tv_nsec = (timeout_ms % 1000) * 1000000L; tp = &ts; }
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, seen, tp, nullptr, 0);
}
// A writer bumps a word only when someone is registered on it. The waiter registers (a full-barrier RMW)
// BEFORE it reads `seen` and checks the data; the writer publishes, fences, then reads the registration.
// One of them sees the other: either the writer bumps, or the waiter's data check sees the write.
// Nobody parked means no shared line is written -- which is the common case, and the one that scales.
static void bump(WaitWord& w) {
    std::atomic_thread_fence(std::memory_order_seq_cst);
    if (w.waiters.load(std::memory_order_seq_cst) == 0) return;
    w.gen.fetch_add(1, std::memory_order_release);
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w.gen), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0);
}
void Region::wait(WaitWord& w, uint32_t seen, int64_t timeout_ms) { futex_wait(w.gen, seen, timeout_ms); }   // caller is registered
void Region::watch(WaitWord& w) { w.waiters.fetch_add(1, std::memory_order_seq_cst); }
void Region::unwatch(WaitWord& w) { w.waiters.fetch_sub(1, std::memory_order_seq_cst); }
// A quiet span: the wakes of every write inside it are held and delivered once at its end, so a held read
// wakes after the whole batch has landed (EXEC uses this; the memory itself uses it for write_many)
static thread_local std::vector<std::pair<Region*, std::string>>* quiet_span = nullptr;
void Region::begin_quiet(std::vector<std::pair<Region*, std::string>>& held) { quiet_span = &held; }
void Region::end_quiet() {
    auto* held = quiet_span; quiet_span = nullptr;
    if (!held) return;
    std::sort(held->begin(), held->end()); held->erase(std::unique(held->begin(), held->end()), held->end());
    for (auto& h : *held) h.first->touched(h.second);
    held->clear();
}
void Region::touched(const std::string& variable) { touched_h(hash_of(variable), variable); }
void Region::touched_h(size_t h, const std::string& variable) {
    if (quiet_span) { quiet_span->emplace_back(this, variable); return; }
    bump(word_h(h)); bump(any);
}
void Region::touched_all() { for (size_t i = 0; i < WORDS; i++) bump(words[i]); bump(any); }

// ---------------------------------------------------------------- Var
// A node that landed alone is retired on its own. A node that landed as a batch member belongs to its batch:
// unlinking it counts down the batch, and the batch (with every member node) is retired when the count reaches
// zero, so a reader still resolving through the array never loses the array under its feet.
// A node built in its slot's storage is never freed on its own: the storage lives and dies with the map node,
// and a reader in an older epoch may still hold the replaced version. (Reusing the storage after an EBR
// callback was tried and is unsafe: the callback and the map node's own reclamation can run on different
// threads in either order.) So the slot holds one version at most; later versions are heap nodes.
static void retire_node(const CellNode* n) {
    if (!n) return;
    if (n->home) return;                                       // the map node owns it; ~Slot runs its destructor
    if (!n->batch) { ebr::retire(const_cast<CellNode*>(n)); return; }
    Batch* b = const_cast<Batch*>(n->batch);
    if (b->live.fetch_sub(1, std::memory_order_acq_rel) == 1) ebr::retire(b);
}
static void free_node(const CellNode* n) {                 // from ~Var: already past every reader's epoch
    if (!n) return;
    if (n->home) return;                                   // ~Slot destroys it with the map node
    if (!n->batch) { delete n; return; }
    Batch* b = const_cast<Batch*>(n->batch);
    if (b->live.fetch_sub(1, std::memory_order_acq_rel) == 1) delete b;
}
CellPtr Memory::resolve(CellPtr n) {
    if (!n || !n->batch) return n;
    const Batch* b = n->batch;
    const std::string& var = b->items[n->batch_ix].variable;
    for (size_t i = n->batch_ix + 1; i < b->items.size(); i++) {
        const Batch::Item& it = b->items[i];
        if (it.variable != var) continue;
        if (it.whole) return nullptr;                                   // the batch removes this variable later
        if (it.instance != n->instance) continue;
        return it.remove ? nullptr : it.landed;                         // a later version of this very cell
    }
    return n;
}
Var::~Var() {
    memstats().vars.fetch_sub(1, std::memory_order_relaxed);
    free_node(val.load()); free_node(ttl.load());
    IMap* m = inst_p.load(); NMap* nm = by_n_p.load();
    if (m) { m->walk_all([](IMap::Node* n) { free_node(n->val.node.load()); return true; }); delete m; }
    delete nm;
}
CellPtr Var::get(const std::string& instance) const {
    if (instance.empty()) return Memory::resolve(val.load(std::memory_order_acquire));
    if (instance == TTL_INST) return Memory::resolve(ttl.load(std::memory_order_acquire));
    auto* m = inst_c(); if (!m) return nullptr;
    auto* n = m->get(instance);
    return n ? Memory::resolve(n->val.node.load(std::memory_order_acquire)) : nullptr;
}
size_t Var::count() const { return inst_count() + (val.load(std::memory_order_acquire) ? 1 : 0) + (ttl.load(std::memory_order_acquire) ? 1 : 0); }
std::optional<Ent> Var::nth(size_t i) const { auto* m = inst_c(); if (!m) return std::nullopt; auto* n = m->nth(i); if (!n) return std::nullopt; CellPtr c = Memory::resolve(n->val.node.load(std::memory_order_acquire)); if (!c) return std::nullopt; return Ent{n->key, c}; }
void Var::range(const std::string& lo, const std::string& hi, bool hi_unbounded, const std::function<bool(const std::string&, const CellPtr&)>& f) const {
    auto* m = inst_c(); if (!m) return;
    m->walk(lo, [&](Var::IMap::Node* n) { if (!hi_unbounded && hi < n->key) return false; CellPtr c = Memory::resolve(n->val.node.load(std::memory_order_acquire)); return c ? f(n->key, c) : true; });
}
void Var::rrange(const std::string& lo, const std::string& hi, bool hi_unbounded, const std::function<bool(const std::string&, const CellPtr&)>& f) const {
    std::vector<Ent> tmp;                                    // a reverse walk of a forward-linked list: collect, then walk back
    range(lo, hi, hi_unbounded, [&](const std::string& k, const CellPtr& c) { tmp.push_back(Ent{k, c}); return true; });
    for (auto it = tmp.rbegin(); it != tmp.rend(); ++it) if (!f(it->key, it->val)) return;
}
void Var::each(const std::function<bool(const std::string&, const CellPtr&)>& f) const {
    auto* m = inst_c(); if (!m) return;
    m->walk_all([&](Var::IMap::Node* n) { CellPtr c = Memory::resolve(n->val.node.load(std::memory_order_acquire)); return c ? f(n->key, c) : true; });
}
void Var::each_all(const std::function<bool(const std::string&, const CellPtr&)>& f) const {
    CellPtr v = Memory::resolve(val.load(std::memory_order_acquire)); if (v && !f(VAL_INST, v)) return;
    CellPtr t = Memory::resolve(ttl.load(std::memory_order_acquire)); if (t && !f(TTL_INST, t)) return;
    each(f);
}
std::optional<NEnt> Var::n_nth(size_t i) const { auto* m = by_n_c(); if (!m) return std::nullopt; auto* n = m->nth(i); if (!n) return std::nullopt; CellPtr c = Memory::resolve(n->val->node.load(std::memory_order_acquire)); if (!c) return std::nullopt; return NEnt{n->key, c}; }
void Var::n_each(const std::function<bool(const NumInst&, const CellPtr&)>& f) const {
    auto* m = by_n_c(); if (!m) return;
    m->walk_all([&](LFMap<NumInst, Slot*>::Node* n) { CellPtr c = Memory::resolve(n->val->node.load(std::memory_order_acquire)); return c ? f(n->key, c) : true; });
}
void Var::n_range(const NumInst& lo, const NumInst& hi, const std::function<bool(const NumInst&, const CellPtr&)>& f) const {
    auto* m = by_n_c(); if (!m) return;
    m->walk(lo, [&](LFMap<NumInst, Slot*>::Node* n) { if (hi < n->key) return false; CellPtr c = Memory::resolve(n->val->node.load(std::memory_order_acquire)); return c ? f(n->key, c) : true; });
}
void Var::n_rrange(const NumInst& lo, const NumInst& hi, const std::function<bool(const NumInst&, const CellPtr&)>& f) const {
    std::vector<NEnt> tmp;
    n_range(lo, hi, [&](const NumInst& k, const CellPtr& c) { tmp.push_back(NEnt{k, c}); return true; });
    for (auto it = tmp.rbegin(); it != tmp.rend(); ++it) if (!f(it->key, it->val)) return;
}

// ---------------------------------------------------------------- regions
void Memory::init(const std::vector<std::string>& services, size_t wait_words) {
    for (auto& s : services) { RegionEntry e; e.name = s; e.r = std::make_unique<Region>(wait_words); regions_[s] = std::move(e); }
}
Region& Memory::region(const std::string& service) const {
    // service names are static strings (db::svc): a two-entry per-thread cache keyed on the string's address
    // turns the hash lookup into a pointer compare on the hot path
    struct Ent { const char* p; size_t n; Region* r; };
    static thread_local Ent cache[2] = {{nullptr, 0, nullptr}, {nullptr, 0, nullptr}};
    static thread_local int next = 0;
    for (auto& e : cache) if (e.p == service.data() && e.n == service.size()) return *e.r;
    auto it = regions_.find(service);
    if (it == regions_.end()) throw std::runtime_error("no such region: " + service);
    cache[next] = Ent{service.data(), service.size(), it->second.r.get()}; next ^= 1;
    return *it->second.r;
}
const std::string& Memory::region_name(const std::string& service) const {
    auto it = regions_.find(service);
    if (it == regions_.end()) throw std::runtime_error("no such region: " + service);
    return it->second.name;
}
VarPtr Memory::var(const std::string& service, const std::string& variable) const {
    return var(region(service), variable, Region::hash_of(variable));
}
VarPtr Memory::var(Region& R, const std::string& variable, size_t hash) const {
    auto& d = R.direct_h(hash);
    Var* c = d.load(std::memory_order_acquire);
    if (c && c->name == variable && !c->dead.load(std::memory_order_acquire)) return c;
    auto* n = R.bucket_h(hash).get(variable);
    if (!n) return nullptr;
    d.store(n->val, std::memory_order_release);
    return n->val;
}
VarPtr Memory::var_or_create(Region& R, const std::string& variable, uint64_t id, size_t hash) {
    auto& d = R.direct_h(hash);
    { Var* c = d.load(std::memory_order_acquire); if (c && c->name == variable && !c->dead.load(std::memory_order_acquire)) return c; }
    auto& B = R.bucket_h(hash);
    auto* n = B.get(variable);
    if (n) { d.store(n->val, std::memory_order_release); return n->val; }
    Var* v = new Var(); v->name = variable; v->born = id;
    Region::Bucket::Node* existing = nullptr;
    if (!B.add(variable, v, &existing)) { delete v; return existing->val; }     // someone else created it first
    R.by_born.add(id, v);
    R.nvars.fetch_add(1, std::memory_order_relaxed);
    d.store(v, std::memory_order_release);
    return v;
}
// after a remove: if the variable holds nothing, it dies. dead is set BEFORE the emptiness check so a
// writer who lands in the window sees it and redoes its write on a fresh variable.
void Memory::maybe_die(Region& R, Var* v) {
    bool f = false;
    if (!v->dead.compare_exchange_strong(f, true)) return;
    if (v->val.load() || v->ttl.load() || v->inst_count()) { v->dead.store(false); return; }
    Var* out = nullptr;
    auto& B = R.bucket(v->name);
    auto* n = B.get(v->name);
    if (!n || n->val != v) { v->dead.store(false); return; }
    if (!B.remove(v->name, &out) || out != v) return;
    R.by_born.remove(v->born);
    R.nvars.fetch_sub(1, std::memory_order_relaxed);
    { auto& d = R.direct_h(Region::hash_of(v->name)); Var* e = v; d.compare_exchange_strong(e, nullptr); }
    ebr::retire(v);
}

// ---------------------------------------------------------------- write
void Memory::apply(Region& R, Var& v, const std::string& instance, const BagPtr& bag, uint64_t id, int64_t now) {
    CellNode* node = new CellNode();
    node->instance = instance; node->id = id; node->written_ms = now; node->set_bag(bag);
    apply_node(R, v, node);
}
void Memory::apply_node(Region& R, Var& v, CellNode* node) {
    const std::string& instance = node->instance; const Bag* bag = node->bag; uint64_t id = node->id;
    if (instance.empty()) {
        const CellNode* old = v.val.load(std::memory_order_acquire);
        node->born = old ? old->born : id;
        old = v.val.exchange(node, std::memory_order_acq_rel);
        retire_node(old);
    } else if (instance == TTL_INST) {
        const CellNode* old = v.ttl.load(std::memory_order_acquire);
        node->born = old ? old->born : id;
        old = v.ttl.exchange(node, std::memory_order_acq_rel);
        if (old) { R.by_ttl.remove(NumKey{old->bag->n, v.name, TTL_INST}); retire_node(old); }
        R.by_ttl.add(NumKey{bag->n, v.name, TTL_INST}, &v);
    } else {
        Slot fresh; fresh.node.store(node);
        Var::IMap::Node* mn = nullptr;
        if (v.inst().add(instance, fresh, &mn)) {                 // the slot lives in the map node
            node->born = id;
            if (bag->indexed) v.by_n().add(NumInst{bag->n, instance}, &mn->val);
        } else {
            Slot* slot = &mn->val;
            const CellNode* old = slot->node.load(std::memory_order_acquire);
            node->born = old ? old->born : id;
            old = slot->node.exchange(node, std::memory_order_acq_rel);
            if (old && old->bag->indexed) v.by_n().remove(NumInst{old->bag->n, instance});
            if (bag->indexed) v.by_n().add(NumInst{bag->n, instance}, slot);
            retire_node(old);
        }
    }
    v.last.store(id, std::memory_order_release);
}
uint64_t Memory::write(const std::string& service, const std::string& variable, const std::string& instance, Bag bag) {
    return write(region(service), variable, Region::hash_of(variable), instance, std::move(bag));
}
CellNode* Memory::apply_value(Region& R, Var& v, const std::string& instance, Bag&& bag, uint64_t id, int64_t now) {
    if (instance.empty() || instance == TTL_INST) {
        CellNode* node = new CellNode();
        node->instance = instance; node->id = id; node->written_ms = now; node->set_bag(std::move(bag));
        apply_node(R, v, node); return node;
    }
    Var::IMap::Node* mn = nullptr;
    bool fresh = v.inst().add(instance, Slot(), &mn);
    Slot* slot = &mn->val;
    CellNode* node;
    int e = 0;
    if (slot->inl.compare_exchange_strong(e, 1, std::memory_order_acq_rel)) { node = new (slot->storage) CellNode(); node->home = slot; }
    else node = new CellNode();
    node->instance = instance; node->id = id; node->written_ms = now; node->set_bag(std::move(bag));
    const CellNode* old = slot->node.load(std::memory_order_acquire);
    node->born = (fresh || !old) ? id : old->born;
    old = slot->node.exchange(node, std::memory_order_acq_rel);
    if (old && old->bag->indexed) v.by_n().remove(NumInst{old->bag->n, instance});
    if (node->bag->indexed) v.by_n().add(NumInst{node->bag->n, instance}, slot);
    retire_node(old);
    v.last.store(id, std::memory_order_release);
    return node;
}
uint64_t Memory::write(Region& R, const std::string& variable, size_t hash, const std::string& instance, Bag bag) {
    ebr::Guard g;
    for (;;) {
        uint64_t id = take_id();
        Var* v = var_or_create(R, variable, id, hash);
        Bag keep = bag;                                            // a dying variable means landing again, on a fresh one
        apply_value(R, *v, instance, std::move(bag), id, now_ms());
        if (v->dead.load(std::memory_order_acquire)) { bag = std::move(keep); continue; }
        R.touched_h(hash, variable);
        return id;
    }
}
uint64_t Memory::write(const std::string& service, const std::string& variable, const std::string& instance, BagPtr bag) {
    return write(region(service), variable, Region::hash_of(variable), instance, std::move(bag));
}
uint64_t Memory::write(Region& R, const std::string& variable, size_t hash, const std::string& instance, BagPtr bag) {
    ebr::Guard g;
    for (;;) {
        uint64_t id = take_id();
        Var* v = var_or_create(R, variable, id, hash);
        apply(R, *v, instance, bag, id, now_ms());
        if (v->dead.load(std::memory_order_acquire)) continue;       // landed in a dying variable: again, on a fresh one
        R.touched_h(hash, variable);
        return id;
    }
}
uint64_t Memory::write_many(const std::string& service, const std::vector<W>& ws) {
    Region& R = region(service);
    ebr::Guard g;
    if (ws.size() == 1 && !ws[0].remove && !ws[0].whole) return ws[0].has_own ? write(service, ws[0].variable, ws[0].instance, Bag(ws[0].own)) : write(service, ws[0].variable, ws[0].instance, ws[0].bag);
    int64_t now = now_ms();
    // the array first: one value, published whole, before any member lands
    Batch* b = new Batch(); b->id = take_id(); b->items.reserve(ws.size());
    long writes = 0;
    for (auto& w : ws) {
        b->items.emplace_back();
        Batch::Item& it = b->items.back(); it.variable = w.variable; it.instance = w.instance; it.remove = w.remove; it.whole = w.whole;
        if (!w.remove && !w.whole) {
            it.node.instance = w.instance; it.node.written_ms = now; if (w.has_own) it.node.set_bag(Bag(w.own)); else it.node.set_bag(w.bag);
            it.node.batch = b; it.node.batch_ix = (uint32_t)(b->items.size() - 1); it.landed = &it.node; writes++;
        }
    }
    b->live.store(writes);
    // the array as a cell: region "batches" holds one variable per writing thread, "the batch in flight on
    // this thread"; publishing is one exchange on that cell, before any member lands. The cell keeps the last
    // batch's array afterwards -- a history value, not garbage -- so nothing is created or destroyed per batch.
    Region* BR = nullptr; { auto bit = regions_.find("batches"); if (bit != regions_.end()) BR = bit->second.r.get(); }
    if (BR) {
        static std::atomic<int> next_tid{0}; static thread_local int tid = -1; if (tid < 0) tid = next_tid.fetch_add(1);
        static thread_local std::string bname; if (bname.empty()) bname = "thread" + std::to_string(tid);
        Bag desc; desc.kind = Kind::Batch; desc.n = (int64_t)b->id; desc.s.reserve(b->items.size() * 24);
        for (auto& it : b->items) { desc.s += it.whole ? "-" : it.remove ? "~" : "+"; desc.s += it.variable; desc.s += '\n'; }
        Var* bv = var_or_create(*BR, bname, b->id); apply(*BR, *bv, VAL_INST, std::make_shared<const Bag>(std::move(desc)), b->id, now);
    }
    // then the members, in array order; every wake waits for the end
    uint64_t last = b->id;
    for (auto& it : b->items) {
        if (it.whole) { remove_variable_quiet(R, it.variable, nullptr); continue; }
        if (it.remove) { Var* v = var(service, it.variable); if (v) { uint64_t id = take_id(); if (drop(R, *v, it.instance, id, nullptr)) maybe_die(R, v); } continue; }
        size_t h = Region::hash_of(it.variable);
        for (;;) {
            it.landed->id = last = take_id();
            Var* v = var_or_create(R, it.variable, last, h);
            apply_node(R, *v, it.landed);
            if (!v->dead.load(std::memory_order_acquire)) break;
            // landed in a dying variable: that node is unlinked with it; land a fresh copy on a fresh variable
            CellNode* n2 = new CellNode(); n2->instance = it.node.instance; n2->written_ms = now; if (it.node.shared_) n2->set_bag(it.node.shared_); else n2->set_bag(Bag(it.node.own_)); n2->batch = b; n2->batch_ix = it.node.batch_ix;
            b->spares.push_back(n2); b->live.fetch_add(1); it.landed = n2;
        }
    }
    if (writes == 0) ebr::retire(b);                                   // nothing links to it: it goes with the epoch
    for (auto& w : ws) R.touched(w.variable);
    return last;
}
// ---------------------------------------------------------------- reads
static Cell mk_cell(const std::string& s, const std::string& v, const std::string& i, const CellPtr& n) { Cell c; c.service = s; c.variable = v; c.instance = i; c.node = n; return c; }

bool Memory::read(const std::string& service, const std::string& variable, const std::string& instance, Cell& out) const {
    VarPtr v = var(service, variable); if (!v) return false;
    CellPtr n = v->get(instance); if (!n) return false;
    out = mk_cell(service, variable, instance, n); return true;
}
std::vector<Cell> Memory::read_all(const std::string& service, const std::string& variable) const {
    std::vector<Cell> r; VarPtr v = var(service, variable); if (!v) return r;
    v->each_all([&](const std::string& i, const CellPtr& c) { r.push_back(mk_cell(service, variable, i, c)); return true; });
    return r;
}
std::vector<Cell> Memory::read_range(const std::string& service, const std::string& variable, const std::string& lo, const std::string& hi,
                                     bool reverse, size_t offset, size_t limit, bool hi_unbounded) const {
    std::vector<Cell> r; VarPtr v = var(service, variable); if (!v) return r;
    auto take = [&](const std::string& i, const CellPtr& c) { if (offset) { offset--; return true; } r.push_back(mk_cell(service, variable, i, c)); return !(limit && r.size() >= limit); };
    if (!reverse) v->range(lo, hi, hi_unbounded, take); else v->rrange(lo, hi, hi_unbounded, take);
    return r;
}
std::vector<Cell> Memory::read_indexed(const std::string& service, int64_t lo, int64_t hi, size_t limit) const {
    std::vector<Cell> r; Region& R = region(service);
    R.by_ttl.walk(NumKey{lo, "", ""}, [&](LFMap<NumKey, Var*>::Node* n) {
        if (n->key.n > hi) return false;
        CellPtr c = n->val->ttl.load(std::memory_order_acquire);
        if (c && c->bag->n == n->key.n) { r.push_back(mk_cell(service, n->key.variable, n->key.instance, c)); if (limit && r.size() >= limit) return false; }
        return true;
    });
    return r;
}
size_t Memory::indexed_count(const std::string& service) const { return region(service).by_ttl.size(); }

std::vector<Cell> Memory::read_after(const std::string& service, const std::string& variable, const std::string* instance,
                                     uint64_t after, int64_t wait_ms) const {
    Region& R = region(service);
    WaitWord& w = R.word(variable);
    Region::watch(w); struct Un { WaitWord& w; ~Un() { Region::unwatch(w); } } un{w};
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(wait_ms > 0 ? wait_ms : 0);
    for (;;) {
        uint32_t seen = w.gen.load(std::memory_order_acquire);
        std::vector<Cell> out;
        VarPtr v = var(service, variable);
        if (v && v->last.load(std::memory_order_acquire) > after) {
            v->each_all([&](const std::string& i, const CellPtr& c) { if (c->id > after && (!instance || i == *instance)) out.push_back(mk_cell(service, variable, i, c)); return true; });
            std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id() < b.id(); });
        }
        if (!out.empty() || wait_ms == 0) return out;
        int64_t left = -1;
        if (wait_ms > 0) { left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count(); if (left <= 0) return out; }
        ebr::exit(); Region::wait(w, seen, left); ebr::enter();     // a parked participant holds nobody's garbage
    }
}
size_t Memory::count(const std::string& service, const std::string& variable) const { VarPtr v = var(service, variable); return v ? v->count() : 0; }

// ---------------------------------------------------------------- remove
bool Memory::drop(Region& R, Var& v, const std::string& instance, uint64_t id, CellPtr* gone) {
    const CellNode* old;
    if (instance.empty()) old = v.val.exchange(nullptr, std::memory_order_acq_rel);
    else if (instance == TTL_INST) {
        old = v.ttl.exchange(nullptr, std::memory_order_acq_rel);
        if (old) R.by_ttl.remove(NumKey{old->bag->n, v.name, TTL_INST});
    } else {
        auto* m = v.inst_c(); if (!m) return false;
        auto* mn = m->get(instance); if (!mn) return false;
        old = mn->val.node.exchange(nullptr, std::memory_order_acq_rel);      // the cell is gone before its map node is
        if (!old) return false;
        if (old->bag->indexed) v.by_n().remove(NumInst{old->bag->n, instance});
        const_cast<Var::IMap*>(m)->remove(instance);                            // node + slot retired together
    }
    if (!old) return false;
    v.last.store(id, std::memory_order_release);
    if (gone) *gone = old;
    retire_node(old);
    return true;
}
uint64_t Memory::remove(const std::string& service, const std::string& variable, const std::string& instance) {
    Region& R = region(service);
    ebr::Guard g;
    VarPtr v = var(service, variable); if (!v) return 0;
    uint64_t id = take_id(); CellPtr gone = nullptr;
    if (!drop(R, *v, instance, id, &gone)) return 0;
    uint64_t gid = gone->id;
    maybe_die(R, v);
    R.touched(variable);
    return gid;
}
size_t Memory::remove_many(const std::string& service, const std::string& variable, const std::vector<std::string>& instances) {
    Region& R = region(service);
    ebr::Guard g;
    VarPtr v = var(service, variable); if (!v) return 0;
    uint64_t id = take_id(); size_t n = 0;
    for (auto& i : instances) if (drop(R, *v, i, id, nullptr)) n++;
    if (n) { maybe_die(R, v); R.touched(variable); }
    return n;
}
size_t Memory::remove_many(const std::string& service, const std::vector<std::pair<std::string, std::string>>& rs) {
    Region& R = region(service);
    ebr::Guard g;
    uint64_t id = take_id(); size_t n = 0;
    std::vector<Var*> touched;
    for (auto& r : rs) { VarPtr v = var(service, r.first); if (!v) continue; if (drop(R, *v, r.second, id, nullptr)) { n++; touched.push_back(v); } }
    for (auto* v : touched) { std::string name = v->name; maybe_die(R, v); R.touched(name); }
    return n;
}
size_t Memory::remove_variable_quiet(Region& R, const std::string& variable, std::vector<BagPtr>* removed) {
    auto& B = R.bucket(variable);
    Var* v = nullptr;
    if (!B.remove(variable, &v) || !v) return 0;
    v->dead.store(true);
    uint64_t id = take_id(); size_t n = 0;
    v->each_all([&](const std::string&, const CellPtr& c) { n++; if (removed) removed->push_back(c->bag_ptr()); return true; });
    const CellNode* t = v->ttl.load();
    if (t) R.by_ttl.remove(NumKey{t->bag->n, variable, TTL_INST});
    R.by_born.remove(v->born);
    R.nvars.fetch_sub(1, std::memory_order_relaxed);
    v->last.store(id);
    { auto& d = R.direct_h(Region::hash_of(variable)); Var* e = v; d.compare_exchange_strong(e, nullptr); }
    ebr::retire(v);                               // the variable and its cells go once no reader holds them
    return n;
}

size_t Memory::remove_variable(const std::string& service, const std::string& variable, std::vector<BagPtr>* removed) {
    Region& R = region(service);
    ebr::Guard g;
    size_t n = remove_variable_quiet(R, variable, removed);
    if (n) R.touched(variable);
    return n;
}

// ---------------------------------------------------------------- enumeration
std::vector<std::string> Memory::variables_after(const std::string& service, uint64_t after_born, size_t cnt, uint64_t& next_born) const {
    std::vector<std::string> r; next_born = 0; Region& R = region(service);
    uint64_t last = 0;
    R.by_born.walk(after_born + 1, [&](LFMap<uint64_t, Var*>::Node* n) { if (r.size() >= cnt) { next_born = last; return false; } r.push_back(n->val->name); last = n->key; return true; });
    return r;
}
size_t Memory::variable_count(const std::string& service) const { long n = region(service).nvars.load(); return n < 0 ? 0 : (size_t)n; }
std::vector<std::string> Memory::variables(const std::string& service) const {
    std::vector<std::string> r;
    region(service).by_born.walk_all([&](LFMap<uint64_t, Var*>::Node* n) { r.push_back(n->val->name); return true; });
    return r;
}
bool Memory::random_variable(const std::string& service, std::string& out) const {
    Region& R = region(service);
    size_t n = R.by_born.size(); if (!n) return false;
    static thread_local std::mt19937_64 rng{std::random_device{}()};
    auto* x = R.by_born.nth(rng() % n); if (!x) return false;
    out = x->val->name; return true;
}
std::vector<std::string> Memory::instances(const std::string& service, const std::string& variable) const {
    std::vector<std::string> r; VarPtr v = var(service, variable); if (!v) return r;
    v->each_all([&](const std::string& i, const CellPtr&) { r.push_back(i); return true; });
    return r;
}
void Memory::each_cell(const std::string& service, const std::function<bool(const Cell&)>& f) const {
    Region& R = region(service);
    bool go = true;
    R.by_born.walk_all([&](LFMap<uint64_t, Var*>::Node* n) { Var* v = n->val; v->each_all([&](const std::string& i, const CellPtr& c) { go = f(mk_cell(service, v->name, i, c)); return go; }); return go; });
}

// ---------------------------------------------------------------- region-wide
uint64_t Memory::clear(const std::string& service) {
    Region& R = region(service);
    ebr::Guard g;
    std::vector<std::string> names = variables(service);
    for (auto& n : names) remove_variable(service, n, nullptr);
    uint64_t id = take_id(); R.touched_all(); return id;
}
uint64_t Memory::swap(const std::string& a, const std::string& b) {
    // moves every variable of each region into the other: N unlinks and N relinks, not one instant (question 3)
    Region& A = region(a); Region& B = region(b);
    ebr::Guard g;
    auto lift = [&](Region& R) {
        std::vector<Var*> vs;
        R.by_born.walk_all([&](LFMap<uint64_t, Var*>::Node* n) { vs.push_back(n->val); return true; });
        for (Var* v : vs) { Var* out = nullptr; R.bucket(v->name).remove(v->name, &out); R.by_born.remove(v->born); const CellNode* t = v->ttl.load(); if (t) R.by_ttl.remove(NumKey{t->bag->n, v->name, TTL_INST}); auto& d = R.direct_h(Region::hash_of(v->name)); Var* e = v; d.compare_exchange_strong(e, nullptr); }
        R.nvars.store(0);
        return vs;
    };
    auto place = [&](Region& R, const std::vector<Var*>& vs) {
        for (Var* v : vs) { R.bucket(v->name).add(v->name, v); R.by_born.add(v->born, v); const CellNode* t = v->ttl.load(); if (t) R.by_ttl.add(NumKey{t->bag->n, v->name, TTL_INST}, v); }
        R.nvars.store((long)vs.size());
    };
    auto va = lift(A), vb = lift(B);
    place(A, vb); place(B, va);
    uint64_t id = take_id(); A.touched_all(); B.touched_all(); return id;
}
