// Test equipment: the same in-process memory as test_host.cpp, listening on a caller-given HOST:PORT so it can
// serve other machines or network namespaces. Not a product component.
// Stops on a line on stdin (the test harnesses) or a signal; no stdin at all (a service) is not a stop.
#include "ribbit_space.hpp"
#include <iostream>
#include <unistd.h>
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: region-host HOST:PORT" << std::endl; return 2; }
    ribbit::Space host(std::string("here:") + argv[1], "senz-binding");
    std::cout << "READY " << argv[1] << std::endl;
    std::string line;
    if (std::getline(std::cin, line)) return 0;
    for (;;) pause();
}
