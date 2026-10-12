// fdf-events -- [FDF_EVENTS_V1] one held session that reports the region's changes as lines on stdout, for programs that
// are not FNWP clients themselves (fdf-hub, region.py): "p" when the participant set changed (a session up or down, an
// announcement, re-announced facts), "m" when any mapping was registered or removed. One line per event, flushed;
// nothing polls. Exits (status 1) when the session ends, which is the reader's signal that the region is gone.
// Reaches the RAM manager like every client (RINA_RAM or RINA_AREA).
#include "rina_region_client.hpp"
#include "fdf_version.hpp"
#include <cstdio>
#include <unistd.h>
int main() {
    try {
        rina_region::Client c; const std::string me = "ev:" + std::to_string(getpid());
        frogram::Json r = c.call("{\"op\":\"watch_participants\",\"proc\":" + frogram::Json::quote(me) + "}");
        if (!r["ok"].b) { fprintf(stderr, "fdf-events: watch_participants refused: %s\n", r["error"].s.c_str()); return 1; }
        r = c.call("{\"op\":\"map_watch\",\"name\":\"*\",\"proc\":" + frogram::Json::quote(me) + "}");
        if (!r["ok"].b) { fprintf(stderr, "fdf-events: map_watch refused: %s\n", r["error"].s.c_str()); return 1; }
        { frogram::Json v = c.call("{\"op\":\"version\"}"); fprintf(stderr, "fdf-events: build %s; region is build %s\n", FDF_BUILD_TAG, v["ok"].b ? v["server"].s.c_str() : "(older than fdf-2026-10-09-r3)"); }
        printf("ready\n"); fflush(stdout);
        for (unsigned long long at = 0;;) {
            const std::string t = c.held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote(me) + ",\"after\":" + std::to_string(at) + "}", 3.0e8);
            bool p = false, m = false;                        // the wait's event lines: "<n>\t<bag>\n" after a 2-byte header
            for (size_t i = 2; i < t.size();) {
                const size_t tab = t.find('\t', i), nl = t.find('\n', i); if (tab == std::string::npos || nl == std::string::npos) break;
                const unsigned long long n = std::stoull(t.substr(i, tab - i)); if (n > at) at = n;
                const std::string bag = t.substr(tab + 1, nl - tab - 1);
                if (bag.find("\"k\":\"p\"") != std::string::npos) p = true;
                if (bag.find("\"k\":\"m\"") != std::string::npos) m = true;
                if (bag.find("\"k\":\"x\"") != std::string::npos) { printf("gone\n"); fflush(stdout); return 1; }
                i = nl + 1;
            }
            if (p) printf("p\n");
            if (m) printf("m\n");
            fflush(stdout);
        }
    } catch (const std::exception& e) { fprintf(stderr, "fdf-events: the session ended: %s\n", e.what()); printf("gone\n"); fflush(stdout); return 1; }
}
