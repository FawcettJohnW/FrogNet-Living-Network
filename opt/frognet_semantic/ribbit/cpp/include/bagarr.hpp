// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// bagarr.hpp -- [DECODE_ONCE_V1] a bag, decoded once: its values as a JSON array in its template's field order, the
// field names held once per template and shared. What arrives on the wire as a difference is applied to the previous
// array and becomes the next array; the memory stores it, encodes the next reader's difference from it, and the reader
// takes its values from it -- nobody prints it as JSON text to have it parsed again. The text is made only for a
// reader that asks for text (the HTTP API, a participant that reads bags as text).
#pragma once
#include <memory>
#include <string>
#include <string_view>
#include <vector>
#include "semtpl.hpp"

namespace ribbit {

struct BagArr {
    std::shared_ptr<const std::vector<std::string>> names;   // the template's field order (paths), shared
    std::vector<pyv::Obj> vals;                              // the values, in that order
    const pyv::Obj* get(std::string_view name) const {
        if (!names) return nullptr;
        const auto& n = *names;
        for (size_t k = 0; k < n.size() && k < vals.size(); ++k) if (n[k] == name) return &vals[k];
        return nullptr;
    }
    std::string str(std::string_view name) const { const pyv::Obj* v = get(name); return v && v->is<pyv::Str>() ? v->as<pyv::Str>().s : std::string(); }
    int64_t num(std::string_view name) const {
        const pyv::Obj* v = get(name); if (!v) return 0;
        if (v->is<pyv::Int>()) { const auto& i = v->as<pyv::Int>(); return i.big ? 0 : i.v; }
        if (v->is<double>()) return int64_t(v->as<double>());
        if (v->is<bool>()) return v->as<bool>() ? 1 : 0;
        return 0;
    }
    // the bag as JSON text: for a reader that asks for text
    std::string json() const {
        if (!names) return "{}";
        pyv::List l(vals.begin(), vals.end());
        return semtpl::dumps_ascii(semtpl::rebuild_json(l, *names), false);
    }
    // a bag that arrived as text: decoded once, here
    static std::shared_ptr<const BagArr> parse(std::string_view text) {
        auto parsed = semtpl::detail::json_try(text);
        if (!parsed || !parsed->is<pyv::Dict>()) return nullptr;
        auto a = std::make_shared<BagArr>();
        auto fo = std::make_shared<std::vector<std::string>>(semtpl::field_order_and_type_map(semtpl::infer_schema(*parsed, "")).first);
        a->vals.reserve(fo->size());
        for (const auto& p : *fo) { pyv::Obj* v = semtpl::path_ptr(*parsed, p); a->vals.push_back(v ? std::move(*v) : pyv::Obj(pyv::None{})); }
        a->names = std::move(fo);
        return a;
    }
};
using BagArrP = std::shared_ptr<const BagArr>;

}  // namespace ribbit
