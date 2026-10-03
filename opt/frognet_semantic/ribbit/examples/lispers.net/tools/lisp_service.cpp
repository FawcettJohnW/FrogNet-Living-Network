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
//   lisp / capability|map-server   / udp:<address>:<port>   {"capability","udp","sites":[{"iid","prefix"}],"beat",...}
//   lisp / capability|map-resolver / udp:<address>:<port>   {"capability","udp","beat",...}
//   lisp / capability|etr          / xtr:<xtr_id>           {"capability","xtr_id","prefixes":[{"iid","prefix"}],...}
//   lisp / capability|itr          / participant:<id>       {"capability","iids":[...],...}
// After the startup wait, each role reads -- one plain read each -- the capabilities it uses, and handles them: an
// ETR the map-servers, an ITR the map-resolvers, a map-server the ETRs' prefixes. The heartbeat rewrites this machine's
// capability tuples with the next beat.
// John 2026-09-26: "everyone installs the same vendor package; systems come up, register themselves by writing
// capability tuples, wait 5 seconds for other machines to come up, then query and handle them with a plain read (no
// blocking read). The service runs the heartbeat thread and the high-speed data socket."
//
// What lispers.net does instead (its source): RUN-LISP starts lisp-core; `lisp enable` starts one process per role
// (lisp-itr, lisp-etr, lisp-ms, lisp-mr, ...); peers are written by hand in lisp.config (lisp map-server {...},
// lisp map-resolver {...}); an ETR re-sends its whole Map-Register to every map-server every 60 s; lisp-core relays
// configuration to the role processes over IPC. Here: capability tuples instead of a hand-written peer list, a
// heartbeat and changes-only registration instead of the 60 s re-register, the shared memory instead of IPC, and the
// roles started in this one process from its configuration.
//
// The configuration (JSON):
//   name           this machine's name in the region (no '|')
//   ram            {"host": H, "port": P, "api": "/Vendor.Product.ram_interface.php"}  -- where the shared space is
//   roles          any of "map-server", "map-resolver", "etr", "itr"
//   udp            {"address": A, "port": N}           map-server / map-resolver: the LISP control-plane front
//   heartbeat_s    seconds between capability heartbeats
//   startup_wait_s seconds to wait for the other machines before reading theirs (John: 5)
//   sites          map-server: [{"iid","prefix","group","accept_more_specifics","key_id","password"}]
//   governor       map-server: {"iid","group"} -- governs the native registrations of Ribbit ETRs (name = `name`)
//   ms_authoritative_prefixes  map-server: ["198.18.0.0/15", ...] -- LISP-DDT authority (Map-Referrals when the
//                  machine is a map-server but not a map-resolver)
//   ms_peers       map-server: ["a.b.c.d", ...] -- its peers: the referral set in its Map-Referrals
//   etr            {"xtr_id", "database_mappings": [{"iid","prefix","group","rloc_set":[...]}],
//                   "liveness": {"interval_s","lifetime_s"}}
//   itr            {"iids": [{"iid","group"}]}         resolver views held from startup

#include "lisp_handler.hpp"
#include "version.hpp"
#include <climits>
#include <csignal>
#include <fcntl.h>
#include <sys/stat.h>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>

static std::atomic<bool> g_stop{false};
static void on_signal(int) { g_stop = true; }

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
        const double beat_s = need(cfg, "heartbeat_s", "the service").n, wait_s = need(cfg, "startup_wait_s", "the service").n;
        const bool ms = has_role(roles, "map-server"), mr = has_role(roles, "map-resolver"), etr = has_role(roles, "etr"), itr = has_role(roles, "itr");
        std::signal(SIGINT, on_signal); std::signal(SIGTERM, on_signal);
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
        const std::string common = ",\"participant\":" + Json::quote(Engine::participant()) + ",\"heartbeat_s\":" + dump(cfg["heartbeat_s"]) +
                                   ",\"version\":" + Json::quote(RIBBIT_LISP_VERSION);
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
        auto publish = [&](uint64_t beat) {
            for (auto& c : caps) e.write_cell(c.var, c.inst, c.body + ",\"beat\":" + std::to_string(beat) + "}");   // bodies are left open for the beat
        };
        publish(0);
        for (auto& c : caps) say("capability registered: " + c.var.substr(11) + " at " + c.inst);

        // 2. the heartbeat thread: this machine's capabilities, rewritten every heartbeat_s with the next beat
        std::atomic<uint64_t> beats{0};
        std::thread heartbeat([&] {
            uint64_t b = 0;
            while (!g_stop) {
                for (double t = 0; t < beat_s && !g_stop; t += 0.05) std::this_thread::sleep_for(std::chrono::milliseconds(50));
                if (g_stop) break;
                try { publish(++b); beats = b; }
                catch (const std::exception& x) { say(std::string("heartbeat: ") + x.what()); g_stop = true; }
            }
        });

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
            if (x["liveness"].type == Json::Obj) say("etr liveness: " + e.call("etr_liveness.start", x["liveness"]));
        }
        if (itr)
            for (auto& v : need(cfg, "itr", "an itr")["iids"].a)
                say("resolver view iid " + v["iid"].s + ": " + e.call("resolver.wait_site", Json::parse("{\"iid\":" + Json::quote(v["iid"].s) + ",\"prefix\":\"0.0.0.0/0\",\"group\":" + Json::quote(v["group"].s) + ",\"active\":false}")));

        // 4. wait for the other machines to come up, then read -- one plain read each -- the capabilities this
        // machine's roles use, and handle them
        for (double t = 0; t < wait_s && !g_stop; t += 0.05) std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto use = [&](const std::string& what, const std::string& why) {
            auto cells = e.read_cells("capability|" + what);
            say(why + ": " + std::to_string(cells.size()) + " " + what + " capabilit" + (cells.size() == 1 ? "y" : "ies"));
            for (auto& c : cells) {
                std::string d = c.bag["udp"].type == Json::Str ? " at " + c.bag["udp"].s : std::string();
                for (auto& p : c.bag["prefixes"].a) d += " " + p["prefix"].s + " (iid " + p["iid"].s + ")";
                for (auto& p : c.bag["sites"].a) d += " site " + p["prefix"].s + " (iid " + p["iid"].s + ")";
                say("  " + what + d);
            }
        };
        if (etr) use("map-server", "etr");
        if (itr) use("map-resolver", "itr");
        if (ms) use("etr", "map-server");
        say("running");
        while (!g_stop) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            std::string f = e.failure();
            if (!f.empty()) { say("stopping: " + f); g_stop = true; }
        }
        heartbeat.join();
        if (front) front->stop();
        say("stopped after " + std::to_string(beats.load()) + " heartbeats");
        return 0;
    } catch (const std::exception& x) {
        std::cerr << "lisp-service: " << x.what() << "\n";
        return 1;
    }
}
