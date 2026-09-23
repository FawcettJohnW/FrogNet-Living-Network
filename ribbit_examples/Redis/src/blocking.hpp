// Blocking commands: shared by lists and sorted sets. See cmd_list.cpp for the design note.
#pragma once
#include "server.hpp"
#include <chrono>
#include <atomic>
bool parse_timeout(const std::string& s, int64_t& ms, Reply& r);
std::string claim_ticket();
// the word a blocking command waits on, registered on for the command's whole life: the registration is
// what makes a writer bump the word at all, so it must exist before the first data check
struct WaitReg { WaitWord& w; WaitReg(WaitWord& ww) : w(ww) { Region::watch(w); } ~WaitReg() { Region::unwatch(w); } WaitReg(const WaitReg&) = delete; };
WaitWord& wait_word_for(Client& c, const std::vector<std::string>& keys);
bool peer_gone(int fd);
bool block_on(Client& c, WaitWord& w, uint32_t seen, int64_t timeout_ms, std::chrono::steady_clock::time_point deadline);
struct Claim {
    Client& c; std::vector<std::string> keys; std::string ticket; bool placed = false;
    Claim(Client& cl, std::vector<std::string> ks) : c(cl), keys(std::move(ks)) { ticket = claim_ticket(); }
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
        std::vector<std::pair<std::string, std::string>> rs; for (auto& k : keys) rs.emplace_back(k, ticket);
        g.mem.remove_many(db::claims(c.db), rs);
        Region& R = g.mem.region(db::svc(c.db)); for (auto& k : keys) R.touched(k);   // the next in line should look
    }
};
