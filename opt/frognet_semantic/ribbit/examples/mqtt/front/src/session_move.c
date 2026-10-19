/*
Copyright (C) 2016-2026 Fawcett Innovations LLC
SPDX-License-Identifier: GPL-2.0-only

session_move.c -- [SESSION_HANDOVER_V1] a client's session moving to the front it reconnects on.

A session lives on the front that last held its client: Mosquitto's context there (online, or offline with its
session expiry and will delay running) is the session, and that front is the one participant responsible for it.
When the client connects to another front, the connecting front claims the client in the region (one exchange of the
client's owner word: the newer claim wins). If the claim shows another front held it, the connecting front writes
that it wants the session and parks the CONNECT exactly where Mosquitto parks a delayed authentication. The holding
front is woken, does what Mosquitto's takeover did to the old context (its will, its DISCONNECT 0x8E), writes the
session whole -- subscriptions list, packet ids, every message in flight or queued in both directions -- and lets it
go. The connecting front takes the session up as if it had found the old context in its own table, and Mosquitto's
own takeover code runs unchanged on it. Deliveries that reached the old front after the claim go on to the new one;
deliveries that reach the new one while it waits are kept and given once the session is up.

Nothing waits on anything: the connect is parked, both fronts keep serving their other clients.
*/

#include "config.h"

#include <stdio.h>
#include <string.h>
#include <utlist.h>

#include "mosquitto_broker_internal.h"
#include "mosquitto/mqtt_protocol.h"
#include "region_client.h"
#include "send_mosq.h"
#include "util_mosq.h"
#include "will_mosq.h"

int sub__props_encode(const mosquitto_property *props, uint8_t **out, uint32_t *outlen);
struct mosquitto__base_msg *sub__msg_from_region(const struct rgn_msg *m);
int sub__mirror_add(struct mosquitto *context, const char *filter, uint8_t options, uint32_t identifier);
int connect__on_authorised(struct mosquitto *context, void *auth_data_out, uint16_t auth_data_out_len);

/* ---- the session written whole ------------------------------------------------------------------------------------ */

struct sbuf { uint8_t *p; size_t n, cap; bool err; };

static void put(struct sbuf *b, const void *d, size_t len)
{
	if(b->err) return;
	if(b->n + len > b->cap){
		size_t cap = b->cap ? b->cap : 256;
		while(cap < b->n + len) cap *= 2;
		uint8_t *np = mosquitto_realloc(b->p, cap);
		if(!np){ b->err = true; return; }
		b->p = np; b->cap = cap;
	}
	memcpy(b->p + b->n, d, len);
	b->n += len;
}
static void put8(struct sbuf *b, uint8_t v){ put(b, &v, 1); }
static void put16(struct sbuf *b, uint16_t v){ put(b, &v, 2); }
static void put32(struct sbuf *b, uint32_t v){ put(b, &v, 4); }
static void put64(struct sbuf *b, uint64_t v){ put(b, &v, 8); }
static void putbytes(struct sbuf *b, const void *d, uint32_t len){ put32(b, len); if(len) put(b, d, len); }
static void putstr(struct sbuf *b, const char *s){ if(s){ put8(b, 1); putbytes(b, s, (uint32_t)strlen(s)); }else{ put8(b, 0); } }

struct rbuf { const uint8_t *p; size_t n, pos; bool err; };
static void get(struct rbuf *b, void *d, size_t len){ if(b->err || b->pos + len > b->n){ b->err = true; memset(d, 0, len); return; } memcpy(d, b->p + b->pos, len); b->pos += len; }
static uint8_t get8(struct rbuf *b){ uint8_t v; get(b, &v, 1); return v; }
static uint16_t get16(struct rbuf *b){ uint16_t v; get(b, &v, 2); return v; }
static uint32_t get32(struct rbuf *b){ uint32_t v; get(b, &v, 4); return v; }
static uint64_t get64(struct rbuf *b){ uint64_t v; get(b, &v, 8); return v; }
static const uint8_t *getbytes(struct rbuf *b, uint32_t *len)
{
	const uint8_t *p;
	*len = get32(b);
	if(b->err || b->pos + *len > b->n){ b->err = true; *len = 0; return NULL; }
	p = b->p + b->pos;
	b->pos += *len;
	return p;
}
/* a nullable string, copied */
static char *getstr(struct rbuf *b)
{
	uint32_t len;
	const uint8_t *p;
	char *s;
	if(!get8(b)) return NULL;
	p = getbytes(b, &len);
	if(b->err) return NULL;
	s = mosquitto_malloc(len + 1);
	if(!s){ b->err = true; return NULL; }
	if(len) memcpy(s, p, len);
	s[len] = 0;
	return s;
}

static const char MAGIC[4] = {'M', 'Q', 'S', '1'};

static void put_list(struct sbuf *b, struct mosquitto__client_msg *head)
{
	struct mosquitto__client_msg *m;
	uint32_t n = 0;
	DL_FOREACH(head, m){ n++; }
	put32(b, n);
	DL_FOREACH(head, m){
		struct mosquitto__base_msg *s = m->base_msg;
		uint8_t *props = NULL; uint32_t propslen = 0;
		put64(b, m->data.cmsg_id); put16(b, m->data.mid); put8(b, m->data.qos); put8(b, m->data.retain);
		put8(b, m->data.dup); put8(b, m->data.state); put8(b, m->data.direction); put32(b, m->data.subscription_identifier);
		putstr(b, s->data.topic);
		putbytes(b, s->data.payload, s->data.payloadlen);
		if(sub__props_encode(s->data.properties, &props, &propslen)){ b->err = true; return; }
		putbytes(b, props, propslen);
		mosquitto_FREE(props);
		put8(b, s->data.qos); put8(b, s->data.retain); put64(b, (uint64_t)s->data.expiry_time);
		putstr(b, s->data.source_id); putstr(b, s->data.source_username);
		put16(b, s->source_listener ? s->source_listener->port : 0);
		put8(b, s->origin == mosq_mo_broker ? 1 : 0); put16(b, s->data.source_mid);
	}
}

static int session__write(struct mosquitto *X, struct sbuf *b)
{
	uint32_t nsubs = 0;

	put(b, MAGIC, 4);
	put8(b, (uint8_t)X->protocol); put8(b, X->clean_start ? 1 : 0); put32(b, X->session_expiry_interval);
	put16(b, X->last_mid); put64(b, X->last_cmsg_id);
	for(int i=0; i<X->subs_capacity; i++){ if(X->subs[i]) nsubs++; }
	put32(b, nsubs);
	for(int i=0; i<X->subs_capacity; i++){
		if(!X->subs[i]) continue;
		putstr(b, X->subs[i]->topic_filter); put8(b, X->subs[i]->subscription_options); put32(b, X->subs[i]->identifier);
	}
	put_list(b, X->msgs_in.inflight); put_list(b, X->msgs_in.queued);
	put_list(b, X->msgs_out.inflight); put_list(b, X->msgs_out.queued);
	return b->err ? MOSQ_ERR_NOMEM : MOSQ_ERR_SUCCESS;
}

static int get_list(struct rbuf *b, struct mosquitto *g, struct mosquitto_msg_data *md, bool inflight)
{
	uint32_t n = get32(b);
	for(uint32_t i=0; i<n && !b->err; i++){
		struct rgn_msg m;
		struct mosquitto__client_msg *cm;
		uint32_t len;
		char *topic, *src_id, *src_user;
		memset(&m, 0, sizeof(m));
		cm = mosquitto_calloc(1, sizeof(struct mosquitto__client_msg));
		if(!cm) return MOSQ_ERR_NOMEM;
		cm->data.cmsg_id = get64(b); cm->data.mid = get16(b); cm->data.qos = get8(b); cm->data.retain = get8(b);
		cm->data.dup = get8(b); cm->data.state = get8(b); cm->data.direction = get8(b); cm->data.subscription_identifier = get32(b);
		topic = getstr(b);
		m.topic = topic ? topic : "";
		m.payload = getbytes(b, &len); m.payloadlen = len;
		m.props = getbytes(b, &len); m.propslen = len;
		m.qos = get8(b); m.retain = get8(b); m.expiry_ms = (int64_t)get64(b) * 1000;
		src_id = getstr(b); src_user = getstr(b);
		m.src_id = src_id ? src_id : ""; m.src_user = src_user;
		m.src_port = get16(b); m.origin = get8(b);
		uint16_t source_mid = get16(b);
		if(b->err){
			mosquitto_FREE(topic); mosquitto_FREE(src_id); mosquitto_FREE(src_user); mosquitto_FREE(cm);
			return MOSQ_ERR_MALFORMED_PACKET;
		}
		cm->base_msg = sub__msg_from_region(&m);
		mosquitto_FREE(topic); mosquitto_FREE(src_id); mosquitto_FREE(src_user);
		if(!cm->base_msg){ mosquitto_FREE(cm); return MOSQ_ERR_NOMEM; }
		cm->base_msg->data.source_mid = source_mid;
		db__msg_store_ref_inc(cm->base_msg);
		if(inflight){
			DL_APPEND(md->inflight, cm);
			db__msg_add_to_inflight_stats(md, cm);
		}else{
			DL_APPEND(md->queued, cm);
			db__msg_add_to_queued_stats(md, cm);
		}
		UNUSED(g);
	}
	return b->err ? MOSQ_ERR_MALFORMED_PACKET : MOSQ_ERR_SUCCESS;
}

/* The handed-over session as a context Mosquitto's takeover code can find: no socket, in no table. */
static struct mosquitto *session__read(const char *client, const void *blob, uint32_t len)
{
	struct rbuf b = {blob, len, 0, false};
	struct mosquitto *g;
	char magic[4];
	uint32_t nsubs;

	get(&b, magic, 4);
	if(b.err || memcmp(magic, MAGIC, 4)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: the session handed over for %s is not one this front can read.", client);
		return NULL;
	}
	g = context__init();
	if(!g) return NULL;
	g->region_is_ghost = true;
	g->id = mosquitto_strdup(client);
	g->protocol = get8(&b); g->clean_start = get8(&b) != 0; g->session_expiry_interval = get32(&b);
	g->last_mid = get16(&b); g->last_cmsg_id = get64(&b);
	nsubs = get32(&b);
	for(uint32_t i=0; i<nsubs && !b.err; i++){
		char *filter = getstr(&b);
		uint8_t options = get8(&b);
		uint32_t identifier = get32(&b);
		if(filter) sub__mirror_add(g, filter, options, identifier);
		mosquitto_FREE(filter);
	}
	if(get_list(&b, g, &g->msgs_in, true) || get_list(&b, g, &g->msgs_in, false)
			|| get_list(&b, g, &g->msgs_out, true) || get_list(&b, g, &g->msgs_out, false)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: the session handed over for %s is damaged.", client);
		context__cleanup(g, true);
		return NULL;
	}
	return g;
}


/* ---- the front that held the session -------------------------------------------------------------------------------- */

void session__wanted(const char *client, int to, bool clean_start)
{
	struct mosquitto *X;
	struct sbuf b = {NULL, 0, 0, false};

	HASH_FIND(hh_id, db.contexts_by_id, client, strlen(client), X);
	if(!X){
		rgn_give_session(client, to, NULL, 0);
		return;
	}
	/* Mosquitto's takeover, the old context's half (handle_connect.c connect__on_authorised) */
	if((X->protocol == mosq_p_mqtt5 && X->session_expiry_interval == MQTT_SESSION_EXPIRY_IMMEDIATE)
			|| (X->protocol != mosq_p_mqtt5 && X->clean_start == true)
			|| clean_start){
		context__send_will(X);
	}
	if(session__write(X, &b)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: out of memory writing the session of %s.", client);
		mosquitto_FREE(b.p);
		rgn_give_session(client, to, NULL, 0);
		return;
	}
	/* the session leaves with the blob: its subscriptions list and its messages are not this front's any more (the
	 * subscriptions themselves are the region's and stay where they are) */
	for(int i=0; i<X->subs_capacity; i++){
		mosquitto_FREE(X->subs[i]);
	}
	mosquitto_FREE(X->subs);
	X->subs_capacity = 0;
	X->subs_count = 0;
	db__messages_delete(X, true);

	session_expiry__remove(X);
	will_delay__remove(X);
	will__clear(X);
	X->clean_start = true;
	X->session_expiry_interval = MQTT_SESSION_EXPIRY_IMMEDIATE;
	mosquitto__set_state(X, mosq_cs_duplicate);
	if(X->protocol == mosq_p_mqtt5 && net__is_connected(X)){
		send__disconnect(X, MQTT_RC_SESSION_TAKEN_OVER, NULL);
	}
	do_disconnect(X, MOSQ_ERR_SESSION_TAKEN_OVER);

	if(rgn_give_session(client, to, b.p, (uint32_t)b.n)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: handing the session of %s on failed: %s", client, rgn_error());
	}
	mosquitto_FREE(b.p);
}


/* ---- the front the client connected to ------------------------------------------------------------------------------ */

struct region_stash {
	struct region_stash *next;
	struct mosquitto__base_msg *msg;
	uint8_t qos, retain;
	uint32_t sid;
};

/* True if the CONNECT is parked waiting for the session: the caller has handled it. */
bool session__claim_or_wait(struct mosquitto *context, void *auth_data_out, uint16_t auth_data_out_len)
{
	int prev = -1;
	int proto = context->protocol == mosq_p_mqtt5 ? 5 : (context->protocol == mosq_p_mqtt311 ? 4 : 3);

	if(context->region_ghost || context->region_claimed){
		return false;               /* claimed already (the session is here, or arrived): carry on */
	}
	if(rgn_claim(context->id, proto, &prev)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: cannot claim client %s: %s", context->id, rgn_error());
		return false;
	}
	context->region_claimed = true;
	if(prev < 0 || prev == rgn_front()){
		return false;
	}
	context->region_awaiting = true;
	context->region_auth_data = auth_data_out;
	context->region_auth_data_len = auth_data_out_len;
	mosquitto__set_state(context, mosq_cs_delayed_auth);
	HASH_ADD_KEYPTR(hh_id, db.contexts_by_id_delayed_auth, context->id, strlen(context->id), context);
	if(rgn_want_session(context->id, prev, context->clean_start)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: cannot ask front %d for the session of %s: %s", prev, context->id, rgn_error());
	}
	return true;
}


/* A delivery for a client whose CONNECT is parked: kept until the session is up. False: not parked here. */
bool session__stash(const char *client, struct mosquitto__base_msg *msg, uint8_t qos, uint8_t retain, uint32_t sid)
{
	struct mosquitto *ctx;
	struct region_stash *s, **tail;

	HASH_FIND(hh_id, db.contexts_by_id_delayed_auth, client, strlen(client), ctx);
	if(!ctx || !ctx->region_awaiting){
		return false;
	}
	s = mosquitto_calloc(1, sizeof(struct region_stash));
	if(!s){
		return true;
	}
	s->msg = msg; s->qos = qos; s->retain = retain; s->sid = sid;
	db__msg_store_ref_inc(msg);
	for(tail = &ctx->region_stash; *tail; tail = &(*tail)->next){}
	*tail = s;
	return true;
}


void session__given(const char *client, const void *blob, uint32_t len)
{
	struct mosquitto *ctx;
	struct region_stash *s;
	int rc;

	HASH_FIND(hh_id, db.contexts_by_id_delayed_auth, client, strlen(client), ctx);
	if(!ctx || !ctx->region_awaiting){
		/* the connection that wanted it is gone: the session stays here, offline, as Mosquitto keeps one */
		if(len){
			struct mosquitto *g = session__read(client, blob, len);
			if(g){
				g->region_is_ghost = false;
				context__add_to_by_id(g);
				if(g->session_expiry_interval != MQTT_SESSION_EXPIRY_IMMEDIATE){
					session_expiry__add(g);
				}else{
					context__add_to_disused(g);
				}
			}
		}
		return;
	}
	HASH_DELETE(hh_id, db.contexts_by_id_delayed_auth, ctx);
	ctx->region_awaiting = false;
	if(len){
		ctx->region_ghost = session__read(client, blob, len);
	}
	rc = connect__on_authorised(ctx, ctx->region_auth_data, ctx->region_auth_data_len);
	ctx->region_auth_data = NULL;
	if(rc){
		do_disconnect(ctx, rc);
	}
	while((s = ctx->region_stash) != NULL){
		ctx->region_stash = s->next;
		if(!rc){
			sub__deliver(ctx, s->msg, s->qos, s->retain, s->sid);
		}
		db__msg_store_ref_dec(&s->msg);
		mosquitto_FREE(s);
	}
}


/* The handed-over session, once Mosquitto's takeover code has taken from it what it needed. */
void session__ghost_free(struct mosquitto *g)
{
	context__cleanup(g, true);
}
