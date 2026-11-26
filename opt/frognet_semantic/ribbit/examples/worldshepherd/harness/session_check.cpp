// session_check.cpp -- the session library itself (libkartram.so, as a SARA's Python loads it), exercised from C++
// so it can be run on a machine type that has no Python here: under emulation, for aarch64.
//   session_check HOST PORT API TICKETS CLAIMANTS
// One session issues TICKETS tickets; CLAIMANTS sessions each claim every one of them, a thread a claim, all at once.
// PASS: every ticket claimed by exactly one session, and its own cell says so.
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>
extern "C" {
void* kr_open(const char* host, int port, const char* api, char** err);
void  kr_close(void* h);
void  kr_free(void* p);
char* kr_call(void* h, const char* method, const char* path, const char* body, char** err);
char* kr_read(void* h, const char* service, const char* variable, const char* instance, long long after, double wait_s, char** err);
}
static std::string call(void* h, const std::string& api, const std::string& op, const std::string& body) {
    char* err = nullptr; char* r = kr_call(h, "POST", (api + "?op=" + op).c_str(), body.c_str(), &err);
    if (!r) { std::string e = err ? err : "?"; if (err) kr_free(err); fprintf(stderr, "FAIL %s: %s\n", op.c_str(), e.c_str()); exit(1); }
    std::string s = r; kr_free(r); return s;
}
int main(int argc, char** argv) {
    if (argc < 6) { fprintf(stderr, "usage: session_check HOST PORT API TICKETS CLAIMANTS\n"); return 2; }
    const std::string host = argv[1], api = argv[3]; const int port = atoi(argv[2]), N = atoi(argv[4]), C = atoi(argv[5]);
    auto open = [&] { char* err = nullptr; void* h = kr_open(host.c_str(), port, api.c_str(), &err); if (!h) { fprintf(stderr, "FAIL open: %s\n", err ? err : "?"); exit(1); } return h; };
    const std::string run = std::to_string(long(time(nullptr)) % 100000);
    void* issuer = open();
    for (int k = 0; k < N; ++k)
        call(issuer, api, "issue", "{\"who\":\"issuer-" + run + "\",\"ticket\":\"t" + run + "-" + std::to_string(k) + "\",\"sha\":\"x\",\"exp\":1e12,\"n\":" + std::to_string(k + 1) + "}");
    std::vector<std::atomic<int>> won(N); for (auto& w : won) w.store(0);
    std::atomic<long> claimed{0}, consumed{0}, other{0};
    std::vector<void*> hs; for (int c = 0; c < C; ++c) hs.push_back(open());
    std::vector<std::thread> th;
    for (int c = 0; c < C; ++c) for (int k = 0; k < N; ++k) th.emplace_back([&, c, k] {
        const std::string r = call(hs[c], api, "claim", "{\"who\":\"c" + std::to_string(c) + "-" + run + "\",\"ticket\":\"t" + run + "-" + std::to_string(k) + "\",\"sha\":\"x\",\"at\":1,\"n\":" + std::to_string(k + 1) + "}");
        if (r.find("\"outcome\":\"claimed\"") != std::string::npos) { won[k].fetch_add(1); claimed.fetch_add(1); }
        else if (r.find("\"outcome\":\"consumed\"") != std::string::npos) consumed.fetch_add(1);
        else other.fetch_add(1); });
    for (auto& t : th) t.join();
    int once = 0, twice = 0, never = 0; for (auto& w : won) { int x = w.load(); if (x == 1) ++once; else if (x > 1) ++twice; else ++never; }
    char* err = nullptr; char* cells = kr_read(issuer, "worldshepherd", ("won.issuer-" + run).c_str(), "", -1, 0.0, &err);
    long says = 0; if (cells) { for (const char* p = cells; (p = strstr(p, "\"won\":\"c")); ++p) ++says; kr_free(cells); }
    for (void* h : hs) kr_close(h);
    kr_close(issuer);
    const bool ok = once == N && !twice && !never && !other.load() && says >= N;
    printf("%s session library: %d tickets, %d sessions, %ld claims on %zu threads: won once %d, twice %d, never %d; answered claimed %ld, consumed %ld, other %ld; cells that name a winner %ld\n",
           ok ? "PASS" : "FAIL", N, C, claimed.load() + consumed.load() + other.load(), th.size(), once, twice, never, claimed.load(), consumed.load(), other.load(), says);
    return ok ? 0 : 1;
}
