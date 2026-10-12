// machine_id.hpp -- [MACHINE_IDENTITY_V1] FD&F build step 2: a member is a MACHINE, named by a GUID, not by the name of a
// Linux network namespace.
//
//   RINA_MACHINE_ID set       that, exactly (a deployment can name its machines itself)
//   otherwise                 "m:" + the machine's GUID: /etc/fnid (FrogNet's machine GUID) if present, else
//                             /etc/machine-id (systemd's)
//   always                    "/" + the inode of this process's network namespace is appended. In the one-machine
//                             suite each namespace stands for a separate machine and must be one; on a real machine
//                             every process is in the initial namespace, whose inode is a kernel constant, so the
//                             identity is the same for every process and across boots. (Comparing with pid 1's
//                             namespace does not work: unprivileged programs, and this sandbox, cannot read it.)
//
// The same identity names the machine in the region (its IPCPs, its registrations, its agent, its flows' ends) and on
// the data plane (rmt::my_node()). A machine's identity no longer changes when Linux renumbers a namespace's name, and
// two machines can no longer collide because their namespaces happened to get the same name.
#pragma once
#include <cstdlib>
#include <fstream>
#include <string>
#include <sys/stat.h>

namespace rina_id {
inline std::string read_first_line(const char* path) {
    std::ifstream f(path); std::string s; if (f) std::getline(f, s);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
    return s;
}
inline std::string machine_id() {
    static const std::string id = [] {
        if (const char* e = getenv("RINA_MACHINE_ID")) if (*e) return std::string(e);
        std::string g = read_first_line("/etc/fnid");
        if (g.empty()) g = read_first_line("/etc/machine-id");
        if (g.empty()) g = "unknown";
        std::string out = "m:" + g;
        struct stat mine{};
        if (stat("/proc/self/ns/net", &mine) == 0) out += "/" + std::to_string((unsigned long long)mine.st_ino);
        return out;
    }();
    return id;
}
}  // namespace rina_id
