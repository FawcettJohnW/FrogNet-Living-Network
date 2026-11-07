// kartram_glibc.cpp -- [KARTRAM_GLIBC_2_35_V1] lets libkartram.so run on glibc 2.35 (Ubuntu 22.04, the droplet).
// glibc 2.38's headers send strtol/strtoul to their C23 versions (__isoc23_*) whenever _GNU_SOURCE is on, which g++
// always sets; those exist only in glibc 2.38+. For base-10/16 parsing of our own wire text the two behave the same
// (C23 adds only 0b prefixes), so the library defines them itself, hidden, over the classic functions.
#include <cstdlib>
extern "C" long kr_classic_strtol(const char*, char**, int) __asm__("strtol");
extern "C" unsigned long kr_classic_strtoul(const char*, char**, int) __asm__("strtoul");
extern "C" __attribute__((visibility("hidden"))) long __isoc23_strtol(const char* s, char** e, int b) { return kr_classic_strtol(s, e, b); }
extern "C" __attribute__((visibility("hidden"))) unsigned long __isoc23_strtoul(const char* s, char** e, int b) { return kr_classic_strtoul(s, e, b); }
// The static libstdc++ (std::random_device) wants arc4random, glibc 2.36+: the same random bytes from getrandom(2).
#include <sys/random.h>
extern "C" __attribute__((visibility("hidden"))) unsigned int arc4random(void) {
    unsigned int v = 0; while (getrandom(&v, sizeof v, 0) != sizeof v) {} return v; }
