// rina_region.cpp -- the RINA region, resident in the RAM host: the Redis server model. Every conversation a RINA
// program, rlite-ctl or a machine's agent has with the region is ONE call (ONE-CALL-PER-CONVERSATION.md). A call that
// waits is a held read on the memory itself, answered when its answer exists; nothing is ever re-asked.
// [SESSION_IS_LIVENESS_V1] A participant is alive while its session is up; the server reports a session's end.
#include "rina_region.h"
#include "rina_memory.hpp"
#include "rina_region_client.hpp"
#include "fdf_version.hpp"
#include <thread>
#include <fcntl.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <cstddef>
#include <ctime>
#include <unistd.h>
#include <cerrno>
#include <map>
#include <algorithm>
#include <cstdio>
#include <random>
#include <string>
#include <set>
#include <vector>
using frogram::Json;

namespace {
std::string field(const std::string& bag, const std::string& k) {
    const std::string kk = k + "=";
    size_t a = bag.find(kk);
    while (a != std::string::npos && a != 0 && bag[a - 1] != ';') a = bag.find(kk, a + 1);
    if (a == std::string::npos) return "";
    a += kk.size();
    const size_t b = bag.find(';', a);
    return bag.substr(a, b - a);
}
// [RINA_RECORDS_V1] the region's control records live in its own memory (rina_memory.hpp), not the generic tuple memory.
// The control code (and rlite-ctl's ported command logic, ctl_run) is written against frogram::MemoryApi; this is that
// interface over the records: a write publishes an immutable value by exchange; a read of a variable walks its tables.
void push_event(const std::string& proc, const std::string& bag);   // [RINA_MEMORY_V1] below
// [REGION_FILE_V1] Permanent records (envelope flag "p":1) are durable: each is appended to the region's file and
// fdatasync'd BEFORE it is published, and at start the file is replayed (the last value of each record wins; an empty
// value is a deletion) and compacted. RINA_REGION_FILE names it; a host without one does not start -- Permanent
// cannot quietly mean "until the host restarts". Every other record lives only in memory and returns by re-assertion.
class RegionFile {
public:
    RegionFile() {
        const char* f = getenv("RINA_REGION_FILE");
        if (!f || !*f) { fprintf(stderr, "rina-host: RINA_REGION_FILE is not set: the region's Permanent records need a file\n"); _exit(2); }
        path_ = f;
    }
    // replay into `put`, rewrite compacted, then open for appending
    template <class Put> size_t load(Put put) {
        std::map<std::pair<std::string, std::string>, std::string> last;
        if (FILE* in = fopen(path_.c_str(), "r")) {
            char* line = nullptr; size_t cap = 0; ssize_t n; size_t lineno = 0;
            while ((n = getline(&line, &cap, in)) > 0) { ++lineno;
                Json j; try { j = Json::parse(std::string(line, size_t(n))); } catch (const std::exception& e) {
                    fprintf(stderr, "rina-host: %s line %zu is not a record (%s): the file is damaged\n", path_.c_str(), lineno, e.what()); _exit(2); }
                last[{j[0].s, j[1].s}] = j[2].s; }
            free(line); fclose(in);
        } else if (errno != ENOENT) { fprintf(stderr, "rina-host: reading %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        const std::string tmp = path_ + ".new"; FILE* out = fopen(tmp.c_str(), "w");
        if (!out) { fprintf(stderr, "rina-host: writing %s: %s\n", tmp.c_str(), strerror(errno)); _exit(2); }
        size_t kept = 0;
        for (auto& [k, bag] : last) { const std::string v = Json::parse(bag)["v"].s; if (v.empty() || (k.first == "map" && v.find(";own=1") != std::string::npos)) continue;   // a deletion: gone for good; [OWNED_MAPPING_IS_NOT_RESTORED_V1] a session-bound mapping went with its session
            put(k.first, k.second, bag); fputs(line_of(k.first, k.second, bag).c_str(), out); ++kept; }
        if (fflush(out) != 0 || fdatasync(fileno(out)) != 0 || fclose(out) != 0 || rename(tmp.c_str(), path_.c_str()) != 0) {
            fprintf(stderr, "rina-host: compacting %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fd_ = ::open(path_.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC);
        if (fd_ < 0) { fprintf(stderr, "rina-host: opening %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
        fprintf(stderr, "rina-host: [REGION_FILE_V1] %zu Permanent record(s) restored from %s\n", kept, path_.c_str());
        return kept;
    }
    // one write() per record (O_APPEND: whole lines, never interleaved), then fdatasync; a failure is said and fatal
    void append(const std::string& v, const std::string& i, const std::string& bag) {
        const std::string l = line_of(v, i, bag);
        if (::write(fd_, l.data(), l.size()) != ssize_t(l.size()) || fdatasync(fd_) != 0) {
            fprintf(stderr, "rina-host: [REGION_FILE_V1] could not make a Permanent record durable in %s: %s\n", path_.c_str(), strerror(errno)); _exit(2); }
    }
private:
    static std::string line_of(const std::string& v, const std::string& i, const std::string& bag) {
        return "[" + Json::quote(v) + "," + Json::quote(i) + "," + Json::quote(bag) + "]\n"; }
    std::string path_; int fd_ = -1;
};
class RecordsApi : public frogram::MemoryApi {
public:
    RecordsApi() { file_.load([&](const std::string& v, const std::string& i, const std::string& bag) { publish(v, i, bag); }); }
    uint64_t write(const std::string&, const std::string& v, const std::string& i, const std::string& bag) override {
        if (v.compare(0, 3, "ev:") != 0 && bag.size() > 7 && bag.compare(bag.size() - 7, 7, ",\"p\":1}") == 0) file_.append(v, i, bag);   // [REGION_FILE_V1]
        return publish(v, i, bag);
    }
    uint64_t publish(const std::string& v, const std::string& i, const std::string& bag) {
        // "ev:<program>" is that program's INPUT SPACE (rlite-ctl's terminate addresses the machine's agent this way):
        // an event pushed there, which answers its parked wait -- not a record
        if (v.compare(0, 3, "ev:") == 0) { push_event(v.substr(3), bag); return 0; }
        rmem::VarSet* vs = rmem::records().get(v, true); if (!vs) throw std::runtime_error("records: the variable table is full");
        rmem::RecSlot* r = vs->get(i, true);
        const uint64_t id = rmem::record_ids().fetch_add(1, std::memory_order_acq_rel) + 1;
        const rmem::Val* old = r->val.exchange(new rmem::Val{id, bag, double(time(nullptr))}, std::memory_order_acq_rel);
        if (old) rmem::ebr().retire(old);
        return id;
    }
    void write_nowait(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override { write(s, v, i, bag); }
    std::vector<frogram::Cell> read(const std::string& svc, const std::string& v, const std::string& i, int64_t after, double wait_s, int) override {
        if (wait_s > 0) throw std::logic_error("records: no held read here (the region's held reads are its input spaces and flow directions)");
        std::vector<frogram::Cell> out; rmem::VarSet* vs = rmem::records().get(v, false); if (!vs) return out;
        auto g = rmem::ebr().guard();
        auto take = [&](rmem::RecSlot& r) { const rmem::Val* x = r.val.load(std::memory_order_acquire);
            if (x && int64_t(x->id) > after) out.push_back(frogram::Cell{x->id, svc, v, r.instance, Json::parse(x->bag), x->updated}); };
        if (!i.empty()) { if (rmem::RecSlot* r = vs->get(i, false)) take(*r); } else vs->each(take);
        return out;
    }
    void remove(uint64_t) override { throw std::logic_error("records: remove by id is not part of this region's API (write the value empty)"); }
    // the value text of one record, as stored ({"v":...}), or false
    bool raw(const std::string& v, const std::string& i, std::string& bag) {
        rmem::VarSet* vs = rmem::records().get(v, false); if (!vs) return false;
        rmem::RecSlot* r = vs->get(i, false); if (!r) return false;
        auto g = rmem::ebr().guard(); const rmem::Val* x = r->val.load(std::memory_order_acquire); if (!x) return false;
        bag = x->bag; return true;
    }
private:
    RegionFile file_;                                          // [REGION_FILE_V1]
};
RecordsApi& region_memory() { static RecordsApi* r = new RecordsApi; return *r; }
// [REGION_FILE_V1] the region (and its Permanent records) exists from the host's start, not from its first request
static const bool g_region_at_start = (region_memory(), true);
struct Rec { std::string rest, value; uint64_t id; };
// The records of a kind whose rest starts with prefix (empty values are records that are gone).
std::vector<Rec> scan(frogram::MemoryApi& m, const std::string& kind, const std::string& prefix) {
    std::vector<Rec> out;
    for (auto& c : m.read("rina", kind, "", -1, 0)) {
        const std::string v = c.bag["v"].s;
        if (!v.empty() && c.instance.compare(0, prefix.size(), prefix) == 0) out.push_back({c.instance, v, c.id});
    }
    return out;
}
// [RAW_GET_V1] a record's value, read from the cell as stored: put() writes {"v":<one JSON string>}, so the value is
// that string, unescaped here -- no JSON document is built for it.
std::string unquote_v(const std::string& bag) {
    std::string out; if (bag.compare(0, 6, "{\"v\":\"") != 0) return out;
    out.reserve(bag.size());
    for (size_t k = 6; k < bag.size(); ++k) {
        const char c = bag[k];
        if (c == '"') break;
        if (c != '\\') { out += c; continue; }
        const char e = bag[++k];
        switch (e) { case 'n': out += '\n'; break; case 't': out += '\t'; break; case 'r': out += '\r'; break; case 'b': out += '\b'; break;
                     case 'f': out += '\f'; break;
                     case 'u': { const unsigned cp = unsigned(strtoul(bag.substr(k + 1, 4).c_str(), nullptr, 16)); k += 4;
                                 if (cp < 0x80) out += char(cp); else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
                                 else { out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); } break; }
                     default: out += e; }
    }
    return out;
}
std::string get(frogram::MemoryApi&, const std::string& kind, const std::string& rest) {
    std::string bag; if (!region_memory().raw(kind, rest, bag)) return "";   // [RINA_RECORDS_V1]
    return unquote_v(bag);
}
void put(frogram::MemoryApi& m, const std::string& variable, const std::string& instance, const std::string& value) {
    m.write("rina", variable, instance, "{\"v\":" + Json::quote(value) + "}");
}  void put_gone_permanent(frogram::MemoryApi& m, const std::string& variable, const std::string& instance) { m.write("rina", variable, instance, "{\"v\":\"\",\"p\":1}"); }   // [MAP_REMOVAL_IS_PERMANENT_V1] removing a Permanent record is Permanent too: filed, so the replay deletes it
struct Ipcp { std::string name, dif, type; int id; };
std::vector<Ipcp> ipcps_of(frogram::MemoryApi& m, const std::string& ns) {
    std::vector<Ipcp> v;
    for (auto& r : scan(m, "ipcp", ns + "|")) v.push_back({r.rest.substr(ns.size() + 1), field(r.value, "dif"), field(r.value, "type"), atoi(field(r.value, "id").c_str())});
    return v;
}
int depth(frogram::MemoryApi& m, const std::string& ns, const std::vector<Ipcp>& all, const std::string& name, int guard = 0) {
    if (guard > 16) return 0;
    int d = 0, best = 0; for (auto& i : all) if (i.name == name) d = i.type == "normal" ? 1 : 0;
    for (auto& r : scan(m, "reg", ns + "|")) {
        if (field(r.value, "appl") != name) continue;
        const std::string rest = r.rest.substr(ns.size() + 1), dif = rest.substr(0, rest.find('|'));
        for (auto& i : all) if (i.dif == dif) best = std::max(best, depth(m, ns, all, i.name, guard + 1));
    }
    return d + best;
}
// The IPCP a participant uses: in the DIF it names; with none named, rlite's choice (ipcp_select_by_dif,
// kernel/ctrl-dev.c 496-525): a normal IPCP over a shim, and among normal IPCPs the one stacked highest.
bool pick(frogram::MemoryApi& m, const std::string& ns, const std::string& dif_name, Ipcp& out) {
    const auto all = ipcps_of(m, ns); bool found = false, bn = false; int bd = -1;
    for (auto& i : all) {
        if (!dif_name.empty()) { if (i.dif == dif_name) { out = i; return true; } continue; }
        const bool n = i.type == "normal"; const int d = depth(m, ns, all, i.name);
        if (!found || (n && (!bn || d > bd))) { found = true; out = i; bn = n; bd = d; }
    }
    return found;
}
// ---- the path check, at the memory. A lower link is up when the node agent of the
// shim's namespace says, in its own record written within the window, that the shim's device is up. Nothing here looks
// at a machine: the record is the measurement, made where the device is (rina_node.cpp). No fresh record: down.
bool lower_up(frogram::MemoryApi& m, const std::string& ns, const std::string& lower) {
    for (auto& i : ipcps_of(m, ns)) if (i.dif == lower) {
        if (i.type != "shim-eth") return true;
        const std::string dev = field(get(m, "ipcp", ns + "|" + i.name), "netdev");
        if (dev.empty()) return false;                                  // a shim-eth with no device carries nothing
        const std::string v = get(m, "node", ns);                       // [SESSION_IS_LIVENESS_V1] "" once its agent's session is gone
        return v.find(";" + dev + "=1;") != std::string::npos;
    }
    return true;
}
bool path_up(frogram::MemoryApi& m, const std::string& dif, const std::string& from, const std::string& to) {
    if (from == to) return true;
    struct E { std::string a, b; bool up; }; std::vector<E> es;
    for (auto& r : scan(m, "adj", dif + "|")) {
        const std::string pair = r.rest.substr(dif.size() + 1); const size_t bar = pair.find('|'); const std::string lower = field(r.value, "lower");
        es.push_back({pair.substr(0, bar), pair.substr(bar + 1), lower_up(m, field(r.value, "ans"), lower) && lower_up(m, field(r.value, "bns"), lower)});
    }
    if (es.empty()) return true;                                    // a DIF of one node
    std::vector<std::string> seen{from}, todo{from};
    while (!todo.empty()) {
        const std::string n = todo.back(); todo.pop_back();
        for (auto& e : es) {
            if (!e.up) continue;
            std::string o; if (e.a == n) o = e.b; else if (e.b == n) o = e.a; else continue;
            if (o == to) return true;
            if (std::find(seen.begin(), seen.end(), o) == seen.end()) { seen.push_back(o); todo.push_back(o); }
        }
    }
    return false;
}
}  // namespace

// [CTL_AT_THE_MEMORY_V1] rlite-ctl's command logic, run here (ctl_host.cpp)
int ctl_run(frogram::MemoryApi& m, const std::string& ns, const std::vector<std::string>& args, std::string& out, std::string& err);

namespace {
constexpr uint64_t RING = 4096;                       // cells per flow direction in its receiver's stream
// [RINA_MEMORY_V1] an event is pushed into the program's own input space (rina_memory.hpp), not written as a tuple
void answer_parked_wait(frogram::MemoryApi& m, rmem::InputSpace* sp);
void push_event(const std::string& proc, const std::string& bag);
// [RMT_OVER_DIF_LINKS_V1] the topology may have changed (a machine's devices, an enrollment, a lower flow, a shim's
// device, a machine gone): every agent is told, through its own input space, and re-derives its routes from the records
void notify_agents(frogram::MemoryApi& m) {
    for (auto& r : scan(m, "node", "")) push_event("agent:" + r.rest, "{\"k\":\"t\"}");
}
// [FDF_PARTICIPANTS_CHANGE_V1] (transport CHANGE) every process watching the participant set hears of each change (an
// announcement, a machine's session up or down) as one event in its input space; its held wait returns, it re-reads
void notify_participants(frogram::MemoryApi& m) {
    for (auto& r : scan(m, "pwatch", "")) if (!r.value.empty()) push_event(r.rest, "{\"k\":\"p\"}");
}
void push_event(const std::string& proc, const std::string& bag) {
    rmem::InputSpace* sp = rmem::spaces().get(proc, true); if (!sp) return;
    sp->push(bag); answer_parked_wait(region_memory(), sp);
}
void ev(frogram::MemoryApi& m, const std::string& proc, const std::string&, const std::string& bag) {
    rmem::InputSpace* sp = rmem::spaces().get(proc, true); if (!sp) return;
    sp->push(bag); answer_parked_wait(m, sp);           // [DEFERRED_ANSWER_V1] a parked wait is answered by this push
}
[[maybe_unused]] void ev_tuple_unused(frogram::MemoryApi& m, const std::string& proc, const std::string& inst, const std::string& bag) { m.write("rina", "ev:" + proc, inst, bag); }
std::string dump(const Json& j) {
    switch (j.type) {
        case Json::Null: return "null"; case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { char b[32]; snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case Json::Str: return Json::quote(j.s);
        case Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        default: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
}
// [FORK_KEEPS_REGISTRATIONS_V1] an input space's holders: every program that holds its fd has its OWN cell
// "ibh|<inbox>|<proc>" = "1" -- no read-modify-write of a shared cell, so concurrent holds cannot lose one. Requests go
// to the newest holder (a daemonizing program's child). A holder that leaves writes its own cell empty, THEN looks:
// whoever writes last sees every cell empty, so the names go exactly when the last holder is gone.
void hold(frogram::MemoryApi& m, const std::string& inbox, const std::string& proc) {
    put(m, "ibh", inbox + "|" + proc, "1");
    put(m, "held:" + proc, inbox, "1");               // [LAZY_CLEANUP_V1] the same fact, in the program's own variable (indexed)
}
std::string holder(frogram::MemoryApi& m, const std::string& inbox) {
    std::string best; uint64_t best_id = 0;
    for (auto& r : scan(m, "ibh", inbox + "|")) if (r.id > best_id) { best_id = r.id; best = r.rest.substr(inbox.size() + 1); }
    return best;
}
bool unhold(frogram::MemoryApi& m, const std::string& inbox, const std::string& proc) {   // true: nobody holds it any more
    put(m, "ibh", inbox + "|" + proc, ""); put(m, "held:" + proc, inbox, "");
    return scan(m, "ibh", inbox + "|").empty();
}
// [LAZY_CLEANUP_V1] a registration stands while some program holds its input space. A session's end changes only its
// own cells (its liveness, its holds, its waits); a registration it left behind is found dead by the next read that
// touches it, which uses that answer at once (the in-memory decision) and writes the erase as it goes.
bool app_live(frogram::MemoryApi& m, const std::string& rest, const std::string& value) {
    if (value.empty()) return false;
    if (!holder(m, field(value, "inbox")).empty()) return true;
    put(m, "app", rest, ""); return false;              // nobody holds it: erased now, by the reader that found it
}
// who a session is: "proc:<id>" (a program) or "node:<ns>" (a machine's agent), in the memory itself
std::string sess_key(uint64_t sid) { return std::to_string((unsigned long long)sid); }
// the program a request's session belongs to: its control set ("proc:") or its SDU set ("pdata:", [CHANNEL_SETS_V1])
std::string sess_proc() { const std::string w = get(region_memory(), "sess", sess_key(rina_session_id()));
    return w.compare(0, 5, "proc:") == 0 ? w.substr(5) : w.compare(0, 6, "pdata:") == 0 ? w.substr(6) : ""; }
// [DEFERRED_ANSWER_V1] a sender's parked credit wait: answered once the credit is past what it had (or its receiver is gone)
void answer_parked_credit(rmem::FlowDir* fd) {
    rmem::Parked* p = fd->take(); if (!p) return;
    if (fd->gone()) { rina_answer(p->cont, "{\"ok\":false,\"error\":\"gone\"}"); delete p; return; }
    const uint64_t n = fd->credit();
    if (n > p->arg) { rina_answer(p->cont, "{\"ok\":true,\"n\":" + std::to_string(n) + "}"); delete p; return; }
    fd->park(p);                                          // not past it yet: parked again
    if (fd->credit() > p->arg || fd->gone()) answer_parked_credit(fd);   // it moved meanwhile
}
// [DEFERRED_ANSWER_V1] the answer to a program's wait: its events after `after` (references resolved where the bytes
// leave the memory), and the taken count of every flow whose SDUs it hands over
std::string wait_answer(frogram::MemoryApi& m, const std::string& me_p, rmem::InputSpace* sp, uint64_t after) {
    std::vector<std::pair<uint64_t, std::string>> rows;
    sp->read_after(after, [&](const rmem::Event& e) { rows.emplace_back(e.seq, e.bag); });
                // [WAIT_ANSWER_IS_TEXT_V1] the answer is the region's own text: "E\n", then one line per event, "<number>\t<event>\n".
    // Not JSON, so the host carries it as one raw field and builds no JSON tree of it; the library parses each event.
    std::string out = "E\n"; struct Used { std::string fid; std::vector<uint64_t> qs; }; std::vector<Used> used;
    size_t total = 0; for (auto& r : rows) total += r.second.size() + 24; out.reserve(out.size() + total + 4);
    size_t emitted = 0;                            // [SDU_BY_REFERENCE_V1] a reference no longer held is skipped (and recorded)
    for (size_t k = 0; k < rows.size(); ++k) {
        std::string resolved; const std::string* bagp = &rows[k].second;
        if (bagp->find("\"rp\":") != std::string::npos) {   // [SDU_BY_REFERENCE_V1] the bytes leave the memory here, once
            Json r = Json::parse(*bagp); std::string dq;
            rmem::FlowDir* src = rmem::flows().get(r["fid"].s + "|" + r["rp"].s, false);
            if (!src || !src->find(uint64_t(r["rq"].n), dq)) {
                put(m, "dropped", r["fid"].s + "|" + me_p, "reference to SDU " + std::to_string((unsigned long long)r["rq"].n) + " of " + r["rp"].s + " no longer held");
                continue; }                       // recorded, never silent
            resolved = "{\"k\":\"s\",\"fid\":" + Json::quote(r["fid"].s) + ",\"q\":" + std::to_string((unsigned long long)r["q"].n) + ",\"d\":" + dq + "}";
            if (rmem::FlowDir* mine = rmem::flows().get(r["fid"].s + "|" + me_p, true)) mine->keep(uint64_t(r["q"].n), dq);   // a reference to THIS one finds it too
            bagp = &resolved;
        }
        const std::string& bag = *bagp;
        ++emitted; out += std::to_string(rows[k].first); out += '\t'; out += bag; out += '\n';
        if (bag.compare(0, 16, "{\"k\":\"s\",\"fid\":\"") == 0) {
            const size_t f0 = 16, f1 = bag.find('"', f0); const std::string fid = bag.substr(f0, f1 - f0);
            const uint64_t q = strtoull(bag.c_str() + f1 + 6, nullptr, 10);              // after "fid":"…","q":
            bool f = false; for (auto& u : used) if (u.fid == fid) { u.qs.push_back(q); f = true; }
            if (!f) used.push_back({fid, {q}}); }
    }
    
    // [CREDIT_ON_DEMAND_V1] the consumed count goes into its own cell, NOT the sender's stream: a sender
    // needs credit only when it is RING SDUs ahead, and then asks for it with one held read (op credit).
    // [CREDIT_BY_QUARTERS_V1] and it is written only when consumption crosses a quarter of the ring: a
    // sender blocked at RING ahead has a full ring outstanding, and taking it crosses at least one boundary,
    // so the cell is always written before that sender could wait for nothing.
    // [CREDIT_IS_CONTIGUOUS_V1] the host executes a session's requests in parallel, so a flow's SDUs can
    // reach the receiver out of order. Credit is the CONTIGUOUS count -- every SDU below it has been taken --
    // never the highest seen: an SDU still missing below the highest would otherwise have its ring cell
    // overwritten by a sender let RING ahead of the wrong count. The receiver's own cell
    // ("taken|<fid>|<me>") holds that count and the sequence numbers seen above it.
    // [RINA_MEMORY_V1] the flow direction keeps the contiguous taken count and publishes credit by quarters
    // ([CREDIT_IS_CONTIGUOUS_V1], [CREDIT_BY_QUARTERS_V1]) on its own word, which the sender sleeps on
    for (auto& u : used) { bool crossed = false; if (rmem::FlowDir* fd = rmem::flows().get(u.fid + "|" + me_p, true)) {
        fd->taken(u.qs, crossed);
        if (crossed) answer_parked_credit(fd); } }       // [DEFERRED_ANSWER_V1] a sender parked for this credit is answered
    return out;
}
// THE held read: everything addressed to this program since `after`, answered the moment there is any.
// [DEFERRED_ANSWER_V1] nothing yet: the request is parked as a continuation in the program's input space, and the next
// event pushed there answers it -- no thread waits.
std::string wait_op(const std::string& me_p, uint64_t after) {
    frogram::MemoryApi& m = region_memory();
    rmem::InputSpace* sp = rmem::spaces().get(me_p, true);
    if (sp->any_after(after) || sp->gone()) return wait_answer(m, me_p, sp, after);
    void* h = rina_defer();
    if (!h) throw std::runtime_error("wait: no continuation to park (not inside a request)");
    sp->park(new rmem::Parked{h, after, me_p});
    if (sp->any_after(after) || sp->gone()) answer_parked_wait(m, sp);   // it arrived meanwhile
    return "";
}
void answer_parked_wait(frogram::MemoryApi& m, rmem::InputSpace* sp) {   // exactly once: whoever takes the parked wait
    rmem::Parked* p = sp->take(); if (!p) return;
    rina_answer(p->cont, wait_answer(m, p->who, sp, p->arg)); delete p;
}
// [PREFIX_OVERLAP_REFUSED_V1] a member's served /24s, as "10.A.B": facts.addrs rows of kind "own" whose ip is 10.x.y.1,
// and facts.subnet. Shared by the overlap check (announce) and the identity resolver (resolve).
static std::set<std::string> served_prefixes(const Json& fj) {
    std::set<std::string> out;
    for (auto& r : fj["addrs"].a) { const std::string ip = r["ip"].s; if (r["kind"].s == "own" && ip.compare(0, 3, "10.") == 0 && ip.size() > 2 && ip.compare(ip.size() - 2, 2, ".1") == 0) out.insert(ip.substr(0, ip.rfind('.'))); }
    const std::string sub = fj["subnet"].s; if (!sub.empty() && sub.find('/') != std::string::npos) { const std::string b = sub.substr(0, sub.find('/')); out.insert(b.substr(0, b.rfind('.'))); }
    return out;
}

std::string resident(const std::string& op, const Json& in) {
    frogram::MemoryApi& m = region_memory();
    // [AGENT_REASSERTS_V1] (experiment 5b) what a machine owns in the region, as stored, so that it can write it back
    // after a host change: its IPCPs (ipcp|<machine>|*), their registrations in lower DIFs (nreg|*|<its ipcp>) and the
    // adjacencies it is an end of (adj|*, ans or bns = it). The agent keeps this copy; it is the machine's own truth.
    if (op == "mine" || op == "reassert") {
        frogram::MemoryApi& m = region_memory(); const std::string ns = in["ns"].s;
        if (ns.empty()) return "{\"ok\":false,\"error\":\"EINVAL\"}";
        auto owned_names = [&] { std::set<std::string> n; for (auto& r : scan(m, "ipcp", ns + "|")) if (!r.value.empty()) n.insert(r.rest.substr(ns.size() + 1)); return n; };
        auto owns = [&](const std::string& v, const std::string& i, const std::string& bagv, const std::set<std::string>& names) {
            if (v == "ipcp") return i.compare(0, ns.size() + 1, ns + "|") == 0;
            if (v == "nreg") return names.count(i.substr(i.find('|') + 1)) > 0;
            if (v == "adj")  return field(bagv, "ans") == ns || field(bagv, "bns") == ns;
            return false; };
        if (op == "mine") {
            const std::set<std::string> names = owned_names(); std::string out = "{\"ok\":true,\"recs\":["; bool first = true;
            for (const char* v : {"ipcp", "nreg", "adj"}) for (auto& r : scan(m, v, "")) {
                if (r.value.empty() || !owns(v, r.rest, r.value, names)) continue;
                std::string bag; if (!region_memory().raw(v, r.rest, bag)) continue;
                out += (first ? "[" : ",[") + Json::quote(v) + "," + Json::quote(r.rest) + "," + Json::quote(bag) + "]"; first = false; }
            const std::string arr = out.substr(out.find("[")); return out + "],\"recs_text\":" + Json::quote(arr + "]") + "}";
        }
        // reassert: IPCPs first (they decide which nreg records are this machine's), each record checked, written verbatim
        std::set<std::string> names; for (auto& r : in["recs"].a) if (r[0].s == "ipcp" && owns("ipcp", r[1].s, "", names)) names.insert(r[1].s.substr(ns.size() + 1));
        size_t n = 0;
        for (auto& r : in["recs"].a) {
            const std::string v = r[0].s, i = r[1].s, bag = r[2].s;
            if (!owns(v, i, Json::parse(bag)["v"].s, names)) return "{\"ok\":false,\"error\":\"EPERM\",\"record\":" + Json::quote(v + "|" + i) + "}";
        }
        for (auto& r : in["recs"].a) { m.write("rina", r[0].s, r[1].s, r[2].s); ++n; }
        notify_agents(m);                                          // the topology may have changed for everyone
        return "{\"ok\":true,\"n\":" + std::to_string(n) + "}";
    }
    // [LIBRARY_REASSERTS_V1] (experiment 5b, part C) what a PROGRAM owns in the region, as stored, so that it can write it
    // back after a host change: its registrations (app|*, whose inbox is one of its own), its flows (fl|<fid> and
    // flow|<dif>|<fid> where it is the client or the server end) and its own flow list (pfl:<proc>|*). Its inboxes and
    // liveness come back by hello; this is the rest.
    if (op == "pmine" || op == "preassert") {
        frogram::MemoryApi& m = region_memory(); const std::string proc = in["proc"].s;
        if (proc.empty()) return "{\"ok\":false,\"error\":\"EINVAL\"}";
        std::set<std::string> inboxes;
        if (op == "pmine") { for (auto& r : scan(m, "ibh", "")) { const size_t b = r.rest.rfind('|'); if (!r.value.empty() && r.rest.substr(b + 1) == proc) inboxes.insert(r.rest.substr(0, b)); } }
        else for (auto& b : in["inboxes"].a) inboxes.insert(b.s);
        auto owns = [&](const std::string& v, const std::string& bagv) {
            if (v == "app") return inboxes.count(field(bagv, "inbox")) > 0;
            if (v == "fl") return field(bagv, "c") == proc || field(bagv, "s") == proc;
            if (v == "flow") return field(bagv, "cproc") == proc || field(bagv, "sproc") == proc;
            return v == "pfl:" + proc; };
        if (op == "pmine") {
            std::string arr = "["; bool first = true;
            for (const std::string& v : {std::string("app"), std::string("fl"), std::string("flow"), "pfl:" + proc}) for (auto& r : scan(m, v, "")) {
                if (r.value.empty() || !owns(v, r.value)) continue;
                std::string bag; if (!region_memory().raw(v, r.rest, bag)) continue;
                arr += (first ? "[" : ",[") + Json::quote(v) + "," + Json::quote(r.rest) + "," + Json::quote(bag) + "]"; first = false; }
            arr += "]";
            return "{\"ok\":true,\"recs_text\":" + Json::quote(arr) + "}";
        }
        for (auto& r : in["recs"].a)
            if (!owns(r[0].s, Json::parse(r[2].s)["v"].s)) return "{\"ok\":false,\"error\":\"EPERM\",\"record\":" + Json::quote(r[0].s + "|" + r[1].s) + "}";
        size_t n = 0; for (auto& r : in["recs"].a) { m.write("rina", r[0].s, r[1].s, r[2].s); ++n; }
        return "{\"ok\":true,\"n\":" + std::to_string(n) + "}";
    }
    // [AREA_MAPPING_IN_THE_REGION_V1] (John, 2026-10-06: no control messages over UDP) LISP's mapping, in a region, over
    // the session. The map-resolver machine's mapping region holds, per area name: the site's authentication key
    // (map_site; Permanent, as lispers.net's site config) and the registration (map_register; Permanent until its OWN
    // expiry -- computed when read, no reaper). A registration is authenticated (HMAC-SHA-256 with the area's password over
    // "name|rloc|ttl|ts") and must carry a strictly newer ts than the last (no replay). map_request answers the RLOC, or
    // ENOENT for an unknown or expired name (LISP's negative reply).
    if (op == "map_site" || op == "map_register" || op == "map_request" || op == "map_watch") {   // [MAP_WATCH_V1] map_watch too
        frogram::MemoryApi& m = region_memory(); const std::string name = in["name"].s;
        if (name.empty() || name.find('|') != std::string::npos) return "{\"ok\":false,\"error\":\"EINVAL\"}";
        struct timespec now; clock_gettime(CLOCK_REALTIME, &now); const double t = double(now.tv_sec) + double(now.tv_nsec) / 1e9;
        if (op == "map_site") { m.write("rina", "site", name, "{\"v\":" + Json::quote(in["key"].s) + ",\"p\":1}"); return "{\"ok\":true}"; }
        // [MAP_WATCH_V1] a member that lost its RAM manager waits on the MAPPING for the area's name: every registration of
        // that name (the RAM manager's own, on its restart or move) is one event in the watcher's input space; its held wait
        // returns and it re-resolves. No timer: the re-registration is the event. Dropped when the watcher's session ends.
        if (op == "map_watch") {                 // name "*": every mapping's registration and removal ([MAP_PREFIX_LOCATOR_SET_V1], the hub's watch)
            const std::string name = in["name"].s, proc = in["proc"].s;
            if (name.empty() || proc.empty()) return "{\"ok\":false,\"error\":\"EINVAL\"}";
            put(m, "mwatch", name + "|" + proc, sess_key(rina_session_id())); return "{\"ok\":true}";
        }
        if (op == "map_register") {
            // [MAP_PREFIX_LOCATOR_SET_V1] (PLAN-FROGNET-ON-FDF 0.3, security contract 2) the same signed registration serves an
            // EID prefix: name = the prefix ("10.9.9.0/24"), rloc = its LOCATOR SET as text ("hub1:prio:weight,hub2:..."),
            // signed with the DIF's site key named by `site` (the area) rather than a per-prefix site. The HMAC covers
            // name|rloc|ttl|ts exactly as before, so the row holds only what was verified. `own:true` binds the mapping to
            // this session: it goes when the session ends, and holders of a watch on it are told (map-notify).
            const std::string site = in["site"].s.empty() ? name : in["site"].s;
            const std::string key = get(m, "site", site);
            if (key.empty()) return "{\"ok\":false,\"error\":\"ENOENT\",\"why\":\"no site for this name\"}";
            const std::string rloc = in["rloc"].s, ttl = in["ttl"].s, ts = in["ts"].s;
            const std::string msg = name + "|" + rloc + "|" + ttl + "|" + ts;
            unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
            HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
            static const char hx[] = "0123456789abcdef"; std::string want; for (unsigned i = 0; i < n; ++i) { want += hx[md[i] >> 4]; want += hx[md[i] & 15]; }
            if (want != in["auth"].s) return "{\"ok\":false,\"error\":\"EPERM\",\"why\":\"authentication failed\"}";
            const std::string cur = get(m, "map", name);
            if (!cur.empty() && strtod(field(cur, "ts").c_str(), nullptr) >= strtod(ts.c_str(), nullptr)) return "{\"ok\":false,\"error\":\"EALREADY\",\"why\":\"not newer than the registration held\"}";
            // [MAP_OWNER_IS_NOT_TRANSFERRED_V1] a live mapping bound to ANOTHER live session is that session's: a second
            // claimant is refused with the holder named, as a served /24 is (PREFIX_OVERLAP_REFUSED_V1). Before this, the
            // newer registration silently took ownership, and when that session later withdrew "its" mapping the first
            // registrant's prefix left the region while its client still believed it held it (Seattle7 2026-10-09:
            // Seattle5 registered 10.170.170.0/24 as its exit, then withdrew it; Seattle7's withdraw said EPERM).
            if (in["own"].b && !cur.empty() && strtod(field(cur, "exp").c_str(), nullptr) > t) {
                const std::string owner = get(m, "mown", name);
                if (!owner.empty() && owner != sess_key(rina_session_id()) && !get(m, "sess", owner).empty())
                    return "{\"ok\":false,\"error\":\"EEXIST\",\"why\":" + Json::quote(name + " is held by a live session (" + get(m, "sess", owner) + "); not transferred") + "}";
            }
            char exp[32]; snprintf(exp, sizeof exp, "%.3f", t + strtod(ttl.c_str(), nullptr));
            m.write("rina", "map", name, "{\"v\":" + Json::quote("rloc=" + rloc + ";exp=" + exp + ";ts=" + ts + ";site=" + site + (in["own"].b ? ";own=1" : "")) + ",\"p\":1}");   // [OWNED_MAPPING_IS_NOT_RESTORED_V1] filed (it supersedes an older one), never restored
            put(m, "mown", name, in["own"].b ? sess_key(rina_session_id()) : "");          // [MAP_PREFIX_LOCATOR_SET_V1] session-bound, or not
            for (auto& w : scan(m, "mwatch", name + "|")) if (!w.value.empty()) push_event(w.rest.substr(name.size() + 1), "{\"k\":\"m\"}");   // [MAP_WATCH_V1]
            for (auto& w : scan(m, "mwatch", "*|")) if (!w.value.empty()) push_event(w.rest.substr(2), "{\"k\":\"m\"}");
            return "{\"ok\":true,\"expires\":" + std::string(exp) + "}";
        }
        const std::string cur = get(m, "map", name);
        if (cur.empty() || strtod(field(cur, "exp").c_str(), nullptr) <= t) return "{\"ok\":false,\"error\":\"ENOENT\"}";
        return "{\"ok\":true,\"rloc\":" + Json::quote(field(cur, "rloc")) + ",\"site\":" + Json::quote(field(cur, "site")) + ",\"ttl_left\":" + std::to_string(int(strtod(field(cur, "exp").c_str(), nullptr) - t)) + "}";
    }
    if (op == "map_remove") {                  // [MAP_PREFIX_LOCATOR_SET_V1] a session withdraws a mapping it registered with own:true
        frogram::MemoryApi& m = region_memory(); const std::string name = in["name"].s;
        if (name.empty() || get(m, "mown", name) != sess_key(rina_session_id())) return "{\"ok\":false,\"error\":\"EPERM\",\"why\":\"not this session's mapping\"}";
        put(m, "mown", name, ""); put_gone_permanent(m, "map", name);   // [MAP_REMOVAL_IS_PERMANENT_V1]
        for (auto& w : scan(m, "mwatch", name + "|")) if (!w.value.empty()) push_event(w.rest.substr(name.size() + 1), "{\"k\":\"m\"}");
        for (auto& w : scan(m, "mwatch", "*|")) if (!w.value.empty()) push_event(w.rest.substr(2), "{\"k\":\"m\"}");
        return "{\"ok\":true}";
    }
    if (op == "map_list") {                    // [MAP_PREFIX_LOCATOR_SET_V1] every live mapping registered under a site, in one read
        frogram::MemoryApi& m = region_memory(); const std::string site = in["site"].s;
        struct timespec now; clock_gettime(CLOCK_REALTIME, &now); const double t = double(now.tv_sec) + double(now.tv_nsec) / 1e9;
        std::string out = "{\"ok\":true,\"mappings\":["; bool f = true;
        for (auto& r : scan(m, "map", "")) {
            if (r.value.empty() || strtod(field(r.value, "exp").c_str(), nullptr) <= t) continue;
            if (!site.empty() && field(r.value, "site") != site) continue;
            out += (f ? "" : ",") + std::string("{\"eid\":") + Json::quote(r.rest) + ",\"rloc\":" + Json::quote(field(r.value, "rloc")) + ",\"site\":" + Json::quote(field(r.value, "site")) +
                   ",\"ttl_left\":" + std::to_string(int(strtod(field(r.value, "exp").c_str(), nullptr) - t)) + "}"; f = false;
        }
        return out + "]}";
    }
    // [SESSION_PEER_V1] the address this request's session arrives FROM, as this host sees it. A machine whose answer is
    // not one of its own addresses is behind NAT (DESIGN-NAT.md step 1): measured, never configured.
    if (op == "seen") {
        const std::string a = get(region_memory(), "speer", sess_key(rina_session_id()));
        return a.empty() ? "{\"ok\":false,\"error\":\"ENOENT\"}" : "{\"ok\":true,\"addr\":" + Json::quote(a) + "}";
    }
    if (op == "taken_now") {                  // [RESEND_ON_ROUTE_CHANGE_V1] the receiver's stated count for a sender's flow, now
        frogram::MemoryApi& m = region_memory();
        const std::string fid = in["fid"].s, me = in["proc"].s, r = get(m, "fl", fid);
        const std::string peer = field(r, "c") == me ? field(r, "s") : field(r, "c");
        rmem::FlowDir* fd = peer.empty() ? nullptr : rmem::flows().get(fid + "|" + peer, false);
        return "{\"ok\":" + std::string(fd ? "true" : "false") + ",\"n\":" + std::to_string(fd ? fd->credit() : 0) + "}";
    }
    if (op == "taken") {                      // [CREDIT_IS_STATE_V1] a receiver states how far it has handed a flow's SDUs to its
        // program; a sender held on that truth (op credit) is answered from it. Posted: the receiver waits for nothing.
        rmem::FlowDir* fd = rmem::flows().get(in["fid"].s + "|" + in["proc"].s, true);
        fd->publish_taken(uint64_t(in["n"].n)); answer_parked_credit(fd);
        return "{\"ok\":true}";
    }
    if (op == "rmt_view") {                   // [RMT_OVER_DIF_LINKS_V1] what an agent derives its routes from: the records as held
        frogram::MemoryApi& m = region_memory();
        std::string out = "{\"ok\":true,\"adj\":[";
        bool first = true;
        for (auto& r : scan(m, "adj", "")) {                  // dif|a|b -> lower, ans, bns
            const std::string rest = r.rest; const size_t b1 = rest.find('|'), b2 = rest.find('|', b1 + 1);
            out += first ? "[" : ",["; first = false;
            out += Json::quote(rest.substr(0, b1)) + "," + Json::quote(rest.substr(b1 + 1, b2 - b1 - 1)) + "," + Json::quote(rest.substr(b2 + 1)) + "," +
                   Json::quote(field(r.value, "lower")) + "," + Json::quote(field(r.value, "ans")) + "," + Json::quote(field(r.value, "bns")) + "]";
        }
        out += "],\"ipcp\":["; first = true;
        for (auto& r : scan(m, "ipcp", "")) {                 // ns|name -> dif, type, netdev
            const size_t b1 = r.rest.find('|');
            out += first ? "[" : ",["; first = false;
            out += Json::quote(r.rest.substr(0, b1)) + "," + Json::quote(r.rest.substr(b1 + 1)) + "," + Json::quote(field(r.value, "dif")) + "," +
                   Json::quote(field(r.value, "type")) + "," + Json::quote(field(r.value, "netdev")) + "]";
        }
        out += "],\"node\":["; first = true;
        for (auto& r : scan(m, "node", "")) { out += first ? "[" : ",["; first = false; out += Json::quote(r.rest) + "," + Json::quote(r.value) + "]"; }
        return out + "]}";
    }
    if (op == "rmem") {                        // [RINA_MEMORY_V1] debugging aid: a program's space, or a flow direction
        if (!in["proc"].s.empty()) { rmem::InputSpace* sp = rmem::spaces().get(in["proc"].s, false);
            return "{\"ok\":true,\"numbered\":" + std::to_string(sp ? sp->numbered() : 0) + ",\"head\":" + std::to_string(sp ? sp->head_base() : 0) + ",\"gone\":" + (sp && sp->gone() ? "true" : "false") + "}"; }
        rmem::FlowDir* fd = rmem::flows().get(in["flow"].s, false);
        return "{\"ok\":true,\"state\":" + Json::quote(fd ? fd->state() : "none") + "}";
    }
    if (op == "peek") {                        // debugging aid: the cells of one variable, as the memory holds them
        std::string o = "{\"ok\":true,\"rows\":["; bool f = true;
        for (auto& c : m.read("rina", in["v"].s, "", -1, 0)) { o += (f ? "" : ",") + std::string("[") + std::to_string(c.id) + "," + Json::quote(c.instance) + "," + dump(c.bag) + "]"; f = false; }
        return o + "]}";
    }
    if (op == "hello") {                       // a program's one connection: it is alive while this session is up
        if (in["set"].s == "data") {           // [CHANNEL_SETS_V1] its SDU set: carries SDUs only; liveness stays the control set's
            put(m, "sess", sess_key(rina_session_id()), "pdata:" + in["proc"].s); return "{\"ok\":true}"; }
        put(m, "sess", sess_key(rina_session_id()), "proc:" + in["proc"].s);
        put(m, "proc", in["proc"].s, "1;ns=" + in["ns"].s + ";");   // [RESEND_ON_ROUTE_CHANGE_V1] alive, and on which machine
        for (auto& b : in["inboxes"].a) hold(m, b.s, in["proc"].s);
        return "{\"ok\":true}";
    }
    if (op == "fork") {                        // [FORK_KEEPS_REGISTRATIONS_V1] the parent, before fork(): the child holds these too
        // the child is a live program from this moment: requests for it are written to its stream, which it reads
        // from the start once its own connection is up (it said nothing yet, so "fork", not "1")
        put(m, "proc", in["child"].s, "fork");
        for (auto& b : in["inboxes"].a) hold(m, b.s, in["child"].s);
        return "{\"ok\":true}";
    }
    // [FDF_DISCOVERY_V1] (plan item 1) discovery by capability announcement. A machine writes its announcement -- its
    // name, its IPs and its measured capability (FrogNet's capability probe, verbatim) -- into the shared space; the
    // participants are the announcements; a participant is alive while its machine's session is up (its node record).
    if (op == "announce") {
        const std::string ns = in["ns"].s;
        if (ns.empty() || in["name"].s.empty() || in["cap"].s.empty()) return "{\"ok\":false,\"error\":\"EINVAL\",\"why\":\"an announcement needs ns, name and cap\"}";
        std::string ips = "["; bool f = true; for (auto& a : in["ips"].a) { ips += (f ? "" : ",") + Json::quote(a.s); f = false; } ips += "]";
        // [IPCP_FACTS_V1] (PLAN-FROGNET-ON-FDF 0.3/0.6) the member's own facts about itself, an opaque JSON object the
        // announcing program supplies as text (like cap): FrogNet's node facts (name, guid, subnet, pubkey, transit,
        // chorus, hosts behind it, echo, role). Re-announced by the same session whenever they change; gone with it.
        std::string facts = in["facts"].s;
        if (!facts.empty()) { try { Json fj = Json::parse(facts); if (fj.type != Json::Obj) facts.clear(); } catch (...) { return "{\"ok\":false,\"error\":\"EINVAL\",\"why\":\"facts is not a JSON object\"}"; } }
        // [PREFIX_OVERLAP_REFUSED_V1] (PLAN-RAM-DISCOVERY §4) a member's served /24s (facts.addrs rows of kind "own"
        // whose ip is 10.x.y.1, and facts.subnet) may not overlap a LIVE member's. Refused with the holder's name, so
        // two islands with the same addressing never both route it; the joiner stays out and says so.
        if (!facts.empty()) {
            auto served = served_prefixes;
            std::set<std::string> mine; try { mine = served(Json::parse(facts)); } catch (...) {}
            if (!mine.empty()) for (auto& r : scan(m, "ann", "")) {
                if (r.rest == ns || r.value.empty() || get(m, "node", r.rest).empty()) continue;      // me, or not alive
                Json a; try { a = Json::parse(r.value); } catch (...) { continue; }
                if (a["facts"].type != Json::Obj) continue;
                for (auto& pfx : served(a["facts"])) if (mine.count(pfx)) {
                    const std::string holder = a["name"].s;
                    return "{\"ok\":false,\"error\":\"EEXIST\",\"why\":" + Json::quote(pfx + ".0/24 is served by " + holder + " (alive); this announcement is refused") + ",\"holder\":" + Json::quote(holder) + ",\"prefix\":" + Json::quote(pfx + ".0/24") + "}";
                }
            }
        }
        // [LEAF_ADDR_FROM_THE_REGION_V1] (John 2026-10-10: "Nobody types shit. That's what the ram manager is for") a leaf
        // off the mesh (facts.role leaf, a WireGuard pubkey, no `lan`) is given its /32 here: the lowest 10.252.X.Y no
        // live session holds (10.252.254.0/24 is the broker's veths, never given), bound to THIS session -- the same on
        // every re-announcement, free when the session ends or when the member is on a LAN (`lan`, no pubkey). The
        // region writes it into the stored facts (fdf-hub and fdf_graph read facts.addr) and returns it. A leaf that
        // says its own addr is refused: an address nobody typed cannot be a duplicate.
        std::string given;
        if (!facts.empty()) {
            Json fj; try { fj = Json::parse(facts); } catch (...) {}
            // [REACH_IS_A_LEAF_HERE_V1] a participant that asks to reach this network ("reach":true, a WireGuard pubkey)
            // needs an address in THIS scope, exactly as a leaf does: the same /32 rule, the same session binding.
            const bool reacher = fj["role"].s == "participant" && fj["reach"].b;
            if (fj["role"].s == "leaf" || reacher) {
                if (fj["addr"].type != Json::Null)
                    return "{\"ok\":false,\"error\":\"EINVAL\",\"why\":\"a leaf's address is the region's to give: facts.addr is not the member's to say (leave it out)\"}";
                const std::string sid = sess_key(rina_session_id());
                for (auto& r : scan(m, "laddr", "")) if (r.value == sid) { given = r.rest; break; }
                const bool wants = !fj["pubkey"].s.empty() && fj["lan"].type == Json::Null;
                if (!wants && !given.empty()) { put(m, "laddr", given, ""); given.clear(); }        // on a LAN now: freed
                if (wants && given.empty()) {
                    std::set<std::string> held;
                    for (auto& r : scan(m, "laddr", "")) if (!r.value.empty() && !get(m, "sess", r.value).empty()) held.insert(r.rest);
                    for (int x = 0; x < 254 && given.empty(); ++x)
                        for (int y = 1; y < 255 && given.empty(); ++y) { const std::string ip = "10.252." + std::to_string(x) + "." + std::to_string(y); if (!held.count(ip)) given = ip; }
                    if (given.empty()) return "{\"ok\":false,\"error\":\"ENOSPC\",\"why\":\"every leaf address in 10.252.0.0/16 is held by a live session\"}";
                    put(m, "laddr", given, sid);
                }
                if (wants) {
                    size_t b = facts.find_first_not_of(" \t\r\n", 1);
                    facts = "{\"addr\":" + Json::quote(given + "/32") + (b != std::string::npos && facts[b] == '}' ? "" : ",") + facts.substr(1);
                }
            }
        }
        put(m, "ann", ns, "{\"ns\":" + Json::quote(ns) + ",\"name\":" + Json::quote(in["name"].s) + ",\"ips\":" + ips + ",\"cap\":" + in["cap"].s +
            (facts.empty() ? "" : ",\"facts\":" + facts) + "}");
        notify_agents(m);
        notify_participants(m);                                          // [FDF_PARTICIPANTS_CHANGE_V1]
        return given.empty() ? "{\"ok\":true}" : "{\"ok\":true,\"addr\":" + Json::quote(given + "/32") + "}";
    }
    if (op == "watch_participants") {           // [FDF_PARTICIPANTS_CHANGE_V1] this process watches; dropped when its session ends
        if (in["proc"].s.empty()) return "{\"ok\":false,\"error\":\"EINVAL\"}";
        put(m, "pwatch", in["proc"].s, sess_key(rina_session_id())); return "{\"ok\":true}";
    }
    if (op == "version") return std::string("{\"ok\":true,\"server\":\"") + FDF_BUILD_TAG + "\"}";     // [FDF_VERSION_V1]
    if (op == "world") {                       // [WORLD_IN_ONE_READ_V1] participants AND the site's live mappings in one answer: the
        // daemon's per-event read is one round trip, not two (a WAN member pays the region's RTT once per event)
        const std::string site = in["site"].s;
        struct timespec now; clock_gettime(CLOCK_REALTIME, &now); const double t = double(now.tv_sec) + double(now.tv_nsec) / 1e9;
        std::string out = std::string("{\"ok\":true,\"server\":\"") + FDF_BUILD_TAG + "\",\"participants\":["; bool f = true;
        for (auto& r : scan(m, "ann", "")) {
            if (r.value.empty()) continue;
            const bool alive = !get(m, "node", r.rest).empty();
            out += (f ? "" : ",") + r.value.substr(0, r.value.size() - 1) + ",\"alive\":" + (alive ? "true" : "false") + ",\"id\":" + std::to_string(r.id) + "}"; f = false;
        }
        out += "],\"mappings\":["; f = true;
        for (auto& r : scan(m, "map", "")) {
            if (r.value.empty() || strtod(field(r.value, "exp").c_str(), nullptr) <= t) continue;
            if (!site.empty() && field(r.value, "site") != site) continue;
            out += (f ? "" : ",") + std::string("{\"eid\":") + Json::quote(r.rest) + ",\"rloc\":" + Json::quote(field(r.value, "rloc")) + ",\"site\":" + Json::quote(field(r.value, "site")) +
                   ",\"ttl_left\":" + std::to_string(int(strtod(field(r.value, "exp").c_str(), nullptr) - t)) + "}"; f = false;
        }
        return out + "]}";
    }
    if (op == "resolve") {
        // [IDENTITY_IN_TUPLES_V1] tuples name machines by identity (w:<guid>), never by address: an address means one
        // machine only inside one addressing scope, and a tuple is read from others. The resolver gives the address the
        // identity has IN THIS POND: a node its own .1 (the /24 it serves), a leaf the /32 the region gave it. A member
        // that is not alive has no address (gone is gone); a participant has none in this pond (no routes here).
        // in {"ids":["w:<guid>",...]} -> {"resolved":{"w:<guid>":{"addr","name","role"} | {"error","why"}}}
        std::string out = std::string("{\"ok\":true,\"server\":\"") + FDF_BUILD_TAG + "\",\"resolved\":{"; bool f = true;
        for (auto& idj : in["ids"].a) {
            const std::string id = idj.s; std::string one;
            const std::string row = get(m, "ann", id);
            if (row.empty() || get(m, "node", id).empty()) one = "{\"error\":\"ENOENT\",\"why\":\"no alive member " + id + " in this pond\"}";
            else {
                Json a; try { a = Json::parse(row); } catch (...) {}
                const Json& fj = a["facts"]; const std::string role = fj["role"].s, name = a["name"].s;
                std::string addr;
                if (role == "leaf") { addr = fj["addr"].s; if (addr.find('/') != std::string::npos) addr = addr.substr(0, addr.find('/')); }
                else if (role == "participant") { addr = fj["addr"].s; if (addr.find('/') != std::string::npos) addr = addr.substr(0, addr.find('/')); }   // a reacher's /32
                else { auto sp = served_prefixes(fj); if (!sp.empty()) addr = *sp.begin() + ".1"; }
                if (addr.empty()) one = "{\"error\":\"ENOADDR\",\"why\":" + Json::quote(name + " (" + (role.empty() ? std::string("no role") : role) + ") has no address in this pond") + ",\"name\":" + Json::quote(name) + "}";
                else one = "{\"addr\":" + Json::quote(addr) + ",\"name\":" + Json::quote(name) + ",\"role\":" + Json::quote(role.empty() ? "node" : role) + "}";
            }
            out += (f ? "" : ",") + Json::quote(id) + ":" + one; f = false;
        }
        return out + "}}";
    }
    if (op == "participants") {
        std::string out = std::string("{\"ok\":true,\"server\":\"") + FDF_BUILD_TAG + "\",\"participants\":["; bool f = true;
        for (auto& r : scan(m, "ann", "")) {
            if (r.value.empty()) continue;
            const bool alive = !get(m, "node", r.rest).empty();
            out += (f ? "" : ",") + r.value.substr(0, r.value.size() - 1) + ",\"alive\":" + (alive ? "true" : "false") + ",\"id\":" + std::to_string(r.id) + "}"; f = false;   // [IPCP_FACTS_V1] id: first claimant wins
        }
        return out + "]}";
    }
    if (op == "node") {                        // a machine's agent: its devices, measured there; and, on a new session, rejoin
        const std::string ns = in["ns"].s, sid = sess_key(rina_session_id());
        const std::string cur = get(m, "node", ns), ep = cur.compare(0, 6, "epoch=") == 0 ? cur.substr(6, cur.find(';') - 6) : "";
        put(m, "sess", sid, "node:" + ns);
        put(m, "node", ns, "epoch=" + sid + ";" + in["devs"].s);
        std::string out, err; int rc = 0;
        if (ep != sid) { rc = ctl_run(m, ns, {"node-rejoin"}, out, err); }       // this machine's word starts a new epoch
        rmem::InputSpace* asp = rmem::spaces().get("agent:" + ns, true);   // [RINA_MEMORY_V1] the agent's input space:
        const uint64_t seen = asp ? asp->numbered() : 0;                  // an earlier agent's terminate is not this one's
        notify_agents(m);                                                 // [RMT_OVER_DIF_LINKS_V1] its devices may have changed every route
        notify_participants(m);                                           // [FDF_PARTICIPANTS_CHANGE_V1] alive may have changed
        return "{\"ok\":true,\"rejoined\":" + std::string(ep != sid ? "true" : "false") + ",\"rc\":" + std::to_string(rc) + ",\"err\":" + Json::quote(err) +
               ",\"after\":" + std::to_string(seen) + "}";
    }
            if (op == "register") {
                Ipcp me; if (!pick(m, in["ns"].s, in["dif"].s, me)) return "{\"ok\":false,\"error\":\"ENXIO\"}";
                // [RESOLVE_ONLY_FOR_UDP4_V1] only a shim-udp4 DIF needs the name as an address: ask the program for it
                if (me.type == "shim-udp4" && in["ip"].type == Json::Null) return "{\"ok\":false,\"error\":\"NEEDIP\"}";
                if (me.type == "shim-udp4" && !in["ip_local"].b) return "{\"ok\":false,\"error\":\"EADDRNOTAVAIL\"}";
                hold(m, in["inbox"].s, sess_proc());
                put(m, "app", me.dif + "|" + in["appl"].s, "inbox=" + in["inbox"].s + ";ipcp=" + me.name + ";ns=" + in["ns"].s + ";type=" + me.type +
                    ";ip=" + (me.type == "shim-udp4" ? in["ip"].s : std::string()) + ";pid=" + in["pid"].s + ";");
                return "{\"ok\":true,\"dif\":" + Json::quote(me.dif) + "}";
            }
            if (op == "alloc") {
                const std::string remote = in["remote"].s, dif_name = in["dif"].s, want_ip = in["want_ip"].s;
                {   // [RESOLVE_ONLY_FOR_UDP4_V1] the remote name as an address is needed only across a shim-udp4 DIF
                    Ipcp chk; if (in["want_ip"].type == Json::Null && pick(m, in["ns"].s, dif_name, chk) && chk.type == "shim-udp4")
                        return "{\"ok\":false,\"error\":\"NEEDIP\"}"; }
                Ipcp me; const bool have = pick(m, in["ns"].s, dif_name, me);
                std::string reg, rdif;
                auto take = [&](const Rec& r) { reg = r.value; rdif = r.rest.substr(0, r.rest.find('|')); };
                if (have) { const std::string v = get(m, "app", me.dif + "|" + remote); if (app_live(m, me.dif + "|" + remote, v)) { reg = v; rdif = me.dif; } }
                if (reg.empty() && have && (me.type == "shim-eth" || me.type == "shim-udp4"))   // across the link
                    for (auto& r : scan(m, "app", "")) {
                        if (r.rest.substr(r.rest.rfind('|') + 1) != remote || field(r.value, "type") != me.type || !app_live(m, r.rest, r.value)) continue;
                        if (me.type == "shim-udp4" && (want_ip.empty() || field(r.value, "ip") != want_ip)) continue;
                        take(r); break;
                    }
                if (reg.empty() && dif_name.empty()) for (auto& r : scan(m, "app", "")) if (r.rest.substr(r.rest.rfind('|') + 1) == remote && app_live(m, r.rest, r.value)) { take(r); break; }
                if (reg.empty()) return "{\"ok\":false,\"error\":\"ENOENT\"}";                 // nobody registered
                const std::string inbox = field(reg, "inbox"), dip = field(reg, "ipcp");
                if (me.type == "normal" && rdif == me.dif && !path_up(m, me.dif, me.name, dip)) return "{\"ok\":false,\"error\":\"EHOSTUNREACH\"}";
                const std::string owner = holder(m, inbox);                      // the program that holds the registrant's inbox
                if (owner.empty() || get(m, "proc", owner).empty()) return "{\"ok\":false,\"error\":\"ENOENT\"}";   // gone with its session
                ev(m, owner, "r:" + in["fid"].s, "{\"k\":\"r\",\"inbox\":" + Json::quote(inbox) + ",\"fid\":" + Json::quote(in["fid"].s) +
                   ",\"src\":" + Json::quote(in["src"].s) + ",\"spec\":" + Json::quote(in["spec"].s) + ",\"req\":" +
                   Json::quote("src=" + in["src"].s + ";dst=" + remote + ";sip=" + (have ? me.name : std::string()) + ";cpid=" + in["cpid"].s +
                               ";cns=" + in["ns"].s + ";cproc=" + in["cproc"].s + ";cloc=" + in["cloc"].s + ";dif=" + rdif + ";") + "}");   // [MEMBER_TO_MEMBER_DATA_V1] cloc: where the allocator receives
                return "{\"ok\":true,\"inbox\":" + Json::quote(inbox) + ",\"dip\":" + Json::quote(dip) + ",\"rdif\":" + Json::quote(rdif) + "}";
            }
            if (op == "ctl") {                                    // one rlite-ctl command, run at the memory
                std::vector<std::string> args; for (auto& a : in["args"].a) args.push_back(a.s);
                std::string out, err; const int rc = ctl_run(m, in["ns"].s, args, out, err);
                notify_agents(m);                                 // [RMT_OVER_DIF_LINKS_V1] an enrollment, a lower flow, a device...
                return "{\"ok\":true,\"rc\":" + std::to_string(rc) + ",\"out\":" + Json::quote(out) + ",\"err\":" + Json::quote(err) + "}";
            }
            if (op == "accept") {
                // ONE call: the acceptor answers the request it was given in its event stream. Positive: the flow's record
                // (what rlite-ctl shows), its routing (who is at each end), and the verdict into the allocator's stream.
                const std::string fid = in["fid"].s, inbox = in["inbox"].s, me_p = sess_proc();
                const std::string reqbag = in["req"].s, dst = in["dst"].s, cproc = field(reqbag, "cproc");
                if (in["verdict"].s == "0") {
                    std::string dif, dip;
                    for (auto& r : scan(m, "app", "")) if (field(r.value, "inbox") == inbox && r.rest.substr(r.rest.rfind('|') + 1) == dst) { dif = r.rest.substr(0, r.rest.find('|')); dip = field(r.value, "ipcp"); break; }
                    put(m, "flow", dif + "|" + fid, "src=" + field(reqbag, "src") + ";sip=" + field(reqbag, "sip") + ";cpid=" + field(reqbag, "cpid") + ";cns=" + field(reqbag, "cns") +
                        ";dst=" + dst + ";dip=" + dip + ";spid=" + in["spid"].s + ";sns=" + in["sns"].s + ";cproc=" + cproc + ";sproc=" + me_p + ";");
                    put(m, "fl", fid, "c=" + cproc + ";s=" + me_p + ";");
                    put(m, "pfl:" + cproc, fid, "1"); put(m, "pfl:" + me_p, fid, "1");   // [LAZY_CLEANUP_V1] in each program's own variable
                }
                ev(m, cproc, "v:" + fid, "{\"k\":\"v\",\"fid\":" + Json::quote(fid) + ",\"v\":" + Json::quote(in["verdict"].s) +
                   ",\"sloc\":" + Json::quote(in["sloc"].s) + ",\"dif\":" + Json::quote(field(reqbag, "dif")) + "}");   // [MEMBER_TO_MEMBER_DATA_V1] where the acceptor receives
                return "{\"ok\":true}";
            }
            if (op == "send") {
                // ONE call, never waited for (posted): every SDU the program had queued, each into the peer program's event
                // stream, in the order sent. A flow's SDUs occupy a ring of RING cells; the sender never has more than RING
                // unconsumed (it holds credit, which comes back in its own stream), so no cell is overwritten unread.
                const std::string me_p = sess_proc();
                std::string last_fid, peer;
                // [FLAT_SEND_V1] (plan item 5) one SDU as a flat body {"fid","q","d"}: BLDC-1 then diffs the fields, so an
                // unchanged flow id is not re-sent with every SDU (an array is ONE atomic value to it). Same handling below.
                std::vector<Json> one;
                if (in["sdus"].type != Json::Arr && in["fid"].type == Json::Str) {
                    Json x; x.type = Json::Arr; Json f; f.type = Json::Str; f.s = in["fid"].s; Json q; q.type = Json::Num; q.n = in["q"].n;
                    Json d; d.type = Json::Str; d.s = in["d"].s; x.a = {f, q, d}; one.push_back(x); }
                for (auto& x : (one.empty() ? in["sdus"].a : one)) {
                    const std::string& fid = x[0].s;
                    if (fid != last_fid) { const std::string r = get(m, "fl", fid); last_fid = fid;
                        peer = field(r, "c") == me_p ? field(r, "s") : field(r, "c"); }
                    if (peer.empty()) { put(m, "dropped", fid + "|" + me_p, "fl=" + get(m, "fl", fid)); continue; }   // a flow that is gone: dropped, as rlite drops it -- and recorded
                    const uint64_t q = uint64_t(x[1].n);
                    if (x.a.size() >= 5) {                     // [SDU_BY_REFERENCE_V1] the SDU this program was given on this flow
                        // (its sequence number in MY flow direction) is written back: the peer's event holds the reference
                        ev(m, peer, "", "{\"k\":\"s\",\"fid\":" + Json::quote(fid) + ",\"q\":" + std::to_string(q) +
                           ",\"rp\":" + Json::quote(me_p) + ",\"rq\":" + std::to_string((unsigned long long)x[3].n) + "}");
                        continue;
                    }
                    const std::string dq = Json::quote(x[2].s);
                    if (rmem::FlowDir* fd = rmem::flows().get(fid + "|" + peer, true)) fd->keep(q, dq);   // [RINA_MEMORY_V1] where a reference finds it
                    ev(m, peer, "", "{\"k\":\"s\",\"fid\":" + Json::quote(fid) + ",\"q\":" + std::to_string(q) + ",\"d\":" + dq + "}");
                }
                return "{\"ok\":true}";
            }
            if (op == "unregister") {                                          // ONE call: picked and removed at the memory
                Ipcp me; if (!pick(m, in["ns"].s, in["dif"].s, me)) return "{\"ok\":false,\"error\":\"ENXIO\"}";
                if (get(m, "app", me.dif + "|" + in["appl"].s).empty()) return "{\"ok\":false,\"error\":\"ENOENT\"}";
                put(m, "app", me.dif + "|" + in["appl"].s, ""); return "{\"ok\":true}";
            }
            if (op == "wait") return wait_op(in["proc"].s, uint64_t(in["after"].n));
            throw std::runtime_error("unknown op " + op);
}
}  // namespace


// ---- [KNOWN_SHAPE_V1] the three operations on the SDU path, read where they lie -----------------------------------------
// We know what these bodies look like (the library writes them; the host's codec renders them from the template, in the
// template's field order): {"op":"wait","proc":"<id>","after":<n>}, {"op":"send","sdus":[["<fid>",<q>,"<data>"(,<rq>,<rid>)]]},
// {"op":"credit","fid":"<fid>","proc":"<id>","have":<n>}. So they are not parsed into a JSON document: the fields are
// found in place (string_views into the body), the SDU's data is used as the JSON string token it already is (it goes
// into the event as that token: no unquote, no requote), and the per-flow route -- the peer, its input space, the flow
// direction -- is held, found by one lookup (a held view: [ROUTE_HELD_V1]). Anything that does not look exactly like
// this goes the ordinary way (parsed), so nothing depends on the guess being right.
namespace ks {
inline bool str_at(std::string_view b, std::string_view key, std::string_view& out) {   // "key":"value" (no escapes)
    const size_t k = b.find(key); if (k == std::string_view::npos) return false;
    size_t i = k + key.size(); if (i >= b.size() || b[i] != '"') return false; ++i;
    const size_t e = b.find('"', i); if (e == std::string_view::npos || b.substr(i, e - i).find('\\') != std::string_view::npos) return false;
    out = b.substr(i, e - i); return true;
}
inline bool num_at(std::string_view b, std::string_view key, uint64_t& out) {           // "key":<digits>
    const size_t k = b.find(key); if (k == std::string_view::npos) return false;
    size_t i = k + key.size(); if (i >= b.size() || b[i] < '0' || b[i] > '9') return false;
    uint64_t v = 0; while (i < b.size() && b[i] >= '0' && b[i] <= '9') v = v * 10 + uint64_t(b[i++] - '0');
    out = v; return true;
}
inline size_t json_string_end(std::string_view b, size_t i) {                             // b[i] == '"': index past the closing quote
    for (++i; i < b.size(); ++i) { if (b[i] == '\\') { ++i; continue; } if (b[i] == '"') return i + 1; }
    return std::string_view::npos;
}
}  // namespace ks
// [ROUTE_HELD_V1] a flow's route as seen by its sender, made once: the peer program, the peer's input space, the flow direction
// state: 0 empty, 1 being filled (by the one thread that won it), 2 ready. Filled once; read freely after.
struct Route { std::string peer; rmem::InputSpace* sp = nullptr; rmem::FlowDir* fd = nullptr; std::atomic<int> state{0}; };
inline rmem::Table<Route>& routes() { static rmem::Table<Route>* t = new rmem::Table<Route>; return *t; }
// the sender's program, by session (sess_proc() reads and unquotes a record on every call; this is held)
struct SessProc { std::string proc; std::atomic<int> state{0}; };   // as Route; reset when the session ends ([SESSION_EPOCH_V1] ids are unique now; the entry is cleared, never matched again)
inline rmem::Table<SessProc>& sessprocs() { static rmem::Table<SessProc>* t = new rmem::Table<SessProc>; return *t; }
const std::string& held_sess_proc() {
    thread_local std::string key; key.assign(std::to_string((unsigned long long)rina_session_id()));
    SessProc* sp = sessprocs().get(key, true);
    if (sp->state.load(std::memory_order_acquire) == 2) return sp->proc;
    thread_local std::string mine; mine = sess_proc();          // not held yet (or being filled): read it, and offer to hold it
    int expect = 0;
    if (!mine.empty() && sp->state.compare_exchange_strong(expect, 1, std::memory_order_acq_rel)) { sp->proc = mine; sp->state.store(2, std::memory_order_release); }
    return mine;
}
std::atomic<uint64_t> g_fast_send{0}, g_fast_wait{0}, g_parsed{0};
std::string fast_send(std::string_view b) {
    frogram::MemoryApi& m = region_memory();
    const std::string& me_p = held_sess_proc();
    const size_t s0 = b.find("\"sdus\":["); if (s0 == std::string_view::npos || me_p.empty()) return std::string();
    size_t i = s0 + 8;
    thread_local std::string key, bag;
    while (i < b.size() && b[i] == '[') {
        ++i;
        if (b[i] != '"') { return std::string(); }
        const size_t fe = ks::json_string_end(b, i); if (fe == std::string_view::npos) return std::string();
        const std::string_view fid = b.substr(i + 1, fe - i - 2); if (fid.find('\\') != std::string_view::npos) return std::string();
        i = fe;
        if (b[i] != ',') { return std::string(); }
        ++i;
        uint64_t q = 0;
        if (b[i] < '0' || b[i] > '9') { return std::string(); }
        while (b[i] >= '0' && b[i] <= '9') { q = q * 10 + uint64_t(b[i++] - '0'); }
        if (b[i] != ',') { return std::string(); }
        ++i;
        if (b[i] != '"') return std::string();
        const size_t de = ks::json_string_end(b, i); if (de == std::string_view::npos) return std::string();
        const std::string_view dq = b.substr(i, de - i);        // the data as its JSON string token
        i = de;
        bool ref = false; uint64_t rq = 0;
        if (b[i] == ',') {
            ++i; ref = true;
            while (b[i] >= '0' && b[i] <= '9') { rq = rq * 10 + uint64_t(b[i++] - '0'); }
            if (b[i] != ',') { return std::string(); }
            ++i;
            while (b[i] >= '0' && b[i] <= '9') { ++i; }
        }
        if (b[i] != ']') { return std::string(); }
        ++i;
        // the route, held
        key.assign(fid.data(), fid.size()); key += '|'; key += me_p;
        Route* r = routes().get(key, true);
        Route local; Route* use = r;
        if (r->state.load(std::memory_order_acquire) != 2) {        // not held yet: work it out, and offer to hold it
            const std::string rec = get(m, "fl", std::string(fid));
            const std::string peer = field(rec, "c") == me_p ? field(rec, "s") : field(rec, "c");
            if (peer.empty()) { put(m, "dropped", std::string(fid) + "|" + me_p, "fl=" + rec); if (b[i] == ',') { ++i; } continue; }
            local.peer = peer; local.sp = rmem::spaces().get(peer, true); local.fd = rmem::flows().get(std::string(fid) + "|" + peer, true);
            int expect = 0;
            if (r->state.compare_exchange_strong(expect, 1, std::memory_order_acq_rel)) { r->peer = local.peer; r->sp = local.sp; r->fd = local.fd; r->state.store(2, std::memory_order_release); }
            else use = &local;
        }
        bag.clear(); bag += "{\"k\":\"s\",\"fid\":\""; bag.append(fid.data(), fid.size()); bag += "\",\"q\":"; bag += std::to_string(q);
        if (ref) { bag += ",\"rp\":"; bag += Json::quote(me_p); bag += ",\"rq\":"; bag += std::to_string(rq); bag += '}'; }
        else { if (use->fd) { use->fd->keep(q, std::string(dq)); } bag += ",\"d\":"; bag.append(dq.data(), dq.size()); bag += '}'; }
        use->sp->push(bag); answer_parked_wait(m, use->sp);
        if (b[i] == ',') ++i;
    }
    g_fast_send.fetch_add(1, std::memory_order_relaxed);
    return "{\"ok\":true}";
}
std::string rina_region_call(const std::string& body) {
    if (body.find("\"op\":\"send\"") != std::string::npos) { std::string r = fast_send(body); if (!r.empty()) return r; }   // [KNOWN_SHAPE_V1] (any field order)
    g_parsed.fetch_add(1, std::memory_order_relaxed);
    Json in = Json::parse(body); return resident(in["op"].s, in);
}
[[maybe_unused]] static const bool g_ks_report = [] { std::atexit([] { if (getenv("RINA_DIAG")) fprintf(stderr, "[KNOWN_SHAPE_V1] sends read in place %llu, waits read in place %llu, other calls parsed %llu\n",
    (unsigned long long)g_fast_send.load(), (unsigned long long)g_fast_wait.load(), (unsigned long long)g_parsed.load()); }); return true; }();
// [CREDIT_ON_DEMAND_V1] op=rina_block credit {fid, proc, have}: the consumed count of proc's SDUs on fid, once it is past
// `have` -- a held read on that one cell, answered when the receiver has taken more.
static std::string credit(const Json& in) {
    // [RINA_MEMORY_V1] the credit of the caller's SDUs on fid, from the flow direction toward its peer.
    // [DEFERRED_ANSWER_V1] not past `have` yet: the request parks as a continuation, answered by the taking that frees it
    frogram::MemoryApi& m = region_memory();
    const std::string fid = in["fid"].s, me = in["proc"].s, r = get(m, "fl", fid);
    const std::string peer = field(r, "c") == me ? field(r, "s") : field(r, "c");
    rmem::FlowDir* fd = peer.empty() ? nullptr : rmem::flows().get(fid + "|" + peer, true);
    const uint64_t have = uint64_t(in["have"].n);
    if (!fd || fd->gone()) return "{\"ok\":false,\"error\":\"gone\"}";   // [HELD_READS_END_WITH_THE_SESSION_V1]
    if (fd->credit() > have) return "{\"ok\":true,\"n\":" + std::to_string(fd->credit()) + "}";
    void* h = rina_defer(); if (!h) throw std::runtime_error("credit: no continuation to park (not inside a request)");
    fd->park(new rmem::Parked{h, have, me});
    if (fd->credit() > have || fd->gone()) answer_parked_credit(fd);   // it moved meanwhile
    return "";
}
std::string rina_region_block(const std::string& body) {
    if (body.find("\"op\":\"wait\"") != std::string::npos) { std::string_view p; uint64_t a = 0;   // [KNOWN_SHAPE_V1] (any field order)
        if (ks::str_at(body, "\"proc\":", p) && ks::num_at(body, "\"after\":", a)) { g_fast_wait.fetch_add(1, std::memory_order_relaxed); return wait_op(std::string(p), a); } }
    Json in = Json::parse(body);
    if (in["op"].s == "credit") return credit(in);
    return resident(in["op"].s, in);
}

// The server calls this once when a session's socket has closed: whoever it was is gone, at once.
void rina_session_down(uint64_t sid) {
    if (SessProc* hp = sessprocs().get(sess_key(sid), false)) hp->state.store(0, std::memory_order_release);   // [KNOWN_SHAPE_V1] ids are reused
    frogram::MemoryApi& m = region_memory();
    // [SESSION_SWEEP_ALWAYS_V1] a session that never said hello/node (a watcher, a registrant) still owns watches and
    // session-bound mappings: the sweeps below run for every session; only the proc/node branches need `who`.
    const std::string who = get(m, "sess", sess_key(sid));
    if (who.compare(0, 5, "proc:") == 0) {
        const std::string p = who.substr(5); put(m, "proc", p, "");
        // [FORK_KEEPS_REGISTRATIONS_V1] an input space's names go when the LAST program holding it is gone -- a forked
        // child (announced by its parent before the fork) keeps them after the parent exits
        // [LAZY_CLEANUP_V1] only this program's own index: its holds (its registrations go, lazily, when found
        // unheld) and its flows (their credit waits end now)
        for (auto& h : scan(m, "held:" + p, "")) unhold(m, h.rest, p);   // the memory indexes the variable: only its own cells are read
        // [HELD_READS_END_WITH_THE_SESSION_V1] every read this program left held ends now. Each parked request keeps its
        // session -- and the session's sockets -- alive until it returns; a held read that never returns leaked three
        // descriptors per program until the host reached its open-file limit (1,024) and could take no new session.
        ev(m, p, "x:gone", "{\"k\":\"x\"}");                                 // its wait
        if (rmem::InputSpace* sp = rmem::spaces().get(p, false)) sp->end();      // [RINA_MEMORY_V1] and every later one returns at once
        for (auto& f : scan(m, "pfl:" + p, "")) {   // [RINA_MEMORY_V1] the flows it received on: their senders' credit waits end
            if (rmem::FlowDir* fd = rmem::flows().get(f.rest + "|" + p, false)) { fd->end(); answer_parked_credit(fd); }
            put(m, "pfl:" + p, f.rest, ""); }
    }
    else if (who.compare(0, 5, "node:") == 0) { const std::string ns = who.substr(5), cur = get(m, "node", ns);
        if (cur.compare(0, 6 + sess_key(sid).size(), "epoch=" + sess_key(sid)) == 0) { put(m, "node", ns, ""); notify_agents(m);
            // [ANN_GOES_WITH_THE_SESSION_V1] gone is gone: the announcement goes with the machine's session. Every reader
            // (fdf_graph, graph_apply, fdf-hub, gen, region.py) already skipped a dead one, so nothing plans differently;
            // what changes is that dead rows no longer pile up for the life of the RAM manager (the stale
            // FrogNetHost.Seattle* duplicates from before ME_BY_GUID) and ride along on every member's `world` read.
            put(m, "ann", ns, "");
            // [ATTENTION_IS_A_TUPLE_V1] the member is gone, so are its notices: (pond, attention, <its ns>) -- gone is gone
            // [SERVICE_WHERE_IS_A_TUPLE_V1] and so are the services it said it runs: (pond, where, <its ns>)
            try { frogram::MemoryApi& ram = rina_resident_memory();
                  for (const char* v : {"attention", "where"}) for (auto& c : ram.read("pond", v, ns)) ram.remove(c.id); } catch (...) {}   // the RAM, not the region's records
        } }   // only if no newer agent took over; [RMT_OVER_DIF_LINKS_V1] its links are gone
    put(m, "sess", sess_key(sid), "");
    put(m, "speer", sess_key(sid), "");                              // [SESSION_PEER_V1]
    for (auto& r : scan(m, "pwatch", "")) if (r.value == sess_key(sid)) put(m, "pwatch", r.rest, "");
    for (auto& r : scan(m, "mwatch", "")) if (r.value == sess_key(sid)) put(m, "mwatch", r.rest, "");   // [MAP_WATCH_V1] a watcher gone   // [FDF_PARTICIPANTS_CHANGE_V1] a watcher gone
    for (auto& r : scan(m, "laddr", "")) if (r.value == sess_key(sid)) put(m, "laddr", r.rest, "");       // [LEAF_ADDR_FROM_THE_REGION_V1] its /32 is free
    for (auto& r : scan(m, "mown", "")) if (r.value == sess_key(sid)) {                                   // [MAP_PREFIX_LOCATOR_SET_V1] its mappings go with it
        put(m, "mown", r.rest, ""); put_gone_permanent(m, "map", r.rest);   // [MAP_REMOVAL_IS_PERMANENT_V1]
        for (auto& w : scan(m, "mwatch", r.rest + "|")) if (!w.value.empty()) push_event(w.rest.substr(r.rest.size() + 1), "{\"k\":\"m\"}");
        for (auto& w : scan(m, "mwatch", "*|")) if (!w.value.empty()) push_event(w.rest.substr(2), "{\"k\":\"m\"}"); }
    notify_participants(m);                                          // [FDF_PARTICIPANTS_CHANGE_V1] a participant may have gone
}

// [RAM_MANAGER_REGISTERS_ITSELF_V1] (plan item 3) the RAM manager registers its own LISP mapping -- the area's name ->
// the address it listens on -- with the mapping region, over the session, the moment it is listening (a start, a
// restart, a move). Configured on the RAM manager's own machine: RINA_MAP_AREA (the area's name), RINA_MAP_RESOLVER
// (where the mapping region is) and RINA_AREA_PASSWORD (the area's password). Anything missing: said, nothing registered.
static void register_self(const std::string& listen_on) {
    const char* area = getenv("RINA_MAP_AREA"); const char* key = getenv("RINA_AREA_PASSWORD");
    if (!area || !*area) return;      // [QUIET_DIRECT_V1] no area configured (direct mode): no registration intended, nothing to say
    const char* why = (!key || !*key) ? "RINA_AREA_PASSWORD is not set" : nullptr;
    if (why) { fprintf(stderr, "rina-host: [RAM_MANAGER_REGISTERS_ITSELF_V1] the area's name is NOT registered: %s\n", why); return; }
    const std::string ip = listen_on.substr(0, listen_on.rfind(':'));
    std::vector<std::string> mrs;                              // [MR_FROM_THE_AREA_NAME_V1] explicit, or the area's SRV record
    try { mrs = rina_region::map_resolvers(area); } catch (const std::exception& e) {
        fprintf(stderr, "rina-host: [RAM_MANAGER_REGISTERS_ITSELF_V1] the area's name is NOT registered: %s\n", e.what()); return; }
    for (const std::string& mr : mrs) {                        // every map-resolver of the area holds the registration
        try { rina_region::map_register(area, ip, mr, key, 86400);
              fprintf(stderr, "rina-host: [RAM_MANAGER_REGISTERS_ITSELF_V1] registered '%s' -> %s in the mapping region at %s\n", area, ip.c_str(), mr.c_str()); }
        catch (const std::exception& e) { fprintf(stderr, "rina-host: [RAM_MANAGER_REGISTERS_ITSELF_V1] at %s: %s\n", mr.c_str(), e.what()); } }
}
// [NET_CONFIG_IS_A_TUPLE_V1] (John 2026-10-10: "a well-known configuration tuple per network") what this network allows
// an outside member: (pond, config, net), written here at start from net.json (RINA_NET_CONFIG, default
// /opt/fdf/etc/net.json) and only here -- the network API can neither write nor remove it
// ([NET_CONFIG_IS_THE_RAM_MANAGERS_V1]). reach: "none" (its RAM only), "listed" (only the addresses its where cells
// name), "all" (every /24 its members serve); map: "allowed" (a reader whose prefixes collide may alias this network
// 1:1) or "refused". No file: reach none, map refused, and it says so. A file that does not parse, or a value outside
// those: nothing is published and it says why -- a reader then finds no config and its reach fails, as it should.
// hub and leaves are this network's fixed overlay ranges (every pond the same: John 2026-10-10, no index; a reader
// whose own ranges are the same aliases them), published so readers take them from the network, never from a constant.
static void publish_net_config() {
    const char* e = getenv("RINA_NET_CONFIG"); const std::string path = e && *e ? e : "/opt/fdf/etc/net.json";
    std::string reach = "none", map = "refused", ram, why;
    if (FILE* f = fopen(path.c_str(), "r")) {
        std::string t; char b[4096]; size_t n; while ((n = fread(b, 1, sizeof b, f)) > 0) t.append(b, n); fclose(f);
        Json j; try { j = Json::parse(t); } catch (const std::exception& x) { why = std::string("does not parse: ") + x.what(); }
        if (why.empty() && j.type != Json::Obj) why = "is not a JSON object";
        if (why.empty()) {
            if (j["reach"].type != Json::Null) reach = j["reach"].s;
            if (j["map"].type != Json::Null) map = j["map"].s;
            ram = j["ram"].s;
            if (reach != "none" && reach != "listed" && reach != "all") why = "reach is \"" + reach + "\" (none, listed or all)";
            else if (map != "allowed" && map != "refused") why = "map is \"" + map + "\" (allowed or refused)";
        }
        if (!why.empty()) { fprintf(stderr, "rina-host: [NET_CONFIG_IS_A_TUPLE_V1] %s %s: NO network config published (outsiders' reaches fail)\n", path.c_str(), why.c_str()); return; }
    } else fprintf(stderr, "rina-host: [NET_CONFIG_IS_A_TUPLE_V1] no %s: reach none, map refused\n", path.c_str());
    const char* area = getenv("RINA_MAP_AREA");
    const std::string bag = std::string("{\"area\":") + Json::quote(area ? area : "") + ",\"reach\":" + Json::quote(reach) + ",\"map\":" + Json::quote(map) +
        ",\"hub\":\"10.253.253.254\",\"edges\":\"10.253.0.0/16\",\"leaves\":\"10.252.0.0/16\"" + (ram.empty() ? "" : ",\"ram\":" + Json::quote(ram)) +
        ",\"server\":\"" + FDF_BUILD_TAG + "\"}";
    try {
        frogram::MemoryApi& m = rina_resident_memory();
        std::vector<uint64_t> old; for (auto& c : m.read("pond", "config", "net")) old.push_back(c.id);
        const uint64_t id = m.write("pond", "config", "net", bag);
        for (uint64_t o : old) if (o != id) m.remove(o);
        fprintf(stderr, "rina-host: [NET_CONFIG_IS_A_TUPLE_V1] (pond, config, net) = %s\n", bag.c_str());
    } catch (const std::exception& x) { fprintf(stderr, "rina-host: [NET_CONFIG_IS_A_TUPLE_V1] could not write the network config: %s\n", x.what()); }
}
// [HOST_ANNOUNCES_ITSELF_V1] superseded (John, 2026-10-07: no direct propagation between machines). Called by the host
// once it is listening; all it does now is register its own LISP mapping ([RAM_MANAGER_REGISTERS_ITSELF_V1]). Members
// that lost it learn it is back by waiting on the mapping ([MAP_WATCH_V1]).
void rina_host_listening(const std::string& listen_on) {
    fprintf(stderr, "rina-host: [FDF_VERSION_V1] build %s listening on %s\n", FDF_BUILD_TAG, listen_on.c_str());
    if (!getenv("RINA_HOST_ROLE") || std::string(getenv("RINA_HOST_ROLE")) != "mapping") {   // the mapping host is not an area's
        publish_net_config();
        std::thread(register_self, listen_on).detach();
    }
}

// [SESSION_PEER_V1] the server tells the region where each session arrives from, once, at "session up"
void rina_session_peer(uint64_t sid, const std::string& peer) { put(region_memory(), "speer", sess_key(sid), peer); }
