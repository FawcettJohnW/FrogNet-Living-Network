// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogbench -- "Can this machine FrogNet? If so, how well?" One executable.
//
//   frogbench                       bring up a RAM target on loopback, qualify + characterise THIS machine
//   frogbench --peer HOST[:PORT]    also run against a peer's frogbench (repeatable): an ephemeral test mesh
//   frogbench --demo WORD           share ONE FrogNet memory on the public broker with anyone else who runs
//                                   the same word: frogbench asks the broker for a server keyed by WORD,
//                                   waits for a second person, and both qualify against that one shared memory
//   frogbench --serve [--port N]    just be a RAM target other frogbenches point at (Ctrl-C to stop)
//   frogbench --seconds N --json D  per-test length; write machine-readable results into directory D
//
// The demo needs no FrogNet, no firewall changes, no ports pasted by hand: two
// strangers on opposite sides of the world run  frogbench --demo turtle  and
// share one memory across the real Internet. The broker and its password are
// built in below; override with --broker URL and --pass WORD if you host your own.
//
// Self-contained: the RAM server, the clients, the workloads, the qualification
// suite and the profiler are all in this one process. It links the same
// frogram + ram_server + mesh/flood logic used everywhere else, so what it
// measures is the real contract, not a mock. Nothing is installed.
//
// A run has two halves. QUALIFICATION answers "does the shared-memory contract
// hold here?": distinct-state (every sequence seen once, in order, none missing),
// latest-value (replacement, ordering, the final value), blocking reads (held,
// woken, expiring), removal. It PASSES or FAILS. CHARACTERISATION answers "how
// well?": the paced ladders the operating envelope is read from.
//
// The per-test result JSON is exactly what tools/mesh and tools/flood write, so
// bench_envelope.py, bench_to_sim.py, bench_report.py and lilypad_plan.py all
// consume a frogbench directory unchanged.
#define FROGBENCH_EMBED 1
#ifdef _WIN32
#  ifndef _WIN32_WINNT
#    define _WIN32_WINNT 0x0600
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  include <direct.h>
#  define FR_CLOSE(fd) closesocket(fd)
#else
#  include <arpa/inet.h>
#  include <netdb.h>
#  include <netinet/in.h>
#  include <sys/socket.h>
#  include <sys/utsname.h>
#  include <unistd.h>
#  define FR_CLOSE(fd) ::close(fd)
#endif

#include "../cpp/frogram.hpp"
#include "../server_cpp/ram_server.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <thread>
#include <random>
#include <set>
#include <vector>
using namespace frogram;

// The public demo broker and password are compiled in so the stranger types one word.
static const char* BROKER_URL_DEFAULT = "http://fawcettinnovations.com:8790/ram_rendezvous.php";
static const char* BROKER_PASS_DEFAULT = "FrogNetD3mo!";

// Minimal HTTP/1.0 GET, standard library only: enough to call the rendezvous.
// Resolve a hostname to an IPv4 literal ONCE, with retries, so the rest of a run never
// re-resolves. A single flaky DNS answer mid-benchmark otherwise poisons a rung; caching the
// address makes the name irrelevant after the first success. A literal IP passes straight through.
static std::string resolve_ipv4(const std::string& host, int tries, std::string& err) {
    for (int attempt = 0; attempt < tries; ++attempt) {
        addrinfo hints{}, *res = nullptr; hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
        int rc = getaddrinfo(host.c_str(), nullptr, &hints, &res);
        if (rc == 0 && res) {
            char ip[64] = {0};
            inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(res->ai_addr)->sin_addr, ip, sizeof ip);
            freeaddrinfo(res);
            if (ip[0]) return ip;
        }
        if (res) freeaddrinfo(res);
        err = "cannot resolve " + host;
        if (attempt + 1 < tries) std::this_thread::sleep_for(std::chrono::milliseconds(400));
    }
    return "";
}

static std::string http_get(const std::string& url, std::string& err) {
    std::string u = url; std::string scheme = "http://";
    if (u.compare(0, scheme.size(), scheme)) { err = "broker URL must be http://"; return ""; }
    u = u.substr(scheme.size());
    std::string host = u.substr(0, u.find('/')); std::string path = u.substr(host.size()); if (path.empty()) path = "/";
    int port = 80; size_t c = host.find(':'); if (c != std::string::npos) { port = atoi(host.c_str() + c + 1); host = host.substr(0, c); }
    addrinfo hints{}, *res = nullptr; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res) || !res) { err = "cannot resolve " + host; return ""; }
#ifdef _WIN32
    { static bool once = [] { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); return true; }(); (void)once; }
#endif
    int fd = -1; for (addrinfo* a = res; a; a = a->ai_next) { fd = int(::socket(a->ai_family, a->ai_socktype, a->ai_protocol)); if (fd < 0) continue; if (!::connect(fd, a->ai_addr, int(a->ai_addrlen))) break; FR_CLOSE(fd); fd = -1; }
    freeaddrinfo(res); if (fd < 0) { err = "cannot reach the broker at " + host; return ""; }
    std::string req = "GET " + path + " HTTP/1.0\r\nHost: " + host + "\r\nConnection: close\r\n\r\n";
    if (::send(fd, req.data(), int(req.size()), 0) < 0) { FR_CLOSE(fd); err = "broker send failed"; return ""; }
    std::string resp; char buf[4096]; int n; while ((n = ::recv(fd, buf, sizeof buf, 0)) > 0) resp.append(buf, size_t(n)); FR_CLOSE(fd);
    size_t hdr = resp.find("\r\n\r\n"); if (hdr == std::string::npos) { err = "no HTTP body from broker"; return ""; }
    return resp.substr(hdr + 4);
}
static std::string url_encode(const std::string& s) {
    static const char* hex = "0123456789ABCDEF"; std::string o;
    for (unsigned char ch : s) { if (isalnum(ch) || ch=='-'||ch=='_'||ch=='.'||ch=='~') o += char(ch); else { o += '%'; o += hex[ch>>4]; o += hex[ch&15]; } }
    return o;
}
static std::string cpu_model_str() {
#ifdef _WIN32
    const char* v = getenv("PROCESSOR_IDENTIFIER"); return v ? std::string(v) : std::string("unknown");
#else
    std::ifstream f("/proc/cpuinfo"); std::string l; while (std::getline(f, l)) if (!l.compare(0, 10, "model name")) return l.substr(l.find(':') + 2); return "unknown";
#endif
}
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static std::string jq(const std::string& s) { return Json::quote(s); }

struct Target { std::string label, host; int port; };

// ---- one distinct-state contention run (the qualification core) -------------
struct RunOut { long writes=0, reads=0, dropped=0, ooo=0, dup=0, gaps=0, bad_last=0; double secs=0, drain=0, p50=0, p99=0; bool pass=false; };
static double pctl(std::vector<double>& v, double p){ if(v.empty())return 0; size_t k=size_t(p*(v.size()-1)); std::nth_element(v.begin(),v.begin()+k,v.end()); return v[k]; }

static RunOut mesh_run(const Target& t, int N, double seconds, double rate_per_pair, bool every, bool ack) {
    RunOut r; std::string tag=std::to_string(long(wall()*1000)%100000);
    std::vector<std::string> nm(N); for(int i=0;i<N;i++) nm[i]="fb"+tag+"_"+std::to_string(i);
    std::vector<std::vector<long>> sent(N,std::vector<long>(N,0)),got(N,std::vector<long>(N,0)),last(N,std::vector<long>(N,-1));
    std::vector<long> ooo(N,0),dup(N,0),gp(N,0),rd(N,0); std::vector<std::vector<double>> lat(N);
    std::atomic<bool> done(false); std::atomic<int> failed(0);
    std::vector<std::unique_ptr<Session>> ws(N),rs(N);
    try{ for(int i=0;i<N;i++){ ws[i].reset(new Session(t.host,t.port)); rs[i].reset(new Session(t.host,t.port)); } }
    catch(const std::exception& e){ fprintf(stderr,"  connect failed: %s\n",e.what()); r.pass=false; return r; }
    std::vector<std::thread> th;
    for(int j=0;j<N;j++) th.emplace_back([&,j]{ try{ Memory m(*rs[j]); int64_t after=0; std::vector<long> prev(N,-1); int idle=0;
        for(;;){ auto rows=m.read("ChatServer",nm[j],"",after,1.0); ++rd[j]; double tn=wall();
            for(auto& c:rows){ after=std::max<int64_t>(after,int64_t(c.id)); size_t u=c.instance.rfind('_'); int i=atoi(c.instance.c_str()+u+1); long n=long(c.bag["n"].n); lat[j].push_back(tn-c.bag["t"].n);
                if(n==prev[i])++dup[j]; else if(n<prev[i])++ooo[j]; else { if(n!=prev[i]+1)gp[j]+=n-prev[i]-1; prev[i]=n; ++got[i][j]; last[i][j]=n; }
                if(every) rs[j]->post("DELETE","/ram.php?op=remove&id="+std::to_string(c.id)); }
            if(done){ bool all=true; for(int i=0;i<N;i++) if(i!=j&&last[i][j]!=sent[i][j]-1) all=false; idle=rows.empty()?idle+1:0; if(all||idle>=3)break; } } }
        catch(const std::exception& e){ (void)e; ++failed; } });
    std::this_thread::sleep_for(std::chrono::milliseconds(300)); double t0=now();
    std::vector<std::thread> wt;
    for(int i=0;i<N;i++) wt.emplace_back([&,i]{ try{ Memory m(*ws[i]); long k=0; double s0=now(); char buf[96];
        auto put=[&](int j){ snprintf(buf,sizeof buf,"{\"n\":%ld,\"t\":%.6f}",sent[i][j],wall()); std::string inst=every?nm[i]+":"+std::to_string(sent[i][j]):nm[i];
            if(ack) m.write("ChatServer",nm[j],inst,buf); else m.write_nowait("ChatServer",nm[j],inst,buf); ++sent[i][j]; };
        while(now()-s0<seconds){ for(int j=0;j<N;j++) if(j!=i){ put(j); ++k; }
            if(rate_per_pair>0){ double due=s0+(double(k)/(N-1))/rate_per_pair,left=due-now(); if(left>0) std::this_thread::sleep_for(std::chrono::duration<double>(left)); } }
        for(int j=0;j<N;j++) if(j!=i) put(j); }
        catch(const std::exception& e){ (void)e; ++failed; } });
    for (auto& x : wt) { x.join(); }
    r.secs = now() - t0; done = true;
    for (auto& x : th) { x.join(); }
    r.drain = now() - t0 - r.secs;
    long G=0,S=0; std::vector<double> L;
    for(int i=0;i<N;i++) for(int j=0;j<N;j++) if(i!=j){ S+=sent[i][j]; G+=got[i][j]; if(last[i][j]!=sent[i][j]-1)++r.bad_last; }
    for(int j=0;j<N;j++){ r.reads+=rd[j]; r.ooo+=ooo[j]; r.dup+=dup[j]; r.gaps+=gp[j]; L.insert(L.end(),lat[j].begin(),lat[j].end()); }
    r.writes=S; r.dropped=S-G; r.p50=pctl(L,.5)*1e3; r.p99=pctl(L,.99)*1e3;
    r.pass = !failed && !r.ooo && !r.dup && !r.bad_last && (!every || (r.gaps==0 && r.dropped==0));
    return r;
}

struct FloodOut { double ack_p50=0, secs=0; long writes=0; };
static FloodOut flood_pair(const Target& t, double secs, long payload) {
    FloodOut r; try { Session ws(t.host,t.port), rs(t.host,t.port); Memory wm(ws), rm(rs); std::string rx="fx"+std::to_string(long(wall()*1000)%100000);
        std::atomic<bool> done(false); std::thread reader([&]{ int64_t a=0; while(!done){ auto rows=rm.read("ChatServer",rx,"",a,1.0); for(auto&c:rows) a=std::max<int64_t>(a,int64_t(c.id)); } });
        std::string pad(payload>0?size_t(payload):0,'x'); std::vector<double> A; double t0=now(); long i=0;
        while(now()-t0<secs){ char tb[48]; snprintf(tb,sizeof tb,"%.6f",wall()); std::string bag="{\"n\":"+std::to_string(i)+",\"t\":"+tb+(payload>0?",\"pad\":\""+pad+"\"":"")+"}";
            double a=now(); wm.write("ChatServer",rx,"tx",bag); A.push_back((now()-a)*1e3); ++i; }
        r.secs=now()-t0; r.writes=i; done=true; reader.join(); std::sort(A.begin(),A.end()); r.ack_p50=A.empty()?0:A[A.size()/2]; } catch(const std::exception&){}
    return r;
}
static void write_flood(const std::string& dir, const std::string& id, const std::string& label, long payload, const FloodOut& r) {
    if (dir.empty()) { return; }
    std::ofstream f(dir + "/runs/" + id + ".json"); if (!f) { return; }
    std::string kind = id.size() > 4 ? id.substr(4) : id;
    f << "{\"tool\":\"flood\",\"_id\":" << jq(id) << ",\"label\":" << jq(kind) << ",\"host_label\":" << jq(label) << ",\"mode\":\"latest\",\"ack\":true,\"rate\":0,\"payload_bytes\":" << payload << ",\"seconds\":" << r.secs
      << ",\"writes\":" << r.writes << ",\"writes_per_s\":" << (r.secs?r.writes/r.secs:0) << ",\"reads\":0,\"reads_per_s\":0,\"skipped\":0,\"out_of_order\":0,\"delivered_twice\":0,"
      << "\"ended_on_last\":true,\"delivery_ms\":{\"p50\":0,\"p99\":0},\"ack_ms\":{\"min\":0,\"p50\":" << r.ack_p50 << ",\"p99\":" << r.ack_p50 << "},\"pass\":true}\n";
}
static void write_json(const std::string& dir, const std::string& id, const std::string& label, const char* mode, bool ack, int N, double rate, const RunOut& r) {
    if (dir.empty()) { return; }
    std::ofstream f(dir + "/runs/" + id + ".json"); if (!f) { return; }
    std::string kind = id.size() > 4 ? id.substr(4) : id;
    f << "{\"tool\":\"mesh\",\"_id\":" << jq(id) << ",\"label\":" << jq(kind) << ",\"host_label\":" << jq(label) << ",\"mode\":\"" << mode << "\",\"ack\":" << (ack?"true":"false") << ",\"rate_per_pair\":" << rate << ",\"rate\":" << rate
      << ",\"clients\":" << N << ",\"pairs\":" << N*(N-1) << ",\"started_epoch\":" << (wall()-r.secs-r.drain) << ",\"seconds\":" << r.secs << ",\"drain_seconds\":" << r.drain
      << ",\"writes\":" << r.writes << ",\"writes_per_s\":" << (r.secs?r.writes/r.secs:0) << ",\"reads\":" << r.reads << ",\"reads_per_s\":" << (r.secs?r.reads/r.secs:0)
      << ",\"cells_per_read\":" << (r.reads?double(r.writes-r.dropped)/r.reads:0) << ",\"dropped\":" << r.dropped << ",\"skipped\":" << r.dropped << ",\"out_of_order\":" << r.ooo
      << ",\"delivered_twice\":" << r.dup << ",\"gaps\":" << r.gaps << ",\"pairs_not_ending_on_last\":" << r.bad_last << ",\"refused\":0,\"delivery_ms\":{\"p50\":" << r.p50 << ",\"p99\":" << r.p99
      << ",\"max\":" << r.p99 << "},\"ack_ms\":{\"p50\":0,\"p99\":0},\"pass\":" << (r.pass?"true":"false") << "}\n";
}

struct Headline { double ack_wps = 0, ack_p99 = 0; long ack_dropped = 0; };
static bool qualify(const Target& t, double secs, const std::string& dir, int& idc, Headline* hl = nullptr) {
    printf("\nQUALIFICATION  --  %s\n", t.label.c_str()); bool all = true; char id[64];
    struct Case { const char* name; int N; const char* mode; bool ack; };
    Case cases[] = {{"distinct_state_ack", 6, "every", true}, {"distinct_state_noack", 6, "every", false}, {"latest_value", 6, "latest", false}, {"latest_value_ack", 4, "latest", true}};
    for (auto& c : cases) {
        RunOut r = mesh_run(t, c.N, secs, 0, !strcmp(c.mode, "every"), c.ack);
        snprintf(id, sizeof id, "%03d_qual_%s", ++idc, c.name);
        write_json(dir, id, t.label, c.mode, c.ack, c.N, 0, r);
        bool contract = r.pass && (strcmp(c.mode, "every") || r.dropped == 0);
        if (hl && !strcmp(c.name, "distinct_state_ack")) { hl->ack_wps = r.secs ? r.writes / r.secs : 0; hl->ack_p99 = r.p99; hl->ack_dropped = r.dropped; }
        all &= contract;
        printf("  %-7s %-22s %ld writes, dropped %ld, out-of-order %ld, twice %ld, ends-on-last %s   (delivery p99 %.1f ms)\n",
               contract ? "PASS" : "FAIL", c.name, r.writes, r.dropped, r.ooo, r.dup, r.bad_last ? "NO" : "yes", r.p99);
    }
    // blocking read held then woken, and removal -- straight against the memory
    try {
        Session s(t.host, t.port); Memory m(s); std::string k = "fbq" + std::to_string(long(wall()) % 100000);
        std::atomic<bool> got(false); double t0 = now(), woke = 0;
        std::thread rr([&]{ auto rows = m.read("qual", k, "", 0, 5.0); if (!rows.empty()) { got = true; woke = now(); } });
        std::this_thread::sleep_for(std::chrono::milliseconds(400)); bool held = !got;
        Session s2(t.host, t.port); Memory m2(s2); uint64_t id2 = m2.write("qual", k, "w", "{\"n\":1}"); rr.join();
        bool blk = held && got && woke - t0 > 0.35;
        printf("  %-7s blocking read          asked before the write, still held at 0.4s: %s; woke on the write: %s\n", blk ? "PASS" : "FAIL", held ? "yes" : "NO", got ? "yes" : "NO");
        m.remove(id2); bool gone = m.read("qual", k).empty();
        printf("  %-7s removal                the cell is gone after remove: %s\n", gone ? "PASS" : "FAIL", gone ? "yes" : "NO");
        all &= blk && gone;
    } catch (const std::exception& e) { printf("  FAIL    blocking/removal    %s\n", e.what()); all = false; }
    printf("  ==> this machine %s FrogNet: the shared-memory contract %s.\n", all ? "CAN" : "CANNOT", all ? "held under every case above" : "FAILED at least one case");
    return all;
}

static void characterise(const Target& t, double secs, const std::string& dir, int& idc) {
    printf("\nCHARACTERISATION  --  %s  (the paced ladders the envelope is read from)\n", t.label.c_str()); char id[64];
    printf("  distinct state, paced (every value kept):\n");
    for (double r : {50.0, 200.0, 800.0, 3200.0}) { RunOut o = mesh_run(t, 10, secs, r, true, false);
        snprintf(id, sizeof id, "%03d_contention_paced_%d", ++idc, int(r)); write_json(dir, id, t.label, "every", false, 10, r, o);
        printf("    %6.0f/s per pair -> %7.0f writes/s, dropped %ld, p99 %.1f ms\n", r, o.secs?o.writes/o.secs:0, o.dropped, o.p99); }
    RunOut nA = mesh_run(t, 10, secs, 0, true, true); snprintf(id, sizeof id, "%03d_contention_ack", ++idc); write_json(dir, id, t.label, "every", true, 10, 0, nA);
    RunOut nN = mesh_run(t, 10, secs, 0, true, false); snprintf(id, sizeof id, "%03d_contention_noack", ++idc); write_json(dir, id, t.label, "every", false, 10, 0, nN);
    printf("  distinct state, flat out: acknowledged %.0f writes/s (0 dropped), ceiling %.0f writes/s\n", nA.secs?nA.writes/nA.secs:0, nN.secs?nN.writes/nN.secs:0);
    printf("  latest value, one sender paced (skipping allowed): ");
    for (double r : {50.0, 500.0, 5000.0}) { RunOut o = mesh_run(t, 2, secs, r, false, false);
        snprintf(id, sizeof id, "%03d_one_pair_paced_%d", ++idc, int(r)); write_json(dir, id, t.label, "latest", false, 2, r, o);
        printf("%.0f/s->%.1f%% skipped  ", r, o.writes?100.0*o.dropped/o.writes:0); }
    printf("\n  latest value, 10 clients paced (freshness): ");
    for (double r : {50.0, 200.0, 400.0}) { RunOut o = mesh_run(t, 10, secs, r, false, false);
        snprintf(id, sizeof id, "%03d_latest_paced_%d", ++idc, int(r)); write_json(dir, id, t.label, "latest", false, 10, r, o);
        printf("%.0f/s/pair p99 %.1f ms  ", r, o.p99); }
    printf("\n  small vs 64KiB acknowledged round trip (payload threshold): ");
    { FloodOut a = flood_pair(t, secs, 0), b = flood_pair(t, secs, 65536);
      snprintf(id, sizeof id, "%03d_rtt_small", ++idc); write_flood(dir, id, t.label, 0, a);
      snprintf(id, sizeof id, "%03d_rtt_64k", ++idc); write_flood(dir, id, t.label, 65536, b);
      printf("small %.2f ms, 64KiB %.2f ms\n", a.ack_p50, b.ack_p50); }
}

static void env_json(const std::string& dir, const Target& t) {
    if (dir.empty()) { return; }
    std::ofstream f(dir + "/env.json");
    std::string model = "unknown", kernel = "?", machine = "?";
#ifdef _WIN32
    { char* v = getenv("PROCESSOR_IDENTIFIER"); if (v) model = v; machine = "windows"; kernel = "windows"; }
#else
    { utsname u{}; uname(&u); kernel = u.release; machine = u.machine;
      std::ifstream ci("/proc/cpuinfo"); std::string l; while (std::getline(ci, l)) if (!l.compare(0, 10, "model name")) { model = l.substr(l.find(':') + 2); break; } }
#endif
    char host[256] = "?"; gethostname(host, sizeof host); time_t now_t = time(nullptr); char ts[32]; strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", gmtime(&now_t));
    f << "{\"started_utc\":\"" << ts << "\",\"client_host\":" << jq(host) << ",\"target\":{\"host\":" << jq(t.host) << ",\"port\":" << t.port << "},\"cpu_model\":" << jq(model)
      << ",\"cores\":" << std::thread::hardware_concurrency() << ",\"kernel\":\"" << kernel << "\",\"machine\":\"" << machine << "\",\"label\":" << jq(t.label) << "}\n";
    std::ofstream pf(dir + "/path.json"); pf << "{\"tcp_connect_ms\":[],\"note\":\"frogbench local run; use run_bench.sh for full path characterisation\"}\n";
}

// Is a target reachable right now, and how long does one connect take?  (When.)
static bool peer_online(const Target& t, double& connect_ms) {
    double t0 = now();
    try { Session s(t.host, t.port); connect_ms = (now() - t0) * 1000.0; return true; }
    catch (const std::exception&) { connect_ms = (now() - t0) * 1000.0; return false; }
}

// Exchange a SPECIFIC message with a peer through the shared memory, and confirm the exact
// bytes came back. This is the "we are really talking to THAT peer" check, not a throughput
// number: write a nonce to peers.echo.<me>, have the peer's frogbench... -- but a peer may be
// a plain RAM target with no frogbench logic, so we verify the memory round-trips OUR own
// distinct message: write it, read it back by its own address, compare byte for byte.
static bool peer_exchange(const Target& t, std::string& detail) {
    try {
        Session s(t.host, t.port); Memory m(s);
        std::string me = "fb" + std::to_string(long(wall() * 1e6) % 1000000000);
        std::string msg = "hello-from-" + me + "-\xf0\x9f\x90\xb8";     // includes non-ASCII so a lossy path shows
        m.write("peers", "echo", me, "{\"msg\":" + Json::quote(msg) + ",\"t\":" + std::to_string(wall()) + "}");
        auto rows = m.read("peers", "echo", me);
        if (rows.size() != 1) { detail = "wrote 1, read " + std::to_string(rows.size()); return false; }
        bool same = rows[0].bag["msg"].s == msg; detail = same ? "exact bytes returned" : "bytes differed";
        double rt = (wall() - rows[0].bag["t"].n) * 1000.0; if (same) detail += " (round trip " + std::to_string(int(rt)) + " ms)";
        m.remove(rows[0].id);
        return same;
    } catch (const std::exception& e) { detail = e.what(); return false; }
}

// Maximum sustainable rate to a target: climb paced distinct-state rungs until delivery p99
// crosses a budget or a value is dropped, and report the last rung that stayed clean. This is
// the field version of the envelope's distinct-state limit, run against a specific peer.
static void peer_max_rate(const Target& t, double secs, double p99_budget_ms) {
    printf("  max-rate to %s (distinct state, climb until p99 > %.0f ms or a drop):\n", t.label.c_str(), p99_budget_ms);
    double clean = 0; long clean_w = 0;
    for (double r : {50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0}) {
        RunOut o = mesh_run(t, 6, secs, r, true, true);
        bool ok = o.pass && o.dropped == 0 && o.p99 <= p99_budget_ms;
        printf("    %6.0f/s/pair -> %7.0f writes/s, dropped %ld, p99 %6.1f ms  %s\n", r, o.secs ? o.writes / o.secs : 0, o.dropped, o.p99, ok ? "ok" : "over budget");
        if (ok) { clean = r; clean_w = long(o.secs ? o.writes / o.secs : 0); } else break;
    }
    if (clean > 0) printf("    ==> sustained: up to ~%.0f writes/s/pair (~%ld writes/s aggregate) within %.0f ms p99 to this peer.\n", clean, clean_w, p99_budget_ms);
    else printf("    ==> even the lowest rung was over budget to this peer: the path or the peer is the limit, not the rate.\n");
}

int main(int argc, char** argv) {
    std::vector<Target> peers; double secs = 3; std::string dir; int port = 8788; bool serve_only = false;
    std::string demo, broker = BROKER_URL_DEFAULT, pass = BROKER_PASS_DEFAULT;
    int demo_n = 2;                       // wait for this many participants (including me) before running
    bool do_max_rate = false; double p99_budget = 50.0;
    for (int i = 1; i < argc; ++i) { std::string a = argv[i];
        if (a == "--serve") serve_only = true;
        else if (a == "--max-rate") do_max_rate = true;
        else if (a == "--p99" && i + 1 < argc) p99_budget = atof(argv[++i]);
        else if (a == "--demo" && i + 1 < argc) { demo = argv[++i]; if (i + 1 < argc && atoi(argv[i + 1]) >= 1) demo_n = atoi(argv[++i]); }
        else if (a == "--broker" && i + 1 < argc) broker = argv[++i];
        else if (a == "--pass" && i + 1 < argc) pass = argv[++i];
        else if (a == "--peer" && i + 1 < argc) { std::string p = argv[++i]; size_t c = p.rfind(':'); Target t; t.host = c == std::string::npos ? p : p.substr(0, c); t.port = c == std::string::npos ? 8788 : atoi(p.c_str() + c + 1); t.label = "peer " + p; peers.push_back(t); }
        else if (a == "--seconds" && i + 1 < argc) secs = atof(argv[++i]);
        else if (a == "--json" && i + 1 < argc) dir = argv[++i];
        else if (a == "--port" && i + 1 < argc) port = atoi(argv[++i]);
        else { fprintf(stderr, "usage: frogbench [--demo WORD [N]] [--peer HOST[:PORT]]... [--max-rate] [--p99 MS] [--serve] [--port N] [--seconds N] [--json DIR] [--broker URL] [--pass WORD]\n"); return 2; }
    }
    // --demo WORD: one word, shared memory on the public broker, no ports pasted by hand.
    std::string demo_id, demo_host, demo_display, broker_ip; int demo_rport = 0; bool in_demo = false;
    std::atomic<bool> heartbeat_run{false}; std::thread heartbeat;
    if (!demo.empty()) {
        // Resolve the broker's name to an IP ONCE (retried), then use that IP for the broker call
        // and for every shared-memory session, so a later DNS hiccup cannot break a rung.
        std::string berr, bhost = broker, bscheme = "http://";
        if (!bhost.compare(0, bscheme.size(), bscheme)) {
            std::string rest = bhost.substr(bscheme.size());
            std::string hostport = rest.substr(0, rest.find('/')), tail = rest.substr(hostport.size());
            std::string h = hostport, port = "80"; size_t cc = hostport.find(':');
            if (cc != std::string::npos) { h = hostport.substr(0, cc); port = hostport.substr(cc + 1); }
            std::string ip = resolve_ipv4(h, 5, berr);
            if (ip.empty()) { fprintf(stderr, "broker name %s could not be resolved after retries: %s\n", h.c_str(), berr.c_str()); return 1; }
            broker_ip = ip;
            broker = bscheme + ip + ":" + port + tail;      // broker URL now carries the literal IP
        }
        std::string url = broker + "?pass=" + url_encode(pass) + "&tag=" + url_encode(demo), err;
        printf("frogbench demo '%s': asking the broker for a shared memory...\n", demo.c_str());
        std::string body = http_get(url, err);
        if (body.empty()) { fprintf(stderr, "broker unreachable: %s\n", err.c_str()); return 1; }
        try {
            Json j = Json::parse(body);
            if (j["ok"].type != Json::Bool || !j["ok"].b) { fprintf(stderr, "broker refused: %s\n", j["error"].s.empty() ? body.c_str() : j["error"].s.c_str()); return 1; }
            std::string host_name = j["host"].s; int rport = int(j["port"].n);
            // Connect to the shared memory by the SAME resolved IP as the broker (same machine); the
            // broker's returned name is used only for display. No further DNS anywhere in the run.
            std::string host = broker_ip.empty() ? host_name : broker_ip;
            printf("  shared memory is at %s:%d (%s). Tell whoever you are demoing with to run:  frogbench --demo %s\n",
                   host_name.c_str(), rport, j["new"].b ? "you started it" : "already running", demo.c_str());
            // The demo is a demo with people in it. Wait until demo_n participants (including me) are
            // present in this shared memory before running, so everyone genuinely shares it. Presence is
            // the shared memory itself: each participant rewrites demo.presence.<id> with a fresh timestamp,
            // reads all of them, and the roster is the rows fresh within 15 s -- so a participant that leaves
            // ages out and does not hold up the rest. No orchestrator: the count is a fact everyone reads.
            Session s(host, rport); Memory m(s);
            char hn[256] = "?"; gethostname(hn, sizeof hn);
            std::random_device rd; char idbuf[32]; snprintf(idbuf, sizeof idbuf, "%08x%08x", rd(), rd());
            std::string me = std::string(hn) + "-" + idbuf;   // hostname + 64 random bits: unique per participant
            demo_id = me; demo_host = host; demo_display = host_name; demo_rport = rport; in_demo = true;
            printf("  waiting for %d participants on '%s' (Ctrl-C to stop)...", demo_n, demo.c_str()); fflush(stdout);
            int here = 1;
            for (int t = 0; t < 600; ++t) {                 // up to ~5 minutes
                m.write("demo", "presence", me, "{\"t\":" + std::to_string(now()) + "}");
                auto rows = m.read("demo", "presence", "", -1, 0, 15);   // fresh within 15 s: the live roster
                here = int(rows.size());
                if (here >= demo_n) break;
                if (t % 4 == 0) { printf(" %d/%d", here, demo_n); fflush(stdout); }
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
            if (here < demo_n) printf("\n  only %d of %d joined '%s' in time. Running with who is here.\n", here, demo_n, demo.c_str());
            else printf("\n  %d participants share this memory. Running.\n", here);
            peers.push_back({"shared memory '" + demo + "' on " + host_name, host, rport});
            // Keep our presence fresh for the WHOLE run, on its own connection, so we do not age off the
            // roster while qualifying/characterising -- a machine on the roster is one still participating.
            heartbeat_run = true;
            heartbeat = std::thread([host, rport, demo_id, &heartbeat_run]{
                try { Session hs(host, rport); Memory hm(hs);
                    while (heartbeat_run) { hm.write("demo", "presence", demo_id, "{\"t\":" + std::to_string(now()) + "}");
                        for (int k = 0; k < 20 && heartbeat_run; ++k) std::this_thread::sleep_for(std::chrono::milliseconds(100)); }
                } catch (const std::exception&) {}
            });
        } catch (const std::exception& e) { fprintf(stderr, "broker reply was not understood: %s\n  raw: %s\n", e.what(), body.substr(0, 200).c_str()); return 1; }
    }

    if (serve_only) { int e = ram_server_start("0.0.0.0:" + std::to_string(port), false); if (e) { fprintf(stderr, "cannot listen on :%d: %s\n", port, strerror(e)); return 1; }
        printf("frogbench RAM target on :%d -- point another frogbench at this host. Ctrl-C to stop.\n", port); for (;;) std::this_thread::sleep_for(std::chrono::hours(1)); }

    // always bring up our own loopback target, so a bare run needs nothing
    int lport = 0; for (int p = 8788; p < 8798; ++p) { if (!ram_server_start("127.0.0.1:" + std::to_string(p), true)) { lport = p; break; } }
    if (!lport) { fprintf(stderr, "could not bring up a local RAM target\n"); return 1; }
    std::vector<Target> targets; targets.push_back({"this machine (loopback)", "127.0.0.1", lport});
    for (auto& p : peers) targets.push_back(p);

    printf("frogbench -- can this machine FrogNet?\n%s, %u cores.  %zu target(s): loopback%s.\n",
           cpu_model_str().c_str(),
           std::thread::hardware_concurrency(), targets.size(), peers.empty() ? " only (pass --peer HOST to form a test mesh)" : (" + " + std::to_string(peers.size()) + " peer(s)").c_str());
#ifdef _WIN32
    if (!dir.empty()) { _mkdir(dir.c_str()); _mkdir((dir + "/runs").c_str()); }
#else
    if (!dir.empty()) { std::string mk = "mkdir -p '" + dir + "/runs'"; if (system(mk.c_str())) {} }
#endif

    // With peers: who is online, right now, and how far away (When / which are up).
    if (targets.size() > 1) {
        printf("\nPEERS  --  reachability and a specific message exchange\n");
        for (size_t i = 1; i < targets.size(); ++i) {
            double cms = 0; bool up = peer_online(targets[i], cms);
            char ts[32]; time_t nt = time(nullptr); strftime(ts, sizeof ts, "%H:%M:%S", localtime(&nt));
            if (!up) { printf("  %-28s OFFLINE at %s (connect failed in %.0f ms)\n", targets[i].label.c_str(), ts, cms); continue; }
            std::string d; bool ex = peer_exchange(targets[i], d);
            printf("  %-28s ONLINE  at %s, connect %.1f ms; message exchange: %s (%s)\n", targets[i].label.c_str(), ts, cms, ex ? "OK" : "FAILED", d.c_str());
        }
    }

    bool all_ok = true; int idc = 0; Headline demo_head;
    for (auto& t : targets) {
        if (!dir.empty() && &t == &targets[0]) env_json(dir, t);
        bool is_demo_target = in_demo && &t != &targets[0];
        bool q = qualify(t, secs, dir, idc, is_demo_target ? &demo_head : nullptr);
        all_ok &= q;
        if (q) {
            if (do_max_rate) peer_max_rate(t, secs, p99_budget);
            else characterise(t, secs, dir, idc);
        } else printf("  (skipping characterisation of %s: qualification did not pass)\n", t.label.c_str());
    }
    // Each participant writes its OWN result into the shared memory; everyone reads them all out.
    // No runner, no side channel: the collected view is a read of the one memory everyone shares.
    if (in_demo) {
        try {
            Session rs(demo_host, demo_rport); Memory rm(rs);
            unsigned cores = std::thread::hardware_concurrency();
            char bag[512];
            snprintf(bag, sizeof bag, "{\"host\":%s,\"cpu\":%s,\"cores\":%u,\"shared_ack_wps\":%.0f,\"shared_ack_p99_ms\":%.1f,\"shared_ack_dropped\":%ld,\"pass\":%s}",
                     Json::quote(demo_id).c_str(), Json::quote(cpu_model_str()).c_str(), cores, demo_head.ack_wps, demo_head.ack_p99, demo_head.ack_dropped, all_ok ? "true" : "false");
            rm.write("results", demo, demo_id, bag);
            printf("\nCOLLECTED RESULTS  --  shared memory '%s' on %s\n", demo.c_str(), demo_display.c_str());
            // Complete is a fact in the memory, not a timer: there is a result for every participant still
            // on the roster. Each participant keeps its presence fresh while it collects (so a machine that is
            // still finishing its run stays on the roster and is waited for); one that leaves ages out of BOTH
            // the roster and, being gone, the set it is counted against -- so the condition stays consistent.
            // The collector blocks until results >= roster. No participant is left behind, and no clock decides it.
            std::vector<Cell> res, roster;
            for (;;) {
                rm.write("results", demo, demo_id, bag);              // keep our own result and presence fresh
                rm.write("demo", "presence", demo_id, "{\"t\":" + std::to_string(now()) + "}");
                roster = rm.read("demo", "presence", "", -1, 0, 20);  // who is still here (fresh within 20 s)
                res = rm.read("results", demo);
                std::set<std::string> reported;
                for (auto& c : res) reported.insert(c.instance);
                bool all_reported = true;
                for (auto& r : roster) if (!reported.count(r.instance)) { all_reported = false; break; }
                if (all_reported && (int)res.size() >= (int)roster.size()) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
            std::sort(res.begin(), res.end(), [](const Cell& a, const Cell& b){ return a.instance < b.instance; });
            printf("  %-26s %-30s %5s  %12s  %9s  %7s  %s\n", "participant", "cpu", "cores", "ack writes/s", "p99 (ms)", "dropped", "ok");
            for (auto& c : res) {
                printf("  %-26s %-30.30s %5d  %12.0f  %9.1f  %7ld  %s\n",
                       c.bag["host"].s.c_str(), c.bag["cpu"].s.c_str(), (int)c.bag["cores"].n,
                       c.bag["shared_ack_wps"].n, c.bag["shared_ack_p99_ms"].n, (long)c.bag["shared_ack_dropped"].n,
                       c.bag["pass"].b ? "yes" : "NO");
            }
            printf("  %d participants, all reported. This table is a read of the shared memory; every machine sees the same one.\n",
                   (int)res.size());
        } catch (const std::exception& e) { printf("\n  could not collect shared results: %s\n", e.what()); }
    }
    heartbeat_run = false; if (heartbeat.joinable()) heartbeat.join();

    printf("\n%s\n", all_ok ? "RESULT: this machine can FrogNet. All targets qualified." : "RESULT: at least one target did not qualify (see FAIL lines above).");
    if (!dir.empty()) printf("Results in %s/. Turn them into an operating envelope:  bench_envelope.py %s\n", dir.c_str(), dir.c_str());
    else printf("Re-run with --json DIR to keep the measurements, then: bench_envelope.py DIR   and   lilypad_plan.py WORKLOAD.json --memory DIR/envelope.json\n");
    return all_ok ? 0 : 1;
}
