// daf_client.hpp -- [DAF_MODEL_V1] a program's session to the DAF region (op=daf) of a RAM host. The session is the
// program's liveness: what it enrolled and joined for the session goes when the session ends. Watching is a held read
// on the memory itself: (daf, nv|<D>, <name>) is written on every change to the name.
#pragma once
#include "frogram.hpp"
#include <atomic>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <stdexcept>
#include <string>
namespace daf {
inline const std::string& api() { static const std::string a = "/FawcettInnovations.RINA.ram_interface.php"; return a; }
using frogram::Json;
inline std::string q(const std::string& s) { return Json::quote(s); }
class Client {
public:
    explicit Client(const std::string& where) {
        const size_t c = where.rfind(':'); if (c == std::string::npos) throw std::runtime_error("daf: '" + where + "' has no port");
        s_.reset(new frogram::Session(where.substr(0, c), atoi(where.c_str() + c + 1), api()));
        m_.reset(new frogram::Memory(*s_, api()));
    }
    // one DAF operation: {"fn": fn, ...fields}; an answer of ok:false is the answer, handed back
    Json call(const std::string& fn, const std::string& fields = "") {
        const std::string body = "{\"fn\":" + q(fn) + (fields.empty() ? "" : "," + fields) + "}";
        return Json::parse(s_->call_text("POST", api() + "?op=daf", body));   // the answer whole, error and why included
    }
    // wait (up to wait_s) for a change to a name after `rev`; returns the new rev, or 0 if nothing changed
    uint64_t watch(const std::string& D, const std::string& name, uint64_t rev, double wait_s) {
        auto cells = m_->read("daf", "nv|" + D, name, int64_t(rev), wait_s);
        uint64_t n = 0; for (auto& c : cells) n = std::max<uint64_t>(n, c.id); return n;
    }
    frogram::Memory& memory() { return *m_; }
    frogram::Stats stats() const { return s_->stats(); }
private:
    std::unique_ptr<frogram::Session> s_; std::unique_ptr<frogram::Memory> m_;
};
// [DAF_WORKING_SET_V1] (plan E) a task's working set: the names it declares are resolved once, then kept current by a
// held read per name on a session of its own; a read of the set is local -- no round trip, nothing waits.
class WorkingSet {
public:
    // apn and token: the enrolled member's, from its enrollment; each watcher is a further connection of that member
    WorkingSet(const std::string& where, const std::string& D, std::vector<std::string> names, const std::string& apn, const std::string& token)
        : where_(where), D_(D), names_(std::move(names)) {
        for (auto& n : names_) { auto c = std::make_unique<Client>(where_);          // each watcher: its own session
            Json e = c->call("connect", "\"daf\":" + q(D_) + ",\"ae_name\":" + q("working-set:" + n) + ",\"objects\":[" + q(n) + "],\"apn\":" + q(apn) + ",\"token\":" + q(token));
            if (!e["ok"].b) throw std::runtime_error("working set: connect refused: " + e["why"].s);
            Json r = c->call("resolve", "\"daf\":" + q(D_) + ",\"name\":" + q(n));
            if (!r["ok"].b) throw std::runtime_error("working set: " + n + ": " + r["why"].s);
            { std::lock_guard<std::mutex> l(m_); cache_[n] = r; }
            Client* cp = c.get(); clients_.push_back(std::move(c));
            threads_.emplace_back([this, cp, n, rev = uint64_t(r["rev"].n)]() mutable {
                // a held read that ends with nothing written is the 1 s wait running out (so the set can be closed):
                // it is asked again from the same place; any other failure ends the watcher, and says so
                while (!stop_) { uint64_t got = 0;
                    try { got = cp->watch(D_, n, rev, 1.0); }
                    catch (const std::exception& x) { const std::string w = x.what(); if (w.find("imeout") != std::string::npos || w.find("timed out") != std::string::npos) continue;
                        fprintf(stderr, "[DAF_WORKING_SET_V1] watcher for %s ended: %s\n", n.c_str(), x.what()); return; }
                    if (!got || stop_) continue; rev = got;
                    Json r2 = cp->call("resolve", "\"daf\":" + q(D_) + ",\"name\":" + q(n));
                    if (r2["ok"].b) { std::lock_guard<std::mutex> l(m_); cache_[n] = r2; ++updates_; } } });
        }
    }
    ~WorkingSet() { stop_ = true; for (auto& t : threads_) t.join(); }
    Json get(const std::string& name) { std::lock_guard<std::mutex> l(m_); return cache_[name]; }   // local
    uint64_t updates() const { return updates_; }
private:
    std::string where_, D_; std::vector<std::string> names_; std::mutex m_; std::map<std::string, Json> cache_;
    std::vector<std::unique_ptr<Client>> clients_; std::vector<std::thread> threads_; std::atomic<bool> stop_{false}; std::atomic<uint64_t> updates_{0};
};
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
