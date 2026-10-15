// write_rate.cpp -- W writer sessions x N new cells each, one variable per writer, no reader. Prints writes/s.
#include "frogram.hpp"
#include <chrono>
#include <cstdio>
#include <thread>
#include <vector>
static const std::string API = "/FawcettInnovations.RINA.ram_interface.php";
int main(int argc, char** argv) {
    const std::string where = argv[1]; const int W = atoi(argv[2]), N = atoi(argv[3]);
    const size_t c = where.rfind(':'); const std::string host = where.substr(0, c); const int port = atoi(where.c_str() + c + 1);
    const std::string svc = "rate" + std::to_string(time(nullptr)) + std::to_string(port);
    std::vector<std::thread> ws; auto t0 = std::chrono::steady_clock::now();
    for (int w = 0; w < W; ++w) ws.emplace_back([&, w] {
        frogram::Session s(host, port, API); frogram::Memory m(s, API);
        for (int i = 0; i < N; ++i) m.write(svc, "v" + std::to_string(w), "w" + std::to_string(w) + "-" + std::to_string(i), "{\"i\":" + std::to_string(i) + "}");
    });
    for (auto& t : ws) t.join();
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    printf("port %d writers=%d writes=%d %.2f s %.0f writes/s\n", port, W, W * N, sec, W * N / sec);
}
