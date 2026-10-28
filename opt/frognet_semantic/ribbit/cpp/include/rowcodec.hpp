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
// rowcodec.hpp -- [ROWS_ON_THE_SEMANTIC_WIRE_V1] the rows a reader is given (a read after a write id, a standing
// trigger's batches) carried as semantic differences, not as whole rows.
//
// A row is the JSON object {"bag", "id", "instance", "service", "updated", "variable"}. Its shape (keys and structure,
// values elided) names a template both ends derive from the row itself (semtpl: schema -> field order). The first row of
// a shape on a session travels whole, as its JSON text, and both ends learn the shape from it. Every later row of that
// shape is a semcodec DIFF against the last row of the same shape on this session -- the reader's previous row of that
// family -- so what repeats (the family's coordinates, the keys, a topic, a client) does not travel; a changed string
// travels as its changed middle ([STRING_SPLICE_V1]). Both ends keep the references in the answers' wire order: the host
// encodes as its one writer puts each answer on the socket, the client decodes as its one reader takes it off.
//
// The rows on either side of the codec are the platform's row layout (u64 id, u64 updated bits, u16+service,
// u16+variable, u32+instance, u32+bag), so the readers above it are unchanged.
//
// Records: see [ROW_RECORD_COMPACT_V1] below.
#pragma once
#include "fnwp_engine.hpp"
#include "bagarr.hpp"
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

namespace rowcodec {
using pyv::Dict; using pyv::Obj; using pyv::Str; using pyv::List;
namespace detail {
inline uint64_t get(std::string_view s, size_t& p, size_t w) {
    if (p + w > s.size()) throw std::runtime_error("rowcodec: cut short");
    uint64_t v = 0; for (size_t k = 0; k < w; ++k) v = v << 8 | uint8_t(s[p + k]); p += w; return v;
}
inline void put(std::string& o, uint64_t v, int w) { for (int k = w - 1; k >= 0; --k) o += char(v >> (8 * k)); }
struct Raw { uint64_t id = 0, updated_bits = 0; std::string_view service, variable, instance, bag; };
inline bool next_raw(std::string_view b, size_t& p, Raw& r) {
    if (p >= b.size()) return false;
    r.id = get(b, p, 8); r.updated_bits = get(b, p, 8);
    size_t n = size_t(get(b, p, 2)); if (p + n > b.size()) throw std::runtime_error("rowcodec: cut short"); r.service = b.substr(p, n); p += n;
    n = size_t(get(b, p, 2)); if (p + n > b.size()) throw std::runtime_error("rowcodec: cut short"); r.variable = b.substr(p, n); p += n;
    n = size_t(get(b, p, 4)); if (p + n > b.size()) throw std::runtime_error("rowcodec: cut short"); r.instance = b.substr(p, n); p += n;
    n = size_t(get(b, p, 4)); if (p + n > b.size()) throw std::runtime_error("rowcodec: cut short"); r.bag = b.substr(p, n); p += n;
    return true;
}
inline void put_raw(std::string& o, uint64_t id, uint64_t ub, const std::string& sv, const std::string& vv, const std::string& iv, const std::string& bag) {
    put(o, id, 8); put(o, ub, 8); put(o, sv.size(), 2); o += sv; put(o, vv.size(), 2); o += vv; put(o, iv.size(), 4); o += iv; put(o, bag.size(), 4); o += bag;
}
// a row's JSON text, keys in sorted order; the bag as it is stored
inline std::string row_text(const Raw& r) {
    double up; std::memcpy(&up, &r.updated_bits, 8);
    std::string t; t.reserve(64 + r.bag.size() + r.service.size() + r.variable.size() + r.instance.size());
    t += "{\"bag\":"; t.append(r.bag.data(), r.bag.size());
    t += ",\"id\":"; t += std::to_string(r.id);
    t += ",\"instance\":"; semtpl::detail::dumps_ascii_str(t, r.instance);
    t += ",\"service\":"; semtpl::detail::dumps_ascii_str(t, r.service);
    t += ",\"updated\":"; t += semcodec::detail::float_repr(up);
    t += ",\"variable\":"; semtpl::detail::dumps_ascii_str(t, r.variable);
    t += '}';
    return t;
}
struct Shape { std::vector<std::string> fo; Dict ref; };
inline std::vector<std::string> field_order_of(const Obj& row) { return semtpl::field_order_and_type_map(semtpl::infer_schema(row, "")).first; }
inline semcodec::Fields fields_of(Obj& row, const std::vector<std::string>& fo) {
    semcodec::Fields f; f.reserve(fo.size());
    for (const auto& p : fo) { Obj* v = semtpl::path_ptr(row, p); f.emplace_back(p, v ? std::move(*v) : Obj(pyv::None{})); }
    return f;
}
inline Dict dict_of(const semcodec::Fields& f) { Dict d; d.keys.reserve(f.size()); d.vals.reserve(f.size()); for (auto& kv : f) { d.keys.push_back(kv.first); d.vals.push_back(kv.second); } return d; }
}  // namespace detail

// [ROW_REFERENCE_OF_K_V1] A row is differenced against the best of the last ROW_REFS rows of its shape, not only the
// last one: rows of one family from several writers interleave (four publishers' messages in one box's window), and a
// writer's own previous row is the one its client, topic and port repeat in (measured: against the last row only, those
// three travelled in every publish). The encoder tries each and keeps the smallest; the record names which (k). Both
// ends keep the same rows, newest first, in the answers' wire order.
// [ROW_RECORD_COMPACT_V1] Record: u8 head = kind | k << 2, varint shape, then by kind -- 0 (a new shape): varint
// length, the row's JSON text; 1 (a difference): u8 packet flags, varint field count, varint length, the field block
// (the packet's version and opcode are the codec's constants and do not travel); 2 (identical to reference k): nothing.
// [GONE_WITH_ITS_WRITER_V1] head GONE (kind 3 with k 1; a new shape is kind 3 with k 0): a row that is gone -- varint
// id, then service, variable and instance, each a varint length and its bytes. No shape, no reference: nothing of it
// is kept by either end.
constexpr size_t ROW_REFS = 8;
constexpr uint8_t GONE = 3 | 1 << 2;
namespace detail {
struct Refs { std::vector<std::string> fo; std::vector<Dict> ring; };   // ring: newest first, at most ROW_REFS
inline void push_ref(Refs& sh, Dict d) { sh.ring.insert(sh.ring.begin(), std::move(d)); if (sh.ring.size() > ROW_REFS) sh.ring.pop_back(); }
}  // namespace detail

// the host's side: one per session, used only by the session's writer
class Encoder {
public:
    // rows: the platform's row layout; returns the records
    std::string encode(std::string_view rows) {
        std::string out; out.reserve(rows.size() / 3 + 64);
        size_t p = 0; detail::Raw r;
        while (detail::next_raw(rows, p, r)) {
            const std::string text = detail::row_text(r);
            thread_local std::string key; fnwp::shape_of(key, "", "", text);
            auto it = idx_.find(key);
            auto parsed = semtpl::detail::json_try(text);
            if (!parsed || !parsed->is<Dict>()) throw std::runtime_error("rowcodec: a stored bag is not a JSON object");
            if (it == idx_.end()) {                          // a new shape: the row whole; both ends learn it from the text
                const uint64_t n = shapes_.size();
                if (shapes_.size() >= 0xFFFF) throw std::runtime_error("rowcodec: too many row shapes on one session");
                detail::Refs sh; sh.fo = detail::field_order_of(*parsed); detail::push_ref(sh, detail::dict_of(detail::fields_of(*parsed, sh.fo)));
                shapes_.push_back(std::move(sh)); idx_.emplace(key, uint16_t(n)); fresh_.push_back(false);
                out += char(0); semcodec::detail::put_varint(out, n); semcodec::detail::put_varint(out, text.size()); out += text;
                continue;
            }
            detail::Refs& sh = shapes_[it->second];
            semcodec::Fields f = detail::fields_of(*parsed, sh.fo);
            // the reference with the most fields equal to this row's (a count, not an encoding: one encoding per row)
            size_t best = 0, most = 0;
            for (size_t k = 0; k < sh.ring.size(); ++k) {
                const Dict& rf = sh.ring[k]; size_t eq = 0;
                for (size_t i = 0; i < f.size() && i < rf.vals.size(); ++i) if (semcodec::values_equal(f[i].second, rf.vals[i])) ++eq;
                if (eq > most || k == 0) { most = eq; best = k; }
                if (eq == f.size()) break;
            }
            semcodec::DiffResult d = semcodec::encode_request_diff(0, {}, f, &sh.ring[best], true);
            const bool same = d.identical; std::string best_pkt = std::move(d.bytes); Dict nref = std::move(d.reference);
            if (same) { out += char(2 | best << 2); semcodec::detail::put_varint(out, it->second); }
            else {
                out += char(1 | best << 2); semcodec::detail::put_varint(out, it->second);
                out += best_pkt[1];                          // flags; version and opcode are constants
                semcodec::detail::put_varint(out, uint8_t(best_pkt[6]) | uint8_t(best_pkt[7]) << 8);
                semcodec::detail::put_varint(out, best_pkt.size() - 8); out.append(best_pkt, 8, std::string::npos);
            }
            detail::push_ref(sh, std::move(nref));
        }
        return out;
    }
    // [DECODE_ONCE_V1] rows given as decoded bags (the memory's arrays): nothing parsed. A new shape travels as kind 3 --
    // its field names, then all its values as one packet -- so the reader learns it without parsing text either.
    // [FORWARD_CHANGES_V1] prev/changed: the array the row's writer made its difference against, and the fields it
    // changed (the memory keeps them with the row). The references here are positional: the arrays this session gave
    // its reader, newest first -- the same rows the reader's Decoder holds. When the writer's array is one of them, the
    // writer's changed fields ARE this row's difference against it: nothing is compared and nothing is copied. Otherwise
    // the row is compared with each reference by position, in place. Same records, same bytes, as comparing Dicts did.
    struct RowIn { uint64_t id; double updated; const std::string* service; const std::string* variable; const std::string* instance; ribbit::BagArrP bag;
                   ribbit::BagArrP prev = nullptr; const ribbit::Fwd* changed = nullptr; bool gone = false; };
    std::string encode_rows(const std::vector<RowIn>& rows) {
        std::string out; out.reserve(rows.size() * 48 + 64);
        for (const RowIn& r : rows) {
            if (r.gone) {                                    // [GONE_WITH_ITS_WRITER_V1]
                out += char(GONE); semcodec::detail::put_varint(out, r.id);
                for (const std::string* c : {r.service, r.variable, r.instance}) { semcodec::detail::put_varint(out, c->size()); out += *c; }
                continue;
            }
            const ribbit::BagArr& b = *r.bag;
            const std::vector<std::string>* names = b.names.get();
            auto pi = by_ptr_.find(names);
            uint16_t sn;
            if (pi != by_ptr_.end()) sn = pi->second;
            else {
                std::string key; for (auto& n : *names) { key += n; key += '\x1f'; }
                auto it = idx_.find(key);
                if (it == idx_.end()) {
                    if (shapes_.size() >= 0xFFFF) throw std::runtime_error("rowcodec: too many row shapes on one session");
                    detail::Refs sh; for (auto& n : *names) sh.fo.push_back("bag." + n);
                    for (const char* c : {"id", "instance", "service", "updated", "variable"}) sh.fo.push_back(c);
                    sn = uint16_t(shapes_.size()); shapes_.push_back(std::move(sh)); idx_.emplace(key, sn); fresh_.push_back(true);
                } else sn = it->second;
                by_ptr_.emplace(names, sn); keep_.push_back(b.names);   // the pointer stays valid while it is a key
            }
            if (pring_.size() <= sn) pring_.resize(size_t(sn) + 1);
            detail::Refs& sh = shapes_[sn];
            const size_t nb = names->size();
            // the row's coordinates as values, in the shape's order after the bag: id instance service updated variable
            PRef me; me.bag = r.bag;
            me.co[0] = pyv::Int::of(int64_t(r.id)); me.co[1] = Str{*r.instance}; me.co[2] = Str{*r.service};
            me.co[3] = r.updated; me.co[4] = Str{*r.variable};
            auto& ring = pring_[sn];
            if (fresh_[sn]) {                                 // a new shape: names, then the whole row as values
                fresh_[sn] = false;
                semcodec::Fields f; f.reserve(sh.fo.size());
                for (size_t k = 0; k < b.vals.size() && k < sh.fo.size(); ++k) f.emplace_back(sh.fo[k], b.vals[k]);
                for (int c = 0; c < 5; ++c) f.emplace_back(sh.fo[nb + size_t(c)], me.co[c]);
                const std::string pkt = semcodec::encode_request(0, {}, f, true);
                out += char(3); semcodec::detail::put_varint(out, sn); semcodec::detail::put_varint(out, sh.fo.size());
                for (auto& n : sh.fo) { semcodec::detail::put_varint(out, n.size()); out += n; }
                out += pkt[1]; semcodec::detail::put_varint(out, uint8_t(pkt[6]) | uint8_t(pkt[7]) << 8);
                semcodec::detail::put_varint(out, pkt.size() - 8); out.append(pkt, 8, std::string::npos);
                push_pref(ring, std::move(me));
                continue;
            }
            static const Obj none{pyv::None{}};
            auto bag_at = [&](const PRef& x, size_t i) -> const Obj& { return i < x.bag->vals.size() ? x.bag->vals[i] : none; };
            std::vector<semcodec::detail::ChangedField> ch; ch.reserve(nb + 5);
            size_t best = 0; bool forwarded = false;
            if (r.prev && r.changed && b.vals.size() == nb && r.prev->vals.size() == nb)
                for (size_t k = 0; k < ring.size(); ++k) if (ring[k].bag.get() == r.prev.get()) { best = k; forwarded = true; break; }
            if (forwarded) {                                  // the writer's difference is this row's: only its fields
                const PRef& rf = ring[best];
                // [SHARED_ENCODE_V1] another reader's encoder made this row's packet already: the same bytes
                if (const std::string* pk = r.changed->pkt.load(std::memory_order_acquire)) {
                    c_forwarded++; c_shared++;
                    if (pk->empty()) { out += char(2 | best << 2); semcodec::detail::put_varint(out, sn); }
                    else { out += char(1 | best << 2); semcodec::detail::put_varint(out, sn);
                           out += (*pk)[1]; semcodec::detail::put_varint(out, uint8_t((*pk)[6]) | uint8_t((*pk)[7]) << 8);
                           semcodec::detail::put_varint(out, pk->size() - 8); out.append(*pk, 8, std::string::npos); }
                    push_pref(ring, std::move(me));
                    continue;
                }
                for (uint16_t i : r.changed->idx) if (i < nb) ch.push_back(semcodec::detail::ChangedField{i, &b.vals[i], &bag_at(rf, i)});
                c_forwarded++;
            } else {                                          // compared by position, in place
                size_t most = 0;
                for (size_t k = 0; k < ring.size(); ++k) {
                    const PRef& rf = ring[k]; size_t eq = 0;
                    for (size_t i = 0; i < nb; ++i) if (semcodec::values_equal(i < b.vals.size() ? b.vals[i] : none, bag_at(rf, i))) ++eq;
                    for (int c = 0; c < 5; ++c) if (semcodec::values_equal(me.co[c], rf.co[c])) ++eq;
                    if (eq > most || k == 0) { most = eq; best = k; }
                    if (eq == nb + 5) break;
                }
                const PRef& rf = ring[best];
                for (size_t i = 0; i < nb; ++i) { const Obj& v = i < b.vals.size() ? b.vals[i] : none;
                    if (!semcodec::values_equal(v, bag_at(rf, i))) ch.push_back(semcodec::detail::ChangedField{i, &v, &bag_at(rf, i)}); }
                c_compared++;
            }
            const PRef& rf = ring[best];
            for (int c = 0; c < 5; ++c) if (!semcodec::values_equal(me.co[c], rf.co[c])) ch.push_back(semcodec::detail::ChangedField{nb + size_t(c), &me.co[c], &rf.co[c]});
            if (forwarded) {                                  // [SHARED_ENCODE_V1] kept for the other readers ("" = identical)
                auto* mine = new std::string(ch.empty() ? std::string() : semcodec::detail::encode_changed(0, ch, true));
                const std::string* none = nullptr;
                if (!r.changed->pkt.compare_exchange_strong(none, mine, std::memory_order_acq_rel)) delete mine;
            }
            if (ch.empty()) { out += char(2 | best << 2); semcodec::detail::put_varint(out, sn); }
            else {
                const std::string* shared = forwarded ? r.changed->pkt.load(std::memory_order_acquire) : nullptr;
                const std::string pkt = shared ? *shared : semcodec::detail::encode_changed(0, ch, true);
                out += char(1 | best << 2); semcodec::detail::put_varint(out, sn);
                out += pkt[1]; semcodec::detail::put_varint(out, uint8_t(pkt[6]) | uint8_t(pkt[7]) << 8);
                semcodec::detail::put_varint(out, pkt.size() - 8); out.append(pkt, 8, std::string::npos);
            }
            push_pref(ring, std::move(me));
        }
        return out;
    }
    // [DIAG-ROWS] rows sent by forwarding their writer's changes, and by comparing
    uint64_t c_forwarded = 0, c_compared = 0, c_shared = 0;   // c_shared: forwarded rows whose packet another reader made
private:
    struct PRef { ribbit::BagArrP bag; Obj co[5]; };   // a row given: its bag array and its coordinates as values
    static void push_pref(std::vector<PRef>& ring, PRef x) { ring.insert(ring.begin(), std::move(x)); if (ring.size() > ROW_REFS) ring.pop_back(); }
    std::vector<std::vector<PRef>> pring_;               // per shape (rows given as arrays): newest first
    std::vector<detail::Refs> shapes_; std::unordered_map<std::string, uint16_t> idx_;
    std::unordered_map<const void*, uint16_t> by_ptr_; std::vector<std::shared_ptr<const std::vector<std::string>>> keep_; std::vector<bool> fresh_;
};

// [DECODE_ONCE_V1] a row as the reader is given it: its coordinates and its bag, decoded once
struct Row { uint64_t id = 0; double updated = 0; std::string service, variable, instance; ribbit::BagArrP bag;
             bool gone = false; };   // [GONE_WITH_ITS_WRITER_V1] gone: no bag
using Rows = std::vector<Row>;
// the client's side: one per session, used only by the session's reader.
// [POSITIONAL_DECODE_V1] A row is rebuilt by position, never by name: its shape's field i is either the bag's value k or
// one of the row's coordinates, learned once per shape. The reference a difference is against is the previous row
// itself -- its bag array (shared with whoever was given that row) and its coordinates. A difference copies the
// reference's values once and overwrites the ones that changed; an identical row is given the reference's own array.
// No key strings are built, no dictionary is made, nothing is copied twice.
class Decoder {
public:
    Rows decode_rows(std::string_view recs) {
        Rows out;
        size_t p = 0;
        while (p < recs.size()) {
            const uint8_t head = uint8_t(recs[p++]), kind = head & 3; const size_t k = head >> 2;
            const uint64_t n = semcodec::detail::get_varint(recs, p);
            if (head == GONE) {                               // [GONE_WITH_ITS_WRITER_V1] a row that is gone: its coordinates
                Row g; g.id = n; g.gone = true;
                for (std::string* c : {&g.service, &g.variable, &g.instance}) { const size_t l = size_t(semcodec::detail::get_varint(recs, p));
                    if (p + l > recs.size()) throw std::runtime_error("rowcodec: cut short");
                    c->assign(recs.substr(p, l)); p += l; }
                out.push_back(std::move(g)); continue;
            }
            Shape* sh = nullptr; Ref row;
            if (head == 3) {                                  // kind 3: a new shape, by its names, then its values
                if (n != shapes_.size()) throw std::runtime_error("rowcodec: a new row shape out of order");
                std::vector<std::string> fo; const uint64_t nfo = semcodec::detail::get_varint(recs, p);
                for (uint64_t i = 0; i < nfo; ++i) { const size_t l = size_t(semcodec::detail::get_varint(recs, p));
                    if (p + l > recs.size()) throw std::runtime_error("rowcodec: cut short");
                    fo.emplace_back(recs.substr(p, l)); p += l; }
                shapes_.push_back(learn(std::move(fo))); sh = &shapes_.back();
                row = apply(*sh, recs, p, nullptr);
            } else if (kind == 0) {                           // a new shape as text
                if (n != shapes_.size()) throw std::runtime_error("rowcodec: a new row shape out of order");
                const size_t len = size_t(semcodec::detail::get_varint(recs, p));
                if (p + len > recs.size()) throw std::runtime_error("rowcodec: cut short");
                auto parsed = semtpl::detail::json_try(recs.substr(p, len)); p += len;
                if (!parsed || !parsed->is<Dict>()) throw std::runtime_error("rowcodec: a new row shape that is not a JSON object");
                std::vector<std::string> fo = detail::field_order_of(*parsed);
                semcodec::Fields f = detail::fields_of(*parsed, fo);
                shapes_.push_back(learn(std::move(fo))); sh = &shapes_.back();
                row = blank(*sh);
                for (size_t i = 0; i < f.size(); ++i) put(*sh, row, i, std::move(f[i].second));
            } else if (kind == 1 || kind == 2) {
                if (n >= shapes_.size()) throw std::runtime_error("rowcodec: a row of a shape never learned");
                sh = &shapes_[n];
                if (k >= sh->ring.size()) throw std::runtime_error("rowcodec: a row against a reference never held");
                row = kind == 2 ? sh->ring[k] : apply(*sh, recs, p, &sh->ring[k]);
            } else throw std::runtime_error("rowcodec: unknown record kind " + std::to_string(kind));
            out.push_back(coordinates(*sh, row));
            sh->ring.insert(sh->ring.begin(), std::move(row)); if (sh->ring.size() > ROW_REFS) sh->ring.pop_back();
        }
        return out;
    }
    // the rows in the platform's row layout, each bag as JSON text: for a reader that asks for text
    std::string decode(std::string_view recs) {
        std::string out;
        for (auto& r : decode_rows(recs)) { uint64_t ub; std::memcpy(&ub, &r.updated, 8); detail::put_raw(out, r.id, ub, r.service, r.variable, r.instance, r.gone ? std::string("null") : r.bag->json()); }
        return out;
    }
private:
    enum : int { C_ID, C_UPDATED, C_SERVICE, C_VARIABLE, C_INSTANCE, C_N };
    struct Ref { ribbit::BagArrP bag; std::vector<Obj> coord; };    // a row as held: its bag (shared) and coordinates
    struct Shape {
        std::vector<std::string> fo;                      // the field order (names only for messages)
        std::shared_ptr<const std::vector<std::string>> names;   // the bag's names, shared by every row of the shape
        std::vector<int> slot;                            // field i: >= 0 the bag's value; < 0, coordinate -(c + 1)
        std::vector<Ref> ring;                            // newest first, at most ROW_REFS
    };
    std::vector<Shape> shapes_;
    static Shape learn(std::vector<std::string> fo) {
        Shape s; auto names = std::make_shared<std::vector<std::string>>(); s.slot.reserve(fo.size());
        for (const std::string& f : fo) {
            if (f.rfind("bag.", 0) == 0) { s.slot.push_back(int(names->size())); names->push_back(f.substr(4)); }
            else s.slot.push_back(f == "id" ? -(C_ID + 1) : f == "updated" ? -(C_UPDATED + 1) : f == "service" ? -(C_SERVICE + 1)
                                  : f == "variable" ? -(C_VARIABLE + 1) : f == "instance" ? -(C_INSTANCE + 1) : -(C_N + 1));
        }
        s.fo = std::move(fo); s.names = std::move(names);
        return s;
    }
    static Ref blank(const Shape& sh) {
        auto b = std::make_shared<ribbit::BagArr>(); b->names = sh.names; b->vals.assign(sh.names->size(), Obj(pyv::None{}));
        Ref r; r.bag = std::move(b); r.coord.assign(C_N, Obj(pyv::None{})); return r;
    }
    static Obj& at(const Shape& sh, Ref& r, size_t i) {
        static thread_local Obj spare;                     // a field that is neither the bag's nor a coordinate
        if (i >= sh.slot.size()) throw std::runtime_error("rowcodec: a field beyond its shape");
        const int s = sh.slot[i];
        if (s >= 0) return const_cast<ribbit::BagArr&>(*r.bag).vals[size_t(s)];
        const int c = -s - 1; if (c >= C_N) { spare = pyv::None{}; return spare; }
        return r.coord[size_t(c)];
    }
    static void put(const Shape& sh, Ref& r, size_t i, Obj v) { at(sh, r, i) = std::move(v); }
    // a difference (or a whole row) applied by position: the reference's values copied once, the changed ones replaced
    static Ref apply(const Shape& sh, std::string_view recs, size_t& p, const Ref* ref) {
        if (p >= recs.size()) throw std::runtime_error("rowcodec: cut short");
        const uint8_t flags = uint8_t(recs[p++]); const uint64_t nf = semcodec::detail::get_varint(recs, p);
        const size_t len = size_t(semcodec::detail::get_varint(recs, p));
        if (p + len > recs.size() || nf > 0xFFFF) throw std::runtime_error("rowcodec: cut short");
        std::string_view fb = recs.substr(p, len); p += len;
        std::string plain; if (flags & semcodec::FLAG_COMPRESSED) { plain = semcodec::lz4_decompress(fb); fb = plain; }
        const bool diff = (flags & semcodec::FLAG_DIFF) != 0;
        std::vector<semcodec::detail::Splice> splices;
        semcodec::detail::Vals vals = semcodec::detail::decode_fieldblock(fb, size_t(nf), diff ? &splices : nullptr);
        Ref r;
        if (ref && diff) {                                // the reference's row, to be changed
            auto b = std::make_shared<ribbit::BagArr>(*ref->bag); r.bag = std::move(b); r.coord = ref->coord;
        } else r = blank(sh);
        Ref empty; const Ref& rf = ref ? *ref : empty;
        for (auto& kv : vals) put(sh, r, kv.first, std::move(kv.second));
        for (auto& sp : splices) {                        // [STRING_SPLICE_V1] [NUMBER_DELTA_V1] against the reference's value
            if (!ref) throw std::runtime_error("rowcodec: a splice without a reference");
            const Obj& rv = at(sh, const_cast<Ref&>(rf), sp.idx);
            using semcodec::TYPE_INT_DELTA; using semcodec::TYPE_FLOAT_DELTA; using semcodec::TYPE_DECSTR_DELTA; using semcodec::detail::decstr;
            if (sp.kind == TYPE_INT_DELTA) { if (!rv.is<pyv::Int>() || rv.as<pyv::Int>().big) throw std::runtime_error("rowcodec: an integer delta without its reference integer");
                put(sh, r, sp.idx, pyv::Int::of(int64_t(uint64_t(rv.as<pyv::Int>().v) + uint64_t(sp.d)))); continue; }
            if (sp.kind == TYPE_FLOAT_DELTA) { if (!rv.is<double>()) throw std::runtime_error("rowcodec: a float delta without its reference float");
                uint64_t u; const double y = rv.as<double>(); std::memcpy(&u, &y, 8); u += uint64_t(sp.d); double x; std::memcpy(&x, &u, 8); put(sh, r, sp.idx, x); continue; }
            if (sp.kind == TYPE_DECSTR_DELTA) { int64_t b; if (!rv.is<Str>() || !decstr(rv.as<Str>().s, b)) throw std::runtime_error("rowcodec: a decimal delta without its reference number");
                put(sh, r, sp.idx, Str{std::to_string(b + sp.d)}); continue; }
            if (!rv.is<Str>()) throw std::runtime_error("rowcodec: a splice without its reference string (field " + (sp.idx < sh.fo.size() ? sh.fo[sp.idx] : std::string("?")) + ")");
            const std::string& o = rv.as<Str>().s;
            if (sp.pre + sp.suf > o.size()) throw std::runtime_error("rowcodec: a splice longer than its reference");
            std::string s; s.reserve(sp.pre + sp.mid.size() + sp.suf); s.append(o, 0, sp.pre); s += sp.mid; s.append(o, o.size() - sp.suf, sp.suf);
            put(sh, r, sp.idx, Str{std::move(s)});
        }
        return r;
    }
    static Row coordinates(const Shape&, const Ref& r) {
        Row out;
        const Obj& id = r.coord[C_ID];
        if (!id.is<pyv::Int>()) throw std::runtime_error("rowcodec: a row without its id");
        { const auto& iv = id.as<pyv::Int>(); out.id = iv.big ? uint64_t(std::stoull(iv.dec)) : uint64_t(iv.v); }
        const Obj& u = r.coord[C_UPDATED];
        out.updated = u.is<double>() ? u.as<double>() : u.is<pyv::Int>() && !u.as<pyv::Int>().big ? double(u.as<pyv::Int>().v) : 0.0;
        auto str = [&](int c) { const Obj& v = r.coord[size_t(c)]; return v.is<Str>() ? v.as<Str>().s : std::string(); };
        out.service = str(C_SERVICE); out.variable = str(C_VARIABLE); out.instance = str(C_INSTANCE);
        out.bag = r.bag;
        return out;
    }
};
// [READ_REQUESTS_BY_REFERENCE_V1] The request for rows (a read after a write id, a standing trigger) names the same
// family again and again; only its position moves. The first request for a family on a session travels whole and is
// given the next slot number on both ends; every later one is its slot and the change in position and wait.
// Request body (after MAGIC + op): u16+service, u16+variable, u32+instance, u64 after, u32 wait_ms, u32 fresh_s,
// u32 min_rows. Record: u8 0 + the body (a new slot), or u8 1 + varint slot + zigzag varint (after - previous after)
// + varint wait_ms.
namespace detail {
inline void put_varint(std::string& o, uint64_t v) { while (v >= 0x80) { o += char(uint8_t(v) | 0x80); v >>= 7; } o += char(uint8_t(v)); }
inline uint64_t get_varint(std::string_view s, size_t& p) {
    uint64_t v = 0; int sh = 0;
    for (;;) { if (p >= s.size() || sh > 63) throw std::runtime_error("rowcodec: a request cut short");
               const uint8_t b = uint8_t(s[p++]); v |= uint64_t(b & 0x7F) << sh; if (!(b & 0x80)) return v; sh += 7; }
}
struct ReadReq { std::string key; uint64_t after = 0; uint32_t wait = 0; std::string head; };   // head: the body up to `after`
inline ReadReq parse_req(std::string_view b) {
    size_t p = 0; ReadReq r;
    const size_t a = size_t(get(b, p, 2)); p += a; const size_t v = size_t(get(b, p, 2)); p += v; const size_t i = size_t(get(b, p, 4)); p += i;
    if (p + 20 > b.size()) throw std::runtime_error("rowcodec: a request cut short");
    r.head.assign(b.substr(0, p)); r.after = get(b, p, 8); r.wait = uint32_t(get(b, p, 4));
    r.key = r.head; r.key.append(b.substr(p, 8));      // the family, its freshness and its minimum: what the slot stands for
    return r;
}
}  // namespace detail
class RequestEncoder {                                   // the client's sender only
public:
    std::string encode(std::string_view body) {
        detail::ReadReq r = detail::parse_req(body);
        auto it = slots_.find(r.key);
        std::string o;
        if (it == slots_.end()) { const uint64_t n = slots_.size(); slots_.emplace(r.key, Slot{n, r.after}); o += char(0); o.append(body); return o; }
        o += char(1); detail::put_varint(o, it->second.n);
        const int64_t d = int64_t(r.after - it->second.after); detail::put_varint(o, uint64_t((d << 1) ^ (d >> 63)));
        detail::put_varint(o, r.wait); it->second.after = r.after;
        return o;
    }
private:
    struct Slot { uint64_t n, after; };
    std::unordered_map<std::string, Slot> slots_;
};
class RequestDecoder {                                   // the host's reader only
public:
    std::string decode(std::string_view rec) {
        if (rec.empty()) throw std::runtime_error("rowcodec: an empty request");
        if (rec[0] == 0) { std::string_view body = rec.substr(1); detail::ReadReq r = detail::parse_req(body);
                           slots_.push_back(Slot{r.head, r.key.substr(r.head.size()), r.after}); return std::string(body); }
        if (rec[0] != 1) throw std::runtime_error("rowcodec: unknown request kind");
        size_t p = 1; const uint64_t n = detail::get_varint(rec, p);
        if (n >= slots_.size()) throw std::runtime_error("rowcodec: a request for a slot never named");
        Slot& sl = slots_[n];
        const uint64_t z = detail::get_varint(rec, p); const int64_t d = int64_t(z >> 1) ^ -int64_t(z & 1);
        sl.after = uint64_t(int64_t(sl.after) + d); const uint64_t wait = detail::get_varint(rec, p);
        std::string body = sl.head; detail::put(body, sl.after, 8); detail::put(body, wait, 4); body += sl.tail;
        return body;
    }
private:
    struct Slot { std::string head, tail; uint64_t after; };
    std::vector<Slot> slots_;
};
}  // namespace rowcodec

