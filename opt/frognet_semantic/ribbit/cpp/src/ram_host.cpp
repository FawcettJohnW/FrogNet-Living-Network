#include <cstdlib>
#include <thread>
#include <pthread.h>
#include <unistd.h>
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ram_host.cpp -- the Ribbit RAM host (ram_host.hpp): one vendor's FrogNet RAM, the whole server side, in C++.
//
//     <vendor>-ram [--listen HOST:PORT]
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
#include "ram_host.hpp"
#include "ebr.hpp"
// [SESSION_IS_LIVENESS_V1] the session a request arrived on (a number unique for the life of the process,
// [SESSION_EPOCH_V1]); the vendor hears when a session starts (its peer address) and when it ends (RamHost::session_up,
// session_down)
static thread_local uint64_t tl_session_id = 0;

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
#include <array>
#include <memory>
#include <unordered_map>
#include <sstream>
#include <tuple>
#include <atomic>
#include <random>
#ifndef _WIN32
#include <linux/futex.h>
#include <sys/syscall.h>
#endif
#include <cerrno>
#include <chrono>
#include "fnwp_engine.hpp"
#include "rowcodec.hpp"
#include "cellwire.hpp"
#include "dataplane.hpp"   // [ENGINES_IN_THE_SESSION_V1] the full FNWP contract for clients that open the data socket

using frogram::Json;
namespace {
const char MAGIC[] = "FNW1";
enum : uint8_t { REQ_REPEAT = 0x02, REQ_RAW = 0x03, RESP_SAME = 0x13, RESP_RAW = 0x14, REQ_MISS = 0x21, OP_ERROR = 0x30, OP_HELLO = 0x50 };
const size_t MAX_FRAME = 1 << 20, CACHE_ENTRIES = 4096, CACHE_MAX_REQ = 8192;
const double WAIT_MAX_S = 30.0;

double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
bool g_quiet = false;
std::string LOG_TAG = "ram-host";                     // "<vendor>-ram", set by RamHost::start
void logline(const std::string& m) { if (g_quiet) return; std::time_t t = std::time(nullptr); char b[32]; std::strftime(b, sizeof b, "%F %T", std::localtime(&t)); std::cout << "[" << LOG_TAG << "] " << b << " " << m << std::endl; }

// ---- wire helpers
bool send_all(int fd, const std::string& b) { size_t o = 0; while (o < b.size()) { ssize_t n = ::send(fd, b.data() + o, b.size() - o, MSG_NOSIGNAL); if (n <= 0) return false; o += size_t(n); } return true; }
bool recv_n(int fd, size_t n, std::string& d) { d.assign(n, '\0'); size_t o = 0; while (o < n) { ssize_t r = ::recv(fd, &d[o], n - o, 0); if (r <= 0) return false; o += size_t(r); } return true; }
std::string be32(uint32_t v) { uint32_t n = htonl(v); return std::string(reinterpret_cast<char*>(&n), 4); }
std::string be16(uint16_t v) { uint16_t n = htons(v); return std::string(reinterpret_cast<char*>(&n), 2); }
uint32_t rd32(const std::string& s, size_t o) { uint32_t n; std::memcpy(&n, s.data() + o, 4); return ntohl(n); }
bool recv_frame(int fd, std::string& f) { std::string l; if (!recv_n(fd, 4, l)) return false; uint32_t n = rd32(l, 0); if (n < 5 || n > MAX_FRAME) return false; return recv_n(fd, n, f) && !f.compare(0, 4, MAGIC); }
// [RECEIVES_GATHERED_V1] the request socket read in large pieces, frames cut out of them: measured, two recv() per frame
// were an eighth of the memory host's CPU at 20k writes/s
struct FrameReader {
    int fd; std::string b; size_t p = 0;
    explicit FrameReader(int f) : fd(f) { b.reserve(256 * 1024); }
    bool fill(size_t need) {                            // at least `need` bytes after p
        while (b.size() - p < need) {
            if (p > 0 && p == b.size()) { b.clear(); p = 0; } else if (p > 128 * 1024) { b.erase(0, p); p = 0; }
            const size_t at = b.size(); const size_t want = std::max<size_t>(64 * 1024, need - (b.size() - p));
            b.resize(at + want); ssize_t r = ::recv(fd, &b[at], want, 0);
            if (r <= 0) { b.resize(at); return false; }
            b.resize(at + size_t(r));
        }
        return true;
    }
    bool frame(std::string& f) {
        if (!fill(4)) return false;
        const uint32_t n = rd32(b, p); if (n < 5 || n > MAX_FRAME) return false;
        if (!fill(4 + size_t(n))) return false;
        f.assign(b, p + 4, n); p += 4 + size_t(n);
        return !f.compare(0, 4, MAGIC);
    }
};
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
             ",\"instance\":" + Json::quote(c.instance) + ",\"bag\":" + c.text() + ",\"updated_epoch\":" + u + "}"; }
    return o + "]}"; }
}  // (anonymous) -- the platform's public functions are outside it
#define RIBBIT_PLATFORM_VERSION "ribbit-platform 0.67"   // 0.67: [NUMBER_DELTA_V1] [ROW_REFERENCE_OF_K_V1] [ROW_RECORD_COMPACT_V1] [REPEAT_REPLAYS_VALUES_V1]; 0.66: [ROWS_ON_THE_SEMANTIC_WIRE_V1] [MOVING_WINDOW_REFERENCE_V1] [STRING_SPLICE_V1]; 0.65: [EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1] [JSON_SURROGATE_PAIRS_V1]; 0.64: [NO_WRITE_ACK_V1] [FOLLOW_STREAM_V1] -- every node at 0.67 together
namespace ribbit {
Reply error_reply(int status, const std::string& msg) { return Reply{status, "{\"ok\":false,\"error\":" + Json::quote(msg) + "}"}; }
const char* platform_version() { return RIBBIT_PLATFORM_VERSION; }
}
namespace {
using ribbit::Reply;
Reply bad(int code, const std::string& msg) { return ribbit::error_reply(code, msg); }
// The host this process serves: set once, by RamHost::start, before the first connection is accepted.
static ribbit::RamHost* HOST = nullptr;
static std::string REGION_SERVICE;

// Runs one API request. Returns false if it must WAIT (a blocking read not yet satisfied);
// the caller then parks it on its own thread by calling again with may_wait = true.
// [RAM_INTERFACE_IS_THE_VENDORS_V1] The endpoint is the application vendor's to name (--api); /ram.php is what this
// server has always answered, and stays what it answers when --api is not given.
std::string API_PATH;                                // the vendor's endpoint: RamHost::api_path()
int LISTEN_PORT = 0;
std::string LISTEN_HOST = "127.0.0.1";
// [VENDOR_API_V1] A vendor's region operations (RamHost::handles / operation). History: the first region, LISP's, ran its
// operations on Engines -- the same lisp_engine.hpp the
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
        if (tl_written_cells && s == REGION_SERVICE) tl_written_cells->push_back(WrittenCell{v, i, bag, id});
        return id;
    }
    void write_nowait(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { write(s, v, i, bag); }
    std::vector<frogram::Cell> read(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s, int fresh_s) override {
        if (wait_s > 0) throw std::logic_error("a resident region operation holds nothing: no blocking read (" + s + "/" + v + ")");
        std::vector<frogram::Cell> out;
        for (auto& c : MEM.match(s, v, i, after, fresh_s, wall()))
            out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, Json::parse(c.text()), c.updated});
        return out;
    }
    void remove(uint64_t id) override { MEM.remove(id, true); }
};
}  // (anonymous)
namespace ribbit {
ramm::Memory& RamHost::memory() { return MEM; }
Reply RamHost::operation(const std::string& op, const std::string& body) {
    (void)body; return error_reply(404, "this host (" + vendor_id() + ") has no operation " + op);
}
Reply RamHost::resident_operation(const std::string& what,
                                  const std::function<std::string(std::unique_ptr<frogram::MemoryApi>)>& f) {
    std::vector<std::pair<std::string, uint64_t>> written;       // [READ_YOUR_OWN_WRITE_V1] what the operation wrote
    std::vector<WrittenCell> cells;                              // [OWN_WRITES_HELD_V1] ... and the region's cells whole
    std::string r;
    try {
        frogram::log_writes_to(&written); tl_written_cells = &cells;
        try { r = f(std::unique_ptr<frogram::MemoryApi>(new ResidentMemory)); }
        catch (...) { frogram::log_writes_to(nullptr); tl_written_cells = nullptr; throw; }
        frogram::log_writes_to(nullptr); tl_written_cells = nullptr;
    } catch (const std::exception& x) { return error_reply(400, what + ": " + x.what()); }
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
}  // namespace ribbit
namespace {
// A private service is this process's own (the region's secrets): the network API never serves it. The vendor says
// which (RamHost::private_service).
static bool private_service(const std::string& s) { return HOST->private_service(s); }
// [WRITE_BODY_SKIMMED_V1] JSON checked without building it: skip_value moves past one well-formed value; skim_write
// reads {"service":s, "variable":s, "instance":s, "bag":{...}} in any order, the strings decoded, the bag's bounds kept.
// Anything it does not take (another key, a coordinate that is not a string, a bag that is not an object) is left to
// the ordinary parse, which says why.
static bool skip_ws(const std::string& t, size_t& i) { while (i < t.size() && (t[i] == ' ' || t[i] == '\t' || t[i] == '\n' || t[i] == '\r')) ++i; return i < t.size(); }
static bool skip_string(const std::string& t, size_t& i, std::string* out) {
    if (t[i] != '"') return false;
    ++i;
    while (i < t.size() && t[i] != '"') {
        const unsigned char c = uint8_t(t[i]);
        if (c < 0x20) return false;
        if (c == '\\') {
            if (i + 1 >= t.size()) return false;
            const char e = t[i + 1];
            if (e == 'u') { if (i + 5 >= t.size()) return false; for (int k = 2; k < 6; ++k) if (!isxdigit(uint8_t(t[i + k]))) return false;
                            if (out) return false;          // an escaped coordinate: the ordinary parse decodes it
                            i += 6; continue; }
            if (!strchr("\"\\/bfnrt", e)) return false;
            if (out) return false;
            i += 2; continue;
        }
        if (out) *out += char(c);
        ++i;
    }
    if (i >= t.size()) return false;
    ++i; return true;
}
static bool skip_value(const std::string& t, size_t& i, int depth) {
    if (depth > 64 || !skip_ws(t, i)) return false;
    const char c = t[i];
    if (c == '"') return skip_string(t, i, nullptr);
    if (c == '{' || c == '[') {
        const char close = c == '{' ? '}' : ']'; ++i;
        if (!skip_ws(t, i)) return false;
        if (t[i] == close) { ++i; return true; }
        for (;;) {
            if (c == '{') { if (!skip_ws(t, i) || !skip_string(t, i, nullptr) || !skip_ws(t, i) || t[i] != ':') return false; ++i; }
            if (!skip_value(t, i, depth + 1) || !skip_ws(t, i)) return false;
            if (t[i] == ',') { ++i; continue; }
            if (t[i] == close) { ++i; return true; }
            return false;
        }
    }
    if (!t.compare(i, 4, "true") || !t.compare(i, 4, "null")) { i += 4; return true; }
    if (!t.compare(i, 5, "false")) { i += 5; return true; }
    const size_t s0 = i;
    if (t[i] == '-') ++i;
    while (i < t.size() && (isdigit(uint8_t(t[i])) || t[i] == '.' || t[i] == 'e' || t[i] == 'E' || t[i] == '+' || t[i] == '-')) ++i;
    return i > s0 && isdigit(uint8_t(t[i - 1]));
}
static bool skim_write(const std::string& t, std::string& sv, std::string& vv, std::string& iv, size_t& b0, size_t& b1) {
    size_t i = 0; bool bag = false;
    if (!skip_ws(t, i) || t[i] != '{') return false;
    ++i;
    for (;;) {
        std::string k;
        if (!skip_ws(t, i) || !skip_string(t, i, &k) || !skip_ws(t, i) || t[i] != ':') return false;
        ++i; if (!skip_ws(t, i)) return false;
        if (k == "bag") { if (t[i] != '{') return false; b0 = i; if (!skip_value(t, i, 0)) return false; b1 = i; bag = true; }
        else if (k == "service" || k == "variable" || k == "instance") { std::string& d = k == "service" ? sv : k == "variable" ? vv : iv; d.clear(); if (!skip_string(t, i, &d)) return false; }
        else return false;
        if (!skip_ws(t, i)) return false;
        if (t[i] == ',') { ++i; continue; }
        if (t[i] != '}') return false;
        ++i; skip_ws(t, i);
        return bag && i >= t.size();
    }
}
bool api(const std::string& method, const std::string& path, const std::string& body, bool may_wait, Reply& out) {
    if (path.compare(0, API_PATH.size(), API_PATH)) { out = bad(403, "not this service's API"); return true; }
    auto q = query(path); const std::string op = q["op"];
    if (op == "version") { out = Reply{200, "{\"ok\":true,\"version\":" + Json::quote(HOST->vendor_version()) + ",\"vendor\":"
                                         + Json::quote(HOST->vendor_id()) + ",\"platform\":" + Json::quote(RIBBIT_PLATFORM_VERSION) + "}"}; return true; }
    if (HOST->handles(op)) {                             // [VENDOR_API_V1] the vendor's region
        if (method != "POST") { out = bad(405, op + " is POST"); return true; }
        if (!may_wait && !HOST->inline_operation(op)) return false;   // its own request thread, unless it never waits [INLINE_REGION_OP_V1]
        out = HOST->operation(op, body); return true;
    }
    if (op == "write") {
        if (method != "POST") { out = bad(405, "write is POST"); return true; }
        {   // [WRITE_BODY_SKIMMED_V1] the body read in place: its three coordinates, and the bag's text as written, checked
            // to be one JSON object -- no tree built and serialised again (measured: a quarter of the memory host's CPU)
            std::string sv, vv, iv; size_t b0 = 0, b1 = 0;
            if (skim_write(body, sv, vv, iv, b0, b1)) {
                if (sv.empty() || vv.empty() || iv.empty()) { out = bad(400, std::string("missing coordinate: ") + (sv.empty() ? "service" : vv.empty() ? "variable" : "instance")); return true; }
                if (private_service(sv)) { out = bad(403, "service " + sv + " is not served"); return true; }
                out = Reply{200, "{\"ok\":true,\"id\":" + std::to_string(MEM.write(sv, vv, iv, body.substr(b0, b1 - b0), wall())) + "}"}; return true;
            }
        }
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
                    c.f = std::move(f); c.t.store(now_ns(), std::memory_order_relaxed); c.seq.store(pos + 1, std::memory_order_release);
                    work_.fetch_add(1, std::memory_order_seq_cst); wake_or_grow(); return; } }
            else if (d < 0) std::this_thread::yield();        // full
        }
    }
    bool pop(std::function<void()>& f) { uint64_t t; return pop(f, t); }
    bool pop(std::function<void()>& f, uint64_t& t) {
        for (;;) {
            size_t pos = head_.load(std::memory_order_relaxed);
            Cell& c = cell_[pos & (N - 1)]; const size_t seq = c.seq.load(std::memory_order_acquire);
            const intptr_t d = intptr_t(seq) - intptr_t(pos + 1);
            if (d == 0) { if (head_.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed)) {
                    f = std::move(c.f); t = c.t.load(std::memory_order_relaxed); c.seq.store(pos + N, std::memory_order_release); return true; } }
            else if (d < 0) return false;                     // empty
        }
    }
    // [ADAPTIVE_POOL_V1] sleepers << 32 | wakes on their way, in one word; only a worker changes the sleeper count
    static constexpr uint64_t ONE_SLEEPER = uint64_t(1) << 32;
    static uint64_t now_ns() { return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()); }
    static uint64_t wait_ns() { static const uint64_t v = [] { const char* e = getenv("RIBBIT_POOL_WAIT_US"); long n = e ? atol(e) : 0; return uint64_t(n > 0 ? n : 1000) * 1000; }(); return v; }
    static int idle_s() { static const int v = [] { const char* e = getenv("RIBBIT_POOL_IDLE_S"); int n = e ? atoi(e) : 0; return n > 0 ? n : 60; }(); return v; }
    void wake_or_grow() {
        uint64_t st = sleep_.load(std::memory_order_seq_cst);
        while ((st >> 32) > (st & 0xffffffffu))
            if (sleep_.compare_exchange_weak(st, st + 1, std::memory_order_seq_cst)) { wake_one(); return; }
        if (threads_.load(std::memory_order_acquire) == 0) { maybe_grow(); return; }   // nobody at all: the first
        start_monitor();
        if (starved()) maybe_grow();
    }
    bool backlog() const { return head_.load(std::memory_order_acquire) != tail_.load(std::memory_order_acquire); }
    bool none_free() const { const uint64_t st = sleep_.load(std::memory_order_seq_cst); return (st >> 32) <= (st & 0xffffffffu); }
    // [WAIT_TIME_TRIGGER_V1] the oldest queued task has waited past the trigger and nobody is free to take it
    bool starved() const {
        if (!none_free()) return false;
        const size_t pos = head_.load(std::memory_order_acquire);
        const Cell& c = cell_[pos & (N - 1)];
        if (c.seq.load(std::memory_order_acquire) != pos + 1) return false;      // empty, or still being filled
        const uint64_t t = c.t.load(std::memory_order_relaxed), now = now_ns();
        return now > t && now - t > wait_ns();
    }
    void maybe_grow() { if (!starting_.exchange(true, std::memory_order_acq_rel)) grow(); }
    void grow() {
        const uint32_t n = threads_.fetch_add(1, std::memory_order_acq_rel) + 1;
        uint32_t pk = peak_.load(std::memory_order_relaxed);
        while (n > pk && !peak_.compare_exchange_weak(pk, n, std::memory_order_relaxed)) {}
        if (n > pk && (n & (n - 1)) == 0)                   // a new peak that is a power of two: say so
            std::cerr << "[ADAPTIVE_POOL_V1] pool: " << n << " threads (started " << started_.load() + 1 << ", retired "
                      << retired_.load() << ", wait trigger " << wait_ns() / 1000 << " us, idle exit " << idle_s() << " s)\n";
        started_.fetch_add(1, std::memory_order_relaxed);
        std::thread([this] { worker(); }).detach();
    }
    // [WAIT_TIME_TRIGGER_V1] every millisecond while anything is queued: all workers may be parked, with no push coming
    void start_monitor() {
        if (monitor_.exchange(true, std::memory_order_acq_rel)) return;
        std::thread([this] { for (;;) {
            if (backlog()) { if (starved()) maybe_grow(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
            else std::this_thread::sleep_for(std::chrono::milliseconds(10));   // its own sleep: a push's wake is for workers
        } }).detach();
    }
    bool leave_sleep() {                                       // true: a wake was on its way and this sleeper took it
        uint64_t st = sleep_.load(std::memory_order_seq_cst);
        for (;;) {
            const bool took = (st & 0xffffffffu) != 0;
            if (sleep_.compare_exchange_weak(st, st - ONE_SLEEPER - (took ? 1 : 0), std::memory_order_seq_cst)) return took;
        }
    }
    void worker() {
        std::function<void()> f; uint64_t t = 0;
        starting_.store(false, std::memory_order_release);
        if (starved()) maybe_grow();                            // running: the next may start, if work still waits
        for (;;) {
            while (pop(f, t)) {
                const uint64_t now = now_ns();
                if (now > t && now - t > wait_ns() && backlog() && none_free()) maybe_grow();   // this one waited: so may the next
                f(); f = nullptr;
            }
            sleep_.fetch_add(ONE_SLEEPER, std::memory_order_seq_cst);  // free: a push may send us its wake
            const uint32_t seen = work_.load(std::memory_order_seq_cst);
            if (backlog()) { leave_sleep(); continue; }
            const bool timed_out = !sleep_for(seen, idle_s());
            const bool woken = leave_sleep();
            if (backlog()) continue;
            if (timed_out && !woken) { threads_.fetch_sub(1, std::memory_order_acq_rel); retired_.fetch_add(1, std::memory_order_relaxed); return; }
        }
    }
private:
    bool sleep_for(uint32_t seen, int secs) {                  // false: the time ran out
#ifdef _WIN32
        return WaitOnAddress(reinterpret_cast<volatile void*>(&work_), &seen, sizeof seen, DWORD(secs) * 1000) || GetLastError() != ERROR_TIMEOUT;
#else
        timespec ts{secs, 0};
        const long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&work_), FUTEX_WAIT_PRIVATE, seen, &ts, nullptr, 0);
        return !(r == -1 && errno == ETIMEDOUT);
#endif
    }
    void wake_one() {
#ifdef _WIN32
        WakeByAddressSingle(reinterpret_cast<void*>(&work_));
#else
        syscall(SYS_futex, reinterpret_cast<uint32_t*>(&work_), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
#endif
    }
    struct Cell { std::atomic<size_t> seq; std::atomic<uint64_t> t{0}; std::function<void()> f; };   // t: when queued
    alignas(64) std::atomic<size_t> head_{0}; alignas(64) std::atomic<size_t> tail_{0};
    alignas(64) std::atomic<uint32_t> work_{0};
    alignas(64) std::atomic<uint64_t> sleep_{0};               // sleepers << 32 | wakes on their way
    std::atomic<bool> starting_{false}, monitor_{false};       // a worker started and not yet running; the monitor is up
    std::atomic<uint32_t> threads_{0}, peak_{0}; std::atomic<uint64_t> started_{0}, retired_{0};
    Cell cell_[N];
};
WorkRing& work_ring() {                                      // [ADAPTIVE_POOL_V1] the pool: empty until work arrives
    static WorkRing* r = new WorkRing;
    return *r;
}
// [HOST_FANOUT_V1] a session's reply queue: intrusive MPSC, any worker pushes, the session's one writer pops
struct OutItem { std::atomic<OutItem*> next{nullptr}; uint32_t seq = 0; bool engine = false; std::string frame;
                 uint8_t rows_op = 0; std::vector<Cell> rows;   // [DECODE_ONCE_V1] rows to send: encoded from their arrays
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
        if (wake) wake->bump();                               // [FAMILY_HOOKS_V1] the session's one word
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
    ramm::WakeWord* wake = nullptr;                           // set before the session's first frame is read
private:
    void push_stub() { stub_.next.store(nullptr, std::memory_order_relaxed); OutItem* prev = head_.exchange(&stub_, std::memory_order_acq_rel); prev->next.store(&stub_, std::memory_order_release); }
    OutItem stub_; std::atomic<OutItem*> head_; OutItem* tail_;
};
struct Session;
void run_in_order(const std::shared_ptr<Session>& s, std::function<void()> f);
static std::atomic<uint64_t> g_session_epoch{1};   // [SESSION_EPOCH_V1] unique per session for the life of the process
// [DIAG-ROWS] rows given to readers: by forwarding their writer's changes, or by comparing ([FORWARD_CHANGES_V1]); said at exit
std::atomic<uint64_t> g_rows_fwd{0}, g_rows_cmp{0}, g_rows_shared{0};
const bool g_rows_said = [] { std::atexit([] { const uint64_t f = g_rows_fwd.load(), c = g_rows_cmp.load();
    if (f + c) fprintf(stderr, "[DIAG-ROWS] rows to readers: forwarded %llu (packet made by another reader's encoder: %llu), compared %llu\n",
                       (unsigned long long)f, (unsigned long long)g_rows_shared.load(), (unsigned long long)c); }); return true; }();

struct Session {
    const uint64_t epoch = g_session_epoch.fetch_add(1, std::memory_order_relaxed);   // [SESSION_EPOCH_V1] the session's id
    int req = -1, ret = -1, data = -1;
    ~Session() { if (ret >= 0) FR_CLOSE(ret); if (data >= 0) FR_CLOSE(data);
                 for (auto* f = follows.load(); f;) { auto* n = f->next; delete f; f = n; } }   /* [SESSION_FREES_ITS_SOCKETS_V1] shut down at session end, closed when the last holder lets go */
    std::mutex data_m; std::mutex ready_m; std::condition_variable ready_cv; bool ready = false;   // set-up only
    // [CELL_WRITE_V1] the families this session's writers have named: (writer slot, family number) -> service, variable.
    // Written once each, read by any job, no lock: an insert-only open-addressed table of pointers set by CAS.
    struct Fam { uint64_t slot, fam; std::string sv, var; cellwire::Chain chain;   // sv, var: set by the chain's write 1, read by its later ones (the chain's job only)
                 Fam(uint64_t a, uint64_t b, std::string c, std::string d) : slot(a), fam(b), sv(std::move(c)), var(std::move(d)) {} };
    static constexpr size_t FAMS = 1 << 14;
    std::unique_ptr<std::atomic<const Fam*>[]> fams{new std::atomic<const Fam*>[FAMS]()};
    const Fam* fam_find(uint64_t slot, uint64_t fam) const {
        size_t h = size_t((slot * 0x9E3779B97F4A7C15ull) ^ (fam * 0xC2B2AE3D27D4EB4Full));
        for (size_t k = 0; k < FAMS; ++k) { const Fam* e = fams[(h + k) & (FAMS - 1)].load(std::memory_order_acquire);
            if (!e) return nullptr;
            if (e->slot == slot && e->fam == fam) return e; }
        return nullptr;
    }
    // the chain's entry, made by whichever of its writes arrives first (its names come with write 1, which every
    // later write of the chain waits for anyway): insert-only, by CAS
    Fam* fam_get(uint64_t slot, uint64_t fam) {
        size_t h = size_t((slot * 0x9E3779B97F4A7C15ull) ^ (fam * 0xC2B2AE3D27D4EB4Full));
        Fam* mine = nullptr;
        for (size_t k = 0; k < FAMS; ++k) { std::atomic<const Fam*>& sl = fams[(h + k) & (FAMS - 1)]; const Fam* e = sl.load(std::memory_order_acquire);
            if (!e) { if (!mine) mine = new Fam(slot, fam, std::string(), std::string());
                      if (sl.compare_exchange_strong(e, mine, std::memory_order_acq_rel)) return mine; }
            if (e->slot == slot && e->fam == fam) { delete mine; return const_cast<Fam*>(e); } }
        delete mine; return nullptr;
    }
    std::unique_ptr<fnwp::ServerEngine> eng; Ceiling ceil; std::atomic<uint64_t> data_frames{0}, data_bytes{0}, data_in{0};
    std::unique_ptr<fnwp::dataplane::Sender> dsend; fnwp::dataplane::Reassembler reasm; fnwp::dataplane::Landed landed;
    // [HOST_FANOUT_V1] a reply is handed to the session's writer; nothing that produces one sends it
    OutQueue outq; std::atomic<bool> closing{false}; std::thread writer;
    // [FAMILY_HOOKS_V1] this session's standing reads: added by its reader (insert-only, by CAS), read and answered by
    // its writer -- the one thread of the session that sends, woken by the session's one word: a write to a family it
    // follows, or a reply to send. It reads each family itself, from where it left off; nobody hands it rows.
    // [READERS_IN_PARALLEL_V1] each standing read is read by itself, as tasks of its own Ordered on the pool: its rows
    // in order, every read at the same time as every other; what it reads goes to the session's writer to send
    struct Follow { uint32_t seq; ramm::Memory::Cursor c; ramm::Hook* hook = nullptr; bool first = true; Follow* next = nullptr;
                    ribbit::Ordered ord; std::atomic<bool> busy{false}; std::atomic<uint64_t> seen{~0ull};
                    Follow(uint32_t s, ramm::Memory::Cursor cc) : seq(s), c(std::move(cc)) {} };
    std::weak_ptr<Session> self;
    std::atomic<Follow*> follows{nullptr};
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

    // [DECODE_ONCE_V1] rows given to a reader: the writer encodes them from the memory's arrays, in the order it sends
    void reply_rows(uint32_t seq, uint8_t op, std::vector<Cell> rows) {
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->rows_op = op; o->rows = std::move(rows); outq.push(o); }
    std::string rows_frame(uint8_t op, std::vector<Cell>& rows) {
        std::vector<rowcodec::Encoder::RowIn> in; in.reserve(rows.size());
        for (auto& c : rows) { if (!c.arr) c.arr = ribbit::BagArr::parse(c.bag);   // a row written as text: decoded once, here
                               if (!c.arr) continue;
                               in.push_back(rowcodec::Encoder::RowIn{c.id, c.updated, &c.service, &c.variable, &c.instance, c.arr, c.prev, c.changed.get()}); }
        try { const uint64_t f0 = rowenc.c_forwarded, c0 = rowenc.c_compared, s0 = rowenc.c_shared;
              std::string out = std::string(MAGIC, 4) + char(op) + rowenc.encode_rows(in);
              g_rows_fwd.fetch_add(rowenc.c_forwarded - f0, std::memory_order_relaxed); g_rows_cmp.fetch_add(rowenc.c_compared - c0, std::memory_order_relaxed);
              g_rows_shared.fetch_add(rowenc.c_shared - s0, std::memory_order_relaxed);
              return out; }
        catch (const std::exception& e) { return std::string(MAGIC, 4) + char(0x30) + be16(500) + "rows: " + e.what(); }
    }
    void reply(uint32_t seq, const std::string& frame) {
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->frame = frame; outq.push(o); }
    // [ROWS_ON_THE_SEMANTIC_WIRE_V1] rows given to a reader (a read after a write id 0x7E, a standing trigger's batch 0x7D)
    // leave as differences against this session's previous rows of their shape, encoded here, in the order they leave
    rowcodec::Encoder rowenc;
    rowcodec::RequestDecoder reqdec;                      // [READ_REQUESTS_BY_REFERENCE_V1] the session's reader only
    std::string rows_out(std::string f) {
        if (f.size() > 5 && (uint8_t(f[4]) == 0x7E || uint8_t(f[4]) == 0x7D)) {
            try { return f.substr(0, 5) + rowenc.encode(std::string_view(f).substr(5)); }
            catch (const std::exception& e) { return std::string(MAGIC, 4) + char(0x30) + be16(500) + "rows: " + e.what(); }
        }
        return f;
    }
    void writer_run() {                                       // the session's ONE writer
        for (;;) {
            const uint32_t seen = outq.wake->n.load(std::memory_order_acquire);
            OutItem* o;
            while ((o = outq.pop())) {
                if (o->action == 1) eng->reset_response(o->w.ik);
                else if (o->action == 2) reply_locked(o->seq, eng->same_miss_frame(o->w.same_miss));
                else if (o->engine) encode_and_send(o->seq, o->w, o->r);
                else if (o->rows_op) reply_locked(o->seq, rows_frame(o->rows_op, o->rows));
                else reply_locked(o->seq, rows_out(std::move(o->frame)));
                delete o;
            }
            if (closing.load(std::memory_order_acquire)) return;
            serve_follows();
            outq.wake->n.wait(seen, std::memory_order_acquire);
        }
    }
    void serve_follows();
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
}  // namespace
// [SESSION_ORDER_V1] for a vendor: tasks run one at a time, in the order run() was called, on the host's pool;
// different Ordered run in parallel. The same lock-free list and single-drain flag as a session's order.
namespace ribbit {
struct Ordered::Impl {
    struct Task { std::atomic<Task*> next{nullptr}; std::function<void()> f; };
    Task stub; std::atomic<Task*> head{&stub}; Task* tail = &stub; std::atomic<bool> scheduled{false};
    std::atomic<uint64_t> pushed{0}; uint64_t popped = 0;
    void push(Task* n) { n->next.store(nullptr, std::memory_order_relaxed); Task* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
    Task* pop() {
        Task* t = tail; Task* next = t->next.load(std::memory_order_acquire);
        if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { tail = next; return t; }
        if (t != head.load(std::memory_order_acquire)) return nullptr;
        stub.next.store(nullptr, std::memory_order_relaxed); Task* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
        next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
        return nullptr;
    }
};
void pool_run(std::function<void()> f) { work_ring().push(std::move(f)); }   // [ADAPTIVE_POOL_V1]
Ordered::Ordered() : d_(new Impl) {}
Ordered::~Ordered() { delete d_; }
void Ordered::run(std::function<void()> f) {
    Impl* d = d_;
    Impl::Task* t = new Impl::Task; t->f = std::move(f); d->push(t);
    d->pushed.fetch_add(1, std::memory_order_seq_cst);
    bool idle = false;
    if (!d->scheduled.compare_exchange_strong(idle, true, std::memory_order_seq_cst)) return;
    work_ring().push([d] {
        for (;;) {
            Impl::Task* n = nullptr;
            while (d->popped != d->pushed.load(std::memory_order_seq_cst)) {
                if ((n = d->pop())) {
                    try { n->f(); } catch (const std::exception& e) { logline(std::string("ordered task: ") + e.what()); }
                    delete n; ++d->popped;
                }
            }
            d->scheduled.store(false, std::memory_order_seq_cst);
            if (d->pushed.load(std::memory_order_seq_cst) == d->popped) return;
            bool idle2 = false;
            if (!d->scheduled.compare_exchange_strong(idle2, true, std::memory_order_seq_cst)) return;
        }
    });
}
}  // namespace ribbit
namespace {
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
namespace ribbit {
void* defer_answer() { if (!tl_current) return nullptr; tl_deferred = true;   // the Work is copied only here, when deferred
    return new RinaDeferred{*tl_current->s, tl_current->seq, *tl_current->w, tl_current->mine}; }
uint64_t current_session() { return tl_session_id; }   // [SESSION_OF_THE_CALL_V1]
void answer_later(void* h, const std::string& body) {
    RinaDeferred* d = static_cast<RinaDeferred*>(h); if (!d) return;
    Reply r{200, body};
    if (d->mine) co_table().answer(d->mine, r);
    d->s->finish_and_send(d->seq, d->w, r);
    delete d;
}
}  // namespace ribbit
namespace {
void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {
    fnwp::ServerEngine::Work w;
    try { w = s->eng->begin(f); }
    catch (const std::exception& e) { s->reply(seq, semwire::wrap_error(400, std::string("decode: ") + e.what())); return; }
    if (!w.frame.empty()) { s->reply(seq, w.frame); return; }
    if (!w.same_miss.empty()) { OutItem* o = new OutItem; o->seq = seq; o->action = 2; o->w = w; s->outq.push(o); return; }   // [ENGINE_LOCKLESS_V1]
    if (w.fresh) { OutItem* o = new OutItem; o->action = 1; o->w = w; s->outq.push(o); }                                     // [ENGINE_LOCKLESS_V1]
    if (std::getenv("FROGRAM_TRACE")) std::cerr << "[ram-server] seq=" << seq << " op=" << int(uint8_t(f[4])) << " " << w.method << " " << w.path.substr(0, 90) << " body=" << w.body.size() << "B raw=" << (w.raw ? "y" : "n") << (w.body_pending ? " pending" : "") << " " << w.body.substr(0, 300) << "\n";
    if (w.unanswered) {                                     // [NO_WRITE_ACK_V1] a write nobody waits for: only a refusal is answered
        run_in_order(s, [s, seq, w = std::move(w)]() mutable {
            tl_session_id = s->epoch; Reply r;
            if (!api(w.method, w.path, w.body, false, r)) api(w.method, w.path, w.body, true, r);
            if (r.status >= 400) s->reply(seq, semwire::wrap_error(uint16_t(r.status), r.body));
        });
        return;
    }
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
        if (tl_deferred) { tl_deferred = false; return; }   // the region answers it later (ribbit::answer_later)
        if (done) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, std::move(w), r); return; }
        work_ring().push([=] { tl_session_id = s->epoch; Reply rr; api(w.method, w.path, w.body, true, rr);
                          if (mine) co_table().answer(mine, rr);
                          s->finish_and_send(seq, w, rr); });   // a parked read, on the pool [ADAPTIVE_POOL_V1]
    };
    if (w.body_pending) {
        work_ring().push([s, seq, w, run] {                  // [ADAPTIVE_POOL_V1] on the pool
            fnwp::ServerEngine::Work ww = w;
            try { ww.body = s->landed.take(seq); }
            catch (const std::exception& e) { s->reply(seq, semwire::wrap_error(400, std::string("data socket: ") + e.what())); return; }
            if (ww.body.size() != w.body_bytes) { s->reply(seq, semwire::wrap_error(400, "data socket: the landed body is " + std::to_string(ww.body.size()) + " B, the marker said " + std::to_string(w.body_bytes))); return; }
            ww.body_pending = false; run(ww);
        });
        return;
    }
    run_in_order(s, [run, w = std::move(w)]() mutable { run(std::move(w)); });   // [SESSION_ORDER_V1] [WORK_MOVED_NOT_COPIED_V1]
}
// [READS_AFTER_IN_BINARY_V1] A read of what was written after a write id -- a trigger, a follower -- comes back as
// new rows every time, so SAME/DIFF have nothing to work on: it is the memory's read verb in binary. Request: MAGIC,
// this op, u16 service, u16 variable, u32 instance, u64 after, u32 wait_ms, u32 fresh_s, u32 min_rows. Answer: MAGIC,
// this op, then per row u64 id, u64 updated (IEEE double bits), u16 service, u16 variable, u32 instance, u32 bag.
// It runs on the pool: a held one waits there, never on the session's reader or in its order.
constexpr uint8_t OP_MEM_READ = 0x7E;
void mem_read_frame(std::shared_ptr<Session> s, uint32_t seq, std::string f) {
    work_ring().push([s, seq, f = std::move(f)] {           // [ADAPTIVE_POOL_V1]
        size_t p = 5; std::string why;
        auto num = [&](size_t w) -> uint64_t { if (p + w > f.size()) { why = "cut short"; return 0; }
            uint64_t v = 0; for (size_t k = 0; k < w; ++k) v = v << 8 | uint8_t(f[p + k]); p += w; return v; };
        auto take = [&](size_t k) -> std::string { if (p + k > f.size()) { why = "cut short"; return std::string(); } std::string x = f.substr(p, k); p += k; return x; };
        const std::string sv = take(size_t(num(2))), vv = take(size_t(num(2))), iv = take(size_t(num(4)));
        const long long after = (long long)int64_t(num(8)); double wait = double(num(4)) / 1000.0; const int fresh = int(num(4)); size_t min_rows = size_t(num(4));
        if (!why.empty() || sv.empty()) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "read: " + (why.empty() ? "missing service" : why)); return; }
        if (HOST->private_service(sv)) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(403) + "service " + sv + " is not served"); return; }
        if (wait > WAIT_MAX_S) wait = WAIT_MAX_S;
        if (after >= 0 && min_rows == 0) min_rows = 1;
        auto eval = [&] { return MEM.match(sv, vv, iv, after, fresh, wall()); };
        std::vector<Cell> rows = eval();
        if (wait > 0 && min_rows > 0 && rows.size() < min_rows) {
            auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait);
            rows = MEM.wait(sv, vv, iv, deadline, eval, [&](const std::vector<Cell>& r) { return r.size() >= min_rows; });
        }
        s->reply_rows(seq, OP_MEM_READ, std::move(rows));   // [DECODE_ONCE_V1]
    });
}
// [FOLLOW_STREAM_V1] A standing trigger: one request (as OP_MEM_READ, after = the last id the reader has), then the
// family's rows written after it, in write order, each batch as one answer under the request's sequence number, until
// the session ends. The first answer comes at once (the rows there now, or none: the reader knows where it stands).
// A follower is a thread of its own, parked in the memory's wait between batches: no request per batch, no pool slot.
constexpr uint8_t OP_MEM_FOLLOW = 0x7D;
std::atomic<int> g_followers{0};
// [FAMILY_HOOKS_V1] A standing read is the session's: its writer reads the family by position when the family's writes
// bump the session's word, and sends what it read under the request's sequence number. No thread looks at anyone
// else's reads, no list is shared between sessions, and a write that nobody follows wakes nobody.
// a read's task: what was written since it last read, read and handed to the writer. busy is cleared before the read,
// so a write that lands after it is either read here or starts the next task.
static void follow_task(const std::shared_ptr<Session>& s, Session::Follow* f) {
    for (;;) {
        f->busy.store(false, std::memory_order_seq_cst);
        f->seen.store(ramm::Memory::head_of(f->c), std::memory_order_release);
        std::vector<Cell> rows; MEM.read(f->c, rows);
        if (rows.empty() && !f->first) return;
        f->first = false;                                       // the first answer is the family's state now, rows or none
        const bool had = !rows.empty();
        s->reply_rows(f->seq, OP_MEM_FOLLOW, std::move(rows));
        if (!had) return;
        bool idle = false;
        if (!f->busy.compare_exchange_strong(idle, true, std::memory_order_seq_cst)) return;   // another task has it now
    }
}
void Session::serve_follows() {                                 // the session's writer: which reads have something new
    for (Follow* f = follows.load(std::memory_order_acquire); f; f = f->next) {
        if (!f->first && ramm::Memory::head_of(f->c) == f->seen.load(std::memory_order_acquire)) continue;
        bool idle = false;
        if (!f->busy.compare_exchange_strong(idle, true, std::memory_order_seq_cst)) continue;   // its task is reading
        std::shared_ptr<Session> s = self.lock(); if (!s) return;
        f->ord.run([s, f] { follow_task(s, f); });
    }
}
void mem_follow_frame(std::shared_ptr<Session> s, uint32_t seq, std::string f) {   // on the session's reader
    size_t p = 5; std::string why;
    auto num = [&](size_t w) -> uint64_t { if (p + w > f.size()) { why = "cut short"; return 0; }
        uint64_t v = 0; for (size_t k = 0; k < w; ++k) v = v << 8 | uint8_t(f[p + k]); p += w; return v; };
    auto take = [&](size_t k) -> std::string { if (p + k > f.size()) { why = "cut short"; return std::string(); } std::string x = f.substr(p, k); p += k; return x; };
    const std::string sv = take(size_t(num(2))), vv = take(size_t(num(2))), iv = take(size_t(num(4)));
    const long long after = (long long)int64_t(num(8));
    if (!why.empty() || sv.empty()) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "follow: " + (why.empty() ? "missing service" : why)); return; }
    if (HOST->private_service(sv)) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(403) + "service " + sv + " is not served"); return; }
    g_followers.fetch_add(1);
    auto* n = new Session::Follow(seq, MEM.cursor(sv, vv, iv, after));   // after < 0: from now on; else every row past it
    n->hook = ramm::Memory::hook(n->c, s->outq.wake);
    n->next = s->follows.load(std::memory_order_acquire);
    while (!s->follows.compare_exchange_weak(n->next, n, std::memory_order_acq_rel)) {}
    s->outq.wake->bump();                                               // its first answer
}
// [CELL_WRITE_V1] A write. The connection's reader reads its frame and appends it to its chain (its writer's writes of
// its family, which arrive in their own order); each chain runs as its own job on the pool, so every chain -- every
// front, every box, every family -- is decoded and stored at the same time as every other, and the only lock is the
// row's, in MEM.write. A write's reference is its chain's previous write. If that is not there (refused, or never sent),
// the write is not waited for: it is refused, said to its writer, and the writer deals with it. Nothing answers a write.
constexpr uint32_t CELL_SEQ = 0xFFFFFFFFu;               // [WRITER_SENDS_ITS_OWN_V1] the refusal of a cell write
std::atomic<uint64_t> g_cell_writes{0}, g_cell_refused{0}, g_cell_in{0};
// [DIAG-QUEUE] MQTT_DIAG=1 or FROGRAM_DIAG=1: once a second, the cell writes read off the connections and those stored,
// so a backlog inside the memory is said, not guessed at
static void cell_diag_start() {
    static std::once_flag once;
    std::call_once(once, [] { if (!getenv("MQTT_DIAG") && !getenv("FROGRAM_DIAG")) return;
        std::thread([] { uint64_t li = 0, lw = 0;
            for (;;) { std::this_thread::sleep_for(std::chrono::seconds(1));
                const uint64_t i = g_cell_in.load(), w = g_cell_writes.load(), r = g_cell_refused.load();
                fprintf(stderr, "[DIAG-QUEUE] memory: cell writes in %llu/s stored %llu/s, waiting %lld, refused %llu\n",
                        (unsigned long long)(i - li), (unsigned long long)(w - lw), (long long)(i - w - r), (unsigned long long)r);
                li = i; lw = w; } }).detach(); });
}
static void cell_refuse(const std::shared_ptr<Session>& s, uint32_t seq, int st, const std::string& why) {
    g_cell_refused.fetch_add(1, std::memory_order_relaxed); s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(uint16_t(st)) + why);
}
static void cell_write_step(const std::shared_ptr<Session>& s, uint32_t seq, cellwire::Frame& x, Session::Fam* nm) {
    cellwire::Chain& ch = nm->chain;
    const cellwire::Ref* ref = (ch.last && ch.last_c + 1 == x.counter) ? ch.last.get() : nullptr;
    auto fail = [&](int st, const std::string& why) { ch.last.reset(); ch.last_c = x.counter;   // the writer is told which chain
        cell_refuse(s, seq, st, "[cell " + std::to_string(x.slot) + " " + std::to_string(x.fam) + " " + std::to_string(x.counter) + "] " + why); };
    try {
        if (x.flags & cellwire::F_NAMES) { nm->sv = x.sv; nm->var = x.var; }
        if (nm->sv.empty() || nm->var.empty()) return fail(409, "cell write: a family its writer never named");
        if (!(x.flags & cellwire::F_KEYS) && !ref)
            return fail(409, "cell write: " + nm->var + " write " + std::to_string(x.counter) + " is a difference against write " +
                        std::to_string(x.counter - 1) + " of its writer, which the memory does not have");
        if (private_service(nm->sv)) return fail(403, "service " + nm->sv + " is not served");
        ribbit::BagArrP bag;                              // [DECODE_ONCE_V1] decoded once, stored as it is
        cellwire::Changed changed;                        // [FORWARD_CHANGES_V1] and what it changed against its reference
        ribbit::BagArrP prev = (ref && !(x.flags & cellwire::F_KEYS)) ? ref->arr : nullptr;
        std::unique_ptr<cellwire::Ref> mine = cellwire::decode(x, ref, bag, &changed);
        if (x.inst.empty()) return fail(400, "cell write: missing coordinate: instance");
        ch.last = std::move(mine); ch.last_c = x.counter;
        { static const bool tr = getenv("FROGRAM_DIAG") && atoi(getenv("FROGRAM_DIAG")) >= 2;   // [DIAG-CELL] each write stored
          if (tr) fprintf(stderr, "[DIAG-CELL] %s/%s/%s chain %llu.%llu #%u %zu fields%s\n", nm->sv.c_str(), nm->var.c_str(), x.inst.c_str(), (unsigned long long)x.slot,
                          (unsigned long long)x.fam, x.counter, bag->vals.size(), (x.flags & cellwire::F_KEYS) ? " whole" : ""); }
        MEM.write(nm->sv, nm->var, x.inst, std::move(bag), wall(), std::move(prev), std::move(changed));
        g_cell_writes.fetch_add(1, std::memory_order_relaxed);
    } catch (const std::exception& e) { fail(400, std::string("cell write: ") + e.what()); }
}
void cell_write_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {   // on the connection's reader
    auto xp = std::make_unique<cellwire::Frame>();
    try { *xp = cellwire::parse(f, 5); } catch (const std::exception& e) { return cell_refuse(s, seq, 400, std::string("cell write: ") + e.what()); }
    Session::Fam* nm = s->fam_get(xp->slot, xp->fam);
    if (!nm) return cell_refuse(s, seq, 507, "cell write: too many families on one session");
    cell_diag_start(); g_cell_in.fetch_add(1, std::memory_order_relaxed);
    cell_write_step(s, seq, *xp, nm);       // stored here, on the connection's reader: a connection's writes are one chain's
                                            // order already; connections store in parallel, each on its own reader
}
// [NEWEST_ID_V1] where a family stands: MAGIC, this op, u16 service, u16 variable -> MAGIC, this op, u64 the newest id
// written to it (0: none). A reader that starts "from now on" asks this, then reads past it.
constexpr uint8_t OP_MEM_NEWEST = 0x7B;
void mem_newest_frame(const std::shared_ptr<Session>& s, uint32_t seq, const std::string& f) {
    size_t p = 5; auto n16 = [&]() -> size_t { if (p + 2 > f.size()) return 0; size_t v = size_t(uint8_t(f[p])) << 8 | uint8_t(f[p + 1]); p += 2; return v; };
    const size_t ls = n16(); const std::string sv = p + ls <= f.size() ? f.substr(p, ls) : std::string(); p += ls;
    const size_t lv = n16(); const std::string vv = p + lv <= f.size() ? f.substr(p, lv) : std::string();
    if (sv.empty() || HOST->private_service(sv)) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "newest: no such service"); return; }
    const uint64_t id = MEM.newest(sv, vv);
    std::string o(MAGIC, 4); o += char(OP_MEM_NEWEST); for (int k = 7; k >= 0; --k) o += char(id >> (8 * k));
    s->reply(seq, o);
}
void handle_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {
    if (f.size() > 5 && uint8_t(f[4]) == OP_MEM_NEWEST) { mem_newest_frame(s, seq, f); return; }
    if (f.size() > 5 && uint8_t(f[4]) == cellwire::OP_CELL_WRITE) { cell_write_frame(s, seq, f); return; }   // [CELL_WRITE_V1]
    if (f.size() > 5 && (uint8_t(f[4]) == OP_MEM_READ || uint8_t(f[4]) == OP_MEM_FOLLOW)) {   // [READ_REQUESTS_BY_REFERENCE_V1]
        std::string whole;                                      // made whole on the session's reader, in arrival order
        try { whole = f.substr(0, 5) + s->reqdec.decode(std::string_view(f).substr(5)); }
        catch (const std::exception& e) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "read: " + e.what()); return; }
        if (uint8_t(f[4]) == OP_MEM_READ) mem_read_frame(s, seq, std::move(whole)); else mem_follow_frame(s, seq, std::move(whole));
        return;
    }
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
    work_ring().push([=] { tl_session_id = s->epoch; Reply rr; Cached o = old; api(method, path, body, true, rr); s->reply(seq, wire_reply(h, http_req, have_old ? &o : nullptr, rr)); });   // a parked read, on the pool [ADAPTIVE_POOL_V1]
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
    HOST->session_up(s->epoch, peer);   // [SESSION_PEER_V1] the address the session arrives from
    s->outq.wake = MEM.wake_word();                             // [FAMILY_HOOKS_V1]
    s->self = s;
    { Session* sp = s.get(); s->writer = std::thread([sp] { sp->writer_run(); }); }   // [HOST_FANOUT_V1]
    // [RECEIVES_GATHERED_V1] A request's sequence number is its place among the session's requests. A cell write is not
    // a request -- its writer sends it on the request socket itself, outside the session's numbering, and nothing answers
    // it -- so it takes none; its refusal, if any, carries CELL_SEQ. [WRITER_SENDS_ITS_OWN_V1]
    { FrameReader rd(fd); while (rd.frame(f)) {
          if (f.size() > 5 && uint8_t(f[4]) == cellwire::OP_CELL_WRITE) cell_write_frame(s, CELL_SEQ, f);
          else handle_frame(s, seq++, f); } }
    logline("session down " + peer + " " + token + " after " + std::to_string(seq) + " requests" +
            (s->eng ? ", " + std::to_string(s->data_frames) + " replies (" + std::to_string(s->data_bytes) + " B) on the data socket" : ""));
    { std::lock_guard<std::mutex> g(pend_m); live.erase(token); }
    s->closing.store(true, std::memory_order_release);
    for (auto* f = s->follows.load(std::memory_order_acquire); f; f = f->next) { ramm::Memory::unhook(f->hook); g_followers.fetch_sub(1); }
    s->outq.wake->bump();
    if (s->writer.joinable()) s->writer.join();                 // [HOST_FANOUT_V1] replies after this have no socket to go to
    ramm::Memory::release(s->outq.wake);
    HOST->session_down(s->epoch);   // [SESSION_IS_LIVENESS_V1] before s can be freed and its address reused
    s->landed.close();
    if (s->dsend) s->dsend->stop();
    ::shutdown(s->ret, FR_SHUT_RDWR); if (s->data >= 0) ::shutdown(s->data, FR_SHUT_RDWR); FR_CLOSE(fd);
}
}  // namespace
// Bring the server up on `listen_on` and serve until the process ends. Returns 0 once it is listening (the accept
// loop runs on its own thread), or an errno. This is what lets one executable be its own RAM target (tools/frogbench).
static int ram_server_start(const std::string& listen_on, bool quiet) {
    g_quiet = quiet; size_t c = listen_on.rfind(':'); if (c == std::string::npos) return EINVAL;
#ifdef _WIN32
    { static bool once = [] { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); return true; }(); (void)once; }
#else
    signal(SIGPIPE, SIG_IGN);
#endif
    int srv = int(::socket(AF_INET, SOCK_STREAM, 0)), one = 1; setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    // [RECEIVE_WINDOW_NOT_CLAMPED_V1] The receive buffer is the kernel's to size. It was clamped to 32 KB ("the buffer is
    // the latency" -- true of the SEND side, where it still holds): with one wire carrying a whole box, the request
    // socket closed its window, the window update did not come, and the sender sat in TCP's persist timer -- measured
    // (ss -ti): rwnd_limited 92%, persist timer, the host getting a batch every ~208 ms, 4 s round trips at many-256.
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(atoi(listen_on.c_str() + c + 1))); inet_pton(AF_INET, listen_on.substr(0, c).c_str(), &a.sin_addr);
    if (::bind(srv, reinterpret_cast<sockaddr*>(&a), sizeof a) || ::listen(srv, 1024)) { int e = errno; FR_CLOSE(srv); return e ? e : EADDRINUSE; }
    { sockaddr_in b{}; socklen_t bl = sizeof b;                       // [ANNOUNCE_PORT_V1] port 0: the one the system chose
      if (!::getsockname(srv, reinterpret_cast<sockaddr*>(&b), &bl)) LISTEN_PORT = ntohs(b.sin_port); }
    logline(HOST->vendor_id() + "-ram " + HOST->vendor_version() + " (" + RIBBIT_PLATFORM_VERSION + "): the memory and the "
            + HOST->vendor_id() + " region, answering " + API_PATH + " on " + listen_on);
    std::thread([srv] { for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl); if (fd < 0) continue;
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip); std::thread(serve, fd, std::string(ip)).detach(); } }).detach();
    return 0;
}

// [COVERAGE_ON_TERM_V1] SIGTERM / SIGINT end the program with exit(), from an ordinary thread (sigwait), so a
// coverage build (--coverage) writes its data and the process still stops at once. The signals are blocked in main
// before any other thread starts, so every thread inherits the mask and only the waiting thread receives them.
static void end_on_term_signals() {
    sigset_t set; sigemptyset(&set); sigaddset(&set, SIGTERM); sigaddset(&set, SIGINT);
    pthread_sigmask(SIG_BLOCK, &set, nullptr);
    std::thread([set] { int sig = 0; sigwait(&set, &sig); std::exit(0); }).detach();
}
namespace ribbit {
int RamHost::start(const std::string& listen_on, bool quiet) {
    HOST = this; API_PATH = api_path(); REGION_SERVICE = region_service(); LOG_TAG = vendor_id() + "-ram";
    const size_t c = listen_on.rfind(':');
    LISTEN_PORT = atoi(listen_on.c_str() + c + 1);
    LISTEN_HOST = listen_on.substr(0, c);
    if (LISTEN_HOST.empty() || LISTEN_HOST == "0.0.0.0") LISTEN_HOST = "127.0.0.1";
    return ram_server_start(listen_on, quiet);
}
int RamHost::run(int argc, char** argv) {
    const std::string name = vendor_id() + "-ram";
    std::string listen_on = default_listen();
    int announce_fd = -1;     // [ANNOUNCE_PORT_V1] once listening, the port goes here ("<port>\n"): a parent that started this
                              // host on port 0 learns which port it has, with no window for another process to take it
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--version")) { std::cout << name << " " << vendor_version() << " (" << RIBBIT_PLATFORM_VERSION << ")\n"; return 0; }
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            std::cout << name << " -- the " << vendor_id() << " region's shared memory, answering " << api_path() << "\n"
                      << "  " << name << " [--listen HOST:PORT]    (default " << default_listen() << ")\n"; return 0; }
        if (!strcmp(argv[i], "--listen") && i + 1 < argc) { listen_on = argv[++i]; continue; }
        if (!strcmp(argv[i], "--announce-fd") && i + 1 < argc) { announce_fd = atoi(argv[++i]); continue; }
        std::cerr << name << ": unknown option " << argv[i] << " (options: --listen HOST:PORT, --announce-fd N, --version, --help)\n"; return 2;
    }
    if (listen_on.find(':') == std::string::npos) { std::cerr << name << ": --listen takes HOST:PORT\n"; return 2; }
    end_on_term_signals();
    int e = start(listen_on, false);
    if (e) { std::cerr << name << ": cannot listen on " << listen_on << ": " << std::strerror(e) << "\n"; return 1; }
    if (announce_fd >= 0) { std::string p = std::to_string(LISTEN_PORT) + "\n"; ssize_t w = ::write(announce_fd, p.data(), p.size()); (void)w; ::close(announce_fd); }
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
}
}  // namespace ribbit
