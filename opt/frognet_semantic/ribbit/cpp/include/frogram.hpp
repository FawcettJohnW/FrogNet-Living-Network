// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogram.hpp -- FrogNet RAM client for C++. The same three things the Python
// reference has, byte for byte on the wire:
//
//   frogram::Session   the client end of FNWP, the full semantic contract
//                      ([ENGINES_IN_THE_SESSION_V1]): fnwp::ClientEngine (REQ_FULL /
//                      REQ_DIFF / REQ_REPEAT / REQ_RAW out; RESP_SAME / RESP_DIFF /
//                      RESP_RAW / REQ_MISS back; templates, instances, the SAME
//                      cache). Three outbound connections: HELLO (requests),
//                      HELLO RETURN:<token> (replies, tagged with the implicit
//                      sequence), HELLO DATA:<token> (the high-speed data socket:
//                      replies too large to cross the return socket without
//                      holding up the others, [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]).
//   frogram::Memory    cells addressed by (service, variable, instance):
//                      write, read (by partial index, optionally BLOCKING until
//                      something newer than `after` is written), remove.
//   frogram::Plane     the fast plane: send-or-drop publish of a current value,
//                      and a read that blocks until the plane has something
//                      newer; one `after` is exact across a whole prefix.
//
// NO FALLBACKS: one store, one plane; a failure is thrown, nothing is substituted.
// C++11; POSIX sockets or Winsock (link ws2_32); no other dependency.
#pragma once
#include <condition_variable>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace frogram {

struct Unreachable : std::runtime_error { using std::runtime_error::runtime_error; };
struct Refused     : std::runtime_error { using std::runtime_error::runtime_error; };

// ---- a small JSON value: enough to read what the memory returns -------------
struct Json {
    enum Type { Null, Bool, Num, Str, Arr, Obj } type = Null;
    bool b = false; double n = 0; std::string s;
    std::vector<Json> a; std::vector<std::pair<std::string, Json>> o;
    const Json& operator[](const std::string& k) const;     // Null if absent
    const Json& operator[](size_t i) const { return a.at(i); }
    size_t size() const { return type == Arr ? a.size() : o.size(); }
    static Json parse(const std::string& text);              // throws Refused on bad JSON
    static std::string quote(const std::string& s);
};

struct Stats { uint64_t coalesced = 0;   // [CLIENT_COALESCES_V1] calls served by an identical request's one response
                uint64_t raw = 0, repeat = 0, same = 0, miss = 0, bytes_out = 0, bytes_in = 0, posted = 0, posts_refused = 0,
                full = 0, diff = 0, rdiff = 0, rraw = 0, data_frames = 0, data_bytes = 0; };

class Session {
public:
    // api: the RAM interface endpoint on that host -- the vendor's name for it ([RAM_INTERFACE_IS_THE_VENDORS_V1]).
    Session(const std::string& host, int port, const std::string& api = "/ram.php");   // throws Unreachable / Refused
    ~Session();
    // One request to the memory's API; blocks the calling thread only.
    Json call(const std::string& method, const std::string& path, const std::string& body = "",
              double timeout_s = 15.0);
    // Send and do not wait. The request goes out and this returns; the reply is read
    // and discarded when it arrives (a refusal is counted in stats().posts_refused,
    // not thrown). For state that is about to be replaced anyway. The only thing that
    // slows the caller is TCP itself, when the far end is not keeping up.
    void post(const std::string& method, const std::string& path, const std::string& body = "");
    Stats stats() const;
    // Closes both connections. Any call parked in the memory returns by throwing
    // Unreachable. The object stays valid until it is destroyed.
    void shutdown();
    const std::string& token() const { return token_; }
    const std::string& api() const { return api_; }
private:
    struct Pending; struct Impl;
    void send_loop();
    void read_loop(int fd, bool data);
    struct InFlight; void apply_reply(const InFlight& inf, const std::string& reply);
    void stamp(InFlight& inf); void ready(const InFlight& inf, const std::string& reply); void turn_done(const InFlight& inf);   // [WIRE_ORDER_TURNS_V1]
    std::string host_, token_, api_; int port_, req_ = -1, ret_ = -1, dat_ = -1;
    std::unique_ptr<Impl> d_;
};

struct Cell { uint64_t id = 0; std::string service, variable, instance; Json bag; double updated = 0; };

uint64_t calls_this_thread();
// [READ_YOUR_OWN_WRITE_V1] While set, every Memory::write on this thread appends (variable, id) here.
void log_writes_to(std::vector<std::pair<std::string, uint64_t>>* sink);
std::vector<std::pair<std::string, uint64_t>>* current_write_log();   // [ROUND_TRIPS_V1] blocking Session::call()s made by the calling thread so far

// [RESIDENT_REGION_V1] What a participant does to the memory. A remote participant does it through a Session
// (Memory, below); the RAM server's own region code does it to the memory itself (ram_server.cpp ResidentMemory),
// with no session, no loopback and no copy of what it reads beyond the one cell it asked for.
class MemoryApi {
public:
    virtual ~MemoryApi() = default;
    virtual uint64_t write(const std::string& service, const std::string& variable, const std::string& instance,
                           const std::string& bag_json) = 0;
    virtual void write_nowait(const std::string& service, const std::string& variable, const std::string& instance,
                              const std::string& bag_json) = 0;
    virtual std::vector<Cell> read(const std::string& service, const std::string& variable,
                                   const std::string& instance = "", int64_t after = -1, double wait_s = 0,
                                   int fresh_s = 0) = 0;
    virtual void remove(uint64_t id) = 0;
};

class Memory : public MemoryApi {
public:
    // The endpoint is the session's: the engine recognizes RAM-interface calls by it. A different one is refused.
    explicit Memory(Session& s, const std::string& api = "/ram.php") : s_(s), api_(api) {
        if (api != s.api()) throw Refused("Memory api " + api + " is not this session's RAM interface " + s.api()); }
    uint64_t write(const std::string& service, const std::string& variable, const std::string& instance,
                   const std::string& bag_json) override;
    // The same write, not waited for: no id comes back.
    void write_nowait(const std::string& service, const std::string& variable, const std::string& instance,
                      const std::string& bag_json) override;
    // instance == "" leaves the third coordinate open. after >= 0 with wait_s > 0 is the
    // blocking read: held by the memory until something is written past `after`.
    std::vector<Cell> read(const std::string& service, const std::string& variable,
                           const std::string& instance = "", int64_t after = -1, double wait_s = 0,
                           int fresh_s = 0) override;
    void remove(uint64_t id) override;
private:
    Session& s_; std::string api_;
};

struct PlaneRow { std::string name; uint64_t gen; std::string bytes; };

class Plane {
public:
    Plane(const std::string& host, int port);
    ~Plane();
    bool publish(const std::string& name, uint64_t gen, const std::string& bytes);   // false = shed
    void drop(const std::string& name);
    // Rows published since `after` (the plane's own sequence; updated in place), in arrival
    // order. Empty = not yet. Throws std::out_of_range if nothing was ever published under
    // the prefix and no wait was asked for.
    std::vector<PlaneRow> read(const std::string& prefix, uint64_t& after, uint32_t wait_ms = 0);
    uint64_t sent = 0, shed = 0;
private:
    struct Impl; std::unique_ptr<Impl> d_; int fd_ = -1;
    void read_loop();
};

}  // namespace frogram
