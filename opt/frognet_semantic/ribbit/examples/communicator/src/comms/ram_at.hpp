// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [THE_MEMORY_MOVES_V1] Where the Communicator's memory (comms-ram) is, for this process.
//
// A program is started with the memory's address. The Role's setting may name another one: its owner moved the memory
// (comms-channel move, or the Security tab's "Change RAM host"). The process's read on the setting fires, the address
// here changes, and everything in the process that holds a session to the memory is told: it ends that session,
// attaches to the new address, asserts its own tuples there and sets up its held reads again. Nothing is copied by a
// participant and nothing asks another participant anything. A call's audio and video go to the media server, not
// to the memory, so they are not touched.
#pragma once
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace comms {

class RamAt {
public:
    static RamAt& process() { static RamAt r; return r; }
    // What the program was started with. The first call says where the memory is; later ones change nothing.
    void start(const std::string& host, int port) {
        std::lock_guard<std::mutex> g(mu_);
        if (host_.empty()) { host_ = host; port_ = port; }
    }
    std::pair<std::string, int> at() const { std::lock_guard<std::mutex> g(mu_); return {host_, port_}; }
    uint64_t moves() const { std::lock_guard<std::mutex> g(mu_); return moves_; }
    // The memory is at another address now. Returns false when it is the address this process already has.
    bool move(const std::string& host, int port) {
        std::vector<std::function<void()>> tell;
        {
            std::lock_guard<std::mutex> g(mu_);
            if (host == host_ && port == port_) return false;
            host_ = host; port_ = port; ++moves_;
            for (auto& kv : told_) tell.push_back(kv.second);
        }
        for (auto& fn : tell) fn();                          // each ends its own session; none is called under the guard
        return true;
    }
    int when_moved(std::function<void()> fn) { std::lock_guard<std::mutex> g(mu_); told_[++next_] = std::move(fn); return next_; }
    void forget(int id) { std::lock_guard<std::mutex> g(mu_); told_.erase(id); }
private:
    mutable std::mutex mu_;                                  // this process's copy of the address and its list of who to tell
    std::string host_;
    int port_ = 0, next_ = 0;
    uint64_t moves_ = 0;
    std::map<int, std::function<void()>> told_;
};

}  // namespace comms
