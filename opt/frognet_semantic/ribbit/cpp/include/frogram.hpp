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
#include "semcodec.hpp"
#include "bagarr.hpp"
#include <condition_variable>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <functional>
#include <string>
#include <string_view>
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
    // [CANONICAL_BODY_V1] the string as the semantic wire rebuilds it (json.dumps, ensure_ascii): a body written in the
    // wire's own form -- keys sorted, compact, ASCII -- is proven by comparing bytes, not by parsing both texts
    static std::string quote_ascii(const std::string& s);
    static void quote_ascii_into(std::string& out, std::string_view s);
};

struct Stats { uint64_t coalesced = 0;   // [CLIENT_COALESCES_V1] calls served by an identical request's one response
                uint64_t raw = 0, repeat = 0, same = 0, miss = 0, bytes_out = 0, bytes_in = 0, posted = 0, posts_refused = 0,
                full = 0, diff = 0, rdiff = 0, rraw = 0, data_frames = 0, data_bytes = 0,
                batch_bytes = 0,   // the memory's own frames: a read after an id, a trigger's request
                cell_bytes = 0, cell_writes = 0,   // [CELL_WRITE_V1] the writes' frames, as they left
                queued_now = 0,   // [DIAG-QUEUE] requests and writes waiting for the sender now
                rows_wire = 0, rows_whole = 0; };   // [ROWS_ON_THE_SEMANTIC_WIRE_V1] rows read: bytes as they arrived, and made whole

struct RawCell;
class Session {
public:
    // api: the RAM interface endpoint on that host -- the vendor's name for it ([RAM_INTERFACE_IS_THE_VENDORS_V1]).
    Session(const std::string& host, int port, const std::string& api = "/ram.php");   // throws Unreachable / Refused
    ~Session();
    // One request to the memory's API; blocks the calling thread only.
    Json call(const std::string& method, const std::string& path, const std::string& body = "",
              double timeout_s = 15.0);
    // [CALL_TEXT_V1] the same call; the answer comes back as its text (an HTTP status >= 400 still throws Refused)
    std::string call_text(const std::string& method, const std::string& path, const std::string& body = "",
                          double timeout_s = 15.0);
    // Send and do not wait. The request goes out and this returns; the reply is read
    // and discarded when it arrives (a refusal is counted in stats().posts_refused,
    // not thrown). For state that is about to be replaced anyway. The only thing that
    // slows the caller is TCP itself, when the far end is not keeping up.
    void post(const std::string& method, const std::string& path, const std::string& body = "");
    // [NOBODY_WAITS_V1] the call, returning at once: same engine, same semantic wire (SAME/DIFF), same permanent
    // sockets, any number in flight. done(status, body, err) runs on the thread that reads the answer (err non-empty:
    // the session is gone or the call could not be made; status 0). Only a queue of QUEUE_MAX unsent requests holds
    // the caller back (an atomic wait), as TCP would.
    using Done = std::function<void(int status, std::string body, std::string err)>;
    void call_async(const std::string& method, const std::string& path, const std::string& body, Done done);
    // A write not waited for, by its coordinates: what Memory::write_nowait sends -- the cell's own request on the
    // semantic wire, a DIFF against that cell's reference ([EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1]).
    void post_write(const std::string& service, const std::string& variable, const std::string& instance, const std::string& bag);
    // [FIELDS_NOT_TEXT_V1] the same write with the bag as fields: its field order (one vector per shape, kept by the
    // caller for the session's life) and its values in that order
    void post_write_fields(const std::string& service, const std::string& variable, const std::string& instance,
                           const std::vector<std::string>& order, semcodec::Fields fields);
    // [READS_AFTER_IN_BINARY_V1] one of the memory's verbs as a frame (MAGIC, op, its bytes): sent, its answer waited
    // for by the calling thread; the answer's bytes after MAGIC + op. What Memory::read uses for reads after an id.
    // rows_out: a read's rows as the session's reader decoded them, once -- shared by every reader its answer released
    std::string call_frame(std::string frame, double timeout_s, std::shared_ptr<const std::vector<RawCell>>* rows_out = nullptr);
    // [FOLLOW_STREAM_V1] a frame answered many times: `on` is called on the session's reader thread with each answer's
    // bytes after MAGIC + op, and once with an error when the trigger or the session ends
    void follow_frame(std::string frame, std::function<void(std::string body, std::string err)> on);
    // false once the session's connections are gone (whoever closed them); why: the reason
    bool alive(std::string* why = nullptr) const;
    Stats stats() const;
    // Closes both connections. Any call parked in the memory returns by throwing
    // Unreachable. The object stays valid until it is destroyed.
    void shutdown();
    // what has been handed to this session has left on its socket (the sender has nothing queued and has flushed), or
    // the time is up; false: it had not. An application leaving -- a disconnect -- before its writes have gone.
    bool flush(double timeout_s = 5.0);
    const std::string& token() const { return token_; }
    const std::string& api() const { return api_; }
private:
    struct Pending; struct Impl;
    void send_write(std::string frame);                  // a cell write, handed to the session's writer
    void send_loop();
    void read_loop(int fd, bool data);
    struct InFlight; void apply_reply(const InFlight& inf, const std::string& reply);
    void stamp(InFlight& inf); void ready(const InFlight& inf, const std::string& reply); void turn_done(const InFlight& inf);   // [WIRE_ORDER_TURNS_V1]
    void apply_loop();                                   // [CLIENT_APPLIER_V1] the one thread that applies replies
    std::string host_, token_, api_; int port_, req_ = -1, ret_ = -1, dat_ = -1;
    std::unique_ptr<Impl> d_;
};

struct Cell { uint64_t id = 0; std::string service, variable, instance; Json bag; double updated = 0;
              bool gone = false; };   // [GONE_WITH_ITS_WRITER_V1] the row is gone (its writer's session ended): bag null
// [RAW_ROWS_V1] a row as the memory holds it: the bag's text, not parsed (a reader that has its own format for it)
// [DECODE_ONCE_V1] arr: the bag as the session decoded it, once (bag is then empty); bag: text, for a row given as text
struct RawCell { uint64_t id = 0; std::string instance, bag; double updated = 0; std::string variable; ribbit::BagArrP arr;
                 bool gone = false;   // [GONE_WITH_ITS_WRITER_V1] the row is gone: no bag
                 std::string text() const { return arr ? arr->json() : bag; } };

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
    // [FIELDS_NOT_TEXT_V1] the bag as fields, not text (Session::post_write_fields)
    void write_fields_nowait(const std::string& service, const std::string& variable, const std::string& instance,
                             const std::vector<std::string>& order, semcodec::Fields fields) { s_.post_write_fields(service, variable, instance, order, std::move(fields)); }
    // instance == "" leaves the third coordinate open. after >= 0 with wait_s > 0 is the
    // blocking read: held by the memory until something is written past `after`.
    std::vector<Cell> read(const std::string& service, const std::string& variable,
                           const std::string& instance = "", int64_t after = -1, double wait_s = 0,
                           int fresh_s = 0) override;
    void remove(uint64_t id) override;
    // [FOLLOW_STREAM_V1] a standing trigger on a family: every row written after `after`, in write order, as it is
    // written, until the session ends -- one request, no read per batch. `on` runs on the session's reader thread:
    // rows (possibly none), or an error once at the end. after >= 0: the first answer is every row past it; after < 0:
    // from now on -- the first answer is empty, and what is written after the memory has the request follows.
    // ([FAMILY_HOOKS_V1] variable "" follows the whole service.)
    void follow(const std::string& service, const std::string& variable, const std::string& instance, int64_t after,
                std::function<void(std::vector<RawCell>& rows, const std::string& err)> on);
    // [NEWEST_ID_V1] the newest id written to a family (variable "" = the whole service): where "from now" begins
    uint64_t newest_id(const std::string& service, const std::string& variable);
    // [RAW_ROWS_V1] the held read (after >= 0) with each bag left as text
    std::vector<RawCell> read_raw(const std::string& service, const std::string& variable, const std::string& instance,
                                  int64_t after, double wait_s = 0, int fresh_s = 0);
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
