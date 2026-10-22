// repro_stale_diff.cpp -- [DIAG-E1] the smallest sequence that gives a long-lived session a wrong resolve answer while a
// fresh session gets the right one. usage: repro_stale_diff HOST:PORT TAG VARIANT
//   VARIANT a: only "one" resolves on the observer          b: "all" and "one" alternate on the observer
//   VARIANT c: "one", then "designated", then "one"         d: like b, but one member's facts change shape order
#include "daf_client.hpp"
#include <cstdio>
#include <memory>
#include <string>
#include <vector>
using daf::Json; using daf::q;
static std::string D;
static std::string one_body() { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"live\":true,\"rank\":{\"f\":\"capacity\"}}"; }
static std::string all_body() { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"all\"}"; }
static std::string des_body() { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"designated\"}"; }
static std::string first_apn(const std::string& t) { Json j = Json::parse(t); return j["members"].a.empty() ? "-" : j["members"].a[0]["apn"].s; }
int main(int argc, char** argv) {
    const std::string where = argv[1], tag = argv[2], var = argv[3]; D = "rp-" + tag + "#1";
    daf::Client admin(where); admin.call("create_daf", "\"dan\":" + q("rp-" + tag) + ",\"daii\":\"1\",\"owner\":\"rp\"");
    admin.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"coordinator\"");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\"");
    daf::Client a(where), b(where), obs(where);
    a.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"ma\""); b.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"mb\""); obs.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"obs\"");
    a.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":16,\"x\":1}");
    b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":10,\"x\":2}");
    admin.call("designate", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"apn\":\"mb\"");
    int bad = 0;
    if (var == "e") {   // one request shape, two filters: Y selects mb; X selects nobody (an empty list goes RESP_RAW)
        auto Y = [&] { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"filter\":[{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}}"; };
        auto X = [&] { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"filter\":[{\"f\":\"capacity\",\"op\":\">\",\"v\":100}],\"rank\":{\"f\":\"capacity\"}}"; };
        b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":18,\"x\":2}");
        auto step = [&](const char* label, const std::string& body) { auto s0 = obs.stats(); const std::string got = obs.raw(body); auto s1 = obs.stats();
            daf::Client fresh(where); fresh.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"f" + std::string(label) + "\""); const std::string want = fresh.raw(body);
            const char* kind = s1.same > s0.same ? "SAME" : s1.rdiff > s0.rdiff ? "DIFF" : s1.rraw > s0.rraw ? "RAW" : "?";
            const bool ok = first_apn(got) == first_apn(want); if (!ok) ++bad;
            printf("  %-34s reply %-4s observer %-3s fresh %-3s %s\n", label, kind, first_apn(got).c_str(), first_apn(want).c_str(), ok ? "ok" : "WRONG"); };
        step("1 Y (selects mb)", Y()); step("2 X (selects nobody)", X()); step("3 X again, nothing changed", X());
        a.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":16,\"x\":9}");   // ma moves: rev changes, Y still mb
        step("4 Y after an unrelated change", Y());
        printf("variant e: %s\n", bad ? "REPRODUCED" : "not reproduced"); fflush(stdout); _exit(bad ? 1 : 0);
    }
    if (var == "f") {   // [SAME_KEEPS_LAST_ANSWER_V1] two requests at one location: A = one ranked on capacity, B = one ranked on x
        // A selects mb (capacity 18 > 16); B selects ma (x 9 > 2). Same shape, same location, different answers.
        a.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":16,\"x\":9}");
        b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":18,\"x\":2}");
        auto A = [&] { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"filter\":[{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}}"; };
        auto B = [&] { return "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"filter\":[{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"x\"}}"; };
        // the checkers enroll BEFORE the sequence: an enrollment moves the name's rev, and the sequence needs byte-identical answers
        std::vector<std::unique_ptr<daf::Client>> fr; for (int i = 0; i < 16; ++i) { fr.push_back(std::make_unique<daf::Client>(where));
            fr.back()->call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"chk" + std::to_string(i) + "\""); }
        int si = 0;
        auto step = [&](const std::string& label, const std::string& body) { auto s0 = obs.stats(); const std::string got = obs.raw(body); auto s1 = obs.stats();
            const std::string want = fr[size_t(si++)]->raw(body);
            const bool ok = first_apn(got) == first_apn(want); if (!ok) ++bad;
            printf("  %-28s req raw %llu repeat %llu diff %llu full %llu | reply same %llu rdiff %llu rraw %llu | observer %-3s fresh %-3s %s\n", label.c_str(),
                   (unsigned long long)(s1.raw - s0.raw), (unsigned long long)(s1.repeat - s0.repeat), (unsigned long long)(s1.diff - s0.diff), (unsigned long long)(s1.full - s0.full),
                   (unsigned long long)(s1.same - s0.same), (unsigned long long)(s1.rdiff - s0.rdiff), (unsigned long long)(s1.rraw - s0.rraw),
                   first_apn(got).c_str(), first_apn(want).c_str(), ok ? "ok" : "WRONG"); };
        for (int i = 1; i <= 4; ++i) { step("A#" + std::to_string(i) + " (mb)", A()); step("B#" + std::to_string(i) + " (ma)", B()); }
        b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":17,\"x\":2}");   // rev moves; A still mb
        step("A after a change (mb)", A()); step("B after a change (ma)", B());   // B's answer is now the last DIFF
        step("A, unchanged (mb)", A());       // request-hash SAME: the server's reference moves to A; last answer stays B
        step("B, unchanged (ma)", B());       // same-as-last SAME: the server's reference stays A; the client's moves to B
        b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":19,\"x\":2}");
        step("A after a second change (mb)", A());   // DIFF encoded against A, applied by the client to B
        printf("variant f: %s\n", bad ? "REPRODUCED" : "not reproduced"); fflush(stdout); _exit(bad ? 1 : 0);
    }
    for (int round = 0; round < 6; ++round) {
        const int cb = round % 2 ? 10 : 18;                         // mb alternates above and below ma
        if (var == "d") b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"x\":2,\"capacity\":" + std::to_string(cb) + "}");
        else b.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{\"capacity\":" + std::to_string(cb) + ",\"x\":2}");
        if (var == "b" || var == "d") obs.raw(all_body());
        if (var == "c") { obs.raw(one_body()); obs.raw(des_body()); }
        const std::string got = obs.raw(one_body());
        daf::Client fresh(where); fresh.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"fresh" + std::to_string(round) + "\"");
        const std::string want = fresh.raw(one_body());
        const bool ok = first_apn(got) == first_apn(want); if (!ok) ++bad;
        auto s = obs.stats();
        printf("variant %s round %d: mb capacity %d  observer %s  fresh %s  %s  (observer wire: same %llu diff %llu rdiff %llu raw %llu rraw %llu)\n", var.c_str(), round, cb,
               first_apn(got).c_str(), first_apn(want).c_str(), ok ? "ok" : "WRONG", (unsigned long long)s.same, (unsigned long long)s.diff, (unsigned long long)s.rdiff, (unsigned long long)s.raw, (unsigned long long)s.rraw);
        if (!ok) printf("  observer answer: %s\n  fresh answer:    %s\n", got.c_str(), want.c_str());
    }
    printf("variant %s: %s\n", var.c_str(), bad ? "REPRODUCED" : "not reproduced");
    fflush(stdout); _exit(bad ? 1 : 0);
}
