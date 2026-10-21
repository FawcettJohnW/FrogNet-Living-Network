// pyval.hpp -- the value model of John's semantic codec, in C++: Python's None, bool, int, float, str, bytes, list and
// dict as core/codec.py sees them (values extracted by templates and values decoded off the wire).
//   int   -- int64 when it fits; otherwise its canonical decimal text (Python ints are unbounded; the codec rejects
//            them only where it packs one into a fixed field, and JSON carries them as digits).
//   str   -- UTF-8. A lone surrogate (which Python strings can hold, e.g. from a JSON "\ud800" escape) is kept as its
//            three-byte WTF-8 form; encoding such a string to UTF-8 raises EncodeError, as Python's strict encode does.
//   dict  -- insertion order, unique keys; assigning an existing key keeps its position and replaces the value.
// Exceptions carry Python's exception kinds so behaviour can be compared case for case with the reference.
#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace pyv {

struct ValueError : std::invalid_argument { using std::invalid_argument::invalid_argument; };   // text is John's
struct CpyValueError : ValueError { using ValueError::ValueError; };          // ValueError whose text is CPython's
struct StructError : std::range_error { using std::range_error::range_error; };          // struct.error
struct TypeError : std::invalid_argument { using std::invalid_argument::invalid_argument; };
struct OverflowError : std::range_error { using std::range_error::range_error; };
struct EncodeError : std::invalid_argument { using std::invalid_argument::invalid_argument; };  // UnicodeEncodeError
struct JsonError : std::invalid_argument { using std::invalid_argument::invalid_argument; };    // JSONDecodeError
struct IndexError : std::out_of_range { using std::out_of_range::out_of_range; };
struct RecursionError : std::runtime_error { using std::runtime_error::runtime_error; };
struct Lz4Error : std::runtime_error { using std::runtime_error::runtime_error; };             // lz4 RuntimeError

struct Int {
    bool big = false;   // true: |value| does not fit int64; dec holds the canonical decimal (no '+', no leading zeros)
    int64_t v = 0;
    std::string dec;
    static Int of(int64_t x) { Int i; i.v = x; return i; }
    std::string str() const { return big ? dec : std::to_string(v); }
    bool operator==(const Int& o) const { return big == o.big && (big ? dec == o.dec : v == o.v); }
};

struct Str { std::string s; };
struct Bytes { std::string b; };
struct Obj;
using List = std::vector<Obj>;
struct Dict {
    std::vector<std::string> keys;
    std::vector<Obj> vals;
    size_t size() const { return keys.size(); }
    long find(const std::string& k) const;
    void set(const std::string& k, Obj v);
};
struct None {};

struct Obj {
    std::variant<None, bool, Int, double, Str, Bytes, List, Dict> v;
    Obj() = default;
    template <class T> Obj(T x) : v(std::move(x)) {}
    template <class T> bool is() const { return std::holds_alternative<T>(v); }
    template <class T> const T& as() const { return std::get<T>(v); }
    template <class T> T& as() { return std::get<T>(v); }
};

inline long Dict::find(const std::string& k) const {
    for (size_t i = 0; i < keys.size(); ++i) if (keys[i] == k) return long(i);
    return -1;
}
inline void Dict::set(const std::string& k, Obj x) {
    long i = find(k);
    if (i >= 0) { vals[size_t(i)] = std::move(x); return; }
    keys.push_back(k); vals.push_back(std::move(x));
}

}  // namespace pyv
