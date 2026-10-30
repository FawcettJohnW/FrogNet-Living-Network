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
// Called on the held read's thread for each packet another node wrote, in the family's write order.
typedef void (*mtr_on_packet)(const uint8_t *bytes, size_t len, void *ctx);

// where: "HOST:PORT" (a memory elsewhere) or "here:HOST:PORT" (this process holds it). me: this node's number.
// Returns 0 when the memory is reached and the held read is running; -1 with the reason logged otherwise.
int mtr_start(const char *where, uint32_t me, mtr_on_packet on, void *ctx);

// Write one encoded MeshPacket into this node's own ring. Returns at once; nobody is waited for.
void mtr_send(const uint8_t *bytes, size_t len);

// The version line every log and the bench print.
const char *mtr_version(void);

// [MT_OWN_FACTS_V1] A node's own fact, one latest-value cell: (mtf, <topic>, !<node>). topic: "user", "pos",
// "tel.<variant>", "status". payload: the protobuf the node would put on the air for it (User, Position, Telemetry,
// StatusMessage). Returns at once.
void mtr_write_fact(const char *topic, uint32_t node, const uint8_t *payload, size_t len);

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
}
