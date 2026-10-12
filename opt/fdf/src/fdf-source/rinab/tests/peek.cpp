// peek VARIABLE -- every cell of rina/<VARIABLE> in the region: id, instance, bag (a debugging aid; op=rina peek)
#include <cstdio>
#include "rina_region_client.hpp"
int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: peek VARIABLE\n"); return 2; }
    rina_region::Client a; auto r = a.call(std::string("{\"op\":\"peek\",\"v\":") + frogram::Json::quote(argv[1]) + "}");
    for (auto& x : r["rows"].a) printf("%.0f %s %s\n", x[0].n, x[1].s.c_str(), x[2].type == frogram::Json::Str ? x[2].s.c_str() : (x[2]["v"].type == frogram::Json::Str ? x[2]["v"].s.c_str() : (x[2]["k"].s + " fid=" + x[2]["fid"].s).c_str()));
    printf("%zu rows\n", r["rows"].a.size());
}
