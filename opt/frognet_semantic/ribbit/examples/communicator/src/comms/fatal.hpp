// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [A_SERVER_SAYS_WHY_IT_ENDED_V1] (John, 27 Nov 2026: comms-media on Seattle7 went away twice during a call and nothing
// said why.) A server that is ended by a fault, or by an exception nobody caught, says so on its console before it
// goes: which program and build, what ended it, which thread, and where it was (the return addresses: the build's
// unstripped binary names the functions, `addr2line -Cfpe <binary> <addresses>`). A kill by the kernel for memory
// cannot be said here: `dmesg` has that one.
#pragma once
#if !defined(_WIN32)
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <initializer_list>
#include <execinfo.h>
#include <unistd.h>
#include <sys/syscall.h>

namespace comms {
inline const char*& fatal_who() { static const char* w = "?"; return w; }
inline void fatal_where() {
    void* at[64];
    int n = backtrace(at, 64);
    char line[1600]; int o = std::snprintf(line, sizeof line, "%s FATAL at:", fatal_who());
    for (int i = 0; i < n && o < int(sizeof line) - 24; ++i) o += std::snprintf(line + o, sizeof line - size_t(o), " %p", at[i]);
    o += std::snprintf(line + o, sizeof line - size_t(o), "\n");
    (void)!write(2, line, size_t(o));
}
inline void fatal_signal(int sig) {
    const char* what = sig == SIGSEGV ? "SIGSEGV (it read or wrote memory that is not its own)" : sig == SIGBUS ? "SIGBUS" : sig == SIGFPE ? "SIGFPE"
                     : sig == SIGILL ? "SIGILL (an instruction this processor does not have)" : sig == SIGABRT ? "SIGABRT (it ended itself: abort)" : "a signal";
    char line[256];
    int o = std::snprintf(line, sizeof line, "%s FATAL: ended by signal %d, %s, on thread %ld\n", fatal_who(), sig, what, long(syscall(SYS_gettid)));
    (void)!write(2, line, size_t(o));
    fatal_where();
    std::signal(sig, SIG_DFL);
    raise(sig);
}
/** who: "[media] comms-media-<build>". Call first in main. */
inline void say_why_it_ended(const char* who) {
    fatal_who() = who;
    for (int s : {SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGABRT}) std::signal(s, fatal_signal);
    std::set_terminate([] {
        char line[512]; int o;
        try {
            if (auto e = std::current_exception()) std::rethrow_exception(e);
            o = std::snprintf(line, sizeof line, "%s FATAL: std::terminate with no exception (a thread that was never joined, or a second fault)\n", fatal_who());
        } catch (const std::exception& x) {
            o = std::snprintf(line, sizeof line, "%s FATAL: an exception nobody caught, on thread %ld: %s\n", fatal_who(), long(syscall(SYS_gettid)), x.what());
        } catch (...) {
            o = std::snprintf(line, sizeof line, "%s FATAL: an exception nobody caught (not a std::exception)\n", fatal_who());
        }
        (void)!write(2, line, size_t(o));
        std::abort();                                     // SIGABRT: fatal_signal says where
    });
}
}  // namespace comms
#else
namespace comms { inline void say_why_it_ended(const char*) {} }
#endif
