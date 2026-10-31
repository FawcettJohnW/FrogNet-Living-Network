// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_pp.cpp -- API++ [API_PLUS_PLUS_V2], see ribbit_pp.hpp. Every function is a write into the caller's own space
// or a read held on someone else's; nothing here is a call answered by anyone.
#include "ribbit_pp.hpp"
#include "lf/lfmap.hpp"
#include <unordered_map>
#include <algorithm>
#include <stdexcept>
#include <cmath>
#include <chrono>
#include <unordered_set>

namespace ribbit::pp {

#ifndef PP_BROKEN
#define PP_BROKEN 0   // tests only: 1 every publish is count 1; 2 subscribe never says what was missed;
                      // 3 capable reads the directory from now on (misses those announced before);
                      // 4 latest takes the first writer's, not the newest; 5 the answerer reads from now on
#endif

void RibbitConnection::ConnectRibbit(const std::string& where, const std::string& vendor) {
    if (space_) throw std::logic_error("Ribbit is already connected; disconnect before connecting again");
    if (where.empty() || vendor.empty() || where.find('\0') != std::string::npos ||
        vendor.find('\0') != std::string::npos)
        throw std::invalid_argument("Ribbit address and vendor must be nonempty and contain no NUL");
    auto connected = std::make_unique<Space>(where, vendor);
    space_ = std::move(connected); // failed construction leaves this object disconnected
}
void RibbitConnection::DisconnectRibbit() { space_.reset(); }
Space& RibbitConnection::GetSpace() const {
    if (!space_) throw std::logic_error("Ribbit is disconnected");
    return *space_;
}

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

// ---- tuple interface [TUPLE_INTERFACE_PP_V1] -----------------------------------------------------------
namespace {
#ifndef CAPABILITY_BROKEN
#define CAPABILITY_BROKEN 0 // test controls: 1 ignore withdrawal; 2 ignore exact C; 3 accept malformed state
#endif
#ifndef TUPLE_HELPER_BROKEN
#define TUPLE_HELPER_BROKEN 0 // controls: 1 Exists always false; 2 WaitUntil never holds; 3 validate while writing
#endif
constexpr const char* ACTIVE_FIELD = "_capability_active";
const pyv::Obj* member(const JsonBag& bag, const std::string& key) {
    if (!bag.is<pyv::Dict>()) return nullptr;
    const auto& d = bag.as<pyv::Dict>(); const auto n = d.find(key);
    return n < 0 ? nullptr : &d.vals[size_t(n)];
}
void valid_json(const pyv::Obj& value, unsigned depth = 0) {
    if (depth > 256) throw std::invalid_argument("JSON bag nesting exceeds 256");
    if (value.is<pyv::Int>() && value.as<pyv::Int>().big)
        throw std::invalid_argument("integer exceeds the signed 64-bit wire field; use a JSON string for larger quantities");
    if (value.is<pyv::Bytes>() || (value.is<double>() && !std::isfinite(value.as<double>())))
        throw std::invalid_argument("JSON bag contains a non-JSON value");
    if (value.is<pyv::List>()) for (const auto& item : value.as<pyv::List>()) valid_json(item, depth + 1);
    if (value.is<pyv::Dict>()) {
        const auto& d = value.as<pyv::Dict>();
        if (d.keys.size() != d.vals.size()) throw std::invalid_argument("invalid JSON object storage");
        std::unordered_set<std::string> keys;
        for (size_t i = 0; i < d.keys.size(); ++i) {
            if (!keys.insert(d.keys[i]).second) throw std::invalid_argument("duplicate JSON object key");
            valid_json(d.vals[i], depth + 1);
        }
    }
}
void tuple_name(const TupleName& name, bool exact = false) {
    if (name.indexA.empty() || (exact && (name.indexB.empty() || name.indexC.empty())))
        throw std::invalid_argument(exact ? "Set requires an exact three-coordinate tuple" : "tuple family requires indexA");
    if (name.indexA.size() > UINT16_MAX || name.indexB.size() > UINT16_MAX || name.indexC.size() > UINT32_MAX)
        throw std::invalid_argument("tuple coordinates exceed read wire limits");
    for (const auto* part : {&name.indexA, &name.indexB, &name.indexC})
        if (part->find('\0') != std::string::npos) throw std::invalid_argument("tuple coordinates cannot contain NUL");
}
std::string prepare_set(const TupleName& name, const JsonBag& bag) {
    tuple_name(name, true);
    if (!bag.is<pyv::Dict>()) throw std::invalid_argument("JSON bag must be an object");
    valid_json(bag); return semtpl::dumps_ascii(bag, false);
}
JsonBag wait_result(JsonBag value, bool matched) {
    value.as<pyv::Dict>().set("matched", matched);
    value.as<pyv::Dict>().set("timed_out", !matched);
    return value;
}
JsonBag tuple_result(const std::string& service, const std::vector<SpaceRow>& rows, uint64_t after = 0) {
    pyv::List values;
    for (const auto& row : rows) {
        pyv::Dict name; name.set("indexA", pyv::Str{service}); name.set("indexB", pyv::Str{row.topic});
        name.set("indexC", pyv::Str{row.instance});
        pyv::Dict value; value.set("name", std::move(name));
        auto bag = row.gone ? std::optional<pyv::Obj>(pyv::None{}) : semtpl::detail::json_try(row.text());
        if (!bag || (!row.gone && !bag->is<pyv::Dict>())) throw std::invalid_argument("stored tuple is not a JSON bag");
        valid_json(*bag);
        value.set("bag", std::move(*bag)); value.set("id", pyv::Int::of(int64_t(row.id)));
        value.set("gone", row.gone); values.emplace_back(std::move(value));
        after = std::max(after, row.id);
    }
    pyv::Dict result; result.set("rows", std::move(values)); result.set("after", pyv::Int::of(int64_t(after)));
    return result;
}
bool selected(const TupleName& name, const SpaceRow& row) {
    return name.indexC.empty() || CAPABILITY_BROKEN == 2 || name.indexC == row.instance;
}
JsonBag capability_result(JsonBag result, bool include_inactive, bool report_gone) {
    auto& d = result.as<pyv::Dict>(); auto& rows = d.vals[size_t(d.find("rows"))].as<pyv::List>();
    pyv::List kept;
    for (auto& row : rows) {
        const auto* gone = member(row, "gone");
        if (gone && gone->as<bool>()) { if (report_gone) kept.push_back(std::move(row)); continue; }
        const auto* bag = member(row, "bag"); const auto* state = member(*bag, ACTIVE_FIELD);
        bool active = true;
        if (state) {
            if (!state->is<bool>()) { if (CAPABILITY_BROKEN != 3) continue; }
            else if (CAPABILITY_BROKEN != 1) active = state->as<bool>();
        }
        if (include_inactive || active) kept.push_back(std::move(row));
    }
    rows = std::move(kept); return result;
}
}
JsonBag Tuple::ParseBag(const std::string& json) {
    auto value = semtpl::detail::json_try(json);
    if (!value || !value->is<pyv::Dict>()) throw std::invalid_argument("JSON bag must be an object");
    valid_json(*value); return std::move(*value);
}
void Tuple::Set(const TupleName& name, const JsonBag& bag) {
    const auto json = prepare_set(name, bag);
    space_.write(name.indexA, name.indexB, name.indexC, json);
}
void Tuple::SetMany(const std::vector<TupleWrite>& writes) {
    if (TUPLE_HELPER_BROKEN == 3) { for (const auto& entry : writes) Set(entry.first, entry.second); return; }
    std::vector<std::string> prepared; prepared.reserve(writes.size());
    for (const auto& entry : writes) prepared.push_back(prepare_set(entry.first, entry.second));
    for (size_t i = 0; i < writes.size(); ++i) {
        const auto& name = writes[i].first;
        space_.write(name.indexA, name.indexB, name.indexC, prepared[i]);
    }
}
bool Tuple::Exists(const TupleName& name) const {
    if (TUPLE_HELPER_BROKEN == 1) return false;
    const auto result = Get(name);
    return !member(result, "rows")->as<pyv::List>().empty();
}
JsonBag Tuple::WaitUntil(const TupleName& name, std::function<bool(const JsonBag&)> predicate, double timeout_s) const {
    tuple_name(name);
    if (!predicate || !std::isfinite(timeout_s) || timeout_s < 0)
        throw std::invalid_argument("WaitUntil requires a predicate and a finite nonnegative timeout");
    using Clock = std::chrono::steady_clock;
    const auto start = Clock::now();
    uint64_t after = space_.newest(name.indexA, name.indexB); // before snapshot: no change can fall between it and hold
    auto result = Get(name);
    for (;;) {
        if (predicate(result)) return wait_result(std::move(result), true);
        const double remaining = timeout_s - std::chrono::duration<double>(Clock::now() - start).count();
        if (remaining <= 0 || TUPLE_HELPER_BROKEN == 2) return wait_result(std::move(result), false);
        // Discard the changed-row batch: predicate semantics require the whole
        // current family, including unchanged tuples and excluding gone rows.
        HeldRead(name, after, std::min(30.0, std::max(0.001, remaining)));
        result = Get(name);
    }
}
JsonBag Tuple::Get(const TupleName& name) const {
    tuple_name(name);
    return tuple_result(name.indexA, space_.read(name.indexA, name.indexB, name.indexC));
}
JsonBag Tuple::HeldRead(const TupleName& name, uint64_t& after, double wait_s) const {
    tuple_name(name);
    if (after > uint64_t(INT64_MAX) || !std::isfinite(wait_s) || wait_s <= 0 || wait_s > 30)
        throw std::invalid_argument("held read requires a valid cursor and 0 < wait_s <= 30");
    auto result = tuple_result(name.indexA, space_.read(name.indexA, name.indexB, name.indexC, int64_t(after), wait_s), after);
    after = uint64_t(member(result, "after")->as<pyv::Int>().v); return result;
}
void Tuple::Watch(const TupleName& name, std::function<void(const JsonBag&)> on) const {
    tuple_name(name); if (!on) throw std::invalid_argument("watch callback is required");
    const uint64_t past = space_.newest(name.indexA, name.indexB);
    std::vector<SpaceRow> initial;
    for (auto& row : space_.read(name.indexA, name.indexB, name.indexC))
        if (row.id <= past) initial.push_back(std::move(row));
    on(tuple_result(name.indexA, initial, past));
    space_.watch(name.indexA, name.indexB, int64_t(past), [name, on](std::vector<SpaceRow>& rows) {
        std::vector<SpaceRow> matched;
        for (const auto& row : rows) if (selected(name, row)) matched.push_back(row);
        if (!matched.empty()) on(tuple_result(name.indexA, matched));
    });
}
void Capability::SetCapability(const TupleName& name, const JsonBag& bag) {
    if (member(bag, ACTIVE_FIELD)) throw std::invalid_argument("_capability_active is reserved; use WithdrawCapability");
    Set(name, bag);
}
void Capability::WithdrawCapability(const TupleName& name) {
    pyv::Dict bag; bag.set(ACTIVE_FIELD, false); Set(name, bag);
}
bool Capability::CheckCapability(const TupleName& name) const {
    const auto result = EnumerateCapabilities(name);
    return !member(result, "rows")->as<pyv::List>().empty();
}
JsonBag Capability::GetCapability(const TupleName& name) const { return Get(name); }
JsonBag Capability::EnumerateCapabilities(const TupleName& name, bool include_inactive) const {
    return capability_result(Get(name), include_inactive, false);
}
void Capability::WatchCapabilities(const TupleName& name, std::function<void(const JsonBag&)> on) const {
    if (!on) throw std::invalid_argument("capability watch callback is required");
    Watch(name, [on](const JsonBag& result) {
        on(capability_result(result, true, true));
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
