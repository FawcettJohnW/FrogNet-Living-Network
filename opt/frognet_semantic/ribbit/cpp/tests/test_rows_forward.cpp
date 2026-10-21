// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_rows_forward.cpp -- [FORWARD_CHANGES_V1] [POSITIONAL_WRITE_V1] oracle.
//
// Writers' cell writes (cellwire::Encoder, as a box sends them) are decoded as the memory decodes them (cellwire::decode:
// positional, with the indices each write changed), then given to a reader's session encoder (rowcodec::Encoder::
// encode_rows) twice: once with each row's writer reference and changed fields (forwarded), once without (compared).
// Rows of several writers and families interleave, shapes differ, and the reader skips some rows (a reader that fell
// behind), so both paths run.
//   1. forwarding costs no wire size: the forwarded encoding is within 2% of the compared one in total bytes (they
//      differ by which reference a row names -- the writer's own previous row, or the best-matching of the last 8)
//   2. the reader's Decoder rebuilds every row exactly as written (each value: same type, same bits)
//   3. the forwarded path is taken (and the compared path too, for skipped rows)
//   4. [SHARED_ENCODE_V1] a second reader, skipping other rows, rebuilds every row exactly too, and each forwarded row's
//      packet is made once, by whichever reader's encoder meets it first, and reused by the other
//
//   g++ -std=c++20 -O2 -pthread -Iinclude -Itests tests/test_rows_forward.cpp src/frogram.cpp -llz4 -lcrypto
#include "cellwire.hpp"
#include "rowcodec.hpp"
#include <cstdio>
#include <map>
#include <random>

using pyv::Obj; using pyv::Str; using pyv::Int;
static int fails = 0;
static void check(bool ok, const std::string& what) { std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str()); if (!ok) ++fails; }

int main() {
    std::mt19937_64 rng(20261019);
    const std::string MAGIC = "FNW1";
    cellwire::Encoder wenc;                                    // the box's session encoder (writers are threads; one here)
    const std::vector<std::string> foA = {"b", "e", "n", "p", "q", "t"};          // a publish-like shape
    const std::vector<std::string> foB = {"c", "flag", "l", "x"};                 // another shape
    const int FAMS = 5, N = 6000;
    std::map<int, std::unique_ptr<cellwire::Ref>> chain;      // the memory's chain reference per family
    std::map<int, uint64_t> nseq;
    std::map<int, Obj> lastq;
    struct Stored { uint64_t id; double updated; std::string sv, var, inst; ribbit::BagArrP arr, prev; cellwire::Changed changed; };
    std::vector<Stored> rows;
    uint64_t id = 0;
    for (int w = 0; w < N; ++w) {
        const int fam = int(rng() % FAMS); const bool shapeB = fam == 4;
        const std::string sv = "mqtt", var = "load/" + std::to_string(fam);
        const uint64_t n = ++nseq[fam];
        semcodec::Fields f;
        if (!shapeB) {
            f.emplace_back("b", Str{"box-a"});
            f.emplace_back("e", rng() % 7 ? Obj(Int::of(0)) : Obj(Int::of(int64_t(rng() % 100000))));
            f.emplace_back("n", Int::of(int64_t(n)));
            std::string p = "payload-" + std::to_string(rng() % 3) + "-" + std::string(size_t(rng() % 40), char('a' + rng() % 26));
            f.emplace_back("p", Str{p});
            Obj q = rng() % 5 ? (lastq.count(fam) ? lastq[fam] : Obj(Str{std::string(80, 'Q')})) : Obj(Str{std::string(size_t(60 + rng() % 40), char('A' + rng() % 26))});
            lastq[fam] = q; f.emplace_back("q", q);
            f.emplace_back("t", Str{var});
        } else {
            f.emplace_back("c", Int::of(int64_t(rng() % 3)));
            f.emplace_back("flag", bool(rng() % 2));
            f.emplace_back("l", rng() % 4 ? Obj(pyv::None{}) : Obj(double(rng() % 1000) / 7.0));
            f.emplace_back("x", Str{std::to_string(1000 + n)});   // a decimal string: travels as a delta
        }
        const std::string inst = "box-a." + std::to_string(n % 16384);
        const std::string frame = wenc.encode_fields(MAGIC, sv, var, inst, shapeB ? foB : foA, f, true);
        cellwire::Frame x = cellwire::parse(frame, 5);
        const cellwire::Ref* ref = chain.count(fam) ? chain[fam].get() : nullptr;
        ribbit::BagArrP arr; cellwire::Changed changed;
        ribbit::BagArrP prev = (ref && !(x.flags & cellwire::F_KEYS)) ? ref->arr : nullptr;
        chain[fam] = cellwire::decode(x, ref, arr, &changed);
        bool same = arr->vals.size() == f.size();             // the memory holds exactly what was written
        for (size_t i = 0; same && i < f.size(); ++i) same = semcodec::values_equal(arr->vals[i], f[i].second);
        if (!same) { check(false, "write " + std::to_string(w) + " decoded as written"); return 1; }
        rows.push_back(Stored{++id, 1.7e9 + double(id) / 1000.0, sv, var, x.inst, arr, prev, changed});
    }
    check(true, "every write decoded positionally as written (" + std::to_string(N) + " writes, " + std::to_string(FAMS) + " families)");

    // the reader: in batches, skipping ~3% of rows (fell behind); the same rows given to both encoders
    rowcodec::Encoder fwd, cmp, fwd2; rowcodec::Decoder dec, dec2;
    size_t given2 = 0, wrong2 = 0;
    size_t given = 0, wrong = 0, bytes_fwd = 0, bytes_cmp = 0;
    for (size_t p = 0; p < rows.size();) {
        const size_t batch = 1 + rng() % 12;
        std::vector<rowcodec::Encoder::RowIn> a, b, c2; std::vector<const Stored*> sent, sent2;
        for (size_t k = 0; k < batch && p < rows.size(); ++k, ++p) {
            const Stored& s = rows[p];
            if (rng() % 29 != 0) { sent2.push_back(&s); c2.push_back({s.id, s.updated, &s.sv, &s.var, &s.inst, s.arr, s.prev, s.changed.get()}); }
            if (rng() % 33 == 0) continue;                       // a row this reader never got
            sent.push_back(&s);
            a.push_back({s.id, s.updated, &s.sv, &s.var, &s.inst, s.arr, s.prev, s.changed.get()});
            b.push_back({s.id, s.updated, &s.sv, &s.var, &s.inst, s.arr});
        }
        if (!c2.empty()) {                                       // the second reader, after the first
            rowcodec::Rows g2 = dec2.decode_rows(fwd2.encode_rows(c2));
            if (g2.size() != sent2.size()) ++wrong2;
            else for (size_t k = 0; k < g2.size(); ++k) {
                const Stored& s = *sent2[k]; given2++;
                bool ok = g2[k].id == s.id && g2[k].instance == s.inst && g2[k].bag && g2[k].bag->vals.size() == s.arr->vals.size();
                for (size_t i = 0; ok && i < s.arr->vals.size(); ++i) ok = semcodec::values_equal(g2[k].bag->vals[i], s.arr->vals[i]);
                if (!ok) ++wrong2;
            }
        }
        if (a.empty()) continue;
        const std::string ea = fwd.encode_rows(a), eb = cmp.encode_rows(b);
        bytes_fwd += ea.size(); bytes_cmp += eb.size();
        rowcodec::Rows got = dec.decode_rows(ea);
        if (got.size() != sent.size()) { ++wrong; continue; }
        for (size_t k = 0; k < got.size(); ++k) {
            const Stored& s = *sent[k]; const rowcodec::Row& g = got[k]; given++;
            bool ok = g.id == s.id && g.instance == s.inst && g.service == s.sv && g.variable == s.var && g.updated == s.updated
                      && g.bag && g.bag->vals.size() == s.arr->vals.size();
            for (size_t i = 0; ok && i < s.arr->vals.size(); ++i) ok = semcodec::values_equal(g.bag->vals[i], s.arr->vals[i]);
            if (!ok && ++wrong <= 5) std::fprintf(stderr, "row id %llu decoded wrong\n", (unsigned long long)s.id);
        }
    }
    check(double(bytes_fwd) <= double(bytes_cmp) * 1.02, "forwarding costs no wire size: forwarded " + std::to_string(bytes_fwd) + " B, compared " + std::to_string(bytes_cmp) + " B");
    check(wrong == 0, "the reader rebuilt every row exactly (" + std::to_string(given) + " rows, " + std::to_string(wrong) + " wrong)");
    check(fwd.c_forwarded > 0 && fwd.c_compared > 0, "both paths ran: forwarded " + std::to_string(fwd.c_forwarded) + ", compared " + std::to_string(fwd.c_compared));
    check(wrong2 == 0 && fwd.c_shared + fwd2.c_shared > 0, "a second reader rebuilt every row exactly (" + std::to_string(given2) + " rows, " + std::to_string(wrong2) +
          " wrong); packets made once and reused by the other reader: " + std::to_string(fwd.c_shared + fwd2.c_shared));
    std::printf("ROWS-FORWARD %s: %d fail\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
