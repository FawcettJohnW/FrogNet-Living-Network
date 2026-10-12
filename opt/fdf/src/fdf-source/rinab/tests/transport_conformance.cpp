// transport_conformance.cpp -- [TRANSPORT_INTERFACE_V1] T2: a transport answers every definition in src/transport.hpp.
// The checks use ONLY the interface (transport::Transport&); the one line naming FD&F is the constructor. Needs a RAM
// manager the transport can locate (RINA_RAM or RINA_AREA). Exit 0 only if every definition answers correctly.
#include "fdf_transport.hpp"
#include <chrono>
#include <cstdio>
#include <future>
static int bad = 0;
static void check(const char* what, bool ok, const std::string& got) { printf("%-12s %-4s %s\n", what, ok ? "ok" : "FAIL", got.c_str()); bad += !ok; }
static int run(transport::Transport& t) {
    const std::string where = t.locate("");
    check("locate", where.find(':') != std::string::npos, where);
    auto s = t.connect(where); check("connect", bool(s), "socket to " + where);
    transport::Participant me; me.id = "conformance:" + std::to_string(getpid()); me.name = "conformance";
    me.ips = {"192.0.2.99"}; me.capability = "{\"cores\":3,\"mem_total_kb\":1234}";
    t.announce(*s, me); check("announce", true, me.id);
    bool found = false;
    for (auto& p : t.participants(*s)) if (p.id == me.id) {
        found = p.name == me.name && p.ips == me.ips && p.capability.find("\"cores\":3") != std::string::npos && !p.alive;   // no session of its own: not alive
        check("participants", found, p.name + " " + (p.ips.empty() ? "" : p.ips[0]) + " " + p.capability + (p.alive ? " alive" : " not-alive")); }
    if (!found) check("participants", false, "the announcement is not listed");
    const std::string cap = t.capability(*s, me.id); check("capability", cap.find("\"mem_total_kb\":1234") != std::string::npos, cap);
    auto s2 = t.connect(where);                                   // CHANGE: one waiter, then a change it must hear of
    auto waiter = std::async(std::launch::async, [&] { return t.change(*s2, 0); });
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    me.ips = {"192.0.2.100"}; t.announce(*s, me);                 // the participant set changes
    const bool heard = waiter.wait_for(std::chrono::seconds(5)) == std::future_status::ready;
    check("change", heard && waiter.get() > 0, heard ? "returned after the re-announcement" : "did not return within 5 s");
    printf("RESULT %s\n", bad ? "FAIL" : "PASS"); return bad;
}
int main() {
    try { fdf::Transport t; return run(t); }
    catch (const std::exception& e) { printf("FAIL: %s\nRESULT FAIL\n", e.what()); return 1; }
}
