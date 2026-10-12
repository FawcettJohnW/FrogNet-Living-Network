// librina_region.cpp -- rlite's rina/api.h on the RINA region (the Redis server model).
//
// Every conversation is ONE call on the program's one permanent session; nothing is ever re-asked:
//   register, unregister, alloc, accept/respond     one call each (op=rina)
//   send                                            one posted call per batch of every SDU queued on a flow's fd
//   wait                                            THE held read: one in flight per program, always; it returns
//                                                   everything addressed to the program -- flow requests, verdicts,
//                                                   SDUs, credit -- the moment there is any (op=rina_block)
// No polling, no timers, no locks: the program's state is append-only lists and atomics; a flow's sender waits for
// credit on its own atomic (C++20 wait/notify), woken by the event thread when credit arrives in the stream.
// rlite's contract: a flow is a SOCK_SEQPACKET fd the program read()s and write()s.
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <pthread.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/syscall.h>
#include <linux/futex.h>
#include <unistd.h>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <mutex>
#include <execinfo.h>
#include <map>
#include <thread>
#include <vector>
#include "rina/api.h"
#include "rina_region_client.hpp"
#include "rmt.hpp"
#include "ebr.hpp"
#include "machine_id.hpp"
using frogram::Json;

namespace {
constexpr uint64_t RING = 4096;                       // = the region's RING: unconsumed SDUs a flow direction may hold

// [FORK_KEEPS_REGISTRATIONS_V1] a forked child's proc id is minted by the PARENT before the fork (the child takes it in
// the fork's child handler), so the parent can tell the region "this child holds my input spaces too" before it can exit
std::string g_child_proc;                                // set in the parent just before fork(); copied into the child
std::string& proc_id_slot() { static std::string id; return id; }
std::atomic<pid_t>& proc_id_pid() { static std::atomic<pid_t> p{0}; return p; }
std::string proc_id() {                               // this process (a fork is a new program)
    if (proc_id_pid().load() != getpid()) { std::random_device r; char b[48]; snprintf(b, sizeof b, "p%d-%08x", getpid(), r()); proc_id_slot() = b; proc_id_pid().store(getpid()); }
    return proc_id_slot();
}
std::string uniq() { static std::atomic<unsigned> n{0}; std::random_device r; char b[64]; snprintf(b, sizeof b, "%d-%u-%08x", getpid(), ++n, r()); return b; }
std::string netns() { return rina_id::machine_id(); }   // [MACHINE_IDENTITY_V1] the machine, by GUID (the field keeps its old name, "ns")
std::string q(const std::string& s) { return Json::quote(s); }
// [BYTES_AS_TEXT_V1] An SDU's bytes travel as a JSON string, not base64: byte b is the code point U+00b (Latin-1 in
// UTF-8), which the semantic wire carries as text. Bytes below 0x80 -- most protocol bytes, every zero -- are one
// byte on the wire; the rest two. Lossless both ways.
std::string to_text(const char* p, size_t n) {
    std::string o; o.reserve(n + n / 8);
    for (size_t i = 0; i < n; ++i) { const unsigned char c = uint8_t(p[i]); if (c < 0x80) o += char(c); else { o += char(0xC0 | (c >> 6)); o += char(0x80 | (c & 0x3F)); } }
    return o;
}
std::string from_text(const std::string& t) {
    std::string o; o.reserve(t.size());
    for (size_t i = 0; i < t.size(); ++i) { const unsigned char c = uint8_t(t[i]); if (c < 0x80) o += char(c); else if (i + 1 < t.size()) { o += char(((c & 0x03) << 6) | (uint8_t(t[i + 1]) & 0x3F)); ++i; } }
    return o;
}
std::string field(const std::string& bag, const std::string& k) {
    const std::string kk = k + "="; size_t a = bag.find(kk);
    while (a != std::string::npos && a != 0 && bag[a - 1] != ';') a = bag.find(kk, a + 1);
    if (a == std::string::npos) return "";
    a += kk.size(); return bag.substr(a, bag.find(';', a) - a);
}

// ---- the program's state: append-only lists, read lock-free ----------------------------------------------------
struct Inbox { std::string id; int app, lib; Inbox* next; };
// [FLOW_DELIVERY_V1] each flow hands its SDUs to the program on ITS OWN thread: the event thread queues them (lock-free
// MPSC) and moves on, so a program not reading one flow (blocked on another, as rinaperf's server waits on its control
// flow) cannot stop the SDUs of every other flow behind it. Before, the one event thread blocked in send() on a full
// flow and the program waited for a control SDU queued behind it -- a deadlock until rinaperf's 5 s timeout.
struct SduNode { std::atomic<SduNode*> next{nullptr}; std::string d; };
struct SduList {
    SduNode stub; std::atomic<SduNode*> head{&stub}; SduNode* tail = &stub;
    void push(SduNode* n) { n->next.store(nullptr, std::memory_order_relaxed); SduNode* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
    SduNode* pop() {
        SduNode* t = tail; SduNode* next = t->next.load(std::memory_order_acquire);
        if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { tail = next; return t; }
        if (t != head.load(std::memory_order_acquire)) return nullptr;
        stub.next.store(nullptr, std::memory_order_relaxed); SduNode* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
        next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
        return nullptr;
    }
};
struct Flow { std::string fid; int lib; std::atomic<uint64_t> sent{0}, consumed{0}; Flow* next;
              uint64_t next_q = 0; std::map<uint64_t, std::string> early;   // [FLOW_ORDER_V1] the event thread's only
              // [SDU_BY_REFERENCE_V1] the SDUs given to the program on this flow, by the bytes, with the cell each lives in:
              // a program that writes back what it read (an echo, a forward) sends a reference to that cell, not a copy.
              // A small ring the event thread fills and the sender reads; each entry published whole (seq odd while written).
              struct Given { std::atomic<uint64_t> seq{0}; uint64_t q = 0, id = 0; uint32_t len = 0; char d[2048]; };   // plain bytes: safe to read under the seq
              Given given[8]; std::atomic<uint64_t> given_n{0};
              std::map<uint64_t, uint64_t> early_id;
              SduList out; std::atomic<uint32_t> out_n{0}; bool deliverer = false;   // [FLOW_DELIVERY_V1]
              std::atomic<uint64_t> queued_n{0}, sent_ok{0}, send_err{0}; std::atomic<int> last_errno{0};   // [WIRE_STATS_V1] delivery counts
              // [MEMBER_TO_MEMBER_DATA_V1] this end's port id; the peer's endpoint and port, learned in the allocation exchange
              uint32_t port = 0; rmt::Addr peer{}; std::atomic<bool> linked{false};   // [RMT_OVER_DIF_LINKS_V1] the peer, by DIF, machine, program, port
              std::atomic<uint64_t> credit_q{0};               // the last quarter we told the sender about
              std::atomic<uint64_t> rx_n{0}, rx_gap{0}; uint64_t rx_next = 0;
              // [RELIABLE_FLOWS_V1] max_sdu_gap 0: the sender keeps each SDU until its receiver states it took it, and
              // re-sends on a route change; the receiver hands SDUs over strictly in sequence and drops duplicates
              bool reliable = false;
              std::atomic<const std::string*>* kept = nullptr;  // RING slots: the SDU sent as seq, kept by its sender
              uint32_t via = 0;                                   // [PRECISE_RESEND_V1] the next machine toward the peer, last told
              std::atomic<uint64_t> kept_seq{0};                // how many SDUs have been sent (and kept)
              uint64_t rx_expect = 0; std::map<uint64_t, std::string> rx_early; std::atomic<uint64_t> rx_dup{0}, resent{0}; };   // arrivals; arrivals out of sequence
struct Alloc { std::string fid; int lib; std::atomic<bool> done{false}; Alloc* next; };
struct Held { int handle; std::string req, fid, inbox, dst; std::atomic<bool> done{false}; Held* next; };
std::atomic<Inbox*> g_inboxes{nullptr}; std::atomic<Flow*> g_flows{nullptr}; std::atomic<Alloc*> g_allocs{nullptr}; std::atomic<Held*> g_held{nullptr};

// ---- [MEMBER_TO_MEMBER_DATA_V1] [RMT_OVER_DIF_LINKS_V1] the data plane: this program's endpoint, its flows' ports,
// delivery and credit. The endpoint is an abstract unix datagram socket "rina-ep-<pid>" on this machine: PDUs to an end
// on the same machine go straight to that program's endpoint; all others go to this machine's agent (the RMT), which
// relays them over the DIF's links.
constexpr uint32_t PORTS = 1u << 16;
std::atomic<Flow*>* g_ports() { static std::atomic<Flow*>* t = [] { auto* a = new std::atomic<Flow*>[PORTS]; for (uint32_t i = 0; i < PORTS; ++i) a[i].store(nullptr); return a; }(); return t; }
std::atomic<uint32_t> g_next_port{1};
uint32_t mint_port(Flow* f) {                             // a port id for this flow end, unique in this program
    for (uint32_t k = 0; k < PORTS; ++k) {
        const uint32_t p = g_next_port.fetch_add(1) % PORTS; if (p == 0) continue;
        Flow* expect = nullptr; if (g_ports()[p].compare_exchange_strong(expect, f)) { f->port = p; return p; }
    }
    return 0;
}
// [ENDPOINT_ONCE_V1] this program's endpoint, made exactly once per process (a forked child makes its own)
struct Ep { int fd = -1; };
Ep& ep() { static Ep e; return e; }
std::atomic<pid_t> g_ep_making{0}, g_ep_ready{0};
void rx_loop(int fd);
void reattach();   // [LIBRARY_REASSERTS_V1] below
int open_ep() {
    const pid_t me = getpid();
    if (g_ep_ready.load(std::memory_order_acquire) == me) return ep().fd;
    pid_t was = g_ep_making.load(std::memory_order_acquire);
    if (was != me && g_ep_making.compare_exchange_strong(was, me, std::memory_order_acq_rel)) {
        if (ep().fd >= 0) ::close(ep().fd);                // inherited across fork: the child makes its own
        int fd = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
        sockaddr_un a; const socklen_t al = rmt::abstract(a, rmt::ep_name(uint32_t(me)));
        if (fd >= 0) { int big = 16 << 20; if (::setsockopt(fd, SOL_SOCKET, SO_RCVBUFFORCE, &big, sizeof big) != 0) ::setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &big, sizeof big); }
        if (fd < 0 || ::bind(fd, reinterpret_cast<sockaddr*>(&a), al) != 0) {
            fprintf(stderr, "librina: opening the data endpoint failed: %s\n", strerror(errno)); if (fd >= 0) { ::close(fd); } fd = -1; }
        ep().fd = fd;
        if (fd >= 0) std::thread(rx_loop, fd).detach();
        g_ep_ready.store(me, std::memory_order_release); return fd;
    }
    while (g_ep_ready.load(std::memory_order_acquire) != me) std::this_thread::yield();
    return ep().fd;
}
// one PDU to an end: straight to its program if it is on this machine, else to this machine's agent (the RMT)
// [DATA_THROUGH_THE_RAM_MANAGER_V1] send_pdu (an SDU handed to the agent, member to member) is gone: every SDU goes through the RAM manager
rina_region::Client& data_set();
void make_reliable(Flow* f, const rina_flow_spec& sp) {   // [RELIABLE_FLOWS_V1] max_sdu_gap 0 asks for every SDU, in order
    if (sp.max_sdu_gap != 0 || f->reliable) return;
    f->kept = new std::atomic<const std::string*>[RING]; for (uint64_t i = 0; i < RING; ++i) f->kept[i].store(nullptr);
    f->reliable = true;
}
ribbit::Ebr& kept_ebr() { static ribbit::Ebr* e = new ribbit::Ebr; return *e; }
// [RESEND_ON_ROUTE_CHANGE_V1] this machine's routes changed: on each reliable flow this program sends on, re-send every
// SDU its receiver has not stated it took (the receiver drops what it already has). The event is the trigger; no timer.
// the receiver states, each time the count handed to the program crosses a quarter of the window, how far it has got
void maybe_credit(Flow* f) {
    if (!f->linked.load(std::memory_order_acquire)) return;
    // [RELIABLE_FLOWS_V1] a reliable flow states every 64 SDUs, so a route change re-sends at most the last 64; an
    // unreliable one by quarters of the window (credit only)
    const uint64_t step = f->reliable ? 64 : RING / 4;
    const uint64_t n = f->sent_ok.load(std::memory_order_acquire), quarter = n / step;
    uint64_t was = f->credit_q.load(std::memory_order_acquire);
    while (quarter > was) if (f->credit_q.compare_exchange_weak(was, quarter)) {   // [CREDIT_IS_STATE_V1] our truth, written once per quarter
        try { data_set().post("{\"op\":\"taken\",\"fid\":" + q(f->fid) + ",\"proc\":" + q(proc_id()) + ",\"n\":" + std::to_string(n) + "}"); }
        catch (const std::exception& e) { fprintf(stderr, "librina: flow %s: stating what was taken failed: %s\n", f->fid.c_str(), e.what()); }
        return; }
}
// one SDU to the program, in the order it arrived; a full socket hands the flow's SDUs to its own delivery thread
void deliver(Flow* f, std::string&& d) {
    const bool backlog = f->queued_n.load(std::memory_order_acquire) != f->sent_ok.load(std::memory_order_acquire) + f->send_err.load(std::memory_order_acquire);
    if (!backlog) {
        const ssize_t w = ::send(f->lib, d.data(), d.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
        if (w >= 0) { f->queued_n.fetch_add(1); f->sent_ok.fetch_add(1); maybe_credit(f); return; }
        if (errno != EAGAIN && errno != EWOULDBLOCK) { f->queued_n.fetch_add(1); f->send_err.fetch_add(1); f->last_errno.store(errno); return; }
    }
    SduNode* nd = new SduNode; nd->d = std::move(d); f->out.push(nd); f->queued_n.fetch_add(1);
    f->out_n.fetch_add(1, std::memory_order_release); f->out_n.notify_one();
    if (!f->deliverer) { f->deliverer = true; std::thread([f] {
        for (;;) { const uint32_t seen = f->out_n.load(std::memory_order_acquire);
            while (SduNode* nd = f->out.pop()) {
                ssize_t w; do { w = ::send(f->lib, nd->d.data(), nd->d.size(), MSG_NOSIGNAL); } while (w < 0 && errno == EINTR);
                delete nd;
                if (w < 0) { f->send_err.fetch_add(1); f->last_errno.store(errno);
                    if (errno != EPIPE && errno != ECONNRESET) fprintf(stderr, "librina: flow %s: handing an SDU to the program failed: %s\n", f->fid.c_str(), strerror(errno));
                    return; }
                f->sent_ok.fetch_add(1); maybe_credit(f); }
            f->out_n.wait(seen, std::memory_order_acquire); } }).detach(); }
}
// the endpoint's one receiving thread: DATA to its flow (credit is state in the region, not a message: [CREDIT_IS_STATE_V1])
void rx_loop(int fd) {
    std::vector<char> buf(70000);
    for (;;) {
        const ssize_t n = ::recv(fd, buf.data(), buf.size(), 0);
        if (n < 0) { if (errno == EINTR) continue; fprintf(stderr, "librina: the data endpoint failed: %s\n", strerror(errno)); return; }
        if (size_t(n) < rmt::HDR) continue;
        const uint8_t type = uint8_t(buf[0]); const rmt::Addr to = rmt::dest_of(buf.data()); const uint64_t seq = rmt::seq_of(buf.data());
        if (type == 4) { std::thread(reattach).detach(); continue; }   // [LIBRARY_REASSERTS_V1] this machine's agent: the host changed
        Flow* f = to.port < PORTS ? g_ports()[to.port].load(std::memory_order_acquire) : nullptr;
        if (!f) {                                           // [NO_FALLBACKS] a PDU for a port this program does not have: said
            fprintf(stderr, "librina (pid %d): a PDU (type %u, seq %llu) for port %u, which this program does not have\n",
                    int(getpid()), unsigned(type), (unsigned long long)seq, to.port);
            continue; }
        if (type == 1) {
            f->rx_n.fetch_add(1, std::memory_order_relaxed);
            if (seq != f->rx_next) f->rx_gap.fetch_add(1, std::memory_order_relaxed);
            f->rx_next = seq + 1;
            if (f->reliable) {                              // [RELIABLE_FLOWS_V1] strictly in sequence; duplicates dropped, counted
                if (seq < f->rx_expect || f->rx_early.count(seq)) { f->rx_dup.fetch_add(1, std::memory_order_relaxed); continue; }
                f->rx_early.emplace(seq, std::string(buf.data() + rmt::HDR, size_t(n) - rmt::HDR));
                for (auto it = f->rx_early.begin(); it != f->rx_early.end() && it->first == f->rx_expect; it = f->rx_early.erase(it), ++f->rx_expect)
                    deliver(f, std::move(it->second));
                continue;
            }
            deliver(f, std::string(buf.data() + rmt::HDR, size_t(n) - rmt::HDR));
        } else {
            fprintf(stderr, "librina: a PDU of unknown type %u for flow %s\n", unsigned(type), f->fid.c_str());   // never silently
        }
    }
}
// "rmt:<dif>:<node>:<pid>:<port>" -> the flow's peer, in the DIF the region says the flow is in
bool link_peer(Flow* f, const std::string& loc, const std::string& dif) {
    rmt::Addr a; if (!rmt::parse(loc, a) || dif.empty()) return false;
    a.dif = rmt::id_of(dif); f->peer = a; f->linked.store(true, std::memory_order_release); return true;
}
// this end's locator (the DIF is filled in by whoever reads it, from the region's word on the flow's DIF)
std::string my_end(Flow* f) {
    if (open_ep() < 0 || (!f->port && !mint_port(f))) return std::string();
    rmt::Addr a; a.node = rmt::my_node(); a.pid = uint32_t(getpid()); a.port = f->port; return rmt::loc_of(a);
}
template <class T> void push(std::atomic<T*>& head, T* n) { n->next = head.load(); while (!head.compare_exchange_weak(n->next, n)) {} }
Flow* flow_of(const std::string& fid) { for (Flow* f = g_flows.load(); f; f = f->next) if (f->fid == fid) return f; return nullptr; }

// ---- the one session, and the one held read --------------------------------------------------------------------
std::atomic<rina_region::Client*> g_ram{nullptr};
// [CHANNEL_SETS_V1] a second, independent set (its own request, return and data sockets) for the SDUs a program writes,
// as the proxy keeps up to three sets per peer: bulk SDU traffic and its replies never queue ahead of a held read's
// answer or a control call. Liveness stays with the control set.
std::atomic<rina_region::Client*> g_data{nullptr};
void events(rina_region::Client* ram, std::string me);
void sender(struct Flow* f);
// [WIRE_STATS_V1] RINA_WIRE_STATS=1: at exit, this program's session counters -- what each request and reply was on
// the wire (REQ_RAW / REQ_FULL / REQ_DIFF / REQ_REPEAT, RESP_RAW / RESP_DIFF / RESP_SAME) and the bytes each way
void print_wire_stats();
// [EXIT_TRACE_V1] RINA_EXIT_TRACE=DIR: every librina program that exits writes DIR/exit.<pid> -- its status, its proc id,
// and the stack that called exit() -- to find a program that ends when it should not (a debugging aid)
void exit_trace(int status) {
    const char* d = getenv("RINA_EXIT_TRACE"); if (!d || !*d) return;
    char path[512]; snprintf(path, sizeof path, "%s/exit.%d", d, int(getpid()));
    int fd = ::open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644); if (fd < 0) return;
    char head[256]; int n = snprintf(head, sizeof head, "pid %d ppid %d status %d proc %s\n", int(getpid()), int(getppid()), status, proc_id().c_str());
    if (::write(fd, head, size_t(n)) < 0) {}
    void* frames[64]; const int k = backtrace(frames, 64); backtrace_symbols_fd(frames, k, fd); ::close(fd);
}
void exit_now(int status, void*) { exit_trace(status); print_wire_stats(); fflush(nullptr); _exit(status); }   // our threads are parked in the held read
rina_region::Client& ram();
// The child of a fork is a new program: its own session, opened at once, and the parent's inboxes become its own (the
// hello carries them), so a request for a name the parent registered reaches the child's held read.
void before_fork() {                                   // the parent, before fork(): the child will hold my input spaces too
    g_child_proc.clear();
    if (!g_inboxes.load()) return;                       // no input spaces: nothing for a child to hold
    // a program that forks again before its own connection is up (a daemonizing child forking once more) still
    // announces its child: the connection is made here, synchronously, first
    rina_region::Client* c = nullptr; try { c = &ram(); } catch (const std::exception& e) {
        fprintf(stderr, "librina (pid %d): cannot announce the forked child to the RINA region: %s\n", int(getpid()), e.what()); return; }
    std::random_device r; char b[48]; snprintf(b, sizeof b, "p%d-c%08x", getpid(), r()); g_child_proc = b;
    std::string ibs = "["; bool first = true; for (Inbox* i = g_inboxes.load(); i; i = i->next) { ibs += (first ? "" : ",") + q(i->id); first = false; } ibs += "]";
    try { c->call("{\"op\":\"fork\",\"child\":" + q(g_child_proc) + ",\"inboxes\":" + ibs + "}"); }
    catch (const std::exception& e) { fprintf(stderr, "librina: announcing the fork to the region failed: %s\n", e.what()); }   // [NO_FALLBACKS] said, not swallowed
}
void parent_after_fork() { g_child_proc.clear(); }      // the next fork mints its own
void after_fork() {
    if (!g_child_proc.empty()) { proc_id_slot() = g_child_proc; proc_id_pid().store(getpid()); }   // the id the parent announced
    g_child_proc.clear();                                // a fork of this child mints its own (never a copy of this one)
    // [NO_FALLBACKS] a child that cannot reach the region says so -- it used to be swallowed (catch (...) {})
    g_data.store(nullptr);                               // [CHANNEL_SETS_V1] the child opens its own
    if (g_ram.exchange(nullptr)) std::thread([] { try { ram(); } catch (const std::exception& e) {
        fprintf(stderr, "librina (pid %d): the forked program cannot reach the RINA region: %s\n", int(getpid()), e.what()); } }).detach(); }
void print_wire_stats() {
    const char* e = getenv("RINA_WIRE_STATS"); if (!e || strcmp(e, "1") != 0) return;
    rina_region::Client* c = g_ram.load(); if (!c) return;
    const frogram::Stats st = c->stats();
    fprintf(stderr, "[wire %d] req raw=%llu full=%llu diff=%llu repeat=%llu posted=%llu | resp raw=%llu diff=%llu same=%llu miss=%llu coalesced=%llu | "
                    "bytes out=%llu in=%llu | data-socket frames=%llu bytes=%llu\n", int(getpid()),
            (unsigned long long)st.raw, (unsigned long long)st.full, (unsigned long long)st.diff, (unsigned long long)st.repeat,
            (unsigned long long)st.posted, (unsigned long long)st.rraw, (unsigned long long)st.rdiff, (unsigned long long)st.same,
            (unsigned long long)st.miss, (unsigned long long)st.coalesced, (unsigned long long)st.bytes_out, (unsigned long long)st.bytes_in,
            (unsigned long long)st.data_frames, (unsigned long long)st.data_bytes);
    if (rina_region::Client* d = g_data.load()) { const frogram::Stats ds = d->stats();
        fprintf(stderr, "[wire %d] data set: req raw=%llu full=%llu diff=%llu posted=%llu refused=%llu | resp raw=%llu diff=%llu | bytes out=%llu in=%llu\n", int(getpid()),
                (unsigned long long)ds.raw, (unsigned long long)ds.full, (unsigned long long)ds.diff, (unsigned long long)ds.posted,
                (unsigned long long)ds.posts_refused, (unsigned long long)ds.rraw, (unsigned long long)ds.rdiff,
                (unsigned long long)ds.bytes_out, (unsigned long long)ds.bytes_in); }
    else fprintf(stderr, "[wire %d] data set: none\n", int(getpid()));
    for (Flow* f = g_flows.load(); f; f = f->next)
        fprintf(stderr, "[wire %d] flow %s: sent %llu (re-sent %llu), arrived %llu (out of sequence %llu, duplicates %llu), next_q %llu, held early %zu, queued %llu, delivered %llu, send errors %llu (errno %d)\n", int(getpid()), f->fid.c_str(),
                (unsigned long long)f->sent.load(), (unsigned long long)f->resent.load(), (unsigned long long)f->rx_n.load(), (unsigned long long)f->rx_gap.load(), (unsigned long long)f->rx_dup.load(),
                (unsigned long long)f->next_q, f->early.size(), (unsigned long long)f->queued_n.load(), (unsigned long long)f->sent_ok.load(),
                (unsigned long long)f->send_err.load(), f->last_errno.load());
}
rina_region::Client& ram();
rina_region::Client& data_set() {   // [CREDIT_IS_STATE_V1] the receivers' posted statements of what they have taken
                       // [CHANNEL_SETS_V1]
    rina_region::Client* r = g_data.load(std::memory_order_acquire); if (r) return *r;
    ram();                                               // the program is known (hello) on its control set first
    rina_region::Client* n = new rina_region::Client;
    if (!g_data.compare_exchange_strong(r, n)) return *r;
    n->call("{\"op\":\"hello\",\"set\":\"data\",\"proc\":" + q(proc_id()) + "}");
    return *n;
}
// ---- [LIBRARY_REASSERTS_V1] (experiment 5b, part C) -----------------------------------------------------------------
// What this program owns in the region (its registrations, its flows' records), as last held: refreshed after every
// register, unregister, accept and positive verdict -- control calls, never per SDU. On a host change it is written back.
std::mutex g_owned_mu; std::string g_owned;
void refresh_owned(rina_region::Client& c) {
    try { Json r = c.call("{\"op\":\"pmine\",\"proc\":" + q(proc_id()) + "}");
          std::lock_guard<std::mutex> l(g_owned_mu); g_owned = r["recs_text"].s; }
    catch (const std::exception& e) { fprintf(stderr, "librina: [LIBRARY_REASSERTS_V1] reading what this program owns failed: %s\n", e.what()); }
}
std::string inboxes_json() { std::string ibs = "["; bool first = true; for (Inbox* b = g_inboxes.load(); b; b = b->next) { ibs += (first ? "" : ",") + q(b->id); first = false; } return ibs + "]"; }
// lost the region: ask this machine's agent to say when the host is back (a type-5 PDU; the answer is a type-4 PDU on
// this program's data endpoint, which is opened here if it was not)
void subscribe_host_change() {
    if (open_ep() < 0) { fprintf(stderr, "librina: [LIBRARY_REASSERTS_V1] no data endpoint to be told on\n"); return; }
    static int out = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    char h[rmt::HDR]; rmt::Addr a; a.node = rmt::my_node(); a.pid = uint32_t(getpid()); rmt::header(h, 5, a, 0);
    sockaddr_un sa; const socklen_t al = rmt::abstract(sa, rmt::agent_name());
    if (::sendto(out, h, sizeof h, MSG_NOSIGNAL, reinterpret_cast<sockaddr*>(&sa), al) < 0)
        fprintf(stderr, "librina: [LIBRARY_REASSERTS_V1] asking this machine's agent about the host failed: %s\n", strerror(errno));
}
void events(rina_region::Client* r, std::string me);
void resend_after_host_change();
// the host changed: a new session, hello (liveness and inboxes), what this program owns written back, the held wait
std::atomic<bool> g_reattaching{false};
void reattach() {
    if (g_reattaching.exchange(true)) return;
    // [FAULT_REATTACH_DELAY_V1] fault injection for experiments ONLY: hold this program's re-attach back, so the agents'
    // routes change while it is detached (the order in which a route event is missed). Never set in normal runs.
    if (const char* d = getenv("RINA_FAULT_REATTACH_DELAY_MS"); d && *d) {
        fprintf(stderr, "librina (pid %d): [FAULT_REATTACH_DELAY_V1] FAULT INJECTED: re-attach held back %s ms\n", int(getpid()), d);
        std::this_thread::sleep_for(std::chrono::milliseconds(atoi(d))); }
    try {
        rina_region::Client::forget_location();                   // [LOCATE_CACHED_V1] it may have moved: resolve afresh
        rina_region::Client* c = new rina_region::Client;
        c->call("{\"op\":\"hello\",\"proc\":" + q(proc_id()) + ",\"ns\":" + q(netns()) + ",\"inboxes\":" + inboxes_json() + "}");
        std::string owned; { std::lock_guard<std::mutex> l(g_owned_mu); owned = g_owned; }
        int n = 0;
        if (!owned.empty() && owned != "[]") {
            Json r = c->call("{\"op\":\"preassert\",\"proc\":" + q(proc_id()) + ",\"inboxes\":" + inboxes_json() + ",\"recs\":" + owned + "}");
            if (!r["ok"].b) throw std::runtime_error("re-asserting: " + r["error"].s + " " + r["record"].s);
            n = int(r["n"].n); }
        g_ram.store(c, std::memory_order_release);              // the dead session stays allocated: other threads may hold it
        g_data.store(nullptr, std::memory_order_release);       // the SDU set is made again on its next use
        std::thread(events, c, proc_id()).detach();
        fprintf(stderr, "librina (pid %d): [LIBRARY_REASSERTS_V1] re-attached; re-asserted %d record(s) this program owns\n", int(getpid()), n);
        resend_after_host_change();                          // [RESEND_ON_HOST_CHANGE_V1] the re-arm step
    } catch (const std::exception& e) {
        fprintf(stderr, "librina (pid %d): [LIBRARY_REASSERTS_V1] re-attaching failed: %s; waiting for the host again\n", int(getpid()), e.what());
        g_reattaching.store(false); subscribe_host_change(); return; }
    g_reattaching.store(false);
}
rina_region::Client& ram() {
    static bool hooked = (pthread_atfork(before_fork, parent_after_fork, after_fork) == 0) && (on_exit(exit_now, nullptr) == 0); (void)hooked;
    rina_region::Client* r = g_ram.load(std::memory_order_acquire); if (r) return *r;
    rina_region::Client* n = new rina_region::Client;
    if (!g_ram.compare_exchange_strong(r, n)) return *r;                // another thread's session won (ours stays open, unused)
    std::string ibs = "["; bool first = true; for (Inbox* b = g_inboxes.load(); b; b = b->next) { ibs += (first ? "" : ",") + q(b->id); first = false; } ibs += "]";
    n->call("{\"op\":\"hello\",\"proc\":" + q(proc_id()) + ",\"ns\":" + q(netns()) + ",\"inboxes\":" + ibs + "}");   // alive from here
    std::thread(events, n, proc_id()).detach();
    return *n;
}

// THE held read. One in flight per program, for as long as the program runs.
void events(rina_region::Client* r, std::string me) {
    double after = 0;
    for (;;) {
        // [WAIT_ANSWER_IS_TEXT_V1] the answer is lines "<number>\t<event>\n" after a first line "E"; each event is parsed
        // here, alone -- the host carries the whole answer as one raw field and never builds a JSON tree of it
        std::string text;
        try { text = r->held_text("{\"op\":\"wait\",\"proc\":" + q(me) + ",\"after\":" + std::to_string((unsigned long long)after) + "}", 3.0e8); }
        catch (const std::exception& e) {                             // [LIBRARY_REASSERTS_V1] said, never silent
            fprintf(stderr, "librina (pid %d): [LIBRARY_REASSERTS_V1] the region is gone (%s); flows keep moving member to member; waiting for the host\n", int(getpid()), e.what());
            subscribe_host_change(); return; }
        if (text.compare(0, 2, "E\n") != 0) { fprintf(stderr, "librina: a wait answer that is not the region's event lines: %.80s\n", text.c_str()); return; }
        for (size_t at = 2; at < text.size();) {
            const size_t tab = text.find('\t', at), nl = text.find('\n', at);
            if (tab == std::string::npos || nl == std::string::npos || tab > nl) { fprintf(stderr, "librina: a malformed event line in a wait answer\n"); return; }
            const double id = strtod(text.c_str() + at, nullptr);
            Json x; x.type = Json::Arr; x.a.resize(2); x.a[0].type = Json::Num; x.a[0].n = id; x.a[1] = Json::parse(text.substr(tab + 1, nl - tab - 1));
            at = nl + 1;
            after = x[0].n; const Json& b = x[1]; const std::string& k = b["k"].s;
            if (k == "s") { if (Flow* f = flow_of(b["fid"].s)) {
                // [FLOW_ORDER_V1] the host executes a session's requests in parallel, so a flow's SDUs can land out of
                // order; they are handed to the program in their sequence order, each exactly once
                const uint64_t sq = uint64_t(b["q"].n);
                if (sq >= f->next_q) { f->early.emplace(sq, from_text(b["d"].s)); f->early_id[sq] = uint64_t(x[0].n); }
                for (auto it = f->early.begin(); it != f->early.end() && it->first == f->next_q; it = f->early.erase(it), ++f->next_q) {
                    {   // [SDU_BY_REFERENCE_V1] remember what this SDU is and where it lives (one writer: this thread)
                        Flow::Given& g = f->given[f->given_n.load(std::memory_order_relaxed) % 8];
                        g.seq.fetch_add(1, std::memory_order_acq_rel);               // odd: being written
                        g.q = it->first; g.id = f->early_id[it->first]; f->early_id.erase(it->first);
                        g.len = it->second.size() <= sizeof g.d ? uint32_t(it->second.size()) : 0; if (g.len) memcpy(g.d, it->second.data(), g.len);
                        g.seq.fetch_add(1, std::memory_order_release);               // even: whole
                        f->given_n.fetch_add(1, std::memory_order_release); }
                    // [DELIVER_IN_ARRIVAL_ORDER_V1] with nothing waiting for this flow, the SDU goes to the program's
                    // socket right here, so SDUs of all flows reach the program in the order they arrived (rinaperf's
                    // "stop" on its control flow can no longer pass the last data SDUs). Only a full socket hands the
                    // flow's SDUs to its own delivery thread, in order, so one slow reader never stalls the others.
                    const bool backlog = f->queued_n.load(std::memory_order_acquire) != f->sent_ok.load(std::memory_order_acquire) + f->send_err.load(std::memory_order_acquire);
                    if (!backlog) {
                        const ssize_t w = ::send(f->lib, it->second.data(), it->second.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
                        if (w >= 0) { f->queued_n.fetch_add(1); f->sent_ok.fetch_add(1); continue; }
                        if (errno != EAGAIN && errno != EWOULDBLOCK) { f->queued_n.fetch_add(1); f->send_err.fetch_add(1); f->last_errno.store(errno); continue; }
                    }
                    SduNode* nd = new SduNode; nd->d = std::move(it->second); f->out.push(nd); f->queued_n.fetch_add(1);   // [FLOW_DELIVERY_V1]
                    f->out_n.fetch_add(1, std::memory_order_release); f->out_n.notify_one(); }
                if (!f->deliverer) { f->deliverer = true; std::thread([f] {          // this flow's own delivery thread
                    for (;;) { const uint32_t seen = f->out_n.load(std::memory_order_acquire);
                        while (SduNode* nd = f->out.pop()) {
                            ssize_t w; do { w = ::send(f->lib, nd->d.data(), nd->d.size(), MSG_NOSIGNAL); } while (w < 0 && errno == EINTR);   // a signal is not an end
                            delete nd;
                            if (w < 0) { f->send_err.fetch_add(1); f->last_errno.store(errno);
                                if (errno != EPIPE && errno != ECONNRESET) fprintf(stderr, "librina: flow %s: handing an SDU to the program failed: %s\n", f->fid.c_str(), strerror(errno));
                                return; }
                            f->sent_ok.fetch_add(1); }
                        f->out_n.wait(seen, std::memory_order_acquire); } }).detach(); } } }
            else if (k == "c") { if (Flow* f = flow_of(b["fid"].s)) { f->consumed.store(uint64_t(b["n"].n)); f->consumed.notify_all(); } }
            else if (k == "r") { for (Inbox* i = g_inboxes.load(); i; i = i->next) if (i->id == b["inbox"].s) {
                    const std::string m = "{\"fid\":" + q(b["fid"].s) + ",\"src\":" + q(b["src"].s) + ",\"spec\":" + q(b["spec"].s) + ",\"req\":" + q(b["req"].s) + ",\"inbox\":" + q(i->id) + "}";
                    ::send(i->lib, m.data(), m.size(), MSG_NOSIGNAL); break; } }
            else if (k == "v") { for (Alloc* a = g_allocs.load(); a; a = a->next) if (a->fid == b["fid"].s && !a->done.exchange(true)) {
                    bool ok = b["v"].s == "0";
                    Flow* fl = ok ? flow_of(a->fid) : nullptr;
                    if (ok && (!fl || !link_peer(fl, b["sloc"].s, b["dif"].s))) {   // [MEMBER_TO_MEMBER_DATA_V1] where its SDUs go
                        fprintf(stderr, "librina: flow %s: the verdict carries no usable peer endpoint (%s)\n", a->fid.c_str(), b["sloc"].s.c_str()); ok = false; }
                    ::send(a->lib, ok ? "0" : "1", 1, MSG_NOSIGNAL);
                    if (!ok) ::close(a->lib); else { refresh_owned(*r); std::thread(sender, fl).detach(); }   // yes: its SDUs may go, member to member; [LIBRARY_REASSERTS_V1]
                    break; } }
        }
    }
}

// [ONE_REQUEST_PER_SDU_V1] A flow's sender: each SDU the program writes is ONE posted request, written once into the
// receiver's input space; nothing is grouped here -- concurrent requests are the session's to fan in. Credit-bounded.
void sender(Flow* f) {
    std::vector<char> buf(65536); int reset_seen = 0;     // [CLOSE_KEEPS_WRITES_V1] taken once
    for (;;) {
        // [CLOSE_KEEPS_WRITES_V1] a program that writes its last SDU and closes its end while an SDU we gave it is still
        // unread makes the kernel report ECONNRESET here once -- BEFORE the SDUs it wrote, which are still queued for
        // us. rinaperf's server does exactly that (result written, stop unread, close). The error is taken once and
        // reading goes on: everything the program wrote is sent, then the end of the flow (0).
        ssize_t n; do { n = ::recv(f->lib, buf.data(), buf.size(), 0); } while (n < 0 && (errno == EINTR || (errno == ECONNRESET && !reset_seen++)));
        if (n == 0) return;                                 // the program closed its end: the flow's sending is over
        if (n < 0) { fprintf(stderr, "librina: flow %s: reading what the program wrote failed: %s\n", f->fid.c_str(), strerror(errno)); return; }
        const uint64_t seq = f->sent.load();
        // [CREDIT_IS_STATE_V1] a full window ahead: one held read of the receiver's stated truth, answered the moment it has
        // taken more (or the receiver is gone -- then this flow's sending is over, said, not retried). No message, no timer.
        while (seq - f->consumed.load(std::memory_order_acquire) >= RING) {
            Json cr; try { cr = ram().held("{\"op\":\"credit\",\"fid\":" + q(f->fid) + ",\"proc\":" + q(proc_id()) + ",\"have\":" + std::to_string(f->consumed.load()) + "}", 3.0e8); }
            catch (const std::exception& e) { fprintf(stderr, "librina: flow %s: waiting for credit failed: %s\n", f->fid.c_str(), e.what()); return; }
            if (!cr["ok"].b) { fprintf(stderr, "librina: flow %s: the receiver is gone\n", f->fid.c_str()); return; }
            uint64_t c = f->consumed.load(); const uint64_t n = uint64_t(cr["n"].n); while (n > c && !f->consumed.compare_exchange_weak(c, n)) {}
        }
        f->sent.store(seq + 1);
        if (f->reliable) {                                  // [RELIABLE_FLOWS_V1] kept until the receiver states it took it
            const std::string* old = f->kept[seq % RING].exchange(new std::string(buf.data(), size_t(n)), std::memory_order_acq_rel);
            if (old) kept_ebr().retire(old);
            f->kept_seq.store(seq + 1, std::memory_order_release); }
        // [DATA_THROUGH_THE_RAM_MANAGER_V1] (John, 2026-10-07) data never runs member to member: each SDU is ONE posted
        // request on this program's own connection to the RAM manager, which writes it into the peer program's stream
        try { const std::string body = "{\"op\":\"send\",\"fid\":" + q(f->fid) + ",\"q\":" + std::to_string((unsigned long long)seq) + ",\"d\":" + q(to_text(buf.data(), size_t(n))) + "}";   // [FLAT_SEND_V1]
              // [SEND_TRACE_V1] instrumentation: RINA_SEND_TRACE=FILE appends every posted send body, to replay elsewhere
              static const char* tr = getenv("RINA_SEND_TRACE");
              if (tr && *tr) if (FILE* t = fopen(tr, "a")) { fputs(body.c_str(), t); fputc('\n', t); fclose(t); }
              data_set().post(body); }
        catch (const std::exception& e) {
            fprintf(stderr, "librina: flow %s: sending SDU %llu through the RAM manager failed: %s\n", f->fid.c_str(), (unsigned long long)seq, e.what()); return; }
    }
}
// [RESEND_ON_HOST_CHANGE_V1] the host changed: route events pushed while this program had no session are gone, and the
// region's statements of what each receiver took are gone with the old host. So, the re-arm step of the reconcile: on
// each reliable flow to another machine, forget the last path ("via", so the next route event re-sends too) and re-send
// now everything kept that the receiver has not stated it took (none, after a restart: up to the window). The receiver
// drops duplicates. A PDU with no route yet is dropped and counted by the agent; the next route event re-sends it.
void resend_after_host_change() {
    size_t flows = 0, sdus = 0;
    for (Flow* f = g_flows.load(); f; f = f->next) {
        if (!f->reliable || !f->linked.load(std::memory_order_acquire) || f->peer.node == rmt::my_node()) continue;
        f->via = 0; ++flows;
        const uint64_t sent = f->kept_seq.load(std::memory_order_acquire); if (!sent) continue;
        uint64_t taken = 0;
        try { taken = uint64_t(ram().call("{\"op\":\"taken_now\",\"fid\":" + q(f->fid) + ",\"proc\":" + q(proc_id()) + "}")["n"].n); }
        catch (const std::exception& e) { fprintf(stderr, "librina: flow %s: reading what its receiver took failed: %s\n", f->fid.c_str(), e.what()); }
        const uint64_t from = std::max<uint64_t>(taken, sent > RING ? sent - RING : 0);
        auto g = kept_ebr().guard();
        for (uint64_t seq = from; seq < sent; ++seq) {
            const std::string* d = g.protect(f->kept[seq % RING]); if (!d) continue;
            // [DATA_THROUGH_THE_RAM_MANAGER_V1] a re-send goes the way every SDU goes: through the RAM manager
            try { data_set().post("{\"op\":\"send\",\"fid\":" + q(f->fid) + ",\"q\":" + std::to_string((unsigned long long)seq) + ",\"d\":" + q(to_text(d->data(), d->size())) + "}"); }   // [FLAT_SEND_V1]
            catch (const std::exception& e) { fprintf(stderr, "librina: flow %s: re-sending SDU %llu through the RAM manager failed: %s\n", f->fid.c_str(), (unsigned long long)seq, e.what()); break; }
            f->resent.fetch_add(1, std::memory_order_relaxed); ++sdus;
        }
    }
    fprintf(stderr, "librina (pid %d): [RESEND_ON_HOST_CHANGE_V1] re-sent %zu SDU(s) on %zu reliable flow(s)\n", int(getpid()), sdus, flows);
}
Flow* end_of(const std::string& fid, int lib) { Flow* f = new Flow; f->fid = fid; f->lib = lib; push(g_flows, f); return f; }

bool pair_fds(int& app, int& lib) { int sv[2]; if (::socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sv)) return false; app = sv[0]; lib = sv[1]; return true; }
int done_fd() { return eventfd(1, EFD_CLOEXEC); }
std::string resolve(const std::string& name) {
    addrinfo h{}, *res = nullptr; h.ai_family = AF_INET; if (getaddrinfo(name.c_str(), nullptr, &h, &res) || !res) return "";
    char b[64]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(res->ai_addr)->sin_addr, b, sizeof b); freeaddrinfo(res); return b;
}
bool is_local(const std::string& ip) {
    int s = ::socket(AF_INET, SOCK_DGRAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; inet_pton(AF_INET, ip.c_str(), &a.sin_addr);
    const bool ok = ::bind(s, reinterpret_cast<sockaddr*>(&a), sizeof a) == 0; ::close(s); return ok;
}
int err_of(const Json& r, int dflt) {
    const std::string& e = r["error"].s;
    return e == "ENXIO" ? ENXIO : e == "EADDRNOTAVAIL" ? EADDRNOTAVAIL : e == "ENOENT" ? ENOENT : e == "EHOSTUNREACH" ? EHOSTUNREACH : dflt;
}
// a positive or negative verdict, in ONE call; positive also records the flow and routes it at the memory
int answer(const std::string& fid, const std::string& inbox, const std::string& req, const std::string& dst, bool yes, const std::string& sloc = std::string()) {
    Json r = ram().call("{\"op\":\"accept\",\"fid\":" + q(fid) + ",\"inbox\":" + q(inbox) + ",\"req\":" + q(req) + ",\"dst\":" + q(dst) + ",\"sloc\":" + q(sloc) +
                        ",\"verdict\":" + q(yes ? "0" : "1") + ",\"spid\":" + q(std::to_string(getpid())) + ",\"sns\":" + q(netns()) + "}");
    return r["ok"].b ? 0 : -1;
}
}  // namespace

extern "C" {
int rl_verbosity = 4;   // rlite's librina-api exports it (user/libs/ctrl.c 45); iporinad's CDAP library reads it

int rina_open(void) {
    int app, lib; if (!pair_fds(app, lib)) return -1;
    Inbox* b = new Inbox; b->id = uniq(); b->app = app; b->lib = lib; push(g_inboxes, b);
    ram(); return app;
}
static Inbox* inbox_of(int fd) { for (Inbox* b = g_inboxes.load(); b; b = b->next) if (b->app == fd) return b; return nullptr; }

int rina_register(int fd, const char* dif_name, const char* local_appl, int flags) {
    Inbox* b = inbox_of(fd); if (!b || !local_appl) { errno = b ? EINVAL : EBADF; return -1; }
    // [RESOLVE_ONLY_FOR_UDP4_V1] ONE call; the name is resolved (DNS, /etc/hosts) only if the region says the DIF is a
    // shim-udp4 (NEEDIP) -- a lookup per registration on every other DIF cost a DNS round trip, and a 2 s stall
    // whenever the resolver timed out
    const std::string base = "{\"op\":\"register\",\"ns\":" + q(netns()) + ",\"dif\":" + q(dif_name ? dif_name : "") + ",\"appl\":" + q(local_appl) +
                             ",\"inbox\":" + q(b->id) + ",\"pid\":" + q(std::to_string(getpid()));
    Json r = ram().call(base + "}");
    if (!r["ok"].b && r["error"].s == "NEEDIP") { const std::string ip = resolve(local_appl);
        r = ram().call(base + ",\"ip\":" + q(ip) + ",\"ip_local\":" + std::string(!ip.empty() && is_local(ip) ? "true" : "false") + "}"); }
    if (!r["ok"].b) { errno = err_of(r, ENXIO); return -1; }
    refresh_owned(ram());                                                // [LIBRARY_REASSERTS_V1]
    return (flags & RINA_F_NOWAIT) ? done_fd() : 0;
}
int rina_unregister(int fd, const char* dif_name, const char* local_appl, int flags) {
    (void)fd; if (!local_appl) { errno = EINVAL; return -1; }
    Json r = ram().call("{\"op\":\"unregister\",\"ns\":" + q(netns()) + ",\"dif\":" + q(dif_name ? dif_name : "") + ",\"appl\":" + q(local_appl) + "}");
    if (!r["ok"].b) { errno = err_of(r, ENOENT); return -1; }
    refresh_owned(ram());                                                // [LIBRARY_REASSERTS_V1]
    return (flags & RINA_F_NOWAIT) ? done_fd() : 0;
}
int rina_register_wait(int fd, int wfd) { (void)fd; uint64_t v; if (::read(wfd, &v, sizeof v) < 0) return -1; ::close(wfd); return 0; }

int rina_flow_accept(int fd, char** remote_appl, struct rina_flow_spec* spec, unsigned int flags) {
    if (!inbox_of(fd)) { errno = EBADF; return -1; }
    std::string m(65536, '\0'); const ssize_t n = ::recv(fd, &m[0], m.size(), 0); if (n <= 0) return -1;   // the request, from the held read
    m.resize(size_t(n)); Json x = Json::parse(m);
    const std::string fid = x["fid"].s, req = x["req"].s, inbox = x["inbox"].s, dst = field(req, "dst");
    if (remote_appl) *remote_appl = strdup(x["src"].s.c_str());
    rina_flow_spec rsp; memset(&rsp, 0, sizeof rsp); { const std::string b = from_text(x["spec"].s); memcpy(&rsp, b.data(), std::min(sizeof rsp, b.size())); }
    if (spec) *spec = rsp;
    int app, lib; if (!pair_fds(app, lib)) return -1;
    if (flags & RINA_F_NORESP) { Held* h = new Held; h->handle = app; h->req = req; h->fid = fid; h->inbox = inbox; h->dst = dst; push(g_held, h); make_reliable(end_of(fid, lib), rsp); return app; }
    Flow* f = end_of(fid, lib);                                          // my end exists before the allocator hears yes
    make_reliable(f, rsp);
    const std::string mine = my_end(f);                                  // [MEMBER_TO_MEMBER_DATA_V1]
    if (mine.empty() || !link_peer(f, field(req, "cloc"), field(req, "dif"))) { fprintf(stderr, "librina: flow %s: no data endpoint (mine %s, peer %s)\n", fid.c_str(), mine.c_str(), field(req, "cloc").c_str());
        answer(fid, inbox, req, dst, false); ::close(app); ::close(lib); errno = EPERM; return -1; }
    if (answer(fid, inbox, req, dst, true, mine) != 0) { ::close(app); ::close(lib); errno = EPERM; return -1; }
    refresh_owned(ram());                                                // [LIBRARY_REASSERTS_V1]
    std::thread(sender, f).detach();
    return app;
}
int rina_flow_respond(int fd, int handle, int response) {
    (void)fd;
    for (Held* h = g_held.load(); h; h = h->next) if (h->handle == handle && !h->done.exchange(true)) {
        Flow* f = flow_of(h->fid);
        std::string mine;                                                // [MEMBER_TO_MEMBER_DATA_V1]
        if (response == 0 && (!f || (mine = my_end(f)).empty() || !link_peer(f, field(h->req, "cloc"), field(h->req, "dif")))) {
            fprintf(stderr, "librina: flow %s: no data endpoint\n", h->fid.c_str()); response = 1; }
        if (answer(h->fid, h->inbox, h->req, h->dst, response == 0, mine) != 0 || response) { ::close(handle); if (f) ::close(f->lib); return response ? 0 : -1; }
        refresh_owned(ram());                                            // [LIBRARY_REASSERTS_V1]
        std::thread(sender, f).detach(); return handle;
    }
    errno = EINVAL; return -1;
}
int rina_flow_alloc(const char* dif_name, const char* local_appl, const char* remote_appl, const struct rina_flow_spec* flowspec, unsigned int flags) {
    if (!remote_appl) { errno = EINVAL; return -1; }
    rina_flow_spec sp; if (flowspec) sp = *flowspec; else rina_flow_spec_unreliable(&sp);
    const std::string fid = uniq();
    int app, lib; if (!pair_fds(app, lib)) return -1;
    Alloc* a = new Alloc; a->fid = fid; a->lib = lib; push(g_allocs, a);   // the verdict will find it in the held read
    Flow* f = end_of(fid, lib);                                          // and the peer's first SDUs will find this end
    make_reliable(f, sp);
    const std::string mine = my_end(f);                                  // [MEMBER_TO_MEMBER_DATA_V1] where they will arrive
    if (mine.empty()) { a->done.store(true); ::close(app); ::close(lib); errno = ENOBUFS; return -1; }
    const std::string base = "{\"op\":\"alloc\",\"ns\":" + q(netns()) + ",\"dif\":" + q(dif_name ? dif_name : "") + ",\"remote\":" + q(remote_appl) +
                             ",\"src\":" + q(local_appl ? local_appl : "") + ",\"cpid\":" + q(std::to_string(getpid())) +
                             ",\"cproc\":" + q(proc_id()) + ",\"spec\":" + q(to_text(reinterpret_cast<const char*>(&sp), sizeof sp)) + ",\"fid\":" + q(fid) + ",\"cloc\":" + q(mine);
    Json r = ram().call(base + "}");                                     // [RESOLVE_ONLY_FOR_UDP4_V1] as rina_register
    if (!r["ok"].b && r["error"].s == "NEEDIP") r = ram().call(base + ",\"want_ip\":" + q(resolve(remote_appl)) + "}");
    if (!r["ok"].b) { a->done.store(true); ::close(app); ::close(lib); errno = err_of(r, ENOENT); return -1; }
    (void)f;
    if (flags & RINA_F_NOWAIT) return app;                               // readable when the verdict arrives
    return rina_flow_alloc_wait(app);
}
int rina_flow_alloc_wait(int wfd) {
    char v = 1; const ssize_t n = ::recv(wfd, &v, 1, 0);
    if (n != 1 || v != '0') { ::close(wfd); errno = n == 1 ? EPERM : ECONNRESET; return -1; }
    return wfd;
}
void rina_flow_spec_unreliable(struct rina_flow_spec* spec) {           // rl_flow_spec_default, common/utils.c 825-836
    memset(spec, 0, sizeof *spec);
    spec->version = RINA_FLOW_SPEC_VERSION; spec->max_sdu_gap = UINT64_MAX; spec->max_loss = RINA_FLOW_SPEC_LOSS_MAX;
    spec->in_order_delivery = 0; spec->msg_boundaries = 1;
}
unsigned int rina_flow_mss_get(int fd) { (void)fd; return 1400; }
}  // extern "C"
