#!/usr/bin/env python3
"""apply_futex_portable.py FROGRAM_CPP -- [FUTEX_PORTABLE_V1] the session client's futex waits, portable: Linux keeps its
syscall exactly as before; Windows uses WaitOnAddress / WakeByAddressAll (the same compare-and-sleep on a 32-bit word).
For the Windows FD&F client (experiments/DESIGN-WINDOWS-CLIENT.md). Applied after every other frogram.cpp patch."""
import re, sys
p = sys.argv[1]; s = open(p).read()
if '[FUTEX_PORTABLE_V1]' in s: sys.exit(0)
inc = '#include <linux/futex.h>\n#include "ebr.hpp"\n#include <sys/syscall.h>\n'
assert s.count(inc) == 1, "the futex includes are not where expected"
s = s.replace(inc, '''#include "ebr.hpp"
#ifdef _WIN32                                   // [FUTEX_PORTABLE_V1]
#include <windows.h>
#else
#include <linux/futex.h>
#include <sys/syscall.h>
#endif
''', 1)
old_wait = '''    long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, expected, &ts, nullptr, 0);
    return !(r == -1 && errno == ETIMEDOUT);
}'''
assert s.count(old_wait) == 1, "futex_wait_for body not found"
s = s.replace(old_wait, '''#ifdef _WIN32
    (void)ts; const DWORD ms = timeout_s >= 4.0e6 ? INFINITE : DWORD(timeout_s * 1000.0);
    return WaitOnAddress(reinterpret_cast<volatile void*>(&w), &expected, sizeof expected, ms) || GetLastError() != ERROR_TIMEOUT;
#else
    long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, expected, &ts, nullptr, 0);
    return !(r == -1 && errno == ETIMEDOUT);
#endif
}
// [FUTEX_PORTABLE_V1] an untimed wait: sleeps while *w == expected (spurious wakes are the caller's loop's to handle)
static inline void fr_futex_wait(std::atomic<uint32_t>* w, uint32_t expected) {
#ifdef _WIN32
    WaitOnAddress(reinterpret_cast<volatile void*>(w), &expected, sizeof expected, INFINITE);
#else
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(w), FUTEX_WAIT_PRIVATE, expected, nullptr, nullptr, 0);
#endif
}''', 1)
old_wake = '''static inline void futex_wake_all(std::atomic<uint32_t>& w) { syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0); }'''
assert s.count(old_wake) == 1, "futex_wake_all not found"
s = s.replace(old_wake, '''static inline void futex_wake_all(std::atomic<uint32_t>& w) {
#ifdef _WIN32
    WakeByAddressAll(reinterpret_cast<void*>(&w));
#else
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0);
#endif
}''', 1)
n0 = len(re.findall(r'syscall\(SYS_futex, reinterpret_cast<uint32_t\*>\(&([^)]+)\), FUTEX_WAIT_PRIVATE, ([^,]+), nullptr, nullptr, 0\)', s))
s = re.sub(r'syscall\(SYS_futex, reinterpret_cast<uint32_t\*>\(&([^)]+)\), FUTEX_WAIT_PRIVATE, ([^,]+), nullptr, nullptr, 0\)', r'fr_futex_wait(&\1, \2)', s)
assert n0 == 5, f"expected 5 untimed waits, found {n0}"
open(p, 'w').write(s)
print(f"[FUTEX_PORTABLE_V1] applied ({n0} untimed waits through the shim)")
