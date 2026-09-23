// Redis on Ribbit -- the memory, third cut: mutable, addressable RAM.
// The contract (network-shared-ram.md section 2): a cell has a current value; a write replaces it and takes the
// next write-order id; a read returns what is there now; instance open reads the partial index; a held read
// returns when something newer than N exists; remove returns the id removed or 0.
// Nothing here is a snapshot, and nothing here locks. A cell is a slot whose node {id, born, written_ms, bag}
// is replaced atomically: SET is one atomic exchange, contended by nobody (one writer per cell). A variable's
// instances and its numeric index are lock-free ordered maps (lfmap.hpp): an insert is a CAS, retried if two
// writers of the same key collide, never waited on. A region's name index and its birth/ttl indexes are the same
// maps. Readers hold raw pointers inside an epoch (ebr.hpp) and unlinked nodes are freed once every reader has
// moved on. Held reads wait on the variable's wait word; the writer bumps it and wakes only if someone is parked.
#pragma once
#include <string>
#include <vector>
#include <map>
#include <unordered_map>
#include <atomic>
#include <memory>
#include "lfmap.hpp"
#include <optional>
#include <functional>
#include <cstdint>

// live-object counters: what the memory is made of right now (INFO ribbit)
struct MemStats {
    std::atomic<long> vars{0}, nodes{0}, bags{0}, batches{0}, slots{0}, mapnodes{0}, maps{0};
    std::atomic<long> retired_pending{0}, retired_freed{0}, collects{0}, epoch_advances{0};
};
MemStats& memstats();

enum class Kind : uint8_t { None = 0, String = 1, Ttl = 2, HashField = 3, SetMember = 4, ListElem = 5, ZMember = 6, StreamEntry = 7, StreamMeta = 8, StreamGroup = 9, StreamPEL = 10, StreamConsumer = 11, Batch = 12 };

struct Bag {
    Bag() { memstats().bags.fetch_add(1, std::memory_order_relaxed); }
    Bag(const Bag& o) : kind(o.kind), s(o.s), n(o.n), d(o.d), indexed(o.indexed) { memstats().bags.fetch_add(1, std::memory_order_relaxed); }
    Bag(Bag&& o) noexcept : kind(o.kind), s(std::move(o.s)), n(o.n), d(o.d), indexed(o.indexed) { memstats().bags.fetch_add(1, std::memory_order_relaxed); }
    Bag& operator=(const Bag&) = default; Bag& operator=(Bag&&) = default;
    ~Bag() { memstats().bags.fetch_sub(1, std::memory_order_relaxed); }
    Kind kind = Kind::None;
    std::string s;          // String: the bytes
    int64_t n = 0;          // Ttl: absolute expiry, unix ms; ZMember: the score's order-preserving key
    double d = 0;           // ZMember: the score
    bool indexed = false;   // keep this cell in its variable's numeric index (by n); Ttl cells in the region's
};
using BagPtr = std::shared_ptr<const Bag>;

struct Batch;               // a batch is a value: the array of member nodes that land together (below)
struct CellNode {           // one version of one cell; never mutated
    CellNode() { memstats().nodes.fetch_add(1, std::memory_order_relaxed); }
    ~CellNode() { memstats().nodes.fetch_sub(1, std::memory_order_relaxed); }
    std::string instance;
    uint64_t id = 0;        // write-order id of the write that produced this value
    uint64_t born = 0;      // write-order id of the write that created the cell
    int64_t written_ms = 0;
    // the value lives in the node (own_) unless it is shared with another node (COPY, a batch handed a
    // BagPtr): one allocation for node + value in the common case, where there were two
    const Bag* bag = nullptr;
    Bag own_;
    BagPtr shared_;
    void set_bag(Bag&& b) { own_ = std::move(b); bag = &own_; }
    void set_bag(const BagPtr& p) { shared_ = p; bag = shared_.get(); }
    BagPtr bag_ptr() const { return shared_ ? shared_ : std::make_shared<const Bag>(*bag); }
    const Batch* batch = nullptr;   // set when this node landed as one member of a batch
    uint32_t batch_ix = 0;          // its position in the batch's array
    struct Slot* home = nullptr;    // set when this node lives inside its element's slot (see Slot)
};
// A batch is one value holding the array of writes that belong together. It exists, whole, before any of its
// members lands, so a reader that meets a member can see the entire batch and decide: if a later member of
// the same batch removes this variable, the cell is already gone; if a later member rewrites this instance,
// that is the version to report. Visibility is a property of the array, not of the cells: no state word, no
// pending flag. The batch owns its member nodes and goes when the last of them has been unlinked.
struct Batch {
    // the member nodes live inside the array: one allocation for the batch, the items contiguous (the
    // vector is sized once, before any member lands, so the addresses the slots hold never move)
    struct Item { std::string variable, instance; bool remove = false; bool whole = false; CellNode node; CellNode* landed = nullptr; };
    std::vector<Item> items;
    std::vector<CellNode*> spares;  // the rare re-landing on a fresh variable (the first landed in a dying one)
    uint64_t id = 0;
    std::atomic<long> live{0};      // member nodes still linked in a slot
    Batch() { memstats().batches.fetch_add(1, std::memory_order_relaxed); }
    ~Batch() { for (auto* s : spares) delete s; memstats().batches.fetch_sub(1, std::memory_order_relaxed); }
};
using CellPtr = const CellNode*;      // raw: valid inside the caller's EBR critical section

struct Cell {               // what a read hands back: the address plus the node
    std::string service, variable, instance;
    CellPtr node;
    const Bag& bag() const { return *node->bag; }
    uint64_t id() const { return node->id; }
    uint64_t born() const { return node->born; }
    int64_t written_ms() const { return node->written_ms; }
};

struct NumKey {             // (n, variable, instance): the region's ttl index key
    int64_t n; std::string variable, instance;
    bool operator<(const NumKey& o) const { if (n != o.n) return n < o.n; if (variable != o.variable) return variable < o.variable; return instance < o.instance; }
};
struct NumInst {            // (n, instance): a variable's numeric index key (sorted sets: score, then member)
    int64_t n; std::string instance;
    bool operator<(const NumInst& o) const { if (n != o.n) return n < o.n; return instance < o.instance; }
};

// a cell's slot: lives inside its instance-map node (one allocation for map node + slot)
// A cell's slot lives inside its instance-map node, and carries one node's worth of storage: the element's
// first version is built there (no allocation beyond the map node). Later versions are heap nodes; the
// storage is not reused (see retire_node) and is destroyed with the map node.
struct Slot {
    std::atomic<const CellNode*> node{nullptr};
    std::atomic<int> inl{0};                              // 0: storage free; 1: a node lives in it
    alignas(CellNode) unsigned char storage[sizeof(CellNode)];
    CellNode* inline_node() { return reinterpret_cast<CellNode*>(storage); }
    Slot() { memstats().slots.fetch_add(1, std::memory_order_relaxed); }
    Slot(const Slot& o) : node(o.node.load(std::memory_order_acquire)) { memstats().slots.fetch_add(1, std::memory_order_relaxed); }
    Slot& operator=(const Slot& o) { node.store(o.node.load(std::memory_order_acquire)); return *this; }
    ~Slot() { if (inl.load(std::memory_order_acquire)) inline_node()->~CellNode(); memstats().slots.fetch_sub(1, std::memory_order_relaxed); }
};

struct Ent { std::string key; CellPtr val; };          // one (instance, node) handed back by an index query
struct NEnt { NumInst key; CellPtr val; };

// A variable: the live thing. No lock anywhere: the value and ttl cells are atomic slots, the instance
// index and the numeric index are lock-free ordered maps. Command code holds a raw Var* inside an EBR
// critical section (one per command), and everything it reads stays valid for that long.
struct Var {
    std::string name;
    uint64_t born = 0;
    std::atomic<uint64_t> last{0};                   // id of the newest write or remove under this variable
    std::atomic<const CellNode*> val{nullptr};       // instance "" (the value cell)
    std::atomic<const CellNode*> ttl{nullptr};       // instance "\x01ttl"
    std::atomic<bool> dead{false};
    Var() { memstats().vars.fetch_add(1, std::memory_order_relaxed); }
    // the element maps exist only once a variable holds an element: a plain string key never pays for them
    using IMap = LFMap<std::string, Slot>; using NMap = LFMap<NumInst, Slot*>;
    std::atomic<IMap*> inst_p{nullptr};              // every other instance, in instance order
    std::atomic<NMap*> by_n_p{nullptr};              // indexed cells by numeric field (where on an indexed field)
    IMap& inst() { IMap* m = inst_p.load(std::memory_order_acquire); if (m) return *m; m = new IMap(); IMap* e = nullptr; if (!inst_p.compare_exchange_strong(e, m)) { delete m; return *e; } return *m; }
    NMap& by_n() { NMap* m = by_n_p.load(std::memory_order_acquire); if (m) return *m; m = new NMap(); NMap* e = nullptr; if (!by_n_p.compare_exchange_strong(e, m)) { delete m; return *e; } return *m; }
    const IMap* inst_c() const { return inst_p.load(std::memory_order_acquire); }
    const NMap* by_n_c() const { return by_n_p.load(std::memory_order_acquire); }

    ~Var();

    CellPtr get(const std::string& instance) const;
    size_t count() const;                             // instances present (val and ttl included when present)
    size_t inst_count() const { auto* m = inst_c(); return m ? m->size() : 0; }
    size_t rank(const std::string& lo) const { auto* m = inst_c(); return m ? m->rank(lo) : 0; }
    std::optional<Ent> nth(size_t i) const;
    void range(const std::string& lo, const std::string& hi, bool hi_unbounded, const std::function<bool(const std::string&, const CellPtr&)>& f) const;
    void rrange(const std::string& lo, const std::string& hi, bool hi_unbounded, const std::function<bool(const std::string&, const CellPtr&)>& f) const;
    void each(const std::function<bool(const std::string&, const CellPtr&)>& f) const;
    void each_all(const std::function<bool(const std::string&, const CellPtr&)>& f) const;
    size_t n_size() const { auto* m = by_n_c(); return m ? m->size() : 0; }
    size_t n_rank(const NumInst& k) const { auto* m = by_n_c(); return m ? m->rank(k) : 0; }
    std::optional<NEnt> n_nth(size_t i) const;
    void n_each(const std::function<bool(const NumInst&, const CellPtr&)>& f) const;
    void n_range(const NumInst& lo, const NumInst& hi, const std::function<bool(const NumInst&, const CellPtr&)>& f) const;
    void n_rrange(const NumInst& lo, const NumInst& hi, const std::function<bool(const NumInst&, const CellPtr&)>& f) const;
};
using VarPtr = Var*;

struct WaitWord { std::atomic<uint32_t> gen{0}; std::atomic<uint32_t> waiters{0}; };

class Region {
public:
    const size_t WORDS;
    std::unique_ptr<WaitWord[]> words;
    WaitWord any;
    // name -> variable: many small lock-free lists (3 levels: a head that fits one cache line) rather than one
    // tall skip list -- the lookup is one hash, one line, and a couple of string compares
    // name -> variable: 16384 buckets, each a 9-level lock-free skip list (at a million keys a bucket holds
    // ~60 names and a lookup is a few steps, not a scan). The array is allocated on the region's first use,
    // so the regions that stay empty (most dbs, the claims regions) cost nothing.
    using Bucket = LFMap<std::string, Var*, 8>;
    static const size_t BUCKETS = 16384;
    // hash indirection in front of the buckets: a direct-mapped table of Var* by name hash. A hit is one
    // load and one name compare -- no walk; a miss falls through to the bucket and fills the entry. Entries
    // are cleared before a dying variable is retired, so a stale pointer is never one to a freed variable.
    static const size_t DIRECT = 1u << 18;
    mutable std::atomic<std::atomic<Var*>*> direct_{nullptr};
    std::atomic<Var*>* direct() const {
        auto* d = direct_.load(std::memory_order_acquire); if (d) return d;
        d = new std::atomic<Var*>[DIRECT]; for (size_t i = 0; i < DIRECT; i++) d[i].store(nullptr, std::memory_order_relaxed);
        std::atomic<Var*>* e = nullptr; if (!direct_.compare_exchange_strong(e, d)) { delete[] d; return e; }
        return d;
    }
    std::atomic<Var*>& direct_h(size_t h) const { return direct()[(h >> 16) & (DIRECT - 1)]; }
    mutable std::atomic<Bucket*> buckets_{nullptr};
    Bucket* buckets() const {
        Bucket* b = buckets_.load(std::memory_order_acquire); if (b) return b;
        b = new Bucket[BUCKETS]; Bucket* e = nullptr;
        if (!buckets_.compare_exchange_strong(e, b)) { delete[] b; return e; }
        return b;
    }
    LFMap<uint64_t, Var*> by_born;                   // creation order (SCAN)
    LFMap<NumKey, Var*> by_ttl;                      // ttl cells by expiry (liveness is derived from it)
    std::atomic<long> nvars{0};

    explicit Region(size_t words_n = 4096) : WORDS(words_n), words(new WaitWord[words_n]) {}
    static size_t hash_of(const std::string& variable) { return std::hash<std::string>{}(variable); }
    WaitWord& word(const std::string& variable) const { return words[hash_of(variable) & (WORDS - 1)]; }
    Bucket& bucket(const std::string& variable) const { return buckets()[hash_of(variable) & (BUCKETS - 1)]; }
    WaitWord& word_h(size_t h) const { return words[h & (WORDS - 1)]; }
    Bucket& bucket_h(size_t h) const { return buckets()[h & (BUCKETS - 1)]; }
    void touched_h(size_t h, const std::string& variable);
    void touched(const std::string& variable);
    void touched_all();
    static void begin_quiet(std::vector<std::pair<Region*, std::string>>& held);   // hold wakes on this thread
    static void end_quiet();                                                      // deliver them, once each
    static void wait(WaitWord& w, uint32_t seen, int64_t timeout_ms);
    static void watch(WaitWord& w);       // register as a waiter BEFORE reading seen and checking the data
    static void unwatch(WaitWord& w);
};

class Memory {
public:
    Memory() = default;
    void init(const std::vector<std::string>& services, size_t wait_words = 4096);
    Region& region(const std::string& service) const;

    uint64_t write(const std::string& service, const std::string& variable, const std::string& instance, Bag bag);
    uint64_t write(const std::string& service, const std::string& variable, const std::string& instance, BagPtr bag);
    // the same, addressed: a caller that already holds the region and the variable's hash (a region API
    // resolving one key once per command) pays neither lookup again
    uint64_t write(Region& R, const std::string& variable, size_t hash, const std::string& instance, BagPtr bag);
    uint64_t write(Region& R, const std::string& variable, size_t hash, const std::string& instance, Bag bag);   // the value lands inside the node
    VarPtr var(Region& R, const std::string& variable, size_t hash) const;
    bool read(const std::string& service, const std::string& variable, const std::string& instance, Cell& out) const;
    VarPtr var(const std::string& service, const std::string& variable) const;     // the live variable, or null
    static CellPtr inst(const VarPtr& v, const std::string& instance) { return v ? v->get(instance) : nullptr; }
    // a member of a batch: a write, a removal of one instance (remove), or of the whole variable (whole)
    struct W {
        std::string variable, instance; BagPtr bag; Bag own; bool has_own = false; bool remove = false; bool whole = false;
        W() = default;
        W(std::string v, std::string i, BagPtr b) : variable(std::move(v)), instance(std::move(i)), bag(std::move(b)) {}
        W(std::string v, std::string i, Bag b) : variable(std::move(v)), instance(std::move(i)), own(std::move(b)), has_own(true) {}
        W(std::string v, std::string i, BagPtr b, bool rm, bool wh) : variable(std::move(v)), instance(std::move(i)), bag(std::move(b)), remove(rm), whole(wh) {}
    };
    // one write: the batch of one, exactly the write it always was. More than one: the array is published first
    // (it is the batch's own cell, in region "batches"), then the members land carrying a pointer to it; every
    // wake is deferred to the end so a held read wakes once and decides from the whole array
    uint64_t write_many(const std::string& service, const std::vector<W>& ws);
    // the version a reader should report for a node that landed as part of a batch (see Batch)
    static CellPtr resolve(CellPtr n);
    size_t remove_many(const std::string& service, const std::string& variable, const std::vector<std::string>& instances);
    size_t remove_many(const std::string& service, const std::vector<std::pair<std::string, std::string>>& rs);
    // no region snapshot exists: this is a view of the live region (multi-key atomicity is an application semantic)
    struct Snapshot { const Memory* m; const std::string* service; VarPtr var(const std::string& variable) const { return m->var(*service, variable); } };
    Snapshot snapshot(const std::string& service) const { return Snapshot{this, &region_name(service)}; }
    std::vector<Cell> read_all(const std::string& service, const std::string& variable) const;
    std::vector<Cell> read_range(const std::string& service, const std::string& variable, const std::string& lo, const std::string& hi,
                                 bool reverse = false, size_t offset = 0, size_t limit = 0, bool hi_unbounded = false) const;
    std::vector<Cell> read_indexed(const std::string& service, int64_t lo, int64_t hi, size_t limit = 0) const;   // ttl cells by expiry
    size_t indexed_count(const std::string& service) const;
    std::vector<Cell> read_after(const std::string& service, const std::string& variable, const std::string* instance,
                                 uint64_t after, int64_t wait_ms) const;
    size_t count(const std::string& service, const std::string& variable) const;
    uint64_t remove(const std::string& service, const std::string& variable, const std::string& instance);
    size_t remove_variable(const std::string& service, const std::string& variable, std::vector<BagPtr>* removed = nullptr);

    std::vector<std::string> variables_after(const std::string& service, uint64_t after_born, size_t count, uint64_t& next_born) const;
    size_t variable_count(const std::string& service) const;
    std::vector<std::string> variables(const std::string& service) const;
    bool random_variable(const std::string& service, std::string& out) const;
    std::vector<std::string> instances(const std::string& service, const std::string& variable) const;
    void each_cell(const std::string& service, const std::function<bool(const Cell&)>& f) const;

    uint64_t clear(const std::string& service);
    uint64_t swap(const std::string& a, const std::string& b);

    uint64_t last_id() const { return next_id_.load() - 1; }
    static int64_t now_ms();

private:
    alignas(64) std::atomic<uint64_t> next_id_{1};   // its own line: every writer bumps it
    struct RegionEntry { std::string name; std::unique_ptr<Region> r; };
    std::unordered_map<std::string, RegionEntry> regions_;
    const std::string& region_name(const std::string& service) const;
    uint64_t take_id() { return next_id_.fetch_add(1); }
    VarPtr var_or_create(Region& R, const std::string& variable, uint64_t id, size_t hash);
    VarPtr var_or_create(Region& R, const std::string& variable, uint64_t id) { return var_or_create(R, variable, id, Region::hash_of(variable)); }
    void apply(Region& R, Var& v, const std::string& instance, const BagPtr& bag, uint64_t id, int64_t now);
    void apply_node(Region& R, Var& v, CellNode* node);
    CellNode* apply_value(Region& R, Var& v, const std::string& instance, Bag&& bag, uint64_t id, int64_t now);   // places the node: in the slot when it can
    size_t remove_variable_quiet(Region& R, const std::string& variable, std::vector<BagPtr>* removed);
    bool drop(Region& R, Var& v, const std::string& instance, uint64_t id, CellPtr* gone);
    void maybe_die(Region& R, Var* v);
};
