/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// Blocking commands: shared by lists and sorted sets. See cmd_list.cpp for the design note.
#pragma once
#include "server.hpp"
#include <chrono>
#include <atomic>
namespace rr {
bool parse_timeout(const std::string& s, int64_t& ms, Reply& r);
std::string claim_ticket();
// the word a blocking command waits on, registered on for the command's whole life: the registration is
// what makes a writer bump the word at all, so it must exist before the first data check
struct WaitReg { WaitWord& w; WaitReg(WaitWord& ww) : w(ww) { Region::watch(w); } ~WaitReg() { Region::unwatch(w); } WaitReg(const WaitReg&) = delete; };
WaitWord& wait_word_for(Client& c, const std::vector<std::string>& keys);
bool block_on(Client& c, WaitWord& w, uint32_t seen, int64_t timeout_ms, std::chrono::steady_clock::time_point deadline);
// A blocked command's place in line: its ticket under each key it waits on. [CLAIM_OUTLIVES_THE_TRY_V1] A command that
// would wait keeps its ticket when it returns (c.park_ticket): it is tried again, with the same place, when what it waits
// on changes. Served, timed out, unblocked or gone, the ticket is removed (here, or by drop_claim).
inline void drop_claim(Client& c) {
    if (c.park_ticket.empty()) return;
    std::vector<std::pair<std::string, std::string>> rs; for (auto& k : c.park_claim_keys) rs.emplace_back(k, c.park_ticket);
    g.mem.remove_many(db::claims(c.park_claim_db), rs);
    Region& R = g.mem.region(db::svc(c.park_claim_db)); for (auto& k : c.park_claim_keys) R.touched(k);   // the next in line should look
    c.park_ticket.clear(); c.park_claim_keys.clear();
}
struct Claim {
    Client& c; std::vector<std::string> keys; std::string ticket; bool placed = false;
    Claim(Client& cl, std::vector<std::string> ks) : c(cl), keys(std::move(ks)) {
        if (!c.park_ticket.empty()) { ticket = c.park_ticket; placed = true; }   // tried again: the same place in line
        else ticket = claim_ticket();
    }
    void place() {
        if (placed) return;
        std::vector<Memory::W> ws; Bag b; b.kind = Kind::None; auto bp = std::make_shared<const Bag>(b);
        for (auto& k : keys) ws.push_back(Memory::W{k, ticket, bp});
        g.mem.write_many(db::claims(c.db), ws); placed = true;
    }
    bool first_for(const std::string& key) const {           // is my ticket the lowest live claim under key?
        auto v = g.mem.read_range(db::claims(c.db), key, "", "", false, 0, 1, true);
        if (!placed) return v.empty();                       // a newcomer defers to anyone already parked: arrival order is the contract
        return v.empty() || v[0].instance == ticket;
    }
    ~Claim() {
        if (!placed) return;
        if (c.would_block) { c.park_ticket = ticket; c.park_claim_keys = keys; c.park_claim_db = c.db; return; }   // parked: kept
        c.park_ticket = ticket; c.park_claim_keys = keys; c.park_claim_db = c.db;
        drop_claim(c);
    }
};

}  // namespace rr
