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
#include "server.hpp"
#include <climits>
#include <cerrno>
namespace rr {

static void cmd_del(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i]) && db::del(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_exists(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_type(Client& c, const Argv& a, Reply& r) { r.status(db::type(c.db, a[1])); }
static void cmd_touch(Client& c, const Argv& a, Reply& r) {
    long long n = 0;
    for (size_t i = 1; i < a.size(); i++) if (db::exists(c.db, a[i])) n++;
    r.integer(n);
}
static void cmd_keys(Client& c, const Argv& a, Reply& r) {
    bool all = a[1] == "*";
    std::vector<std::string> out;
    for (auto& k : db::keys(c.db)) if (all || stringmatch(a[1], k)) out.push_back(k);
    r.array(out.size()); for (auto& k : out) r.bulk(k);
}
static void cmd_randomkey(Client& c, const Argv&, Reply& r) {
    std::string k;
    if (db::randomkey(c.db, k)) r.bulk(k); else r.null();
}

static void rename_generic(Client& c, const Argv& a, Reply& r, bool nx) {
    bool same = a[1] == a[2];
    if (!db::exists(c.db, a[1])) { r.error("ERR no such key"); return; }
    if (same) { if (nx) r.integer(0); else r.ok(); return; }
    // [RENAME_ONE_BATCH_V1] one batch, published whole: the destination's old cells go, the source's cells appear under
    // the destination, the source goes -- and every wake is delivered once, after the whole array. It was three
    // separately visible steps (delete destination, copy, delete source): a reader parked on the destination woke at
    // the first and saw no key at all (XREADGROUP answered NOGROUP), a state Redis's RENAME never shows. The batch is
    // the memory's existing shape; nothing new was added to the memory for it.
    std::vector<Memory::W> ws;
    if (nx) {
        // [THE_MEMORY_DECIDES_V1] RENAMENX claims the destination first: its value cell is inserted only if absent
        // (one claimant wins; a concurrent RENAMENX or SETNX to the same name loses), then the batch below replaces the
        // claim with the source's cells. The claim is the source's own value when it has one.
        db::Key dk(c.db, a[2]);
        if (dk.k != Kind::None) { r.integer(0); return; }
        CellPtr sv = g.mem.cell(g.mem.region(db::svc(c.db)), a[1], Region::hash_of(a[1]), db::VAL);
        Bag claim; if (sv) claim = *db::copy_bag(sv); else { claim.kind = Kind::String; }
        ebr::Guard eg;
        if (!g.mem.replace_if(dk.R, dk.name, dk.h, db::VAL, nullptr, std::move(claim))) { r.integer(0); return; }
        if (!sv) ws.push_back(Memory::W(a[2], db::VAL, nullptr, true, false));   // a container source: the claim goes in the batch
        // (the batch writes over the claim; removing the destination first would let another claimant win in the gap)
    } else if (db::exists(c.db, a[2])) {
        if (nx) { r.integer(0); return; }
        Cell old; if (db::get(c.db, a[2], old) && old.bag().kind == Kind::String) g.used_memory -= (int64_t)old.bag().s.size();
        ws.push_back(Memory::W(a[2], "", nullptr, false, true));
    }
    for (auto& cell : g.mem.read_all(db::svc(c.db), a[1])) ws.push_back(Memory::W(a[2], cell.instance, db::copy_bag(cell.node)));
    ws.push_back(Memory::W(a[1], "", nullptr, false, true));
    g.mem.write_many(db::svc(c.db), ws);
    g.stats.dirty++;
    if (nx) r.integer(1); else r.ok();
}
static void cmd_rename(Client& c, const Argv& a, Reply& r) { rename_generic(c, a, r, false); }
static void cmd_renamenx(Client& c, const Argv& a, Reply& r) { rename_generic(c, a, r, true); }

static void cmd_copy(Client& c, const Argv& a, Reply& r) {
    int dst = c.db; bool replace = false;
    for (size_t j = 3; j < a.size(); j++) {
        size_t more = a.size() - j - 1;
        std::string o = lower(a[j]);
        if (o == "replace") replace = true;
        else if (o == "db" && more >= 1) {
            long long d; if (!string2ll(a[j+1], d)) { err_notint(r); return; }
            if (d < 0 || d >= g.databases) { r.error("ERR DB index is out of range"); return; }
            dst = (int)d; j++;
        } else { err_syntax(r); return; }
    }
    if (dst == c.db && a[1] == a[2]) { r.error("ERR source and destination objects are the same"); return; }
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    // [REPLACE_IN_ONE_STEP_V1] / [THE_MEMORY_DECIDES_V1] the destination is written in ONE batch in its database: with
    // REPLACE its old cells go in the same step; without, the destination's value cell is first claimed (inserted only if
    // absent) so a concurrent writer of that name makes this COPY answer 0 instead of being overwritten.
    db::Key dk(dst, a[2]);
    std::vector<Memory::W> ws;
    auto cells = g.mem.read_all(db::svc(c.db), a[1]);
    if (dk.k != Kind::None) { if (!replace) { r.integer(0); return; } }
    else if (!replace) {
        Bag claim; bool have = false;
        for (auto& cl : cells) if (cl.instance == db::VAL) { claim = *db::copy_bag(cl.node); have = true; }
        if (!have) claim.kind = Kind::String;
        ebr::Guard eg;
        if (!g.mem.replace_if(dk.R, dk.name, dk.h, db::VAL, nullptr, std::move(claim))) { r.integer(0); return; }
    }
    // with REPLACE the old destination goes in the same step; without, the batch writes OVER this call's claim (removing
    // it first would open a gap in which another claimant could win too). A source with no value cell drops the claim.
    bool src_has_val = false; for (auto& cl : cells) if (cl.instance == db::VAL) src_has_val = true;
    if (replace) ws.push_back(Memory::W(a[2], "", nullptr, false, true));
    for (auto& cl : cells) ws.push_back(Memory::W(a[2], cl.instance, db::copy_bag(cl.node)));
    if (!replace && !src_has_val) ws.push_back(Memory::W(a[2], db::VAL, nullptr, true, false));
    g.mem.write_many(db::svc(dst), ws);
    g.stats.dirty++;
    r.integer(1);
}

static void cmd_move(Client& c, const Argv& a, Reply& r) {
    long long d;
    if (!string2ll(a[2], d)) { err_notint(r); return; }
    if (d < 0 || d >= g.databases) { r.error("ERR DB index is out of range"); return; }
    if ((int)d == c.db) { r.error("ERR source and destination objects are the same"); return; }
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    if (db::exists((int)d, a[1])) { r.integer(0); return; }
    // [THE_MEMORY_DECIDES_V1] the source is TAKEN (removed whole, by exactly one mover), then written in the target
    // database only if the name is still free there; if it was taken meanwhile, the source is put back. A key can no
    // longer land in two databases, and a MOVE that did not happen leaves the key where it was.
    std::vector<Memory::W> back;
    for (auto& cl : g.mem.read_all(db::svc(c.db), a[1])) back.push_back(Memory::W(a[1], cl.instance, db::copy_bag(cl.node)));
    ebr::Guard eg;
    CellPtr val = nullptr;
    if (!g.mem.take_variable(db::svc(c.db), a[1], &val)) { r.integer(0); return; }        // another mover (or a DEL) took it
    db::Key dk((int)d, a[1]);
    if (dk.k != Kind::None) { g.mem.write_many(db::svc(c.db), back); r.integer(0); return; }
    std::vector<Memory::W> ws;
    for (auto& w : back) ws.push_back(Memory::W(a[1], w.instance, w.bag));
    g.mem.write_many(db::svc((int)d), ws);
    g.stats.dirty++;
    r.integer(1);
}

// SCAN: the cursor is a creation (born) id in the memory's write order; keys that existed when the
// scan started are returned exactly once as long as they are not deleted or renamed.
static void cmd_scan(Client& c, const Argv& a, Reply& r) {
    errno = 0; char* ep = nullptr;
    unsigned long cursor = strtoul(a[1].c_str(), &ep, 10);
    if (isspace((unsigned char)a[1][0]) || *ep != '\0' || errno == ERANGE) { r.error("ERR invalid cursor"); return; }
    long long count = 10; std::string pattern; bool use_pat = false; std::string type;
    for (size_t i = 2; i < a.size(); i++) {
        size_t more = a.size() - i - 1; std::string o = lower(a[i]);
        if (o == "count" && more >= 1) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count < 1) { err_syntax(r); return; } i++; }
        else if (o == "match" && more >= 1) { pattern = a[i+1]; use_pat = !(pattern == "*"); i++; }
        else if (o == "type" && more >= 1) {
            type = lower(a[i+1]); i++;
            // 7.2 accepts any type name and matches nothing for an unknown one (the error arrives in 8.0)
        }
        else { err_syntax(r); return; }
    }
    uint64_t next = 0;
    std::vector<std::string> out;
    uint64_t cur = cursor;
    // walk in chunks until we have something to say or the keyspace is exhausted
    for (int rounds = 0; rounds < 1000; rounds++) {
        auto vars = g.mem.variables_after(db::svc(c.db), cur, (size_t)count, next);
        for (auto& k : vars) {
            if (!db::exists(c.db, k)) continue;
            if (use_pat && !stringmatch(pattern, k)) continue;
            if (!type.empty() && db::type(c.db, k) != type) continue;
            out.push_back(k);
        }
        if (next == 0 || !out.empty()) break;
        cur = next;
    }
    r.array(2);
    r.bulk(std::to_string(next));
    r.array(out.size()); for (auto& k : out) r.bulk(k);
}

// ---------------------------------------------------------------- expiry
static void expire_generic(Client& c, const Argv& a, Reply& r, long long basetime, bool seconds) {
    int nx = 0, xx = 0, gt = 0, lt = 0;
    for (size_t j = 3; j < a.size(); j++) {
        std::string o = lower(a[j]);
        if (o == "nx") nx = 1; else if (o == "xx") xx = 1; else if (o == "gt") gt = 1; else if (o == "lt") lt = 1;
        else { r.error("ERR Unsupported option " + a[j]); return; }
    }
    if ((nx && xx) || (nx && gt) || (nx && lt)) { r.error("ERR NX and XX, GT or LT options at the same time are not compatible"); return; }
    if (gt && lt) { r.error("ERR GT and LT options at the same time are not compatible"); return; }
    long long when;
    if (!string2ll(a[2], when)) { err_notint(r); return; }
    std::string cmdname = lower(a[0]);
    if (seconds) { if (when > LLONG_MAX / 1000 || when < LLONG_MIN / 1000) { r.error("ERR invalid expire time in '" + cmdname + "' command"); return; } when *= 1000; }
    if (when > LLONG_MAX - basetime) { r.error("ERR invalid expire time in '" + cmdname + "' command"); return; }
    when += basetime;
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    if (nx || xx || gt || lt) {
        // [THE_MEMORY_DECIDES_V1] the condition is checked on the ttl cell as read, and the new ttl lands only on that
        // cell (replace_if): two EXPIRE NX cannot both set it, and a GT/LT never lands over a ttl it did not compare to
        db::Key k(c.db, a[1]);
        if (k.k == Kind::None) { r.integer(0); return; }
        ebr::Guard eg;
        CellPtr tc = g.mem.cell(k.R, k.name, k.h, db::TTL);
        int64_t cur = tc ? tc->bag->n : -1;
        if (nx && cur != -1) { r.integer(0); return; }
        if (xx && cur == -1) { r.integer(0); return; }
        if (gt && (when <= cur || cur == -1)) { r.integer(0); return; }
        if (lt && cur != -1 && when >= cur) { r.integer(0); return; }
        if (when <= Memory::now_ms()) { db::del(c.db, a[1]); r.integer(1); return; }
        Bag b; b.kind = Kind::Ttl; b.n = when; b.indexed = true;
        // one decision: the ttl lands only on the ttl it was compared to; if another expiry landed first, the memory's
        // answer is final and this one did not apply
        if (!g.mem.replace_if(k.R, k.name, k.h, db::TTL, tc, std::move(b))) { r.integer(0); return; }
        g.stats.dirty++; r.integer(1); return;
    }
    if (when <= Memory::now_ms()) { db::del(c.db, a[1]); r.integer(1); return; }
    db::set_expire(c.db, a[1], when);
    r.integer(1);
}
static void cmd_expire(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, Memory::now_ms(), true); }
static void cmd_pexpire(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, Memory::now_ms(), false); }
static void cmd_expireat(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, 0, true); }
static void cmd_pexpireat(Client& c, const Argv& a, Reply& r) { expire_generic(c, a, r, 0, false); }

static void ttl_generic(Client& c, const Argv& a, Reply& r, bool ms, bool abs) {
    int64_t e = db::expire_at(c.db, a[1]);
    if (e == -2) { r.integer(-2); return; }
    if (e == -1) { r.integer(-1); return; }
    long long ttl = abs ? e : e - Memory::now_ms();
    if (ttl < 0) ttl = 0;
    r.integer(ms ? ttl : (ttl + 500) / 1000);
}
static void cmd_ttl(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, false, false); }
static void cmd_pttl(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, true, false); }
static void cmd_expiretime(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, false, true); }
static void cmd_pexpiretime(Client& c, const Argv& a, Reply& r) { ttl_generic(c, a, r, true, true); }
static void cmd_persist(Client& c, const Argv& a, Reply& r) {
    if (!db::exists(c.db, a[1])) { r.integer(0); return; }
    r.integer(db::persist(c.db, a[1]) ? 1 : 0);
}

void register_keyspace_commands() {
    register_cmd("del", cmd_del); register_cmd("unlink", cmd_del); register_cmd("exists", cmd_exists); register_cmd("type", cmd_type);
    register_cmd("touch", cmd_touch); register_cmd("keys", cmd_keys); register_cmd("randomkey", cmd_randomkey);
    register_cmd("rename", cmd_rename); register_cmd("renamenx", cmd_renamenx); register_cmd("copy", cmd_copy); register_cmd("move", cmd_move);
    register_cmd("scan", cmd_scan);
    register_cmd("expire", cmd_expire); register_cmd("pexpire", cmd_pexpire); register_cmd("expireat", cmd_expireat); register_cmd("pexpireat", cmd_pexpireat);
    register_cmd("ttl", cmd_ttl); register_cmd("pttl", cmd_pttl); register_cmd("expiretime", cmd_expiretime); register_cmd("pexpiretime", cmd_pexpiretime);
    register_cmd("persist", cmd_persist);
}

}  // namespace rr
