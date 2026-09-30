// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// tuples_c.cpp -- tuples.h: ribbit::TuplesClient behind a C ABI.
#include "tuples.h"
#include "tuples.hpp"
#include <cstdlib>
#include <cstring>

namespace {
class Named : public ribbit::TuplesClient {
public:
    explicit Named(std::string id) : id_(std::move(id)) {}
    std::string vendor_id() const override { return id_; }
private:
    std::string id_;
};
char* dup(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
void fail(char** err, const std::exception& x) { if (err) *err = dup(x.what()); }
}
struct tuples_client { Named c; explicit tuples_client(const char* id) : c(id) {} };

extern "C" {
tuples_client* tuples_open(const char* vendor_id, const char* host, int port, char** err) {
    tuples_client* h = new tuples_client(vendor_id);
    try { h->c.connect(host, port); return h; } catch (const std::exception& x) { fail(err, x); delete h; return nullptr; }
}
void tuples_close(tuples_client* c) { delete c; }
int64_t tuples_put(tuples_client* c, const char* service, const char* var, const char* scope, const char* value, int own, char** err) {
    try { return int64_t(c->c.put(service, var, scope, value, own != 0)); } catch (const std::exception& x) { fail(err, x); return -1; }
}
char* tuples_get_all(tuples_client* c, const char* service, int fresh_s, double wait_s, int min_rows, const char* var, char** err) {
    try { return dup(ribbit::TuplesClient::to_json(c->c.get_all(service, fresh_s, wait_s, min_rows, var ? var : ""))); }
    catch (const std::exception& x) { fail(err, x); return nullptr; }
}
int tuples_remove(tuples_client* c, const char* service, const char* var, const char* scope, char** err) {
    try { return c->c.remove_tuple(service, var, scope) ? 1 : 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
int tuples_remove_id(tuples_client* c, int64_t id, char** err) {
    try { c->c.remove(uint64_t(id)); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
char* tuples_my_ip(tuples_client* c, char** err) {
    try { return dup(c->c.my_ip()); } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
void tuples_free(char* s) { std::free(s); }
}
