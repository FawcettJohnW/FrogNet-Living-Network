// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// fdf_ram.cpp -- [FDF_RAM_ON_PLATFORM_V1] FD&F's RAM manager on the Ribbit platform (ribbit::RamHost), replacing
// rina-host (John, 2026-11-02: "We should not be running rina_host." "Move fully to the RAM manager model.").
// RAM-MANAGER-ON-PLATFORM.md says what rina-host's server gave the region and what the platform gives instead.
//
// The platform's host, with FD&F's endpoint (/FawcettInnovations.RINA.ram_interface.php), FD&F's write contract
// ([OWN_FLAG_DECIDES_V1]: only an "own":true write goes with its session; "permanent":true is filed in the region's
// file), and the region -- fdf_region.cpp, daf_region.cpp, ctl_host.cpp, unchanged -- as its operations: op=rina and
// op=daf (inline, never wait), op=rina_block (answered now or later, through a continuation). Every RAM FD&F runs is
// this: a region's RAM manager, an app's RAM, the kart's; one that holds no region simply never gets a region call.
// Settings as rina-host's: RINA_REGION_FILE (required), RINA_NET_CONFIG, RINA_MAP_AREA, RINA_HOST_ROLE, RIBBIT_TEMPLATES.
//   fdf-ram [--listen HOST:PORT] [--api /FawcettInnovations.RINA.ram_interface.php] [--announce-fd N] [--version]
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include "frogram.hpp"
#include "fdf_region.h"
#include "fdf_version.hpp"
#include <chrono>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

using frogram::Json;
static const std::string API = "/FawcettInnovations.RINA.ram_interface.php";
static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static ramm::Memory& MEM() { return ribbit::RamHost::memory(); }

// ---------------------------------------------------------------- what the region calls (fdf_region.h), on the platform
uint64_t rina_session_id() { return ribbit::current_session(); }                       // [SESSION_IS_LIVENESS_V1]
void* rina_defer() { return ribbit::defer_answer(); }                                   // [DEFERRED_ANSWER_V1]
void rina_answer(void* h, const std::string& body) { ribbit::answer_later(h, body); }

// [RINA_RESIDENT_V1] the memory itself, in this process
namespace {
class Resident : public frogram::MemoryApi {
public:
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { return MEM().write(s, v, i, bag, now_s()); }
    void write_nowait(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { write(s, v, i, bag); }
    std::vector<frogram::Cell> read(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s, int fresh_s) override {
        if (wait_s > 0) return rina_resident_wait(s, v, i, after, wait_s);
        std::vector<frogram::Cell> out;
        for (auto& c : MEM().match(s, v, i, after, fresh_s, now_s()))
            out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, c.gone ? Json() : Json::parse(c.text()), c.updated, c.gone});
        return out;
    }
    void remove(const std::string& s, const std::string& v, uint64_t id) override { MEM().remove(s, v, id, true); }
};
std::vector<ramm::Cell> held(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {
    auto eval = [&] { return MEM().match(s, v, i, after, 0, now_s()); };
    std::vector<ramm::Cell> rows = eval();
    if (rows.empty() && wait_s > 0) {
        auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s);
        rows = MEM().wait(s, v, i, deadline, eval, [](const std::vector<ramm::Cell>& r) { return !r.empty(); });
    }
    return rows;
}
}  // namespace
frogram::MemoryApi& rina_resident_memory() { static Resident m; return m; }
std::vector<frogram::Cell> rina_resident_wait(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {
    std::vector<frogram::Cell> out;
    for (auto& c : held(s, v, i, after, wait_s)) out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, Json::parse(c.text()), c.updated});
    return out;
}
std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {
    std::vector<std::pair<uint64_t, std::string>> out;                                 // [RAW_HELD_READ_V1]
    for (auto& c : held(s, v, i, after, wait_s)) out.emplace_back(c.id, c.text());
    return out;
}
bool rina_resident_get_raw_id(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id) {
    for (auto& c : MEM().match(s, v, i, -1, 0, now_s())) if (c.instance == i) { bag = c.text(); id = c.id; return true; }   // [SDU_BY_REFERENCE_V1]
    return false;
}
bool rina_resident_get_raw(const std::string& s, const std::string& v, const std::string& i, std::string& bag) { uint64_t id; return rina_resident_get_raw_id(s, v, i, bag, id); }   // [RAW_GET_V1]

// the region's own (fdf_region.cpp) entry points the host calls
void rina_host_listening(const std::string& listen_on);
void rina_session_peer(uint64_t sid, const std::string& peer);

// ---------------------------------------------------------------- the host
class FdfRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "fdf"; }
    std::string vendor_version() const override { return FDF_BUILD_TAG; }
    std::string api_path() const override { return API; }
    std::string default_listen() const override { return "0.0.0.0:8788"; }
    bool handles(const std::string& op) const override { return op == "rina" || op == "rina_block" || op == "daf"; }
    // never wait on a thread: rina and daf answer at once; rina_block answers at once or takes its request's continuation
    // (ribbit::defer_answer), which exists only on the request's own thread -- on the pool, after a non-inline first try,
    // there is none, and fdf-events' held wait failed "no continuation to park" (found 2026-11-03, test_fdf_app.py)
    bool inline_operation(const std::string& op) const override { return op == "rina" || op == "daf" || op == "rina_block"; }
    bool coalesces(const std::string& op) const override { return !handles(op); }   // [REGION_COALESCE_PER_SESSION_V1]
    bool own_flag_decides() const override { return true; }                          // [OWN_FLAG_DECIDES_V1]
    bool keeps_permanent() const override { return true; }                           // [PERMANENT_CELL_V1]
    void permanent(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { rina_permanent_cell(s, v, i, bag); }
    std::string refuse_write(const std::string& s, const std::string& v, const std::string& i) const override {
        (void)i; if (s == "pond" && v == "config") return "(pond, config) is the RAM manager's: it is written from its net.json, never over the network";   // [NET_CONFIG_IS_THE_RAM_MANAGERS_V1]
        return ""; }
    void listening(const std::string& bound) override { rina_host_listening(bound); }   // [HOST_ANNOUNCES_ITSELF_V1]
    void session_up(uint64_t id, const std::string& peer) override { rina_session_peer(id, peer); }   // [SESSION_PEER_V1]
    void session_down(uint64_t id) override { rina_session_down(id); }                 // [SESSION_IS_LIVENESS_V1]
    ribbit::Reply operation(const std::string& op, const std::string& body) override {
        try {
            if (op == "rina") return ribbit::Reply{200, fdf_region_call(body)};
            if (op == "daf") return ribbit::Reply{200, daf_region_call(body)};
            return ribbit::Reply{200, fdf_region_block(body)};   // "" when it took the continuation: answered later
        } catch (const std::exception& x) { return ribbit::error_reply(400, op + ": " + x.what()); }
    }
};

int main(int argc, char** argv) {
    // [FDF_RAM_ON_PLATFORM_V1] rina-host's command line, so every caller's stays as it was: --api names the endpoint,
    // which is FD&F's and only FD&F's
    std::vector<char*> a{argv[0]};
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--api") && i + 1 < argc) {
            if (API != argv[i + 1]) { std::cerr << "fdf-ram: --api " << argv[i + 1] << ": this RAM manager answers " << API << " only\n"; return 2; }
            ++i; continue; }
        a.push_back(argv[i]);
    }
    FdfRam host;
    return host.run(int(a.size()), a.data());
}
