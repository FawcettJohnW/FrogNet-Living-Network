// rina_region_client.hpp -- a participant's ONE permanent connection to the RINA region (v0.63 frogram::Session over
// FNW1, the Redis front's client). Every conversation is one call: op=rina (inline) or op=rina_block (a held read on
// the memory itself, answered when its answer exists). The session is the participant's liveness.
#pragma once
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <cstddef>
#include <cstring>
#include <sched.h>
#include <unistd.h>
#include <memory>
#include <stdexcept>
#include <string>
#include <mutex>
#include <openssl/crypto.h>
#include <openssl/sha.h>
#include <arpa/nameser.h>
#include <resolv.h>
#include <algorithm>
#include <vector>
#include "frogram.hpp"
#include "lisp_area.hpp"
namespace rina_region {
inline const std::string& api() { static const std::string a = "/FawcettInnovations.RINA.ram_interface.php"; return a; }
// [AREA_MAPPING_IN_THE_REGION_V1] LISP's mapping in a region, over the session (TCP; liveness is the session): the
// map-resolver machine's mapping region listens on its RLOC at MAP_PORT (LISP's control port number, over TCP).
constexpr int MAP_PORT = 4342;
inline frogram::Json map_call(const std::string& mr, const std::string& body) {
    frogram::Session s(mr, MAP_PORT, api());
    try { return s.call("POST", api() + "?op=rina", body); }
    catch (const frogram::Refused& e) {                         // an answer of ok:false is the answer
        const std::string w = e.what(), pre = "HTTP 200: "; if (w.compare(0, pre.size(), pre) != 0) throw;
        return frogram::Json::parse("{\"ok\":false,\"error\":" + frogram::Json::quote(w.substr(pre.size())) + "}"); }
}
// the area's name -> its region host's RLOC; a negative answer (unknown or expired name) is said, never a fallback
inline std::string map_lookup(const std::string& name, const std::string& mr) {
    frogram::Json r = map_call(mr, "{\"op\":\"map_request\",\"name\":" + frogram::Json::quote(name) + "}");
    if (!r["ok"].b || r["rloc"].s.empty()) throw std::runtime_error("rina: the mapping region at " + mr + " has no RLOC for '" + name + "' (" + r["error"].s + ")");
    return r["rloc"].s;
}
// [MR_ADDRESS_FROM_NAME_V1] (John, 2026-10-07: option B; experiments/DESIGN-MR-RULE.md) where the area's Map-Resolver is,
// computed from the area's name alone: h = SHA-256(name); the address is a fixed prefix plus h's leading bits. Every
// machine gets the same answer; nothing configured, no DNS. The machine serving the area's mapping binds that address.
inline std::string mr_address_v4(const std::string& name) {      // 198.18.0.0/15 + the first 17 bits of h (not .0 or .255)
    unsigned char h[32]; SHA256(reinterpret_cast<const unsigned char*>(name.data()), name.size(), h);
    uint32_t v = ((uint32_t(h[0]) << 9) | (uint32_t(h[1]) << 1) | (h[2] >> 7)) & 0x1ffff;
    if ((v & 0xff) == 0) v |= 1; else if ((v & 0xff) == 0xff) v ^= 1;
    const uint32_t a = 0xC6120000u + v; char b[INET_ADDRSTRLEN]; in_addr ia; ia.s_addr = htonl(a); inet_ntop(AF_INET, &ia, b, sizeof b); return b;
}
inline std::string mr_address_v6(const std::string& name) {      // fd46:4446::/32 + the first 96 bits of h
    unsigned char h[32]; SHA256(reinterpret_cast<const unsigned char*>(name.data()), name.size(), h);
    in6_addr a{}; a.s6_addr[0] = 0xfd; a.s6_addr[1] = 0x46; a.s6_addr[2] = 0x44; a.s6_addr[3] = 0x46; memcpy(a.s6_addr + 4, h, 12);
    char b[INET6_ADDRSTRLEN]; inet_ntop(AF_INET6, &a, b, sizeof b); return b;
}
// [MR_ADDRESS_FROM_NAME_V1] the map-resolvers for an area: RINA_MAP_RESOLVER if a deployment sets one explicitly
// (an override), otherwise the address DERIVED from the area's name by the rule above. No DNS (John, 2026-10-07:
// option B replaces the earlier SRV record, [MR_FROM_THE_AREA_NAME_V1]).
inline std::vector<std::string> map_resolvers(const std::string& area) {
    if (const char* m = getenv("RINA_MAP_RESOLVER"); m && *m) return {m};
    return {mr_address_v4(area)};
}
// [AREA_HOST_REGISTERS_ITSELF_V1] one authenticated registration of the area's name -> RLOC, with its own TTL, over the
// session; the request the region checks (HMAC-SHA-256 of name|rloc|ttl|ts with the area's password; ts orders them).
// Throws with the region's refusal (EPERM wrong key, ENOENT no site, EALREADY not newer) -- said, never a fallback.
inline void map_register(const std::string& name, const std::string& rloc, const std::string& mr, const std::string& key, unsigned ttl_s) {
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t); char ts[40]; snprintf(ts, sizeof ts, "%lld.%09ld", (long long)t.tv_sec, t.tv_nsec);
    const std::string ttl = std::to_string(ttl_s), msg = name + "|" + rloc + "|" + ttl + "|" + ts;
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string auth; for (unsigned i = 0; i < n; ++i) { auth += hx[md[i] >> 4]; auth += hx[md[i] & 15]; }
    frogram::Json r = map_call(mr, "{\"op\":\"map_register\",\"name\":" + frogram::Json::quote(name) + ",\"rloc\":" + frogram::Json::quote(rloc) +
        ",\"ttl\":" + frogram::Json::quote(ttl) + ",\"ts\":" + frogram::Json::quote(ts) + ",\"auth\":" + frogram::Json::quote(auth) + "}");
    if (!r["ok"].b) throw std::runtime_error("the mapping region at " + mr + " refused '" + name + "' -> " + rloc + ": " + r["error"].s + " " + r["why"].s);
}
// the capability probe for the map-resolver role: does a mapping region on `ip` answer the area's name positively?
inline bool probe_mapping(const std::string& name, const std::string& ip, std::string* why) {
    try { map_lookup(name, ip); return true; } catch (const std::exception& e) { if (why) *why = e.what(); return false; }
}
class Client {
public:
    // [TRANSPORT_INTERFACE_V1] LOCATE: where the area's RAM manager is, "host:port" -- RINA_RAM outright, or the area's
    // name (RINA_AREA, or this machine's agent), resolved through LISP when a Map-Resolver is known
    // [LOCATE_CACHED_V1] one lookup per process, not one per Client(): the answer is kept until it may be wrong --
    // forgotten when a connection to it fails, and by forget_location() before any re-attach after losing the RAM
    // manager (the agent's rejoin, a program's reattach), which then resolve afresh.
    static std::mutex& located_mu() { static std::mutex m; return m; }
    static std::string& located() { static std::string s; return s; }
    static void forget_location() { std::lock_guard<std::mutex> l(located_mu()); located().clear(); }
    static std::string locate() {
        { std::lock_guard<std::mutex> l(located_mu()); if (!located().empty()) return located(); }
        const std::string w = locate_afresh();
        { std::lock_guard<std::mutex> l(located_mu()); located() = w; }
        return w;
    }
    static std::string locate_afresh() {
        // [OPENSSL_NO_ATEXIT_V1] the session's threads hash every request (SHA-256) until the process is gone. OpenSSL's
        // default atexit cleanup frees its locks while they still run, and a request in flight at exit then crashes in
        // CRYPTO_THREAD_read_lock(NULL) (cores from rinaperf: exit_now in thread 1, Session::send_loop -> SHA256 in
        // another). The same crash Mode B fixed in its client; this client had lost the fix.
        // [ONE_NAME_BOOTSTRAP_V1] (D5) where the area's region is: RINA_RAM ("host:port") if a deployment names it outright;
        // otherwise the ONE well-known name of the area, RINA_AREA, resolved where the region is reached from, at the area's
        // port (RINA_AREA_PORT, default 8840). The name is all a machine is given; nothing else about the area is configured.
        std::string s;
        if (const char* a = getenv("RINA_RAM"); a && *a) s = a;
        else if (const char* area = getenv("RINA_AREA"); area && *area) {
            const char* ap = getenv("RINA_AREA_PORT"); s = std::string(area) + ":" + (ap && *ap ? ap : "8840");
        } else {
            // [LAN_JOIN_V1] third in the order: this machine's agent, which knows (or learns from its links) the area
            int q = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
            sockaddr_un me{}; me.sun_family = AF_UNIX;                  // autobind: an abstract name for the answer
            sockaddr_un ag{}; ag.sun_family = AF_UNIX; const char nm[] = "\0rina-area"; memcpy(ag.sun_path, nm, sizeof nm - 1);
            const socklen_t al = socklen_t(offsetof(sockaddr_un, sun_path) + sizeof nm - 1);
            char buf[256]; ssize_t n = -1;
            if (q >= 0 && ::bind(q, reinterpret_cast<sockaddr*>(&me), sizeof(sa_family_t)) == 0 && ::sendto(q, "?", 1, 0, reinterpret_cast<sockaddr*>(&ag), al) == 1)
                n = ::recv(q, buf, sizeof buf, 0);
            if (q >= 0) ::close(q);
            if (n <= 0) throw std::runtime_error("rina: no RINA_RAM, no RINA_AREA, and this machine's agent did not say which area (is rlite-uipcps running?)");
            s.assign(buf, size_t(n));
        }
        // [AREA_NAME_IS_A_LISP_EID_V1] the area's name resolves through LISP when a Map-Resolver is known: from the answer
        // itself ("name:port@map-resolver", what a member tells its links) or RINA_MAP_RESOLVER. Then LISP is the ONLY way
        // the name is resolved -- no hosts file, no DNS, and no falling back to them. Without a Map-Resolver the name goes to
        // the system resolver as before. Either way the choice is configuration, said here, never a retry of the other.
        std::string mr;
        if (const size_t at = s.find('@'); at != std::string::npos) { mr = s.substr(at + 1); s.resize(at); }
        else if (const char* m = getenv("RINA_MAP_RESOLVER"); m && *m) mr = m;
        if (in_addr lit{}; mr.empty() && s.rfind(':') != std::string::npos && inet_pton(AF_INET, s.substr(0, s.rfind(':')).c_str(), &lit) != 1)
            mr = mr_address_v4(s.substr(0, s.rfind(':')));      // [MR_ADDRESS_FROM_NAME_V1] none configured: derived from the name (IPv4 form: no IPv6 here)
        const size_t c = s.rfind(':');
        if (c == std::string::npos) throw std::runtime_error("rina: the area '" + s + "' has no port");
        std::string host = s.substr(0, c);
        if (in_addr probe{}; !mr.empty() && inet_pton(AF_INET, host.c_str(), &probe) != 1) {     // [AREA_MAPPING_IN_THE_REGION_V1]
            const char* rn = getenv("RINA_RAM_NETNS");
            int here = ::open("/proc/self/ns/net", O_RDONLY | O_CLOEXEC), root = ::open(rn && *rn ? rn : "/proc/1/ns/net", O_RDONLY | O_CLOEXEC);
            const bool hop = here >= 0 && root >= 0 && setns(root, CLONE_NEWNET) == 0;
            try { host = map_lookup(host, mr); } catch (...) { if (hop) setns(here, CLONE_NEWNET); if (here >= 0) ::close(here); if (root >= 0) ::close(root); throw; }
            if (hop) setns(here, CLONE_NEWNET);
            if (here >= 0) ::close(here);
            if (root >= 0) ::close(root);
        }
        return host + s.substr(c);
    }
    // [TRANSPORT_INTERFACE_V1] Client() = Client(locate()): where the area is, then the socket to it
    Client() : Client(locate()) {}
    explicit Client(const std::string& where) {
        static const bool ssl_once = [] { return OPENSSL_init_crypto(OPENSSL_INIT_NO_ATEXIT, nullptr) == 1; }();
        if (!ssl_once) throw std::runtime_error("rina: OpenSSL did not initialise");
        const size_t c = where.rfind(':');
        if (c == std::string::npos) throw std::runtime_error("rina: '" + where + "' has no port");
        const std::string host = where.substr(0, c); const int port = atoi(where.c_str() + c + 1);
        // the RAM host is reached from its own network (RINA_RAM_NETNS, else pid 1's); the connection stays open after
        const char* rn = getenv("RINA_RAM_NETNS");
        int here = ::open("/proc/self/ns/net", O_RDONLY | O_CLOEXEC), root = ::open(rn && *rn ? rn : "/proc/1/ns/net", O_RDONLY | O_CLOEXEC);
        const bool hop = here >= 0 && root >= 0 && setns(root, CLONE_NEWNET) == 0;
        try {
            s_.reset(new frogram::Session(host, port, api())); }
        catch (...) { if (hop) setns(here, CLONE_NEWNET); if (here >= 0) ::close(here); if (root >= 0) ::close(root); forget_location(); throw; }   // [LOCATE_CACHED_V1]
        if (hop) setns(here, CLONE_NEWNET);
        if (here >= 0) ::close(here);
        if (root >= 0) ::close(root);
    }
    // [REGION_ANSWER_IS_DATA_V1] frogram::Session::call throws Refused for {"ok":false,...} even with HTTP 200. A region
    // answer of ok:false (ENOENT, EHOSTUNREACH, NEEDIP ...) is the operation's result, for the caller to act on -- it is
    // handed back as that answer. Anything else (HTTP >= 400, a broken session) still throws.
    frogram::Json call(const std::string& body) { return answer([&] { return s_->call("POST", api() + "?op=rina", body); }); }
    void post(const std::string& body) { s_->post("POST", api() + "?op=rina", body); }   // sent, not waited for
    frogram::Stats stats() const { return s_->stats(); }     // the session's wire counters
    frogram::Json held(const std::string& body, double t) { return answer([&] { return s_->call("POST", api() + "?op=rina_block", body, t); }); }
    // [WAIT_ANSWER_IS_TEXT_V1] a held call whose answer is the region's own text (the wait's event lines), not JSON
    std::string held_text(const std::string& body, double t) { return s_->call_text("POST", api() + "?op=rina_block", body, t); }
    // [CALL_TEXT_OVER_SESSION_V1] a plain (non-held) call whose answer comes back as its raw text, over THIS session --
    // so a held-read owner (fdf-events) can pull a snapshot (participants/world) on its own one connection, never a
    // second channel. Same op=rina path as call(); the text is the region's reply verbatim, for the caller to ship on.
    std::string call_text(const std::string& body, double t = 15.0) { return s_->call_text("POST", api() + "?op=rina", body, t); }
private:
    template <class F> static frogram::Json answer(F f) {
        try { return f(); }
        catch (const frogram::Refused& e) {
            const std::string w = e.what(), pre = "HTTP 200: ";
            if (w.compare(0, pre.size(), pre) != 0) throw;
            return frogram::Json::parse("{\"ok\":false,\"error\":" + frogram::Json::quote(w.substr(pre.size())) + "}");
        }
    }
    std::unique_ptr<frogram::Session> s_;
};
// [MAP_WATCH_V1] wait for the next registration of `name` in the mapping region at `mr` (the RAM manager's own, on
// its restart or move), then return the address it now resolves to. Held: one watch, one wait -- no timer, no poll.
inline std::string map_wait(const std::string& name, const std::string& mr) {
    Client c(mr + ":" + std::to_string(MAP_PORT));
    const std::string me = "mw:" + std::to_string(getpid());
    frogram::Json w = c.call("{\"op\":\"map_watch\",\"name\":" + frogram::Json::quote(name) + ",\"proc\":" + frogram::Json::quote(me) + "}");
    if (!w["ok"].b) throw std::runtime_error("the mapping region at " + mr + " refused the watch: " + w["error"].s);
    (void)c.held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote(me) + ",\"after\":0}", 3.0e8);
    return map_lookup(name, mr);
}
}  // namespace rina_region
