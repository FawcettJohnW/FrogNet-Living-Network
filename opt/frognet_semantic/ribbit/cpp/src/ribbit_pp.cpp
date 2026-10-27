// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_pp.cpp -- API++ [API_PLUS_PLUS_V2], see ribbit_pp.hpp. Every function is a write into the caller's own space
// or a read held on someone else's; nothing here is a call answered by anyone.
#include "ribbit_pp.hpp"
#include "lf/lfmap.hpp"
#include <unordered_map>

namespace ribbit::pp {

#ifndef PP_BROKEN
#define PP_BROKEN 0   // tests only: 1 every publish is count 1; 2 subscribe never says what was missed;
                      // 3 capable reads the directory from now on (misses those announced before);
                      // 4 latest takes the first writer's, not the newest; 5 the answerer reads from now on
#endif

// ---- publish / subscribe ----------------------------------------------------------------------------------------
// a topic's publish count, this publisher's: one atomic per topic, found without a lock
struct Publisher::Counts {
    using C = std::shared_ptr<std::atomic<uint64_t>>;     // never removed: freed with the map, with the publisher
    lf::LFMap<std::string, C> m;
    std::atomic<uint64_t>* of(const std::string& topic) {
        lf::ebr::Guard g;
        if (auto* n = m.get(topic)) return n->val.get();
        C c = std::make_shared<std::atomic<uint64_t>>(0);
        decltype(m)::Node* found = nullptr;
        if (m.add(topic, c, &found)) return c.get();
        return found->val.get();                        // another thread put the topic's count there first
    }
    // a caller's shape with API++'s two fields in front: made once per shape, kept for the publisher's life
    using O = std::shared_ptr<const std::vector<std::string>>;
    lf::LFMap<uintptr_t, O> shapes;
    const std::vector<std::string>& shape(const std::vector<std::string>& order) {
        lf::ebr::Guard g;
        if (auto* n = shapes.get(uintptr_t(&order))) return *n->val;
        auto v = std::make_shared<std::vector<std::string>>(); v->reserve(order.size() + 2);
        v->push_back("_b"); v->push_back("_n"); v->insert(v->end(), order.begin(), order.end());
        O o = v; decltype(shapes)::Node* found = nullptr;
        if (shapes.add(uintptr_t(&order), o, &found)) return *o;
        return *found->val;
    }
};

Publisher::Publisher(Space& sp, std::string space, std::string writer, std::vector<std::string> order, uint64_t ring)
    : sp_(sp), space_(std::move(space)), writer_(std::move(writer)), ring_(ring ? ring : 1), counts_(new Counts) {
    order_.reserve(order.size() + 2); order_.push_back("_b"); order_.push_back("_n");
    for (auto& o : order) order_.push_back(std::move(o));
}
Publisher::~Publisher() {}
uint64_t Publisher::publish(const std::string& topic, semcodec::Fields f) {
    return publish(topic, order_, std::move(f));
}
uint64_t Publisher::publish(const std::string& topic, const std::vector<std::string>& order, semcodec::Fields f) {
    const std::vector<std::string>& all_order = &order == &order_ ? order_ : counts_->shape(order);
    const uint64_t n = PP_BROKEN == 1 ? 1 : counts_->of(topic)->fetch_add(1, std::memory_order_acq_rel) + 1;
    semcodec::Fields all; all.reserve(f.size() + 2);
    all.emplace_back("_b", pyv::Str{writer_}); all.emplace_back("_n", pyv::Int::of(int64_t(n)));
    for (auto& kv : f) all.push_back(std::move(kv));
    sp_.write_fields(space_, topic, Space::slot(writer_, n, ring_), all_order, std::move(all));
    return n;
}

void subscribe(Space& sp, const std::string& space, const std::string& topic, long long from, OnDelivery on, OnMissed missed,
               std::function<void()> batch_end) {
    auto last = std::make_shared<std::unordered_map<std::string, uint64_t>>();   // this read's alone: one thread
    if (from < 0) from = (long long)sp.newest(space, topic);     // "from now on" is now, at the call: nothing after it is missed
    sp.watch(space, topic, from, [last, on = std::move(on), missed = std::move(missed), batch_end = std::move(batch_end)](std::vector<SpaceRow>& rows) {
        for (auto& r : rows) {
            if (r.gone) continue;                        // a publisher that has gone took its cells: not a publish
            ribbit::BagArrP a = r.fields(); if (!a) continue;
            Delivery d; d.id = r.id; d.topic = r.topic; d.writer = a->str("_b"); d.n = uint64_t(a->num("_n")); d.fields = a;
            uint64_t& l = (*last)[d.writer];
            if (PP_BROKEN != 2 && l && d.n > l + 1 && missed) missed(d.topic, d.writer, d.n - l - 1);
            if (d.n > l) l = d.n;
            on(d);
        }
        if (batch_end) batch_end();
    });
}

// ---- capabilities -----------------------------------------------------------------------------------------------
static const char SEP = '\x1f';
void announce(Space& sp, const std::string& directory, const std::string& capability, const std::string& who, const std::string& about) {
    sp.write(directory, "all", capability + SEP + who, about);
}
static bool to_capable(const SpaceRow& r, Capable& c) {
    const size_t s = r.instance.find(SEP); if (s == std::string::npos) return false;
    c.id = r.id; c.capability = r.instance.substr(0, s); c.who = r.instance.substr(s + 1); c.gone = r.gone;
    if (!r.gone) c.about = r.fields();
    return true;
}
void capable(Space& sp, const std::string& directory, std::function<bool(const std::string&)> match, std::function<void(const Capable&)> on) {
    const uint64_t d0 = sp.newest(directory, "all");
    if (PP_BROKEN != 3)
        for (auto& r : sp.read(directory, "all")) {      // those there now; a later one is the held read's
            Capable c; if (r.id <= d0 && to_capable(r, c) && match(c.capability)) { c.already = true; on(c); }
        }
    sp.watch(directory, "all", PP_BROKEN == 3 ? -1 : (long long)d0, [match, on](std::vector<SpaceRow>& rows) {
        for (auto& r : rows) { Capable c; if (to_capable(r, c) && match(c.capability)) on(c); }
    });
}

// ---- the last value -----------------------------------------------------------------------------------------------
void offer(Space& sp, const std::string& space, const std::string& topic, const std::string& writer, const std::string& bag) {
    sp.write(space, topic, writer, bag);
}
void offer(Space& sp, const std::string& space, const std::string& topic, const std::string& writer,
           const std::vector<std::string>& order, semcodec::Fields fields) {
    sp.write_fields(space, topic, writer, order, std::move(fields));
}
bool latest(Space& sp, const std::string& space, const std::string& topic, SpaceRow& out) {
    std::vector<SpaceRow> rows = sp.read(space, topic);
    if (rows.empty()) return false;
    size_t best = 0;
    if (PP_BROKEN != 4) for (size_t k = 1; k < rows.size(); ++k) if (rows[k].id > rows[best].id) best = k;
    out = std::move(rows[best]);
    return true;
}

// ---- ask / answer -------------------------------------------------------------------------------------------------
Asker::Asker(Space& sp, std::string service, std::string me, std::function<void(const SpaceRow&)> on_reply,
             std::function<void(const std::string&)> ended)
    : sp_(sp), service_(std::move(service)), me_(std::move(me)) {
    // from now, at the call: a reply to anything asked after this returns is read
    // streamed (Space::follow): an asker reads its replies as they land, without asking again per batch
    sp_.follow(service_ + ".answer", me_, (long long)sp_.newest(service_ + ".answer", me_), [on_reply = std::move(on_reply)](std::vector<SpaceRow>& rows) {
        for (auto& r : rows) on_reply(r);
    }, std::move(ended));
}
void Asker::ask(const std::string& key, const std::string& bag) { sp_.write(service_ + ".ask", me_, key, bag); }
void Asker::ask(const std::string& key, const std::vector<std::string>& order, semcodec::Fields f) {
    sp_.write_fields(service_ + ".ask", me_, key, order, std::move(f));
}
// every asker's requests: the whole ask space, from its first write (an asker may have asked before the answerer read)
Answerer::Answerer(Space& sp, std::string service, std::function<void(const std::string&, const SpaceRow&)> on_request)
    : sp_(sp), service_(std::move(service)) {
    sp_.watch(service_ + ".ask", "", PP_BROKEN == 5 ? -1 : 0, [on_request = std::move(on_request)](std::vector<SpaceRow>& rows) {
        for (auto& r : rows) on_request(r.topic, r);
    });
}
void Answerer::answer(const std::string& asker, const std::string& key, const std::string& bag) {
    sp_.write(service_ + ".answer", asker, key, bag);
}
void Answerer::answer(const std::string& asker, const std::string& key, const std::vector<std::string>& order, semcodec::Fields f) {
    sp_.write_fields(service_ + ".answer", asker, key, order, std::move(f));
}

}  // namespace ribbit::pp
