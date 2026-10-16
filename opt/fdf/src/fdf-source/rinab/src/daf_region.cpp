// daf_region.cpp -- [DAF_MODEL_V1] John Day's distributed-application model (RINA Reference Model, Part 2, Chapters 1
// and 2) as a region of the FD&F RAM host: op=daf. Plan: "The RINA DAF Model on Ribbit: A Prototype Plan" (2026-10-16).
//
// [DAF_LOCKLESS_V1] One region holds each DAF; every member reads and writes it. No lock, no replication, no
// reconciliation. The region's own memory (daf_memory.hpp) is lock-free: a record its owner alone writes is published
// by exchange (a member's enrollment, its entry in a name, its inbox items); a decision others may race is a
// compare-and-swap from the value read (a DAF or a name created, a synonym claimed, a nonce used, a request answered,
// an approval completing), and the loser re-reads and sees the winner. Holding a read is a continuation parked on the
// variable, answered by whoever writes it -- no thread waits. A partition is not merged: a member cut off from the
// region loses its session and leaves; on its return it re-enrolls (short rejoin: its synonyms are kept).
//
// Records (variable, instance) -> JSON text; "" is a record that is gone. D = "<dan>#<daii>", one DAF instance.
//   dafs            <D>                 the DAF: dan, daii, owner, synonym sets, synonym lifetime          (Permanent)
//   m|<D>           <apn>               an enrolled member: role, facts, synonyms, session
//   ret|<D>         <apn>               a member that left: its synonyms and role, kept until `until` (short rejoin)
//   s|<D>|<set>     <synonym>           who holds a synonym of a set
//   n|<D>           <name>              a whatevercast name: rule, lifetime, designated                     (Permanent)
//   e|<D>|<name>    <apn>               a member of the name: facts, ref, session (lifetime session) or p (Permanent)
//   nv|<D>          <name>              the name's version: written on every change -- a wait on it is a watch
//   pol|<D>         <key>               a policy (key: *, op:<op>, role:<role>, name:<name>, objects:<role>) (Permanent)
//   audit|<D>       <n>                 one record per check-point decision
//   ref|<D>         <key>               the DAF's reference data, given at enrollment                       (Permanent)
//   sx|<sid>        <D>\x1f<apn>[\x1f<name>]   what a session enrolled and joined (gone with the session)
//   #tok|<D>, #auth, #owner             secrets: never answered by any operation
// Nothing here is readable except through op=daf, so every read passes the check point.
#include "rina_region.h"
#include "daf_memory.hpp"
#include "frogram.hpp"
#include "fdf_version.hpp"
#include <atomic>
#include <chrono>
#include <functional>
#include <thread>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <map>
#include <set>
#include <string>
#include <unistd.h>
#include <vector>
#include <algorithm>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <cerrno>
using frogram::Json;

namespace {
std::atomic<uint64_t> g_seq{0};

std::string dump(const Json& j) {
    switch (j.type) {
        case Json::Null: return "null"; case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { if (std::fabs(j.n) < 9e15 && j.n == double((long long)j.n)) return std::to_string((long long)j.n);
                          char b[32]; snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case Json::Str: return Json::quote(j.s);
        case Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        default: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
}
Json num(double v) { return Json{Json::Num, false, v, {}, {}, {}}; }
double now() { return dmem::clock_s(); }
std::string err(const std::string& code, const std::string& why) { return "{\"ok\":false,\"error\":" + Json::quote(code) + ",\"why\":" + Json::quote(why) + "}"; }
struct Refuse { std::string code, why; };
[[noreturn]] void refuse(const std::string& code, const std::string& why) { throw Refuse{code, why}; }

// ---- [DAF_FILE_V1] Permanent records: appended with their id and fdatasync'd before they are published; replayed at
// start, where the highest surviving id of each record wins (a publication that lost its race left a void line)
class DafFile {
public:
    void open_and_replay() {
        const char* f = getenv("RINA_DAF_FILE"); const char* r = getenv("RINA_REGION_FILE");
        path_ = (f && *f) ? f : (r && *r) ? std::string(r) + ".daf" : "";
        if (path_.empty()) { fprintf(stderr, "rina-host: [DAF_FILE_V1] no RINA_DAF_FILE or RINA_REGION_FILE: the DAF region does not start\n"); _exit(2); }
        struct L { std::string bag; uint64_t id; };
        std::vector<std::pair<std::pair<std::string, std::string>, L>> lines; std::set<uint64_t> voids;
        if (FILE* in = fopen(path_.c_str(), "r")) {
            char* line = nullptr; size_t cap = 0; ssize_t n; size_t no = 0;
            while ((n = getline(&line, &cap, in)) > 0) { ++no; Json j;
                try { j = Json::parse(std::string(line, size_t(n))); } catch (...) { fprintf(stderr, "rina-host: [DAF_FILE_V1] %s line %zu is damaged\n", path_.c_str(), no); _exit(2); }
                if (j.type == Json::Obj) { voids.insert(uint64_t(j["void"].n)); continue; }
                lines.push_back({{j[0].s, j[1].s}, L{j[2].s, j.a.size() > 3 ? uint64_t(j[3].n) : uint64_t(no)}}); }
            free(line); fclose(in);
        } else if (errno != ENOENT) { fprintf(stderr, "rina-host: [DAF_FILE_V1] reading %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        std::map<std::pair<std::string, std::string>, L> last;
        for (auto& [k, l] : lines) { if (voids.count(l.id)) continue; auto it = last.find(k); if (it == last.end() || it->second.id < l.id) last[k] = l; }
        const std::string tmp = path_ + ".new"; FILE* out = fopen(tmp.c_str(), "w");
        if (!out) { fprintf(stderr, "rina-host: [DAF_FILE_V1] writing %s: %s\n", tmp.c_str(), strerror(errno)); _exit(2); }
        size_t kept = 0;
        for (auto& [k, l] : last) { if (l.bag.empty()) continue;                 // a deletion: gone for good
            dmem::restore(k.first, k.second, l.bag, l.id); fputs(line_of(k.first, k.second, l.bag, l.id).c_str(), out); ++kept; }
        if (fflush(out) != 0 || fdatasync(fileno(out)) != 0 || fclose(out) != 0 || rename(tmp.c_str(), path_.c_str()) != 0) {
            fprintf(stderr, "rina-host: [DAF_FILE_V1] compacting %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fd_ = ::open(path_.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC | O_CREAT, 0600);
        if (fd_ < 0) { fprintf(stderr, "rina-host: [DAF_FILE_V1] opening %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fprintf(stderr, "rina-host: [DAF_FILE_V1] %zu Permanent DAF record(s) restored from %s ([DAF_MODEL_V1] [DAF_LOCKLESS_V1] op=daf, build %s)\n", kept, path_.c_str(), FDF_BUILD_TAG);
    }
    // one write() per line (O_APPEND: whole lines, never interleaved), then fdatasync; a failure is said and fatal
    void append(const std::string& v, const std::string& i, const std::string& bag, uint64_t id) { line(line_of(v, i, bag, id)); }
    void void_id(uint64_t id) { line("{\"void\":" + std::to_string((unsigned long long)id) + "}\n"); }
private:
    void line(const std::string& l) {
        if (::write(fd_, l.data(), l.size()) != ssize_t(l.size()) || fdatasync(fd_) != 0) {
            fprintf(stderr, "rina-host: [DAF_FILE_V1] could not make a Permanent record durable in %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
    }
    static std::string line_of(const std::string& v, const std::string& i, const std::string& bag, uint64_t id) {
        return "[" + Json::quote(v) + "," + Json::quote(i) + "," + Json::quote(bag) + "," + std::to_string((unsigned long long)id) + "]\n"; }
    std::string path_; int fd_ = -1;
};
DafFile& dfile() { static DafFile* f = new DafFile; return *f; }

// ---- the memory, in the region's terms
bool get(const std::string& v, const std::string& i, Json& out, uint64_t* id = nullptr) {
    std::string b; if (!dmem::get(v, i, b, id)) return false; out = Json::parse(b); return true; }
struct Cell { std::string instance; uint64_t id; Json bag; };
std::vector<Cell> rows(const std::string& v) {
    std::vector<Cell> out; for (auto& r : dmem::rows(v)) out.push_back(Cell{r.instance, r.id, Json::parse(r.bag)}); return out; }
uint64_t put(const std::string& v, const std::string& i, const std::string& bag) { return dmem::put(v, i, bag); }   // its one writer
void gone(const std::string& v, const std::string& i) { dmem::put(v, i, ""); }
// a decision others may race, not durable: decide(current, next) -> false to decline
uint64_t cas(const std::string& v, const std::string& i, const std::function<bool(const std::string&, std::string&)>& decide) { return dmem::cas(v, i, decide); }
// a Permanent decision: durable before it is published
uint64_t pcas(const std::string& v, const std::string& i, const std::function<bool(const std::string&, std::string&)>& decide) {
    return dmem::cas(v, i, decide, [&](uint64_t id, const std::string& bag) { dfile().append(v, i, bag, id); }, [](uint64_t id) { dfile().void_id(id); }); }
uint64_t pput(const std::string& v, const std::string& i, const std::string& bag) { return pcas(v, i, [&](const std::string&, std::string& n) { n = bag; return true; }); }
void pgone(const std::string& v, const std::string& i) { pcas(v, i, [](const std::string& c, std::string& n) { n = ""; return !c.empty(); }); }
// claim a record that is free: true if this call wrote it
bool claim(const std::string& v, const std::string& i, const std::string& bag) { return cas(v, i, [&](const std::string& c, std::string& n) { if (!c.empty()) return false; n = bag; return true; }) != 0; }
// take a record away (exactly one caller gets it): true if this call made it gone
bool take(const std::string& v, const std::string& i) { return cas(v, i, [](const std::string& c, std::string& n) { if (c.empty()) return false; n = ""; return true; }) != 0; }
std::string sess() { return std::to_string((unsigned long long)rina_session_id()); }

// ---- the DAF
struct Daf { std::string D; Json j; };
Daf daf_of(const Json& in) {
    const std::string D = in["daf"].s; if (D.empty()) refuse("EINVAL", "no daf named");
    Daf d{D, {}}; if (!get("dafs", D, d.j)) refuse("ENOENT", "not found"); return d;   // [DAF_NOT_FOUND_IS_ONE_ANSWER_V1]
}
// the caller: the member this session enrolled in D (empty if none)
struct Who { std::string apn, role; Json mem; };
Who caller(const std::string& D) {
    for (auto& c : rows("sx|" + sess())) { const std::string k = c.instance; const size_t b = k.find('\x1f');
        if (b == std::string::npos || k.compare(0, b, D) != 0 || k.find('\x1f', b + 1) != std::string::npos) { continue; }
        Who w; w.apn = k.substr(b + 1);
        // the member's own session, or a further connection it opened on another session ([DAF_MORE_CONNECTIONS_V1])
        if (get("m|" + D, w.apn, w.mem) && (w.mem["sess"].s == sess() || c.bag["kind"].s == "conn")) { w.role = w.mem["role"].s; return w; } }
    return Who{};
}

// ---- [DAF_CHECK_POINT_V1] (plan H) every operation asks here first. The most specific policy decides:
// name:<object>, then op:<op>, then role:<role>, then *. None: open. Every decision is recorded.
void record_audit(const std::string& D, const Who& w, const std::string& op, const std::string& object, bool allow, const std::string& why) {
    put("audit|" + D, std::to_string(++g_seq), "{\"t\":" + std::to_string(now()) + ",\"apn\":" + Json::quote(w.apn) + ",\"role\":" + Json::quote(w.role) +
        ",\"sess\":" + Json::quote(sess()) + ",\"op\":" + Json::quote(op) + ",\"object\":" + Json::quote(object) + ",\"decision\":" +
        (allow ? "\"allow\"" : "\"refuse\"") + ",\"why\":" + Json::quote(why) + "}");
}
bool check(const std::string& D, const Who& w, const std::string& op, const std::string& object, std::string& why) {
    Json p; std::string key;
    const std::string keys[] = {"name:" + object, "op:" + op, "role:" + w.role, "*"};
    for (auto& k : keys) { if (k == "name:" || k == "role:") continue; if (get("pol|" + D, k, p)) { key = k; break; } }
    bool allow = true; why = "open";
    if (!key.empty()) { const std::string mode = p["mode"].s;
        if (mode == "refuse") { allow = false; why = "refused by policy " + key; }
        else if (mode == "redirect") { allow = false; why = "redirect:" + dump(p["to"]); }   // [DAF_ALLOCATOR_V1] an alternative offered
        else if (mode == "roles") { allow = false; for (auto& r : p["roles"].a) if (r.s == w.role) allow = true;
                                    why = allow ? "role " + w.role + " allowed by policy " + key : "role " + (w.role.empty() ? std::string("(none)") : w.role) + " not allowed by policy " + key; }
        else why = "open by policy " + key; }
    record_audit(D, w, op, object, allow, why);
    return allow;
}
thread_local const Json* g_in = nullptr;                    // the request being answered
// [DAF_AE_OBJECT_SET_V1] (plan D) does an object fall within a granted object set? patterns: exact, "prefix*", "*"
bool within(const Json& objects, const std::string& object) {
    for (auto& p : objects.a) { const std::string& x = p.s;
        if (x == "*" || x == object) return true;
        if (!x.empty() && x.back() == '*' && object.compare(0, x.size() - 1, x, 0, x.size() - 1) == 0) return true; }
    return false;
}
void must(const std::string& D, const Who& w, const std::string& op, const std::string& object) {
    // a DAF's objects are its members' business: everything but enrolling needs this session enrolled (Part 2-1 5.5)
    // [DAF_NOT_FOUND_IS_ONE_ANSWER_V1] (Part 2-2 2.6.3) a session that is not a member gets the answer a DAF that does
    // not exist gets: "not found". The audit keeps the real reason.
    if (w.apn.empty() && op != "enroll" && op != "request") { record_audit(D, w, op, object, false, "not enrolled"); refuse("ENOENT", "not found"); }
    std::string why; if (!check(D, w, op, object, why)) refuse(why.compare(0, 9, "redirect:") == 0 ? "EREDIRECT" : "EPERM", why);
    // an operation made on an Application-Entity's connection is checked against the object set it was granted
    if (g_in && !(*g_in)["ae"].s.empty()) {
        Json g; if (!get("ae|" + D + "|" + sess(), (*g_in)["ae"].s, g)) refuse("ENOTCONN", "no connection " + (*g_in)["ae"].s + " on this session");
        if (!within(g["objects"], object) && !check(D, w, "outside_object_set", (*g_in)["ae"].s + ":" + object, why)) refuse("EPERM", why + " (" + object + " is outside the object set of " + (*g_in)["ae"].s + ")");
    }
}

// ---- section A: DAFs, names, synonyms
const char* BUILTIN[][2] = {{"members", "{\"rule\":\"all\",\"lifetime\":\"session\",\"builtin\":true}"},
                            {"join", "{\"rule\":\"designated\",\"lifetime\":\"session\",\"builtin\":true}"},
                            {"service", "{\"rule\":\"one\",\"lifetime\":\"session\",\"builtin\":true}"}};
std::string create_daf(const Json& in) {
    const std::string dan = in["dan"].s, daii = in["daii"].s.empty() ? "1" : in["daii"].s;
    if (dan.empty() || dan.find('#') != std::string::npos || daii.find('#') != std::string::npos) refuse("EINVAL", "a DAF needs a dan (no '#')");
    const std::string D = dan + "#" + daii;
    std::string sets = "[\"addr\""; for (auto& s : in["synsets"].a) if (s.s != "addr") sets += "," + Json::quote(s.s); sets += "]";
    const double life = in["syn_lifetime_s"].type == Json::Num ? in["syn_lifetime_s"].n : 300;
    // its three whatevercast names exist from the start (Part 2-1 4.2): all members, the member to join through, the
    // member that serves requests. Each is claimed only if absent, so racing creators write the same thing once.
    for (auto& b : BUILTIN) pcas("n|" + D, b[0], [&](const std::string& c, std::string& n) { if (!c.empty()) return false; n = b[1]; return true; });
    // the DAF's own record is the decision: whoever claims it created the DAF
    const std::string bag = "{\"dan\":" + Json::quote(dan) + ",\"daii\":" + Json::quote(daii) + ",\"owner\":" + Json::quote(in["owner"].s) +
        ",\"synsets\":" + sets + ",\"syn_lifetime_s\":" + dump(num(life)) + ",\"created\":" + std::to_string(now()) + "}";
    if (!pcas("dafs", D, [&](const std::string& c, std::string& n) { if (!c.empty()) return false; n = bag; return true; })) refuse("EEXIST", "DAF " + D + " exists");
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + "}";
}
std::string list_dafs(const Json& in) {
    std::string out = "{\"ok\":true,\"dafs\":["; bool f = true;
    for (auto& c : rows("dafs")) if (in["dan"].s.empty() || c.bag["dan"].s == in["dan"].s) { out += (f ? "" : ",") + std::string("{\"daf\":") + Json::quote(c.instance) + ",\"info\":" + dump(c.bag) + "}"; f = false; }
    return out + "]}";
}
// a synonym slot is free for apn if nobody holds it, apn holds it, or its holder is away and past its lifetime
bool free_for(const std::string& D, const std::string& cur, const std::string& apn) {
    if (cur.empty()) return true;
    const std::string holder = Json::parse(cur)["apn"].s; if (holder == apn) return true;
    Json m; if (get("m|" + D, holder, m)) return false;                                 // held by a live member
    Json r; if (get("ret|" + D, holder, r) && r["until"].n > now()) return false;       // reserved for its return
    return true;
}
// the lowest synonym of a set free for apn, claimed by compare-and-swap (a racer for the same one moves on)
std::string assign_synonym(const std::string& D, const std::string& set, const std::string& apn) {
    const std::string mine = "{\"apn\":" + Json::quote(apn) + "}";
    for (unsigned n = 1; ; ++n) {
        const std::string syn = (set == "addr" ? "a" : set.substr(0, 1)) + std::to_string(n);
        bool ok = false;
        cas("s|" + D + "|" + set, syn, [&](const std::string& c, std::string& nx) { ok = free_for(D, c, apn); if (!ok || c == mine) return false; nx = mine; return true; });
        if (ok) return syn;
    }
}

// ---- section B: whatevercast names
Json name_of(const std::string& D, const std::string& name) { Json n; if (!get("n|" + D, name, n)) refuse("ENOENT", "no name " + name + " in " + D); return n; }
void on_change(const std::string& D, const std::string& name);   // section E, below
void bump(const std::string& D, const std::string& name) { put("nv|" + D, name, "{\"t\":" + std::to_string(now()) + "}"); on_change(D, name); }
std::string define_name(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s;
    if (name.empty() || name.find('|') != std::string::npos) refuse("EINVAL", "a name needs text without '|'");
    must(d.D, w, "define_name", name);
    const std::string rule = in["rule"].s.empty() ? "all" : in["rule"].s, life = in["lifetime"].s.empty() ? "session" : in["lifetime"].s;
    if (rule != "all" && rule != "one" && rule != "designated" && rule != "nearest") refuse("EINVAL", "rule is all, one, designated or nearest");
    if (life != "session" && life != "persistent") refuse("EINVAL", "lifetime is session or persistent");
    std::string clash;
    pcas("n|" + d.D, name, [&](const std::string& c, std::string& n) {
        Json old = c.empty() ? Json{} : Json::parse(c);
        if (!c.empty() && old["lifetime"].s != life) { clash = old["lifetime"].s; return false; }
        n = "{\"rule\":" + Json::quote(rule) + ",\"lifetime\":" + Json::quote(life) + ",\"designated\":" + Json::quote(old["designated"].s) + (old["builtin"].b ? ",\"builtin\":true" : "") + "}";
        return true; });
    if (!clash.empty()) refuse("EEXIST", "name " + name + " exists with lifetime " + clash);
    bump(d.D, name);
    return "{\"ok\":true}";
}
std::string join(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; Json n = name_of(d.D, name);
    const std::string apn = in["apn"].s.empty() ? w.apn : in["apn"].s;
    if (apn.empty()) refuse("ENOENT", "not found");
    must(d.D, w, apn == w.apn ? "join" : "admit", name);
    const bool persistent = n["lifetime"].s == "persistent";
    if (!persistent && apn != w.apn) refuse("EINVAL", "a session name is joined by the member itself, over its own session");
    const std::string facts = in["facts"].type == Json::Obj ? dump(in["facts"]) : "{}";
    std::string bag = "{\"apn\":" + Json::quote(apn) + ",\"facts\":" + facts + ",\"ref\":" + Json::quote(in["ref"].s) + ",\"t\":" + std::to_string(now());
    if (persistent) pput("e|" + d.D + "|" + name, apn, bag + ",\"p\":1,\"by\":" + Json::quote(w.apn) + "}");
    else { put("e|" + d.D + "|" + name, apn, bag + ",\"sess\":" + Json::quote(sess()) + "}");   // the member's own entry
           put("sx|" + sess(), d.D + "\x1f" + apn + "\x1f" + name, "{\"kind\":\"entry\"}"); }
    bump(d.D, name);
    return "{\"ok\":true}";
}
std::string unjoin(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; Json n = name_of(d.D, name);
    const std::string apn = in["apn"].s.empty() ? w.apn : in["apn"].s;
    must(d.D, w, apn == w.apn ? "leave_name" : "remove", name);
    Json e; if (!get("e|" + d.D + "|" + name, apn, e)) refuse("ENOENT", apn + " is not in " + name);
    if (n["lifetime"].s == "persistent") pgone("e|" + d.D + "|" + name, apn); else gone("e|" + d.D + "|" + name, apn);
    bump(d.D, name);
    return "{\"ok\":true}";
}
std::string designate(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; name_of(d.D, name);
    must(d.D, w, "designate", name);
    pcas("n|" + d.D, name, [&](const std::string& c, std::string& nx) { if (c.empty()) return false; Json n = Json::parse(c);
        nx = "{\"rule\":" + Json::quote(n["rule"].s) + ",\"lifetime\":" + Json::quote(n["lifetime"].s) + ",\"designated\":" + Json::quote(in["apn"].s) + (n["builtin"].b ? ",\"builtin\":true" : "") + "}";
        return true; });
    bump(d.D, name);
    return "{\"ok\":true}";
}
// a fact against a filter clause {f, op, v}
bool pass(const Json& facts, const Json& c) {
    const Json& x = facts[c["f"].s]; const std::string op = c["op"].s.empty() ? "=" : c["op"].s; const Json& v = c["v"];
    if (x.type == Json::Null) return op == "absent";
    if (op == "present") return true;
    if (x.type == Json::Num && v.type == Json::Num) {
        if (op == "=") { return x.n == v.n; }
        if (op == "!=") { return x.n != v.n; }
        if (op == ">") { return x.n > v.n; }
        if (op == ">=") { return x.n >= v.n; }
        if (op == "<") { return x.n < v.n; }
        if (op == "<=") { return x.n <= v.n; }
        return false; }
    if (x.type == Json::Bool && v.type == Json::Bool) return op == "=" ? x.b == v.b : op == "!=" ? x.b != v.b : false;
    if (x.type == Json::Str && v.type == Json::Str) return op == "=" ? x.s == v.s : op == "!=" ? x.s != v.s : false;
    return false;
}
struct Entry { std::string apn, ref; Json facts; bool live; double key = 0; };
std::vector<Entry> members_of(const std::string& D, const std::string& name) {
    std::vector<Entry> out;
    for (auto& c : rows("e|" + D + "|" + name)) { Json m; const bool live = get("m|" + D, c.instance, m); out.push_back(Entry{c.instance, c.bag["ref"].s, c.bag["facts"], live}); }
    std::sort(out.begin(), out.end(), [](const Entry& a, const Entry& b) { return a.apn < b.apn; });
    return out;
}
// [DAF_RESOLVE_V1] a name, resolved by its rule (or the caller's), against the members as they are now
std::string resolve_core(const std::string& D, const Json& in, std::string* chosen) {
    const std::string name = in["name"].s; Json n = name_of(D, name);
    const std::string rule = in["rule"].s.empty() ? n["rule"].s : in["rule"].s;
    const uint64_t rev = dmem::version("nv|" + D, name);          // read first: the members below are at least this new
    std::vector<Entry> sel; const bool live_only = in["live"].b;
    for (auto& e : members_of(D, name)) {
        if (live_only && !e.live) continue;
        bool ok = true; for (auto& c : in["filter"].a) if (!pass(e.facts, c)) { ok = false; break; }
        if (ok) sel.push_back(e);
    }
    if (rule == "designated") { std::vector<Entry> d; for (auto& e : sel) if (e.apn == n["designated"].s) d.push_back(e); sel = d; }
    else if (rule == "one" || rule == "nearest") {
        // ranked: by a fact (desc by default), or by distance from a point; ties to the lowest identity
        std::vector<Entry> rk; const std::string f = in["rank"]["f"].s; const bool asc = in["rank"]["dir"].s == "asc";
        for (auto& e : sel) {
            if (rule == "nearest") { const Json& at = in["near"]; if (e.facts["x"].type != Json::Num || e.facts["y"].type != Json::Num) continue;
                e.key = -std::hypot(e.facts["x"].n - at["x"].n, e.facts["y"].n - at["y"].n); }
            else if (!f.empty()) { if (e.facts[f].type != Json::Num) continue; e.key = asc ? -e.facts[f].n : e.facts[f].n; }
            rk.push_back(e); }
        std::stable_sort(rk.begin(), rk.end(), [](const Entry& a, const Entry& b) { return a.key != b.key ? a.key > b.key : a.apn < b.apn; });
        if (rk.size() > 1) { rk.resize(1); }
        sel = rk;
    }
    if (chosen) *chosen = sel.empty() ? "" : sel[0].apn;
    std::string out = "{\"ok\":true,\"name\":" + Json::quote(name) + ",\"rule\":" + Json::quote(rule) + ",\"rev\":" + std::to_string(rev) + ",\"members\":["; bool f = true;
    for (auto& e : sel) { out += (f ? "" : ",") + std::string("{\"apn\":") + Json::quote(e.apn) + ",\"ref\":" + Json::quote(e.ref) + ",\"live\":" + (e.live ? "true" : "false") + ",\"facts\":" + dump(e.facts) + "}"; f = false; }
    return out + "]}";
}
std::string resolve_inner(const std::string& D, const Who& w, const Json& in, std::string* chosen) { must(D, w, "resolve", in["name"].s); return resolve_core(D, in, chosen); }
std::string resolve(const Json& in) { Daf d = daf_of(in); return resolve_inner(d.D, caller(d.D), in, nullptr); }
std::string names(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "names", d.D); std::string out = "{\"ok\":true,\"names\":["; bool f = true;
    for (auto& c : rows("n|" + d.D)) { out += (f ? "" : ",") + std::string("{\"name\":") + Json::quote(c.instance) + ",\"settings\":" + dump(c.bag) + "}"; f = false; }
    return out + "]}";
}

// ---- section H: policies and the audit
std::string set_policy(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string key = in["key"].s.empty() ? "*" : in["key"].s, mode = in["mode"].s;
    if (mode != "open" && mode != "refuse" && mode != "roles" && mode != "redirect") refuse("EINVAL", "mode is open, refuse, roles or redirect");
    must(d.D, w, "set_policy", key);
    if (mode == "open" && in["clear"].b) pgone("pol|" + d.D, key);
    else pput("pol|" + d.D, key, "{\"mode\":" + Json::quote(mode) + ",\"roles\":" + (in["roles"].type == Json::Arr ? dump(in["roles"]) : "[]") +
              ",\"to\":" + (in["to"].type == Json::Obj ? dump(in["to"]) : "null") + "}");
    return "{\"ok\":true}";
}
std::string audit(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "audit", d.D); const double after = in["after"].n;
    std::vector<std::pair<unsigned long long, std::string>> rs;
    for (auto& c : rows("audit|" + d.D)) { const unsigned long long n = strtoull(c.instance.c_str(), nullptr, 10); if (double(n) > after) rs.push_back({n, dump(c.bag)}); }
    std::sort(rs.begin(), rs.end());
    std::string out = "{\"ok\":true,\"records\":["; bool f = true;
    for (auto& [n, b] : rs) { out += (f ? "" : ",") + std::string("{\"n\":") + std::to_string(n) + ",\"r\":" + b + "}"; f = false; }
    return out + "]}";
}

// ---- enrollment (Part 2-1 5.5; Enrollment specs)
std::string enroll(const Json& in) {
    Daf d = daf_of(in); const std::string apn = in["apn"].s, role = in["role"].s.empty() ? "member" : in["role"].s;
    if (apn.empty() || apn.find('\x1f') != std::string::npos) refuse("EINVAL", "enroll needs an apn");
    Who w; w.apn = apn; w.role = role;
    // 1) state: new, returning within its synonym lifetime, or returning after it
    Json cur; if (get("m|" + d.D, apn, cur) && cur["sess"].s != sess()) refuse("EEXIST", apn + " is enrolled on another session");
    Json ret; const bool back = get("ret|" + d.D, apn, ret), shortr = back && ret["until"].n > now() && ret["role"].s == role;
    // 2) role and capabilities: the role asked for, at the check point; the facts it declares
    must(d.D, w, "enroll", role);
    // 3) synonyms: one from each set; on a short rejoin its own again, unless someone live took it meanwhile
    const std::string mine = "{\"apn\":" + Json::quote(apn) + "}";
    std::string syn = "{"; bool f = true;
    for (auto& s : d.j["synsets"].a) {
        std::string v;
        if (shortr && ret["syn"][s.s].type == Json::Str) { bool ok = false; const std::string want = ret["syn"][s.s].s;
            cas("s|" + d.D + "|" + s.s, want, [&](const std::string& c, std::string& nx) { Json held; ok = c.empty() || c == mine || !get("m|" + d.D, Json::parse(c)["apn"].s, held); if (!ok || c == mine) return false; nx = mine; return true; });
            if (ok) v = want; }
        if (v.empty()) v = assign_synonym(d.D, s.s, apn);
        syn += (f ? "" : ",") + Json::quote(s.s) + ":" + Json::quote(v); f = false; }
    syn += "}";
    const std::string facts = in["facts"].type == Json::Obj ? dump(in["facts"]) : "{}";
    // the member record is the decision: one session holds an apn
    const std::string mbag = "{\"apn\":" + Json::quote(apn) + ",\"role\":" + Json::quote(role) + ",\"facts\":" + facts + ",\"syn\":" + syn +
                             ",\"sess\":" + Json::quote(sess()) + ",\"t\":" + std::to_string(now()) + "}";
    bool taken = false;
    const uint64_t inst = cas("m|" + d.D, apn, [&](const std::string& c, std::string& nx) { if (!c.empty() && Json::parse(c)["sess"].s != sess()) { taken = true; return false; } nx = mbag; return true; });
    if (taken) refuse("EEXIST", apn + " is enrolled on another session");
    put("sx|" + sess(), d.D + "\x1f" + apn, "{\"kind\":\"member\",\"inst\":" + std::to_string(inst) + "}");
    // [DAF_MORE_CONNECTIONS_V1] a member may hold several connections (Part 2-1 5.4.2), each its own session -- so a
    // long transfer never waits behind, or holds up, its short requests (a session runs its requests in order). The
    // token lets the member's other sessions connect as it; it changes at every enrollment, and no operation returns it
    // except this one, to this member.
    unsigned char rnd[16]; { FILE* u = fopen("/dev/urandom", "rb"); if (!u || fread(rnd, 1, 16, u) != 16) { if (u) fclose(u); refuse("EIO", "no randomness for a token"); } fclose(u); }
    static const char hx[] = "0123456789abcdef"; std::string token; for (unsigned char c : rnd) { token += hx[c >> 4]; token += hx[c & 15]; }
    put("#tok|" + d.D, apn, "{\"token\":" + Json::quote(token) + ",\"inst\":" + std::to_string(inst) + "}");
    if (back) gone("ret|" + d.D, apn);
    // the member is in the DAF's all-members name (session) from now on
    put("e|" + d.D + "|members", apn, "{\"apn\":" + Json::quote(apn) + ",\"facts\":" + facts + ",\"ref\":" + Json::quote(in["ref"].s) + ",\"t\":" + std::to_string(now()) + ",\"sess\":" + Json::quote(sess()) + "}");
    put("sx|" + sess(), d.D + "\x1f" + apn + "\x1f" + "members", "{\"kind\":\"entry\"}");
    bump(d.D, "members");
    // 4) static and near-static data, skipped on a short rejoin: the DAF and its reference data
    std::string refd = "[";
    if (!shortr) { bool g = true; for (auto& c : rows("ref|" + d.D)) { refd += (g ? "" : ",") + std::string("{\"key\":") + Json::quote(c.instance) + ",\"v\":" + dump(c.bag) + "}"; g = false; } }
    refd += "]";
    // [DAF_REQUEST_V1] what was stored for this member while it was away: each taken exactly once
    std::string stored = "["; { bool g = true; for (auto& c : rows("store|" + d.D + "|" + apn)) { if (!take("store|" + d.D + "|" + apn, c.instance)) continue;
        stored += (g ? "" : ",") + std::string("{\"rid\":") + Json::quote(c.instance) + ",\"m\":" + dump(c.bag) + "}"; g = false; } }
    stored += "]";
    // 6) the state the member continues from: its names and the write it holds reads from
    std::string ns = "["; f = true; for (auto& c : rows("n|" + d.D)) { ns += (f ? "" : ",") + Json::quote(c.instance); f = false; } ns += "]";
    return "{\"ok\":true,\"daf\":" + Json::quote(d.D) + ",\"apn\":" + Json::quote(apn) + ",\"inst\":" + std::to_string(inst) + ",\"from\":" + std::to_string(inst) + ",\"token\":" + Json::quote(token) + ",\"role\":" + Json::quote(role) +
           ",\"state\":" + (shortr ? "\"short\"" : back ? "\"returning\"" : "\"new\"") + ",\"syn\":" + syn + ",\"static\":" + (shortr ? "\"kept\"" : "\"sent\"") +
           ",\"ref\":" + refd + ",\"stored\":" + stored + ",\"names\":" + ns + ",\"daf_info\":" + dump(d.j) + "}";
}
// a member's leaving, by its own word (leave) or its session's end: only the session that holds the member makes it
// leave. Its synonyms are kept for the lifetime (written before the member goes, so a re-enrollment sees them).
void depart(const std::string& D, const std::string& apn, const std::string& s, bool keep) {
    Json m; if (!get("m|" + D, apn, m) || m["sess"].s != s) return;
    Json d; get("dafs", D, d);
    if (keep) put("ret|" + D, apn, "{\"syn\":" + dump(m["syn"]) + ",\"role\":" + Json::quote(m["role"].s) + ",\"until\":" + std::to_string(now() + d["syn_lifetime_s"].n) + "}");
    if (!cas("m|" + D, apn, [&](const std::string& c, std::string& nx) { if (c.empty() || Json::parse(c)["sess"].s != s) return false; nx = ""; return true; })) return;
    for (auto& c : rows("pend|" + D + "|" + apn)) {        // [DAF_REQUEST_V1] its unanswered requests: an error, never silence
        claim("ans|" + D, c.instance, "{\"ok\":false,\"error\":\"gone\",\"why\":\"the member serving the request left before answering\"}"); gone("pend|" + D + "|" + apn, c.instance); }
    bump(D, "members");
}
std::string leave(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); if (w.apn.empty()) refuse("ENOENT", "not found");
    must(d.D, w, "leave", w.apn);
    for (auto& c : rows("sx|" + sess())) { const std::string k = c.instance;
        if (k.compare(0, d.D.size() + 1 + w.apn.size() + 1, d.D + "\x1f" + w.apn + "\x1f") == 0) {
            const std::string name = k.substr(d.D.size() + w.apn.size() + 2); gone("e|" + d.D + "|" + name, w.apn); bump(d.D, name); gone("sx|" + sess(), k); } }
    gone("sx|" + sess(), d.D + "\x1f" + w.apn);
    depart(d.D, w.apn, sess(), false);
    return "{\"ok\":true}";
}
std::string set_ref(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_ref", in["key"].s);
    if (in["key"].s.empty()) refuse("EINVAL", "set_ref needs a key");
    pput("ref|" + d.D, in["key"].s, in["v"].type == Json::Obj ? dump(in["v"]) : "{\"v\":" + dump(in["v"]) + "}");
    return "{\"ok\":true}";
}
std::string whoami(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D);
    if (w.apn.empty()) return "{\"ok\":true,\"enrolled\":false}";
    return "{\"ok\":true,\"enrolled\":true,\"member\":" + dump(w.mem) + "}";
}
// a synonym, resolved within its DAF and set only (Part 2-1 4.2: unambiguous only within the DAF)
std::string synonym(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string set = in["set"].s.empty() ? "addr" : in["set"].s;
    must(d.D, w, "synonym", set);
    Json h; if (!get("s|" + d.D + "|" + set, in["syn"].s, h)) refuse("ENOENT", "no synonym " + in["syn"].s + " in set " + set + " of " + d.D);
    Json m; if (!get("m|" + d.D, h["apn"].s, m) || m["syn"][set].s != in["syn"].s) refuse("ENOENT", "synonym " + in["syn"].s + " is held for a member who is away");
    return "{\"ok\":true,\"apn\":" + Json::quote(h["apn"].s) + "}";
}
std::string members(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "members", d.D); std::string out = "{\"ok\":true,\"members\":["; bool f = true;
    for (auto& c : rows("m|" + d.D)) { out += (f ? "" : ",") + dump(c.bag); f = false; }
    return out + "]}";
}

// ---- section D: Application-Entities and connections (Part 2-1 5.4)
// [DAF_AUTH_MODULES_V1] authentication, chosen per DAF: "null", or "key" -- HMAC-SHA-256 with the DAF's key of
// "<daf>|<ae>|<apn>|<nonce>". The key is a secret record (#auth): no operation answers it.
std::string hmac_hex(const std::string& key, const std::string& msg);
std::string set_auth(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_auth", in["module"].s);
    const std::string mod = in["module"].s; if (mod != "null" && mod != "key") refuse("EINVAL", "module is null or key");
    put("#auth", d.D, "{\"module\":" + Json::quote(mod) + ",\"key\":" + Json::quote(in["key"].s) + "}");
    return "{\"ok\":true}";
}
bool authenticate(const std::string& D, const std::string& ae, const std::string& apn, const Json& a, std::string& why) {
    Json au; if (!get("#auth", D, au) || au["module"].s == "null") { why = "null"; return true; }
    const std::string key = au["key"].s, nonce = a["nonce"].s;
    if (nonce.size() < 8) { why = "key: a nonce of 8 or more characters is needed"; return false; }
    const std::string want = hmac_hex(key, D + "|" + ae + "|" + apn + "|" + nonce);
    if (a["proof"].s != want) { why = "key: the proof does not match"; return false; }
    if (!claim("nonce|" + D, nonce, "{}")) { why = "key: the nonce was used before"; return false; }   // first use wins
    why = "key"; return true;
}
std::string connect(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string ae = in["ae_name"].s;
    if (ae.empty()) refuse("EINVAL", "connect names an Application-Entity (ae_name)");
    if (w.apn.empty() && !in["apn"].s.empty()) {           // [DAF_MORE_CONNECTIONS_V1] another session of an enrolled member
        Json t, m;
        if (!get("#tok|" + d.D, in["apn"].s, t) || t["token"].s != in["token"].s || !get("m|" + d.D, in["apn"].s, m)) refuse("EACCES", "not a connection of an enrolled member: wrong apn or token");
        put("sx|" + sess(), d.D + "\x1f" + in["apn"].s, "{\"kind\":\"conn\"}");
        w = caller(d.D);
    }
    if (w.apn.empty()) refuse("ENOENT", "not found");
    std::string why; if (!authenticate(d.D, ae, w.apn, in["auth"], why)) { std::string rec; check(d.D, w, "connect_auth_failed", ae, rec); refuse("EACCES", "authentication failed: " + why); }
    must(d.D, w, "connect", ae);
    // the object set granted: what was asked, narrowed to what the role's policy allows (objects:<role>)
    Json lim; std::string granted = "["; bool f = true;
    const bool limited = get("pol|" + d.D, "objects:" + w.role, lim);
    for (auto& o : in["objects"].a) { if (limited && !within(lim["objects"], o.s)) continue; granted += (f ? "" : ",") + Json::quote(o.s); f = false; }
    granted += "]";
    put("ae|" + d.D + "|" + sess(), ae, "{\"apn\":" + Json::quote(w.apn) + ",\"objects\":" + granted + ",\"auth\":" + Json::quote(why) + "}");
    put("sxa|" + sess(), d.D + "\x1f" + ae, "{}");
    return "{\"ok\":true,\"ae\":" + Json::quote(ae) + ",\"granted\":" + granted + ",\"auth\":" + Json::quote(why) + "}";
}
std::string disconnect(const Json& in) {
    Daf d = daf_of(in); const std::string ae = in["ae_name"].s; gone("ae|" + d.D + "|" + sess(), ae); gone("sxa|" + sess(), d.D + "\x1f" + ae);
    return "{\"ok\":true}";
}
std::string set_objects(const Json& in) {     // the object set a role may be granted
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_policy", "objects:" + in["role"].s);
    pput("pol|" + d.D, "objects:" + in["role"].s, "{\"mode\":\"objects\",\"objects\":" + (in["objects"].type == Json::Arr ? dump(in["objects"]) : "[]") + "}");
    return "{\"ok\":true}";
}
// [DAF_FLOWS_V1] a flow between members: opened to a member, or to a whatevercast name resolved by its rule. The
// receiver waits (op=daf wait) on its inbox ("box") for new flows and on the flow's data ("fd").
std::atomic<uint64_t> g_fid{0};
std::string flow_open(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); std::string to = in["to"].s;
    if (to.empty()) {                                   // to a whatevercast name: resolve it first
        std::string chosen; must(d.D, w, "flow_open", in["name"].s); resolve_inner(d.D, w, in, &chosen); to = chosen;
        if (to.empty()) refuse("ENOENT", "no member of " + in["name"].s + " is selected by its rule");
    } else must(d.D, w, "flow_open", to);
    Json m; if (!get("m|" + d.D, to, m)) refuse("ENOENT", to + " is not a member now");
    const std::string fid = std::to_string((unsigned long long)(now() * 1000)) + "-" + std::to_string(++g_fid);
    const std::string spec = in["spec"].type == Json::Obj ? dump(in["spec"]) : "{}";
    put("fl|" + d.D, fid, "{\"from\":" + Json::quote(w.apn) + ",\"to\":" + Json::quote(to) + ",\"spec\":" + spec + ",\"sess\":" + Json::quote(sess()) + "}");
    put("box|" + d.D + "|" + to, fid, "{\"from\":" + Json::quote(w.apn) + ",\"spec\":" + spec + "}");
    return "{\"ok\":true,\"fid\":" + Json::quote(fid) + ",\"to\":" + Json::quote(to) + "}";
}
std::string flow_send(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string fid = in["fid"].s;
    Json f; if (!get("fl|" + d.D, fid, f)) refuse("ENOENT", "no flow " + fid);
    if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
    if (f["closed"].b) refuse("EPIPE", "flow " + fid + " is closed");
    must(d.D, w, "flow_send", "flow:" + fid);
    const std::string dir = f["from"].s == w.apn ? "f" : "r";                 // f: toward the receiver; r: back
    const uint64_t id = put("fd|" + d.D + "|" + fid + "|" + dir, std::to_string((unsigned long long)in["seq"].n), "{\"d\":" + dump(in["data"]) + "}");
    return "{\"ok\":true,\"id\":" + std::to_string(id) + "}";
}
std::string flow_close(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string fid = in["fid"].s;
    Json f; if (!get("fl|" + d.D, fid, f)) refuse("ENOENT", "no flow " + fid);
    if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
    put("fd|" + d.D + "|" + fid + "|f", "end", "{\"end\":true}"); put("fd|" + d.D + "|" + fid + "|r", "end", "{\"end\":true}");
    // the flow's record stays, closed: its ends can still read what was sent, and its end; nothing more is sent on it
    cas("fl|" + d.D, fid, [](const std::string& c, std::string& nx) { if (c.empty()) return false; Json j = Json::parse(c); if (j["closed"].b) return false;
        nx = c.substr(0, c.size() - 1) + ",\"closed\":true}"; return true; });
    gone("box|" + d.D + "|" + f["to"].s, fid);
    return "{\"ok\":true}";
}

// ---- section E: the RIB Daemon (Part 2-1 4.0, 2) -- subscriptions, distribution, and scripts started beside the memory
// [DAF_SUBSCRIPTIONS_V1] a subscription is a spec; identical specs are ONE subscription however many hold it, computed
// once per change (or per period) and published to one record, ("subd|<D>", <key>), on which every holder waits.
// [DAF_LOCKLESS_V1] A subscription object is made once (insert-only table); its spec is published once by
// compare-and-swap; its holders are a counter. Its result is published by compare-and-swap from the value read before
// computing it, so a result computed from older state never replaces one computed from newer.
struct SubSpec { std::string D, key; Json j; double period; };
struct SubObj { std::atomic<const SubSpec*> spec{nullptr}; std::atomic<int> holders{0}; std::atomic<double> next{0}; std::atomic<bool> indexed{false}; };
struct ScriptObj { std::string D, id, from, out, sess; Json j; double every = 0; bool on_change = false; std::atomic<bool> stopped{false}; std::atomic<double> next{0}; };
struct IdxNode { SubObj* s; ScriptObj* sc; IdxNode* next; };
struct IdxList { std::atomic<IdxNode*> head{nullptr}; };
void idx_push(IdxList& L, SubObj* s, ScriptObj* sc) { IdxNode* n = new IdxNode{s, sc, L.head.load(std::memory_order_acquire)};
    while (!L.head.compare_exchange_weak(n->next, n, std::memory_order_acq_rel, std::memory_order_acquire)) {} }
rmem::Table<SubObj>& subs() { static auto* t = new rmem::Table<SubObj>; return *t; }
rmem::Table<ScriptObj>& scripts() { static auto* t = new rmem::Table<ScriptObj>; return *t; }
rmem::Table<IdxList>& by_name() { static auto* t = new rmem::Table<IdxList>; return *t; }   // "D\x1fname" -> what follows it
IdxList& periodic() { static auto* l = new IdxList; return *l; }
std::atomic<uint64_t> g_sub_runs{0}, g_script_runs{0}; std::atomic<int> g_subs_held{0}, g_scripts_live{0};
std::string sub_key(const std::string& spec) { char b[20]; snprintf(b, sizeof b, "%016llx", (unsigned long long)std::hash<std::string>{}(spec)); return b; }
std::string sub_data(const SubSpec& s) {
    std::string data = "{"; bool f = true;
    if (s.j["kind"].s == "collect") {                       // one fact from every member of each name
        for (auto& n : s.j["names"].a) { data += (f ? "" : ",") + Json::quote(n.s) + ":{"; f = false; bool g = true;
            for (auto& e : members_of(s.D, n.s)) { const Json& x = e.facts[s.j["fact"].s]; if (x.type == Json::Null) continue;
                data += (g ? "" : ",") + Json::quote(e.apn) + ":" + dump(x); g = false; }
            data += "}"; }
    } else {                                                // the state of each name: its members and their facts
        for (auto& n : s.j["names"].a) { data += (f ? "" : ",") + Json::quote(n.s) + ":["; f = false; bool g = true;
            for (auto& e : members_of(s.D, n.s)) { bool ok = true; for (auto& c : s.j["filter"].a) { if (!pass(e.facts, c)) { ok = false; break; } }
                if (!ok) { continue; }
                data += (g ? "" : ",") + std::string("{\"apn\":") + Json::quote(e.apn) + ",\"live\":" + (e.live ? "true" : "false") + ",\"facts\":" + dump(e.facts) + "}"; g = false; }
            data += "]"; }
    }
    return data + "}";
}
void sub_run(const SubSpec& s) {
    cas("subd|" + s.D, s.key, [&](const std::string&, std::string& nx) {   // computed afresh on every attempt
        const uint64_t run = ++g_sub_runs;
        nx = "{\"t\":" + std::to_string(now()) + ",\"run\":" + std::to_string(run) + ",\"data\":" + sub_data(s) + "}"; return true; });
}
std::string subscribe(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D);
    for (auto& n : in["names"].a) { must(d.D, w, "subscribe", n.s); name_of(d.D, n.s); }
    if (in["names"].a.empty()) refuse("EINVAL", "a subscription names one or more whatevercast names");
    const std::string kind = in["kind"].s.empty() ? "state" : in["kind"].s;
    if (kind != "state" && kind != "collect") refuse("EINVAL", "kind is state or collect");
    const std::string spec = "{\"kind\":" + Json::quote(kind) + ",\"names\":" + dump(in["names"]) + ",\"filter\":" + (in["filter"].type == Json::Arr ? dump(in["filter"]) : "[]") +
                             ",\"fact\":" + Json::quote(in["fact"].s) + ",\"period_s\":" + dump(in["period_s"].type == Json::Num ? in["period_s"] : num(0)) + "}";
    const std::string key = sub_key(d.D + spec);
    SubObj* s = subs().get(d.D + "\x1f" + key, true); if (!s) refuse("ENOSPC", "the subscription table is full");
    { const SubSpec* none = nullptr; SubSpec* made = new SubSpec{d.D, key, Json::parse(spec), 0}; made->period = made->j["period_s"].n;
      const SubSpec* mine = made;
      if (!s->spec.compare_exchange_strong(none, mine, std::memory_order_acq_rel)) delete mine; }   // made once: the first spec stands
    const SubSpec& sp = *s->spec.load(std::memory_order_acquire);
    if (!s->indexed.exchange(true, std::memory_order_acq_rel)) {   // indexed once: by each name it follows, and by time
        for (auto& n : sp.j["names"].a) if (IdxList* L = by_name().get(d.D + "\x1f" + n.s, true)) idx_push(*L, s, nullptr);
        if (sp.period > 0) { s->next.store(now() + sp.period); idx_push(periodic(), s, nullptr); } }
    const bool fresh = s->holders.fetch_add(1, std::memory_order_acq_rel) == 0;
    if (fresh) ++g_subs_held;
    put("sxs|" + sess(), d.D + "\x1f" + key + "\x1f" + std::to_string(++g_seq), "{}");
    if (fresh || !dmem::version("subd|" + d.D, key)) sub_run(sp);
    return "{\"ok\":true,\"sub\":" + Json::quote(key) + ",\"rev\":" + std::to_string(dmem::version("subd|" + d.D, key)) + ",\"shared\":" + (fresh ? "false" : "true") + "}";
}
void sub_release(const std::string& D, const std::string& key) {
    SubObj* s = subs().get(D + "\x1f" + key, false); if (!s) return;
    if (s->holders.fetch_sub(1, std::memory_order_acq_rel) == 1) --g_subs_held;   // held by nobody: no longer computed
}
std::string unsubscribe(const Json& in) {
    Daf d = daf_of(in); const std::string key = in["sub"].s;
    for (auto& c : rows("sxs|" + sess())) if (c.instance.compare(0, d.D.size() + key.size() + 2, d.D + "\x1f" + key + "\x1f") == 0 && take("sxs|" + sess(), c.instance)) {
        sub_release(d.D, key); return "{\"ok\":true}"; }
    refuse("ENOENT", "this session holds no subscription " + key);
}
// distribute: a value to every member of a name, into each member's own inbox ("in|<D>|<apn>")
std::string distribute(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; name_of(d.D, name);
    must(d.D, w, "distribute", name); if (in["key"].s.empty()) refuse("EINVAL", "distribute needs a key");
    int n = 0; for (auto& e : members_of(d.D, name)) { put("in|" + d.D + "|" + e.apn, in["key"].s, "{\"from\":" + Json::quote(w.apn) + ",\"v\":" + dump(in["value"]) + "}"); ++n; }
    return "{\"ok\":true,\"delivered\":" + std::to_string(n) + "}";
}
// [DAF_START_V1] START (Part 2-1 5.4.3: "transfer a script or program to the other process and START it"). A script is a
// restricted form, as CDAP's scope and filter are: from a name, filter the members, take a fact, reduce it (count, sum,
// min, max, avg, list), and write the result to ("out|<D>", <out>). It runs once, every N seconds, or on every change
// to its name, until STOPped or its session ends. Not arbitrary code: a program that can do anything at the memory is
// a decision this prototype leaves to the region's owner.
void script_run(const ScriptObj& sc) {
    cas("out|" + sc.D, sc.out, [&](const std::string&, std::string& nx) {   // computed afresh on every attempt
        ++g_script_runs;
        const std::string agg = sc.j["agg"].s.empty() ? "count" : sc.j["agg"].s, f = sc.j["fact"].s;
        double acc = 0; int n = 0; double mn = 0, mx = 0; std::string list = "["; bool first = true;
        for (auto& e : members_of(sc.D, sc.from)) {
            if (sc.j["live"].b && !e.live) continue;
            bool ok = true; for (auto& c : sc.j["filter"].a) { if (!pass(e.facts, c)) { ok = false; break; } }
            if (!ok) continue;
            const Json& x = e.facts[f];
            if (agg == "list") { list += (first ? "" : ",") + Json::quote(e.apn); first = false; ++n; continue; }
            if (agg == "count") { ++n; continue; }
            if (x.type != Json::Num) continue;
            if (n == 0) { mn = mx = x.n; } else { mn = std::min(mn, x.n); mx = std::max(mx, x.n); }
            acc += x.n; ++n;
        }
        list += "]";
        const std::string v = agg == "count" ? std::to_string(n) : agg == "sum" ? dump(num(acc)) : agg == "min" ? dump(num(mn)) :
                              agg == "max" ? dump(num(mx)) : agg == "avg" ? dump(num(n ? acc / n : 0)) : list;
        nx = "{\"v\":" + v + ",\"n\":" + std::to_string(n) + ",\"t\":" + std::to_string(now()) + ",\"script\":" + Json::quote(sc.id) + "}"; return true; });
}
std::string start(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const Json& j = in["script"];
    must(d.D, w, "start", j["out"].s);
    const std::string agg = j["agg"].s;
    if (j["from"].s.empty() || j["out"].s.empty()) refuse("EINVAL", "a script needs from and out");
    if (!agg.empty() && agg != "count" && agg != "sum" && agg != "min" && agg != "max" && agg != "avg" && agg != "list") refuse("EINVAL", "agg is count, sum, min, max, avg or list");
    name_of(d.D, j["from"].s);
    const std::string id = "s" + std::to_string(++g_seq);
    ScriptObj* sc = scripts().get(id, true); if (!sc) refuse("ENOSPC", "the script table is full");
    // the id is this call's alone: its fields are written before the script is indexed (published) anywhere
    sc->D = d.D; sc->id = id; sc->from = j["from"].s; sc->out = j["out"].s; sc->j = j; sc->sess = sess();
    sc->every = j["every_s"].type == Json::Num ? j["every_s"].n : 0; sc->on_change = j["on_change"].b; sc->next.store(now() + sc->every);
    script_run(*sc);
    if (sc->every > 0 || sc->on_change) {
        ++g_scripts_live;
        put("sxs|" + sess(), d.D + "\x1f" + "script:" + id + "\x1f" + std::to_string(++g_seq), "{}");
        if (sc->on_change) if (IdxList* L = by_name().get(d.D + "\x1f" + sc->from, true)) idx_push(*L, nullptr, sc);
        if (sc->every > 0) idx_push(periodic(), nullptr, sc);
    } else sc->stopped.store(true);
    Json o; get("out|" + d.D, sc->out, o);
    return "{\"ok\":true,\"id\":" + Json::quote(id) + ",\"result\":" + dump(o) + "}";
}
bool script_stop(const std::string& id) { ScriptObj* sc = scripts().get(id, false); if (!sc || sc->stopped.exchange(true)) return false; --g_scripts_live; return true; }
std::string stop(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "stop", in["id"].s);
    if (!script_stop(in["id"].s)) refuse("ENOENT", "no running script " + in["id"].s);
    return "{\"ok\":true}";
}
std::string stats(const Json&) {
    return "{\"ok\":true,\"subscription_runs\":" + std::to_string(g_sub_runs.load()) + ",\"script_runs\":" + std::to_string(g_script_runs.load()) +
           ",\"subscriptions\":" + std::to_string(g_subs_held.load()) + ",\"scripts\":" + std::to_string(g_scripts_live.load()) + "}";
}
void on_change(const std::string& D, const std::string& name) {
    IdxList* L = by_name().get(D + "\x1f" + name, false); if (!L) return;
    for (IdxNode* n = L->head.load(std::memory_order_acquire); n; n = n->next) {
        if (n->s) { const SubSpec* sp = n->s->spec.load(std::memory_order_acquire); if (sp && sp->period == 0 && n->s->holders.load() > 0) sub_run(*sp); }
        else if (n->sc && !n->sc->stopped.load()) script_run(*n->sc); }
}
// [DAF_PERIODIC_V1] time is the one thing no write announces: the periodic subscriptions and scripts are run by the
// host's own thread when they fall due. Lock-free like the rest: whoever moves `next` forward runs it.
void tick() {
    const double t = now();
    for (IdxNode* n = periodic().head.load(std::memory_order_acquire); n; n = n->next) {
        if (n->s) { const SubSpec* sp = n->s->spec.load(); double due = n->s->next.load();
            if (sp && n->s->holders.load() > 0 && t >= due && n->s->next.compare_exchange_strong(due, t + sp->period)) sub_run(*sp); }
        else if (n->sc) { double due = n->sc->next.load();
            if (!n->sc->stopped.load() && t >= due && n->sc->next.compare_exchange_strong(due, t + n->sc->every)) script_run(*n->sc); } }
}

// ---- section F: name-space management (Part 2-2 2.5). A name space lives in a DAF (its NSM function). Names are
// hierarchical ("county/harborview/er"); the creator owns the root; an owner may delegate a subtree, and then assigns
// only within what it owns. A name resolves to an object or to another name (indirection), possibly in another space or
// another DAF's space, followed up to a hop limit. A requester without access gets exactly the answer a missing name
// gets. A space marked internal resolves only for members of its own DAF.
bool owns(const std::string& D, const std::string& space, const std::string& apn, const std::string& name) {
    Json sp; if (!get("nsp|" + D, space, sp)) return false;
    if (sp["owner"].s == apn) return true;
    std::string pre = name;
    for (;;) { Json dg; if (get("dlg|" + D + "|" + space, pre, dg) && dg["to"].s == apn) return true;
        const size_t sl = pre.rfind('/'); if (sl == std::string::npos) return false; pre = pre.substr(0, sl); }
}
std::string ns_create(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s;
    if (sp.empty() || sp.find('|') != std::string::npos) refuse("EINVAL", "a space needs a name without '|'");
    must(d.D, w, "ns_create", sp);
    const std::string bag = "{\"owner\":" + Json::quote(w.apn) + ",\"internal\":" + (in["internal"].b ? "true" : "false") + "}";
    if (!pcas("nsp|" + d.D, sp, [&](const std::string& c, std::string& n) { if (!c.empty()) return false; n = bag; return true; })) refuse("EEXIST", "space " + sp + " exists");
    return "{\"ok\":true}";
}
std::string ns_delegate(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s, pre = in["prefix"].s;
    must(d.D, w, "ns_delegate", sp + ":" + pre);
    if (!owns(d.D, sp, w.apn, pre)) refuse("EPERM", w.apn + " does not own " + pre + " in " + sp);
    pput("dlg|" + d.D + "|" + sp, pre, "{\"to\":" + Json::quote(in["to"].s) + ",\"by\":" + Json::quote(w.apn) + "}");
    return "{\"ok\":true}";
}
std::string ns_assign(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s, name = in["name"].s;
    must(d.D, w, "ns_assign", sp + ":" + name);
    if (!owns(d.D, sp, w.apn, name)) refuse("EPERM", w.apn + " may not assign " + name + " in " + sp);
    const Json& t = in["target"];
    if (t["alias"].type == Json::Obj) pput("nsn|" + d.D + "|" + sp, name, "{\"alias\":" + dump(t["alias"]) + ",\"by\":" + Json::quote(w.apn) + "}");
    else pput("nsn|" + d.D + "|" + sp, name, "{\"object\":" + dump(t["object"]) + ",\"by\":" + Json::quote(w.apn) + "}");
    return "{\"ok\":true}";
}
std::string ns_resolve(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D);
    std::string D = d.D, sp = in["space"].s, name = in["name"].s; const int limit = in["max_hops"].type == Json::Num ? int(in["max_hops"].n) : 8;
    const std::string missing = err("ENOENT", "not found");     // the one answer for a missing name AND a refused one
    std::string path = "[";
    for (int hop = 0; ; ++hop) {
        if (hop > limit) refuse("ELOOP", "more than " + std::to_string(limit) + " indirections from " + in["name"].s);
        Json spj; if (!get("nsp|" + D, sp, spj)) return missing;
        if (spj["internal"].b && D != d.D) return missing;      // an internal space, asked from another DAF
        std::string why; if (!check(D, w, "ns_resolve", sp + ":" + name, why)) return missing;
        Json n; if (!get("nsn|" + D + "|" + sp, name, n)) return missing;
        path += (hop ? "," : "") + std::string("{\"daf\":") + Json::quote(D) + ",\"space\":" + Json::quote(sp) + ",\"name\":" + Json::quote(name) + "}";
        if (n["alias"].type != Json::Obj) return "{\"ok\":true,\"object\":" + dump(n["object"]) + ",\"path\":" + path + "]}";
        if (!n["alias"]["daf"].s.empty()) { D = n["alias"]["daf"].s; }
        if (!n["alias"]["space"].s.empty()) { sp = n["alias"]["space"].s; }
        name = n["alias"]["name"].s;
    }
}

// ---- section I: requests without enrollment (Part 2-1 6). [DAF_REQUEST_V1] A requester that is not a member asks a
// DAF's name; the request goes to the member the name's rule selects, which answers into ("ans|<D>", <rid>), on which
// the requester -- that session alone -- waits. The requester gains no membership, no synonym and no view of the DAF.
// With store, the request is held for a named member until its next enrollment (the mail model).
std::atomic<uint64_t> g_rid{0};
std::string request(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s;
    must(d.D, w, "request", in["store"].b ? "store:" + in["to"].s : name);
    const std::string rid = std::to_string((unsigned long long)(now() * 1000)) + "-" + std::to_string(++g_rid);
    const std::string body = "{\"body\":" + dump(in["body"]) + ",\"from\":" + Json::quote(w.apn.empty() ? "(not a member)" : w.apn) + ",\"t\":" + std::to_string(now()) + "}";
    if (in["store"].b) {
        if (in["to"].s.empty()) refuse("EINVAL", "a stored request names its recipient (to)");
        put("store|" + d.D + "|" + in["to"].s, rid, body); return "{\"ok\":true,\"rid\":" + Json::quote(rid) + ",\"stored\":true}";
    }
    std::string chosen; resolve_core(d.D, in, &chosen);
    if (chosen.empty()) refuse("ENOENT", "not found");
    Json m; if (!get("m|" + d.D, chosen, m)) refuse("ENOENT", "not found");
    put("ask|" + d.D, rid, "{\"sess\":" + Json::quote(sess()) + "}");   // whose answer it is
    put("pend|" + d.D + "|" + chosen, rid, "{}");
    put("req|" + d.D + "|" + chosen, rid, body);
    // the member may have left between the choice and the posting: then its departure did not see this request
    if (!get("m|" + d.D, chosen, m) && take("pend|" + d.D + "|" + chosen, rid))
        claim("ans|" + d.D, rid, "{\"ok\":false,\"error\":\"gone\",\"why\":\"the member serving the request left before answering\"}");
    return "{\"ok\":true,\"rid\":" + Json::quote(rid) + "}";
}
std::string answer(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "answer", in["rid"].s);
    Json p; if (!get("pend|" + d.D + "|" + w.apn, in["rid"].s, p)) refuse("ENOENT", "no request " + in["rid"].s + " waits on " + w.apn);
    if (!claim("ans|" + d.D, in["rid"].s, "{\"ok\":true,\"body\":" + dump(in["body"]) + "}")) refuse("ENOENT", "request " + in["rid"].s + " was already answered");
    gone("pend|" + d.D + "|" + w.apn, in["rid"].s); gone("req|" + d.D + "|" + w.apn, in["rid"].s);
    return "{\"ok\":true}";
}

// ---- section G: the DIF Allocator (Part 2-2 2.6). [DAF_ALLOCATOR_V1] Searching is name-space management (F): each
// region registers the applications it offers in a directory DAF's "apps" space; a requester resolves there, and the
// search ends at the owning region, whose own check point decides -- join (enrollment), an alternative (the redirect
// policy), or "not found". Creating a DAF that spans two owners takes both owners' agreement, each proven with its key:
// each owner's approval is its own record, and the creation is the DAF's record, claimed once by whoever completes it.
std::string register_owner(const Json& in) {
    const std::string o = in["owner"].s; if (o.empty() || in["key"].s.size() < 16) refuse("EINVAL", "an owner and a key of 16 or more characters");
    if (!claim("#owner", o, "{\"key\":" + Json::quote(in["key"].s) + "}")) refuse("EEXIST", "owner " + o + " is registered");
    return "{\"ok\":true}";
}
std::string propose_daf(const Json& in) {
    const std::string dan = in["dan"].s, daii = in["daii"].s.empty() ? "1" : in["daii"].s, D = dan + "#" + daii;
    if (dan.empty() || in["owners"].a.size() < 2) refuse("EINVAL", "a proposal names a DAF and two or more owners");
    for (auto& o : in["owners"].a) { Json k; if (!get("#owner", o.s, k)) refuse("ENOENT", "owner " + o.s + " is not registered here"); }
    Json old; if (get("dafs", D, old)) refuse("EEXIST", D + " exists or is proposed");
    const std::string bag = "{\"owners\":" + dump(in["owners"]) + ",\"spec\":" + dump(in) + "}";
    if (!pcas("prop", D, [&](const std::string& c, std::string& n) { if (!c.empty()) return false; n = bag; return true; })) refuse("EEXIST", D + " exists or is proposed");
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"proposed\"}";
}
std::string approve_daf(const Json& in) {
    const std::string D = in["daf"].s, o = in["owner"].s; Json p; if (!get("prop", D, p)) refuse("ENOENT", "not found");
    bool listed = false; for (auto& x : p["owners"].a) if (x.s == o) listed = true;
    if (!listed) refuse("EPERM", o + " is not an owner of " + D);
    Json k; const std::string nonce = in["nonce"].s;
    if (!get("#owner", o, k) || nonce.size() < 8 || in["proof"].s != hmac_hex(k["key"].s, "approve|" + D + "|" + nonce)) refuse("EACCES", "the owner's proof does not match");
    if (!claim("nonce|prop", nonce, "{}")) refuse("EACCES", "the nonce was used before");
    pput("apv|" + D, o, "{\"t\":" + std::to_string(now()) + "}");                // this owner's approval: its own record
    std::set<std::string> have; for (auto& c : rows("apv|" + D)) have.insert(c.instance);
    std::string ap = "["; bool f = true; for (auto& x : have) { ap += (f ? "" : ",") + Json::quote(x); f = false; } ap += "]";
    bool all = true; for (auto& x : p["owners"].a) if (!have.count(x.s)) all = false;
    if (!all) return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"proposed\",\"approved\":" + ap + "}";
    std::string owners; for (auto& x : p["owners"].a) owners += (owners.empty() ? "" : "+") + x.s;
    const std::string spec = p["spec"]["synsets"].type == Json::Arr ? dump(p["spec"]["synsets"]) : "[]";
    Json c = Json::parse("{\"dan\":" + Json::quote(p["spec"]["dan"].s) + ",\"daii\":" + Json::quote(p["spec"]["daii"].s.empty() ? "1" : p["spec"]["daii"].s) +
                         ",\"owner\":" + Json::quote(owners) + ",\"synsets\":" + spec + "}");
    try { create_daf(c); } catch (const Refuse& x) { if (x.code != "EEXIST") throw; }   // two last approvers: one creates, both see it created
    pgone("prop", D);
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"created\",\"approved\":" + ap + "}";
}

// ---- [DAF_WAIT_V1] holding a read: the region's own, through the check point. {fn:"wait", daf, what, key, after}
//   what   name  a whatevercast name's version ("watch")       -> {rev}
//          sub   a subscription's result (key: from subscribe) -> {rev, rows}
//          ans   the answer to this session's request (key: rid)
//          box   the caller's flow inbox      fd  a flow's data (key: "<fid>|f" or "<fid>|r"; the caller is an end)
//          in    the caller's distribution inbox      req  the caller's requests      out  a script's output (key: out)
// Answered at once if anything there is past `after` (or "now":true); otherwise parked as this request's continuation
// and answered by the write that makes the answer exist -- or dropped when this session ends. Key "" = every instance.
std::string rows_answer(const std::string& v, const std::string& inst, uint64_t after, bool always) {
    auto rs = dmem::rows(v, inst, after, true);
    if (rs.empty() && !always) return "";
    std::sort(rs.begin(), rs.end(), [](const dmem::Row& a, const dmem::Row& b) { return a.id < b.id; });
    uint64_t rev = after; std::string out = ",\"rows\":["; bool f = true;
    for (auto& r : rs) { rev = std::max(rev, r.id);
        out += (f ? "" : ",") + std::string("{\"i\":") + Json::quote(r.instance) + ",\"id\":" + std::to_string(r.id) + ",\"v\":" + (r.bag.empty() ? std::string("null") : r.bag) + "}"; f = false; }
    return "{\"ok\":true,\"rev\":" + std::to_string(rev) + out + "]}";
}
std::string wait(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string what = in["what"].s, key = in["key"].s;
    const uint64_t after = in["after"].type == Json::Num ? uint64_t(in["after"].n) : 0; const bool now_only = in["now"].b;
    std::string v, inst = key; bool name_watch = false;
    if (what == "name") { must(d.D, w, "resolve", key); name_of(d.D, key); v = "nv|" + d.D; name_watch = true; }
    else if (what == "sub") { must(d.D, w, "subscribe", key); v = "subd|" + d.D; }
    else if (what == "out") { must(d.D, w, "read_out", key); v = "out|" + d.D; }
    else if (what == "box" || what == "in" || what == "req") { must(d.D, w, "wait_" + what, w.apn); v = what + "|" + d.D + "|" + w.apn; }
    else if (what == "fd") { const size_t b = key.rfind('|'); const std::string fid = key.substr(0, b);
        Json f; if (b == std::string::npos || !get("fl|" + d.D, fid, f)) refuse("ENOENT", "no flow " + fid);
        if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
        must(d.D, w, "flow_read", "flow:" + fid); v = "fd|" + d.D + "|" + key; inst = in["seq"].type == Json::Num ? std::to_string((unsigned long long)in["seq"].n) : ""; }
    else if (what == "ans") { Json a; if (!get("ask|" + d.D, key, a) || a["sess"].s != sess()) refuse("ENOENT", "not found");   // this session's request only
        v = "ans|" + d.D; }
    else refuse("EINVAL", "what is name, sub, out, box, in, req, fd or ans");
    auto answer_fn = [v, inst, name_watch](uint64_t aft, bool always) -> std::string {
        if (name_watch) { const uint64_t r = dmem::version(v, inst); return r > aft || always ? "{\"ok\":true,\"rev\":" + std::to_string(r) + "}" : ""; }
        return rows_answer(v, inst, aft, always); };
    const std::string a = answer_fn(after, now_only);
    if (!a.empty()) return a;
    void* h = rina_defer(); if (!h) refuse("ENOTSUP", "a held read needs a request to park (FNW1)");
    put("sw|" + sess(), v, "{}");                         // so this session's end can drop its waits
    dmem::park(v, new dmem::Waiter{h, after, inst, rina_session_id(), [answer_fn](const dmem::Waiter& x) { return answer_fn(x.after, false); }});
    return "";
}

std::string dispatch(const std::string& fn, const Json& in) {
    if (fn == "create_daf") return create_daf(in);
    if (fn == "list_dafs") return list_dafs(in);
    if (fn == "enroll") return enroll(in);
    if (fn == "leave") return leave(in);
    if (fn == "whoami") return whoami(in);
    if (fn == "members") return members(in);
    if (fn == "synonym") return synonym(in);
    if (fn == "set_auth") return set_auth(in);
    if (fn == "connect") return connect(in);
    if (fn == "disconnect") return disconnect(in);
    if (fn == "set_objects") return set_objects(in);
    if (fn == "flow_open") return flow_open(in);
    if (fn == "flow_send") return flow_send(in);
    if (fn == "flow_close") return flow_close(in);
    if (fn == "subscribe") return subscribe(in);
    if (fn == "unsubscribe") return unsubscribe(in);
    if (fn == "distribute") return distribute(in);
    if (fn == "start") return start(in);
    if (fn == "stop") return stop(in);
    if (fn == "stats") return stats(in);
    if (fn == "ns_create") return ns_create(in);
    if (fn == "ns_delegate") return ns_delegate(in);
    if (fn == "ns_assign") return ns_assign(in);
    if (fn == "ns_resolve") return ns_resolve(in);
    if (fn == "request") return request(in);
    if (fn == "answer") return answer(in);
    if (fn == "register_owner") return register_owner(in);
    if (fn == "propose_daf") return propose_daf(in);
    if (fn == "approve_daf") return approve_daf(in);
    if (fn == "set_ref") return set_ref(in);
    if (fn == "define_name") return define_name(in);
    if (fn == "names") return names(in);
    if (fn == "join") return join(in);
    if (fn == "unjoin") return unjoin(in);
    if (fn == "designate") return designate(in);
    if (fn == "resolve") return resolve(in);
    if (fn == "set_policy") return set_policy(in);
    if (fn == "audit") return audit(in);
    if (fn == "wait") return wait(in);
    if (fn == "version") return std::string("{\"ok\":true,\"daf_model\":\"DAF_MODEL_V1\",\"memory\":\"DAF_LOCKLESS_V1\",\"server\":\"") + FDF_BUILD_TAG + "\"}";
    refuse("ENOSYS", "no DAF operation " + fn);
}
// the region exists from the host's start: its Permanent records restored, its periodic work running
const bool g_daf_at_start = [] { dfile().open_and_replay();
    std::thread([] { for (;;) { std::this_thread::sleep_for(std::chrono::milliseconds(20)); tick(); } }).detach(); return true; }();
}  // namespace

// [DAF_DIAG_V1] DAF_DIAG=<ms>: every call slower than that is logged with its parse and run times
static const double g_diag_ms = [] { const char* e = getenv("DAF_DIAG"); return e && *e ? atof(e) : -1.0; }();
std::string daf_region_call(const std::string& body) {
    const double t0 = now();
    Json in; try { in = Json::parse(body); } catch (const std::exception& e) { return err("EINVAL", std::string("body is not JSON: ") + e.what()); }
    // [DAF_NEEDS_A_SESSION_V1] membership, connections and requests are bound to the caller's session. The plain-HTTP
    // front has none (every request there is session 0), so it would make every caller one member: refused, said.
    if (rina_session_id() == 0 && in["fn"].s != "version" && in["fn"].s != "list_dafs")
        return err("ENOTSUP", "op=daf needs a session (FNW1); a plain HTTP request has none");
    const double t1 = now();
    std::string r;
    g_in = &in;
    try { r = dispatch(in["fn"].s, in); } catch (const Refuse& x) { r = err(x.code, x.why); }
    g_in = nullptr;
    if (g_diag_ms >= 0) { const double t2 = now(); if ((t2 - t0) * 1000 > g_diag_ms)
        fprintf(stderr, "[DAF_DIAG_V1] fn=%s bytes=%zu parse=%.2fms run=%.2fms sess=%s\n", in["fn"].s.c_str(), body.size(), (t1 - t0) * 1e3, (t2 - t1) * 1e3, sess().c_str()); }
    return r;
}
void daf_start() { (void)g_daf_at_start; }
// [DAF_SESSION_IS_LIVENESS_V1] a session's end: its waits are dropped, its connections, subscriptions and scripts end,
// its session-held entries go (every name they were in is bumped, so its watchers wake), and its members leave
// (synonyms kept for the lifetime). Everything here is this session's own records, taken once each.
void daf_session_down(uint64_t sid) {
    const std::string s = std::to_string((unsigned long long)sid);
    dmem::put("ended", s, "1");
    for (auto& r : dmem::rows("sw|" + s)) { gone("sw|" + s, r.instance); dmem::wake(r.instance); }   // its parked waits, dropped
    for (auto& c : rows("sxa|" + s)) { const size_t a = c.instance.find('\x1f');           // its connections end
        gone("ae|" + c.instance.substr(0, a) + "|" + s, c.instance.substr(a + 1)); gone("sxa|" + s, c.instance); }
    for (auto& c : rows("sxs|" + s)) {                                                      // its subscriptions and scripts end
        if (!take("sxs|" + s, c.instance)) continue;
        const size_t a = c.instance.find('\x1f'), b = c.instance.find('\x1f', a + 1); const std::string D = c.instance.substr(0, a), k = c.instance.substr(a + 1, b - a - 1);
        if (k.compare(0, 7, "script:") == 0) script_stop(k.substr(7)); else sub_release(D, k); }
    std::vector<Cell> mine = rows("sx|" + s);
    for (auto& c : mine) { const std::string k = c.instance; const size_t a = k.find('\x1f'), b = k.find('\x1f', a + 1);
        if (b == std::string::npos) { continue; }
        const std::string D = k.substr(0, a), apn = k.substr(a + 1, b - a - 1), name = k.substr(b + 1);
        if (cas("e|" + D + "|" + name, apn, [&](const std::string& cur, std::string& nx) { if (cur.empty() || Json::parse(cur)["sess"].s != s) return false; nx = ""; return true; })) bump(D, name); }
    for (auto& c : mine) { const std::string k = c.instance; const size_t a = k.find('\x1f');
        if (k.find('\x1f', a + 1) != std::string::npos) { gone("sx|" + s, k); continue; }
        depart(k.substr(0, a), k.substr(a + 1), s, true);
        gone("sx|" + s, k); }
}

namespace {
std::string hmac_hex(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; }
    return a;
}
}  // namespace
