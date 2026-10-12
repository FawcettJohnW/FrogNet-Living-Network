"""apply_client_coalesce_lockless.py FROGRAM_CPP -- [CLIENT_COALESCE_LOCKLESS_V1] the client's coalescing
([CLIENT_COALESCES_V1]: identical calls in flight share one request and its answer) without its mutex (co_m).

The table is 4,096 slots of atomic entry pointers (key + the shared call). A call claims a free slot for its key and
leads, or finds its key's entry and joins the call in flight. An entry is taken out of the table BEFORE its call is
finished, so a call made after the answer is its own call, as before. Entries are freed through ribbit::Ebr, so a
caller still reading one is never handed freed memory. If no slot near the key's home is free, the call simply runs
uncoalesced. Applied at build after apply_client_applier.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''                          Session::Impl* co = nullptr; std::string co_key; };   // [CLIENT_COALESCES_V1] its entry, if any''',
    '''                          Session::Impl* co = nullptr; std::string co_key; const void* co_entry = nullptr; };   // [CLIENT_COALESCES_V1] its entry, if any''')
rep('''    std::mutex co_m; std::map<std::string, std::shared_ptr<Pending>> coalesce;''',
'''    // [CLIENT_COALESCE_LOCKLESS_V1] calls in flight, by the exact request
    struct CoEnt { std::string key; std::shared_ptr<Pending> p; };
    static constexpr size_t CO_N = 4096;
    std::atomic<const CoEnt*> co_slot[CO_N];
    ribbit::Ebr co_ebr;
    static size_t co_home(const std::string& k) { uint64_t h = 1469598103934665603ull; for (unsigned char c : k) { h ^= c; h *= 1099511628211ull; } return size_t(h); }
    void co_remove(const Pending* p) {
        const CoEnt* mine = static_cast<const CoEnt*>(p->co_entry); if (!mine) return;
        const size_t h0 = co_home(mine->key);
        for (size_t i = 0; i < 32; ++i) { const CoEnt* expect = mine; if (co_slot[(h0 + i) & (CO_N - 1)].compare_exchange_strong(expect, nullptr, std::memory_order_acq_rel)) break; }
        co_ebr.retire(mine);
    }''')
rep('''        if (p->co) {   // out of the table first: a request made after this answer is its own request
            std::lock_guard<std::mutex> g(p->co->co_m);
            auto it = p->co->coalesce.find(p->co_key); if (it != p->co->coalesce.end() && it->second == p) p->co->coalesce.erase(it);''',
'''        if (p->co) {   // out of the table first: a request made after this answer is its own request
            p->co->co_remove(p.get());                    // [CLIENT_COALESCE_LOCKLESS_V1]''')
rep('''    {   std::lock_guard<std::mutex> g(d_->co_m);
        auto it = d_->coalesce.find(key);
        if (it != d_->coalesce.end()) { p = it->second; joined = true; }
        else { p = std::make_shared<Pending>(); p->co = d_.get(); p->co_key = key; d_->coalesce.emplace(key, p); }''',
'''    {   // [CLIENT_COALESCE_LOCKLESS_V1] join the identical call in flight, or claim a slot and lead
        auto g = d_->co_ebr.guard(); const size_t h0 = Impl::co_home(key);
        for (size_t i = 0; i < 32 && !p; ++i) {
            std::atomic<const Impl::CoEnt*>& sl = d_->co_slot[(h0 + i) & (Impl::CO_N - 1)];
            const Impl::CoEnt* e = g.protect(sl);
            if (!e) {
                Impl::CoEnt* n = new Impl::CoEnt; n->key = key; n->p = std::make_shared<Pending>(); n->p->co = d_.get(); n->p->co_key = key; n->p->co_entry = n;
                const Impl::CoEnt* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { p = n->p; break; }
                n->p->co_entry = nullptr; delete n; e = expect; if (!e) continue;
            }
            if (e->key == key && !e->p->done.load(std::memory_order_acquire)) { p = e->p; joined = true; }
        }
        if (!p) p = std::make_shared<Pending>();          // no free slot near home: this call runs on its own''')
rep('''    for (uint32_t i = 0; i < Impl::RING; ++i) d_->ring[i].store(nullptr, std::memory_order_relaxed);   // [CLIENT_LOCKLESS_V1]''',
    '''    for (uint32_t i = 0; i < Impl::RING; ++i) d_->ring[i].store(nullptr, std::memory_order_relaxed);   // [CLIENT_LOCKLESS_V1]
    for (auto& x : d_->co_slot) x.store(nullptr, std::memory_order_relaxed);                             // [CLIENT_COALESCE_LOCKLESS_V1]''')
if '#include "ebr.hpp"' not in s:
    rep('#include <linux/futex.h>\n', '#include <linux/futex.h>\n#include "ebr.hpp"\n')
open(p, 'w').write(s)
print("client coalesce lockless applied")
