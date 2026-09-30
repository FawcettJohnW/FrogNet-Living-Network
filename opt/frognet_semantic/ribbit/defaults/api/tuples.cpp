// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// tuples.cpp -- ribbit::TuplesClient (tuples.hpp).
#include "tuples.hpp"
#include <arpa/inet.h>
#include <chrono>
#include <cstdio>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>

namespace ribbit {
using frogram::Json;

static std::string urlenc(const std::string& s) {
    static const char* hx = "0123456789ABCDEF"; std::string o;
    for (unsigned char c : s) { if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') o += char(c); else { o += '%'; o += hx[c >> 4]; o += hx[c & 15]; } }
    return o;
}
static double now_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }

TuplesClient::~TuplesClient() { try { cleanup_owned(); } catch (...) {} }

std::string TuplesClient::my_ip() {
    if (!my_ip_.empty()) return my_ip_;
    // the address this machine uses toward the host: a UDP socket connected to the host's address (nothing is sent)
    addrinfo hints{}, *res = nullptr; hints.ai_family = AF_INET; hints.ai_socktype = SOCK_DGRAM;
    if (host().empty() || getaddrinfo(host().c_str(), "9", &hints, &res) != 0 || !res)
        throw frogram::Unreachable(vendor_id() + ": cannot resolve " + host());
    std::string ip;
    int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd >= 0) {
        if (::connect(fd, res->ai_addr, res->ai_addrlen) == 0) {
            sockaddr_in l{}; socklen_t ll = sizeof l;
            if (::getsockname(fd, reinterpret_cast<sockaddr*>(&l), &ll) == 0) { char b[64]; inet_ntop(AF_INET, &l.sin_addr, b, sizeof b); ip = b; }
        }
        ::close(fd);
    }
    freeaddrinfo(res);
    if (ip.empty()) throw frogram::Unreachable(vendor_id() + ": no local address toward " + host());
    return my_ip_ = ip;
}

uint64_t TuplesClient::put(const std::string& service, const std::string& var, const std::string& scope,
                           const std::string& value_json, bool own) {
    Json v = Json::parse(value_json);
    if (v.type != Json::Obj) throw frogram::Refused("a tuple's value is a JSON object");
    std::string value = value_json;
    if (v["ts"].type == Json::Null) {                             // stamp ts, as discovery's put does
        char ts[32]; snprintf(ts, sizeof ts, "%lld", (long long)now_s());
        size_t b = value.rfind('}');
        value = value.substr(0, b) + (v.size() ? "," : "") + "\"ts\":" + ts + "}";
    }
    uint64_t id = write(service, var, scope, "{\"value\":" + value + ",\"addr\":" + Json::quote(my_ip()) + "}");
    if (own) {
        bool have = false; for (auto& o : owned_) if (o.service == service && o.var == var && o.scope == scope) have = true;
        if (!have) owned_.push_back(Owned{service, var, scope});
    }
    return id;
}

std::vector<Tuple> TuplesClient::get_all(const std::string& service, int fresh_s, double wait_s, int min_rows,
                                         const std::string& var, int64_t after) {
    std::string q = api_path() + "?op=read&service=" + urlenc(service);
    if (!var.empty()) q += "&variable=" + urlenc(var);
    if (fresh_s > 0) q += "&fresh_s=" + std::to_string(fresh_s);
    if (after >= 0) q += "&after=" + std::to_string(after);
    if (wait_s > 0) { char w[32]; snprintf(w, sizeof w, "%.3f", wait_s); q += "&wait_s=" + std::string(w) + "&min_rows=" + std::to_string(min_rows > 0 ? min_rows : 1); }
    Json r = session().call("GET", q, "", 15.0 + wait_s);
    if (r["ok"].type != Json::Bool || !r["ok"].b)
        throw frogram::Refused(vendor_id() + " read " + service + ": " + (r["error"].type == Json::Str ? r["error"].s : std::string("refused")));
    std::vector<Tuple> out; const double now = now_s();
    for (auto& row : r["rows"].a) {
        Tuple t; t.var = row["variable"].s; t.scope = row["instance"].s; t.name = var_name(t.var, t.scope);
        t.addr = row["bag"]["addr"].s; t.value = row["bag"]["value"]; t.id = uint64_t(row["id"].n);
        t.ts_env = row["updated_epoch"].n; t.age_s = now - t.ts_env;
        out.push_back(t);
    }
    return out;
}

bool TuplesClient::remove_tuple(const std::string& service, const std::string& var, const std::string& scope) {
    auto cells = read(service, var, scope);
    if (cells.empty()) return false;
    for (auto& c : cells) remove(c.id);
    return true;
}

void TuplesClient::cleanup_owned() {
    if (!connected()) { owned_.clear(); return; }
    for (auto& o : owned_) remove_tuple(o.service, o.var, o.scope);
    owned_.clear();
}

std::string TuplesClient::to_json(const std::vector<Tuple>& ts) {
    std::string o = "[";
    for (size_t k = 0; k < ts.size(); ++k) {
        const auto& t = ts[k]; char e[64]; snprintf(e, sizeof e, "\"ts_env\":%.3f,\"age_s\":%.3f", t.ts_env, t.age_s);
        o += (k ? "," : "") + std::string("{\"var\":") + Json::quote(t.var) + ",\"scope\":" + Json::quote(t.scope)
           + ",\"name\":" + Json::quote(t.name) + ",\"addr\":" + Json::quote(t.addr) + ",\"value\":" + RamClient::to_json(t.value)
           + "," + e + ",\"id\":" + std::to_string(t.id) + "}";
    }
    return o + "]";
}

}  // namespace ribbit
