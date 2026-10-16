// daf_names.cpp -- [DAF_MODEL_V1] milestone 2 of the plan: sections A (application naming), B (whatevercast names) and
// H (the check point). Run by tools/daf-check.sh: phase 1, then the RAM host is killed and restarted on the same files,
// then phase 2. usage: daf_names HOST:PORT 1|2 TAG
#include "daf_client.hpp"
#include <chrono>
#include <cstdio>
#include <future>
#include <string>
#include <thread>
using daf::Json; using daf::q;
static int bad = 0;
static void expect(const char* sec, const std::string& what, bool ok, const std::string& detail = "") {
    printf("%-3s %-74s %s%s%s\n", sec, what.c_str(), ok ? "ok" : "FAIL", detail.empty() ? "" : "  ", detail.c_str()); fflush(stdout);
    if (!ok) ++bad;
}
static std::string apns(const Json& r) { std::string s; for (auto& m : r["members"].a) s += (s.empty() ? "" : ",") + m["apn"].s; return s; }
static std::string enroll(daf::Client& c, const std::string& D, const std::string& apn, const std::string& role = "member", const std::string& facts = "{}") {
    Json r = c.call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":" + q(role) + ",\"facts\":" + facts);
    if (!r["ok"].b) { printf("enroll %s in %s refused: %s %s\n", apn.c_str(), D.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); exit(2); }
    return r["syn"]["addr"].s + "|" + std::to_string((long long)r["inst"].n) + "|" + r["syn"]["catalog"].s;
}
static Json join(daf::Client& c, const std::string& D, const std::string& name, const std::string& facts, const std::string& apn = "") {
    return c.call("join", "\"daf\":" + q(D) + ",\"name\":" + q(name) + ",\"facts\":" + facts + ",\"ref\":\"ref-" + name + "\"" + (apn.empty() ? "" : ",\"apn\":" + q(apn)));
}
static Json one(daf::Client& c, const std::string& D, const std::string& name) {
    return c.call("resolve", "\"daf\":" + q(D) + ",\"name\":" + q(name) + ",\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"op\":\"=\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}");
}
static Json all(daf::Client& c, const std::string& D, const std::string& name) { return c.call("resolve", "\"daf\":" + q(D) + ",\"name\":" + q(name) + ",\"rule\":\"all\""); }
int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "usage: daf_names HOST:PORT 1|2 TAG [HOST_B:PORT]\n"); return 2; }
    const std::string where = argv[1], phase = argv[2], tag = argv[3];
    const std::string I1 = "hcm-" + tag + "#inc1", I2 = "hcm-" + tag + "#inc2";
    daf::Client admin(where);
    if (phase == "1") {
        // ---- A: application naming
        for (auto d : {"inc1", "inc2"}) { Json r = admin.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":" + q(d) + ",\"owner\":\"county\",\"synsets\":[\"catalog\"],\"syn_lifetime_s\":30");
            if (!r["ok"].b) { printf("create_daf refused: %s %s\n", r["error"].s.c_str(), r["why"].s.c_str()); return 2; } }
        enroll(admin, I1, "coordinator", "coordinator"); enroll(admin, I2, "coordinator", "coordinator");
        std::string first, second;
        { daf::Client x(where); first = enroll(x, I1, "harborview"); }       // the session ends: the member leaves
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        { daf::Client x(where); second = enroll(x, I1, "harborview"); }
        const auto in1 = first.find('|'), in2 = second.find('|');
        expect("A", "a member restarts: same application-process name, new instance", first.substr(in1, first.rfind('|') - in1) != second.substr(in2, second.rfind('|') - in2), first + " then " + second);
        { daf::Client x(where); enroll(x, I1, "stmary");
          Json m2 = admin.call("members", "\"daf\":" + q(I2)); bool seen = false; for (auto& m : m2["members"].a) if (m["apn"].s == "stmary") seen = true;
          Json n1 = admin.call("resolve", "\"daf\":" + q(I2) + ",\"name\":\"members\"");
          expect("A", "two instances of one DAF: a member of one is invisible to the other", !seen && apns(n1).find("stmary") == std::string::npos, "inc2 members: " + apns(n1)); }
        { daf::Client x(where), y(where);
          const std::string s1 = enroll(x, I1, "valley"), s2 = enroll(y, I2, "valley");
          const std::string a1 = s1.substr(0, s1.find('|')), a2 = s2.substr(0, s2.find('|'));
          Json r1 = admin.call("synonym", "\"daf\":" + q(I1) + ",\"syn\":" + q(a1)), r2 = admin.call("synonym", "\"daf\":" + q(I2) + ",\"syn\":" + q(a2));
          daf::Client z(where); const std::string s3 = enroll(z, I2, "overlake"); const std::string a3 = s3.substr(0, s3.find('|'));
          Json r3 = admin.call("synonym", "\"daf\":" + q(I1) + ",\"syn\":" + q(a3));
          expect("A", "the same synonym in two DAFs resolves only within its own DAF",
                 r1["apn"].s == "valley" && r2["apn"].s == "valley" && (r3["apn"].s != "overlake"), "inc1 " + a1 + "->" + r1["apn"].s + ", inc2 " + a2 + "->" + r2["apn"].s + ", inc2's " + a3 + " in inc1 -> " + (r3["ok"].b ? r3["apn"].s : r3["error"].s));
          const std::string c1 = s1.substr(s1.rfind('|') + 1);
          Json rc = admin.call("synonym", "\"daf\":" + q(I1) + ",\"set\":\"catalog\",\"syn\":" + q(c1));
          expect("A", "a second synonym set: synonyms from both sets, each resolving on its own", !c1.empty() && rc["apn"].s == "valley" && c1 != a1, "addr " + a1 + ", catalog " + c1); }

        // ---- B: whatevercast names
        Json dn = admin.call("define_name", "\"daf\":" + q(I1) + ",\"name\":\"receiving\",\"rule\":\"one\",\"lifetime\":\"session\"");
        Json df = admin.call("define_name", "\"daf\":" + q(I1) + ",\"name\":\"facilities\",\"rule\":\"all\",\"lifetime\":\"persistent\"");
        expect("B", "names are defined with a rule and a lifetime", dn["ok"].b && df["ok"].b, dn["why"].s + df["why"].s);
        daf::Client ha(where), hb(where), hc(where);
        enroll(ha, I1, "h-a"); enroll(hb, I1, "h-b"); enroll(hc, I1, "h-c");
        join(ha, I1, "receiving", "{\"trauma\":true,\"capacity\":5}"); join(hb, I1, "receiving", "{\"trauma\":true,\"capacity\":5}"); join(hc, I1, "receiving", "{\"trauma\":true,\"capacity\":3}");
        Json ra = all(admin, I1, "receiving"), r1 = one(admin, I1, "receiving");
        expect("B", "three members under one name: all returns three; one picks; a tie goes to the lowest", apns(ra) == "h-a,h-b,h-c" && apns(r1) == "h-a", "all=" + apns(ra) + " one=" + apns(r1));
        // a member's facts change: one moves, and a held read on the name returns
        const uint64_t rev = uint64_t(r1["rev"].n);
        auto w = std::async(std::launch::async, [&] { return hc.watch(I1, "receiving", rev, 10); });   // a member watches (a non-member gets "not found")
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        join(ha, I1, "receiving", "{\"trauma\":true,\"capacity\":0}");
        const uint64_t woke = w.get(); Json r2 = one(admin, I1, "receiving");
        expect("B", "a member's facts change: one moves, and a held read on the name returns", apns(r2) == "h-b" && woke > rev, "one=" + apns(r2) + " watch " + std::to_string(rev) + "->" + std::to_string(woke));
        // the chosen member's session ends
        { daf::Client* gone = new daf::Client(where); enroll(*gone, I1, "h-d"); join(*gone, I1, "receiving", "{\"trauma\":true,\"capacity\":9}");
          Json r3 = one(admin, I1, "receiving"); delete gone; std::this_thread::sleep_for(std::chrono::milliseconds(300));
          Json r4 = one(admin, I1, "receiving"), r5 = all(admin, I1, "receiving");
          expect("B", "the chosen member's session ends: it leaves all, one moves to the next best", apns(r3) == "h-d" && apns(r4) == "h-b" && apns(r5).find("h-d") == std::string::npos, "before=" + apns(r3) + " after=" + apns(r4) + " all=" + apns(r5)); }
        // a member joins between a resolve and the held read after it
        { Json r = all(admin, I1, "receiving"); const uint64_t rv = uint64_t(r["rev"].n);
          daf::Client he(where); enroll(he, I1, "h-e"); join(he, I1, "receiving", "{\"trauma\":false,\"capacity\":4}");
          const uint64_t got = he.watch(I1, "receiving", rv, 5);
          expect("B", "a member joins between a resolve and the held read: the read reports it at once", got > rv, std::to_string(rv) + "->" + std::to_string(got)); }
        // a member that fails the filter does not change one
        { Json before = one(admin, I1, "receiving"); daf::Client hf(where); enroll(hf, I1, "h-f"); join(hf, I1, "receiving", "{\"trauma\":false,\"capacity\":99}");
          Json after = one(admin, I1, "receiving");
          expect("B", "a member that fails the filter joins: one does not change", apns(before) == apns(after), apns(before) + " / " + apns(after)); }
        // designated and nearest
        { Json d = admin.call("define_name", "\"daf\":" + q(I1) + ",\"name\":\"command\",\"rule\":\"designated\"");
          join(admin, I1, "command", "{}"); admin.call("designate", "\"daf\":" + q(I1) + ",\"name\":\"command\",\"apn\":\"coordinator\"");
          Json r = admin.call("resolve", "\"daf\":" + q(I1) + ",\"name\":\"command\"");
          admin.call("define_name", "\"daf\":" + q(I1) + ",\"name\":\"ambulances\",\"rule\":\"nearest\"");
          daf::Client a1(where), a2(where); enroll(a1, I1, "amb-1"); enroll(a2, I1, "amb-2");
          join(a1, I1, "ambulances", "{\"x\":10,\"y\":0}"); join(a2, I1, "ambulances", "{\"x\":2,\"y\":1}");
          Json n = admin.call("resolve", "\"daf\":" + q(I1) + ",\"name\":\"ambulances\",\"near\":{\"x\":0,\"y\":0}");
          expect("B", "designated returns the designated member; nearest returns the nearest", apns(r) == "coordinator" && apns(n) == "amb-2", "command=" + apns(r) + " nearest=" + apns(n)); }
        // persistent names: a member admitted, then its session ends; another admitted, then removed
        { daf::Client p(where); enroll(p, I1, "harborview");
          Json j1 = join(p, I1, "facilities", "{\"beds\":400}");
          Json j2 = join(admin, I1, "facilities", "{\"beds\":120}", "stmary");
          Json j3 = join(admin, I1, "facilities", "{\"beds\":80}", "closed-clinic");
          Json rm = admin.call("unjoin", "\"daf\":" + q(I1) + ",\"name\":\"facilities\",\"apn\":\"closed-clinic\"");
          expect("B", "persistent name: members join and are admitted; one is removed", j1["ok"].b && j2["ok"].b && j3["ok"].b && rm["ok"].b, j1["why"].s + j2["why"].s + j3["why"].s + rm["why"].s); }
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        Json pf = all(admin, I1, "facilities");
        expect("B", "persistent name: after the member's session ended it is still in all", apns(pf) == "harborview,stmary", apns(pf));

        // ---- H: the check point
        const std::string pol = "\"daf\":" + q(I2);
        admin.call("define_name", pol + ",\"name\":\"locked\",\"rule\":\"all\""); admin.call("define_name", pol + ",\"name\":\"open\",\"rule\":\"all\"");
        admin.call("set_policy", pol + ",\"key\":\"name:locked\",\"mode\":\"refuse\"");
        admin.call("set_policy", pol + ",\"key\":\"role:intruder\",\"mode\":\"refuse\"");
        admin.call("set_policy", pol + ",\"key\":\"op:define_name\",\"mode\":\"refuse\"");
        daf::Client t(where); enroll(t, I2, "tester");
        Json jl = join(t, I2, "locked", "{}"), jo = join(t, I2, "open", "{}");
        Json rl = all(t, I2, "locked"), ro = all(t, I2, "open");
        Json dl = t.call("define_name", pol + ",\"name\":\"another\"");
        daf::Client u(where); Json eu = u.call("enroll", pol + ",\"apn\":\"mallory\",\"role\":\"intruder\"");
        Json desl = t.call("designate", pol + ",\"name\":\"locked\",\"apn\":\"tester\"");
        expect("H", "the refusing policy refuses join, resolve, designate, define_name and enroll with a reason",
               !jl["ok"].b && !rl["ok"].b && !desl["ok"].b && !dl["ok"].b && !eu["ok"].b && jl["error"].s == "EPERM" && !jl["why"].s.empty(),
               "join " + jl["error"].s + " " + jl["why"].s + " | resolve " + rl["error"].s + " " + rl["why"].s + " | designate " + desl["error"].s +
               " | define " + dl["error"].s + " " + dl["why"].s + " | enroll " + eu["error"].s + " " + eu["why"].s);
        expect("H", "an open name beside it is unaffected", jo["ok"].b && ro["ok"].b && apns(ro) == "tester", apns(ro));
        Json au = admin.call("audit", pol); bool found = false;
        for (auto& r : au["records"].a) { const Json& x = r["r"]; if (x["apn"].s == "tester" && x["op"].s == "join" && x["object"].s == "locked" && x["decision"].s == "refuse" && x["role"].s == "member") found = true; }
        expect("H", "an audit record names who, role, operation, object and decision", found, std::to_string(au["records"].a.size()) + " records");
        admin.call("set_policy", pol + ",\"key\":\"name:locked\",\"mode\":\"open\"");
        Json jl2 = join(t, I2, "locked", "{}");
        expect("H", "a policy changed while members are connected decides the next request", jl2["ok"].b, jl2["why"].s);
    } else {
        // ---- after the RAM host was killed and restarted on the same files
        enroll(admin, I1, "coordinator", "coordinator"); enroll(admin, I2, "coordinator", "coordinator");   // re-asserts, as every member does
        Json pf = all(admin, I1, "facilities");
        expect("B", "persistent name after a restart: members kept, the removed one stays removed", apns(pf) == "harborview,stmary", apns(pf));
        Json rr = all(admin, I1, "receiving");
        expect("B", "session name after a restart: empty until members re-assert", rr["ok"].b && rr["members"].a.empty(), apns(rr));
        daf::Client hb(where); enroll(hb, I1, "h-b"); join(hb, I1, "receiving", "{\"trauma\":true,\"capacity\":5}");
        Json r2 = all(admin, I1, "receiving");
        expect("B", "session name after a restart: a member that re-asserts is back", apns(r2) == "h-b", apns(r2));
        Json pl = admin.call("set_policy", "\"daf\":" + q(I2) + ",\"key\":\"name:locked\",\"mode\":\"refuse\""); daf::Client t(where); enroll(t, I2, "tester");
        Json jl = join(t, I2, "locked", "{}");
        expect("H", "policies survive a restart", pl["ok"].b && !jl["ok"].b, jl["why"].s);
        Json fac = admin.call("resolve", "\"daf\":" + q(I1) + ",\"name\":\"facilities\",\"live\":true");
        expect("B", "persistent group crossed with presence: only members connected now", fac["members"].a.empty(), apns(fac));
    }
    printf("RESULT %s phase %s\n", bad ? "FAIL" : "PASS", phase.c_str());
    return bad ? 1 : 0;
}
