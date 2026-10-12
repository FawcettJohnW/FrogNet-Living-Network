#!/usr/bin/env python3
"""apply_windows_portable.py RIBBIT_CPP_DIR -- [WINDOWS_PORTABLE_V1] the three POSIX-only spots this tree's own patches
put in the client's headers, made portable for the Windows FD&F client (experiments/DESIGN-WINDOWS-CLIENT.md). Linux
behaviour is unchanged:
  fnwp_engine.hpp  the primed-dictionary reader used POSIX getline(): now a portable line reader (fgetc)
                   the exemplar recorder used O_CLOEXEC: defined as 0 where the platform has none
  semtpl.hpp       the blob store's mkdir(dir, 0755): Windows' mkdir takes no mode
Applied last, after every other patch to these files."""
import sys
d = sys.argv[1].rstrip('/')
p = d + '/fnwp_engine.hpp'; s = open(p).read()
if '[WINDOWS_PORTABLE_V1]' not in s:
    old = '''        char* line = nullptr; size_t cap = 0; ssize_t n;
        while ((n = getline(&line, &cap, fp)) > 0) {
            std::string l(line, size_t(n));'''
    assert s.count(old) == 1, "the dictionary reader is not where expected"
    s = s.replace(old, '''        std::string l;                                     // [WINDOWS_PORTABLE_V1] a portable line reader (no POSIX getline)
        for (int ch = 0; ch != EOF;) {
            l.clear(); while ((ch = fgetc(fp)) != EOF) { l += char(ch); if (ch == '\\n') break; }
            if (l.empty()) break;
            ''', 1)
    old2 = '''        free(line); fclose(fp);'''
    assert s.count(old2) == 1, "the reader's cleanup is not where expected"
    s = s.replace(old2, '''        fclose(fp);''', 1)
    first_inc = s.index('#include')
    s = s[:first_inc] + '#include <fcntl.h>\n#ifndef O_CLOEXEC                                 // [WINDOWS_PORTABLE_V1]\n#define O_CLOEXEC 0\n#endif\n' + s[first_inc:]
    open(p, 'w').write(s)
p = d + '/semtpl.hpp'; s = open(p).read()
if '[WINDOWS_PORTABLE_V1]' not in s:
    old = '''::mkdir(dir.c_str(), 0755)'''
    assert s.count(old) == 1, "the blob store's mkdir is not where expected"
    s = s.replace(old, '''fr_mkdir_755(dir.c_str())''', 1)
    first_inc = s.index('#include')
    s = s[:first_inc] + '''#include <sys/stat.h>
#ifdef _WIN32                                       // [WINDOWS_PORTABLE_V1]
#include <direct.h>
inline int fr_mkdir_755(const char* p) { return ::_mkdir(p); }
#else
inline int fr_mkdir_755(const char* p) { return ::mkdir(p, 0755); }
#endif
''' + s[first_inc:]
    open(p, 'w').write(s)
print("[WINDOWS_PORTABLE_V1] applied")
