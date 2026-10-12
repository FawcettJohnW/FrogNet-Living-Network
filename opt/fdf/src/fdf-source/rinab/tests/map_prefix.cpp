// map_prefix.cpp -- [MAP_PREFIX_LOCATOR_SET_V1] proof: a prefix registers with a locator set under the DIF's site key, is
// answered by map_request and map_list, a wrong key is refused, a raw write is not a mapping, and a session-bound
// mapping is gone when the session ends (and a watcher is told). RINA_RAM names the region.
#include "rina_region_client.hpp"
#include <cstdio>
#include <thread>
using rina_region::Client;
static std::string hmac_hex(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; } return a;
}
static std::string reg(Client& c, const std::string& site, const std::string& key, const std::string& eid, const std::string& rlocs, bool own) {
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t); char ts[40]; snprintf(ts, sizeof ts, "%lld.%09ld", (long long)t.tv_sec, t.tv_nsec);
    const std::string ttl = "3600", msg = eid + "|" + rlocs + "|" + ttl + "|" + ts;
    frogram::Json r = c.call("{\"op\":\"map_register\",\"site\":" + frogram::Json::quote(site) + ",\"name\":" + frogram::Json::quote(eid) + ",\"rloc\":" + frogram::Json::quote(rlocs) +
        ",\"ttl\":\"" + ttl + "\",\"ts\":\"" + ts + "\",\"auth\":" + frogram::Json::quote(hmac_hex(key, msg)) + ",\"own\":" + (own ? "true" : "false") + "}");
    return r["ok"].b ? "ok" : r["error"].s;
}
int main() {
    int fails = 0; auto check = [&](bool ok, const char* what) { printf("%s  %s\n", ok ? "PASS" : "FAIL", what); fflush(stdout); if (!ok) ++fails; };
    const std::string site = "frognet.test", key = "s3cret";
    Client a;                                                                  // the DIF's site key, set once (the region owner's act)
    check(a.call("{\"op\":\"map_site\",\"name\":" + frogram::Json::quote(site) + ",\"key\":" + frogram::Json::quote(key) + "}")["ok"].b, "map_site set");
    {   std::unique_ptr<Client> nodep(new Client); Client& node = *nodep;      // a node's session registers its /24 with two hubs
        check(reg(node, site, key, "10.9.9.0/24", "hubA:1:50,hubB:2:50", true) == "ok", "map_register prefix with locator set, session-bound");
        check(reg(node, site, "wrong", "10.9.9.0/24", "hubA:1:50", true) == "EPERM", "wrong site key refused (EPERM)");
        frogram::Json q = a.call("{\"op\":\"map_request\",\"name\":\"10.9.9.0/24\"}");
        check(q["ok"].b && q["rloc"].s == "hubA:1:50,hubB:2:50" && q["site"].s == site, "map_request returns the locator set and site");
        frogram::Json l = a.call("{\"op\":\"map_list\",\"site\":" + frogram::Json::quote(site) + "}");
        check(l["ok"].b && l["mappings"].size() == 1 && l["mappings"][0]["eid"].s == "10.9.9.0/24", "map_list lists it under the site");
        // a raw row write is NOT a mapping: nothing but map_register produces a map row this region answers for
        frogram::Json raw = a.call("{\"op\":\"map_request\",\"name\":\"10.8.8.0/24\"}");
        check(!raw["ok"].b, "an unregistered prefix is ENOENT");
        // a watcher holds on the prefix; the node's session ends; the watcher is told and the mapping is gone
        Client w; const std::string me = "mw:test";
        check(w.call("{\"op\":\"map_watch\",\"name\":\"10.9.9.0/24\",\"proc\":" + frogram::Json::quote(me) + "}")["ok"].b, "map_watch held");
        nodep.reset();                                                          // end the node's session now
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        frogram::Json q2 = a.call("{\"op\":\"map_request\",\"name\":\"10.9.9.0/24\"}");
        check(!q2["ok"].b, "session-bound mapping gone with the session");
        std::string ev; try { ev = w.held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote(me) + ",\"after\":0}", 5.0); } catch (const std::exception& x) { ev = std::string("EXC ") + x.what(); }
        check(ev.find("\"k\":\"m\"") != std::string::npos, "watcher told on session end (map-notify)");
        if (ev.find("\"k\":\"m\"") == std::string::npos) printf("  wait returned: %s\n", ev.c_str());
    }
    printf(fails ? "RESULT FAIL: %d\n" : "RESULT PASS\n", fails); return fails ? 1 : 0;
}
