// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_space.cpp -- [SPACE_V1] see ribbit_space.hpp.
#include "ribbit_space.hpp"
#include "semtpl.hpp"
#include "frogram.hpp"
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>
#include <unordered_map>

namespace ribbit {
namespace {
double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
// [NODE_IS_ITS_OWN_RAM_V1] the memory, held in this process, answering /<vendor>-api to other nodes
class HeldHost : public RamHost {
public:
    explicit HeldHost(std::string v) : v_(std::move(v)) {}
    std::string vendor_id() const override { return v_; }
    std::string vendor_version() const override { return "space"; }
private:
    std::string v_;
};
}  // namespace

struct Space::Impl {
    std::string where, vendor; bool local = false;
    std::unique_ptr<frogram::Session> s; std::unique_ptr<frogram::Memory> mem;   // a memory elsewhere
    ramm::Memory* M = nullptr;                                                    // the memory here
    std::unique_ptr<HeldHost> host;
};

Space::Space(const std::string& where, const std::string& vendor) : d_(new Impl) {
    d_->where = where; d_->vendor = vendor;
    if (where == "here" || where.rfind("here:", 0) == 0) {
        d_->local = true; d_->M = &RamHost::memory();
        if (where.size() > 5) {
            d_->host.reset(new HeldHost(vendor));
            if (int rc = d_->host->start(where.substr(5), true))
                throw std::runtime_error("the memory here could not listen on " + where.substr(5) + ": " + strerror(rc));
        }
        return;
    }
    const size_t c = where.rfind(':');
    if (c == std::string::npos) throw std::runtime_error("the memory's address is HOST:PORT or here[:HOST:PORT], not " + where);
    d_->s.reset(new frogram::Session(where.substr(0, c), atoi(where.c_str() + c + 1), "/" + vendor + "-api"));
    d_->mem.reset(new frogram::Memory(*d_->s, "/" + vendor + "-api"));
}
Space::~Space() { if (d_->s) d_->s->flush(); }
bool Space::local() const { return d_->local; }
const std::string& Space::where() const { return d_->where; }

// [DECODE_ONCE_V1] the memory here stores a bag as its array: decoded once, at the write, and never printed for itself
void Space::write(const std::string& sp, const std::string& topic, const std::string& inst, const std::string& bag) {
    if (!d_->local) { d_->mem->write_nowait(sp, topic, inst, bag); return; }
    ribbit::BagArrP a = ribbit::BagArr::parse(bag);
    if (a) d_->M->write(sp, topic, inst, std::move(a), wall()); else d_->M->write(sp, topic, inst, bag, wall());
}
void Space::write_fields(const std::string& sp, const std::string& topic, const std::string& inst,
                         const std::vector<std::string>& order, semcodec::Fields f) {
    if (!d_->local) { d_->mem->write_fields_nowait(sp, topic, inst, order, std::move(f)); return; }
    thread_local std::unordered_map<const void*, std::shared_ptr<const std::vector<std::string>>> names;   // the shape's names, once per thread
    auto& nm = names[&order]; if (!nm || *nm != order) nm = std::make_shared<const std::vector<std::string>>(order);
    auto a = std::make_shared<ribbit::BagArr>(); a->names = nm; a->vals.reserve(f.size());
    for (auto& kv : f) a->vals.push_back(std::move(kv.second));
    d_->M->write(sp, topic, inst, ribbit::BagArrP(std::move(a)), wall());
}
void Space::linger(const std::string& sp, const std::string& topic, int32_t seconds) { if (d_->s) d_->s->linger(sp, topic, seconds); }
std::vector<SpaceRow> Space::read(const std::string& sp, const std::string& topic, const std::string& inst, long long after, double wait_s) {
    std::vector<SpaceRow> out;
    if (d_->local) {
        auto eval = [&] { return d_->M->match(sp, topic, inst, after, 0, wall()); };
        std::vector<ramm::Cell> cells = eval();
        if (cells.empty() && after >= 0 && wait_s > 0)
            cells = d_->M->wait(sp, topic, inst, std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s), eval,
                                [](const std::vector<ramm::Cell>& r) { return !r.empty(); });
        for (auto& c : cells) out.push_back(SpaceRow{c.id, std::move(c.variable), std::move(c.instance), std::move(c.bag), std::move(c.arr), c.gone});
        return out;
    }
    for (auto& r : d_->mem->read_raw(sp, topic, inst, after < 0 ? 0 : after, wait_s, 0)) {
        if (r.gone && after < 0) continue;            // [GONE_WITH_ITS_WRITER_V1] what is there now: only the live
        out.push_back(SpaceRow{r.id, std::move(r.variable), std::move(r.instance), std::move(r.bag), std::move(r.arr), r.gone});
    }
    return out;
}
uint64_t Space::newest(const std::string& sp, const std::string& topic) {
    return d_->local ? d_->M->newest(sp, topic) : d_->mem->newest_id(sp, topic);
}
// [STANDING_WATCH_V1] A held read on a family: a thread of its own is handed each batch in the family's write order.
//   here        the memory's own cursor on the family and a hook on it (FAMILY_HOOKS_V1): a write to the family bumps
//               the read's word; the thread reads by position what is new and waits on its word when there is nothing.
//   elsewhere   watch: a held read asked again after each answer. follow (below): one standing follow, streamed.
// The first batch comes at once: what is there past the id, or none for a read from now on.
void Space::watch(const std::string& sp, const std::string& topic, long long after, OnRows on) {
    if (d_->local) {
        ramm::Memory* M = d_->M;
        std::thread([M, sp, topic, after, on = std::move(on)] {
            ramm::WakeWord* w = M->wake_word();
            ramm::Memory::Cursor c = M->cursor(sp, topic, "", after);
            ramm::Memory::hook(c, w);
            std::vector<ramm::Cell> cells; std::vector<SpaceRow> rows; bool first = true;
            for (;;) {
                const uint32_t seen = w->n.load(std::memory_order_acquire);
                cells.clear(); M->read(c, cells);
                if (!cells.empty() || first) {
                    first = false; rows.clear(); rows.reserve(cells.size());
                    for (auto& x : cells) rows.push_back(SpaceRow{x.id, std::move(x.variable), std::move(x.instance), std::move(x.bag), std::move(x.arr), x.gone});
                    on(rows);
                }
                if (cells.empty()) w->n.wait(seen, std::memory_order_acquire);
            }
        }).detach();
        return;
    }
    // elsewhere: a held read, asked again after each answer -- what the reader has not read yet stays in the memory
    // (a writer's ring may move past it: the reader is told by what it reads, not buffered here)
    std::thread([this, sp, topic, after, on = std::move(on)] {
        try {
            long long a = after;
            if (a < 0) { a = (long long)newest(sp, topic); std::vector<SpaceRow> none; on(none); }   // from now on
            else {                                                    // past an id: what is there now, at once
                std::vector<SpaceRow> rows = read(sp, topic, "", a, 0);
                for (auto& r : rows) if ((long long)r.id > a) a = (long long)r.id;
                on(rows);
            }
            for (;;) {
                std::vector<SpaceRow> rows = read(sp, topic, "", a, 30.0);   // held by the memory until something lands
                if (rows.empty()) continue;
                for (auto& r : rows) if ((long long)r.id > a) a = (long long)r.id;
                on(rows);
            }
        } catch (const std::exception& x) { fprintf(stderr, "ribbit space: the read held on %s/%s ended: %s\n", sp.c_str(), topic.c_str(), x.what()); }
    }).detach();
}
// [STANDING_WATCH_V1] follow: the same, but elsewhere the memory streams each batch as it lands (one standing follow on
// the session) into this read's own list, and its thread is woken: no request per batch. For a reader that keeps up:
// what it has not handled yet waits in this process, not in the memory.
void Space::follow(const std::string& sp, const std::string& topic, long long after, OnRows on,
                   std::function<void(const std::string&)> ended) {
    if (d_->local) { watch(sp, topic, after, std::move(on)); return; }
    struct Batch { std::vector<frogram::RawCell> rows; std::string err; Batch* next = nullptr; };
    struct Inbox { std::atomic<Batch*> head{nullptr}; std::atomic<uint32_t> gen{0}; };
    auto box = std::make_shared<Inbox>();
    d_->mem->follow(sp, topic, "", after, [box](std::vector<frogram::RawCell>& rows, const std::string& err) {
        Batch* b = new Batch; b->rows = std::move(rows); b->err = err;
        Batch* h = box->head.load(std::memory_order_relaxed);
        do { b->next = h; } while (!box->head.compare_exchange_weak(h, b, std::memory_order_release, std::memory_order_relaxed));
        box->gen.fetch_add(1, std::memory_order_release); box->gen.notify_one();
    });
    std::thread([box, sp, topic, on = std::move(on), ended = std::move(ended)] {
        std::vector<Batch*> got; std::vector<SpaceRow> rows;
        for (;;) {
            const uint32_t g = box->gen.load(std::memory_order_acquire);
            got.clear();
            for (Batch* b = box->head.exchange(nullptr, std::memory_order_acquire); b; b = b->next) got.push_back(b);
            if (got.empty()) { box->gen.wait(g, std::memory_order_acquire); continue; }
            for (auto it = got.rbegin(); it != got.rend(); ++it) {   // oldest first: the family's write order
                std::unique_ptr<Batch> b(*it);
                if (!b->err.empty()) {
                    fprintf(stderr, "ribbit space: the read held on %s/%s ended: %s\n", sp.c_str(), topic.c_str(), b->err.c_str());
                    for (auto jt = it + 1; jt != got.rend(); ++jt) delete *jt;
                    if (ended) ended(b->err);
                    return;
                }
                rows.clear(); rows.reserve(b->rows.size());
                for (auto& r : b->rows) rows.push_back(SpaceRow{r.id, std::move(r.variable), std::move(r.instance), std::move(r.bag), std::move(r.arr), r.gone});
                on(rows);
            }
        }
    }).detach();
}
uint64_t Space::refused() const { return d_->s ? d_->s->stats().posts_refused : 0; }
bool Space::flush(double timeout_s) { return d_->s ? d_->s->flush(timeout_s) : true; }
std::string Space::stats() const {
    if (!d_->s) return "here";
    const auto st = d_->s->stats();
    auto u = [](uint64_t v) { return std::to_string(v); };
    return "cell_writes=" + u(st.cell_writes) + " cell_bytes=" + u(st.cell_bytes) + " refused=" + u(st.posts_refused) +
           " read_request_bytes=" + u(st.batch_bytes) + " rows_wire=" + u(st.rows_wire) + " rows_whole=" + u(st.rows_whole) +
           " engine_out=" + u(st.bytes_out) + " engine_in=" + u(st.bytes_in) + " raw=" + u(st.raw) + " full=" + u(st.full) +
           " diff=" + u(st.diff) + " repeat=" + u(st.repeat) + " same=" + u(st.same) + " rraw=" + u(st.rraw) + " rdiff=" + u(st.rdiff) +
           " coalesced=" + u(st.coalesced);
}
}  // namespace ribbit
