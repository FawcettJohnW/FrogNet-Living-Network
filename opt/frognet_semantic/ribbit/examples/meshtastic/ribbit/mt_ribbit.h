// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mt_ribbit.h -- the firmware's one view of the memory [MT_PACKETS_ON_THE_RAM_V1]. Plain functions so the firmware
// (C++17, Arduino/Portduino headers) never includes the platform (C++20). The platform side is mt_ribbit.cpp.
//
// A node sends a MeshPacket by writing it into its own cells; it receives by a held read on the family. Nothing is
// asked of another node and nothing is acknowledged.
#pragma once
#include <cstddef>
#include <cstdint>

extern "C" {
// Called on the held read's thread for each packet another node wrote, in the family's write order. row: the row's id
// in the memory; family: the family it came from ("msg|8", "dm|!0012abcd") -- what the node's mark names once a
// client has been handed the packet. [MT_READ_MARKS_V1]
typedef void (*mtr_on_packet)(const uint8_t *bytes, size_t len, uint64_t row, const char *family, void *ctx);

// where: "HOST:PORT" (a memory elsewhere) or "here:HOST:PORT" (this process holds it). me: this node's number.
// Returns 0 when the memory is reached and the held read is running; -1 with the reason logged otherwise.
int mtr_start(const char *where, uint32_t me, mtr_on_packet on, void *ctx);

// [MT_MESSAGES_BY_FAMILY_V1] Write one encoded MeshPacket into this node's own ring of the family it belongs to:
// a broadcast is (mt, msg|<channel hash>, !S.<n>), read by the edges holding that channel; anything addressed to one
// node is (mt, dm|!R, !S.<n>), read by R's edge only. Returns at once; nobody is waited for. Rings outlive the
// writer's session (a message is a record the sender made; the reader's mark decides what it has seen).
void mtr_send(const uint8_t *bytes, size_t len, uint32_t to, uint8_t channel);
// Hold the channel family (mt, msg|<hash>): the node has this channel. Idempotent; from the node's mark on that family
// (nothing handed to a client yet: from now on).
void mtr_hold_channel(uint8_t hash);

// [MT_READ_MARKS_V1] The node's mark on a family: the newest row of it a client of this node has been handed,
// (mt, mark, !N.<family>), the node's own cell, kept past its session. Stock's to-phone queue (32 deep, persisted as
// receive_queue) becomes this: nothing is queued; what a client has not been handed is still in the senders' rings.
void mtr_mark(const char *family, uint64_t row);
// One read of the mark: the row id, 0 when there is none.
uint64_t mtr_mark_read(const char *family);
// What a family holds past the node's mark, oldest first, on the caller's thread (a client connected: catch up).
// Returns the count. Rows are handed to on() like the held read's.
size_t mtr_read_past_mark(const char *family, mtr_on_packet on, void *ctx);
// Every family this node holds (msg|<hash> for its channels, dm|!me), one call of on() per family name.
size_t mtr_families(void (*on)(const char *family, void *ctx), void *ctx);

// The version line every log and the bench print.
const char *mtr_version(void);
// The example's VERSION line as built (every tag the firmware carries), so a node's log says what is deployed.
const char *mtr_front_version(void);

// [MT_OWN_FACTS_V1] A node's own fact, one latest-value cell: (mtf, <topic>, !<node>). topic: "user", "pos",
// "tel.<variant>", "status". payload: the protobuf the node would put on the air for it (User, Position, Telemetry,
// StatusMessage). Returns at once.
void mtr_write_fact(const char *topic, uint32_t node, const uint8_t *payload, size_t len);
// [MT_PEER_MARKS_V1] The same, for a fact that outlives the writer's session (a mark on a peer -- favorite, ignored --
// is the node's standing opinion, not its presence): said once per topic, before its first write.
void mtr_write_fact_kept(const char *topic, uint32_t node, const uint8_t *payload, size_t len);
// [MT_OWN_FACTS_ON_CHANGE_V1] The owner no longer has this fact (a fixed position removed): the cell stays the owner's
// but carries no payload. Readers see no cell; the held read hands it over as gone. Returns at once.
void mtr_clear_fact(const char *topic, uint32_t node);

// [MT_NODEDB_IS_A_HELD_VIEW_V1] Hold one read on every fact family (the whole mtf space), from what is there now.
// Rows are collected on the held read's thread and handed over only by mtr_take_facts, on the caller's thread; wake(ctx)
// is called on the held read's thread when rows arrive and none were waiting (the caller schedules its take).
int mtr_hold_facts(void (*wake)(void *ctx), void *ctx);
// gone: the owner's session ended and the cell went with it (payload empty). t: the owner's write time (unix s).
// live: 0 for the rows that were there when the hold started, 1 for every change after (a cell written while held).
typedef void (*mtr_on_fact)(const char *topic, uint32_t node, const uint8_t *payload, size_t len, int gone, uint32_t t,
                            int live, void *ctx);
// Everything the held read collected since the last take, oldest first, on the caller's thread. Returns the count.
size_t mtr_take_facts(mtr_on_fact on, void *ctx);

// [MT_DUMP_READS_CELLS_V1] One read of fact cells as they are now (live ones only), on the caller's thread: on() per
// cell, then it returns the count. topic: "user", "pos", "status", or "tel.*" (every topic with that prefix).
// t: when the owner wrote the cell (its clock, unix seconds), 0 when the cell carries none.
typedef void (*mtr_on_cell)(const char *topic, uint32_t node, const uint8_t *payload, size_t len, uint32_t t, void *ctx);
size_t mtr_read_facts(const char *topic, mtr_on_cell on, void *ctx);

// [MT_OTHER_NODES_ARE_CELLS_V1] One read of one owner's cell (mtf, topic, !node) as it is now. 1: the payload is in
// out (len bytes, at most cap) and t is the owner's write time; 0: no such cell (or it does not decode).
int mtr_read_cell(const char *topic, uint32_t node, uint8_t *out, size_t cap, size_t *len, uint32_t *t);
}
