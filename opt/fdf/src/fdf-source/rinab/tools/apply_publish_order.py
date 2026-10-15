"""apply_publish_order.py RAM_MEMORY_HPP -- [PUBLISH_IN_ID_ORDER_V1] a reader that follows `after` never skips a write.

Ids are taken under each ROW's lock, so writes to different rows run in parallel and a smaller id can become visible
after a larger one. A reader of more than one row (a whole service, or a variable past its write window) that advanced
`after` to the largest id it got would then never see the smaller one. Callers that follow a whole service:
fdf-monitor and fdf-browser (term.hosts).

The rule: a watermark `published_` = the largest id such that every id at or below it is visible. A read with `after`
returns only ids at or below the watermark it loaded before scanning; anything newer is returned by a later read.

Nobody waits for anybody. A write makes its row visible as before, then marks its id done in a ring (id and row), then
moves the watermark forward over every consecutive done id it finds, whoever wrote them. A write's triggers fire once
the watermark has passed its id: by the write itself if the watermark is already past it, and by whichever write moves
the watermark past it otherwise (it fires the rows it passes), so the wake of a held read always comes after the cell
it is for can be returned. Ids the write window's claim loop takes and does not keep are marked done with no row.

The ring holds 65,536 ids. A write more than that far ahead of the watermark waits until the watermark is within
reach before marking (65,536 writes completing behind one unfinished write). Applied at build after
apply_fire_keys.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''        { std::unique_lock<std::shared_mutex> w(r->m);
          uint64_t p = wnd->head.load(std::memory_order_acquire);
          do { id = ++next_id_; } while (!wnd->head.compare_exchange_weak(p, p + 1, std::memory_order_acq_rel, std::memory_order_acquire));''',
'''        uint64_t burned[16]; size_t nb = 0; std::vector<uint64_t> more;   // [PUBLISH_IN_ID_ORDER_V1] ids the claim did not keep
        { std::unique_lock<std::shared_mutex> w(r->m);
          uint64_t p = wnd->head.load(std::memory_order_acquire);
          for (;;) {
              id = ++next_id_;
              if (wnd->head.compare_exchange_weak(p, p + 1, std::memory_order_acq_rel, std::memory_order_acquire)) break;
              if (nb < 16) burned[nb++] = id; else more.push_back(id);
          }''')
rep('''          r->id = id; r->bag = bag; r->updated = now; r->live = true; }
        fire(r);
        return id;''',
'''          r->id = id; r->bag = bag; r->updated = now; r->live = true; }
        for (size_t k = 0; k < nb; ++k) done(burned[k], nullptr);    // [PUBLISH_IN_ID_ORDER_V1]
        for (uint64_t x : more) done(x, nullptr);
        done(id, r);
        if (published_.load(std::memory_order_acquire) >= id) fire(r);   // else the write that moves the watermark past it fires it
        return id;''')
rep('''        std::vector<Cell> out; const double cutoff = now - fresh;
        auto take = [&](Row* r) {
            std::shared_lock<std::shared_mutex> g(r->m);
            if (!r->live) return;''',
'''        std::vector<Cell> out; const double cutoff = now - fresh;
        const uint64_t upto = published_.load(std::memory_order_acquire);   // [PUBLISH_IN_ID_ORDER_V1] loaded before the scan
        auto take = [&](Row* r) {
            std::shared_lock<std::shared_mutex> g(r->m);
            if (!r->live) return;
            if (after >= 0 && r->id > upto) return;   // [PUBLISH_IN_ID_ORDER_V1] a smaller id may not be visible yet''')
rep('''private:
    std::atomic<uint64_t> next_id_{0};''',
'''private:
    std::atomic<uint64_t> next_id_{0};
    // [PUBLISH_IN_ID_ORDER_V1] every id <= published_ is visible; ring_ holds the ids done and not yet passed
    static constexpr uint64_t RING = 1 << 16;
    struct Done { std::atomic<uint64_t> id{0}; std::atomic<Row*> row{nullptr}; };
    std::atomic<uint64_t> published_{0};
    Done* ring_ = new Done[RING];
    void done(uint64_t x, Row* r) {
        while (x > published_.load(std::memory_order_acquire) + RING) std::this_thread::yield();   // its slot is still in use
        Done& d = ring_[x % RING];
        d.row.store(r, std::memory_order_relaxed);
        d.id.store(x);   // seq_cst with the load below: of two writes finishing at once, one sees the other's mark
        for (uint64_t h = published_.load();;) {   // move the watermark over every done id
            Done& n = ring_[(h + 1) % RING];
            if (n.id.load() != h + 1) return;
            Row* nr = n.row.load(std::memory_order_relaxed);
            if (!published_.compare_exchange_strong(h, h + 1)) continue;   // h reloaded
            if (nr && nr != r) fire(nr);   // passed another write's id: its wake is ours to give
            ++h;
        }
    }''')
if '#include <thread>' not in s: rep('#include <shared_mutex>\n', '#include <shared_mutex>\n#include <thread>\n')
open(p, 'w').write(s)
print("publish in id order applied")
