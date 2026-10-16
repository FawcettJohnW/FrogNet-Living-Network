// daf_membership.cpp -- [DAF_MODEL_V1] milestone 3 of the plan: sections C (enrollment, partitions) and D
// (Application-Entities, connections, flows), and B's flow to a whatevercast name. Run by tools/daf-check.sh, which
// gives it two RAM hosts (two islands). usage: daf_membership HOST_A:PORT 1|2 TAG HOST_B:PORT
#include "daf_client.hpp"
#include <openssl/hmac.h>
#include <openssl/evp.h>
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
static std::string apns(const Json& r) { std::string s; for (auto& m : r["members"].a) s += (s.empty() ? "" : ",") + m["apn"].s; return s; }
static Json enroll(daf::Client& c, const std::string& D, const std::string& apn, const std::string& role = "member") {
    Json r = c.call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":" + q(role));
    if (!r["ok"].b && role != "intruder") { printf("enroll %s refused: %s %s\n", apn.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); exit(2); }
    return r;
}
static std::string hmac_hex(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; } return a;
}
static std::string dj(const Json& j) {
    switch (j.type) { case Json::Null: return "null"; case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { char x[40]; snprintf(x, sizeof x, "%.17g", j.n); return x; } case Json::Str: return q(j.s);
        case Json::Arr: { std::string o = "["; for (size_t k = 0; k < j.a.size(); ++k) o += (k ? "," : "") + dj(j.a[k]); return o + "]"; }
        default: { std::string o = "{"; for (size_t k = 0; k < j.o.size(); ++k) o += (k ? "," : "") + q(j.o[k].first) + ":" + dj(j.o[k].second); return o + "}"; } }
}
static std::string cells_of(const Json& ex) {
    std::string s = "["; bool f = true;
    for (auto& c : ex["cells"].a) { s += (f ? "" : ",") + std::string("{\"v\":") + q(c["v"].s) + ",\"i\":" + q(c["i"].s) + ",\"bag\":" + dj(c["bag"]) + "}"; f = false; }
    return s + "]";
}
static double ms_since(std::chrono::steady_clock::time_point t) { return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t).count(); }
static double pct(std::vector<double> v, double p) { std::sort(v.begin(), v.end()); return v.empty() ? 0 : v[std::min(v.size() - 1, size_t(p * double(v.size())))]; }
static double nowd() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: daf_membership HOST_A:PORT 1|2 TAG HOST_B:PORT\n"); return 2; }
    const std::string A = argv[1], phase = argv[2], tag = argv[3], B = argv[4];
    const std::string D = "hcm-" + tag + "#m1", P = "hcm-" + tag + "#part";
    if (phase == "2") { printf("RESULT PASS phase 2 (nothing to do after the restart)\n"); return 0; }
    daf::Client admin(A);
    { Json r = admin.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"m1\",\"owner\":\"county\",\"synsets\":[\"catalog\"],\"syn_lifetime_s\":2");
      if (!r["ok"].b) { printf("create_daf: %s\n", r["why"].s.c_str()); return 2; } }
    enroll(admin, D, "coordinator", "coordinator");
    // reference data, 64 facilities x ~1 KB: the static and near-static information a new member receives
    for (int i = 0; i < 64; ++i) admin.call("set_ref", "\"daf\":" + q(D) + ",\"key\":\"facility-" + std::to_string(i) + "\",\"v\":{\"beds\":" + std::to_string(100 + i) + ",\"notes\":" + q(std::string(1000, 'x')) + "}");

    // ---- C: enrollment
    std::string syn1; double full_ms = 0, short_ms = 0; size_t full_ref = 0, short_ref = 0;
    { daf::Client x(A); auto t = std::chrono::steady_clock::now(); Json r = enroll(x, D, "harborview"); full_ms = ms_since(t);
      syn1 = r["syn"]["addr"].s; full_ref = r["ref"].a.size();
      // then live updates: a held read from where enrollment left the member sees the next change
      const uint64_t from = uint64_t(r["from"].n);
      auto w = std::async(std::launch::async, [&] { daf::Client wc(A); return wc.memory().read("daf", "nv|" + D, "members", int64_t(from), 5.0).size(); });
      std::this_thread::sleep_for(std::chrono::milliseconds(200)); daf::Client y(A); enroll(y, D, "stmary");
      const size_t woke = w.get();
      expect("C", "a new member: synonyms, reference data and state in one answer, then live updates",
             r["state"].s == "new" && !syn1.empty() && full_ref == 64 && r["static"].s == "sent" && !r["names"].a.empty() && woke > 0,
             "state=" + r["state"].s + " syn=" + syn1 + " ref=" + std::to_string(full_ref) + " names=" + std::to_string(r["names"].a.size()) + " live=" + std::to_string(woke)); }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    { daf::Client x(A); auto t = std::chrono::steady_clock::now(); Json r = enroll(x, D, "harborview"); short_ms = ms_since(t); short_ref = r["ref"].a.size();
      expect("C", "a member returns within its synonym lifetime: same synonyms, static data not resent",
             r["state"].s == "short" && r["syn"]["addr"].s == syn1 && r["static"].s == "kept" && short_ref == 0,
             "state=" + r["state"].s + " syn " + syn1 + "->" + r["syn"]["addr"].s + "  full " + std::to_string(full_ref) + " ref rows " + std::to_string(full_ms).substr(0, 5) +
             " ms, short " + std::to_string(short_ref) + " rows " + std::to_string(short_ms).substr(0, 5) + " ms"); }
    std::this_thread::sleep_for(std::chrono::milliseconds(2500));           // past the 2 s lifetime
    { daf::Client z(A); Json rz = enroll(z, D, "overlake");                 // takes the free synonym
      daf::Client x(A); Json r = enroll(x, D, "harborview");
      expect("C", "a member returns after the lifetime: full enrollment, its old synonym reassigned",
             r["state"].s == "returning" && r["static"].s == "sent" && rz["syn"]["addr"].s == syn1 && r["syn"]["addr"].s != syn1,
             "overlake got " + rz["syn"]["addr"].s + ", harborview now " + r["syn"]["addr"].s + " (was " + syn1 + ")"); }
    { admin.call("set_policy", "\"daf\":" + q(D) + ",\"key\":\"role:intruder\",\"mode\":\"refuse\"");
      daf::Client u(A); Json r = enroll(u, D, "mallory", "intruder");
      Json m = u.call("members", "\"daf\":" + q(D)); Json n = u.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"members\"");
      expect("C", "a role the policy refuses: enrollment refused with the reason; nothing visible",
             !r["ok"].b && r["error"].s == "EPERM" && !r["why"].s.empty() && !n["ok"].b && n["error"].s == "ENOENT" && n["why"].s == "not found",
             r["why"].s + " | resolve: " + n["error"].s); (void)m; }

    // ---- C: two islands of one DAF rejoin
    { daf::Client a(A), b(B);
      a.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"part\",\"owner\":\"county\"");
      enroll(a, P, "coord-a", "coordinator");
      a.call("define_name", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"rule\":\"all\",\"lifetime\":\"persistent\"");
      for (auto h : {"x", "y"}) a.call("join", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":" + q(h) + ",\"facts\":{}");
      Json ex = a.call("export", "\"daf\":" + q(P));
      Json mb = b.call("merge", "\"daf\":" + q(P) + ",\"since\":0,\"daf_info\":" + dj(ex["daf_info"]) + ",\"cells\":" + cells_of(ex));
      enroll(b, P, "coord-b", "coordinator");
      Json both = b.call("resolve", "\"daf\":" + q(P) + ",\"name\":\"facilities\"");
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      const double since = nowd();                              // the network splits here
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      a.call("join", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":\"z\",\"facts\":{}");
      a.call("unjoin", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":\"y\"");
      b.call("unjoin", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":\"x\"");
      b.call("join", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":\"w\",\"facts\":{}");
      b.call("join", "\"daf\":" + q(P) + ",\"name\":\"facilities\",\"apn\":\"y\",\"facts\":{\"beds\":12}");
      daf::Client live_b(B); enroll(live_b, P, "only-on-b");               // session state on island B
      // the islands rejoin: B's persistent state merges into A
      Json eb = b.call("export", "\"daf\":" + q(P));
      const std::string cells = cells_of(eb);
      char sb[40]; snprintf(sb, sizeof sb, "%.6f", since);
      Json mg = a.call("merge", "\"daf\":" + q(P) + ",\"since\":" + sb + ",\"cells\":" + cells);
      Json after = a.call("resolve", "\"daf\":" + q(P) + ",\"name\":\"facilities\"");
      Json cf = a.call("conflicts", "\"daf\":" + q(P)); std::string cfs; for (auto& c : cf["conflicts"].a) cfs += c["apn"].s;
      Json mem = a.call("resolve", "\"daf\":" + q(P) + ",\"name\":\"members\"");
      expect("C", "two islands rejoin: persistent sets merge as joins minus removals",
             mb["ok"].b && apns(both) == "x,y" && apns(after) == "w,z",
             "before split " + apns(both) + "; after merge " + apns(after) + " (" + std::to_string((int)mg["added"].n) + " added, " + std::to_string((int)mg["removed"].n) + " removed)");
      expect("C", "an add on one side and a removal on the other is flagged, not settled", cfs == "y" && mg["conflicts"].n == 1, "conflicts: " + cfs);
      expect("C", "session state is not merged: it is whoever is connected here", apns(mem).find("only-on-b") == std::string::npos && apns(mem).find("coord-a") != std::string::npos, apns(mem)); }

    // ---- D: Application-Entities and connections
    admin.call("set_objects", "\"daf\":" + q(D) + ",\"role\":\"ambulance\",\"objects\":[\"receiving\",\"transfer*\"]");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"rule\":\"one\"");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"command\",\"rule\":\"designated\"");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"transfer-requests\",\"rule\":\"all\"");
    daf::Client amb(A); const std::string amb_token = enroll(amb, D, "amb-7", "ambulance")["token"].s;
    Json g = amb.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"capacity\",\"objects\":[\"receiving\",\"command\",\"transfer-requests\"]");
    std::string gs; for (auto& o : g["granted"].a) gs += o.s + " ";
    expect("D", "a connection requests an object set; the grant is recorded, narrower here", g["ok"].b && gs == "receiving transfer-requests ", "granted: " + gs);
    Json soft = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"command\",\"ae\":\"capacity\"");
    admin.call("set_policy", "\"daf\":" + q(D) + ",\"key\":\"op:outside_object_set\",\"mode\":\"refuse\"");
    Json in1 = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"ae\":\"capacity\"");
    Json out1 = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"command\",\"ae\":\"capacity\"");
    expect("D", "enforcement off: outside the set is allowed and recorded; on: refused, inside succeeds",
           soft["ok"].b && in1["ok"].b && !out1["ok"].b && out1["error"].s == "EPERM", "off: " + std::string(soft["ok"].b ? "allowed" : "refused") + "; on: inside " + (in1["ok"].b ? "ok" : in1["why"].s) + ", outside " + out1["why"].s);
    Json g2 = amb.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"transfers\",\"objects\":[\"transfer-requests\"]");
    Json t_in = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"transfer-requests\",\"ae\":\"transfers\"");
    Json t_out = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"ae\":\"transfers\"");
    Json c_in = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"ae\":\"capacity\"");
    expect("D", "one member, two Application-Entities: each sees only its own object set", g2["ok"].b && t_in["ok"].b && !t_out["ok"].b && c_in["ok"].b,
           "transfers->transfer-requests " + std::string(t_in["ok"].b ? "ok" : "refused") + ", transfers->receiving " + (t_out["ok"].b ? "ok" : "refused") + ", capacity->receiving " + (c_in["ok"].b ? "ok" : "refused"));
    admin.call("set_policy", "\"daf\":" + q(D) + ",\"key\":\"op:outside_object_set\",\"mode\":\"open\",\"clear\":true");
    // authentication
    { admin.call("set_auth", "\"daf\":" + q(D) + ",\"module\":\"key\",\"key\":\"county-secret\"");
      Json bad1 = amb.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"secure\",\"objects\":[\"*\"],\"auth\":{\"nonce\":\"n-000001\",\"proof\":\"00\"}");
      Json use = amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"ae\":\"secure\"");
      const std::string proof = hmac_hex("county-secret", D + "|secure|amb-7|n-000002");
      Json good = amb.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"secure\",\"objects\":[\"*\"],\"auth\":{\"nonce\":\"n-000002\",\"proof\":" + q(proof) + "}");
      amb.call("disconnect", "\"daf\":" + q(D) + ",\"ae_name\":\"secure\"");
      // the same connection, the same nonce and proof again: a replay
      Json replay = amb.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"secure\",\"objects\":[\"*\"],\"auth\":{\"nonce\":\"n-000002\",\"proof\":" + q(proof) + "}");
      expect("D", "authentication fails: no connection, nothing visible on it; a right proof connects; a replay fails",
             !bad1["ok"].b && bad1["error"].s == "EACCES" && !use["ok"].b && use["error"].s == "ENOTCONN" && good["ok"].b && !replay["ok"].b && replay["why"].s.find("used before") != std::string::npos,
             "wrong: " + bad1["why"].s + " | use: " + use["error"].s + " | right: " + (good["ok"].b ? "ok" : good["why"].s) + " | replay: " + replay["why"].s);
      admin.call("set_auth", "\"daf\":" + q(D) + ",\"module\":\"null\""); }
    // ---- B: a flow to a whatevercast name; D: a bulk transfer beside small requests
    { daf::Client ha(A), hb(A); enroll(ha, D, "h-a"); enroll(hb, D, "h-b");
      ha.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":2}");
      hb.call("join", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":7}");
      Json fo = amb.call("flow_open", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"rule\":\"one\",\"filter\":[{\"f\":\"trauma\",\"v\":true}],\"rank\":{\"f\":\"capacity\"},\"spec\":{\"qos\":\"bulk\"}");
      auto box = hb.memory().read("daf", "box|" + D + "|h-b", fo["fid"].s);
      expect("B", "a flow allocated to a name with rule one reaches the member one selects", fo["ok"].b && fo["to"].s == "h-b" && box.size() == 1, "to=" + fo["to"].s);
      // small requests alone, then during a bulk transfer on the same session
      auto small = [&](int n) { std::vector<double> v; for (int i = 0; i < n; ++i) { auto t = std::chrono::steady_clock::now();
          amb.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\""); v.push_back(ms_since(t)); } return v; };
      std::vector<double> base = small(300);
      std::atomic<bool> done{false}; const std::string chunk = std::string(1 << 20, 'p'); double bulk_ms = 0;
      // the bulk transfer gets a connection of its own (Part 2-1 4.2: "separate connections for short responses and
      // longer responses"), on its own session: a session runs its requests in order, so one session would queue the
      // short requests behind the long ones
      daf::Client bulkc(A);
      Json bc = bulkc.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"bulk\",\"objects\":[\"flow:*\"],\"apn\":\"amb-7\",\"token\":" + q(amb_token));
      if (!bc["ok"].b) { printf("bulk connect refused: %s\n", bc["why"].s.c_str()); exit(2); }
      auto bulk = std::async(std::launch::async, [&] { auto t = std::chrono::steady_clock::now();
          for (int i = 0; i < 32; ++i) bulkc.call("flow_send", "\"daf\":" + q(D) + ",\"fid\":" + q(fo["fid"].s) + ",\"seq\":" + std::to_string(i) + ",\"data\":" + q(chunk));
          bulk_ms = ms_since(t); done = true; });
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
      std::vector<double> during; while (!done) { auto v = small(1); during.push_back(v[0]); }
      bulk.get();
      auto got = hb.memory().read("daf", "fd|" + D + "|" + fo["fid"].s + "|f", "", 0, 0);
      size_t bytes = 0; for (auto& c : got) bytes += c.bag["d"].s.size();
      char det[400]; snprintf(det, sizeof det, "small requests p50/p99: alone %.2f/%.2f ms (n=%zu), during 32 MB %.2f/%.2f ms (n=%zu); bulk %.0f ms; received %zu MB",
                              pct(base, .5), pct(base, .99), base.size(), pct(during, .5), pct(during, .99), during.size(), bulk_ms, bytes >> 20);
      expect("D", "a large transfer beside small requests: small requests keep moving", bytes == size_t(32) << 20 && during.size() >= 5 && pct(during, .99) < std::max(20.0, 5 * pct(base, .99)), det);
      // for comparison, not a pass/fail: the same transfer on the SAME session as the small requests
      { std::atomic<bool> d2{false};
        auto b2 = std::async(std::launch::async, [&] { for (int i = 0; i < 16; ++i) amb.call("flow_send", "\"daf\":" + q(D) + ",\"fid\":" + q(fo["fid"].s) + ",\"seq\":" + std::to_string(100 + i) + ",\"data\":" + q(chunk)); d2 = true; });
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        std::vector<double> same; while (!d2) { auto v = small(1); same.push_back(v[0]); } b2.get();
        printf("    (comparison) 16 MB on the same session as the small requests: small p50/p99 %.2f/%.2f ms (n=%zu)\n", pct(same, .5), pct(same, .99), same.size()); }
      amb.call("flow_close", "\"daf\":" + q(D) + ",\"fid\":" + q(fo["fid"].s)); }
    printf("RESULT %s phase %s\n", bad ? "FAIL" : "PASS", phase.c_str());
    return bad ? 1 : 0;
}
