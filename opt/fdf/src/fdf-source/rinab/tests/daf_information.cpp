// daf_information.cpp -- [DAF_MODEL_V1] milestone 4 of the plan: sections E (the RIB Daemon: subscriptions, working
// sets, distribution, START) and F (name-space management). usage: daf_information HOST_A:PORT 1|2 TAG HOST_B:PORT
#include "daf_client.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <future>
#include <string>
#include <thread>
#include <vector>
using daf::Json; using daf::q;
static int bad = 0;
static void expect(const char* sec, const std::string& what, bool ok, const std::string& detail = "") {
    printf("%-3s %-74s %s%s%s\n", sec, what.c_str(), ok ? "ok" : "FAIL", detail.empty() ? "" : "  ", detail.c_str()); fflush(stdout);
    if (!ok) ++bad;
}
static Json enroll(daf::Client& c, const std::string& D, const std::string& apn, const std::string& role = "member") {
    Json r = c.call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":" + q(role));
    if (!r["ok"].b) { printf("enroll %s refused: %s %s\n", apn.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); exit(2); }
    return r;
}
static double us_since(std::chrono::steady_clock::time_point t) { return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t).count(); }
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: daf_information HOST_A:PORT 1|2 TAG HOST_B:PORT\n"); return 2; }
    const std::string A = argv[1], phase = argv[2], tag = argv[3];
    const std::string D = "hcm-" + tag + "#e1", N = "names-" + tag + "#n1", X = "other-" + tag + "#x1";
    if (phase == "2") {
        daf::Client a(A); enroll(a, N, "county", "coordinator");
        Json r = a.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\"");
        expect("F", "names, spaces and delegations survive a restart", r["ok"].b && r["object"]["beds"].n == 40, r["ok"].b ? "" : r["why"].s);
        printf("RESULT %s phase 2\n", bad ? "FAIL" : "PASS"); return bad ? 1 : 0;
    }
    daf::Client admin(A);
    admin.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"e1\",\"owner\":\"county\"");
    enroll(admin, D, "coordinator", "coordinator");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"rule\":\"all\"");
    daf::Client ha(A), hb(A); enroll(ha, D, "h-a"); enroll(hb, D, "h-b");
    ha.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":4}");
    hb.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":6}");

    // ---- E: a periodic subscription, 10 intervals
    { daf::Client sc(A); enroll(sc, D, "board-1");
      Json s = sc.call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"receiving\"],\"period_s\":0.2");
      uint64_t rev = uint64_t(s["rev"].n); std::vector<double> ts; std::vector<double> caps; int changed_at = -1;
      for (int i = 0; i < 10; ++i) {
          auto got = sc.memory().read("daf", "subd|" + D, s["sub"].s, int64_t(rev), 2.0);
          if (got.empty()) break; rev = got[0].id; ts.push_back(got[0].bag["t"].n);
          double cap = 0; for (auto& m : got[0].bag["data"]["receiving"].a) if (m["apn"].s == "h-a") cap = m["facts"]["capacity"].n; caps.push_back(cap);
          if (i == 4) { ha.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":9}"); changed_at = i; }
      }
      double mn = 1e9, mx = 0; for (size_t i = 1; i < ts.size(); ++i) { mn = std::min(mn, ts[i] - ts[i - 1]); mx = std::max(mx, ts[i] - ts[i - 1]); }
      const bool before = caps.size() == 10 && caps[0] == 4 && caps[4] == 4, after = caps.size() == 10 && caps[6] == 9 && caps[9] == 9;
      char d[160]; snprintf(d, sizeof d, "%zu deliveries, interval %.3f..%.3f s, h-a capacity %g then %g", ts.size(), mn, mx, caps.empty() ? -1 : caps[0], caps.empty() ? -1 : caps.back());
      expect("E", "a periodic subscription, 10 intervals: 10 deliveries, each the state at that time", ts.size() == 10 && mn > 0.15 && mx < 0.35 && before && after, d);
      (void)changed_at; sc.call("unsubscribe", "\"daf\":" + q(D) + ",\"sub\":" + q(s["sub"].s)); }
    ha.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":4}");

    // ---- E: 50 members subscribe to the same set: one computation per change
    { std::vector<std::unique_ptr<daf::Client>> boards; std::vector<std::string> subs; std::vector<uint64_t> revs; int shared = 0;
      for (int i = 0; i < 50; ++i) { boards.push_back(std::make_unique<daf::Client>(A)); enroll(*boards.back(), D, "board-" + std::to_string(100 + i));
          Json s = boards.back()->call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"receiving\"],\"filter\":[{\"f\":\"trauma\",\"v\":true}]");
          subs.push_back(s["sub"].s); revs.push_back(uint64_t(s["rev"].n)); if (s["shared"].b) ++shared; }
      const double runs0 = admin.call("stats")["subscription_runs"].n;
      std::atomic<int> woke{0}; std::vector<std::thread> ws;
      for (int i = 0; i < 50; ++i) ws.emplace_back([&, i] { daf::Client w(A); if (!w.memory().read("daf", "subd|" + D, subs[i], int64_t(revs[i]), 5.0).empty()) ++woke; });
      std::this_thread::sleep_for(std::chrono::milliseconds(300));
      for (int k = 0; k < 5; ++k) hb.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":" + std::to_string(10 + k) + "}");
      for (auto& t : ws) t.join();
      const double runs = admin.call("stats")["subscription_runs"].n - runs0;
      bool same = true; for (auto& x : subs) same = same && x == subs[0];
      expect("E", "50 members subscribe to the same set: the work is done once per change, not 50 times",
             same && shared == 49 && runs == 5 && woke == 50, "one subscription shared by " + std::to_string(shared + 1) + "; 5 changes -> " + std::to_string(int(runs)) + " computations; " + std::to_string(woke.load()) + " of 50 holders woke"); }

    // ---- E: a working set: local reads, kept current
    { daf::Client t(A); Json e = enroll(t, D, "task-1");
      daf::WorkingSet set(A, D, {"receiving"}, "task-1", e["token"].s);
      const auto s0 = t.stats(); std::vector<double> us;
      for (int i = 0; i < 1000; ++i) { auto st = std::chrono::steady_clock::now(); Json r = set.get("receiving"); us.push_back(us_since(st)); if (r["members"].a.size() != 2) break; }
      const auto s1 = t.stats(); std::sort(us.begin(), us.end());
      const auto up0 = set.updates(); ha.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":3}");
      auto st = std::chrono::steady_clock::now(); double cap = 0;
      while (us_since(st) < 2e6) { const Json g = set.get("receiving");     // a copy: the set may replace its own at any moment
          for (auto& m : g["members"].a) if (m["apn"].s == "h-a") cap = m["facts"]["capacity"].n;
          if (cap == 3) break; std::this_thread::sleep_for(std::chrono::microseconds(200)); }
      const double lag_ms = us_since(st) / 1000;
      char d[200]; snprintf(d, sizeof d, "1000 reads: p50 %.1f us, p99 %.1f us, requests sent %llu; a change reached the set in %.1f ms", us[500], us[990],
                            (unsigned long long)((s1.raw + s1.repeat + s1.same + s1.diff + s1.full) - (s0.raw + s0.repeat + s0.same + s0.diff + s0.full)), lag_ms);
      expect("E", "a task reads from its declared working set: no round trip; changes arrive", us.size() == 1000 && us[990] < 200 && cap == 3 && set.updates() > up0 &&
             (s1.raw + s1.repeat + s1.same + s1.diff + s1.full) == (s0.raw + s0.repeat + s0.same + s0.diff + s0.full), d); }

    // ---- E: distribute a value to every member of a name
    { Json r = admin.call("distribute", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"key\":\"alert-1\",\"value\":{\"text\":\"mass casualty incident, 40 patients\"}");
      auto a1 = ha.memory().read("daf", "in|" + D + "|h-a", "alert-1"), b1 = hb.memory().read("daf", "in|" + D + "|h-b", "alert-1");
      expect("E", "a value distributed to a name reaches every member's inbox", r["delivered"].n == 2 && a1.size() == 1 && b1.size() == 1 && a1[0].bag["v"]["text"].s.find("40") != std::string::npos,
             std::to_string(int(r["delivered"].n)) + " delivered"); }

    // ---- E: START
    { Json st = admin.call("start", "\"daf\":" + q(D) + ",\"script\":{\"from\":\"receiving\",\"filter\":[{\"f\":\"trauma\",\"v\":true}],\"fact\":\"capacity\",\"agg\":\"sum\",\"out\":\"trauma-capacity\",\"on_change\":true}");
      const double first = st["result"]["v"].n;
      hb.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":20}");
      Json o; for (auto& c : admin.memory().read("daf", "out|" + D, "trauma-capacity")) o = c.bag;
      expect("E", "a script started at the RAM manager runs beside the memory; its result is written there", st["ok"].b && first == 3 + 14 && o["v"].n == 3 + 20,
             "sum of trauma capacity: " + std::to_string(int(first)) + ", after a change " + std::to_string(int(o["v"].n)));
      admin.call("set_policy", "\"daf\":" + q(D) + ",\"key\":\"op:start\",\"mode\":\"roles\",\"roles\":[\"coordinator\"]");
      Json no = ha.call("start", "\"daf\":" + q(D) + ",\"script\":{\"from\":\"receiving\",\"agg\":\"count\",\"out\":\"x\"}");
      Json yes = admin.call("start", "\"daf\":" + q(D) + ",\"script\":{\"from\":\"receiving\",\"agg\":\"count\",\"out\":\"count\"}");
      expect("E", "a role without permission starts a script: refused (with enforcement on)", !no["ok"].b && no["error"].s == "EPERM" && yes["ok"].b && yes["result"]["v"].n == 2, no["why"].s);
      admin.call("stop", "\"daf\":" + q(D) + ",\"id\":" + q(st["id"].s)); }

    // ---- F: name spaces
    { daf::Client county(A), hsys(A), other(A), outsider(A);
      county.call("create_daf", "\"dan\":" + q("names-" + tag) + ",\"daii\":\"n1\",\"owner\":\"county\"");
      enroll(county, N, "county", "coordinator"); enroll(hsys, N, "harborview-system"); enroll(other, N, "stmary-system");
      Json c1 = county.call("ns_create", "\"daf\":" + q(N) + ",\"space\":\"facilities\"");
      county.call("ns_create", "\"daf\":" + q(N) + ",\"space\":\"roles\"");
      Json dl = county.call("ns_delegate", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"prefix\":\"county/harborview\",\"to\":\"harborview-system\"");
      Json in1 = hsys.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\",\"target\":{\"object\":{\"beds\":40,\"ref\":\"h-a\"}}");
      Json out1 = hsys.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/stmary/er\",\"target\":{\"object\":{\"beds\":1}}");
      Json out2 = other.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\",\"target\":{\"object\":{\"beds\":0}}");
      expect("F", "an authority delegates a subtree: the delegate assigns inside it, is refused outside it", c1["ok"].b && dl["ok"].b && in1["ok"].b && !out1["ok"].b && !out2["ok"].b,
             "inside " + std::string(in1["ok"].b ? "ok" : in1["why"].s) + "; outside: " + out1["why"].s + "; someone else: " + out2["why"].s);
      county.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"roles\",\"name\":\"trauma-center\",\"target\":{\"alias\":{\"space\":\"facilities\",\"name\":\"county/harborview/er\"}}");
      Json r1 = other.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"roles\",\"name\":\"trauma-center\"");
      expect("F", "a name resolves to another name in a second name space, followed to the object", r1["ok"].b && r1["object"]["beds"].n == 40 && r1["path"].a.size() == 2,
             r1["ok"].b ? std::to_string(r1["path"].a.size()) + " steps" : r1["why"].s);
      county.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"roles\",\"name\":\"loop-a\",\"target\":{\"alias\":{\"name\":\"loop-b\"}}");
      county.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"roles\",\"name\":\"loop-b\",\"target\":{\"alias\":{\"name\":\"loop-a\"}}");
      auto t0 = std::chrono::steady_clock::now(); Json lp = other.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"roles\",\"name\":\"loop-a\""); const double lp_us = us_since(t0);
      expect("F", "a chain of names loops: resolution stops at the limit with an error, not a hang", !lp["ok"].b && lp["error"].s == "ELOOP" && lp_us < 1e6, lp["why"].s);
      county.call("set_policy", "\"daf\":" + q(N) + ",\"key\":\"name:facilities:county/harborview/er\",\"mode\":\"roles\",\"roles\":[\"coordinator\"]");
      Json denied = other.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\"");
      Json missing = other.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/nowhere\"");
      Json allowed = county.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\"");
      expect("F", "a requester without access gets exactly the answer a missing name gets", !denied["ok"].b && denied["error"].s == missing["error"].s && denied["why"].s == missing["why"].s && allowed["ok"].b,
             "denied: " + denied["error"].s + " " + denied["why"].s + " | missing: " + missing["error"].s + " " + missing["why"].s);
      county.call("set_policy", "\"daf\":" + q(N) + ",\"key\":\"name:facilities:county/harborview/er\",\"mode\":\"open\",\"clear\":true");
      // an internal space: resolvable only within its own DAF
      county.call("ns_create", "\"daf\":" + q(N) + ",\"space\":\"internal-catalog\",\"internal\":true");
      county.call("ns_assign", "\"daf\":" + q(N) + ",\"space\":\"internal-catalog\",\"name\":\"bed-17\",\"target\":{\"object\":{\"unit\":\"ICU\"}}");
      outsider.call("create_daf", "\"dan\":" + q("other-" + tag) + ",\"daii\":\"x1\",\"owner\":\"red-cross\"");
      enroll(outsider, X, "shelter-1", "coordinator");
      outsider.call("ns_create", "\"daf\":" + q(X) + ",\"space\":\"links\"");
      outsider.call("ns_assign", "\"daf\":" + q(X) + ",\"space\":\"links\",\"name\":\"icu-bed\",\"target\":{\"alias\":{\"daf\":" + q(N) + ",\"space\":\"internal-catalog\",\"name\":\"bed-17\"}}");
      outsider.call("ns_assign", "\"daf\":" + q(X) + ",\"space\":\"links\",\"name\":\"er\",\"target\":{\"alias\":{\"daf\":" + q(N) + ",\"space\":\"facilities\",\"name\":\"county/harborview/er\"}}");
      Json ext = outsider.call("ns_resolve", "\"daf\":" + q(X) + ",\"space\":\"links\",\"name\":\"icu-bed\"");
      Json pub = outsider.call("ns_resolve", "\"daf\":" + q(X) + ",\"space\":\"links\",\"name\":\"er\"");
      Json own = county.call("ns_resolve", "\"daf\":" + q(N) + ",\"space\":\"internal-catalog\",\"name\":\"bed-17\"");
      expect("F", "a DAF-internal name resolved from outside the DAF: not found; inside: found", !ext["ok"].b && ext["error"].s == "ENOENT" && pub["ok"].b && own["ok"].b,
             "from outside: " + ext["error"].s + "; a public name from outside: " + (pub["ok"].b ? "found" : pub["why"].s) + "; inside: " + (own["ok"].b ? "found" : own["why"].s)); }
    printf("RESULT %s phase %s\n", bad ? "FAIL" : "PASS", phase.c_str());
    return bad ? 1 : 0;
}
