// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// glibc_shim.cpp -- [NODE_BINARIES_V1] with rinab/kart/kartram_glibc.cpp, lets the node's libkartram.so load on glibc
// 2.35 and later (a Pi on Debian 12 has 2.36; it was built on 2.39). glibc 2.38's headers send sscanf, strtoull and
// strtoll to their C23 versions (__isoc23_*), which exist only in 2.38 and later. For the base-10 and base-16 text
// this library parses the two behave the same, so it defines them itself, hidden, over the classic functions.
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
extern "C" int ws_classic_vsscanf(const char*, const char*, va_list) __asm__("vsscanf");
extern "C" unsigned long long ws_classic_strtoull(const char*, char**, int) __asm__("strtoull");
extern "C" long long ws_classic_strtoll(const char*, char**, int) __asm__("strtoll");
extern "C" __attribute__((visibility("hidden"))) int __isoc23_sscanf(const char* s, const char* f, ...) {
    va_list ap; va_start(ap, f); const int n = ws_classic_vsscanf(s, f, ap); va_end(ap); return n; }
extern "C" __attribute__((visibility("hidden"))) unsigned long long __isoc23_strtoull(const char* s, char** e, int b) { return ws_classic_strtoull(s, e, b); }
extern "C" __attribute__((visibility("hidden"))) long long __isoc23_strtoll(const char* s, char** e, int b) { return ws_classic_strtoll(s, e, b); }
