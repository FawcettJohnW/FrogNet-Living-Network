// net_config_test RINA_RAM_HOST:PORT EXPECT_REACH EXPECT_MAP -- [NET_CONFIG_IS_A_TUPLE_V1] [NET_CONFIG_IS_THE_RAM_MANAGERS_V1]
// over FNW1, as every member speaks: (pond, config, net) is there with the expected reach and map; a member's write of
// it is refused; a member's remove of it removes nothing; an ordinary pond cell is still written and removed.
#include "frogram.hpp"
#include <cstdio>
#include <cstdlib>
#include <string>

static int fails = 0;
static void check(bool ok, const std::string& w) { printf("%s %s\n", ok ? "ok  " : "FAIL", w.c_str()); if (!ok) ++fails; }

int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: net_config_test HOST:PORT EXPECT_REACH EXPECT_MAP\n"); return 2; }
    const std::string where = argv[1]; const size_t c = where.rfind(':');
    static const char* API = "/FawcettInnovations.RINA.ram_interface.php";
    frogram::Session s(where.substr(0, c), atoi(where.c_str() + c + 1), API); frogram::Memory m(s, API);
    auto cells = m.read("pond", "config", "net");
    check(cells.size() == 1, "one (pond, config, net) cell: " + std::to_string(cells.size()));
    if (cells.empty()) return 1;
    const uint64_t id = cells[0].id;
    check(cells[0].bag["reach"].s == argv[2], std::string("reach = ") + cells[0].bag["reach"].s);
    check(cells[0].bag["map"].s == argv[3], std::string("map = ") + cells[0].bag["map"].s);
    check(cells[0].bag["hub"].s == "10.253.253.254" && cells[0].bag["leaves"].s == "10.252.0.0/16", "hub and leaves published");
    bool refused = false; std::string why;
    try { m.write("pond", "config", "net", "{\"reach\":\"all\",\"map\":\"allowed\"}"); } catch (const std::exception& e) { refused = true; why = e.what(); }
    check(refused, "a member's write of (pond, config) is refused: " + why);
    m.remove(id);
    auto after = m.read("pond", "config", "net");
    check(after.size() == 1 && after[0].id == id && after[0].bag["reach"].s == argv[2], "a member's remove of it removes nothing");
    const uint64_t w = m.write("pond", "where", "w:net-config-test", "{\"from\":\"t\"}");
    m.remove(w);
    check(m.read("pond", "where", "w:net-config-test").empty(), "an ordinary pond cell is still written and removed");
    printf("%s\n", fails ? "RESULT FAIL" : "RESULT PASS net config: published, read-only to members");
    return fails ? 1 : 0;
}
