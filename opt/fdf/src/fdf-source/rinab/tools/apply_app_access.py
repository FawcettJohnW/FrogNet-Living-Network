#!/usr/bin/env python3
"""apply_app_access.py RAM_SERVER_CPP -- [APP_ACCESS_V1] an app's droplet checks who reads and writes its RAM.

A droplet started with RINA_APP_ACCESS=<file> (fdf-app serve writes it, mode 0600: {"app", "ram", "login", "key"})
checks every read and write on its HTTP API. A role's RAM takes only that role's grant; the general RAM takes any of the
app's grants when the app requires login, and every client when it does not. A grant
(&grant=<payload>.<hmac>): the payload is base64url JSON {"a": app, "d": device, "s": station, "t": role, "u": user}, the hmac is
HMAC-SHA256 under the app's key, hex, given to the device by a droplet at login (fdf-app). Without RINA_APP_ACCESS (the network RAM, every other host) nothing is
checked. A refusal is the answer, with why (403)."""
import sys
p = sys.argv[1]; s = open(p).read(); tag = "[APP_ACCESS_V1]"
if tag in s: print("app access already applied"); sys.exit(0)
old = 'bool api(const std::string& method, const std::string& path, const std::string& body, bool may_wait, Reply& out) {\n'
assert s.count(old) == 1, "apply_app_access: api() not found exactly once"
fn = r'''// %s an app droplet's access check (tools/apply_app_access.py)
#include <openssl/hmac.h>
struct AppAccess { bool on = false, login = false; std::string app, ram, key; };
static const AppAccess& app_access_conf() {
    static const AppAccess a = [] {
        AppAccess c; const char* f = getenv("RINA_APP_ACCESS"); if (!f || !*f) return c;
        std::ifstream in(f); std::stringstream ss; ss << in.rdbuf();
        Json j = Json::parse(ss.str());                       // a droplet without its access file does not start
        c.on = true; c.app = j["app"].s; c.ram = j["ram"].s; c.key = j["key"].s; c.login = j["login"].type == Json::Bool && j["login"].b;
        std::cerr << "[APP_ACCESS_V1] " << c.app << "." << c.ram << ": " << (c.ram != "general" ? "a role's RAM: its grants only" : c.login ? "login required: the app's grants only" : "no login: every client") << "\n";
        return c; }();
    return a;
}
static std::string b64url_decode(const std::string& in) {
    std::string out; int val = 0, bits = -8;
    for (char ch : in) { int d;
        if (ch >= 'A' && ch <= 'Z') d = ch - 'A'; else if (ch >= 'a' && ch <= 'z') d = ch - 'a' + 26; else if (ch >= '0' && ch <= '9') d = ch - '0' + 52;
        else if (ch == '-' || ch == '+') d = 62; else if (ch == '_' || ch == '/') d = 63; else if (ch == '=') break; else return std::string("\x01", 1);
        val = (val << 6) | d; bits += 6; if (bits >= 0) { out += char((val >> bits) & 0xff); bits -= 8; } }
    return out;
}
static bool app_access(std::map<std::string, std::string>& q, Reply& out) {
    const AppAccess& a = app_access_conf(); if (!a.on) return true;
    if (a.ram == "general" && !a.login) return true;                     // the general RAM with no login: every client
    const std::string g = q["grant"]; const size_t dot = g.rfind('.');
    if (g.empty() || dot == std::string::npos) { out = bad(403, a.app + "." + a.ram + ": log in first (no grant)"); return false; }
    const std::string payload = g.substr(0, dot), mac = g.substr(dot + 1);
    unsigned char md[32]; unsigned int ml = 0;
    HMAC(EVP_sha256(), a.key.data(), int(a.key.size()), reinterpret_cast<const unsigned char*>(payload.data()), payload.size(), md, &ml);
    static const char* hx = "0123456789abcdef"; std::string want; for (unsigned i = 0; i < ml; ++i) { want += hx[md[i] >> 4]; want += hx[md[i] & 15]; }
    if (want.size() != mac.size() || CRYPTO_memcmp(want.data(), mac.data(), want.size())) { out = bad(403, a.app + "." + a.ram + ": the grant is not this app's"); return false; }
    Json j; try { j = Json::parse(b64url_decode(payload)); } catch (const std::exception&) { out = bad(403, "the grant does not read"); return false; }
    if (j["a"].s != a.app) { out = bad(403, "the grant is for another app"); return false; }
    if (a.ram != "general" && j["t"].s != a.ram) { out = bad(403, a.app + "." + a.ram + ": " + (j["t"].s.empty() ? "no team" : "team " + j["t"].s) + " is not this team"); return false; }
    return true;
}
''' % tag
s = s.replace(old, fn + old, 1)
old2 = '    auto q = query(path); const std::string op = q["op"];\n'
i = s.index(old) ; j = s.index(old2, i)
assert j - i < 400, "apply_app_access: the op line is not at the top of api()"
s = s[:j] + old2 + '    if ((op == "read" || op == "write") && !app_access(q, out)) return true;   // %s\n' % tag + s[j + len(old2):]
if "#include <fstream>" not in s: s = "#include <fstream>\n#include <sstream>\n" + s
open(p, "w").write(s); print("%s applied" % tag)
