// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mesh_app_demo.cpp -- the app library, driven from a script. [MT_APP_LIBRARY_V1]
//   mesh-app-demo HOST:PORT NAME nodes                 list every node now, decoded
//   mesh-app-demo HOST:PORT NAME listen SECONDS        hold reads: print node changes and texts on LongFast
//   mesh-app-demo HOST:PORT NAME send TEXT [TO]        send TEXT on LongFast (broadcast, or to node TO in hex)
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>

#include "mesh_app.h"

static void printNode(const mta_node_t *n, void *)
{
    if (n->gone) { printf("NODE %s gone\n", n->id); fflush(stdout); return; }
    printf("NODE %s %s \"%s\" last_heard=%u", n->id, n->short_name, n->long_name, n->last_heard);
    if (n->has_position) printf(" pos=%.5f,%.5f,%d", n->latitude, n->longitude, n->altitude);
    if (n->has_metrics) printf(" batt=%u%% %.2fV up=%us", n->battery_level, n->voltage, n->uptime_seconds);
    printf("\n");
    fflush(stdout);
}
static void printText(const mta_text_t *t, void *)
{
    printf("TEXT from=!%08x to=!%08x ch=%u id=0x%08x \"%s\"\n", t->from, t->to, t->channel, t->id, t->text);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: see header\n"); return 2; }
    if (mta_init(argv[1], argv[2]) != 0) return 1;
    std::string mode = argv[3];
    int lf = mta_channel("LongFast", nullptr, 0);
    if (mode == "nodes") {
        size_t n = mta_nodes(printNode, nullptr);
        printf("NODES %zu\n", n);
        return 0;
    }
    if (mode == "listen" && argc >= 5) {
        mta_on_node(printNode, nullptr);
        mta_on_text(printText, nullptr);
        std::this_thread::sleep_for(std::chrono::seconds(atoi(argv[4])));
        printf("LISTEN DONE\n");
        return 0;
    }
    if (mode == "send" && argc >= 5) {
        uint32_t to = argc >= 6 ? (uint32_t)strtoul(argv[5], nullptr, 16) : 0;
        int rc = mta_send_text(lf, to, argv[4]);
        std::this_thread::sleep_for(std::chrono::milliseconds(500)); // the write leaves before we do
        printf("SENT rc=%d\n", rc);
        return rc ? 1 : 0;
    }
    return 2;
}
