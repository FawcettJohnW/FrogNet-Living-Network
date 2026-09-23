// RESP2 / RESP3 wire. Request parsing (inline + multibulk, with Redis's protocol-error strings)
// and reply building with the RESP2 downgrade rules.
#pragma once
#include <string>
#include <vector>
#include <cstdint>
#include <cstdio>
#include <cstring>

struct ParseResult {
    bool fatal = false;            // protocol error: reply with err and close
    std::string err;               // "Protocol error: ..." (without the -ERR prefix)
    std::vector<std::vector<std::string>> cmds;
};

class RespParser {
public:
    size_t inline_max = 64 * 1024;             // PROTO_INLINE_MAX_SIZE
    int64_t max_bulk = 512LL * 1024 * 1024;    // proto-max-bulk-len
    int64_t max_multibulk = 1024 * 1024;
    bool authenticated = true;                 // unauthenticated limits: 10 args / 16k bulk
    std::string buf;
    void feed(const char* p, size_t n) { buf.append(p, n); }
    ParseResult drain();                        // parse as many complete commands as present
private:
    // multibulk state
    int64_t mb_left = -1;                       // -1: not inside a multibulk
    int64_t bulk_len = -1;
    std::vector<std::string> cur;
    size_t pos = 0;
    bool parse_inline(ParseResult& r, size_t nl);
    bool parse_multibulk(ParseResult& r);
};

// splits an inline request like sdssplitargs(); returns false on unbalanced quotes
bool split_args(const std::string& line, std::vector<std::string>& out);

class Reply {
public:
    int proto = 2;
    std::string out;
    void status(const std::string& s) { item(); out += '+'; out += s; out += "\r\n"; }
    void error(const std::string& s)  { item(); out += '-'; for (char ch : s) out += (ch == '\r' || ch == '\n') ? ' ' : ch; out += "\r\n"; }
    void integer(long long n)         { item(); char b[32]; int l = snprintf(b, sizeof b, ":%lld\r\n", n); out.append(b, l); }
    void bulk(const std::string& s)   { item(); char b[32]; int l = snprintf(b, sizeof b, "$%zu\r\n", s.size()); out.append(b, l); out += s; out += "\r\n"; }
    void bulk(const char* p, size_t n){ item(); char b[32]; int l = snprintf(b, sizeof b, "$%zu\r\n", n); out.append(b, l); out.append(p, n); out += "\r\n"; }
    void null()                       { item(); out += proto == 3 ? "_\r\n" : "$-1\r\n"; }
    void null_array()                 { item(); out += proto == 3 ? "_\r\n" : "*-1\r\n"; }
    void array(size_t n)              { item(n); char b[32]; int l = snprintf(b, sizeof b, "*%zu\r\n", n); out.append(b, l); }
    void map(size_t n)                { if (proto == 3) { item(n * 2); char b[32]; int l = snprintf(b, sizeof b, "%%%zu\r\n", n); out.append(b, l); } else array(n * 2); }
    void set(size_t n)                { if (proto == 3) { item(n); char b[32]; int l = snprintf(b, sizeof b, "~%zu\r\n", n); out.append(b, l); } else array(n); }
    void push(size_t n)               { if (proto == 3) { item(n); char b[32]; int l = snprintf(b, sizeof b, ">%zu\r\n", n); out.append(b, l); } else array(n); }
    void attribute(size_t n);          // RESP3: |n ; RESP2: the next n pairs are discarded
    void boolean(bool v)              { if (proto == 3) { item(); out += v ? "#t\r\n" : "#f\r\n"; } else integer(v ? 1 : 0); }
    void double_(double d);
    void bignum(const std::string& s) { if (proto == 3) { item(); out += '('; out += s; out += "\r\n"; } else bulk(s); }
    void verbatim(const std::string& fmt, const std::string& s);
    void ok() { status("OK"); }
    // raw text of a protocol-formatted double (used by RESP2 too)
    static std::string fmt_double(double d);
    // discard-tracking for RESP2 attributes
private:
    // stack of remaining item counts for aggregates being discarded (RESP2 attribute handling)
    std::vector<size_t> discard_;
    size_t mark_ = 0;        // out.size() at attribute start
    bool discarding_ = false;
    void item(size_t children = 0);
};
