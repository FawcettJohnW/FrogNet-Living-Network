// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// sub_address.hpp -- [SUBS_BY_FILTER_V1] a subscription is addressed by its filter, so the question "does anyone
// subscribe to what matches this topic" is a lookup, not a scan.
//
//   (mqtt.subs, <filter>, <client>␟<box>␟<group>)   the box holding the client; <filter> without its $share/<group>/
//                                                   prefix; <group> "" when not shared. Fields b live o sid.
//   (mqtt.subs.shapes, all, <shape>␟<box>)          the filter shapes a box has used, written once each: the manifest
//
// A shape is a filter with each literal level replaced by L: "load/+" -> "L/+", "a/b/#" -> "L/L/#", "#" -> "#". A topic
// and a shape give exactly one filter (the topic's levels where the shape says L), or none (depth does not fit; a $
// topic under a leading wildcard). So a topic's candidate subscriptions are one family per shape in use -- bounded by
// the shapes, not by 2^depth and not by the number of subscriptions.
#pragma once
#include <string>
#include <string_view>
#include <vector>

namespace subaddr {

// $share/<group>/<filter> -> group, filter; anything else -> "", itself
inline void split_share(const std::string& f, std::string& group, std::string& filter) {
    if (f.rfind("$share/", 0) == 0) {
        const size_t s = f.find('/', 7);
        if (s != std::string::npos) { group = f.substr(7, s - 7); filter = f.substr(s + 1); return; }
    }
    group.clear(); filter = f;
}

inline std::vector<std::string_view> levels(std::string_view s) {
    std::vector<std::string_view> out; size_t a = 0;
    for (;;) { const size_t z = s.find('/', a);
        if (z == std::string_view::npos) { out.push_back(s.substr(a)); return out; }
        out.push_back(s.substr(a, z - a)); a = z + 1; }
}

// the filter's shape (the filter without its $share prefix)
inline std::string shape_of(const std::string& filter) {
    std::string out;
    bool first = true;
    for (std::string_view l : levels(filter)) {
        if (!first) out += '/';
        first = false;
        out += (l == "+" || l == "#") ? std::string(l) : std::string("L");
    }
    return out;
}

// the one filter of this shape that matches the topic: true and the filter in f, or false when the shape cannot match it
inline bool filter_for(const std::vector<std::string_view>& topic, const std::string& shape, std::string& f) {
    f.clear();
    if (topic.size() == 1 && topic[0].empty()) return false;     // "" is no topic (as mosquitto_topic_matches_sub)
    const std::vector<std::string_view> s = levels(shape);
    const bool hash = !s.empty() && s.back() == "#";
    const size_t fixed = hash ? s.size() - 1 : s.size();          // levels before any '#'
    if (hash ? fixed > topic.size() : fixed != topic.size()) return false;
    if (!topic.empty() && !topic[0].empty() && topic[0][0] == '$' && (s[0] == "+" || s[0] == "#"))
        return false;                                               // $ topics are not matched by a leading wildcard
    for (size_t i = 0; i < fixed; i++) {
        if (i) f += '/';
        if (s[i] == "+") f += '+';
        else if (s[i] == "L") f.append(topic[i].data(), topic[i].size());
        else return false;                                          // '#' before the end: not a filter
    }
    if (hash) { if (fixed) f += '/'; f += '#'; }
    return true;
}

}  // namespace subaddr
