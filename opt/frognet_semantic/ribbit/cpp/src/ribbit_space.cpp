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
// [READERS_IN_PARALLEL_V1] FNWP's own reader model: each held read is a thread of its own that asks the memory for what
// was written past what it has, and waits for its answer -- its own, filed under its own sequence number; the session's
// reader hands each answer to the caller it belongs to (fan-out), and the memory runs each read as its own job, parked
// until something lands past it (the daemon's fan-out). Hundreds of reads are hundreds of these threads, all in flight
// on the one session at once (fan-in). Nothing serializes them; only a write blocks, and only on its own tuple.
uint64_t Space::newest(const std::string& sp, const std::string& topic) {
    return d_->local ? d_->M->newest(sp, topic) : d_->mem->newest_id(sp, topic);
}
void Space::watch(const std::string& sp, const std::string& topic, long long after, OnRows on) {
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
