// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_sub_address.cpp -- [SUBS_BY_FILTER_V1] the addressed lookup finds exactly the subscriptions Mosquitto's own
// matcher (mosquitto_topic_matches_sub) says match: for every (filter, topic) pair drawn from a vocabulary that covers
// literal, '+', '#', '$' topics, empty levels and $share, "a subscription on F matches T" must equal "F is the filter
// that F's shape gives for T".
//
//   g++ -std=c++20 -O2 -I. test_sub_address.cpp ../front/libcommon/libmosquitto_common.a -o /tmp/t && /tmp/t
#include <cstdio>
#include <string>
#include <vector>
#include "sub_address.hpp"
extern "C" int mosquitto_topic_matches_sub(const char* sub, const char* topic, bool* result);

int main() {
    const std::vector<std::string> L = {"a", "b", "$SYS", "", "x y"};
    std::vector<std::string> topics, filters;
    // topics: every sequence of 1..4 levels from L (a '$' level only first)
    std::vector<std::vector<std::string>> seqs = {{}};
    for (int d = 1; d <= 4; d++) {
        std::vector<std::vector<std::string>> next;
        for (auto& s : seqs) if (int(s.size()) == d - 1) for (auto& l : L) {
            if (l[0] == '$' && d > 1) continue;
            auto t = s; t.push_back(l); next.push_back(t);
        }
        for (auto& t : next) { std::string x; for (size_t i = 0; i < t.size(); i++) x += (i ? "/" : "") + t[i]; topics.push_back(x); }
        seqs.insert(seqs.end(), next.begin(), next.end());
    }
    // filters: every sequence of 1..4 levels from L + {+}, optionally ending in #, and "#" alone; each also as $share
    std::vector<std::string> FL = {"a", "b", "$SYS", "", "+"};
    std::vector<std::vector<std::string>> fs = {{}};
    for (int d = 1; d <= 4; d++) {
        std::vector<std::vector<std::string>> next;
        for (auto& s : fs) if (int(s.size()) == d - 1) for (auto& l : FL) { auto t = s; t.push_back(l); next.push_back(t); }
        for (auto& t : next) {
            std::string x; for (size_t i = 0; i < t.size(); i++) x += (i ? "/" : "") + t[i];
            filters.push_back(x); filters.push_back(x + "/#");
        }
        fs.insert(fs.end(), next.begin(), next.end());
    }
    filters.push_back("#");
    const size_t plain = filters.size();
    for (size_t i = 0; i < plain; i += 7) filters.push_back("$share/g1/" + filters[i]);

    long pairs = 0, hits = 0, bad = 0;
    for (auto& f0 : filters) {
        std::string group, f; subaddr::split_share(f0, group, f);
        const std::string shape = subaddr::shape_of(f);
        for (auto& t : topics) {
            bool want = false;
            if (mosquitto_topic_matches_sub(f.c_str(), t.c_str(), &want) != 0) want = false;
            std::string cand; const bool got = subaddr::filter_for(subaddr::levels(t), shape, cand) && cand == f;
            ++pairs; hits += want;
            if (want != got) { if (++bad <= 20) fprintf(stderr, "MISMATCH filter=[%s] topic=[%s] mosquitto=%d addressed=%d\n", f0.c_str(), t.c_str(), want, got); }
        }
    }
    printf("test_sub_address: %ld pairs, %ld matching, %ld mismatched -- %s\n", pairs, hits, bad, bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
