// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// worldshepherd_ram.cpp -- worldshepherd-ram: the RAM host of the app worldshepherd. [THE_APPS_RAM_HOST_DECIDES_ITS_WRITES_V1]
//
// John, 2026-11-25, on who may write a cell: "That is an app problem and the ram host for the app should enforce it.
// Remember that every ram server is derived from the system, but they are not bound by it. This is all by design."
// Found by Brandon Davis reading ribbit_claim_store.py (2026-11-25): on the platform's general RAM host any session
// in WorldShepherd's memory could write claim.<anyone>.<n> saying "won": 1, with any digest and any time, and every
// SARA took it as the claim that consumed the ticket.
//
// This is the platform's RAM host (ribbit::RamHost) with WorldShepherd's tickets in it, written the way a lockless
// multithreaded program on one machine keeps a table of tickets. [A_TICKET_IS_ONE_RECORD_V1]
//
// (John, 2026-11-25. On 0.9, where every claimant wrote its claim into the ticket's family: "That's not froggish. One
// thread is writing into the process space of another thread." "The ticket should be writing into its own space,
// after it does what it needs to do. If you're going to go OO, go fully OO." On the first rebuild, which walked the
// service's log and took four calls a claim: "You do not have to use what is in ram_memory.hpp. You can derive or
// just write your own thing. You can combine things so there's only one call to the server." On 0.10, which kept the
// ticket as bookkeeping cells and read its state back as text on every call: "There should be no walks. No acks, no
// copying things around." "Stop now and restart. You have learned what you need to know about how the algorithms
// work. You have the contract and the validation. The rest is now greenfield. Write it like a single machine
// multithreaded lockless system.")
//
// A ticket is one record in this process, found by its id in a table that is only ever added to:
//     its issue    one word. Set once, by one compare-and-swap, from nothing to the issue (digest, expiry, issuer).
//     its winner   one word. Set once, by one compare-and-swap, from nothing to the claim that won.
//     its held     claims that say "held", made before its issue was (below).
// Nothing is locked, nothing is searched for but the ticket by its id, nothing is moved, and nothing is freed while
// the memory lives -- so there is nothing to reclaim and nobody to ask whether it is safe to.
//
// The ticket's two methods are this host's region operations. Each is one call on the caller's own session:
//     op=issue {"who","ticket","sha","exp","n"}            -> {"state": the ticket's state, or null}
//     op=claim {"who","ticket","sha","at","n"[,"held":1]}  -> {"outcome": claimed|consumed|digest|expired|absent, "state"}
//     op=again (a line {"who"}, then a line an issue or claim) -> every issue and every won claim of one SARA, in one call
// A claim is refused, in this order: no such ticket (absent); another digest; "at" past its expiry; a winner already.
// A call made again (its session ended under it) carries the same "n" and is answered as it was before.
//
// The ticket writes in its own space, two cells, each once ([EACH_FACT_ITS_OWN_CELL_V1]; John 2026-11-26: "You are not
// locked into any specific tuple indices. If the ones you have aren't working, use others."):
//     (worldshepherd, ticket, <ticket>)        {"sha","exp","by","bn"}   when it is issued
//     (worldshepherd, won.<issuer>, <ticket>)  {"won","wn","at"}         when it is won
// How a ticket stands is read, not asked: its issue by the ticket's name alone, then its win in the family its issue
// names -- two reads of one cell each. Its issuer holds one read on won.<itself> and is given each of its tickets as
// it is won. The answer to a call is made from the record, never read back from a cell.
//
// After the memory is replaced each SARA brings its own back in one call (op=again): its issues, and the claims it
// won, as held claims. A held claim made before its ticket's issue is kept on the ticket; the issue, when it is made,
// takes the first of them that names its digest within its expiry.
//
// [THE_ISSUER_SAYS_WHO_WON_V1] (the acceptance study's one OPEN control, on 0.9, 0.10 and the first 0.11: in a new
// memory, before the true winner returned, a member that said it held a ticket was taken for it, 4 of 4.) A ticket's
// cell is in its issuer's own family, and the issuer holds a read on that family: it sees each of its tickets won.
// When it makes a ticket again in a new memory it says who it saw win it, and the ticket is made consumed by that
// winner. A member that then says it held the ticket is answered consumed; the true winner, returning, is answered
// claimed. What is still open is the win the issuer had not yet seen when the memory was lost: for that ticket the
// first claim that says "held" is taken, as before. SARA's read receipts cannot close it: they are sealed with an
// unkeyed SHA-256 of their own fields (connector_receipt.py), which anyone who has the ticket can compute.
//
//   [A_SARA_WRITES_ONLY_ITS_OWN_SPACE_V1]  <who> belongs to the machine whose session first named it, for the life of
//       this memory, and while that session is up to that session alone. A call or a presence cell naming <who> from
//       another machine, or from another session while the first is up, is refused. The machine is the address the
//       session arrives from: on a FrogNet network that is the node's own address, which the network admitted by key.
//       The only cell a SARA writes itself is (worldshepherd, sara, <who>): it is here; it goes with its session.
//
//     worldshepherd-ram [--listen HOST:PORT] [--api PATH]      PATH: the endpoint fdf-app's clients use (fdfram.API)
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include "frogram.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

#define WORLDSHEPHERD_RAM_BUILD "worldshepherd-ram 0.11 2026-11-26j [THE_APPS_RAM_HOST_DECIDES_ITS_WRITES_V1] [A_TICKET_IS_ONE_RECORD_V1] [A_SARA_BRINGS_ITS_OWN_BACK_IN_ONE_CALL_V1] [THE_ISSUER_SAYS_WHO_WON_V1] [A_SARA_WRITES_ONLY_ITS_OWN_SPACE_V1]"

using frogram::Json;
static const std::string SVC = "worldshepherd";
static std::string API = "/worldshepherd-api";
static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static ramm::Memory& MEM() { return ribbit::RamHost::memory(); }
static void said(const std::string& line) { fprintf(stderr, "[WS-RAM] %s\n", line.c_str()); fflush(stderr); }
static bool is_number(const Json& j) { return j.type == Json::Num; }
static bool is_true(const Json& j) { return (j.type == Json::Bool && j.b) || (j.type == Json::Num && j.n != 0); }
static std::string num(double x) { char b[40]; snprintf(b, sizeof b, "%.17g", x); return b; }

// A table that is only ever added to. A record is found by its id, is added once, and is never moved or freed, so a
// reader needs no protection and a writer needs one compare-and-swap. R has `id` and `next`.
template <class K, class R> struct Table {
    static const size_t N = size_t(1) << 18;
    std::atomic<R*> first[N];
    std::atomic<R*>& home(const K& id) { return first[std::hash<K>()(id) & (N - 1)]; }
    R* find(const K& id) { for (R* r = home(id).load(std::memory_order_acquire); r; r = r->next) if (r->id == id) return r; return nullptr; }
    template <class Make> R* find_or_add(const K& id, Make make) {
        std::atomic<R*>& b = home(id); R* mine = nullptr; R* seen = nullptr;
        for (;;) {
            R* head = b.load(std::memory_order_acquire);
            for (R* r = head; r != seen; r = r->next) if (r->id == id) { delete mine; return r; }
            if (!mine) mine = make();
            mine->next = seen = head;
            if (b.compare_exchange_strong(head, mine, std::memory_order_acq_rel)) return mine;
        }
    }
};

// ---- who may call: [A_SARA_WRITES_ONLY_ITS_OWN_SPACE_V1] ----------------------------------------------------------
struct Session { const uint64_t id; const std::string peer; Session* next = nullptr; std::atomic<bool> up{true};
                 Session(uint64_t i, const std::string& p) : id(i), peer(p) {} };
struct Name    { const std::string id; const std::string peer; Name* next = nullptr; std::atomic<uint64_t> session;
                 Name(const std::string& w, const std::string& p, uint64_t s) : id(w), peer(p), session(s) {} };
static Table<uint64_t, Session> g_sessions;
static Table<std::string, Name> g_names;
static std::string peer_of(uint64_t session) { Session* s = session ? g_sessions.find(session) : nullptr; return s ? s->peer : ""; }
// "" when <who> is this session's
static std::string owns(uint64_t session, const std::string& who) {
    if (!session) return "a WorldShepherd call is made on a session; this one has none";
    Name* n = g_names.find(who);
    if (n && n->session.load(std::memory_order_acquire) == session) return "";      // the usual case: one read
    Session* me = g_sessions.find(session);
    if (!me) return "the session's address is not known to this host";
    if (!n) n = g_names.find_or_add(who, [&] { return new Name(who, me->peer, session); });
    if (n->peer != me->peer) return who + " is the SARA at " + n->peer + "; this is from " + me->peer;
    for (;;) {
        uint64_t theirs = n->session.load(std::memory_order_acquire);
        if (theirs == session) return "";
        Session* t = g_sessions.find(theirs);
        if (t && t->up.load(std::memory_order_acquire)) return who + " is session " + std::to_string(theirs) + "'s, which is up; this is on session " + std::to_string(session);
        if (n->session.compare_exchange_strong(theirs, session, std::memory_order_acq_rel)) return "";   // its session ended: the name is this one's
    }
}

// ---- the ticket: [A_TICKET_IS_ONE_RECORD_V1] ----------------------------------------------------------------------
struct Issue { const std::string sha, who; const double exp; const uint64_t n; };
struct Claim { const std::string who, sha; const double at; const uint64_t n; Claim* next = nullptr; };
struct Ticket {
    const std::string id; Ticket* next = nullptr;
    std::atomic<const Issue*> issue{nullptr};
    std::atomic<const Claim*> won{nullptr};
    std::atomic<Claim*> held{nullptr};
    explicit Ticket(const std::string& t) : id(t) {}
    std::string state(const Issue& is, const Claim* w) const {
        return "{\"sha\":" + Json::quote(is.sha) + ",\"exp\":" + num(is.exp) + ",\"by\":" + Json::quote(is.who) + ",\"bn\":" + std::to_string(is.n)
             + ",\"won\":" + Json::quote(w ? w->who : "") + ",\"wn\":" + std::to_string(w ? w->n : 0) + ",\"at\":" + num(w ? w->at : 0) + "}";
    }
    // [EACH_FACT_ITS_OWN_CELL_V1] In its own space, two cells, each written once by the call that made the fact:
    //   (worldshepherd, ticket, <ticket>)        {"sha","exp","by","bn"}   that it was issued: read by its name alone
    //   (worldshepherd, won.<issuer>, <ticket>)  {"won","wn","at"}         that it was won: its issuer's family
    void tell_issued(const Issue& is) const {
        ramm::WritingFor mine(0, ramm::LINGER_STAYS);
        MEM().write(SVC, "ticket", id, "{\"sha\":" + Json::quote(is.sha) + ",\"exp\":" + num(is.exp) + ",\"by\":" + Json::quote(is.who) + ",\"bn\":" + std::to_string(is.n) + "}", now_s());
    }
    void tell_won(const Issue& is, const Claim& w) const {
        ramm::WritingFor mine(0, ramm::LINGER_STAYS);
        MEM().write(SVC, "won." + is.who, id, "{\"won\":" + Json::quote(w.who) + ",\"wn\":" + std::to_string(w.n) + ",\"at\":" + num(w.at) + "}", now_s());
    }
};
static Table<std::string, Ticket> g_tickets;
static std::atomic<uint64_t> g_issued{0}, g_consumed{0}, g_calls{0}; static std::atomic<int64_t> g_told{0};
static void count() {                                             // at most once a second, by whichever call notices
    g_calls.fetch_add(1, std::memory_order_relaxed);
    const int64_t now = int64_t(now_s()); int64_t was = g_told.load(std::memory_order_relaxed);
    if (now > was && g_told.compare_exchange_strong(was, now))
        said("tickets: issued " + std::to_string(g_issued.load()) + ", consumed " + std::to_string(g_consumed.load()) + "; calls " + std::to_string(g_calls.load()));
}

// The ticket's issue: made by this call, or there already (this call made again, or a duplicate: its caller reads which).
// [THE_ISSUER_SAYS_WHO_WON_V1] `seen`: the winner the issuer saw before, when it makes its ticket again in a new memory.
static Ticket* issue(const std::string& who, const std::string& id, const std::string& sha, double exp, uint64_t n, const Issue*& is, const Claim* seen = nullptr) {
    Ticket* t = g_tickets.find_or_add(id, [&] { return new Ticket(id); });
    is = new Issue{sha, who, exp, n}; const Issue* was = nullptr;
    if (!t->issue.compare_exchange_strong(was, is, std::memory_order_acq_rel)) { delete is; delete seen; is = was; return t; }
    g_issued.fetch_add(1, std::memory_order_relaxed);
    const Claim* first = seen;                                    // what its issuer saw; else, of those that say they held it: the first made (the last on the list)
    if (!first) for (const Claim* h = t->held.load(std::memory_order_acquire); h; h = h->next) if (h->sha == is->sha && h->at <= is->exp) first = h;
    const Claim* none = nullptr;
    t->tell_issued(*is);
    if (first && t->won.compare_exchange_strong(none, first, std::memory_order_acq_rel)) { g_consumed.fetch_add(1, std::memory_order_relaxed); t->tell_won(*is, *first); }
    else delete seen;                                             // a claim made here already won it: what was seen is not kept
    return t;
}
// A claim: claimed | consumed | digest | expired | absent. t and is are the ticket and its issue as the claim found them.
static const char* claim(const std::string& who, const std::string& id, const std::string& sha, double at, uint64_t n, bool held, Ticket*& t, const Issue*& is) {
    t = held ? g_tickets.find_or_add(id, [&] { return new Ticket(id); }) : g_tickets.find(id);
    is = t ? t->issue.load(std::memory_order_acquire) : nullptr;
    Claim* mine = nullptr; bool kept = false;                     // kept: on the ticket's held list, the ticket's from then on
    if (t && !is && held) {                                       // its issuer has not made it in this memory yet
        mine = new Claim{who, sha, at, n}; kept = true;
        Claim* head = t->held.load(std::memory_order_acquire);
        do { mine->next = head; } while (!t->held.compare_exchange_weak(head, mine, std::memory_order_acq_rel));
        is = t->issue.load(std::memory_order_acquire);            // made since: that issue may not have seen this claim
    }
    if (!is) return "absent";
    if (is->sha != sha) return "digest";
    if (at > is->exp) return "expired";
    const Claim* w = t->won.load(std::memory_order_acquire);
    if (!w) {
        if (!mine) mine = new Claim{who, sha, at, n};
        if (t->won.compare_exchange_strong(w, mine, std::memory_order_acq_rel)) { g_consumed.fetch_add(1, std::memory_order_relaxed); t->tell_won(*is, *mine); return "claimed"; }
        if (!kept) delete mine;                                   // never seen by anyone else
    }
    return w->who == who && w->n == n ? "claimed" : "consumed";   // claimed: this call, made again
}

class WorldShepherdRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "worldshepherd"; }
    std::string vendor_version() const override { return WORLDSHEPHERD_RAM_BUILD; }
    std::string api_path() const override { return API; }
    bool own_flag_decides() const override { return true; }      // a cell stays until it is written again or removed
    bool rules_on_cells() const override { return true; }

    void listening(const std::string& bound) override { said(std::string(WORLDSHEPHERD_RAM_BUILD) + " listening at " + bound + ", answering " + API); }
    void session_up(uint64_t id, const std::string& peer) override { g_sessions.find_or_add(id, [&] { return new Session(id, peer); }); }
    void session_down(uint64_t id) override { if (Session* s = g_sessions.find(id)) s->up.store(false, std::memory_order_release); }

    // The ticket's methods. Each call is its own (it names its caller and its number), never waits, and runs on its
    // session's own thread in the order it arrived.
    // [A_SARA_BRINGS_ITS_OWN_BACK_IN_ONE_CALL_V1] (the acceptance study on the first 0.11, 2026-11-25: a SARA made each
    // of its issues and won claims again as a call of its own, one after another; recovery after the memory was
    // replaced went from 0.9's 0.7 s to 10-33 s on a 100 ms link.) op=again is every issue and every won claim of one
    // SARA in one call: a line {"who"}, then a line {"ticket","sha","exp","n"} for each issue and a line
    // {"ticket","sha","at","n"} for each won claim. Its claims are held claims.
    bool handles(const std::string& op) const override { return op == "issue" || op == "claim" || op == "again"; }
    bool coalesces(const std::string& op) const override { (void)op; return false; }
    bool inline_operation(const std::string& op) const override { return handles(op); }
    static bool says(const Json& b, bool issuing) {
        return b.type == Json::Obj && !b["ticket"].s.empty() && b["sha"].type == Json::Str && !b["sha"].s.empty() && is_number(b["n"]) && is_number(issuing ? b["exp"] : b["at"]);
    }
    ribbit::Reply operation(const std::string& op, const std::string& body) override {
        const bool issuing = op == "issue", again = op == "again";
        // again: a line for the SARA, then a line for each issue and each won claim. Lines, not one document with
        // lists in it: the wire keeps a list out of line, in a file on this host's disk.
        std::vector<Json> lines; Json b;
        try {
            if (!again) b = Json::parse(body);
            else for (size_t at = 0; at < body.size();) {
                size_t e = body.find('\n', at);
                if (e == std::string::npos) e = body.size();
                if (e > at) lines.push_back(Json::parse(body.substr(at, e - at)));
                at = e + 1;
            }
            if (again && !lines.empty()) b = lines[0];
        } catch (const std::exception&) { return ribbit::error_reply(400, op + ": the body is not JSON"); }
        if (b.type != Json::Obj || b["who"].s.empty() || (!again && !says(b, issuing)))
            return ribbit::error_reply(400, op + " says \"who\", \"ticket\", \"sha\", \"n\" and " + (issuing ? "\"exp\"" : "\"at\""));
        const std::string& who = b["who"].s;
        const uint64_t session = ribbit::current_session();
        const std::string no = owns(session, who);
        if (!no.empty()) { said("refused " + op + " from session " + std::to_string(session) + " at " + peer_of(session) + ": " + no); return ribbit::error_reply(403, no); }
        count();
        Ticket* t = nullptr; const Issue* is = nullptr;
        if (again) {
            for (size_t k = 1; k < lines.size(); ++k) if (!says(lines[k], is_number(lines[k]["exp"])))
                return ribbit::error_reply(400, "again: a line says \"ticket\", \"sha\", \"n\" and \"exp\" (an issue) or \"at\" (a claim)");
            uint64_t issues = 0, mine = 0, claims = 0, kept = 0;
            for (size_t k = 1; k < lines.size(); ++k) { const Json& x = lines[k]; if (!is_number(x["exp"])) continue;   // every issue, then every claim
                ++issues;
                const Claim* seen = x["won"].s.empty() || !is_number(x["wn"]) || !is_number(x["at"]) ? nullptr : new Claim{x["won"].s, x["sha"].s, x["at"].n, uint64_t(x["wn"].n)};
                t = issue(who, x["ticket"].s, x["sha"].s, x["exp"].n, uint64_t(x["n"].n), is, seen); if (is->who == who && is->n == uint64_t(x["n"].n)) ++mine; }
            for (size_t k = 1; k < lines.size(); ++k) { const Json& x = lines[k]; if (is_number(x["exp"])) continue;
                ++claims; if (!strcmp(claim(who, x["ticket"].s, x["sha"].s, x["at"].n, uint64_t(x["n"].n), true, t, is), "claimed")) ++kept; }
            said(who + " made its own again: " + std::to_string(issues) + " issue(s), " + std::to_string(mine) + " its; " + std::to_string(claims) + " claim(s) it held, " + std::to_string(kept) + " kept");
            return ribbit::Reply{200, "{\"ok\":true,\"issues\":" + std::to_string(mine) + ",\"claims\":" + std::to_string(kept) + "}"};
        }
        const char* outcome = nullptr;
        if (issuing) t = issue(who, b["ticket"].s, b["sha"].s, b["exp"].n, uint64_t(b["n"].n), is);
        else outcome = claim(who, b["ticket"].s, b["sha"].s, b["at"].n, uint64_t(b["n"].n), is_true(b["held"]), t, is);
        return ribbit::Reply{200, std::string("{\"ok\":true,") + (outcome ? std::string("\"outcome\":\"") + outcome + "\"," : std::string())
                                  + "\"state\":" + (is ? t->state(*is, t->won.load(std::memory_order_acquire)) : std::string("null")) + "}"};
    }

    // the only cell a SARA writes itself: it is here
    std::string refuse_cell(uint64_t session, const std::string& s, const std::string& v, const std::string& i, const std::string& bag_text) const override {
        (void)bag_text;
        if (s != SVC) return "";
        const std::string no = v == "sara" ? owns(session, i)
                             : "(worldshepherd, " + v + ") is written by the ticket; a SARA writes (worldshepherd, sara, <who>) and calls issue and claim";
        if (!no.empty()) said("refused (" + v + ", " + i + ") from session " + std::to_string(session) + " at " + peer_of(session) + ": " + no);
        return no;
    }
};

int main(int argc, char** argv) {
    std::vector<char*> a{argv[0]};
    for (int k = 1; k < argc; ++k) {
        if (!strcmp(argv[k], "--api") && k + 1 < argc) { API = argv[++k]; continue; }
        a.push_back(argv[k]);
    }
    WorldShepherdRam host;
    return host.run(int(a.size()), a.data());
}
