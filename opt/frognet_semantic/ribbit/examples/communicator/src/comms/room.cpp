// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "room.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <random>

namespace comms {

using frogram::Json;
static long long now_ms() { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static std::string jstr(const std::string& s) { Json j; j.type = Json::Str; j.s = s; return ribbit::RamClient::to_json(j); }
static std::string field(const Json& v, const char* k) {
    if (v.type != Json::Obj) return "";
    for (auto& kv : v.o) if (kv.first == k) return kv.second.type == Json::Str ? kv.second.s : ribbit::RamClient::to_json(kv.second);
    return "";
}
static std::string hex(int n) { static std::mt19937 r(std::random_device{}()); std::string s; for (int i = 0; i < n; ++i) s += "0123456789abcdef"[r() % 16]; return s; }

Room::Room(const std::string& host, int port, const std::string& name, const std::string& caps)
    : name_(name), caps_(caps) {
    me_key_ = name;                                       // comms_control: the name without the '*' of an unattended source
    while (!me_key_.empty() && me_key_[0] == '*') me_key_.erase(0, 1);
    me_id_ = me_key_;
    c_.connect(host, port);
    w_.connect(host, port);
    announce();
    absorb(w_.get_all(SERVICE), true);                    // the starting copy
    watch_ = std::thread([this] { watch(); });
    beat_ = std::thread([this] {
        while (!stop_) {
            for (int i = 0; i < HEARTBEAT_S * 10 && !stop_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (!stop_) {
                try { announce(status()); refresh_calls(); }
                catch (const std::exception& e) { std::fprintf(stderr, "[room] heartbeat failed: %s\n", e.what()); }   // logged, never silent
            }
        }
    });
}

Room::~Room() {
    stop_ = true;
    // [A_HELD_READ_ENDS_AT_ONCE_V1] The watcher may be parked in a held read for up to 15 s. Shutting its session's
    // sockets (shutdown only: nothing freed under the other thread) wakes it with an error now, so closing a room
    // never waits on a read. Found 2026-10-01: three rooms took 45 s to close.
    try { w_.session().shutdown(); } catch (const std::exception&) {}
    cache_cv_.notify_all();
    if (beat_.joinable()) beat_.join();
    if (watch_.joinable()) watch_.join();
    try { w_.close(); } catch (const std::exception&) {}
    try {                                                 // a clean stop withdraws at once; only a crash waits to age out
        { std::lock_guard<std::mutex> g(mu_); offered_.clear(); joined_.clear(); }
        if (!ever_offered_.empty()) write_announce();
        write_joined();
        announce("offline");
    } catch (const std::exception&) {}
    try { c_.cleanup_owned(); c_.close(); } catch (const std::exception&) {}
}

// [HOLD_THE_READ_V1] One held read keeps this participant's copy of the region current: it wakes the moment anything
// newer than the last write-order id is written, and returns only what is new. A read that wakes on nothing for
// RESYNC_S is followed by one full read, because a removal or an expiry writes nothing newer.
void Room::watch() {
    constexpr double HOLD_S = 15.0;
    while (!stop_) {
        try {
            int64_t after;
            { std::lock_guard<std::mutex> g(cache_mu_); after = last_id_; }
            auto ts = w_.get_all(SERVICE, 0, HOLD_S, 1, "", after);
            if (stop_) break;
            if (ts.empty()) absorb(w_.get_all(SERVICE), true);   // quiet for HOLD_S: resync removals and ages
            else absorb(std::move(ts), false);
        } catch (const std::exception& e) {
            if (stop_) break;                                 // the close itself ended the read
            std::fprintf(stderr, "[room] watch failed: %s (retrying)\n", e.what());   // logged, never silent
            for (int i = 0; i < 10 && !stop_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
}

void Room::absorb(std::vector<ribbit::Tuple>&& ts, bool full) {
    auto now = std::chrono::steady_clock::now();
    {
        std::lock_guard<std::mutex> g(cache_mu_);
        if (full) rows_.clear();
        for (auto& t : ts) {
            if (int64_t(t.id) > last_id_) last_id_ = int64_t(t.id);
            std::string k = t.var + "|" + t.scope;
            rows_[k] = Row{std::move(t), now};
        }
    }
    version_++;
    cache_cv_.notify_all();
}

// [READ_YOUR_OWN_WRITES_V1] A participant sees what it just wrote, at once: the row goes into its own copy as it goes
// to memory, not when the held read next delivers it. The held read's position is NOT advanced, so writes by others
// that landed just before this one are still delivered; this row comes back through the held read too, harmlessly.
void Room::write(const std::string& var, const std::string& scope, const std::string& value_json, bool own) {
    uint64_t id = c_.put(SERVICE, var, scope, value_json, own);
    ribbit::Tuple t;
    t.var = var; t.scope = scope; t.id = id; t.age_s = 0;
    t.value = frogram::Json::parse(value_json);
    {
        std::lock_guard<std::mutex> g(cache_mu_);
        rows_[var + "|" + scope] = Row{std::move(t), std::chrono::steady_clock::now()};
    }
    version_++;
    cache_cv_.notify_all();
}

bool Room::wait_change(uint64_t seen, int timeout_ms) {
    std::unique_lock<std::mutex> g(cache_mu_);
    return cache_cv_.wait_for(g, std::chrono::milliseconds(timeout_ms), [&] { return version_.load() != seen || stop_; });
}

std::vector<ribbit::Tuple> Room::cached(const std::string& var, int fresh_s) const {
    std::vector<ribbit::Tuple> out;
    auto now = std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> g(cache_mu_);
    for (auto& kv : rows_) {
        if (kv.second.t.var != var) continue;
        double age = kv.second.t.age_s + std::chrono::duration<double>(now - kv.second.got).count();
        if (fresh_s > 0 && age > fresh_s) continue;
        ribbit::Tuple t = kv.second.t; t.age_s = age;
        out.push_back(std::move(t));
    }
    return out;
}

Room::Hold Room::my_hold(const std::string& session) const {
    Hold h;
    for (auto& t : cached("MediaHold", 10)) {
        if (field(t.value, "session") != session || field(t.value, "viewer") != me_id_) continue;
        auto num = [&](const char* k) -> uint64_t { std::string v = field(t.value, k); return v.empty() ? 0 : std::stoull(v); };
        h.present = true;
        h.unanchored = field(t.value, "unanchored") == "true"; h.key_held = field(t.value, "key_held") == "true";
        h.passthrough = field(t.value, "passthrough") == "true";
        h.audio_only = field(t.value, "audio_only") == "true";
        h.aimed = num("aimed"); h.delivered = num("delivered"); h.inters_shed = num("inters_shed"); h.keys_held = num("keys_held");
        h.w = int(num("w")); h.h = int(num("h"));
    }
    return h;
}

void Room::announce(const std::string& status) {
    { std::lock_guard<std::mutex> g(mu_); status_ = status; }
    std::string v = "{\"name\":" + jstr(name_) + ",\"status\":" + jstr(status) + ",\"caps\":" + jstr(caps_) +
                    ",\"ts\":" + std::to_string(now_ms() / 1000) + "}";
    write("presence", "host:" + c_.my_ip() + ":presence:" + me_id_, v, true);
}

std::vector<Member> Room::roster() {
    std::vector<Member> out;
    for (auto& t : cached("presence", PRESENCE_FRESH_S)) {
        auto p = t.scope.find(":presence:");
        out.push_back(Member{p == std::string::npos ? t.scope : t.scope.substr(p + 10), field(t.value, "name"),
                             field(t.value, "status"), field(t.value, "caps"), t.addr, t.age_s});
    }
    return out;
}

void Room::send_chat(const std::string& session, const std::string& text) {
    std::string v = "{\"from_id\":" + jstr(me_id_) + ",\"from_name\":" + jstr(name_) + ",\"text\":" + jstr(text) +
                    ",\"ts\":" + std::to_string(now_ms()) + "}";
    write("chat", "session:chat:" + session + ":" + hex(10), v, false);   // lossless: outlives the writer
}

std::vector<Line> Room::read_chat(const std::string& session) {
    std::vector<Line> out;
    const std::string pre = "session:chat:" + session + ":";
    for (auto& t : cached("chat", 3600)) {
        if (t.scope.compare(0, pre.size(), pre) != 0) continue;
        std::string ts = field(t.value, "ts");
        out.push_back(Line{field(t.value, "from_id"), field(t.value, "from_name"), field(t.value, "text"), ts.empty() ? 0 : std::stoll(ts)});
    }
    std::sort(out.begin(), out.end(), [](const Line& a, const Line& b) { return a.ts_ms < b.ts_ms; });
    return out;
}

void Room::set_media_speed(const std::string& session, long bps) {
    std::string v = "{\"session\":" + jstr(session) + ",\"viewer\":" + jstr(me_id_) + ",\"bps\":" + std::to_string(bps) +
                    ",\"addr\":" + jstr(c_.my_ip()) + ",\"ts\":" + std::to_string(now_ms() / 1000) + "}";
    write("MediaSpeed", "session:" + session + ":viewer:" + me_id_, v, false);
}

static std::string calls_json(const std::map<std::string, std::pair<std::string, int>>& m, const std::string& from = "") {
    std::string o = "{";
    for (auto& kv : m) {
        if (o.size() > 1) o += ",";
        o += jstr(kv.first) + ":{\"host\":" + jstr(kv.second.first) + ",\"port\":" + std::to_string(kv.second.second);
        if (!from.empty()) o += ",\"from\":" + jstr(from);
        o += "}";
    }
    return o + "}";
}
static const Json* member(const Json& v, const char* k) {
    if (v.type != Json::Obj) return nullptr;
    for (auto& kv : v.o) if (kv.first == k) return &kv.second;
    return nullptr;
}

// [ANNOUNCE_IS_THE_HOSTS_ROW_V1] one row per machine: keep the sessions other processes here offer, drop only the ones
// THIS process offered and no longer does, add its own.
void Room::write_announce() {
    // [ONE_ANNOUNCEMENT_PER_PARTICIPANT_V1] [FOUR_TUPLES_V1] applied to the rewrite: the party with standing to say
    // what is offered is the PARTICIPANT, so the row is keyed to the participant -- never to an address, which on the
    // Internet identifies nothing (two people behind home routers can both be 192.168.1.10). The name is stable across
    // restarts, so a restarted participant announces into the same row.
    const std::string scope = "user:" + me_key_;
    std::map<std::string, std::pair<std::string, int>> current;
    for (auto& t : cached("CallsAnnounce", 30)) {
        if (t.scope != scope) continue;
        if (const Json* calls = member(t.value, "calls"); calls && calls->type == Json::Obj)
            for (auto& kv : calls->o) {
                std::string h = field(kv.second, "host"), p = field(kv.second, "port");
                current[kv.first] = {h, p.empty() ? 0 : std::stoi(p)};
            }
        break;
    }
    for (auto& sid : ever_offered_) if (!offered_.count(sid)) current.erase(sid);
    for (auto& kv : offered_) current[kv.first] = kv.second;
    write("CallsAnnounce", scope, "{\"user\":" + jstr(me_key_) + ",\"host\":" + jstr(c_.my_ip()) + ",\"calls\":" + calls_json(current) + "}", false);
}

void Room::write_joined() {
    write("CallsJoined", "user:" + me_key_, "{\"user\":" + jstr(me_key_) + ",\"calls\":" + calls_json(joined_) + "}", false);
}

void Room::offer_call(const std::string& session, const std::string& h, int p) {
    { std::lock_guard<std::mutex> g(mu_); offered_[session] = {h, p}; ever_offered_.insert(session); }
    write_announce();
}
void Room::join_call(const std::string& session, const std::string& h, int p) {
    { std::lock_guard<std::mutex> g(mu_); joined_[session] = {h, p}; }
    write_joined();
}
void Room::invite(const std::string& who, const std::string& session, const std::string& h, int p) {
    std::map<std::string, std::pair<std::string, int>> calls{{session, {h, p}}};
    write("CallsInvitations", "user:" + who, "{\"user\":" + jstr(who) + ",\"calls\":" + calls_json(calls, name_) + "}", false);
}
void Room::refresh_calls() {
    bool any_offer, any_join;
    { std::lock_guard<std::mutex> g(mu_); any_offer = !offered_.empty(); any_join = !joined_.empty(); }
    if (any_offer) write_announce();
    if (any_join) write_joined();
}

std::vector<Offer> Room::offers() {
    std::vector<Offer> out;
    for (auto& t : cached("CallsAnnounce", 30))
        if (const Json* calls = member(t.value, "calls"); calls && calls->type == Json::Obj)
            for (auto& kv : calls->o) {
                std::string p = field(kv.second, "port");
                out.push_back(Offer{kv.first, field(kv.second, "host"), field(t.value, "user"), p.empty() ? 0 : std::stoi(p)});   // who offers it
            }
    return out;
}

std::vector<std::string> Room::calls_of(const std::string& user) {
    std::vector<std::string> out;
    for (auto& t : cached("CallsJoined", 30)) {
        if (t.scope != "user:" + user) continue;
        if (const Json* calls = member(t.value, "calls"); calls && calls->type == Json::Obj)
            for (auto& kv : calls->o) out.push_back(kv.first);
    }
    return out;
}

std::vector<Offer> Room::invitations() {
    std::vector<Offer> out;
    for (auto& t : cached("CallsInvitations", 120)) {
        if (t.scope != "user:" + me_key_) continue;
        if (const Json* calls = member(t.value, "calls"); calls && calls->type == Json::Obj)
            for (auto& kv : calls->o) {
                std::string p = field(kv.second, "port");
                out.push_back(Offer{kv.first, field(kv.second, "host"), field(kv.second, "from"), p.empty() ? 0 : std::stoi(p)});
            }
    }
    return out;
}

}  // namespace comms
