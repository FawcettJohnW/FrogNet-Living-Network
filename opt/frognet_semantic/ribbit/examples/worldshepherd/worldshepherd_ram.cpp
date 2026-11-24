// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// worldshepherd_ram.cpp -- worldshepherd-ram: WorldShepherd's own RAM manager, one executable.
//
// The Ribbit RAM host (ribbit/cpp: the memory, FNW1, the host) with WorldShepherd's region compiled in. The region
// owns SARA's read tickets: a ticket is two cells, and only the region writes them.
//
//     (worldshepherd, ticket.<ticket_id>, issue) = {"sha": <ticket sha256>, "exp": <expires_at>}
//     (worldshepherd, ticket.<ticket_id>, claim) = {"at": <the claimant's now>}        there, once it is consumed
//
// A SARA asks with one call, answered on its own session (op=ticket):
//     {"do":"register","id":ID,"sha":SHA,"exp":T,"who":W,"n":N}  -> {"ok":true} | {"ok":false,"reason":"duplicate ticket_id"}
//     {"do":"claim","id":ID,"sha":SHA,"now":T,"who":W,"n":N}     -> {"ok":true,"consumed_at":T} | {"ok":false,"reason":...}
// "who" and "n" are the asker's own name and its own count: they make every ask its own request, so the session never
// answers it with another ask's answer ([CLIENT_COALESCES_V1]) and this host never runs two as one (coalesces: false).
// Status is not asked: it is a read of the ticket's two cells, by anyone.
//
// One ticket takes one ask at a time ([ONE_ASK_AT_A_TIME_PER_TICKET_V1]): each ask is handed to the ticket's Ordered
// (ribbit::Ordered, lock-free, on the host's adaptive pool) and answered from there (ribbit::answer_later). Asks for
// different tickets run in parallel. Nothing waits: the session's thread hands the ask over and reads its next frame.
//
// A ticket outlives this process ([TICKETS_ARE_FILED_V1]): each cell is appended to the file named by
// WORLDSHEPHERD_RAM_FILE and synced BEFORE it is written into the memory and before its asker is answered; at start
// the file is read back in the order it was written. Without that variable the host does not start.
//
//     WORLDSHEPHERD_RAM_FILE=/path/tickets.log worldshepherd-ram [--listen HOST:PORT]     answers /worldshepherd-api
#include "ram_host.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <unistd.h>
#define WORLDSHEPHERD_VERSION "worldshepherd-ram 0.4 [ONE_ASK_AT_A_TIME_PER_TICKET_V1] [TICKETS_ARE_FILED_V1]"
using frogram::Json;

namespace {
const char* SERVICE = "worldshepherd";
std::string num(double x) { char b[40]; snprintf(b, sizeof b, "%.17g", x); return b; }
std::string refused(const std::string& why) { return "{\"ok\":false,\"reason\":" + Json::quote(why) + "}"; }
}

class WorldShepherdRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "worldshepherd"; }
    std::string vendor_version() const override { return WORLDSHEPHERD_VERSION; }
    bool own_flag_decides() const override { return true; }             // a cell stays until it is written again or removed
    bool handles(const std::string& op) const override { return op == "ticket"; }
    bool inline_operation(const std::string& op) const override { return op == "ticket"; }   // it only hands the ask over
    bool coalesces(const std::string& op) const override { return op != "ticket"; }
    // the tickets are the region's: nobody writes them over the network
    std::string refuse_write(const std::string& s, const std::string& v, const std::string&) const override {
        return s == SERVICE && v.rfind("ticket.", 0) == 0 ? "a ticket's cells are written by the region (op=ticket), never over the network" : ""; }

    ribbit::Reply operation(const std::string&, const std::string& body) override {
        Json in; try { in = Json::parse(body); } catch (const std::exception&) { return ribbit::error_reply(400, "ticket: body is not a JSON object"); }
        const std::string what = in["do"].type == Json::Str ? in["do"].s : "", id = in["id"].type == Json::Str ? in["id"].s : "";
        if (what != "register" && what != "claim") return ribbit::error_reply(400, "ticket: do must be register or claim");
        if (id.empty() || in["sha"].type != Json::Str) return ribbit::error_reply(400, "ticket: id and sha are required");
        if (in[what == "register" ? "exp" : "now"].type != Json::Num) return ribbit::error_reply(400, "ticket: " + std::string(what == "register" ? "exp" : "now") + " must be a number");
        void* asker = ribbit::defer_answer();
        if (!asker) return ribbit::error_reply(500, "ticket: no continuation for this request");
        const std::string sha = in["sha"].s; const double t = in[what == "register" ? "exp" : "now"].n; const bool reg = what == "register";
        order_[std::hash<std::string>()(id) % STRIPES].run([this, asker, id, sha, t, reg] {
            ribbit::Reply r = resident_operation(reg ? "ticket register" : "ticket claim", [&](std::unique_ptr<frogram::MemoryApi> m) {
                try { return reg ? do_register(*m, id, sha, t) : do_claim(*m, id, sha, t); }
                catch (const std::exception& x) { return std::string("{\"ok\":false,\"error\":") + Json::quote(x.what()) + "}"; }
            });
            ribbit::answer_later(asker, r.body);
        });
        return ribbit::Reply{200, ""};                                   // answered from the ticket's Ordered
    }

    void listening(const std::string& bound) override {
        const char* f = getenv("WORLDSHEPHERD_RAM_FILE");
        if (!f || !*f) { fprintf(stderr, "worldshepherd-ram: WORLDSHEPHERD_RAM_FILE is not set: the tickets need a file\n"); exit(2); }
        file_ = f;
        size_t restored = 0, lineno = 0; std::ifstream in(file_); std::string line;
        resident_operation("ticket restore", [&](std::unique_ptr<frogram::MemoryApi> m) {
            while (std::getline(in, line)) {
                ++lineno; if (line.empty()) continue;
                Json j; try { j = Json::parse(line); } catch (const std::exception& x) {
                    fprintf(stderr, "worldshepherd-ram: %s line %zu is not a record (%s): not starting on a damaged file\n", file_.c_str(), lineno, x.what()); exit(2); }
                m->write(SERVICE, j["v"].s, j["i"].s, j["bag"].s); ++restored;
            }
            return std::string("{}");
        });
        fd_ = open(file_.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd_ < 0) { fprintf(stderr, "worldshepherd-ram: cannot open %s for append: %s\n", file_.c_str(), strerror(errno)); exit(2); }
        fprintf(stderr, "worldshepherd-ram: %s (%s) listening on %s; [TICKETS_ARE_FILED_V1] %zu cell(s) restored from %s, in the order written\n",
                WORLDSHEPHERD_VERSION, ribbit::platform_version(), bound.c_str(), restored, file_.c_str());
    }

private:
    static constexpr size_t STRIPES = 256;
    ribbit::Ordered order_[STRIPES];
    std::string file_; int fd_ = -1;

    // one record, on disk before it is in the memory. O_APPEND: each line lands whole, whichever Ordered wrote it.
    void file_cell(const std::string& variable, const std::string& instance, const std::string& bag) {
        const std::string line = "{\"v\":" + Json::quote(variable) + ",\"i\":" + Json::quote(instance) + ",\"bag\":" + Json::quote(bag) + "}\n";
        if (write(fd_, line.data(), line.size()) != ssize_t(line.size()) || fdatasync(fd_) != 0)
            throw std::runtime_error("cannot file the ticket in " + file_ + ": " + strerror(errno));
    }
    static const frogram::Cell* find(const std::vector<frogram::Cell>& cells, const char* instance) {
        for (auto& c : cells) if (!c.gone && c.instance == instance) return &c;
        return nullptr;
    }
    std::string do_register(frogram::MemoryApi& m, const std::string& id, const std::string& sha, double exp) {
        const std::string v = "ticket." + id;
        if (find(m.read(SERVICE, v), "issue")) return refused("duplicate ticket_id");
        const std::string bag = "{\"sha\":" + Json::quote(sha) + ",\"exp\":" + num(exp) + "}";
        file_cell(v, "issue", bag); m.write(SERVICE, v, "issue", bag);
        return "{\"ok\":true}";
    }
    std::string do_claim(frogram::MemoryApi& m, const std::string& id, const std::string& sha, double now) {
        const std::string v = "ticket." + id; const auto cells = m.read(SERVICE, v);
        const frogram::Cell* issue = find(cells, "issue");
        if (!issue) return refused("ticket not registered in this ledger");
        if (issue->bag["sha"].s != sha) return refused("ticket digest does not match registered ticket");
        if (now > issue->bag["exp"].n) return refused("ticket expired");
        if (find(cells, "claim")) return refused("ticket already consumed");
        const std::string bag = "{\"at\":" + num(now) + "}";
        file_cell(v, "claim", bag); m.write(SERVICE, v, "claim", bag);
        return "{\"ok\":true,\"consumed_at\":" + num(now) + "}";
    }
};

int main(int argc, char** argv) { return WorldShepherdRam().run(argc, argv); }
