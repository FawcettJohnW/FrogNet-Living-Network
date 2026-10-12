// fdf-participants -- [FDF_DISCOVERY_V1] FD&F's getHosts: the participants, as the shared space holds them.
// Prints a JSON array, one object per announced machine: ip (its first address) and hostname -- getHosts.php's keys,
// so pages built on it keep working -- plus ips, alive (its session to the RAM manager is up) and cap (its capability,
// FrogNet's probe output). Reaches the RAM manager like every client (RINA_RAM or RINA_AREA, through the transport).
#include "fdf_transport.hpp"
#include "fdf_version.hpp"
#include <cstdio>
// --wait GEN: [FDF_DASHBOARD_V1] hold until the participant set changes after generation GEN (the transport's CHANGE),
// then print {"gen":N,"participants":[...]} -- a page's long-poll returns only on a real change, never on a timer.
int main(int argc, char** argv) {
    try {
        rina_region::Client c; unsigned long long gen = 0; const bool wait = argc == 3 && std::string(argv[1]) == "--wait";
        if (wait) { fdf::Transport t; fdf::Socket s(rina_region::Client::locate()); gen = t.change(s, std::stoull(argv[2])); }
        frogram::Json r = c.call("{\"op\":\"participants\"}");
        if (!r["ok"].b) { fprintf(stderr, "fdf-participants: refused: %s\n", r["error"].s.c_str()); return 1; }
        std::string out = "["; bool f = true;
        for (auto& p : r["participants"].a) {
            std::string ips = "["; bool g = true; for (auto& a : p["ips"].a) { ips += (g ? "" : ",") + frogram::Json::quote(a.s); g = false; } ips += "]";
            out += std::string(f ? "" : ",") + "\n {\"ip\":" + frogram::Json::quote(p["ips"].a.empty() ? "" : p["ips"].a[0].s) + ",\"hostname\":" + frogram::Json::quote(p["name"].s) +
                   ",\"ips\":" + ips + ",\"alive\":" + (p["alive"].b ? "true" : "false") + ",\"ns\":" + frogram::Json::quote(p["ns"].s) +
                   ",\"cores\":" + std::to_string((long)p["cap"]["cores"].n) + ",\"cpu_bench_total\":" + std::to_string(p["cap"]["cpu_bench_total"].n) +
                   ",\"mem_total_kb\":" + std::to_string((long long)p["cap"]["mem_total_kb"].n) + "}";
            f = false; }
        if (wait) printf("{\"gen\":%llu,\"participants\":%s\n]}\n", gen, out.c_str());
        else { printf("%s\n]\n", out.c_str()); fprintf(stderr, "fdf-participants: build %s; region is build %s\n", FDF_BUILD_TAG, r["server"].s.empty() ? "(older than fdf-2026-10-09-r2)" : r["server"].s.c_str()); }
        return 0;
    } catch (const std::exception& e) { fprintf(stderr, "fdf-participants: %s\n", e.what()); return 1; }
}
