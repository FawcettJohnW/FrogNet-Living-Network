// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// fdf_attention.cpp -- [ATTENTION_IS_A_TUPLE_V1] (PLAN-PONDS §3.4) "something happening in a pond that needs attention can
// say so. This is just a tuple." (John, 2026-10-10). One write to the pond's RAM:
//     (pond, attention, w:<this machine's guid>)   bag {"why":..., "from":<this machine's name>, "ts":...}
// Every participant of that pond holds a read on (pond, attention) and says it the moment it is written. The notice is
// this MEMBER's (its ns, as fdf-client announces it), so the region removes it when the member's session ends.
//   usage: fdf-attention "<why>"        (RINA_RAM = the pond's RAM manager, as fdf-client@<area> has it)
//          fdf-attention --clear        removes this member's notices in that pond
#include "frogram.hpp"
#include "fdf_version.hpp"
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>

static const char* API = "/FawcettInnovations.RINA.ram_interface.php";

int main(int argc, char** argv) {
    const char* e = getenv("RINA_RAM"); const std::string where = e ? e : ""; const size_t c = where.rfind(':');
    if (argc < 2 || where.empty() || c == std::string::npos) {
        fprintf(stderr, "usage: RINA_RAM=<host:port> fdf-attention \"<why>\" | --clear\n"); return 2; }
    const char* fe = getenv("FDF_FACTS"); std::ifstream in(fe && *fe ? fe : "/opt/fdf/var/facts.json");
    std::stringstream b; b << in.rdbuf(); std::string name, guid;
    try { frogram::Json f = frogram::Json::parse(b.str()); name = f["name"].s; guid = f["guid"].s; } catch (...) {}
    if (guid.empty()) { fprintf(stderr, "fdf-attention: no guid in facts.json: this machine is not a member (nothing written)\n"); return 1; }
    const std::string ns = "w:" + guid;
    try {
        frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), API); frogram::Memory m(s, API);
        if (std::string(argv[1]) == "--clear") {
            int n = 0; for (auto& cell : m.read("pond", "attention", ns)) { m.remove(cell.id); ++n; }
            printf("fdf-attention: build %s; %d notice(s) of %s removed at %s\n", FDF_BUILD_TAG, n, ns.c_str(), where.c_str()); return 0;
        }
        const uint64_t id = m.write("pond", "attention", ns, "{\"why\":" + frogram::Json::quote(argv[1]) + ",\"from\":" +
                                    frogram::Json::quote(name.empty() ? ns : name) + ",\"ts\":" + std::to_string(time(nullptr)) + "}");
        printf("fdf-attention: build %s; notice %llu from %s at %s: %s\n", FDF_BUILD_TAG, (unsigned long long)id, name.c_str(), where.c_str(), argv[1]);
        return 0;
    } catch (const std::exception& x) { fprintf(stderr, "fdf-attention: %s: %s\n", where.c_str(), x.what()); return 1; }
}
