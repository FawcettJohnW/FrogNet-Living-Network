// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lisper_c.cpp -- lisper.h: lisper::Client behind a C ABI.
#include "lisper.h"
#include "lisper_client.hpp"
#include <cstdlib>
#include <cstring>

struct lisper_client { lisper::Client c; };
static char* dup(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
static void fail(char** err, const std::exception& x) { if (err) *err = dup(x.what()); }

extern "C" {
lisper_client* lisper_open(const char* host, int port, char** err) {
    lisper_client* h = new lisper_client;
    try { h->c.connect(host, port); return h; } catch (const std::exception& x) { fail(err, x); delete h; return nullptr; }
}
void lisper_close(lisper_client* c) { delete c; }
char* lisper_operation(lisper_client* c, const char* name, const char* args, char** err) {
    try { return dup(c->c.operation(name, args)); } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
char* lisper_register(lisper_client* c, const char* hex, const char* source, char** err) {
    try { return dup(c->c.register_packet(hex, source)); } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
char* lisper_resolve(lisper_client* c, const char* iid, const char* prefix, const char* group, char** err) {
    try { return dup(c->c.resolve(iid, prefix, group ? group : "")); } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
int lisper_add_site(lisper_client* c, const char* iid, const char* prefix, const char* group, int ams, int key_id, const char* pw, char** err) {
    try { c->c.add_site(iid, prefix, group ? group : "", ams != 0, key_id, pw ? pw : ""); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
int lisper_delete_site(lisper_client* c, const char* iid, const char* prefix, const char* group, char** err) {
    try { c->c.delete_site(iid, prefix, group ? group : ""); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
void lisper_free(char* s) { std::free(s); }
}
