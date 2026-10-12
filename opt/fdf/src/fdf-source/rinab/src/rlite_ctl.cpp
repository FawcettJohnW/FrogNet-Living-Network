// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// rlite_ctl.cpp -- Mode B's rlite-ctl. The command line, its lines and its exit codes are rlite's (the contract,
// rlite-ctl.c as cited); everything behind them is Mode B: each IPCP, registration, policy and parameter is one record
// in the machine's memory (rina_cells), written by its one owner. IPCPs belong to the network namespace they were
// created in, as rlite's kernel keeps them; a DIF's records are shared by everyone in the DIF.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#include <thread>
#ifdef RINA_CTL_IN_HOST
// Built into rina-ram ([CTL_AT_THE_MEMORY_V1]): the commands run where the records are. ctl_host.hpp supplies mem()
// (the host's memory), netns() (the caller's namespace, from the request), and output buffers.
#include "ctl_host.hpp"
#else
#include "rina_ram_cells.hpp"
#endif

namespace {
#ifndef RINA_CTL_IN_HOST
rina_ram::Cells& mem() { static rina_ram::Cells* c = new rina_ram::Cells; return *c; }   // the RAM manager (rina-ram)
#endif

#ifndef RINA_CTL_IN_HOST
std::string netns() {                                        // this process's network namespace, read once
    static std::string ns = [] { char b[128] = {0}; ssize_t n = readlink("/proc/self/ns/net", b, sizeof b - 1); return n > 0 ? std::string(b, size_t(n)) : std::string("net"); }();
    return ns;
}
#endif
// A record's bag: k=v pairs separated by ';' (values never contain ';' or '=').
using Bag = std::map<std::string, std::string>;
std::string enc(const Bag& b) { std::string s; for (auto& kv : b) { s += kv.first; s += '='; s += kv.second; s += ';'; } return s; }
Bag dec(const std::string& s) { Bag b; size_t i = 0; while (i < s.size()) { size_t e = s.find(';', i); if (e == std::string::npos) e = s.size(); std::string kv = s.substr(i, e - i); size_t q = kv.find('='); if (q != std::string::npos) b[kv.substr(0, q)] = kv.substr(q + 1); i = e + 1; } return b; }

// IPCPs of this namespace: record "ipcp|<netns>|<name>"; a destroyed one is written empty (gone).
std::string ipcp_key(const std::string& name) { return "ipcp|" + netns() + "|" + name; }
bool ipcp_get(const std::string& name, Bag& b) { std::string s; if (!mem().read(ipcp_key(name), s) || s.empty()) return false; b = dec(s); return true; }
void ipcp_put(const std::string& name, const Bag& b) { mem().write(ipcp_key(name), enc(b)); }
std::vector<std::pair<std::string, Bag>> ipcps() {
    std::vector<std::pair<std::string, Bag>> v; const std::string p = "ipcp|" + netns() + "|";
    for (auto& kv : mem().scan(p)) if (!kv.second.empty()) v.emplace_back(kv.first.substr(p.size()), dec(kv.second));
    std::sort(v.begin(), v.end(), [](auto& a, auto& b) { return atoi(a.second["id"].c_str()) < atoi(b.second["id"].c_str()); });
    return v;
}
bool ipcp_in_dif(const std::string& dif, std::string& name, Bag& b) {
    for (auto& kv : ipcps()) if (kv.second["dif"] == dif) { name = kv.first; b = kv.second; return true; }
    return false;
}
bool operational(Bag& b) { return b["type"] != "normal" || b["enroller"] == "1" || b["enrolled"] == "1"; }

// Registrations of this namespace: "reg|<netns>|<dif>|<appl>" = {lower IPCP's id}.
std::string reg_key(const std::string& dif, const std::string& appl) { return "reg|" + netns() + "|" + dif + "|" + appl; }

int fail(const char* m) { fprintf(stderr, "%s\n", m); return 1; }


// ---- groups 3 and 4: neighbours, enrollment, routes, shims, flows ------------------------------------------------
bool ipcp_get_ns(const std::string& ns, const std::string& name, Bag& b) { std::string s; if (!mem().read("ipcp|" + ns + "|" + name, s) || s.empty()) return false; b = dec(s); return true; }
// An IPCP registered in a lower DIF is a member of that DIF's neighbourhood, seen from every namespace:
// "nreg|<lower dif>|<ipcp>" = {ns}.
std::string nreg_key(const std::string& lower, const std::string& ipcp) { return "nreg|" + lower + "|" + ipcp; }
// Adjacencies within a DIF: "adj|<dif>|<a>|<b>" = {lower dif, a's ns, b's ns} (one record per pair, a < b).
std::string adj_key(const std::string& dif, std::string a, std::string b) { if (b < a) std::swap(a, b); return "adj|" + dif + "|" + a + "|" + b; }
bool is_operational_member(const std::string& ns, const std::string& name, const std::string& dif) {
    Bag b; if (!ipcp_get_ns(ns, name, b) || b["dif"] != dif) return false;
    return b["enroller"] == "1" || b["enrolled"] == "1";
}
// [FDF_POLICY_IS_THE_DIF_V1] A DIF's policies and parameters are the DIF's: one record each, "pol|<dif>|<component>"
// and "par|<dif>|<component>|<param>", read by every member. Enrolling copies nothing; the joiner reads the DIF's.
// [FDF_E2_DERIVED_ADDRESS_V1] An IPCP's address in its DIF is derived from its own name, not allocated:
// a_k = H(ipcp | dif | k) mod (2^W - 1) + 1, claimed by writing the IPCP's own cell "aclaim:<dif>" / <ipcp>. The owner
// of an address is the claim for it with the smallest write id (the memory's one counter). The decision is taken from
// a window read past 0 that contains this claim: that read stops below any position still being filled, so every
// older claim is in it. An IPCP that already owns an address keeps it. No allocator, no conflict check, no wait.
// RINA_ADDR_WIDTH in rina-ram's environment (default 32) narrows the space, for the uniqueness test only.
#ifdef RINA_CTL_IN_HOST
uint64_t fnv1a(const std::string& s) { uint64_t h = 1469598103934665603ull; for (unsigned char c : s) { h ^= c; h *= 1099511628211ull; } return h; }
// [FDF_ADDRESS_RELEASE_V1] A claim names the claimant's machine (ns) and that machine's epoch. It counts only while
// the machine's own record is fresh and in the same epoch; a dead or restarted machine's claims stop counting, so its
// addresses are free again -- with nobody writing into its cells.
std::string node_epoch(const std::string& ns) {
    std::string v; if (!mem().read("node|" + ns, v) || v.compare(0, 6, "epoch=") != 0) return "";   // [SESSION_IS_LIVENESS_V1] "" once its agent is gone
    return v.substr(6, v.find(';') - 6);
}
bool claim_counts(const frogram::Cell& c, std::map<std::string, std::string>& epochs) {
    const std::string ns = c.bag["ns"].s; if (ns.empty()) return false;
    auto it = epochs.find(ns); if (it == epochs.end()) it = epochs.emplace(ns, node_epoch(ns)).first;
    return !it->second.empty() && it->second == c.bag["ep"].s;
}
std::vector<frogram::Cell> claims(const std::string& dif, const std::string& me, uint64_t mine) {
    for (;;) {                                                       // a read that contains my claim
        auto all = ctl_host::tl_m->read("rina", "aclaim:" + dif, "", 0, 0);
        for (auto& c : all) if (c.instance == me && c.id == mine) return all;
        std::this_thread::yield();                                   // a lower position is still being filled
    }
}
bool owns(const std::vector<frogram::Cell>& all, const std::string& me, const std::string& a, uint64_t mine) {
    std::map<std::string, std::string> epochs;
    for (auto& c : all) if (c.instance != me && c.id < mine && c.bag["v"].s == a && claim_counts(c, epochs)) return false;
    return true;
}
uint64_t claim(const std::string& dif, const std::string& me, const std::string& a, const std::string& ep) {
    return ctl_host::tl_m->write("rina", "aclaim:" + dif, me, "{\"v\":" + frogram::Json::quote(a) + ",\"ns\":" + frogram::Json::quote(netns()) +
                                 ",\"ep\":" + frogram::Json::quote(ep) + "}");
}
// prev: the address this IPCP held, tried first so a returning machine keeps its addresses when nobody took them
std::string derived_address(const std::string& dif, const std::string& me, const std::string& prev = "") {
    static const int width = [] { const char* w = getenv("RINA_ADDR_WIDTH"); const int v = w ? atoi(w) : 32; return v >= 2 && v <= 48 ? v : 32; }();
    const uint64_t space = (1ull << width) - 1;
    const std::string ep = node_epoch(netns());
    if (ep.empty()) throw std::runtime_error("this machine's node agent (rlite-uipcps) has no fresh record: no address can be claimed");
    for (auto& c : ctl_host::tl_m->read("rina", "aclaim:" + dif, me, -1, 0))   // a claim in this epoch that still owns: keep it
        if (c.instance == me && !c.bag["v"].s.empty() && c.bag["ep"].s == ep && owns(claims(dif, me, c.id), me, c.bag["v"].s, c.id)) return c.bag["v"].s;
    if (!prev.empty() && prev != "0") { const uint64_t mine = claim(dif, me, prev, ep); if (owns(claims(dif, me, mine), me, prev, mine)) return prev; }
    const int tries = int(std::min<uint64_t>(4096, 4 * space + 16));      // a full space is refused without thousands of claims
    for (int k = 0; k < tries; ++k) {
        const std::string a = std::to_string(fnv1a(me + "|" + dif + "|" + std::to_string(k)) % space + 1);
        const uint64_t mine = claim(dif, me, a, ep);
        if (owns(claims(dif, me, mine), me, a, mine)) return a;
    }
    throw std::runtime_error("no address in DIF " + dif + " for " + me + " after " + std::to_string(tries) + " candidates");
}
// node-rejoin: this machine starts a new epoch; each of its IPCPs with an address re-claims it (or takes another)
int node_rejoin() {
    for (auto& kv : ipcps()) {
        Bag b = kv.second; if (b["type"] != "normal" || b["dif"].empty()) continue;
        const bool member = b["enroller"] == "1" || b["enrolled"] == "1";
        if (!member && (b["address"].empty() || b["address"] == "0")) continue;   // never took an address: nothing to rejoin
        std::string a;
        try { a = derived_address(b["dif"], kv.first, b["address"]); }
        catch (const std::exception& e) { a = "0"; fprintf(stderr, "%s: no address after rejoin (%s)\n", kv.first.c_str(), e.what()); }
        if (a != b["address"]) { b["address"] = a; ipcp_put(kv.first, b); }   // "0": it holds none -- the truth, not a guess
    }
    return 0;
}
#endif
int ipcp_enroll(const std::vector<std::string>& r) {        // ipcp-enroll IPCP DIF LOWER_DIF [NEIGHBOR]
    if (r.size() < 3) return fail("Not enough arguments");
    const std::string& me = r[0]; const std::string& dif = r[1]; const std::string& lower = r[2];
    Bag b; if (!ipcp_get(me, b)) return fail("Could not find the IPC process");
    if (b["dif"] != dif) return fail("IPCP is not in that DIF");
    std::string ln; Bag lb; if (!ipcp_in_dif(lower, ln, lb)) return fail("No IPCP in the lower DIF");
    std::string neigh, nns;
    auto usable = [&](const std::string& name, const std::string& ns) { return name != me && is_operational_member(ns, name, dif); };
    if (r.size() > 3) {
        std::string v; if (!mem().read(nreg_key(lower, r[3]), v) || v.empty()) return fail("Neighbor not registered in the lower DIF");
        nns = dec(v)["ns"]; if (!usable(r[3], nns)) return fail("Neighbor is not an enroller of the DIF");
        neigh = r[3];
    } else {                                                  // broadcast enrollment: any member reachable in the lower DIF
        for (auto& kv : mem().scan("nreg|" + lower + "|")) {
            if (kv.second.empty()) continue;
            const std::string n = kv.first.substr(5 + lower.size() + 1), ns = dec(kv.second)["ns"];
            if (usable(n, ns)) { neigh = n; nns = ns; break; }
        }
        if (neigh.empty()) return fail("No enroller found in the lower DIF");
    }
    b["enrolled"] = "1"; b["neighbor"] = neigh;
    if (b["address"].empty() || b["address"] == "0") b["address"] = derived_address(dif, me);
    ipcp_put(me, b);
    mem().write(adj_key(dif, me, neigh), "lower=" + lower + ";" + (me < neigh ? "ans=" + netns() + ";bns=" + nns : "ans=" + nns + ";bns=" + netns()) + ";");
    return 0;
}
int lower_flow_alloc(const std::vector<std::string>& r) {   // ipcp-lower-flow-alloc IPCP DIF LOWER_DIF NEIGHBOR
    if (r.size() < 4) return fail("Not enough arguments");
    Bag b; if (!ipcp_get(r[0], b) || b["dif"] != r[1]) return fail("Could not find the IPC process in that DIF");
    std::string v; if (!mem().read(nreg_key(r[2], r[3]), v) || v.empty()) return fail("Neighbor not registered in the lower DIF");
    const std::string nns = dec(v)["ns"];
    mem().write(adj_key(r[1], r[0], r[3]), "lower=" + r[2] + ";" + (r[0] < r[3] ? "ans=" + netns() + ";bns=" + nns : "ans=" + nns + ";bns=" + netns()) + ";");
    return 0;
}
int neigh_disconnect(const std::vector<std::string>& r) {   // ipcp-neigh-disconnect IPCP NEIGHBOR
    if (r.size() < 2) return fail("Not enough arguments");
    Bag b; if (!ipcp_get(r[0], b)) return fail("Could not find the IPC process");
    std::string v; const std::string k = adj_key(b["dif"], r[0], r[1]);
    if (!mem().read(k, v) || v.empty()) return fail("Not a neighbor");
    mem().write(k, ""); b["enrolled"] = "0"; b["neighbor"] = ""; ipcp_put(r[0], b);
    return 0;
}
// Static routes: "route|<ns>|<ipcp>|<dest>" = next hops (StaticRouting::route_mod, uipcp-normal-lower-flows.cpp 722-760).
int route_mod(const std::vector<std::string>& r, bool add) {
    if (r.size() < (add ? 3u : 2u)) return fail("Not enough arguments");
    Bag b; if (!ipcp_get(r[0], b)) return fail("Could not find IPC process");
    if (r[1].empty()) return fail("No destination IPCP specified");
    const std::string k = "route|" + netns() + "|" + r[0] + "|" + r[1];
    if (add) {
        if (r[2].empty()) return fail("No next hop specified");
        std::vector<std::string> nh; size_t i = 0; while (i <= r[2].size()) { size_t e = r[2].find(',', i); if (e == std::string::npos) e = r[2].size(); nh.push_back(r[2].substr(i, e - i)); i = e + 1; }
        std::vector<std::string> u = nh; std::sort(u.begin(), u.end()); if (std::unique(u.begin(), u.end()) != u.end()) return fail("Next hops list contains duplicates");
        mem().write(k, r[2]); return 0;
    }
    std::string v; if (!mem().read(k, v) || v.empty()) return fail("No route to destination");
    mem().write(k, ""); return 0;
}
// Flows: "flow|<dif>|<id>" written by librina when a flow is up (src/dst application and IPCP, their programs).
// [SESSION_IS_LIVENESS_V1] a flow is live while a program at either end has its session up (proc|<id>; the
// region clears it the moment that session ends). No pid, no heartbeat.
bool proc_live(const std::string& id) { std::string v; return !id.empty() && mem().read("proc|" + id, v) && !v.empty(); }   // [SESSION_IS_LIVENESS_V1]
bool flow_live(Bag& f) { return proc_live(f["cproc"]) || proc_live(f["sproc"]); }
bool ipcp_has_flows(const std::string& name) {
    for (auto& kv : mem().scan("flow|")) { if (kv.second.empty()) continue; Bag f = dec(kv.second);
        if (((f["sip"] == name && f["cns"] == netns()) || (f["dip"] == name && f["sns"] == netns())) && flow_live(f)) return true; }
    return false;
}
// The IPCP rlite picks when none is named (ipcp_select_by_dif, kernel/ctrl-dev.c 496-525): a normal IPCP over a
// shim, and among normal IPCPs the one stacked highest (rlite: the largest txhdroom).
int depth(const std::string& name, int guard = 0) {
    if (guard > 16) return 0;
    Bag b;
    if (!ipcp_get(name, b)) return 0;
    const int d = b["type"] == "normal" ? 1 : 0;
    int best = 0;
    for (auto& kv : mem().scan("reg|" + netns() + "|")) { if (kv.second.empty()) continue; Bag r = dec(kv.second); if (r["appl"] != name) continue;
        const std::string rest = kv.first.substr(("reg|" + netns() + "|").size()), dif = rest.substr(0, rest.find('|'));
        std::string ln; Bag lb; if (ipcp_in_dif(dif, ln, lb)) best = std::max(best, depth(ln, guard + 1)); }
    return d + best;
}
bool default_ipcp(std::string& name, Bag& b) {
    bool found = false; int bd = -1; bool bn = false;
    for (auto& kv : ipcps()) { const bool n = kv.second["type"] == "normal"; const int d = depth(kv.first);
        if (!found || (n && (!bn || d > bd))) { found = true; name = kv.first; b = kv.second; bn = n; bd = d; } }
    return found;
}

int ipcps_show() {                                            // rlite-ctl.c 814-835
    printf("IPC Processes table:\n");
    for (auto& kv : ipcps()) {
        Bag& b = kv.second; const std::string addr = (b["address"].empty() || b["address"] == "0") ? "*" : b["address"];
        printf("    id=%s, name='%s', dif_type='%s', dif_name='%s', address=%s, txhdroom=%s, rxhdroom=%s, troom=0, mss=%s\n",
               b["id"].c_str(), kv.first.c_str(), b["type"].c_str(), b["dif"].c_str(), addr.c_str(),
               b["txhdroom"].c_str(), b["rxhdroom"].c_str(), b["mss"].c_str());
    }
    return 0;
}
int ipcp_create(const std::string& name, const std::string& type, const std::string& dif) {
    static const char* types[] = {"normal", "shim-loopback", "shim-eth", "shim-udp4", "shim-tcp4", "shim-wifi", nullptr};
    bool ok = false; for (int i = 0; types[i]; i++) ok |= type == types[i];
    if (!ok) return fail("Unknown IPCP type");
    Bag x; if (ipcp_get(name, x)) return fail("An IPCP with this name already exists");
    int id = 0; for (auto& kv : ipcps()) id = std::max(id, atoi(kv.second["id"].c_str()) + 1);
    Bag b{{"id", std::to_string(id)}, {"type", type}, {"dif", dif}, {"txhdroom", "0"}, {"rxhdroom", "0"}, {"mss", "0"},
          {"flow-del-wait-ms", "0"}};
    if (type == "normal") { b["address"] = "0"; b["ttl"] = "64"; b["csum"] = "none"; b["sched"] = "none"; }
    if (type == "shim-loopback") { b["queued"] = "0"; b["drop-fract"] = "0"; }
    ipcp_put(name, b);
    printf("IPCP of type '%s' created, assigned id %d\n", type.c_str(), id);
    return 0;
}
int ipcp_destroy(const std::string& name) {
    Bag b; if (!ipcp_get(name, b)) return fail("No such IPCP process");
    for (auto& kv : mem().scan("reg|" + netns() + "|" + b["dif"] + "|")) if (!kv.second.empty()) mem().write(kv.first, "");
#ifdef RINA_CTL_IN_HOST
    if (!b["dif"].empty()) ctl_host::tl_m->write("rina", "aclaim:" + b["dif"], name, "{\"v\":\"\"}");   // its address is free
#endif
    bool others = false;                                     // the DIF's policy goes with its last member
    for (auto& kv : mem().scan("ipcp|")) if (!kv.second.empty() && kv.first != "ipcp|" + netns() + "|" + name && dec(kv.second)["dif"] == b["dif"]) { others = true; break; }
    if (!others) for (const char* pre : {"pol|", "par|"}) for (auto& kv : mem().scan(std::string(pre) + b["dif"] + "|")) if (!kv.second.empty()) mem().write(kv.first, "");
    for (auto& kv : mem().scan("nreg|")) if (!kv.second.empty() && dec(kv.second)["ns"] == netns() && kv.first.size() > name.size() && kv.first.compare(kv.first.size() - name.size() - 1, std::string::npos, "|" + name) == 0) mem().write(kv.first, "");
    for (auto& kv : mem().scan("adj|" + b["dif"] + "|")) { if (kv.second.empty()) continue; const std::string pair = kv.first.substr(5 + b["dif"].size());
        if (pair == name + "|" + pair.substr(name.size() + 1) || (pair.size() > name.size() && pair.compare(pair.size() - name.size() - 1, std::string::npos, "|" + name) == 0)) mem().write(kv.first, ""); }
    for (auto& kv : mem().scan("route|" + netns() + "|" + name + "|")) if (!kv.second.empty()) mem().write(kv.first, "");
    mem().write(ipcp_key(name), "");
    printf("IPCP %s destroyed\n", b["id"].c_str());
    return 0;
}
int reset() {
    for (auto& kv : ipcps()) ipcp_destroy(kv.first);
    for (auto& kv : mem().scan("app|")) if (!kv.second.empty() && dec(kv.second)["ns"] == netns()) mem().write(kv.first, "");
    return 0;
}

bool u_ok(const std::string& v, unsigned long long max) { if (v.empty()) return false; char* e; unsigned long long x = strtoull(v.c_str(), &e, 10); return *e == 0 && x <= max; }
int ipcp_config(const std::string& name, const std::string& p, const std::string& v) {
    Bag b; if (!ipcp_get(name, b)) return fail("Could not find a suitable IPC process");
    const std::string t = b["type"]; bool ok = false;
    if (p == "txhdroom" || p == "rxhdroom") ok = u_ok(v, 65535);
    else if (p == "mss" || p == "flow-del-wait-ms") ok = u_ok(v, 4294967295ull);
    else if (t == "normal" && p == "address") ok = u_ok(v, ~0ull);
    else if (t == "normal" && p == "ttl") ok = u_ok(v, 65535);
    else if (t == "normal" && p == "csum") ok = v == "inet" || v == "none" || v.empty();
    else if (t == "normal" && p == "sched") {                // rl_sched_replace, kernel/normal.c 1134-1170
        if (v != "none" && v != "pfifo" && v != "wrr") return fail("Failed to configure IPCP");
        if (b["sched"] == v) return 0;
        if (ipcp_has_flows(name)) return fail("IPCP has flows: scheduler busy");
        ok = true;
    } else if (t == "shim-eth" && p == "netdev") {
#ifndef RINA_CTL_IN_HOST
        struct stat st; if (v.empty() || stat(("/sys/class/net/" + v).c_str(), &st) != 0) return fail("No such network device");
#else
        if (v.empty()) return fail("No such network device");   // the caller checked the device in its own namespace
#endif
        for (auto& kv : ipcps()) if (kv.first != name && kv.second["type"] == "shim-eth" && kv.second["netdev"] == v) return fail("Network device already in use");
        ok = true;
    }
    else if ((t == "shim-udp4" || t == "shim-tcp4" || t == "shim-wifi") && !v.empty()) ok = true;   // their uipcp's own params
    else if (t == "shim-loopback" && (p == "queued" || p == "drop-fract")) ok = u_ok(v, 4294967295ull);
    if (!ok) return fail("Failed to configure IPCP");
    b[p] = (p == "csum" && v.empty()) ? "none" : v; ipcp_put(name, b);
#ifdef RINA_CTL_IN_HOST
    // [FDF_E2_DERIVED_ADDRESS_V1] a configured address is a claim like any other, so a derived one never takes it
    if (t == "normal" && p == "address" && !b["dif"].empty()) claim(b["dif"], name, v, node_epoch(netns()));
#endif
    return 0;
}
int ipcp_config_get(const std::string& name, const std::string& p) {           // rlite-ctl.c 491-514
    Bag b; if (!ipcp_get(name, b)) return fail("No such IPCP process");
    auto it = b.find(p);
    static const char* hidden[] = {"id", "type", "dif", "enroller", "enrolled", nullptr};
    for (int i = 0; hidden[i]; i++) if (p == hidden[i]) it = b.end();
    if (it == b.end()) { fprintf(stderr, "Failed to get config parameter '%s'\n", p.c_str()); return 1; }
    printf("%s\n", it->second.c_str());
    return 0;
}
int ipcp_enroller(const std::string& name, bool on) {
    Bag b; if (!ipcp_get(name, b)) return fail("Could not find the IPC process");
    if (b["type"] != "normal") return fail("IPCP is not a normal IPCP");
    b["enroller"] = on ? "1" : "0"; if (on && (b["address"].empty() || b["address"] == "0")) b["address"] = derived_address(b["dif"], name);
    ipcp_put(name, b);
    return 0;
}
int ipcp_register(const std::string& appl, const std::string& dif, bool reg) {   // rlite-ctl.c 702-730
    Bag a; if (!ipcp_get(appl, a)) return fail("Could not find the IPC process to register");
    std::string lower; Bag lb; if (!ipcp_in_dif(dif, lower, lb)) return fail("No IPCP in that DIF");
    std::string cur; const bool have = mem().read(reg_key(dif, appl), cur) && !cur.empty();
    if (reg) { mem().write(reg_key(dif, appl), "ipcp=" + lb["id"] + ";appl=" + appl + ";"); mem().write(nreg_key(dif, appl), "ns=" + netns() + ";"); return 0; }
    if (!have) return fail("Not registered");
    mem().write(reg_key(dif, appl), ""); mem().write(nreg_key(dif, appl), ""); return 0;
}
int regs_show(const std::vector<std::string>& a) {                                  // rlite-ctl.c 1031-1060
    std::string only;
    if (!a.empty()) { std::string n; Bag b; if (!ipcp_in_dif(a[0], n, b)) return fail("Could not find IPC process in DIF"); only = a[0]; }
    printf("Locally registered applications:\n");
    for (auto& kv : mem().scan("reg|" + netns() + "|")) {
        if (kv.second.empty()) continue;
        Bag r = dec(kv.second); const std::string rest = kv.first.substr(("reg|" + netns() + "|").size());
        const std::string dif = rest.substr(0, rest.find('|'));
        if (!only.empty() && dif != only) continue;
        Bag a2; if (ipcp_get(r["appl"], a2) && !operational(a2)) continue;     // pending: not yet in the table
        printf("  ipcp %s, name %s\n", r["ipcp"].c_str(), r["appl"].c_str());
    }
    return 0;
}

// ---- policies and parameters: rlite's tables (user/uipcps, upstream 6ac276b) ------------------------------------
// addra_lib_init (uipcp-normal-addr-alloc.cpp 734-763), dft_lib_init (uipcp-normal-appl-reg.cpp 736-757), enrollment /
// resalloc (uipcp-normal-enroll.cpp 1992-1993), fa_lib_init (uipcp-normal-flow-alloc.cpp 725-743), routing_lib_init
// (uipcp-normal-lower-flows.cpp 762-790); component parameters and default policies (uipcp-normal.cpp 486-514);
// defaults from the k* constants; raft defaults include/rlite/raft.hpp 447-448.
struct Param { char type; std::string dflt; };           // 'd' duration (ms), 'b' bool, 'i' int, 's' string
using Params = std::vector<std::pair<std::string, Param>>;
struct Policy { std::string name; Params params; };
const Params& ceft() { static Params p{{"replicas", {'s', ""}}, {"cli-timeout", {'d', "5000"}}, {"raft-election-timeout", {'d', "1000"}},
                                        {"raft-heartbeat-timeout", {'d', "100"}}, {"raft-rtx-timeout", {'d', "2000"}}}; return p; }
const std::map<std::string, std::vector<Policy>>& available() {
    static std::map<std::string, std::vector<Policy>> a{
        {"addralloc", {{"static", {}}, {"distributed", {{"nack-wait", {'d', "4000"}}}}, {"centralized-fault-tolerant", ceft()}}},
        {"dft", {{"fully-replicated", {}}, {"centralized-fault-tolerant", ceft()}}},
        {"enrollment", {{"default", {}}}},
        {"resalloc", {{"default", {}}}},
        {"flowalloc", {{"local", {{"force-flow-control", {'b', "false"}}, {"max-cwq-len", {'i', "128"}}, {"initial-credit", {'i', "512"}},
                                  {"initial-a", {'d', "20"}}, {"initial-rtx-timeout", {'d', "1000"}}, {"max-rtxq-len", {'i', "512"}}}}}},
        {"routing", {{"link-state", {{"age-incr-intval", {'d', "10000"}}, {"age-max", {'d', "900000"}}}},
                     {"link-state-lfa", {{"age-incr-intval", {'d', "10000"}}, {"age-max", {'d', "900000"}}}}, {"static", {}}}}};
    return a;
}
const std::map<std::string, Params>& component_params() {
    static std::map<std::string, Params> c{
        {"enrollment", {{"timeout", {'d', "7000"}}, {"keepalive", {'d', "20000"}}, {"keepalive-thresh", {'i', "3"}}, {"auto-reconnect", {'b', "true"}}}},
        {"resalloc", {{"reliable-flows", {'b', "false"}}, {"reliable-n-flows", {'b', "false"}}, {"broadcast-enroller", {'b', "true"}}}},
        {"ribd", {{"refresh-intval", {'d', "30000"}}}}};
    return c;
}
const char* default_policy(const std::string& c) {
    if (c == "addralloc") return "distributed";
    if (c == "dft") return "fully-replicated";
    if (c == "routing") return "link-state";
    if (c == "flowalloc") return "local";
    return "default";
}
// A DIF's policy state lives with this namespace's IPCP of that DIF (as rlite's lives in its uipcp): records
// "pol|<netns>|<dif>|<component>" = policy name, "par|<netns>|<dif>|<component>|<param>" = type,value.
std::string pol_key(const std::string& dif, const std::string& c) { return "pol|" + dif + "|" + c; }
std::string par_key(const std::string& dif, const std::string& c, const std::string& p) { return "par|" + dif + "|" + c + "|" + p; }
std::string policy_of(const std::string& dif, const std::string& c) { std::string v; return mem().read(pol_key(dif, c), v) && !v.empty() ? v : default_policy(c); }
const Policy* find_policy(const std::string& c, const std::string& name) {
    auto it = available().find(c); if (it == available().end()) return nullptr;
    for (auto& p : it->second) if (p.name == name) return &p;
    return nullptr;
}
// The parameters a component has now: its own, plus its current policy's. Value: the record if set, else the default.
Params current_params(const std::string& dif, const std::string& c) {
    Params out; auto cp = component_params().find(c); if (cp != component_params().end()) out = cp->second;
    if (const Policy* p = find_policy(c, policy_of(dif, c))) out.insert(out.end(), p->params.begin(), p->params.end());
    std::sort(out.begin(), out.end(), [](auto& a, auto& b) { return a.first < b.first; });   // params_map: an ordered map
    return out;
}
std::string value_of(const std::string& dif, const std::string& c, const std::string& name, const Param& p) {
    std::string v; return mem().read(par_key(dif, c, name), v) && !v.empty() ? v : p.dflt;
}
std::string shown(const Param& p, const std::string& v) { return p.type == 'd' ? v + "ms" : v; }   // operator<< 2148-2170
bool known_component(const std::string& c) { return available().count(c) || component_params().count(c); }

int need_dif(const std::string& dif) { std::string n; Bag b; if (!ipcp_in_dif(dif, n, b)) { fprintf(stderr, "Could not find IPC process in DIF %s\n", dif.c_str()); return 1; } return 0; }
int policy_mod(const std::string& dif, const std::string& c, const std::string& name) {      // policy_mod
    if (need_dif(dif)) return 1;
    if (!available().count(c)) return fail("Unknown component");
    const Policy* p = find_policy(c, name); if (!p) return fail("Unknown policy");
    if (policy_of(dif, c) == name) return 0;
    if (const Policy* old = find_policy(c, policy_of(dif, c))) for (auto& pp : old->params) mem().write(par_key(dif, c, pp.first), "");
    mem().write(pol_key(dif, c), name);                      // the new policy's parameters start at their defaults
    return 0;
}
int policy_list(const std::string& dif, const std::vector<std::string>& r) {                 // policy_list
    if (need_dif(dif)) return 1;
    auto one = [&](const std::string& c) { std::string line; auto& v = available().at(c);
        for (size_t i = 0; i < v.size(); i++) { line += (v[i].name == policy_of(dif, c)) ? "[" + v[i].name + "]" : v[i].name; if (i + 1 < v.size()) line += " "; }
        return line; };
    if (r.size() > 1) { if (!available().count(r[1])) { fprintf(stderr, "Unknown component %s\n", r[1].c_str()); return 1; } printf("%s\n", one(r[1]).c_str()); return 0; }
    size_t k = 0; for (auto& kv : available()) { printf("/mgmt/%s\n\t%s\n", kv.first.c_str(), one(kv.first).c_str()); ++k; }
    return 0;
}
bool valid(const Param& p, const std::string& v, std::string& stored) {                  // PolicyParam::set_value
    if (p.type == 'b') { if (v != "true" && v != "false") return false; stored = v; return true; }
    if (p.type == 's') { stored = v; return true; }
    if (p.type == 'i') { char* e; long x = strtol(v.c_str(), &e, 10); if (v.empty() || *e) return false; stored = std::to_string(x); return true; }
    size_t i = 0; while (i < v.size() && isdigit((unsigned char)v[i])) ++i;            // "<unsigned><s|ms>"
    if (i == 0) return false;
    const std::string unit = v.substr(i);
    unsigned long n = strtoul(v.substr(0, i).c_str(), nullptr, 10);
    if (unit == "s") n *= 1000;
    else if (unit != "ms") return false;
    stored = std::to_string(n); return true;
}
int param_mod(const std::string& dif, const std::string& c, const std::string& name, const std::string& v) {
    if (need_dif(dif)) return 1;
    if (!known_component(c)) return fail("Unknown component");
    for (auto& kv : current_params(dif, c)) if (kv.first == name) {
        std::string stored; if (!valid(kv.second, v, stored)) return fail("Invalid parameter value");
        mem().write(par_key(dif, c, name), stored); return 0;
    }
    return fail("Unknown parameter");
}
int param_list(const std::string& dif, const std::vector<std::string>& r) {                // policy_param_list
    if (need_dif(dif)) return 1;
    auto line = [&](const std::string& c, const std::string& n, const Param& p) { printf("/mgmt/%s.%s = '%s'\n", c.c_str(), n.c_str(), shown(p, value_of(dif, c, n, p)).c_str()); };
    if (r.size() > 2) {
        if (!known_component(r[1])) { fprintf(stderr, "Unknown component %s\n", r[1].c_str()); return 1; }
        for (auto& kv : current_params(dif, r[1])) if (kv.first == r[2]) { line(r[1], kv.first, kv.second); return 0; }
        fprintf(stderr, "Unknown parameter %s\n", r[2].c_str()); return 1;
    }
    if (r.size() > 1) {
        if (!known_component(r[1])) { fprintf(stderr, "Unknown component %s\n", r[1].c_str()); return 1; }
        for (auto& kv : current_params(dif, r[1])) line(r[1], kv.first, kv.second);
        return 0;
    }
    std::vector<std::string> comps; for (auto& kv : available()) comps.push_back(kv.first); for (auto& kv : component_params()) comps.push_back(kv.first);
    std::sort(comps.begin(), comps.end()); comps.erase(std::unique(comps.begin(), comps.end()), comps.end());
    for (auto& c : comps) for (auto& kv : current_params(dif, c)) line(c, kv.first, kv.second);
    return 0;
}
int dif_show(std::string dif, const char* what) {
    std::string n; Bag b;
    if (dif.empty()) { if (!default_ipcp(n, b)) return fail("No IPC process"); dif = b["dif"]; }
    if (!ipcp_in_dif(dif, n, b)) return fail("No such DIF");
    if (std::string(what) == "Routing table") {             // RoutingEngine::dump: this namespace's routes in the DIF
        printf("Routing table for IPCP %s:\n", n.c_str());
        for (auto& kv : mem().scan("route|" + netns() + "|" + n + "|")) if (!kv.second.empty()) printf("    %s --> %s\n", kv.first.substr(("route|" + netns() + "|" + n + "|").size()).c_str(), kv.second.c_str());
        for (auto& kv : mem().scan("adj|" + dif + "|")) if (!kv.second.empty()) printf("    adjacency %s\n", kv.first.substr(5 + dif.size()).c_str());
        return 0;
    }
    if (std::string(what) == "RIB") {                       // FullyReplicatedDFT::dump, uipcp-normal-appl-reg.cpp 274-285
        printf("Directory Forwarding Table:\n");
        unsigned seq = 1;
        for (auto& kv : mem().scan("app|" + dif + "|")) {
            if (kv.second.empty()) continue;
            {   // [LAZY_CLEANUP_V1] a registration nobody holds any more is not shown (and is erased by the read that finds it)
                bool held = false; for (auto& h : mem().scan("ibh|" + dec(kv.second)["inbox"] + "|")) if (!h.second.empty()) { held = true; break; }
                if (!held) { mem().write(kv.first, ""); continue; } }
            Bag r = dec(kv.second);
            printf("    Application: %s, Remote node: %s, Seqnum: %u\n", kv.first.substr(4 + dif.size() + 1).c_str(), r["ipcp"].c_str(), seq++);
        }
        printf("\n");
        printf("Supported flows (Src/Dst <Appl,IPCP,port>):\n");   // LocalFlowAllocator::dump, uipcp-normal-flow-alloc.cpp 692-712
        for (auto& kv : mem().scan("flow|" + dif + "|")) {
            if (kv.second.empty()) continue;
            Bag f = dec(kv.second);
            if (!flow_live(f)) continue;
            printf("    [%s], Src=<%s,%s,%s> Dst=<%s,%s,%s> Connections: []\n", f["cns"] == netns() ? "L" : "R", f["src"].c_str(), f["sip"].c_str(), f["cpid"].c_str(),
                   f["dst"].c_str(), f["dip"].c_str(), f["spid"].c_str());
        }
        printf("\n");
        return 0;
    }
    printf("%s of DIF '%s':\n", what, dif.c_str());
    return 0;
}
int flows_show() { printf("Flows table:\n"); return 0; }
int ipcp_stats(const std::vector<std::string>& r) {
    if (!r.empty()) { Bag b; if (!ipcp_get(r[0], b)) return fail("Could not find IPCP"); }
    printf("IPCP stats:\n"); return 0;
}

int sched_config(const std::vector<std::string>& r) {
    if (r.size() < 4) return fail("Not enough arguments");
    Bag b; if (!ipcp_get(r[0], b)) return fail("Could not find a suitable IPC process");
    if (r[2] != "qsize") return fail("Missing 'qsize' argument");
    const long qsize = atol(r[3].c_str()); if (qsize <= 0 || qsize > (1 << 24)) return fail("Invalid qsize");
    const std::string& sched = r[1];
    if (sched == "wrr") {
        if (r.size() < 8) return fail("Not enough arguments for wrr");
        if (r[4] != "quantum") return fail("Missing 'quantum' argument");
        const long q = atol(r[5].c_str()); if (q <= 0 || q > 1000000) return fail("Invalid quantum");
        if (r[6] != "weights") return fail("Missing 'weights' argument");
        int n = 0; std::string w = r[7]; for (char& c : w) if (c == ',') c = ' ';
        char* sp = nullptr; for (char* t = strtok_r(&w[0], " ", &sp); t; t = strtok_r(nullptr, " ", &sp)) { const int x = atoi(t); if (x <= 0 || x >= 1000) return fail("Invalid weight"); ++n; }
        if (n <= 0) return fail("No valid weights");
    } else if (sched == "pfifo") {
        if (r.size() < 6) return fail("Not enough arguments for pfifo");
        if (r[4] != "levels") return fail("Missing 'levels' argument");
        const long l = atol(r[5].c_str()); if (l <= 0 || l > 128) return fail("Invalid number of levels");
    } else return fail("Unknown scheduler");
    if (b["sched"] != sched) return fail("The IPCP does not use that scheduler");
    return 0;
}
}  // namespace

#ifdef RINA_CTL_IN_HOST
int ctl_main(int argc, char** argv) {
#else
int main(int argc, char** argv) {
#endif
    std::vector<std::string> a(argv + 1, argv + argc);
    const std::string cmd = a.empty() ? "ipcps-show" : a[0];
    std::vector<std::string> r(a.empty() ? a.begin() : a.begin() + 1, a.end());
#ifdef RINA_CTL_IN_HOST
    auto need = [&](size_t n) { if (r.size() < n) { fprintf(stderr, "Not enough arguments\n"); throw CtlExit{1}; } };
#else
    auto need = [&](size_t n) { if (r.size() < n) { fprintf(stderr, "Not enough arguments\n"); exit(1); } };
#endif
    if (cmd == "ipcps-show") return ipcps_show();
    if (cmd == "ipcp-create") { need(3); return ipcp_create(r[0], r[1], r[2]); }
    if (cmd == "ipcp-destroy") { need(1); return ipcp_destroy(r[0]); }
    if (cmd == "reset") return reset();
#ifdef RINA_CTL_IN_HOST
    if (cmd == "node-rejoin") return node_rejoin();
#endif
    if (cmd == "terminate") { mem().write("ev:agent:" + netns() + "|t", "1"); return 0; }   // into this machine's agent's held read: it ends
    if (cmd == "ipcp-config") { need(3); return ipcp_config(r[0], r[1], r[2]); }
    if (cmd == "ipcp-config-get") { need(2); return ipcp_config_get(r[0], r[1]); }
    if (cmd == "ipcp-enroller-enable") { need(1); return ipcp_enroller(r[0], true); }
    if (cmd == "ipcp-enroller-disable") { need(1); return ipcp_enroller(r[0], false); }
    if (cmd == "ipcp-register") { need(2); return ipcp_register(r[0], r[1], true); }
    if (cmd == "ipcp-unregister") { need(2); return ipcp_register(r[0], r[1], false); }
    if (cmd == "regs-show") return regs_show(r);
    if (cmd == "dif-rib-show") return dif_show(r.empty() ? "" : r[0], "RIB");
    if (cmd == "dif-routing-show") return dif_show(r.empty() ? "" : r[0], "Routing table");
    if (cmd == "dif-rib-paths-show") return dif_show(r.empty() ? "" : r[0], "RIB paths");
    if (cmd == "ipcp-enroll" || cmd == "ipcp-enroll-retry") return ipcp_enroll(r);
    if (cmd == "ipcp-lower-flow-alloc") return lower_flow_alloc(r);
    if (cmd == "ipcp-neigh-disconnect") return neigh_disconnect(r);
    if (cmd == "ipcp-route-add") return route_mod(r, true);
    if (cmd == "ipcp-route-del") return route_mod(r, false);
    if (cmd == "ipcp-sched-config") return sched_config(r);
    if (cmd == "uipcp-stats-show") { printf("uipcp stats:\n"); return 0; }
    if (cmd == "flows-show") return flows_show();
    if (cmd == "ipcp-stats") return ipcp_stats(r);
    if (cmd == "dif-policy-mod") { need(3); return policy_mod(r[0], r[1], r[2]); }
    if (cmd == "dif-policy-list") { need(1); return policy_list(r[0], r); }
    if (cmd == "dif-policy-param-mod") { need(4); return param_mod(r[0], r[1], r[2], r[3]); }
    if (cmd == "dif-policy-param-list") { need(1); return param_list(r[0], r); }
    fprintf(stderr, "Unknown command '%s'\n", cmd.c_str());
    return 1;
}
