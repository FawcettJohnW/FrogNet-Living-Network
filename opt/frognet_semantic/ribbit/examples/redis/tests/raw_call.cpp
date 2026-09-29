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
// raw_call <port> <api> <json body>: one POST ?op=redis straight to the RAM host, as anything on the network could send
#include "frogram.hpp"
#include <cstdio>
int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage\n"); return 2; }
    try { frogram::Session s("127.0.0.1", atoi(argv[1]), argv[2]);
          auto r = s.call("POST", std::string(argv[2]) + "?op=redis", argv[3]);
          std::string o = "{"; bool f = true;
          for (auto& [k, v] : r.o) { o += (f ? "" : ",") + frogram::Json::quote(k) + ":" + (v.type == frogram::Json::Str ? frogram::Json::quote(v.s) : v.type == frogram::Json::Num ? std::to_string((long long)v.n) : v.type == frogram::Json::Bool ? (v.b ? "true" : "false") : "null"); f = false; }
          printf("RESULT %s}\n", o.c_str()); }
    catch (const std::exception& e) { printf("THROWN %s\n", e.what()); }
}
