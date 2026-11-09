// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// space_c.cpp -- ribbit::Space ([SPACE_V1], ribbit/cpp/include/ribbit_space.hpp: the one library every publish/subscribe
// system on the memory is built on -- the MQTT and Redis conversions' own) behind a C ABI, for the experiment's Python
// agents (ctypes). Producers write their own cells; consumers hold standing follows on families ([STANDING_WATCH_V1]):
// the memory streams each batch as it lands, and `on` is given it, in the family's write order.
//
// Rows come to `on` as JSON text: [{"id":N,"topic":"..","instance":"..","bag":{...}|null,"gone":bool}, ...]
#include "ribbit_space.hpp"
#include "frogram.hpp"
#include <cstdlib>
#include <cstring>
#include <string>

namespace {
char* dup(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
void fail(char** err, const std::exception& x) { if (err) *err = dup(x.what()); }
std::string rows_json(const std::vector<ribbit::SpaceRow>& rows) {
    std::string o = "[";
    for (size_t i = 0; i < rows.size(); ++i) {
        const auto& r = rows[i];
        const std::string b = r.gone ? std::string() : r.text();
        if (i) o += ",";
        o += "{\"id\":" + std::to_string(r.id) + ",\"topic\":" + frogram::Json::quote(r.topic) + ",\"instance\":" +
             frogram::Json::quote(r.instance) + ",\"bag\":" + (b.empty() ? std::string("null") : b) + ",\"gone\":" + (r.gone ? "true" : "false") + "}";
    }
    return o + "]";
}
}

extern "C" {
typedef void (*space_on_rows)(void* ctx, const char* rows_json);
typedef void (*space_on_end)(void* ctx, const char* why);

void* space_open(const char* where, const char* vendor, char** err) {
    try { return new ribbit::Space(where, vendor); } catch (const std::exception& x) { fail(err, x); return nullptr; }
}
void space_close(void* h) { delete static_cast<ribbit::Space*>(h); }
int space_write(void* h, const char* sp, const char* topic, const char* inst, const char* bag_json, char** err) {
    try { static_cast<ribbit::Space*>(h)->write(sp, topic, inst, bag_json); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
int space_linger(void* h, const char* sp, const char* topic, int seconds, char** err) {
    try { static_cast<ribbit::Space*>(h)->linger(sp, topic, seconds); return 0; } catch (const std::exception& x) { fail(err, x); return -1; }
}
char* space_read(void* h, const char* sp, const char* topic, const char* inst, long long after, double wait_s, char** err) {
    try { return dup(rows_json(static_cast<ribbit::Space*>(h)->read(sp, topic, inst ? inst : "", after, wait_s))); }
    catch (const std::exception& x) { fail(err, x); return nullptr; }
}
int space_follow(void* h, const char* sp, const char* topic, long long after, space_on_rows on, space_on_end end, void* ctx, char** err) {
    try {
        static_cast<ribbit::Space*>(h)->follow(sp, topic, after,
            [on, ctx](std::vector<ribbit::SpaceRow>& rows) { const std::string j = rows_json(rows); on(ctx, j.c_str()); },
            [end, ctx](const std::string& why) { if (end) end(ctx, why.c_str()); });
        return 0;
    } catch (const std::exception& x) { fail(err, x); return -1; }
}
long long space_newest(void* h, const char* sp, const char* topic, char** err) {
    try { return (long long)static_cast<ribbit::Space*>(h)->newest(sp, topic); } catch (const std::exception& x) { fail(err, x); return -1; }
}
int space_flush(void* h, double timeout_s) { return static_cast<ribbit::Space*>(h)->flush(timeout_s) ? 1 : 0; }
char* space_stats(void* h) { return dup(static_cast<ribbit::Space*>(h)->stats()); }
void space_free(char* s) { std::free(s); }
}
