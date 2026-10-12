// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ctl_host.cpp -- [CTL_AT_THE_MEMORY_V1] rlite-ctl's command logic, compiled into rina-ram: one function runs one
// command at the memory and returns its exit code, its output and its errors.
#define RINA_CTL_IN_HOST
#include "rlite_ctl.cpp"
#undef printf
#undef fprintf
namespace ctl_host {
thread_local frogram::MemoryApi* tl_m = nullptr;
thread_local std::string tl_ns, tl_out, tl_err;
}
int ctl_run(frogram::MemoryApi& m, const std::string& ns, const std::vector<std::string>& args, std::string& out, std::string& err) {
    ctl_host::tl_m = &m; ctl_host::tl_ns = ns; ctl_host::tl_out.clear(); ctl_host::tl_err.clear();
    std::vector<std::string> a{"rlite-ctl"}; a.insert(a.end(), args.begin(), args.end());
    std::vector<char*> argv; for (auto& s : a) argv.push_back(const_cast<char*>(s.c_str()));
    int rc;
    try { rc = ctl_main(int(argv.size()), argv.data()); } catch (const CtlExit& e) { rc = e.code; } catch (const std::exception& e) { ctl_host::tl_err += std::string(e.what()) + "\n"; rc = 1; }
    out = ctl_host::tl_out; err = ctl_host::tl_err; ctl_host::tl_m = nullptr;
    return rc;
}
