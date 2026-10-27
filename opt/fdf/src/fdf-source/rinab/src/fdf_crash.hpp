// fdf_crash.hpp -- [FDF_CRASH_HANDLER_V1] a FrogNet binary reports its OWN fatal signal. On SIGSEGV/SIGBUS/SIGABRT/
// SIGFPE/SIGILL it writes the program name, the signal, the faulting address, and a backtrace to stderr, then re-raises
// the default disposition so it still dies. The supervisor already logs each unit's stderr, so the crash self-reports
// in the run's own logs -- no dependence on dmesg (kernel.print-fatal-signals is off by default), coredumpctl, a core
// ulimit, or any sysctl. Async-signal-safe: only backtrace()/backtrace_symbols_fd()/write() and a tiny hand-rolled hex
// writer -- no malloc, printf or stdio in the handler. Install once at the top of main(): fdf_crash::install("name").
#pragma once
#include <execinfo.h>
#include <initializer_list>
#include <signal.h>
#include <string.h>
#include <unistd.h>

namespace fdf_crash {

inline const char* g_prog = "?";

inline void write_str(const char* s) { if (s) (void)!write(2, s, strlen(s)); }

inline void write_hex(unsigned long v) {                 // "0x...." without malloc/printf, for the fault address
    char buf[2 + sizeof(v) * 2]; char* p = buf + sizeof(buf); int n = 0;
    do { unsigned d = v & 0xf; *--p = char(d < 10 ? '0' + d : 'a' + d - 10); v >>= 4; ++n; } while (v && n < int(sizeof(v) * 2));
    *--p = 'x'; *--p = '0';
    (void)!write(2, p, size_t(buf + sizeof(buf) - p));
}

inline void handler(int sig, siginfo_t* si, void*) {
    const char* nm = sig == SIGSEGV ? "SIGSEGV" : sig == SIGBUS ? "SIGBUS" : sig == SIGABRT ? "SIGABRT"
                   : sig == SIGFPE ? "SIGFPE" : sig == SIGILL ? "SIGILL" : "signal";
    // The readable backtrace, one frame per line, for the full log.
    write_str("\n[FDF-CRASH] ");
    write_str(g_prog); write_str(" got "); write_str(nm);
    write_str(" at fault addr "); write_hex(si ? (unsigned long)si->si_addr : 0UL);
    write_str("  (addr2line/nm against the binary for the function):\n");
    void* bt[64];
    int k = backtrace(bt, 64);
    backtrace_symbols_fd(bt, k, 2);
    // One self-contained summary line LAST, so the supervisor/controller tail surfaces signal + fault + every frame
    // address even when it can show only one line. These are what addr2line resolves.
    write_str("[FDF-CRASH] ");
    write_str(g_prog); write_str(" "); write_str(nm); write_str(" addr "); write_hex(si ? (unsigned long)si->si_addr : 0UL);
    write_str(" frames");
    for (int i = 0; i < k; ++i) { write_str(" "); write_hex((unsigned long)bt[i]); }
    write_str("\n");
    signal(sig, SIG_DFL);                                // re-raise so the process still dies with the real signal
    raise(sig);
}

inline void install(const char* prog) {
    g_prog = prog;
    // [FDF_CRASH_ALTSTACK_V1] run the handler on a DEDICATED stack. A stack overflow faults on the guard page, and a
    // handler on the (exhausted) normal stack cannot run -- it double-faults and the process dies SILENTLY, which is
    // exactly how this reporter looked useless. With an alt stack the handler still runs and prints the recursion.
    static char altstk[64 * 1024];
    stack_t ss; memset(&ss, 0, sizeof ss);
    ss.ss_sp = altstk; ss.ss_size = sizeof altstk; ss.ss_flags = 0;
    sigaltstack(&ss, nullptr);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO | SA_RESETHAND | SA_ONSTACK;  // one shot, on the alt stack so a stack overflow is caught
    for (int s : {SIGSEGV, SIGBUS, SIGABRT, SIGFPE, SIGILL}) sigaction(s, &sa, nullptr);
}

}  // namespace fdf_crash
