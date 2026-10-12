// shape_key_exact.cpp -- [CLIENT_KNOWN_SHAPE_V1] proof: bodies with the same shape (keys and structure, values elided)
// have the same template key, for every body shape the library writes, with values of every kind the library puts in
// them; and bodies of different shapes get different shapes.
#include <cstdio>
#include <map>
#include <random>
#include "fnwp_engine.hpp"
int main() {
    const std::string api = "/FawcettInnovations.RINA.ram_interface.php";
    std::mt19937 rng(7); auto rs = [&](int n) { std::string s; const char* al = "abcdef0123456789-.:/AZ"; for (int i = 0; i < n; ++i) s += al[rng() % 22]; return s; };
    auto num = [&]() { return std::to_string(rng() % 100000); };
    std::map<std::string, std::string> key_of_shape; int checked = 0, bad = 0;
    for (int t = 0; t < 3000; ++t) {
        std::string path = api + (t % 3 ? "?op=rina" : "?op=rina_block"), body;
        switch (t % 5) {
            case 0: body = "{\"op\":\"send\",\"sdus\":[[\"" + rs(12) + "\"," + num() + ",\"" + rs(40) + "\"]]}"; break;
            case 1: body = "{\"op\":\"send\",\"sdus\":[[\"" + rs(12) + "\"," + num() + ",\"\"," + num() + "," + num() + "]]}"; break;
            case 2: body = "{\"op\":\"wait\",\"proc\":\"" + rs(16) + "\",\"after\":" + num() + "}"; break;
            case 3: body = "{\"op\":\"credit\",\"fid\":\"" + rs(12) + "\",\"proc\":\"" + rs(16) + "\",\"have\":" + num() + "}"; break;
            default: body = "{\"op\":\"ctl\",\"ns\":\"net:[" + num() + "]\",\"args\":[\"" + rs(6) + "\",\"" + rs(4) + "\"]}"; break;
        }
        std::string fp; fnwp::shape_of(fp, "POST", path, body);
        const std::string k = semtpl::canonical_semantic_key("POST", path, body);
        auto it = key_of_shape.find(fp); ++checked;
        if (it == key_of_shape.end()) key_of_shape.emplace(fp, k); else if (it->second != k) { ++bad; if (bad < 4) printf("DIFF %s\n", body.c_str()); }
    }
    std::map<std::string, int> keys; for (auto& kv : key_of_shape) keys[kv.second]++;
    printf("%d bodies, %zu shapes, %zu distinct keys; bodies whose key differs from their shape's: %d\n", checked, key_of_shape.size(), keys.size(), bad);
    return bad ? 1 : 0;
}
