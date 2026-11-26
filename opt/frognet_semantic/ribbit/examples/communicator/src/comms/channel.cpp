// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "channel.hpp"
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <random>
#include <stdexcept>
#ifdef _WIN32
#  include <process.h>
#  define getpid _getpid
#else
#  include <unistd.h>
#endif

namespace comms {
using frogram::Json;

static void say(const std::string& s) { std::printf("[channel] %s\n", s.c_str()); std::fflush(stdout); }

std::string channel_secrets_dir() {
    const char* d = std::getenv("FROGNET_CHANNEL_SECRETS");
    return d && *d ? d : channel_frame::DEFAULT_SECRETS;
}

std::string channel_sender_id(const std::string& me) {
    try { return channel_frame::fnid_here(); }
    catch (const channel_frame::DoesNotOpen&) { return "comms-" + me; }   // not a FrogNet node: it has no FNID file
}

std::map<std::string, std::string> channel_fields(const Json& value) {
    std::map<std::string, std::string> f;
    if (value.type != Json::Obj) return f;
    for (auto& kv : value.o) {
        const Json& v = kv.second;
        if (v.type == Json::Str) f[kv.first] = v.s;
        else if (v.type == Json::Bool) f[kv.first] = v.b ? "true" : "false";
        else if (v.type == Json::Num) {
            char b[64];
            if (v.n == std::floor(v.n) && std::fabs(v.n) < 9e15) std::snprintf(b, sizeof b, "%lld", static_cast<long long>(v.n));
            else std::snprintf(b, sizeof b, "%.17g", v.n);
            f[kv.first] = b;
        }
    }
    return f;
}

std::string channel_value_json(const std::map<std::string, std::string>& fields) {
    std::string j = "{";
    for (auto& kv : fields) {
        if (j.size() > 1) j += ",";
        j += Json::quote(kv.first) + ":" + (kv.first == "step" ? kv.second : Json::quote(kv.second));
    }
    return j + "}";
}

Channel::Channel(std::string role, std::string sender_id, std::string secrets, bool framed)
    : role_(std::move(role)), sender_(std::move(sender_id)), secrets_(std::move(secrets)), framed_(framed), pid_(long(::getpid())) {
    if (framed_) say(std::string(channel_frame::BUILD) + ": this program's planes are under the frame, for the Role '" + role_ + "', sending as " + sender_
                     + ", secrets in " + secrets_);
    else say("this program's planes are not under the frame (no --channel); it holds the read on '" + role_ + "'s setting to follow the memory when it moves");
}

std::shared_ptr<channel_frame::Codec> Channel::codec(bool opener, const std::string& far) {
    auto c = std::make_shared<channel_frame::Codec>(opener, far, sender_, secrets_, [](const std::string& s) { say(s); });
    std::lock_guard<std::mutex> g(mu_);
    c->setting(role_, held_);
    std::vector<std::weak_ptr<channel_frame::Codec>> live;
    for (auto& w : codecs_) if (!w.expired()) live.push_back(w);
    live.push_back(c);
    codecs_.swap(live);
    return c;
}

static std::string text_of(const std::optional<channel_frame::Setting>& s) {
    if (!s) return "no setting: tcp, not sealed";
    return s->driver + ", " + (s->number ? "sealed with " + s->protection : std::string("not sealed"))
           + (s->step > 0 ? ", the Role's own secret at step " + std::to_string(s->step) : std::string());
}

void Channel::read(const std::optional<std::map<std::string, std::string>>& fields) {
    std::string to_host; int to_port = 0;
    {
        std::lock_guard<std::mutex> g(mu_);
        if (!fields) {
            // No tuple carries no check. A process that holds a setting keeps it ([A_SETTING_PROVES_ITSELF_V1]).
            if (held_) say("the setting of '" + role_ + "' is gone from the memory; this process keeps the one it holds (" + text_of(held_) + ")");
            return;
        }
        const channel_frame::Taken t = channel_frame::take_setting(role_, *fields, secrets_);
        if (!t.taken) { ignored_ = t.why; say("a setting of '" + role_ + "' is IGNORED: " + t.why + ". This process keeps " + text_of(held_)); return; }
        ignored_.clear();
        held_ = t.setting;
        fields_ = *fields; fields_.erase("ts");
        changes_++;
        say("the setting of '" + role_ + "' is now " + text_of(held_) + (t.fingerprint.empty() ? " (this machine holds no secret for it)" : " (secret " + t.fingerprint + ")")
            + (t.moved ? "; this machine's secret file moved forward to that step and the earlier secret was erased" : ""));
        for (auto& w : codecs_) if (auto c = w.lock()) c->setting(role_, held_);     // each switches in its own stream
        auto h = fields_.find("ram_host"); auto p = fields_.find("ram_port");
        if (h != fields_.end() && p != fields_.end() && !h->second.empty()) { to_host = h->second; to_port = std::atoi(p->second.c_str()); }
    }
    // [THE_MEMORY_MOVES_V1] the setting names where the memory is. Told outside the guard: each holder of a session
    // ends its own and attaches there.
    if (!to_host.empty() && to_port > 0 && RamAt::process().move(to_host, to_port))
        say("the memory of '" + role_ + "' is at " + to_host + ":" + std::to_string(to_port) + " now: this process follows it");
}

std::map<std::string, std::string> Channel::fields() const { std::lock_guard<std::mutex> g(mu_); return fields_; }
std::string Channel::ignored() const { std::lock_guard<std::mutex> g(mu_); return ignored_; }

std::string Channel::held() const { std::lock_guard<std::mutex> g(mu_); return text_of(held_); }
std::string Channel::report_json() const {
    std::lock_guard<std::mutex> g(mu_);
    const channel_frame::Setting s = held_ ? *held_ : channel_frame::Setting{};
    return "{\"role\":" + Json::quote(role_) + ",\"end\":" + Json::quote(sender_ + "." + std::to_string(pid_)) + ",\"driver\":" + Json::quote(s.driver)
           + ",\"number\":" + Json::quote(std::to_string(s.number)) + ",\"protection\":" + Json::quote(s.number ? s.protection : std::string())
           + ",\"step\":" + std::to_string(std::max(0L, s.step)) + ",\"sealed\":" + (s.number ? "true" : "false")
           + ",\"framed\":" + (framed_ ? "true" : "false") + ",\"ram\":" + Json::quote(RamAt::process().at().first + ":" + std::to_string(RamAt::process().at().second))
           + ",\"ignored\":" + Json::quote(ignored_) + ",\"build\":" + Json::quote(channel_frame::BUILD) + "}";
}
bool Channel::sealed() const { std::lock_guard<std::mutex> g(mu_); return held_ && held_->number != 0; }

static std::shared_ptr<Channel>& process_channel() { static std::shared_ptr<Channel> c; return c; }
void Channel::set_for_process(std::shared_ptr<Channel> c) { process_channel() = std::move(c); }
std::shared_ptr<Channel> Channel::of_process() { return process_channel(); }
static std::shared_ptr<Channel>& watched_channel() { static std::shared_ptr<Channel> c; return c; }
std::shared_ptr<Channel> Channel::watching() { return watched_channel(); }

ChannelWatch::ChannelWatch(std::shared_ptr<Channel> channel, const std::string& host, int port) : channel_(std::move(channel)) {
    RamAt::process().start(host, port);
    // [THE_MEMORY_MOVES_V1] this read is what moves the process, and it moves too: its session ends and the loop
    // attaches where the memory is now, reads the setting as it stands there and writes this end's report there.
    moved_id_ = RamAt::process().when_moved([this] { moved_ = true; try { c_.session().shutdown(); } catch (const std::exception&) {} });
    th_ = std::thread([this] {
        bool up = false, fresh = true;
        int64_t after = -1;
        while (!stop_) {
            try {
                if (moved_.exchange(false) && up) { try { c_.close(); } catch (const std::exception&) {} up = false; }
                if (!up) { const auto at = RamAt::process().at(); c_.connect(at.first, at.second); up = true; after = -1; fresh = true; }
                // the setting as it stands, then: wake me when something newer than write-order id `after` exists
                auto ts = after < 0 ? c_.get_all(CHANNEL_SERVICE, 0, 0, 0, CHANNEL_VAR)
                                    : c_.get_all(CHANNEL_SERVICE, 0, double(CHANNEL_REPORT_S), 1, CHANNEL_VAR, after);
                bool mine = false;
                for (auto& t : ts) {
                    if (int64_t(t.id) > after) after = int64_t(t.id);
                    if (t.scope == channel_->role()) { channel_->read(channel_fields(t.value)); mine = true; }
                }
                if (!read_once_ && !mine) channel_->read(std::nullopt);
                // this end's report, on this session, between two held reads: its own tuple, gone when the process is.
                // It is written again each time the held read comes back with nothing, so a reader that asks for
                // reports no older than CHANNEL_REPORT_FRESH_S never sees an end that died without closing.
                // A setting that moved the memory is reported at the new address, by the next pass.
                if (!moved_) c_.put(CHANNEL_SERVICE, CHANNEL_AT, channel_->role() + ":" + channel_->end(), channel_->report_json(), true);
                read_once_ = true; fresh = false;
                if (after < 0) after = 0;
            } catch (const std::exception& e) {
                if (stop_) break;
                if (moved_) continue;                             // the move ended the read
                std::fprintf(stderr, "[channel] the read of the setting failed: %s (the setting held is kept)\n", e.what());
                try { c_.close(); } catch (const std::exception&) {}
                up = false;
                for (int i = 0; i < 10 && !stop_ && !moved_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
        try { c_.close(); } catch (const std::exception&) {}
    });
}

ChannelWatch::~ChannelWatch() {
    stop_ = true;
    RamAt::process().forget(moved_id_);
    // A clean stop takes this end's report with it, on a session of its own: the watcher's is parked in its held read.
    try {
        CommsClient c; const auto at = RamAt::process().at();
        c.connect(at.first, at.second);
        c.remove_tuple(CHANNEL_SERVICE, CHANNEL_AT, channel_->role() + ":" + channel_->end());
        c.close();
    } catch (const std::exception&) {}
    try { c_.session().shutdown(); } catch (const std::exception&) {}   // [A_HELD_READ_ENDS_AT_ONCE_V1]
    if (th_.joinable()) th_.join();
}

ProgramChannel channel_for_program(const std::string& role, const std::string& me, const std::string& ram_host, int ram_port) {
    ProgramChannel p;
    const bool framed = !role.empty();
    p.channel = std::make_shared<Channel>(framed ? role : std::string(CHANNEL_ROLE), channel_sender_id(me), channel_secrets_dir(), framed);
    watched_channel() = p.channel;
    if (framed) Channel::set_for_process(p.channel);
    if (ram_host.empty()) { if (framed) say("no memory was named: this process holds no setting for '" + role + "' (tcp, not sealed)"); return p; }
    p.watch = std::make_unique<ChannelWatch>(p.channel, ram_host, ram_port);
    if (!framed) return p;                                    // nothing of its planes waits on the setting
    for (int i = 0; i < 1000 && !p.watch->read_once(); ++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    if (!p.watch->read_once())
        throw std::runtime_error("the setting of '" + role + "' could not be read from the memory at " + ram_host + ":" + std::to_string(ram_port)
                                 + " in 10 s: no plane is opened before this program knows whether the Role is sealed");
    return p;
}

// ------------------------------------------------------------------------------------------------ the owner's work
namespace cf = channel_frame;

static std::map<std::string, std::string> setting_at(CommsClient& c, const std::string& role) {
    for (auto& t : c.get_all(CHANNEL_SERVICE, 0, 0, 0, CHANNEL_VAR))
        if (t.scope == role) { auto f = channel_fields(t.value); f.erase("ts"); return f; }
    return {};
}
static std::vector<ChannelEnd> ends_at(CommsClient& c, const std::string& role) {
    std::vector<ChannelEnd> out;
    for (auto& t : c.get_all(CHANNEL_SERVICE, CHANNEL_REPORT_FRESH_S, 0, 0, CHANNEL_AT))
        if (t.scope.rfind(role + ":", 0) == 0) { auto f = channel_fields(t.value); f.erase("ts"); out.push_back(ChannelEnd{t.scope.substr(role.size() + 1), f}); }
    return out;
}
static std::string get(const std::map<std::string, std::string>& f, const char* k) { auto it = f.find(k); return it == f.end() ? std::string() : it->second; }

ChannelNow channel_show(const std::string& ram_host, int ram_port, const std::string& role) {
    CommsClient c;
    c.connect(ram_host, ram_port);
    ChannelNow n;
    n.setting = setting_at(c, role); n.setting.erase("check");
    n.ends = ends_at(c, role);
    c.close();
    return n;
}

std::string channel_secret_line(const std::string& role, const std::string& secrets) {
    const cf::Secret s = cf::read_secret(role, secrets);
    return std::string(s.own ? "the Role's own" : "the network's default") + ", hash " + cf::secret_fingerprint(s.value) + (s.own ? ", at step " + std::to_string(s.step) : std::string());
}

std::string ChannelDone::summary() const {
    char ms_text[48]; std::snprintf(ms_text, sizeof ms_text, "%.0f", ms);
    return std::to_string(ends) + " end(s) there: " + std::to_string(running) + (moved ? " attached there" : " running it") + (ignoring ? ", " + std::to_string(ignoring) + " ignored it" : std::string())
           + (behind ? ", " + std::to_string(behind) + " not yet" : std::string()) + " (" + ms_text + " ms after the write)";
}

ChannelDone channel_change(const std::string& ram_host, int ram_port, const std::string& role, const ChannelChange& ch, double wait_s, const std::string& secrets) {
    const bool encryption = ch.seal || ch.unseal || ch.new_key || !ch.algorithm.empty(), move = !ch.move_host.empty();
    if (int(!ch.protocol.empty()) + int(encryption) + int(move) > 1) throw std::runtime_error("a protocol, an encryption and the memory's address are not changed in one write: give one, then the next");
    if (ch.protocol.empty() && !encryption && !move) throw std::runtime_error("nothing to set: give a protocol, seal, new key, algorithm, unseal, or the memory's new address");
    CommsClient c;
    c.connect(ram_host, ram_port);
    const auto now = setting_at(c, role);
    auto next = now; next.erase("check");
    if (!ch.protocol.empty()) {
        if (!cf::driver_known(ch.protocol)) throw std::runtime_error("no protocol '" + ch.protocol + "' is registered here (there are: tcp, chorus, openqasm)");
        next["driver"] = ch.protocol;
    } else if (ch.unseal) {
        next.erase("number"); next.erase("protection");
    } else if (encryption) {
        if (!ch.algorithm.empty() && !cf::protection_known(ch.algorithm)) throw std::runtime_error("no algorithm '" + ch.algorithm + "' is registered here (there are: aes-256-gcm, chacha20-poly1305)");
        const std::string was = get(now, "number");
        if (ch.new_key && (was.empty() || was == "0")) throw std::runtime_error(role + " is not sealed: seal turns its encryption on");
        cf::read_secret(role, secrets);                    // this machine must hold a secret: it makes the setting's check
        std::random_device rd;
        std::string number;
        do { number = std::to_string(((uint64_t(rd()) << 32 | rd()) >> 2) + 1); } while (number == was);   // 62 bits, never 0
        next["number"] = number;                           // the owner's random number: the key is computed from it
        next["protection"] = !ch.algorithm.empty() ? ch.algorithm : !get(now, "protection").empty() ? get(now, "protection") : cf::DEFAULT_PROTECTION;
    } else {
        if (ch.move_port <= 0) throw std::runtime_error("the memory's new address needs a port");
        if (ch.move_host == ram_host && ch.move_port == ram_port) throw std::runtime_error("the memory is at " + ram_host + ":" + std::to_string(ram_port) + " already");
        next["ram_host"] = ch.move_host; next["ram_port"] = std::to_string(ch.move_port);
    }
    if (!next.count("driver")) next["driver"] = "tcp";
    // [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] a Role with its own secret: the step is raised with each new number
    next.erase("step");
    ChannelDone done;
    cf::Secret file;
    try { file = cf::read_secret(role, secrets); } catch (const cf::DoesNotOpen&) { done.checked = false; }
    long step = 0;
    if (done.checked && file.own) {
        step = std::max(std::strtol(get(now, "step").c_str(), nullptr, 10), file.step);
        if (!get(next, "number").empty() && get(next, "number") != get(now, "number")) step += 1;
        if (step) next["step"] = std::to_string(step);
    }
    if (done.checked) next["check"] = cf::check_of(cf::secret_at(role, step, secrets).value, role, next);
    done.setting = next;

    CommsClient moved_to;                                  // [THE_MEMORY_MOVES_V1]
    std::vector<ChannelEnd> were;
    if (move) {
        were = ends_at(c, role);
        try { moved_to.connect(ch.move_host, ch.move_port); }
        catch (const std::exception& e) { throw std::runtime_error("nothing answers as a memory at " + ch.move_host + ":" + std::to_string(ch.move_port) + " (" + e.what() + "): the memory is not moved"); }
        // the chat has no owner to assert it at the new address, so the one who moves the memory carries it
        for (auto& t : c.get_all(SERVICE, 0, 0, 0, "chat")) { moved_to.put(SERVICE, "chat", t.scope, ribbit::RamClient::to_json(t.value), false); done.chat_carried++; }
        moved_to.put(CHANNEL_SERVICE, CHANNEL_VAR, role, channel_value_json(next), false);   // first where everyone is going
    }
    CommsClient& w = move ? moved_to : c;                  // where the ends' reports are read
    int64_t after = 0;
    for (auto& t : w.get_all(CHANNEL_SERVICE, 0, 0, 0, CHANNEL_AT)) if (int64_t(t.id) > after) after = int64_t(t.id);
    const uint64_t wrote = c.put(CHANNEL_SERVICE, CHANNEL_VAR, role, channel_value_json(next), false);
    if (!move) after = int64_t(wrote);
    // The ends' reports, (channel, at, <Role>:*): a held read until every end shows this setting or says it ignored
    // it, or wait_s has passed. An end with nothing to say is named.
    const std::string here = ch.move_host + ":" + std::to_string(ch.move_port);
    auto matches = [&](const std::map<std::string, std::string>& r) {
        if (move) return get(r, "ram") == here;
        const std::string want = get(next, "number").empty() ? "0" : get(next, "number");
        return get(r, "driver") == get(next, "driver") && get(r, "number") == want && (want == "0" || get(r, "protection") == get(next, "protection"))
               && (get(next, "step").empty() || get(r, "step") == get(next, "step"));
    };
    const auto t0 = std::chrono::steady_clock::now();
    std::map<std::string, std::map<std::string, std::string>> ends;
    for (bool first = true;; first = false) {
        const double left = wait_s - std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        if (!first && left <= 0) break;
        // An end that follows reports at the new address; one that ignores the move says so at the old one. So for a
        // move the read held at the new address is held half a second at a time, and the old one is looked at between.
        if (!first) for (auto& t : w.get_all(CHANNEL_SERVICE, 0, move ? std::min(left, 0.5) : left, 1, CHANNEL_AT, after)) if (int64_t(t.id) > after) after = int64_t(t.id);
        ends.clear();
        for (auto& e : were) ends[e.end] = {};                                  // every end that was at the old address is expected at the new
        if (move) for (auto& e : ends_at(c, role)) if (!get(e.report, "ignored").empty()) ends[e.end] = e.report;
        for (auto& e : ends_at(w, role)) ends[e.end] = e.report;
        bool all = true;
        for (auto& e : ends) if (!matches(e.second) && get(e.second, "ignored").empty()) all = false;
        if (all) break;
    }
    done.ms = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() * 1000;
    done.moved = move;
    char waited[32]; std::snprintf(waited, sizeof waited, "%.0f", wait_s);
    for (auto& e : ends) {
        ++done.ends;
        if (matches(e.second)) { ++done.running; continue; }
        if (!get(e.second, "ignored").empty()) { ++done.ignoring; done.lines.push_back(e.first + " IGNORED it: " + get(e.second, "ignored")); }
        else { ++done.behind; done.lines.push_back(e.first + (move ? " has not arrived at " + here + " in " : " has not reported it in ") + waited + " s"); }
    }
    try { c.close(); } catch (const std::exception&) {}
    if (move) try { moved_to.close(); } catch (const std::exception&) {}
    return done;
}

}  // namespace comms
