// daf_client.hpp -- [DAF_MODEL_V1] a program's session to the DAF region (op=daf) of a RAM host. The session is the
// program's liveness: what it enrolled and joined for the session goes when the session ends. [DAF_WAIT_V1] Watching is
// the region's own held read (fn "wait"), through the check point: answered by the write that makes its answer exist,
// or ended with the session. A program waits on a session of its own; shutdown() ends it.
// [DAF_FROG_V1] The region is handed to the program as a library: Client() finds the area's region the way every FD&F
// participant does (rina_region::Client::locate(): RINA_RAM, or the area's one name), so no program names a host. A
// locator is passed only when it was itself read from the memory (a DIF Allocator answer names the region that offers
// an application) or by a harness that plays several machines. No timeouts, no retries: a wait is answered by the write
// that makes its answer exist, or ends with its session.
#pragma once
#include "frogram.hpp"
#include "rina_region_client.hpp"
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <string>
namespace daf {
inline const std::string& api() { static const std::string a = "/FawcettInnovations.RINA.ram_interface.php"; return a; }
using frogram::Json;
inline std::string q(const std::string& s) { return Json::quote(s); }
class Client {
public:
    Client() : Client(rina_region::Client::locate()) {}       // this area's region
    explicit Client(const std::string& where) {
        const size_t c = where.rfind(':'); if (c == std::string::npos) throw std::runtime_error("daf: '" + where + "' has no port");
        s_.reset(new frogram::Session(where.substr(0, c), atoi(where.c_str() + c + 1), api()));
    }
    // one DAF operation: {"fn": fn, ...fields}; an answer of ok:false is the answer, handed back
    Json call(const std::string& fn, const std::string& fields = "") {
        const std::string body = "{\"fn\":" + q(fn) + (fields.empty() ? "" : "," + fields) + "}";
        return Json::parse(s_->call_text("POST", api() + "?op=daf", body));   // the answer whole, error and why included
    }
    // one DAF operation, sent without waiting for its answer: a writer that need not know the outcome does not hold
    // its thread for a round trip (the session keeps its order; a refusal is counted in the session's stats)
    void post(const std::string& fn, const std::string& fields = "") {
        s_->post("POST", api() + "?op=daf", "{\"fn\":" + q(fn) + (fields.empty() ? "" : "," + fields) + "}"); }
    // one DAF operation from a whole body ({"fn":...,...}); the answer's text as it came
    std::string raw(const std::string& body, double timeout_s = 15.0) { return s_->call_text("POST", api() + "?op=daf", body, timeout_s); }
    // [DAF_WAIT_V1] a held read: what = name | sub | ans | box | fd | in | req | out (daf_region.cpp, wait). The answer
    // {ok, rev, rows:[{i, id, v}]} comes when something there is past `after`; now=true answers at once (a plain read).
    // timeout_s: how long THIS caller waits for the answer (default: until answered or the session ends). On expiry the
    // call throws; the read stays parked at the host, and an identical wait joins it ([CLIENT_COALESCES_V1]).
    Json wait(const std::string& D, const std::string& what, const std::string& key, uint64_t after, bool now = false, const std::string& extra = "", double timeout_s = 1e9) {
        const std::string body = "{\"fn\":\"wait\",\"daf\":" + q(D) + ",\"what\":" + q(what) + ",\"key\":" + q(key) + ",\"after\":" + std::to_string((unsigned long long)after) +
                                 (now ? ",\"now\":true" : "") + (extra.empty() ? "" : "," + extra) + "}";
        return Json::parse(s_->call_text("POST", api() + "?op=daf", body, timeout_s));   // held until answered, the session ends, or timeout_s
    }
    Json peek(const std::string& D, const std::string& what, const std::string& key, uint64_t after = 0, const std::string& extra = "") { return wait(D, what, key, after, true, extra); }
    // watch a name: its new rev once it changes after `rev`; a refusal (the name or the DAF is gone, or not ours) throws
    uint64_t watch(const std::string& D, const std::string& name, uint64_t rev) {
        Json r = wait(D, "name", name, rev);
        if (!r["ok"].b) throw std::runtime_error("daf: watch " + D + " " + name + ": " + r["error"].s + " " + r["why"].s);
        return uint64_t(r["rev"].n);
    }
    void shutdown() { s_->shutdown(); }                 // ends this session: a wait parked on it returns by throwing
    frogram::Stats stats() const { return s_->stats(); }
private:
    std::unique_ptr<frogram::Session> s_;
};
// [DAF_NO_STATE_CACHE_V1] There is no working set: a program keeps no copy of application state. It reads the region
// when it needs a value, and holds a read (wait) on what it depends on to learn when it changed. (The transport's
// semantic message cache compresses repeated messages on the wire; it holds messages, not application state.)
// [DAF_ALLOCATOR_V1] (plan G) find the region that offers an application and join it. The search is a resolution in a
// directory DAF's "apps" space (where regions register what they offer); it ends at the owning region, whose own check
// point decides: enrollment (joined), an alternative (EREDIRECT, with where), or refusal -- reported as "not found".
struct Joined { bool ok = false; std::string error, why, region, daf; Json alternative, enrollment; std::unique_ptr<Client> session; };
inline Joined find_and_join(Client& directory, const std::string& dir_daf, const std::string& app, const std::string& apn, const std::string& role,
                            const std::string& facts = "{}") {
    Joined j; Json r = directory.call("ns_resolve", "\"daf\":" + q(dir_daf) + ",\"space\":\"apps\",\"name\":" + q(app));
    if (!r["ok"].b) { j.error = "ENOENT"; j.why = "not found"; return j; }
    j.region = r["object"]["region"].s; j.daf = r["object"]["daf"].s;
    j.session = std::make_unique<Client>(j.region);
    Json e = j.session->call("enroll", "\"daf\":" + q(j.daf) + ",\"apn\":" + q(apn) + ",\"role\":" + q(role) + ",\"facts\":" + facts);
    if (e["ok"].b) { j.ok = true; j.enrollment = e; return j; }
    if (e["error"].s == "EREDIRECT") { j.error = "EREDIRECT"; j.why = "the owner offers an alternative";
        const std::string w = e["why"].s; j.alternative = Json::parse(w.substr(w.find(':') + 1)); return j; }
    j.error = "ENOENT"; j.why = "not found"; j.session.reset(); return j;         // a refusal is not told apart from absence
}
}  // namespace daf
