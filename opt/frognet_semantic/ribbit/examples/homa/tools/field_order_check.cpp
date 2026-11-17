// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// field_order_check.cpp -- [FIELD_ORDER_WITHOUT_LEAF_TYPES_V1] (ribbit/cpp/include/cellwire.hpp)
// Compares the field order a cell write computes today (infer_schema, leaves classified) with shape_only's, over
// generated bags: nested objects, lists of scalars / objects / lists / mixed, empty lists and objects, unsafe keys,
// the keys "type" and "_array", and strings of every kind leaf_type tells apart.
#include "cellwire.hpp"
#include <cstdio>
#include <random>
using namespace cellwire::detail;
static std::mt19937_64 rng(20261117);
static int pick(int n) { return int(rng() % uint64_t(n)); }
static std::string key() {
    static const char* K[] = {"a","b","c","type","_array","host","dev","tags","status","name","x1","_p","Z","ip","a.b","a b","1a","","é","k-1","reason","kind","items","value","b0","c","f","i","k","p","s","t"};
    return K[pick(int(sizeof K / sizeof *K))];
}
static std::string esc(const std::string& s) { std::string o = "\""; for (char c : s) { if (c == '"' || c == '\\') o += '\\'; o += c; } return o + "\""; }
static std::string str() {
    static const char* S[] = {"10.1.2.3","192.168.1.1/24","eth0","wlan0","wg0","ENABLED","UP","OK_2","node1.frognet","databasehost.frognet","a.b.c","hello world","","é","QUJDREVGRw==","0","true","null","fe80::1","Seattle7","x","A","aGVsbG8gd29ybGQ+Lw==","1.2.3","tun0.5","HOST-NAME","under_score","{\"a\":1}","[1,2]"};
    return S[pick(int(sizeof S / sizeof *S))];
}
static std::string value(int depth) {
    int k = pick(depth > 4 ? 6 : 10);
    switch (k) {
        case 0: return std::to_string(int64_t(rng() % 100000) - 50000);
        case 1: return pick(2) ? "true" : "false";
        case 2: return "null";
        case 3: return std::to_string(pick(1000)) + "." + std::to_string(pick(1000));
        case 4: case 5: return esc(str());
        case 6: case 7: { int n = pick(5); std::string o = "{"; std::vector<std::string> seen;
            for (int i = 0; i < n; ++i) { std::string kk = key(); bool dup = false; for (auto& s : seen) dup |= s == kk; if (dup) continue; seen.push_back(kk);
                if (o.size() > 1) { o += ","; }
                o += esc(kk) + ":" + value(depth + 1); }
            return o + "}"; }
        default: { int n = pick(4); std::string o = "["; for (int i = 0; i < n; ++i) { if (i) o += ","; o += value(depth + 1); } return o + "]"; }
    }
}
int main(int argc, char** argv) {
    const long N = argc > 1 ? atol(argv[1]) : 300000; long same = 0, differ = 0, threw_both = 0, objects = 0, with_list_of_obj = 0, with_unsafe = 0;
    for (long n = 0; n < N; ++n) {
        std::string text = value(0);
        auto parsed = semtpl::detail::json_try(text); if (!parsed) { fprintf(stderr, "generator made bad JSON: %s\n", text.c_str()); return 2; }
        if (parsed->is<pyv::Dict>()) ++objects;
        if (text.find("[{") != std::string::npos) ++with_list_of_obj;
        if (text.find("\"a.b\"") != std::string::npos || text.find("\"\":") != std::string::npos) ++with_unsafe;
        std::vector<std::string> was, now; bool was_threw = false, now_threw = false;
        try { was = semtpl::field_order_and_type_map(semtpl::infer_schema(*parsed, "")).first; } catch (const std::exception&) { was_threw = true; }
        try { now = field_order(*parsed); } catch (const std::exception&) { now_threw = true; }
        if (was_threw && now_threw) { ++threw_both; continue; }
        if (was_threw != now_threw || was != now) { if (++differ <= 5) { fprintf(stderr, "DIFFER on %s\n  was:", text.c_str()); for (auto& p : was) fprintf(stderr, " %s", p.c_str());
                fprintf(stderr, "%s\n  now:", was_threw ? " (threw)" : ""); for (auto& p : now) fprintf(stderr, " %s", p.c_str()); fprintf(stderr, "%s\n", now_threw ? " (threw)" : ""); } }
        else ++same;
    }
    printf("bags %ld: same %ld, differ %ld, both refused %ld (objects %ld, with a list of objects %ld, with an unsafe key %ld)\n", N, same, differ, threw_both, objects, with_list_of_obj, with_unsafe);
    return differ ? 1 : 0;
}
