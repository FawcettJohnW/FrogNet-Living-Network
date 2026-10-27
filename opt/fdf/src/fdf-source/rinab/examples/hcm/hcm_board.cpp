// hcm_board.cpp -- [DAF_MODEL_V1] the hospital capacity manager's board: a member of the incident's DAF that holds one
// subscription to its names and serves what it sees as a web page. Nothing here asks for a hospital's address: it reads
// names. usage: hcm_board RAM_HOST:PORT DAF HTTP_PORT. It waits for its subscription's changes and resolves, one after
// the other, on its one session (a wait never overlaps another call there).
//   GET /            the board (one page, no external files)
//   GET /state.json  what the board holds now: hospitals, live or not, the current trauma choice, the incident's events
#include "daf_client.hpp"
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
using daf::Json; using daf::q;
static std::mutex mu; static std::string state = "{}"; static std::deque<std::string> events;
static std::string dj(const Json& j) {
    switch (j.type) { case Json::Null: return "null"; case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: { char x[40]; snprintf(x, sizeof x, "%.17g", j.n); return x; } case Json::Str: return q(j.s);
        case Json::Arr: { std::string o = "["; for (size_t k = 0; k < j.a.size(); ++k) o += (k ? "," : "") + dj(j.a[k]); return o + "]"; }
        default: { std::string o = "{"; for (size_t k = 0; k < j.o.size(); ++k) o += (k ? "," : "") + q(j.o[k].first) + ":" + dj(j.o[k].second); return o + "}"; } }
}
static std::string clock_now() { time_t t = time(nullptr); char b[16]; strftime(b, sizeof b, "%H:%M:%S", localtime(&t)); return b; }
static const char* PAGE = R"HTML(<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hospital Capacity</title><style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a66;--line:#e2e0da;--ok:#1f7a4d;--off:#b3261e;--pick:#0b5cad}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe7;--mute:#9a9893;--line:#2e2d2a;--ok:#4cc38a;--off:#ff8a80;--pick:#7ab8ff}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}main{max-width:980px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 18px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}
@media (max-width:760px){.grid{grid-template-columns:1fr}}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.pick{font-size:22px;font-weight:600;color:var(--pick)}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line)}
th{font-weight:500;color:var(--mute);font-size:13px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}.on{background:var(--ok)}.offl{background:var(--off)}
.ev{font-family:ui-monospace,monospace;font-size:12.5px;color:var(--mute);margin:3px 0}.chosen td{font-weight:600}</style></head><body><main>
<h1>Hospital Capacity</h1><p class="sub" id="sub">connecting...</p>
<div class="grid"><div class="card"><table><thead><tr><th>Hospital</th><th>Status</th><th>Trauma</th><th>Capacity</th></tr></thead><tbody id="rows"></tbody></table></div>
<div><div class="card" style="margin-bottom:16px"><div style="color:var(--mute);font-size:13px">Next trauma patient goes to</div><div class="pick" id="pick">-</div></div>
<div class="card"><div style="color:var(--mute);font-size:13px;margin-bottom:6px">Incident events</div><div id="ev"></div></div></div></div></main>
<script>
async function tick(){try{const s=await (await fetch('state.json',{cache:'no-store'})).json();
document.getElementById('sub').textContent=s.daf+' · updated '+s.at;
document.getElementById('pick').textContent=s.trauma_choice||'none available';
const rows=document.getElementById('rows');rows.innerHTML='';
for(const h of s.hospitals){const tr=document.createElement('tr');if(h.apn===s.trauma_choice)tr.className='chosen';
const f=h.facts||{};tr.innerHTML='<td>'+h.apn+'</td><td><span class="dot '+(h.live?'on':'offl')+'"></span>'+(h.live?'connected':'offline')+'</td><td>'+(h.live?(f.trauma?'yes':'no'):'\u2014')+'</td><td>'+(h.live&&f.capacity!==undefined?f.capacity:'—')+'</td>';rows.appendChild(tr);}
const ev=document.getElementById('ev');ev.innerHTML='';for(const e of s.events.slice().reverse()){const d=document.createElement('div');d.className='ev';d.textContent=e;ev.appendChild(d);}
}catch(e){document.getElementById('sub').textContent='board unreachable';}}
tick();setInterval(tick,500);</script></body></html>)HTML";
static void serve_http(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(port)); a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(s, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || listen(s, 16) != 0) { perror("hcm_board: http"); exit(2); }
    for (;;) { int c = accept(s, nullptr, nullptr); if (c < 0) continue;
        char buf[2048]; ssize_t n = recv(c, buf, sizeof buf - 1, 0); if (n <= 0) { close(c); continue; } buf[n] = 0;
        std::string req(buf), body, type = "text/html; charset=utf-8";
        if (req.compare(0, 16, "GET /state.json ") == 0) { std::lock_guard<std::mutex> l(mu); body = state; type = "application/json"; }
        else body = PAGE;
        std::string out = "HTTP/1.1 200 OK\r\nContent-Type: " + type + "\r\nCache-Control: no-store\r\nContent-Length: " + std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
        (void)!send(c, out.data(), out.size(), MSG_NOSIGNAL); close(c); }
}
int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: hcm_board RAM_HOST:PORT DAF HTTP_PORT\n"); return 2; }
    const std::string where = argv[1], D = argv[2];
    std::thread(serve_http, atoi(argv[3])).detach();
    daf::Client c(where);
    Json e = c.call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q("board-" + std::to_string(getpid())) + ",\"role\":\"board\"");
    if (!e["ok"].b) { fprintf(stderr, "hcm_board: enroll refused: %s %s\n", e["error"].s.c_str(), e["why"].s.c_str()); return 2; }
    Json s = c.call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"facilities\",\"receiving\"]");
    if (!s["ok"].b) { fprintf(stderr, "hcm_board: subscribe refused: %s\n", s["why"].s.c_str()); return 2; }
    const std::string sub = s["sub"].s; int64_t rev = 0; std::string last_choice = "?"; std::map<std::string, bool> was_live;
    for (;;) {
        Json got = c.wait(D, "sub", sub, uint64_t(rev));    // [DAF_WAIT_V1] answered by the change; no polling
        if (!got["ok"].b || got["rows"].a.empty()) continue; rev = int64_t(got["rev"].n); const Json data = got["rows"].a[0]["v"]["data"];
        Json one = c.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"receiving\",\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}");
        const std::string choice = one["members"].a.empty() ? "" : one["members"].a[0]["apn"].s;
        std::map<std::string, Json> live; for (auto& m : data["receiving"].a) live[m["apn"].s] = m;
        std::string hs = "["; bool f = true;
        for (auto& m : data["facilities"].a) { const std::string apn = m["apn"].s; const bool on = live.count(apn) > 0;
            hs += (f ? "" : ",") + std::string("{\"apn\":") + q(apn) + ",\"live\":" + (on ? "true" : "false") + ",\"facts\":" + (on ? dj(live[apn]["facts"]) : dj(m["facts"])) + "}"; f = false;
            if (was_live.count(apn) && was_live[apn] != on) { std::lock_guard<std::mutex> l(mu); events.push_back(clock_now() + "  " + apn + (on ? " connected" : " went offline")); }
            was_live[apn] = on; }
        hs += "]";
        std::lock_guard<std::mutex> l(mu);
        if (choice != last_choice) { events.push_back(clock_now() + "  trauma choice: " + (choice.empty() ? "none" : choice)); last_choice = choice; }
        while (events.size() > 40) events.pop_front();
        std::string ev = "["; bool g = true; for (auto& x : events) { ev += (g ? "" : ",") + q(x); g = false; } ev += "]";
        state = "{\"daf\":" + q(D) + ",\"at\":" + q(clock_now()) + ",\"trauma_choice\":" + q(choice) + ",\"hospitals\":" + hs + ",\"events\":" + ev + "}";
    }
}
