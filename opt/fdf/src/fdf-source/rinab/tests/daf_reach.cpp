// daf_reach.cpp -- [DAF_MODEL_V1] milestone 5 of the plan: sections G (the DIF Allocator, across two RAM hosts) and I
// (requests without enrollment). usage: daf_reach HOST_A:PORT 1|2 TAG HOST_B:PORT
#include "daf_client.hpp"
#include <openssl/hmac.h>
#include <openssl/evp.h>
#include <atomic>
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
static Json enroll(daf::Client& c, const std::string& D, const std::string& apn, const std::string& role = "member") {
    Json r = c.call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":" + q(role));
    if (!r["ok"].b) { printf("enroll %s in %s refused: %s %s\n", apn.c_str(), D.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); exit(2); }
    return r;
}
static std::string apns(const Json& r) { std::string s; for (auto& m : r["members"].a) s += (s.empty() ? "" : ",") + m["apn"].s; return s; }
static std::string hmac_hex(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; } return a;
}
// a member that answers the requests sent to it, until told to stop; `leave_on` names a request body it leaves on
static void serve(daf::Client& c, const std::string& D, const std::string& apn, std::atomic<bool>& stop, const std::string& reply) {
    int64_t after = 0;
    while (!stop) {
        auto got = c.memory().read("daf", "req|" + D + "|" + apn, "", after, 0.5);
        for (auto& r : got) { after = std::max<int64_t>(after, int64_t(r.id)); if (r.bag["gone"].b) continue;
            c.call("answer", "\"daf\":" + q(D) + ",\"rid\":" + q(r.instance) + ",\"body\":{\"from\":" + q(apn) + ",\"text\":" + q(reply) + "}"); }
    }
}
static Json wait_answer(daf::Client& c, const std::string& D, const std::string& rid, double s) {
    auto got = c.memory().read("daf", "ans|" + D, rid, 0, s); return got.empty() ? Json{} : got[0].bag;
}
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: daf_reach HOST_A:PORT 1|2 TAG HOST_B:PORT\n"); return 2; }
    const std::string A = argv[1], phase = argv[2], tag = argv[3], B = argv[4];
    if (phase == "2") { printf("RESULT PASS phase 2 (nothing to do after the restart)\n"); return 0; }
    const std::string INC = "hcm-" + tag + "#g1", DIR = "dir-" + tag + "#1", NB = "kingco-" + tag + "#1", NB2 = "kingco-" + tag + "#overflow";

    // ---- G: the directory (on A), the incident (on A), the neighboring county's region (on B)
    daf::Client a(A), b(B);
    a.call("create_daf", "\"dan\":" + q("dir-" + tag) + ",\"owner\":\"state\""); enroll(a, DIR, "state-registrar", "coordinator");
    a.call("ns_create", "\"daf\":" + q(DIR) + ",\"space\":\"apps\"");
    a.call("ns_delegate", "\"daf\":" + q(DIR) + ",\"space\":\"apps\",\"prefix\":\"burn-unit\",\"to\":\"kingco-registrar\"");
    a.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"g1\",\"owner\":\"pierce\""); enroll(a, INC, "pierce-coordinator", "coordinator");
    b.call("create_daf", "\"dan\":" + q("kingco-" + tag) + ",\"owner\":\"kingco\""); enroll(b, NB, "kingco-coordinator", "coordinator");
    b.call("define_name", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\",\"rule\":\"one\"");
    daf::Client burn(B); enroll(burn, NB, "harborview-burn"); burn.call("join", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\",\"facts\":{\"beds\":6}");
    { daf::Client reg(A); enroll(reg, DIR, "kingco-registrar");          // the neighboring region registers what it offers
      Json r = reg.call("ns_assign", "\"daf\":" + q(DIR) + ",\"space\":\"apps\",\"name\":\"burn-unit\",\"target\":{\"object\":{\"region\":" + q(B) + ",\"daf\":" + q(NB) + "}}");
      if (!r["ok"].b) { printf("registration refused: %s\n", r["why"].s.c_str()); return 2; } }
    // a member of the incident needs a burn unit; the incident has none
    { daf::Client amb(A); enroll(amb, INC, "amb-3", "ambulance");
      Json local = amb.call("resolve", "\"daf\":" + q(INC) + ",\"name\":\"burn-unit\"");
      daf::Client dir(A); enroll(dir, DIR, "amb-3");
      daf::Joined j = daf::find_and_join(dir, DIR, "burn-unit", "amb-3", "mutual-aid");
      Json pick = j.ok ? j.session->call("resolve", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\"") : Json{};
      Json fo = j.ok ? j.session->call("flow_open", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\"") : Json{};
      expect("G", "a member requests a name offered only by another region: found, enrolled, allocated",
             !local["ok"].b && j.ok && apns(pick) == "harborview-burn" && fo["to"].s == "harborview-burn",
             "in the incident: " + local["error"].s + "; found in " + j.daf + " at " + j.region + "; flow to " + fo["to"].s); }
    // the owner refuses
    { b.call("set_policy", "\"daf\":" + q(NB) + ",\"key\":\"role:tourist\",\"mode\":\"refuse\"");
      daf::Client dir(A); enroll(dir, DIR, "curious-1");
      daf::Joined j = daf::find_and_join(dir, DIR, "burn-unit", "curious-1", "tourist");
      Json m = b.call("members", "\"daf\":" + q(NB)); bool in = false; for (auto& x : m["members"].a) if (x["apn"].s == "curious-1") in = true;
      Json missing; { daf::Joined k = daf::find_and_join(dir, DIR, "no-such-app", "curious-1", "tourist"); missing = Json::parse("{\"e\":" + q(k.error) + ",\"w\":" + q(k.why) + "}"); }
      expect("G", "the owner refuses: the requester is told \"not found\" and is not enrolled",
             !j.ok && j.error == missing["e"].s && j.why == missing["w"].s && !in, "refused: " + j.error + " " + j.why + " | missing app: " + missing["e"].s + " " + missing["w"].s); }
    // the owner offers an alternative
    { b.call("create_daf", "\"dan\":" + q("kingco-" + tag) + ",\"daii\":\"overflow\",\"owner\":\"kingco\""); daf::Client bo(B); enroll(bo, NB2, "kingco-overflow-coordinator", "coordinator");
      b.call("set_policy", "\"daf\":" + q(NB) + ",\"key\":\"role:overflow\",\"mode\":\"redirect\",\"to\":{\"region\":" + q(B) + ",\"daf\":" + q(NB2) + "}");
      daf::Client dir(A); enroll(dir, DIR, "amb-9");
      daf::Joined j = daf::find_and_join(dir, DIR, "burn-unit", "amb-9", "overflow");
      bool accepted = false;
      if (j.error == "EREDIRECT") { daf::Client alt(j.alternative["region"].s); Json e = alt.call("enroll", "\"daf\":" + q(j.alternative["daf"].s) + ",\"apn\":\"amb-9\",\"role\":\"overflow\"");
          accepted = e["ok"].b && j.alternative["daf"].s == NB2; }
      expect("G", "the owner offers an alternative region: the requester is told, and can accept it", j.error == "EREDIRECT" && accepted,
             j.error + " -> " + j.alternative["daf"].s + (accepted ? ", accepted" : "")); }
    // a new region spanning two owners: created only when both agree
    { const std::string KA = "pierce-county-key-0001", KB = "king-county-key-00002", SD = "mutual-" + tag + "#1";
      a.call("register_owner", "\"owner\":\"pierce\",\"key\":" + q(KA)); a.call("register_owner", "\"owner\":\"kingco\",\"key\":" + q(KB));
      Json p = a.call("propose_daf", "\"dan\":" + q("mutual-" + tag) + ",\"owners\":[\"pierce\",\"kingco\"]");
      Json ap1 = a.call("approve_daf", "\"daf\":" + q(SD) + ",\"owner\":\"pierce\",\"nonce\":\"nonce-0001\",\"proof\":" + q(hmac_hex(KA, "approve|" + SD + "|nonce-0001")));
      daf::Client m1(A); Json early = m1.call("enroll", "\"daf\":" + q(SD) + ",\"apn\":\"pierce-coordinator\",\"role\":\"coordinator\"");
      Json forged = a.call("approve_daf", "\"daf\":" + q(SD) + ",\"owner\":\"kingco\",\"nonce\":\"nonce-0002\",\"proof\":" + q(hmac_hex(KA, "approve|" + SD + "|nonce-0002")));
      Json ap2 = a.call("approve_daf", "\"daf\":" + q(SD) + ",\"owner\":\"kingco\",\"nonce\":\"nonce-0003\",\"proof\":" + q(hmac_hex(KB, "approve|" + SD + "|nonce-0003")));
      daf::Client m2(A); Json e1 = m1.call("enroll", "\"daf\":" + q(SD) + ",\"apn\":\"pierce-coordinator\",\"role\":\"coordinator\"");
      Json e2 = m2.call("enroll", "\"daf\":" + q(SD) + ",\"apn\":\"kingco-coordinator\",\"role\":\"coordinator\"");
      expect("G", "a new region for two members of different regions: created only when both owners agree",
             p["ok"].b && ap1["state"].s == "proposed" && !early["ok"].b && !forged["ok"].b && ap2["state"].s == "created" && e1["ok"].b && e2["ok"].b,
             "after one approval: " + ap1["state"].s + ", enroll " + early["error"].s + "; a forged approval: " + forged["error"].s + "; after both: " + ap2["state"].s); }

    // ---- I: requests without enrollment
    const std::string SV = "shelter-" + tag + "#1";
    daf::Client s(A); s.call("create_daf", "\"dan\":" + q("shelter-" + tag) + ",\"owner\":\"redcross\""); enroll(s, SV, "rc-coordinator", "coordinator");
    s.call("define_name", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"rule\":\"one\"");
    daf::Client d1(A), d2(A); enroll(d1, SV, "desk-1"); enroll(d2, SV, "desk-2");
    d1.call("join", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"facts\":{\"load\":1}");
    d2.call("join", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"facts\":{\"load\":5}");
    std::atomic<bool> stop{false};
    std::thread t2([&] { serve(d2, SV, "desk-2", stop, "Jane Doe checked in at the Tacoma Dome shelter"); });
    { daf::Client pub(A);                                                   // a member of the public: never enrolls
      auto t0 = std::chrono::steady_clock::now();
      Json rq = pub.call("request", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"rule\":\"one\",\"rank\":{\"f\":\"load\"},\"body\":{\"ask\":\"Jane Doe\"}");
      Json an = rq["ok"].b ? wait_answer(pub, SV, rq["rid"].s, 5) : Json{};
      const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
      Json mem = s.call("members", "\"daf\":" + q(SV)); bool listed = false; for (auto& m : mem["members"].a) if (m["apn"].s.empty() || m["apn"].s == "(not a member)") listed = true;
      Json all = s.call("resolve", "\"daf\":" + q(SV) + ",\"name\":\"members\"");
      expect("I", "a non-member sends a request to a service name: it is answered and appears in no member list",
             rq["ok"].b && an["ok"].b && an["body"]["text"].s.find("Jane Doe") != std::string::npos && !listed && apns(all).find("not a member") == std::string::npos,
             "answer from " + an["body"]["from"].s + " in " + std::to_string(int(ms)) + " ms; members: " + apns(all));
      Json peek = pub.call("resolve", "\"daf\":" + q(SV) + ",\"name\":\"reunification\""), peek2 = pub.call("members", "\"daf\":" + q(SV));
      expect("I", "the non-member tries to read the DAF: not found", !peek["ok"].b && peek["why"].s == "not found" && !peek2["ok"].b && peek2["why"].s == "not found", peek["error"].s + " " + peek["why"].s); }
    // the selected member leaves before answering
    { daf::Client* d3 = new daf::Client(A); enroll(*d3, SV, "desk-3"); d3->call("join", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"facts\":{\"load\":9}");
      daf::Client pub(A);
      Json rq = pub.call("request", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"rule\":\"one\",\"rank\":{\"f\":\"load\"},\"body\":{\"ask\":\"John Roe\"}");
      std::this_thread::sleep_for(std::chrono::milliseconds(100)); delete d3;          // desk-3 had it, and left without answering
      Json an = wait_answer(pub, SV, rq["rid"].s, 5);
      Json rq2 = pub.call("request", "\"daf\":" + q(SV) + ",\"name\":\"reunification\",\"rule\":\"one\",\"rank\":{\"f\":\"load\"},\"body\":{\"ask\":\"John Roe\"}");
      Json an2 = wait_answer(pub, SV, rq2["rid"].s, 5);
      expect("I", "the selected member leaves before answering: an error, not silence; a retry is answered",
             !an["ok"].b && an["error"].s == "gone" && an2["ok"].b, "first: " + an["error"].s + " (" + an["why"].s + "); retry answered by " + an2["body"]["from"].s); }
    stop = true; t2.join();
    // a message stored for a member who is offline
    { daf::Client pub(A);
      Json st = pub.call("request", "\"daf\":" + q(SV) + ",\"to\":\"desk-7\",\"store\":true,\"body\":{\"note\":\"your sister is looking for you\"}");
      daf::Client d7(A); Json e = enroll(d7, SV, "desk-7");
      expect("I", "a message stored for a member who is offline: received on its next enrollment",
             st["ok"].b && e["stored"].a.size() == 1 && e["stored"].a[0]["m"]["body"]["note"].s.find("sister") != std::string::npos, std::to_string(e["stored"].a.size()) + " stored message(s)"); }
    printf("RESULT %s phase %s\n", bad ? "FAIL" : "PASS", phase.c_str());
    return bad ? 1 : 0;
}
