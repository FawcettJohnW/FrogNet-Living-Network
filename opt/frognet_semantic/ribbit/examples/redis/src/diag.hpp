// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// diag.hpp -- [DIAG-REDIS] one line per event of a call's life, when RIBBIT_REDIS_DIAG names a file (else nothing):
// monotonic microseconds, thread, who, the event, the connection, and what it carried.
#pragma once
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <sys/syscall.h>
#include <unistd.h>

namespace rr {
inline FILE* diag_fp() {
    static FILE* f = [] { const char* p = getenv("RIBBIT_REDIS_DIAG"); if (!p || !*p) return (FILE*)nullptr;
                          FILE* x = fopen(p, "a"); if (x) setvbuf(x, nullptr, _IOLBF, 0); return x; }();
    return f;
}
inline void diag(const char* who, const char* ev, const std::string& key, const std::string& more = std::string()) {
    FILE* f = diag_fp(); if (!f) return;
    const long long us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    fprintf(f, "[DIAG-REDIS] %lld tid=%ld pid=%d %s %s %s %s\n", us, (long)syscall(SYS_gettid), (int)getpid(), who, ev, key.c_str(), more.c_str());
}
}  // namespace rr
// the call site's arguments are built only when the trace is on: free when it is off
#define DIAG(...) do { if (rr::diag_fp()) rr::diag(__VA_ARGS__); } while (0)
