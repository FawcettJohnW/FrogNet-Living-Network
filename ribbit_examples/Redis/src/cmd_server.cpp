#include "server.hpp"
#include "ebr.hpp"
#include "cfgtable.hpp"
#include <sys/resource.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <climits>
#include <sys/time.h>
#include <unistd.h>
#include <thread>
#include <chrono>
#include <set>
#include <sstream>
#include <algorithm>

// ---------------------------------------------------------------- INFO
static size_t blocking_keys(bool nokey_only) {
    std::set<std::string> ks;
    g.clients.walk_all([&](LFMap<uint64_t, Client*>::Node* n) { auto* v = n->val->blocked_keys.load(std::memory_order_acquire); if (v && (!nokey_only || v->nokey)) for (auto& k : v->keys) ks.insert(k); return true; });
    return ks.size();
}
static std::string info_section(const std::string& name) {
    std::ostringstream o;
    auto now = Memory::now_ms();
    if (name == "server") {
        o << "# Server\r\n"
          << "redis_version:7.2.11\r\n" << "redis_git_sha1:00000000\r\n" << "redis_git_dirty:0\r\n" << "redis_build_id:ribbit-memory-v3\r\n"
          << "redis_mode:standalone\r\n" << "os:Linux\r\n" << "arch_bits:64\r\n" << "multiplexing_api:threads\r\n"
          << "process_id:" << getpid() << "\r\n" << "run_id:ribbit0000000000000000000000000000000000000\r\n"
          << "tcp_port:" << g.port << "\r\n" << "server_time_usec:" << now * 1000 << "\r\n"
          << "uptime_in_seconds:" << (now - g.start_ms) / 1000 << "\r\n" << "uptime_in_days:0\r\n" << "hz:10\r\n" << "configured_hz:10\r\n"
          << "executable:" << g.executable << "\r\n" << "config_file:" << g.config_file << "\r\n";
    } else if (name == "clients") {
        size_t n = g.clients.size();
        o << "# Clients\r\n" << "connected_clients:" << n << "\r\n" << "cluster_connections:0\r\n" << "maxclients:10000\r\n"
          << "client_recent_max_input_buffer:0\r\n" << "client_recent_max_output_buffer:0\r\n"
          << "blocked_clients:" << g.blocked_clients.load() << "\r\n" << "tracking_clients:0\r\n" << "clients_in_timeout_table:0\r\n"
          << "total_blocking_keys:" << blocking_keys(false) << "\r\n" << "total_blocking_keys_on_nokey:" << blocking_keys(true) << "\r\n";
    } else if (name == "ribbit") {
        // what the memory is made of, right now: live objects by kind, and the reclamation's state
        MemStats& ms = memstats();
        long rss = 0; { FILE* f = fopen("/proc/self/status", "r"); char line[256]; while (f && fgets(line, sizeof line, f)) if (!strncmp(line, "VmRSS:", 6)) rss = atol(line + 6); if (f) fclose(f); }
        long objs = ms.vars.load() + ms.nodes.load() + ms.bags.load() + ms.slots.load() + ms.mapnodes.load() + ms.maps.load() + ms.batches.load();
        o << "# Ribbit\r\n"
          << "rss_kb:" << rss << "\r\n"
          << "live_variables:" << ms.vars.load() << "\r\n"
          << "live_cell_nodes:" << ms.nodes.load() << "\r\n"
          << "live_bags:" << ms.bags.load() << "\r\n"
          << "live_slots:" << ms.slots.load() << "\r\n"
          << "live_maps:" << ms.maps.load() << "\r\n"
          << "live_map_nodes:" << ms.mapnodes.load() << "\r\n"
          << "live_batches:" << ms.batches.load() << "\r\n"
          << "live_objects_total:" << objs << "\r\n"
          << "bytes_per_live_object_rss:" << (objs ? rss * 1024 / objs : 0) << "\r\n"
          << "ebr_epoch:" << ebr::global().epoch.load() << "\r\n"
          << "ebr_threads_registered:" << ebr::global().high.load() << "\r\n"
          << "ebr_retired_pending:" << ms.retired_pending.load() << "\r\n"
          << "ebr_retired_freed:" << ms.retired_freed.load() << "\r\n"
          << "ebr_collects:" << ms.collects.load() << "\r\n"
          << "ebr_epoch_advances:" << ms.epoch_advances.load() << "\r\n"
          << "blocked_clients:" << g.blocked_clients.load() << "\r\n"
          << "clients_registered:" << g.clients.size() << "\r\n";
    } else if (name == "memory") {
        int64_t um = db::used_bytes() + 1024 * 1024;
        o << "# Memory\r\n" << "used_memory:" << um << "\r\n" << "used_memory_human:" << um / 1024 << "K\r\n" << "used_memory_rss:" << um << "\r\n"
          << "used_memory_peak:" << um << "\r\n" << "used_memory_lua:0\r\n" << "used_memory_vm_eval:0\r\n" << "used_memory_scripts:0\r\n"
          << "maxmemory:" << g.maxmemory.load() << "\r\n" << "maxmemory_policy:" << g.cfg.get("maxmemory-policy") << "\r\n"
          << "allocator_frag_ratio:1.00\r\n" << "mem_fragmentation_ratio:1.00\r\n" << "mem_allocator:ribbit\r\n" << "mem_not_counted_for_evict:0\r\n"
          << "mem_clients_normal:0\r\n" << "mem_clients_slaves:0\r\n" << "lazyfree_pending_objects:0\r\n" << "lazyfreed_objects:0\r\n";
    } else if (name == "persistence") {
        o << "# Persistence\r\n" << "loading:0\r\n" << "async_loading:0\r\n" << "rdb_changes_since_last_save:" << g.stats.dirty.load() << "\r\n"
          << "rdb_bgsave_in_progress:0\r\n" << "rdb_last_save_time:" << g.start_ms / 1000 << "\r\n" << "rdb_last_bgsave_status:ok\r\n"
          << "aof_enabled:0\r\n" << "aof_rewrite_in_progress:0\r\n" << "aof_rewrite_scheduled:0\r\n" << "aof_last_bgrewrite_status:ok\r\n"
          << "current_cow_size:0\r\n" << "module_fork_in_progress:0\r\n";
    } else if (name == "stats") {
        o << "# Stats\r\n" << "total_connections_received:" << g.stats.connections_received.load() << "\r\n"
          << "total_commands_processed:" << g.stats.commands.load() << "\r\n" << "instantaneous_ops_per_sec:0\r\n"
          << "total_net_input_bytes:0\r\n" << "total_net_output_bytes:0\r\n" << "rejected_connections:" << g.stats.rejected_conn.load() << "\r\n"
          << "sync_full:0\r\n" << "sync_partial_ok:0\r\n" << "sync_partial_err:0\r\n"
          << "expired_keys:" << g.stats.expired_keys.load() << "\r\n" << "expired_stale_perc:0.00\r\n" << "expired_time_cap_reached_count:0\r\n"
          << "evicted_keys:" << g.stats.evicted_keys.load() << "\r\n" << "evicted_clients:0\r\n"
          << "keyspace_hits:" << g.stats.hits.load() << "\r\n" << "keyspace_misses:" << g.stats.misses.load() << "\r\n"
          << "pubsub_channels:0\r\n" << "pubsub_patterns:0\r\n" << "pubsubshard_channels:0\r\n" << "latest_fork_usec:0\r\n" << "total_forks:0\r\n"
          << "tracking_total_keys:0\r\n" << "total_error_replies:" << g.stats.error_replies.load() << "\r\n"
          << "unexpected_error_replies:0\r\n" << "total_reads_processed:0\r\n" << "total_writes_processed:0\r\n"
          << "instantaneous_eventloop_cycles_per_sec:0\r\n" << "instantaneous_eventloop_duration_usec:0\r\n" << "acl_access_denied_auth:0\r\n"
          << "acl_access_denied_cmd:0\r\n" << "acl_access_denied_key:0\r\n" << "acl_access_denied_channel:0\r\n";
    } else if (name == "replication") {
        o << "# Replication\r\n" << "role:master\r\n" << "connected_slaves:0\r\n" << "master_failover_state:no-failover\r\n"
          << "master_replid:0000000000000000000000000000000000000000\r\n" << "master_replid2:0000000000000000000000000000000000000000\r\n"
          << "master_repl_offset:" << g.mem.last_id() << "\r\n" << "second_repl_offset:-1\r\n" << "repl_backlog_active:0\r\n"
          << "repl_backlog_size:0\r\n" << "repl_backlog_first_byte_offset:0\r\n" << "repl_backlog_histlen:0\r\n";
    } else if (name == "cpu") {
        struct rusage ru; getrusage(RUSAGE_SELF, &ru);
        o << "# CPU\r\n" << "used_cpu_sys:" << ru.ru_stime.tv_sec << "." << ru.ru_stime.tv_usec << "\r\n"
          << "used_cpu_user:" << ru.ru_utime.tv_sec << "." << ru.ru_utime.tv_usec << "\r\n"
          << "used_cpu_sys_children:0.000000\r\n" << "used_cpu_user_children:0.000000\r\n";
    } else if (name == "modules") {
        o << "# Modules\r\n";
    } else if (name == "errorstats") {
        o << "# Errorstats\r\n";
        std::lock_guard<std::mutex> lk(g.stats.mu);
        for (auto& kv : g.stats.errorstat) o << "errorstat_" << kv.first << ":count=" << kv.second << "\r\n";
    } else if (name == "commandstats") {
        o << "# Commandstats\r\n";
        std::map<std::string, const CmdEntry*> sorted;
        for (auto& kv : g.cmds) if (kv.second.calls || kv.second.rejected) sorted[kv.first] = &kv.second;
        for (auto& kv : sorted) {
            uint64_t calls = kv.second->calls, usec = kv.second->usec;
            char upc[64]; snprintf(upc, sizeof upc, "%.2f", calls ? (double)usec / calls : 0.0);
            o << "cmdstat_" << kv.first << ":calls=" << calls << ",usec=" << usec << ",usec_per_call=" << upc
              << ",rejected_calls=" << kv.second->rejected.load() << ",failed_calls=" << kv.second->failed.load() << "\r\n";
        }
    } else if (name == "cluster") {
        o << "# Cluster\r\n" << "cluster_enabled:0\r\n";
    } else if (name == "keyspace") {
        o << "# Keyspace\r\n";
        for (int d = 0; d < g.databases; d++) {
            size_t n = db::dbsize(d);
            if (!n) continue;
            size_t exp = g.mem.indexed_count(db::svc(d)) - db::expired_count(d);
            o << "db" << d << ":keys=" << n << ",expires=" << exp << ",avg_ttl=0\r\n";
        }
    } else if (name == "latencystats") {
        o << "# Latencystats\r\n";
    }
    return o.str();
}

static void cmd_info(Client&, const Argv& a, Reply& r) {
    static const char* def[] = {"server","clients","memory","persistence","stats","replication","cpu","modules","errorstats","cluster","keyspace",nullptr};
    static const char* all_extra[] = {"commandstats","latencystats","ribbit",nullptr};
    std::vector<std::string> want; std::set<std::string> seen;
    auto add = [&](const std::string& s) { if (seen.insert(s).second) want.push_back(s); };
    if (a.size() == 1) { for (int i = 0; def[i]; i++) add(def[i]); }
    for (size_t i = 1; i < a.size(); i++) {
        std::string s = lower(a[i]);
        if (s == "default") { for (int j = 0; def[j]; j++) add(def[j]); }
        else if (s == "all" || s == "everything") { for (int j = 0; def[j]; j++) add(def[j]); for (int j = 0; all_extra[j]; j++) add(all_extra[j]); }
        else add(s);
    }
    std::string out; bool first = true;
    for (auto& s : want) {
        std::string sec = info_section(s);
        if (sec.empty()) continue;
        if (!first) out += "\r\n";
        out += sec; first = false;
    }
    r.verbatim("txt", out);
}

// ---------------------------------------------------------------- CONFIG
static void cmd_config_get(Client&, const Argv& a, Reply& r) {
    std::map<std::string, std::string> res;
    std::lock_guard<std::mutex> lk(g.cfg.mu);
    for (size_t i = 2; i < a.size(); i++) {
        for (int j = 0; j < CFG_TABLE_N; j++) {
            const CfgSpec& c = CFG_TABLE[j];
            bool exact = a[i] == c.name || (c.alias[0] && a[i] == c.alias);
            if (c.hidden && !exact) continue;
            auto vit = g.cfg.v.find(c.name); std::string val = vit == g.cfg.v.end() ? c.def : vit->second;
            if (stringmatch(a[i], c.name, true)) res[c.name] = val;
            if (c.alias[0] && stringmatch(a[i], c.alias, true)) res[c.alias] = val;
        }
    }
    r.map(res.size());
    for (auto& kv : res) { r.bulk(kv.first); r.bulk(kv.second); }
}

// validate (no side effects). Returns false with err set.
static bool config_check(const std::string& k, const std::string& v, std::string& err, bool local_client = true) {
    long long n;
    const CfgSpec* sp = Config::spec(k);
    if (sp && sp->immutable) { err = "can't set immutable config"; return false; }
    if (sp && sp->prot) { std::string en = lower(g.cfg.get("enable-protected-configs")); if (en == "no" || (en == "local" && !local_client)) { err = "can't set protected config"; return false; } }
    if (k == "maxmemory" || k == "proto-max-bulk-len" || k == "maxmemory-clients") {
        std::string t = v; bool pct = false;
        if (k == "maxmemory-clients" && !t.empty() && t.back() == '%') { pct = true; t.pop_back(); }
        double mult = 1; if (!t.empty()) { char u = tolower(t.back());
            if (u == 'b' && t.size() > 1) { char p = tolower(t[t.size()-2]); if (p == 'k' || p == 'm' || p == 'g') { mult = p == 'k' ? 1024 : p == 'm' ? 1048576 : 1073741824; t.resize(t.size()-2); } else t.pop_back(); }
            else if (u == 'k' || u == 'm' || u == 'g') { mult = u == 'k' ? 1000 : u == 'm' ? 1000000 : 1000000000; t.pop_back(); } }
        if (!string2ll(t, n) || n < 0) { err = "argument must be a memory value"; return false; }
        if (pct && n > 100) { err = "percentage argument must be less or equal to 100"; return false; }
        if (k == "proto-max-bulk-len" && (double)n * mult < 1024 * 1024) { err = "argument must be between 1048576 and 9223372036854775807 inclusive"; return false; }
        return true;
    }
    if (k == "port" || k == "bind") { if (v != g.cfg.get(k)) { err = k == "port" ? "Unable to listen on this port: Ribbit has one listener for the life of the process" : "the bind address is fixed for the life of the process"; return false; } return true; }
    if (k == "maxmemory-policy") {
        static const char* pol[] = {"volatile-lru","volatile-lfu","volatile-random","volatile-ttl","allkeys-lru","allkeys-lfu","allkeys-random","noeviction",nullptr};
        for (int i = 0; pol[i]; i++) if (lower(v) == pol[i]) return true;
        err = "argument(s) must be one of the following: volatile-lru, volatile-lfu, volatile-random, volatile-ttl, allkeys-lru, allkeys-lfu, allkeys-random, noeviction"; return false;
    }
    return true;
}
static void config_apply(const std::string& k, const std::string& v) {
    if (k == "requirepass") g.requirepass = v;
    else if (k == "maxmemory") g.maxmemory = atoll(memory_value(v).c_str());
    else if (k == "proto-max-bulk-len") g.max_bulk = atoll(memory_value(v).c_str());
}

static void cmd_config_set(Client& c, const Argv& a, Reply& r) {
    if ((a.size() - 2) % 2) { r.error("ERR wrong number of arguments for 'config|set' command"); return; }
    std::set<std::string> dup; std::vector<std::pair<std::string, std::string>> kv;
    for (size_t i = 2; i < a.size(); i += 2) {
        std::string canon; std::string k = lower(a[i]);
        if (!Config::spec(k, &canon)) { r.error("ERR Unknown option or number of arguments for CONFIG SET - '" + a[i] + "'"); return; }
        if (!dup.insert(canon).second) { r.error("ERR CONFIG SET failed (possibly related to argument '" + a[i] + "') - duplicate parameter"); return; }
        std::string err;
        if (!config_check(canon, a[i+1], err, c.addr.rfind("127.", 0) == 0)) { r.error("ERR CONFIG SET failed (possibly related to argument '" + a[i] + "') - " + err); return; }
        kv.emplace_back(canon, a[i+1]);
    }
    for (auto& p : kv) { config_apply(p.first, p.second); g.cfg.set(p.first, memory_value(p.second)); }
    r.ok();
}
static void cmd_config_resetstat(Client&, const Argv&, Reply& r) { g.stats.reset(); r.ok(); }
static void cmd_config_rewrite(Client&, const Argv&, Reply& r) { r.error("ERR not applicable: CONFIG REWRITE rewrites redis.conf; Ribbit has no config file to rewrite"); }
static void cmd_config_help(Client&, const Argv&, Reply& r) {
    const char* lines[] = {"CONFIG <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","GET <pattern>","    Return parameters matching the glob-like <pattern> and their values.","SET <directive> <value>","    Set the configuration <directive> to <value>.","RESETSTAT","    Reset statistics reported by the INFO command.","REWRITE","    Rewrite the configuration file.","HELP","    Print this help."};
    r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
}

// ---------------------------------------------------------------- CLIENT
static std::string client_info_line(Client& c) {
    int64_t now = Memory::now_ms();
    std::ostringstream o;
    o << "id=" << c.id << " addr=" << c.addr << " laddr=" << c.laddr << " fd=" << c.fd << " name=" << c.name
      << " age=" << (now - c.created_ms) / 1000 << " idle=" << (now - c.last_cmd_ms) / 1000 << " flags=" << (c.waiting_on.load() ? "b" : c.flags)
      << " db=" << c.db << " sub=0 psub=0 ssub=0 multi=-1 qbuf=" << c.qbuf << " qbuf-free=0 argv-mem=0 multi-mem=0 rbs=1024 rbp=0 obl=0 oll=0 omem=0 tot-mem=1024"
      << " events=r cmd=" << c.lastcmd << " user=" << c.user << " redir=-1 resp=" << c.proto << " lib-name=" << c.libname << " lib-ver=" << c.libver;
    return o.str();
}

static std::vector<Client*> all_clients() {           // raw: valid for this command's epoch
    std::vector<Client*> v;
    g.clients.walk_all([&](LFMap<uint64_t, Client*>::Node* n) { v.push_back(n->val); return true; });
    return v;
}

static void cmd_client_id(Client& c, const Argv&, Reply& r) { r.integer((long long)c.id); }
static void cmd_client_info(Client& c, const Argv&, Reply& r) { r.verbatim("txt", client_info_line(c) + "\n"); }
static void cmd_client_list(Client&, const Argv& a, Reply& r) {
    std::set<uint64_t> ids; bool by_id = false;
    if (a.size() > 2) {
        if (lower(a[2]) == "id" && a.size() > 3) { by_id = true; for (size_t i = 3; i < a.size(); i++) { long long v; if (!string2ll(a[i], v) || v <= 0) { r.error("ERR Invalid client ID"); return; } ids.insert((uint64_t)v); } }
        else if (lower(a[2]) == "type" && a.size() == 4) { std::string t = lower(a[3]); if (t != "normal" && t != "master" && t != "replica" && t != "pubsub" && t != "slave") { r.error("ERR Unknown client type '" + a[3] + "'"); return; } if (t != "normal") { r.verbatim("txt", ""); return; } }
        else { err_syntax(r); return; }
    }
    std::string out;
    for (auto& c : all_clients()) { if (by_id && !ids.count(c->id)) continue; out += client_info_line(*c) + "\n"; }
    r.verbatim("txt", out);
}
static void cmd_client_setname(Client& c, const Argv& a, Reply& r) {
    if (a[2].find_first_of(" \n\r\t") != std::string::npos) { r.error("ERR Client names cannot contain spaces, newlines or special characters."); return; }
    c.name = a[2]; r.ok();
}
static void cmd_client_getname(Client& c, const Argv&, Reply& r) { if (c.name.empty()) r.null(); else r.bulk(c.name); }
static void cmd_client_setinfo(Client& c, const Argv& a, Reply& r) {
    std::string which = lower(a[2]);
    if (a[3].find_first_of(" \n\r\t") != std::string::npos) { r.error("ERR " + which + " cannot contain spaces, newlines or special characters."); return; }
    if (which == "lib-name") c.libname = a[3];
    else if (which == "lib-ver") c.libver = a[3];
    else { r.error("ERR Unrecognized option '" + a[2] + "'"); return; }
    r.ok();
}
static void cmd_client_reply(Client& c, const Argv& a, Reply& r) {
    std::string m = lower(a[2]);
    if (m == "on") { c.reply_mode = 0; c.skip_count = 0; r.ok(); }
    else if (m == "off") c.reply_mode = 1;
    else if (m == "skip") { c.reply_mode = 0; c.skip_count = 2; }   // this (empty) reply and the next one
    else err_syntax(r);
}
static void cmd_client_noevict(Client& c, const Argv& a, Reply& r) {
    std::string m = lower(a[2]); if (m == "on") c.no_evict = true; else if (m == "off") c.no_evict = false; else { err_syntax(r); return; } r.ok();
}
static void cmd_client_notouch(Client& c, const Argv& a, Reply& r) {
    std::string m = lower(a[2]); if (m == "on") c.no_touch = true; else if (m == "off") c.no_touch = false; else { err_syntax(r); return; } r.ok();
}
static void cmd_client_kill(Client& c, const Argv& a, Reply& r) {
    std::string addr, laddr, user; uint64_t id = 0; bool skipme = true; std::string type;
    if (a.size() == 3) { addr = a[2]; skipme = false; }
    else if (a.size() > 3) {
        for (size_t i = 2; i < a.size(); i += 2) {
            if (i + 1 >= a.size()) { err_syntax(r); return; }
            std::string o = lower(a[i]);
            if (o == "id") { long long v; if (!string2ll(a[i+1], v) || v < 1) { r.error("ERR client-id should be greater than 0"); return; } id = (uint64_t)v; }
            else if (o == "type") { type = lower(a[i+1]); if (type != "normal" && type != "master" && type != "replica" && type != "slave" && type != "pubsub") { r.error("ERR Unknown client type '" + a[i+1] + "'"); return; } }
            else if (o == "addr") addr = a[i+1];
            else if (o == "laddr") laddr = a[i+1];
            else if (o == "user") { user = a[i+1]; if (user != "default") { r.error("ERR No such user '" + user + "'"); return; } }
            else if (o == "skipme") { std::string v = lower(a[i+1]); if (v == "yes") skipme = true; else if (v == "no") skipme = false; else { err_syntax(r); return; } }
            else { err_syntax(r); return; }
        }
    } else { err_syntax(r); return; }
    int killed = 0; bool close_me = false;
    for (auto& cl : all_clients()) {
        if (!addr.empty() && cl->addr != addr) continue;
        if (!laddr.empty() && cl->laddr != laddr) continue;
        if (!type.empty() && type != "normal") continue;
        if (id && cl->id != id) continue;
        if (!user.empty() && cl->user != user) continue;
        if (cl == &c && skipme) continue;
        if (cl == &c) close_me = true; else cl->kill();
        killed++;
    }
    if (a.size() == 3) { if (!killed) r.error("ERR No such client"); else r.ok(); }
    else r.integer(killed);
    if (close_me) c.close_after_reply = true;
}
static void cmd_client_unblock(Client&, const Argv& a, Reply& r) {
    bool err = false;
    if (a.size() == 4) { std::string m = lower(a[3]); if (m == "timeout") err = false; else if (m == "error") err = true; else { r.error("ERR CLIENT UNBLOCK reason should be TIMEOUT or ERROR"); return; } }
    long long id; if (!string2ll(a[2], id)) { err_notint(r); return; }
    Client* target = nullptr;
    { auto* n = g.clients.get((uint64_t)id); if (n) target = n->val; }
    WaitWord* w = target ? target->waiting_on.load() : nullptr;
    if (!w) {
        // [DIAG-UNBLOCK] a miss is the interesting case: log everything that could explain it
        std::string all; size_t nreg = 0;
        g.clients.walk_all([&](LFMap<uint64_t, Client*>::Node* n) { nreg++; Client* x = n->val; all += " " + std::to_string(x->id) + (x->waiting_on.load() ? "b" : "") + (x->closing ? "c" : ""); return true; });
        logmsg('#', "[DIAG-UNBLOCK] id=" + std::to_string(id) + " target=" + (target ? "found" : "MISSING") + " waiting_on=" + (w ? "set" : "null")
                    + " blocked_clients=" + std::to_string(g.blocked_clients.load()) + " registry_size=" + std::to_string(g.clients.size()) + " walked=" + std::to_string(nreg)
                    + " target_closing=" + (target ? std::to_string((int)target->closing) : "-") + " target_lastcmd=" + (target ? target->lastcmd : "-")
                    + " target_worker=" + (target ? std::to_string(target->worker) : "-") + " ids:" + all);
        r.integer(0); return;
    }
    target->unblock = err ? 2 : 1;
    w->gen.fetch_add(1, std::memory_order_release);
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w->gen), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0);
    r.integer(1);
}
static void cmd_client_help(Client&, const Argv&, Reply& r) {
    const char* lines[] = {"CLIENT <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","ID","    Return the ID of the current connection.","INFO","    Return information about the current client connection.","LIST","    Return information about client connections.","KILL <ip:port>|ID <id>|...","    Kill connections.","SETNAME <name>","    Assign the name <name> to the current connection.","GETNAME","    Get the name of the current connection.","REPLY (ON|OFF|SKIP)","    Control the replies sent to the current connection.","UNBLOCK <clientid> [TIMEOUT|ERROR]","    Unblock the specified blocked client.","HELP","    Print this help."};
    r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
}

// ---------------------------------------------------------------- COMMAND
static void reply_cmd_info(Reply& r, const CmdSpec& s) {
    std::string full = s.container[0] ? std::string(s.container) + "|" + s.name : std::string(s.name);
    r.array(10);
    r.bulk(full); r.integer(s.arity);
    std::vector<std::string> flags; { std::istringstream is(s.flags); std::string f; while (is >> f) flags.push_back(f); }
    r.set(flags.size()); for (auto& f : flags) r.status(f);
    r.integer(s.first); r.integer(s.last); r.integer(s.step);
    std::vector<std::string> acl; { std::istringstream is(s.acl); std::string f; while (is >> f) acl.push_back(f); }
    r.set(acl.size()); for (auto& f : acl) r.status(f);
    r.set(0);          // tips
    r.array(0);        // key specs (not exposed)
    // subcommands
    std::vector<const CmdSpec*> subs;
    if (!s.container[0]) for (int i = 0; i < CMD_TABLE_N; i++) if (std::string(CMD_TABLE[i].container) == s.name) subs.push_back(&CMD_TABLE[i]);
    r.array(subs.size()); for (auto sp : subs) reply_cmd_info(r, *sp);
}
static const CmdSpec* spec_by_name(const std::string& name) {
    std::string l = lower(name);
    for (int i = 0; i < CMD_TABLE_N; i++) {
        std::string full = CMD_TABLE[i].container[0] ? std::string(CMD_TABLE[i].container) + "|" + CMD_TABLE[i].name : std::string(CMD_TABLE[i].name);
        if (full == l) return &CMD_TABLE[i];
    }
    return nullptr;
}
static int top_level_count() { int n = 0; for (int i = 0; i < CMD_TABLE_N; i++) if (!CMD_TABLE[i].container[0]) n++; return n; }
static void cmd_command(Client&, const Argv&, Reply& r) {
    r.array(top_level_count());
    for (int i = 0; i < CMD_TABLE_N; i++) if (!CMD_TABLE[i].container[0]) reply_cmd_info(r, CMD_TABLE[i]);
}
static void cmd_command_count(Client&, const Argv&, Reply& r) { r.integer(top_level_count()); }
static void cmd_command_info(Client& c, const Argv& a, Reply& r) {
    if (a.size() == 2) { cmd_command(c, a, r); return; }
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) { const CmdSpec* s = spec_by_name(a[i]); if (s) reply_cmd_info(r, *s); else r.null(); }
}
static void cmd_command_list(Client&, const Argv& a, Reply& r) {
    std::string filter_kind, filter_val;
    if (a.size() > 2) {
        if (a.size() != 5 || lower(a[2]) != "filterby") { err_syntax(r); return; }
        filter_kind = lower(a[3]); filter_val = a[4];
        if (filter_kind != "aclcat" && filter_kind != "pattern" && filter_kind != "module") { err_syntax(r); return; }
    }
    std::vector<std::string> out;
    for (int i = 0; i < CMD_TABLE_N; i++) {
        const CmdSpec& s = CMD_TABLE[i];
        std::string full = s.container[0] ? std::string(s.container) + "|" + s.name : std::string(s.name);
        if (filter_kind == "module") continue;
        if (filter_kind == "aclcat") { std::string acl = std::string(" ") + s.acl + " "; if (acl.find(" @" + lower(filter_val) + " ") == std::string::npos) continue; }
        if (filter_kind == "pattern" && !stringmatch(filter_val, full, true)) continue;
        out.push_back(full);
    }
    r.set(out.size()); for (auto& s : out) r.bulk(s);
}
static void cmd_command_docs(Client&, const Argv& a, Reply& r) {
    // minimal: name -> {summary, group}. redis-cli tolerates this.
    std::vector<const CmdSpec*> specs;
    if (a.size() == 2) { for (int i = 0; i < CMD_TABLE_N; i++) if (!CMD_TABLE[i].container[0]) specs.push_back(&CMD_TABLE[i]); }
    else for (size_t i = 2; i < a.size(); i++) { const CmdSpec* s = spec_by_name(a[i]); if (s) specs.push_back(s); }
    r.map(specs.size());
    for (auto s : specs) { r.bulk(s->name); r.map(2); r.bulk("summary"); r.bulk(s->summary); r.bulk("group"); r.bulk(s->group); }
}
static void cmd_command_getkeys(Client&, const Argv& a, Reply& r) {
    if (a.size() < 3) { r.error("ERR wrong number of arguments for 'command|getkeys' command"); return; }
    std::string fullname; bool sub; Argv sub_argv(a.begin() + 2, a.end());
    const CmdEntry* e = find_cmd(lower(a[2]), sub_argv, fullname, sub);
    if (!e || sub) { r.error("ERR Invalid command specified"); return; }
    const CmdSpec* s = e->spec; int argc = (int)sub_argv.size();
    if ((s->arity > 0 && argc != s->arity) || (s->arity < 0 && argc < -s->arity)) { r.error("ERR Invalid number of arguments specified for command"); return; }
    if (s->first == 0) { r.error("ERR The command has no key arguments"); return; }
    if (fullname == "sort" || fullname == "sort_ro") {          // key, plus every STORE destination
        std::vector<std::string> ks{sub_argv[1]}; std::string last_store;
        if (fullname == "sort") for (size_t i = 2; i + 1 < sub_argv.size(); i++) { if (str_eq_ci(sub_argv[i], "store")) { last_store = sub_argv[i+1]; i++; } else if (str_eq_ci(sub_argv[i], "by") || str_eq_ci(sub_argv[i], "get")) i++; else if (str_eq_ci(sub_argv[i], "limit")) i += 2; }
        if (!last_store.empty()) ks.push_back(last_store);          // the last STORE wins, as in the command itself
        r.array(ks.size()); for (auto& k : ks) r.bulk(k); return;
    }
    if (std::string(s->flags).find("movablekeys") != std::string::npos) { r.error("ERR Invalid arguments specified for command"); return; }
    int last = s->last < 0 ? argc + s->last : s->last;
    std::vector<int> ks; for (int i = s->first; i <= last && i < argc; i += s->step) ks.push_back(i);
    if (ks.empty()) { r.error("ERR Invalid arguments specified for command"); return; }
    r.array(ks.size()); for (int k : ks) r.bulk(sub_argv[k]);
}
static void cmd_command_getkeysandflags(Client& c, const Argv& a, Reply& r) {
    Reply tmp; tmp.proto = r.proto; cmd_command_getkeys(c, a, tmp);
    if (!tmp.out.empty() && tmp.out[0] == '-') { r.out += tmp.out; return; }
    std::string fullname; bool sub; Argv sub_argv(a.begin() + 2, a.end());
    const CmdEntry* e = find_cmd(lower(a[2]), sub_argv, fullname, sub);
    const CmdSpec* s = e->spec; int argc = (int)sub_argv.size();
    int last = s->last < 0 ? argc + s->last : s->last;
    std::vector<int> ks; for (int i = s->first; i <= last && i < argc; i += s->step) ks.push_back(i);
    bool w = std::string(s->flags).find("write") != std::string::npos;
    r.array(ks.size());
    for (int k : ks) { r.array(2); r.bulk(sub_argv[k]); r.set(2); r.status(w ? "RW" : "RO"); r.status(w ? "update" : "access"); }
}
static void cmd_command_help(Client&, const Argv&, Reply& r) {
    const char* lines[] = {"COMMAND <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","(no subcommand)","    Return details about all Redis commands.","COUNT","    Return the total number of commands in this Redis server.","LIST","    Return a list of all commands in this Redis server.","INFO [<command-name> ...]","    Return details about multiple Redis commands.","DOCS [<command-name> ...]","    Return documentation details about multiple Redis commands.","GETKEYS <full-command>","    Return the keys from a full Redis command.","GETKEYSANDFLAGS <full-command>","    Return the keys and the access flags from a full Redis command.","HELP","    Print this help."};
    r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
}

// ---------------------------------------------------------------- DEBUG
static void cmd_debug(Client& c, const Argv& a, Reply& r) {
    std::string sub = lower(a[1]);
    if (sub == "sleep") {
        double s = a.size() > 2 ? atof(a[2].c_str()) : 0;
        // this participant sleeps; nobody else does
        std::this_thread::sleep_for(std::chrono::microseconds((int64_t)(s * 1e6)));
        r.ok();
    } else if (sub == "set-active-expire" && a.size() == 3) {
        r.ok();      // accepted, no effect: liveness is derived and there is no reaper to switch off
    } else if (sub == "log" && a.size() == 3) {
        logmsg('#', "DEBUG LOG: " + a[2]); r.ok();
    } else if (sub == "protocol" && a.size() == 3) {
        std::string t = lower(a[2]);
        if (t == "string") r.bulk("Hello World");
        else if (t == "integer") r.integer(12345);
        else if (t == "double") r.double_(3.141);
        else if (t == "bignum") r.bignum("1234567999999999999999999999999999999");
        else if (t == "null") r.null();
        else if (t == "array") { r.array(3); for (int j = 0; j < 3; j++) r.integer(j); }
        else if (t == "set") { r.set(3); for (int j = 0; j < 3; j++) r.integer(j); }
        else if (t == "map") { r.map(3); for (int j = 0; j < 3; j++) { r.integer(j); r.boolean(j == 1); } }
        else if (t == "attrib") { r.attribute(1); r.bulk("key-popularity"); r.array(2); r.bulk("key:123"); r.integer(90); r.bulk("Some real reply following the attribute"); }
        else if (t == "push") { if (c.proto != 3) { r.error("ERR RESP2 is not supported by this command"); return; } r.push(2); r.bulk("server-cpu-usage"); r.integer(42); r.status("Some real reply following the push reply"); }
        else if (t == "true") r.boolean(true);
        else if (t == "false") r.boolean(false);
        else if (t == "verbatim") r.verbatim("txt", "This is a verbatim\nstring");
        else r.error("ERR Wrong protocol type name. Please use one of the following: string|integer|double|bignum|null|array|set|map|attrib|push|verbatim|true|false");
    } else if (sub == "object" && a.size() == 3) {
        VarPtr v = db::live_var(c.db, a[2]);
        if (db::kind_of(v) == Kind::None) { r.error("ERR no such key"); return; }
        size_t bytes = 0; uint64_t newest = 0; v->each_all([&](const std::string&, const CellPtr& cp) { bytes += cp->bag->s.size(); newest = std::max(newest, cp->id); return true; });
        r.status("Value at:ribbit refcount:1 encoding:ribbit-cells serializedlength:" + std::to_string(bytes) + " lru:0 lru_seconds_idle:0 cells:" + std::to_string(db::elem_count(v)) + " write_order_id:" + std::to_string(newest));
    } else if (sub == "set-disable-deny-scripts" || sub == "pause-cron" || sub == "quicklist-packed-threshold" || sub == "replybuffer" || sub == "stringmatch-len" || sub == "set-skip-checksum-validation" || sub == "dict-resizing") {
        r.ok();
    } else if (sub == "help") {
        const char* lines[] = {"DEBUG <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","LOG <message>","    Write <message> to the server log.","OBJECT <key>","    Show low level info about `key` and associated value.","PROTOCOL <type>","    Reply with a test value of the specified type.","SLEEP <seconds>","    Stop the server for <seconds>. Decimals allowed.","HELP","    Print this help."};
        r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l);
    } else if (sub == "reload" || sub == "loadaof" || sub == "digest" || sub == "digest-value" || sub == "populate" || sub == "change-repl-id" || sub == "jmap" || sub == "segfault" || sub == "restart" || sub == "crash-and-recover") {
        r.error("ERR not applicable: DEBUG " + sub + " belongs to RDB/AOF/replication; Ribbit materializes, it does not reload");
    } else {
        r.error("ERR unknown subcommand '" + a[1] + "'. Try DEBUG HELP.");
    }
}

// ---------------------------------------------------------------- db-wide
static void cmd_dbsize(Client& c, const Argv&, Reply& r) { r.integer((long long)db::dbsize(c.db)); }
static bool flush_args_ok(const Argv& a, Reply& r) {
    if (a.size() > 2) { err_syntax(r); return false; }
    if (a.size() == 2) { std::string m = lower(a[1]); if (m != "async" && m != "sync") { err_syntax(r); return false; } }
    return true;
}
static void cmd_flushdb(Client& c, const Argv& a, Reply& r) { if (!flush_args_ok(a, r)) return; db::flush(c.db); r.ok(); }
static void cmd_flushall(Client&, const Argv& a, Reply& r) { if (!flush_args_ok(a, r)) return; for (int d = 0; d < g.databases; d++) db::flush(d); r.ok(); }
static void cmd_swapdb(Client&, const Argv& a, Reply& r) {
    long long a1, a2;
    if (!string2ll(a[1], a1) || !string2ll(a[2], a2)) { r.error("ERR invalid first DB index"); return; }
    if (a1 < 0 || a1 >= g.databases || a2 < 0 || a2 >= g.databases) { r.error("ERR DB index is out of range"); return; }
    if (a1 != a2) g.mem.swap(db::svc((int)a1), db::svc((int)a2));
    g.stats.dirty++; r.ok();
}
static void cmd_time(Client&, const Argv&, Reply& r) {
    struct timeval tv; gettimeofday(&tv, nullptr);
    r.array(2); r.bulk(std::to_string(tv.tv_sec)); r.bulk(std::to_string(tv.tv_usec));
}
static void cmd_lastsave(Client&, const Argv&, Reply& r) { r.integer(g.start_ms / 1000); }
static void cmd_object(Client& c, const Argv& a, Reply& r) {
    std::string sub = lower(a[1]);
    if (sub == "help") {
        const char* lines[] = {"OBJECT <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","ENCODING <key>","    Return the kind of internal representation used in order to store the value","    associated with a <key>.","FREQ <key>","    Return the access frequency index of the <key>. The returned integer is","    proportional to the logarithm of the recent access frequency of the key.","IDLETIME <key>","    Return the idle time of the <key>, that is the approximated number of","    seconds elapsed since the last access to the key.","REFCOUNT <key>","    Return the number of references of the value associated with the specified","    <key>.","HELP","    Print this help."};
        r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l); return;
    }
    if (a.size() != 3) { r.error("ERR unknown subcommand or wrong number of arguments for '" + a[1] + "'. Try OBJECT HELP."); return; }
    VarPtr v = db::live_var(c.db, a[2]);
    if (db::kind_of(v) == Kind::None) { r.null(); return; }
    int64_t newest = 0; v->each_all([&](const std::string&, const CellPtr& cp) { newest = std::max(newest, cp->written_ms); return true; });
    if (sub == "encoding") r.bulk("ribbit-cells");          // truthful: there is no Redis encoding here. Run the suite with --ignore-encoding.
    else if (sub == "refcount") r.integer(1);
    else if (sub == "idletime") r.integer((Memory::now_ms() - newest) / 1000);
    else if (sub == "freq") r.error("ERR An LFU maxmemory policy is not selected, access frequency not tracked. Please note that when switching between policies at runtime LRU and LFU data will take some time to adjust.");
    else r.error("ERR unknown subcommand '" + a[1] + "'. Try OBJECT HELP.");
}
// MEMORY USAGE: an estimate from what the cells hold; there is no allocator to ask
static void cmd_memory_usage(Client& c, const Argv& a, Reply& r) {
    long long samples = 5;
    for (size_t i = 3; i < a.size(); i++) {
        if (str_eq_ci(a[i], "samples") && i + 1 < a.size()) { if (!string2ll(a[++i], samples) || samples < 0) { err_notint(r); return; } }
        else { err_syntax(r); return; }
    }
    VarPtr v = db::live_var(c.db, a[2]);
    if (db::kind_of(v) == Kind::None) { r.null(); return; }
    long long bytes = 56 + (long long)a[2].size();
    v->each_all([&](const std::string& i, const CellPtr& cp) { bytes += 48 + (long long)i.size() + (long long)cp->bag->s.size(); return true; });
    r.integer(bytes);
}
static void cmd_memory_misc(Client&, const Argv& a, Reply& r) {
    std::string sub = lower(a[1]);
    if (sub == "help") { const char* lines[] = {"MEMORY <subcommand> [<arg> [value] [opt] ...]. Subcommands are:","DOCTOR","    Return memory problems reports.","MALLOC-STATS","    Return internal statistics report from the memory allocator.","PURGE","    Attempt to purge dirty pages for reclamation by the allocator.","STATS","    Return information about the memory usage of the server.","USAGE <key> [SAMPLES <count>]","    Return memory in bytes used by <key> and its value.","HELP","    Print this help."}; r.array(sizeof lines / sizeof *lines); for (auto l : lines) r.status(l); }
    else if (sub == "doctor") r.verbatim("txt", "Ribbit has no allocator to diagnose; values are immutable and shared.");
    else if (sub == "malloc-stats") r.verbatim("txt", "Stats not supported for the current allocator");
    else if (sub == "purge") r.ok();
    else if (sub == "stats") { r.map(2); r.bulk("total.allocated"); r.integer(db::used_bytes()); r.bulk("keys.count"); long long n = 0; for (int d = 0; d < g.databases; d++) n += (long long)db::dbsize(d); r.integer(n); }
    else r.error("ERR unknown subcommand '" + a[1] + "'. Try MEMORY HELP.");
}
static void cmd_shutdown(Client&, const Argv&, Reply&) { logmsg('#', "User requested shutdown..."); fflush(stdout); exit(0); }
static void cmd_not_applicable(Client&, const Argv& a, Reply& r) {
    r.error("ERR not applicable: " + lower(a[0]) + " belongs to replication/persistence/cluster, which Ribbit removes by construction");
}

void register_server_commands() {
    register_cmd("info", cmd_info);
    register_cmd("config|get", cmd_config_get); register_cmd("config|set", cmd_config_set); register_cmd("config|resetstat", cmd_config_resetstat);
    register_cmd("config|rewrite", cmd_config_rewrite); register_cmd("config|help", cmd_config_help);
    register_cmd("client|id", cmd_client_id); register_cmd("client|info", cmd_client_info); register_cmd("client|list", cmd_client_list);
    register_cmd("client|setname", cmd_client_setname); register_cmd("client|getname", cmd_client_getname); register_cmd("client|setinfo", cmd_client_setinfo);
    register_cmd("client|reply", cmd_client_reply); register_cmd("client|no-evict", cmd_client_noevict); register_cmd("client|no-touch", cmd_client_notouch);
    register_cmd("client|kill", cmd_client_kill); register_cmd("client|unblock", cmd_client_unblock); register_cmd("client|help", cmd_client_help);
    register_cmd("command", cmd_command); register_cmd("command|count", cmd_command_count); register_cmd("command|info", cmd_command_info);
    register_cmd("command|list", cmd_command_list); register_cmd("command|docs", cmd_command_docs); register_cmd("command|getkeys", cmd_command_getkeys);
    register_cmd("command|getkeysandflags", cmd_command_getkeysandflags); register_cmd("command|help", cmd_command_help);
    register_cmd("debug", cmd_debug);
    register_cmd("dbsize", cmd_dbsize); register_cmd("flushdb", cmd_flushdb); register_cmd("flushall", cmd_flushall); register_cmd("swapdb", cmd_swapdb);
    register_cmd("time", cmd_time); register_cmd("lastsave", cmd_lastsave);
    for (const char* n : {"object|encoding","object|refcount","object|idletime","object|freq","object|help"}) register_cmd(n, cmd_object);
    for (const char* n : {"save","bgsave","bgrewriteaof","replicaof","slaveof","sync","psync","replconf","wait","waitaof","migrate","dump","restore","failover"}) register_cmd(n, cmd_not_applicable);
    register_cmd("shutdown", cmd_shutdown);
    register_cmd("memory|usage", cmd_memory_usage); for (const char* n : {"memory|help","memory|doctor","memory|malloc-stats","memory|purge","memory|stats"}) register_cmd(n, cmd_memory_misc);
}
