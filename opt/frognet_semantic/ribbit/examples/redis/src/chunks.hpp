// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// chunks.hpp -- a payload larger than one wire string, as consecutive fields of one cell. [PAYLOAD_IN_FIELDS_V1]
//
// A string field on the semantic wire holds at most 65,535 bytes. A pipeline (a 4 MB SET) or a reply (XREADGROUP of
// thousands of entries) can be larger, so it travels as fields <name>, <name>1, <name>2 ... each at most CHUNK bytes,
// in one cell: still one write. The field order for k pieces is made once and kept (the session keeps one order per
// shape for its life).
#pragma once
#include <atomic>
#include <memory>
#include <string>
#include <vector>
#include "bagarr.hpp"

namespace rr {
constexpr size_t CHUNK = 60000;                 // under the wire's 65,535, with room for the code points' second byte
constexpr size_t MAX_PIECES = 16384;            // 983 MB: past proto-max-bulk-len's 512 MB default
// the field names: `fixed` first, then <name>, <name>1 ... <name>(k-1) -- one shared vector per (table, k)
struct ChunkNames {
    std::vector<std::string> fixed; std::string name;
    std::unique_ptr<std::atomic<std::shared_ptr<const std::vector<std::string>>*>[]> by_k{new std::atomic<std::shared_ptr<const std::vector<std::string>>*>[MAX_PIECES + 1]()};
    ChunkNames(std::vector<std::string> f, std::string n) : fixed(std::move(f)), name(std::move(n)) {}
    const std::shared_ptr<const std::vector<std::string>>& of(size_t k) {
        auto* p = by_k[k].load(std::memory_order_acquire);
        if (!p) {
            auto v = std::make_shared<std::vector<std::string>>(fixed);
            for (size_t i = 0; i < k; ++i) v->push_back(i ? name + std::to_string(i) : name);
            auto* mine = new std::shared_ptr<const std::vector<std::string>>(std::move(v));
            std::shared_ptr<const std::vector<std::string>>* e = nullptr;
            if (by_k[k].compare_exchange_strong(e, mine, std::memory_order_acq_rel)) p = mine; else { delete mine; p = e; }
        }
        return *p;
    }
};
inline size_t pieces_of(const std::string& text) { return text.empty() ? 1 : (text.size() + CHUNK - 1) / CHUNK; }
// the k-th piece: cut on a code point's first byte (the text is UTF-8: a piece never splits a code point)
inline std::vector<std::string> cut(const std::string& text) {
    std::vector<std::string> out; size_t at = 0;
    do {
        size_t end = std::min(text.size(), at + CHUNK);
        while (end < text.size() && end > at && (uint8_t(text[end]) & 0xC0) == 0x80) --end;
        out.push_back(text.substr(at, end - at)); at = end;
    } while (at < text.size());
    return out;
}
// the payload back: <name>, <name>1 ... as long as they are there
inline std::string joined(const ribbit::BagArr& a, const std::string& name) {
    std::string s = a.str(name);
    for (size_t i = 1;; ++i) { const pyv::Obj* v = a.get(name + std::to_string(i)); if (!v || !v->is<pyv::Str>()) break; s += v->as<pyv::Str>().s; }
    return s;
}
}  // namespace rr

namespace rr {
// [PAYLOAD_IN_CELLS_V1] A cell's write is one frame, and a frame on the session is at most 1 MB (ram_host MAX_FRAME).
// A payload past CELL_MAX goes as piece cells <instance>:0, <instance>:1 ... and then the cell itself with the rest:
// one variable, so they are read in write order, and the reader joins them when the cell itself arrives.
constexpr size_t CELL_MAX = 180000;
inline std::vector<std::string> cells(const std::string& text) {   // cut on a code point's first byte
    std::vector<std::string> out; size_t at = 0;
    do {
        size_t end = std::min(text.size(), at + CELL_MAX);
        while (end < text.size() && end > at && (uint8_t(text[end]) & 0xC0) == 0x80) --end;
        out.push_back(text.substr(at, end - at)); at = end;
    } while (at < text.size());
    return out;
}
}  // namespace rr
