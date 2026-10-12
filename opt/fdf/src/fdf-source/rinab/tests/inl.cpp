#include <cstdio>
#include "rina_region_client.hpp"
int main(int argc, char** argv) {
    rina_region::Client a;
    auto r = a.call(std::string("{\"op\":\"wait\",\"proc\":\"agent:") + argv[1] + "\",\"after\":0}"); printf("inline wait returned ok=%d events=%zu\n", r["ok"].b, r["ev"].a.size());
}
