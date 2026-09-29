#include "server.hpp"
#include "cfgtable.hpp"
#include <sys/socket.h>
#include <sys/epoll.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <climits>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <cstring>
#include <cerrno>
#include <ctime>
#include <fstream>
#include <sstream>
#include <chrono>
namespace rr {

Server g;
static std::mutex log_mu;
FILE* log_fp = stdout;   // the front sets it from the logfile directive

void logmsg(char level, const std::string& msg) {
    std::lock_guard<std::mutex> lk(log_mu);
    auto now = std::chrono::system_clock::now();
    time_t t = std::chrono::system_clock::to_time_t(now);
    int ms = (int)(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000);
    char tb[64]; struct tm tmv; localtime_r(&t, &tmv);
    strftime(tb, sizeof tb, "%d %b %Y %H:%M:%S", &tmv);
    fprintf(log_fp, "%d:M %s.%03d %c %s\n", (int)getpid(), tb, ms, level, msg.c_str());
    fflush(log_fp);
}

// ---------------------------------------------------------------- Config
std::string Config::get(const std::string& k) { std::lock_guard<std::mutex> lk(mu); auto it = v.find(k); return it == v.end() ? "" : it->second; }
bool Config::has(const std::string& k) { std::lock_guard<std::mutex> lk(mu); return v.count(k) > 0; }
void Config::set(const std::string& k, const std::string& val) { std::lock_guard<std::mutex> lk(mu); v[k] = val; }

const CfgSpec* Config::spec(const std::string& n, std::string* canonical) {
    for (int i = 0; i < CFG_TABLE_N; i++) {
        if (n == CFG_TABLE[i].name || (CFG_TABLE[i].alias[0] && n == CFG_TABLE[i].alias)) { if (canonical) *canonical = CFG_TABLE[i].name; return &CFG_TABLE[i]; }
    }
    return nullptr;
}

void Config::init_defaults() {
    std::lock_guard<std::mutex> lk(mu);
    for (int i = 0; i < CFG_TABLE_N; i++) v[CFG_TABLE[i].name] = CFG_TABLE[i].def;
    v["port"] = "6379"; v["save"] = "3600 1 300 100 60 10000";
}

// "700mb" -> "734003200"; a percentage or an unparsable value is kept as given
std::string memory_value(const std::string& v) {
    if (v.empty() || v.back() == '%') return v;
    std::string t = v; long long mult = 1;
    char u = (char)tolower(t.back());
    if (u == 'b' && t.size() > 1) { char p = (char)tolower(t[t.size()-2]); if (p == 'k') { mult = 1024; t.resize(t.size()-2); } else if (p == 'm') { mult = 1048576; t.resize(t.size()-2); } else if (p == 'g') { mult = 1073741824; t.resize(t.size()-2); } else t.pop_back(); }
    else if (u == 'k') { mult = 1000; t.pop_back(); } else if (u == 'm') { mult = 1000000; t.pop_back(); } else if (u == 'g') { mult = 1000000000; t.pop_back(); }
    long long n; if (!string2ll(t, n)) return v;
    return std::to_string(n * mult);
}

// one directive from the config file or the command line
static void take_directive(Config& c, const std::string& k0, const std::string& val) {
    std::string k = k0; std::string canon;
    if (Config::spec(k, &canon)) k = canon;
    std::lock_guard<std::mutex> lk(c.mu);
    if (k == "maxmemory" || k == "proto-max-bulk-len" || k == "client-query-buffer-limit" || k == "repl-backlog-size" || k == "maxmemory-clients") { c.v[k] = memory_value(val); return; }
    if (k == "shutdown-on-sigint" || k == "shutdown-on-sigterm") {   // canonical flag order, as Redis prints it
        std::string out; for (const char* f : {"default","save","nosave","now","force"}) { std::string t = " " + lower(val) + " "; if (t.find(std::string(" ") + f + " ") != std::string::npos) { if (!out.empty()) out += ' '; out += f; } }
        c.v[k] = out; return;
    }
    if (k == "save") {
        if (val.empty()) { c.v["save"] = ""; }
        else if (!c.save_seen) c.v["save"] = val;
        else c.v["save"] += (c.v["save"].empty() ? "" : " ") + val;
        c.save_seen = true;
        return;
    }
    c.v[k] = val;
}

static int directive_arity(const std::string& k) {    // -1 = any number
    if (k == "save" || k == "bind" || k == "user" || k == "loadmodule" || k == "sentinel" || k == "include" || k == "shutdown-on-sigint" || k == "shutdown-on-sigterm") return -1;
    if (k == "replicaof" || k == "slaveof" || k == "rename-command") return 2;
    if (k == "client-output-buffer-limit") return 4;
    if (k == "oom-score-adj-values") return 3;
    return 1;
}
[[noreturn]] static void config_fatal(const std::string& where, const std::string& line, const std::string& msg) {
    fprintf(stderr, "\n*** FATAL CONFIG FILE ERROR (Redis 7.2.11) ***\n%s\n>>> '%s'\n%s\n", where.c_str(), line.c_str(), msg.c_str());
    exit(1);
}
static std::string repr_line(const std::string& k, const std::vector<std::string>& vals) {
    std::string l = k; for (auto& v : vals) l += " \"" + v + "\""; return l;
}
// validate one directive the way Redis does at load time; returns an error message or ""
static std::string directive_check(const std::string& k, const std::vector<std::string>& vals) {
    const CfgSpec* sp = Config::spec(k);
    if (!sp && k != "rename-command" && k != "user" && k != "loadmodule" && k != "include" && k != "sentinel") return "Bad directive or wrong number of arguments";
    if (k == "shutdown-on-sigint" || k == "shutdown-on-sigterm") { if (vals.empty()) return "argument(s) must be one of the following: default, save, nosave, now, force"; }
    int ar = directive_arity(k);
    if (ar >= 0 && (int)vals.size() != ar) return "wrong number of arguments";
    long long n;
    if (k == "port" || k == "databases" || k == "maxclients" || k == "tcp-backlog" || k == "hz") { if (!string2ll(vals[0], n)) return "argument couldn't be parsed into an integer"; }
    if (k == "loglevel") { std::string v = lower(vals[0]); if (v != "debug" && v != "verbose" && v != "notice" && v != "warning" && v != "nothing") return "argument(s) must be one of the following: debug, verbose, notice, warning, nothing"; }
    if (k == "shutdown-on-sigint" || k == "shutdown-on-sigterm") { for (auto& t : vals) { std::string v = lower(t); if (v != "default" && v != "save" && v != "nosave" && v != "now" && v != "force") return "argument(s) must be one of the following: default, save, nosave, now, force"; } }
    if (k == "replicaof" || k == "slaveof") { if (!string2ll(vals[1], n) || n < 0 || n > 65535) return "Invalid master port"; }
    return "";
}

void Config::load_file(const std::string& path) {
    std::ifstream f(path);
    if (!f) { fprintf(stderr, "Fatal error, can't open config file '%s': %s\n", path.c_str(), strerror(errno)); exit(1); }
    std::string line; int lineno = 0;
    while (std::getline(f, line)) {
        lineno++;
        size_t s = line.find_first_not_of(" \t");
        if (s == std::string::npos || line[s] == '#') continue;
        std::vector<std::string> args;
        if (!split_args(line, args) || args.empty()) { fprintf(stderr, "\n*** FATAL CONFIG FILE ERROR ***\nUnbalanced quotes in configuration line\n"); exit(1); }
        std::string k = lower(args[0]); std::string val;
        std::vector<std::string> vals(args.begin() + 1, args.end());
        std::string err = directive_check(k, vals);
        if (!err.empty()) config_fatal("Reading the configuration file, at line " + std::to_string(lineno), repr_line(k, vals), err);
        for (size_t i = 1; i < args.size(); i++) { if (i > 1) val += ' '; val += args[i]; }
        take_directive(*this, k, val);
    }
}

void Config::apply_args(int argc, char** argv, int from) {
    // Redis joins the command line and re-splits it; an argument "--maxmemory 700mb" is two tokens.
    std::vector<std::string> tok;
    for (int i = from; i < argc; i++) { std::vector<std::string> parts; if (!split_args(argv[i], parts)) config_fatal("Reading the configuration file, at line 0", argv[i], "Unbalanced quotes in configuration line"); tok.insert(tok.end(), parts.begin(), parts.end()); }
    std::string k; std::vector<std::string> vals; bool have = false;
    auto flush = [&]() {
        if (!have) return;
        std::string err = directive_check(k, vals);
        if (!err.empty()) config_fatal("Reading the configuration file, at line " + std::to_string(vals.size() + 1), repr_line(k, vals), err);
        std::string val; for (size_t i = 0; i < vals.size(); i++) { if (i) val += ' '; val += vals[i]; }
        take_directive(*this, k, val);
        have = false; vals.clear();
    };
    for (size_t i = 0; i < tok.size(); i++) {
        const std::string& a = tok[i];
        bool is_opt = a.size() > 2 && a[0] == '-' && a[1] == '-';
        // an option that has not received a value yet takes the next token even if it looks like an option;
        // "--save" alone before another option means save ""
        if (is_opt && (!have || !vals.empty() || k == "save")) { flush(); k = lower(a.substr(2)); have = true; }
        else if (have) vals.push_back(a);
        else { k = lower(a); have = true; }        // a bare token opens a directive, as in a config file line
    }
    flush();
}

void Stats::reset() {
    commands = 0; error_replies = 0; expired_keys = 0; hits = 0; misses = 0;
    for (auto& kv : g.cmds) { kv.second.calls = 0; kv.second.usec = 0; kv.second.rejected = 0; kv.second.failed = 0; }
    std::lock_guard<std::mutex> lk(mu); errorstat.clear();
}

// ---------------------------------------------------------------- Client
bool Client::send(const std::string& bytes) {
    std::lock_guard<std::mutex> lk(wmu);
    if (closing) return false;
    size_t off = 0;
    while (off < bytes.size()) {
        ssize_t n = ::write(fd, bytes.data() + off, bytes.size() - off);
        if (n < 0) { if (errno == EINTR) continue; closing = true; return false; }
        off += (size_t)n;
    }
    return true;
}

void Client::kill() {
    closing = true;
    ::shutdown(fd, SHUT_RDWR);
    WaitWord* w = waiting_on.load();
    if (w) { w->gen.fetch_add(1, std::memory_order_release); syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w->gen), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0); }
}

// ---------------------------------------------------------------- registry / dispatch
void register_cmd(const std::string& fullname, CmdFn fn) {
    CmdEntry e; e.fn = fn; e.fullname = fullname;
    for (int i = 0; i < CMD_TABLE_N; i++) {
        const CmdSpec& s = CMD_TABLE[i];
        std::string fn2 = s.container[0] ? std::string(s.container) + "|" + s.name : std::string(s.name);
        if (fn2 == fullname) { e.spec = &s; break; }
    }
    if (!e.spec) { fprintf(stderr, "register_cmd: %s is not in the 7.2.11 command table\n", fullname.c_str()); exit(1); }
    { std::string fl = std::string(" ") + e.spec->flags + " "; e.f_write = fl.find(" write ") != std::string::npos; e.f_denyoom = fl.find(" denyoom ") != std::string::npos; e.f_noauth = fl.find(" no_auth ") != std::string::npos; }
    g.cmds[fullname] = e;
    size_t bar = fullname.find('|');
    if (bar != std::string::npos) {
        std::string cont = fullname.substr(0, bar);
        CmdEntry& ce = g.cmds[cont];
        ce.is_container = true; ce.fullname = cont;
        if (!ce.spec) for (int i = 0; i < CMD_TABLE_N; i++) if (!CMD_TABLE[i].container[0] && cont == CMD_TABLE[i].name) ce.spec = &CMD_TABLE[i];
    }
}

void register_conn_commands(); void register_server_commands(); void register_keyspace_commands(); void register_string_commands(); void register_hash_commands(); void register_set_commands(); void register_list_commands(); void register_multi_commands(); void register_sort_commands(); void register_zset_commands(); void register_geo_commands(); void register_hll_commands(); void register_stream_commands(); void register_stream_group_commands();
void register_all_commands() { register_conn_commands(); register_server_commands(); register_keyspace_commands(); register_string_commands(); register_hash_commands(); register_set_commands(); register_list_commands(); register_multi_commands(); register_sort_commands(); register_zset_commands(); register_geo_commands(); register_hll_commands(); register_stream_commands(); register_stream_group_commands(); }

void err_arity(Reply& r, const std::string& fullname) { r.error("ERR wrong number of arguments for '" + fullname + "' command"); }

const CmdEntry* find_cmd(const std::string& lname, const Argv& argv, std::string& fullname_out, bool& subcmd_missing) {
    subcmd_missing = false;
    if (lname.find('|') != std::string::npos) return nullptr;      // "config|get" typed by a client is not a command
    auto it = g.cmds.find(lname);
    if (it != g.cmds.end() && it->second.is_container) {
        if (argv.size() < 2) { fullname_out = lname; return &it->second; }   // the container itself (COMMAND) or an arity error
        std::string sub = lname + "|" + lower(argv[1]);
        auto st = g.cmds.find(sub);
        if (st == g.cmds.end()) { subcmd_missing = true; fullname_out = lname; return &it->second; }
        fullname_out = sub; return &st->second;
    }
    if (it == g.cmds.end()) return nullptr;
    fullname_out = lname; return &it->second;
}

static void count_error(const std::string& errline) {
    g.stats.error_replies++;
    std::string pre = errline.substr(1, errline.find_first_of(" \r") - 1);
    std::lock_guard<std::mutex> lk(g.stats.mu);
    g.stats.errorstat[pre]++;
}

static void dispatch_command(Client& c, const Argv& argv, Reply& r, const std::string& lname);
void dispatch_command(Client& c, const Argv& argv, Reply& r) { dispatch_command(c, argv, r, lower(argv[0])); }
void execute_command(Client& c, const Argv& argv, Reply& r) {
    std::string lname = lower(argv[0]);
    if (c.in_multi && lname != "exec" && lname != "discard" && lname != "multi" && lname != "watch" && lname != "quit" && lname != "reset") {
        // queue after the same checks a real call would make (arity, unknown, NOAUTH, OOM); a failure marks the transaction
        std::string fullname; bool submissing;
        const CmdEntry* e = find_cmd(lname, argv, fullname, submissing);
        std::string err;
        if (!e) err = "ERR unknown command '" + argv[0].substr(0, 128) + "', with args beginning with: ";
        else if (submissing) err = "ERR unknown subcommand '" + argv[1].substr(0, 128) + "'. Try " + lname + " HELP.";
        else { int ar = e->spec->arity, argc = (int)argv.size(); if (!e->fn || (ar > 0 && argc != ar) || (ar < 0 && argc < -ar)) err = "ERR wrong number of arguments for '" + fullname + "' command";
               else if (!c.authed && !e->f_noauth) err = "NOAUTH Authentication required.";
               else if (e->f_denyoom && db::oom()) err = "OOM command not allowed when used memory > 'maxmemory'."; }
        if (!err.empty()) { c.multi_err = true; r.error(err); count_error("-" + err); if (e) e->rejected++; return; }
        c.queued.push_back(argv); r.status("QUEUED"); return;
    }
    dispatch_command(c, argv, r, lname);
}

static void dispatch_command(Client& c, const Argv& argv, Reply& r, const std::string& lname) {
    ebr::Guard epoch;                       // every pointer this command reads stays valid until it returns
    static const bool trace = getenv("RIBBIT_TRACE") != nullptr;
    if (trace) { std::string line = std::to_string(c.fd) + ":"; for (auto& a : argv) { line += ' '; line += a.size() > 40 ? a.substr(0, 40) + "..." : a; } fprintf(stderr, "%s\n", line.c_str()); }
    std::string fullname; bool submissing;
    const CmdEntry* e = find_cmd(lname, argv, fullname, submissing);
    auto reject = [&](const std::string& msg) {
        r.error(msg); count_error("-" + msg);
        if (e) e->rejected++;
    };
    if (!e) {
        std::string args;
        for (size_t i = 1; i < argv.size() && i < 4; i++) { args += "'" + argv[i].substr(0, 128) + "' "; }
        reject("ERR unknown command '" + argv[0].substr(0, 128) + "', with args beginning with: " + args);
        return;
    }
    if (submissing) {
        std::string args;
        for (size_t i = 2; i < argv.size() && i < 5; i++) args += "'" + argv[i].substr(0, 128) + "' ";
        std::string up = lname; for (auto& ch : up) ch = (char)toupper((unsigned char)ch);
        reject("ERR unknown subcommand '" + argv[1].substr(0, 128) + "'. Try " + up + " HELP.");
        return;
    }
    int arity = e->spec->arity; int argc = (int)argv.size();
    if ((arity > 0 && argc != arity) || (arity < 0 && argc < -arity)) { reject("ERR wrong number of arguments for '" + fullname + "' command"); return; }
    if (lname == "debug") {
        std::string en = lower(g.cfg.get("enable-debug-command"));
        bool local = c.addr.rfind("127.", 0) == 0;
        if (en == "no" || (en == "local" && !local)) { reject("ERR DEBUG command not allowed. If the enable-debug-command option is set to \"local\", you can run it from a local connection, otherwise you need to set this option in the configuration file, and then restart the server."); return; }
    }
    if (!e->fn) { reject("ERR wrong number of arguments for '" + fullname + "' command"); return; }
    if (!c.authed && !e->f_noauth) { reject("NOAUTH Authentication required."); return; }
    if (e->f_denyoom && db::oom()) { reject("OOM command not allowed when used memory > 'maxmemory'."); return; }
    c.lastcmd = fullname;
    auto t0 = std::chrono::steady_clock::now();
    size_t before = r.out.size();
    e->fn(c, argv, r);
    auto us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count();
    g.stats.commands++;
    bool failed = r.out.size() > before && r.out[before] == '-';
    if (failed) count_error(r.out.substr(before, r.out.find("\r\n", before) - before));
    e->calls++; e->usec += (uint64_t)us; if (failed) e->failed++;
}


}  // namespace rr
