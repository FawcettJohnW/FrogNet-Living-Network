// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// front.cpp -- a Redis front [ONE_KEYSPACE_V1] (DESIGN.md).
//
// Speaks RESP to stock clients and holds nothing of the keyspace: the keyspace is in the memory, once. Each pipeline a
// client sends is one call on the front's session (region.hpp); its answer is the replies. A connection's next bytes are
// read only after its answer (Redis's order for one connection); while a call is out the socket is watched for hang-ups
// only. A connection killed by another (CLIENT KILL) while it has no call out is a cell the front holds a read on.
//
//   RIBBIT_RAM=host:port   the memory (another node). Not set: the front starts one of its own on a loopback port and
//                          stops it on exit -- a fresh region per server, which is what Redis's suite expects.
//   RIBBIT_REDIS_HOST      the memory binary for the self-started case (default: ribbit-redis-host beside this one)
//   RIBBIT_REDIS_CONFIG_OWNER=1   this front's configuration is the region's (the front that starts its memory: always)
//   RIBBIT_FRONT_LOOPS     event loops reading clients (default: half the cores)
#include "server.hpp"
#include "frogram.hpp"
#include "ribbit_space.hpp"
#include "bytes_text.hpp"
#include "diag.hpp"
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/epoll.h>
#include <poll.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <cstring>
#include <cerrno>
#include <thread>
#include <atomic>
#ifndef RIBBIT_REDIS_VERSION
#define RIBBIT_REDIS_VERSION "unversioned"
#endif

namespace rr { extern FILE* log_fp; }
using namespace rr;

namespace {
const std::string PATH = "/redis-api?op=redis";
std::unique_ptr<frogram::Session> S;              // the front's session: its calls, in order
std::unique_ptr<ribbit::Space> SP;               // its read of the kills the region wrote for it
std::string ME;                                   // this front's name
pid_t g_host_pid = 0;
void fatal(const std::string& why) { logmsg('#', why); if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(1); }

// ---- a client connection ----------------------------------------------------------------------------------------
struct Conn {
    int fd = -1, ep = -1;
    uint64_t local = 0;
    std::string key;                              // "<front>/<n>": its name in the region
    RespParser parser;
    std::atomic<bool> authed{true};
    std::atomic<bool> inflight{false};            // a call is out: the socket is watched for hang-ups only
    std::atomic<bool> gone{false};
    bool fatal = false; std::string fatal_err;    // a protocol error after the pipeline in flight
    // [REPLIES_IN_ORDER_V1] what is still to be written to the client, in order; whoever holds `sending` writes
    struct Chunk { std::atomic<Chunk*> next{nullptr}; std::string b; size_t off = 0; bool close = false; };
    Chunk stub; Chunk* ohead = &stub; std::atomic<Chunk*> otail{&stub}; std::atomic<bool> sending{false};
    std::atomic<int> refs{1};                     // the table holds one; a writing thread holds one while it waits
    ~Conn() { if (fd >= 0) ::close(fd); for (Chunk* h = ohead; h;) { Chunk* n = h->next.load(); if (h != &stub) delete h; h = n; } }
};
void release(Conn* c) { if (c->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) ebr::retire(c); }
LFMap<uint64_t, Conn*> conns;
std::atomic<uint64_t> next_local{0};
std::vector<int> loops;

void arm(Conn* c, uint32_t events) { epoll_event ev{}; ev.events = events | EPOLLONESHOT; ev.data.u64 = c->local; epoll_ctl(c->ep, EPOLL_CTL_MOD, c->fd, &ev); }
void gone(Conn* c) {                              // the client hung up, or was closed: it leaves the region and this table
    if (c->gone.exchange(true)) return;
    DIAG("front", "gone", c->key, "inflight=" + std::to_string(c->inflight.load()));
    S->post("POST", PATH, bytes_to_text("g " + c->key));
    Conn* out = nullptr;
    if (conns.remove(c->local, &out) && out) { epoll_ctl(out->ep, EPOLL_CTL_DEL, out->fd, nullptr); release(out); }
}
void pump(Conn* c) {
    for (;;) {
        if (c->sending.exchange(true, std::memory_order_acq_rel)) return;
        for (;;) {
            Conn::Chunk* h = c->ohead; Conn::Chunk* nx = h->next.load(std::memory_order_acquire);
            if (!nx) break;
            while (nx->off < nx->b.size()) {
                ssize_t n = ::send(c->fd, nx->b.data() + nx->off, nx->b.size() - nx->off, MSG_NOSIGNAL | MSG_DONTWAIT);
                if (n > 0) { nx->off += size_t(n); continue; }
                if (n < 0 && errno == EINTR) continue;
                if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {     // the socket is full: a thread waits for room
                    c->refs.fetch_add(1, std::memory_order_acq_rel);
                    std::thread([c] { pollfd p{c->fd, POLLOUT, 0}; while (::poll(&p, 1, -1) < 0 && errno == EINTR) {}
                                      c->sending.store(false, std::memory_order_release); pump(c); release(c); }).detach();
                    return;
                }
                nx->off = nx->b.size();          // the peer is gone: its hang-up arrives on the loop
            }
            if (nx->close) { ::shutdown(c->fd, SHUT_RDWR); gone(c); }
            c->ohead = nx; nx->b.clear(); nx->b.shrink_to_fit();
            if (h != &c->stub) delete h;
        }
        c->sending.store(false, std::memory_order_seq_cst);
        if (!c->ohead->next.load(std::memory_order_seq_cst)) return;
    }
}
void send_out(Conn* c, std::string bytes, bool close = false) {
    if (bytes.empty() && !close) return;
    auto* k = new Conn::Chunk; k->b = std::move(bytes); k->close = close;
    Conn::Chunk* prev = c->otail.exchange(k, std::memory_order_acq_rel);
    prev->next.store(k, std::memory_order_release);
    pump(c);
}

// ---- the call: one pipeline, answered with its replies ----------------------------------------------------------
void answered(uint64_t local, int status, const std::string& text, const std::string& err) {
    if (!err.empty()) fatal("region: the memory is gone (" + err + "): this front stops");
    std::string body; if (status < 400 && !text_to_bytes(text, body)) { status = 502; body = "the answer is not bytes as code points"; }
    if (status >= 400) body = text;
    ebr::Guard g;
    auto* x = conns.get(local); Conn* c = x ? x->val : nullptr;
    DIAG("front", "answered", ME + "/" + std::to_string(local), "status=" + std::to_string(status) + " bytes=" + std::to_string(text.size()) + " found=" + std::to_string(c != nullptr) + " head=" + text.substr(0, 2));
    if (!c) return;                               // hung up meanwhile
    std::string out; bool close = false, more = false;
    if (status >= 400 || body.size() < 3 || body[2] != '\n') { Reply e; e.error("ERR region: " + body.substr(0, 200)); out = e.out; close = true; }
    else { c->authed = body[0] == '1'; close = body[1] == '1'; more = body[1] == '2'; out = body.substr(3); }
    if (more) {                                   // parked: the replies before it now, and a held read for the rest
        S->call_async("POST", PATH, bytes_to_text("w " + c->key), [local](int st, std::string b, std::string er) { answered(local, st, b, er); });   // [REPLIES_BEFORE_THE_BLOCK_V1]
        send_out(c, std::move(out));
        return;
    }
    if (c->fatal) { Reply e; e.error("ERR " + c->fatal_err); out += e.out; close = true; }
    c->inflight.store(false, std::memory_order_release);
    // [REPLIES_IN_ORDER_V1] re-armed BEFORE the reply is written: the client's next command can only follow the reply
    if (!close && !c->gone.load()) arm(c, EPOLLIN | EPOLLRDHUP);
    send_out(c, std::move(out), close);
}
std::string resp_of(const Argv& a) {
    std::string o = "*" + std::to_string(a.size()) + "\r\n";
    for (auto& x : a) { o += "$" + std::to_string(x.size()) + "\r\n"; o += x; o += "\r\n"; }
    return o;
}

// ---- clients ----------------------------------------------------------------------------------------------------
void event_loop(int ep) {
    epoll_event evs[512]; char buf[65536];
    for (;;) {
        int n = epoll_wait(ep, evs, 512, -1);
        if (n < 0) { if (errno == EINTR) continue; fatal(std::string("epoll_wait: ") + strerror(errno)); }
        for (int k = 0; k < n; k++) {
            ebr::Guard epoch;
            auto* x = conns.get(evs[k].data.u64); Conn* c = x ? x->val : nullptr;
            if (!c) continue;
            if (c->inflight.load(std::memory_order_acquire)) {          // armed for hang-ups only
                DIAG("front", "event-inflight", c->key, "events=" + std::to_string(evs[k].events));
                if (evs[k].events & (EPOLLRDHUP | EPOLLHUP | EPOLLERR)) gone(c);
                continue;
            }
            ssize_t r = ::recv(c->fd, buf, sizeof buf, MSG_DONTWAIT);
            if (r < 0 && (errno == EAGAIN || errno == EINTR)) { arm(c, EPOLLIN | EPOLLRDHUP); continue; }
            if (r <= 0) { gone(c); continue; }
            c->parser.authenticated = c->authed.load();
            c->parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
            c->parser.feed(buf, size_t(r));
            ParseResult pr = c->parser.drain();
            if (pr.cmds.empty()) {
                if (pr.fatal) { Reply e; e.error("ERR " + pr.err); send_out(c, e.out, true); }
                else arm(c, EPOLLIN | EPOLLRDHUP);   // a partial command: wait for the rest
                continue;
            }
            c->fatal = pr.fatal; c->fatal_err = pr.err;
            std::string body = "p " + c->key + "\n"; for (auto& a : pr.cmds) body += resp_of(a);
            c->inflight.store(true, std::memory_order_release);
            arm(c, EPOLLRDHUP);
            const uint64_t local = c->local;
            DIAG("front", "call", c->key, "cmds=" + std::to_string(pr.cmds.size()) + " bytes=" + std::to_string(body.size()));
            S->call_async("POST", PATH, bytes_to_text(body), [local](int status, std::string b, std::string err) { answered(local, status, b, err); });
        }
    }
}
void accept_conn(int fd, const std::string& addr, const std::string& laddr) {
    auto* c = new Conn; c->fd = fd; c->local = ++next_local; c->key = ME + "/" + std::to_string(c->local);
    c->authed = g.requirepass.empty();
    static std::atomic<uint32_t> deal{0};
    c->ep = loops[deal.fetch_add(1, std::memory_order_relaxed) % loops.size()];
    conns.add(c->local, c);
    S->post("POST", PATH, bytes_to_text("o " + c->key + " " + addr + " " + laddr));   // before anything it sends: one session, one order
    epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.u64 = c->local;
    epoll_ctl(c->ep, EPOLL_CTL_ADD, fd, &ev);
}
// a connection the region killed while it had no call out: closed here
void on_kills(std::vector<ribbit::SpaceRow>& rows) {
    for (auto& r : rows) {
        ebr::Guard g;
        auto* x = conns.get(strtoull(r.instance.c_str(), nullptr, 10));
        if (x && x->val) ::shutdown(x->val->fd, SHUT_RDWR);   // its hang-up arrives on the loop
    }
}

// A memory of its own, on a loopback port, when none is named: it dies with this process (PR_SET_PDEATHSIG).
std::string start_own_memory(const std::string& self) {
    int s = ::socket(AF_INET, SOCK_STREAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ::bind(s, (sockaddr*)&a, sizeof a); socklen_t l = sizeof a; getsockname(s, (sockaddr*)&a, &l); int port = ntohs(a.sin_port); ::close(s);
    const char* hb = getenv("RIBBIT_REDIS_HOST");
    std::string bin = hb ? hb : self.substr(0, self.rfind('/') + 1) + "ribbit-redis-host";
    std::string listen = "127.0.0.1:" + std::to_string(port);
    g_host_pid = fork();
    if (g_host_pid == 0) {
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        int dn = open("/dev/null", O_WRONLY); if (dn >= 0) { dup2(dn, 1); dup2(dn, 2); }
        execl(bin.c_str(), bin.c_str(), "--listen", listen.c_str(), (char*)nullptr);
        _exit(127);
    }
    return listen;
}
void on_term(int) { if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(0); }
}  // namespace

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    g.executable = argv[0];
    int from = 1;
    if (argc > 1 && argv[1][0] != '-') { g.config_file = argv[1]; from = 2; }
    g.cfg.init_defaults();
    { char cwd[4096]; if (getcwd(cwd, sizeof cwd)) g.cfg.set("dir", cwd); }   // Redis: dir is the working directory at startup
    if (!g.config_file.empty()) g.cfg.load_file(g.config_file);
    g.cfg.apply_args(argc, argv, from);
    g.port = atoi(g.cfg.get("port").c_str());
    g.requirepass = g.cfg.get("requirepass");
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    std::string lf = g.cfg.get("logfile");
    if (!lf.empty()) { FILE* f = fopen(lf.c_str(), "a"); if (f) log_fp = f; }
    {   char h[256] = {0}; gethostname(h, sizeof h - 1); unsigned char r[4]; FILE* u = fopen("/dev/urandom", "rb");
        if (!u || fread(r, 1, 4, u) != 4) fatal("no /dev/urandom");
        fclose(u);
        char x[16]; snprintf(x, sizeof x, "%02x%02x%02x%02x", r[0], r[1], r[2], r[3]);
        ME = std::string(h) + "." + std::to_string(getpid()) + "." + x; }
    const char* ram = getenv("RIBBIT_RAM");
    std::string ep = ram ? ram : start_own_memory(argv[0]);
    signal(SIGTERM, on_term); signal(SIGINT, on_term);
    const size_t colon = ep.rfind(':');
    if (colon == std::string::npos) fatal("RIBBIT_RAM is host:port, not " + ep);
    for (int tries = 0; !S; tries++) {           // a memory started here needs a moment to listen; a named one must answer
        try { S.reset(new frogram::Session(ep.substr(0, colon), atoi(ep.c_str() + colon + 1), "/redis-api")); }
        catch (const std::exception& e) {
            if (ram || tries > 300) fatal(std::string("Cannot reach the memory ") + ep + ": " + e.what());
            usleep(10000);
        }
    }
    try { SP.reset(new ribbit::Space(ep, "redis")); }
    catch (const std::exception& e) { fatal(std::string("Cannot reach the memory ") + ep + ": " + e.what()); }
    SP->watch("redis.kill", ME, -1, on_kills);
    // the region's configuration is this front's when it started the memory (the suite's case), or when it is told to
    if (!ram || getenv("RIBBIT_REDIS_CONFIG_OWNER")) {
        Argv kv; for (auto& [k, v] : g.cfg.v) { kv.push_back(k); kv.push_back(v); }
        S->post("POST", PATH, bytes_to_text("x\n" + resp_of(kv)));
    }
    logmsg('*', "Redis Ribbit front " + ME + " (" RIBBIT_REDIS_VERSION "), Redis compatibility 7.2.11, PID: " + std::to_string(getpid()) + ", memory " + ep + " [ONE_KEYSPACE_V1]");
    logmsg('*', "Running mode=standalone, port=" + std::to_string(g.port) + ".");
    logmsg('#', "Server initialized");
    int lfd = ::socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa{}; sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)g.port); sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    { std::string b = g.cfg.get("bind"); if (b.find('*') != std::string::npos || b.find("0.0.0.0") != std::string::npos || b.empty()) sa.sin_addr.s_addr = htonl(INADDR_ANY); }
    if (::bind(lfd, (sockaddr*)&sa, sizeof sa) < 0 || ::listen(lfd, 511) < 0) fatal("Failed listening on port " + std::to_string(g.port) + " (tcp), aborting.");
    {   const char* e = getenv("RIBBIT_FRONT_LOOPS");
        int n = e ? atoi(e) : (int)std::thread::hardware_concurrency() / 2; if (n < 1) n = 1;
        for (int i = 0; i < n; i++) { int l = epoll_create1(EPOLL_CLOEXEC); loops.push_back(l); std::thread(event_loop, l).detach(); }
    }
    logmsg('*', "Ready to accept connections tcp");
    for (;;) {
        sockaddr_in p{}; socklen_t pl = sizeof p;
        int fd = ::accept(lfd, (sockaddr*)&p, &pl);
        if (fd < 0) continue;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip);
        std::string addr = std::string(ip) + ":" + std::to_string(ntohs(p.sin_port));
        sockaddr_in la{}; socklen_t ll = sizeof la; getsockname(fd, (sockaddr*)&la, &ll); inet_ntop(AF_INET, &la.sin_addr, ip, sizeof ip);
        accept_conn(fd, addr, std::string(ip) + ":" + std::to_string(ntohs(la.sin_port)));
    }
}
