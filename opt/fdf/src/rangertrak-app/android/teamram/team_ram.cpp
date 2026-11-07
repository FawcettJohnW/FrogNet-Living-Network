// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// team_ram.cpp -- [TEAM_RAM_V1] the team's RAM on the team lead's phone: the Ribbit platform's RAM host
// (opt/frognet_semantic/ribbit/cpp, ribbit::RamHost), with no region, answering the endpoint every FD&F client and the
// Team app already use. Nothing else: cells written, read, held and removed; a write that says "own":true goes with
// its session, any other stays ([OWN_FLAG_DECIDES_V1], fdf-ram's contract). Built static (musl) for each Android ABI by build.sh and shipped in the app's jniLibs as
// libteamram.so; the app runs it from nativeLibraryDir, as it runs libfdftun.so.
//
//     libteamram.so [--listen HOST:PORT]        default 0.0.0.0:21800: the hotspot, loopback and the tunnel alike
#include <cstdio>
#include <cstring>
#include <vector>
#include "ram_host.hpp"

#ifndef TEAM_RAM_BUILD
#define TEAM_RAM_BUILD "team-ram-dev"
#endif

class TeamRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "rangertrak-team"; }
    std::string vendor_version() const override { return TEAM_RAM_BUILD; }
    std::string api_path() const override { return "/FawcettInnovations.RINA.ram_interface.php"; }
    std::string default_listen() const override { return "0.0.0.0:21800"; }
    bool own_flag_decides() const override { return true; }   // [OWN_FLAG_DECIDES_V1] the contract the Team app was written to
};

// --api PATH is taken as fdf-ram takes it (the incumbent tests start every host that way), and must name this
// host's endpoint: any other is refused, never served under a different name.
int main(int argc, char** argv) {
    TeamRam host; std::vector<char*> a;
    for (int i = 0; i < argc; ++i) {
        if (!strcmp(argv[i], "--api") && i + 1 < argc) {
            if (host.api_path() != argv[i + 1]) { fprintf(stderr, "team-ram: --api %s: this host answers %s only\n", argv[i + 1], host.api_path().c_str()); return 2; }
            ++i; continue;
        }
        a.push_back(argv[i]);
    }
    return host.run(int(a.size()), a.data());
}
