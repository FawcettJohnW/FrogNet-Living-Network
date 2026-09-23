#include "server.hpp"

static bool auth_ok(const std::string& user, const std::string& pass) {
    return (user == "default") && (!g.requirepass.empty() && pass == g.requirepass);
}

static void cmd_ping(Client&, const Argv& a, Reply& r) {
    if (a.size() > 2) { err_arity(r, "ping"); return; }
    if (a.size() == 1) r.status("PONG"); else r.bulk(a[1]);
}
static void cmd_echo(Client&, const Argv& a, Reply& r) { r.bulk(a[1]); }
static void cmd_quit(Client&, const Argv&, Reply& r) { r.ok(); }

static void cmd_select(Client& c, const Argv& a, Reply& r) {
    long long id;
    if (!string2ll(a[1], id)) { r.error("ERR invalid DB index"); return; }
    if (id < 0 || id >= g.databases) { r.error("ERR DB index is out of range"); return; }
    c.db = (int)id; r.ok();
}

static void cmd_auth(Client& c, const Argv& a, Reply& r) {
    if (a.size() > 3) { err_syntax(r); return; }
    std::string user = a.size() == 3 ? a[1] : "default";
    std::string pass = a.size() == 3 ? a[2] : a[1];
    if (a.size() == 2 && g.requirepass.empty()) {
        r.error("ERR AUTH <password> called without any password configured for the default user. Are you sure your configuration is correct?");
        return;
    }
    if (auth_ok(user, pass)) { c.authed = true; c.user = user; r.ok(); }
    else r.error("WRONGPASS invalid username-password pair or user is disabled.");
}

static void hello_reply(Client& c, Reply& r) {
    r.map(7);
    r.bulk("server"); r.bulk("redis");
    r.bulk("version"); r.bulk("7.2.11");
    r.bulk("proto"); r.integer(c.proto);
    r.bulk("id"); r.integer((long long)c.id);
    r.bulk("mode"); r.bulk("standalone");
    r.bulk("role"); r.bulk("master");
    r.bulk("modules"); r.array(0);
}

static void cmd_hello(Client& c, const Argv& a, Reply& r) {
    int ver = c.proto;
    size_t i = 1;
    if (a.size() > 1) {
        long long v;
        if (!string2ll(a[1], v)) { r.error("ERR Protocol version is not an integer or out of range"); return; }
        if (v < 2 || v > 3) { r.error("NOPROTO unsupported protocol version"); return; }
        ver = (int)v; i = 2;
    }
    std::string user, pass, name; bool doauth = false;
    for (; i < a.size(); i++) {
        std::string opt = lower(a[i]); size_t more = a.size() - i - 1;
        if (opt == "auth" && more >= 2) { user = a[i+1]; pass = a[i+2]; doauth = true; i += 2; }
        else if (opt == "setname" && more >= 1) { name = a[i+1]; i++; }
        else { r.error("ERR Syntax error in HELLO option '" + a[i] + "'"); return; }
    }
    if (doauth) {
        if (!auth_ok(user, pass)) { r.error("WRONGPASS invalid username-password pair or user is disabled."); return; }
        c.authed = true; c.user = user;
    }
    if (!c.authed) { r.error("NOAUTH HELLO must be called with the client already authenticated, otherwise the HELLO <proto> AUTH <user> <pass> option can be used to authenticate the client and select the RESP protocol version at the same time"); return; }
    if (!name.empty()) {
        if (name.find_first_of(" \n\r\t") != std::string::npos) { r.error("ERR Client names cannot contain spaces, newlines or special characters."); return; }
        c.name = name;
    }
    c.proto = ver; r.proto = ver;
    hello_reply(c, r);
}

static void cmd_reset(Client& c, const Argv&, Reply& r) {
    c.db = 0; c.proto = 2; r.proto = 2; c.name.clear(); c.reply_mode = 0; c.skip_count = 0; c.in_multi = false; c.multi_err = false; c.queued.clear(); c.watches.clear(); c.no_evict = false; c.no_touch = false;
    if (!g.requirepass.empty()) c.authed = false;
    r.status("RESET");
}

void register_conn_commands() {
    register_cmd("ping", cmd_ping); register_cmd("echo", cmd_echo); register_cmd("quit", cmd_quit);
    register_cmd("select", cmd_select); register_cmd("auth", cmd_auth); register_cmd("hello", cmd_hello);
    register_cmd("reset", cmd_reset);
}
