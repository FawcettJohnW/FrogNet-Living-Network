/*
Copyright (c) 2016-2021 Roger Light <roger@atchoo.org>

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
   Dmitry Kaukov - windows named events implementation.
*/
#ifdef WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#endif

#include "config.h"

#include <stdio.h>
#include <stdbool.h>
#include <signal.h>

#include "mosquitto_broker_internal.h"

extern int g_run;
extern int g_stop_requested;

/* [FRONT_THREADS_V1] A signal is for the process, and every front thread must act on it: the handler bumps a
 * generation (an atomic add -- async-signal-safe, lock-free), and each front acts when the generation it has seen
 * is behind. A flag one thread clears would reach only that thread. */
static unsigned gen_reload = 0, gen_log_rotate = 0, gen_db_backup = 0, gen_tree_print = 0, gen_xtreport = 0;
static __thread unsigned seen_reload = 0, seen_log_rotate = 0, seen_db_backup = 0, seen_tree_print = 0;
#ifdef WITH_XTREPORT
static __thread unsigned seen_xtreport = 0;
#endif
#define SIG_BUMP(g) __atomic_add_fetch(&(g), 1, __ATOMIC_RELEASE)
static bool sig_due(unsigned *gen, unsigned *seen)
{
	unsigned g = __atomic_load_n(gen, __ATOMIC_ACQUIRE);
	if(g == *seen){
		return false;
	}
	*seen = g;
	return true;
}


static void handle_signal(int signal)
{
	UNUSED(signal);

	if(signal == SIGINT || signal == SIGTERM){
		__atomic_store_n(&g_stop_requested, 1, __ATOMIC_RELEASE);   /* [STOP_IS_FOR_EVERY_FRONT_V1] */
		g_run = 0;
#ifdef SIGHUP
	}else if(signal == SIGHUP){
		SIG_BUMP(gen_reload);
#endif
#ifdef SIGUSR1
	}else if(signal == SIGUSR1){
#ifdef WITH_PERSISTENCE
		SIG_BUMP(gen_db_backup);
#endif
#endif
#ifdef SIGUSR2
	}else if(signal == SIGUSR2){
		SIG_BUMP(gen_tree_print);
		SIG_BUMP(gen_xtreport);
#endif
#ifdef SIGRTMIN
	}else if(signal == SIGRTMIN){
		SIG_BUMP(gen_log_rotate);
#endif
	}
	region__wake_all();     /* [FRONT_THREADS_V1] every front acts on it now, not at its next timeout */
}


void signal__setup(void)
{
	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);
#ifdef SIGHUP
	signal(SIGHUP, handle_signal);
#endif
#ifndef WIN32
	signal(SIGUSR1, handle_signal);
	signal(SIGUSR2, handle_signal);
	signal(SIGPIPE, SIG_IGN);
#endif
#ifdef SIGRTMIN
	signal(SIGRTMIN, handle_signal);
#endif
#ifdef WIN32
	CreateThread(NULL, 0, SigThreadProc, NULL, 0, NULL);
#endif
}


int signal__flag_check(void)
{
#ifdef WITH_PERSISTENCE
	if(sig_due(&gen_db_backup, &seen_db_backup)){
		persist__backup(false);
	}
#endif
	if(sig_due(&gen_log_rotate, &seen_log_rotate)){
		log__close(db.config);
		log__init(db.config);
	}
	if(sig_due(&gen_reload, &seen_reload)){
		int rc;
		log__printf(NULL, MOSQ_LOG_INFO, "Reloading config.");
		config__read(db.config, true);
		listeners__reload_all_certificates();
		rc = plugin__handle_reload();
		if(rc){
			return rc;
		}
		mosquitto_security_cleanup(true);
		rc = mosquitto_security_init(true);
		if(rc){
			return rc;
		}
		rc = mosquitto_security_apply_default();
		if(rc){
			return rc;
		}
		log__close(db.config);
		log__init(db.config);
		keepalive__cleanup();
		rc = keepalive__init();
		if(rc){
			return rc;
		}
		broker_control__reload();
#ifdef WITH_BRIDGE
		bridge__reload();
#endif
	}
	if(sig_due(&gen_tree_print, &seen_tree_print)){
		sub__tree_print(db.normal_subs, 0);
		sub__tree_print(db.shared_subs, 0);
	}
#ifdef WITH_XTREPORT
	if(sig_due(&gen_xtreport, &seen_xtreport)){
		xtreport();
	}
#endif

	return MOSQ_ERR_SUCCESS;
}

/*
 *
 * Signalling mosquitto process on Win32.
 *
 *  On Windows we can use named events to pass signals to the mosquitto process.
 *  List of events :
 *
 *    mosqPID_shutdown
 *    mosqPID_reload
 *    mosqPID_backup
 *    mosqPID_log_rotate
 *    mosqPID_tree_print
 *    mosqPID_xtreport
 *
 * (where PID is the PID of the mosquitto process).
 */
#ifdef WIN32

#define MOSQ_MAX_EVTS 6


DWORD WINAPI SigThreadProc(void *data)
{
	TCHAR evt_name[MAX_PATH];
	static HANDLE evt[MOSQ_MAX_EVTS];
	int pid = GetCurrentProcessId();
	const char *evt_names[MOSQ_MAX_EVTS] = {
		"shutdown",
		"reload",
		"backup",
		"log_rotate",
		"tree_print",
		"xtreport"
	};

	UNUSED(data);

	for(int i=0; i<MOSQ_MAX_EVTS; i++){
		sprintf_s(evt_name, MAX_PATH, "mosq%d_%s", pid, evt_names[i]);
		evt[i] = CreateEvent(NULL, TRUE, FALSE, evt_name);
	}

	while(g_run){
		int wr = WaitForMultipleObjects(MOSQ_MAX_EVTS, evt, FALSE, INFINITE);
		switch(wr){
			case WAIT_OBJECT_0 + 0:
				handle_signal(SIGINT);
				break;
			case WAIT_OBJECT_0 + 1:
				SIG_BUMP(gen_reload);
				continue;
			case WAIT_OBJECT_0 + 2:
#ifdef WITH_PERSISTENCE
				SIG_BUMP(gen_db_backup);
#endif
				continue;
				break;
			case WAIT_OBJECT_0 + 3:
				SIG_BUMP(gen_log_rotate);
				continue;
			case WAIT_OBJECT_0 + 4:
				SIG_BUMP(gen_tree_print);
				continue;
			case WAIT_OBJECT_0 + 5:
				SIG_BUMP(gen_xtreport);
				continue;
		}
	}
	for(int i=0; i<MOSQ_MAX_EVTS; i++){
		if(evt[i]){
			CloseHandle(evt[i]);
		}
	}
	return 0;
}
#undef MOSQ_MAX_EVTS
#endif
