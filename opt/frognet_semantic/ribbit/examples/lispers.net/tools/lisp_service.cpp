/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// lisp_service.cpp -- the Ribbit-LISP service: the one program a machine runs after installing the LISP vendor package.
//   lisp-service [--config FILE] [--service]
// The configuration is /etc/lispers.d/lisp-service.config (John 2026-09-27), unless --config names another file.
// --service runs it in the background the way lispers.net's RUN-LISP runs lisp-core: detached, output to
// logs/lisp-service.log beside the program, its pid in logs/lisp-service.pid.
//
// [CAPABILITY_REGISTRATION_V1] John 2026-09-27: "this is automatic registration of capabilities, not identification
// of peers." Every role this machine plays is registered as a capability tuple of its own, addressed by the
// capability and by what the capability is reached through -- never by which machine offers it:
//   lisp / capability|map-server   / udp:<address>:<port>   {"capability","udp","sites":[{"iid","prefix"}],...}
//   lisp / capability|map-resolver / udp:<address>:<port>   {"capability","udp",...}
//   lisp / capability|etr          / xtr:<xtr_id>           {"capability","xtr_id","prefixes":[{"iid","prefix"}],...}
//   lisp / capability|itr          / participant:<id>       {"capability","iids":[...],...}
// [CAPABILITIES_HELD_V1] Each tuple is written ONCE, by this service's own session, and lives exactly as long as that
// session: when the service stops, crashes, or its host stops answering, the memory removes it and every read held on
// the family is handed it as gone. Each role holds a read on the capabilities it uses -- an ETR the map-servers, an
// ITR the map-resolvers, a map-server the ETRs' prefixes -- and handles each as it arrives or goes: a machine that
// comes up later is seen when it writes, not after a wait. The service also holds a read on its own first tuple: when
// that goes, its session has, and it stops.
// John 2026-11-11: "make it all memory and role based". Before (John 2026-09-26): a startup wait of startup_wait_s,
// one plain read per family, and a heartbeat thread rewriting every tuple every heartbeat_s with the next beat.
//
// What lispers.net does instead (its source): RUN-LISP starts lisp-core; `lisp enable` starts one process per role
// (lisp-itr, lisp-etr, lisp-ms, lisp-mr, ...); peers are written by hand in lisp.config (lisp map-server {...},
// lisp map-resolver {...}); an ETR re-sends its whole Map-Register to every map-server every 60 s; lisp-core relays
// configuration to the role processes over IPC. Here: capability tuples instead of a hand-written peer list, held by
// the session; liveness that is the session itself and changes-only registration instead of the 60 s re-register, the shared memory instead of IPC, and the
// roles started in this one process from its configuration.
//
// The configuration (JSON):
//   name           this machine's name in the region (no '|')
//   ram            {"host": H, "port": P, "api": "/Vendor.Product.ram_interface.php"}  -- where the shared space is
//   roles          any of "map-server", "map-resolver", "etr", "itr"
//   udp            {"address": A, "port": N}           map-server / map-resolver: the LISP control-plane front
//   (heartbeat_s, startup_wait_s: gone -- refused, so a configuration from before says so)
//   sites          map-server: [{"iid","prefix","group","accept_more_specifics","key_id","password"}]
//   governor       map-server: {"iid","group"} -- governs the native registrations of Ribbit ETRs (name = `name`)
//   ms_authoritative_prefixes  map-server: ["198.18.0.0/15", ...] -- LISP-DDT authority (Map-Referrals when the
//                  machine is a map-server but not a map-resolver)
//   ms_peers       map-server: ["a.b.c.d", ...] -- its peers: the referral set in its Map-Referrals
//   etr            {"xtr_id", "database_mappings": [{"iid","prefix","group","rloc_set":[...]}]}
//                  -- its liveness cell is written at start and lives with the session ("liveness": gone, refused)
//   itr            {"iids": [{"iid","group"}]}         resolver views held from startup

#include "lisp_handler.hpp"
#include "version.hpp"
#include <climits>
#include <csignal>
#include <pthread.h>
#include <thread>
#include <fcntl.h>
#include <sys/stat.h>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>

static std::atomic<bool> g_stop{false};

static std::string dump(const Json& j) {
    switch (j.type) {
        case Json::Null: return "null";
        case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { char b[40]; std::snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case Json::Str: return Json::quote(j.s);
        case Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        case Json::Obj: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
    return "null";
}
static std::string json_str(const std::string& quoted) { return quoted.size() >= 2 ? quoted.substr(1, quoted.size() - 2) : quoted; }
static void say(const std::string& m) { std::cout << "[lisp-service] " << m << "\n" << std::flush; }
static const Json& need(const Json& j, const char* k, const char* what) {
    if (j[k].type == Json::Null) throw std::runtime_error(std::string("configuration: ") + what + " needs \"" + k + "\"");
    return j[k];
}
static bool has_role(const Json& roles, const std::string& r) { for (auto& x : roles.a) if (x.s == r) return true; return false; }

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") { std::cout << "lisp-service " << RIBBIT_LISP_VERSION << "\n"; return 0; }
    std::string config_path, dir; bool service = false;
    { char exe[PATH_MAX] = {0}; ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
      if (n <= 0) { std::cerr << "lisp-service: cannot find its own directory (/proc/self/exe)\n"; return 2; }
      dir = std::string(exe, size_t(n)); dir = dir.substr(0, dir.rfind('/')); }
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--config" && i + 1 < argc) config_path = argv[++i];
        else if (a == "--service") service = true;
        else { std::cerr << "usage: lisp-service [--config FILE] [--service]\n"; return 2; }
    }
    if (config_path.empty()) config_path = "/etc/lispers.d/lisp-service.config";
    if (service) {                                   // RUN-LISP's way: in the background, output under logs/
        const std::string logs = dir + "/logs"; ::mkdir(logs.c_str(), 0755);
        pid_t pid = ::fork();
        if (pid < 0) { std::perror("lisp-service: fork"); return 1; }
        if (pid > 0) { std::cout << "lisp-service: started, pid " << pid << ", log " << logs << "/lisp-service.log\n"; return 0; }
        ::setsid();
        int fd = ::open((logs + "/lisp-service.log").c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
        int nul = ::open("/dev/null", O_RDONLY);
        if (fd < 0 || nul < 0) return 1;
        ::dup2(nul, 0); ::dup2(fd, 1); ::dup2(fd, 2); ::close(fd); ::close(nul);
        std::ofstream(logs + "/lisp-service.pid") << ::getpid() << "\n";
    }
    Json cfg;
    { std::ifstream f(config_path); if (!f) { std::cerr << "lisp-service: cannot read " << config_path << "\n"; return 2; }
      std::stringstream ss; ss << f.rdbuf(); cfg = Json::parse(ss.str()); }
    try {
        const std::string name = need(cfg, "name", "the service").s;
        if (name.empty() || name.find('|') != std::string::npos) throw std::runtime_error("configuration: name must be non-empty, without '|'");
        const Json& ram = need(cfg, "ram", "the service");
        const std::string host = need(ram, "host", "ram").s, api = need(ram, "api", "ram").s;
        const int port = int(need(ram, "port", "ram").n);
        const Json& roles = need(cfg, "roles", "the service");
        for (const char* k : {"heartbeat_s", "startup_wait_s"})
            if (cfg[k].type != Json::Null) throw std::runtime_error(std::string("configuration: \"") + k + "\" is gone -- capability tuples live as long as this service's session and each role holds a read on the ones it uses ([CAPABILITIES_HELD_V1])");
        if (cfg["etr"].type == Json::Obj && cfg["etr"]["liveness"].type != Json::Null)
            throw std::runtime_error("configuration: etr \"liveness\" is gone -- an ETR's liveness cell is written at start and lives with its session ([LIVE_WHILE_ITS_SESSION_IS_V1])");
        const bool ms = has_role(roles, "map-server"), mr = has_role(roles, "map-resolver"), etr = has_role(roles, "etr"), itr = has_role(roles, "itr");
        // [WAIT_ON_THE_EVENT_WORD_V1] SIGINT / SIGTERM are taken by one thread in sigwait, not a handler: blocked here,
        // before any thread exists, so every thread inherits the mask
        sigset_t sigs; sigemptyset(&sigs); sigaddset(&sigs, SIGINT); sigaddset(&sigs, SIGTERM);
        pthread_sigmask(SIG_BLOCK, &sigs, nullptr);
        say(std::string("version ") + RIBBIT_LISP_VERSION + ", name " + name + ", RAM host " + host + ":" + std::to_string(port) + api);

        // The session: this Engine's Session holds the semantic socket and the high-speed data socket open for as
        // long as the service runs.
        Engine e(host, port, api);
        std::string udp_text = "null";
        std::unique_ptr<frognet::LispBoundary> front;
        if (ms || mr) {
            const Json& udp = need(cfg, "udp", "a map-server / map-resolver");
            udp_text = Json::quote(need(udp, "address", "udp").s + ":" + std::to_string(int(need(udp, "port", "udp").n)));
            front.reset(new frognet::LispBoundary(host, port, int(udp["port"].n), frognet::LispBoundary::REGISTER_WORKERS, api));
            say("LISP control-plane front on UDP " + std::to_string(int(udp["port"].n)));
        }

        // 1. register this machine's capabilities: one tuple per role, addressed by what reaches it
        struct Cap { std::string var, inst, body; };
        std::vector<Cap> caps;
        const std::string common = ",\"participant\":" + Json::quote(Engine::participant()) + ",\"version\":" + Json::quote(RIBBIT_LISP_VERSION) + "}";
        if (ms) {
            std::string sites = "[";
            for (auto& x : cfg["sites"].a) sites += (sites.size() > 1 ? "," : "") + std::string("{\"iid\":") + Json::quote(x["iid"].s) + ",\"prefix\":" + Json::quote(x["prefix"].s) + "}";
            caps.push_back({"capability|map-server", "udp:" + json_str(udp_text), "{\"capability\":\"map-server\",\"udp\":" + udp_text + ",\"sites\":" + sites + "]" + common});
        }
        if (mr) caps.push_back({"capability|map-resolver", "udp:" + json_str(udp_text), "{\"capability\":\"map-resolver\",\"udp\":" + udp_text + common});
        if (etr) {
            const Json& x = need(cfg, "etr", "an etr");
            std::string pf = "[";
            for (auto& m : x["database_mappings"].a) pf += (pf.size() > 1 ? "," : "") + std::string("{\"iid\":") + Json::quote(m["iid"].s) + ",\"prefix\":" + Json::quote(m["prefix"].s) + "}";
            caps.push_back({"capability|etr", "xtr:" + need(x, "xtr_id", "etr").s, "{\"capability\":\"etr\",\"xtr_id\":" + Json::quote(x["xtr_id"].s) + ",\"prefixes\":" + pf + "]" + common});
        }
        if (itr) {
            std::string iids = "[";
            for (auto& v : need(cfg, "itr", "an itr")["iids"].a) iids += (iids.size() > 1 ? "," : "") + Json::quote(v["iid"].s);
            caps.push_back({"capability|itr", "participant:" + Engine::participant(), "{\"capability\":\"itr\",\"iids\":" + iids + "]" + common});
        }
        for (auto& c : caps) e.write_cell(c.var, c.inst, c.body);      // once: the session holds them
        for (auto& c : caps) say("capability registered: " + c.var.substr(11) + " at " + c.inst);

        // 2. the signal thread: a signal ends the wait below
        std::atomic<bool> signalled{false};
        std::thread signals([&] { int sig = 0; sigwait(&sigs, &sig); signalled = true; g_stop = true; e.wake(); });

        // 3. the roles
        if (ms) {
            for (auto& s : cfg["sites"].a) {
                std::string r = front->call("site.add", dump(s));                  // hosted: the host's Engines hold the key
                say("site " + s["prefix"].s + " (iid " + s["iid"].s + "): " + r);
            }
            // [DDT_REFERRAL_V1] a map-server that is not also the map-resolver is a DDT authority for its
            // ms_authoritative_prefixes: DDT-originated Map-Requests also get a Map-Referral
            for (auto& ap : cfg["ms_authoritative_prefixes"].a) say("ms-authoritative-prefix " + ap.s + ": " + front->call("ms.authoritative_prefix", "{\"iid\":\"0\",\"prefix\":" + Json::quote(ap.s) + "}"));
            for (auto& pe : cfg["ms_peers"].a) say("map-server peer " + pe.s + ": " + front->call("ms.peer", "{\"address\":" + Json::quote(pe.s) + "}"));
            if (!mr) say("DDT authority: " + front->call("front.ddt_authority", "{\"enabled\":true}"));
            if (cfg["governor"].type == Json::Obj) {
                const Json& g = cfg["governor"];
                std::string r = e.call("ms_governor.start", Json::parse("{\"name\":" + Json::quote(name) + ",\"iid\":" + Json::quote(g["iid"].s) + ",\"group\":" + Json::quote(g["group"].s) + "}"));
                say("governor for iid " + g["iid"].s + ": " + r);
            }
        }
        if (etr) {
            const Json& x = need(cfg, "etr", "an etr");
            say("etr identity: " + e.call("etr.identity", Json::parse("{\"name\":" + Json::quote(name) + ",\"xtr_id\":" + Json::quote(need(x, "xtr_id", "etr").s) + "}")));
            for (auto& m : x["database_mappings"].a) say("database mapping " + m["prefix"].s + ": " + e.call("database_mapping.add", m));
            say("etr liveness: " + e.call("etr_liveness.start", Json::parse("{}")));   // gone with this session
        }
        if (itr)
            for (auto& v : need(cfg, "itr", "an itr")["iids"].a)
                say("resolver view iid " + v["iid"].s + ": " + e.call("resolver.wait_site", Json::parse("{\"iid\":" + Json::quote(v["iid"].s) + ",\"prefix\":\"0.0.0.0/0\",\"group\":" + Json::quote(v["group"].s) + ",\"active\":false}")));

        // 4. each role holds a read on the capabilities it uses, and handles each as it arrives or goes
        auto describe = [](const frogram::Cell& c) {
            std::string d = c.bag["udp"].type == Json::Str ? " at " + c.bag["udp"].s : std::string();
            for (auto& p : c.bag["prefixes"].a) d += " " + p["prefix"].s + " (iid " + p["iid"].s + ")";
            for (auto& p : c.bag["sites"].a) d += " site " + p["prefix"].s + " (iid " + p["iid"].s + ")";
            return d;
        };
        std::vector<std::thread> watches;
        auto use = [&](const std::string& what, const std::string& why) {
            watches.emplace_back([&, what, why] {
                const std::string var = "capability|" + what;
                try {
                    uint64_t after = 0;
                    auto cells = e.read_cells(var);                 // what is there now, then everything after it
                    for (;;) {
                        for (auto& c : cells) {
                            if (c.id > after) after = c.id;
                            if (c.gone) say(why + ": " + what + " " + c.instance + " gone");
                            else say(why + ": " + what + describe(c));
                        }
                        cells = e.wait_cells(var, after);
                    }
                } catch (const std::exception& x) { if (!g_stop) e.report("capability watch " + var + ": " + x.what()); }
            });
        };
        if (etr) use("map-server", "etr");
        if (itr) use("map-resolver", "itr");
        if (ms) use("etr", "map-server");
        // this service's own first tuple: when it goes, the session has
        watches.emplace_back([&] {
            const Cap own = caps.front();
            try {
                uint64_t after = 0;
                for (auto& c : e.read_cells(own.var)) if (c.instance == own.inst && c.id > after) after = c.id;
                for (;;) for (auto& c : e.wait_cells(own.var, after)) {
                    if (c.id > after) after = c.id;
                    if (c.instance == own.inst && c.gone) { e.report("this service's capability " + own.var.substr(11) + " at " + own.inst + " is gone: its session has ended"); return; }
                }
            } catch (const std::exception& x) { if (!g_stop) e.report("session watch: " + std::string(x.what())); }
        });
        say("running");
        // [WAIT_ON_THE_EVENT_WORD_V1] asleep until a signal or a failure moves the Engine's event word
        for (uint32_t seen = e.events.load(); !g_stop && e.failure().empty(); seen = e.events.load()) e.events.wait(seen);
        const std::string failed = e.failure();
        if (!failed.empty()) say("stopping: " + failed);
        g_stop = true;
        e.close();                                          // the parked reads return; the session's rows go
        for (auto& t : watches) t.join();
        if (!signalled) pthread_kill(signals.native_handle(), SIGTERM);   // a failure ended the wait: end the signal thread
        signals.join();
        if (front) front->stop();
        say("stopped");
        return failed.empty() ? 0 : 3;                      // a lost memory is a failure: the supervisor restarts it
    } catch (const std::exception& x) {
        std::cerr << "lisp-service: " << x.what() << "\n";
        return 1;
    }
}
