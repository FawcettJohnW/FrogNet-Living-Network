// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mesh_app.h -- Meshtastic on the memory, for a program on the node. [MT_APP_LIBRARY_V1]
//
// An app is a participant: it has a node number, writes its own user cell, reads every other node's cells and holds
// reads on them, and sends text into a channel's family or to a node -- with the packet built and encrypted here.
// Nothing Meshtastic is needed on the caller's side: no protobufs, no packets, no keys beyond the channel's PSK.
// Held reads are the callbacks; nothing polls. Plain C so any language binds.
//
//   mta_init("127.0.0.1:4404", "my-app")   join that memory          mta_init(NULL, "my-app")  stand up our own
//   mta_nodes(cb, ctx)                      every node now, decoded   mta_node(num, &n)         one node
//   mta_on_node(cb, ctx)                    a held read: each node change, decoded, as it lands
//   mta_channel("LongFast", NULL, 0)        a channel we are on: name + PSK (NULL/0: the default key); returns its hash
//   mta_on_text(cb, ctx)                    a held read: each text on our channels, or to us, decrypted, as it lands
//   mta_send_text(hash, 0, "hello")         broadcast on the channel; mta_send_text(hash, node, ...) to a node
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t num;
    char id[16], short_name[8], long_name[40];
    int has_position;
    double latitude, longitude;
    int altitude;
    int has_metrics;
    uint32_t battery_level; // percent; 101 = powered
    float voltage, channel_utilization, air_util_tx;
    uint32_t uptime_seconds;
    uint32_t last_heard; // unix s: when the node last wrote its user cell
    int gone;            // 1: the node's session ended (only from mta_on_node)
} mta_node_t;

typedef struct {
    uint32_t from, to; // to: 0xffffffff for a broadcast
    uint8_t channel;   // the channel hash
    uint32_t id, rx_time;
    char text[240];
} mta_text_t;

typedef void (*mta_node_cb)(const mta_node_t *node, void *ctx);
typedef void (*mta_text_cb)(const mta_text_t *text, void *ctx);

// where: "HOST:PORT" (a memory elsewhere), "here:HOST:PORT" (this process holds it and listens), NULL ("here": our own,
// not listening). name: this app's name; its node number and short name come from it. Returns 0 when the memory is
// reached and our user cell is written, -1 otherwise (the reason is on stderr).
int mta_init(const char *where, const char *name);
uint32_t mta_my_num(void);
const char *mta_version(void);

// one read of every node's cells now (user; position, device metrics when present), cb() per node; returns the count
size_t mta_nodes(mta_node_cb cb, void *ctx);
// one node, 1 if its user cell is present
int mta_node(uint32_t num, mta_node_t *out);
// a held read on every node's cells: cb() per change, decoded, on the library's thread, from now on
int mta_on_node(mta_node_cb cb, void *ctx);

// a channel this app is on. psk: the key bytes (16 or 32), or NULL/len 1 with psk_len 0 for Meshtastic's default key;
// returns the channel hash (what messages carry), or -1
int mta_channel(const char *name, const uint8_t *psk, size_t psk_len);
// a held read on our channels' families and on messages to us: cb() per text, decrypted, on the library's thread
int mta_on_text(mta_text_cb cb, void *ctx);
// send text: to 0 = broadcast on the channel; otherwise to that node (channel-encrypted, as a stock DM without PKI)
int mta_send_text(int channel_hash, uint32_t to, const char *text);

#ifdef __cplusplus
}
#endif
