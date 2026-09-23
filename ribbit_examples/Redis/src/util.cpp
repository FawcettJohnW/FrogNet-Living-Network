#include "server.hpp"
#include <climits>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <cerrno>

bool string2ll(const std::string& s, long long& v) {
    size_t slen = s.size(); const char* p = s.data(); size_t plen = 0; bool neg = false; unsigned long long acc;
    if (slen == 0 || slen >= 21) return false;
    if (slen == 1 && p[0] == '0') { v = 0; return true; }
    if (p[0] == '-') { neg = true; p++; plen++; if (plen == slen) return false; }
    if (p[0] >= '1' && p[0] <= '9') { acc = p[0] - '0'; p++; plen++; } else return false;
    while (plen < slen && p[0] >= '0' && p[0] <= '9') {
        if (acc > ULLONG_MAX / 10) return false;
        acc *= 10;
        if (acc > ULLONG_MAX - (unsigned)(p[0] - '0')) return false;
        acc += p[0] - '0'; p++; plen++;
    }
    if (plen < slen) return false;
    if (neg) { if (acc > (unsigned long long)(-(LLONG_MIN + 1)) + 1) return false; v = -(long long)(acc - 1) - 1; }
    else { if (acc > LLONG_MAX) return false; v = (long long)acc; }
    return true;
}

// string2ld(): strtold with the checks Redis makes (no spaces, no nan, whole string consumed)
bool string2ld(const std::string& s, long double& v) {
    if (s.empty() || s.size() >= 5 * 1024) return false;
    if (isspace((unsigned char)s[0])) return false;
    char* end = nullptr; errno = 0;
    long double x = strtold(s.c_str(), &end);
    if (end == s.c_str() || end != s.c_str() + s.size() || errno == ERANGE || std::isnan(x)) return false;
    v = x; return true;
}

std::string ld2string_human(long double v) {
    if (std::isinf(v)) return v > 0 ? "inf" : "-inf";
    if (std::isnan(v)) return "nan";
    char buf[5 * 1024];
    int l = snprintf(buf, sizeof buf, "%.17Lf", v);
    if (strchr(buf, '.')) {
        char* p = buf + l - 1;
        while (*p == '0') { p--; l--; }
        if (*p == '.') l--;
    }
    if (l == 2 && buf[0] == '-' && buf[1] == '0') { buf[0] = '0'; l = 1; }
    return std::string(buf, l);
}

std::string ll2string(long long v) { char b[32]; snprintf(b, sizeof b, "%lld", v); return b; }

std::string lower(std::string s) { for (auto& c : s) c = (char)tolower((unsigned char)c); return s; }
bool str_eq_ci(const std::string& a, const char* b) { return strcasecmp(a.c_str(), b) == 0; }

// stringmatchlen() from util.c
static bool matchlen(const char* pattern, int patternLen, const char* string, int stringLen, int nocase, int* skipLongerMatches, int nesting = 0) {
    if (nesting > 1000) return false;
    while (patternLen && stringLen) {
        switch (pattern[0]) {
        case '*':
            while (patternLen && pattern[1] == '*') { pattern++; patternLen--; }
            if (patternLen == 1) return true;
            while (stringLen) {
                if (matchlen(pattern + 1, patternLen - 1, string, stringLen, nocase, skipLongerMatches, nesting + 1)) return true;
                if (*skipLongerMatches) return false;
                string++; stringLen--;
            }
            *skipLongerMatches = 1;
            return false;
        case '?':
            string++; stringLen--; break;
        case '[': {
            int not_, match;
            pattern++; patternLen--;
            not_ = pattern[0] == '^';
            if (not_) { pattern++; patternLen--; }
            match = 0;
            while (1) {
                if (pattern[0] == '\\' && patternLen >= 2) {
                    pattern++; patternLen--;
                    if (pattern[0] == string[0]) match = 1;
                } else if (pattern[0] == ']') {
                    break;
                } else if (patternLen == 0) {
                    pattern--; patternLen++; break;
                } else if (patternLen >= 3 && pattern[1] == '-') {
                    int start = pattern[0], end = pattern[2], c = string[0];
                    if (start > end) { int t = start; start = end; end = t; }
                    if (nocase) { start = tolower(start); end = tolower(end); c = tolower(c); }
                    pattern += 2; patternLen -= 2;
                    if (c >= start && c <= end) match = 1;
                } else {
                    if (!nocase) { if (pattern[0] == string[0]) match = 1; }
                    else if (tolower((int)pattern[0]) == tolower((int)string[0])) match = 1;
                }
                pattern++; patternLen--;
            }
            if (not_) match = !match;
            if (!match) return false;
            string++; stringLen--;
            break;
        }
        case '\\':
            if (patternLen >= 2) { pattern++; patternLen--; }
            /* fall through */
        default:
            if (!nocase) { if (pattern[0] != string[0]) return false; }
            else if (tolower((int)pattern[0]) != tolower((int)string[0])) return false;
            string++; stringLen--;
            break;
        }
        pattern++; patternLen--;
        if (stringLen == 0) { while (*pattern == '*') { pattern++; patternLen--; } break; }
    }
    return patternLen == 0 && stringLen == 0;
}

bool stringmatch(const std::string& pattern, const std::string& str, bool nocase) {
    int skip = 0;
    return matchlen(pattern.data(), (int)pattern.size(), str.data(), (int)str.size(), nocase, &skip);
}
