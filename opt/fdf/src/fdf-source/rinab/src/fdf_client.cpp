// fdf_client.cpp -- [FDF_WINDOWS_CLIENT_V1] (experiments/DESIGN-WINDOWS-CLIENT.md) an FD&F member as one small program,
// Windows and Linux alike: the FD&F client owns this machine's connection to the region, so applications on it just get
// and set. It does what the Linux agent does for membership, and nothing else:
//   connect   one FNWP session to the region's RAM manager (RINA_RAM=host:port, or the first argument)
//   alive     writes this machine's node record on that session, so the RAM manager counts it alive while it runs
//   announce  its name (computer name), its IPv4 addresses, its capability (cores, memory)
//   hold      a held read on its own input space; when the session ends it says why and exits
//   facts     [IPCP_FACTS_V1] (Linux) this machine's own facts about itself, a JSON object in FDF_FACTS
//             (default /opt/fdf/var/facts.json), sent with the announcement and RE-ANNOUNCED through this same session
//             whenever the file changes (inotify; no polling). The row is this session's, so a program that wants the
//             machine's facts changed (FrogNet's runMerge after a commit) writes the file; it never writes the region.
//   maps      [MAP_FROM_FACTS_V1] facts.maps = [{"eid":"10.A.B.0/24","rlocs":"hub:prio:weight,..."}]: each is a LISP
//             Map-Register through THIS session (own:true, so it goes with the session), signed with the DIF's site key
//             (RINA_SITE_KEY) under the site RINA_AREA. Changed -> re-registered; dropped from the file -> withdrawn.
#include "fdf_up.hpp"
#include "frogram.hpp"
#include "fdf_version.hpp"
#include "fdf_crash.hpp"
#include <cerrno>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <mutex>

#include <vector>
#include <chrono>
#include <cmath>
#include <ctime>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>
#include <windows.h>
#else
#include <ifaddrs.h>
#include <sys/inotify.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <climits>
#include <sys/stat.h>
#include <map>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/sysinfo.h>
#include <sys/statvfs.h>
#include <sys/wait.h>
#include <signal.h>
#include <cstring>
#include <unistd.h>
#endif

static const char* API = "/FawcettInnovations.RINA.ram_interface.php";

static std::string host_name() {
#ifdef _WIN32
    char b[256]; DWORD n = sizeof b; if (GetComputerNameA(b, &n)) return std::string(b, n); return "windows";
#else
    char b[256] = "?"; gethostname(b, sizeof b - 1); return b;
#endif
}
static std::string ipv4s() {                          // this machine's own non-loopback IPv4 addresses, as a JSON array
    std::string out = "["; bool f = true;
#ifdef _WIN32
    ULONG len = 15000; std::string buf(len, '\0');
    auto* a = reinterpret_cast<IP_ADAPTER_ADDRESSES*>(&buf[0]);
    if (GetAdaptersAddresses(AF_INET, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER, nullptr, a, &len) == NO_ERROR)
        for (; a; a = a->Next) {
            if (a->OperStatus != IfOperStatusUp || a->IfType == IF_TYPE_SOFTWARE_LOOPBACK) continue;
            for (auto* u = a->FirstUnicastAddress; u; u = u->Next) {
                char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(u->Address.lpSockaddr)->sin_addr, ip, sizeof ip);
                out += (f ? "" : ",") + frogram::Json::quote(ip); f = false; } }
#else
    ifaddrs* ia = nullptr;
    if (getifaddrs(&ia) == 0) { for (ifaddrs* a = ia; a; a = a->ifa_next) {
        if (!a->ifa_addr || a->ifa_addr->sa_family != AF_INET || (a->ifa_flags & IFF_LOOPBACK)) continue;
        char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(a->ifa_addr)->sin_addr, ip, sizeof ip);
        out += (f ? "" : ",") + frogram::Json::quote(ip); f = false; } freeifaddrs(ia); }
#endif
    return out + "]";
}
static std::string capability() {                     // what this OS reports; the same field names as FrogNet's probe
    unsigned long long mem_kb = 0;
#ifdef _WIN32
    MEMORYSTATUSEX m; m.dwLength = sizeof m; if (GlobalMemoryStatusEx(&m)) mem_kb = m.ullTotalPhys / 1024;
    const char* os = "windows";
#else
    struct sysinfo si; if (sysinfo(&si) == 0) mem_kb = (unsigned long long)si.totalram * si.mem_unit / 1024;
    const char* os = "linux";
#endif
    return "{\"cores\":" + std::to_string(std::thread::hardware_concurrency()) + ",\"mem_total_kb\":" + std::to_string(mem_kb) +
           ",\"os\":\"" + os + "\",\"client\":\"fdf-client\"}";
}

static void gen_wait(std::atomic<uint32_t>& w, uint32_t seen, int secs) {   // until w moves past seen, or secs (<0: none)
#ifdef _WIN32
    WaitOnAddress(reinterpret_cast<volatile void*>(&w), &seen, sizeof seen, secs < 0 ? INFINITE : DWORD(secs) * 1000);
#else
    timespec ts{secs, 0};
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, seen, secs < 0 ? nullptr : &ts, nullptr, 0);
#endif
}
static void gen_bump(std::atomic<uint32_t>& w) {
    w.fetch_add(1, std::memory_order_seq_cst);
#ifdef _WIN32
    WakeByAddressAll(reinterpret_cast<void*>(&w));
#else
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0);
#endif
}
#ifndef _WIN32
static std::string facts_path() { const char* f = getenv("FDF_FACTS"); return f && *f ? f : "/opt/fdf/var/facts.json"; }
static std::string file_text(const std::string& p) {
    std::ifstream in(p); if (!in) return ""; std::stringstream b; b << in.rdbuf(); std::string t = b.str();
    while (!t.empty() && (t.back() == '\n' || t.back() == ' ' || t.back() == '\r')) t.pop_back();
    return t;
}
// [FORWARD_IS_A_FACT_V1] (PLAN-PONDS §3.1) one fdf-client per pond (fdf-client@<area>); /opt/fdf/var/forward names the
// forward pond. Forward (or no forward file: a node in one pond, as before): this machine's facts.json and its mappings.
// Participant: {"role":"participant","name","guid"} -- a session, presence and the pond's shared memory, nothing routed;
// its mappings are withdrawn by sync_maps because the participant facts carry none.
static std::string forward_path() { const char* f = getenv("FDF_FORWARD_FILE"); return f && *f ? f : "/opt/fdf/var/forward"; }
static bool is_forward() {
    std::ifstream in(forward_path()); if (!in) return true;
    const char* a = getenv("RINA_AREA"); return file_text(forward_path()) == std::string(a ? a : "");
}
static std::string read_facts() {                       // the file's text, or "" (absent/empty); the region checks it is an object
    const std::string t = file_text(facts_path());
    if (is_forward() || t.empty()) return t;
    std::string name, guid;
    try { frogram::Json j = frogram::Json::parse(t); name = j["name"].s; guid = j["guid"].s; } catch (...) {}
    // [REACH_IS_A_LEAF_HERE_V1] a participant that is to REACH this network (FDF_REACH=1, from ponds/<area>.env) says so
    // with this machine's WireGuard pubkey: the region gives it a /32 in this scope and the pond's hub peers it.
    std::string pub; try { pub = frogram::Json::parse(t)["pubkey"].s; } catch (...) {}
    const char* rw = getenv("FDF_REACH"); const bool reach = rw && rw[0] == '1' && !pub.empty();
    return "{\"role\":\"participant\",\"name\":" + frogram::Json::quote(name) + ",\"guid\":" + frogram::Json::quote(guid) +
           (reach ? ",\"pubkey\":" + frogram::Json::quote(pub) + ",\"reach\":true" : std::string()) + "}";
}
// [REACH_V1] (John 2026-10-10: "a failed reach closes it. No fallbacks") fdf-reach, run on every region event of this
// pond while this machine is a participant here with FDF_REACH=1: it reads the pond's (pond, config, net), plans what
// this machine may reach, aliases what collides (only if the network's config says map allowed), and applies it on its
// own interface, table and chains. Exit 0 = applied, 5 = not yet (no /32 or the hub has not peered this key yet: the
// next event runs it again), anything else = REFUSED: the reach is taken down and this session ends with it (exit 4,
// which systemd's fdf-client@ does not restart). A reach that is not wanted (forward, or no FDF_REACH) is taken down.
static bool reach_wanted() { const char* r = getenv("FDF_REACH"); return r && r[0] == '1' && !is_forward(); }
// [NO_LOCK_IN_THE_CLIENT_V1] the reach is brought in line by ONE thread (reach_loop): every other thread only says so
// (reach_want: a new address, if any, then a bump of reach_gen) -- no mutex. reach_addr and reach_up are that thread's.
static std::string reach_addr;                           // the /32 this pond's region gave this machine (announce answer)
static std::atomic<std::string*> reach_next{nullptr};    // a new address for the reach thread to take (it frees it)
static std::atomic<uint32_t> reach_gen{0}, reach_done{0};
static std::string reach_bin() { const char* b = getenv("FDF_BIN"); return std::string(b && *b ? b : "/opt/fdf/bin") + "/fdf-reach"; }
// [REACH_V1] fdf-reach is run with an argv, never through a shell: the address comes from ANOTHER network's region
// and the area from the environment, and neither may become a command (found by review 2026-10-10: popen with quotes).
static int reach_run(const std::string& verb, const std::string& addr, std::string& out) {
    const char* a = getenv("RINA_AREA"); const std::string area = a ? a : "", bin = reach_bin();
    out.clear(); int fd[2]; if (pipe(fd) != 0) { out = "pipe failed"; return 127; }
    const pid_t p = fork();
    if (p < 0) { close(fd[0]); close(fd[1]); out = "fork failed"; return 127; }
    if (p == 0) {
        dup2(fd[1], 1); dup2(fd[1], 2); close(fd[0]); close(fd[1]);
        std::vector<char*> av = {const_cast<char*>(bin.c_str()), const_cast<char*>(verb.c_str()), const_cast<char*>(area.c_str()),
                                 const_cast<char*>(addr.c_str()), nullptr};
        execv(bin.c_str(), av.data()); _exit(127);
    }
    close(fd[1]); char b[512]; ssize_t n; while ((n = read(fd[0], b, sizeof b)) > 0) out.append(b, size_t(n)); close(fd[0]);
    int st = 0; waitpid(p, &st, 0);
    while (!out.empty() && out.back() == '\n') out.pop_back();
    return WIFEXITED(st) ? WEXITSTATUS(st) : 128;
}
static char reach_term_msg[1024], reach_bin_c[512], reach_area_c[256], reach_refused_c[512];
static bool reach_up = false;                            // the reach thread's: a reach was applied and not taken down since
static void reach_sync_now() {                          // the one place the reach is brought in line with the facts
    std::string out;
    const char* a = getenv("RINA_AREA"); const std::string area = a ? a : "";
    if (!getenv("FDF_REACH")) return;
    if (!reach_wanted() || reach_addr.empty()) {           // not wanted here (forward), or no address here now: none stands
        if (reach_up) { reach_run("down", "", out); printf("fdf-client: %s%s\n", out.empty() ? "reach taken down" : out.c_str(),
                                                          reach_wanted() ? " (the region gives this machine no address here now)" : " (this pond is forward)"); }
        else if (reach_wanted()) printf("fdf-client: reach into %s: the region has given this machine no address here yet\n", area.c_str());
        reach_up = false; fflush(stdout); return; }
    const int rc = reach_run("apply", reach_addr, out);
    if (rc == 0) { reach_up = true; if (!out.empty()) printf("fdf-client: %s\n", out.c_str()); fflush(stdout); return; }
    // 5 = not yet: fdf-reach took any reach down. 6 = not now: what was up still stands (reach_up unchanged), so a later
    // switch to forward or a lost address still takes it down (found by review 2026-10-10)
    if (rc == 5 || rc == 6) { if (rc == 5) reach_up = false; if (!out.empty()) printf("fdf-client: %s\n", out.c_str()); fflush(stdout); return; }
    if (rc != 3) { fprintf(stderr, "fdf-client: reach into %s: fdf-reach exited %d: %s -- tried again on the next event\n", area.c_str(), rc, out.c_str()); fflush(stderr); return; }
    std::string d; reach_run("down", "", d);
    fprintf(stderr, "fdf-client: REACH REFUSED into %s: %s -- a failed reach closes this session\n", area.c_str(), out.c_str()); fflush(stderr);
    if (reach_refused_c[0]) { std::ofstream o(reach_refused_c); o << out << "\n"; }     // fdf-ponds says it
    _exit(4);
}
static void reach_want(const std::string* addr) {        // a new address (or none: nullptr), then the reach thread runs
    if (addr) delete reach_next.exchange(new std::string(*addr), std::memory_order_acq_rel);
    gen_bump(reach_gen);
}
static void reach_loop() {
    uint32_t seen = 0;
    for (;;) {
        const uint32_t g = reach_gen.load(std::memory_order_seq_cst);
        if (g == seen) { gen_wait(reach_gen, seen, -1); continue; }
        seen = g;
        if (std::string* n = reach_next.exchange(nullptr, std::memory_order_acq_rel)) { reach_addr = *n; delete n; }
        reach_sync_now();
        reach_done.store(g, std::memory_order_seq_cst); gen_bump(reach_done);
    }
}
static void reach_sync() {                               // ask the reach thread to bring it in line, and wait until it has
    static std::once_flag started; std::call_once(started, [] { std::thread(reach_loop).detach(); });
    reach_want(nullptr);
    const uint32_t want = reach_gen.load(std::memory_order_seq_cst);
    for (uint32_t d; int32_t((d = reach_done.load(std::memory_order_seq_cst)) - want) < 0;) gen_wait(reach_done, d, -1);
}
// [REACH_V1] gone is gone: when this session ends the reach goes with it. SIGTERM (systemd stop, a test's kill, or
// fdf-reach itself after a re-plan refused the reach) runs `fdf-reach down` before exiting -- fork/exec/waitpid, all
// async-signal-safe, with the arguments prepared at start. If fdf-reach left <area>.refused, the exit is 4 (refused).
static void reach_on_term(int) {
    if (reach_bin_c[0]) { pid_t p = fork(); if (p == 0) { char* av[] = {reach_bin_c, (char*)"down", reach_area_c, (char*)"", nullptr}; execv(reach_bin_c, av); _exit(127); }
                          if (p > 0) { int st; waitpid(p, &st, 0); } }
    const bool refused = reach_refused_c[0] && access(reach_refused_c, F_OK) == 0;
    if (refused) { ssize_t w = write(2, reach_term_msg, strlen(reach_term_msg)); (void)w; }
    _exit(refused ? 4 : 0);
}
static void session_over(int code) {                       // [REACH_V1] every path that ends this session takes the reach down
    if (getenv("FDF_REACH")) { std::string o; reach_run("down", "", o); if (!o.empty()) fprintf(stderr, "fdf-client: %s\n", o.c_str()); }
    fflush(stderr); fflush(stdout); _exit(code);
}
static void reach_arm() {                                  // once, at start, when this client may ever reach
    if (!getenv("FDF_REACH")) return;
    const char* a = getenv("RINA_AREA"); const std::string area = a ? a : "";
    const char* v = getenv("FDF_VAR"); const std::string var = v && *v ? v : "/opt/fdf/var";
    snprintf(reach_bin_c, sizeof reach_bin_c, "%s", reach_bin().c_str()); snprintf(reach_area_c, sizeof reach_area_c, "%s", area.c_str());
    snprintf(reach_refused_c, sizeof reach_refused_c, "%s/reach/%s.refused", var.c_str(), area.c_str());
    snprintf(reach_term_msg, sizeof reach_term_msg, "fdf-client: REACH REFUSED into %s by a re-plan (%s) -- a failed reach closes this session\n", area.c_str(), reach_refused_c);
    unlink(reach_refused_c);
    mkdir((var + "/reach").c_str(), 0755);
    { std::ofstream o(var + "/reach/" + area + ".pid"); o << getpid() << "\n"; }
    struct sigaction sa{}; sa.sa_handler = reach_on_term; sigaction(SIGTERM, &sa, nullptr); sigaction(SIGINT, &sa, nullptr);
}
#else
static std::string facts_path() { const char* f = getenv("FDF_FACTS"); return f && *f ? f : "facts.json"; }   // Windows: next to the exe, or FDF_FACTS
static bool is_forward() { return true; }               // [FORWARD_IS_A_FACT_V1] a Windows member is in one pond: it is that pond's (found 2026-10-10: the Windows build had not compiled since PONDS_V1)
static std::string read_facts() {                       // read once at start (no inotify, no maps on Windows: a leaf's facts do not change)
    std::ifstream in(facts_path()); if (!in) return ""; std::stringstream b; b << in.rdbuf(); std::string t = b.str();
    while (!t.empty() && (t.back() == '\n' || t.back() == ' ' || t.back() == '\r')) t.pop_back();
    return t;
}
#endif
#ifndef _WIN32
static std::string hmac_hex(const std::string& key, const std::string& msg) {
    unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n = 0;
    HMAC(EVP_sha256(), key.data(), int(key.size()), reinterpret_cast<const unsigned char*>(msg.data()), msg.size(), md, &n);
    static const char hx[] = "0123456789abcdef"; std::string a; for (unsigned i = 0; i < n; ++i) { a += hx[md[i] >> 4]; a += hx[md[i] & 15]; } return a;
}
// [MAP_FROM_FACTS_V1] register/withdraw this machine's mappings so the region holds exactly facts.maps. `have` is what this
// session registered (eid -> rlocs). Says what it did; a refusal is said with the region's reason, never retried.
// [MAPS_ASSERTED_V1] with assert=true the region is READ first (map_list): every mapping of mine it lacks, or holds with
// other locators, is forgotten from `have` so it is registered again below. The client keeps no belief about what the
// region holds. (Seattle7 16:22: registered at :35, gone from the region by 16:24 with the session up -- the manager's
// late cleanup of the PREVIOUS session, whose id this one reused, erased it. The timer that asserted every 30 s is gone
// [EVENTS_NOT_TIMERS_V1]: on the platform a session's id is its epoch, never reused [SESSION_EPOCH_V1]; sync_maps runs
// at the session's start and on every change of the facts.)
// [ONE_OWNER_V1] this member's mappings are its own: signed with the site key (eid|rloc|ttl|ts, HMAC-SHA256, as
// map_register was) and written in its own (pond, member) cell -- the region verifies them when it is read, and writes
// nothing for them
static std::string signed_maps() {
    const char* site = getenv("RINA_AREA"); const char* key = getenv("RINA_SITE_KEY");
    const std::string facts = read_facts(); std::string out = "["; bool f = true;
    if (facts.empty()) return "[]";
    frogram::Json fj; try { fj = frogram::Json::parse(facts); } catch (...) { return "[]"; }
    if (fj["maps"].a.empty()) return "[]";
    if (!site || !*site || !key || !*key) { fprintf(stderr, "fdf-client: facts.maps present but RINA_AREA / RINA_SITE_KEY not set: no mapping said\n"); return "[]"; }
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t); char ts[40]; snprintf(ts, sizeof ts, "%lld.%09ld", (long long)t.tv_sec, t.tv_nsec);
    const std::string ttl = "86400";
    for (auto& m : fj["maps"].a) {
        const std::string eid = m["eid"].s, rloc = m["rlocs"].s; if (eid.empty()) continue;
        out += (f ? "" : ",") + std::string("{\"eid\":") + frogram::Json::quote(eid) + ",\"rloc\":" + frogram::Json::quote(rloc) + ",\"site\":" + frogram::Json::quote(site) +
               ",\"ttl\":\"" + ttl + "\",\"ts\":\"" + ts + "\",\"auth\":" + frogram::Json::quote(hmac_hex(key, eid + "|" + rloc + "|" + ttl + "|" + ts)) + "}"; f = false;
        printf("fdf-client: mapping %s -> %s said in this member's own cell (site %s)\n", eid.c_str(), rloc.c_str(), site);
    }
    fflush(stdout);
    return out + "]";
}
#endif
// [TELEMETRY_IS_A_TUPLE_V1] what this machine reports, (pond, telem, <its ns>): written on THIS session with own:true,
// so it is the session's and goes with the member ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]). Looked at every FDF_TELEM_S
// seconds (default 30; 0: nothing reported) -- CPU, memory, disk, battery and addresses have no event to wait on -- and
// written only when it moved: memory 5 %, disk 1 %, battery 1 % or plugged/unplugged, CPU 10 points, load 0.5, addresses.
// Position: none on these machines (no GPS); the phone's member reports its own.
struct Telem { double mem_total_mb = 0, mem_avail_mb = 0, disk_total_gb = 0, disk_free_gb = 0, cpu_pct = -1, load1 = -1, up_s = 0;
               int bat_pct = -1; bool charging = false; std::string os, ips; };
static std::string num(double v, int prec = 1) { char b[64]; snprintf(b, sizeof b, "%.*f", prec, v); return b; }
#ifdef _WIN32
static unsigned long long ft(const FILETIME& f) { return (static_cast<unsigned long long>(f.dwHighDateTime) << 32) | f.dwLowDateTime; }
#endif
static Telem telem_now() {
    Telem t; t.ips = ipv4s();
#ifdef _WIN32
    MEMORYSTATUSEX m; m.dwLength = sizeof m;
    if (GlobalMemoryStatusEx(&m)) { t.mem_total_mb = m.ullTotalPhys / 1048576.0; t.mem_avail_mb = m.ullAvailPhys / 1048576.0; }
    ULARGE_INTEGER fr, tot, tf; if (GetDiskFreeSpaceExA("C:\\", &fr, &tot, &tf)) { t.disk_free_gb = fr.QuadPart / 1073741824.0; t.disk_total_gb = tot.QuadPart / 1073741824.0; }
    SYSTEM_POWER_STATUS ps; if (GetSystemPowerStatus(&ps) && ps.BatteryLifePercent != 255) { t.bat_pct = ps.BatteryLifePercent; t.charging = ps.ACLineStatus == 1; }
    static unsigned long long pi = 0, pk = 0, pu = 0; FILETIME i, k, u;          // CPU busy since the last look
    if (GetSystemTimes(&i, &k, &u)) { const unsigned long long I = ft(i), K = ft(k), U = ft(u);
        if (pk + pu) { const double total = double((K - pk) + (U - pu)), idle = double(I - pi); if (total > 0) t.cpu_pct = 100.0 * (total - idle) / total; }
        pi = I; pk = K; pu = U; }
    t.up_s = GetTickCount64() / 1000.0;
    t.os = "Windows";
#else
    struct sysinfo si; if (sysinfo(&si) == 0) { t.mem_total_mb = double(si.totalram) * si.mem_unit / 1048576.0; t.up_s = si.uptime; t.load1 = si.loads[0] / 65536.0; }
    { std::ifstream mi("/proc/meminfo"); std::string k; unsigned long long v; std::string unit;
      while (mi >> k >> v >> unit) if (k == "MemAvailable:") { t.mem_avail_mb = v / 1024.0; break; } }
    struct statvfs fs; if (statvfs("/", &fs) == 0) { t.disk_free_gb = double(fs.f_bavail) * fs.f_frsize / 1073741824.0; t.disk_total_gb = double(fs.f_blocks) * fs.f_frsize / 1073741824.0; }
    for (const char* b : {"/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"}) {
        std::ifstream c(std::string(b) + "/capacity"), st(std::string(b) + "/status"); int pct; std::string s2;
        if (c >> pct) { t.bat_pct = pct; if (st >> s2) t.charging = s2 == "Charging" || s2 == "Full"; break; } }
    { std::ifstream o("/etc/os-release"); std::string l; while (std::getline(o, l)) if (l.compare(0, 12, "PRETTY_NAME=") == 0) { t.os = l.substr(12); if (t.os.size() > 1 && t.os[0] == '"') t.os = t.os.substr(1, t.os.size() - 2); } }
    if (t.os.empty()) t.os = "Linux";
#endif
    return t;
}
static std::string telem_moved(const Telem& a, const Telem* b) {
    if (!b) return "first";
    if (a.ips != b->ips) return "addresses";
    if (std::abs(a.mem_avail_mb - b->mem_avail_mb) >= 0.05 * (a.mem_total_mb > 0 ? a.mem_total_mb : 1)) return "memory";
    if (std::abs(a.disk_free_gb - b->disk_free_gb) >= 0.01 * (a.disk_total_gb > 0 ? a.disk_total_gb : 1)) return "disk";
    if (a.bat_pct != b->bat_pct || a.charging != b->charging) return "battery";
    if (a.cpu_pct >= 0 && b->cpu_pct >= 0 && std::abs(a.cpu_pct - b->cpu_pct) >= 10) return "cpu";
    if (a.load1 >= 0 && std::abs(a.load1 - b->load1) >= 0.5) return "load";
    return "";
}
// [SETTINGS_ARE_TUPLES_V1] this machine's settings are its tuple (pond, cap, <ns>), permanent: whoever writes it (a
// settings page, fdf-monitor --set, any program) this client follows it -- telem_on, telem_period_s -- with a held read on
// its own session. None yet: written once from what it has (FDF_TELEM_S, default 30), so every monitor shows it.
// [NO_LOCK_IN_THE_CLIENT_V1] the settings are atomics; a change bumps telem_gen, the word the telemetry thread sleeps
// on (a futex: no mutex, no condition variable)
static std::atomic<bool> telem_on{true}; static std::atomic<int> telem_every{30}; static std::atomic<uint32_t> telem_gen{0};

static void settings_loop(frogram::Session& s, const std::string& api, const std::string& ns, const std::string& name) {
    frogram::Memory mem(s, api); int64_t after = -1;
    for (;;) {
        std::vector<frogram::Cell> rows;
        try { rows = mem.read("pond", "cap", ns, after, after < 0 ? 0 : 600); }
        catch (const std::exception& x) { fprintf(stderr, "fdf-client: [SETTINGS_ARE_TUPLES_V1] reading (pond, cap, %s) failed: %s -- the next session follows it again\n", ns.c_str(), x.what()); return; }
        bool found = false; frogram::Json bag;
        for (auto& c : rows) { if (int64_t(c.id) > after) after = int64_t(c.id); if (c.instance == ns) { found = true; bag = c.bag; } }
        if (after < 0 && !found) {
            const bool on = telem_on.load(); const int every = telem_every.load();
            const std::string b = "{\"name\":" + frogram::Json::quote(name) + ",\"telem_on\":" + (on ? "true" : "false") + ",\"telem_period_s\":" + std::to_string(every) +
                                  ",\"at\":" + std::to_string(time(nullptr)) + ",\"by\":\"fdf-client, from what it had (FDF_TELEM_S)\"}";
            try { s.call("POST", api + "?op=write", "{\"service\":\"pond\",\"variable\":\"cap\",\"instance\":" + frogram::Json::quote(ns) + ",\"bag\":" + b + ",\"permanent\":true}");
                  printf("fdf-client: [SETTINGS_ARE_TUPLES_V1] (pond, cap, %s) did not exist: written from this machine's own: %s\n", ns.c_str(), b.c_str()); fflush(stdout); }
            catch (const std::exception& x) { fprintf(stderr, "fdf-client: [SETTINGS_ARE_TUPLES_V1] writing (pond, cap, %s) failed: %s\n", ns.c_str(), x.what()); }
            after = 0; continue;
        }
        if (after < 0) after = 0;
        if (!found || bag.size() == 0) continue;
        const bool was_on = telem_on.load(); const int was_every = telem_every.load();   // only this thread writes them
        if (bag["telem_on"].type == frogram::Json::Bool) telem_on.store(bag["telem_on"].b);
        if (bag["telem_period_s"].type == frogram::Json::Num && bag["telem_period_s"].n >= 1) telem_every.store(int(bag["telem_period_s"].n));
        if (telem_on.load() != was_on || telem_every.load() != was_every) {
            printf("fdf-client: [SETTINGS_ARE_TUPLES_V1] (pond, cap, %s): telemetry %s, every %d s (was %s, %d s)\n", ns.c_str(), telem_on.load() ? "on" : "off", telem_every.load(), was_on ? "on" : "off", was_every);
            fflush(stdout); gen_bump(telem_gen);
        }
    }
}

static void telemetry_loop(frogram::Session& s, const std::string& api, const std::string& ns, const std::string& name) {
    { const char* e = getenv("FDF_TELEM_S");
      if (e) { telem_every.store(atoi(e) > 0 ? atoi(e) : 30); telem_on.store(atoi(e) > 0); } }
    std::thread([&s, api, ns, name] { settings_loop(s, api, ns, name); }).detach();
#ifdef _WIN32
    telem_now();                                                                 // the first CPU sample: the next one has a delta
#endif
    printf("fdf-client: [TELEMETRY_IS_A_TUPLE_V1] reporting (pond, telem, %s) on this session, written when it moved; what and how often: (pond, cap, %s)\n", ns.c_str(), ns.c_str()); fflush(stdout);
    Telem last; bool have = false, reported = false; unsigned long long writes = 0; uint32_t gen = 0;
    for (;;) {
        const uint32_t g = telem_gen.load(std::memory_order_seq_cst); if (gen != g) { gen = g; have = false; }
        const bool on = telem_on.load(); const int every = telem_every.load();
        if (!on) {                                                                   // off: taken back now, not at session end
            if (reported) try { s.call("POST", api + "?op=write", "{\"service\":\"pond\",\"variable\":\"telem\",\"instance\":" + frogram::Json::quote(ns) + ",\"bag\":{},\"own\":true}");
                                printf("fdf-client: telemetry off: (pond, telem, %s) emptied\n", ns.c_str()); fflush(stdout); reported = false; }
                           catch (const std::exception& x) { fprintf(stderr, "fdf-client: telemetry off, emptying (pond, telem) failed: %s (it goes with the session)\n", x.what()); }
            gen_wait(telem_gen, gen, -1); continue;
        }
        Telem t = telem_now();
        const std::string why = telem_moved(t, have ? &last : nullptr);
        if (!why.empty()) {
            std::string bag = "{\"name\":" + frogram::Json::quote(name) +
#ifdef _WIN32
                ",\"kind\":\"windows\"" +
#else
                ",\"kind\":\"linux\"" +
#endif
                ",\"build\":" + frogram::Json::quote(FDF_BUILD_TAG) + ",\"model\":" + frogram::Json::quote(host_name()) + ",\"os\":" + frogram::Json::quote(t.os) +
                ",\"cores\":" + std::to_string(std::thread::hardware_concurrency()) +
                ",\"mem\":{\"total_mb\":" + num(t.mem_total_mb, 0) + ",\"avail_mb\":" + num(t.mem_avail_mb, 0) + "}" +
                ",\"disk\":{\"total_gb\":" + num(t.disk_total_gb) + ",\"free_gb\":" + num(t.disk_free_gb) + "}" +
                (t.bat_pct >= 0 ? ",\"battery\":{\"pct\":" + std::to_string(t.bat_pct) + ",\"charging\":" + (t.charging ? "true" : "false") + "}" : std::string()) +
                (t.cpu_pct >= 0 ? ",\"cpu_pct\":" + num(t.cpu_pct) : std::string()) + (t.load1 >= 0 ? ",\"load1\":" + num(t.load1, 2) : std::string()) +
                ",\"net\":{\"ips\":" + t.ips + "},\"up_s\":" + num(t.up_s, 0) + ",\"at\":" + num(double(time(nullptr)), 0) + "}";
            try {
                s.call("POST", api + "?op=write", "{\"service\":\"pond\",\"variable\":\"telem\",\"instance\":" + frogram::Json::quote(ns) +
                       ",\"bag\":" + bag + ",\"own\":true}");
                last = t; have = true; reported = true; ++writes;
                if (writes <= 2 || writes % 100 == 0) { printf("fdf-client: telemetry written (#%llu, %s): %s\n", writes, why.c_str(), bag.c_str()); fflush(stdout); }
            } catch (const std::exception& x) { fprintf(stderr, "fdf-client: telemetry write failed (%s): %s -- written again when it next moves\n", why.c_str(), x.what()); }
        }
        gen_wait(telem_gen, gen, every);                                             // the period, or at once when the settings change
    }
}

// [MEMBER_IS_A_TUPLE_V1] (John 2026-11-03: "no notifications ... not like a messaging thing") this member's own truth,
// (pond, member, <its ns>) = {ns, name, ips, cap, facts}, written on THIS session with own:true: it is there while the
// member is (it goes with the session), and whoever wants the members holds a read on the family -- no event pushed to
// a watcher, no relay (fdf-events) turning the region's notices into lines.
static void write_member(frogram::Session& s, const std::string& api, const std::string& ns, const std::string& name, const std::string& facts,
                         const std::string& addr = "") {
    std::string f = "null";
    if (!facts.empty()) { try { frogram::Json fj = frogram::Json::parse(facts); f = facts;
        // [LEAF_BY_INVITE_V1] a leaf's address is its edge: said in its own facts (the readers place it by facts.addr)
        if (!addr.empty() && fj["addr"].type == frogram::Json::Null) { const size_t b = f.find_first_not_of(" \t\r\n", 1);
            f = "{\"addr\":" + frogram::Json::quote(addr) + (b != std::string::npos && f[b] == '}' ? "" : ",") + f.substr(1); } } catch (...) {} }
#ifdef _WIN32
    const std::string maps = "[]";
#else
    const std::string maps = signed_maps();
#endif
    s.call("POST", api + "?op=write", "{\"service\":\"pond\",\"variable\":\"member\",\"instance\":" + frogram::Json::quote(ns) + ",\"bag\":{\"ns\":" +
           frogram::Json::quote(ns) + ",\"name\":" + frogram::Json::quote(name) + ",\"ips\":" + ipv4s() + ",\"cap\":" + frogram::Json::quote(capability()) +
           ",\"facts\":" + f + ",\"maps\":" + maps + ",\"build\":\"" FDF_BUILD_TAG "\"},\"own\":true}");
}
static frogram::Json announce(frogram::Session& s, const std::string& api, const std::string& ns, const std::string& name) {
    const std::string facts = read_facts();
    frogram::Json a = s.call("POST", api + "?op=rina", "{\"op\":\"announce\",\"ns\":" + frogram::Json::quote(ns) + ",\"name\":" + frogram::Json::quote(name) +
                  ",\"ips\":" + ipv4s() + ",\"cap\":" + frogram::Json::quote(capability()) + (facts.empty() ? "" : ",\"facts\":" + frogram::Json::quote(facts)) + "}");
    return a;
}

int main(int argc, char** argv) {
    fdf_crash::install("fdf-client");                                             // [FDF_CRASH_HANDLER_V1] self-report a fatal signal
    const char* e = getenv("RINA_RAM"); const std::string where = argc > 1 ? argv[1] : (e ? e : "");
    const size_t c = where.rfind(':');
    if (where.empty() || c == std::string::npos) { fprintf(stderr, "usage: fdf-client <ram-server>:<port>   (or set RINA_RAM)\n"); return 2; }
#ifndef _WIN32
    reach_arm();                                                                  // [REACH_V1]
    // [PONDS_ARE_LISTED_V1] the pond's RAM manager, without its key, for whatever shows this machine's networks
    // (getSnapshot.php, the dashboard, the monitor): ponds/<area>.env is 0600 (it holds the site key); this file is not
    if (const char* a = getenv("RINA_AREA"); a && *a) {
        const char* v = getenv("FDF_VAR"); const std::string dir = std::string(v && *v ? v : "/opt/fdf/var") + "/ponds";
        struct stat st; if (stat(dir.c_str(), &st) == 0 && S_ISDIR(st.st_mode)) {
            const std::string f = dir + "/" + a + ".ram", t = f + ".tmp";
            { std::ofstream o(t); o << where << "\n"; } chmod(t.c_str(), 0644); rename(t.c_str(), f.c_str()); } }
#endif
    // [MEMBER_IDENTITY_V1] every FrogNet node's hostname is "FrogNetHost" (and every container's may be the same), so the
    // hostname cannot be the member's identity: the name is FDF_NAME, else facts.name, else the hostname; the namespace
    // (the record key) is facts.guid when there is one, else the name. The announcement's `name` is what tools show.
    std::string name = host_name(), ident;
    if (const char* e = getenv("FDF_NAME"); e && *e) name = e;
    if (const std::string f = read_facts(); !f.empty()) {
        try {
            frogram::Json j = frogram::Json::parse(f);
            if (!getenv("FDF_NAME") && !j["name"].s.empty()) name = j["name"].s;
            ident = j["guid"].s;
        } catch (...) {}
    }
    const std::string ns = "w:" + (ident.empty() ? name : ident), api = API;
    try {
        frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), api);
        auto rina = [&](const std::string& body) { return s.call("POST", api + "?op=rina", body); };
        frogram::Json r = rina("{\"op\":\"node\",\"ns\":" + frogram::Json::quote(ns) + ",\"devs\":\"\"}");                 // alive: this session
        // [JOIN_REFUSED_WAITS_V1] a refused announcement (EEXIST: my served /24 is a live member's, PLAN-RAM-DISCOVERY §4)
        // is said at ERROR with the holder's name, and this process WAITS for the region to change (a member left or
        // re-announced) before trying again -- not exit 1 and a systemd restart every 2 s against the RAM manager.
        std::string announced, given;                           // [FACTS_BASELINE_IS_WHAT_WAS_ANNOUNCED_V1]
        for (unsigned long long jat = 0;;) {
            const std::string facts = read_facts(); announced = facts;
            const std::string body = s.call_text("POST", api + "?op=rina", "{\"op\":\"announce\",\"ns\":" + frogram::Json::quote(ns) + ",\"name\":" + frogram::Json::quote(name) +
                  ",\"ips\":" + ipv4s() + ",\"cap\":" + frogram::Json::quote(capability()) + (facts.empty() ? "" : ",\"facts\":" + frogram::Json::quote(facts)) + "}");
            frogram::Json a; try { a = frogram::Json::parse(body); } catch (...) { fprintf(stderr, "fdf-client: unreadable answer to the announcement: %s\n", body.substr(0, 200).c_str()); return 1; }
            if (a["ok"].b) {   // [LEAF_BY_INVITE_V1] a leaf's /32 is its invitation's edge, said back by the region's check
                given = a["addr"].s;
                if (!a["addr"].s.empty()) {
#ifndef _WIN32
                    if (reach_wanted()) { const std::string ad = a["addr"].s; reach_want(&ad);
                        printf("fdf-client: the region gave this machine %s here, to reach this network [REACH_V1]\n", a["addr"].s.c_str()); fflush(stdout); }
                    else
#endif
                    { printf("fdf-client: the region gave this leaf %s (its WireGuard Address)\n", a["addr"].s.c_str()); fflush(stdout); } }
                break; }
            if (a["error"].s != "EEXIST") { fprintf(stderr, "fdf-client: the announcement was refused: %s %s\n", a["error"].s.c_str(), a["why"].s.c_str()); return 1; }
            fprintf(stderr, "fdf-client: JOIN REFUSED by the region: %s. Not a member until that changes; waiting for the members to change (a held read, no retry loop).\n", a["why"].s.c_str());
            // [MEMBER_IS_A_TUPLE_V1] the region changing is the members' family changing: (pond, member), held
            frogram::Memory mm(s, api);
            if (jat == 0) for (auto& c : mm.read("pond", "member")) if (c.id > jat) jat = c.id;
            for (auto& c : mm.read("pond", "member", "", (int64_t)jat, 600.0)) if (c.id > jat) jat = c.id;
            fprintf(stderr, "fdf-client: the members changed; trying the announcement again\n");
        }
        { frogram::Json v = rina("{\"op\":\"version\"}");
          printf("fdf-client: build %s; region at %s is build %s\n", FDF_BUILD_TAG, where.c_str(), v["ok"].b ? v["server"].s.c_str() : "(no version op: older than fdf-2026-10-09-r2)"); }
        printf("fdf-client: %s joined the region at %s, announced %s%s%s\n", name.c_str(), where.c_str(), ipv4s().c_str(),
               read_facts().empty() ? "" : " with facts", is_forward() ? "" : " as a PARTICIPANT (not the forward pond)"); fflush(stdout);
        fdf_say_up(where, "fdf-client " FDF_BUILD_TAG);                               // [EVENTS_NOT_TIMERS_V1] the region's RAM is there again
        std::thread([&s, api, ns, name] { telemetry_loop(s, api, ns, name); }).detach();     // [TELEMETRY_IS_A_TUPLE_V1]
        write_member(s, api, ns, name, announced, given);   // [MEMBER_IS_A_TUPLE_V1][ONE_OWNER_V1] its announcement and its mappings, its own
#ifndef _WIN32
        // [IPCP_FACTS_V1] re-announce when the facts file changes. Watch the DIRECTORY (editors and `mv` replace the file,
        // which would orphan a watch on the inode); react to our file's name only. Held in the kernel; nothing polls.
        std::thread([&s, api, ns, name, announced] {
            const std::string path = facts_path(), dir = path.substr(0, path.rfind('/')), base = path.substr(path.rfind('/') + 1);
            // [WATCH_FAILURE_SAYS_SO_V1] without this watch the node stays in the region and never re-announces a changed
            // fact: a member that looks alive and is wrong. Say so, with the reason and the limit to raise.
            int ifd = inotify_init1(IN_CLOEXEC);
            if (ifd < 0) { fprintf(stderr, "fdf-client: FACTS WATCH FAILED: inotify_init1: %s%s -- %s's facts will NOT be re-announced when they change\n",
                                   strerror(errno), errno == EMFILE ? " (fs.inotify.max_user_instances reached)" : "", name.c_str()); fflush(stderr); return; }
            if (inotify_add_watch(ifd, dir.empty() ? "." : dir.c_str(), IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE) < 0) {
                fprintf(stderr, "fdf-client: FACTS WATCH FAILED: inotify_add_watch %s: %s%s -- %s's facts will NOT be re-announced when they change\n",
                        dir.c_str(), strerror(errno), errno == ENOSPC ? " (fs.inotify.max_user_watches reached)" : "", name.c_str()); fflush(stderr); close(ifd); return; }
            { const std::string fp = forward_path(), fdir = fp.substr(0, fp.rfind('/'));          // [FORWARD_IS_A_FACT_V1]
              if (fdir != dir) inotify_add_watch(ifd, fdir.empty() ? "." : fdir.c_str(), IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE); }
            // [FACTS_BASELINE_IS_WHAT_WAS_ANNOUNCED_V1] the baseline is the text the join announced, not the file re-read
            // here: a write landing between the announcement and this watch was in the file when it was re-read, so every
            // later event compared equal and the region kept the join's facts for good (netns-scale-test, S=200 E=10:
            // child c95 announced its starting file, fdf-facts wrote its three rows ~ms later, never re-announced). And
            // the first pass runs before any event: that same write raised none this watch could see.
            char buf[4096]; std::string last = announced; int was_fwd = is_forward() ? 1 : 0;   // [FORWARD_IS_A_FACT_V1]
            for (bool first = true;; first = false) {
                if (!first) {
                    ssize_t n = read(ifd, buf, sizeof buf); if (n <= 0) return;
                    bool mine = false;
                    const std::string fwd = forward_path().substr(forward_path().rfind('/') + 1);   // [FORWARD_IS_A_FACT_V1]
                    for (ssize_t off = 0; off < n;) { auto* e = reinterpret_cast<inotify_event*>(buf + off); if (e->len && (base == e->name || fwd == e->name)) mine = true; off += sizeof(inotify_event) + e->len; }
                    if (!mine) continue; }
                const std::string now = read_facts(); if (now == last) continue; last = now;
                { const int f = is_forward() ? 1 : 0;                                              // [FORWARD_IS_A_FACT_V1]
                  if (was_fwd != f) { printf("fdf-client: %s is now %s\n", getenv("RINA_AREA") ? getenv("RINA_AREA") : "?", f ? "FORWARD: announcing this machine's facts and mappings" : "a PARTICIPANT: presence and shared memory, nothing routed"); fflush(stdout); }
                  was_fwd = f; }
                try { frogram::Json a = announce(s, api, ns, name);
                      if (a["ok"].b) printf("fdf-client: facts changed, re-announced%s\n", a["addr"].s.empty() ? "" : (" (leaf " + a["addr"].s + ")").c_str()); else fprintf(stderr, "fdf-client: re-announcement refused: %s %s\n", a["error"].s.c_str(), a["why"].s.c_str());
                      { const std::string ad = reach_wanted() ? a["addr"].s : std::string(); reach_want(&ad); }   // [REACH_V1]
                      fflush(stdout); write_member(s, api, ns, name, now, a["addr"].s); reach_sync(); }
                catch (const std::exception& x) { fprintf(stderr, "fdf-client: re-announce failed: %s -- ending this session so a fresh one re-announces and re-registers\n", x.what()); session_over(1); }
            }
        }).detach();
        // [ATTENTION_IS_A_TUPLE_V1] (PLAN-PONDS §3.4-5) a notice in this pond is the tuple (pond, attention, <writer ns>);
        // a held read on this same session sees each one the moment it is written. Said here when this pond is not the
        // forward one (there you already are), and kept in /opt/fdf/var/attention/<area>.json for whatever shows it.
        // A failed read ends the session, as a failed assert does: the fresh one reads again.
        std::thread([&s, api, ns] {
            frogram::Memory mem(s, api); int64_t after = -1;
            const char* ae = getenv("RINA_AREA"); const std::string area = ae ? ae : "";
            const char* de = getenv("FDF_ATTENTION_DIR"); const std::string dir = de && *de ? de : "/opt/fdf/var/attention";
            for (;;) {
                std::vector<frogram::Cell> rows;
                try { rows = mem.read("pond", "attention", "", after, after < 0 ? 0 : 600); }
                catch (const std::exception& x) { fprintf(stderr, "fdf-client: attention read failed: %s -- ending this session for a fresh one\n", x.what()); session_over(1); }
                for (auto& c : rows) {
                    if (int64_t(c.id) > after) after = int64_t(c.id);
                    if (c.instance == ns || is_forward()) continue;
                    const std::string from = c.bag["from"].s.empty() ? c.instance : c.bag["from"].s;
                    printf("fdf-client: ATTENTION %s: %s (from %s)\n", area.c_str(), c.bag["why"].s.c_str(), from.c_str()); fflush(stdout);
                    mkdir(dir.c_str(), 0755);
                    const std::string f = dir + "/" + area + ".json", t = f + ".tmp";
                    { std::ofstream o(t); o << "{\"area\":" << frogram::Json::quote(area) << ",\"why\":" << frogram::Json::quote(c.bag["why"].s)
                                            << ",\"from\":" << frogram::Json::quote(from) << ",\"id\":" << c.id << ",\"ts\":" << std::to_string(c.updated) << "}\n"; }
                    rename(t.c_str(), f.c_str());
                }
                if (after < 0) after = 0;
            }
        }).detach();
        // [EVENTS_NOT_TIMERS_V1] (was [MAPS_ASSERTED_V1]: every 30 s the region was read and this member's mappings
        // registered again if it lacked them -- a timer standing in for a defect.) A mapping goes with the session that
        // registered it ([OWNED_MAPPING_IS_NOT_RESTORED_V1]); this session registers them at its start and again on every
        // change of its facts.
#endif
#ifndef _WIN32
        reach_sync();                                                            // [REACH_V1] after the join
#endif
        // [MEMBER_IS_A_TUPLE_V1] hold: the session IS this machine's membership, and the region changing is the members'
        // family changing -- one held read on (pond, member) (was: a wait on this machine's input space, where the region
        // pushed a notice to every agent on every change)
        { frogram::Memory held(s, api); int64_t at = 0;
          for (auto& c : held.read("pond", "member")) if (int64_t(c.id) > at) at = int64_t(c.id);
          for (;;) {
            for (auto& c : held.read("pond", "member", "", at, 600)) if (int64_t(c.id) > at) at = int64_t(c.id);
#ifndef _WIN32
            reach_sync();                                                        // [REACH_V1] every region event
#endif
          } }
    } catch (const std::exception& x) {
        fprintf(stderr, "fdf-client: the session to %s ended: %s\n", where.c_str(), x.what());
#ifndef _WIN32
        if (getenv("FDF_REACH")) { const std::string none; reach_want(&none); reach_sync(); }   // gone is gone: the reach thread takes it down
#endif
        return 1; }
}
