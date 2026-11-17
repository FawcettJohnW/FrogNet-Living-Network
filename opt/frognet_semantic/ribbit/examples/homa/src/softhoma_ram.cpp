// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// softhoma_ram.cpp -- the memory soft Homa runs on, as a process of its own: softhoma-ram HOST:PORT
// (the "homa" space, [SPACE_V1]; ../DESIGN.md). It holds the memory and does nothing else.
#include "ribbit_space.hpp"
#include <csignal>
#include <cstdio>
#include <unistd.h>
int main(int argc, char** argv) {
    if (argc != 2) { fprintf(stderr, "usage: softhoma-ram HOST:PORT\n"); return 2; }
    ribbit::Space memory(std::string("here:") + argv[1], "homa");
    fprintf(stderr, "[SOFTHOMA-RAM] softhoma-ram-v1-20261116k the homa space is held here and reached at %s\n", argv[1]);
    sigset_t s; sigemptyset(&s); sigaddset(&s, SIGINT); sigaddset(&s, SIGTERM); sigprocmask(SIG_BLOCK, &s, nullptr);
    int sig; sigwait(&s, &sig);
    return 0;
}
