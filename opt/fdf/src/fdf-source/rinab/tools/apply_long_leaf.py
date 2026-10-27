"""apply_long_leaf.py SEMTPL_HPP -- [LONG_LEAF_ONE_PASS_V1] the type of a long ASCII string leaf, decided in one pass.
Root cause, measured 2026-10-16 (exp/bulk_probe, loopback): a 1 MiB value cost 630-690 ms to write, whether through
the raw memory API or a region operation, with both ends on-CPU in semtpl::leaf_type -- is_ipv4, looks_like_interface,
looks_like_enum (twice) and is_plausible_host each decode the whole string into code points, strip it, and
looks_like_enum upper-cases it through a table, character by character. Every template learned from a body walks
every string leaf this way, on the client and on the host.
For an ASCII string longer than 64 bytes the answer is the same with one pass: it cannot be an IPv4 address (at most
15 characters once stripped), and it can be a host only if, once ASCII whitespace is stripped, every character is a
letter, digit, '.' or '-', it has a letter, it contains a '.' or starts "FrogNetHost", and it starts and ends with a
letter or digit -- such a string can be neither an interface name (a '.' is not a word character; "FrogNetHost" is no
interface prefix) nor an enum (lower case or '.'). A string with any byte >= 0x80 takes the full path unchanged, so
Unicode whitespace and case are judged exactly as before."""
import sys
p = sys.argv[1]; s = open(p).read()
a = '''    if (v.is<Str>()) { const std::string& s = v.as<Str>().s;
        if (is_ipv4(s)) return "ip";'''
b = '''    if (v.is<Str>()) { const std::string& s = v.as<Str>().s;
        if (s.size() > 64) {                                       // [LONG_LEAF_ONE_PASS_V1]
            bool ascii = true; for (unsigned char c : s) if (c >= 0x80) { ascii = false; break; }
            if (ascii) {
                if (detail::force_string_for_path(path)) return "string";
                auto ws = [](unsigned char c) { return c == ' ' || (c >= 9 && c <= 13) || (c >= 0x1c && c <= 0x1f); };
                size_t i = 0, e = s.size(); while (i < e && ws((unsigned char)s[i])) ++i; while (e > i && ws((unsigned char)s[e - 1])) --e;
                auto an = [](unsigned char c) { return std::isalnum(c) != 0; };
                bool ok = e > i + 1 && an((unsigned char)s[i]) && an((unsigned char)s[e - 1]), alpha = false, dot = false;
                for (size_t k = i; ok && k < e; ++k) { const unsigned char c = (unsigned char)s[k];
                    if (std::isalpha(c)) alpha = true; else if (c == '.') dot = true; else if (!(std::isdigit(c) || c == '-')) ok = false; }
                ok = ok && alpha && (dot || s.compare(i, 11, "FrogNetHost") == 0);
                return ok ? "host" : "string";
            }
        }
        if (is_ipv4(s)) return "ip";'''
assert s.count(a) == 1, s.count(a)
s = s.replace(a, b)
open(p, 'w').write(s)
print("[LONG_LEAF_ONE_PASS_V1] applied")
