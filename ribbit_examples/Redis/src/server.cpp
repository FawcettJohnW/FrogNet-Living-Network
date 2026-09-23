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

Server g;
static std::mutex log_mu;
static FILE* log_fp = stdout;

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

// ---------------------------------------------------------------- connection thread
// ---------------------------------------------------------------- the front
// Two fronts, same execution. "event": one epoll thread reads, parses, executes and replies inline, the
// pipeline a client already sent is drained in one pass and answered in one write. A command that can
// park (blocking pop, XREAD BLOCK, DEBUG SLEEP, WAIT) is handed, with the rest of its pipeline, to a thread
// of its own; when that command is answered the connection returns to the loop. "thread": one OS thread
// per connection, the original front, kept for comparison. Neither touches the memory's semantics.
static bool is_blocking_class(const Argv& a) {
    const char* n = a[0].c_str(); char f = (char)tolower((unsigned char)n[0]);
    if (f != 'b' && f != 'w' && f != 'd' && f != 'x') return false;
    static const char* names[] = {"blpop", "brpop", "blmove", "brpoplpush", "blmpop", "bzpopmin", "bzpopmax", "bzmpop", "wait", "debug", nullptr};
    for (int i = 0; names[i]; i++) if (!strcasecmp(n, names[i])) return true;
    if (!strcasecmp(n, "xread") || !strcasecmp(n, "xreadgroup")) { for (size_t i = 1; i < a.size(); i++) if (!strcasecmp(a[i].c_str(), "block")) return true; }
    return false;
}
static void close_client(Client& c, const char* why = "") {
    static const bool trace = getenv("RIBBIT_TRACE") != nullptr;
    if (trace) fprintf(stderr, "[DIAG-CLOSE] id=%llu fd=%d why=%s closing=%d handed_off=%d last=%s\n", (unsigned long long)c.id, c.fd, why, (int)c.closing.load(), (int)c.handed_off.load(), c.lastcmd.c_str());
    ::close(c.fd);
    Client* out = nullptr;
    if (g.clients.remove(c.id, &out) && out) ebr::retire(out);      // freed once no command's epoch can still see it
}
// execute cmds[from..) for a client; replies go out in one write. Returns false when the connection is done.
// When may_block is false and a blocking-class command is met, execution stops there and *stopped_at is set.
static bool run_cmds(Client& c, std::vector<Argv>& cmds, size_t from, bool may_block, size_t* stopped_at) {
    std::string batch; bool done = false;
    if (stopped_at) *stopped_at = cmds.size();
    for (size_t i = from; i < cmds.size(); i++) {
        auto& argv = cmds[i];
        if (c.closing || done) break;
        if (!may_block && !c.in_multi && is_blocking_class(argv)) { if (stopped_at) *stopped_at = i; break; }
        Reply r; r.proto = c.proto;
        c.last_cmd_ms = Memory::now_ms();
        { auto digits = [](size_t v) { size_t d = 1; while (v >= 10) { v /= 10; d++; } return d; };
          size_t q = 3 + digits(argv.size()); for (auto& a : argv) q += 5 + digits(a.size()) + a.size(); c.qbuf = q; }
        execute_command(c, argv, r);
        c.parser.authenticated = c.authed;
        bool suppress = false;
        if (c.reply_mode == 1) suppress = true;
        else if (c.skip_count > 0) { suppress = true; c.skip_count--; }
        if (!suppress && !r.out.empty()) {
            if (!c.authed) { c.obuf_pending += r.out.size(); if (c.obuf_pending > 1024 * 1024) { done = true; break; } }
            batch += r.out;
        }
        if (c.close_after_reply || !strcasecmp(argv[0].c_str(), "quit")) done = true;
    }
    if (!batch.empty() && !c.send(batch)) return false;
    return !done && !c.closing;
}
// one read's worth of input; returns false when the connection is done
static bool handle_input(Client& c, const char* buf, size_t n, bool may_block, std::vector<Argv>* leftover, size_t* stopped_at) {
    c.parser.feed(buf, n);
    c.parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
    ParseResult pr = c.parser.drain();
    if (!run_cmds(c, pr.cmds, 0, may_block, stopped_at)) return false;
    if (stopped_at && *stopped_at < pr.cmds.size()) { *leftover = std::move(pr.cmds); return true; }
    if (pr.fatal) {
        Reply r; r.error("ERR " + pr.err); c.send(r.out); count_error("-ERR " + pr.err);
        return false;
    }
    return true;
}

// -------- thread-per-connection front
static void serve_client(Client* cp) {
    Client& c = *cp;
    char rbuf[65536];
    c.parser.authenticated = c.authed;
    while (!c.closing) {
        ssize_t n = ::read(c.fd, rbuf, sizeof rbuf);
        if (n <= 0) { if (n < 0 && errno == EINTR) continue; break; }
        if (!handle_input(c, rbuf, (size_t)n, true, nullptr, nullptr)) break;
    }
    close_client(c);
}

static Client* accept_one() {
    sockaddr_in sa; socklen_t sl = sizeof sa;
    int fd = ::accept(g.listen_fd, (sockaddr*)&sa, &sl);
    if (fd < 0) return nullptr;
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    Client* c = new Client();
    c->fd = fd; c->id = g.next_client_id++;
    char ip[64]; inet_ntop(AF_INET, &sa.sin_addr, ip, sizeof ip);
    c->addr = std::string(ip) + ":" + std::to_string(ntohs(sa.sin_port));
    sockaddr_in la; socklen_t ll = sizeof la; getsockname(fd, (sockaddr*)&la, &ll);
    inet_ntop(AF_INET, &la.sin_addr, ip, sizeof ip);
    c->laddr = std::string(ip) + ":" + std::to_string(ntohs(la.sin_port));
    c->created_ms = c->last_cmd_ms = Memory::now_ms();
    c->authed = g.requirepass.empty();
    c->parser.authenticated = c->authed;
    g.stats.connections_received++;
    if (lower(g.cfg.get("protected-mode")) == "yes" && g.requirepass.empty() && g.bind_addr == "*" && c->addr.rfind("127.", 0) != 0) {
        const char* msg = "-DENIED Redis is running in protected mode because protected mode is enabled and no password is set for the default user. In this mode connections are only accepted from the loopback interface.\r\n";
        if (::write(fd, msg, strlen(msg)) < 0) {} ::close(fd); delete c; return nullptr;
    }
    g.clients.add(c->id, c);
    return c;
}

static void accept_loop() {
    while (!g.shutting_down) {
        auto c = accept_one();
        if (!c) { if (errno == EINTR) continue; if (g.shutting_down) break; continue; }
        std::thread(serve_client, c).detach();
    }
}

// -------- event front
// N event threads (io-threads), each with its own epoll set; a new connection is assigned round-robin at
// accept and belongs to that thread for its life (or to a hand-off thread while a command parks). There is
// no shared queue: the partition is the dispatch. On one core N=1 is the fair comparison with Redis; on
// real cores N>1 asks what independent participants do with independent CPUs.
static std::vector<int> epfds;
static std::atomic<size_t> next_worker{0};
static void ep_add(int epfd, int fd, void* ptr) { epoll_event ev{}; ev.events = EPOLLIN; ev.data.ptr = ptr; epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev); }
static void ep_del(int epfd, int fd) { epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr); }
// while a connection is handed off, its event thread keeps watching it for hangup only: a peer that closes
// is noticed at once, and the parked participant is woken through its own wait word instead of waiting for
// its next slice. Redis frees a closed client the instant it sees EOF; a blocked-client count that lags
// behind by a slice is a count a test can race.
static void ep_watch_hangup(int epfd, int fd, void* ptr) { epoll_event ev{}; ev.events = EPOLLRDHUP; ev.data.ptr = ptr; epoll_ctl(epfd, EPOLL_CTL_MOD, fd, &ev); }
static void ep_watch_input(int epfd, int fd, void* ptr) { epoll_event ev{}; ev.events = EPOLLIN; ev.data.ptr = ptr; epoll_ctl(epfd, EPOLL_CTL_MOD, fd, &ev); }
static void ep_disarm(int epfd, int fd, void* ptr) { epoll_event ev{}; ev.events = 0; ev.data.ptr = ptr; epoll_ctl(epfd, EPOLL_CTL_MOD, fd, &ev); }
static void wake_parked(Client& c) {
    WaitWord* w = c.waiting_on.load(std::memory_order_acquire);
    if (!w) return;
    w->gen.fetch_add(1, std::memory_order_release);
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w->gen), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0);
}
struct Handoff { Client* c; std::vector<Argv> cmds; size_t from; };
// a blocking-class command and the pipeline behind it run on their own thread; then the connection comes back
// Only the event thread ever closes a connection. A hand-off thread that finds the connection finished
// marks it closing and hands it back; the event thread closes it on the next event. (Closing here raced
// the event thread's current batch: a stale event for a just-closed fd, reused by a new client, closed the
// new client. That was the intermittent stall.)
static void serve_handoff(Handoff h) {
    Client& c = *h.c;
    bool alive = run_cmds(c, h.cmds, h.from, true, nullptr);
    if (!alive) c.closing = true;
    c.handed_off.store(false, std::memory_order_release);
    ep_watch_input(epfds[c.worker], c.fd, h.c);          // back to the loop, which closes it if it is closing
}
static void event_loop(size_t w) {
    int epfd = epfds[w];
    std::vector<epoll_event> evs(1024);
    char rbuf[65536];
    while (!g.shutting_down) {
        int n = epoll_wait(epfd, evs.data(), (int)evs.size(), 1000);
        if (n < 0) { if (errno == EINTR) continue; break; }
        for (int i = 0; i < n; i++) {
            if (evs[i].data.ptr == nullptr) {                          // the listener (worker 0 only)
                auto c = accept_one();
                if (c) { c->worker = next_worker.fetch_add(1) % epfds.size(); ep_add(epfds[c->worker], c->fd, c); }
                continue;
            }
            Client* c = (Client*)evs[i].data.ptr;          // owned by this loop until handed off or closed
            if (c->handed_off.load(std::memory_order_acquire)) {          // hangup on a parked connection
                if (evs[i].events & (EPOLLRDHUP | EPOLLHUP | EPOLLERR)) {
                    static const bool trace = getenv("RIBBIT_TRACE") != nullptr; if (trace) fprintf(stderr, "[DIAG-HUP] id=%llu events=%x\n", (unsigned long long)c->id, (unsigned)evs[i].events);
                    c->closing = true; wake_parked(*c);
                    ep_disarm(epfd, c->fd, c);                                 // once is enough: the hand-off thread returns it
                }
                continue;                                                  // the hand-off thread owns it
            }
            if (c->closing.load(std::memory_order_acquire)) { ep_del(epfd, c->fd); close_client(*c, "deferred"); continue; }
            ssize_t r = ::read(c->fd, rbuf, sizeof rbuf);
            if (r <= 0) { if (r < 0 && (errno == EINTR || errno == EAGAIN)) continue; int e = errno; ep_del(epfd, c->fd); close_client(*c, r == 0 ? "eof" : strerror(e)); continue; }
            std::vector<Argv> leftover; size_t stopped = 0;
            if (!handle_input(*c, rbuf, (size_t)r, false, &leftover, &stopped)) { ep_del(epfd, c->fd); close_client(*c, "input-done"); continue; }
            if (!leftover.empty()) {                                    // a command that may park: off the loop it goes
                c->handed_off.store(true, std::memory_order_release);
                ep_watch_hangup(epfd, c->fd, c);
                std::thread(serve_handoff, Handoff{c, std::move(leftover), stopped}).detach();
            }
        }
    }
}
static void event_front() {
    int nthreads = atoi(g.cfg.get("io-threads").c_str()); if (nthreads < 1) nthreads = 1; if (nthreads > 128) nthreads = 128;
    for (int i = 0; i < nthreads; i++) epfds.push_back(epoll_create1(0));
    ep_add(epfds[0], g.listen_fd, nullptr);
    std::vector<std::thread> ts;
    for (int i = 1; i < nthreads; i++) ts.emplace_back(event_loop, (size_t)i);
    event_loop(0);
    for (auto& t : ts) t.join();
}

static void on_sigterm(int) { g.shutting_down = true; ::shutdown(g.listen_fd, SHUT_RDWR); ::close(g.listen_fd); }

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    g.executable = argv[0];
    g.start_ms = Memory::now_ms();
    int from = 1;
    if (argc > 1 && argv[1][0] != '-') { g.config_file = argv[1]; from = 2; }
    g.cfg.init_defaults();
    if (!g.config_file.empty()) g.cfg.load_file(g.config_file);
    g.cfg.apply_args(argc, argv, from);
    g.port = atoi(g.cfg.get("port").c_str());
    {   // bind: "* -::*" (the default) or a list; we listen on the first IPv4 address, or on every interface for "*"
        std::string b = g.cfg.get("bind"); std::istringstream is(b); std::string t; g.bind_addr = "127.0.0.1";
        while (is >> t) { if (!t.empty() && t[0] == '-') t = t.substr(1); if (t == "*" || t == "0.0.0.0") { g.bind_addr = "*"; break; } sockaddr_in tmp; if (inet_pton(AF_INET, t.c_str(), &tmp.sin_addr) == 1) { g.bind_addr = t; break; } }
        if (b.empty()) g.bind_addr = "*";
    }
    g.databases = atoi(g.cfg.get("databases").c_str()); if (g.databases <= 0) g.databases = 16;
    g.requirepass = g.cfg.get("requirepass");
    g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    std::string lf = g.cfg.get("logfile");
    if (!lf.empty()) { FILE* f = fopen(lf.c_str(), "a"); if (f) log_fp = f; }

    { std::vector<std::string> svcs; for (int d = 0; d < g.databases; d++) { svcs.push_back(db::svc(d)); svcs.push_back(db::claims(d)); } svcs.push_back("batches"); g.mem.init(svcs); }
    register_all_commands();

    logmsg('*', "Redis Ribbit Memory V3, Redis compatibility 7.2.11, PID: " + std::to_string(getpid()));
    logmsg('*', "Running mode=standalone, port=" + std::to_string(g.port) + ".");
    logmsg('#', "Server initialized");

    g.listen_fd = ::socket(AF_INET, SOCK_STREAM, 0);
    int one = 1; setsockopt(g.listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa{}; sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)g.port);
    if (g.bind_addr == "*" || g.bind_addr == "0.0.0.0") sa.sin_addr.s_addr = htonl(INADDR_ANY);
    else if (inet_pton(AF_INET, g.bind_addr.c_str(), &sa.sin_addr) != 1) sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (::bind(g.listen_fd, (sockaddr*)&sa, sizeof sa) < 0 || ::listen(g.listen_fd, 511) < 0) {
        logmsg('#', "Warning: Could not create server TCP listening socket " + g.bind_addr + ":" + std::to_string(g.port) + ": " + strerror(errno));
        logmsg('#', "Failed listening on port " + std::to_string(g.port) + " (tcp), aborting.");
        return 1;
    }
    signal(SIGTERM, on_sigterm); signal(SIGINT, on_sigterm);
    logmsg('*', "Ready to accept connections tcp");
    if (lower(g.cfg.get("io-mode")) == "thread") accept_loop(); else event_front();
    logmsg('#', "User requested shutdown...");
    logmsg('#', "Redis on Ribbit is now ready to exit, bye bye...");
    fflush(log_fp);
    _exit(0);
}
