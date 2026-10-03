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
// tuple_write.cpp -- write tuples into a Ribbit memory from JSON lines on stdin.
//   tuple-write --ram HOST PORT [--api ENDPOINT]
//   stdin, one per line: {"service":"...","variable":"...","instance":"...","bag":{...}}
//   stdout, one per line: {"ok":true,"id":N}   or   {"ok":false,"error":"..."}
//   A line {"read":{"service":"...","variable":"..."}} answers {"ok":true,"instances":[...]} -- the instances held.
//   A line {"get":{"service":"...","variable":"...","instance":"..."}} answers {"ok":true,"bag":{...}} (or null).
//   A line {"wait":{"service":"...","variable":"...","after":N,"timeout_s":T}} answers {"ok":true,"cells":[{id,instance,bag}]}
//   -- every cell of the variable with an id above N, waiting up to T seconds for one to exist.
// Used by tools/compare_lispers.py --record to put every measurement into the tuple space as the run goes.
#include "frogram.hpp"
#include <cstdio>
#include <iostream>
static std::string dump(const frogram::Json& j) {       // the client keeps a bag parsed; this writes it back out
    switch (j.type) {
        case frogram::Json::Null: return "null";
        case frogram::Json::Bool: return j.b ? "true" : "false";
        case frogram::Json::Num: { char b[40]; std::snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case frogram::Json::Str: return frogram::Json::quote(j.s);
        case frogram::Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        case frogram::Json::Obj: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + frogram::Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
    return "null";
}
int main(int argc, char** argv) {
    if (!(argc == 4 || (argc == 6 && std::string(argv[4]) == "--api")) || std::string(argv[1]) != "--ram") {
        std::cerr << "usage: tuple-write --ram HOST PORT [--api /Vendor.Product.ram_interface.php]\n"; return 2; }
    const std::string api = argc == 6 ? argv[5] : "/ram.php";
    try {
    frogram::Session session(argv[2], std::stoi(argv[3]), api);
    frogram::Memory mem(session, api);
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            frogram::Json in = frogram::Json::parse(line);
            if (in["get"].type == frogram::Json::Obj) {       // {"get":{service,variable,instance}} -> the bag, as stored
                auto cells = mem.read(in["get"]["service"].s, in["get"]["variable"].s, in["get"]["instance"].s);
                std::cout << "{\"ok\":true,\"bag\":" << (cells.empty() ? std::string("null") : dump(cells.front().bag)) << "}\n" << std::flush; continue;
            }
            if (in["wait"].type == frogram::Json::Obj) {      // {"wait":{service,variable,after,timeout_s}} -> cells with id > after
                const auto& w = in["wait"];                    // (a held read: returns when one exists, or at the timeout)
                auto cells = mem.read(w["service"].s, w["variable"].s, "", (int64_t)w["after"].n, w["timeout_s"].n);
                std::string o = "{\"ok\":true,\"cells\":[";
                for (size_t i = 0; i < cells.size(); ++i)
                    o += (i ? "," : "") + std::string("{\"id\":") + std::to_string(cells[i].id) + ",\"instance\":" + frogram::Json::quote(cells[i].instance) + ",\"bag\":" + dump(cells[i].bag) + "}";
                std::cout << o << "]}\n" << std::flush; continue;
            }
            if (in["read"].type == frogram::Json::Obj) {
                auto cells = mem.read(in["read"]["service"].s, in["read"]["variable"].s);
                std::string o = "{\"ok\":true,\"instances\":[";
                for (size_t i = 0; i < cells.size(); ++i) o += (i ? "," : "") + frogram::Json::quote(cells[i].instance);
                std::cout << o << "]}\n" << std::flush; continue;
            }
            // the bag goes through as written: everything after "bag": up to the line's final brace
            auto k = line.find("\"bag\""); auto c = line.find(':', k); auto e = line.rfind('}');
            if (k == std::string::npos || c == std::string::npos || e == std::string::npos || e <= c) throw std::runtime_error("expected {...,\"bag\":{...}}");
            std::string bag = line.substr(c + 1, e - c - 1);
            uint64_t id = mem.write(in["service"].s, in["variable"].s, in["instance"].s, bag);
            std::cout << "{\"ok\":true,\"id\":" << id << "}\n" << std::flush;
        } catch (const std::exception& x) {
            std::cout << "{\"ok\":false,\"error\":" << frogram::Json::quote(x.what()) << "}\n" << std::flush;
        }
    }
    } catch (const std::exception& x) {           // the RAM host is not there: say so once and stop (no abort)
        std::cout << "{\"ok\":false,\"error\":" << frogram::Json::quote(std::string("RAM host ") + argv[2] + ":" + argv[3] + " unreachable: " + x.what()) << "}\n" << std::flush;
        std::cerr << "tuple-write: RAM host " << argv[2] << ":" << argv[3] << " unreachable: " << x.what() << "\n";
        return 3;
    }
}
