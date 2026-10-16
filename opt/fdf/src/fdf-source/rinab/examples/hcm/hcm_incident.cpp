// hcm_incident.cpp -- [DAF_MODEL_V1] the plan's demonstration: one scripted incident that exercises every section of the
// DAF model through the hospital capacity manager. Each step checks what the system answers, and what the board shows.
// usage: hcm_incident HOST_A:PORT HOST_B:PORT TAG BOARD_PORT
//   host A: the incident's region and the state's directory; host B: the neighboring county's region (another island)
// The board (hcm_board) must be started on the incident's DAF, "hcm-<TAG>#incident", once step 1 has created it.
#include "daf_client.hpp"
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <atomic>
#include <functional>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
using daf::Json; using daf::q;
static int bad = 0, step_no = 0;
static void check(const std::string& what, bool ok, const std::string& detail = "") {
    printf("  %-70s %s%s%s\n", what.c_str(), ok ? "ok" : "FAIL", detail.empty() ? "" : "  ", detail.c_str()); fflush(stdout); if (!ok) ++bad;
}
// HCM_PAUSE_MS: a pause before each step, for a person watching the board
static void step(const std::string& title, const std::string& sections) {
    if (const char* p = getenv("HCM_PAUSE_MS"); p && step_no > 0) std::this_thread::sleep_for(std::chrono::milliseconds(atoi(p)));
    printf("%d. %s (%s)\n", ++step_no, title.c_str(), sections.c_str()); fflush(stdout); }
static Json must_ok(const Json& r, const char* what) { if (!r["ok"].b) { printf("  %s refused: %s %s\n", what, r["error"].s.c_str(), r["why"].s.c_str()); exit(2); } return r; }
static std::string http_get(int port, const std::string& path) {
    int s = socket(AF_INET, SOCK_STREAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(port)); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(s, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0) { close(s); return ""; }
    const std::string rq = "GET " + path + " HTTP/1.0\r\n\r\n"; (void)!send(s, rq.data(), rq.size(), 0);
    std::string out; char b[4096]; ssize_t n; while ((n = recv(s, b, sizeof b, 0)) > 0) out.append(b, size_t(n)); close(s);
    const size_t h = out.find("\r\n\r\n"); return h == std::string::npos ? "" : out.substr(h + 4);
}
// what the board shows, once it shows `want` as the trauma choice (or after 3 s)
static Json board_until(int port, const std::string& want) {
    Json j; for (int i = 0; i < 60; ++i) { const std::string t = http_get(port, "/state.json"); try { j = Json::parse(t); } catch (...) {}
        if (j["trauma_choice"].s == want) return j; std::this_thread::sleep_for(std::chrono::milliseconds(50)); }
    return j;
}
static bool board_live(const Json& b, const std::string& apn) { for (auto& h : b["hospitals"].a) if (h["apn"].s == apn) return h["live"].b; return false; }
static bool board_has(const Json& b, const std::string& apn) { for (auto& h : b["hospitals"].a) if (h["apn"].s == apn) return true; return false; }
static std::string apns(const Json& r) { std::string s; for (auto& m : r["members"].a) s += (s.empty() ? "" : ",") + m["apn"].s; return s; }
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: hcm_incident HOST_A:PORT HOST_B:PORT TAG BOARD_PORT\n"); return 2; }
    const std::string A = argv[1], B = argv[2], tag = argv[3]; const int BP = atoi(argv[4]);
    const std::string I = "hcm-" + tag + "#incident", DIR = "dir-" + tag + "#1", NB = "kingco-" + tag + "#1", SH = "shelter-" + tag + "#1";
    const std::string TRAUMA = "\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}";
    auto inc = [&](const std::string& rest) { return "\"daf\":" + q(I) + (rest.empty() ? "" : "," + rest); };

    // 1. the incident is opened: a new instance of the application; the coordinator enrolls and becomes command
    step("An incident is opened; the coordinator enrolls and becomes command", "A, B, C");
    daf::Client coord(A);
    must_ok(coord.call("create_daf", "\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"incident\",\"owner\":\"pierce-county\",\"synsets\":[\"catalog\"]"), "create_daf");
    must_ok(coord.call("enroll", inc("\"apn\":\"pierce-ems-coordinator\",\"role\":\"coordinator\"")), "enroll coordinator");
    for (auto n : {"\"name\":\"receiving\",\"rule\":\"one\"", "\"name\":\"facilities\",\"rule\":\"all\",\"lifetime\":\"persistent\"", "\"name\":\"command\",\"rule\":\"designated\""})
        must_ok(coord.call("define_name", inc(n)), "define_name");
    coord.call("join", inc("\"name\":\"command\",\"facts\":{}")); coord.call("designate", inc("\"name\":\"command\",\"apn\":\"pierce-ems-coordinator\""));
    coord.call("set_objects", inc("\"role\":\"hospital\",\"objects\":[\"receiving\",\"facilities\",\"flow:*\"]"));
    coord.call("set_objects", inc("\"role\":\"ambulance\",\"objects\":[\"receiving\",\"command\",\"flow:*\"]"));
    for (auto f : {"st-joseph", "good-samaritan", "tacoma-general", "mary-bridge"})
        coord.call("set_ref", inc("\"key\":" + q(std::string("facility:") + f) + ",\"v\":{\"name\":" + q(f) + ",\"county\":\"pierce\"}"));
    Json cmd = coord.call("resolve", inc("\"name\":\"command\""));
    check("command resolves to the coordinator", apns(cmd) == "pierce-ems-coordinator", apns(cmd));
    printf("  (board) start hcm_board %s %s %d now\n", A.c_str(), I.c_str(), BP); fflush(stdout);
    std::this_thread::sleep_for(std::chrono::milliseconds(800));

    // 2. three hospitals and two ambulances enroll in their roles; each sees only its own object set
    step("Three hospitals and two ambulances enroll in their roles", "C, D");
    struct H { std::string apn; bool trauma; int cap; daf::Client* c; std::string token; };
    H hs[3] = {{"st-joseph", true, 6, nullptr, ""}, {"good-samaritan", true, 4, nullptr, ""}, {"tacoma-general", true, 2, nullptr, ""}};
    bool all_ref = true;
    for (auto& h : hs) { h.c = new daf::Client(A);
        Json e = must_ok(h.c->call("enroll", inc("\"apn\":" + q(h.apn) + ",\"role\":\"hospital\"")), "enroll hospital");
        all_ref = all_ref && e["ref"].a.size() == 4 && !e["syn"]["addr"].s.empty(); h.token = e["token"].s;
        coord.call("join", inc("\"name\":\"facilities\",\"apn\":" + q(h.apn) + ",\"facts\":{\"beds\":300}"));
        must_ok(h.c->call("join", inc("\"name\":\"receiving\",\"facts\":{\"trauma\":" + std::string(h.trauma ? "true" : "false") + ",\"capacity\":" + std::to_string(h.cap) + "}")), "join receiving"); }
    daf::Client amb1(A), amb2(A);
    Json ea = must_ok(amb1.call("enroll", inc("\"apn\":\"medic-12\",\"role\":\"ambulance\"")), "enroll ambulance");
    must_ok(amb2.call("enroll", inc("\"apn\":\"medic-31\",\"role\":\"ambulance\"")), "enroll ambulance");
    Json g = amb1.call("connect", inc("\"ae_name\":\"dispatch\",\"objects\":[\"receiving\",\"command\",\"facilities\"]"));
    std::string gs; for (auto& o : g["granted"].a) gs += o.s + " ";
    check("each received the reference data and its synonyms", all_ref, "4 facilities, synonyms assigned");
    check("an ambulance's connection is granted only its role's object set", gs == "receiving command ", "asked receiving command facilities; granted " + gs);

    // 3. an ambulance asks where to send a trauma patient; the patient's record goes on a bulk flow
    step("An ambulance asks where to send a trauma patient", "B, D");
    Json fo = must_ok(amb1.call("flow_open", inc("\"name\":\"receiving\"," + TRAUMA + ",\"spec\":{\"qos\":\"bulk\"}")), "flow_open");
    daf::Client bulk(A); must_ok(bulk.call("connect", inc("\"ae_name\":\"records\",\"objects\":[\"flow:*\"],\"apn\":\"medic-12\",\"token\":" + q(ea["token"].s))), "connect records");
    const std::string record(512 * 1024, 'r');
    Json fs = bulk.call("flow_send", inc("\"fid\":" + q(fo["fid"].s) + ",\"seq\":0,\"data\":{\"patient\":\"P-0193\",\"ecg\":" + q(record) + "}"));
    auto box = hs[0].c->memory().read("daf", "box|" + I + "|st-joseph", fo["fid"].s);
    auto rec = hs[0].c->memory().read("daf", "fd|" + I + "|" + fo["fid"].s + "|f", "0");
    check("the trauma choice is st-joseph; the flow reached it", fo["to"].s == "st-joseph" && box.size() == 1, "to " + fo["to"].s);
    check("the patient record (512 KB) arrived on its own connection", fs["ok"].b && rec.size() == 1 && rec[0].bag["d"]["ecg"].s.size() == record.size(), "");
    Json b3 = board_until(BP, "st-joseph");
    check("the board shows st-joseph as the trauma choice", b3["trauma_choice"].s == "st-joseph", b3["trauma_choice"].s);

    // 4. st-joseph's capacity drops to zero; every board updates; the next trauma choice is good-samaritan
    step("St. Joseph's capacity drops to zero", "B, E");
    hs[0].c->call("join", inc("\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":0}"));
    Json c4 = amb2.call("resolve", inc("\"name\":\"receiving\"," + TRAUMA));
    Json b4 = board_until(BP, "good-samaritan");
    check("the next trauma choice is good-samaritan", apns(c4) == "good-samaritan", apns(c4));
    check("the board shows it, without being asked", b4["trauma_choice"].s == "good-samaritan", b4["trauma_choice"].s);

    // 5. good-samaritan loses its connection; it leaves receiving but stays in facilities; it returns on a short rejoin
    step("Good Samaritan loses its connection, then comes back", "B, C");
    Json syn_before = must_ok(hs[1].c->call("whoami", inc("")), "whoami");
    delete hs[1].c; hs[1].c = nullptr;
    Json c5 = amb2.call("resolve", inc("\"name\":\"receiving\"," + TRAUMA));
    Json fac = coord.call("resolve", inc("\"name\":\"facilities\""));
    Json b5 = board_until(BP, "tacoma-general");
    check("it leaves receiving; the choice moves to tacoma-general", apns(c5) == "tacoma-general", apns(c5));
    check("it stays in facilities (the roster), shown offline on the board", apns(fac).find("good-samaritan") != std::string::npos && board_has(b5, "good-samaritan") && !board_live(b5, "good-samaritan"), apns(fac));
    hs[1].c = new daf::Client(A);
    Json back = must_ok(hs[1].c->call("enroll", inc("\"apn\":\"good-samaritan\",\"role\":\"hospital\"")), "re-enroll");
    hs[1].c->call("join", inc("\"name\":\"receiving\",\"facts\":{\"trauma\":true,\"capacity\":4}"));
    Json b5b = board_until(BP, "good-samaritan");
    check("it returns on a short rejoin: same synonym, reference data not resent", back["state"].s == "short" && back["syn"]["addr"].s == syn_before["member"]["syn"]["addr"].s && back["ref"].a.empty(),
          back["state"].s + ", synonym " + back["syn"]["addr"].s);
    check("the board shows it connected and chosen again", board_live(b5b, "good-samaritan") && b5b["trauma_choice"].s == "good-samaritan", b5b["trauma_choice"].s);

    // 6. the network splits in two, then rejoins: persistent groups merge; live membership rebuilds from who is present
    step("The network splits, and rejoins", "C");
    daf::Client island(B);
    Json ex = must_ok(coord.call("export", inc("")), "export");
    auto cells = [&](const Json& e) { std::string s = "["; bool f = true; for (auto& c : e["cells"].a) { s += (f ? "" : ",") + std::string("{\"v\":") + q(c["v"].s) + ",\"i\":" + q(c["i"].s) + ",\"bag\":" + [&] {
        std::function<std::string(const Json&)> d = [&](const Json& j) -> std::string { switch (j.type) { case Json::Null: return "null"; case Json::Bool: return j.b ? "true" : "false";
            case Json::Num: { char x[40]; snprintf(x, sizeof x, "%.17g", j.n); return x; } case Json::Str: return q(j.s);
            case Json::Arr: { std::string o = "["; for (size_t k = 0; k < j.a.size(); ++k) o += (k ? "," : "") + d(j.a[k]); return o + "]"; }
            default: { std::string o = "{"; for (size_t k = 0; k < j.o.size(); ++k) o += (k ? "," : "") + q(j.o[k].first) + ":" + d(j.o[k].second); return o + "}"; } } };
        return d(c["bag"]); }() + "}"; f = false; } return s + "]"; };
    must_ok(island.call("merge", inc("\"since\":0,\"daf_info\":{\"dan\":" + q("hcm-" + tag) + ",\"daii\":\"incident\",\"owner\":\"pierce-county\",\"synsets\":[\"addr\",\"catalog\"],\"syn_lifetime_s\":300},\"cells\":" + cells(ex))), "copy to the island");
    must_ok(island.call("enroll", inc("\"apn\":\"gig-harbor-coordinator\",\"role\":\"coordinator\"")), "enroll on the island");
    const double split = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    island.call("join", inc("\"name\":\"facilities\",\"apn\":\"st-anthony\",\"facts\":{\"beds\":110}"));       // admitted on the island
    coord.call("join", inc("\"name\":\"facilities\",\"apn\":\"mary-bridge\",\"facts\":{\"beds\":80}"));          // admitted here
    char sb[40]; snprintf(sb, sizeof sb, "%.6f", split);
    Json mg = must_ok(coord.call("merge", inc("\"since\":" + std::string(sb) + ",\"cells\":" + cells(must_ok(island.call("export", inc("")), "island export")))), "merge");
    Json fac6 = coord.call("resolve", inc("\"name\":\"facilities\""));
    Json live6 = coord.call("resolve", inc("\"name\":\"receiving\",\"rule\":\"all\""));
    check("persistent groups merge: both sides' admissions are in facilities", apns(fac6) == "good-samaritan,mary-bridge,st-anthony,st-joseph,tacoma-general", apns(fac6));
    check("live membership is whoever is connected here", apns(live6) == "good-samaritan,st-joseph,tacoma-general", apns(live6) + "; merge: " + std::to_string(int(mg["conflicts"].n)) + " conflicts");

    // 7. a burn unit is needed and none is in the incident: the neighboring county's region is found and joined
    step("A burn unit is needed; the incident has none", "F, G");
    daf::Client dir(A), kc(B), burn(B), kreg(A);
    dir.call("create_daf", "\"dan\":" + q("dir-" + tag) + ",\"owner\":\"wa-state\""); must_ok(dir.call("enroll", "\"daf\":" + q(DIR) + ",\"apn\":\"state-registrar\",\"role\":\"coordinator\""), "enroll dir");
    dir.call("ns_create", "\"daf\":" + q(DIR) + ",\"space\":\"apps\"");
    dir.call("ns_delegate", "\"daf\":" + q(DIR) + ",\"space\":\"apps\",\"prefix\":\"burn-unit\",\"to\":\"kingco-registrar\"");
    kc.call("create_daf", "\"dan\":" + q("kingco-" + tag) + ",\"owner\":\"king-county\""); must_ok(kc.call("enroll", "\"daf\":" + q(NB) + ",\"apn\":\"kingco-coordinator\",\"role\":\"coordinator\""), "enroll kc");
    kc.call("define_name", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\",\"rule\":\"one\"");
    must_ok(burn.call("enroll", "\"daf\":" + q(NB) + ",\"apn\":\"harborview-burn-center\",\"role\":\"hospital\""), "enroll burn");
    burn.call("join", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\",\"facts\":{\"beds\":4}");
    must_ok(kreg.call("enroll", "\"daf\":" + q(DIR) + ",\"apn\":\"kingco-registrar\""), "enroll registrar");
    must_ok(kreg.call("ns_assign", "\"daf\":" + q(DIR) + ",\"space\":\"apps\",\"name\":\"burn-unit\",\"target\":{\"object\":{\"region\":" + q(B) + ",\"daf\":" + q(NB) + "}}"), "register burn-unit");
    Json local = amb1.call("resolve", inc("\"name\":\"burn-unit\""));
    daf::Client adir(A); must_ok(adir.call("enroll", "\"daf\":" + q(DIR) + ",\"apn\":\"medic-12\""), "enroll medic in dir");
    daf::Joined j = daf::find_and_join(adir, DIR, "burn-unit", "medic-12", "mutual-aid");
    Json pick = j.ok ? j.session->call("resolve", "\"daf\":" + q(NB) + ",\"name\":\"burn-unit\"") : Json{};
    check("the incident has no burn-unit", !local["ok"].b, local["error"].s);
    check("the directory finds King County's region; its owner admits medic-12", j.ok, j.ok ? j.daf + " at " + j.region : j.error + " " + j.why);
    check("a burn center there is selected", apns(pick) == "harborview-burn-center", apns(pick));

    // 8. a member of the public asks whether a relative has checked in, without joining
    step("A member of the public asks about a relative", "I");
    daf::Client rc(A); rc.call("create_daf", "\"dan\":" + q("shelter-" + tag) + ",\"owner\":\"red-cross\""); must_ok(rc.call("enroll", "\"daf\":" + q(SH) + ",\"apn\":\"rc-coordinator\",\"role\":\"coordinator\""), "enroll rc");
    rc.call("define_name", "\"daf\":" + q(SH) + ",\"name\":\"reunification\",\"rule\":\"one\"");
    daf::Client desk(A); must_ok(desk.call("enroll", "\"daf\":" + q(SH) + ",\"apn\":\"tacoma-dome-desk\""), "enroll desk");
    desk.call("join", "\"daf\":" + q(SH) + ",\"name\":\"reunification\",\"facts\":{}");
    std::atomic<bool> stop{false};
    std::thread server([&] { int64_t after = 0; while (!stop) { auto got = desk.memory().read("daf", "req|" + SH + "|tacoma-dome-desk", "", after, 0.3);
        for (auto& r : got) { after = std::max<int64_t>(after, int64_t(r.id)); if (r.bag["gone"].b) continue;
            desk.call("answer", "\"daf\":" + q(SH) + ",\"rid\":" + q(r.instance) + ",\"body\":{\"found\":true,\"where\":\"Tacoma Dome shelter, checked in 14:05\"}"); } } });
    daf::Client pub(A);
    Json rq = pub.call("request", "\"daf\":" + q(SH) + ",\"name\":\"reunification\",\"body\":{\"ask\":\"Maria Lopez, 34\"}");
    auto an = rq["ok"].b ? pub.memory().read("daf", "ans|" + SH, rq["rid"].s, 0, 5.0) : std::vector<frogram::Cell>{};
    Json peek = pub.call("members", "\"daf\":" + q(SH));
    check("the answer comes back; the asker never joined", !an.empty() && an[0].bag["body"]["found"].b && !peek["ok"].b, an.empty() ? "no answer" : an[0].bag["body"]["where"].s);

    // 9. the refusing test policy is switched on for the public, and the same request is refused with its reason
    step("The refusing test policy is switched on for the public", "H");
    must_ok(rc.call("set_policy", "\"daf\":" + q(SH) + ",\"key\":\"op:request\",\"mode\":\"refuse\""), "set_policy");
    Json rq2 = pub.call("request", "\"daf\":" + q(SH) + ",\"name\":\"reunification\",\"body\":{\"ask\":\"Maria Lopez, 34\"}");
    Json au = rc.call("audit", "\"daf\":" + q(SH)); std::string last; for (auto& r : au["records"].a) if (r["r"]["op"].s == "request") last = r["r"]["decision"].s + ": " + r["r"]["why"].s;
    check("refused, with its reason; the audit records it", !rq2["ok"].b && rq2["error"].s == "EPERM" && last.find("refuse") == 0, rq2["why"].s);
    stop = true; server.join();

    if (const char* p = getenv("HCM_PAUSE_MS")) std::this_thread::sleep_for(std::chrono::milliseconds(atoi(p)));
    printf("\nThe coordinator's and ambulances' programs used only names -- never an address.\nRESULT %s\n", bad ? "FAIL" : "PASS");
    for (auto& h : hs) delete h.c;
    return bad ? 1 : 0;
}
