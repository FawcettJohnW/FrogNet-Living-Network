/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
#include <cstdlib>
#include <thread>
#include <pthread.h>
#include <unistd.h>
#include <csignal>
// lisp_boundary.cpp -- the Ribbit Map-Server's UDP front: LispBoundary as a program.
//   lisp-boundary --ram HOST PORT --udp PORT
// Map-Requests are answered from held views on the receive thread; Map-Registers are written into the front's ask
// space and answered by the region through the memory ([REGISTER_ASKED_V1]). The front takes no configuration: the
// region's configuration is in the memory ([CONFIG_WRITTEN_ONCE_V1]). When it is serving it writes its own row in
// lisp.fronts ([FRONT_ANNOUNCES_ITSELF_V1]). SIGTERM or SIGINT stops it and prints its counters.
#include "lisp_handler.hpp"
#include <iostream>
// [COVERAGE_ON_TERM_V1] SIGTERM / SIGINT end the program by returning from main, so a coverage build (--coverage)
// writes its data. The signals are blocked before any other thread starts, so every thread inherits the mask and main
// alone receives them, in sigwait.
int main(int argc, char** argv) {
    sigset_t set; sigemptyset(&set); sigaddset(&set, SIGTERM); sigaddset(&set, SIGINT);
    pthread_sigmask(SIG_BLOCK, &set, nullptr);
    std::string host; int port = 0, udp = 0;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--ram" && i + 2 < argc) { host = argv[++i]; port = std::stoi(argv[++i]); }
        else if (a == "--udp" && i + 1 < argc) udp = std::stoi(argv[++i]);
        else { std::cerr << "usage: lisp-boundary --ram HOST PORT --udp PORT\n"; return 2; }
    }
    if (host.empty() || !port || !udp) { std::cerr << "usage: lisp-boundary --ram HOST PORT --udp PORT\n"; return 2; }
    frognet::LispBoundary b(host, port, udp);
    std::cerr << "[LISP-BOUNDARY] " << RIBBIT_LISP_VERSION << " UDP " << udp << ", RAM " << host << ":" << port << "\n" << std::flush;
    int sig = 0; sigwait(&set, &sig);
    b.stop();
    std::cerr << "[LISP-BOUNDARY] registers=" << b.registers() << " requests=" << b.requests() << " rejected=" << b.rejected() << " answered=" << b.answered() << "\n";
    return 0;
}
