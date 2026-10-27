// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// fdf_where.cpp -- [SERVICE_WHERE_IS_A_TUPLE_V1] a member says where the services it runs are, in its pond's memory:
//     (pond, where, w:<this machine's guid>)   bag {"from":<name>, "<service>":{"<key>":"<value>",...}, ...}
// One cell per member, owned by that member (its ns, as fdf-client announces it), so the region removes it when the
// member's session ends -- a service on a machine that is gone is gone. Readers (the phone's Communicator, fdf-monitor)
// read (pond, where) and take the services they need; nobody types an address.
//   usage: fdf-where <service> key=value [key=value ...]   e.g. fdf-where comms ram=self:8800 media=self:8994
//          (self:<port> is written as w:<this machine's guid>:<port> -- an identity, resolved by each reader in its scope)
//          fdf-where <service> --clear                     this member no longer runs <service>
//          fdf-where                                       prints this member's cell
//   RINA_RAM = the pond's RAM manager (as fdf-client has it); FDF_FACTS = this machine's facts (name, guid)
// [ADDIN_OWNS_ITS_CELL_V1] an add-in writes its OWN cell, one owner per tuple: FDF_ADDIN=<name> makes the instance
// <ns>/<name> and the bag only that add-in's services (two add-ins never rewrite one cell). The region sweeps
// <ns>/* with <ns> when the machine's session ends. FDF_NS=<ns> names the member when there is no facts.json (a leaf:
// w:<its name>); FDF_STANDALONE=1: the machine's own control RAM, no member session (ns "local", nothing to be alive).
#include "frogram.hpp"
#include "fdf_version.hpp"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

static const char* API = "/FawcettInnovations.RINA.ram_interface.php";

static std::string dump(const frogram::Json& j) {          // a cell's bag back to JSON, as it was read
    switch (j.type) {
    case frogram::Json::Null: return "null";
    case frogram::Json::Bool: return j.b ? "true" : "false";
    case frogram::Json::Num: { std::ostringstream o; o.precision(17); o << j.n; return o.str(); }
    case frogram::Json::Str: return frogram::Json::quote(j.s);
    case frogram::Json::Arr: { std::string r = "["; for (size_t i = 0; i < j.a.size(); ++i) r += (i ? "," : "") + dump(j.a[i]); return r + "]"; }
    case frogram::Json::Obj: { std::string r = "{"; for (size_t i = 0; i < j.o.size(); ++i) r += (i ? "," : "") + frogram::Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return r + "}"; }
    }
    return "null";
}

int main(int argc, char** argv) {
    const char* e = getenv("RINA_RAM"); const std::string where = e ? e : ""; const size_t c = where.rfind(':');
    if (where.empty() || c == std::string::npos) {
        fprintf(stderr, "usage: RINA_RAM=<host:port> fdf-where [<service> key=value ... | <service> --clear]\n"); return 2; }
    const char* fe = getenv("FDF_FACTS"); std::ifstream in(fe && *fe ? fe : "/opt/fdf/var/facts.json");
    std::stringstream b; b << in.rdbuf(); std::string name, guid;
    try { frogram::Json f = frogram::Json::parse(b.str()); name = f["name"].s; guid = f["guid"].s; } catch (...) {}
    const char* xns = getenv("FDF_NS"); const char* xa = getenv("FDF_ADDIN"); const char* xs = getenv("FDF_STANDALONE");
    const bool standalone = xs && *xs && std::string(xs) != "0";
    if (standalone) guid = "local"; else if (xns && *xns) guid = std::string(xns).compare(0, 2, "w:") == 0 ? std::string(xns).substr(2) : xns;
    if (guid.empty()) { fprintf(stderr, "fdf-where: no guid in facts.json (and no FDF_NS): this machine is not a member (nothing written)\n"); return 1; }
    const std::string ns = standalone ? std::string("local") : "w:" + guid;
    const std::string addin = xa ? xa : "";
    const std::string inst = addin.empty() ? ns : ns + "/" + addin;
    try {
        frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), API); frogram::Memory m(s, API);
        // this member's cell as it stands: service -> its raw JSON object (kept verbatim; only the named one changes)
        std::map<std::string, std::string> svc; std::vector<uint64_t> old;
        for (auto& cell : m.read("pond", "where", inst)) {
            old.push_back(cell.id);
            for (auto& kv : cell.bag.o) if (kv.first != "from" && kv.second.type == frogram::Json::Obj) svc[kv.first] = dump(kv.second);
        }
        if (argc < 2) {
            printf("fdf-where: build %s; %s at %s:", FDF_BUILD_TAG, inst.c_str(), where.c_str());
            if (svc.empty()) printf(" no services\n"); else { printf("\n"); for (auto& kv : svc) printf("  %s %s\n", kv.first.c_str(), kv.second.c_str()); }
            return 0;
        }
        const std::string service = argv[1];
        const bool clearing = argc == 3 && std::string(argv[2]) == "--clear";
        // [WHERE_ONLY_WHILE_A_MEMBER_V1] the cell is this member's and goes with its session: written while that session
        // is down, it would outlive the member (nothing sweeps it until the member's NEXT session ends). So a service is
        // said only while this machine's ns is an alive participant; otherwise nothing is written and it says so.
        if (!clearing && !addin.empty() && service != addin) { fprintf(stderr, "fdf-where: FDF_ADDIN=%s writes only its own service, not %s (nothing written)\n", addin.c_str(), service.c_str()); return 2; }
        if (!clearing && !standalone) {
            frogram::Json r = s.call("POST", std::string(API) + "?op=rina", "{\"op\":\"participants\"}");
            bool alive = false; for (auto& p : r["participants"].a) if (p["ns"].s == ns && p["alive"].b) alive = true;
            if (!alive) { fprintf(stderr, "fdf-where: %s is not an alive member at %s (its fdf-client is not joined): nothing written\n", ns.c_str(), where.c_str()); return 3; }
        }
        if (clearing) svc.erase(service);
        else {
            std::string o = "{"; bool first = true;
            for (int i = 2; i < argc; ++i) {
                std::string a = argv[i]; size_t q = a.find('=');
                if (q == std::string::npos || q == 0) { fprintf(stderr, "fdf-where: '%s' is not key=value (nothing written)\n", a.c_str()); return 2; }
                // [IDENTITY_IN_TUPLES_V1] "self:<port>" is this member's endpoint by identity, w:<guid>:<port>: a reader
                // resolves the guid to the address it has in the reader's scope (the region's `resolve`), so the cell
                // means the same machine in every pond that reads it. An address written here means one machine only
                // where that address is unique.
                std::string v = a.substr(q + 1);
                if (v.compare(0, 5, "self:") == 0) v = ns + ":" + v.substr(5);
                o += (first ? "" : ",") + frogram::Json::quote(a.substr(0, q)) + ":" + frogram::Json::quote(v); first = false;
            }
            svc[service] = o + "}";
        }
        // write the new cell first, then remove the old one(s): a reader sees the old or the new cell, never none
        if (!svc.empty()) {
            std::string bag = "{\"from\":" + frogram::Json::quote(name.empty() ? ns : name);
            for (auto& kv : svc) bag += "," + frogram::Json::quote(kv.first) + ":" + kv.second;
            const uint64_t id = m.write("pond", "where", inst, bag + "}");
            for (uint64_t o : old) if (o != id) m.remove(o);
            printf("fdf-where: build %s; %s at %s: %s\n", FDF_BUILD_TAG, inst.c_str(), where.c_str(), (bag + "}").c_str());
        } else {
            for (uint64_t o : old) m.remove(o);
            printf("fdf-where: build %s; %s at %s: no services (cell removed)\n", FDF_BUILD_TAG, inst.c_str(), where.c_str());
        }
        return 0;
    } catch (const std::exception& x) { fprintf(stderr, "fdf-where: %s: %s\n", where.c_str(), x.what()); return 1; }
}
