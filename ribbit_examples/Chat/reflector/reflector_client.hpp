// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// reflector_client.hpp -- the participant end of the fast-plane reflector.
//
//   Reflector r("host:8789", "my-id");
//   r.subscribe("race42/kart", [](uint64_t gen, const std::string& body){ ... });   // pushed as they arrive
//   r.publish("race42/kart", tick, bytes);      // send-or-drop; reflected to that stream's subscribers
//
// One kept connection. Publishing is send-or-drop at this socket (a value that
// cannot go now is dropped; the next is newer). Received frames arrive on a
// reader thread and are handed to the per-stream callback. C++11, POSIX/Winsock.
#pragma once
#include <atomic>
#include <stdexcept>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>

namespace reflector {
struct Gone : std::runtime_error { using std::runtime_error::runtime_error; };
typedef std::function<void(uint64_t gen, const std::string& body)> OnFrame;

class Reflector {
public:
    Reflector(const std::string& hostport, const std::string& id, double connect_timeout = 10.0);
    ~Reflector();
    void subscribe(const std::string& stream, OnFrame cb);
    void unsubscribe(const std::string& stream);
    bool publish(const std::string& stream, uint64_t gen, const std::string& body);   // false = shed at our socket
    uint64_t sent = 0, shed = 0, received = 0;
private:
    void reader_loop();
    int fd_ = -1; std::string id_; std::mutex w_, cb_m_; std::map<std::string, OnFrame> cbs_;
    std::atomic<bool> dead_{false}; std::thread reader_;
};
}  // namespace reflector
