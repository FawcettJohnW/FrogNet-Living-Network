/*
Copyright (C) 2016-2026 Fawcett Innovations LLC
SPDX-License-Identifier: GPL-2.0-only

region.c -- this front's place in the MQTT region.

[FRONT_FINDS_ITS_REGION_V1] Where the region lives is the deployment's choice (MQTT_REGION):
  unset       the region is resident in this process, shared by every front thread in it [CO_RESIDENT_FRONT_V1]
  host:port   one mqtt-ram on a neutral machine, fronts anywhere -- the Internet shape
  own         this front starts the mqtt-ram beside its own executable on a loopback port; that host goes when the
              front goes (it is the front's child, and dies with it)
Either way the region's deliveries for this front's clients wake the loop through the front's eventfd.
*/

#ifndef _GNU_SOURCE
#define _GNU_SOURCE   /* pipe2 */
#endif
#include "config.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

#include "mosquitto_broker_internal.h"
#include "region_client.h"
#include "sys_tree.h"

static __thread pid_t host_pid = 0;
static pid_t own_host_pid = 0;                  /* [ONE_OWN_HOST_V1] left for main when several fronts share it */
/* [ADMIT_BY_RESERVATION_V1] where the other fronts are: 0 resident (the region's slots), 1 own (sibling threads of this
 * process only: a process-local slot table, no round trip), 2 host:port (other processes: the host's slots) */
static int region_mode = 0;
#define LOCAL_FRONTS 1024
static int64_t local_clients[LOCAL_FRONTS], local_conns[LOCAL_FRONTS], local_listener[LOCAL_FRONTS][32];
/* [FRONT_THREADS_V1] every front's eventfd, so a signal can wake every front loop, not just the thread it landed on */
static int front_efds[1024];
static int front_efd_count = 0;
static struct { int ident; } region_ev = { id_region };


static int start_own_host(int *port)
{
	char self[PATH_MAX], exe[PATH_MAX+16], fdarg[16], buf[32];
	ssize_t n = readlink("/proc/self/exe", self, sizeof(self)-1);
	char *slash;
	int pfd[2];
	struct pollfd pw;
	size_t got = 0;

	if(n <= 0){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: cannot find this executable: %s", strerror(errno));
		return 1;
	}
	self[n] = 0;
	slash = strrchr(self, '/');
	if(slash){
		*slash = 0;
	}
	snprintf(exe, sizeof(exe), "%s/mqtt-ram", self);
	/* [ANNOUNCE_PORT_V1] the host listens on a port the system chooses and tells us which, through this pipe */
	if(pipe2(pfd, O_CLOEXEC)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: pipe: %s", strerror(errno));
		return 1;
	}
	host_pid = fork();
	if(host_pid < 0){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: fork: %s", strerror(errno));
		return 1;
	}
	if(host_pid == 0){
		char uq[8], ad[8], mq[24];
		int wfd = dup(pfd[1]);                  /* not close-on-exec: the host writes its port here */
		prctl(PR_SET_PDEATHSIG, SIGTERM);       /* the host goes when its front goes */
		snprintf(uq, sizeof(uq), "%d", db.config->upgrade_outgoing_qos ? 1 : 0);
		snprintf(ad, sizeof(ad), "%d", db.config->allow_duplicate_messages ? 1 : 0);
		snprintf(mq, sizeof(mq), "%ld", (long)db.config->max_queued_messages);
		snprintf(fdarg, sizeof(fdarg), "%d", wfd);
		{   /* [ONE_OWN_HOST_V1] the host holds none of the front's descriptors: only stdio and its announce pipe */
			long maxfd = sysconf(_SC_OPEN_MAX);
			for(int fd = 3; fd < (maxfd > 0 && maxfd < 65536 ? (int)maxfd : 65536); fd++){
				if(fd != wfd){
					close(fd);
				}
			}
		}
		(void)uq; (void)ad; (void)mq;           /* [MEMORY_IS_MEMORY_V1] the memory has no MQTT options: the box's region applies them */
		execl(exe, exe, "--listen", "127.0.0.1:0", "--announce-fd", fdarg, (char *)NULL);
		fprintf(stderr, "Error: region: cannot run %s: %s\n", exe, strerror(errno));
		_exit(127);
	}
	close(pfd[1]);
	pw.fd = pfd[0];
	pw.events = POLLIN;
	while(got < sizeof(buf)-1){
		if(poll(&pw, 1, 10000) <= 0){
			break;
		}
		n = read(pfd[0], buf+got, sizeof(buf)-1-got);
		if(n <= 0){
			break;
		}
		got += (size_t)n;
		buf[got] = 0;
		if(memchr(buf, '\n', got)){
			break;
		}
	}
	close(pfd[0]);
	buf[got] = 0;
	*port = atoi(buf);
	if(*port <= 0){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: mqtt-ram did not report its port.");
		return 1;
	}
	return 0;
}


int region__start(void)
{
	const char *env = getenv("MQTT_REGION");
	int rc = 0, port = 0;

	region_mode = (!env || !*env) ? 0 : (!strcmp(env, "own") ? 1 : 2);

	/* [MEMORY_IS_MEMORY_V1] every front answers from its box's region, in this process, shared by every front thread */
	rgn_config(db.config->upgrade_outgoing_qos, db.config->allow_duplicate_messages, (long)db.config->max_queued_messages);
	if(!env || !*env){
		rc = rgn_open(NULL, 0);
		if(rc){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: region: %s", rgn_error());
			return MOSQ_ERR_UNKNOWN;
		}
	}else if(!strcmp(env, "own")){
		/* [ONE_OWN_HOST_V1] one mqtt-ram per process: the first front thread starts it, the others use its port (a
		 * host started per thread went unused -- the process has one session -- and, forked later, held copies of
		 * sockets the broker had already closed) */
		static int own_claimed = 0, own_port = 0;
		if(!__atomic_exchange_n(&own_claimed, 1, __ATOMIC_ACQ_REL)){
			if(start_own_host(&port)){
				__atomic_store_n(&own_port, -1, __ATOMIC_RELEASE);
				return MOSQ_ERR_UNKNOWN;
			}
			__atomic_store_n(&own_port, port, __ATOMIC_RELEASE);
		}else{
			while((port = __atomic_load_n(&own_port, __ATOMIC_ACQUIRE)) == 0){
				usleep(1000);
			}
			if(port < 0){
				log__printf(NULL, MOSQ_LOG_ERR, "Error: region: the first front could not start mqtt-ram.");
				return MOSQ_ERR_UNKNOWN;
			}
		}
		rc = rgn_open("127.0.0.1", port);
		if(rc){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: region: mqtt-ram on port %d did not answer: %s", port, rgn_error());
			return MOSQ_ERR_UNKNOWN;
		}
	}else{
		char host[256];
		const char *colon = strrchr(env, ':');
		if(!colon || colon == env || (size_t)(colon - env) >= sizeof(host)){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: MQTT_REGION must be host:port or \"own\", not %s", env);
			return MOSQ_ERR_INVAL;
		}
		memcpy(host, env, (size_t)(colon - env));
		host[colon - env] = 0;
		rc = rgn_open(host, atoi(colon + 1));
		if(rc){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: region %s: %s", env, rgn_error());
			return MOSQ_ERR_UNKNOWN;
		}
	}
	{
		int k = __atomic_fetch_add(&front_efd_count, 1, __ATOMIC_ACQ_REL);
		if(k < 1024){
			__atomic_store_n(&front_efds[k], rgn_eventfd(), __ATOMIC_RELEASE);
		}
	}
	sub__region_init();                         /* [PIPELINE_V1] before anything can be taken for this front */
	if(rgn_start_waiter()){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region waiter: %s", rgn_error());
		return MOSQ_ERR_UNKNOWN;
	}
	sub__hold_announce();                       /* [HOLD_FOR_ROOM_V1] the bounds it runs with */
	log__printf(NULL, MOSQ_LOG_INFO, "Region: front %d of %s.", rgn_front(),
			(!env || !*env) ? "this box alone" : (!strcmp(env, "own") ? "this box, on its own memory" : env));
	return MOSQ_ERR_SUCCESS;
}


/* [CO_RESIDENT_FRONT_V1] before the loop sleeps: deliveries already waiting are taken now, not after the sleep.
 * [FRONT_COUNTS_V1] and this front's counts go where the other fronts' limits can read them. */
void region__before_sleep(void)
{
	if(g_front_count > 1 || region_mode != 0){     /* [FRONT_COUNTS_V1] the other fronts', and the other boxes', limits read them */
		int64_t lc[32];
		int n = db.config->listener_count < 32 ? db.config->listener_count : 32;
		for(int i=0; i<n; i++){
			lc[i] = db.config->listeners[i].client_count;
		}
		rgn_counts_publish((int64_t)HASH_CNT(hh_id, db.contexts_by_id), (int64_t)HASH_CNT(hh_sock, db.contexts_by_sock), lc, n);
		metrics__front_gauges();            /* [METRIC_ROWS_V1] */
	}
	sub__own_held_retry();                      /* [HOLD_FOR_ROOM_V1] what this front holds, offered again */
	if(rgn_arm()){
		sub__region_drain();
	}
}


int region__add_to_mux(void)
{
#ifdef WITH_EPOLL
	struct epoll_event ev;

	memset(&ev, 0, sizeof(ev));
	ev.events = EPOLLIN;
	ev.data.ptr = &region_ev;
	if(epoll_ctl(db.epollfd, EPOLL_CTL_ADD, rgn_eventfd(), &ev) == -1){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: epoll_ctl: %s", strerror(errno));
		return MOSQ_ERR_UNKNOWN;
	}
	return MOSQ_ERR_SUCCESS;
#else
	log__printf(NULL, MOSQ_LOG_ERR, "Error: region deliveries need the epoll loop.");
	return MOSQ_ERR_NOT_SUPPORTED;
#endif
}


void region__host_stop(void)
{
	pid_t p = __atomic_exchange_n(&own_host_pid, 0, __ATOMIC_ACQ_REL);
	if(p > 0){
		int status;
		kill(p, SIGTERM);
		waitpid(p, &status, 0);
	}
}


void region__stop(void)
{
	if(host_pid > 0){
		/* [ONE_OWN_HOST_V1] with several front threads the host is stopped once every front has finished (main,
		 * region__host_stop): the first front to finish must not take it from the others still cleaning up */
		if(g_front_count > 1){
			__atomic_store_n(&own_host_pid, host_pid, __ATOMIC_RELEASE);
		}else{
			int status;
			kill(host_pid, SIGTERM);
			waitpid(host_pid, &status, 0);
		}
		host_pid = 0;
	}
}


/* [ADMIT_BY_RESERVATION_V1] Before a limit is checked, this front writes its counts to its own slot (with
 * clients_ahead added: a CONNECT being admitted is not in the table yet -- the reservation) and then reads the other
 * fronts' slots. A front that admits has already said so; the next front to check sees it. Two fronts checking at the
 * same moment can both refuse; neither can over-admit. Every slot keeps one writer. The slots are where the other
 * fronts are: the resident region's; this process's own table when the host is this process's (`own`: no round trip
 * between accepting and refusing); the host's when fronts run in other processes (`host:port`). */
static int64_t lc_self[32];
static void region__exchange(int64_t clients_ahead, int64_t *others_clients, int64_t *others_conns, int listener, int64_t *others_listener)
{
	int n = db.config->listener_count < 32 ? db.config->listener_count : 32;
	int64_t c = (int64_t)HASH_CNT(hh_id, db.contexts_by_id) + clients_ahead, s = (int64_t)HASH_CNT(hh_sock, db.contexts_by_sock);

	for(int i=0; i<n; i++){
		lc_self[i] = db.config->listeners[i].client_count;
	}
	if(region_mode == 1){
		int me = g_front_index, fronts = g_front_count < LOCAL_FRONTS ? g_front_count : LOCAL_FRONTS;
		int64_t oc = 0, os = 0, ol = 0;
		if(me >= 0 && me < LOCAL_FRONTS){
			__atomic_store_n(&local_clients[me], c, __ATOMIC_RELEASE);
			__atomic_store_n(&local_conns[me], s, __ATOMIC_RELEASE);
			for(int i=0; i<n; i++){
				__atomic_store_n(&local_listener[me][i], lc_self[i], __ATOMIC_RELEASE);
			}
		}
		for(int f=0; f<fronts; f++){
			if(f == me) continue;
			oc += __atomic_load_n(&local_clients[f], __ATOMIC_ACQUIRE);
			os += __atomic_load_n(&local_conns[f], __ATOMIC_ACQUIRE);
			if(listener >= 0 && listener < 32) ol += __atomic_load_n(&local_listener[f][listener], __ATOMIC_ACQUIRE);
		}
		if(others_clients) *others_clients = oc;
		if(others_conns) *others_conns = os;
		if(others_listener) *others_listener = ol;
		return;
	}
	if(rgn_counts_exchange(c, s, lc_self, n)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: region: counts: %s", rgn_error());
	}
	rgn_counts_others(others_clients, others_conns, listener, others_listener);
}

/* [FRONT_COUNTS_V1] the whole broker's counts, as limits see them: this front's own, exactly, plus the others' */
long region__clients_total(void)
{
	int64_t others = 0;
	region__exchange(1, &others, NULL, -1, NULL);
	return (long)HASH_CNT(hh_id, db.contexts_by_id) + (long)others;
}

long region__connections_total(void)
{
	int64_t others = 0;
	region__exchange(0, NULL, &others, -1, NULL);
	return (long)HASH_CNT(hh_sock, db.contexts_by_sock) + (long)others;
}

long region__listener_total(struct mosquitto__listener *listener)
{
	int64_t others = 0;
	region__exchange(0, NULL, NULL, (int)(listener - db.config->listeners), &others);
	return (long)listener->client_count + (long)others;
}


/* Called from a signal handler: write(2) is async-signal-safe, and nothing else is touched. */
void region__wake_all(void)
{
	int n = __atomic_load_n(&front_efd_count, __ATOMIC_ACQUIRE);
	uint64_t one = 1;
	for(int i=0; i<n && i<1024; i++){
		int fd = __atomic_load_n(&front_efds[i], __ATOMIC_ACQUIRE);
		if(fd > 0){
			ssize_t w = write(fd, &one, sizeof(one));
			(void)w;
		}
	}
}
