// elect_test.cpp -- [MR_RANKED_BY_CAPABILITY_V1] the election's rule on synthetic records (the sandbox's namespaces all
// share one kernel, so they cannot differ in capability). Exit 0 only if every case gives the expected winner.
#include "rina_elect.hpp"
#include <cstdio>
int main() {
    int bad = 0;
    auto t = [&](const char* what, const std::map<std::string, std::string>& n, const std::string& want) {
        const std::string got = rina_elect::map_resolver(n);
        printf("%-58s -> %-10s %s\n", what, got.empty() ? "(none)" : got.c_str(), got == want ? "ok" : ("FAIL, wanted " + want).c_str()); bad += got != want; };
    t("no machine capable", {{"m:a", "eth0=1;"}, {"m:b", "eth0=1;"}}, "");
    t("one capable", {{"m:a", "eth0=1;"}, {"m:b", "cap.mr=10.0.0.2@4@8000;"}}, "10.0.0.2");
    t("more cores wins over a lower identity", {{"m:a", "cap.mr=10.0.0.1@2@64000;"}, {"m:b", "cap.mr=10.0.0.2@8@4000;"}}, "10.0.0.2");
    t("same cores: more memory wins", {{"m:a", "cap.mr=10.0.0.1@4@8000;"}, {"m:b", "cap.mr=10.0.0.2@4@16000;"}}, "10.0.0.2");
    t("equal capability: the lowest identity", {{"m:b", "cap.mr=10.0.0.2@4@8000;"}, {"m:a", "cap.mr=10.0.0.1@4@8000;"}}, "10.0.0.1");
    t("unity: a machine alone elects itself", {{"m:a", "x;cap.mr=10.0.0.1@1@512;y=1;"}}, "10.0.0.1");
    // [RTR_IS_A_CAPABILITY_V1] the same rule for the RTR role, and the winner's identity (the via for NATed machines)
    auto r = [&](const char* what, const std::map<std::string, std::string>& n, const std::string& want_rloc, const std::string& want_who) {
        const auto got = rina_elect::rtr(n);
        const bool ok = got.first == want_rloc && got.second == want_who;
        printf("%-58s -> %-10s %-4s %s\n", what, got.first.empty() ? "(none)" : got.first.c_str(), got.second.c_str(), ok ? "ok" : "FAIL"); bad += !ok; };
    r("rtr: a NATed machine states no cap.rtr; the reachable one wins", {{"m:a", "nat=10.30.0.1;"}, {"m:b", "cap.rtr=10.30.0.2@2@4000;"}}, "10.30.0.2", "m:b");
    r("rtr: the map-resolver capability does not count", {{"m:a", "cap.mr=10.0.0.1@8@8000;"}}, "", "");
    r("rtr: capability first, then identity", {{"m:a", "cap.rtr=10.0.0.1@2@8000;"}, {"m:b", "cap.rtr=10.0.0.2@4@1000;"}, {"m:c", "cap.rtr=10.0.0.3@4@1000;"}}, "10.0.0.2", "m:b");
    printf("RESULT %s\n", bad ? "FAIL" : "PASS"); return bad;
}
