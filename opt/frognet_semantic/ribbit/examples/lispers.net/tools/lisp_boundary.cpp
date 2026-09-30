#include <cstdlib>
#include <thread>
#include <pthread.h>
#include <unistd.h>
#include <csignal>
// lisp_boundary.cpp -- the Ribbit Map-Server's UDP front: LispBoundary as a program.
//   lisp-boundary --ram HOST PORT --udp PORT [--workers N]
// Map-Requests are answered from held views on the receive thread; Map-Registers go to N register workers
// ([BOUNDARY_NEVER_WAITS_V1]). Configuration (site.add with the site's key, ...) arrives as JSON lines on stdin, the
// same {"operation","args"} ribbit-lisp takes, and is answered on stdout. EOF on stdin stops the boundary and prints
// its counters.
#include "lisp_handler.hpp"
#include <iostream>
// [COVERAGE_ON_TERM_V1] SIGTERM / SIGINT end the program with exit(), from an ordinary thread (sigwait), so a
// coverage build (--coverage) writes its data and the process still stops at once. The signals are blocked in main
// before any other thread starts, so every thread inherits the mask and only the waiting thread receives them.
static void end_on_term_signals() {
    sigset_t set; sigemptyset(&set); sigaddset(&set, SIGTERM); sigaddset(&set, SIGINT);
    pthread_sigmask(SIG_BLOCK, &set, nullptr);
    std::thread([set] { int sig = 0; sigwait(&set, &sig); std::exit(0); }).detach();
}
int main(int argc, char** argv) {
    end_on_term_signals();
    std::string host; int port = 0, udp = 0, workers = 8;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--ram" && i + 2 < argc) { host = argv[++i]; port = std::stoi(argv[++i]); }
        else if (a == "--udp" && i + 1 < argc) udp = std::stoi(argv[++i]);
        else if (a == "--workers" && i + 1 < argc) workers = std::stoi(argv[++i]);
        else { std::cerr << "usage: lisp-boundary --ram HOST PORT --udp PORT [--workers N]\n"; return 2; }
    }
    if (host.empty() || !port || !udp) { std::cerr << "usage: lisp-boundary --ram HOST PORT --udp PORT [--workers N]\n"; return 2; }
#ifdef OLD_BOUNDARY
    frognet::LispBoundary b(host, port, udp);
#else
    frognet::LispBoundary b(host, port, udp, workers);
#endif
    std::cerr << "[LISP-BOUNDARY] UDP " << udp << ", RAM " << host << ":" << port << "\n" << std::flush;
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            // {"operation":"...","args":{...}}: the args object as written, handed to call() as text
            Json r = Json::parse(line); auto k = line.find("\"args\""); auto c = line.find(':', k); auto e = line.rfind('}');
            if (k == std::string::npos || c == std::string::npos || e == std::string::npos || e <= c) throw std::runtime_error("expected {\"operation\",\"args\":{...}}");
            // the answer first: streaming the prefix before call() returns left a half line when call() threw
            const std::string res = b.call(r["operation"].s, line.substr(c + 1, e - c - 1));
            std::cout << "{\"ok\":true,\"result\":" << res << "}\n" << std::flush;
        }
        catch (const std::exception& x) { std::cout << "{\"ok\":false,\"error\":" << Json::quote(x.what()) << "}\n" << std::flush; }
    }
    b.stop();
    std::cerr << "[LISP-BOUNDARY] registers=" << b.registers() << " requests=" << b.requests() << " rejected=" << b.rejected() << "\n";
}
