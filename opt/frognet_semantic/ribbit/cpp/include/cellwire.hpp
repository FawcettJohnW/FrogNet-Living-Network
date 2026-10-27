/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// cellwire.hpp -- [CELL_WRITE_V1] a write is its cell's REQ_DIFF against the writer's own previous write of the family.
//
// Every write is the same request (write this bag at this location), so what travels is the location and the bag's
// fields that changed. A writer -- one thread on one session -- numbers its writes of each family 1, 2, 3: a chain. Write
// c is a difference against write c-1 of the same chain, which the writer holds (it made it) and the memory holds in
// that chain's entry (a ring of its last few references, published by an atomic counter: no lock, a wait is a futex on
// that counter). Chains are independent: every front, every box, every family runs at once; within a chain write c+1
// decodes as soon as c has decoded, while c is still being stored. Nothing answers a write; a refusal is said. A row
// keeps its last writer's chain and number, so an older write of a chain landing after a newer one of the same chain is
// not stored ([WRITER_ORDER_IN_THE_ROW_V1]); writers of different chains land in the memory's write order, as ever.
//
// Frame (after MAGIC and OP_CELL_WRITE): u8 flags; varint family; if F_NAMES: varint+service, varint+variable;
// varint+instance unless F_NEXT (the instance is the reference's plus one, both canonical decimals); varint writer
// slot; varint number in the chain; if F_KEYS: varint count, varint+key each (the bag's field order: a whole value,
// no reference); then the packet: u8 packet flags, varint field count, varint length, the field block (semcodec;
// version and opcode are constants and do not travel). Without F_KEYS the packet is a difference against write c-1.
// [LINGER_BY_FAMILY_V1] F_LINGER (only with F_NAMES): after the variable, varint linger+1 -- what happens to the family's
// rows when the writer's session ends: 0 they go at once (the default, and then F_LINGER is not set), N > 0 they stay N
// seconds and are reaped, -1 they stay. The writer says it each time it names the family.
#pragma once
#include <mutex>
#include "bagarr.hpp"
#include "semcodec.hpp"
#include "semtpl.hpp"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#ifndef LINGER_BROKEN
#define LINGER_BROKEN 0   // [LINGER_BY_FAMILY_V1] tests only: 1 the writer's linger never travels; 2 (ram_host.cpp) the reaper never reaps
#endif
namespace cellwire {
using pyv::Dict; using pyv::Obj; using pyv::List;
constexpr uint8_t OP_CELL_WRITE = 0x7C;
constexpr uint8_t F_NAMES = 1, F_DIFF = 2, F_KEYS = 4, F_NEXT = 8, F_LINGER = 16;

// a chain's reference: its last write's bag -- the very array the memory stored for it ([POSITIONAL_WRITE_V1]) -- and
// that write's instance
struct Ref { std::shared_ptr<const std::vector<std::string>> fo; ribbit::BagArrP arr; std::string inst; };
using Changed = ribbit::FwdP;   // the field indices a write changed, ascending (and its shared packet)

namespace detail {
using semcodec::detail::put_varint; using semcodec::detail::get_varint;
inline void put_str(std::string& o, const std::string& s) { put_varint(o, s.size()); o += s; }
inline std::string get_str(std::string_view f, size_t& p) {
    const size_t n = size_t(get_varint(f, p)); if (p + n > f.size()) throw std::runtime_error("cellwire: cut short");
    std::string s(f.substr(p, n)); p += n; return s;
}
inline std::vector<std::string> field_order(const Obj& bag) { return semtpl::field_order_and_type_map(semtpl::infer_schema(bag, "")).first; }
inline semcodec::Fields fields(Obj& bag, const std::vector<std::string>& fo) {
    semcodec::Fields f; f.reserve(fo.size());
    for (const auto& p : fo) { Obj* v = semtpl::path_ptr(bag, p); f.emplace_back(p, v ? std::move(*v) : Obj(pyv::None{})); }
    return f;
}
inline Dict dict(const semcodec::Fields& f) { Dict d; d.keys.reserve(f.size()); d.vals.reserve(f.size()); for (auto& kv : f) { d.keys.push_back(kv.first); d.vals.push_back(kv.second); } return d; }
inline bool next_of(const std::string& ref, const std::string& inst) {
    int64_t a, b; return semcodec::detail::decstr(ref, a) && semcodec::detail::decstr(inst, b) && b == a + 1;
}
}  // namespace detail

// The writer's side. One per session; encode() may be called from any number of threads at once: each thread keeps its
// own chains (thread_local), and the only shared things are the counters that hand out writer slots and family numbers.
class Encoder {
public:
    // bag: the bag's JSON text (an object). Returns the frame, MAGIC and op included.
    std::string encode(const std::string& magic, const std::string& sv, const std::string& var, const std::string& inst, const std::string& bag) {
        auto parsed = semtpl::detail::json_try(bag);
        if (!parsed || !parsed->is<Dict>()) throw std::runtime_error("cellwire: a bag that is not a JSON object");
        std::vector<std::string> fo = detail::field_order(*parsed);
        return encode_fields(magic, sv, var, inst, fo, detail::fields(*parsed, fo), false);   // a local: compared by value
    }
    // [FIELDS_NOT_TEXT_V1] the bag as its writer holds it: its field order (the same vector each time for one shape:
    // compared by address first) and its values in that order -- nothing parsed, nothing inferred
    std::string encode_fields(const std::string& magic, const std::string& sv, const std::string& var, const std::string& inst,
                              const std::vector<std::string>& fo, semcodec::Fields f, bool kept = true) {
        struct W { uint64_t fam = 0; const std::vector<std::string>* fop = nullptr; std::vector<std::string> fo; Dict ref; std::string inst; uint32_t counter = 0, refused_seen = 0; };
        struct Mine { uint64_t gen = 0; uint64_t slot = 0; std::unordered_map<std::string, W> fams; };
        thread_local std::vector<Mine> mine;                       // this thread's chains, on each session it writes to
        Mine* m = nullptr;
        for (auto& x : mine) if (x.gen == gen_) { m = &x; break; }
        if (!m) { mine.push_back(Mine{gen_, slots_.fetch_add(1, std::memory_order_relaxed), {}}); m = &mine.back(); }
        thread_local std::string key; key.clear(); key += sv; key += '\x1f'; key += var;
        auto it = m->fams.find(key); bool named = false;
        if (it == m->fams.end()) { it = m->fams.emplace(key, W{}).first; it->second.fam = fams_.fetch_add(1, std::memory_order_relaxed); named = true; }
        W& w = it->second;
        const uint32_t rg = refused_gen(m->slot, w.fam);         // the memory refused a write of this chain: start it again
        const bool keys = w.counter == 0 || ((!kept || w.fop != &fo) && fo != w.fo) || rg != w.refused_seen;
        w.refused_seen = rg;
        std::string pkt; Dict nref;
        if (keys) { pkt = semcodec::encode_request(0, {}, f, true); nref = detail::dict(f); }
        else { semcodec::DiffResult d = semcodec::encode_request_diff(0, {}, f, &w.ref, true);
               nref = std::move(d.reference); pkt = d.identical ? semcodec::detail::finish(0, semcodec::FLAG_DIFF, 0, std::string(), false) : std::move(d.bytes); }
        const bool next = !keys && detail::next_of(w.inst, inst);
        const uint32_t counter = ++w.counter;
        std::string o; o.reserve(32 + pkt.size() + (named ? sv.size() + var.size() : 0));
        o += magic; o += char(OP_CELL_WRITE);
        const int32_t lg = (named && LINGER_BROKEN != 1) ? linger_of(key) : 0;
        o += char((named ? F_NAMES : 0) | (keys ? F_KEYS : 0) | (next ? F_NEXT : 0) | (lg ? F_LINGER : 0));
        detail::put_varint(o, w.fam);
        if (named) { detail::put_str(o, sv); detail::put_str(o, var); if (lg) detail::put_varint(o, uint64_t(int64_t(lg) + 1)); }
        if (!next) detail::put_str(o, inst);
        detail::put_varint(o, m->slot); detail::put_varint(o, counter);
        if (keys) { detail::put_varint(o, fo.size()); for (auto& k : fo) detail::put_str(o, k); }
        o += pkt[1]; detail::put_varint(o, uint8_t(pkt[6]) | uint8_t(pkt[7]) << 8); detail::put_varint(o, pkt.size() - 8); o.append(pkt, 8, std::string::npos);
        if (keys) w.fo = fo;
        w.fop = kept ? &fo : nullptr; w.ref = std::move(nref); w.inst = inst;
        return o;
    }
    // [CELL_WRITE_V1] the memory refused a write of chain (slot, fam) -- said on the session's reader: that chain's next
    // write goes whole. Lock-free: an insert-only table of (chain, count), read by the chain's own thread.
    void refused(uint64_t slot, uint64_t fam) { if (auto* e = entry(slot, fam, true)) e->gen.fetch_add(1, std::memory_order_acq_rel); }
    uint64_t gen() const { return gen_; }                     // this session's: a thread's state for it is keyed by it
    // [LINGER_BY_FAMILY_V1] the family's rows when this session ends (said before its first write; read only when a
    // thread names the family -- once per thread per family)
    void linger(const std::string& sv, const std::string& var, int32_t seconds) {
        std::lock_guard<std::mutex> g(linger_m_); lingers_[sv + '\x1f' + var] = seconds; }
private:
    // the family's own choice, else its service's (said with variable ""), else GOES
    int32_t linger_of(const std::string& key) {
        std::lock_guard<std::mutex> g(linger_m_); auto it = lingers_.find(key);
        if (it == lingers_.end()) it = lingers_.find(key.substr(0, key.find('\x1f') + 1));
        return it == lingers_.end() ? 0 : it->second; }
    std::mutex linger_m_; std::unordered_map<std::string, int32_t> lingers_;
    struct RE { std::atomic<uint64_t> key{0}; std::atomic<uint32_t> gen{0}; };
    static constexpr size_t RES = 4096;
    std::unique_ptr<RE[]> res_{new RE[RES]};
    RE* entry(uint64_t slot, uint64_t fam, bool make) {
        const uint64_t k = ((slot << 32) ^ fam) + 1; size_t h = size_t(k * 0x9E3779B97F4A7C15ull);
        for (size_t i = 0; i < RES; ++i) { RE& e = res_[(h + i) & (RES - 1)]; uint64_t cur = e.key.load(std::memory_order_acquire);
            if (cur == k) return &e;
            if (!cur) { if (!make) return nullptr; if (e.key.compare_exchange_strong(cur, k, std::memory_order_acq_rel) || cur == k) return &e; } }
        return nullptr;
    }
    uint32_t refused_gen(uint64_t slot, uint64_t fam) { RE* e = entry(slot, fam, false); return e ? e->gen.load(std::memory_order_acquire) : 0; }
    static inline std::atomic<uint64_t> gens_{1};
    const uint64_t gen_ = gens_.fetch_add(1) + 1;               // this session's: a thread's chains on an earlier one are not these
    std::atomic<uint64_t> slots_{0}, fams_{0};
};

// The memory's side: what one frame says, before its reference is fetched
struct Frame {
    uint8_t flags = 0; uint64_t fam = 0; std::string sv, var;   // sv/var only if F_NAMES
    int32_t linger = 0;                                         // [LINGER_BY_FAMILY_V1] only if F_NAMES
    std::string inst; uint64_t slot = 0; uint32_t counter = 0;  // inst empty if F_NEXT (taken from the reference)
    std::vector<std::string> keys; std::string pkt;            // pkt: a whole semcodec packet
};
inline Frame parse(std::string_view f, size_t p) {            // p: just past the op byte
    Frame x; if (p >= f.size()) throw std::runtime_error("cellwire: cut short");
    x.flags = uint8_t(f[p++]); x.fam = detail::get_varint(f, p);
    if (x.flags & F_NAMES) { x.sv = detail::get_str(f, p); x.var = detail::get_str(f, p);
                             if (x.flags & F_LINGER) x.linger = int32_t(int64_t(detail::get_varint(f, p)) - 1); }
    if (!(x.flags & F_NEXT)) x.inst = detail::get_str(f, p);
    x.slot = detail::get_varint(f, p); x.counter = uint32_t(detail::get_varint(f, p));
    if (x.flags & F_KEYS) { const size_t n = size_t(detail::get_varint(f, p)); if (n > 4096) throw std::runtime_error("cellwire: too many keys");
                            x.keys.reserve(n); for (size_t k = 0; k < n; ++k) x.keys.push_back(detail::get_str(f, p)); }
    if (p >= f.size()) throw std::runtime_error("cellwire: cut short");
    const uint8_t pf = uint8_t(f[p++]); const uint64_t nf = detail::get_varint(f, p); const size_t len = size_t(detail::get_varint(f, p));
    if (p + len > f.size() || nf > 0xFFFF) throw std::runtime_error("cellwire: cut short");
    x.pkt.reserve(8 + len); x.pkt += char(semcodec::WIRE_VERSION); x.pkt += char(pf); x.pkt.append(4, '\0');
    x.pkt += char(nf & 0xFF); x.pkt += char(nf >> 8); x.pkt.append(f.substr(p, len));
    return x;
}
// the bag and the chain's new reference, from the frame and (for a difference) write c-1's reference
// [DECODE_ONCE_V1] the write's bag, decoded once: its values against its chain's reference, as the array the memory
// stores (the field names shared with the chain); no text is made.
// [POSITIONAL_WRITE_V1] by position, as the reader's rowcodec::Decoder does: write c-1's array copied once and the fields
// the difference carries put in place by index -- no dictionary is built, nothing is looked up by name, and the chain's
// reference is the stored array itself. The same values as semcodec::decode_reply against that reference. changed_out:
// the indices the write changed (every index for a whole value), for the memory to give its readers.
inline std::unique_ptr<Ref> decode(Frame& x, const Ref* ref, ribbit::BagArrP& arr_out, Changed* changed_out = nullptr) {
    using namespace semcodec::detail;
    auto out = std::make_unique<Ref>();
    const bool keys = x.flags & F_KEYS;
    if (!keys && !ref) throw std::runtime_error("cellwire: a difference without its reference");
    if (x.flags & F_NEXT) { int64_t a; if (!semcodec::detail::decstr(ref->inst, a)) throw std::runtime_error("cellwire: next of an instance that is not a number"); x.inst = std::to_string(a + 1); }
    out->fo = keys ? std::make_shared<const std::vector<std::string>>(std::move(x.keys)) : ref->fo;
    const size_t nf = out->fo->size();
    Decoded d = decode_packet(x.pkt);
    const std::vector<Obj>* rv = (!keys && ref->arr) ? &ref->arr->vals : nullptr;   // the reference, by index
    auto a = std::make_shared<ribbit::BagArr>(); a->names = out->fo;
    if (d.diff && rv && !rv->empty()) { a->vals = *rv; a->vals.resize(nf, Obj(pyv::None{})); }
    else a->vals.assign(nf, Obj(pyv::None{}));
    std::vector<uint16_t> ch; ch.reserve(d.vals.size() + d.splices.size());
    for (auto& kv : d.vals) if (kv.first < nf) { a->vals[kv.first] = std::move(kv.second); ch.push_back(uint16_t(kv.first)); }
    for (auto& sp : d.splices) {                     // [STRING_SPLICE_V1] [NUMBER_DELTA_V1] against the reference's value
        if (!rv || sp.idx >= nf || sp.idx >= rv->size() || (sp.kind == semcodec::TYPE_SPLICE && !(*rv)[sp.idx].is<pyv::Str>()))
            throw semcodec::ValueError("semcodec: a splice without its reference string (field " + (sp.idx < nf ? (*out->fo)[sp.idx] : std::string("?")) + ")");
        const Obj& r = (*rv)[sp.idx];
        if (sp.kind == semcodec::TYPE_INT_DELTA) { if (!r.is<pyv::Int>() || r.as<pyv::Int>().big) throw semcodec::ValueError("semcodec: an integer delta without its reference integer");
            a->vals[sp.idx] = pyv::Int::of(int64_t(uint64_t(r.as<pyv::Int>().v) + uint64_t(sp.d))); }
        else if (sp.kind == semcodec::TYPE_FLOAT_DELTA) { if (!r.is<double>()) throw semcodec::ValueError("semcodec: a float delta without its reference float");
            uint64_t u; const double y = r.as<double>(); std::memcpy(&u, &y, 8); u += uint64_t(sp.d); double z; std::memcpy(&z, &u, 8); a->vals[sp.idx] = z; }
        else if (sp.kind == semcodec::TYPE_DECSTR_DELTA) { int64_t bb; if (!r.is<pyv::Str>() || !decstr(r.as<pyv::Str>().s, bb)) throw semcodec::ValueError("semcodec: a decimal delta without its reference number");
            a->vals[sp.idx] = pyv::Str{std::to_string(bb + sp.d)}; }
        else { const std::string& o = r.as<pyv::Str>().s;
            if (sp.pre + sp.suf > o.size()) throw semcodec::ValueError("semcodec: a splice longer than its reference");
            a->vals[sp.idx] = pyv::Str{o.substr(0, sp.pre) + sp.mid + o.substr(o.size() - sp.suf)}; }
        ch.push_back(uint16_t(sp.idx));
    }
    if (keys || !d.diff) { ch.clear(); for (size_t i = 0; i < nf; ++i) ch.push_back(uint16_t(i)); }   // a whole value: all of it
    else { std::sort(ch.begin(), ch.end()); ch.erase(std::unique(ch.begin(), ch.end()), ch.end()); }
    if (changed_out) { auto fw = std::make_shared<ribbit::Fwd>(); fw->idx = std::move(ch); *changed_out = std::move(fw); }
    out->arr = a; out->inst = x.inst;
    arr_out = std::move(a);
    return out;
}
// The memory's per-chain state. A chain's writes come from one writer thread through one connection, so they reach the
// memory in their own order: the connection's reader appends each to its chain, and the chain runs as one job at a time
// on the pool, in that order -- different chains run as different jobs at the same time. Nothing parks, nothing waits
// for anything; write c finds write c-1's reference in `last`, which only the chain's running job touches. A write that
// failed leaves `last` empty, so the next one still goes (a whole value is stored, a difference is refused and said).
// Lock-free: the queue is an MPSC list, and `running` makes sure exactly one job drains it.
struct Chain {
    struct Node { std::atomic<Node*> next{nullptr}; std::function<void()> f; };
    Node stub; std::atomic<Node*> head{&stub}; Node* tail = &stub;   // tail: the running job's
    std::atomic<bool> running{false};
    std::unique_ptr<Ref> last;                                  // the running job's: write c-1's reference
    uint32_t last_c = 0;                                        // ...and its number
    ~Chain() { for (Node* n = tail; n;) { Node* x = n->next.load(); if (n != &stub) delete n; n = x; } }
    // queue k on this chain; `sched` starts a job that runs `run()` when none is running
    template <class Sched> void post(std::function<void()> k, Sched sched) {
        Node* n = new Node; n->f = std::move(k);
        Node* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release);
        if (!running.exchange(true, std::memory_order_acq_rel)) sched([this] { run(); });
    }
    void run() {                                                // one at a time (running)
        for (;;) {
            for (Node* nx; (nx = tail->next.load(std::memory_order_acquire));) {
                if (tail != &stub) { delete tail; }
                tail = nx;
                std::function<void()> f = std::move(nx->f); f();
            }
            running.store(false, std::memory_order_release);
            if (!tail->next.load(std::memory_order_acquire) || running.exchange(true, std::memory_order_acq_rel)) return;
        }
    }
};
}  // namespace cellwire
