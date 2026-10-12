// mcount.so -- LD_PRELOAD: samples every 64th malloc's call stack (3 frames above malloc) and prints the most frequent
// call sites at exit (a measurement aid for the preallocation work)
#define _GNU_SOURCE
#include <dlfcn.h>
#include <execinfo.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
static void* (*real_malloc)(size_t);
static __thread int inside;
static atomic_ulong calls;
#define NS 4096
static struct { void* pc[10]; unsigned long n; } site[NS];
static void record(void) {
    void* bt[12]; int k = backtrace(bt, 12); if (k < 12) memset(bt + k, 0, sizeof(void*) * (12 - k));
    unsigned long h = ((unsigned long)bt[2] ^ ((unsigned long)bt[3] << 1) ^ ((unsigned long)bt[4] << 2)) % NS;
    for (int t = 0; t < NS; ++t, h = (h + 1) % NS) {
        if (site[h].n == 0) { memcpy(site[h].pc, bt + 2, sizeof(void*) * 10); site[h].n = 1; return; }
        if (!memcmp(site[h].pc, bt + 2, sizeof(void*) * 10)) { site[h].n++; return; }
    }
}
void* malloc(size_t n) {
    if (!real_malloc) real_malloc = dlsym(RTLD_NEXT, "malloc");
    void* p = real_malloc(n);
    if (!inside && (atomic_fetch_add(&calls, 1) & 63) == 0) { inside = 1; record(); inside = 0; }
    return p;
}
static int cmp(const void* a, const void* b) { unsigned long x = *(const unsigned long*)((const char*)a + sizeof(void*) * 10), y = *(const unsigned long*)((const char*)b + sizeof(void*) * 10); return x < y ? 1 : x > y ? -1 : 0; }
__attribute__((destructor)) static void report(void) {
    inside = 1; const char* out = getenv("MCOUNT_OUT"); FILE* f = out ? fopen(out, "w") : stderr; if (!f) return;
    qsort(site, NS, sizeof site[0], cmp);
    fprintf(f, "mallocs: %lu (sampled 1/64)\n", (unsigned long)calls);
    for (int i = 0; i < 40 && site[i].n; ++i) {
        fprintf(f, "%6lu x64 ", site[i].n);
        int shown = 0;
        for (int j = 0; j < 10 && shown < 7; ++j) { Dl_info d; if (!site[i].pc[j]) break;
            if (!(dladdr(site[i].pc[j], &d) && d.dli_sname)) continue;
            const char* nm = d.dli_sname;
            if (!strncmp(nm, "_ZNSt", 5) || !strncmp(nm, "_ZNKSt", 6) || !strncmp(nm, "_ZN3pyv", 7) || !strncmp(nm, "_ZNSt8__detail", 14) || !strncmp(nm, "_Znwm", 5)) continue;
            fprintf(f, " <- %s+%#lx", nm, (unsigned long)((char*)site[i].pc[j] - (char*)d.dli_saddr)); ++shown; }
        fputc('\n', f);
    }
    if (f != stderr) fclose(f);
}
