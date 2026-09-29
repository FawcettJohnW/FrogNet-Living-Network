// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// chat_c.cpp -- chat.h: chat::Client behind a C ABI.
#include "chat.h"
#include "chat_client.hpp"
#include <cstdlib>
#include <cstring>

struct chat_client { chat::Client c; };

static char* dup(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
static void fail(char** err, const std::exception& x) { if (err) *err = dup(x.what()); }

extern "C" {
chat_client* chat_open(const char* host, int port, char** err) {
    chat_client* h = new chat_client;
    try { h->c.connect(host, port); return h; } catch (const std::exception& x) { fail(err, x); delete h; return nullptr; }
}
void chat_close(chat_client* c) { delete c; }
int chat_send(chat_client* c, const char* to, const char* from, const char* text, char** err) {
    try { c->c.send(to, from, text); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
int64_t chat_position(chat_client* c, const char* me, char** err) {
    try { return int64_t(c->c.position(me)); } catch (const std::exception& x) { fail(err, x); return -1; }
}
char* chat_receive(chat_client* c, const char* me, int64_t* after, double wait_s, char** err) {
    try {
        uint64_t a = uint64_t(*after);
        auto ms = c->c.receive(me, a, wait_s);
        std::string o = "[";
        for (size_t k = 0; k < ms.size(); ++k) {
            char ts[32]; snprintf(ts, sizeof ts, "%.3f", ms[k].ts);
            o += (k ? "," : "") + std::string("{\"from\":") + frogram::Json::quote(ms[k].from) + ",\"text\":" + frogram::Json::quote(ms[k].text)
               + ",\"ts\":" + ts + ",\"id\":" + std::to_string(ms[k].id) + "}";
        }
        *after = int64_t(a);
        return dup(o + "]");
    } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
void chat_free(char* s) { std::free(s); }
}
