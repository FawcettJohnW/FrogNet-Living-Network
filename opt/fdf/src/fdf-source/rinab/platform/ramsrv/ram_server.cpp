#include <cstdlib>
#include <thread>
#include <pthread.h>
#include <unistd.h>
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ram_server -- one service's FrogNet RAM, the whole server side, in C++.
//
//     ram_server --listen 0.0.0.0:8788
//
// The same contract as ram_listener.py + ram.php, in one process: it speaks
// FNW1 to clients (HELLO / HELLO RETURN:<token>, replies tagged with the
// implicit sequence, REQ_RAW / REQ_REPEAT in, RESP_RAW / RESP_SAME / REQ_MISS
// out) and it IS the memory: cells addressed by (service, variable, instance),
// held in RAM. Existing clients, Python and C++, work against it unchanged.
//
//   op=write   replaces the cell; the write takes the next id -- the memory's
//              own write order, assigned under one lock at the one place
//              everything arrives.
//   op=read    by partial index; fresh_s; and the blocking reads: min_rows, and
//              after=N ("wake me when something is written past N").
//   op=remove  by id.
//
// THE BLOCKING READ PARKS ON A CONDITION VARIABLE and is woken by the write that
// satisfies it. There is no re-check interval, so there is nothing to tune and
// nothing for a fast writer to run past while the reader naps.
//
// The backing store is this application's choice (here: memory; a restart is
// an empty memory). Nothing here knows what the application is.
// NO FALLBACKS: a request it does not understand is refused, not guessed at.
#include "frogram.hpp"
#include "ram_memory.hpp"
#include "lisp_engine.hpp"
#include "ebr.hpp"
// [SESSION_IS_LIVENESS_V1] the session a request arrived on, and the region's hook for a session ending
static thread_local uint64_t tl_session_id = 0;
uint64_t rina_session_id() { return tl_session_id; }
void rina_session_down(uint64_t id);
void rina_session_peer(uint64_t id, const std::string& peer);   // [SESSION_PEER_V1]
#include "version.hpp"

#ifdef _WIN32
#  ifndef _WIN32_WINNT
#    define _WIN32_WINNT 0x0600
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  define FR_CLOSE(fd) closesocket(fd)
#  define FR_SHUT_RDWR SD_BOTH
#  define MSG_NOSIGNAL 0
   typedef char fr_optval_t;
#else
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <sys/socket.h>
#  include <unistd.h>
#  define FR_CLOSE(fd) ::close(fd)
#  define FR_SHUT_RDWR SHUT_RDWR
   typedef int fr_optval_t;
#endif

#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstring>
#include <iostream>
#include <list>
#include <sstream>
#include <tuple>
#include <atomic>
#include <random>
#include "fnwp_engine.hpp"
#include "dataplane.hpp"   // [ENGINES_IN_THE_SESSION_V1] the full FNWP contract for clients that open the data socket

using frogram::Json;
namespace {
const char MAGIC[] = "FNW1";
enum : uint8_t { REQ_REPEAT = 0x02, REQ_RAW = 0x03, RESP_SAME = 0x13, RESP_RAW = 0x14, REQ_MISS = 0x21, OP_ERROR = 0x30, OP_HELLO = 0x50 };
const size_t MAX_FRAME = 1 << 20, CACHE_ENTRIES = 4096, CACHE_MAX_REQ = 8192;
const double WAIT_MAX_S = 30.0;

double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
bool g_quiet = false;
void logline(const std::string& m) { if (g_quiet) return; std::time_t t = std::time(nullptr); char b[32]; std::strftime(b, sizeof b, "%F %T", std::localtime(&t)); std::cout << "[RAM-SERVER] " << b << " " << m << std::endl; }

// ---- wire helpers
bool send_all(int fd, const std::string& b) { size_t o = 0; while (o < b.size()) { ssize_t n = ::send(fd, b.data() + o, b.size() - o, MSG_NOSIGNAL); if (n <= 0) return false; o += size_t(n); } return true; }
bool recv_n(int fd, size_t n, std::string& d) { d.assign(n, '\0'); size_t o = 0; while (o < n) { ssize_t r = ::recv(fd, &d[o], n - o, 0); if (r <= 0) return false; o += size_t(r); } return true; }
std::string be32(uint32_t v) { uint32_t n = htonl(v); return std::string(reinterpret_cast<char*>(&n), 4); }
std::string be16(uint16_t v) { uint16_t n = htons(v); return std::string(reinterpret_cast<char*>(&n), 2); }
uint32_t rd32(const std::string& s, size_t o) { uint32_t n; std::memcpy(&n, s.data() + o, 4); return ntohl(n); }
bool recv_frame(int fd, std::string& f) { std::string l; if (!recv_n(fd, 4, l)) return false; uint32_t n = rd32(l, 0); if (n < 5 || n > MAX_FRAME) return false; return recv_n(fd, n, f) && !f.compare(0, 4, MAGIC); }
bool send_frame(int fd, const std::string& f) { return send_all(fd, be32(uint32_t(f.size())) + f); }
std::string hash16(const std::string& s) { uint64_t a = 1469598103934665603ULL, b = 0x9E3779B97F4A7C15ULL; for (unsigned char c : s) { a = (a ^ c) * 1099511628211ULL; b = (b ^ (c + 0x5bu)) * 0x100000001B3ULL; b ^= b >> 29; }
    std::string o(16, '\0'); for (int i = 0; i < 8; ++i) { o[7 - i] = char(a & 0xff); a >>= 8; o[15 - i] = char(b & 0xff); b >>= 8; } return o; }

// ---- JSON out (frogram::Json reads; this writes it back exactly enough)
std::string dump(const Json& j) {
    switch (j.type) {
        case Json::Null: return "null";
        case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { char b[40]; if (std::floor(j.n) == j.n && std::fabs(j.n) < 9e15) snprintf(b, sizeof b, "%.0f", j.n); else snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case Json::Str: return Json::quote(j.s);
        case Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        case Json::Obj: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
    return "null";
}
// [URLDEC_AS_RAM_PHP_V1] ram.php reads its query through PHP's $_GET (urldecode): "%" + two hex digits is that byte, any
// other "%" is literal, "+" is a space. This used to hand whatever followed "%" to std::stoi(.., 16): "%zz" threw out of
// the session thread and aborted the whole server (every client lost), and "%4z" silently decoded as byte 0x04.
int hexval(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; }
std::string urldec(const std::string& s) {
    std::string o;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size() && hexval(s[i + 1]) >= 0 && hexval(s[i + 2]) >= 0) {
            o += char(hexval(s[i + 1]) * 16 + hexval(s[i + 2])); i += 2;
        } else o += (s[i] == '+' ? ' ' : s[i]);
    }
    return o;
}
std::map<std::string, std::string> query(const std::string& path) { std::map<std::string, std::string> q; size_t p = path.find('?'); if (p == std::string::npos) return q;
    std::stringstream ss(path.substr(p + 1)); std::string kv; while (std::getline(ss, kv, '&')) { size_t e = kv.find('='); q[urldec(kv.substr(0, e))] = e == std::string::npos ? "" : urldec(kv.substr(e + 1)); } return q; }

// ---- the memory: ram_memory.hpp. [ROW_LOCKS_V1] John 2026-09-27: row-level multi-reader/single-writer locks and no
// other lock; a condition belongs to the wait (a trigger on the row's envelope for an exact address, on the shared
// space for a pattern), built only when someone waits.
using Cell = ramm::Cell;
ramm::Memory MEM;

std::string ok_rows(const std::vector<Cell>& rows) { std::string o = "{\"ok\":true,\"rows\":[";
    for (size_t k = 0; k < rows.size(); ++k) { const Cell& c = rows[k]; char u[40]; snprintf(u, sizeof u, "%.3f", c.updated);
        o += (k ? "," : "") + std::string("{\"id\":") + std::to_string(c.id) + ",\"service\":" + Json::quote(c.service) + ",\"variable\":" + Json::quote(c.variable) +
             ",\"instance\":" + Json::quote(c.instance) + ",\"bag\":" + c.bag + ",\"updated_epoch\":" + u + "}"; }
    return o + "]}"; }
struct Reply { int status; std::string body; };
Reply bad(int code, const std::string& msg) { return Reply{code, "{\"ok\":false,\"error\":" + Json::quote(msg) + "}"}; }

// Runs one API request. Returns false if it must WAIT (a blocking read not yet satisfied);
// the caller then parks it on its own thread by calling again with may_wait = true.
// [RAM_INTERFACE_IS_THE_VENDORS_V1] The endpoint is the application vendor's to name (--api); /ram.php is what this
// server has always answered, and stays what it answers when --api is not given.
std::string API_PATH = "/ram.php";
int LISTEN_PORT = 0;
std::string LISTEN_HOST = "127.0.0.1";
// [VENDOR_API_V1] The LISP region's operations, executed by the host on Engines -- the same lisp_engine.hpp the
// participants run -- connected to THIS server over loopback, so each round trip a compound operation makes stays on
// the host and the caller pays one Internet round trip for the whole operation.
// [RESIDENT_REGION_V1] John 2026-09-28. The region's operations run IN the memory. Until now they ran through a pool of
// Engines that were each a client of this very server -- a loopback session, copied views of these rows, a mutex per
// Engine held across the whole operation, and a wait for every Engine to see each write: "absolutely unacceptable".
// Now each operation, on its own request thread, gets a resident Engine whose memory is MEM itself: it reads the rows it
// names (each under that row's shared lock) and writes its own rows (each under that row's exclusive lock). There is no
// other lock, no session, no view, no copy of a table, and nothing to wait for: the write is the row.
// [OWN_WRITES_HELD_V1] the cells a region operation wrote, whole, so the caller can hold them at once
struct WrittenCell { std::string variable, instance, bag; uint64_t id; };
static thread_local std::vector<WrittenCell>* tl_written_cells = nullptr;
class ResidentMemory : public frogram::MemoryApi {
public:
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override {
        uint64_t id = MEM.write(s, v, i, bag, wall());
        if (auto* log = frogram::current_write_log()) log->emplace_back(v, id);   // [READ_YOUR_OWN_WRITE_V1] for the caller
        if (tl_written_cells && s == "lisp") tl_written_cells->push_back(WrittenCell{v, i, bag, id});
        return id;
    }
    void write_nowait(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { write(s, v, i, bag); }
    std::vector<frogram::Cell> read(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s, int fresh_s) override {
        if (wait_s > 0) throw std::logic_error("a resident region operation holds nothing: no blocking read (" + s + "/" + v + ")");
        std::vector<frogram::Cell> out;
        for (auto& c : MEM.match(s, v, i, after, fresh_s, wall()))
            out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, Json::parse(c.bag), c.updated});
        return out;
    }
    void remove(uint64_t id) override { MEM.remove(id, true); }
};
frogram::MemoryApi& rina_resident_memory_i() { static ResidentMemory m; return m; }
std::vector<frogram::Cell> rina_resident_wait_i(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {
    auto eval = [&] { return MEM.match(s, v, i, after, 0, wall()); };
    std::vector<ramm::Cell> rows = eval();
    if (rows.empty() && wait_s > 0) {
        auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s);
        rows = MEM.wait(s, v, i, deadline, eval, [](const std::vector<ramm::Cell>& r) { return !r.empty(); });
    }
    std::vector<frogram::Cell> out;
    for (auto& c : rows) out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, Json::parse(c.bag), c.updated});
    return out;
}
// [RAW_GET_V1] one cell as stored: its bag text, or nothing
bool rina_resident_get_raw_i(const std::string& s, const std::string& v, const std::string& i, std::string& bag) {
    for (auto& c : MEM.match(s, v, i, -1, 0, wall())) if (c.instance == i) { bag = std::move(c.bag); return true; }
    return false;
}
// [SDU_BY_REFERENCE_V1] one cell as stored, with its id (a reference names a cell AND the version it meant)
bool rina_resident_get_raw_id_i(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id) {
    for (auto& c : MEM.match(s, v, i, -1, 0, wall())) if (c.instance == i) { bag = std::move(c.bag); id = c.id; return true; }
    return false;
}
// [RAW_HELD_READ_V1] the same held read, the cells as the memory holds them: id and the bag text, nothing parsed
std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw_i(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {
    auto eval = [&] { return MEM.match(s, v, i, after, 0, wall()); };
    std::vector<ramm::Cell> rows = eval();
    if (rows.empty() && wait_s > 0) {
        auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s);
        rows = MEM.wait(s, v, i, deadline, eval, [](const std::vector<ramm::Cell>& r) { return !r.empty(); });
    }
    std::vector<std::pair<uint64_t, std::string>> out; out.reserve(rows.size());
    for (auto& c : rows) out.emplace_back(c.id, std::move(c.bag));
    return out;
}
Reply lisp_op(const std::string& body) {
    Json in; try { in = Json::parse(body); } catch (const std::exception&) { return bad(400, "body is not a JSON object"); }
    const std::string op = in["operation"].type == Json::Str ? in["operation"].s : "";
    if (op.empty()) return bad(400, "missing operation");
    std::vector<std::pair<std::string, uint64_t>> written;       // [READ_YOUR_OWN_WRITE_V1] what the operation wrote
    std::vector<WrittenCell> cells;                              // [OWN_WRITES_HELD_V1] ... and the cells themselves
    std::string r;
    try {
        ::Engine e(std::unique_ptr<frogram::MemoryApi>(new ResidentMemory));
        frogram::log_writes_to(&written); tl_written_cells = &cells;
        try { r = e.call(op, in["args"]); } catch (...) { frogram::log_writes_to(nullptr); tl_written_cells = nullptr; throw; }
        frogram::log_writes_to(nullptr); tl_written_cells = nullptr;
    } catch (const std::exception& x) { return bad(400, std::string("lisp ") + op + ": " + x.what()); }
    std::string cj = "[";
    for (size_t k = 0; k < cells.size(); ++k)
        cj += (k ? "," : "") + std::string("{\"variable\":") + Json::quote(cells[k].variable) + ",\"instance\":" + Json::quote(cells[k].instance)
            + ",\"id\":" + std::to_string(cells[k].id) + ",\"bag\":" + cells[k].bag + "}";
    cj += "]";
    std::string w = "[";
    for (size_t k = 0; k < written.size(); ++k) w += (k ? "," : "") + std::string("[") + Json::quote(written[k].first) + "," + std::to_string(written[k].second) + "]";
    // the cells travel as JSON text, as the result does: the semantic wire's reply templates carry strings and flat lists
    return Reply{200, "{\"ok\":true,\"result_json\":" + Json::quote(r) + ",\"written\":" + w + "],\"cells_json\":" + Json::quote(cj) + "}"};
}
// A service whose name begins with '#' is this process's own (the region's secrets): the network API never serves it.
static bool private_service(const std::string& s) { return !s.empty() && s[0] == '#'; }
bool api(const std::string& method, const std::string& path, const std::string& body, bool may_wait, Reply& out) {
    if (path.compare(0, API_PATH.size(), API_PATH)) { out = bad(403, "not this service's API"); return true; }
    auto q = query(path); const std::string op = q["op"];
    if (op == "version") { out = Reply{200, std::string("{\"ok\":true,\"version\":\"") + RIBBIT_LISP_VERSION + "\"}"}; return true; }
    if (op == "lisp") {                                  // [VENDOR_API_V1]
        if (method != "POST") { out = bad(405, "lisp is POST"); return true; }
        if (!may_wait) return false;                     // every region operation runs on its own request thread
        out = lisp_op(body); return true;
    }
    if (op == "write") {
        if (method != "POST") { out = bad(405, "write is POST"); return true; }
        Json in; try { in = Json::parse(body); } catch (const std::exception&) { out = bad(400, "body is not a JSON object"); return true; }
        for (const char* k : {"service", "variable", "instance"}) if (in[k].type != Json::Str || in[k].s.empty()) { out = bad(400, std::string("missing coordinate: ") + k); return true; }
        if (in["bag"].type != Json::Obj) { out = bad(400, "bag must be a JSON object"); return true; }
        if (private_service(in["service"].s)) { out = bad(403, "service " + in["service"].s + " is not served"); return true; }
        out = Reply{200, "{\"ok\":true,\"id\":" + std::to_string(MEM.write(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]), wall())) + "}"}; return true;
    }
    if (op == "read") {
        if (method != "GET") { out = bad(405, "read is GET"); return true; }
        if (q["service"].empty()) { out = bad(400, "missing coordinate: service"); return true; }
        if (private_service(q["service"])) { out = bad(403, "service " + q["service"] + " is not served"); return true; }
        long long after = q.count("after") ? atoll(q["after"].c_str()) : -1; int fresh = q.count("fresh_s") ? atoi(q["fresh_s"].c_str()) : 0;
        double wait = q.count("wait_s") ? atof(q["wait_s"].c_str()) : 0; size_t min_rows = q.count("min_rows") ? size_t(atol(q["min_rows"].c_str())) : 0;
        if (wait > WAIT_MAX_S) wait = WAIT_MAX_S;
        if (after >= 0 && min_rows == 0) min_rows = 1;                        // "something newer than what I hold"
        const std::string sv = q["service"], vv = q["variable"], iv = q["instance"];
        auto eval = [&] { return MEM.match(sv, vv, iv, after, fresh, wall()); };
        std::vector<Cell> rows = eval();
        if (wait > 0 && min_rows > 0 && rows.size() < min_rows) {
            if (!may_wait) return false;
            auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait);
            rows = MEM.wait(sv, vv, iv, deadline, eval, [&](const std::vector<Cell>& r) { return r.size() >= min_rows; });
        }
        out = Reply{200, ok_rows(rows)}; return true;
    }
    if (op == "remove") {
        if (method != "DELETE") { out = bad(405, "remove is DELETE"); return true; }
        long long id = atoll(q["id"].c_str()); if (id <= 0) { out = bad(400, "missing id"); return true; }
        out = Reply{200, "{\"ok\":true,\"removed\":" + std::to_string(MEM.remove(uint64_t(id), false)) + "}"}; return true;
    }
    out = bad(400, "unknown op: " + op); return true;
}

// ---- the wire's request cache: memory only, bounded by entries AND by size
struct Cached { std::string req, body_hash, same_id; std::list<std::string>::iterator lru; };
std::mutex cache_m; std::map<std::string, Cached> cache; std::list<std::string> cache_lru;

// [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] and [HIGH_SPEED_DATA_SOCKET_V1]: dataplane.hpp -- the ceiling, the fair
// segment interleaver and the reassembler, shared with the client.
using Ceiling = fnwp::dataplane::Ceiling;
// [HOST_FANOUT_V1] the work ring: bounded MPMC (Dmitry Vyukov's), one cell per task, sequence numbers instead of locks
class WorkRing {
public:
    static constexpr size_t N = 1 << 16;
    WorkRing() { for (size_t i = 0; i < N; ++i) cell_[i].seq.store(i, std::memory_order_relaxed); }
    void push(std::function<void()> f) {                      // never fails: a full ring waits for a free cell
        for (;;) {
            size_t pos = tail_.load(std::memory_order_relaxed);
            Cell& c = cell_[pos & (N - 1)]; const size_t seq = c.seq.load(std::memory_order_acquire);
            const intptr_t d = intptr_t(seq) - intptr_t(pos);
            if (d == 0) { if (tail_.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed)) {
                    c.f = std::move(f); c.seq.store(pos + 1, std::memory_order_release);
                    work_.fetch_add(1, std::memory_order_release); work_.notify_one(); return; } }
            else if (d < 0) std::this_thread::yield();        // full
        }
    }
    bool pop(std::function<void()>& f) {
        for (;;) {
            size_t pos = head_.load(std::memory_order_relaxed);
            Cell& c = cell_[pos & (N - 1)]; const size_t seq = c.seq.load(std::memory_order_acquire);
            const intptr_t d = intptr_t(seq) - intptr_t(pos + 1);
            if (d == 0) { if (head_.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed)) {
                    f = std::move(c.f); c.seq.store(pos + N, std::memory_order_release); return true; } }
            else if (d < 0) return false;                     // empty
        }
    }
    void worker() {
        for (;;) {
            std::function<void()> f;
            if (pop(f)) { f(); continue; }
            const uint32_t seen = work_.load(std::memory_order_acquire);
            if (pop(f)) { f(); continue; }
            work_.wait(seen, std::memory_order_acquire);      // futex sleep until a push
        }
    }
private:
    struct Cell { std::atomic<size_t> seq; std::function<void()> f; };
    alignas(64) std::atomic<size_t> head_{0}; alignas(64) std::atomic<size_t> tail_{0};
    alignas(64) std::atomic<uint32_t> work_{0};
    Cell cell_[N];
};
WorkRing& work_ring() {                                      // the pool: started once, sized to the machine
    static WorkRing* r = [] { WorkRing* w = new WorkRing;
        unsigned n = std::thread::hardware_concurrency(); n = n ? n * 4 : 16;
        for (unsigned i = 0; i < n; ++i) std::thread([w] { w->worker(); }).detach();
        return w; }();
    return *r;
}
// [HOST_FANOUT_V1] a session's reply queue: intrusive MPSC, any worker pushes, the session's one writer pops
struct OutItem { std::atomic<OutItem*> next{nullptr}; uint32_t seq = 0; bool engine = false; std::string frame;
                 int action = 0;                         // [ENGINE_LOCKLESS_V1] 1: reset w.ik's response reference; 2: answer SAME-miss
                 fnwp::ServerEngine::Work w; Reply r; };
class OutQueue {
public:
    OutQueue() : head_(&stub_), tail_(&stub_) {}
    ~OutQueue() { OutItem* n; while ((n = pop())) delete n; }
    void push(OutItem* n) {
        n->next.store(nullptr, std::memory_order_relaxed);
        OutItem* prev = head_.exchange(n, std::memory_order_acq_rel);
        prev->next.store(n, std::memory_order_release);
        ready_.fetch_add(1, std::memory_order_release); ready_.notify_one();
    }
    OutItem* pop() {                                          // single consumer
        OutItem* t = tail_; OutItem* next = t->next.load(std::memory_order_acquire);
        if (t == &stub_) { if (!next) return nullptr; tail_ = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { tail_ = next; return t; }
        if (t != head_.load(std::memory_order_acquire)) return nullptr;   // a push is half done: next time
        push_stub(); next = t->next.load(std::memory_order_acquire);
        if (next) { tail_ = next; return t; }
        return nullptr;
    }
    std::atomic<uint32_t> ready_{0};
private:
    void push_stub() { stub_.next.store(nullptr, std::memory_order_relaxed); OutItem* prev = head_.exchange(&stub_, std::memory_order_acq_rel); prev->next.store(&stub_, std::memory_order_release); }
    OutItem stub_; std::atomic<OutItem*> head_; OutItem* tail_;
};
struct Session;
void run_in_order(const std::shared_ptr<Session>& s, std::function<void()> f);
static std::atomic<uint64_t> g_session_epoch{1};   // [SESSION_EPOCH_V1] unique per session for the life of the process
struct Session {
    const uint64_t epoch = g_session_epoch.fetch_add(1, std::memory_order_relaxed);   // [SESSION_EPOCH_V1] the session's id
    int req = -1, ret = -1, data = -1;
    ~Session() { if (ret >= 0) FR_CLOSE(ret); if (data >= 0) FR_CLOSE(data); }   /* [SESSION_FREES_ITS_SOCKETS_V1] shut down at session end, closed when the last holder lets go */
    std::mutex send_m, data_m; std::mutex ready_m; std::condition_variable ready_cv; bool ready = false;
    std::unique_ptr<fnwp::ServerEngine> eng; Ceiling ceil; std::atomic<uint64_t> data_frames{0}, data_bytes{0}, data_in{0};
    std::unique_ptr<fnwp::dataplane::Sender> dsend; fnwp::dataplane::Reassembler reasm; fnwp::dataplane::Landed landed;
    // [HOST_FANOUT_V1] a reply is handed to the session's writer; nothing that produces one sends it
    OutQueue outq; std::atomic<bool> closing{false}; std::thread writer;
    // [SESSION_ORDER_V1] this session's tasks, in arrival order; at most one drain of them runs at a time
    struct Task { std::atomic<Task*> next{nullptr}; std::function<void()> f; };
    Task tstub; std::atomic<Task*> thead{&tstub}; Task* ttail = &tstub; std::atomic<bool> scheduled{false};
    void tpush(Task* n) { n->next.store(nullptr, std::memory_order_relaxed); Task* prev = thead.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
    Task* tpop() {                                            // the one drain only
        Task* t = ttail; Task* next = t->next.load(std::memory_order_acquire);
        if (t == &tstub) { if (!next) return nullptr; ttail = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { ttail = next; return t; }
        if (t != thead.load(std::memory_order_acquire)) return nullptr;
        tstub.next.store(nullptr, std::memory_order_relaxed); Task* prev = thead.exchange(&tstub, std::memory_order_acq_rel); prev->next.store(&tstub, std::memory_order_release);
        next = t->next.load(std::memory_order_acquire); if (next) { ttail = next; return t; }
        return nullptr;
    }
    std::atomic<uint64_t> tpushed{0}; uint64_t tpopped = 0;   // tpopped: only the drain that holds `scheduled`

    void reply(uint32_t seq, const std::string& frame) {
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->frame = frame; outq.push(o); }
    void writer_run() {                                       // the session's ONE writer
        for (;;) {
            OutItem* o;
            while ((o = outq.pop())) {
                if (o->action == 1) eng->reset_response(o->w.ik);
                else if (o->action == 2) reply_locked(o->seq, eng->same_miss_frame(o->w.same_miss));
                else if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o;
            }
            if (closing.load(std::memory_order_acquire)) return;
            const uint32_t seen = outq.ready_.load(std::memory_order_acquire);
            if ((o = outq.pop())) {
                if (o->action == 1) eng->reset_response(o->w.ik);
                else if (o->action == 2) reply_locked(o->seq, eng->same_miss_frame(o->w.same_miss));
                else if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o; continue; }
            outq.ready_.wait(seen, std::memory_order_acquire);
        }
    }
    void reply_locked(uint32_t seq, const std::string& frame) { std::string f = be32(seq) + frame; send_frame(ret, f); ceil.note(f.size() + 4); }
    // [ENGINES_IN_THE_SESSION_V1] Encode and send under one lock: the response reference moves in the order the reply
    // goes on the return socket, which is the order the client applies it. [HIGH_SPEED_DATA_SOCKET_V1] [DATA_MARKER_V1]
    // A reply that cannot be differenced (RESP_RAW) and would own the return socket longer than the ceiling puts its
    // MARKER on the return socket in that same order and its body on the data socket, in segments, interleaved fairly
    // with every other transfer in flight -- so every other reply keeps moving.
    void finish_and_send(uint32_t seq, fnwp::ServerEngine::Work&& w, const Reply& r) {   // [WORK_MOVED_NOT_COPIED_V1] its last use
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->engine = true; o->w = std::move(w); o->r = r; outq.push(o); }
    void finish_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {   // [HOST_FANOUT_V1] queued
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->engine = true; o->w = w; o->r = r; outq.push(o); }
    void encode_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {    // on the writer only
        std::string fr;
        try { fr = eng->finish(w, fnwp::Answer{r.status, r.body}); }
        catch (const std::exception& e) { if (std::getenv("FROGRAM_TRACE")) std::cerr << "[ram-server] seq=" << seq << " ENCODE FAILED " << e.what() << "\n"; reply_locked(seq, semwire::wrap_error(500, std::string("encode: ") + e.what())); return; }
        if (std::getenv("FROGRAM_TRACE")) std::cerr << "[ram-server] seq=" << seq << " reply op=" << int(uint8_t(fr[4])) << " status=" << r.status << " " << fr.size() << "B\n";
        if (dsend && uint8_t(fr[4]) == RESP_RAW && fr.size() > ceil.whole_max()) {
            std::string body; std::string marker = fnwp::ServerEngine::reply_marker(fr, body);
            reply_locked(seq, marker); ++data_frames; data_bytes += body.size(); dsend->submit(seq, std::move(body)); return; }
        reply_locked(seq, fr);
    }
};
std::mutex pend_m; std::map<std::string, std::shared_ptr<Session>> pending;
std::map<std::string, std::weak_ptr<Session>> live;      // sessions whose return socket is up, awaiting their data socket
std::string engine_secret() { std::random_device rd; std::string k(32, '\0'); for (auto& c : k) c = char(rd()); return k; }

std::string wire_reply(const std::string& h, const std::string& http_req, const Cached* old, const Reply& r) {
    std::string body_hash = hash16(std::to_string(r.status) + r.body);
    if (old && old->body_hash == body_hash) return std::string(MAGIC, 4) + char(RESP_SAME) + old->same_id;
    std::string same_id = hash16(h + body_hash);
    { std::lock_guard<std::mutex> g(cache_m); auto it = cache.find(h); if (it != cache.end()) { cache_lru.erase(it->second.lru); cache.erase(it); }
      if (r.status < 400 && http_req.size() <= CACHE_MAX_REQ) { cache_lru.push_back(h); cache[h] = Cached{http_req, body_hash, same_id, std::prev(cache_lru.end())};
          while (cache.size() > CACHE_ENTRIES) { cache.erase(cache_lru.front()); cache_lru.pop_front(); } } }
    std::string payload = be16(uint16_t(r.status)) + be16(2) + "{}" + r.body;
    return std::string(MAGIC, 4) + char(RESP_RAW) + same_id + be32(uint32_t(payload.size())) + payload;
}
// [ENGINES_IN_THE_SESSION_V1] Decode in arrival order on the socket's thread (the request reference moves in wire
// order); execute (a held read parks on its own thread); encode and send in one step, so the response reference moves
// in the order the reply is put on the return socket. [DATA_MARKER_V1] A request that arrived as a marker executes on
// its own thread once its body has landed on the data socket; nothing else on the session waits for it.
// [SESSION_ORDER_V1] push the task; if the session was idle, schedule one drain of it on the pool
void run_in_order(const std::shared_ptr<Session>& s, std::function<void()> f) {
    Session::Task* t = new Session::Task; t->f = std::move(f); s->tpush(t);
    s->tpushed.fetch_add(1, std::memory_order_seq_cst);       // counted BEFORE trying to schedule (see the drain's check)
    bool idle = false;
    if (s->scheduled.compare_exchange_strong(idle, true, std::memory_order_seq_cst)) {
        work_ring().push([s] {
            for (;;) {
                Session::Task* n = nullptr;
                while (s->tpopped != s->tpushed.load(std::memory_order_seq_cst)) {   // every counted task is (being) linked
                    if ((n = s->tpop())) { n->f(); delete n; ++s->tpopped; }
                }
                s->scheduled.store(false, std::memory_order_seq_cst);
                // a task counted after the last check: its pusher's schedule attempt may have failed while we held the
                // flag -- take the flag back and drain it (if someone else took it, they will)
                if (s->tpushed.load(std::memory_order_seq_cst) == s->tpopped) return;
                bool idle2 = false;
                if (!s->scheduled.compare_exchange_strong(idle2, true, std::memory_order_seq_cst)) return;
            }
        });
    }
}
// [HOST_COALESCE_V1] requests in flight, by request hash
struct CoWaiter { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; CoWaiter* next = nullptr; };
struct CoEntry { std::string key; std::atomic<CoWaiter*> waiters{nullptr}; };
static CoWaiter* const CO_SEALED = reinterpret_cast<CoWaiter*>(uintptr_t(1));
struct CoTable {
    static constexpr size_t N = 4096;
    std::atomic<const CoEntry*> slot[N];
    ribbit::Ebr ebr;
    std::atomic<uint64_t> led{0}, joined_n{0}, alone{0};      // executions led, requests answered by another's execution, ran alone
    CoTable() { for (auto& x : slot) x.store(nullptr, std::memory_order_relaxed); }
    static size_t home(const std::string& k) { uint64_t h = 1469598103934665603ull; for (unsigned char c : k) { h ^= c; h *= 1099511628211ull; } return size_t(h); }
    // the first to claim a key leads (true); a later identical request joins as a waiter (false, joined); if the
    // leader has already answered (sealed), or the table is full near home, the request runs on its own (false, !joined)
    bool claim(const std::string& key, CoWaiter* me, const CoEntry*& mine, bool& joined) {
        auto g = ebr.guard(); joined = false; const size_t h0 = home(key);
        for (size_t i = 0; i < 32; ++i) {
            std::atomic<const CoEntry*>& sl = slot[(h0 + i) & (N - 1)];
            const CoEntry* e = g.protect(sl);
            if (!e) {
                CoEntry* n = new CoEntry; n->key = key; const CoEntry* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { mine = n; return true; }
                delete n; e = expect; if (!e) continue;
            }
            if (e->key != key) continue;
            CoEntry* ce = const_cast<CoEntry*>(e); CoWaiter* head = ce->waiters.load(std::memory_order_acquire);
            for (;;) {
                if (head == CO_SEALED) return false;              // answered already: run it ourselves
                me->next = head;
                if (ce->waiters.compare_exchange_weak(head, me, std::memory_order_acq_rel)) { joined = true; return false; }
            }
        }
        return false;                                               // no slot near home: run it ourselves
    }
    // the leader, with its answer: seal, unpublish, hand every waiter the answer
    void answer(const CoEntry* mine, const Reply& r) {
        CoEntry* ce = const_cast<CoEntry*>(mine);
        CoWaiter* list = ce->waiters.exchange(CO_SEALED, std::memory_order_acq_rel);
        const size_t h0 = home(ce->key);
        for (size_t i = 0; i < 32; ++i) { std::atomic<const CoEntry*>& sl = slot[(h0 + i) & (N - 1)]; const CoEntry* expect = mine;
            if (sl.compare_exchange_strong(expect, nullptr, std::memory_order_acq_rel)) break; }
        ebr.retire(mine);
        while (list) { CoWaiter* nx = list->next; list->s->finish_and_send(list->seq, list->w, r); delete list; list = nx; }
    }
};
CoTable& co_table() { static CoTable* t = [] { CoTable* c = new CoTable;
    std::atexit([] { CoTable& x = co_table(); if (getenv("RINA_DIAG")) fprintf(stderr, "[HOST_COALESCE_V1] executions led %llu, requests answered by another's execution %llu, ran alone %llu\n",
        (unsigned long long)x.led.load(), (unsigned long long)x.joined_n.load(), (unsigned long long)x.alone.load()); });
    return c; }(); return *t; }
// [DEFERRED_ANSWER_V1] a request's continuation, handed to the region when it cannot answer yet
struct RinaDeferred { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; const CoEntry* mine; };
struct RinaCurrent { const std::shared_ptr<Session>* s; uint32_t seq; const fnwp::ServerEngine::Work* w; const CoEntry* mine; };   // [WORK_MOVED_NOT_COPIED_V1]
static thread_local RinaCurrent* tl_current = nullptr;    // the request this thread is executing (set around api())
static thread_local bool tl_deferred = false;             // the region took its continuation
}  // namespace
void* rina_defer() { if (!tl_current) return nullptr; tl_deferred = true;   // the Work is copied only here, when deferred
    return new RinaDeferred{*tl_current->s, tl_current->seq, *tl_current->w, tl_current->mine}; }
void rina_answer(void* h, const std::string& body) {
    RinaDeferred* d = static_cast<RinaDeferred*>(h); if (!d) return;
    Reply r{200, body};
    if (d->mine) co_table().answer(d->mine, r);
    d->s->finish_and_send(d->seq, d->w, r);
    delete d;
}
namespace {
void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {
    fnwp::ServerEngine::Work w;
    try { w = s->eng->begin(f); }
    catch (const std::exception& e) { s->reply(seq, semwire::wrap_error(400, std::string("decode: ") + e.what())); return; }
    if (!w.frame.empty()) { s->reply(seq, w.frame); return; }
    if (!w.same_miss.empty()) { OutItem* o = new OutItem; o->seq = seq; o->action = 2; o->w = w; s->outq.push(o); return; }   // [ENGINE_LOCKLESS_V1]
    if (w.fresh) { OutItem* o = new OutItem; o->action = 1; o->w = w; s->outq.push(o); }                                     // [ENGINE_LOCKLESS_V1]
    if (std::getenv("FROGRAM_TRACE")) std::cerr << "[ram-server] seq=" << seq << " op=" << int(uint8_t(f[4])) << " " << w.method << " " << w.path.substr(0, 90) << " body=" << w.body.size() << "B raw=" << (w.raw ? "y" : "n") << (w.body_pending ? " pending" : "") << " " << w.body.substr(0, 300) << "\n";
    auto run = [s, seq](fnwp::ServerEngine::Work w) {
        tl_session_id = s->epoch;
        // [HOST_COALESCE_V1] one execution per request in flight: join an identical one, or lead
        const CoEntry* mine = nullptr; bool joined = false;
        if (w.req_hash.size() == 16) {
            const std::string key = w.req_hash;
            CoWaiter* me = new CoWaiter{s, seq, std::move(w)};   // [WORK_MOVED_NOT_COPIED_V1] the waiter takes it...
            const bool lead = co_table().claim(key, me, mine, joined);
            if (joined) { co_table().joined_n.fetch_add(1, std::memory_order_relaxed); return; }   // answered with the leader's answer, by our writer
            w = std::move(me->w); delete me; if (!lead) mine = nullptr;   // ...and gives it back when this request runs
            (lead ? co_table().led : co_table().alone).fetch_add(1, std::memory_order_relaxed);
        }
        RinaCurrent here{&s, seq, &w, mine}; tl_current = &here; tl_deferred = false;   // [DEFERRED_ANSWER_V1] [WORK_MOVED_NOT_COPIED_V1]
        Reply r; const bool done = api(w.method, w.path, w.body, false, r); tl_current = nullptr;
        if (tl_deferred) { tl_deferred = false; return; }   // the region answers it later (rina_answer)
        if (done) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, std::move(w), r); return; }
        std::thread([=] { tl_session_id = s->epoch; Reply rr; api(w.method, w.path, w.body, true, rr);
                          if (mine) co_table().answer(mine, rr); s->finish_and_send(seq, w, rr); }).detach();   // a parked read
    };
    if (w.body_pending) {
        std::thread([s, seq, w, run] {
            fnwp::ServerEngine::Work ww = w;
            try { ww.body = s->landed.take(seq); }
            catch (const std::exception& e) { s->reply(seq, semwire::wrap_error(400, std::string("data socket: ") + e.what())); return; }
            if (ww.body.size() != w.body_bytes) { s->reply(seq, semwire::wrap_error(400, "data socket: the landed body is " + std::to_string(ww.body.size()) + " B, the marker said " + std::to_string(w.body_bytes))); return; }
            ww.body_pending = false; run(ww);
        }).detach();
        return;
    }
    run_in_order(s, [run, w = std::move(w)]() mutable { run(std::move(w)); });   // [SESSION_ORDER_V1] [WORK_MOVED_NOT_COPIED_V1]
}
void handle_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {
    if (s->eng) { engine_frame(s, seq, f); return; }
    uint8_t op = uint8_t(f[4]); if (f.size() < 21) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "short frame"); return; }
    std::string h = f.substr(5, 16), http_req; Cached old; bool have_old = false;
    if (op == REQ_RAW) { if (f.size() < 25 || f.size() != 25 + rd32(f, 21)) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "short REQ_RAW"); return; } http_req = f.substr(25); }
    else if (op == REQ_REPEAT) { std::lock_guard<std::mutex> g(cache_m); auto it = cache.find(h);
        if (it == cache.end()) { s->reply(seq, std::string(MAGIC, 4) + char(REQ_MISS) + h); return; }
        old = it->second; have_old = true; http_req = old.req; cache_lru.splice(cache_lru.end(), cache_lru, it->second.lru); }
    else { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "op not spoken here"); return; }
    Json q; try { q = Json::parse(http_req); } catch (const std::exception&) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "bad request"); return; }
    std::string method = q["method"].s, path = q["path"].s, body = q["body"].s;
    run_in_order(s, [=] {                                    // [SESSION_ORDER_V1] in this session's order; sessions in parallel
    Reply r;
    tl_session_id = s->epoch;
    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }
    std::thread([=] { tl_session_id = s->epoch; Reply rr; Cached o = old; api(method, path, body, true, rr); s->reply(seq, wire_reply(h, http_req, have_old ? &o : nullptr, rr)); }).detach();   // a parked read
    });
}
// [HTTP_FRONT_V1] one plain-HTTP request on this connection (ram.php's contract), through the same api() as FNW1
void serve_http(int fd) {
    std::string req; char b[65536]; size_t he;
    while ((he = req.find("\r\n\r\n")) == std::string::npos) {
        const ssize_t n = ::recv(fd, b, sizeof b, 0); if (n <= 0 || req.size() > (1u << 20)) { FR_CLOSE(fd); return; }
        req.append(b, size_t(n)); }
    const std::string head = req.substr(0, he); std::string body = req.substr(he + 4);
    const size_t sp1 = head.find(' '), sp2 = sp1 == std::string::npos ? sp1 : head.find(' ', sp1 + 1);
    if (sp2 == std::string::npos) { FR_CLOSE(fd); return; }
    const std::string method = head.substr(0, sp1), path = head.substr(sp1 + 1, sp2 - sp1 - 1);
    std::string low = head; for (char& c : low) c = char(tolower(static_cast<unsigned char>(c)));
    size_t clen = 0; if (const size_t k = low.find("\r\ncontent-length:"); k != std::string::npos) clen = size_t(strtoul(low.c_str() + k + 17, nullptr, 10));
    while (body.size() < clen) { const ssize_t n = ::recv(fd, b, sizeof b, 0); if (n <= 0) { FR_CLOSE(fd); return; } body.append(b, size_t(n)); }
    Reply rr{500, "{\"ok\":false,\"error\":\"internal\"}"}; api(method, path, body, true, rr);
    static const std::string rows = "{\"ok\":true,\"rows\":";                      // ram.php's read reply names them cells
    if (rr.body.compare(0, rows.size(), rows) == 0) rr.body = "{\"ok\":true,\"cells\":" + rr.body.substr(rows.size());
    send_all(fd, "HTTP/1.1 " + std::to_string(rr.status) + (rr.status == 200 ? " OK" : " Error") + "\r\nContent-Type: application/json\r\nContent-Length: " +
                 std::to_string(rr.body.size()) + "\r\nConnection: close\r\n\r\n" + rr.body);
    FR_CLOSE(fd);
}
void serve(int fd, std::string peer) {
    { int ka = 1, idle = 2, intvl = 1, cnt = 3, uto = 5000; setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const fr_optval_t*>(&ka), sizeof ka);
#ifdef TCP_KEEPIDLE
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, reinterpret_cast<const fr_optval_t*>(&idle), sizeof idle);
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, reinterpret_cast<const fr_optval_t*>(&intvl), sizeof intvl);
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, reinterpret_cast<const fr_optval_t*>(&cnt), sizeof cnt);
#ifdef TCP_USER_TIMEOUT
      setsockopt(fd, IPPROTO_TCP, TCP_USER_TIMEOUT, reinterpret_cast<const fr_optval_t*>(&uto), sizeof uto);
#endif
#endif
    }   // [KEEPALIVE_5S_V1] (was [KEEPALIVE_20S_V1])
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one); setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    { char pk[4]; if (::recv(fd, pk, 4, MSG_PEEK | MSG_WAITALL) == 4 && (!memcmp(pk, "GET ", 4) || !memcmp(pk, "POST", 4))) { serve_http(fd); return; } }   // [HTTP_FRONT_V1]
    std::string f; if (!recv_frame(fd, f) || uint8_t(f[4]) != OP_HELLO || f.size() < 6) { FR_CLOSE(fd); return; }
    std::string token = f.substr(6, uint8_t(f[5]));
    if (!token.compare(0, 7, "RETURN:")) { token = token.substr(7); std::shared_ptr<Session> s;
        for (int i = 0; i < 200 && !s; ++i) { { std::lock_guard<std::mutex> g(pend_m); auto it = pending.find(token); if (it != pending.end()) { s = it->second; pending.erase(it); } } if (!s) std::this_thread::sleep_for(std::chrono::milliseconds(50)); }
        if (!s) { FR_CLOSE(fd); return; }
        s->ret = fd; { std::lock_guard<std::mutex> g(pend_m); live[token] = s; }
        send_frame(fd, std::string(MAGIC, 4) + char(OP_HELLO) + char(3) + "ram");
        { std::lock_guard<std::mutex> g(s->ready_m); s->ready = true; } s->ready_cv.notify_all(); return; }
    if (!token.compare(0, 5, "DATA:")) { token = token.substr(5); std::shared_ptr<Session> s;
        for (int i = 0; i < 200 && !s; ++i) { { std::lock_guard<std::mutex> g(pend_m); auto it = live.find(token); if (it != live.end()) { s = it->second.lock(); live.erase(it); } }
                                             if (!s) std::this_thread::sleep_for(std::chrono::milliseconds(50)); }
        if (!s) { FR_CLOSE(fd); return; }
        { int sb = 0; socklen_t sl = sizeof sb; getsockopt(s->ret, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<fr_optval_t*>(&sb), &sl);
          s->ceil.set_hard(size_t(sb)); }
        s->eng.reset(new fnwp::ServerEngine(engine_secret(), API_PATH));   // before the data socket is answered: the
        s->data = fd;                                                    // client sends nothing until it is
        { int buf = 32 * 1024; setsockopt(fd, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&buf), sizeof buf); }   // the buffer is the latency
        { std::weak_ptr<Session> ws = s;
          s->dsend.reset(new fnwp::dataplane::Sender([ws](const std::string& rec) { if (auto sp = ws.lock()) { std::lock_guard<std::mutex> g(sp->data_m); send_all(sp->data, rec); } })); }
        s->dsend->start();
        send_frame(fd, std::string(MAGIC, 4) + char(OP_HELLO) + char(3) + "ram");
        logline("data socket up " + peer + " " + token);
        // [HIGH_SPEED_DATA_SOCKET_V1] [DATA_MARKER_V1] the bodies of requests whose markers came on the request socket
        // arrive here in segments; a body lands the moment its last segment does, and the frame parked on its marker
        // takes it up
        std::string l, rec;
        while (recv_n(fd, 4, l)) { uint32_t n = rd32(l, 0); if (n < 12 || n > MAX_FRAME || !recv_n(fd, n, rec)) break;
            s->data_in += rec.size() + 4;
            std::optional<std::pair<uint32_t, std::string>> done;
            try { done = s->reasm.feed(rec); } catch (const std::exception& e) { logline(std::string("data socket protocol error ") + e.what()); break; }
            if (done) s->landed.put(done->first, std::move(done->second)); }
        s->landed.close();
        return; }
    auto s = std::make_shared<Session>(); s->req = fd; { std::lock_guard<std::mutex> g(pend_m); pending[token] = s; }
    { std::unique_lock<std::mutex> l(s->ready_m); if (!s->ready_cv.wait_for(l, std::chrono::seconds(10), [&] { return s->ready; })) { std::lock_guard<std::mutex> g(pend_m); pending.erase(token); FR_CLOSE(fd); return; } }
    logline("session up   " + peer + " " + token); uint32_t seq = 0;
    rina_session_peer(s->epoch, peer);   // [SESSION_PEER_V1]
    { Session* sp = s.get(); s->writer = std::thread([sp] { sp->writer_run(); }); }   // [HOST_FANOUT_V1]
    while (recv_frame(fd, f)) handle_frame(s, seq++, f);
    logline("session down " + peer + " " + token + " after " + std::to_string(seq) + " requests" +
            (s->eng ? ", " + std::to_string(s->data_frames) + " replies (" + std::to_string(s->data_bytes) + " B) on the data socket" : ""));
    { std::lock_guard<std::mutex> g(pend_m); live.erase(token); }
    s->closing.store(true, std::memory_order_release); s->outq.ready_.fetch_add(1); s->outq.ready_.notify_one();
    if (s->writer.joinable()) s->writer.join();                 // [HOST_FANOUT_V1] replies after this have no socket to go to
    rina_session_down(s->epoch);   // [SESSION_IS_LIVENESS_V1] before s can be freed and its address reused
    s->landed.close();
    if (s->dsend) s->dsend->stop();
    ::shutdown(s->ret, FR_SHUT_RDWR); if (s->data >= 0) ::shutdown(s->data, FR_SHUT_RDWR); FR_CLOSE(fd);
}
}  // namespace
frogram::MemoryApi& rina_resident_memory() { return rina_resident_memory_i(); }
std::vector<frogram::Cell> rina_resident_wait(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) { return rina_resident_wait_i(s, v, i, after, wait_s); }
bool rina_resident_get_raw(const std::string& s, const std::string& v, const std::string& i, std::string& bag) { return rina_resident_get_raw_i(s, v, i, bag); }
bool rina_resident_get_raw_id(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id) { return rina_resident_get_raw_id_i(s, v, i, bag, id); }
std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) { return rina_resident_wait_raw_i(s, v, i, after, wait_s); }

// Bring the server up on `listen_on` and serve until the process ends. Returns 0 once it is listening (the accept
// loop runs on its own thread), or an errno. This is what lets one executable be its own RAM target (tools/frogbench).
int ram_server_start(const std::string& listen_on, bool quiet) {
    g_quiet = quiet; size_t c = listen_on.rfind(':'); if (c == std::string::npos) return EINVAL;
#ifdef _WIN32
    { static bool once = [] { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); return true; }(); (void)once; }
#else
    signal(SIGPIPE, SIG_IGN);
#endif
    int srv = int(::socket(AF_INET, SOCK_STREAM, 0)), one = 1, rcv = 32 * 1024; setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    setsockopt(srv, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const fr_optval_t*>(&rcv), sizeof rcv);      // the buffer is the latency; accepted sockets inherit it
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(atoi(listen_on.c_str() + c + 1))); inet_pton(AF_INET, listen_on.substr(0, c).c_str(), &a.sin_addr);
    if (::bind(srv, reinterpret_cast<sockaddr*>(&a), sizeof a) || ::listen(srv, 1024)) { int e = errno; FR_CLOSE(srv); return e ? e : EADDRINUSE; }
    logline(std::string("ram-server ") + RIBBIT_LISP_VERSION + ": FNW1 and the memory, in one process, on " + listen_on);
    { extern void rina_host_listening(const std::string&); rina_host_listening(listen_on); }   // [HOST_ANNOUNCES_ITSELF_V1]
    std::thread([srv] { for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl); if (fd < 0) continue;
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip); std::thread(serve, fd, std::string(ip)).detach(); } }).detach();
    return 0;
}

#ifndef FROGBENCH_EMBED
// [COVERAGE_ON_TERM_V1] SIGTERM / SIGINT end the program with exit(), from an ordinary thread (sigwait), so a
// coverage build (--coverage) writes its data and the process still stops at once. The signals are blocked in main
// before any other thread starts, so every thread inherits the mask and only the waiting thread receives them.
static void end_on_term_signals() {
    sigset_t set; sigemptyset(&set); sigaddset(&set, SIGTERM); sigaddset(&set, SIGINT);
    pthread_sigmask(SIG_BLOCK, &set, nullptr);
    std::thread([set] { int sig = 0; sigwait(&set, &sig); std::exit(0); }).detach();
}
int main(int argc, char** argv) {
    end_on_term_signals();
    std::string listen_on = "0.0.0.0:8788";
    for (int i = 1; i < argc; ++i) if (!strcmp(argv[i], "--version")) { std::cout << "ram-server " << RIBBIT_LISP_VERSION << "\n"; return 0; }
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--listen")) listen_on = argv[i + 1];
        else if (!strcmp(argv[i], "--api")) API_PATH = argv[i + 1];
        else if (!strcmp(argv[i], "--version")) { std::cout << "ram-server " << RIBBIT_LISP_VERSION << "\n"; return 0; }
        else { std::cerr << "unknown option " << argv[i] << " (options: --listen HOST:PORT, --api /Vendor.Product.ram_interface.php)\n"; return 2; }
    }
    LISTEN_PORT = atoi(listen_on.c_str() + listen_on.rfind(':') + 1);
    // the host's own Engines connect to the address it listens on (loopback when it listens on every address)
    LISTEN_HOST = listen_on.substr(0, listen_on.rfind(':'));
    if (LISTEN_HOST.empty() || LISTEN_HOST == "0.0.0.0") LISTEN_HOST = "127.0.0.1";
    int e = ram_server_start(listen_on, false);
    if (e) { std::cerr << "cannot listen on " << listen_on << ": " << std::strerror(e) << "\n"; return 1; }
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
}
#endif
