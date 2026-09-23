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
std::string urldec(const std::string& s) { std::string o; for (size_t i = 0; i < s.size(); ++i) { if (s[i] == '%' && i + 2 < s.size()) { o += char(std::stoi(s.substr(i + 1, 2), nullptr, 16)); i += 2; } else o += (s[i] == '+' ? ' ' : s[i]); } return o; }
std::map<std::string, std::string> query(const std::string& path) { std::map<std::string, std::string> q; size_t p = path.find('?'); if (p == std::string::npos) return q;
    std::stringstream ss(path.substr(p + 1)); std::string kv; while (std::getline(ss, kv, '&')) { size_t e = kv.find('='); q[urldec(kv.substr(0, e))] = e == std::string::npos ? "" : urldec(kv.substr(e + 1)); } return q; }

// ---- the memory
struct Cell { uint64_t id; std::string service, variable, instance, bag; double updated; };
typedef std::tuple<std::string, std::string, std::string> Addr;
struct Memory {
    // cells by address; and two indexes in the memory's own write order, so that
    // "everything under this variable written since N" and "remove id N" cost
    // what they return, not what the memory holds. (tools/mesh found the need: with
    // 400,000 cells a linear scan per read and per remove starved the readers.)
    std::mutex m; std::condition_variable cv; std::map<Addr, Cell> cells; uint64_t next_id = 0;
    std::map<uint64_t, Addr> by_id; std::map<std::pair<std::string, std::string>, std::map<uint64_t, Addr>> by_var;
    void unindex(const Cell& c) { by_id.erase(c.id); auto it = by_var.find(std::make_pair(c.service, c.variable)); if (it != by_var.end()) { it->second.erase(c.id); if (it->second.empty()) by_var.erase(it); } }
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) {
        std::lock_guard<std::mutex> g(m); Addr a(s, v, i); auto it = cells.find(a); if (it != cells.end()) unindex(it->second);
        Cell& c = cells[a]; c.id = ++next_id; c.service = s; c.variable = v; c.instance = i; c.bag = bag; c.updated = wall();
        by_id[c.id] = a; by_var[std::make_pair(s, v)][c.id] = a; cv.notify_all(); return c.id; }
    size_t remove(uint64_t id) { std::lock_guard<std::mutex> g(m); auto it = by_id.find(id); if (it == by_id.end()) return 0;
        auto c = cells.find(it->second); Cell copy = c->second; cells.erase(c); unindex(copy); return 1; }
    std::vector<Cell> match(const std::map<std::string, std::string>& q, long long after, int fresh) {       // m held
        std::vector<Cell> out; auto s = q.find("service"), v = q.find("variable"), i = q.find("instance"); double cutoff = wall() - fresh;
        bool has_v = v != q.end() && !v->second.empty(), has_i = i != q.end() && !i->second.empty();
        auto take = [&](const Cell& c) { if (has_i && c.instance != i->second) return; if (after >= 0 && (long long)c.id <= after) return; if (fresh > 0 && c.updated < cutoff) return; out.push_back(c); };
        if (has_v && has_i) { auto c = cells.find(Addr(s->second, v->second, i->second)); if (c != cells.end()) take(c->second); return out; }
        if (has_v) { auto bv = by_var.find(std::make_pair(s->second, v->second)); if (bv == by_var.end()) return out;
            for (auto it = after >= 0 ? bv->second.upper_bound(uint64_t(after)) : bv->second.begin(); it != bv->second.end(); ++it) take(cells.find(it->second)->second);
            return out; }                                                       // already in write order
        for (auto it = cells.lower_bound(Addr(s->second, "", "")); it != cells.end() && std::get<0>(it->first) == s->second; ++it) take(it->second);
        std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; }); return out; }
} MEM;

std::string ok_rows(const std::vector<Cell>& rows) { std::string o = "{\"ok\":true,\"rows\":[";
    for (size_t k = 0; k < rows.size(); ++k) { const Cell& c = rows[k]; char u[40]; snprintf(u, sizeof u, "%.3f", c.updated);
        o += (k ? "," : "") + std::string("{\"id\":") + std::to_string(c.id) + ",\"service\":" + Json::quote(c.service) + ",\"variable\":" + Json::quote(c.variable) +
             ",\"instance\":" + Json::quote(c.instance) + ",\"bag\":" + c.bag + ",\"updated_epoch\":" + u + "}"; }
    return o + "]}"; }
struct Reply { int status; std::string body; };
Reply bad(int code, const std::string& msg) { return Reply{code, "{\"ok\":false,\"error\":" + Json::quote(msg) + "}"}; }

// Runs one API request. Returns false if it must WAIT (a blocking read not yet satisfied);
// the caller then parks it on its own thread by calling again with may_wait = true.
bool api(const std::string& method, const std::string& path, const std::string& body, bool may_wait, Reply& out) {
    if (path.compare(0, 8, "/ram.php")) { out = bad(403, "not this service's API"); return true; }
    auto q = query(path); const std::string op = q["op"];
    if (op == "write") {
        if (method != "POST") { out = bad(405, "write is POST"); return true; }
        Json in; try { in = Json::parse(body); } catch (const std::exception&) { out = bad(400, "body is not a JSON object"); return true; }
        for (const char* k : {"service", "variable", "instance"}) if (in[k].type != Json::Str || in[k].s.empty()) { out = bad(400, std::string("missing coordinate: ") + k); return true; }
        if (in["bag"].type != Json::Obj) { out = bad(400, "bag must be a JSON object"); return true; }
        out = Reply{200, "{\"ok\":true,\"id\":" + std::to_string(MEM.write(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]))) + "}"}; return true;
    }
    if (op == "read") {
        if (method != "GET") { out = bad(405, "read is GET"); return true; }
        if (q["service"].empty()) { out = bad(400, "missing coordinate: service"); return true; }
        long long after = q.count("after") ? atoll(q["after"].c_str()) : -1; int fresh = q.count("fresh_s") ? atoi(q["fresh_s"].c_str()) : 0;
        double wait = q.count("wait_s") ? atof(q["wait_s"].c_str()) : 0; size_t min_rows = q.count("min_rows") ? size_t(atol(q["min_rows"].c_str())) : 0;
        if (wait > WAIT_MAX_S) wait = WAIT_MAX_S;
        if (after >= 0 && min_rows == 0) min_rows = 1;                        // "something newer than what I hold"
        std::unique_lock<std::mutex> l(MEM.m); std::vector<Cell> rows = MEM.match(q, after, fresh);
        if (wait > 0 && min_rows > 0 && rows.size() < min_rows) {
            if (!may_wait) return false;
            auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait);
            while (rows.size() < min_rows && MEM.cv.wait_until(l, deadline) != std::cv_status::timeout) rows = MEM.match(q, after, fresh);
            if (rows.size() < min_rows) rows = MEM.match(q, after, fresh);      // expiry: what is there now, short of what was asked
        }
        out = Reply{200, ok_rows(rows)}; return true;
    }
    if (op == "remove") {
        if (method != "DELETE") { out = bad(405, "remove is DELETE"); return true; }
        long long id = atoll(q["id"].c_str()); if (id <= 0) { out = bad(400, "missing id"); return true; }
        out = Reply{200, "{\"ok\":true,\"removed\":" + std::to_string(MEM.remove(uint64_t(id))) + "}"}; return true;
    }
    out = bad(400, "unknown op: " + op); return true;
}

// ---- the wire's request cache: memory only, bounded by entries AND by size
struct Cached { std::string req, body_hash, same_id; std::list<std::string>::iterator lru; };
std::mutex cache_m; std::map<std::string, Cached> cache; std::list<std::string> cache_lru;

struct Session {
    int req = -1, ret = -1; std::mutex send_m; std::mutex ready_m; std::condition_variable ready_cv; bool ready = false;
    void reply(uint32_t seq, const std::string& frame) { std::lock_guard<std::mutex> g(send_m); send_frame(ret, be32(seq) + frame); }
};
std::mutex pend_m; std::map<std::string, std::shared_ptr<Session>> pending;

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
void handle_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {
    uint8_t op = uint8_t(f[4]); if (f.size() < 21) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "short frame"); return; }
    std::string h = f.substr(5, 16), http_req; Cached old; bool have_old = false;
    if (op == REQ_RAW) { if (f.size() < 25 || f.size() != 25 + rd32(f, 21)) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "short REQ_RAW"); return; } http_req = f.substr(25); }
    else if (op == REQ_REPEAT) { std::lock_guard<std::mutex> g(cache_m); auto it = cache.find(h);
        if (it == cache.end()) { s->reply(seq, std::string(MAGIC, 4) + char(REQ_MISS) + h); return; }
        old = it->second; have_old = true; http_req = old.req; cache_lru.splice(cache_lru.end(), cache_lru, it->second.lru); }
    else { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "op not spoken here"); return; }
    Json q; try { q = Json::parse(http_req); } catch (const std::exception&) { s->reply(seq, std::string(MAGIC, 4) + char(OP_ERROR) + be16(400) + "bad request"); return; }
    std::string method = q["method"].s, path = q["path"].s, body = q["body"].s; Reply r;
    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }
    std::thread([=] { Reply rr; Cached o = old; api(method, path, body, true, rr); s->reply(seq, wire_reply(h, http_req, have_old ? &o : nullptr, rr)); }).detach();   // a parked read
}
void serve(int fd, std::string peer) {
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one); setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    std::string f; if (!recv_frame(fd, f) || uint8_t(f[4]) != OP_HELLO || f.size() < 6) { FR_CLOSE(fd); return; }
    std::string token = f.substr(6, uint8_t(f[5]));
    if (!token.compare(0, 7, "RETURN:")) { token = token.substr(7); std::shared_ptr<Session> s;
        for (int i = 0; i < 200 && !s; ++i) { { std::lock_guard<std::mutex> g(pend_m); auto it = pending.find(token); if (it != pending.end()) { s = it->second; pending.erase(it); } } if (!s) std::this_thread::sleep_for(std::chrono::milliseconds(50)); }
        if (!s) { FR_CLOSE(fd); return; }
        s->ret = fd; send_frame(fd, std::string(MAGIC, 4) + char(OP_HELLO) + char(3) + "ram");
        { std::lock_guard<std::mutex> g(s->ready_m); s->ready = true; } s->ready_cv.notify_all(); return; }
    auto s = std::make_shared<Session>(); s->req = fd; { std::lock_guard<std::mutex> g(pend_m); pending[token] = s; }
    { std::unique_lock<std::mutex> l(s->ready_m); if (!s->ready_cv.wait_for(l, std::chrono::seconds(10), [&] { return s->ready; })) { std::lock_guard<std::mutex> g(pend_m); pending.erase(token); FR_CLOSE(fd); return; } }
    logline("session up   " + peer + " " + token); uint32_t seq = 0;
    while (recv_frame(fd, f)) handle_frame(s, seq++, f);
    logline("session down " + peer + " " + token + " after " + std::to_string(seq) + " requests");
    ::shutdown(s->ret, FR_SHUT_RDWR); FR_CLOSE(fd);
}
}  // namespace

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
    logline("FNW1 and the memory, in one process, on " + listen_on);
    std::thread([srv] { for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl); if (fd < 0) continue;
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip); std::thread(serve, fd, std::string(ip)).detach(); } }).detach();
    return 0;
}

#ifndef FROGBENCH_EMBED
int main(int argc, char** argv) {
    std::string listen_on = "0.0.0.0:8788";
    for (int i = 1; i + 1 < argc; i += 2) if (!strcmp(argv[i], "--listen")) listen_on = argv[i + 1];
    int e = ram_server_start(listen_on, false);
    if (e) { std::cerr << "cannot listen on " << listen_on << ": " << std::strerror(e) << "\n"; return 1; }
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
}
#endif
