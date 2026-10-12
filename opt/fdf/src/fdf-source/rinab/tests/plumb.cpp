// plumb.cpp -- step 0: op=rina reaches the region, and each session is told its own id.
#include <cstdio>
#include "frogram.hpp"
int main() {
    const std::string api = "/FawcettInnovations.RINA.ram_interface.php";
    frogram::Session a("127.0.0.1", 8850, api), b("127.0.0.1", 8850, api);
    auto ra = a.call("POST", api + "?op=rina", "{\"op\":\"session\"}"), rb = b.call("POST", api + "?op=rina", "{\"op\":\"session\"}");
    printf("session a %s, session b %s, distinct: %s\n", ra["session"].type == frogram::Json::Num ? std::to_string((unsigned long long)ra["session"].n).c_str() : "?",
           std::to_string((unsigned long long)rb["session"].n).c_str(), ra["session"].n != rb["session"].n ? "yes" : "NO");
}
