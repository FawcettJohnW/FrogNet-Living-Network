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
// This is the platform's RAM host (ribbit::RamHost) with WorldShepherd's rules about its own cells. It runs no ticket
// operation: a ticket is still its cells, each written by its own SARA and derived by every reader
// (ribbit_claim_store.py, [A_TICKET_IS_ITS_CELLS_V1]). It keeps no file. What it adds is what a write must be to be
// taken into the service "worldshepherd"; every other service in this memory is the platform's, unruled here.
//
//   [A_SARA_WRITES_ONLY_ITS_OWN_CELLS_V1]  A cell names its writer: (worldshepherd, sara, <who>),
//       (worldshepherd, ticket.<id>, issue.<who>.<n> | claim.<who>.<n>). <who> belongs to the machine whose session
//       first wrote a cell naming it, for the life of this memory, and while that session is up to that session
//       alone. A write naming <who> from another machine, or from another session while the first is up, is refused.
//       The machine is the address the session arrives from: on a FrogNet network that is the node's own address,
//       which the network admitted by key. A write with no session (plain HTTP) is refused.
//   [A_CELL_SAYS_WHAT_ITS_KIND_SAYS_V1]    An issue says "sha" (text) and "exp" (a number); a claim says "sha" and "at"
//       (a number). Anything else in the service is refused: a reader never meets a cell it cannot derive from.
//   [WON_IS_CONSISTENT_WITH_THE_TICKET_V1] A claim that says "won" is refused when this memory shows it false: the
//       ticket's issue is here and names another digest, or the claim's "at" is past the issue's "exp", or another
//       claim is the one that consumed the ticket here. When the issue is not here yet (after the memory moved, a
//       winner may write before the issuer does) the claim is taken, and every reader applies the same digest and
//       expiry rule when the issue arrives (Ticket, ribbit_claim_store.py).
//
// Who owns a name is itself memory: the first cell in (#worldshepherd, who.<who>), by write order, the one place
// every write of that name passes. A service whose name begins with '#' is this process's own; the network API
// never serves it. Nothing here is locked, compared-and-set or retried.
//
// What this host cannot know: whether a claim that says "won" in a NEW memory, consistent with its ticket, was the
// winner in the memory before. That memory is gone and kept no file. Proof of it is evidence the winner carries from
// before -- SARA's own signed receipts -- and is WorldShepherd's to add; it is measured, not hidden, in
// harness/acceptance.py (case restored-winner-provenance).
//
//     worldshepherd-ram [--listen HOST:PORT] [--api PATH]      PATH: the endpoint fdf-app's clients use (fdfram.API)
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include "frogram.hpp"
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#define WORLDSHEPHERD_RAM_BUILD "worldshepherd-ram 0.9 2026-11-25a [THE_APPS_RAM_HOST_DECIDES_ITS_WRITES_V1] [A_SARA_WRITES_ONLY_ITS_OWN_CELLS_V1] [A_CELL_SAYS_WHAT_ITS_KIND_SAYS_V1] [WON_IS_CONSISTENT_WITH_THE_TICKET_V1]"

using frogram::Json;
static const std::string SVC = "worldshepherd", OWN = "#worldshepherd";
static std::string API = "/worldshepherd-api";
static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static ramm::Memory& MEM() { return ribbit::RamHost::memory(); }
static std::vector<ramm::Cell> cells(const std::string& s, const std::string& v, const std::string& i = "") {
    std::vector<ramm::Cell> out;
    for (auto& c : MEM().match(s, v, i, -1, 0, now_s())) if (!c.gone) out.push_back(c);
    return out;
}
static void said(const std::string& line) { fprintf(stderr, "[WS-RAM] %s\n", line.c_str()); fflush(stderr); }
static bool is_number(const Json& j) { return j.type == Json::Num; }
static bool is_true(const Json& j) { return (j.type == Json::Bool && j.b) || (j.type == Json::Num && j.n != 0); }

class WorldShepherdRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "worldshepherd"; }
    std::string vendor_version() const override { return WORLDSHEPHERD_RAM_BUILD; }
    std::string api_path() const override { return API; }
    bool own_flag_decides() const override { return true; }      // a cell stays until it is written again or removed
    bool rules_on_cells() const override { return true; }

    void listening(const std::string& bound) override { said(std::string(WORLDSHEPHERD_RAM_BUILD) + " listening at " + bound + ", answering " + API); }
    void session_up(uint64_t id, const std::string& peer) override {
        ramm::WritingFor mine(0);                                 // this process's own cell, not the session's
        MEM().write(OWN, "session", std::to_string(id), "{\"peer\":" + Json::quote(peer) + "}", now_s());
    }
    void session_down(uint64_t id) override {
        for (auto& c : cells(OWN, "session", std::to_string(id))) MEM().remove(OWN, "session", c.id, true);
    }

    std::string refuse_cell(uint64_t session, const std::string& s, const std::string& v, const std::string& i, const std::string& bag_text) const override {
        if (s != SVC) return "";
        const std::string no = rule(session, v, i, bag_text);
        if (!no.empty()) said("refused (" + v + ", " + i + ") from session " + std::to_string(session) + " at " + peer_of(session) + ": " + no);
        return no;
    }

private:
    static std::string peer_of(uint64_t session) {
        if (!session) return "";
        for (auto& c : cells(OWN, "session", std::to_string(session))) return Json::parse(c.text())["peer"].s;
        return "";
    }
    // [A_SARA_WRITES_ONLY_ITS_OWN_CELLS_V1] "" when <who> is this session's to write
    static std::string owns(uint64_t session, const std::string& who) {
        if (!session) return "a WorldShepherd cell is written on a session; this write has none";
        const std::string peer = peer_of(session), fam = "who." + who, me = std::to_string(session);
        if (peer.empty()) return "the session's address is not known to this host";
        auto first = [&]() -> const std::vector<ramm::Cell> { return cells(OWN, fam); };
        std::vector<ramm::Cell> all = first();
        if (all.empty()) {                                        // nobody has written this name here: mine is written, and the
            { ramm::WritingFor mine(0);                           // first in write order is the owner, whoever else wrote one
              MEM().write(OWN, fam, me, "{\"peer\":" + Json::quote(peer) + ",\"session\":" + me + "}", now_s()); }
            all = first();
        }
        const ramm::Cell* owner = nullptr;
        for (auto& c : all) if (!owner || c.id < owner->id) owner = &c;
        if (!owner) return "the name's owner could not be read";
        const Json o = Json::parse(owner->text());
        if (o["peer"].s != peer) return who + " is the SARA at " + o["peer"].s + "; this write is from " + peer;
        const std::string theirs = std::to_string(uint64_t(o["session"].n));
        if (theirs != me && !cells(OWN, "session", theirs).empty()) return who + " is session " + theirs + "'s, which is up; this write is on session " + me;
        return "";
    }
    static std::string rule(uint64_t session, const std::string& v, const std::string& i, const std::string& bag_text) {
        Json bag;
        try { bag = Json::parse(bag_text); } catch (const std::exception&) { return "the bag is not a JSON object"; }
        if (v == "sara") return owns(session, i);
        if (v.compare(0, 7, "ticket.") != 0 || v.size() == 7) return "(worldshepherd, " + v + ") is not a WorldShepherd family: sara, or ticket.<id>";
        const bool issue = i.compare(0, 6, "issue.") == 0, claim = i.compare(0, 6, "claim.") == 0;
        const size_t last = i.rfind('.');
        if (!(issue || claim) || last <= 6) return "a ticket's cell is issue.<who>.<n> or claim.<who>.<n>";
        const std::string who = i.substr(6, last - 6);
        const std::string no = owns(session, who);
        if (!no.empty()) return no;
        if (bag.type != Json::Obj) return "the bag is not a JSON object";
        if (bag.o.empty()) return "";                             // its own cell, removed
        if (bag["sha"].type != Json::Str || bag["sha"].s.empty()) return "a ticket's cell says \"sha\", the ticket's digest as text";
        if (issue) return is_number(bag["exp"]) ? "" : "an issue says \"exp\", a number";
        if (!is_number(bag["at"])) return "a claim says \"at\", a number";
        if (!is_true(bag["won"])) return "";
        // [WON_IS_CONSISTENT_WITH_THE_TICKET_V1] the ticket as this memory shows it, derived as every reader derives it
        const ramm::Cell* is = nullptr; std::vector<ramm::Cell> all = cells(SVC, v);
        for (auto& c : all) if (c.instance.compare(0, 6, "issue.") == 0 && (!is || c.id < is->id)) is = &c;
        Json ib; if (is) ib = Json::parse(is->text());
        if (is && ib["sha"].s != bag["sha"].s) return "it says won with a digest that is not the ticket's";
        if (is && bag["at"].n > ib["exp"].n) return "it says won at a time past the ticket's expiry";
        const ramm::Cell* win = nullptr; bool said_won = false;
        for (auto& c : all) {
            if (c.instance.compare(0, 6, "claim.") != 0) continue;
            const Json b = Json::parse(c.text());
            if (is && (b["sha"].s != ib["sha"].s || b["at"].n > ib["exp"].n)) continue;
            const bool w = is_true(b["won"]);
            if (w && (!said_won || c.id < win->id)) { win = &c; said_won = true; }
            else if (!w && !said_won && is && c.id > is->id && (!win || c.id < win->id)) win = &c;
        }
        if (win && win->instance != i) return "the ticket was consumed by " + win->instance + " in this memory";
        return "";
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
