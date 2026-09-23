// Redis on Ribbit -- the RESP front and the compatibility participant.
#pragma once
#include "memory.hpp"
#include "sharded.hpp"
#include "lfmap.hpp"
#include "resp.hpp"
#include "cmdtable.hpp"
#include <atomic>
#include <string>
#include <vector>
#include <map>
#include <unordered_map>
#include <mutex>
#include <memory>
#include <functional>
#include <thread>

struct Client;
using Argv = std::vector<std::string>;
using CmdFn = void (*)(Client&, const Argv&, Reply&);

struct CmdEntry {
    CmdFn fn = nullptr; const CmdSpec* spec = nullptr; std::string fullname; bool is_container = false;
    bool f_write = false, f_denyoom = false, f_noauth = false;                 // flags decoded once at registration
    mutable Sharded calls, usec, rejected, failed;   // commandstats: per-thread slots, no shared line
    CmdEntry() = default;
    CmdEntry(const CmdEntry& o) : fn(o.fn), spec(o.spec), fullname(o.fullname), is_container(o.is_container), f_write(o.f_write), f_denyoom(o.f_denyoom), f_noauth(o.f_noauth),
        calls(), usec(), rejected(), failed() { calls = o.calls.load(); usec = o.usec.load(); rejected = o.rejected.load(); failed = o.failed.load(); }
    CmdEntry& operator=(const CmdEntry& o) { fn = o.fn; spec = o.spec; fullname = o.fullname; is_container = o.is_container; f_write = o.f_write; f_denyoom = o.f_denyoom; f_noauth = o.f_noauth;
        calls = o.calls.load(); usec = o.usec.load(); rejected = o.rejected.load(); failed = o.failed.load(); return *this; }
};

struct Stats {
    Sharded commands, error_replies, expired_keys, hits, misses, dirty, connections_received, rejected_conn, evicted_keys;
    std::mutex mu;
    std::map<std::string, uint64_t> errorstat;   // by error prefix (ERR, WRONGTYPE ...); commandstats live in CmdEntry
    void reset();
};

struct Config {
    std::mutex mu;
    std::map<std::string, std::string> v;        // canonical name -> value (multi-arg joined by ' ')
    bool save_seen = false;                      // the save directive accumulates; an empty one resets
    void init_defaults();                        // the 7.2.11 table (cfgtable.hpp)
    static const struct CfgSpec* spec(const std::string& name_or_alias, std::string* canonical = nullptr);
    std::string get(const std::string& k);
    bool has(const std::string& k);
    void set(const std::string& k, const std::string& val);
    void load_file(const std::string& path);
    void apply_args(int argc, char** argv, int from);
};

struct Client {
    int fd = -1;
    size_t worker = 0;             // the event thread that owns this connection (io-mode event)
    uint64_t id = 0;
    std::string addr, laddr, name, libname, libver;
    int db = 0;
    int proto = 2;
    bool authed = true;
    std::string user = "default";
    int64_t created_ms = 0, last_cmd_ms = 0;
    std::atomic<bool> closing{false};
    std::atomic<bool> blocked{false};
    std::atomic<bool> handed_off{false};   // served by a hand-off thread; the event thread only watches for hangup
    std::atomic<WaitWord*> waiting_on{nullptr};   // the word this client is parked on, for CLIENT UNBLOCK
    struct BlockKeys { std::vector<std::string> keys; bool nokey = false; };   // nokey: XREADGROUP, unblocks when the key goes
    BlockKeys block_keys;                          // the blocking command being served (own thread only)
    std::atomic<const BlockKeys*> blocked_keys{nullptr};   // published while parked (INFO total_blocking_keys[_on_nokey])
    std::atomic<int> unblock{0};                   // 0 none, 1 timeout, 2 error
    // MULTI/EXEC/WATCH
    bool in_multi = false, multi_err = false, in_exec = false;
    std::vector<Argv> queued;
    struct Watch { int db; std::string key; uint64_t version; };
    std::vector<Watch> watches;
    int reply_mode = 0;            // 0 on, 1 off
    int skip_count = 0;            // CLIENT REPLY SKIP: replies still to swallow
    bool close_after_reply = false;
    size_t qbuf = 0;               // bytes of the command being executed (CLIENT LIST qbuf)
    bool no_evict = false, no_touch = false;
    std::string lastcmd = "";
    std::string flags = "N";
    size_t obuf_pending = 0;
    std::mutex wmu;                // serializes writes to the socket (blocking commands + pushes later)
    RespParser parser;
    bool send(const std::string& bytes);   // false when the socket is gone
    void kill();                            // CLIENT KILL: close from another thread
};

struct Server {
    Memory mem;
    Config cfg;
    Stats stats;
    int listen_fd = -1;
    int port = 6379;
    std::string bind_addr = "127.0.0.1";
    int databases = 16;
    std::atomic<uint64_t> next_client_id{1};
    // the client registry: a lock-free ordered map, id -> Client*. A client is owned by whoever serves its
    // connection (the event thread, or a hand-off thread while a command parks); it is unlinked and retired
    // through EBR on close, so a CLIENT LIST walking the map inside its epoch never sees a freed client.
    LFMap<uint64_t, Client*> clients;
    std::atomic<bool> active_expire{true};
    std::atomic<bool> shutting_down{false};
    std::atomic<int64_t> maxmemory{0};
    Sharded used_memory;                   // bytes of string payload, kept for maxmemory/INFO
    std::atomic<int64_t> max_bulk{512LL * 1024 * 1024};   // proto-max-bulk-len, read on the hot path without the config lock
    int64_t start_ms = 0;
    std::unordered_map<std::string, CmdEntry> cmds;   // "set", "client|kill", "client" (container)
    std::string requirepass;               // empty = none
    std::atomic<uint64_t> blocked_clients{0};
    std::string executable;
    std::string config_file;
};
extern Server g;

// --- logging (redis-shaped lines; the harness greps them) ---
void logmsg(char level, const std::string& msg);

// --- registry ---
void register_cmd(const std::string& fullname, CmdFn fn);   // fullname "set" or "client|kill"
void register_all_commands();
const CmdEntry* find_cmd(const std::string& lower_name, const Argv& argv, std::string& fullname_out, bool& subcmd_missing);
void execute_command(Client& c, const Argv& argv, Reply& r);
void dispatch_command(Client& c, const Argv& argv, Reply& r);   // execute_command minus the MULTI queueing

// --- reply helpers ---
inline void err_wrongtype(Reply& r) { r.error("WRONGTYPE Operation against a key holding the wrong kind of value"); }
inline void err_syntax(Reply& r) { r.error("ERR syntax error"); }
inline void err_notint(Reply& r) { r.error("ERR value is not an integer or out of range"); }
inline void err_notfloat(Reply& r) { r.error("ERR value is not a valid float"); }
void err_arity(Reply& r, const std::string& fullname);
std::string memory_value(const std::string& v);

// --- parsing helpers (util.c semantics) ---
bool string2ll(const std::string& s, long long& v);
bool string2ld(const std::string& s, long double& v);
std::string ld2string_human(long double v);
std::string ll2string(long long v);
std::string lower(std::string s);
bool str_eq_ci(const std::string& a, const char* b);
bool stringmatch(const std::string& pattern, const std::string& str, bool nocase = false);

// --- the Redis key layer over the memory (db.cpp) ---
namespace db {
    // The Redis region's own API. A Key is one Redis key resolved once for a command: region, hash, variable,
    // liveness. Every operation on it reuses that work instead of re-hashing and re-looking-up per call.
    struct Key {
        int dbi; const std::string& name; Region& R; size_t h; VarPtr v = nullptr; Kind k = Kind::None;
        Key(int dbi_, const std::string& name_);            // resolves the live variable and its kind
        CellPtr val() const;                                // the value cell (String / stream meta), or null
        uint64_t write(const std::string& instance, BagPtr bag);
        uint64_t set_string(const std::string& val, bool keepttl);
        long long push_count() const;                       // elements now (a list/set/hash), for length replies
    };
    extern const std::string VAL;      // instance of the value cell
    extern const std::string TTL;      // instance of the ttl cell
    extern const std::string ELEM;     // prefix of every container element instance ("\x02" + name): a field, a member
    // a container key: its elements are cells; the collection is the partial index over them
    Kind kind_of(const VarPtr& v);                         // None when the key does not exist
    VarPtr live_var(int dbi, const std::string& key);      // snapshot of the key honouring expiry (nullptr = absent)
    VarPtr live_var(const Memory::Snapshot& snap, int dbi, const std::string& key);   // same, from a region snapshot
    size_t elem_count(const VarPtr& v);
    std::vector<Cell> elems(const VarPtr& v, int dbi, const std::string& key);       // every element, instance order
    // write elements (kind = HashField / SetMember); returns how many were new
    size_t put_elems(int dbi, const std::string& key, Kind kind, const std::vector<std::pair<std::string, std::string>>& name_value);
    size_t put_elem(int dbi, const std::string& key, Kind kind, const std::string& name, const std::string& value);
    // remove elements; when the key becomes empty its ttl goes with it. Returns how many existed
    size_t drop_elems(int dbi, const std::string& key, const std::vector<std::string>& names);
    // replace the whole element set of a key in one publish (STORE commands): remove old, write new
    void store_elems(int dbi, const std::string& key, Kind kind, const std::vector<std::pair<std::string, std::string>>& name_value);
    const std::string& svc(int dbi);
    const std::string& claims(int dbi);   // region holding blocked participants' claim tickets for db<N>
    // read the value cell honouring expiry (readers decide; an expired cell is absent and is reaped by the reader)
    bool get(int dbi, const std::string& key, Cell& out);
    bool exists(int dbi, const std::string& key);
    std::string type(int dbi, const std::string& key);   // "none", "string", ...
    // -1 no ttl, -2 no key, else absolute unix ms
    int64_t expire_at(int dbi, const std::string& key);
    uint64_t set_string(int dbi, const std::string& key, const std::string& val, bool keepttl);
    void set_strings(int dbi, const std::vector<std::pair<std::string, std::string>>& kvs);
    void set_expire(int dbi, const std::string& key, int64_t at_ms);
    bool persist(int dbi, const std::string& key);
    bool del(int dbi, const std::string& key);           // every instance of the variable
    size_t dbsize(int dbi);
    std::vector<std::string> keys(int dbi);               // all live keys
    bool randomkey(int dbi, std::string& out);
    void flush(int dbi);
    // copy every instance of key from one db/key to another (COPY/RENAME/MOVE build on it)
    void copy_key(int sdb, const std::string& skey, int ddb, const std::string& dkey);
    size_t expired_count(int dbi);
    uint64_t version(int dbi, const std::string& key);   // newest write id under the key, 0 if absent: what WATCH compares
    int64_t used_bytes();
    bool oom();   // maxmemory in force and exceeded

}

// sorted-set primitives shared with geo (cmd_zset.cpp)
int z_var(Client& c, const std::string& key, VarPtr& v, Reply& r);
bool z_score(const VarPtr& v, const std::string& m, double& d);
std::vector<std::pair<std::string, double>> z_all(const VarPtr& v);
void z_range_scores(const VarPtr& v, double lo, double hi, const std::function<bool(const std::string&, double)>& f);
std::pair<long long, long long> z_put(int dbi, const std::string& key, const VarPtr& v, const std::vector<std::pair<std::string, double>>& ms);
void z_store(int dbi, const std::string& key, const std::vector<std::pair<std::string, double>>& ms);
