// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// fdf_attention.cpp -- [ATTENTION_IS_A_TUPLE_V1] (PLAN-PONDS §3.4) "something happening in a pond that needs attention can
// say so. This is just a tuple." (John, 2026-10-10). One write to the pond's RAM:
//     (pond, attention, w:<this machine's guid>)   bag {"why":..., "from":<this machine's name>, "ts":...}
// Every participant of that pond holds a read on (pond, attention) and says it the moment it is written. The notice is
// this MEMBER's (its ns), and [ONE_OWNER_V1] written own:true on this program's OWN session, which it holds: the notice
// is there while this program runs and goes with it -- stopped (--clear), killed, or its network gone. Nobody else writes
// or sweeps it.
//   usage: fdf-attention "<why>"        (RINA_RAM = the pond's RAM manager, as fdf-client@<area> has it); runs until cleared
//          fdf-attention --clear        ends the holder of this member's notice in that pond (its session ends: it goes)
#include "frogram.hpp"
#include "fdf_version.hpp"
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>
#include <csignal>
#include <unistd.h>

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
        const char* ve = getenv("FDF_VAR"); const std::string pidf = std::string(ve && *ve ? ve : "/opt/fdf/var") + "/attention-" + where + ".pid";
        if (std::string(argv[1]) == "--clear") {
            std::ifstream pf(pidf); long pid = 0; pf >> pid;
            if (pid > 0 && kill(pid_t(pid), SIGTERM) == 0) { printf("fdf-attention: build %s; the notice's holder (pid %ld) ended: its notice goes with its session\n", FDF_BUILD_TAG, pid); return 0; }
            printf("fdf-attention: build %s; no notice of %s held here at %s\n", FDF_BUILD_TAG, ns.c_str(), where.c_str()); return 0;
        }
        s.call("POST", std::string(API) + "?op=write", "{\"service\":\"pond\",\"variable\":\"attention\",\"instance\":" + frogram::Json::quote(ns) +
               ",\"bag\":{\"why\":" + frogram::Json::quote(argv[1]) + ",\"from\":" + frogram::Json::quote(name.empty() ? ns : name) +
               ",\"ts\":" + std::to_string(time(nullptr)) + "},\"own\":true}");
        { std::ofstream o(pidf); o << getpid() << "\n"; }
        printf("fdf-attention: build %s; notice from %s at %s, held by this process (pid %d): %s\n", FDF_BUILD_TAG, name.c_str(), where.c_str(), int(getpid()), argv[1]);
        fflush(stdout);
        for (int64_t at = 0;;) for (auto& c : m.read("pond", "attention", ns, at, 600)) if (int64_t(c.id) > at) at = int64_t(c.id);   // held: the session is the notice
    } catch (const std::exception& x) { fprintf(stderr, "fdf-attention: %s: %s\n", where.c_str(), x.what()); return 1; }
}
