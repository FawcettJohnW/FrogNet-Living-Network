// rmt.hpp -- [RMT_OVER_DIF_LINKS_V1] FD&F build step 1, second piece: SDUs follow the DIF's own links, relayed hop by hop.
//
// The DIF's members on a machine (its namespace, in the one-machine suite) are served by that machine's agent, which is
// the relaying and multiplexing task (RMT) for every normal IPCP there. A program's flow end is addressed by
//   dif   -- the DIF the flow is in (routes are per DIF: a flow never crosses into another DIF's links)
//   node  -- the machine (namespace) its program runs on
//   pid   -- the program, whose data endpoint on that machine is the abstract unix socket "rina-ep-<pid>"
//   port  -- the flow end within the program
// written as its locator "rmt:<dif>:<node>:<pid>:<port>" in the allocation exchange, by each end, as its own truth.
//
// A PDU to an end on the same machine goes straight to that program's endpoint. Any other PDU goes to the machine's
// agent ("rina-rmt"), which sends it out of the link its routes give for that DIF and machine, as an Ethernet frame
// (ethertype 0xD1F0, the shim-eth's), and every agent on the way does the same until the PDU reaches its machine.
// The routes are derived by each agent from the DIF's records: the adjacencies (written by enrollment and lower-flow
// allocation) and each machine's own statement of its devices -- up or down, and their link addresses.
//
// The PDU:  type (1) | ttl (1) | dif (4) | node (4) | pid (4) | port (4) | seq (8) | payload
//           type: 1 DATA, 2 CREDIT, 3 CREDIT_REQ; all fields little-endian.
//           ttl: [HOP_LIMIT_V1] each agent that relays a PDU to another machine takes one off and drops it at 0. Routes
//           are derived by each agent independently, so while a change is converging two agents can briefly point at
//           each other; the hop limit ends such a loop. 64, as rlite's normal IPCP (ttl 64).
#pragma once
#include "machine_id.hpp"
#include <cstdint>
#include <cstring>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace rmt {
constexpr size_t HDR = 26;
constexpr uint8_t TTL = 64;
constexpr uint16_t ETHERTYPE = 0xD1F0;
inline uint32_t id_of(const std::string& s) {           // FNV-1a, 32 bits: a DIF name or a machine (namespace) name
    uint32_t h = 2166136261u; for (unsigned char c : s) { h ^= c; h *= 16777619u; } return h ? h : 1; }
inline std::string netns_name() { char b[128] = {0}; ssize_t n = readlink("/proc/self/ns/net", b, sizeof b - 1); return n > 0 ? std::string(b, size_t(n)) : "net"; }
inline uint32_t my_node() { static const uint32_t n = id_of(rina_id::machine_id()); return n; }   // [MACHINE_IDENTITY_V1]
struct Addr { uint32_t dif = 0, node = 0, pid = 0, port = 0; };
inline void put32(char* p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = char((v >> (8 * i)) & 0xff); }
inline void put64(char* p, uint64_t v) { for (int i = 0; i < 8; ++i) p[i] = char((v >> (8 * i)) & 0xff); }
inline uint32_t get32(const char* p) { uint32_t v = 0; for (int i = 3; i >= 0; --i) v = (v << 8) | uint8_t(p[i]); return v; }
inline uint64_t get64(const char* p) { uint64_t v = 0; for (int i = 7; i >= 0; --i) v = (v << 8) | uint8_t(p[i]); return v; }
inline void header(char* h, uint8_t type, const Addr& to, uint64_t seq) {
    h[0] = char(type); h[1] = char(TTL); put32(h + 2, to.dif); put32(h + 6, to.node); put32(h + 10, to.pid); put32(h + 14, to.port); put64(h + 18, seq); }
inline Addr dest_of(const char* h) { Addr a; a.dif = get32(h + 2); a.node = get32(h + 6); a.pid = get32(h + 10); a.port = get32(h + 14); return a; }
inline uint64_t seq_of(const char* h) { return get64(h + 18); }
// abstract unix socket addresses (per network namespace, as the machine is)
inline socklen_t abstract(sockaddr_un& a, const std::string& name) {
    a = sockaddr_un{}; a.sun_family = AF_UNIX; a.sun_path[0] = '\0'; memcpy(a.sun_path + 1, name.data(), name.size());
    return socklen_t(offsetof(sockaddr_un, sun_path) + 1 + name.size()); }
inline std::string ep_name(uint32_t pid) { return "rina-ep-" + std::to_string(pid); }
inline const std::string& agent_name() { static const std::string n = "rina-rmt"; return n; }
// "rmt:<dif>:<node>:<pid>:<port>" <-> Addr
inline std::string loc_of(const Addr& a) { return "rmt:" + std::to_string(a.dif) + ":" + std::to_string(a.node) + ":" + std::to_string(a.pid) + ":" + std::to_string(a.port); }
inline bool parse(const std::string& s, Addr& a) {
    if (s.compare(0, 4, "rmt:") != 0) return false;
    unsigned long v[4]; if (sscanf(s.c_str() + 4, "%lu:%lu:%lu:%lu", &v[0], &v[1], &v[2], &v[3]) != 4) return false;
    a.dif = uint32_t(v[0]); a.node = uint32_t(v[1]); a.pid = uint32_t(v[2]); a.port = uint32_t(v[3]); return true; }
}  // namespace rmt
