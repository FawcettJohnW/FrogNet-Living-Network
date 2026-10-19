/*
Copyright (C) 2016-2026 Fawcett Innovations LLC
SPDX-License-Identifier: GPL-2.0-only

region_client.h -- the front's door to the MQTT region, as plain C for the broker code. [MEMORY_IS_MEMORY_V1]

Everything Mosquitto kept in its global `db` that another front must see -- subscriptions, the retained messages,
the fan-out of every publish, who holds which client -- lives in the box's region, in this process, shared by every
front thread: a call is a call. With MQTT_REGION set, the box also writes its truths to the memory and reads the other
boxes' from there by triggers (region_client.cpp); nothing here waits for another box. Calls are made from the
broker's loop thread, in order. Deliveries other fronts (or other boxes) produced for this front's clients are handed
to the loop through a lock-free stack; the loop sees them when rgn_eventfd() becomes readable and takes them with
rgn_drain(). Nothing here falls back: a call that cannot be made is logged by the caller and fails.
*/
#ifndef REGION_CLIENT_H
#define REGION_CLIENT_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct rgn_msg {
	uint64_t id;
	const char *topic;
	const void *payload; uint32_t payloadlen;
	const void *props; uint32_t propslen;           /* MQTT v5 properties, wire-encoded with their length prefix */
	uint8_t qos, retain, origin;                    /* origin: 0 a client, 1 the broker */
	int64_t expiry_ms;                              /* absolute unix ms; 0 never */
	const char *src_id;
	const char *src_user;                           /* NULL: none */
	int src_port;
};

struct rgn_match { const char *client; uint8_t qos, retain; uint32_t sid; };

/* [CO_RESIDENT_FRONT_V1] host NULL: the region resident in this process (made by the first front thread to open it,
 * shared by all). Otherwise connects to the mqtt-ram at host:port and says hello. 0 or an error. Per thread: each
 * front thread that opens is a front of its own. */
int rgn_open(const char *host, int port);
bool rgn_is_open(void);
/* the resident region's broker options (an mqtt-ram takes them on its command line) */
void rgn_config(bool upgrade_outgoing_qos, bool allow_duplicate_messages, long max_queued_messages);
/* Resident: say this front is about to sleep. True: deliveries are already waiting -- drain instead of sleeping.
 * (Remote: always false; the waiter thread writes the eventfd.) */
bool rgn_arm(void);
int rgn_front(void);
const char *rgn_error(void);                        /* the last failure's text */

int rgn_own(const char *client, int protocol);
int rgn_sub(const char *client, const char *filter, uint8_t options, uint32_t identifier, bool *is_new);
int rgn_unsub(const char *client, const char *filter, bool *had, bool *root);
int rgn_clean(const char *client);
/* Publish: fan out in the region and retain. Deliveries for this front's own clients are given to `own`, in
 * Mosquitto's order, while the publish runs ([LAZY_MESSAGE_V1]: resident, the client id points into the region and
 * nothing is copied; the message itself is copied only if another front or the retained slot keeps it). */
typedef void (*rgn_own_cb)(const char *client, uint8_t qos, uint8_t retain, uint32_t sid, void *ud);
int rgn_pub(const struct rgn_msg *m, bool *has_subscribers, rgn_own_cb own, void *ud, bool *had_retained);
/* [QOS0_BACKLOG_V1] QoS 0 deliveries to other fronts the last rgn_pub on this thread dropped: their front was backlogged */
int rgn_pub_dropped(void);

/* A publish whose result is handed to `done` after `own` for each of this front's own deliveries, in Mosquitto's
 * order, both before this returns. 0, or an error (then neither callback runs). */
typedef void (*rgn_pub_done_cb)(void *ud, bool has_subscribers, bool had_retained, int dropped);
int rgn_pub_async(const struct rgn_msg *m, rgn_own_cb own, rgn_pub_done_cb done, void *ud);
/* Set (empty payload: clear) the retained message for m->topic without fanning it out (restores, plugins). */
int rgn_retain(const struct rgn_msg *m, bool *had_retained);
/* The retained messages a new subscription to `filter` receives, in Mosquitto's order (valid until the next call). */
int rgn_retained(const char *filter, const struct rgn_msg **msgs, int *n);
/* Every retained message (for the persistence writer). */
int rgn_retained_all(const struct rgn_msg **msgs, int *n);

/* The waiter: started once; its eventfd becomes readable when deliveries are queued. */
int rgn_start_waiter(void);
int rgn_eventfd(void);
/* A delivery for one of this front's clients. Return 0 when taken; 1 when this front no longer holds the client
 * (its session moved): the delivery then goes on to the client's front now; 2 ([HOLD_FOR_ROOM_V1], only when `force`
 * is 0) when the client is connected and its outgoing queue has no room for a QoS 1/2 message: the delivery stays
 * with the region, undrained, and is offered again -- so the fronts publishing into it are paced, not dropped. */
typedef int (*rgn_delivery_cb)(const struct rgn_msg *m, const char *client, uint8_t qos, uint8_t retain, uint32_t sid, int force, void *ud);
/* [SESSION_HANDOVER_V1] kind 1: front `from` wants the session of `client` (clean_start: the new connection's flag);
 * kind 2: the session of `client` handed to us (blob, len: as the giving front wrote it; len 0: there was none). */
typedef void (*rgn_session_cb)(int kind, const char *client, int from, bool clean_start, const void *blob, uint32_t len, void *ud);
int rgn_drain(rgn_delivery_cb cb, rgn_session_cb scb, void *ud);
/* [HOLD_FOR_ROOM_V1] this front's deliveries from other fronts: held ever, delivered anyway at the bound, held now */
void rgn_hold_stats(uint64_t *held, uint64_t *forced, int64_t *now);
/* inside a publish's completion: it sent a QoS 1/2 delivery into a front that is behind (pause its publisher) */
int rgn_last_backpressure(void);
int rgn_held_for(const char *client);   /* [ONE_LINE_PER_SUBSCRIBER_V1] deliveries from other fronts held for it */
/* the most-behind other front's undrained deliveries (remote: as of the last result; asks for a fresh one) */
int64_t rgn_others_behind(void);

/* [SESSION_HANDOVER_V1] Claim a connecting client for this front: *prev is the front that held it (-1: none). */
int rgn_claim(const char *client, int protocol, int *prev);
int rgn_want_session(const char *client, int holder, bool clean_start);
int rgn_give_session(const char *client, int to, const void *blob, uint32_t len);

/* [FRONT_COUNTS_V1] this front's counts, for the others; and the other fronts' counts (resident; remote fronts:
 * zero -- their limits are their own) */
void rgn_counts_publish(int64_t clients, int64_t conns, const int64_t *listener, int n);
/* [ADMIT_BY_RESERVATION_V1] remote: write this front's counts to its slot now and read the others' sums (then
 * rgn_counts_others returns them); a limit check's exact view. Resident: publishes, as rgn_counts_publish. */
int rgn_counts_exchange(int64_t clients, int64_t conns, const int64_t *listener, int n);
void rgn_counts_others(int64_t *clients, int64_t *conns, int listener, int64_t *in_listener);

/* The region's counters as JSON text (owned by the library until the next call). */
const char *rgn_stats(void);
/* how many retained messages the region holds (derived from every thread's counts); -1 if it cannot be read */
long rgn_retained_count(void);

#ifdef __cplusplus
}
#endif
#endif
