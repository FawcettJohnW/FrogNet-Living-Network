/*
Copyright (c) 2010-2021 Roger Light <roger@atchoo.org>

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

#include "config.h"

#ifdef WITH_PERSISTENCE

#ifndef WIN32
#include <arpa/inet.h>
#endif
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "mosquitto_broker_internal.h"
#include "region_client.h"
#include "persist.h"
#include "util_mosq.h"


static int persist__client_messages_save(FILE *db_fptr, struct mosquitto *context, struct mosquitto__client_msg *queue)
{
	struct P_client_msg chunk;
	struct mosquitto__client_msg *cmsg;
	int rc;

	assert(db_fptr);
	assert(context);

	cmsg = queue;
	while(cmsg){
		if(!strncmp(cmsg->base_msg->data.topic, "$SYS", 4)
				&& cmsg->base_msg->ref_count <= 1
				&& cmsg->base_msg->dest_id_count == 0){

			/* This $SYS message won't have been persisted, so we can't persist
			 * this client message. */
			cmsg = cmsg->next;
			continue;
		}

		memset(&chunk, 0, sizeof(struct P_client_msg));

		chunk.F.store_id = cmsg->base_msg->data.store_id;
		chunk.F.mid = cmsg->data.mid;
		chunk.F.id_len = (uint16_t)strlen(context->id);
		chunk.F.qos = cmsg->data.qos;
		chunk.F.retain_dup = (uint8_t)((cmsg->data.retain&0x0F)<<4 | (cmsg->data.dup&0x0F));
		chunk.F.direction = (uint8_t)cmsg->data.direction;
		chunk.F.state = (uint8_t)cmsg->data.state;
		chunk.clientid = context->id;
		chunk.subscription_identifier = cmsg->data.subscription_identifier;

		rc = persist__chunk_client_msg_write_v6(db_fptr, &chunk);
		if(rc){
			return rc;
		}

		cmsg = cmsg->next;
	}

	return MOSQ_ERR_SUCCESS;
}


static int persist__message_store_save(FILE *db_fptr)
{
	struct P_base_msg chunk;
	struct mosquitto__base_msg *base_msg, *base_msg_tmp;
	int rc;

	assert(db_fptr);

	base_msg = db.msg_store;
	HASH_ITER(hh, db.msg_store, base_msg, base_msg_tmp){
		if(base_msg->ref_count < 1 || base_msg->data.topic == NULL){
			continue;
		}

		memset(&chunk, 0, sizeof(struct P_base_msg));

		if(!strncmp(base_msg->data.topic, "$SYS", 4)){
			if(base_msg->ref_count <= 1 && base_msg->dest_id_count == 0){
				/* $SYS messages that are only retained shouldn't be persisted. */
				continue;
			}
			/* Don't save $SYS messages as retained otherwise they can give
			 * misleading information when reloaded. They should still be saved
			 * because a disconnected durable client may have them in their
			 * queue. */
			chunk.F.retain = 0;
		}else{
			chunk.F.retain = (uint8_t)base_msg->data.retain;
		}

		chunk.F.store_id = base_msg->data.store_id;
		chunk.F.expiry_time = base_msg->data.expiry_time;
		chunk.F.payloadlen = base_msg->data.payloadlen;
		chunk.F.source_mid = base_msg->data.source_mid;
		if(base_msg->data.source_id){
			chunk.F.source_id_len = (uint16_t)strlen(base_msg->data.source_id);
			chunk.source.id = base_msg->data.source_id;
		}else{
			chunk.F.source_id_len = 0;
			chunk.source.id = NULL;
		}
		if(base_msg->data.source_username){
			chunk.F.source_username_len = (uint16_t)strlen(base_msg->data.source_username);
			chunk.source.username = base_msg->data.source_username;
		}else{
			chunk.F.source_username_len = 0;
			chunk.source.username = NULL;
		}

		chunk.F.topic_len = (uint16_t)strlen(base_msg->data.topic);
		chunk.topic = base_msg->data.topic;

		if(base_msg->source_listener){
			chunk.F.source_port = base_msg->source_listener->port;
		}else{
			chunk.F.source_port = 0;
		}
		chunk.F.qos = base_msg->data.qos;
		chunk.payload = base_msg->data.payload;
		chunk.properties = base_msg->data.properties;

		rc = persist__chunk_message_store_write_v6(db_fptr, &chunk);
		if(rc){
			return rc;
		}
	}

	return MOSQ_ERR_SUCCESS;
}


static int persist__client_save(FILE *db_fptr)
{
	struct mosquitto *context, *ctxt_tmp;
	struct P_client chunk;
	int rc;

	assert(db_fptr);

	HASH_ITER(hh_id, db.contexts_by_id, context, ctxt_tmp){
		memset(&chunk, 0, sizeof(struct P_client));

		if(context &&
				context->session_expiry_interval != MQTT_SESSION_EXPIRY_IMMEDIATE &&
#ifdef WITH_BRIDGE
				((!context->bridge && context->clean_start == false)
				|| (context->bridge && context->bridge->clean_start_local == false))
#else
				context->clean_start == false
#endif
				){
			chunk.F.session_expiry_time = context->session_expiry_time;
			if(context->session_expiry_interval != MQTT_SESSION_EXPIRY_NEVER
					&& context->session_expiry_time == 0){

				chunk.F.session_expiry_time = context->session_expiry_interval + db.now_real_s;
			}else{
				chunk.F.session_expiry_time = context->session_expiry_time;
			}
			chunk.F.session_expiry_interval = context->session_expiry_interval;
			chunk.F.last_mid = context->last_mid;
			chunk.F.id_len = (uint16_t)strlen(context->id);
			chunk.clientid = context->id;
			if(context->username){
				chunk.F.username_len = (uint16_t)strlen(context->username);
				chunk.username = context->username;
			}
			if(context->listener){
				chunk.F.listener_port = context->listener->port;
			}

			if(chunk.F.id_len == 0){
				/* This should never happen, but in case we have a client with
				 * zero length ID, don't persist them. */
				continue;
			}

			rc = persist__chunk_client_write_v6(db_fptr, &chunk);
			if(rc){
				return rc;
			}

			if(persist__client_messages_save(db_fptr, context, context->msgs_in.inflight)){
				return 1;
			}
			if(persist__client_messages_save(db_fptr, context, context->msgs_in.queued)){
				return 1;
			}
			if(persist__client_messages_save(db_fptr, context, context->msgs_out.inflight)){
				return 1;
			}
			if(persist__client_messages_save(db_fptr, context, context->msgs_out.queued)){
				return 1;
			}
		}
	}

	return MOSQ_ERR_SUCCESS;
}


/* [SUBS_IN_THE_REGION_V1] Subscriptions: each durable client's own list (the mirror of its cells in the region),
 * written with the same conditions Mosquitto applied to the tree's leaves. */
static int persist__subs_save_all(FILE *db_fptr)
{
	struct mosquitto *context, *ctxt_tmp;
	struct P_sub sub_chunk;
	int rc;

	HASH_ITER(hh_id, db.contexts_by_id, context, ctxt_tmp){
		if(context->session_expiry_interval == MQTT_SESSION_EXPIRY_IMMEDIATE
				|| context->clean_start == true
				|| !context->id){
			continue;
		}
		for(int i=0; i<context->subs_capacity; i++){
			struct mosquitto__subleaf *sub = context->subs[i];
			if(!sub){
				continue;
			}
			memset(&sub_chunk, 0, sizeof(struct P_sub));
			sub_chunk.F.identifier = sub->identifier;
			sub_chunk.F.id_len = (uint16_t)strlen(context->id);
			sub_chunk.F.topic_len = (uint16_t)strlen(sub->topic_filter);
			sub_chunk.F.qos = MQTT_SUB_OPT_GET_QOS(sub->subscription_options);
			sub_chunk.F.options = sub->subscription_options & 0xFC;
			sub_chunk.clientid = context->id;
			sub_chunk.topic = sub->topic_filter;
			rc = persist__chunk_sub_write_v6(db_fptr, &sub_chunk);
			if(rc){
				return rc;
			}
		}
	}
	return MOSQ_ERR_SUCCESS;
}


/* [RETAIN_IN_THE_REGION_V1] Retained messages: what the region holds, brought into this front's store for the
 * duration of the write (persist__retained_fetch), then one chunk each, $SYS excepted as Mosquitto did. */
struct mosquitto__base_msg *sub__msg_from_region(const struct rgn_msg *m);
static __thread struct mosquitto__base_msg **retained_now = NULL;
static __thread int retained_now_n = 0;

static int persist__retained_fetch(void)
{
	const struct rgn_msg *msgs = NULL;
	int n = 0;

	if(rgn_retained_all(&msgs, &n)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region retained dump failed: %s", rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	retained_now = mosquitto_calloc((size_t)(n ? n : 1), sizeof(struct mosquitto__base_msg *));
	if(!retained_now){
		return MOSQ_ERR_NOMEM;
	}
	for(int i=0; i<n; i++){
		retained_now[i] = sub__msg_from_region(&msgs[i]);
		if(!retained_now[i]){
			return MOSQ_ERR_NOMEM;
		}
		db__msg_store_ref_inc(retained_now[i]);
		retained_now_n++;
	}
	return MOSQ_ERR_SUCCESS;
}


static void persist__retained_release(void)
{
	for(int i=0; i<retained_now_n; i++){
		db__msg_store_ref_dec(&retained_now[i]);
	}
	mosquitto_FREE(retained_now);
	retained_now_n = 0;
}


static int persist__retain_save_all(FILE *db_fptr)
{
	struct P_retain retain_chunk;
	int rc;

	for(int i=0; i<retained_now_n; i++){
		if(retained_now[i] && strncmp(retained_now[i]->data.topic, "$SYS", 4)){
			memset(&retain_chunk, 0, sizeof(struct P_retain));
			retain_chunk.F.store_id = retained_now[i]->data.store_id;
			rc = persist__chunk_retain_write_v6(db_fptr, &retain_chunk);
			if(rc){
				return rc;
			}
		}
	}
	return MOSQ_ERR_SUCCESS;
}

static int persist__write_data(FILE *db_fptr, void *user_data);


static void persist__log_write_error(const char *msg)
{
	log__printf(NULL, MOSQ_LOG_ERR, "Error saving in-memory database, %s", msg);
}


int persist__backup(bool shutdown)
{
	if(db.config == NULL){
		return MOSQ_ERR_INVAL;
	}
	if(db.config->persistence == false){
		return MOSQ_ERR_SUCCESS;
	}
	if(db.config->persistence_filepath == NULL){
		return MOSQ_ERR_INVAL;
	}

	log__printf(NULL, MOSQ_LOG_INFO, "Saving in-memory database to %s.", db.config->persistence_filepath);

	return mosquitto_write_file(db.config->persistence_filepath, true, &persist__write_data, &shutdown, &persist__log_write_error);
}


static int persist__write_data(FILE *db_fptr, void *user_data)
{
	bool shutdown = *(bool *)(user_data);
	uint32_t db_version_w = htonl(MOSQ_DB_VERSION);
	uint32_t crc = 0;
	const char *err;
	struct PF_cfg cfg_chunk;
	int rc = MOSQ_ERR_UNKNOWN;

	/* Header */
	write_e(db_fptr, magic, 15);
	write_e(db_fptr, &crc, sizeof(uint32_t));
	write_e(db_fptr, &db_version_w, sizeof(uint32_t));

	memset(&cfg_chunk, 0, sizeof(struct PF_cfg));
	cfg_chunk.last_db_id = db.last_db_id;
	cfg_chunk.shutdown = shutdown;
	cfg_chunk.dbid_size = sizeof(dbid_t);
	if(persist__chunk_cfg_write_v6(db_fptr, &cfg_chunk)){
		goto error;
	}

	if(persist__retained_fetch()){
		persist__retained_release();
		goto error;
	}
	if(persist__message_store_save(db_fptr)){
		persist__retained_release();
		goto error;
	}

	if(persist__client_save(db_fptr)
			|| persist__subs_save_all(db_fptr)
			|| persist__retain_save_all(db_fptr)){
		persist__retained_release();
		goto error;
	}
	persist__retained_release();
	return MOSQ_ERR_SUCCESS;

error:
	err = strerror(errno);
	log__printf(NULL, MOSQ_LOG_ERR, "Error during saving in-memory database %s: %s.", db.config->persistence_filepath, err);
	if(db_fptr){
		fclose(db_fptr);
	}
	return rc;
}


#endif
