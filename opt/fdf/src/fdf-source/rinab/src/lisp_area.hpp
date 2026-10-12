// lisp_area.hpp -- [AREA_NAME_IS_A_LISP_EID_V1] (FD&F D5 through LISP)
//
// An area's one well-known name is a LISP distinguished-name EID (AFI 17, the name and a NUL; mask-len is 8 x the
// name's length, as lispers.net's lisp_address.host_mask_len). The area's region host REGISTERS that EID with the
// mapping system, its RLOC as the one locator; a machine RESOLVES it with a Map-Request and connects to the RLOC in
// the Map-Reply at the area's port. The mapping system is LISP's own (here unmodified lispers.net as Map-Server and
// Map-Resolver): nothing in this file invents a protocol.
//
// Wire references (lispers.net lisp.py): lisp_map_register.encode (first long, nonce in NATIVE byte order as
// struct.pack("Q"), key-id, alg-id, auth-len, auth zeroed then HMAC over the whole message), lisp_eid_record.encode,
// lisp_rloc_record.encode, lisp_eid_record.store_ttl (TTL with the high bit set is in seconds), lisp_map_notify,
// lisp_ecm, lisp_map_request, lisp_map_reply.
//
// Registration lifetime: [REGISTER_ONCE_WITH_ITS_OWN_TTL_V1] the T bit (0x800, use-TTL-for-timeout) makes the
// Map-Server expire the registration by the record's own TTL, so the host registers ONCE and re-registers only when
// the host changes (the host-change path), never on a timer.
#pragma once
#include <arpa/inet.h>
#include <netinet/in.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <sys/socket.h>
#include <poll.h>
#include <unistd.h>
#include <cerrno>
#include <cstdio>
#include <ctime>
#include <random>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace lisp_area {

using Bytes = std::vector<uint8_t>;
constexpr uint16_t AFI_IPV4 = 1, AFI_NAME = 17;
constexpr uint16_t CTRL_PORT = 4342;

inline void put16(Bytes& o, uint16_t v) { o.push_back(uint8_t(v >> 8)); o.push_back(uint8_t(v)); }
inline void put32(Bytes& o, uint32_t v) { put16(o, uint16_t(v >> 16)); put16(o, uint16_t(v)); }
inline void put64_native(Bytes& o, uint64_t v) { uint8_t b[8]; memcpy(b, &v, 8); o.insert(o.end(), b, b + 8); }   // lispers.net: struct.pack("Q")
inline uint16_t get16(const uint8_t* p) { return uint16_t(p[0] << 8 | p[1]); }
inline uint32_t get32(const uint8_t* p) { return uint32_t(get16(p)) << 16 | get16(p + 2); }
inline uint64_t get64_native(const uint8_t* p) { uint64_t v; memcpy(&v, p, 8); return v; }

// AFI 17: the name, NUL-terminated
inline void put_name_eid(Bytes& o, const std::string& name) { put16(o, AFI_NAME); o.insert(o.end(), name.begin(), name.end()); o.push_back(0); }
inline uint8_t name_mask_len(const std::string& name) {
    if (name.empty() || name.size() > 31) throw std::runtime_error("an area name must be 1 to 31 characters (mask-len is 8 x length, one byte)");
    return uint8_t(name.size() * 8);
}

// ---- Map-Register (type 3): one record, the area's name -> the host's RLOC -----------------------------------------
struct Register {
    std::string name;          // the area's well-known name
    in_addr rloc{};            // the region host's RLOC
    std::string key;           // the site's authentication-key
    uint64_t nonce = 0;
    uint32_t ttl_seconds = 0;  // the registration's own lifetime (T bit)
};
inline Bytes encode_register(const Register& r) {
    Bytes o; o.reserve(4 + 8 + 4 + 32 + 10 + 2 + r.name.size() + 1 + 12);   // [PREALLOCATE_KNOWN_SHAPE] the message's exact size
    const uint32_t first = (3u << 28) | 0x08000000u /*P: proxy-reply*/ | 0x800u /*T: use TTL for timeout*/ | 0x100u /*M: want Map-Notify*/ | 1u;
    put32(o, first); put64_native(o, r.nonce);
    o.push_back(0);            // key-id 0: the site's (only) authentication-key
    o.push_back(2);            // alg-id 2: HMAC-SHA-256-128
    put16(o, 32); o.insert(o.end(), 32, 0);                         // auth data, zeroed for the HMAC
    put32(o, 0x80000000u | (r.ttl_seconds & 0x7fffffffu));         // record TTL in seconds
    o.push_back(1);                                                 // locator count
    o.push_back(name_mask_len(r.name));
    put16(o, 0x1000);                                               // A: authoritative
    put16(o, 0);                                                    // map-version
    put_name_eid(o, r.name);
    o.push_back(1); o.push_back(100); o.push_back(255); o.push_back(0);   // priority, weight, m-priority, m-weight
    put16(o, 0x0005);                                               // L (this registrant's own locator) + R (reachable)
    put16(o, AFI_IPV4); const uint8_t* a = reinterpret_cast<const uint8_t*>(&r.rloc.s_addr); o.insert(o.end(), a, a + 4);
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    if (!HMAC(EVP_sha256(), r.key.data(), int(r.key.size()), o.data(), o.size(), md, &n) || n != 32) throw std::runtime_error("map-register hmac failed");
    memcpy(o.data() + 16, md, 32);
    return o;
}
// Map-Notify (type 4) answering our Map-Register. Returns "" if it is that Map-Notify AND it is signed with our key;
// "rejected: ..." if it is ours but not signed with our key; else why it is not ours.
// [MAP_NOTIFY_MUST_BE_SIGNED_WITH_OUR_KEY_V1] lispers.net acknowledges a Map-Register whose authentication FAILED with a
// Map-Notify all the same ("Authentication failed ... Send Map-Notify to ack Map-Register"), so the arrival of a
// Map-Notify proves nothing. Its HMAC (the site's key over the message, auth zeroed) does: a registrant holding the
// site's key reproduces it; one with the wrong key does not, and is told it was refused.
inline std::string check_notify(const uint8_t* p, size_t n, uint64_t nonce, const std::string& key) {
    if (n < 16) return "short packet";
    if ((p[0] >> 4) != 4) return "not a Map-Notify (type " + std::to_string(p[0] >> 4) + ")";
    if (get64_native(p + 4) != nonce) return "Map-Notify for another nonce";
    if (p[13] != 2 || get16(p + 14) != 32 || n < 48) return "rejected: the Map-Notify is not HMAC-SHA-256 signed (alg " + std::to_string(p[13]) + ")";
    Bytes z(p, p + n); std::fill(z.begin() + 16, z.begin() + 48, 0);
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int ml = 0;
    if (!HMAC(EVP_sha256(), key.data(), int(key.size()), z.data(), z.size(), md, &ml) || ml != 32) return "rejected: hmac failed";
    if (memcmp(md, p + 16, 32) != 0) return "rejected: the Map-Server's Map-Notify is not signed with our key (it refused the registration)";
    return "";
}

// ---- Map-Request (type 1) for the area's name, ECM-encapsulated to a Map-Resolver ----------------------------------
// [NAME_LOOKUP_ECM_INNER_IPV4_V1] An ECM carries an inner IP/UDP header. For an IP EID its destination is the EID; a
// name EID has no IP form, so the inner destination is the Map-Resolver itself (RFC 9301 sec. 5.8 leaves it to the
// EID family; the MR routes on the Map-Request's EID record). lispers.net's own lig/ITR pass the name as the inner
// destination and fail to encode (lisp_ecm.encode: neither IPv4 nor IPv6 -> str + bytes), recorded for Dino.
inline uint16_t ip_checksum(const uint8_t* p, size_t n) {
    uint32_t sum = 0; for (size_t i = 0; i + 1 < n; i += 2) sum += uint32_t(p[i] << 8 | p[i + 1]);
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return uint16_t(~sum);
}
inline Bytes encode_request_ecm(const std::string& name, in_addr itr_rloc, uint16_t itr_port, in_addr mr, uint64_t nonce) {
    Bytes req; req.reserve(4 + 8 + 2 + 6 + 2 + 2 + name.size() + 1);    // [PREALLOCATE_KNOWN_SHAPE]
    put32(req, (1u << 28) | (0u << 8) /*IRC 0: one ITR-RLOC*/ | 1u /*one record*/);
    put64_native(req, nonce);
    put16(req, 0);                                                  // source-EID: none (AFI 0)
    put16(req, AFI_IPV4); const uint8_t* a = reinterpret_cast<const uint8_t*>(&itr_rloc.s_addr); req.insert(req.end(), a, a + 4);
    req.push_back(0);                                               // reserved / subscribe
    req.push_back(0);                                               // mask-len: 0 for a non-binary EID (lispers.net)
    put_name_eid(req, name);
    Bytes o; o.reserve(4 + 20 + 8 + req.size());                        // [PREALLOCATE_KNOWN_SHAPE]
    put32(o, 8u << 28);                                             // ECM, no S/D/E/M bits
    const size_t ip0 = o.size();
    o.push_back(0x45); o.push_back(0); put16(o, uint16_t(20 + 8 + req.size())); put16(o, 0); put16(o, 0);
    o.push_back(64); o.push_back(17); put16(o, 0);                  // TTL, UDP, checksum (below)
    o.insert(o.end(), a, a + 4); const uint8_t* m = reinterpret_cast<const uint8_t*>(&mr.s_addr); o.insert(o.end(), m, m + 4);
    const uint16_t ck = ip_checksum(o.data() + ip0, 20); o[ip0 + 10] = uint8_t(ck >> 8); o[ip0 + 11] = uint8_t(ck);
    put16(o, itr_port); put16(o, CTRL_PORT); put16(o, uint16_t(8 + req.size())); put16(o, 0);
    o.insert(o.end(), req.begin(), req.end());
    return o;
}
// ---- Map-Reply (type 2): the RLOCs of the record for our name ------------------------------------------------------
struct Reply { std::string eid; uint32_t ttl_seconds = 0; int action = 0; std::vector<std::pair<in_addr, uint8_t>> rlocs; /* address, priority */ };
// Returns "" and fills out if p is the Map-Reply to nonce for name; else why not.
inline std::string decode_reply(const uint8_t* p, size_t n, uint64_t nonce, const std::string& name, Reply& out) {
    if (n < 12) return "short packet";
    if ((p[0] >> 4) != 2) return "not a Map-Reply (type " + std::to_string(p[0] >> 4) + ")";
    if (get64_native(p + 4) != nonce) return "Map-Reply for another nonce";
    if (p[3] < 1) return "Map-Reply with no record";
    size_t o = 12;
    if (n < o + 12) return "truncated record";
    const uint32_t ttl = get32(p + o); out.ttl_seconds = (ttl & 0x80000000u) ? (ttl & 0x7fffffffu) : ttl * 60;
    const int locs = p[o + 4]; out.action = p[o + 6] >> 5; o += 10;
    const uint16_t afi = get16(p + o); o += 2;
    if (afi != AFI_NAME) return "record EID is AFI " + std::to_string(afi) + ", not a name";
    const void* z = memchr(p + o, 0, n - o); if (!z) return "unterminated name EID";
    out.eid.assign(reinterpret_cast<const char*>(p + o), static_cast<const uint8_t*>(z) - (p + o)); o = size_t(static_cast<const uint8_t*>(z) - p) + 1;
    if (out.eid != name) return "record is for '" + out.eid + "', not '" + name + "'";
    for (int i = 0; i < locs; ++i) {
        if (n < o + 8) return "truncated locator";
        const uint8_t pri = p[o]; const uint16_t lafi = get16(p + o + 6); o += 8;
        if (lafi != AFI_IPV4) return "locator AFI " + std::to_string(lafi) + " (only IPv4 RLOCs here)";
        if (n < o + 4) return "truncated IPv4 locator";
        in_addr a; memcpy(&a.s_addr, p + o, 4); o += 4; out.rlocs.push_back({a, pri});
    }
    return "";
}

// ---- resolve: the area's name -> its region host's RLOC, through the Map-Resolver -----------------------------------
// One ECM'd Map-Request; then wait for the Map-Reply (held, no timer -- [NO_FALLBACKS]; if it does not come the
// caller waits, and says so once). The best (lowest-priority-value) RLOC is the answer. A negative reply (no RLOCs)
// is an error, said.
inline std::string resolve(const std::string& name, const std::string& map_resolver, double* ms_taken = nullptr) {
    in_addr mr{}; if (inet_pton(AF_INET, map_resolver.c_str(), &mr) != 1) throw std::runtime_error("map-resolver is not an IPv4 address: " + map_resolver);
    sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(CTRL_PORT); to.sin_addr = mr;
    in_addr me{};                                                   // the ITR-RLOC: this machine's address toward the MR
    { int t = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0); sockaddr_in l{}; socklen_t ll = sizeof l;
      if (t < 0 || ::connect(t, reinterpret_cast<sockaddr*>(&to), sizeof to) != 0 || ::getsockname(t, reinterpret_cast<sockaddr*>(&l), &ll) != 0) {
          const int e = errno; if (t >= 0) ::close(t); throw std::runtime_error(std::string("no route to the map-resolver: ") + strerror(e)); }
      me = l.sin_addr; ::close(t); }
    int s = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0); sockaddr_in b{}; b.sin_family = AF_INET; b.sin_addr = me; socklen_t bl = sizeof b;
    if (s < 0 || ::bind(s, reinterpret_cast<sockaddr*>(&b), sizeof b) != 0 || ::getsockname(s, reinterpret_cast<sockaddr*>(&b), &bl) != 0) {
        const int e = errno; if (s >= 0) ::close(s); throw std::runtime_error(std::string("the requester's socket: ") + strerror(e)); }
    const uint64_t nonce = std::random_device{}() | uint64_t(std::random_device{}()) << 32;
    const Bytes pkt = encode_request_ecm(name, me, ntohs(b.sin_port), mr, nonce);
    struct timespec t0; clock_gettime(CLOCK_MONOTONIC, &t0);
    if (::sendto(s, pkt.data(), pkt.size(), 0, reinterpret_cast<sockaddr*>(&to), sizeof to) < 0) { const int e = errno; ::close(s); throw std::runtime_error(std::string("sending the Map-Request: ") + strerror(e)); }
    uint8_t buf[2048]; bool said = false;
    for (;;) {
        sockaddr_in from{}; socklen_t fl = sizeof from;
        const ssize_t n = ::recvfrom(s, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&from), &fl);
        if (n < 0) { if (errno == EINTR) continue; const int e = errno; ::close(s); throw std::runtime_error(std::string("receiving the Map-Reply: ") + strerror(e)); }
        Reply r; const std::string why = decode_reply(buf, size_t(n), nonce, name, r);
        if (!why.empty()) { if (!said) { fprintf(stderr, "lisp_area: ignored a packet while resolving '%s': %s\n", name.c_str(), why.c_str()); said = true; } continue; }
        ::close(s);
        if (r.rlocs.empty()) throw std::runtime_error("the mapping system has no RLOC for '" + name + "' (negative Map-Reply, action " + std::to_string(r.action) + ")");
        size_t best = 0; for (size_t i = 1; i < r.rlocs.size(); ++i) if (r.rlocs[i].second < r.rlocs[best].second) best = i;
        if (ms_taken) { struct timespec t1; clock_gettime(CLOCK_MONOTONIC, &t1); *ms_taken = double(t1.tv_sec - t0.tv_sec) * 1e3 + double(t1.tv_nsec - t0.tv_nsec) / 1e6; }
        char txt[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &r.rlocs[best].first, txt, sizeof txt); return txt;
    }
}

// ---- [MR_IS_A_CAPABILITY_V1] the capability probe for the map-resolver role --------------------------------------
// Does a Map-Resolver at `ip` answer a Map-Request for this area's name with a positive mapping? Measured, as FrogNet's
// capability probe measures mysql_running rather than mysql installed. A connected socket: no listener at ip:4342 is
// an immediate ECONNREFUSED. bound_ms bounds the measurement itself (FrogNet's probe runs under timeout=10); it is not
// a retry -- one Map-Request, one verdict.
inline bool probe_resolver(const std::string& name, const std::string& ip, int bound_ms, std::string* why = nullptr) {
    auto no = [&](const std::string& w) { if (why) *why = w; return false; };
    in_addr mr{}; if (inet_pton(AF_INET, ip.c_str(), &mr) != 1) return no("not an IPv4 address");
    int s = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0); if (s < 0) return no(strerror(errno));
    sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(CTRL_PORT); to.sin_addr = mr;
    sockaddr_in me{}; socklen_t ml = sizeof me;
    if (::connect(s, reinterpret_cast<sockaddr*>(&to), sizeof to) != 0 || ::getsockname(s, reinterpret_cast<sockaddr*>(&me), &ml) != 0) { const std::string e = strerror(errno); ::close(s); return no(e); }
    const uint64_t nonce = std::random_device{}() | uint64_t(std::random_device{}()) << 32;
    const Bytes pkt = encode_request_ecm(name, me.sin_addr, ntohs(me.sin_port), mr, nonce);
    if (::send(s, pkt.data(), pkt.size(), 0) < 0) { const std::string e = strerror(errno); ::close(s); return no(e); }
    pollfd pf{s, POLLIN, 0}; uint8_t buf[2048];
    for (;;) {
        const int r = ::poll(&pf, 1, bound_ms);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) { ::close(s); return no(r == 0 ? "no answer within the probe's measurement bound" : strerror(errno)); }
        const ssize_t n = ::recv(s, buf, sizeof buf, 0);
        if (n < 0) { const std::string e = strerror(errno); ::close(s); return no(e); }   // ECONNREFUSED: nothing listens
        Reply rep; if (!decode_reply(buf, size_t(n), nonce, name, rep).empty()) continue;
        ::close(s);
        if (rep.rlocs.empty()) return no("it answers, but has no mapping for the area");
        return true;
    }
}

}  // namespace lisp_area
