// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// The room: a participant's view of the Communicator's shared memory (comms-ram), by the tuple contract in
// COMMUNICATOR-SPEC.md -- the same rows the Python Communicator reads and writes.
//   presence    host:<ip>:presence:<me_id>        {name, status, caps, ts}     heartbeat every 5 s; stale = gone
//   chat        session:chat:<session>:<msg_id>   {from_id, from_name, text, ts(ms)}   one row per line
//   MediaSpeed  session:<session>:viewer:<me_id>  {session, viewer, bps, addr, ts}     this viewer's download cap
#pragma once
#include "tuples.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <map>
#include <set>
#include <mutex>
#include <string>
#include <thread>
#include <vector>


namespace comms {

class CommsClient : public ribbit::TuplesClient {
public:
    std::string vendor_id() const override { return "comms"; }
};

constexpr const char* SERVICE = "communicator";

struct Member { std::string me_id, name, status, caps, addr; double age_s = 0; };
struct Line { std::string from_id, from_name, text; long long ts_ms = 0; };
struct Offer { std::string session, media_host, offered_by; int media_port = 0; };   // a call someone is offering

class Room {
public:
    static constexpr int HEARTBEAT_S = 5, PRESENCE_FRESH_S = 15;
    Room(const std::string& ram_host, int ram_port, const std::string& name, const std::string& caps = "video,audio");
    ~Room();
    void announce(const std::string& status = "online");
    std::vector<Member> roster();                    // everyone fresh, this participant included
    void send_chat(const std::string& session, const std::string& text);
    std::vector<Line> read_chat(const std::string& session);   // in order of ts
    void set_media_speed(const std::string& session, long bps); // 0 = no cap
    // Calls (comms_control.py's contract): CallsAnnounce at host:<ip> (this machine's offers, merged with other
    // processes' on the same machine), CallsJoined at user:<me_key>, CallsInvitations at user:<invitee>.
    void offer_call(const std::string& session, const std::string& media_host, int media_port);
    void join_call(const std::string& session, const std::string& media_host, int media_port);
    void invite(const std::string& who, const std::string& session, const std::string& media_host, int media_port);
    std::vector<Offer> offers();                         // every call offered anywhere, fresh
    std::vector<Offer> invitations();                    // calls this participant is invited to
    void refresh_calls();                                // rewrite this participant's call rows (heartbeat)
    std::vector<std::string> calls_of(const std::string& user);   // the sessions `user` says it is on (CallsJoined)
    const std::string& me_id() const { return me_id_; }
    // [HOLD_THE_READ_V1] Everything above that READS is answered from this participant's copy of the region, kept by
    // one held read ("wake me when anything newer than write-order id N exists"). version() changes when it does;
    // wait_change() blocks until it does or timeout_ms passes.
    uint64_t version() const { return version_.load(); }
    // [MEDIAHOLD_IS_MEMORY_V1] what the media server is doing for THIS participant in this call (from the copy)
    struct Hold { bool present = false, unanchored = false, key_held = false, passthrough = false, audio_only = false;
                  uint64_t aimed = 0, delivered = 0, inters_shed = 0, keys_held = 0; int w = 0, h = 0; };
    Hold my_hold(const std::string& session) const;
    bool wait_change(uint64_t seen, int timeout_ms);
    std::string status() const { std::lock_guard<std::mutex> g(mu_); return status_; }
private:
    CommsClient c_;
    CommsClient w_;                                      // the watcher's own session: its held read never delays a write
    struct Row { ribbit::Tuple t; std::chrono::steady_clock::time_point got; };
    std::map<std::string, Row> rows_;                    // (var, scope) -> newest; guarded by cache_mu_
    mutable std::mutex cache_mu_;
    std::condition_variable cache_cv_;
    std::atomic<uint64_t> version_{0};
    int64_t last_id_ = -1;
    std::thread watch_;
    void watch();
    void absorb(std::vector<ribbit::Tuple>&& ts, bool full);
    // [READ_YOUR_OWN_WRITES_V1] every write goes to memory AND into this participant's own copy at once
    void write(const std::string& var, const std::string& scope, const std::string& value_json, bool own);
    std::vector<ribbit::Tuple> cached(const std::string& var, int fresh_s) const;
    std::string name_, me_id_, me_key_, caps_, status_ = "online";
    std::map<std::string, std::pair<std::string, int>> offered_, joined_;
    std::set<std::string> ever_offered_;
    void write_announce();
    void write_joined();
    mutable std::mutex mu_;
    std::atomic<bool> stop_{false};
    std::thread beat_;
};

// For the media server: every viewer's MediaSpeed row in one read; and a poller that applies them every 2 s.
}  // namespace comms
