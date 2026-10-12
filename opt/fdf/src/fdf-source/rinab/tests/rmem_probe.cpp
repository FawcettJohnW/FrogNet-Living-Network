// rmem-probe PROC | FID|PROC -- [RINA_MEMORY_V1] a debugging aid: a program's input space (events numbered, the oldest
// segment still kept, gone?) or a flow direction (contiguous taken count, credit published, gone?), as the region holds them
#include <cstdio>
#include <cstring>
#include "rina_region_client.hpp"
int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: rmem-probe PROC | FID|PROC\n"); return 2; }
    rina_region::Client a; const bool flow = strchr(argv[1], '|') != nullptr;
    auto r = a.call(std::string("{\"op\":\"rmem\",\"") + (flow ? "flow" : "proc") + "\":" + frogram::Json::quote(argv[1]) + "}");
    if (flow) printf("%s\n", r["state"].s.c_str());
    else printf("numbered=%.0f head=%.0f gone=%s\n", r["numbered"].n, r["head"].n, r["gone"].b ? "yes" : "no");
}
