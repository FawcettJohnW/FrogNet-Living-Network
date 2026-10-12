// ram -- [FDF_CHAT_V1] (plan item 7) Ribbit's own tuple operations, from the command line, over this machine's FNWP
// connection to the RAM manager (located by the transport, as every client):
//   ram write SERVICE VARIABLE INSTANCE BAG_JSON          -> {"ok":true,"id":N}
//   ram read  SERVICE VARIABLE [INSTANCE] [AFTER] [WAIT_S] -> {"ok":true,"rows":[...]}; with AFTER, only newer cells,
//                                                              held up to WAIT_S seconds until one exists
// Application addressing <App>.<QueueName>.<submitter> is (service, variable, instance).
#include "rina_region_client.hpp"
#include <cstdio>
static std::string enc(const std::string& s) { std::string o; char b[4]; for (unsigned char c : s) { if (isalnum(c) || strchr("-_.~", c)) o += char(c); else { snprintf(b, sizeof b, "%%%02X", c); o += b; } } return o; }
int main(int argc, char** argv) {
    try {
        const std::string where = rina_region::Client::locate(); const size_t c = where.rfind(':');
        frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), rina_region::api());
        const std::string cmd = argc > 1 ? argv[1] : "", api = rina_region::api();
        if (cmd == "write" && argc == 6) {
            const std::string body = "{\"service\":" + frogram::Json::quote(argv[2]) + ",\"variable\":" + frogram::Json::quote(argv[3]) +
                                     ",\"instance\":" + frogram::Json::quote(argv[4]) + ",\"bag\":" + argv[5] + "}";
            frogram::Json r = s.call("POST", api + "?op=write", body); printf("{\"ok\":true,\"id\":%lld}\n", (long long)r["id"].n); return 0; }
        if (cmd == "read" && argc >= 4 && argc <= 7) {
            std::string path = api + "?op=read&service=" + enc(argv[2]) + "&variable=" + enc(argv[3]);
            if (argc > 4 && *argv[4]) path += "&instance=" + enc(argv[4]);
            if (argc > 5) path += "&after=" + enc(argv[5]);
            if (argc > 6) path += "&wait_s=" + enc(argv[6]);
            const std::string text = s.call_text("GET", path, "", argc > 6 ? atof(argv[6]) + 5 : 30.0); printf("%s\n", text.c_str()); return 0; }
        fprintf(stderr, "usage: ram write SERVICE VARIABLE INSTANCE BAG_JSON | ram read SERVICE VARIABLE [INSTANCE] [AFTER] [WAIT_S]\n"); return 2;
    } catch (const std::exception& e) { fprintf(stderr, "ram: %s\n", e.what()); return 1; }
}
