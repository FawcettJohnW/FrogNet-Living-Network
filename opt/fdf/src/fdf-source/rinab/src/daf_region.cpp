// daf_region.cpp -- [DAF_MODEL_V1] John Day's distributed-application model (RINA Reference Model, Part 2, Chapters 1
// and 2) as a region of the FD&F RAM host: op=daf. Plan: "The RINA DAF Model on Ribbit: A Prototype Plan" (2026-10-16).
//
// [DAF_FROG_V1] Written as a lockless multithreaded program over one memory ("think like a frog"):
//   - One region holds each DAF; every member reads and writes it. No lock, no replication, no reconciliation.
//   - Every mutable truth has an owner, and only its owner writes it. A member owns its enrollment, its entries, its
//     inbox items, its requests, its subscriptions; a session owns its connections; a creator owns what it created.
//   - Where several parties say something about one thing (a policy, a persistent entry, a designation, a name's
//     rule), each says it in its own record and a reader folds them: the newest says.
//   - A decision several may race for (a DAF, a name, a member name, a synonym, a nonce, an answer) is a set of single
//     tuples, one per claim, each its claimant's own, read in write order: the first claimant wins, and every reader
//     folds the same tuples to the same answer.
//     Nothing is compare-and-retried; nobody's write is undone.
//   - Nothing computed is stored as a collection inside one record: a subscription or a script is a stored request,
//     and its answer is read from the members' own records when it is asked for.
//   - No counter records: an id is a number pre-allocated from the memory's write clock.
//   - Holding a read is a continuation parked on the variables that can change its answer, answered by their writers.
//   - A partition is not merged: a member cut off loses its session and leaves; on its return it re-enrolls.
//
// Records (variable, instance) -> JSON text; "" is a record that is gone. D = "<dan>#<daii>". "w" below is the
// writer's member name (or a session); "\x1f" separates an instance's parts.
//   dafs             <D>                 the DAF (its creator's)                                             (Permanent)
//   nl|<D>           <name>              a name's lifetime (its first definer's)                             (Permanent)
//   n|<D>            <name>\x1f<w>       a name's rule, as w says it                                         (Permanent)
//   des|<D>          <name>\x1f<w>       the member a designated name designates, as w says it               (Permanent)
//   e|<D>|<name>     <apn>\x1f<w>        an entry in the name: <apn> itself for a session entry; for a persistent
//                                        name, whoever admitted or removed it                     (Permanent if so)
//   m|<D>            <apn>               an enrolled member (the session holding the name writes it)
//   ret|<D>          <apn>               a member that left: its synonyms and role, until `until` (short rejoin)
//   pol|<D>          <key>\x1f<w>        a policy, as w set it                                               (Permanent)
//   ref|<D>          <key>\x1f<w>        reference data                                                      (Permanent)
//   audit|<D>        <n>                 one record per check-point decision (n: a pre-allocated number)
//   sx|<sid> ...                         what a session holds (gone with the session)
//   #tok|<D>, #auth, #owner              secrets: never answered by any operation
//   c|<what>         <n>                 claims, one tuple each, read in write order: c|dafs|D, c|nl|D|name, c|m|D|apn,
//                                        c|s|D|set|syn, c|nonce|D|n, c|ans|D|rid, c|stk|D|rid, ...
#include "rina_region.h"
#include "daf_memory.hpp"
#include "frogram.hpp"
#include "fdf_version.hpp"
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
std::string nows() { return std::to_string(now()); }
std::string err(const std::string& code, const std::string& why) { return "{\"ok\":false,\"error\":" + Json::quote(code) + ",\"why\":" + Json::quote(why) + "}"; }
struct Refuse { std::string code, why; };
[[noreturn]] void refuse(const std::string& code, const std::string& why) { throw Refuse{code, why}; }
const char SEP = '\x1f';
std::string cell(const std::string& key, const std::string& writer) { return key + SEP + writer; }

// ---- [DAF_FILE_V1] Permanent records: appended with their id and fdatasync'd before they are published (each by its
// one writer, so ids rise in publication order); replayed at start, where the highest id of each record wins
class DafFile {
public:
    void open_and_replay() {
        const char* f = getenv("RINA_DAF_FILE"); const char* r = getenv("RINA_REGION_FILE");
        path_ = (f && *f) ? f : (r && *r) ? std::string(r) + ".daf" : "";
        if (path_.empty()) { fprintf(stderr, "rina-host: [DAF_FILE_V1] no RINA_DAF_FILE or RINA_REGION_FILE: the DAF region does not start\n"); _exit(2); }
        struct L { std::string bag; uint64_t id; };
        std::map<std::pair<std::string, std::string>, L> last;
        if (FILE* in = fopen(path_.c_str(), "r")) {
            char* line = nullptr; size_t cap = 0; ssize_t n; size_t no = 0;
            while ((n = getline(&line, &cap, in)) > 0) { ++no; Json j;
                try { j = Json::parse(std::string(line, size_t(n))); } catch (...) { fprintf(stderr, "rina-host: [DAF_FILE_V1] %s line %zu is damaged\n", path_.c_str(), no); _exit(2); }
                if (j.type != Json::Arr || j.a.size() < 4) { fprintf(stderr, "rina-host: [DAF_FILE_V1] %s line %zu is not a record with an id\n", path_.c_str(), no); _exit(2); }
                const L l{j[2].s, uint64_t(j[3].n)}; auto it = last.find({j[0].s, j[1].s});
                if (it == last.end() || it->second.id < l.id) last[{j[0].s, j[1].s}] = l; }
            free(line); fclose(in);
        } else if (errno != ENOENT) { fprintf(stderr, "rina-host: [DAF_FILE_V1] reading %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        const std::string tmp = path_ + ".new"; FILE* out = fopen(tmp.c_str(), "w");
        if (!out) { fprintf(stderr, "rina-host: [DAF_FILE_V1] writing %s: %s\n", tmp.c_str(), strerror(errno)); _exit(2); }
        size_t kept = 0;
        for (auto& [k, l] : last) { if (l.bag.empty()) continue;                 // a deletion: gone for good
            dmem::restore(k.first, k.second, l.bag, l.id); fputs(line_of(k.first, k.second, l.bag, l.id).c_str(), out); ++kept; }
        dmem::restored();                                                     // everything restored is published
        if (fflush(out) != 0 || fdatasync(fileno(out)) != 0 || fclose(out) != 0 || rename(tmp.c_str(), path_.c_str()) != 0) {
            fprintf(stderr, "rina-host: [DAF_FILE_V1] compacting %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fd_ = ::open(path_.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC | O_CREAT, 0600);
        if (fd_ < 0) { fprintf(stderr, "rina-host: [DAF_FILE_V1] opening %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fprintf(stderr, "rina-host: [DAF_FILE_V1] %zu Permanent DAF record(s) restored from %s ([DAF_MODEL_V1] [DAF_FROG_V1] op=daf, build %s)\n", kept, path_.c_str(), FDF_BUILD_TAG);
    }
    // one write() per line (O_APPEND: whole lines, never interleaved), then fdatasync; a failure is said and fatal
    void append(const std::string& v, const std::string& i, const std::string& bag, uint64_t id) {
        const std::string l = line_of(v, i, bag, id);
        if (::write(fd_, l.data(), l.size()) != ssize_t(l.size()) || fdatasync(fd_) != 0) {
            fprintf(stderr, "rina-host: [DAF_FILE_V1] could not make a Permanent record durable in %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
    }
private:
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
uint64_t put(const std::string& v, const std::string& i, const std::string& bag) { return dmem::put(v, i, bag); }        // by its owner
void gone(const std::string& v, const std::string& i) { dmem::put(v, i, ""); }
uint64_t pput(const std::string& v, const std::string& i, const std::string& bag) {                                       // permanent, by its owner
    return dmem::put(v, i, bag, [&](uint64_t id) { dfile().append(v, i, bag, id); }); }
// several writers about one key, each in its own record: the newest says. A "cleared" or "removed" record says "none".
bool says(const std::string& v, const std::string& key, Json& out, uint64_t* id = nullptr) {
    const std::string pre = key + SEP; uint64_t best = 0; std::string bag;
    for (auto& r : dmem::rows(v)) if (r.instance.compare(0, pre.size(), pre) == 0 && r.id > best) { best = r.id; bag = r.bag; }
    if (!best) return false;
    out = Json::parse(bag); if (id) *id = best;
    return !out["cleared"].b && !out["removed"].b;
}
std::map<std::string, Cell> all_say(const std::string& v) {                       // every key: what its newest writer says
    std::map<std::string, Cell> best;
    for (auto& r : dmem::rows(v)) { const std::string key = r.instance.substr(0, r.instance.find(SEP));
        auto it = best.find(key); if (it == best.end() || it->second.id < r.id) best[key] = Cell{key, r.id, Json::parse(r.bag)}; }
    for (auto it = best.begin(); it != best.end();) { if (it->second.bag["cleared"].b || it->second.bag["removed"].b) it = best.erase(it); else ++it; }
    return best;
}
std::string sess() { return std::to_string((unsigned long long)rina_session_id()); }
// [DAF_PREALLOCATED_NUMBERS_V1] an id no one else will get: a number pre-allocated from the memory's clock. No counter
// record is kept, read or rewritten.
std::string own_id() { return std::to_string((unsigned long long)dmem::number()); }
// ---- [DAF_CLAIMS_AS_TUPLES_V1] claims. A contested key is a variable of single tuples: each claim (or release) is its
// claimant's own tuple, and its write id is its place in line. claims_upto() reads them in write order, after waiting
// until every write up to `upto` is published, so every reader sees the same line.
uint64_t claim(const std::string& key, const std::string& actor, const std::string& bag) { (void)actor; return put(key, own_id(), bag); }
std::vector<dmem::Row> claims_upto(const std::string& key, uint64_t upto) {
    if (upto) dmem::wait_published(upto); else upto = dmem::published().load(std::memory_order_acquire);
    std::vector<dmem::Row> rs; for (auto& r : dmem::rows(key)) if (r.id <= upto) rs.push_back(r);
    std::sort(rs.begin(), rs.end(), [](const dmem::Row& a, const dmem::Row& b) { return a.id < b.id; });
    return rs;
}
// the first claimant wins: true if this caller's claim is the first in line
bool claim_first(const std::string& key, const std::string& actor, const std::string& bag) {
    const uint64_t id = claim(key, actor, bag); auto rs = claims_upto(key, id); return !rs.empty() && rs[0].id == id; }

// ---- the DAF
struct Daf { std::string D; Json j; };
Daf daf_of(const Json& in) {
    const std::string D = in["daf"].s; if (D.empty()) refuse("EINVAL", "no daf named");
    Daf d{D, {}}; if (!get("dafs", D, d.j)) refuse("ENOENT", "not found"); return d;   // [DAF_NOT_FOUND_IS_ONE_ANSWER_V1]
}
struct Who { std::string apn, role; Json mem; };
Who caller(const std::string& D) {
    for (auto& c : rows("sx|" + sess())) { const std::string k = c.instance; const size_t b = k.find(SEP);
        if (b == std::string::npos || k.compare(0, b, D) != 0 || k.find(SEP, b + 1) != std::string::npos) { continue; }
        Who w; w.apn = k.substr(b + 1);
        // the member's own session, or a further connection it opened on another session ([DAF_MORE_CONNECTIONS_V1])
        if (get("m|" + D, w.apn, w.mem) && (w.mem["sess"].s == sess() || c.bag["kind"].s == "conn")) { w.role = w.mem["role"].s; return w; } }
    return Who{};
}

// ---- [DAF_CHECK_POINT_V1] (plan H) every operation asks here first. The most specific policy decides:
// name:<object>, then op:<op>, then role:<role>, then *. None: open. Every decision is recorded, by its session.
void record_audit(const std::string& D, const Who& w, const std::string& op, const std::string& object, bool allow, const std::string& why) {
    put("audit|" + D, own_id(), "{\"t\":" + nows() + ",\"apn\":" + Json::quote(w.apn) + ",\"role\":" + Json::quote(w.role) +
        ",\"sess\":" + Json::quote(sess()) + ",\"op\":" + Json::quote(op) + ",\"object\":" + Json::quote(object) + ",\"decision\":" +
        (allow ? "\"allow\"" : "\"refuse\"") + ",\"why\":" + Json::quote(why) + "}");
}
bool check(const std::string& D, const Who& w, const std::string& op, const std::string& object, std::string& why) {
    Json p; std::string key;
    const std::string keys[] = {"name:" + object, "op:" + op, "role:" + w.role, "*"};
    for (auto& k : keys) { if (k == "name:" || k == "role:") continue; if (says("pol|" + D, k, p)) { key = k; break; } }
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
    // [DAF_NOT_FOUND_IS_ONE_ANSWER_V1] a session that is not a member gets the answer a DAF that does not exist gets
    if (w.apn.empty() && op != "enroll" && op != "request") { record_audit(D, w, op, object, false, "not enrolled"); refuse("ENOENT", "not found"); }
    std::string why; if (!check(D, w, op, object, why)) refuse(why.compare(0, 9, "redirect:") == 0 ? "EREDIRECT" : "EPERM", why);
    if (g_in && !(*g_in)["ae"].s.empty()) {
        Json g; if (!get("ae|" + D + "|" + sess(), (*g_in)["ae"].s, g)) refuse("ENOTCONN", "no connection " + (*g_in)["ae"].s + " on this session");
        if (!within(g["objects"], object) && !check(D, w, "outside_object_set", (*g_in)["ae"].s + ":" + object, why)) refuse("EPERM", why + " (" + object + " is outside the object set of " + (*g_in)["ae"].s + ")");
    }
}

// ---- section A: DAFs, names, synonyms
const char* BUILTIN[][3] = {{"members", "all", "session"}, {"join", "designated", "session"}, {"service", "one", "session"}};
std::string create_daf(const Json& in) {
    const std::string dan = in["dan"].s, daii = in["daii"].s.empty() ? "1" : in["daii"].s;
    if (dan.empty() || dan.find('#') != std::string::npos || daii.find('#') != std::string::npos) refuse("EINVAL", "a DAF needs a dan (no '#')");
    const std::string D = dan + "#" + daii;
    Json old; if (get("dafs", D, old)) refuse("EEXIST", "DAF " + D + " exists");
    std::string sets = "[\"addr\""; for (auto& s : in["synsets"].a) if (s.s != "addr") sets += "," + Json::quote(s.s); sets += "]";
    const double life = in["syn_lifetime_s"].type == Json::Num ? in["syn_lifetime_s"].n : 300;
    const std::string bag = "{\"dan\":" + Json::quote(dan) + ",\"daii\":" + Json::quote(daii) + ",\"owner\":" + Json::quote(in["owner"].s) +
        ",\"synsets\":" + sets + ",\"syn_lifetime_s\":" + dump(num(life)) + ",\"created\":" + nows() + "}";
    if (!claim_first("c|dafs|" + D, sess(), bag)) refuse("EEXIST", "DAF " + D + " exists");     // the first claimant creates it
    // its three whatevercast names exist from the start (Part 2-1 4.2), written by the DAF's creator; the DAF's own
    // record is written last: the DAF exists, for everyone, once it is whole
    for (auto& b : BUILTIN) { pput("nl|" + D, b[0], std::string("{\"lifetime\":\"") + b[2] + "\",\"builtin\":true}");
                              pput("n|" + D, cell(b[0], "daf"), std::string("{\"rule\":\"") + b[1] + "\"}"); }
    pput("dafs", D, bag);
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + "}";
}
std::string list_dafs(const Json& in) {
    std::string out = "{\"ok\":true,\"dafs\":["; bool f = true;
    for (auto& c : rows("dafs")) if (in["dan"].s.empty() || c.bag["dan"].s == in["dan"].s) { out += (f ? "" : ",") + std::string("{\"daf\":") + Json::quote(c.instance) + ",\"info\":" + dump(c.bag) + "}"; f = false; }
    return out + "]}";
}
// [DAF_SYNONYM_CLAIMS_V1] a synonym slot's holder, folded from its claim log up to `upto` (0: all of it). A claim takes
// the slot if it is free, already the claimant's, or released and past its reservation AT THE CLAIM'S OWN TIME (so every
// reader folds the same log to the same holder, whenever it reads). A release reserves it until `u`.
struct SlotState { std::string holder; bool released = false; double until = 0; };
SlotState slot_state(const std::string& key, uint64_t upto) {
    SlotState s;
    for (auto& c : claims_upto(key, upto)) { Json e = Json::parse(c.bag); const std::string a = e["a"].s;
        if (e["k"].s == "c") { if (s.holder.empty() || s.holder == a || (s.released && s.until < e["t"].n)) { s.holder = a; s.released = false; } }
        else if (a == s.holder) { s.released = true; s.until = e["u"].n; } }
    return s;
}
bool take_slot(const std::string& key, const std::string& apn) {
    const uint64_t n = claim(key, sess(), "{\"k\":\"c\",\"a\":" + Json::quote(apn) + ",\"t\":" + nows() + "}");
    return slot_state(key, n).holder == apn;
}
std::string assign_synonym(const std::string& D, const std::string& set, const std::string& apn) {
    for (unsigned n = 1; ; ++n) {
        const std::string syn = (set == "addr" ? "a" : set.substr(0, 1)) + std::to_string(n), key = "c|s|" + D + "|" + set + "|" + syn;
        const SlotState s = slot_state(key, 0);                        // looks taken: move on without claiming it
        if (!s.holder.empty() && s.holder != apn && !(s.released && s.until < now())) continue;
        if (take_slot(key, apn)) return syn;
    }
}

// ---- section B: whatevercast names
struct Name { std::string rule, lifetime, designated; bool builtin = false; };
Name name_of(const std::string& D, const std::string& name) {
    Json l; if (!get("nl|" + D, name, l)) refuse("ENOENT", "no name " + name + " in " + D);
    Name n; n.lifetime = l["lifetime"].s; n.builtin = l["builtin"].b;
    Json r; if (says("n|" + D, name, r)) n.rule = r["rule"].s;
    Json d; if (says("des|" + D, name, d)) n.designated = d["apn"].s;
    return n;
}
std::string define_name(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s;
    if (name.empty() || name.find('|') != std::string::npos || name.find(SEP) != std::string::npos) refuse("EINVAL", "a name needs text without '|'");
    must(d.D, w, "define_name", name);
    const std::string rule = in["rule"].s.empty() ? "all" : in["rule"].s, life = in["lifetime"].s.empty() ? "session" : in["lifetime"].s;
    if (rule != "all" && rule != "one" && rule != "designated" && rule != "nearest") refuse("EINVAL", "rule is all, one, designated or nearest");
    if (life != "session" && life != "persistent") refuse("EINVAL", "lifetime is session or persistent");
    // the lifetime is the first definer's (a claim); the rule is whoever said it last (each definer in its own record)
    Json l; std::string fixed;
    if (get("nl|" + d.D, name, l)) fixed = l["lifetime"].s;
    else { const std::string key = "c|nl|" + d.D + "|" + name;
        const uint64_t id = claim(key, sess(), "{\"lifetime\":" + Json::quote(life) + "}"); auto line = claims_upto(key, id);
        fixed = Json::parse(line[0].bag)["lifetime"].s;                // the first definer's
        if (line[0].id == id) pput("nl|" + d.D, name, "{\"lifetime\":" + Json::quote(life) + "}"); }
    if (fixed != life) refuse("EEXIST", "name " + name + " exists with lifetime " + fixed);
    pput("n|" + d.D, cell(name, w.apn), "{\"rule\":" + Json::quote(rule) + "}");
    return "{\"ok\":true}";
}
std::string join(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; Name n = name_of(d.D, name);
    const std::string apn = in["apn"].s.empty() ? w.apn : in["apn"].s;
    if (apn.empty()) refuse("ENOENT", "not found");
    must(d.D, w, apn == w.apn ? "join" : "admit", name);
    const bool persistent = n.lifetime == "persistent";
    if (!persistent && apn != w.apn) refuse("EINVAL", "a session name is joined by the member itself, over its own session");
    const std::string facts = in["facts"].type == Json::Obj ? dump(in["facts"]) : "{}";
    std::string bag = "{\"apn\":" + Json::quote(apn) + ",\"facts\":" + facts + ",\"ref\":" + Json::quote(in["ref"].s) + ",\"t\":" + nows();
    if (persistent) pput("e|" + d.D + "|" + name, cell(apn, w.apn), bag + ",\"p\":1,\"by\":" + Json::quote(w.apn) + "}");
    else { put("e|" + d.D + "|" + name, cell(apn, apn), bag + ",\"sess\":" + Json::quote(sess()) + "}");    // the member's own entry
           put("sx|" + sess(), d.D + SEP + apn + SEP + name, "{\"kind\":\"entry\"}"); }
    return "{\"ok\":true}";
}
struct Entry { std::string apn, ref; Json facts; bool live; double key = 0; };
std::vector<Entry> members_of(const std::string& D, const std::string& name) {
    std::vector<Entry> out;
    for (auto& [apn, c] : all_say("e|" + D + "|" + name)) { Json m; const bool live = get("m|" + D, apn, m); out.push_back(Entry{apn, c.bag["ref"].s, c.bag["facts"], live}); }
    return out;                                                          // all_say is ordered by apn
}
std::string unjoin(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; Name n = name_of(d.D, name);
    const std::string apn = in["apn"].s.empty() ? w.apn : in["apn"].s;
    must(d.D, w, apn == w.apn ? "leave_name" : "remove", name);
    Json e; if (!says("e|" + d.D + "|" + name, apn, e)) refuse("ENOENT", apn + " is not in " + name);
    if (n.lifetime == "persistent") pput("e|" + d.D + "|" + name, cell(apn, w.apn), "{\"removed\":true,\"t\":" + nows() + ",\"by\":" + Json::quote(w.apn) + "}");
    else gone("e|" + d.D + "|" + name, cell(apn, apn));
    return "{\"ok\":true}";
}
std::string designate(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; name_of(d.D, name);
    must(d.D, w, "designate", name);
    pput("des|" + d.D, cell(name, w.apn), "{\"apn\":" + Json::quote(in["apn"].s) + "}");
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
// a name's version: the newest change to its entries, or to the enrollment of a member it lists (gone records included)
uint64_t name_rev(const std::string& D, const std::string& name) {
    uint64_t r = 0; std::set<std::string> in_name;
    for (auto& e : dmem::rows("e|" + D + "|" + name, "", 0, true)) { r = std::max(r, e.id); in_name.insert(e.instance.substr(0, e.instance.find(SEP))); }
    for (auto& m : dmem::rows("m|" + D, "", 0, true)) if (in_name.count(m.instance)) r = std::max(r, m.id);
    return r;
}
// [DAF_RESOLVE_V1] a name, resolved by its rule (or the caller's), against the members as they are now
std::string resolve_core(const std::string& D, const Json& in, std::string* chosen) {
    const std::string name = in["name"].s; Name n = name_of(D, name);
    const std::string rule = in["rule"].s.empty() ? n.rule : in["rule"].s;
    const uint64_t rev = name_rev(D, name);                     // read first: the members below are at least this new
    std::vector<Entry> sel; const bool live_only = in["live"].b;
    for (auto& e : members_of(D, name)) {
        if (live_only && !e.live) continue;
        bool ok = true; for (auto& c : in["filter"].a) if (!pass(e.facts, c)) { ok = false; break; }
        if (ok) sel.push_back(e);
    }
    if (rule == "designated") { std::vector<Entry> d; for (auto& e : sel) if (e.apn == n.designated) d.push_back(e); sel = d; }
    else if (rule == "one" || rule == "nearest") {
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
    for (auto& c : rows("nl|" + d.D)) { Name n = name_of(d.D, c.instance);
        out += (f ? "" : ",") + std::string("{\"name\":") + Json::quote(c.instance) + ",\"settings\":{\"rule\":" + Json::quote(n.rule) + ",\"lifetime\":" + Json::quote(n.lifetime) +
               ",\"designated\":" + Json::quote(n.designated) + (n.builtin ? ",\"builtin\":true" : "") + "}}"; f = false; }
    return out + "]}";
}

// ---- section H: policies and the audit
std::string set_policy(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string key = in["key"].s.empty() ? "*" : in["key"].s, mode = in["mode"].s;
    if (mode != "open" && mode != "refuse" && mode != "roles" && mode != "redirect") refuse("EINVAL", "mode is open, refuse, roles or redirect");
    must(d.D, w, "set_policy", key);
    if (mode == "open" && in["clear"].b) pput("pol|" + d.D, cell(key, w.apn), "{\"cleared\":true}");
    else pput("pol|" + d.D, cell(key, w.apn), "{\"mode\":" + Json::quote(mode) + ",\"roles\":" + (in["roles"].type == Json::Arr ? dump(in["roles"]) : "[]") +
              ",\"to\":" + (in["to"].type == Json::Obj ? dump(in["to"]) : "null") + "}");
    return "{\"ok\":true}";
}
std::string audit(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "audit", d.D); const double after = in["after"].n;
    std::vector<std::pair<uint64_t, std::string>> rs;
    for (auto& c : rows("audit|" + d.D)) if (double(c.id) > after) rs.push_back({c.id, dump(c.bag)});
    std::sort(rs.begin(), rs.end());
    std::string out = "{\"ok\":true,\"records\":["; bool f = true;
    for (auto& [n, b] : rs) { out += (f ? "" : ",") + std::string("{\"n\":") + std::to_string((unsigned long long)n) + ",\"r\":" + b + "}"; f = false; }
    return out + "]}";
}

// ---- enrollment (Part 2-1 5.5; Enrollment specs)
// [DAF_MEMBER_CLAIMS_V1] who holds a member name: its claim log folded up to `upto`. A session's claim takes the name if
// no session holds it (or it already does); a release by the holding session frees it.
std::string member_holder(const std::string& key, uint64_t upto) {
    std::string holder;
    for (auto& c : claims_upto(key, upto)) { Json e = Json::parse(c.bag);
        if (e["k"].s == "c") { if (holder.empty() || holder == e["s"].s) holder = e["s"].s; }
        else if (holder == e["s"].s) holder.clear(); }
    return holder;
}
std::string enroll(const Json& in) {
    Daf d = daf_of(in); const std::string apn = in["apn"].s, role = in["role"].s.empty() ? "member" : in["role"].s;
    if (apn.empty() || apn.find(SEP) != std::string::npos) refuse("EINVAL", "enroll needs an apn");
    Who w; w.apn = apn; w.role = role;
    // 1) state: new, returning within its synonym lifetime, or returning after it
    Json ret; const bool back = get("ret|" + d.D, apn, ret), shortr = back && ret["until"].n > now() && ret["role"].s == role;
    // 2) role and capabilities: the role asked for, at the check point
    must(d.D, w, "enroll", role);
    // the member name is held by one session: this session claims it, and holds it if it is first in line
    const std::string mkey = "c|m|" + d.D + "|" + apn;
    const uint64_t mine = claim(mkey, sess(), "{\"k\":\"c\",\"s\":" + Json::quote(sess()) + "}");
    if (member_holder(mkey, mine) != sess()) refuse("EEXIST", apn + " is enrolled on another session");
    // 3) synonyms: one from each set; on a short rejoin its own again, unless someone live took it meanwhile
    std::string syn = "{"; bool f = true;
    for (auto& s : d.j["synsets"].a) {
        std::string v;
        if (shortr && ret["syn"][s.s].type == Json::Str && take_slot("c|s|" + d.D + "|" + s.s + "|" + ret["syn"][s.s].s, apn)) v = ret["syn"][s.s].s;
        if (v.empty()) v = assign_synonym(d.D, s.s, apn);
        syn += (f ? "" : ",") + Json::quote(s.s) + ":" + Json::quote(v); f = false; }
    syn += "}";
    const std::string facts = in["facts"].type == Json::Obj ? dump(in["facts"]) : "{}";
    const uint64_t inst = put("m|" + d.D, apn, "{\"apn\":" + Json::quote(apn) + ",\"role\":" + Json::quote(role) + ",\"facts\":" + facts + ",\"syn\":" + syn +
                              ",\"sess\":" + Json::quote(sess()) + ",\"t\":" + nows() + "}");
    put("sx|" + sess(), d.D + SEP + apn, "{\"kind\":\"member\",\"inst\":" + std::to_string(inst) + "}");
    // [DAF_MORE_CONNECTIONS_V1] a token for the member's further connections (Part 2-1 5.4.2); new at every enrollment
    unsigned char rnd[16]; { FILE* u = fopen("/dev/urandom", "rb"); if (!u || fread(rnd, 1, 16, u) != 16) { if (u) fclose(u); refuse("EIO", "no randomness for a token"); } fclose(u); }
    static const char hx[] = "0123456789abcdef"; std::string token; for (unsigned char c : rnd) { token += hx[c >> 4]; token += hx[c & 15]; }
    put("#tok|" + d.D, apn, "{\"token\":" + Json::quote(token) + ",\"inst\":" + std::to_string(inst) + "}");
    if (back) gone("ret|" + d.D, apn);
    put("e|" + d.D + "|members", cell(apn, apn), "{\"apn\":" + Json::quote(apn) + ",\"facts\":" + facts + ",\"ref\":" + Json::quote(in["ref"].s) + ",\"t\":" + nows() + ",\"sess\":" + Json::quote(sess()) + "}");
    put("sx|" + sess(), d.D + SEP + apn + SEP + "members", "{\"kind\":\"entry\"}");
    // 4) static and near-static data, skipped on a short rejoin
    std::string refd = "[";
    if (!shortr) { bool g = true; for (auto& [k, c] : all_say("ref|" + d.D)) { refd += (g ? "" : ",") + std::string("{\"key\":") + Json::quote(k) + ",\"v\":" + dump(c.bag) + "}"; g = false; } }
    refd += "]";
    // [DAF_REQUEST_V1] what was stored for this member while it was away: each handed over once (the first taker)
    std::string stored = "["; { bool g = true; for (auto& c : rows("store|" + d.D + "|" + apn)) { if (!claim_first("c|stk|" + d.D + "|" + c.instance, sess(), "{}")) continue;
        stored += (g ? "" : ",") + std::string("{\"rid\":") + Json::quote(c.instance) + ",\"m\":" + dump(c.bag) + "}"; g = false; } }
    stored += "]";
    // 6) the state the member continues from: its names and the write it holds reads from
    std::string ns = "["; f = true; for (auto& c : rows("nl|" + d.D)) { ns += (f ? "" : ",") + Json::quote(c.instance); f = false; } ns += "]";
    return "{\"ok\":true,\"daf\":" + Json::quote(d.D) + ",\"apn\":" + Json::quote(apn) + ",\"inst\":" + std::to_string(inst) + ",\"from\":" + std::to_string(inst) + ",\"token\":" + Json::quote(token) + ",\"role\":" + Json::quote(role) +
           ",\"state\":" + (shortr ? "\"short\"" : back ? "\"returning\"" : "\"new\"") + ",\"syn\":" + syn + ",\"static\":" + (shortr ? "\"kept\"" : "\"sent\"") +
           ",\"ref\":" + refd + ",\"stored\":" + stored + ",\"names\":" + ns + ",\"daf_info\":" + dump(d.j) + "}";
}
void answer_gone(const std::string& D, const std::string& rid, const std::string& actor) {      // the first answer wins
    if (claim_first("c|ans|" + D + "|" + rid, actor, "{}")) put("ans|" + D, rid, "{\"ok\":false,\"error\":\"gone\",\"why\":\"the member serving the request left before answering\"}"); }
// a member's leaving, by its own word or its session's end; only the session holding the member makes it leave
void depart(const std::string& D, const std::string& apn, const std::string& s, bool keep, const std::string& actor) {
    Json m; if (!get("m|" + D, apn, m) || m["sess"].s != s) return;
    Json d; get("dafs", D, d); const double until = now() + d["syn_lifetime_s"].n;
    if (keep) put("ret|" + D, apn, "{\"syn\":" + dump(m["syn"]) + ",\"role\":" + Json::quote(m["role"].s) + ",\"until\":" + std::to_string(until) + "}");
    for (auto& [set, v] : m["syn"].o) claim("c|s|" + D + "|" + set + "|" + v.s, actor, "{\"k\":\"r\",\"a\":" + Json::quote(apn) + ",\"u\":" + std::to_string(keep ? until : 0) + "}");
    gone("m|" + D, apn);
    claim("c|m|" + D + "|" + apn, actor, "{\"k\":\"r\",\"s\":" + Json::quote(s) + "}");
    for (auto& c : rows("pend|" + D + "|" + apn)) answer_gone(D, c.instance, actor);   // [DAF_REQUEST_V1] an error, never silence
}
std::string leave(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); if (w.apn.empty()) refuse("ENOENT", "not found");
    must(d.D, w, "leave", w.apn);
    const std::string pre = d.D + SEP + w.apn + SEP;
    for (auto& c : rows("sx|" + sess())) if (c.instance.compare(0, pre.size(), pre) == 0) {
        gone("e|" + d.D + "|" + c.instance.substr(pre.size()), cell(w.apn, w.apn)); gone("sx|" + sess(), c.instance); }
    gone("sx|" + sess(), d.D + SEP + w.apn);
    depart(d.D, w.apn, sess(), false, sess());
    return "{\"ok\":true}";
}
std::string set_ref(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_ref", in["key"].s);
    if (in["key"].s.empty()) refuse("EINVAL", "set_ref needs a key");
    pput("ref|" + d.D, cell(in["key"].s, w.apn), in["v"].type == Json::Obj ? dump(in["v"]) : "{\"v\":" + dump(in["v"]) + "}");
    return "{\"ok\":true}";
}
std::string whoami(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D);
    if (w.apn.empty()) return "{\"ok\":true,\"enrolled\":false}";
    return "{\"ok\":true,\"enrolled\":true,\"member\":" + dump(w.mem) + "}";
}
std::string synonym(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string set = in["set"].s.empty() ? "addr" : in["set"].s;
    must(d.D, w, "synonym", set);
    const SlotState s = slot_state("c|s|" + d.D + "|" + set + "|" + in["syn"].s, 0);
    if (s.holder.empty()) refuse("ENOENT", "no synonym " + in["syn"].s + " in set " + set + " of " + d.D);
    Json m; if (s.released || !get("m|" + d.D, s.holder, m) || m["syn"][set].s != in["syn"].s) refuse("ENOENT", "synonym " + in["syn"].s + " is held for a member who is away");
    return "{\"ok\":true,\"apn\":" + Json::quote(s.holder) + "}";
}
std::string members(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "members", d.D); std::string out = "{\"ok\":true,\"members\":["; bool f = true;
    for (auto& c : rows("m|" + d.D)) { out += (f ? "" : ",") + dump(c.bag); f = false; }
    return out + "]}";
}

// ---- section D: Application-Entities and connections (Part 2-1 5.4)
std::string hmac_hex(const std::string& key, const std::string& msg);
std::string set_auth(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_auth", in["module"].s);
    const std::string mod = in["module"].s; if (mod != "null" && mod != "key") refuse("EINVAL", "module is null or key");
    put("#auth", cell(d.D, w.apn), "{\"module\":" + Json::quote(mod) + ",\"key\":" + Json::quote(in["key"].s) + "}");
    return "{\"ok\":true}";
}
bool authenticate(const std::string& D, const std::string& ae, const std::string& apn, const Json& a, std::string& why) {
    Json au; if (!says("#auth", D, au) || au["module"].s == "null") { why = "null"; return true; }
    const std::string key = au["key"].s, nonce = a["nonce"].s;
    if (nonce.size() < 8) { why = "key: a nonce of 8 or more characters is needed"; return false; }
    if (a["proof"].s != hmac_hex(key, D + "|" + ae + "|" + apn + "|" + nonce)) { why = "key: the proof does not match"; return false; }
    if (!claim_first("c|nonce|" + D + "|" + nonce, sess(), "{}")) { why = "key: the nonce was used before"; return false; }   // its first use
    why = "key"; return true;
}
std::string connect(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string ae = in["ae_name"].s;
    if (ae.empty()) refuse("EINVAL", "connect names an Application-Entity (ae_name)");
    if (w.apn.empty() && !in["apn"].s.empty()) {           // [DAF_MORE_CONNECTIONS_V1] another session of an enrolled member
        Json t, m;
        if (!get("#tok|" + d.D, in["apn"].s, t) || t["token"].s != in["token"].s || !get("m|" + d.D, in["apn"].s, m)) refuse("EACCES", "not a connection of an enrolled member: wrong apn or token");
        put("sx|" + sess(), d.D + SEP + in["apn"].s, "{\"kind\":\"conn\"}");
        w = caller(d.D);
    }
    if (w.apn.empty()) refuse("ENOENT", "not found");
    std::string why; if (!authenticate(d.D, ae, w.apn, in["auth"], why)) { std::string rec; check(d.D, w, "connect_auth_failed", ae, rec); refuse("EACCES", "authentication failed: " + why); }
    must(d.D, w, "connect", ae);
    Json lim; std::string granted = "["; bool f = true;
    const bool limited = says("pol|" + d.D, "objects:" + w.role, lim);
    for (auto& o : in["objects"].a) { if (limited && !within(lim["objects"], o.s)) continue; granted += (f ? "" : ",") + Json::quote(o.s); f = false; }
    granted += "]";
    put("ae|" + d.D + "|" + sess(), ae, "{\"apn\":" + Json::quote(w.apn) + ",\"objects\":" + granted + ",\"auth\":" + Json::quote(why) + "}");
    put("sxa|" + sess(), d.D + SEP + ae, "{}");
    return "{\"ok\":true,\"ae\":" + Json::quote(ae) + ",\"granted\":" + granted + ",\"auth\":" + Json::quote(why) + "}";
}
std::string disconnect(const Json& in) {
    Daf d = daf_of(in); const std::string ae = in["ae_name"].s; gone("ae|" + d.D + "|" + sess(), ae); gone("sxa|" + sess(), d.D + SEP + ae);
    return "{\"ok\":true}";
}
std::string set_objects(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "set_policy", "objects:" + in["role"].s);
    pput("pol|" + d.D, cell("objects:" + in["role"].s, w.apn), "{\"mode\":\"objects\",\"objects\":" + (in["objects"].type == Json::Arr ? dump(in["objects"]) : "[]") + "}");
    return "{\"ok\":true}";
}
// [DAF_FLOWS_V1] a flow between members: its record and inbox item are its opener's; each direction's data is its
// sender's; a close is its closer's own record. The receiver waits on its inbox ("box") and the flow's data ("fd").
bool closed(const std::string& D, const std::string& fid) { const std::string pre = fid + SEP; for (auto& r : dmem::rows("flc|" + D)) if (r.instance.compare(0, pre.size(), pre) == 0) return true; return false; }
std::string flow_open(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); std::string to = in["to"].s;
    if (to.empty()) { std::string chosen; must(d.D, w, "flow_open", in["name"].s); resolve_inner(d.D, w, in, &chosen); to = chosen;
        if (to.empty()) refuse("ENOENT", "no member of " + in["name"].s + " is selected by its rule"); }
    else must(d.D, w, "flow_open", to);
    Json m; if (!get("m|" + d.D, to, m)) refuse("ENOENT", to + " is not a member now");
    const std::string fid = own_id();
    const std::string spec = in["spec"].type == Json::Obj ? dump(in["spec"]) : "{}";
    put("fl|" + d.D, fid, "{\"from\":" + Json::quote(w.apn) + ",\"to\":" + Json::quote(to) + ",\"spec\":" + spec + ",\"sess\":" + Json::quote(sess()) + "}");
    put("box|" + d.D + "|" + to, fid, "{\"from\":" + Json::quote(w.apn) + ",\"spec\":" + spec + "}");
    return "{\"ok\":true,\"fid\":" + Json::quote(fid) + ",\"to\":" + Json::quote(to) + "}";
}
std::string flow_send(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string fid = in["fid"].s;
    Json f; if (!get("fl|" + d.D, fid, f)) refuse("ENOENT", "no flow " + fid);
    if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
    if (closed(d.D, fid)) refuse("EPIPE", "flow " + fid + " is closed");
    must(d.D, w, "flow_send", "flow:" + fid);
    const std::string dir = f["from"].s == w.apn ? "f" : "r";
    const uint64_t id = put("fd|" + d.D + "|" + fid + "|" + dir, std::to_string((unsigned long long)in["seq"].n), "{\"d\":" + dump(in["data"]) + "}");
    return "{\"ok\":true,\"id\":" + std::to_string(id) + "}";
}
std::string flow_close(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string fid = in["fid"].s;
    Json f; if (!get("fl|" + d.D, fid, f)) refuse("ENOENT", "no flow " + fid);
    if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
    put("fd|" + d.D + "|" + fid + "|" + (f["from"].s == w.apn ? "f" : "r"), "end", "{\"end\":true}");   // in its own direction
    put("flc|" + d.D, cell(fid, w.apn), "{\"t\":" + nows() + "}");
    return "{\"ok\":true}";
}

// ---- section E: the RIB Daemon (Part 2-1 4.0, 2). [DAF_SUBSCRIPTIONS_V1] A subscription is a stored request, its
// holder's own record. Its answer is read from the members' own records when the holder waits: no computed collection
// is stored, and no one else writes it. A change wakes the holders of the names it touched; a periodic one is answered
// on its period.
std::string sub_data(const std::string& D, const Json& j) {
    std::string data = "{"; bool f = true;
    if (j["kind"].s == "collect") {
        for (auto& n : j["names"].a) { data += (f ? "" : ",") + Json::quote(n.s) + ":{"; f = false; bool g = true;
            for (auto& e : members_of(D, n.s)) { const Json& x = e.facts[j["fact"].s]; if (x.type == Json::Null) continue;
                data += (g ? "" : ",") + Json::quote(e.apn) + ":" + dump(x); g = false; }
            data += "}"; }
    } else {
        for (auto& n : j["names"].a) { data += (f ? "" : ",") + Json::quote(n.s) + ":["; f = false; bool g = true;
            for (auto& e : members_of(D, n.s)) { bool ok = true; for (auto& c : j["filter"].a) { if (!pass(e.facts, c)) { ok = false; break; } }
                if (!ok) { continue; }
                data += (g ? "" : ",") + std::string("{\"apn\":") + Json::quote(e.apn) + ",\"live\":" + (e.live ? "true" : "false") + ",\"facts\":" + dump(e.facts) + "}"; g = false; }
            data += "]"; }
    }
    return data + "}";
}
uint64_t names_rev(const std::string& D, const Json& names) { uint64_t r = 0; for (auto& n : names.a) r = std::max(r, name_rev(D, n.s)); return r; }
uint64_t now_ms() { return uint64_t(now() * 1000); }
std::string subscribe(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D);
    for (auto& n : in["names"].a) { must(d.D, w, "subscribe", n.s); name_of(d.D, n.s); }
    if (in["names"].a.empty()) refuse("EINVAL", "a subscription names one or more whatevercast names");
    const std::string kind = in["kind"].s.empty() ? "state" : in["kind"].s;
    if (kind != "state" && kind != "collect") refuse("EINVAL", "kind is state or collect");
    const double period = in["period_s"].type == Json::Num ? in["period_s"].n : 0;
    const std::string id = own_id();
    put("sub|" + d.D, id, "{\"kind\":" + Json::quote(kind) + ",\"names\":" + dump(in["names"]) + ",\"filter\":" + (in["filter"].type == Json::Arr ? dump(in["filter"]) : "[]") +
        ",\"fact\":" + Json::quote(in["fact"].s) + ",\"period_s\":" + dump(num(period)) + ",\"sess\":" + Json::quote(sess()) + "}");
    put("sxs|" + sess(), d.D + SEP + "sub" + SEP + id, "{}");
    const uint64_t rev = period > 0 ? now_ms() : names_rev(d.D, in["names"]);
    return "{\"ok\":true,\"sub\":" + Json::quote(id) + ",\"rev\":" + std::to_string(rev) + "}";
}
std::string unsubscribe(const Json& in) {
    Daf d = daf_of(in); const std::string id = in["sub"].s;
    Json s; if (!get("sub|" + d.D, id, s) || s["sess"].s != sess()) refuse("ENOENT", "this session holds no subscription " + id);
    gone("sub|" + d.D, id); gone("sxs|" + sess(), d.D + SEP + "sub" + SEP + id);
    return "{\"ok\":true}";
}
std::string distribute(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s; name_of(d.D, name);
    must(d.D, w, "distribute", name); if (in["key"].s.empty()) refuse("EINVAL", "distribute needs a key");
    int n = 0; for (auto& e : members_of(d.D, name)) { put("in|" + d.D + "|" + e.apn, cell(in["key"].s, w.apn), "{\"from\":" + Json::quote(w.apn) + ",\"v\":" + dump(in["value"]) + "}"); ++n; }
    return "{\"ok\":true,\"delivered\":" + std::to_string(n) + "}";
}
// [DAF_START_V1] START: a restricted script -- from a name, filter, take a fact, reduce it -- stored as its starter's
// request. Its result is computed from the members' records when it is read; a script run once keeps its one result
// as its starter's record. It answers "on change" or "every N s" waits until STOPped or its session ends.
std::string script_value(const std::string& D, const Json& j, const std::string& id) {
    const std::string agg = j["agg"].s.empty() ? "count" : j["agg"].s, f = j["fact"].s;
    double acc = 0; int n = 0; double mn = 0, mx = 0; std::string list = "["; bool first = true;
    for (auto& e : members_of(D, j["from"].s)) {
        if (j["live"].b && !e.live) continue;
        bool ok = true; for (auto& c : j["filter"].a) { if (!pass(e.facts, c)) { ok = false; break; } }
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
    return "{\"v\":" + v + ",\"n\":" + std::to_string(n) + ",\"t\":" + nows() + ",\"script\":" + Json::quote(id) + "}";
}
bool script_running(const std::string& D, const std::string& id, const Json& j) {
    if (!(j["every_s"].n > 0 || j["on_change"].b)) return false;
    const std::string pre = id + SEP; for (auto& r : dmem::rows("scrs|" + D)) if (r.instance.compare(0, pre.size(), pre) == 0) return false;
    return true;
}
// the newest script writing `out`: (id, spec), or false
bool script_for(const std::string& D, const std::string& out, std::string& id, Json& j) {
    uint64_t best = 0; for (auto& c : rows("scr|" + D)) if (c.bag["out"].s == out && c.id > best) { best = c.id; id = c.instance; j = c.bag; }
    return best != 0;
}
std::string out_value(const std::string& D, const std::string& out) {
    std::string id; Json j; if (!script_for(D, out, id, j)) return "";
    if (script_running(D, id, j)) return script_value(D, j, id);
    std::string b; return dmem::get("outs|" + D, cell(out, id), b) ? b : "";
}
std::string start(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const Json& j = in["script"];
    must(d.D, w, "start", j["out"].s);
    const std::string agg = j["agg"].s;
    if (j["from"].s.empty() || j["out"].s.empty()) refuse("EINVAL", "a script needs from and out");
    if (!agg.empty() && agg != "count" && agg != "sum" && agg != "min" && agg != "max" && agg != "avg" && agg != "list") refuse("EINVAL", "agg is count, sum, min, max, avg or list");
    name_of(d.D, j["from"].s);
    const std::string id = own_id(), v = script_value(d.D, j, id);
    std::string spec = dump(j); spec = spec.substr(0, spec.size() - 1) + (spec.size() > 2 ? "," : "") + "\"sess\":" + Json::quote(sess()) + "}";
    put("scr|" + d.D, id, spec);
    if (j["every_s"].n > 0 || j["on_change"].b) put("sxs|" + sess(), d.D + SEP + "script" + SEP + id, "{}");
    else put("outs|" + d.D, cell(j["out"].s, id), v);                          // run once: its one result, the starter's
    return "{\"ok\":true,\"id\":" + Json::quote(id) + ",\"result\":" + v + "}";
}
std::string stop(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "stop", in["id"].s);
    Json j; if (!get("scr|" + d.D, in["id"].s, j) || !script_running(d.D, in["id"].s, j)) refuse("ENOENT", "no running script " + in["id"].s);
    put("scrs|" + d.D, cell(in["id"].s, w.apn), "{\"t\":" + nows() + "}");
    return "{\"ok\":true}";
}
std::string stats(const Json& in) {
    Daf d = daf_of(in); must(d.D, caller(d.D), "stats", d.D);
    int subs = 0, scripts = 0; for (auto& c : rows("sub|" + d.D)) { (void)c; ++subs; }
    for (auto& c : rows("scr|" + d.D)) if (script_running(d.D, c.instance, c.bag)) ++scripts;
    return "{\"ok\":true,\"subscriptions\":" + std::to_string(subs) + ",\"scripts\":" + std::to_string(scripts) + "}";
}

// ---- section F: name-space management (Part 2-2 2.5)
bool owns(const std::string& D, const std::string& space, const std::string& apn, const std::string& name) {
    Json sp; if (!get("nsp|" + D, space, sp)) return false;
    if (sp["owner"].s == apn) return true;
    std::string pre = name;
    for (;;) { Json dg; if (says("dlg|" + D + "|" + space, pre, dg) && dg["to"].s == apn) return true;
        const size_t sl = pre.rfind('/'); if (sl == std::string::npos) return false; pre = pre.substr(0, sl); }
}
std::string ns_create(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s;
    if (sp.empty() || sp.find('|') != std::string::npos) refuse("EINVAL", "a space needs a name without '|'");
    must(d.D, w, "ns_create", sp);
    Json old; if (get("nsp|" + d.D, sp, old) || !claim_first("c|nsp|" + d.D + "|" + sp, sess(), "{}")) refuse("EEXIST", "space " + sp + " exists");
    pput("nsp|" + d.D, sp, "{\"owner\":" + Json::quote(w.apn) + ",\"internal\":" + (in["internal"].b ? "true" : "false") + "}");
    return "{\"ok\":true}";
}
std::string ns_delegate(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s, pre = in["prefix"].s;
    must(d.D, w, "ns_delegate", sp + ":" + pre);
    if (!owns(d.D, sp, w.apn, pre)) refuse("EPERM", w.apn + " does not own " + pre + " in " + sp);
    pput("dlg|" + d.D + "|" + sp, cell(pre, w.apn), "{\"to\":" + Json::quote(in["to"].s) + ",\"by\":" + Json::quote(w.apn) + "}");
    return "{\"ok\":true}";
}
std::string ns_assign(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string sp = in["space"].s, name = in["name"].s;
    must(d.D, w, "ns_assign", sp + ":" + name);
    if (!owns(d.D, sp, w.apn, name)) refuse("EPERM", w.apn + " may not assign " + name + " in " + sp);
    const Json& t = in["target"];
    if (t["alias"].type == Json::Obj) pput("nsn|" + d.D + "|" + sp, cell(name, w.apn), "{\"alias\":" + dump(t["alias"]) + ",\"by\":" + Json::quote(w.apn) + "}");
    else pput("nsn|" + d.D + "|" + sp, cell(name, w.apn), "{\"object\":" + dump(t["object"]) + ",\"by\":" + Json::quote(w.apn) + "}");
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
        if (spj["internal"].b && D != d.D) return missing;
        std::string why; if (!check(D, w, "ns_resolve", sp + ":" + name, why)) return missing;
        Json n; if (!says("nsn|" + D + "|" + sp, name, n)) return missing;
        path += (hop ? "," : "") + std::string("{\"daf\":") + Json::quote(D) + ",\"space\":" + Json::quote(sp) + ",\"name\":" + Json::quote(name) + "}";
        if (n["alias"].type != Json::Obj) return "{\"ok\":true,\"object\":" + dump(n["object"]) + ",\"path\":" + path + "]}";
        if (!n["alias"]["daf"].s.empty()) { D = n["alias"]["daf"].s; }
        if (!n["alias"]["space"].s.empty()) { sp = n["alias"]["space"].s; }
        name = n["alias"]["name"].s;
    }
}

// ---- section I: requests without enrollment (Part 2-1 6). [DAF_REQUEST_V1] The requester owns its request (to the
// member the name selects, or stored for a member who is away); the answer is the first answer claimed.
std::string request(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string name = in["name"].s;
    must(d.D, w, "request", in["store"].b ? "store:" + in["to"].s : name);
    const std::string rid = own_id();
    const std::string body = "{\"body\":" + dump(in["body"]) + ",\"from\":" + Json::quote(w.apn.empty() ? "(not a member)" : w.apn) + ",\"t\":" + nows() + "}";
    if (in["store"].b) {
        if (in["to"].s.empty()) refuse("EINVAL", "a stored request names its recipient (to)");
        put("store|" + d.D + "|" + in["to"].s, rid, body); return "{\"ok\":true,\"rid\":" + Json::quote(rid) + ",\"stored\":true}";
    }
    std::string chosen; resolve_core(d.D, in, &chosen);
    if (chosen.empty()) refuse("ENOENT", "not found");
    Json m; if (!get("m|" + d.D, chosen, m)) refuse("ENOENT", "not found");
    put("ask|" + d.D, rid, "{\"sess\":" + Json::quote(sess()) + "}");
    put("pend|" + d.D + "|" + chosen, rid, "{}");
    put("req|" + d.D + "|" + chosen, rid, body);
    if (!get("m|" + d.D, chosen, m)) answer_gone(d.D, rid, sess());    // it left between the choice and the posting
    return "{\"ok\":true,\"rid\":" + Json::quote(rid) + "}";
}
std::string answer(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); must(d.D, w, "answer", in["rid"].s);
    Json p; if (!get("pend|" + d.D + "|" + w.apn, in["rid"].s, p)) refuse("ENOENT", "no request " + in["rid"].s + " waits on " + w.apn);
    if (!claim_first("c|ans|" + d.D + "|" + in["rid"].s, sess(), "{}")) refuse("ENOENT", "request " + in["rid"].s + " was already answered");
    put("ans|" + d.D, in["rid"].s, "{\"ok\":true,\"body\":" + dump(in["body"]) + "}");
    return "{\"ok\":true}";
}

// ---- section G: the DIF Allocator (Part 2-2 2.6). [DAF_ALLOCATOR_V1] Each owner's approval is its own record; the
// DAF is created by whoever claims it first once every owner has approved.
std::string register_owner(const Json& in) {
    const std::string o = in["owner"].s; if (o.empty() || in["key"].s.size() < 16) refuse("EINVAL", "an owner and a key of 16 or more characters");
    if (!claim_first("c|owner|" + o, sess(), "{}")) refuse("EEXIST", "owner " + o + " is registered");
    put("#owner", o, "{\"key\":" + Json::quote(in["key"].s) + "}");
    return "{\"ok\":true}";
}
std::string propose_daf(const Json& in) {
    const std::string dan = in["dan"].s, daii = in["daii"].s.empty() ? "1" : in["daii"].s, D = dan + "#" + daii;
    if (dan.empty() || in["owners"].a.size() < 2) refuse("EINVAL", "a proposal names a DAF and two or more owners");
    for (auto& o : in["owners"].a) { Json k; if (!get("#owner", o.s, k)) refuse("ENOENT", "owner " + o.s + " is not registered here"); }
    Json old; if (get("dafs", D, old) || get("prop", D, old) || !claim_first("c|prop|" + D, sess(), "{}")) refuse("EEXIST", D + " exists or is proposed");
    pput("prop", D, "{\"owners\":" + dump(in["owners"]) + ",\"spec\":" + dump(in) + "}");
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"proposed\"}";
}
std::string approve_daf(const Json& in) {
    const std::string D = in["daf"].s, o = in["owner"].s; Json p; if (!get("prop", D, p)) refuse("ENOENT", "not found");
    bool listed = false; for (auto& x : p["owners"].a) if (x.s == o) listed = true;
    if (!listed) refuse("EPERM", o + " is not an owner of " + D);
    Json k; const std::string nonce = in["nonce"].s;
    if (!get("#owner", o, k) || nonce.size() < 8 || in["proof"].s != hmac_hex(k["key"].s, "approve|" + D + "|" + nonce)) refuse("EACCES", "the owner's proof does not match");
    if (!claim_first("c|nonce|prop|" + nonce, sess(), "{}")) refuse("EACCES", "the nonce was used before");
    pput("apv|" + D, o, "{\"t\":" + nows() + "}");                      // this owner's approval: its own record
    std::set<std::string> have; for (auto& c : rows("apv|" + D)) have.insert(c.instance);
    std::string ap = "["; bool f = true; for (auto& x : have) { ap += (f ? "" : ",") + Json::quote(x); f = false; } ap += "]";
    bool all = true; for (auto& x : p["owners"].a) if (!have.count(x.s)) all = false;
    if (!all) return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"proposed\",\"approved\":" + ap + "}";
    std::string owners; for (auto& x : p["owners"].a) owners += (owners.empty() ? "" : "+") + x.s;
    const std::string spec = p["spec"]["synsets"].type == Json::Arr ? dump(p["spec"]["synsets"]) : "[]";
    Json c = Json::parse("{\"dan\":" + Json::quote(p["spec"]["dan"].s) + ",\"daii\":" + Json::quote(p["spec"]["daii"].s.empty() ? "1" : p["spec"]["daii"].s) +
                         ",\"owner\":" + Json::quote(owners) + ",\"synsets\":" + spec + "}");
    try { create_daf(c); } catch (const Refuse& x) { if (x.code != "EEXIST") throw; }   // two last approvers: one creates, both see it created
    return "{\"ok\":true,\"daf\":" + Json::quote(D) + ",\"state\":\"created\",\"approved\":" + ap + "}";
}

// ---- [DAF_WAIT_V1] holding a read: the region's own, through the check point. {fn:"wait", daf, what, key, after}
//   what   name  a whatevercast name's version ("watch")       sub  a subscription (key: from subscribe), computed now
//          ans   the answer to this session's request           box  the caller's flow inbox
//          fd    a flow's data (key "<fid>|f" or "<fid>|r")     in   the caller's distribution inbox
//          req   the caller's requests                          out  a script's output (key: out)
// Answered at once if anything there is past `after` (or "now":true); otherwise parked on the variables that can
// change the answer, answered by their writers -- or dropped when this session ends.
std::string rows_answer(const std::string& v, const std::string& inst, uint64_t after, bool always) {
    auto rs = dmem::rows(v, inst, after, true);
    if (rs.empty() && !always) return "";
    std::sort(rs.begin(), rs.end(), [](const dmem::Row& a, const dmem::Row& b) { return a.id < b.id; });
    uint64_t rev = after; std::string out = ",\"rows\":["; bool f = true;
    for (auto& r : rs) { rev = std::max(rev, r.id);
        out += (f ? "" : ",") + std::string("{\"i\":") + Json::quote(r.instance) + ",\"id\":" + std::to_string(r.id) + ",\"v\":" + (r.bag.empty() ? std::string("null") : r.bag) + "}"; f = false; }
    return "{\"ok\":true,\"rev\":" + std::to_string(rev) + out + "]}";
}
std::string one_row(const std::string& key, uint64_t rev, const std::string& v) {
    return "{\"ok\":true,\"rev\":" + std::to_string((unsigned long long)rev) + ",\"rows\":[{\"i\":" + Json::quote(key) + ",\"id\":" + std::to_string((unsigned long long)rev) + ",\"v\":" + v + "}]}"; }
std::string wait(const Json& in) {
    Daf d = daf_of(in); Who w = caller(d.D); const std::string what = in["what"].s, key = in["key"].s, D = d.D;
    const uint64_t after = in["after"].type == Json::Num ? uint64_t(in["after"].n) : 0; const bool now_only = in["now"].b;
    std::vector<std::string> vars; std::function<std::string(bool)> answer;
    if (what == "name") { must(D, w, "resolve", key); name_of(D, key);
        vars = {"e|" + D + "|" + key, "m|" + D};
        answer = [D, key, after](bool always) { const uint64_t r = name_rev(D, key); return r > after || always ? "{\"ok\":true,\"rev\":" + std::to_string(r) + "}" : std::string(); }; }
    else if (what == "sub") { must(D, w, "subscribe", key);
        Json s; if (!get("sub|" + D, key, s)) refuse("ENOENT", "no subscription " + key);
        const double period = s["period_s"].n;
        if (period > 0) { vars = {"tick", "sub|" + D};
            answer = [D, key, s, after, period](bool always) { const uint64_t t = now_ms(); std::string b;
                if (!dmem::get("sub|" + D, key, b)) return std::string("{\"ok\":false,\"error\":\"ENOENT\",\"why\":\"the subscription ended\"}");
                if (!always && t < after + uint64_t(period * 1000)) return std::string();
                return one_row(key, t, "{\"t\":" + nows() + ",\"data\":" + sub_data(D, s) + "}"); }; }
        else { vars = {"m|" + D, "sub|" + D}; for (auto& n : s["names"].a) vars.push_back("e|" + D + "|" + n.s);
            answer = [D, key, s, after](bool always) { std::string b;
                if (!dmem::get("sub|" + D, key, b)) return std::string("{\"ok\":false,\"error\":\"ENOENT\",\"why\":\"the subscription ended\"}");
                const uint64_t r = names_rev(D, s["names"]); if (r <= after && !always) return std::string();
                return one_row(key, r, "{\"t\":" + nows() + ",\"data\":" + sub_data(D, s) + "}"); }; } }
    else if (what == "out") { must(D, w, "read_out", key);
        std::string id; Json j; if (!script_for(D, key, id, j)) refuse("ENOENT", "no script writes " + key);
        vars = {"scr|" + D, "scrs|" + D, "outs|" + D};
        if (j["on_change"].b) { vars.push_back("e|" + D + "|" + j["from"].s); vars.push_back("m|" + D); }
        if (j["every_s"].n > 0) vars.push_back("tick");
        const std::string from = j["from"].s; const double every = j["every_s"].n;
        answer = [D, key, from, every, after](bool always) { const uint64_t r = every > 0 ? now_ms() : std::max(names_rev(D, Json::parse("[" + Json::quote(from) + "]")), dmem::newest("outs|" + D));
            if (!always && (every > 0 ? r < after + uint64_t(every * 1000) : r <= after)) return std::string();
            const std::string v = out_value(D, key); return v.empty() ? std::string() : one_row(key, r, v); }; }
    else if (what == "box" || what == "in" || what == "req") { must(D, w, "wait_" + what, w.apn);
        const std::string v = what + "|" + D + "|" + w.apn; vars = {v};
        answer = [v, after](bool always) { return rows_answer(v, "", after, always); }; }
    else if (what == "fd") { const size_t b = key.rfind('|'); const std::string fid = b == std::string::npos ? key : key.substr(0, b);
        Json f; if (b == std::string::npos || !get("fl|" + D, fid, f)) refuse("ENOENT", "no flow " + fid);
        if (f["from"].s != w.apn && f["to"].s != w.apn) refuse("EPERM", "not an end of flow " + fid);
        must(D, w, "flow_read", "flow:" + fid);
        const std::string v = "fd|" + D + "|" + key, inst = in["seq"].type == Json::Num ? std::to_string((unsigned long long)in["seq"].n) : "";
        vars = {v}; answer = [v, inst, after](bool always) { return rows_answer(v, inst, after, always); }; }
    else if (what == "ans") { Json a; if (!get("ask|" + D, key, a) || a["sess"].s != sess()) refuse("ENOENT", "not found");   // this session's request only
        const std::string v = "ans|" + D; vars = {v};
        answer = [v, key, after](bool always) { return rows_answer(v, key, after, always); }; }
    else refuse("EINVAL", "what is name, sub, out, box, in, req, fd or ans");
    const std::string a = answer(now_only);
    if (!a.empty() || now_only) return a.empty() ? "{\"ok\":true,\"rev\":" + std::to_string(after) + ",\"rows\":[]}" : a;
    void* h = rina_defer(); if (!h) refuse("ENOTSUP", "a held read needs a request to park (FNW1)");
    for (auto& v : vars) put("sw|" + sess(), v, "{}");     // so this session's end can drop its waits
    dmem::park(vars, h, rina_session_id(), [answer]() { return answer(false); });
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
    if (fn == "version") return std::string("{\"ok\":true,\"daf_model\":\"DAF_MODEL_V1\",\"memory\":\"DAF_FROG_V1\",\"server\":\"") + FDF_BUILD_TAG + "\"}";
    refuse("ENOSYS", "no DAF operation " + fn);
}
// the region exists from the host's start; its clock answers the periodic waits (the only work time drives)
const bool g_daf_at_start = [] { dfile().open_and_replay();
    std::thread([] { for (;;) { std::this_thread::sleep_for(std::chrono::milliseconds(20)); dmem::wake("tick"); } }).detach(); return true; }();
}  // namespace

static const double g_diag_ms = [] { const char* e = getenv("DAF_DIAG"); return e && *e ? atof(e) : -1.0; }();
std::string daf_region_call(const std::string& body) {
    const double t0 = now();
    Json in; try { in = Json::parse(body); } catch (const std::exception& e) { return err("EINVAL", std::string("body is not JSON: ") + e.what()); }
    // [DAF_NEEDS_A_SESSION_V1] membership, connections and requests are bound to the caller's session
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
// [DAF_SESSION_IS_LIVENESS_V1] a session's end: its waits are dropped; its connections, subscriptions and scripts
// end; its own entries go; its members leave. Everything written here is this session's own.
void daf_session_down(uint64_t sid) {
    const std::string s = std::to_string((unsigned long long)sid);
    dmem::put("ended", s, "1");
    for (auto& r : dmem::rows("sw|" + s)) { gone("sw|" + s, r.instance); dmem::wake(r.instance); }
    for (auto& c : rows("sxa|" + s)) { const size_t a = c.instance.find(SEP);
        gone("ae|" + c.instance.substr(0, a) + "|" + s, c.instance.substr(a + 1)); gone("sxa|" + s, c.instance); }
    for (auto& c : rows("sxs|" + s)) { const size_t a = c.instance.find(SEP), b = c.instance.find(SEP, a + 1);
        const std::string D = c.instance.substr(0, a), kind = c.instance.substr(a + 1, b - a - 1), id = c.instance.substr(b + 1);
        if (kind == "sub") gone("sub|" + D, id); else put("scrs|" + D, cell(id, "session:" + s), "{\"t\":" + nows() + "}");
        gone("sxs|" + s, c.instance); }
    std::vector<Cell> mine = rows("sx|" + s);
    for (auto& c : mine) { const std::string k = c.instance; const size_t a = k.find(SEP), b = k.find(SEP, a + 1);
        if (b == std::string::npos) { continue; }
        const std::string D = k.substr(0, a), apn = k.substr(a + 1, b - a - 1), name = k.substr(b + 1);
        Json e; if (get("e|" + D + "|" + name, cell(apn, apn), e) && e["sess"].s == s) gone("e|" + D + "|" + name, cell(apn, apn)); }
    for (auto& c : mine) { const std::string k = c.instance; const size_t a = k.find(SEP);
        if (k.find(SEP, a + 1) != std::string::npos) { gone("sx|" + s, k); continue; }
        depart(k.substr(0, a), k.substr(a + 1), s, true, "down:" + s);
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
