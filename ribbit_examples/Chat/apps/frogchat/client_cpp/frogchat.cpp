// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogchat.cpp -- P2P chat through shared memory, in C++. The same program as
// client/frogchat.py: same addresses, same JSON, same commands; the two talk to
// each other through the memory and neither knows what the other is written in.
//
//     frogchat --me Dave [--to Bob] [--store HOST:PORT]
//
//   SEND   waits for me to hit Return, then WRITES the line to
//              ChatServer.<recipient>.<me>          one cell per recipient
//   READ   sits in a BLOCKING READ on my own buffer, ChatServer.<me>.*,
//          and wakes when anyone writes into it.
//   HERE   I am present because ChatPresence.here.<me> is fresh; the reader
//          rewrites it each time its read returns, so presence needs no thread.
//
//   /list    /Dave hello    /Dave /Alice hello    plain line = same people    /help  /quit
//
// Two threads. No chat server. No fallbacks: one store, named on the command
// line or in store.json beside the executable; anything else is an error.
#include "frogram.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>

#ifdef _WIN32
#  include <windows.h>
#else
#  include <unistd.h>
#endif

using namespace frogram;

static const char* SERVICE = "ChatServer";
static const char* PRESENCE = "ChatPresence";
static const char* HERE = "here";
static const int   PRESENCE_FRESH_S = 60;
static const double WAIT_S = 25.0;
static const char* HELP = "/list   /Name message   /Name /Other message   plain line = same people as last time   /quit";

static std::string lower(std::string s) { std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); }); return s; }
static bool is_command(const std::string& w) { std::string l = lower(w); return l == "list" || l == "quit" || l == "help"; }
static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }

// '/Dave /Alice hi there' -> names {Dave, Alice}, text 'hi there'.  '/list' -> command "list".
struct Line { std::string command; std::vector<std::string> names; std::string text; };
static Line parse_line(const std::string& raw) {
    Line out; std::istringstream in(raw); std::string w; std::vector<std::string> words;
    while (in >> w) words.push_back(w);
    size_t i = 0;
    while (i < words.size() && words[i].size() > 1 && words[i][0] == '/') out.names.push_back(words[i++].substr(1));
    for (; i < words.size(); ++i) out.text += (out.text.empty() ? "" : " ") + words[i];
    if (out.names.size() == 1 && out.text.empty() && is_command(out.names[0])) { out.command = lower(out.names[0]); out.names.clear(); }
    return out;
}

static std::string exe_dir() {
    char buf[4096] = {0};
#ifdef _WIN32
    DWORD n = GetModuleFileNameA(NULL, buf, sizeof buf - 1); (void)n;
#else
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1); if (n < 0) buf[0] = 0;
#endif
    std::string p(buf); size_t cut = p.find_last_of("/\\");
    return cut == std::string::npos ? "." : p.substr(0, cut);
}

class Chat {
public:
    Chat(Memory& m, const std::string& me, const std::vector<std::string>& to) : mem(m), me(me), to(to) {
        if (me.empty() || is_command(me) || me.find('/') != std::string::npos || me.find(' ') != std::string::npos)
            throw std::invalid_argument("'" + me + "' cannot be used as a name");
        for (auto& c : mem.read(SERVICE, me)) after = std::max<int64_t>(after, int64_t(c.id));   // what is already there is not new to me
        announce();
    }
    void announce() { mem.write(PRESENCE, HERE, me, "{\"name\":" + Json::quote(me) + "}"); }
    void say(const std::string& text, const std::vector<std::string>& names) {
        if (!names.empty()) to = names;
        if (to.empty()) throw std::invalid_argument("nobody addressed yet: start the line with /Name");
        std::string list; for (auto& n : to) list += (list.empty() ? "" : ",") + Json::quote(n);
        char ts[64]; snprintf(ts, sizeof ts, "%.6f", now_s());
        std::string bag = "{\"from\":" + Json::quote(me) + ",\"to\":[" + list + "],\"ts\":" + ts + ",\"text\":" + Json::quote(text) + "}";
        for (auto& n : to) mem.write(SERVICE, n, me, bag);
    }
    std::vector<std::string> who() {
        std::vector<std::string> out;
        for (auto& c : mem.read(PRESENCE, HERE, "", -1, 0, PRESENCE_FRESH_S)) out.push_back(c.bag["name"].s.empty() ? c.instance : c.bag["name"].s);
        std::sort(out.begin(), out.end(), [](const std::string& a, const std::string& b) { return lower(a) < lower(b); });
        return out;
    }
    std::vector<Json> next_messages(double wait_s) {            // block on ChatServer.<me>.*
        std::vector<Json> out;
        for (auto& c : mem.read(SERVICE, me, "", after, wait_s)) { after = std::max<int64_t>(after, int64_t(c.id)); out.push_back(c.bag); }
        return out;
    }
    void leave() { for (auto& c : mem.read(PRESENCE, HERE, me)) mem.remove(c.id); }
    Memory& mem; std::string me; std::vector<std::string> to; int64_t after = 0;
};

static void show(const Chat& chat, const Json& m) {
    std::time_t t = std::time_t(m["ts"].n); char when[16] = "??:??:??"; std::tm tmv;
#ifdef _WIN32
    localtime_s(&tmv, &t);
#else
    localtime_r(&t, &tmv);
#endif
    std::strftime(when, sizeof when, "%H:%M:%S", &tmv);
    std::string also;
    for (auto& n : m["to"].a) if (lower(n.s) != lower(chat.me)) also += (also.empty() ? " +" : ",") + n.s;
    std::cout << "[" << (m["from"].s.empty() ? "?" : m["from"].s) << also << " " << when << "] " << m["text"].s << std::endl;
}

int main(int argc, char** argv) {
    std::string store, me; std::vector<std::string> to;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--help" || a == "-h") { std::cout << "usage: frogchat --me NAME [--to NAME]... [--store HOST:PORT]\n" << HELP << "\n"; return 0; }
        if (i + 1 >= argc) { std::cerr << "missing value for " << a << "\n"; return 2; }
        if (a == "--store") store = argv[++i]; else if (a == "--me") me = argv[++i]; else if (a == "--to") to.push_back(argv[++i]);
        else { std::cerr << "unknown argument: " << a << "\n"; return 2; }
    }
    if (me.empty()) { std::cerr << "--me is required\n"; return 2; }
    if (store.empty()) {
        std::string conf = exe_dir() + "/store.json"; std::ifstream f(conf.c_str()); std::stringstream ss; ss << f.rdbuf();
        try { store = Json::parse(ss.str())["store"].s; } catch (const std::exception&) {}
        if (!f || store.empty()) { std::cerr << "no --store given and " << conf << " is not usable\n"; return 2; }
    }
    size_t colon = store.rfind(':');
    if (colon == std::string::npos) { std::cerr << "store must be HOST:PORT\n"; return 2; }

    std::unique_ptr<Session> session; std::unique_ptr<Memory> mem; std::unique_ptr<Chat> chat;
    try {
        session.reset(new Session(store.substr(0, colon), std::atoi(store.c_str() + colon + 1)));
        mem.reset(new Memory(*session)); chat.reset(new Chat(*mem, me, to));
    } catch (const std::invalid_argument& e) { std::cerr << e.what() << "\n"; return 2; }
      catch (const std::exception& e) { std::cerr << "cannot use the memory at " << store << ": " << e.what() << "\n"; return 2; }

    std::atomic<bool> stop(false); std::string fatal; std::mutex fatal_m;
    // One session for the application. The library matches replies by sequence,
    // so the reader's parked read and the writer's calls share it without waiting
    // on each other. The two threads touch different halves of Chat.
    std::thread reader([&] {
        try {
            while (!stop) { for (auto& m : chat->next_messages(WAIT_S)) show(*chat, m); if (!stop) chat->announce(); }
        } catch (const std::exception& e) { if (!stop) { std::lock_guard<std::mutex> g(fatal_m); fatal = e.what(); stop = true; } }
    });

    std::cout << me << " on " << store << ".  " << HELP << std::endl;
    std::string raw;
    try {
        while (!stop && std::getline(std::cin, raw)) {
            Line l = parse_line(raw);
            if (l.command == "quit") break;
            if (l.command == "help") { std::cout << HELP << std::endl; continue; }
            if (l.command == "list") {
                std::string out; for (auto& n : chat->who()) out += (out.empty() ? "" : ", ") + n + (lower(n) == lower(me) ? " (you)" : "");
                std::cout << "here: " << out << std::endl; continue;
            }
            if (l.names.empty() && l.text.empty()) continue;
            if (l.text.empty()) { chat->to = l.names; std::string t; for (auto& n : chat->to) t += (t.empty() ? "" : ", ") + n; std::cout << "now talking to " << t << std::endl; continue; }
            try { chat->say(l.text, l.names); } catch (const std::invalid_argument& e) { std::cout << e.what() << std::endl; continue; }
            if (!l.names.empty()) {
                std::vector<std::string> here = chat->who(); std::string absent;
                for (auto& n : l.names) if (std::none_of(here.begin(), here.end(), [&](const std::string& h) { return lower(h) == lower(n); })) absent += (absent.empty() ? "" : ", ") + n;
                if (!absent.empty()) std::cout << "(" << absent << " not here right now; it is in their buffer)" << std::endl;
            }
        }
    } catch (const std::exception& e) { std::lock_guard<std::mutex> g(fatal_m); fatal = e.what(); }
    bool failed; { std::lock_guard<std::mutex> g(fatal_m); failed = !fatal.empty(); }
    int rc = 0;
    if (failed) { std::cerr << "memory failure: " << fatal << "\n"; rc = 1; }
    else { try { chat->leave(); } catch (const std::exception& e) { std::cerr << "could not remove my presence cell: " << e.what() << "\n"; rc = 1; } }
    stop = true;
    session->shutdown();          // the reader is parked in the memory; this is what lets it go
    reader.join();
    return rc;
}
