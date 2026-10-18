// kartram.cpp -- [KART_ON_THE_SESSION_V1] the kart game's memory, as a C interface (libkartram.so / kartram.dll) over
// this tree's own session client, frogram::Session: one permanent FNW1 connection per region, semantically
// compressed, the same client fdf-client uses. Python reaches it through ctypes (ribbit.py).
//
//   kr_open(host, port, api, &err)                    a session to the region at host:port (api: "/ram.php")
//   kr_write(h, s, v, i, bag_json, flags, &err)       posted: goes out, nothing is waited for. flags 1 (own): the tuple
//                                                     goes ({} written) when this session ends [OWNED_CELL_GOES_WITH_THE_SESSION_V1];
//                                                     flags 2 (permanent): filed, and back after the region restarts [PERMANENT_CELL_V1]
//   kr_read(h, s, v, i, after, wait_s, &err)          a family read (i "" = any instance); after>=0 with wait_s>0 is the
//                                                     trigger: held until something is written past `after`.
//                                                     Returns a JSON array of {id, variable, instance, bag}
//   kr_call(h, method, path, body, &err)              any request on the session (FD&F's op=rina); returns its JSON
//   kr_call_text(h, method, path, body, timeout_s, &err)  the same, answered as text (FD&F's held op=rina_block wait)
//   kr_refused(h)                                     how many posted writes the region refused so far
//   kr_close(h); kr_free(s)
// A failure returns NULL / -1 and sets *err (free it with kr_free). Nothing is retried here: the caller says it.
#include "frogram.hpp"
#include <cstring>
#include <string>
#ifdef _WIN32
#  define KR_API extern "C" __declspec(dllexport)
#else
#  define KR_API extern "C" __attribute__((visibility("default")))
#endif
using frogram::Json;

namespace {
struct KR { frogram::Session s; std::string api; KR(const char* h, int p, const char* a) : s(h, p, a), api(a) {} };
char* dupe(const std::string& x) { char* r = static_cast<char*>(malloc(x.size() + 1)); memcpy(r, x.c_str(), x.size() + 1); return r; }
void fail(char** err, const std::string& why) { if (err) *err = dupe(why); }
void dump(const Json& j, std::string& o) {
    switch (j.type) {
        case Json::Null: o += "null"; return;
        case Json::Bool: o += j.b ? "true" : "false"; return;
        case Json::Num: { char b[40]; snprintf(b, sizeof b, "%.17g", j.n); o += b; return; }
        case Json::Str: o += Json::quote(j.s); return;
        case Json::Arr: o += '['; for (size_t k = 0; k < j.a.size(); ++k) { if (k) o += ','; dump(j.a[k], o); } o += ']'; return;
        case Json::Obj: o += '{'; for (size_t k = 0; k < j.o.size(); ++k) { if (k) o += ','; o += Json::quote(j.o[k].first); o += ':'; dump(j.o[k].second, o); } o += '}'; return;
    }
}
std::string urlenc(const std::string& s) {
    static const char* hx = "0123456789ABCDEF"; std::string o;
    for (unsigned char c : s) { if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') o += char(c); else { o += '%'; o += hx[c >> 4]; o += hx[c & 15]; } }
    return o;
}
}  // namespace

KR_API void* kr_open(const char* host, int port, const char* api, char** err) {
    try { return new KR(host, port, api); } catch (const std::exception& e) { fail(err, std::string(host) + ":" + std::to_string(port) + ": " + e.what()); return nullptr; }
}
KR_API void kr_close(void* h) { delete static_cast<KR*>(h); }
KR_API void kr_free(char* s) { free(s); }
KR_API int kr_write(void* h, const char* s, const char* v, const char* i, const char* bag, int flags, char** err) {
    KR* k = static_cast<KR*>(h);
    try { k->s.post("POST", k->api + "?op=write", "{\"service\":" + Json::quote(s) + ",\"variable\":" + Json::quote(v) + ",\"instance\":" +
                    Json::quote(i) + ",\"bag\":" + bag + ((flags & 1) ? ",\"own\":true" : "") + ((flags & 2) ? ",\"permanent\":true" : "") + "}"); return 0; }
    catch (const std::exception& e) { fail(err, e.what()); return -1; }
}
KR_API char* kr_read(void* h, const char* s, const char* v, const char* i, long long after, double wait_s, char** err) {
    KR* k = static_cast<KR*>(h);
    std::string q = k->api + "?op=read&service=" + urlenc(s);
    if (*v) q += "&variable=" + urlenc(v);
    if (*i) q += "&instance=" + urlenc(i);
    if (after >= 0) q += "&after=" + std::to_string(after);
    if (wait_s > 0) { char b[32]; snprintf(b, sizeof b, "&wait_s=%.3f", wait_s); q += b; }
    try {
        Json o = k->s.call("GET", q, "", 15.0 + wait_s);
        const Json& rows = o["rows"].type == Json::Arr ? o["rows"] : o["cells"];
        std::string out = "["; bool first = true;
        for (auto& r : rows.a) {
            out += first ? "{" : ",{"; first = false;
            out += "\"id\":" + std::to_string(uint64_t(r["id"].n)) + ",\"variable\":" + Json::quote(r["variable"].s) +
                   ",\"instance\":" + Json::quote(r["instance"].s) + ",\"bag\":"; dump(r["bag"], out); out += "}";
        }
        return dupe(out + "]");
    } catch (const std::exception& e) { fail(err, e.what()); return nullptr; }
}
KR_API char* kr_call(void* h, const char* method, const char* path, const char* body, char** err) {
    KR* k = static_cast<KR*>(h);
    try { std::string out; dump(k->s.call(method, path, body ? body : ""), out); return dupe(out); }
    catch (const std::exception& e) { fail(err, e.what()); return nullptr; }
}
KR_API char* kr_call_text(void* h, const char* method, const char* path, const char* body, double timeout_s, char** err) {
    KR* k = static_cast<KR*>(h);
    try { return dupe(k->s.call_text(method, path, body ? body : "", timeout_s)); }
    catch (const std::exception& e) { fail(err, e.what()); return nullptr; }
}
KR_API long long kr_refused(void* h) { return (long long)static_cast<KR*>(h)->s.stats().posts_refused; }
