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
   Fawcett Innovations - retained messages moved into the MQTT region.
*/

/* [RETAIN_IN_THE_REGION_V1] A retained message is one cell per topic in the MQTT region: a publish with the retain
 * flag writes it (the region does this after the live fan-out, as Mosquitto did), an empty payload withdraws it.
 * A new subscription asks the region for what matches, in Mosquitto's retain__search order, and this front does what
 * retain__process did with each: expiry, the read ACL, the original publisher's write ACL, QoS, packet id, insert.
 * Expiry is the reader's arithmetic: an expired retained message is simply not delivered. */

#include "config.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "mosquitto_broker_internal.h"
#include "mosquitto/mqtt_protocol.h"
#include "region_client.h"
#include "util_mosq.h"

#include "utlist.h"

int sub__props_encode(const mosquitto_property *props, uint8_t **out, uint32_t *outlen);
struct mosquitto__base_msg *sub__msg_from_region(const struct rgn_msg *m);


int retain__init(void)
{
	return MOSQ_ERR_SUCCESS;
}


/* [RETAIN_PERSIST_VIEW_V1] A persistence plugin (persist-sqlite) is told about retained messages by store id and keeps
 * the message row while it is retained. Mosquitto's retained tree held a reference to the message, so the row stayed;
 * here the region holds the message, so a front hosting a persistence plugin keeps its own reference to each retained
 * message it published, by topic, for the plugin's sake -- and fires the plugin's events where retain__store did. */
struct retain_held {
	UT_hash_handle hh;
	struct mosquitto__base_msg *msg;
	char topic[];
};
static __thread struct retain_held *held = NULL;

static bool retain__plugin_persisting(void)
{
	return db.config->security_options.plugin_callbacks.persist_retain_msg_set != NULL
		|| db.config->security_options.plugin_callbacks.persist_retain_msg_delete != NULL;
}


void retain__persist_view(const char *topic, struct mosquitto__base_msg *base_msg, bool had_old, bool persist)
{
	struct retain_held *h = NULL;
	size_t tlen;

	if(!retain__plugin_persisting()){
		return;
	}
	tlen = strlen(topic);
	HASH_FIND(hh, held, topic, tlen, h);
	if(h && h->msg == base_msg){
		return;
	}
	if(h || had_old){
		if(persist && topic[0] != '$' && base_msg->data.payloadlen == 0){
			/* Only delete if another retained message isn't replacing this one */
			plugin_persist__handle_retain_msg_delete(h ? h->msg : base_msg);
		}
		if(h){
			HASH_DELETE(hh, held, h);
			db__msg_store_ref_dec(&h->msg);
			mosquitto_FREE(h);
		}
	}
	if(base_msg->data.payloadlen){
		h = mosquitto_calloc(1, sizeof(struct retain_held) + tlen + 1);
		if(!h){
			return;
		}
		memcpy(h->topic, topic, tlen + 1);
		h->msg = base_msg;
		db__msg_store_ref_inc(base_msg);
		HASH_ADD(hh, held, topic, tlen, h);
		if(persist && topic[0] != '$'){
			plugin_persist__handle_base_msg_add(base_msg);
			plugin_persist__handle_retain_msg_set(base_msg);
		}
	}
}


static int retain__region_set(const char *topic, struct mosquitto__base_msg *base_msg)
{
	bool had = false;

	struct rgn_msg m;
	uint8_t *props = NULL;
	uint32_t propslen = 0;
	int rc;

	if(sub__props_encode(base_msg->data.properties, &props, &propslen)){
		return MOSQ_ERR_NOMEM;
	}
	memset(&m, 0, sizeof(m));
	m.topic = topic;
	m.payload = base_msg->data.payload;
	m.payloadlen = base_msg->data.payloadlen;
	m.props = props;
	m.propslen = propslen;
	m.qos = base_msg->data.qos;
	m.retain = 1;
	m.origin = base_msg->origin == mosq_mo_broker ? 1 : 0;
	m.expiry_ms = (int64_t)base_msg->data.expiry_time * 1000;
	m.src_id = base_msg->data.source_id ? base_msg->data.source_id : "";
	m.src_user = base_msg->data.source_username;
	m.src_port = base_msg->source_listener ? base_msg->source_listener->port : 0;
	rc = rgn_retain(&m, &had);
	mosquitto_FREE(props);
	if(rc){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region retain on %s failed: %s", topic, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	retain__persist_view(topic, base_msg, had, false);
	return MOSQ_ERR_SUCCESS;
}


BROKER_EXPORT int mosquitto_persist_retain_msg_set(const char *topic, uint64_t base_msg_id)
{
	struct mosquitto__base_msg *base_msg;

	if(topic == NULL){
		return MOSQ_ERR_INVAL;
	}
	HASH_FIND(hh, db.msg_store, &base_msg_id, sizeof(base_msg_id), base_msg);
	if(base_msg){
		return retain__region_set(topic, base_msg);
	}
	return MOSQ_ERR_UNKNOWN;
}


BROKER_EXPORT int mosquitto_persist_retain_msg_delete(const char *topic)
{
	struct mosquitto__base_msg base_msg;

	if(topic == NULL){
		return MOSQ_ERR_INVAL;
	}
	memset(&base_msg, 0, sizeof(base_msg));
	/* With payloadlen == 0, this means the message will be removed */
	return retain__region_set(topic, &base_msg);
}


/* Restores (persist_read.c) still call this with a tokenised topic; the region tokenises for itself. */
int retain__store(const char *topic, struct mosquitto__base_msg *base_msg, char **split_topics, bool persist)
{
	UNUSED(split_topics);
	UNUSED(persist);
	return retain__region_set(topic, base_msg);
}


static int retain__process(struct mosquitto__base_msg *retained, struct mosquitto *context, const struct mosquitto_subscription *sub)
{
	int rc = 0;
	uint8_t qos, sub_qos;
	uint16_t mid;

	if(retained->data.expiry_time > 0 && db.now_real_s >= retained->data.expiry_time){
		return MOSQ_ERR_SUCCESS;
	}

	rc = mosquitto_acl_check(context, retained->data.topic, retained->data.payloadlen, retained->data.payload,
			retained->data.qos, retained->data.retain, retained->data.properties, MOSQ_ACL_READ);
	if(rc == MOSQ_ERR_ACL_DENIED){
		return MOSQ_ERR_SUCCESS;
	}else if(rc != MOSQ_ERR_SUCCESS){
		return rc;
	}

	/* Check for original source access */
	if(db.config->check_retain_source && retained->origin != mosq_mo_broker && retained->data.source_id){
		struct mosquitto retain_ctxt;
		memset(&retain_ctxt, 0, sizeof(struct mosquitto));

		retain_ctxt.id = retained->data.source_id;
		retain_ctxt.username = retained->data.source_username;
		retain_ctxt.listener = retained->source_listener;

		rc = mosquitto_acl_check(&retain_ctxt, retained->data.topic, retained->data.payloadlen, retained->data.payload,
				retained->data.qos, retained->data.retain, retained->data.properties, MOSQ_ACL_WRITE);
		if(rc == MOSQ_ERR_ACL_DENIED){
			return MOSQ_ERR_SUCCESS;
		}else if(rc != MOSQ_ERR_SUCCESS){
			return rc;
		}
	}

	sub_qos = sub->options & 0x03;
	if(db.config->upgrade_outgoing_qos){
		qos = sub_qos;
	}else{
		qos = retained->data.qos;
		if(qos > sub_qos){
			qos = sub_qos;
		}
	}
	if(qos > 0){
		mid = mosquitto__mid_generate(context);
	}else{
		mid = 0;
	}
	return db__message_insert_outgoing(context, 0, mid, qos, true, retained, sub->identifier, false, true);
}


int retain__queue(struct mosquitto *context, const struct mosquitto_subscription *sub)
{
	const struct rgn_msg *msgs = NULL;
	int n = 0;

	assert(context);
	assert(sub);

	if(!strncmp(sub->topic_filter, "$share/", strlen("$share/"))){
		return MOSQ_ERR_SUCCESS;
	}
	if(rgn_retained(sub->topic_filter, &msgs, &n)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region retained lookup for %s failed: %s", sub->topic_filter, rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	for(int i=0; i<n; i++){
		struct mosquitto__base_msg *b = sub__msg_from_region(&msgs[i]);
		if(!b){
			return MOSQ_ERR_NOMEM;
		}
		db__msg_store_ref_inc(b);
		retain__process(b, context, sub);
		db__msg_store_ref_dec(&b);
	}
	return MOSQ_ERR_SUCCESS;
}


void retain__expire(struct mosquitto__retainhier **retainhier)
{
	UNUSED(retainhier);
}


void retain__clean(struct mosquitto__retainhier **retainhier)
{
	struct retain_held *h, *tmp;

	UNUSED(retainhier);
	HASH_ITER(hh, held, h, tmp){
		HASH_DELETE(hh, held, h);
		db__msg_store_ref_dec(&h->msg);
		mosquitto_FREE(h);
	}
}


void retain__expiry_check(void)
{
}
