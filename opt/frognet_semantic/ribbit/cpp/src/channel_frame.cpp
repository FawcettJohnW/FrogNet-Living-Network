// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// channel_frame.cpp -- [THE_FRAME_IS_A_LIBRARY_V1] see channel_frame.hpp. Every rule here is core/channel_frame.py's
// (build channel-frame-v13-20261123f); the names in brackets are that file's. Where the two differ it is because this
// one has no socket and no thread of its own, and those places say so.
#include "channel_frame.hpp"

#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#ifndef _WIN32
#  include <fcntl.h>
#  include <sys/file.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

namespace channel_frame {
namespace {

const std::string SWITCH_PROTOCOL("SWP\x01", 4), SWITCH_ENCRYPTION("SWE\x01", 4), NOT_OPENED("NOT\x01", 4),
                  AGAIN("AGN\x01", 4), RECEIVED("RCV\x01", 4);
const std::string DEFAULT_SECRET = "default";
const std::string STEPPED = "FROGNET ROLE SECRET AT STEP ";
enum { NAMED, CHANGED, LOST, IN_STEP, TAKEN_UP, SEND_AGAIN, THEY_HAVE, WE_HAVE, ASK };

std::string lower(std::string s) { for (auto& c : s) if (c >= 'A' && c <= 'Z') c = char(c + 32); return s; }
Bytes bytes_of(const std::string& s) { return Bytes(s.begin(), s.end()); }
std::string hex(const uint8_t* p, size_t n) {
    static const char* d = "0123456789abcdef"; std::string out;
    for (size_t i = 0; i < n; ++i) { out += d[p[i] >> 4]; out += d[p[i] & 15]; }
    return out;
}
void varint(Bytes& out, uint64_t z) { while (z >= 0x80) { out.push_back(uint8_t(0x80 | (z & 0x7f))); z >>= 7; } out.push_back(uint8_t(z)); }
Bytes with_number(const std::string& call, uint64_t n) { Bytes b(call.begin(), call.end()); varint(b, n); return b; }
uint64_t number_in(const uint8_t* p, size_t n) {
    uint64_t z = 0; int shift = 0;
    for (size_t i = 0; i < n; ++i) { z |= uint64_t(p[i] & 0x7f) << shift; shift += 7; if (!(p[i] & 0x80)) return z; }
    throw Gone("a call's number does not end");
}
void be64(uint8_t out[8], uint64_t v) { for (int i = 7; i >= 0; --i) { out[i] = uint8_t(v); v >>= 8; } }
Bytes hmac(const EVP_MD* md, const Bytes& key, const std::string& text) {
    uint8_t out[EVP_MAX_MD_SIZE]; unsigned n = 0;
    static const uint8_t none = 0;
    if (!HMAC(md, key.empty() ? &none : key.data(), int(key.size()), reinterpret_cast<const uint8_t*>(text.data()), text.size(), out, &n))
        throw DoesNotOpen("this machine's libcrypto refused an HMAC");
    return Bytes(out, out + n);
}
uint64_t packet_id(uint64_t counter, int stream, bool reply) { return (counter << 9) | (uint64_t(stream) << 1) | (reply ? 1 : 0); }

// ---- the algorithms: this machine's libcrypto, as core/channel_protection.py reaches it
struct Protection { SealFn seal; OpenFn open; };
std::map<std::string, Protection>& protections() {
    static std::map<std::string, Protection> m = [] {
        std::map<std::string, Protection> p;
        auto aead = [](const EVP_CIPHER* (*which)()) {
            Protection pr;
            pr.seal = [which](const Bytes& key, const uint8_t nonce[12], const uint8_t aad[8], const uint8_t* d, size_t n) {
                Bytes out(n + TAG + 16); int got = 0, m2 = 0;
                EVP_CIPHER_CTX* c = EVP_CIPHER_CTX_new();
                bool ok = c && EVP_EncryptInit_ex(c, which(), nullptr, nullptr, nullptr) == 1
                          && EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_IVLEN, 12, nullptr) == 1
                          && EVP_EncryptInit_ex(c, nullptr, nullptr, key.data(), nonce) == 1
                          && EVP_EncryptUpdate(c, nullptr, &m2, aad, 8) == 1
                          && EVP_EncryptUpdate(c, out.data(), &got, d, int(n)) == 1
                          && EVP_EncryptFinal_ex(c, out.data() + got, &m2) == 1
                          && EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_GET_TAG, int(TAG), out.data() + got + m2) == 1;
                if (c) EVP_CIPHER_CTX_free(c);
                if (!ok) throw Gone("libcrypto refused to seal: the key, the nonce or the data");
                out.resize(size_t(got + m2) + TAG);
                return out;
            };
            pr.open = [which](const Bytes& key, const uint8_t nonce[12], const uint8_t aad[8], const uint8_t* d, size_t n, Bytes& out) {
                if (n < TAG) return false;
                out.resize(n - TAG + 16); int got = 0, m2 = 0;
                EVP_CIPHER_CTX* c = EVP_CIPHER_CTX_new();
                bool ok = c && EVP_DecryptInit_ex(c, which(), nullptr, nullptr, nullptr) == 1
                          && EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_IVLEN, 12, nullptr) == 1
                          && EVP_DecryptInit_ex(c, nullptr, nullptr, key.data(), nonce) == 1
                          && EVP_DecryptUpdate(c, nullptr, &m2, aad, 8) == 1
                          && EVP_DecryptUpdate(c, out.data(), &got, d, int(n - TAG)) == 1
                          && EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_AEAD_SET_TAG, int(TAG), const_cast<uint8_t*>(d + n - TAG)) == 1
                          && EVP_DecryptFinal_ex(c, out.data() + got, &m2) == 1;
                if (c) EVP_CIPHER_CTX_free(c);
                if (ok) out.resize(size_t(got + m2));
                return ok;
            };
            return pr;
        };
        p["aes-256-gcm"] = aead(EVP_aes_256_gcm);
        p["chacha20-poly1305"] = aead(EVP_chacha20_poly1305);
        return p;
    }();
    return m;
}

// ---- the protocols under the frame
struct Driver { std::string word; PackFn pack; UnpackFn unpack; };
struct Drivers { std::map<std::string, Driver> by_name; std::map<std::string, std::string> by_word; };

constexpr size_t PREFIX = 24;        // core/chorus.py
constexpr size_t SPAN = 4096;        // core/openqasm.py

uint64_t take_number(const uint8_t* d, size_t n, size_t& p, uint64_t pid, const char* whose) {
    uint64_t z = 0; int shift = 0;
    for (;;) {
        if (p >= n || shift > 35) throw Gone("packet " + std::to_string(pid) + " opened to something that is not " + whose);
        uint8_t c = d[p++]; z |= uint64_t(c & 0x7f) << shift; shift += 7;
        if (!(c & 0x80)) return z;
    }
}

Drivers& drivers() {
    static Drivers d = [] {
        Drivers r;
        auto add = [&r](const std::string& name, const std::string& word, PackFn pack, UnpackFn unpack) {
            r.by_name[name] = Driver{word, std::move(pack), std::move(unpack)}; r.by_word[word] = name;
        };
        // tcp: the message as it is. Its length and number are the frame's.
        add("tcp", std::string("TCP\x01", 4), [](DriverState&, uint64_t, const Bytes& m) { return m; },
            [](DriverState&, uint64_t, const uint8_t* p, size_t n) { return Bytes(p, p + n); });
        // chorus (core/chorus.py _frame_pack): a message's first PREFIX bytes cross as a difference against the
        // message before it for the same Role in the same direction.
        add("chorus", std::string("CHO\x03", 4),
            [](DriverState& st, uint64_t, const Bytes& m) {
                Bytes& was = st["first"]; if (was.empty()) was.assign(PREFIX, 0);
                size_t k = std::min(PREFIX, m.size()), nb = (k + 7) / 8;
                Bytes out(1 + nb, 0), diff; out[0] = uint8_t(k);
                for (size_t i = 0; i < k; ++i) if (m[i] != was[i]) { out[1 + i / 8] |= uint8_t(1 << (i % 8)); diff.push_back(m[i]); }
                std::copy(m.begin(), m.begin() + long(k), was.begin());
                out.insert(out.end(), diff.begin(), diff.end());
                out.insert(out.end(), m.begin() + long(k), m.end());
                return out;
            },
            [](DriverState& st, uint64_t pid, const uint8_t* d, size_t n) {
                Bytes& was = st["first"]; if (was.empty()) was.assign(PREFIX, 0);
                size_t k = n ? d[0] : PREFIX + 1, nb = (k + 7) / 8, ones = 0;
                if (k <= PREFIX && n >= 1 + nb) for (size_t i = 0; i < k; ++i) ones += (d[1 + i / 8] >> (i % 8)) & 1;
                if (k > PREFIX || n < 1 + nb + ones) throw Gone("packet " + std::to_string(pid) + " opened to something that is not chorus's");
                size_t at = 1 + nb; Bytes out(was.begin(), was.begin() + long(k));
                for (size_t i = 0; i < k; ++i) if ((d[1 + i / 8] >> (i % 8)) & 1) out[i] = d[at++];
                std::copy(out.begin(), out.end(), was.begin());
                out.insert(out.end(), d + at, d + n);
                return out;
            });
        // openqasm (core/openqasm.py): the whole message, up to SPAN bytes, as a difference against the one before.
        add("openqasm", std::string("OQ3\x02", 4),
            [](DriverState& st, uint64_t, const Bytes& f) {
                Bytes& was = st["was"];
                size_t m = std::min({f.size(), was.size(), SPAN}), i = 0;
                Bytes out; varint(out, m);
                while (i < m) {
                    size_t j = i;
                    while (j < m && f[j] == was[j]) ++j;
                    varint(out, j - i);
                    if (j >= m) break;
                    size_t k = j;
                    while (k < m && !(k + 3 <= m && f[k] == was[k] && f[k + 1] == was[k + 1] && f[k + 2] == was[k + 2])) ++k;
                    varint(out, k - j);
                    out.insert(out.end(), f.begin() + long(j), f.begin() + long(k));
                    i = k;
                }
                out.insert(out.end(), f.begin() + long(m), f.end());
                was.assign(f.begin(), f.begin() + long(std::min(f.size(), SPAN)));
                return out;
            },
            [](DriverState& st, uint64_t pid, const uint8_t* d, size_t n) {
                Bytes& was = st["was"]; size_t p = 0;
                uint64_t m = take_number(d, n, p, pid, "OpenQASM's");
                if (m > std::min(was.size(), SPAN))
                    throw Gone("packet " + std::to_string(pid) + " is a difference against " + std::to_string(m) + " bytes and this end holds " + std::to_string(was.size()));
                Bytes out;
                while (out.size() < m) {
                    uint64_t same = take_number(d, n, p, pid, "OpenQASM's");
                    size_t from = out.size(), to = std::min<size_t>(was.size(), from + same);
                    out.insert(out.end(), was.begin() + long(from), was.begin() + long(to));
                    if (out.size() >= m) break;
                    uint64_t k = take_number(d, n, p, pid, "OpenQASM's");
                    size_t take = std::min<size_t>(k, n - p);
                    out.insert(out.end(), d + p, d + p + take); p += take;
                    if (!same && !k) break;                  // nothing moved: the difference does not come to m
                }
                if (out.size() != m) throw Gone("packet " + std::to_string(pid) + ": its difference does not come to the " + std::to_string(m) + " bytes it says");
                out.insert(out.end(), d + p, d + n);
                was.assign(out.begin(), out.begin() + long(std::min(out.size(), SPAN)));
                return out;
            });
        return r;
    }();
    return d;
}

bool is_call(const std::string& w) { return w == SWITCH_PROTOCOL || w == SWITCH_ENCRYPTION || w == NOT_OPENED || w == AGAIN || w == RECEIVED; }

std::string read_file(const std::string& path, bool& there) {
    std::ifstream f(path, std::ios::binary);
    there = bool(f);
    std::ostringstream s; s << f.rdbuf();
    return s.str();
}
bool is_file(const std::string& path) {
#ifdef _WIN32
    std::ifstream f(path, std::ios::binary); return bool(f);
#else
    struct stat st; return ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
#endif
}

Secret read_secret_held(const std::string& role, const std::string& d) {
    bool own = !role.empty() && role != DEFAULT_SECRET && is_file(d + "/" + role);
    const std::string path = d + "/" + (own ? role : DEFAULT_SECRET);
    bool there = false;
    std::string raw = read_file(path, there);
    if (!there) throw DoesNotOpen("this machine holds no secret for '" + role + "' and no default secret (" + path + "): it does not carry that Role's sealed traffic");
    long step = 0;
    if (own && raw.compare(0, STEPPED.size(), STEPPED) == 0) {
        size_t nl = raw.find('\n');
        const std::string line = raw.substr(STEPPED.size(), nl == std::string::npos ? std::string::npos : nl - STEPPED.size());
        char* end = nullptr; step = std::strtol(line.c_str(), &end, 10);
        if (line.empty() || *end) throw DoesNotOpen("the secret for '" + role + "' in " + path + " does not say its step");
        raw = nl == std::string::npos ? "" : raw.substr(nl + 1);
    }
    if (raw.size() < 32) throw DoesNotOpen("the secret for '" + role + "' in " + path + " is " + std::to_string(raw.size()) + " bytes; a secret is at least 32");
    return Secret{bytes_of(raw), step, own};
}

}  // namespace

// ------------------------------------------------------------------------------------------ secrets, keys, checks
Secret read_secret(const std::string& role, const std::string& dir) {
#ifdef _WIN32
    return read_secret_held(role, dir);
#else
    // [A_SECRET_IS_NOT_READ_WHILE_IT_IS_BEING_REPLACED_V1] a read holds the directory shared while it has the file open
    int dfd = ::open(dir.c_str(), O_RDONLY);
    if (dfd < 0) throw DoesNotOpen("this machine holds no secret for '" + role + "' and no default secret (" + dir + "): it does not carry that Role's sealed traffic");
    ::flock(dfd, LOCK_SH);
    try { Secret s = read_secret_held(role, dir); ::close(dfd); return s; }
    catch (...) { ::close(dfd); throw; }
#endif
}

Bytes next_secret(const Bytes& secret, const std::string& role, long step) {
    return hmac(EVP_sha384(), secret, "next\n" + role + "\n" + std::to_string(step));
}

Secret secret_at(const std::string& role, long step, const std::string& dir) {
    Secret s = read_secret(role, dir);
    if (!s.own || step < 0) return s;
    if (step < s.step)
        throw DoesNotOpen("step " + std::to_string(step) + " is lower than the step this machine's secret for '" + role + "' is at (" + std::to_string(s.step) + ")");
    Bytes v = s.value;
    for (long k = s.step + 1; k <= step; ++k) v = next_secret(v, role, k);
    return Secret{v, s.step, true};
}

bool keep_secret(const std::string& role, const Bytes& secret, long step, const std::string& dir) {
#ifdef _WIN32
    (void)role; (void)secret; (void)step; (void)dir;
    throw Gone("moving a Role's secret file forward is not built for Windows: the secret is worked out in memory there");
#else
    const std::string path = dir + "/" + role, tmp = path + ".next." + std::to_string(::getpid());
    int dfd = ::open(dir.c_str(), O_RDONLY);
    if (dfd < 0) throw DoesNotOpen("no secrets directory " + dir);
    ::flock(dfd, LOCK_EX);                           // reading the file's step and replacing the file is one act on a machine
    int old = -1; bool wrote = false;
    try {
        if (read_secret_held(role, dir).step < step) {
            old = ::open(path.c_str(), O_RDWR);
            struct stat st; if (old < 0 || ::fstat(old, &st) != 0) throw Gone("the secret file " + path + " cannot be opened to be replaced");
            int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
            if (fd < 0) throw Gone("cannot write " + tmp);
            const std::string head = STEPPED + std::to_string(step) + "\n";
            bool ok = ::write(fd, head.data(), head.size()) == ssize_t(head.size())
                      && ::write(fd, secret.data(), secret.size()) == ssize_t(secret.size()) && ::fsync(fd) == 0;
            ::close(fd);
            if (!ok || ::rename(tmp.c_str(), path.c_str()) != 0) throw Gone("the secret for '" + role + "' could not be written at step " + std::to_string(step));
            const std::string zeros(size_t(st.st_size), '\0');          // erase the secret it replaced
            ::lseek(old, 0, SEEK_SET);
            if (::write(old, zeros.data(), zeros.size()) != ssize_t(zeros.size())) throw Gone("the earlier secret for '" + role + "' could not be erased");
            ::fsync(old); ::fsync(dfd);
            wrote = true;
        }
    } catch (...) { if (old >= 0) ::close(old); ::close(dfd); throw; }
    if (old >= 0) ::close(old);
    ::close(dfd);
    return wrote;
#endif
}

Bytes key_for(const Bytes& secret, const std::string& fnid, const std::string& role, uint64_t number) {
    Bytes k = hmac(EVP_sha384(), secret, "frognet channel key\n" + fnid + "\n" + role + "\n" + std::to_string(number));
    k.resize(32);                                    // [KEY_AND_CHECK_USE_SHA_384_V1] the first 32 of its 48 bytes
    return k;
}

std::string secret_fingerprint(const Bytes& secret) {
    Bytes h = hmac(EVP_sha256(), secret, "frognet channel secret fingerprint");
    return hex(h.data(), 4);
}

std::string check_text(const std::string& role, const std::map<std::string, std::string>& fields) {
    std::string text = lower(role);
    for (auto& kv : fields) if (kv.first != "check" && kv.first != "ts") text += "\n" + kv.first + "=" + kv.second;
    return text;
}

std::string check_of(const Bytes& secret, const std::string& role, const std::map<std::string, std::string>& fields) {
    Bytes h = hmac(EVP_sha384(), secret, check_text(role, fields));
    return hex(h.data(), h.size());
}

std::string fnid_here() {
    const char* named = std::getenv("FROGNET_GUID_FILE");
    const std::string path = named && *named ? named : "/etc/fnid";
    bool there = false;
    std::string s = read_file(path, there);
    if (!there) throw DoesNotOpen("this node has no FNID (" + path + "): it cannot compute the key it sends a sealed Role under");
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    s = a == std::string::npos ? "" : s.substr(a, b - a + 1);
    if (s.empty() || s.size() > 255) throw DoesNotOpen("the FNID in " + path + " is " + std::to_string(s.size()) + " bytes; it is 1 to 255");
    return s;
}

Taken take_setting(const std::string& role_as_named, const std::map<std::string, std::string>& fields, const std::string& dir) {
    const std::string role = lower(role_as_named);
    auto get = [&fields](const char* k) { auto it = fields.find(k); return it == fields.end() ? std::string() : it->second; };
    Taken t;
    if (!get("driver").empty()) t.setting.driver = get("driver");
    if (!get("protection").empty()) t.setting.protection = get("protection");
    t.setting.number = std::strtoull(get("number").c_str(), nullptr, 10);
    t.setting.step = std::strtol(get("step").c_str(), nullptr, 10);
    Secret s;
    try { s = secret_at(role, t.setting.step, dir); }
    catch (const DoesNotOpen& e) {
        if (std::string(e.what()).rfind("step ", 0) == 0) { t.why = std::string(e.what()) + ": a setting does not take a Role's secret back"; return t; }
        t.taken = true;                              // no secret here: nothing to verify with, nothing to protect
        return t;
    }
    if (get("check") != check_of(s.value, role, fields)) {
        t.why = "its check does not verify against this machine's secret for " + role + (s.own ? " at step " + std::to_string(t.setting.step) : "") + "; nothing is written";
        return t;
    }
#ifndef _WIN32
    if (s.own && t.setting.step > s.step) t.moved = keep_secret(role, s.value, t.setting.step, dir);
#endif
    t.taken = true;
    t.fingerprint = secret_fingerprint(s.value);
    return t;
}

void register_protection(const std::string& name, SealFn seal, OpenFn open) { protections()[name] = Protection{std::move(seal), std::move(open)}; }
bool protection_known(const std::string& name) { return protections().count(name) != 0; }

void register_driver(const std::string& name, const char word[4], PackFn pack, UnpackFn unpack) {
    const std::string w(word, 4);
    Drivers& d = drivers();
    auto it = d.by_word.find(w);
    if (is_call(w) || (it != d.by_word.end() && it->second != name)) throw Gone("the protocol '" + name + "' cannot have that word");
    d.by_name[name] = Driver{w, std::move(pack), std::move(unpack)};
    d.by_word[w] = name;
}
bool driver_known(const std::string& name) { return drivers().by_name.count(name) != 0; }

// ------------------------------------------------------------------------------------------------------ the Codec
Codec::Codec(bool opener, std::string far, std::string fnid, std::string secrets, Say say)
    : opener_(opener), far_(std::move(far)), secrets_(std::move(secrets)), fnid_(std::move(fnid)), say_(std::move(say)) {
    if (RAND_bytes(nonce_head_, 8) != 1) throw Gone("this machine's libcrypto gave no random bytes for the nonce");
}

void Codec::setting(const std::string& role, std::optional<Setting> s) {
    {
        std::lock_guard<std::mutex> g(guard_);
        settings_[role] = std::move(s);
        handed_.push_back(Item{CHANGED, 0, 0, false, role});
    }
    if (wake_) wake_();
}

std::optional<Setting> Codec::setting_of(const std::string& role) {
    std::lock_guard<std::mutex> g(guard_);
    auto it = settings_.find(role);
    return it == settings_.end() ? std::nullopt : it->second;
}

void Codec::hand(Item i) {
    const bool owes = i.kind != NAMED && i.kind != TAKEN_UP && i.kind != THEY_HAVE && i.kind != LOST;
    { std::lock_guard<std::mutex> g(guard_); handed_.push_back(std::move(i)); }
    if (owes && wake_) wake_();
}

const std::string& Codec::mine() { if (fnid_.empty()) fnid_ = fnid_here(); return fnid_; }

static std::pair<uint64_t, std::string> wanted(const std::optional<Setting>& s) {
    if (!s) return {0, DEFAULT_PROTECTION};
    return {s->number, s->protection.empty() ? std::string(DEFAULT_PROTECTION) : s->protection};
}

// The key for this number, under this algorithm. Nothing of the key before is kept. The secret is the Role's at the
// step of the setting this process holds (core/channel_setting.py secret_in_use), worked out in memory.
void Codec::rekey(Side& s, uint64_t number, const std::string& protection) {
    if (!protection_known(protection)) throw DoesNotOpen("the channel of '" + s.role + "' names the algorithm '" + protection + "', which this machine has not registered");
    s.protection = protection; s.number = number; s.key.clear(); s.sealed = false;
    if (!number) return;
    auto held = setting_of(s.role);
    s.key = key_for(secret_at(lower(s.role), held ? held->step : -1, secrets_).value, s.fnid, s.role, number);
    s.sealed = true;
}

// ---- the wire: one frame appended to the unit ([_put])
void Codec::put(Bytes& unit, uint64_t pid, const Bytes& body) {
    const uint64_t counter = pid >> 9; const int stream = int((pid >> 1) & 0xff); const bool reply = pid & 1;
    const bool same = stream == o_.last;
    uint64_t x = 0;
    if (counter) {
        const int64_t d = int64_t(counter) - int64_t(o_.count[stream]) - 1;
        x = 1 + (d >= 0 ? uint64_t(d) << 1 : (uint64_t(-d) << 1) - 1);
        o_.count[stream] = counter;
    }
    const size_t was = unit.size();
    varint(unit, body.size());
    varint(unit, (x << 2) | (reply ? 2 : 0) | (same ? 1 : 0));
    if (!same) unit.push_back(uint8_t(stream));
    unit.insert(unit.end(), body.begin(), body.end());
    o_.last = stream;
    o_.counts.sent += unit.size() - was;
}

Bytes Codec::seal(Side& s, uint64_t pid, const Bytes& data) {
    if (!s.sealed) return data;
    // 12 bytes never used twice under any key: 8 drawn for this connection (and again when the count runs out) and a
    // count. An undone unit's nonce is not used again either: undo() does not put the count back.
    if (++nonce_n_ == 0) { if (RAND_bytes(nonce_head_, 8) != 1) throw Gone("no random bytes for the nonce"); nonce_n_ = 1; }
    uint8_t nonce[12], aad[8];
    std::memcpy(nonce, nonce_head_, 8);
    nonce[8] = uint8_t(nonce_n_ >> 24); nonce[9] = uint8_t(nonce_n_ >> 16); nonce[10] = uint8_t(nonce_n_ >> 8); nonce[11] = uint8_t(nonce_n_);
    be64(aad, pid);
    Bytes out(nonce, nonce + 12);
    Bytes sealed = protections()[s.protection].seal(s.key, nonce, aad, data.data(), data.size());
    out.insert(out.end(), sealed.begin(), sealed.end());
    return out;
}

static const Driver& driver_named(const std::optional<Setting>& s, const std::string& role) {
    const std::string name = s && !s->driver.empty() ? s->driver : "tcp";
    auto it = drivers().by_name.find(name);
    if (it == drivers().by_name.end()) throw Gone("the channel of '" + role + "' names the protocol '" + name + "', which this machine has not registered");
    return it->second;
}

static std::string running_of(const std::string& word, uint64_t number, const std::string& protection, bool sealed) {
    auto it = drivers().by_word.find(word);
    return "driver=" + (it == drivers().by_word.end() ? std::string() : it->second) + " number=" + std::to_string(number)
           + " protection=" + (sealed ? protection : std::string()) + " sealed=" + (sealed ? "true" : "false");
}

// This end's sending side for a Role, named on the connection before its first frame ([_out_side]).
std::pair<int, Codec::Side*> Codec::out_side(Bytes& unit, const std::string& role, int stream) {
    if (stream < 0) {
        auto it = o_.stream.find(role);
        if (it == o_.stream.end()) {
            if (!opener_) throw Gone("this end did not open the connection to " + far_ + " and has no sequence for '" + role + "'");
            if (int(o_.stream.size()) >= STREAMS) throw Gone("the connection to " + far_ + " already carries " + std::to_string(STREAMS) + " Roles");
            it = o_.stream.emplace(role, int(o_.stream.size())).first;
        }
        stream = it->second;
    }
    Side& side = o_.side[stream];
    if (!side.named) {
        side.role = role;
        const auto s = setting_of(role);
        const auto w = wanted(s);
        // [A_SEQUENCE_NAMED_UNSEALED_SAYS_WHO_SENDS_IT_V1] the name always says who sends
        try { side.fnid = mine(); } catch (const DoesNotOpen&) { if (w.first) throw; side.fnid.clear(); }
        rekey(side, w.first, w.second);
        side.word = driver_named(s, role).word;
        Bytes body(9); be64(body.data(), side.number); body[8] = uint8_t(side.fnid.size());
        body.insert(body.end(), side.fnid.begin(), side.fnid.end());
        body.insert(body.end(), role.begin(), role.end());
        put(unit, packet_id(0, stream, false), body);
        side.named = true;
        say("to " + far_ + ", sequence " + std::to_string(stream) + " is '" + role + "': " + running_of(side.word, side.number, side.protection, side.sealed));
    }
    return {stream, &side};
}

void Codec::call(Bytes& unit, int stream, Side& s, const Bytes& data) {
    const uint64_t pid = packet_id(0, stream, true);
    put(unit, pid, seal(s, pid, data));
}

// Bring this end's sending for a Role to its setting: one call for a change of protocol, one for a change of
// encryption. Each call goes out the old way; what follows is new ([_catch_up]).
void Codec::catch_up(Bytes& unit, int stream, Side& s) {
    const auto held = setting_of(s.role);
    const std::string& word = driver_named(held, s.role).word;
    if (word != s.word) {
        call(unit, stream, s, bytes_of(SWITCH_PROTOCOL));
        o_.counts.protocol[0]++;
        const std::string was = drivers().by_word[s.word];
        s.word = word; s.state.clear();
        say("to " + far_ + " for '" + s.role + "': SWITCH PROTOCOL sent; " + was + " until here, " + drivers().by_word[word] + " from here");
    }
    const auto w = wanted(held);
    if (w.first != s.number || (w.first && w.second != s.protection)) {
        call(unit, stream, s, bytes_of(SWITCH_ENCRYPTION));
        o_.counts.encryption[0]++;
        if (w.first && s.fnid.empty()) s.fnid = mine();
        rekey(s, w.first, w.second);
        s.state.clear();
        call(unit, stream, s, with_number(AGAIN, s.base + s.kept.size()));   // the first thing under the new key says where this end is
        o_.counts.again[0]++;
        say("to " + far_ + " for '" + s.role + "': SWITCH ENCRYPTION sent; now " + running_of(s.word, s.number, s.protection, s.sealed));
    }
}

void Codec::message(Bytes& unit, Side& s, uint64_t pid, const Bytes& m) {
    Bytes data(s.word.begin(), s.word.end());
    const Bytes packed = drivers().by_name[drivers().by_word[s.word]].pack(s.state, pid, m);
    data.insert(data.end(), packed.begin(), packed.end());
    put(unit, pid, seal(s, pid, data));
}

void Codec::ask_again(Bytes& unit, int stream) {
    auto first = o_.parked.find(stream); auto there = o_.in_role.find(stream);
    if (first == o_.parked.end() || there == o_.in_role.end()) return;
    auto os = out_side(unit, there->second, stream);
    catch_up(unit, os.first, *os.second);
    call(unit, os.first, *os.second, with_number(NOT_OPENED, first->second));
    o_.asked.insert(stream);
    o_.counts.not_opened_calls[0]++;
    say("to " + far_ + " for '" + there->second + "': NOT OPENED sent: its messages from number " + std::to_string(first->second) + " on did not open here; send them again");
}

// What the reader, or whoever changed a setting, handed the writer: taken in the order it was handed. None of this
// puts anything on the connection, so none of it is undone.
void Codec::take_handed() {
    std::deque<Item> items;
    { std::lock_guard<std::mutex> g(guard_); items.swap(handed_); }
    for (auto& i : items) {
        switch (i.kind) {
        case NAMED: o_.in_role[i.stream] = i.role; if (!opener_) o_.stream.emplace(i.role, i.stream); break;
        case CHANGED: o_.owed.push_back(Owed{CHANGED, 0, 0, false, i.role}); break;
        case LOST: o_.parked.emplace(i.stream, i.n); break;
        case IN_STEP: if (i.flag || !o_.asked.count(i.stream)) o_.owed.push_back(Owed{ASK, i.stream, 0, false, ""}); break;
        case TAKEN_UP: o_.parked.erase(i.stream); o_.asked.erase(i.stream); break;
        case SEND_AGAIN: o_.owed.push_back(Owed{SEND_AGAIN, i.stream, i.n, false, ""}); break;
        case WE_HAVE: o_.owed.push_back(Owed{WE_HAVE, i.stream, i.n, false, ""}); break;
        case THEY_HAVE: {
            auto it = o_.side.find(i.stream);
            // [A_REPEAT_ONCE_ANNOUNCED_IS_SENT_WHOLE_V1] AGAIN <n> tells the far end that every message from n follows,
            // and it counts the ones it already has to skip them. So what a repeat under way has still to send is
            // kept, whatever the far end has said since that it has: sending fewer would make it skip new messages.
            uint64_t have = i.n;
            for (const Owed& w : o_.owed) if (w.kind == SEND_AGAIN && w.started && w.stream == i.stream) have = std::min(have, w.n);
            if (it != o_.side.end() && have > it->second.base) {
                Side& s = it->second;
                const uint64_t gone = std::min<uint64_t>(have - s.base, s.kept.size());
                s.kept.erase(s.kept.begin(), s.kept.begin() + long(gone));
                s.base += gone;
            }
            break; }
        }
    }
}

// One owed thing into the unit. Returns false when the thing at the front is a message being sent again and `calls_only`.
bool Codec::owed_into(Bytes& unit, bool calls_only) {
    while (!o_.owed.empty()) {
        Owed e = o_.owed.front();
        if (e.kind == CHANGED) {
            o_.owed.pop_front();
            for (auto& kv : o_.side) if (kv.second.named && kv.second.role == e.role) catch_up(unit, kv.first, kv.second);
            std::vector<int> parked;
            for (auto& kv : o_.parked) parked.push_back(kv.first);
            for (int stream : parked) { auto r = o_.in_role.find(stream); if (r != o_.in_role.end() && r->second == e.role) ask_again(unit, stream); }
        } else if (e.kind == ASK) {
            o_.owed.pop_front();
            ask_again(unit, e.stream);
        } else if (e.kind == WE_HAVE) {
            o_.owed.pop_front();
            auto r = o_.in_role.find(e.stream);
            if (r != o_.in_role.end()) {
                auto os = out_side(unit, r->second, e.stream);
                catch_up(unit, os.first, *os.second);
                call(unit, os.first, *os.second, with_number(RECEIVED, e.n));
                o_.counts.received_calls[0]++;
            }
        } else {                                              // SEND_AGAIN: the far end said NOT OPENED from e.n
            auto it = o_.side.find(e.stream);
            if (it == o_.side.end() || !it->second.named) { o_.owed.pop_front(); continue; }
            Side& s = it->second;
            if (!e.started) {
                // Asked twice for the same loss, the far end may have taken the first repeat and said RECEIVED before
                // this end comes to the second ask: what it asks for from below `base` it has since said it has.
                if (e.n < s.base) { e.n = s.base; o_.owed.front().n = e.n; }
                if (e.n > s.base + s.kept.size())
                    throw Gone(far_ + " asks for '" + s.role + "'s messages again from number " + std::to_string(e.n) + " and this end holds "
                               + std::to_string(s.base) + " to " + std::to_string(s.base + s.kept.size()));
                catch_up(unit, e.stream, s);
                s.state.clear();
                call(unit, e.stream, s, with_number(AGAIN, e.n));
                o_.counts.again[0]++;
                say("to " + far_ + " for '" + s.role + "': " + std::to_string(s.base + s.kept.size() - e.n) + " message(s) to send again, from number "
                    + std::to_string(e.n) + ", under " + running_of(s.word, s.number, s.protection, s.sealed));
                o_.owed.front().started = true;
                if (e.n >= s.base + s.kept.size()) o_.owed.pop_front();
                return true;                                  // the call is a unit of its own; the messages follow, one a unit
            }
            if (calls_only) return false;
            if (e.n < s.base) throw Gone("a repeat to " + far_ + " for '" + s.role + "' is at message " + std::to_string(e.n) + " and this end keeps them only from " + std::to_string(s.base));
            if (e.n >= s.base + s.kept.size()) { o_.owed.pop_front(); continue; }
            const Kept& k = s.kept[e.n - s.base];
            message(unit, s, k.pid, *k.message);
            o_.counts.sent_again++;
            o_.owed.front().n = e.n + 1;
            if (e.n + 1 >= s.base + s.kept.size()) o_.owed.pop_front();
            return true;
        }
        if (!unit.empty() && !calls_only) return true;        // next(): one owed thing a unit
    }
    return true;
}

bool Codec::next(Bytes& unit) {
    take_handed();
    unit.clear();
    if (o_.owed.empty()) return false;
    before_ = o_; can_undo_ = true;
    owed_into(unit, false);
    if (unit.empty()) { can_undo_ = false; return false; }    // what was owed needed nothing on the connection
    return true;
}

bool Codec::one(Bytes& unit, const std::string& role, int stream, uint64_t counter, bool reply, const Bytes& m) {
    take_handed();
    unit.clear();
    before_ = o_; can_undo_ = true;
    if (!owed_into(unit, true) || (!o_.owed.empty() && o_.owed.front().kind == SEND_AGAIN)) {
        o_ = before_; can_undo_ = false; unit.clear();        // messages are being sent again: next() first
        return false;
    }
    auto os = out_side(unit, role, stream);
    Side& s = *os.second;
    if (!reply) counter = ++s.count;
    catch_up(unit, os.first, s);
    const uint64_t pid = packet_id(counter, os.first, reply);
    auto kept = std::make_shared<const Bytes>(m);
    s.kept.push_back(Kept{pid, reply, kept});                 // until the far end says it has it
    message(unit, s, pid, m);
    return true;
}

bool Codec::pack(const std::string& role, const Bytes& m, Bytes& unit) { return one(unit, role, -1, 0, false, m); }
bool Codec::pack_reply(const std::string& role, int sequence, uint64_t counter, const Bytes& m, Bytes& unit) { return one(unit, role, sequence, counter, true, m); }

void Codec::undo() {
    if (!can_undo_) throw Gone("undo() with no unit to take back");
    o_ = before_; can_undo_ = false;
}

std::map<std::string, std::string> Codec::running() {
    std::map<std::string, std::string> r;
    for (auto& kv : o_.side) if (kv.second.named) r[kv.second.role] = running_of(kv.second.word, kv.second.number, kv.second.protection, kv.second.sealed);
    return r;
}

Counts Codec::counts() const {
    Counts c = o_.counts;
    c.received = received_; c.not_opened = not_opened_;
    c.protocol[1] = calls_in_[0]; c.encryption[1] = calls_in_[1]; c.not_opened_calls[1] = calls_in_[2]; c.again[1] = calls_in_[3]; c.received_calls[1] = calls_in_[4];
    return c;
}

// ------------------------------------------------------------------------------------------------- receiving
void Codec::rekey_in(Side& s, uint64_t number, const std::string& protection) {
    s.owed = false;
    try { rekey(s, number, protection); }
    catch (const DoesNotOpen& e) {
        say("from " + far_ + " for '" + s.role + "': " + e.what() + "; its frames do not open here");
        s.number = number; s.key.clear(); s.sealed = false;
    }
}

// The data of a frame, under the one key and algorithm this side holds. There is no other key to try ([_open]).
bool Codec::open(Side& s, uint64_t pid, const Bytes& body, Bytes& data) {
    for (int attempt = 0; attempt < 2; ++attempt) {
        if (!s.sealed) {
            const std::string w(body.begin(), body.begin() + long(std::min<size_t>(4, body.size())));
            if (w.size() == 4 && (is_call(w) || drivers().by_word.count(w))) { data = body; return true; }
        } else if (body.size() >= 12 + TAG) {
            uint8_t aad[8]; be64(aad, pid);
            if (protections()[s.protection].open(s.key, body.data(), aad, body.data() + 12, body.size() - 12, data)) return true;
        }
        if (attempt || !(s.owed || s.lost)) break;
        const auto w = wanted(setting_of(s.role));
        if (w.first == s.number && (w.first ? w.second : std::string()) == (s.number ? s.protection : std::string())) break;
        rekey_in(s, w.first, w.second);                       // this end's setting has arrived since
    }
    return false;
}

bool Codec::take(uint64_t& pid, Bytes& body) {
    size_t p = at_;
    auto number = [&](uint64_t& z) {
        z = 0; int shift = 0;
        for (;;) {
            if (p >= buf_.size()) return false;
            const uint8_t b = buf_[p++]; z |= uint64_t(b & 0x7f) << shift; shift += 7;
            if (!(b & 0x80)) return true;
            if (shift > 70) throw Gone("a number from " + far_ + " does not end: the far end does not speak this frame");
        }
    };
    uint64_t n = 0, z = 0;
    if (!number(n) || !number(z)) return false;
    if (n > MAX_FRAME) throw Gone("a frame from " + far_ + " gives its length as " + std::to_string(n) + ": the far end does not speak this frame");
    int stream = last_in_;
    if (!(z & 1)) { if (p >= buf_.size()) return false; stream = buf_[p++]; }
    if (buf_.size() - p < n) return false;
    body.assign(buf_.begin() + long(p), buf_.begin() + long(p + n));
    p += n;
    received_ += p - at_; at_ = p;
    last_in_ = stream;
    uint64_t x = z >> 2, counter = 0;
    if (x) {
        x -= 1;
        counter = uint64_t(int64_t(count_in_[stream]) + 1 + ((x & 1) ? -int64_t((x + 1) >> 1) : int64_t(x >> 1)));
        count_in_[stream] = counter;
    }
    pid = packet_id(counter, stream, (z & 2) != 0);
    return true;
}

// One frame taken in: a name, a call, or a message ([_one]).
void Codec::frame(uint64_t pid, const Bytes& body, std::vector<Message>& out) {
    const uint64_t counter = pid >> 9; const int stream = int((pid >> 1) & 0xff); const bool reply = pid & 1;
    if (counter == 0 && !reply) {                             // the first frame of a sequence, this direction
        if (body.size() < 9 || body.size() < size_t(9) + body[8]) throw Gone("a sequence's first frame from " + far_ + " is " + std::to_string(body.size()) + " bytes");
        uint64_t number = 0; for (int i = 0; i < 8; ++i) number = (number << 8) | body[size_t(i)];
        Side s;
        s.fnid.assign(body.begin() + 9, body.begin() + 9 + body[8]);
        s.role.assign(body.begin() + 9 + body[8], body.end());
        s.named = true;
        Side& side = in_[stream] = s;
        hand(Item{NAMED, stream, 0, false, side.role});
        rekey_in(side, number, wanted(setting_of(side.role)).second);
        say("from " + far_ + ", sequence " + std::to_string(stream) + " is '" + side.role + "', from node " + (side.fnid.empty() ? "(not said)" : side.fnid)
            + (side.sealed ? ", sealed with " + side.protection : ", not sealed"));
        return;
    }
    auto found = in_.find(stream);
    if (found == in_.end()) throw Gone("a frame from " + far_ + " on sequence " + std::to_string(stream) + ", which nothing has named");
    Side& side = found->second;
    Bytes data;
    if (!open(side, pid, body, data)) {
        // Not sealed here, no switch owed, this end's setting not sealed, and what arrived is not a plain frame. From a
        // far end nothing of whose has ever opened, that is a far end that does not speak this frame. From one whose
        // frames have opened, it is a sender one setting behind this end: this end has already read "sealed" and then
        // "not sealed" while the sender's switch to sealed was still on its way. It goes back for a retry like any
        // frame that does not open ([TWO_SETTINGS_AHEAD_IS_NOT_A_STRANGER_V1]; core/channel_frame.py ends the
        // connection there, and its replacement starts from the setting).
        if (!side.number && !side.owed && !wanted(setting_of(side.role)).first && !side.spoke)
            throw Gone("data from " + far_ + " for '" + side.role + "', which is not sealed, begins with no registered protocol's word");
        // A frame did not open. It is not taken, and nothing after it on this sequence is, until the sender starts again.
        not_opened_++;
        if (!side.lost) {
            side.lost = true; side.state.clear();
            say("from " + far_ + " for '" + side.role + "': a frame did not open. It goes back for a retry: nothing of this sequence is taken from message number "
                + std::to_string(side.got) + " until the sender starts again");
        }
        hand(Item{LOST, stream, side.got, false, ""});
        return;
    }
    side.spoke = true;
    if (counter == 0) {                                       // a call
        const std::string c(data.begin(), data.begin() + long(std::min<size_t>(4, data.size())));
        if (c == SWITCH_PROTOCOL && data.size() == 4) {
            calls_in_[0]++; side.state.clear();
            say("from " + far_ + " for '" + side.role + "': SWITCH PROTOCOL read");
        } else if (c == SWITCH_ENCRYPTION && data.size() == 4) {
            calls_in_[1]++;
            const auto w = wanted(setting_of(side.role));
            if (w.first != side.number || (w.first && w.second != side.protection)) {
                rekey_in(side, w.first, w.second);
                say("from " + far_ + " for '" + side.role + "': SWITCH ENCRYPTION read; this end's setting says " + (side.sealed ? "sealed with " + side.protection : std::string("not sealed")));
            } else {
                side.owed = true;                             // the sender's setting changed and this end's has not yet
                say("from " + far_ + " for '" + side.role + "': SWITCH ENCRYPTION read before this end's setting changed; what follows goes back for a retry until it has");
            }
        } else if (c == AGAIN) {
            calls_in_[3]++;
            const uint64_t first = number_in(data.data() + 4, data.size() - 4);
            side.state.clear();
            if (first > side.got) { if (side.lost) hand(Item{IN_STEP, stream, 0, true, ""}); }
            else if (side.lost || first < side.got) {
                side.lost = false; side.skip = side.got - first;
                hand(Item{TAKEN_UP, stream, 0, false, ""});
                say("from " + far_ + " for '" + side.role + "': the sender starts again from message number " + std::to_string(first) + "; this end has " + std::to_string(side.got));
            }
        } else if (c == NOT_OPENED) {
            calls_in_[2]++;
            hand(Item{SEND_AGAIN, stream, number_in(data.data() + 4, data.size() - 4), false, ""});
        } else if (c == RECEIVED) {
            calls_in_[4]++;
            hand(Item{THEY_HAVE, stream, number_in(data.data() + 4, data.size() - 4), false, ""});
        } else throw Gone("a call from " + far_ + " for '" + side.role + "' that this end does not know");
        return;
    }
    // [A_SEALED_ROLE_TAKES_NO_UNSEALED_MESSAGE_V1] (this library's; core/channel_frame.py v13 has no such rule.) A
    // sender names its own number, and one that names 0 sends unsealed. When the setting THIS end holds for the Role
    // is sealed, an unsealed message is not what the key this end holds would have made: it is not taken and goes
    // back for a retry like any frame that does not open. A sender whose setting is only late switches, is asked, and
    // sends it again sealed, so nothing is lost; a sender with no secret is never taken. Calls are still read
    // unsealed: the call that says "I switch" goes out the old way.
    if (!side.sealed && wanted(setting_of(side.role)).first) {
        not_opened_++;
        if (!side.lost) {
            side.lost = true; side.state.clear();
            say("from " + far_ + " for '" + side.role + "': an UNSEALED message, and this end's setting for the Role is sealed. It is not taken: nothing of "
                "this sequence is, from message number " + std::to_string(side.got) + ", until the sender starts again under the key");
        }
        hand(Item{LOST, stream, side.got, false, ""});
        return;
    }
    if (side.lost) { hand(Item{IN_STEP, stream, 0, false, ""}); return; }   // it opens, and it is from before the sender started again
    const std::string word(data.begin(), data.begin() + long(std::min<size_t>(4, data.size())));
    auto name = drivers().by_word.find(word);
    if (word.size() != 4 || name == drivers().by_word.end()) throw Gone("data from " + far_ + " for '" + side.role + "' begins with no registered protocol's word: refused");
    if (word != side.word) { side.word = word; side.state.clear(); }
    Message m;
    m.data = drivers().by_name[name->second].unpack(side.state, pid, data.data() + 4, data.size() - 4);
    if (side.skip) { side.skip--; return; }                   // sent again, and this end already had it
    side.got++;
    if (++side.since >= uint64_t(SAY_RECEIVED_EVERY)) { side.since = 0; hand(Item{WE_HAVE, stream, side.got, false, ""}); }
    m.role = side.role; m.sequence = stream; m.counter = counter; m.reply = reply;
    out.push_back(std::move(m));
}

void Codec::feed(const uint8_t* p, size_t n, std::vector<Message>& out) {
    if (at_ && at_ == buf_.size()) { buf_.clear(); at_ = 0; }
    buf_.insert(buf_.end(), p, p + n);
    uint64_t pid = 0; Bytes body;
    while (take(pid, body)) frame(pid, body, out);
    if (at_ > (size_t(1) << 16)) { buf_.erase(buf_.begin(), buf_.begin() + long(at_)); at_ = 0; }
}

}  // namespace channel_frame
