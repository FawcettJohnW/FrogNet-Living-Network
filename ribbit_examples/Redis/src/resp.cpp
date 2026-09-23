#include "resp.hpp"
#include <cmath>
#include <cstdlib>
#include <cerrno>
#include <cctype>

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// sdssplitargs() semantics (sds.c)
bool split_args(const std::string& line, std::vector<std::string>& out) {
    size_t i = 0, n = line.size();
    while (true) {
        while (i < n && isspace((unsigned char)line[i])) i++;
        if (i >= n) return true;
        bool inq = false, insq = false, done = false;
        std::string cur;
        while (!done) {
            if (inq) {
                if (i >= n) return false;
                char c = line[i];
                if (c == '\\' && i + 3 < n && line[i+1] == 'x' && hexval(line[i+2]) >= 0 && hexval(line[i+3]) >= 0) {
                    cur += (char)(hexval(line[i+2]) * 16 + hexval(line[i+3])); i += 4; continue;
                }
                if (c == '\\' && i + 1 < n) {
                    char e = line[i+1]; char v;
                    switch (e) { case 'n': v = '\n'; break; case 'r': v = '\r'; break; case 't': v = '\t'; break;
                                 case 'b': v = '\b'; break; case 'a': v = '\a'; break; default: v = e; }
                    cur += v; i += 2; continue;
                }
                if (c == '"') {
                    if (i + 1 < n && !isspace((unsigned char)line[i+1])) return false;
                    done = true; i++; continue;
                }
                cur += c; i++;
            } else if (insq) {
                if (i >= n) return false;
                char c = line[i];
                if (c == '\\' && i + 1 < n && line[i+1] == '\'') { cur += '\''; i += 2; continue; }
                if (c == '\'') {
                    if (i + 1 < n && !isspace((unsigned char)line[i+1])) return false;
                    done = true; i++; continue;
                }
                cur += c; i++;
            } else {
                if (i >= n) { done = true; continue; }
                char c = line[i];
                switch (c) {
                    case ' ': case '\n': case '\r': case '\t': case '\0': done = true; break;
                    case '"': inq = true; i++; break;
                    case '\'': insq = true; i++; break;
                    default: cur += c; i++; break;
                }
            }
        }
        out.push_back(std::move(cur));
    }
}

bool RespParser::parse_inline(ParseResult& r, size_t nl) {
    // nl = index of '\n'
    size_t linelen = nl - pos;
    if (linelen && buf[nl-1] == '\r') linelen--;
    std::vector<std::string> args;
    if (!split_args(buf.substr(pos, linelen), args)) { r.fatal = true; r.err = "Protocol error: unbalanced quotes in request"; return false; }
    pos = nl + 1;
    if (!args.empty()) r.cmds.push_back(std::move(args));
    return true;
}


// numbers are parsed in place; the buffer is consumed by offset and compacted once per drain
static bool parse_ll_at(const char* p, size_t n, int64_t& v) {
    if (n == 0 || n > 20) return false;
    bool neg = false; size_t i = 0;
    if (p[0] == '-') { neg = true; i = 1; if (n == 1) return false; }
    int64_t x = 0;
    for (; i < n; i++) { if (p[i] < '0' || p[i] > '9') return false; x = x * 10 + (p[i] - '0'); }
    v = neg ? -x : x; return true;
}
bool RespParser::parse_multibulk(ParseResult& r) {
    // returns false when more data is needed or on fatal error
    if (mb_left == -1) {
        size_t nl = buf.find('\n', pos);
        if (nl == std::string::npos) {
            if (buf.size() - pos > inline_max) { r.fatal = true; r.err = "Protocol error: too big mbulk count string"; }
            return false;
        }
        size_t linelen = nl - pos; if (linelen && buf[nl-1] == '\r') linelen--;
        int64_t count;
        if (!parse_ll_at(buf.data() + pos + 1, linelen - 1, count) || count > max_multibulk) { r.fatal = true; r.err = "Protocol error: invalid multibulk length"; return false; }
        if (!authenticated && count > 10) { r.fatal = true; r.err = "Protocol error: unauthenticated multibulk length"; return false; }
        pos = nl + 1;
        if (count <= 0) return true;               // empty command, keep going
        mb_left = count; cur.clear(); cur.reserve((size_t)count); bulk_len = -1;
    }
    while (mb_left > 0) {
        if (bulk_len == -1) {
            size_t nl = buf.find('\n', pos);
            if (nl == std::string::npos) {
                if (buf.size() - pos > inline_max) { r.fatal = true; r.err = "Protocol error: too big bulk count string"; }
                return false;
            }
            if (buf[pos] != '$') { r.fatal = true; r.err = std::string("Protocol error: expected '$', got '") + buf[pos] + "'"; return false; }
            size_t linelen = nl - pos; if (linelen && buf[nl-1] == '\r') linelen--;
            int64_t len;
            if (!parse_ll_at(buf.data() + pos + 1, linelen - 1, len) || len < 0 || len > max_bulk) { r.fatal = true; r.err = "Protocol error: invalid bulk length"; return false; }
            if (!authenticated && len > 16384) { r.fatal = true; r.err = "Protocol error: unauthenticated bulk length"; return false; }
            pos = nl + 1;
            bulk_len = len;
        }
        if ((int64_t)(buf.size() - pos) < bulk_len + 2) return false;
        cur.emplace_back(buf.data() + pos, (size_t)bulk_len);
        pos += (size_t)bulk_len + 2;
        bulk_len = -1; mb_left--;
    }
    r.cmds.push_back(std::move(cur)); cur = std::vector<std::string>(); mb_left = -1;
    return true;
}

ParseResult RespParser::drain() {
    ParseResult r; r.cmds.reserve(16);
    while (pos < buf.size()) {
        if (mb_left != -1 || buf[pos] == '*') {
            if (!parse_multibulk(r)) break;
        } else {
            size_t nl = buf.find('\n', pos);
            if (nl == std::string::npos) {
                if (buf.size() - pos > inline_max) { r.fatal = true; r.err = "Protocol error: too big inline request"; }
                break;
            }
            if (!parse_inline(r, nl)) break;
        }
    }
    if (pos) { buf.erase(0, pos); pos = 0; }      // one compaction per drain, not one per frame
    return r;
}

// ---------------------------------------------------------------- Reply

void Reply::item(size_t children) {
    if (!discarding_) return;
    if (discard_.empty()) { out.resize(mark_); discarding_ = false; return; }
    discard_.back()--;
    if (children) discard_.push_back(children);
    while (!discard_.empty() && discard_.back() == 0) discard_.pop_back();
}

void Reply::attribute(size_t n) {
    if (proto == 3) { item(n * 2); char b[32]; int l = snprintf(b, sizeof b, "|%zu\r\n", n); out.append(b, l); return; }
    if (n == 0) return;
    mark_ = out.size();
    discarding_ = true; discard_.clear(); discard_.push_back(n * 2);
}

std::string Reply::fmt_double(double d) {
    if (std::isinf(d)) return d > 0 ? "inf" : "-inf";
    if (std::isnan(d)) return "nan";
    char b[64];
    // shortest round-tripping repr, like fpconv_dtoa in 7.2; integers print without an exponent
    int p = 1;
    for (; p <= 17; p++) { snprintf(b, sizeof b, "%.*g", p, d); if (strtod(b, nullptr) == d) break; }
    double ad = std::fabs(d);
    if (ad >= 1 && ad < 1e17) { int intdigits = (int)std::floor(std::log10(ad)) + 1; if (intdigits > p) { p = std::min(intdigits, 17); snprintf(b, sizeof b, "%.*g", p, d); } }
    return b;
}

void Reply::double_(double d) {
    std::string s = fmt_double(d);
    if (proto == 3) { item(); out += ','; out += s; out += "\r\n"; } else bulk(s);
}

void Reply::verbatim(const std::string& fmt, const std::string& s) {
    if (proto == 3) { item(); char b[32]; int l = snprintf(b, sizeof b, "=%zu\r\n", s.size() + 4); out.append(b, l); out += fmt.substr(0, 3); out += ':'; out += s; out += "\r\n"; }
    else bulk(s);
}
