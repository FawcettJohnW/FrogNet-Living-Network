// rlite_ctl_client.cpp -- rlite-ctl: ONE call per command (op=rina, ctl), run at the memory.
#include <cstdio>
#include <cstdlib>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#include "rina_region_client.hpp"
#include "machine_id.hpp"
static std::string netns() { return rina_id::machine_id(); }   // [MACHINE_IDENTITY_V1]
int main(int argc, char** argv) {
    std::vector<std::string> a(argv + 1, argv + argc);
    if (a.size() >= 4 && a[0] == "ipcp-config" && a[2] == "netdev") {          // the device is in this namespace
        struct stat st; if (stat(("/sys/class/net/" + a[3]).c_str(), &st) != 0) { fprintf(stderr, "No such network device\n"); return 1; }
    }
    std::string args = "["; for (size_t i = 0; i < a.size(); i++) { args += (i ? "," : "") + frogram::Json::quote(a[i]); } args += "]";
    frogram::Json r;
    try {
        rina_region::Client ram;
        r = ram.call("{\"op\":\"ctl\",\"ns\":" + frogram::Json::quote(netns()) + ",\"args\":" + args + "}");
    } catch (const std::exception& e) {                                   // say so and fail -- never a core dump
        const char* where = getenv("RINA_RAM");
        fprintf(stderr, "rlite-ctl: cannot reach the RAM host at %s: %s\n", where && *where ? where : "(RINA_AREA / RINA_RAM not set)", e.what());
        return 1;
    }
    fputs(r["out"].s.c_str(), stdout); fputs(r["err"].s.c_str(), stderr);
    return int(r["rc"].n);
}
