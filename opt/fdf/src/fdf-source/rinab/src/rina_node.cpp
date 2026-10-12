// rina_node.cpp -- installed as rlite-uipcps: a machine's agent. It states what only this machine can know -- its
// devices -- in ONE call (op=rina node) when it starts and again on every link change the kernel reports (netlink).
// Nothing on a timer. Its session is the machine's liveness. It ends when `rlite-ctl terminate` is run in its
// namespace: that lands in the one held read it keeps in flight.
//   rlite-uipcps [-d]     -d: in the background; the foreground returns once the machine has been stated
#include <fcntl.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <functional>
#include "rina_region_client.hpp"
#include "rina_elect.hpp"
#include "rmt.hpp"
#include "machine_id.hpp"
#include "ebr.hpp"
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <sys/uio.h>
#include <netdb.h>
#include <poll.h>
#include <ifaddrs.h>
#include <algorithm>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <atomic>
#include <map>
#include <mutex>
#include <deque>
#include <set>
#include <vector>

static std::string netns() { return rina_id::machine_id(); }   // [MACHINE_IDENTITY_V1]
static std::string devices() {
    std::string out = ";"; int s = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (struct if_nameindex* ix = if_nameindex()) {
        for (struct if_nameindex* i = ix; i->if_index; ++i) {
            ifreq r{}; snprintf(r.ifr_name, sizeof r.ifr_name, "%s", i->if_name);
            out += std::string(i->if_name) + "=" + ((ioctl(s, SIOCGIFFLAGS, &r) == 0 && (r.ifr_flags & IFF_UP)) ? "1" : "0") + ";";
            ifreq h{}; snprintf(h.ifr_name, sizeof h.ifr_name, "%s", i->if_name);   // [RMT_OVER_DIF_LINKS_V1] its link address
            if (ioctl(s, SIOCGIFHWADDR, &h) == 0) { const unsigned char* a = reinterpret_cast<unsigned char*>(h.ifr_hwaddr.sa_data); char mac[18];
                snprintf(mac, sizeof mac, "%02x:%02x:%02x:%02x:%02x:%02x", a[0], a[1], a[2], a[3], a[4], a[5]); out += "mac." + std::string(i->if_name) + "=" + mac + ";"; }
        }
        if_freenameindex(ix);
    }
    ::close(s);
    // [LISP_RLOCS_IN_THE_REGION_V1] this machine's locators: each device's IPv4 addresses, "rloc.<dev>=a.b.c.d/len;" --
    // stated by the machine itself, as its own truth, beside its devices; rewritten whenever the kernel reports a change
    if (ifaddrs* ia = nullptr; getifaddrs(&ia) == 0) {
        for (ifaddrs* a = ia; a; a = a->ifa_next) {
            if (!a->ifa_addr || a->ifa_addr->sa_family != AF_INET || !a->ifa_netmask) continue;
            char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(a->ifa_addr)->sin_addr, ip, sizeof ip);
            const uint32_t m = ntohl(reinterpret_cast<sockaddr_in*>(a->ifa_netmask)->sin_addr.s_addr); int len = 0; for (uint32_t b = m; b & 0x80000000u; b <<= 1) ++len;
            out += "rloc." + std::string(a->ifa_name) + "=" + ip + "/" + std::to_string(len) + ";";
        }
        freeifaddrs(ia);
    }
    return out;
}
// ---- [RMT_OVER_DIF_LINKS_V1] this machine's relaying and multiplexing task ---------------------------------------------
constexpr uint16_t RMT_UDP_PORT = 0x0d1f;                 // the shim-udp4's port in rlite
static const char* const AREA_KEY = "area";                  // [MATRIX_DISCOVERY_V1] machine-level routes live under this "DIF"
static std::atomic<uint64_t> g_fwd{0}, g_local{0}, g_noroute{0}, g_senderr{0}, g_other{0}, g_ttl{0};
// (DNS is not asked: [LISP_RLOCS_IN_THE_REGION_V1] locators come from each machine's own record)
// the RLOCs a machine stated: (address, prefix length)
// the next machine's locator toward which this machine has a link: one on a subnet one of mine is on
// one PDU: to a program on this machine, or out of the link its route gives
// ---- [RTR_LINK_V1] (DESIGN-NAT.md steps 4-5) the link between a machine behind NAT and its RTR --------------------
// The NATed machine opens a TCP connection UP to the elected RTR (connections are up only; the NAT keeps the binding
// because the connection is open) and says which machine it is. Frames: 4-byte length, then the PDU. Liveness is the
// connection: its end is said at once, at both ends; the NATed machine dials again on that event (no timer).
// forward(): a NATed machine sends every PDU not for itself UP its link; an RTR sends a PDU for a machine that has a
// link to it DOWN that link, before any route. What arrives on a link is forwarded like anything else.
constexpr uint16_t RMT_TCP_PORT = 0x0d1f;
// [RTR_LINK_SAYS_WHY_V1] every link line carries a wall-clock stamp (ms; both machines' logs order against each other
// and the wire), the connection's two ports, and the exact reason a link ended
static void forward(const char* p, size_t n);
static void on_subscribe(uint32_t pid);   // [LIBRARY_REASSERTS_V1] below
static void forward(const char* p, size_t n) {          // [AGENT_IS_LOCAL_ONLY_V1] a program's subscription, nothing else
    if (n < rmt::HDR) return;
    if (uint8_t(p[0]) == 5) { const uint32_t pid = rmt::dest_of(p).pid; std::thread(on_subscribe, pid).detach(); return; }
    static std::atomic<bool> said{false};
    if (!said.exchange(true)) fprintf(stderr, "rlite-uipcps: [AGENT_IS_LOCAL_ONLY_V1] a PDU of type %u was handed to the agent; it carries nothing between machines any more\n", unsigned(uint8_t(p[0])));
}
// RINA_RMT_STATS=FILE: each agent appends what its RMT did when it ends (a measurement aid)
static void rmt_stats(const std::string& ns) {
    const char* f = getenv("RINA_RMT_STATS"); if (!f || !*f) return;
    if (FILE* o = fopen(f, "a")) { fprintf(o, "%s forwarded=%llu delivered_here=%llu no_route=%llu send_errors=%llu not_ours=%llu hop_limit=%llu\n", ns.c_str(),
        (unsigned long long)g_fwd.load(), (unsigned long long)g_local.load(), (unsigned long long)g_noroute.load(), (unsigned long long)g_senderr.load(), (unsigned long long)g_other.load(), (unsigned long long)g_ttl.load()); fclose(o); } }   // [RTR_LINK_V1] the link's counts
static void rx(int fd) { std::vector<char> b(70000); for (;;) { const ssize_t n = ::recv(fd, b.data(), b.size(), 0);
    if (n < 0) { if (errno == EINTR) continue; fprintf(stderr, "rlite-uipcps: receiving failed: %s\n", strerror(errno)); _exit(3); }   // [NO_FALLBACKS] said
    forward(b.data(), size_t(n)); } }
// the DIF's Ethernet links: only frames addressed to THIS machine's link (a bridge floods frames for addresses it has not
// learned to every port; relaying those copies duplicated and looped PDUs -- rlite test 0026, four machines on one bridge)
// ---- [LAN_JOIN_V1] (D5) self-joining on a LAN ----------------------------------------------------------------------
// A machine with nothing configured has an agent that asks its links, once per link as each comes up: an Ethernet
// broadcast (ethertype 0xD1F0, type 4). Any member's agent on the link answers (type 5) with the area it is in -- the
// locator it reached its region by. The agent then joins that area; the machine's programs ask their own agent
// (abstract socket "rina-area") and need nothing configured either. No timer: a link coming up is the event to ask on.
static std::string g_area;                                  // set once
static std::atomic<uint32_t> g_area_known{0};               // 1 once g_area is set (local askers wait on it)
// [LAN_JOIN_ANSWERS_NAME_V1] what a member tells its links is the area's NAME (RINA_AREA[:port]), which the asker resolves
// where it reaches the region from -- not this member's own locator, which may mean nothing on the asker (a member that
// reaches its region by loopback: the RAM host itself, or a local tunnel end). A member that knows only a loopback
// locator does not answer, and says so once (no fallback: another member, or the name, must answer).
static std::string g_answer;                                // what this member answers on its links; empty: it does not
static std::atomic<uint32_t> g_unanswered_said{0};
static bool loopback_locator(const std::string& hp) {
    const size_t c = hp.rfind(':'); const std::string h = hp.substr(0, c);
    return h.rfind("127.", 0) == 0 || h == "localhost" || h == "::1" || h == "[::1]";
}
static bool g_area_served = false;
static void serve_area_locally() {                          // this machine's programs: "?" -> the area
    g_area_served = true;
    int s = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0); sockaddr_un a; const socklen_t al = rmt::abstract(a, "rina-area");
    if (s < 0 || ::bind(s, reinterpret_cast<sockaddr*>(&a), al) != 0) { fprintf(stderr, "rlite-uipcps: the area socket: %s\n", strerror(errno)); return; }
    std::thread([s] { char q[16]; for (;;) { sockaddr_un from{}; socklen_t fl = sizeof from;
        if (::recvfrom(s, q, sizeof q, 0, reinterpret_cast<sockaddr*>(&from), &fl) < 0) { if (errno == EINTR) continue; return; }
        g_area_known.wait(0, std::memory_order_acquire);   // answered once this machine knows its area
        ::sendto(s, g_area.data(), g_area.size(), 0, reinterpret_cast<sockaddr*>(&from), fl); } }).detach();
}
// ask every up link which area this is; ask again on each link that comes up (netlink tells us); return the first answer
// [AGENT_IS_LOCAL_ONLY_V1] (plan item 4c) the agent no longer carries anything between machines: no Ethernet or UDP
// links, no routes, no relay. All it keeps is this machine's local socket, where its programs subscribe to be told the
// RAM manager is back ([LIBRARY_REASSERTS_V1]).
static void start_rmt(const std::function<void(const std::string&)>& fail) {
    int loc = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    sockaddr_un a; const socklen_t al = rmt::abstract(a, rmt::agent_name());
    if (loc < 0 || ::bind(loc, reinterpret_cast<sockaddr*>(&a), al) != 0) fail(std::string("the agent's local socket: ") + strerror(errno));
    std::thread(rx, loc).detach();
}


// ---- [AGENT_SURVIVES_THE_REGION_V1] / [AGENT_REASSERTS_V1] / [HOST_CHANGE_BY_NEIGHBOUR_PROPAGATION_V1] ----------------
static std::string g_ns;
static std::atomic<rina_region::Client*> g_ram{nullptr};   // the session to the area's region; null while there is none
static std::mutex g_mine_mu; static std::string g_mine;    // this machine's own records, as last held (a JSON array)
static double state(rina_region::Client& ram) {
    frogram::Json r = ram.call("{\"op\":\"node\",\"ns\":" + frogram::Json::quote(g_ns) + ",\"devs\":" + frogram::Json::quote(devices()) + "}");
    if (r["rc"].n != 0) throw std::runtime_error("rejoin: " + r["err"].s);
    return r["after"].n;
}
static void refresh_mine(rina_region::Client& ram) {       // [AGENT_REASSERTS_V1] on every topology signal: the copy stays current
    frogram::Json r = ram.call("{\"op\":\"mine\",\"ns\":" + frogram::Json::quote(g_ns) + "}");
    if (r["ok"].type == frogram::Json::Bool && !r["ok"].b) throw std::runtime_error("mine: " + r["error"].s);
    std::lock_guard<std::mutex> l(g_mine_mu); g_mine = r["recs_text"].s;
}
// [FDF_DISCOVERY_V1] (plan item 1) this machine's announcement: its name (hostname), its own non-loopback IPv4 addresses,
// and its capability as FrogNet's capability probe measures it (RINA_CAPABILITY_PROBE; run once, its JSON kept verbatim).
// Written on every attach and again when an address changes. No probe, or a probe that fails: said, nothing announced.
static std::string g_cap;
static std::string capability() {
    if (!g_cap.empty()) return g_cap;
    const char* p = getenv("RINA_CAPABILITY_PROBE");
    if (!p || !*p) { fprintf(stderr, "rlite-uipcps: [FDF_DISCOVERY_V1] RINA_CAPABILITY_PROBE is not set: this machine is NOT announced\n"); return ""; }
    std::string out; if (FILE* f = popen((std::string("bash ") + p + " databasehost 2>/dev/null").c_str(), "r")) {
        char b[4096]; size_t n; while ((n = fread(b, 1, sizeof b, f)) > 0) out.append(b, n);
        if (pclose(f) != 0) out.clear(); }
    while (!out.empty() && (out.back() == '\n' || out.back() == ' ')) out.pop_back();
    try { (void)frogram::Json::parse(out); } catch (const std::exception&) { out.clear(); }
    if (out.empty() || out[0] != '{') { fprintf(stderr, "rlite-uipcps: [FDF_DISCOVERY_V1] the capability probe %s gave no capability: this machine is NOT announced\n", p); return ""; }
    return g_cap = out;
}
static void announce(rina_region::Client& c) {
    const std::string cap = capability(); if (cap.empty()) return;
    char host[256] = "?"; gethostname(host, sizeof host - 1);
    std::string ips = "[";
    if (ifaddrs* ia = nullptr; getifaddrs(&ia) == 0) { bool f = true;
        for (ifaddrs* a = ia; a; a = a->ifa_next) {
            if (!a->ifa_addr || a->ifa_addr->sa_family != AF_INET || (a->ifa_flags & IFF_LOOPBACK)) continue;
            char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(a->ifa_addr)->sin_addr, ip, sizeof ip);
            ips += (f ? "" : ",") + frogram::Json::quote(ip); f = false; }
        freeifaddrs(ia); }
    ips += "]";
    frogram::Json r = c.call("{\"op\":\"announce\",\"ns\":" + frogram::Json::quote(g_ns) + ",\"name\":" + frogram::Json::quote(host) + ",\"ips\":" + ips + ",\"cap\":" + frogram::Json::quote(cap) + "}");
    if (!r["ok"].b) fprintf(stderr, "rlite-uipcps: [FDF_DISCOVERY_V1] the announcement was refused: %s %s\n", r["error"].s.c_str(), r["why"].s.c_str());
    else fprintf(stderr, "rlite-uipcps: [FDF_DISCOVERY_V1] announced %s at %s\n", host, ips.c_str());
}
static void waiter(rina_region::Client* c, uint64_t at);
static void wake_subscribers();   // [LIBRARY_REASSERTS_V1] below
// connect, re-assert what this machine owns (if it held anything), state its node, derive routes, hold the wait
static void attach() {
    rina_region::Client* c = new rina_region::Client;          // throws if the region cannot be reached: said by the caller
    std::string mine; { std::lock_guard<std::mutex> l(g_mine_mu); mine = g_mine; }
    if (!mine.empty() && mine != "[]") {
        frogram::Json r = c->call("{\"op\":\"reassert\",\"ns\":" + frogram::Json::quote(g_ns) + ",\"recs\":" + mine + "}");
        if (r["ok"].type == frogram::Json::Bool && !r["ok"].b) throw std::runtime_error("re-asserting this machine's records: " + r["error"].s + " " + r["record"].s);
        fprintf(stderr, "rlite-uipcps: [AGENT_REASSERTS_V1] re-asserted %d record(s) this machine owns\n", int(r["n"].n));
    }
    const double after = state(*c);
    announce(*c);                                              // [FDF_DISCOVERY_V1]
    refresh_mine(*c);
    g_ram.store(c, std::memory_order_release);
    std::thread(waiter, c, uint64_t(after)).detach();
    wake_subscribers();                                        // [LIBRARY_REASSERTS_V1]
}
// [LIBRARY_REASSERTS_V1] programs on this machine that lost the region wait to be told the host changed: a type-4 PDU
// on their own data endpoint. Answered at once if this agent holds a live session (checked by stating its node, not
// assumed), else after its next successful attach.
static std::mutex g_sub_mu; static std::set<uint32_t> g_subs;
static void wake(uint32_t pid) {
    static thread_local int out = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    char h[rmt::HDR]; rmt::Addr a; a.node = rmt::my_node(); a.pid = pid; rmt::header(h, 4, a, 0);
    sockaddr_un sa; const socklen_t al = rmt::abstract(sa, rmt::ep_name(pid));
    if (::sendto(out, h, sizeof h, MSG_NOSIGNAL, reinterpret_cast<sockaddr*>(&sa), al) < 0)
        fprintf(stderr, "rlite-uipcps: [LIBRARY_REASSERTS_V1] telling program %u the host changed failed: %s\n", pid, strerror(errno));
    else fprintf(stderr, "rlite-uipcps: [LIBRARY_REASSERTS_V1] told program %u the host changed\n", pid);
}
static void wake_subscribers() { std::set<uint32_t> s; { std::lock_guard<std::mutex> l(g_sub_mu); s.swap(g_subs); } for (uint32_t p : s) wake(p); }
static void on_subscribe(uint32_t pid) {
    if (rina_region::Client* c = g_ram.load(std::memory_order_acquire)) {
        try { state(*c); wake(pid); return; } catch (const std::exception&) {} }   // no live session: wait for the next attach
    std::lock_guard<std::mutex> l(g_sub_mu); g_subs.insert(pid);
    fprintf(stderr, "rlite-uipcps: [LIBRARY_REASSERTS_V1] program %u waits for the host\n", pid);
}
// [MAP_WATCH_V1] the RAM manager is gone: wait on the MAPPING for it (its own re-registration, on a restart or a move,
// is the event), then re-attach by name. The watch is set BEFORE each attempt, so a registration made between a failed
// attempt and the wait cannot be missed. Needs the area's name and the mapping region: RINA_AREA, RINA_MAP_RESOLVER.
static void rejoin_on_mapping() {
    const char* area = getenv("RINA_AREA");
    if (!area || !*area) { fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] cannot wait on the mapping: RINA_AREA is not set; this machine stays detached\n"); return; }
    const std::string name = std::string(area).substr(0, std::string(area).find(':'));
    std::string mrs;                                           // [MR_FROM_THE_AREA_NAME_V1] the first map-resolver that takes the watch
    try { mrs = rina_region::map_resolvers(name).front(); }
    catch (const std::exception& e) { fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] %s; this machine stays detached\n", e.what()); return; }
    const char* mr = mrs.c_str();
    const std::string me = "mw:" + std::to_string(getpid());
    for (uint64_t after = 0;;) {
        try {
            rina_region::Client w(std::string(mr) + ":" + std::to_string(rina_region::MAP_PORT));
            frogram::Json r = w.call("{\"op\":\"map_watch\",\"name\":" + frogram::Json::quote(name) + ",\"proc\":" + frogram::Json::quote(me) + "}");
            if (!r["ok"].b) { fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] the mapping region refused the watch: %s\n", r["error"].s.c_str()); return; }
            for (;;) {
                rina_region::Client::forget_location();               // [LOCATE_CACHED_V1] it may have moved: resolve afresh
                try { attach(); fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] re-attached to the area by name after the RAM manager registered\n"); return; }
                catch (const std::exception& e) { fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] not attached yet (%s); waiting for the RAM manager to register\n", e.what()); }
                const std::string text = w.held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote(me) + ",\"after\":" + std::to_string((unsigned long long)after) + "}", 3.0e8);
                for (size_t p = 2; p < text.size();) { const size_t tab = text.find('\t', p), nl = text.find('\n', p); if (tab == std::string::npos || nl == std::string::npos) break;
                    after = std::max<uint64_t>(after, std::stoull(text.substr(p, tab - p))); p = nl + 1; }
            }
        } catch (const std::exception& e) {
            fprintf(stderr, "rlite-uipcps: [MAP_WATCH_V1] the mapping region is unreachable (%s); this machine stays detached\n", e.what()); return; }
    }
}
static void waiter(rina_region::Client* c, uint64_t at) {     // the one held read: terminate ends it; "t" re-derives the routes
    for (;;) {
        std::string text;
        try { text = c->held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote("agent:" + g_ns) + ",\"after\":" + std::to_string((unsigned long long)at) + "}", 3.0e8); }
        catch (const std::exception& e) {                       // [NO_FALLBACKS] losing the region is not a terminate -- and not an end
            rina_region::Client* expect = c; g_ram.compare_exchange_strong(expect, nullptr);
            fprintf(stderr, "rlite-uipcps: [AGENT_SURVIVES_THE_REGION_V1] the region is gone (%s); waiting on the mapping for the RAM manager\n", e.what());
            rmt_stats(g_ns); std::thread(rejoin_on_mapping).detach(); return; }                         // the old session object stays allocated: its threads may still hold it
        bool topo = false, end = false;
        for (size_t p = 2; p < text.size();) { const size_t tab = text.find('\t', p), nl = text.find('\n', p); if (tab == std::string::npos || nl == std::string::npos) break;
            at = std::stoull(text.substr(p, tab - p)); const std::string ev = text.substr(tab + 1, nl - tab - 1);
            if (ev.find("\"k\":\"t\"") != std::string::npos) topo = true; else end = true;
            p = nl + 1; }
        if (end) { rmt_stats(g_ns); _exit(0); }
        if (topo) { try { refresh_mine(*c); } catch (const std::exception& e) { fprintf(stderr, "rlite-uipcps: deriving routes: %s\n", e.what()); } }
    }
}
// [RTR_LINK_V1] after each attach and each topology signal: an RTR-capable machine accepts links; a NATed one links up
// to the elected RTR (if it has none). Event-driven: no timer.

int main(int argc, char** argv) {
    bool bg = argc > 1 && !strcmp(argv[1], "-d");
    int ready[2] = {-1, -1};
    if (bg) {
        if (pipe(ready) != 0) { perror("pipe"); return 1; }
        const pid_t c = fork(); if (c < 0) { perror("fork"); return 1; }
        if (c > 0) { ::close(ready[1]); char x = 0; return (::read(ready[0], &x, 1) == 1 && x == 'k') ? 0 : 1; }
        ::close(ready[0]); setsid();
        int nul = ::open("/dev/null", O_RDWR); if (nul >= 0) { dup2(nul, 0); dup2(nul, 1); dup2(nul, 2); }
        // [AGENT_LOGS_KEPT_V1] RINA_AGENT_LOG=<dir>: this agent's stderr goes to <dir>/agent.<machine>.log (a member's
        // refusal to answer, the probe's verdict, the elected resolver), instead of being lost
        if (const char* d = getenv("RINA_AGENT_LOG"); d && *d) {
            std::string f = std::string(d) + "/agent." + netns() + ".log";
            for (size_t i = strlen(d) + 1; i < f.size(); ++i) if (f[i] == '/' || f[i] == ':') f[i] = '_';
            int lf = ::open(f.c_str(), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644); if (lf >= 0) { dup2(lf, 2); ::close(lf); } }
    }
    auto fail = [&](const std::string& m) { fprintf(stderr, "rlite-uipcps: %s\n", m.c_str()); if (bg) { char f = 'f'; if (::write(ready[1], &f, 1) != 1) {} } _exit(1); };
    // [ONE_AGENT_PER_MACHINE_V1] an abstract socket name is per network namespace: a new agent waits for the old one
    int lock = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    { sockaddr_un a{}; a.sun_family = AF_UNIX; const char nm[] = "\0rina-node-agent"; memcpy(a.sun_path, nm, sizeof nm - 1);
      const socklen_t len = socklen_t(offsetof(sockaddr_un, sun_path) + sizeof nm - 1); int tries = 0;
      // [NO_FALLBACKS] no timed waiting for an old agent: one still running here is an error, said at once
      (void)tries; if (::bind(lock, reinterpret_cast<sockaddr*>(&a), len) != 0) fail(errno == EADDRINUSE ? "another agent still runs on this machine" : std::string("its lock: ") + strerror(errno)); }
    const std::string ns = netns();
    // [LAN_JOIN_V1] the area: configured (RINA_RAM / RINA_AREA), or asked of the links
    const char* cfg_area = getenv("RINA_AREA"); const char* cfg_port = getenv("RINA_AREA_PORT");
    // [AREA_NAME_IS_A_LISP_EID_V1] a member that resolves its area through LISP tells its links where: "name:port@map-resolver"
    const char* cfg_mr = getenv("RINA_MAP_RESOLVER");
    const std::string named = (cfg_area && *cfg_area) ? std::string(cfg_area) + ":" + (cfg_port && *cfg_port ? cfg_port : "8840") + (cfg_mr && *cfg_mr ? std::string("@") + cfg_mr : std::string()) : std::string();
    if (const char* r = getenv("RINA_RAM"); r && *r) { g_area = r; g_answer = !named.empty() ? named : (loopback_locator(g_area) ? std::string() : g_area); }
    else if (!named.empty()) { g_area = named; g_answer = named; }
    else fail("no area: give this machine the area's name (RINA_AREA) and the mapping region (RINA_MAP_RESOLVER), or RINA_RAM; "
              "it no longer asks its links (John, 2026-10-07: no direct propagation between machines)");   // [LAN_JOIN_V1] retired
    if (!g_area.empty()) { g_area_known.store(1, std::memory_order_release); g_area_known.notify_all(); }
    // ---- [AGENT_SURVIVES_THE_REGION_V1] (experiment 5b) ------------------------------------------------------------
    // The agent no longer ends when the region goes. It says so, keeps the routes it last derived (flows keep moving
    // member to member), and waits for a host-change announcement ([HOST_CHANGE_BY_NEIGHBOUR_PROPAGATION_V1]): then it
    // re-attaches -- re-asserting what it owns ([AGENT_REASSERTS_V1]), stating its node, re-deriving its routes.
    g_ns = ns;
    int nl = ::socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_ROUTE);
    sockaddr_nl sa{}; sa.nl_family = AF_NETLINK; sa.nl_groups = RTMGRP_LINK | RTMGRP_IPV4_IFADDR;   // [LISP_RLOCS_IN_THE_REGION_V1] a re-address is a new truth
    if (nl < 0 || ::bind(nl, reinterpret_cast<sockaddr*>(&sa), sizeof sa) != 0) fail(std::string("netlink: ") + strerror(errno));
    try { attach(); }                                              // stated (and rejoined) before -d returns
    catch (const std::exception& e) { fail(e.what()); }
    start_rmt(fail);                                               // [RMT_OVER_DIF_LINKS_V1]
    if (!g_area_served) serve_area_locally();                      // [LAN_JOIN_V1] this machine's programs ask here
    if (bg) { char k = 'k'; if (::write(ready[1], &k, 1) != 1) {} ::close(ready[1]); }
    char buf[8192];
    for (;;) {                                                     // the kernel tells us; we tell the region (when there is one)
        if (::recv(nl, buf, sizeof buf, 0) <= 0) fail("netlink closed");
        while (::recv(nl, buf, sizeof buf, MSG_DONTWAIT) > 0) {}
        if (rina_region::Client* c = g_ram.load(std::memory_order_acquire)) {
            try { state(*c); announce(*c); } catch (const std::exception& e) { fprintf(stderr, "rlite-uipcps: stating a device change: %s\n", e.what()); } }   // [FDF_DISCOVERY_V1] new IPs
    }
}
