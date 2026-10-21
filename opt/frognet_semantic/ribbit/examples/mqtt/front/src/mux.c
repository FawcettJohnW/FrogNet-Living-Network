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
   Tatsuzo Osawa - Add epoll.
*/

#include "mux.h"
#include "packet_mosq.h"   /* [WRITE_COALESCED_V1] packet__write, for out__mark and out__flush */


int mux__init(void)
{
#ifdef WITH_EPOLL
	return mux_epoll__init();
#elif defined(WITH_KQUEUE)
	return mux_kqueue__init();
#else
	return mux_poll__init();
#endif
}


int mux__add_listeners(struct mosquitto__listener_sock *listensock, int listensock_count)
{
#ifdef WITH_EPOLL
	return mux_epoll__add_listeners(listensock, listensock_count);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__add_listeners(listensock, listensock_count);
#else
	return mux_poll__add_listeners(listensock, listensock_count);
#endif
}


int mux__delete_listeners(struct mosquitto__listener_sock *listensock, int listensock_count)
{
#ifdef WITH_EPOLL
	return mux_epoll__delete_listeners(listensock, listensock_count);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__delete_listeners(listensock, listensock_count);
#else
	return mux_poll__delete_listeners(listensock, listensock_count);
#endif
}


int mux__add_out(struct mosquitto *context)
{
#ifdef WITH_EPOLL
	return mux_epoll__add_out(context);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__add_out(context);
#else
	return mux_poll__add_out(context);
#endif
}


int mux__remove_out(struct mosquitto *context)
{
#ifdef WITH_EPOLL
	return mux_epoll__remove_out(context);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__remove_out(context);
#else
	return mux_poll__remove_out(context);
#endif
}


int mux__new(struct mosquitto *context)
{
#ifdef WITH_EPOLL
	return mux_epoll__new(context);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__new(context);
#else
	return mux_poll__new(context);
#endif
}


int mux__delete(struct mosquitto *context)
{
#ifdef WITH_EPOLL
	return mux_epoll__delete(context);
#elif defined(WITH_KQUEUE)
	return mux_kqueue__delete(context);
#else
	return mux_poll__delete(context);
#endif
}


/* [WRITE_COALESCED_V1] the clients with packets queued this loop pass (each front thread its own: front threads share
 * nothing). Written once each, all queued packets in one writev, before the loop waits again. */
static __thread struct mosquitto **out_list = NULL;
static __thread int out_n = 0, out_cap = 0;
void out__mark(struct mosquitto *context)
{
	if(context->out_dirty) return;
	if(out_n == out_cap){
		int cap = out_cap ? out_cap * 2 : 256;
		struct mosquitto **nl = mosquitto_realloc(out_list, sizeof(*nl) * (size_t)cap);
		if(!nl){ packet__write(context); return; }      /* no room to defer it: written now, as Mosquitto did */
		out_list = nl; out_cap = cap;
	}
	context->out_dirty = true;
	out_list[out_n++] = context;
}
void out__forget(struct mosquitto *context)
{
	if(!context->out_dirty) return;
	context->out_dirty = false;
	for(int i = 0; i < out_n; i++) if(out_list[i] == context) out_list[i] = NULL;
}
void out__flush(void)
{
	for(int i = 0; i < out_n; i++){                     /* a disconnect below may queue more: the list grows, i follows */
		struct mosquitto *context = out_list[i];
		if(!context) continue;
		out_list[i] = NULL;
		context->out_dirty = false;
		if(context->sock == INVALID_SOCKET) continue;
		int rc = packet__write(context);
		if(rc) do_disconnect(context, rc);
	}
	out_n = 0;
}


int mux__handle(struct mosquitto__listener_sock *listensock, int listensock_count)
{
	out__flush();                                       /* [WRITE_COALESCED_V1] before the loop waits */
#ifdef WITH_EPOLL
	UNUSED(listensock);
	UNUSED(listensock_count);
	return mux_epoll__handle();
#elif defined(WITH_KQUEUE)
	UNUSED(listensock);
	UNUSED(listensock_count);
	return mux_kqueue__handle();
#else
	return mux_poll__handle(listensock, listensock_count);
#endif
}


int mux__cleanup(void)
{
#ifdef WITH_EPOLL
	return mux_epoll__cleanup();
#elif defined(WITH_KQUEUE)
	return mux_kqueue__cleanup();
#else
	return mux_poll__cleanup();
#endif
}
