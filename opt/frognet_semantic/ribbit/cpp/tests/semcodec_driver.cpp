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
// semcodec oracle driver: one command per stdin line, one answer per stdout line (see tools/test_semcodec_oracle.py for
// the commands and the canonical value form).
#include "semcodec.hpp"
#include <cstring>
#include <iostream>
#include <sstream>

using namespace semcodec;

static int nib(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    throw std::runtime_error("driver: bad hex digit");
}
static std::string unhex(std::string_view h) {
    if (h == ".") return {};
    if (h.size() % 2) throw std::runtime_error("driver: odd hex");
    std::string o(h.size() / 2, '\0');
    for (size_t i = 0; i < o.size(); ++i) o[i] = char(nib(h[2 * i]) << 4 | nib(h[2 * i + 1]));
    return o;
}
static std::string tohex(std::string_view b) {
    static const char* d = "0123456789abcdef";
    std::string o(b.size() * 2, '\0');
    for (size_t i = 0; i < b.size(); ++i) { o[2 * i] = d[uint8_t(b[i]) >> 4]; o[2 * i + 1] = d[uint8_t(b[i]) & 15]; }
    return o;
}

struct P {  // canonical-form parser
    std::string_view s; size_t i = 0;
    char peek() const { return i < s.size() ? s[i] : '\0'; }
    void want(char c) { if (peek() != c) throw std::runtime_error(std::string("driver: expected ") + c); ++i; }
    std::string hexrun() { size_t j = i; while (i < s.size() && std::isxdigit(uint8_t(s[i]))) ++i; return unhex(s.substr(j, i - j)); }
    std::string dec() { size_t j = i; if (peek() == '-') ++i; while (i < s.size() && std::isdigit(uint8_t(s[i]))) ++i;
                        return std::string(s.substr(j, i - j)); }
    Obj val() {
        char c = s[i++];
        switch (c) {
        case 'N': return None{};
        case 'B': return bool(s[i++] == '1');
        case 'I': return mkint(dec());
        case 'F': { std::string b = hexrun(); uint64_t u = 0; for (char x : b) u = u << 8 | uint8_t(x);
                    double d; std::memcpy(&d, &u, 8); return d; }
        case 'S': return Str{hexrun()};
        case 'R': return Bytes{hexrun()};
        case 'L': { List l; want('('); while (peek() != ')') { l.push_back(val()); if (peek() == ',') ++i; } ++i; return l; }
        case 'D': { Dict d; want('('); while (peek() != ')') { want('S'); std::string k = hexrun(); want('='); d.set(k, val());
                    if (peek() == ',') ++i; } ++i; return d; }
        }
        throw std::runtime_error(std::string("driver: bad value tag ") + c);
    }
    Fields fields() {
        Fields f; want('Q'); want('(');
        while (peek() != ')') { want('S'); std::string k = hexrun(); want('='); f.emplace_back(k, val()); if (peek() == ',') ++i; }
        ++i; return f;
    }
    static Int mkint(const std::string& d) {
        errno = 0; char* e = nullptr; long long x = std::strtoll(d.c_str(), &e, 10);
        if (errno == 0 && *e == '\0') return Int::of(x);
        Int r; r.big = true; r.dec = d; return r;
    }
};

static std::string cv(const Obj& o);
static std::string cfield(const std::string& k, const Obj& v) { return "S" + tohex(k) + "=" + cv(v); }
static std::string cv(const Obj& o) {
    if (o.is<None>()) return "N";
    if (o.is<bool>()) return o.as<bool>() ? "B1" : "B0";
    if (o.is<Int>()) return "I" + o.as<Int>().str();
    if (o.is<double>()) {
        uint64_t u; double d = o.as<double>(); std::memcpy(&u, &d, 8);
        std::string b(8, '\0');
        for (int k = 0; k < 8; ++k) b[k] = char(u >> (56 - 8 * k));
        return "F" + tohex(b);
    }
    if (o.is<Str>()) return "S" + tohex(o.as<Str>().s);
    if (o.is<Bytes>()) return "R" + tohex(o.as<Bytes>().b);
    std::string r;
    if (o.is<List>()) {
        r = "L(";
        bool f = true;
        for (auto& x : o.as<List>()) { if (!f) r += ','; f = false; r += cv(x); }
        return r + ")";
    }
    const Dict& d = o.as<Dict>(); r = "D(";
    for (size_t k = 0; k < d.size(); ++k) { if (k) r += ','; r += cfield(d.keys[k], d.vals[k]); }
    return r + ")";
}
static std::string cq(const Fields& f) {
    std::string r = "Q(";
    for (size_t k = 0; k < f.size(); ++k) { if (k) r += ','; r += cfield(f[k].first, f[k].second); }
    return r + ")";
}
static std::vector<std::string> names(const std::string& tok) {
    P p{tok}; Obj l = p.val(); std::vector<std::string> n;
    for (auto& x : l.as<List>()) n.push_back(x.as<Str>().s);
    return n;
}
static std::optional<Dict> ref(const std::string& tok) {
    if (tok == "N") return std::nullopt;
    P p{tok}; return p.val().as<Dict>();
}

static std::string command(const std::vector<std::string>& a) {
    const std::string& c = a.at(0);
    auto F = [&](size_t i) { P p{a.at(i)}; return p.fields(); };
    auto U = [&](size_t i) { return uint64_t(std::stoull(a.at(i))); };
    auto diff = [](const DiffResult& d) { return "ok " + (d.bytes.empty() ? std::string(".") : tohex(d.bytes)) + " " +
                                               cv(Obj(d.reference)) + " " + (d.identical ? "1" : "0"); };
    if (c == "enc_req") return "ok " + tohex(encode_request(U(1), F(3), F(4), U(2) != 0));
    if (c == "enc_rep") return "ok " + tohex(encode_reply(U(1), F(3), U(2) != 0));
    if (c == "enc_req_diff") { auto r = ref(a.at(5)); return diff(encode_request_diff(U(1), F(3), F(4), r ? &*r : nullptr, U(2) != 0)); }
    if (c == "enc_rep_diff") { auto r = ref(a.at(4)); return diff(encode_reply_diff(U(1), F(3), r ? &*r : nullptr, U(2) != 0)); }
    if (c == "enc_err") return "ok " + tohex(encode_error_reply(P::mkint(a.at(1)), unhex(a.at(2))));
    if (c == "dec_err") {
        std::string b = unhex(a.at(1)); auto r = decode_error_reply(b);
        return r ? "ok I" + r->first.str() + " S" + tohex(r->second) : "none";
    }
    if (c == "dec_req") {
        std::string b = unhex(a.at(1)); auto r = ref(a.at(4));
        auto out = decode_request(b, names(a.at(2)), names(a.at(3)), r ? &*r : nullptr);
        return "ok " + cq(out.first) + " " + cq(out.second);
    }
    if (c == "dec_rep") { std::string b = unhex(a.at(1)); auto r = ref(a.at(3)); return "ok " + cq(decode_reply(b, names(a.at(2)), r ? &*r : nullptr)); }
    if (c == "veq") { P p{a.at(1)}, q{a.at(2)}; Obj x = p.val(), y = q.val(); return std::string("ok ") + (values_equal(x, y) ? "1" : "0"); }
    if (c == "lz4smart") { auto r = lz4_smart(unhex(a.at(1))); return "ok " + (r.first.empty() ? std::string(".") : tohex(r.first)) + (r.second ? " 1" : " 0"); }
    if (c == "lz4dec") { std::string r = lz4_decompress(unhex(a.at(1))); return "ok " + (r.empty() ? std::string(".") : tohex(r)); }
    throw std::runtime_error("driver: unknown command " + c);
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::vector<std::string> a; for (std::string t; in >> t;) a.push_back(t);
        std::string out;
        try { out = command(a); }
        catch (const CpyValueError&) { out = "raise value *"; }
        catch (const ValueError& e) { out = std::string("raise value ") + e.what(); }
        catch (const StructError&) { out = "raise struct"; }
        catch (const TypeError&) { out = "raise type"; }
        catch (const OverflowError&) { out = "raise overflow"; }
        catch (const EncodeError&) { out = "raise encode"; }
        catch (const JsonError&) { out = "raise json"; }
        catch (const IndexError&) { out = "raise index"; }
        catch (const RecursionError&) { out = "raise recursion"; }
        catch (const std::bad_alloc&) { out = "raise memory"; }   // the frame declared a size that cannot be allocated
        catch (const Lz4Error&) { out = "raise lz4"; }
        catch (const std::exception& e) { out = std::string("raise other:") + e.what(); }
        std::cout << out << '\n';
    }
    return 0;
}
