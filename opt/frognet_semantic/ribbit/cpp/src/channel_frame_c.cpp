// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// channel_frame_c.cpp -- [THE_FRAME_IS_A_LIBRARY_V1] channel_frame.hpp for any language that can call C: the shared
// library libchannel_frame. python/channel_codec.py is its Python binding. Every function that can fail returns -1
// (or null) and puts the reason in *err, which the caller frees with cf_free. Nothing is swallowed.
#include "channel_frame.hpp"

#include <cstdlib>
#include <cstring>
#include <deque>

#ifdef _WIN32
#  define CF_API extern "C" __declspec(dllexport)
#else
#  define CF_API extern "C" __attribute__((visibility("default")))
#endif

using namespace channel_frame;

namespace {
struct Handle {
    std::unique_ptr<Codec> codec;
    std::deque<Message> ready;
    std::mutex said_guard;
    std::string said;
};
char* text(const std::string& s) { char* p = static_cast<char*>(std::malloc(s.size() + 1)); std::memcpy(p, s.c_str(), s.size() + 1); return p; }
uint8_t* bytes(const Bytes& b) { uint8_t* p = static_cast<uint8_t*>(std::malloc(b.size() ? b.size() : 1)); if (!b.empty()) std::memcpy(p, b.data(), b.size()); return p; }
std::map<std::string, std::string> fields_of(const char* lines) {      // "name=value\n" for each field
    std::map<std::string, std::string> f;
    std::string s(lines ? lines : "");
    for (size_t a = 0; a < s.size();) {
        size_t nl = s.find('\n', a); if (nl == std::string::npos) nl = s.size();
        const std::string line = s.substr(a, nl - a); a = nl + 1;
        const size_t eq = line.find('=');
        if (eq != std::string::npos) f[line.substr(0, eq)] = line.substr(eq + 1);
    }
    return f;
}
template <class F> int guarded(char** err, F f) {
    try { return f(); }
    catch (const std::exception& e) { if (err) *err = text(e.what()); return -1; }
}
}  // namespace

CF_API const char* cf_build() { return BUILD; }
CF_API void cf_free(void* p) { std::free(p); }

CF_API void* cf_open(int opener, const char* far, const char* fnid, const char* secrets, char** err) {
    try {
        auto* h = new Handle;
        h->codec = std::make_unique<Codec>(opener != 0, far ? far : "", fnid ? fnid : "", secrets && *secrets ? secrets : DEFAULT_SECRETS,
                                           [h](const std::string& s) { std::lock_guard<std::mutex> g(h->said_guard); h->said += s + "\n"; });
        return h;
    } catch (const std::exception& e) { if (err) *err = text(e.what()); return nullptr; }
}
CF_API void cf_close(void* c) { delete static_cast<Handle*>(c); }

// present 0: this process holds no setting for the Role.
CF_API int cf_setting(void* c, const char* role, int present, const char* driver, unsigned long long number, const char* protection, long step, char** err) {
    return guarded(err, [&] {
        std::optional<Setting> s;
        if (present) { s = Setting{}; if (driver && *driver) s->driver = driver; s->number = number; if (protection && *protection) s->protection = protection; s->step = step; }
        static_cast<Handle*>(c)->codec->setting(role ? role : "", s);
        return 0;
    });
}

// 1: *unit holds a unit (cf_free it). 0: none (pack: messages are being sent again, take cf_next first).
CF_API int cf_pack(void* c, const char* role, const uint8_t* m, size_t n, uint8_t** unit, size_t* unit_n, char** err) {
    return guarded(err, [&] {
        Bytes u;
        if (!static_cast<Handle*>(c)->codec->pack(role ? role : "", Bytes(m, m + n), u)) return 0;
        *unit = bytes(u); *unit_n = u.size(); return 1;
    });
}
CF_API int cf_pack_reply(void* c, const char* role, int sequence, unsigned long long counter, const uint8_t* m, size_t n, uint8_t** unit, size_t* unit_n, char** err) {
    return guarded(err, [&] {
        Bytes u;
        if (!static_cast<Handle*>(c)->codec->pack_reply(role ? role : "", sequence, counter, Bytes(m, m + n), u)) return 0;
        *unit = bytes(u); *unit_n = u.size(); return 1;
    });
}
CF_API int cf_next(void* c, uint8_t** unit, size_t* unit_n, char** err) {
    return guarded(err, [&] {
        Bytes u;
        if (!static_cast<Handle*>(c)->codec->next(u)) return 0;
        *unit = bytes(u); *unit_n = u.size(); return 1;
    });
}
CF_API int cf_undo(void* c, char** err) { return guarded(err, [&] { static_cast<Handle*>(c)->codec->undo(); return 0; }); }

// Bytes as they arrived. Returns how many messages are ready for cf_message.
CF_API int cf_feed(void* c, const uint8_t* p, size_t n, char** err) {
    return guarded(err, [&] {
        auto* h = static_cast<Handle*>(c);
        std::vector<Message> out;
        h->codec->feed(p, n, out);
        for (auto& m : out) h->ready.push_back(std::move(m));
        return int(h->ready.size());
    });
}
// 1: the next message (cf_free *role and *data). 0: none ready.
CF_API int cf_message(void* c, char** role, int* sequence, unsigned long long* counter, int* reply, uint8_t** data, size_t* n) {
    auto* h = static_cast<Handle*>(c);
    if (h->ready.empty()) return 0;
    Message m = std::move(h->ready.front()); h->ready.pop_front();
    *role = text(m.role); *sequence = m.sequence; *counter = m.counter; *reply = m.reply ? 1 : 0; *data = bytes(m.data); *n = m.data.size();
    return 1;
}

// What this end said since the last call (one line each), and what it is sending under ("role\tdriver=.. ..\n").
CF_API char* cf_said(void* c) { auto* h = static_cast<Handle*>(c); std::lock_guard<std::mutex> g(h->said_guard); std::string s; s.swap(h->said); return text(s); }
CF_API char* cf_running(void* c) {
    std::string s;
    for (auto& kv : static_cast<Handle*>(c)->codec->running()) s += kv.first + "\t" + kv.second + "\n";
    return text(s);
}
// sent, received, not opened, sent again, then [sent, received] of: protocol, encryption, not opened, again, received
CF_API void cf_counts(void* c, unsigned long long out[14]) {
    const Counts k = static_cast<Handle*>(c)->codec->counts();
    const unsigned long long v[14] = {k.sent, k.received, k.not_opened, k.sent_again, k.protocol[0], k.protocol[1], k.encryption[0], k.encryption[1],
                                      k.not_opened_calls[0], k.not_opened_calls[1], k.again[0], k.again[1], k.received_calls[0], k.received_calls[1]};
    std::memcpy(out, v, sizeof v);
}

// A setting read from its tuple, taken or ignored. `fields`: "name=value\n" for every field, check included.
// Returns "taken=0|1\nmoved=0|1\nwhy=...\nfingerprint=...\ndriver=...\nnumber=...\nprotection=...\nstep=...\n".
CF_API char* cf_take_setting(const char* role, const char* fields, const char* secrets, char** err) {
    try {
        const Taken t = take_setting(role ? role : "", fields_of(fields), secrets && *secrets ? secrets : DEFAULT_SECRETS);
        return text(std::string("taken=") + (t.taken ? "1" : "0") + "\nmoved=" + (t.moved ? "1" : "0") + "\nwhy=" + t.why + "\nfingerprint=" + t.fingerprint
                    + "\ndriver=" + t.setting.driver + "\nnumber=" + std::to_string(t.setting.number) + "\nprotection=" + t.setting.protection
                    + "\nstep=" + std::to_string(t.setting.step) + "\n");
    } catch (const std::exception& e) { if (err) *err = text(e.what()); return nullptr; }
}
// This machine's secret for a Role, as a program may know it: "own=0|1\nstep=N\nfingerprint=8 characters\n".
// The secret itself is never returned. Fails (err) when the machine holds none.
CF_API char* cf_secret(const char* role, const char* secrets, char** err) {
    try {
        std::string r(role ? role : ""); for (auto& ch : r) if (ch >= 'A' && ch <= 'Z') ch = char(ch + 32);
        const Secret s = read_secret(r, secrets && *secrets ? secrets : DEFAULT_SECRETS);
        return text(std::string("own=") + (s.own ? "1" : "0") + "\nstep=" + std::to_string(s.step) + "\nfingerprint=" + secret_fingerprint(s.value) + "\n");
    } catch (const std::exception& e) { if (err) *err = text(e.what()); return nullptr; }
}
// The check a setting for this Role must carry, made with this machine's secret for it at the setting's step.
CF_API char* cf_check_of(const char* role, const char* fields, const char* secrets, char** err) {
    try {
        const auto f = fields_of(fields);
        auto step = f.find("step");
        std::string r(role ? role : ""); for (auto& ch : r) if (ch >= 'A' && ch <= 'Z') ch = char(ch + 32);
        const Secret s = secret_at(r, step == f.end() ? 0 : std::strtol(step->second.c_str(), nullptr, 10), secrets && *secrets ? secrets : DEFAULT_SECRETS);
        return text(check_of(s.value, r, f));
    } catch (const std::exception& e) { if (err) *err = text(e.what()); return nullptr; }
}
