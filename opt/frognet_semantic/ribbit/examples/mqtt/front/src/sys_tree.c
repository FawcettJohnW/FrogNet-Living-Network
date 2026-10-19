/*
Copyright (c) 2009-2021 Roger Light <roger@atchoo.org>

All rights reserved. This program and the accompanying materials
are made available under the terms of the Eclipse Public License 2.0
and Eclipse Distribution License v1.0 which accompany this distribution.

The Eclipse Public License is available at
   https://www.eclipse.org/legal/epl-2.0/
and the Eclipse Distribution License is available at
  http://www.eclipse.org/org/documents/edl-v10.php.

SPDX-License-Identifier: EPL-2.0 OR BSD-3-Clause

Contributors:
   Roger Light - initial implementation and documentation.
*/

#ifdef WITH_SYS_TREE

#include "config.h"

#include <math.h>
#include <stdio.h>
#include <inttypes.h>
#include <limits.h>
#include <inttypes.h>

#include "mosquitto_broker_internal.h"
#include "region_client.h"
#include "sys_tree.h"

#define BUFLEN 100

#define SYS_TREE_QOS 2

#define METRIC_LOAD_1MIN 1
#define METRIC_LOAD_5MIN 2
#define METRIC_LOAD_15MIN 3

struct metric {
	int64_t current;
	int64_t next;
	const char *topic, *topic_alias;
	bool is_max;
};

struct metric_load {
	double current;
	const char *topic;
	int load_ref;
	uint8_t interval;
};

/* [FRONT_THREADS_V1] one table for the process: every front thread counts into it, with atomic adds */
struct metric metrics[mosq_metric_max] = {
	{ 1, 0, "$SYS/broker/clients/total", NULL, false }, /* mosq_gauge_clients_total */
	{ 1, 0, "$SYS/broker/clients/maximum", NULL, true }, /* metric_clients_maximum */
	{ 1, 0, "$SYS/broker/clients/disconnected", "$SYS/broker/clients/inactive", false }, /* mosq_gauge_clients_disconnected */
	{ 1, 0, "$SYS/broker/clients/connected", "$SYS/broker/clients/active", false }, /* mosq_gauge_clients_connected */
	{ 1, 0, "$SYS/broker/clients/expired", NULL, false }, /* mosq_counter_clients_expired */
	{ 1, 0, "$SYS/broker/messages/stored", "$SYS/broker/store/messages/count", false }, /* mosq_gauge_message_store_count */
	{ 1, 0, "$SYS/broker/store/messages/bytes", NULL, false }, /* mosq_gauge_message_store_bytes */
	{ 1, 0, "$SYS/broker/subscriptions/count", NULL, false }, /* mosq_gauge_subscription_count */
	{ 1, 0, "$SYS/broker/shared_subscriptions/count", NULL, false }, /* mosq_gauge_shared_subscription_count */
	{ 1, 0, "$SYS/broker/retained messages/count", NULL, false }, /* mosq_gauge_retained_message_count */
#ifdef WITH_MEMORY_TRACKING
	{ 1, 0, "$SYS/broker/heap/current", NULL, false }, /* mosq_gauge_heap_current */
	{ 1, 0, "$SYS/broker/heap/maximum", NULL, true }, /* mosq_gauge_heap_maximum */
#else
	{ 1, 0, NULL, NULL, 0 }, /* mosq_gauge_heap_current */
	{ 1, 0, NULL, NULL, 0 }, /* mosq_gauge_heap_maximum */
#endif
	{ 1, 0, "$SYS/broker/messages/received", NULL, false }, /* mosq_counter_messages_received */
	{ 1, 0, "$SYS/broker/messages/sent", NULL, false }, /* mosq_counter_messages_sent */
	{ 1, 0, "$SYS/broker/bytes/received", NULL, false }, /* mosq_counter_bytes_received */
	{ 1, 0, "$SYS/broker/bytes/sent", NULL, false }, /* mosq_counter_bytes_sent */
	{ 1, 0, "$SYS/broker/publish/bytes/received", NULL, false }, /* mosq_counter_pub_bytes_received */
	{ 1, 0, "$SYS/broker/publish/bytes/sent", NULL, false }, /* mosq_counter_pub_bytes_sent */
	{ 1, 0, "$SYS/broker/packet/out/count", NULL, false }, /* mosq_gauge_out_packet_count */
	{ 1, 0, "$SYS/broker/packet/out/bytes", NULL, false }, /* mosq_gauge_out_packet_bytes */
	{ 1, 0, "$SYS/broker/connections/socket/count", NULL, false }, /* mosq_counter_socket_connections */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_connect_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_connect_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_connack_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_connack_sent */
	{ 1, 0, "$SYS/broker/publish/messages/dropped", NULL, false }, /* mosq_counter_mqtt_publish_dropped */
	{ 1, 0, "$SYS/broker/publish/messages/received", NULL, false }, /* mosq_counter_mqtt_publish_received */
	{ 1, 0, "$SYS/broker/publish/messages/sent", NULL, false }, /* mosq_counter_mqtt_publish_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_puback_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_puback_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubrec_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubrec_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubrel_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubrel_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubcomp_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pubcomp_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_subscribe_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_subscribe_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_suback_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_suback_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_unsubscribe_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_unsubscribe_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_unsuback_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_unsuback_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pingreq_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pingreq_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pingresp_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_pingresp_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_disconnect_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_disconnect_sent */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_auth_received */
	{ 1, 0, NULL, NULL, false }, /* mosq_counter_mqtt_auth_sent */
};

struct metric_load metric_loads[mosq_metric_load_max] = {
	{ 0.0, "$SYS/broker/load/messages/received/1min", mosq_counter_messages_received, METRIC_LOAD_1MIN }, /* metric_load_messages_received_1min */
	{ 0.0, "$SYS/broker/load/messages/received/5min", mosq_counter_messages_received, METRIC_LOAD_5MIN }, /* metric_load_messages_received_5min */
	{ 0.0, "$SYS/broker/load/messages/received/15min", mosq_counter_messages_received, METRIC_LOAD_15MIN }, /* metric_load_messages_received_15min */
	{ 0.0, "$SYS/broker/load/messages/sent/1min", mosq_counter_messages_sent, METRIC_LOAD_1MIN }, /* metric_load_messages_sent_1min */
	{ 0.0, "$SYS/broker/load/messages/sent/5min", mosq_counter_messages_sent, METRIC_LOAD_5MIN }, /* metric_load_messages_sent_5min */
	{ 0.0, "$SYS/broker/load/messages/sent/15min", mosq_counter_messages_sent, METRIC_LOAD_15MIN }, /* metric_load_messages_sent_15min */
	{ 0.0, "$SYS/broker/load/publish/dropped/1min", mosq_counter_mqtt_publish_dropped, METRIC_LOAD_1MIN }, /* metric_load_pub_messages_dropped_1min */
	{ 0.0, "$SYS/broker/load/publish/dropped/5min", mosq_counter_mqtt_publish_dropped, METRIC_LOAD_5MIN }, /* metric_load_pub_messages_dropped_5min */
	{ 0.0, "$SYS/broker/load/publish/dropped/15min", mosq_counter_mqtt_publish_dropped, METRIC_LOAD_15MIN }, /* metric_load_pub_messages_dropped_15min */
	{ 0.0, "$SYS/broker/load/publish/received/1min", mosq_counter_mqtt_publish_received, METRIC_LOAD_1MIN }, /* metric_load_pub_messages_received_1min */
	{ 0.0, "$SYS/broker/load/publish/received/5min", mosq_counter_mqtt_publish_received, METRIC_LOAD_5MIN }, /* metric_load_pub_messages_received_5min */
	{ 0.0, "$SYS/broker/load/publish/received/15min", mosq_counter_mqtt_publish_received, METRIC_LOAD_15MIN }, /* metric_load_pub_messages_received_15min */
	{ 0.0, "$SYS/broker/load/publish/sent/1min", mosq_counter_mqtt_publish_sent, METRIC_LOAD_1MIN }, /* metric_load_pub_messages_sent_1min */
	{ 0.0, "$SYS/broker/load/publish/sent/5min", mosq_counter_mqtt_publish_sent, METRIC_LOAD_5MIN }, /* metric_load_pub_messages_sent_5min */
	{ 0.0, "$SYS/broker/load/publish/sent/15min", mosq_counter_mqtt_publish_sent, METRIC_LOAD_15MIN }, /* metric_load_pub_messages_sent_15min */
	{ 0.0, "$SYS/broker/load/bytes/received/1min", mosq_counter_bytes_received, METRIC_LOAD_1MIN }, /* metric_load_bytes_received_1min */
	{ 0.0, "$SYS/broker/load/bytes/received/5min", mosq_counter_bytes_received, METRIC_LOAD_5MIN }, /* metric_load_bytes_received_5min */
	{ 0.0, "$SYS/broker/load/bytes/received/15min", mosq_counter_bytes_received, METRIC_LOAD_15MIN }, /* metric_load_bytes_received_15min */
	{ 0.0, "$SYS/broker/load/bytes/sent/1min", mosq_counter_bytes_sent, METRIC_LOAD_1MIN }, /* metric_load_bytes_sent_1min */
	{ 0.0, "$SYS/broker/load/bytes/sent/5min", mosq_counter_bytes_sent, METRIC_LOAD_5MIN }, /* metric_load_bytes_sent_5min */
	{ 0.0, "$SYS/broker/load/bytes/sent/15min", mosq_counter_bytes_sent, METRIC_LOAD_15MIN }, /* metric_load_bytes_sent_15min */
	{ 0.0, "$SYS/broker/load/sockets/1min", mosq_counter_socket_connections, METRIC_LOAD_1MIN }, /* metric_load_socket_connections_1min */
	{ 0.0, "$SYS/broker/load/sockets/5min", mosq_counter_socket_connections, METRIC_LOAD_5MIN }, /* metric_load_socket_connections_5min */
	{ 0.0, "$SYS/broker/load/sockets/15min", mosq_counter_socket_connections, METRIC_LOAD_15MIN }, /* metric_load_socket_connections_15min */
	{ 0.0, "$SYS/broker/load/connections/1min", mosq_counter_mqtt_connect_received, METRIC_LOAD_1MIN }, /* metric_load_connections_1min */
	{ 0.0, "$SYS/broker/load/connections/5min", mosq_counter_mqtt_connect_received, METRIC_LOAD_5MIN }, /* metric_load_connections_5min */
	{ 0.0, "$SYS/broker/load/connections/15min", mosq_counter_mqtt_connect_received, METRIC_LOAD_15MIN }, /* metric_load_connections_15min */
};

static time_t start_time = 0;   /* the process's, read by the HTTP API's own thread too */
static __thread time_t last_update = 0;


time_t broker_uptime(void)
{
	return db.now_s - start_time;
}


static void calc_load(char *buf, double exponent, double i_mult, struct metric_load *m, bool force)
{
	double new_value;
	uint32_t len;
	double interval;

	interval = (double)(metrics[m->load_ref].next - metrics[m->load_ref].current)*i_mult;
	new_value = interval + exponent*(m->current - interval);
	if(fabs(new_value - (m->current)) >= 0.01 || force){
		len = (uint32_t)snprintf(buf, BUFLEN, "%.2f", new_value);
		db__messages_easy_queue(NULL, m->topic, SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);
	}
	m->current = new_value;
}


void sys_tree__init(void)
{
	char buf[BUFLEN];
	uint32_t len;

	if(db.config->sys_interval == 0){
		return;
	}

	/* Set static $SYS messages */
	len = (uint32_t)snprintf(buf, 64, "mosquitto version %s", VERSION);
	db__messages_easy_queue(NULL, "$SYS/broker/version", SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);

	start_time = mosquitto_time();
	last_update = start_time;

	sys_tree__update(true);

	/* Force published load values to 0 */
	for(int i=0; i<mosq_metric_load_max; i++){
		if(metric_loads[i].interval == METRIC_LOAD_1MIN){
			calc_load(buf, 0.0, 0.0, &metric_loads[i], true);
		}else if(metric_loads[i].interval == METRIC_LOAD_5MIN){
			calc_load(buf, 0.0, 0.0, &metric_loads[i], true);
		}else{
			calc_load(buf, 0.0, 0.0, &metric_loads[i], true);
		}
	}
}


/* [METRIC_ROWS_V1] Every front thread counts into its own row -- one writer per cell, no shared counter on the hot
 * path (the process-wide atomic table was one cache line every front fought over). Each front also writes its own
 * gauges into its row. Front 0 sums the rows when it publishes $SYS; the HTTP API reads what it published. A row is
 * made on a thread's first count and linked once, lock-free; rows are never freed. */
enum { G_STORE_COUNT, G_STORE_BYTES, G_SUBS, G_SHARED_SUBS, G_HEAP, G_HEAP_MAX, G_CLIENTS, G_CONNECTED,
	G_HELD_NOW, G_HELD, G_PAUSED, G_FORCED,     /* [HOLD_FOR_ROOM_V1] */
	G_MAX };
struct metric_row {
	int64_t v[mosq_metric_max];
	int64_t g[G_MAX];
	struct metric_row *next;
};
static struct metric_row *metric_rows = NULL;
static __thread struct metric_row *my_row = NULL;

static struct metric_row *metric_row(void)
{
	struct metric_row *r = my_row, *head;

	if(r){
		return r;
	}
	r = calloc(1, sizeof(*r));          /* not mosquitto_calloc: the row outlives any one front's memory accounting */
	if(!r){
		return NULL;
	}
	head = __atomic_load_n(&metric_rows, __ATOMIC_ACQUIRE);
	do{
		r->next = head;
	}while(!__atomic_compare_exchange_n(&metric_rows, &head, r, true, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE));
	my_row = r;
	return r;
}

void metrics__int_inc(enum mosq_metric_type m, int64_t value)
{
	struct metric_row *r;

	if(m < mosq_metric_max && (r = metric_row())){
		__atomic_store_n(&r->v[m], r->v[m] + value, __ATOMIC_RELAXED);
	}
}


void metrics__int_dec(enum mosq_metric_type m, int64_t value)
{
	struct metric_row *r;

	if(m < mosq_metric_max && (r = metric_row())){
		__atomic_store_n(&r->v[m], r->v[m] - value, __ATOMIC_RELAXED);
	}
}


/* this front's gauges, into its own row: called by every front before it sleeps, and by front 0 before publishing */
void metrics__front_gauges(void)
{
	struct metric_row *r = metric_row();

	if(!r){
		return;
	}
	__atomic_store_n(&r->g[G_STORE_COUNT], (int64_t)db.msg_store_count, __ATOMIC_RELAXED);
	__atomic_store_n(&r->g[G_STORE_BYTES], (int64_t)db.msg_store_bytes, __ATOMIC_RELAXED);
	__atomic_store_n(&r->g[G_SUBS], (int64_t)db.subscription_count, __ATOMIC_RELAXED);
	__atomic_store_n(&r->g[G_SHARED_SUBS], (int64_t)db.shared_subscription_count, __ATOMIC_RELAXED);
#ifdef WITH_MEMORY_TRACKING
	__atomic_store_n(&r->g[G_HEAP], (int64_t)mosquitto_memory_used(), __ATOMIC_RELAXED);
	__atomic_store_n(&r->g[G_HEAP_MAX], (int64_t)mosquitto_max_memory_used(), __ATOMIC_RELAXED);
#endif
	__atomic_store_n(&r->g[G_CLIENTS], (int64_t)HASH_CNT(hh_id, db.contexts_by_id), __ATOMIC_RELAXED);
	__atomic_store_n(&r->g[G_CONNECTED], (int64_t)HASH_CNT(hh_sock, db.contexts_by_sock), __ATOMIC_RELAXED);
	{
		uint64_t held, paused, forced; int now;
		uint64_t xheld, xforced; int64_t xnow;
		sub__own_held_stats(&held, &paused, &forced, &now);
		rgn_hold_stats(&xheld, &xforced, &xnow);           /* and what came from other fronts */
		__atomic_store_n(&r->g[G_HELD_NOW], (int64_t)now + xnow, __ATOMIC_RELAXED);
		__atomic_store_n(&r->g[G_HELD], (int64_t)(held + xheld), __ATOMIC_RELAXED);
		__atomic_store_n(&r->g[G_PAUSED], (int64_t)paused, __ATOMIC_RELAXED);
		__atomic_store_n(&r->g[G_FORCED], (int64_t)(forced + xforced), __ATOMIC_RELAXED);
	}
}
static int64_t held_g[4];   /* [HOLD_FOR_ROOM_V1] the last sums, for $SYS/broker/ribbit/held/... */


/* the sums of every row into metrics[].next */
static void metrics__sum_rows(void)
{
	int64_t g[G_MAX] = {0};

	metrics__front_gauges();
	for(int i=0; i<mosq_metric_max; i++){
		metrics[i].next = 0;
	}
	for(struct metric_row *r = __atomic_load_n(&metric_rows, __ATOMIC_ACQUIRE); r; r = r->next){
		for(int i=0; i<mosq_metric_max; i++){
			metrics[i].next += __atomic_load_n(&r->v[i], __ATOMIC_RELAXED);
		}
		for(int i=0; i<G_MAX; i++){
			g[i] += __atomic_load_n(&r->g[i], __ATOMIC_RELAXED);
		}
	}
	for(int i=0; i<4; i++){
		held_g[i] = g[G_HELD_NOW + i];
	}
	metrics[mosq_gauge_message_store_count].next = g[G_STORE_COUNT];
	metrics[mosq_gauge_message_store_bytes].next = g[G_STORE_BYTES];
	metrics[mosq_gauge_subscriptions].next = g[G_SUBS];
	metrics[mosq_gauge_shared_subscriptions].next = g[G_SHARED_SUBS];
#ifdef WITH_MEMORY_TRACKING
	metrics[mosq_gauge_heap_current].next = g[G_HEAP];
	metrics[mosq_counter_heap_maximum].next = g[G_HEAP_MAX];
#endif
	metrics[mosq_gauge_clients_total].next = g[G_CLIENTS];
	metrics[mosq_counter_clients_maximum].next = g[G_CLIENTS];
	metrics[mosq_gauge_clients_connected].next = g[G_CONNECTED];
	metrics[mosq_gauge_clients_disconnected].next = g[G_CLIENTS] - g[G_CONNECTED];
}


/* Send messages for the $SYS hierarchy if the last update is longer than
 * 'interval' seconds ago.
 * 'interval' is the amount of seconds between updates. If 0, then no periodic
 * messages are sent for the $SYS hierarchy.
 * 'start_time' is the result of time() that the broker was started at.
 */
void sys_tree__update(bool force)
{
	time_t uptime;
	char buf[BUFLEN];
	uint32_t len;
	time_t next_event;
	static __thread time_t last_update_real = 0;

	if(db.config->sys_interval){
		next_event = db.config->sys_interval - db.now_real_s % db.config->sys_interval - 1;
		if(next_event <= 0){
			next_event = db.config->sys_interval;
		}
		loop__update_next_event(next_event*1000);
	}

	if(db.config->sys_interval
			&& ((db.now_real_s % db.config->sys_interval == 0 && last_update_real != db.now_real_s) || force)){

		uptime = db.now_s - start_time;
		len = (uint32_t)snprintf(buf, BUFLEN, "%" PRIu64 " seconds", (uint64_t)uptime);
		db__messages_easy_queue(NULL, "$SYS/broker/uptime", SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);

		/*  Update metrics values where not otherwise updated: [METRIC_ROWS_V1] every front's rows, summed */
		metrics__sum_rows();
		{
			/* [HOLD_FOR_ROOM_V1] deliveries held for a full subscriber queue instead of dropped: held now, held ever,
			 * publishers paused, held delivered anyway at the bound (Mosquitto's own limit then applied) */
			static const char *names[4] = {"$SYS/broker/ribbit/held/now", "$SYS/broker/ribbit/held/total",
				"$SYS/broker/ribbit/held/publishers-paused", "$SYS/broker/ribbit/held/delivered-at-bound"};
			for(int i=0; i<4; i++){
				len = (uint32_t)snprintf(buf, BUFLEN, "%" PRId64, held_g[i]);
				db__messages_easy_queue(NULL, names[i], SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);
			}
		}
		{
			long rc = rgn_retained_count();   /* [RETAIN_IN_THE_REGION_V1] the region's, derived */
			if(rc >= 0){
				metrics[mosq_gauge_retained_messages].next = rc;
			}
		}

		/* Handle loads first, because they reference other metrics and need next != current */
		if(db.now_s > last_update){
			double i_mult = 60.0/(double)(db.now_s-last_update);

			double exponent_1min = exp(-1.0*(double)(db.now_s-last_update)/60.0);
			double exponent_5min = exp(-1.0*(double)(db.now_s-last_update)/300.0);
			double exponent_15min = exp(-1.0*(double)(db.now_s-last_update)/900.0);

			for(int i=0; i<mosq_metric_load_max; i++){
				if(metric_loads[i].interval == METRIC_LOAD_1MIN){
					calc_load(buf, exponent_1min, i_mult, &metric_loads[i], false);
				}else if(metric_loads[i].interval == METRIC_LOAD_5MIN){
					calc_load(buf, exponent_5min, i_mult, &metric_loads[i], false);
				}else{
					calc_load(buf, exponent_15min, i_mult, &metric_loads[i], false);
				}
			}
		}

		for(int i=0; i<mosq_metric_max; i++){
			if((metrics[i].is_max && metrics[i].next > metrics[i].current) ||
					(!metrics[i].is_max && metrics[i].next != metrics[i].current)){

				metrics[i].current = metrics[i].next;
				len = (uint32_t)snprintf(buf, BUFLEN, "%lu", metrics[i].current);
				if(metrics[i].topic){
					db__messages_easy_queue(NULL, metrics[i].topic, SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);
				}
				if(metrics[i].topic_alias){
					db__messages_easy_queue(NULL, metrics[i].topic_alias, SYS_TREE_QOS, len, buf, 1, MSG_EXPIRY_INFINITE, NULL);
				}
			}
		}

		last_update = db.now_s;
		last_update_real = db.now_real_s;
	}
}

#endif
