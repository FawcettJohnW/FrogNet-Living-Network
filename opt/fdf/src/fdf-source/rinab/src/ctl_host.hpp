// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ctl_host.hpp -- [CTL_AT_THE_MEMORY_V1] what rlite-ctl's command logic sees when it runs inside rina-ram: the records
// are the host's own memory (no request, no copy), netns() is the caller's namespace (from the request), and what the
// command prints is collected and returned to the caller. Included by rlite_ctl.cpp after the standard headers.
#pragma once
#include <cstdarg>
#include <string>
#include <utility>
#include <vector>
#include "frogram.hpp"

namespace ctl_host {
extern thread_local frogram::MemoryApi* tl_m;
extern thread_local std::string tl_ns, tl_out, tl_err;
}
struct CtlExit { int code; };

class HostMem {                                       // the same three calls the client made over the wire, here at the memory
public:
    void write(const std::string& name, const std::string& value) {
        const size_t b = name.find('|');
        // [PERMANENT_ENVELOPE_V1] the writer decides lifetime: a DIF's policy and parameters belong to the DIF, no member
        // re-asserts them, so their envelope carries the Permanent flag and the region keeps them in its file
        const std::string kind = name.substr(0, b); const bool perm = kind == "pol" || kind == "par";
        ctl_host::tl_m->write("rina", kind, b == std::string::npos ? "" : name.substr(b + 1), "{\"v\":" + frogram::Json::quote(value) + (perm ? ",\"p\":1}" : "}"));
    }
    bool read(const std::string& name, std::string& value) {
        const size_t b = name.find('|'); if (b == std::string::npos) return false;
        const std::string inst = name.substr(b + 1);
        for (auto& c : ctl_host::tl_m->read("rina", name.substr(0, b), inst, -1, 0)) if (c.instance == inst) { value = c.bag["v"].s; return true; }
        return false;
    }
    std::vector<std::pair<std::string, std::string>> scan(const std::string& prefix) {
        std::vector<std::pair<std::string, std::string>> out;
        const size_t b = prefix.find('|'); const std::string kind = prefix.substr(0, b), rest = b == std::string::npos ? "" : prefix.substr(b + 1);
        for (auto& c : ctl_host::tl_m->read("rina", kind, "", -1, 0))
            if (c.instance.compare(0, rest.size(), rest) == 0) out.emplace_back(kind + "|" + c.instance, c.bag["v"].s);
        return out;
    }
};
inline HostMem& mem() { static HostMem m; return m; }
inline std::string netns() { return ctl_host::tl_ns; }
inline int ctl_vprint(std::string& to, const char* fmt, va_list ap) {
    char b[4096]; const int n = vsnprintf(b, sizeof b, fmt, ap); to.append(b, n < 0 ? 0 : (n < int(sizeof b) ? size_t(n) : sizeof b - 1)); return n;
}
inline int ctl_printf(const char* fmt, ...) { va_list ap; va_start(ap, fmt); const int n = ctl_vprint(ctl_host::tl_out, fmt, ap); va_end(ap); return n; }
inline int ctl_eprintf(const char* fmt, ...) { va_list ap; va_start(ap, fmt); const int n = ctl_vprint(ctl_host::tl_err, fmt, ap); va_end(ap); return n; }
#define printf(...) ctl_printf(__VA_ARGS__)
#define fprintf(stream, ...) ctl_eprintf(__VA_ARGS__)
