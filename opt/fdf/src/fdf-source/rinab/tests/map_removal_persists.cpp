// map_removal_persists.cpp -- [MAP_REMOVAL_IS_PERMANENT_V1] a mapping that was removed stays removed across a RAM host
// restart. Run by tools/map-removal-check.sh, which starts the host, runs PHASE 1, restarts the host on the same region
// file, then runs PHASE 2.
//   phase 1: map_site; register A and B with own:true on one session; map_remove A; end the session (B goes with it);
//            register C without own (it must survive). Then every lookup is checked: A gone, B gone, C present.
//   phase hold: register D with own:true and keep the session open; the script kills the host with SIGKILL while it is
//            held, so the session never ends cleanly ([OWNED_MAPPING_IS_NOT_RESTORED_V1]).
//   phase 2: after the restart, the same three lookups must give the same answers, and D must be gone.
// usage: map_removal_persists HOST:PORT 1|hold|2 AREA KEY
#include "rina_region_client.hpp"
#include <cstdio>
#include <string>
#include <unistd.h>
static std::string auth_of(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; }
    return a;
}
static frogram::Json reg(rina_region::Client& c, const std::string& site, const std::string& key, const std::string& name, const std::string& rloc, bool own) {
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t); char ts[40]; snprintf(ts, sizeof ts, "%lld.%09ld", (long long)t.tv_sec, t.tv_nsec);
    const std::string ttl = "3600";
    return c.call("{\"op\":\"map_register\",\"name\":" + frogram::Json::quote(name) + ",\"rloc\":" + frogram::Json::quote(rloc) +
                  ",\"ttl\":\"" + ttl + "\",\"ts\":\"" + ts + "\",\"auth\":\"" + auth_of(key, name + "|" + rloc + "|" + ttl + "|" + ts) + "\"" +
                  ",\"site\":" + frogram::Json::quote(site) + (own ? ",\"own\":true" : "") + "}");
}
static bool present(const std::string& where, const std::string& name) {   // the mapping is asked on its own port, 4342
    try { rina_region::map_lookup(name, where.substr(0, where.rfind(':'))); return true; } catch (const std::exception&) { return false; }
}
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: map_removal_persists HOST:PORT 1|2 AREA KEY\n"); return 2; }
    const std::string where = argv[1], phase = argv[2], area = argv[3], key = argv[4];
    const std::string A = area + "-a", B = area + "-b", C = area + "-c", D = area + "-d";
    if (phase == "hold") {
        rina_region::Client h(where);
        frogram::Json x = reg(h, area, key, D, "192.0.2.4", true); if (!x["ok"].b) { fprintf(stderr, "register D refused\n"); return 2; }
        printf("held %s present=%s\n", D.c_str(), present(where, D) ? "yes" : "NO"); fflush(stdout);
        for (;;) pause();                                   // until the script kills the host (and then this)
    }
    if (phase == "1") {
        rina_region::Client s(where);
        frogram::Json r = s.call("{\"op\":\"map_site\",\"name\":" + frogram::Json::quote(area) + ",\"key\":" + frogram::Json::quote(key) + "}");
        if (!r["ok"].b) { fprintf(stderr, "map_site refused\n"); return 2; }
        { rina_region::Client c(where);
          for (auto& [n, ip] : {std::pair<std::string, std::string>{A, "192.0.2.1"}, {B, "192.0.2.2"}}) {
              frogram::Json x = reg(c, area, key, n, ip, true); if (!x["ok"].b) { fprintf(stderr, "register %s refused: %s %s\n", n.c_str(), x["error"].s.c_str(), x["why"].s.c_str()); return 2; } }
          frogram::Json x = c.call("{\"op\":\"map_remove\",\"name\":" + frogram::Json::quote(A) + "}");
          if (!x["ok"].b) { fprintf(stderr, "map_remove refused: %s\n", x["error"].s.c_str()); return 2; } }   // the session ends here: B goes
        frogram::Json y = reg(s, area, key, C, "192.0.2.3", false); if (!y["ok"].b) { fprintf(stderr, "register C refused\n"); return 2; }
    }
    const bool a = present(where, A), b = present(where, B), c = present(where, C), d = phase == "2" && present(where, D);
    printf("phase %s: %s removed=%s  %s session-ended=%s  %s kept=%s\n", phase.c_str(), A.c_str(), a ? "PRESENT" : "gone",
           B.c_str(), b ? "PRESENT" : "gone", C.c_str(), c ? "present" : "GONE");
    if (phase == "2") printf("phase 2: %s held at an unclean stop=%s\n", D.c_str(), d ? "PRESENT" : "gone");
    const bool ok = !a && !b && c && !d;
    printf("RESULT %s phase %s\n", ok ? "PASS" : "FAIL", phase.c_str());
    return ok ? 0 : 1;
}
