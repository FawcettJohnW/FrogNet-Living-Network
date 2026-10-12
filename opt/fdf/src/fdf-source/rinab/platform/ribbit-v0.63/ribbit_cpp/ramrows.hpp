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
// ramrows.hpp -- the RAM-answer handler (RAM-ANSWER-HANDLER-SPEC.md): a RAM read answer {"ok":true,"rows":[...]} as a field
// list, so FNWP's RESP_DIFF (semcodec encode_reply_diff) carries field-level changes. The template is the reference
// answer both ends hold; learn / extract / rebuild are the UnRESTHandler codec slot for mode "ram_rows".
#pragma once
#include "frogram.hpp"
#include "semcodec.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
namespace ramrows {
using pyv::Dict; using pyv::List; using pyv::Obj; using semcodec::Fields;
using frogram::Json;
namespace detail {
// ram_server.cpp dump(), exactly: every number is a double; integral and |x| < 9e15 prints "%.0f", else "%.17g".
inline std::string num(double n) {
    char b[40];
    if (std::floor(n) == n && std::fabs(n) < 9e15) std::snprintf(b, sizeof b, "%.0f", n);
    else std::snprintf(b, sizeof b, "%.17g", n);
    return b;
}
inline std::string dump(const Json& j) {
    switch (j.type) {
        case Json::Null: return "null";
        case Json::Bool: return j.b ? "true" : "false";
        case Json::Num: return num(j.n);
        case Json::Str: return Json::quote(j.s);
        case Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        case Json::Obj: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
    return "null";
}
inline std::string epoch(double u) { char b[64]; std::snprintf(b, sizeof b, "%.3f", u); return b; }   // row_json's updated_epoch
inline bool dup_keys(const Json& j) {                                  // a pyv Dict cannot hold duplicate keys
    if (j.type == Json::Arr) { for (auto& x : j.a) if (dup_keys(x)) return true; return false; }
    if (j.type != Json::Obj) return false;
    for (size_t i = 0; i < j.o.size(); ++i) { for (size_t k = 0; k < i; ++k) if (j.o[k].first == j.o[i].first) return true; if (dup_keys(j.o[i].second)) return true; }
    return false;
}
inline Obj to_obj(const Json& j) {                                     // a leaf value for the codec; numbers stay doubles
    switch (j.type) {
        case Json::Null: return pyv::None{};
        case Json::Bool: return j.b;
        case Json::Num: return j.n;
        case Json::Str: return pyv::Str{j.s};
        case Json::Arr: { List l; for (auto& x : j.a) l.push_back(to_obj(x)); return l; }
        case Json::Obj: { Dict d; for (auto& kv : j.o) d.set(kv.first, to_obj(kv.second)); return d; }
    }
    return pyv::None{};
}
inline Json to_json(const Obj& o) {                                    // back, for the server's dump
    Json j;
    if (o.is<pyv::None>()) j.type = Json::Null;
    else if (o.is<bool>()) { j.type = Json::Bool; j.b = o.as<bool>(); }
    else if (o.is<double>()) { j.type = Json::Num; j.n = o.as<double>(); }
    else if (o.is<pyv::Int>()) { j.type = Json::Num; j.n = semcodec::detail::int_to_float(o.as<pyv::Int>()); }
    else if (o.is<pyv::Str>()) { j.type = Json::Str; j.s = o.as<pyv::Str>().s; }
    else if (o.is<List>()) { j.type = Json::Arr; for (auto& x : o.as<List>()) j.a.push_back(to_json(x)); }
    else if (o.is<Dict>()) { j.type = Json::Obj; const Dict& d = o.as<Dict>(); for (size_t i = 0; i < d.size(); ++i) j.o.push_back({d.keys[i], to_json(d.vals[i])}); }
    else throw std::runtime_error("ramrows: a bytes value in a RAM answer");
    return j;
}
inline Obj shape(const Json& bag) {                                    // object structure: Dict of key -> shape, leaf = None
    if (bag.type != Json::Obj) return pyv::None{};
    Dict d; for (auto& kv : bag.o) d.set(kv.first, shape(kv.second)); return d;
}
inline bool same_shape(const Json& bag, const Obj& sh) {
    if (!sh.is<Dict>()) return bag.type != Json::Obj;
    if (bag.type != Json::Obj) return false;
    const Dict& d = sh.as<Dict>(); if (d.size() != bag.o.size()) return false;
    for (size_t i = 0; i < d.size(); ++i) if (d.keys[i] != bag.o[i].first || !same_shape(bag.o[i].second, d.vals[i])) return false;
    return true;
}
inline std::string esc(const std::string& k) { std::string o; for (char c : k) { if (c == '~') o += "~0"; else if (c == '/') o += "~1"; else o += c; } return o; }
inline void leaf_names(const Obj& sh, const std::string& pre, List& names) {
    if (!sh.is<Dict>()) { names.push_back(pyv::Str{pre}); return; }
    const Dict& d = sh.as<Dict>(); for (size_t i = 0; i < d.size(); ++i) leaf_names(d.vals[i], pre + "/" + esc(d.keys[i]), names);
}
inline void leaf_values(const Json& bag, Fields& out, const std::string& pre) {
    if (bag.type != Json::Obj) { out.emplace_back(pre, to_obj(bag)); return; }
    for (auto& kv : bag.o) leaf_values(kv.second, out, pre + "/" + esc(kv.first));
}
inline Json fill(const Obj& sh, const List& vals, size_t& k) {         // the bag from its shape and the leaf values, in order
    if (!sh.is<Dict>()) return to_json(vals.at(k++));
    Json j; j.type = Json::Obj; const Dict& d = sh.as<Dict>();
    for (size_t i = 0; i < d.size(); ++i) j.o.push_back({d.keys[i], fill(d.vals[i], vals, k)});
    return j;
}
struct Row { double id; std::string s, v, i; const Json* bag; double upd; };
inline std::vector<Row> rows(const Json& ans) {
    if (ans.type != Json::Obj || !ans["ok"].b || ans["rows"].type != Json::Arr) throw std::runtime_error("ramrows: not a RAM read answer");
    std::vector<Row> r;
    for (const Json& x : ans["rows"].a) r.push_back(Row{x["id"].n, x["service"].s, x["variable"].s, x["instance"].s, &x["bag"], x["updated_epoch"].n});
    return r;
}
}  // namespace detail

inline Dict learn(std::string_view answer) {
    Json ans = Json::parse(std::string(answer)); auto rs = detail::rows(ans);
    Dict frag; frag.set("mode", pyv::Str{"ram_rows"});
    bool holdable = true; List rows, names;
    for (size_t n = 0; n < rs.size(); ++n) {
        if (detail::dup_keys(*rs[n].bag)) holdable = false;
        Dict r; r.set("service", pyv::Str{rs[n].s}); r.set("variable", pyv::Str{rs[n].v}); r.set("instance", pyv::Str{rs[n].i});
        Obj sh = detail::shape(*rs[n].bag); r.set("shape", sh); rows.push_back(r);
        std::string p = std::to_string(n); names.push_back(pyv::Str{p + "/id"}); names.push_back(pyv::Str{p + "/updated"});
        detail::leaf_names(sh, p + "/bag", names);
    }
    frag.set("holdable", holdable); frag.set("rows", rows); frag.set("field_order", names);
    return frag;
}
inline std::optional<Fields> extract(std::string_view answer, const Dict& frag) {
    if (!frag.vals[size_t(frag.find("holdable"))].as<bool>()) return std::nullopt;
    Json ans = Json::parse(std::string(answer)); auto rs = detail::rows(ans);
    const List& tr = frag.vals[size_t(frag.find("rows"))].as<List>();
    if (rs.size() != tr.size()) return std::nullopt;                   // a different address set
    Fields out;
    for (size_t n = 0; n < tr.size(); ++n) {
        const Dict& t = tr[n].as<Dict>(); auto str = [&](const char* k) { return t.vals[size_t(t.find(k))].as<pyv::Str>().s; };
        const detail::Row* m = nullptr;
        for (auto& r : rs) if (r.s == str("service") && r.v == str("variable") && r.i == str("instance")) { m = &r; break; }
        if (!m || detail::dup_keys(*m->bag) || !detail::same_shape(*m->bag, t.vals[size_t(t.find("shape"))])) return std::nullopt;
        std::string p = std::to_string(n);
        out.emplace_back(p + "/id", pyv::Int::of(int64_t(m->id))); out.emplace_back(p + "/updated", pyv::Str{detail::epoch(m->upd)});
        detail::leaf_values(*m->bag, out, p + "/bag");
    }
    return out;
}
inline std::string rebuild(const Dict& frag, const List& values) {
    const List& tr = frag.vals[size_t(frag.find("rows"))].as<List>();
    struct Out { uint64_t id; std::string text; }; std::vector<Out> outs; size_t k = 0;
    for (const Obj& ro : tr) {
        const Dict& t = ro.as<Dict>(); auto str = [&](const char* key) { return t.vals[size_t(t.find(key))].as<pyv::Str>().s; };
        const pyv::Int& id = values.at(k++).as<pyv::Int>(); std::string upd = values.at(k++).as<pyv::Str>().s;
        Json bag = detail::fill(t.vals[size_t(t.find("shape"))], values, k);
        outs.push_back(Out{uint64_t(id.v), std::string("{\"id\":") + std::to_string(uint64_t(id.v)) + ",\"service\":" + Json::quote(str("service")) +
                           ",\"variable\":" + Json::quote(str("variable")) + ",\"instance\":" + Json::quote(str("instance")) + ",\"bag\":" +
                           detail::dump(bag) + ",\"updated_epoch\":" + upd + "}"});
    }
    if (k != values.size()) throw std::runtime_error("ramrows: " + std::to_string(values.size()) + " values for " + std::to_string(k) + " fields");
    std::sort(outs.begin(), outs.end(), [](const Out& a, const Out& b) { return a.id < b.id; });   // the server answers in id order
    std::string o = "{\"ok\":true,\"rows\":[";
    for (size_t i = 0; i < outs.size(); ++i) o += (i ? "," : "") + outs[i].text;
    return o + "]}";
}
}  // namespace ramrows
