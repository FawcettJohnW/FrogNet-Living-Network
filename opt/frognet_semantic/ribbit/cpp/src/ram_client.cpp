// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ram_client.cpp -- ribbit::RamClient (ram_client.hpp).
#include "ram_client.hpp"
#include <cmath>
#include <cstdio>

namespace ribbit {

RamClient::~RamClient() { close(); }

void RamClient::connect(const std::string& host, int port) {
    close();
    host_ = host; port_ = port;
    session_.reset(new frogram::Session(host, port, api_path()));
    memory_.reset(new frogram::Memory(*session_, api_path()));
}

void RamClient::close() {
    memory_.reset();
    if (session_) { session_->shutdown(); session_.reset(); }
}

frogram::Session& RamClient::session() {
    if (!session_) throw frogram::Unreachable(vendor_id() + ": not connected (call connect first)");
    return *session_;
}

uint64_t RamClient::write(const std::string& service, const std::string& variable, const std::string& instance,
                          const std::string& bag_json) {
    session(); return memory_->write(service, variable, instance, bag_json);
}

std::vector<frogram::Cell> RamClient::read(const std::string& service, const std::string& variable,
                                           const std::string& instance, int64_t after, double wait_s) {
    session(); return memory_->read(service, variable, instance, after, wait_s);
}

void RamClient::remove(uint64_t id) { session(); memory_->remove(id); }

frogram::Json RamClient::call(const std::string& op, const std::string& body_json, double timeout_s) {
    frogram::Json r = session().call("POST", api_path() + "?op=" + op, body_json, timeout_s);
    if (r["ok"].type != frogram::Json::Bool || !r["ok"].b)
        throw frogram::Refused(vendor_id() + " " + op + ": " + (r["error"].type == frogram::Json::Str ? r["error"].s : std::string("refused")));
    return r;
}

std::string RamClient::to_json(const frogram::Json& v) {
    switch (v.type) {
    case frogram::Json::Null: return "null";
    case frogram::Json::Bool: return v.b ? "true" : "false";
    case frogram::Json::Num: {
        if (std::isfinite(v.n) && v.n == std::floor(v.n) && std::fabs(v.n) < 9.007199254740992e15) {
            char b[32]; snprintf(b, sizeof b, "%.0f", v.n); return b; }
        char b[40]; snprintf(b, sizeof b, "%.17g", v.n); return b; }
    case frogram::Json::Str: return frogram::Json::quote(v.s);
    case frogram::Json::Arr: { std::string o = "["; for (size_t i = 0; i < v.a.size(); ++i) o += (i ? "," : "") + to_json(v.a[i]); return o + "]"; }
    case frogram::Json::Obj: { std::string o = "{"; for (size_t i = 0; i < v.o.size(); ++i) o += (i ? "," : "") + frogram::Json::quote(v.o[i].first) + ":" + to_json(v.o[i].second); return o + "}"; }
    }
    return "null";
}

std::string RamClient::to_json(const std::vector<frogram::Cell>& cells) {
    std::string o = "[";
    for (size_t k = 0; k < cells.size(); ++k) {
        const auto& c = cells[k]; char u[40]; snprintf(u, sizeof u, "%.3f", c.updated);
        o += (k ? "," : "") + std::string("{\"id\":") + std::to_string(c.id) + ",\"service\":" + frogram::Json::quote(c.service)
           + ",\"variable\":" + frogram::Json::quote(c.variable) + ",\"instance\":" + frogram::Json::quote(c.instance)
           + ",\"bag\":" + to_json(c.bag) + ",\"updated\":" + u + "}";
    }
    return o + "]";
}

}  // namespace ribbit
