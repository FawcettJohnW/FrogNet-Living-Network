/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// semwire oracle driver: one request per stdin line, one answer per stdout line (see tools/test_semwire_oracle.py).
//   wrap <name> <args...>   byte args as hex ('.' = empty), integers decimal  ->  ok <hex> | raise <kind> <message>
//   parse <hex>             ->  none | msg op=N field=hex|N|~ ...  | raise <kind> <message>
//   is_fnw1 <hex>           ->  true | false
//   op_name <n>             ->  name
#include "semwire.hpp"
#include <cstdio>
#include <iostream>
#include <sstream>
#include <vector>

using namespace semwire;

static std::string unhex(const std::string& h) {
    if (h == ".") return {};
    if (h.size() % 2) throw std::runtime_error("driver: odd hex length");
    std::string out(h.size() / 2, '\0');
    auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        throw std::runtime_error("driver: bad hex digit");
    };
    for (size_t i = 0; i < out.size(); ++i) out[i] = char(nib(h[2 * i]) << 4 | nib(h[2 * i + 1]));
    return out;
}

static std::string tohex(std::string_view b) {
    static const char* d = "0123456789abcdef";
    std::string out(b.size() * 2, '\0');
    for (size_t i = 0; i < b.size(); ++i) { out[2 * i] = d[uint8_t(b[i]) >> 4]; out[2 * i + 1] = d[uint8_t(b[i]) & 15]; }
    return out;
}

static std::string canon(const std::optional<WireMsg>& m) {
    if (!m) return "none";
    std::string s = "msg op=" + std::to_string(m->op);
    auto bytes = [&](const char* n, const std::optional<std::string_view>& v) {
        s += std::string(" ") + n + "=" + (v ? tohex(*v) : std::string("~"));
    };
    auto num = [&](const char* n, const auto& v) { s += std::string(" ") + n + "=" + (v ? std::to_string(*v) : "~"); };
    bytes("req_hash", m->req_hash); bytes("same_id", m->same_id); bytes("payload", m->payload); num("status", m->status);
    bytes("headers", m->headers); bytes("body", m->body); num("ping_id", m->ping_id);
    num("proxy_t_send_ns", m->proxy_t_send_ns); num("daemon_t_recv_ns", m->daemon_t_recv_ns);
    num("daemon_t_reply_ns", m->daemon_t_reply_ns);
    return s;
}

static std::string wrap(const std::string& name, const std::vector<std::string>& a) {
    auto B = [&](size_t i) { return unhex(a.at(i)); };
    auto N = [&](size_t i) { return uint64_t(std::stoull(a.at(i))); };
    std::string f;
    if (name == "req_full") f = wrap_req_full(B(0), B(1));
    else if (name == "req_repeat") f = wrap_req_repeat(B(0));
    else if (name == "req_raw") f = wrap_req_raw(B(0), B(1));
    else if (name == "req_diff") f = wrap_req_diff(B(0), B(1));
    else if (name == "resp_same") f = wrap_resp_same(B(0));
    else if (name == "resp_diff") f = wrap_resp_diff(B(0), B(1));
    else if (name == "resp_raw") f = wrap_resp_raw(B(0), N(1), B(2), B(3));
    else if (name == "req_miss") f = wrap_req_miss(B(0));
    else if (name == "error") f = wrap_error(N(0), B(1));
    else if (name == "seq_reset") f = wrap_seq_reset();
    else if (name == "hello") f = wrap_hello(B(0));
    else if (name == "rtt_ping") f = wrap_rtt_ping(N(0), N(1), N(2));
    else if (name == "rtt_pong") f = wrap_rtt_pong(N(0), N(1), N(2), N(3));
    else if (name == "rtt_loop") f = wrap_rtt_loop();
    else throw std::runtime_error("driver: unknown wrap " + name);
    return "ok " + (f.empty() ? std::string(".") : tohex(f));
}

template <class F> static std::string guarded(F f) {
    try { return f(); }
    catch (const ValueError& e) { return std::string("raise value ") + e.what(); }
    catch (const StructError&) { return "raise struct "; }
    catch (const EncodeError&) { return "raise encode "; }
    catch (const std::exception& e) { return std::string("raise other:") + e.what(); }
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string line, out;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string cmd; in >> cmd;
        std::vector<std::string> a; for (std::string t; in >> t;) a.push_back(t);
        if (cmd == "wrap") {
            std::string name = a.at(0); a.erase(a.begin());
            out = guarded([&] { return wrap(name, a); });
        } else if (cmd == "parse") {
            std::string frame = unhex(a.at(0));
            out = guarded([&] { return canon(try_parse(frame)); });
        } else if (cmd == "is_fnw1") {
            std::string frame = unhex(a.at(0));
            out = guarded([&] { return std::string(is_fnw1(frame) ? "true" : "false"); });
        } else if (cmd == "op_name") {
            uint8_t op = uint8_t(std::stoul(a.at(0)));
            out = guarded([&] { return op_name(op); });
        } else {
            std::cerr << "driver: unknown command " << cmd << "\n"; return 2;
        }
        std::cout << out << '\n';
    }
    return 0;
}
