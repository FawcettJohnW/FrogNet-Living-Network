// fdf-events -- [FDF_EVENTS_V1] one held READ on the members' family that reports the region's changes as lines on stdout, for programs that
// are not FNWP clients themselves (fdf-hub, region.py): on a participant change it pulls the current snapshot over THIS
// same session and prints it ("p\t<ms>\t<participants-json>"); "m" when any mapping was registered or removed. One line
// per event, flushed; nothing polls, and there is no second channel -- the snapshot rides the one held connection, so a
// reader that reconnects after a cut gets the authoritative set in the same round trip (no out-of-band poll to race).
// [SNAPSHOT_OVER_HELD_SESSION_V1] Exits (status 1) when the session ends, which is the reader's signal that the region
// is gone. Reaches the RAM manager like every client (RINA_RAM or RINA_AREA).
#include "fdf_region_client.hpp"
#include "fdf_version.hpp"
#include "fdf_crash.hpp"
#include <chrono>
#include <cstdio>
#include <string>
#include <unistd.h>
int main() {
    fdf_crash::install("fdf-events");                                             // [FDF_CRASH_HANDLER_V1] self-report a fatal signal
    try {
        // [MEMBER_IS_A_TUPLE_V1][NO_PUSH_V1] a READ, not a notice: one held read on the members' own cells (pond, member) --
        // a member joining, changing its facts (written after its mappings, so "m" too) or gone -- and each time it
        // returns, the snapshot over this same session. Nothing is pushed to this program.
        fdf_region::Client c; frogram::Memory mem(c.session(), fdf_region::api());
        { frogram::Json v = c.call("{\"op\":\"version\"}"); fprintf(stderr, "fdf-events: build %s; region is build %s\n", FDF_BUILD_TAG, v["ok"].b ? v["server"].s.c_str() : "?"); }
        auto emit = [&](const char* kind) {
            const auto t0 = std::chrono::steady_clock::now();
            const std::string snap = c.call_text("{\"op\":\"participants\"}");
            const long ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
            printf("%s\t%ld\t%s\n", kind, ms, snap.c_str()); fflush(stdout);
        };
        int64_t at = 0;
        for (auto& r : mem.read("pond", "member")) if (int64_t(r.id) > at) at = int64_t(r.id);
        emit("ready");
        for (;;) {
            bool any = false;
            for (auto& r : mem.read("pond", "member", "", at, 600)) { if (int64_t(r.id) > at) at = int64_t(r.id); any = true; }
            if (any) { emit("p"); printf("m\n"); fflush(stdout); }
        }
    } catch (const std::exception& e) { fprintf(stderr, "fdf-events: the session ended: %s\n", e.what()); printf("gone\n"); fflush(stdout); return 1; }
}
