/*
Copyright (c) 2010-2021 Roger Light <roger@atchoo.org>
Copyright (C) 2016-2026 Fawcett Innovations LLC (the region conversion)

All rights reserved. This program and the accompanying materials
are made available under the terms of the Eclipse Public License 2.0
and Eclipse Distribution License v1.0 which accompany this distribution.

The Eclipse Public License is available at
   https://www.eclipse.org/legal/epl-2.0/
and the Eclipse Distribution License is available at
  http://www.eclipse.org/org/documents/edl-v10.php.

SPDX-License-Identifier: EPL-2.0 OR BSD-3-Clause
(used here under BSD-3-Clause; this file as changed is part of FrogNet, GPL-2.0-only)

Contributors:
   Roger Light - initial implementation and documentation.
   Fawcett Innovations - subscriptions and fan-out moved into the MQTT region.
*/

/* [SUBS_IN_THE_REGION_V1] Mosquitto kept every subscription in two trees in its one global `db` and walked them on
 * every publish, writing into each subscriber's own message lists. Those trees now live in the MQTT region (mqtt-ram),
 * shared by every front: a subscribe is a write of this client's own subscription cell, a publish is one region
 * operation that walks the trees there (Mosquitto's walk, ported) and hands back the deliveries for this front's own
 * clients; deliveries for clients on other fronts reach those fronts through their waits.
 *
 * What stays here, per client: the client's own list of its subscriptions (context->subs, a mirror the plugin and
 * persistence code read), the read-ACL check, the packet id, and the insert into the client's outgoing lists -- the
 * parts that need the client's connection. */

#include "config.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "mosquitto_broker_internal.h"
#include "mosquitto/mqtt_protocol.h"
#include "net_mosq.h"
#include "mux.h"
#include <time.h>
#include <stdlib.h>
#include "packet_mosq.h"
#include "property_mosq.h"
#include "region_client.h"
#include "sys_tree.h"
#include "send_mosq.h"
#include "util_mosq.h"

#include "utlist.h"


/* ---- the client's own mirror of what it is subscribed to ------------------------------------------------------ */

static struct mosquitto__subleaf *mirror_find(struct mosquitto *context, const char *filter)
{
	for(int i=0; i<context->subs_capacity; i++){
		if(context->subs[i] && !strcmp(context->subs[i]->topic_filter, filter)){
			return context->subs[i];
		}
	}
	return NULL;
}


static int mirror_add(struct mosquitto *context, const struct mosquitto_subscription *sub)
{
	struct mosquitto__subleaf *leaf, **subs;

	leaf = mirror_find(context, sub->topic_filter);
	if(leaf){
		leaf->identifier = sub->identifier;
		leaf->subscription_options = sub->options;
		return MOSQ_ERR_SUCCESS;
	}
	leaf = mosquitto_calloc(1, sizeof(struct mosquitto__subleaf) + strlen(sub->topic_filter) + 1);
	if(!leaf){
		return MOSQ_ERR_NOMEM;
	}
	leaf->context = context;
	leaf->identifier = sub->identifier;
	leaf->subscription_options = sub->options;
	strcpy(leaf->topic_filter, sub->topic_filter);
	for(int i=0; i<context->subs_capacity; i++){
		if(!context->subs[i]){
			context->subs[i] = leaf;
			context->subs_count++;
			return MOSQ_ERR_SUCCESS;
		}
	}
	subs = mosquitto_realloc(context->subs, sizeof(struct mosquitto__subleaf *)*(size_t)(context->subs_capacity + 1));
	if(!subs){
		mosquitto_FREE(leaf);
		return MOSQ_ERR_NOMEM;
	}
	context->subs = subs;
	context->subs_capacity++;
	context->subs_count++;
	context->subs[context->subs_capacity-1] = leaf;
	return MOSQ_ERR_SUCCESS;
}


static void mirror_remove(struct mosquitto *context, const char *filter)
{
	for(int i=0; i<context->subs_capacity; i++){
		if(context->subs[i] && !strcmp(context->subs[i]->topic_filter, filter)){
			mosquitto_FREE(context->subs[i]);
			context->subs_count--;
			return;
		}
	}
}


/* ---- one delivery to one client of this front: subs.c subs__send, after the region's decisions ----------------- */

int sub__deliver(struct mosquitto *context, struct mosquitto__base_msg *stored, uint8_t msg_qos, uint8_t client_retain, uint32_t identifier)
{
	uint16_t mid;
	int rc2;

	if(!context || !context->id){
		return MOSQ_ERR_SUCCESS;
	}
	rc2 = mosquitto_acl_check(context, stored->data.topic, stored->data.payloadlen, stored->data.payload, stored->data.qos, stored->data.retain, stored->data.properties, MOSQ_ACL_READ);
	if(rc2 == MOSQ_ERR_ACL_DENIED){
		return MOSQ_ERR_SUCCESS;
	}else if(rc2 != MOSQ_ERR_SUCCESS){
		return 1;
	}
	if(msg_qos){
		mid = mosquitto__mid_generate(context);
	}else{
		mid = 0;
	}
	if(db__message_insert_outgoing(context, 0, mid, msg_qos, client_retain, stored, identifier, true, true) == 1){
		return 1;
	}
	return 0;
}


/* ---- properties travel wire-encoded ----------------------------------------------------------------------------- */

void retain__persist_view(const char *topic, struct mosquitto__base_msg *base_msg, bool had_old, bool persist);

int sub__props_encode(const mosquitto_property *props, uint8_t **out, uint32_t *outlen)
{
	struct mosquitto__packet *p;
	uint32_t len = mosquitto_property_get_length_all(props);

	*out = NULL; *outlen = 0;
	if(!props){
		return MOSQ_ERR_SUCCESS;
	}
	p = mosquitto_calloc(1, sizeof(struct mosquitto__packet) + len + 5);
	if(!p){
		return MOSQ_ERR_NOMEM;
	}
	p->packet_length = len + 5;
	if(property__write_all(p, props, true)){
		mosquitto_FREE(p);
		return MOSQ_ERR_INVAL;
	}
	*out = mosquitto_malloc(p->pos);
	if(!*out){
		mosquitto_FREE(p);
		return MOSQ_ERR_NOMEM;
	}
	memcpy(*out, p->payload, p->pos);
	*outlen = p->pos;
	mosquitto_FREE(p);
	return MOSQ_ERR_SUCCESS;
}


int sub__props_decode(const void *bytes, uint32_t len, mosquitto_property **props)
{
	struct mosquitto__packet_in in;

	*props = NULL;
	if(len == 0){
		return MOSQ_ERR_SUCCESS;
	}
	memset(&in, 0, sizeof(in));
	in.payload = (uint8_t *)bytes;
	in.remaining_length = len;
	return property__read_all(CMD_PUBLISH, &in, props);
}


/* A message the region sent us, as a local store entry this front's lists can point at. */
struct mosquitto__base_msg *sub__msg_from_region(const struct rgn_msg *m)
{
	struct mosquitto__base_msg *b;
	uint32_t expiry = 0;

	b = mosquitto_calloc(1, sizeof(struct mosquitto__base_msg));
	if(!b){
		return NULL;
	}
	b->data.topic = mosquitto_strdup(m->topic);
	b->data.payloadlen = m->payloadlen;
	if(m->payloadlen){
		b->data.payload = mosquitto_malloc(m->payloadlen + 1);
		if(b->data.payload){
			memcpy(b->data.payload, m->payload, m->payloadlen);
			((uint8_t *)b->data.payload)[m->payloadlen] = 0;
		}
	}
	if(!b->data.topic || (m->payloadlen && !b->data.payload)
			|| sub__props_decode(m->props, m->propslen, &b->data.properties)){
		db__msg_store_free(b);
		return NULL;
	}
	b->data.qos = m->qos;
	b->data.retain = m->retain;
	b->data.source_id = mosquitto_strdup(m->src_id ? m->src_id : "");
	if(m->src_user){
		b->data.source_username = mosquitto_strdup(m->src_user);
	}
	b->origin = m->origin ? mosq_mo_broker : mosq_mo_client;
	for(int i=0; i<db.config->listener_count; i++){
		if(db.config->listeners[i].port == m->src_port){
			b->source_listener = &db.config->listeners[i];
			break;
		}
	}
	(void)expiry;
	b->data.expiry_time = m->expiry_ms ? (time_t)(m->expiry_ms / 1000) : 0;
	b->dest_ids = NULL;
	b->dest_id_count = 0;
	db.msg_store_count++;
	db.msg_store_bytes += b->data.payloadlen;
	b->data.store_id = db__new_msg_id();
	if(db__msg_store_add(b)){
		db__msg_store_free(b);
		return NULL;
	}
	return b;
}


/* ---- subscribe / unsubscribe / clean ------------------------------------------------------------------------------ */

int sub__add(struct mosquitto *context, const struct mosquitto_subscription *sub)
{
	int rc = 0;
	const char *sharename = NULL;
	char *local_sub;
	char **topics;
	bool is_new = false;

	assert(sub);
	assert(sub->topic_filter);

	rc = sub__topic_tokenise(sub->topic_filter, &local_sub, &topics, &sharename);
	if(rc){
		return rc;
	}
	if(strlen(topics[0]) > UINT16_MAX){
		mosquitto_FREE(local_sub);
		mosquitto_FREE(topics);
		return MOSQ_ERR_INVAL;
	}
	mosquitto_FREE(local_sub);
	mosquitto_FREE(topics);

	if(!context || !context->id){
		return MOSQ_ERR_SUCCESS;
	}
	if(rgn_sub(context->id, sub->topic_filter, sub->options, sub->identifier, &is_new)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region subscribe for %s failed: %s", context->id, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	rc = mirror_add(context, sub);
	if(rc){
		return rc;
	}
	if(is_new){
#ifdef WITH_SYS_TREE
		if(sharename){
			db.shared_subscription_count++;
		}else{
			db.subscription_count++;
		}
#endif
		rc = MOSQ_ERR_SUCCESS;
	}else{
		rc = MOSQ_ERR_SUB_EXISTS;
	}
	if(context->protocol == mosq_p_mqtt31 || context->protocol == mosq_p_mqtt5){
		return rc;
	}else{
		/* mqttv311/mqttv5 requires retained messages are resent on
		 * resubscribe. */
		return MOSQ_ERR_SUCCESS;
	}
}


int sub__remove(struct mosquitto *context, const char *sub, uint8_t *reason)
{
	int rc = 0;
	const char *sharename = NULL;
	char *local_sub = NULL;
	char **topics = NULL;
	bool had = false, root = false;

	assert(sub);

	rc = sub__topic_tokenise(sub, &local_sub, &topics, &sharename);
	if(rc){
		return rc;
	}
	mosquitto_FREE(local_sub);
	mosquitto_FREE(topics);

	if(rgn_unsub(context->id, sub, &had, &root)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region unsubscribe for %s failed: %s", context->id, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	if(root){
		*reason = had ? 0 : MQTT_RC_NO_SUBSCRIPTION_EXISTED;
	}
	if(had){
		mirror_remove(context, sub);
#ifdef WITH_SYS_TREE
		if(sharename){
			db.shared_subscription_count--;
		}else{
			db.subscription_count--;
		}
#endif
	}
	return MOSQ_ERR_SUCCESS;     /* as sub__remove_recurse: the reason code says whether there was one */
}


/* Remove all subscriptions for a client. A context whose list is empty has nothing to remove: in particular the old
 * context of a session that was just taken over, whose list moved to the new connection (handle_connect.c) -- cleaning
 * the region for its client id then would remove the new connection's subscriptions. */
int sub__clean_session(struct mosquitto *context)
{
	bool any = false;

	for(int i=0; i<context->subs_capacity; i++){
		if(context->subs[i] == NULL){
			continue;
		}
		any = true;
		plugin_persist__handle_subscription_delete(context, context->subs[i]->topic_filter);
#ifdef WITH_SYS_TREE
		if(!strncmp(context->subs[i]->topic_filter, "$share/", 7)){
			db.shared_subscription_count--;
		}else{
			db.subscription_count--;
		}
#endif
		mosquitto_FREE(context->subs[i]);
	}
	mosquitto_FREE(context->subs);
	context->subs_capacity = 0;
	context->subs_count = 0;

	if(any && context->id && rgn_clean(context->id)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region clean for %s failed: %s", context->id, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	return MOSQ_ERR_SUCCESS;
}


/* ---- publish -------------------------------------------------------------------------------------------------------- */

/* one publish on its way through the region */
struct pub_pass {
	struct mosquitto__base_msg *stored;
	int rc;
	bool retain, have, had_retained;
};

/* [HOLD_FOR_ROOM_V1] Back-pressure on this front. Stock Mosquitto drops a QoS 1/2 message for a connected subscriber
 * whose queue is full (db__message_insert), after the publisher has been told it was accepted. Here the delivery is
 * held instead, in order behind anything already held for that client, and the publisher that caused it is paused:
 * its socket is not read until nothing is held, so TCP and its own inflight window pace it. Every loop pass offers
 * the held again; the subscriber's PUBACKs make room. Bounded: past HOLD_MAX held, or once the oldest has waited
 * HOLD_MAX_MS (a publisher must not stay unread past its keepalive), the held are delivered and Mosquitto's own limit
 * applies, as in stock. A client that has deliveries held is never paused: its acknowledgements are what frees it. */
static int hold_env(const char *name, int dflt)
{
	const char *v = getenv(name);
	int n = v && *v ? atoi(v) : 0;
	return n > 0 ? n : dflt;
}
/* the bounds: 16384 held, 1000 ms; RIBBIT_HOLD_MAX / RIBBIT_HOLD_MAX_MS change them (to test them), and the front says
 * which it runs with when it starts (sub__hold_announce) */
#define HOLD_MAX hold_max()
#define HOLD_MAX_MS hold_max_ms()
static int hold_max(void) { static int v; if(!v) v = hold_env("RIBBIT_HOLD_MAX", 16384); return v; }
static int hold_max_ms(void) { static int v; if(!v) v = hold_env("RIBBIT_HOLD_MAX_MS", 1000); return v; }
void sub__hold_announce(void)
{
	log__printf(NULL, MOSQ_LOG_INFO, "Hold for room: up to %d deliveries held, %d ms the longest, before Mosquitto's own queue limit applies.",
			hold_max(), hold_max_ms());
}
struct own_held {
	struct own_held *next;
	char *client;
	struct mosquitto__base_msg *b;
	int64_t since_ms;
	uint32_t sid;
	uint8_t qos, retain;
};
static __thread struct own_held *oh_head, *oh_tail;
static __thread int oh_n;
struct paused_list { char **ids; int n, cap; int64_t since_ms; };
static __thread struct paused_list paused_own, paused_x;   /* paused for this front's held; for a front behind */
static __thread uint32_t oh_pass;
static __thread const char *delivering_from = "mosquitto";   /* [DIAG-DROP] which path is delivering, for the drop log */
const char *sub__delivering_from(void) { return delivering_from; }
static __thread uint64_t oh_held_total, oh_paused_total, oh_forced_total;
/* [DIAG-PACE] process-wide: publishers paused, and for how long, for this front's own held (own) and for a reader
 * behind (x: another front, another box). Said at exit by the region client. */
static uint64_t pace_own_n, pace_own_ms, pace_x_n, pace_x_ms, pace_x_forced;
void sub__pace_stats(uint64_t *own_n, uint64_t *own_ms, uint64_t *x_n, uint64_t *x_ms, uint64_t *x_forced)
{
	*own_n = __atomic_load_n(&pace_own_n, __ATOMIC_RELAXED); *own_ms = __atomic_load_n(&pace_own_ms, __ATOMIC_RELAXED);
	*x_n = __atomic_load_n(&pace_x_n, __ATOMIC_RELAXED); *x_ms = __atomic_load_n(&pace_x_ms, __ATOMIC_RELAXED);
	*x_forced = __atomic_load_n(&pace_x_forced, __ATOMIC_RELAXED);
}

static int64_t now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static bool no_room(struct mosquitto *context, uint8_t qos)
{
	return qos > 0 && net__is_connected(context)
			&& !db__ready_for_flight(context, mosq_md_out, qos)
			&& !db__ready_for_queue(context, qos, &context->msgs_out);
}

static void set_paused(struct mosquitto *context, bool paused)
{
#ifdef WITH_EPOLL
	mux_epoll__set_paused(context, paused);
#else
	UNUSED(context); UNUSED(paused);
#endif
}

static void pause_on(struct paused_list *l, const char *id)
{
	struct mosquitto *pub;

	if(!id || !*id) return;
	HASH_FIND(hh_id, db.contexts_by_id, id, strlen(id), pub);
	if(!pub || pub->region_paused || pub->region_held > 0 || !net__is_connected(pub)) return;
	if(l->n == l->cap){
		int cap = l->cap ? l->cap * 2 : 16;
		char **n = mosquitto_realloc(l->ids, sizeof(char *) * (size_t)cap);
		if(!n) return;
		l->ids = n; l->cap = cap;
	}
	l->ids[l->n] = mosquitto_strdup(id);
	if(!l->ids[l->n]) return;
	if(l->n == 0) l->since_ms = now_ms();
	l->n++;
	oh_paused_total++;
	__atomic_add_fetch(l == &paused_x ? &pace_x_n : &pace_own_n, 1, __ATOMIC_RELAXED);
	set_paused(pub, true);
}

static void resume_list(struct paused_list *l)
{
	if(l->n) __atomic_add_fetch(l == &paused_x ? &pace_x_ms : &pace_own_ms, (uint64_t)(now_ms() - l->since_ms), __ATOMIC_RELAXED);
	for(int i=0; i<l->n; i++){
		struct mosquitto *pub;
		HASH_FIND(hh_id, db.contexts_by_id, l->ids[i], strlen(l->ids[i]), pub);
		if(pub && pub->region_paused){
			set_paused(pub, false);
		}
		mosquitto_FREE(l->ids[i]);
	}
	l->n = 0;
}
static void pause_publisher(const char *id) { pause_on(&paused_own, id); }
static void resume_publishers(void) { resume_list(&paused_own); }

/* Across fronts: a publisher whose QoS 1/2 message went into a front more than 1024 behind is paused the same way, and
 * read again once the most-behind other front is under half that. Until then the loop wakes every millisecond to look
 * (remote: each look also asks the host for a fresh figure, with a counts-only batch). */
#define RESUME_BEHIND 512
static void others_check(void)
{
	if(!paused_x.n) return;
	/* read again once the others have caught up -- or after HOLD_MAX_MS at most: a publisher is never left unread
	 * past that, whatever the backlog (its keepalive, its PINGREQ); if it publishes into the backlog again it is
	 * paused again */
	if(rgn_others_behind() < RESUME_BEHIND || now_ms() - paused_x.since_ms >= HOLD_MAX_MS){
		if(rgn_others_behind() >= RESUME_BEHIND) __atomic_add_fetch(&pace_x_forced, 1, __ATOMIC_RELAXED);
		resume_list(&paused_x);
	}else{
		loop__update_next_event(1);
	}
}

static bool own_hold(struct mosquitto *context, struct mosquitto__base_msg *b, uint8_t qos, uint8_t retain, uint32_t sid)
{
	struct own_held *h;

	if(oh_n >= HOLD_MAX) return false;
	h = mosquitto_calloc(1, sizeof(*h));
	if(!h) return false;
	h->client = mosquitto_strdup(context->id);
	if(!h->client){ mosquitto_FREE(h); return false; }
	db__msg_store_ref_inc(b);
	h->b = b; h->qos = qos; h->retain = retain; h->sid = sid; h->since_ms = now_ms();
	if(oh_tail) oh_tail->next = h; else oh_head = h;
	oh_tail = h;
	oh_n++;
	oh_held_total++;
	context->region_held++;
	if(context->region_paused){
		set_paused(context, false);      /* its acknowledgements are what make room: read them */
	}
	return true;
}

/* each loop pass, before sleeping: the held, offered again in order */
void sub__own_held_retry(void)
{
	struct own_held *h, *next, *keep_head = NULL, *keep_tail = NULL;
	bool force;
	int kept = 0;

	others_check();
	if(!oh_head){
		if(paused_own.n) resume_publishers();
		return;
	}
	force = oh_n >= HOLD_MAX || now_ms() - oh_head->since_ms >= HOLD_MAX_MS;
	if(force){                                  /* [DIAG-HOLD] the bound reached: what is held is delivered whether or not it fits */
		log__printf(NULL, MOSQ_LOG_NOTICE, "[DIAG-HOLD] own: %d held deliveries forced (bound %d, oldest waited %lld ms, %d publishers paused)",
				oh_n, HOLD_MAX, (long long)(now_ms() - oh_head->since_ms), paused_own.n);
	}
	oh_pass++;
	for(h=oh_head; h; h=next){
		struct mosquitto *context;
		next = h->next;
		h->next = NULL;
		HASH_FIND(hh_id, db.contexts_by_id, h->client, strlen(h->client), context);
		if(context && !force && (context->region_pass == oh_pass || (h->qos > 0 && rgn_held_for(context->id) > 0) || no_room(context, h->qos))){
			context->region_pass = oh_pass;                 /* the rest of its deliveries stay behind this one */
			if(keep_tail) keep_tail->next = h; else keep_head = h;
			keep_tail = h;
			kept++;
			continue;
		}
		if(context){
			context->region_held--;
			delivering_from = force ? "this front, held, its bound forced" : "this front, held, room made";
			sub__deliver(context, h->b, h->qos, h->retain, h->sid);
			delivering_from = "mosquitto";
			if(force) oh_forced_total++;
		}else{
			session__stash(h->client, h->b, h->qos, h->retain, h->sid);   /* the session moved on */
		}
		db__msg_store_ref_dec(&h->b);
		mosquitto_FREE(h->client);
		mosquitto_FREE(h);
	}
	oh_head = keep_head; oh_tail = keep_tail; oh_n = kept;
	if(!oh_head) resume_publishers();
}

bool sub__own_held_any(void)
{
	return oh_head != NULL;
}

void sub__own_held_stats(uint64_t *held, uint64_t *paused, uint64_t *forced, int *now)
{
	*held = oh_held_total; *paused = oh_paused_total; *forced = oh_forced_total; *now = oh_n;
}

/* [LAZY_MESSAGE_V1] one of this front's own clients matched */
static void deliver_own(const char *client, uint8_t qos, uint8_t retain, uint32_t sid, void *ud)
{
	struct pub_pass *p = ud;
	struct mosquitto *context;

	HASH_FIND(hh_id, db.contexts_by_id, client, strlen(client), context);
	if(context){
		/* [HOLD_FOR_ROOM_V1] behind what is held for it, or held because it has no room */
		/* [ONE_LINE_PER_SUBSCRIBER_V1] behind what other fronts have held for it, too */
		if((context->region_held > 0 || (qos > 0 && rgn_held_for(context->id) > 0) || no_room(context, qos)) && own_hold(context, p->stored, qos, retain, sid)){
			pause_publisher(p->stored->data.source_id);
			return;
		}
		delivering_from = "this front, as published";
		if(sub__deliver(context, p->stored, qos, retain, sid) == 1){
			p->rc = 1;
		}
		delivering_from = "mosquitto";
	}
}

/* the publish's result: counted, persisted, acknowledged (when taken over), released */
static void pub_done(void *ud, bool has_subscribers, bool had_retained, int dropped)
{
	struct pub_pass *p = ud;
	/* [QOS0_BACKLOG_V1] QoS 0 deliveries the region dropped because their front was backlogged: counted as Mosquitto
	 * counts a QoS 0 drop, and said once per episode on this front, as it says one per client */
	static __thread bool dropping = false;

	if(dropped > 0){
		metrics__int_inc(mosq_counter_mqtt_publish_dropped, dropped);
		if(!dropping){
			dropping = true;
			log__printf(NULL, MOSQ_LOG_NOTICE, "Outgoing QoS 0 messages to another front are being dropped: it is %ld behind per client.", (long)db.config->max_queued_messages);
		}
	}else if(dropping){
		dropping = false;
	}
	p->have = has_subscribers;
	p->had_retained = had_retained;
	if(rgn_last_backpressure()){                /* [HOLD_FOR_ROOM_V1] it published into a front that is behind */
		pause_on(&paused_x, p->stored->data.source_id);
	}
	if(p->retain){
#ifdef WITH_PERSISTENCE
		if(strncmp(p->stored->data.topic, "$SYS", 4)){
			db.persistence_changes++;
		}
#endif
		retain__persist_view(p->stored->data.topic, p->stored, had_retained, true);
	}
	db__msg_store_ref_dec(&p->stored);
}

int sub__messages_queue(const char *source_id, const char *topic, uint8_t qos, int retain, struct mosquitto__base_msg **stored)
{
	struct rgn_msg m;
	struct pub_pass p;
	int rc;
	uint8_t *props = NULL;
	uint32_t propslen = 0;

	assert(topic);

	/* [MEMORY_IS_MEMORY_V1] the publish is made in this box's region and written once to the memory, and the caller
	 * answers its client now, as Mosquitto does: nothing waits for another box */
	memset(&p, 0, sizeof(p));
	/* Protect this message until we have sent it to all clients (released by pub_done). */
	db__msg_store_ref_inc(*stored);
	p.stored = *stored;
	p.retain = retain ? true : false;
	p.rc = MOSQ_ERR_SUCCESS;

	if(sub__props_encode((*stored)->data.properties, &props, &propslen)){
		db__msg_store_ref_dec(stored);
		return MOSQ_ERR_NOMEM;
	}
	memset(&m, 0, sizeof(m));
	m.topic = topic;
	m.payload = (*stored)->data.payload;
	m.payloadlen = (*stored)->data.payloadlen;
	m.props = props;
	m.propslen = propslen;
	m.qos = qos;
	m.retain = (uint8_t)(retain ? 1 : 0);
	m.origin = (*stored)->origin == mosq_mo_broker ? 1 : 0;
	m.expiry_ms = (int64_t)(*stored)->data.expiry_time * 1000;
	m.src_id = source_id ? source_id : "";
	m.src_user = (*stored)->data.source_username;
	m.src_port = (*stored)->source_listener ? (*stored)->source_listener->port : 0;

	if(rgn_pub_async(&m, deliver_own, pub_done, &p)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region publish to %s failed: %s", topic, rgn_error());
		mosquitto_FREE(props);
		db__msg_store_ref_dec(stored);
		return MOSQ_ERR_UNKNOWN;
	}
	mosquitto_FREE(props);
	rc = p.rc;
	if(rc == MOSQ_ERR_SUCCESS && !p.have){
		rc = MOSQ_ERR_NO_SUBSCRIBERS;
	}
	return rc;
}


/* A delivery another front's publish produced for one of this front's clients. Nonzero: this front no longer holds
 * the client (its session moved on), and the delivery goes on to its front. [SESSION_HANDOVER_V1] */
static int deliver_from_region(const struct rgn_msg *m, const char *client, uint8_t qos, uint8_t retain, uint32_t sid, int force, void *ud)
{
	struct mosquitto *context;
	struct mosquitto__base_msg *b;

	UNUSED(ud);
	HASH_FIND(hh_id, db.contexts_by_id, client, strlen(client), context);
	/* [HOLD_FOR_ROOM_V1] Stock Mosquitto's publisher is paced by the subscriber's queue: the thread that reads the
	 * publish delivers it. Across fronts it is not -- a full queue would drop (db__message_insert) what the publisher
	 * was already told was accepted. So a QoS 1/2 delivery to a connected client with no room is not taken yet. */
	if(!force && context && no_room(context, qos)){   /* [ONE_LINE_PER_SUBSCRIBER_V1] ahead of this front's own held */
		return 2;
	}
	b = sub__msg_from_region(m);
	if(!b){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: out of memory taking a delivery for %s.", client);
		return 0;
	}
	db__msg_store_ref_inc(b);
	if(context){
		delivering_from = force ? "another front, its hold bound forced" : "another front";
		sub__deliver(context, b, qos, retain, sid);
		delivering_from = "mosquitto";
	}else if(!session__stash(client, b, qos, retain, sid)){
		db__msg_store_ref_dec(&b);
		return 1;
	}
	db__msg_store_ref_dec(&b);
	return 0;
}


static void session_from_region(int kind, const char *client, int from, bool clean_start, const void *blob, uint32_t len, void *ud)
{
	UNUSED(ud);
	if(kind == 1){
		session__wanted(client, from, clean_start);
	}else if(kind == 2){
		session__given(client, blob, len);
	}
}


/* [ASYNC_PUBACK_V1] An MQTT 5 client is told "no matching subscribers" only when no box has one. When this box has none
 * the question goes to the memory, off this thread (rgn_ask_subscribers), and the PUBACK waits for the answer: in a
 * list on the client, in PUBLISH order (MQTT 5 4.6), so a PUBACK whose answer this box knew at once waits behind any
 * still asked. Nothing else on this front waits. */
struct region_ack {
	struct region_ack *next;
	uint64_t seq;
	uint16_t mid;
	uint8_t reason;
	bool ready;
};
static uint64_t ack_token_next = 0;

static int acks_flush(struct mosquitto *context)
{
	int rc = MOSQ_ERR_SUCCESS;
	while(context->region_acks && context->region_acks->ready){
		struct region_ack *a = context->region_acks;
		context->region_acks = a->next;
		if(!context->region_acks){
			context->region_acks_tail = NULL;
		}
		if(!rc){
			rc = send__puback(context, a->mid, a->reason, NULL);
		}
		mosquitto_FREE(a);
	}
	return rc;
}

static struct region_ack *acks_append(struct mosquitto *context, uint16_t mid, uint8_t reason, bool ready)
{
	struct region_ack *a = mosquitto_calloc(1, sizeof(struct region_ack));
	if(!a){
		return NULL;
	}
	a->seq = ++context->region_ack_seq;
	a->mid = mid;
	a->reason = reason;
	a->ready = ready;
	if(context->region_acks_tail){
		context->region_acks_tail->next = a;
	}else{
		context->region_acks = a;
	}
	context->region_acks_tail = a;
	return a;
}

int sub__puback(struct mosquitto *context, uint16_t mid, uint8_t reason)
{
	if(!context->region_acks){
		return send__puback(context, mid, reason, NULL);
	}
	return acks_append(context, mid, reason, true) ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NOMEM;
}

int sub__puback_ask(struct mosquitto *context, uint16_t mid, const char *topic)
{
	struct region_ack *a;

	if(!context->region_ack_token){
		context->region_ack_token = __atomic_add_fetch(&ack_token_next, 1, __ATOMIC_RELAXED);
	}
	a = acks_append(context, mid, 0, false);
	if(!a){
		return MOSQ_ERR_NOMEM;
	}
	if(rgn_ask_subscribers(topic, context->id, context->region_ack_token, a->seq)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: asking for subscribers to %s: %s", topic, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	return MOSQ_ERR_SUCCESS;
}

void sub__acks_forget(struct mosquitto *context)
{
	while(context->region_acks){
		struct region_ack *a = context->region_acks;
		context->region_acks = a->next;
		mosquitto_FREE(a);
	}
	context->region_acks_tail = NULL;
	context->region_ack_token = 0;                /* an answer still on its way finds no one */
}

static void on_answer(const char *client, uint64_t token, uint64_t seq, bool has_subscribers, void *ud)
{
	struct mosquitto *context;
	struct region_ack *a;

	UNUSED(ud);
	HASH_FIND(hh_id, db.contexts_by_id, client, strlen(client), context);
	if(!context || context->region_ack_token != token || mosquitto__get_state(context) != mosq_cs_active){
		return;                                     /* that connection is gone: nothing to acknowledge */
	}
	for(a = context->region_acks; a; a = a->next){
		if(a->seq == seq){
			a->ready = true;
			a->reason = has_subscribers ? 0 : MQTT_RC_NO_MATCHING_SUBSCRIBERS;
			break;
		}
	}
	if(acks_flush(context)){
		log__printf(NULL, MOSQ_LOG_NOTICE, "Client %s: a PUBACK could not be sent.", client);
	}
}

void sub__region_drain(void)
{
	rgn_answers(on_answer, NULL);                 /* [ASYNC_PUBACK_V1] */
	rgn_drain(deliver_from_region, session_from_region, NULL);
}


void sub__region_init(void)
{
}


/* [SESSION_HANDOVER_V1] a handed-over session's subscriptions list */
int sub__mirror_add(struct mosquitto *context, const char *filter, uint8_t options, uint32_t identifier)
{
	struct mosquitto_subscription sub;

	memset(&sub, 0, sizeof(sub));
	sub.topic_filter = (char *)filter;
	sub.options = options;
	sub.identifier = identifier;
	return mirror_add(context, &sub);
}


void sub__tree_print(struct mosquitto__subhier *root, int level)
{
	UNUSED(root);
	UNUSED(level);
	printf("%s\n", rgn_stats());
}


int sub__init(void)
{
	return MOSQ_ERR_SUCCESS;
}
