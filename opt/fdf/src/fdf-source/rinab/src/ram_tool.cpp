// ram -- [FDF_CHAT_V1] (plan item 7) Ribbit's own tuple operations, from the command line, over this machine's FNWP
// connection to the RAM manager (located by the transport, as every client):
//   ram write SERVICE VARIABLE INSTANCE BAG_JSON          -> {"ok":true,"id":N}
//   ram read  SERVICE VARIABLE [INSTANCE] [AFTER] [WAIT_S] -> {"ok":true,"rows":[...]}; with AFTER, only newer cells,
//                                                              held up to WAIT_S seconds until one exists
//   ram watch WAIT_S RAM SERVICE VARIABLE AFTER [RAM SERVICE VARIABLE AFTER ...]
//        [ONE_FOLLOW_PER_FAMILY_V1] the door's op=watch: a follow on each family (RAM "" = the one located; one session
//        per RAM), and the first rows any of them is given end it: {"ok":true,"got":[{"k":i,"rows":[...]}]}; none in
//        WAIT_S: {"ok":true,"got":[]}; a failure: {"ok":false,"k":i,"why":...} (ESTALE: another memory than the page
//        read); a RAM not reachable: held until it says it is up ("up": read it again in full), else "down": [k..].
//        Nothing is asked twice: the memory hands each follow its rows as they are written.
//   ram hold SERVICE VARIABLE INSTANCE BAG_JSON
//        [CAPABILITY_HELD_BY_ITS_SESSION_V1] written own:true on THIS session and held: the tuple is the session's, so
//        it is written {} the moment this process ends (stopped, crashed, its network gone) -- a service's capability
//        tuple goes with the service. Prints "ready" once written; held on the tuple's family, it writes the tuple again
//        if it finds it gone; exits 1 when the session ends. RINA_RAM names the RAM host.
//   ram written WAIT_S SINCE PATH [PATH ...]
//        [EVENTS_NOT_TIMERS_V1] held until one of the files is written at or after SINCE (epoch seconds), at most
//        WAIT_S: {"ok":true,"written":PATH} or {"ok":true,"written":""}. The door's op=rams waits on this: the AppHosts
//        elected again (/etc/hosts) or a RAM saying it is up (ram/<host>_<port>.up) -- an event, not a poll.
// Application addressing <App>.<QueueName>.<submitter> is (service, variable, instance).
#include "fdf_region_client.hpp"
#include <cstdio>
#include <atomic>
#include <map>
#include <memory>
#include <poll.h>
#include <chrono>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <cerrno>
#include <cstring>
#include <unistd.h>
static std::string row_json(const std::string& sv, const std::string& vv, const frogram::RawCell& c) {
    char u[40]; snprintf(u, sizeof u, "%.3f", c.updated);
    return "{\"id\":" + std::to_string(c.id) + ",\"service\":" + frogram::Json::quote(sv) + ",\"variable\":" +
           frogram::Json::quote(c.variable.empty() ? vv : c.variable) + ",\"instance\":" + frogram::Json::quote(c.instance) +
           ",\"bag\":" + (c.gone ? std::string("null,\"gone\":true") : c.text()) + ",\"updated_epoch\":" + u + "}";
}
static std::string up_name(const std::string& at) { std::string h = at.substr(0, at.rfind(':')); return h + "_" + at.substr(at.rfind(':') + 1) + ".up"; }
static int watch(int argc, char** argv) {
    if (argc < 7 || (argc - 3) % 4) { fprintf(stderr, "usage: ram watch WAIT_S RAM SERVICE VARIABLE AFTER [RAM SERVICE VARIABLE AFTER ...]\n"); return 2; }
    const double wait_s = atof(argv[2]);
    int fds[2]; if (pipe(fds) != 0) { perror("ram watch: pipe"); return 1; }
    std::atomic<std::string*> first{nullptr};            // the one answer: whichever follow is given rows first
    auto answer = [&](std::string out) {
        std::string* mine = new std::string(std::move(out)); std::string* none = nullptr;
        if (first.compare_exchange_strong(none, mine)) { char b = 1; if (write(fds[1], &b, 1) != 1) {} } else delete mine; };
    std::map<std::string, std::unique_ptr<frogram::Session>> sessions;
    std::map<std::string, std::vector<int>> down;         // [EVENTS_NOT_TIMERS_V1] a RAM not reachable now: its families
    std::vector<std::unique_ptr<frogram::Memory>> mems;
    for (int a = 3, k = 0; a < argc; a += 4, ++k) {
        std::string at = *argv[a] ? argv[a] : fdf_region::Client::locate();
        if (at == "-" || at.rfind(':') == std::string::npos) { down[at].push_back(k); continue; }   // no AppHost yet: /etc/hosts is its event
        if (down.count(at)) { down[at].push_back(k); continue; }
        auto& s = sessions[at];
        if (!s) { const size_t c = at.rfind(':');
                  try { s.reset(new frogram::Session(at.substr(0, c), atoi(at.c_str() + c + 1), fdf_region::api())); }
                  catch (const std::exception& e) { sessions.erase(at); down[at].push_back(k); continue; } }
        mems.emplace_back(new frogram::Memory(*s, fdf_region::api()));
        const std::string sv = argv[a + 1], vv = argv[a + 2];
        mems.back()->follow(sv, vv, "", atoll(argv[a + 3]), [&, k, sv, vv](std::vector<frogram::RawCell>& rows, const std::string& err) {
            if (rows.empty() && err.empty()) return;          // the follow's first answer, with nothing past AFTER yet
            if (!err.empty()) { answer("{\"ok\":false,\"k\":" + std::to_string(k) + ",\"why\":" + frogram::Json::quote(err) + "}"); return; }
            std::string out = "{\"ok\":true,\"got\":[{\"k\":" + std::to_string(k) + ",\"rows\":[";
            for (size_t i = 0; i < rows.size(); ++i) out += (i ? "," : "") + row_json(sv, vv, rows[i]);
            answer(out + "]}]}"); });
    }
    // [EVENTS_NOT_TIMERS_V1] a RAM that did not answer is not asked again on a timer: the wait is for it to say it is up
    // ($FDF_VAR/ram/<host>_<port>.up, written by that fdf-ram, or by this machine's member for the address it reached)
    // or for the AppHosts to be elected again ([APP_HOST_ELECTION_V1]: /etc/hosts, FDF_HOSTS, written): either ends this
    // watch with "up", and the page reads those families again in full.
    const char* var_env = getenv("FDF_VAR"); const std::string var = var_env && *var_env ? var_env : "/opt/fdf/var";
    const char* hosts_env = getenv("FDF_HOSTS"); const std::string hosts = hosts_env && *hosts_env ? hosts_env : "/etc/hosts";
    const std::string hosts_dir = hosts.rfind('/') == std::string::npos ? "." : hosts.substr(0, hosts.rfind('/')),
                      hosts_name = hosts.substr(hosts.rfind('/') == std::string::npos ? 0 : hosts.rfind('/') + 1);
    int in = -1;
    if (!down.empty()) {
        in = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);
        mkdir(var.c_str(), 0755); mkdir((var + "/ram").c_str(), 0755);
        const uint32_t m = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE;
        if (in < 0 || inotify_add_watch(in, (var + "/ram").c_str(), m) < 0 || inotify_add_watch(in, hosts_dir.c_str(), m) < 0) {
            perror("ram watch: inotify"); return 1; }
    }
    std::string ks; for (auto& [at, v] : down) for (int k : v) ks += (ks.empty() ? "" : ",") + std::to_string(k);
    const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(int64_t(wait_s * 1000));
    for (;;) {
        struct pollfd p[2] = {{fds[0], POLLIN, 0}, {in, POLLIN, 0}};
        const int left = int(std::max<int64_t>(0, std::chrono::duration_cast<std::chrono::milliseconds>(end - std::chrono::steady_clock::now()).count()));
        if (poll(p, in >= 0 ? 2 : 1, left) <= 0 || (p[0].revents & POLLIN)) break;   // the page's hold: the one bound
        alignas(struct inotify_event) char buf[8192]; const ssize_t n = read(in, buf, sizeof buf);
        bool up = false;
        for (ssize_t i = 0; i < n;) { auto* e = reinterpret_cast<struct inotify_event*>(buf + i); i += sizeof *e + e->len;
            const std::string nm = e->len ? e->name : "";
            if (nm == hosts_name) up = true;
            for (auto& [at, v] : down) if (at.rfind(':') != std::string::npos && (nm == up_name(at) || nm == "0.0.0.0_" + at.substr(at.rfind(':') + 1) + ".up")) up = true; }
        if (up) { std::string g; for (auto& [at, v] : down) for (int k : v) g += (g.empty() ? "" : ",") + std::string("{\"k\":") + std::to_string(k) + ",\"rows\":[],\"up\":true}";
                  answer("{\"ok\":true,\"got\":[" + g + "]}"); break; }
    }
    std::string* got = first.load();
    printf("%s\n", got ? got->c_str() : ("{\"ok\":true,\"got\":[]" + (ks.empty() ? std::string() : ",\"down\":[" + ks + "]") + "}").c_str());
    fflush(stdout);
    _exit(got && got->compare(0, 11, "{\"ok\":false") == 0 ? 1 : 0);   // the sessions' threads are not joined: the answer is out
}
static bool written_since(const std::string& p, double since) {
    struct stat st; if (stat(p.c_str(), &st) != 0) return false;
    return double(st.st_mtim.tv_sec) + st.st_mtim.tv_nsec / 1e9 >= since;
}
static int written(int argc, char** argv) {
    if (argc < 5) { fprintf(stderr, "usage: ram written WAIT_S SINCE PATH [PATH ...]\n"); return 2; }
    const double wait_s = atof(argv[2]), since = atof(argv[3]);
    std::vector<std::string> paths(argv + 4, argv + argc);
    const int in = inotify_init1(IN_CLOEXEC);
    if (in < 0) { perror("ram written: inotify"); return 1; }
    std::map<int, std::string> dirs;                      // the watch is set BEFORE the files are looked at
    for (auto& p : paths) { const size_t sl = p.rfind('/'); const std::string d = sl == std::string::npos ? "." : p.substr(0, sl);
        const int wd = inotify_add_watch(in, d.c_str(), IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE);
        if (wd < 0) { fprintf(stderr, "ram written: %s: %s\n", d.c_str(), strerror(errno)); return 1; }
        dirs[wd] = d; }
    auto said = [&](const std::string& p) { printf("{\"ok\":true,\"written\":%s}\n", frogram::Json::quote(p).c_str()); fflush(stdout); return 0; };
    for (auto& p : paths) if (written_since(p, since)) return said(p);
    const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(int64_t(wait_s * 1000));
    for (;;) {
        const int left = int(std::max<int64_t>(0, std::chrono::duration_cast<std::chrono::milliseconds>(end - std::chrono::steady_clock::now()).count()));
        struct pollfd p{in, POLLIN, 0};
        if (poll(&p, 1, left) <= 0) return said("");
        alignas(struct inotify_event) char buf[8192]; const ssize_t n = read(in, buf, sizeof buf);
        for (ssize_t i = 0; i < n;) { auto* e = reinterpret_cast<struct inotify_event*>(buf + i); i += sizeof *e + e->len;
            if (!e->len) continue;
            const std::string full = dirs[e->wd] + "/" + e->name;
            for (auto& q : paths) if (q == full && written_since(q, since)) return said(q); }
    }
}
static std::string enc(const std::string& s) { std::string o; char b[4]; for (unsigned char c : s) { if (isalnum(c) || strchr("-_.~", c)) o += char(c); else { snprintf(b, sizeof b, "%%%02X", c); o += b; } } return o; }
int main(int argc, char** argv) {
    try {
        if (argc > 1 && std::string(argv[1]) == "watch") return watch(argc, argv);
        if (argc > 1 && std::string(argv[1]) == "written") return written(argc, argv);
        const std::string where = fdf_region::Client::locate(); const size_t c = where.rfind(':');
        frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), fdf_region::api());
        const std::string cmd = argc > 1 ? argv[1] : "", api = fdf_region::api();
        if (cmd == "write" && argc == 6) {
            const std::string body = "{\"service\":" + frogram::Json::quote(argv[2]) + ",\"variable\":" + frogram::Json::quote(argv[3]) +
                                     ",\"instance\":" + frogram::Json::quote(argv[4]) + ",\"bag\":" + argv[5] + "}";
            frogram::Json r = s.call("POST", api + "?op=write", body); printf("{\"ok\":true,\"id\":%lld}\n", (long long)r["id"].n); return 0; }
        if (cmd == "hold" && argc == 6) {
            const std::string body = "{\"service\":" + frogram::Json::quote(argv[2]) + ",\"variable\":" + frogram::Json::quote(argv[3]) +
                                     ",\"instance\":" + frogram::Json::quote(argv[4]) + ",\"bag\":" + argv[5] + ",\"own\":true}";
            frogram::Memory m(s, api);
            s.call("POST", api + "?op=write", body);
            printf("ready\n"); fflush(stdout);
            try {
                int64_t at = 0; for (auto& c : m.read(argv[2], argv[3])) if (int64_t(c.id) > at) at = int64_t(c.id);
                for (;;) {
                    bool ev = false;
                    for (auto& c : m.read(argv[2], argv[3], "", at, 600)) { if (int64_t(c.id) > at) at = int64_t(c.id); ev = true; }
                    if (!ev) continue;
                    bool mine = false;
                    for (auto& c : m.read(argv[2], argv[3], argv[4])) if (c.bag.type == frogram::Json::Obj && !c.bag.o.empty()) mine = true;
                    if (!mine) { s.call("POST", api + "?op=write", body); printf("written again (it was gone)\n"); fflush(stdout); }
                }
            } catch (const std::exception& x) { fprintf(stderr, "ram hold: the session ended: %s\n", x.what()); return 1; }
        }
        if (cmd == "read" && argc >= 4 && argc <= 7) {
            std::string path = api + "?op=read&service=" + enc(argv[2]) + "&variable=" + enc(argv[3]);
            if (argc > 4 && *argv[4]) path += "&instance=" + enc(argv[4]);
            if (argc > 5) path += "&after=" + enc(argv[5]);
            if (argc > 6) path += "&wait_s=" + enc(argv[6]);
            const std::string text = s.call_text("GET", path, "", argc > 6 ? atof(argv[6]) + 5 : 30.0); printf("%s\n", text.c_str()); return 0; }
        fprintf(stderr, "usage: ram write SERVICE VARIABLE INSTANCE BAG_JSON | ram read SERVICE VARIABLE [INSTANCE] [AFTER] [WAIT_S] | ram hold SERVICE VARIABLE INSTANCE BAG_JSON | ram watch WAIT_S RAM SERVICE VARIABLE AFTER ...\n"); return 2;
    } catch (const std::exception& e) { fprintf(stderr, "ram: %s\n", e.what()); return 1; }
}
