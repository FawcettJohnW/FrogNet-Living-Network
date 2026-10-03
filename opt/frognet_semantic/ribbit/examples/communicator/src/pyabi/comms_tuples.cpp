// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// libcomms_tuples -- the Communicator's memory, as a C ABI for the Python implementation (ctypes).
// The platform's TuplesClient (vendor "comms") with everything the room needs, including the held read's
// write-order "after" ([HOLD_THE_READ_V1]), which the platform's generic Python wrapper (frognet_tuples) does not take.
// Everything above this -- room, call, wire, codecs, window -- is Python.
#include "tuples.hpp"
#include <cstdlib>
#include <cstring>
#include <string>

#ifdef _WIN32
#  define CT_API extern "C" __declspec(dllexport)
#else
#  define CT_API extern "C" __attribute__((visibility("default")))
#endif

namespace {
struct Client : ribbit::TuplesClient { std::string vendor_id() const override { return "comms"; } };
char* dup(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
void fail(char** err, const std::exception& e) { if (err) *err = dup(e.what()); }
}

CT_API void* ct_open(const char* host, int port, char** err) {
    try { auto* c = new Client; c->connect(host, port); return c; }
    catch (const std::exception& e) { fail(err, e); return nullptr; }
}
CT_API void ct_close(void* h) { delete static_cast<Client*>(h); }
// Ends a held read in another thread now (sockets shut, nothing freed) -- [A_HELD_READ_ENDS_AT_ONCE_V1].
CT_API void ct_shutdown(void* h) { try { static_cast<Client*>(h)->session().shutdown(); } catch (const std::exception&) {} }
CT_API long long ct_put(void* h, const char* service, const char* var, const char* scope, const char* json, int own, char** err) {
    try { return (long long)static_cast<Client*>(h)->put(service, var, scope, json, own != 0); }
    catch (const std::exception& e) { fail(err, e); return -1; }
}
// JSON array of tuples ({var, scope, name, addr, value, ts_env, age_s, id}); NULL with *err on failure.
CT_API char* ct_get(void* h, const char* service, int fresh_s, double wait_s, int min_rows, const char* var, long long after, char** err) {
    try { return dup(ribbit::TuplesClient::to_json(static_cast<Client*>(h)->get_all(service, fresh_s, wait_s, min_rows, var ? var : "", after))); }
    catch (const std::exception& e) { fail(err, e); return nullptr; }
}
CT_API int ct_remove(void* h, const char* service, const char* var, const char* scope, char** err) {
    try { return static_cast<Client*>(h)->remove_tuple(service, var, scope) ? 1 : 0; }
    catch (const std::exception& e) { fail(err, e); return -1; }
}
CT_API char* ct_my_ip(void* h, char** err) {
    try { return dup(static_cast<Client*>(h)->my_ip()); }
    catch (const std::exception& e) { fail(err, e); return nullptr; }
}
CT_API void ct_free(char* p) { std::free(p); }
