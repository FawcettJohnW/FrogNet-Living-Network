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
#include "chunks.hpp"
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
#include <unordered_map>
#ifndef RIBBIT_REDIS_VERSION
#define RIBBIT_REDIS_VERSION "unversioned"
#endif

namespace rr { extern FILE* log_fp; }
using namespace rr;

namespace {
const std::string PATH = "/redis-api?op=redis";
std::unique_ptr<frogram::Session> S;              // the front's one session to the memory
std::unique_ptr<frogram::Memory> M;               // its writes (unanswered) and its one standing read, on that session
std::string ME;                                   // this front's name
pid_t g_host_pid = 0;
void fatal(const std::string& why) { logmsg('#', why); if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(1); }

// ---- a client connection ----------------------------------------------------------------------------------------
struct Conn {
    int fd = -1, ep = -1;
    uint64_t local = 0;
    std::string key;                              // "<front>/<n>": its name in the region
    std::string addr, laddr;                      // carried with its pipelines: the region opens it on its first
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
    static const std::vector<std::string> FO_GONE = {"g"};
    S->post_write_fields("redis.req", ME, "g" + std::to_string(c->local), FO_GONE, {{"g", pyv::Int::of(1)}});   // after its last pipeline: one variable, write order
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

// ---- the replies: the region's cells, read by one standing read [REPLIES_AS_TRUTH_V1] ----------------------------
// f: "<authenticated><0 open | 1 close | 2 parked: more follows>"; r: the replies (bytes as code points)
void replied(uint64_t local, const std::string& f, const std::string& r) {
    std::string out;
    ebr::Guard g;
    auto* x = conns.get(local); Conn* c = x ? x->val : nullptr;
    DIAG("front", "replied", ME + "/" + std::to_string(local), "f=" + f + " bytes=" + std::to_string(r.size()) + " found=" + std::to_string(c != nullptr));
    if (!c) return;                               // hung up meanwhile
    bool close = false;
    if (f.size() != 2 || !text_to_bytes(r, out)) { Reply e; e.error("ERR region: a reply that is not one"); out = e.out; close = true; }
    else { c->authed = f[0] == '1'; close = f[1] == '1'; }
    if (!close && f[1] == '2') { send_out(c, std::move(out)); return; }   // parked: the replies before it now [REPLIES_BEFORE_THE_BLOCK_V1]
    if (c->fatal) { Reply e; e.error("ERR " + c->fatal_err); out += e.out; close = true; }
    c->inflight.store(false, std::memory_order_release);
    // [REPLIES_IN_ORDER_V1] re-armed BEFORE the reply is written: the client's next command can only follow the reply
    if (!close && !c->gone.load()) arm(c, EPOLLIN | EPOLLRDHUP);
    send_out(c, std::move(out), close);
}
void on_replies(std::vector<frogram::RawCell>& rows, const std::string& err) {
    if (!err.empty()) fatal("region: the memory is gone (" + err + "): this front stops");
    static std::unordered_map<std::string, std::string> pieces;   // [PAYLOAD_IN_CELLS_V1] a reply's piece cells so far (this reader's)
    for (auto& row : rows) {                      // the region writes fields: the session decoded them once [DECODE_ONCE_V1]
        ribbit::BagArrP a = row.arr ? row.arr : ribbit::BagArr::parse(row.bag);
        DIAG("front", "row", ME + "/" + row.instance, "id=" + std::to_string(row.id) + " arr=" + std::to_string(row.arr != nullptr) + " fields=" + std::to_string(a ? a->vals.size() : 0));
        if (!a) { logmsg('#', "region: a reply that is not a bag: " + row.instance); continue; }
        const size_t colon = row.instance.find(':');
        if (colon != std::string::npos) { pieces[row.instance.substr(0, colon)] += joined(*a, "r"); continue; }
        std::string r = joined(*a, "r");          // [PAYLOAD_IN_FIELDS_V1]
        auto pc = pieces.find(row.instance); if (pc != pieces.end()) { r = std::move(pc->second) + r; pieces.erase(pc); }
        replied(strtoull(row.instance.c_str(), nullptr, 10), a->str("f"), r);
    }
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
            std::string body; for (auto& a : pr.cmds) body += resp_of(a);
            c->inflight.store(true, std::memory_order_release);
            arm(c, EPOLLRDHUP);
            DIAG("front", "write", c->key, "cmds=" + std::to_string(pr.cmds.size()) + " bytes=" + std::to_string(body.size()));
            // [REPLIES_AS_TRUTH_V1] its truth, written once and not answered: the region's reply is a cell of its own
            // as fields, not JSON text: the session encodes them as they are -- no parse, no template analysis [DECODE_ONCE_V1]
            static ChunkNames FO_REQ({"a", "l"}, "p");   // [PAYLOAD_IN_FIELDS_V1] a pipeline past one wire string: in pieces
            static ChunkNames FO_PIECE({}, "p");
            std::vector<std::string> parts = cells(bytes_to_text(body));   // [PAYLOAD_IN_CELLS_V1] past one frame: piece cells first
            for (size_t i = 0; i + 1 < parts.size(); ++i) {
                std::vector<std::string> ps = cut(parts[i]); const std::vector<std::string>& po = *FO_PIECE.of(ps.size());
                semcodec::Fields pf; for (size_t j = 0; j < ps.size(); ++j) pf.emplace_back(po[j], pyv::Str{std::move(ps[j])});
                S->post_write_fields("redis.req", ME, std::to_string(c->local) + ":" + std::to_string(i), po, std::move(pf));
            }
            std::vector<std::string> pieces = cut(parts.back());
            const std::vector<std::string>& order = *FO_REQ.of(pieces.size());
            semcodec::Fields f; f.reserve(2 + pieces.size());
            f.emplace_back("a", pyv::Str{c->addr}); f.emplace_back("l", pyv::Str{c->laddr});
            for (size_t i = 0; i < pieces.size(); ++i) f.emplace_back(order[2 + i], pyv::Str{std::move(pieces[i])});
            S->post_write_fields("redis.req", ME, std::to_string(c->local), order, std::move(f));
            if (diag_fp()) { static std::atomic<uint64_t> seen{0}; const auto st = S->stats(); const uint64_t r = st.posts_refused;
                if (r != seen.exchange(r)) DIAG("front", "REFUSED", c->key, "posts_refused=" + std::to_string(r) + " body=" + std::to_string(body.size())); }
        }
    }
}
void accept_conn(int fd, const std::string& addr, const std::string& laddr) {
    auto* c = new Conn; c->fd = fd; c->local = ++next_local; c->key = ME + "/" + std::to_string(c->local); c->addr = addr; c->laddr = laddr;
    c->authed = g.requirepass.empty();
    static std::atomic<uint32_t> deal{0};
    c->ep = loops[deal.fetch_add(1, std::memory_order_relaxed) % loops.size()];
    conns.add(c->local, c);
    epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.u64 = c->local;
    epoll_ctl(c->ep, EPOLL_CTL_ADD, fd, &ev);
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
    M.reset(new frogram::Memory(*S, "/redis-api"));
    M->follow("redis.rep", ME, "", -1, on_replies);              // the one standing read: every reply, as it is written
    try { S->call("POST", PATH, "h " + ME); }                     // its one call: this session is this front's
    catch (const std::exception& e) { fatal(std::string("region: the memory refused this front (") + e.what() + ")"); }
    // the region's configuration is this front's when it started the memory (the suite's case), or when it is told to
    if (!ram || getenv("RIBBIT_REDIS_CONFIG_OWNER")) {
        Argv kv; for (auto& [k, v] : g.cfg.v) { kv.push_back(k); kv.push_back(v); }
        static const std::vector<std::string> FO_CFG = {"p"};
        S->post_write_fields("redis.req", ME, "x", FO_CFG, {{"p", pyv::Str{bytes_to_text(resp_of(kv))}}});   // before its first pipeline
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
