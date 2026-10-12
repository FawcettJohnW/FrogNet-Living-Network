// rina-lisp -- [AREA_NAME_IS_A_LISP_EID_V1] the region host's side of D5 through LISP.
//
// FD&F's own mapping, IN A REGION over the session (TCP) -- [AREA_MAPPING_IN_THE_REGION_V1]:
//   rina-lisp map-site NAME KEY MAPPING-HOST            the site (its authentication key) for an area name
//   rina-lisp map-register NAME RLOC MAPPING-HOST KEY TTL_SECONDS
//   rina-lisp map-resolve NAME MAPPING-HOST
// lispers.net's wire (UDP 4342): EDGE INTEROP AND THE ORACLE ONLY, never FD&F's own control path:
//   rina-lisp register NAME RLOC MAP-SERVER KEY TTL_SECONDS
//   rina-lisp resolve NAME MAP-RESOLVER
//   rina-lisp seen HOST PORT                      (where that host sees this machine's session from)
//
// Sends ONE Map-Register (the area's distinguished-name EID -> RLOC, P/T/M bits) to the Map-Server and waits for its
// Map-Notify. No retransmission timer: if the Map-Notify does not come, it says it is waiting, once, and keeps
// waiting (a lost UDP control packet is a transport question, recorded as open in RESULTS.md, not hidden here).
#include "lisp_area.hpp"
#include "rina_region_client.hpp"
#include <openssl/hmac.h>
#include <sys/socket.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <random>

static int die(const std::string& m) { fprintf(stderr, "rina-lisp: %s\n", m.c_str()); return 1; }

int main(int argc, char** argv) {
    const std::string cmd = argc > 1 ? argv[1] : "";
    try {
        if (cmd == "map-site" && argc == 5) {
            frogram::Json r = rina_region::map_call(argv[4], "{\"op\":\"map_site\",\"name\":" + frogram::Json::quote(argv[2]) + ",\"key\":" + frogram::Json::quote(argv[3]) + "}");
            if (!r["ok"].b) return die("map-site: " + r["error"].s);
            printf("site '%s' on the mapping region at %s\n", argv[2], argv[4]); return 0; }
        if (cmd == "map-register" && argc == 7) {                 // [AREA_HOST_REGISTERS_ITSELF_V1] the shared request
            const auto t0 = std::chrono::steady_clock::now();
            rina_region::map_register(argv[2], argv[3], argv[4], argv[5], unsigned(std::stoul(argv[6])));
            printf("registered '%s' -> %s in the mapping region at %s (%.3f ms, over the session)\n", argv[2], argv[3], argv[4],
                   std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count()); return 0; }
        if (cmd == "mr-address" && argc == 3) {                    // [MR_ADDRESS_FROM_NAME_V1] rina-lisp mr-address NAME
            printf("%s\n%s\n", rina_region::mr_address_v4(argv[2]).c_str(), rina_region::mr_address_v6(argv[2]).c_str()); return 0; }
        if (cmd == "map-wait" && argc == 4) {                      // [MAP_WATCH_V1] rina-lisp map-wait NAME MAP-RESOLVER
            const std::string to = rina_region::map_wait(argv[2], argv[3]);
            printf("'%s' was registered again: -> %s\n", argv[2], to.c_str()); return 0; }
        if (cmd == "seen" && argc == 4) {                          // [SESSION_PEER_V1] rina-lisp seen HOST PORT: where it sees us from
            frogram::Session ses(argv[2], std::stoi(argv[3]), rina_region::api());
            frogram::Json r = ses.call("POST", rina_region::api() + "?op=rina", "{\"op\":\"seen\"}");
            printf("%s:%s sees this machine at %s\n", argv[2], argv[3], r["addr"].s.c_str()); return 0; }
        if (cmd == "map-resolve" && argc == 4) {
            const auto t0 = std::chrono::steady_clock::now(); const std::string rloc = rina_region::map_lookup(argv[2], argv[3]);
            printf("'%s' -> %s (%.3f ms, over the session)\n", argv[2], rloc.c_str(), std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count()); return 0; }
    } catch (const std::exception& e) { return die(e.what()); }
    if (argc == 4 && std::string(argv[1]) == "resolve") {             // rina-lisp resolve NAME MAP-RESOLVER
        try { double ms = 0; const std::string rloc = lisp_area::resolve(argv[2], argv[3], &ms);
              printf("'%s' -> %s (Map-Reply in %.3f ms)\n", argv[2], rloc.c_str(), ms); return 0; }
        catch (const std::exception& e) { return die(e.what()); } }
    if (argc != 7 || std::string(argv[1]) != "register") return die("usage: rina-lisp register NAME RLOC MAP-SERVER KEY TTL_SECONDS | rina-lisp resolve NAME MAP-RESOLVER");
    lisp_area::Register r; r.name = argv[2]; r.key = argv[5]; r.ttl_seconds = uint32_t(std::stoul(argv[6]));
    if (inet_pton(AF_INET, argv[3], &r.rloc) != 1) return die(std::string("RLOC is not an IPv4 address: ") + argv[3]);
    sockaddr_in ms{}; ms.sin_family = AF_INET; ms.sin_port = htons(lisp_area::CTRL_PORT);
    if (inet_pton(AF_INET, argv[4], &ms.sin_addr) != 1) return die(std::string("MAP-SERVER is not an IPv4 address: ") + argv[4]);
    r.nonce = std::random_device{}() | uint64_t(std::random_device{}()) << 32;
    lisp_area::Bytes pkt;
    try { pkt = lisp_area::encode_register(r); } catch (const std::exception& e) { return die(e.what()); }
    int s = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (s < 0) return die(std::string("socket: ") + strerror(errno));
    const auto t0 = std::chrono::steady_clock::now();
    if (::sendto(s, pkt.data(), pkt.size(), 0, reinterpret_cast<sockaddr*>(&ms), sizeof ms) < 0) return die(std::string("sending the Map-Register: ") + strerror(errno));
    fprintf(stderr, "rina-lisp: Map-Register sent: '%s' -> %s, ttl %us, to %s:%u (%zu bytes); waiting for its Map-Notify\n",
            r.name.c_str(), argv[3], r.ttl_seconds, argv[4], lisp_area::CTRL_PORT, pkt.size());
    uint8_t b[2048];
    for (;;) {
        sockaddr_in from{}; socklen_t fl = sizeof from;
        const ssize_t n = ::recvfrom(s, b, sizeof b, 0, reinterpret_cast<sockaddr*>(&from), &fl);
        if (n < 0) { if (errno == EINTR) continue; return die(std::string("receiving: ") + strerror(errno)); }
        const std::string why = lisp_area::check_notify(b, size_t(n), r.nonce, r.key);
        if (why.rfind("rejected:", 0) == 0) return die("the Map-Server " + std::string(argv[4]) + " refused '" + r.name + "': " + why.substr(10));
        if (!why.empty()) { fprintf(stderr, "rina-lisp: ignored a %zd-byte packet from %s: %s\n", n, inet_ntoa(from.sin_addr), why.c_str()); continue; }
        const double ms_taken = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        printf("registered '%s' -> %s (Map-Notify from %s in %.3f ms)\n", r.name.c_str(), argv[3], inet_ntoa(from.sin_addr), ms_taken);
        return 0;
    }
}
