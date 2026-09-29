// raw_call <port> <api> <json body>: one POST ?op=redis straight to the RAM host, as anything on the network could send
#include "frogram.hpp"
#include <cstdio>
int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage\n"); return 2; }
    try { frogram::Session s("127.0.0.1", atoi(argv[1]), argv[2]);
          auto r = s.call("POST", std::string(argv[2]) + "?op=redis", argv[3]);
          std::string o = "{"; bool f = true;
          for (auto& [k, v] : r.o) { o += (f ? "" : ",") + frogram::Json::quote(k) + ":" + (v.type == frogram::Json::Str ? frogram::Json::quote(v.s) : v.type == frogram::Json::Num ? std::to_string((long long)v.n) : v.type == frogram::Json::Bool ? (v.b ? "true" : "false") : "null"); f = false; }
          printf("RESULT %s}\n", o.c_str()); }
    catch (const std::exception& e) { printf("THROWN %s\n", e.what()); }
}
