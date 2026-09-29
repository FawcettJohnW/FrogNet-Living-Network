// semtpl oracle driver: one command per stdin line, one answer per stdout line. Commands and the canonical value form
// are defined in tools/test_semtpl_oracle.py (the value form is the one tools/semcodec_driver.cpp uses).
#include "semtpl.hpp"
#include <cstring>
#include <iostream>
#include <sstream>

using namespace semcodec;
using namespace semtpl;

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

static std::string S(std::string_view s) { return "S" + tohex(s); }
static std::string SL(const std::vector<std::string>& v) {
    std::string r = "L(";
    for (size_t k = 0; k < v.size(); ++k) { if (k) r += ','; r += S(v[k]); }
    return r + ")";
}
static std::string SP(const Pairs& v) {
    std::string r = "P(";
    for (size_t k = 0; k < v.size(); ++k) { if (k) r += ','; r += S(v[k].first) + "=" + S(v[k].second); }
    return r + ")";
}
static Obj V(const std::string& tok) { P p{tok}; return p.val(); }
static std::string str(const std::string& tok) { return V(tok).as<Str>().s; }
static Pairs pairs(const std::string& tok) {  // D(...) of str -> str, order kept
    Obj d = V(tok); Pairs out;
    for (size_t k = 0; k < d.as<Dict>().size(); ++k) out.emplace_back(d.as<Dict>().keys[k], d.as<Dict>().vals[k].as<Str>().s);
    return out;
}
static Pairs plist(const std::string& tok) {  // L(L(S,S),...) -> pairs with duplicates
    Obj l = V(tok); Pairs out;
    for (auto& x : l.as<List>()) out.emplace_back(x.as<List>().at(0).as<Str>().s, x.as<List>().at(1).as<Str>().s);
    return out;
}
static std::string learned(const Learned& l) {
    if (l.refused_empty) return "ok R " + SL(l.empty_at);
    return "ok T " + cv(Obj(l.req)) + " " + cv(Obj(l.resp));
}

static std::string command(const std::vector<std::string>& a) {
    const std::string& c = a.at(0);
    auto F = [&](size_t i) { P p{a.at(i)}; return p.fields(); };
    auto B = [&](size_t i) { return unhex(a.at(i)); };
    if (c == "urlparse") { auto u = urlparse(str(a.at(1)));
        return "ok " + S(u.scheme) + " " + S(u.netloc) + " " + S(u.path) + " " + S(u.params) + " " + S(u.query) + " " + S(u.fragment); }
    if (c == "parse_qsl") return "ok " + SP(parse_qsl(str(a.at(1))));
    if (c == "parse_qs") { auto q = parse_qs(str(a.at(1))); std::string r = "ok Q(";
        for (size_t k = 0; k < q.size(); ++k) { if (k) r += ','; r += S(q[k].first) + "=" + SL(q[k].second); }
        return r + ")"; }
    if (c == "urlencode") return "ok " + S(urlencode(plist(a.at(1))));
    if (c == "normalize") { auto r = normalize_path_for_semantics(str(a.at(1))); return "ok " + S(r.first) + " " + SL(r.second); }
    if (c == "dyn_vals") return "ok " + SP(extract_dynamic_query_vals(str(a.at(1)), names(a.at(2))));
    if (c == "dyn_shape") return "ok " + S(with_dynamic_shape(str(a.at(1)), names(a.at(2))));
    if (c == "canon_key") return "ok " + S(canonical_semantic_key(str(a.at(1)), str(a.at(2)), B(3)));
    if (c == "first_row") return "ok " + cv(first_row_only(V(a.at(1))));
    if (c == "empty_arrays") return "ok " + SL(empty_arrays_in(V(a.at(1))));
    if (c == "tpl_id") return "ok " + S(template_id(str(a.at(1)), str(a.at(2))));
    if (c == "opcode") return "ok " + std::to_string(opcode_for(str(a.at(1))));
    if (c == "pred") {
        std::string s = str(a.at(2)); const std::string& w = a.at(1); bool r;
        if (w == "ipv4") r = is_ipv4(s); else if (w == "iface") r = looks_like_interface(s);
        else if (w == "enum") r = looks_like_enum(s); else if (w == "host") r = is_plausible_host(s);
        else throw std::runtime_error("driver: bad pred " + w);
        return std::string("ok ") + (r ? "1" : "0");
    }
    if (c == "leaf") return "ok " + S(leaf_type(V(a.at(1)), str(a.at(2))));
    if (c == "sanitize") return "ok " + cv(sanitize_nonfinite(V(a.at(1))));
    if (c == "dumps") return "ok " + S(dumps_ascii(V(a.at(2)), a.at(1) == "1"));
    if (c == "schema") return "ok " + cv(infer_schema(V(a.at(1)), str(a.at(2))));
    if (c == "merge") { bool nx = a.at(1) == "-", ny = a.at(2) == "-"; Obj x = nx ? Obj() : V(a.at(1)), y = ny ? Obj() : V(a.at(2));
        return "ok " + cv(merge_schema(nx ? nullptr : &x, ny ? nullptr : &y)); }
    if (c == "fields") { auto r = field_order_and_type_map(V(a.at(1))); return "ok " + SL(r.first) + " " + cv(Obj(r.second)); }
    if (c == "rebuild_json") return "ok " + cv(rebuild_json(V(a.at(1)).as<List>(), names(a.at(2))));
    if (c == "learn") return "ok " + cv(Obj(learn(a.at(1), str(a.at(3)), a.at(2) == "1")));   // mode is a bare word
    if (c == "extract") { auto f = extract(V(a.at(1)).as<Dict>(), str(a.at(3)), a.at(2) == "1");
        return f ? "ok " + cq(*f) : std::string("ok H"); }   // H: the template cannot hold the body -> RAW
    if (c == "rebuild") return "ok " + S(rebuild_reply(V(a.at(1)).as<Dict>(), V(a.at(2)).as<List>()));
    if (c == "build_url") return "ok " + S(build_url(str(a.at(1)), F(2)));
    if (c == "rebuild_body") return "ok " + S(rebuild_body(V(a.at(1)).as<Dict>(), F(2)));
    if (c == "ctype") return "ok " + S(content_type(V(a.at(1)).as<Dict>()));
    if (c == "sniff") return "ok " + S(sniff_body_mode(B(1)));
    if (c == "detect_req") return "ok " + S(detect_request_mode(pairs(a.at(1)), B(2)));
    if (c == "detect_rep") return "ok " + S(detect_reply_mode(pairs(a.at(1)), B(2)));
    if (c == "train") return learned(learn_templates(str(a.at(1)), str(a.at(2)), pairs(a.at(3)), B(4), pairs(a.at(5)), B(6),
                                                     str(a.at(7))));
    throw std::runtime_error("driver: unknown command " + c);
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::vector<std::string> a; for (std::string t; in >> t;) a.push_back(t);
        std::string out;
        try { out = command(a); }
        catch (const Declined& e) { out = std::string("raise declined ") + e.what(); }
        catch (const NotPorted&) { out = "raise notported"; }
        catch (const semtpl::NotFound&) { out = "raise notfound"; }
        catch (const CpyValueError&) { out = "raise value *"; }
        catch (const ValueError& e) { std::string t = e.what(); out = "raise value " + t.substr(0, t.find(' ')); }
        catch (const StructError&) { out = "raise struct"; }
        catch (const TypeError&) { out = "raise type"; }
        catch (const OverflowError&) { out = "raise overflow"; }
        catch (const EncodeError&) { out = "raise encode"; }
        catch (const JsonError&) { out = "raise json"; }
        catch (const IndexError&) { out = "raise index"; }
        catch (const RecursionError&) { out = "raise recursion"; }
        catch (const Lz4Error&) { out = "raise lz4"; }
        catch (const std::exception& e) { out = std::string("raise other:") + e.what(); }
        std::cout << out << '\n';
    }
    return 0;
}
