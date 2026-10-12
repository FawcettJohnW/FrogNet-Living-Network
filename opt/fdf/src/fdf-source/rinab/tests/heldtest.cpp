#include <cstdio>
#include "rina_region_client.hpp"
int main(int argc, char** argv) {
    rina_region::Client a;
    auto r = a.held(std::string("{\"op\":\"wait\",\"proc\":\"agent:") + argv[1] + "\",\"after\":0}", 30); printf("held returned: %zu events\n", r["ev"].a.size());
}
