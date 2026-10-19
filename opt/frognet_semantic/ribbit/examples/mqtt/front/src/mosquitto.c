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

#ifndef _GNU_SOURCE
#define _GNU_SOURCE   /* pthread_tryjoin_np */
#endif
#include "config.h"
#include <pthread.h>
#include <time.h>

#ifndef WIN32
/* For initgroups() */
#  include <unistd.h>
#  include <grp.h>
#  include <assert.h>
/* For umask() */
#  include <sys/stat.h>
#endif

#ifndef WIN32
#include <pwd.h>
#else
#include <process.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#endif

#ifndef WIN32
#  include <sys/time.h>
#endif

#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#ifdef WITH_SYSTEMD
#  include <systemd/sd-daemon.h>
#endif
#ifdef WITH_WRAP
#include <tcpd.h>
#endif

#include "mosquitto_broker_internal.h"
#include "region_client.h"
#include "util_mosq.h"

static struct mosquitto_db front0_db;
__thread struct mosquitto_db *mosq_db = &front0_db;
__thread int g_front_index = 0;
int g_front_count = 1;

__thread struct mosquitto__listener_sock *g_listensock = NULL;
__thread int g_listensock_count = 0;

int g_run = 0;
int g_stop_requested = 0;     /* [STOP_IS_FOR_EVERY_FRONT_V1] set by SIGINT/SIGTERM, never cleared */
#ifdef WITH_WRAP
#include <syslog.h>
int allow_severity = LOG_INFO;
int deny_severity = LOG_INFO;
#endif


static int set_umask(void)
{
#if !defined(__CYGWIN__) && !defined(WIN32)
	/* This affects files that are written to, apart from those that are
	 * created using mosquitto_fopen(..., restrict_read=true), which sets a
	 * umask of 077. */
	const char *mask_s;
	char *endptr = NULL;
	long mask;


	mask_s = getenv("UMASK_SET");
	if(mask_s){
		errno = 0;
		mask = strtol(mask_s, &endptr, 8);
		if(errno || endptr == mask_s || *endptr != '\0'){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: UMASK_SET environment variable not a valid octal number.");
			return MOSQ_ERR_INVAL;
		}
		if(mask < 000 || mask > 0777){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: UMASK_SET environment variable out of range.");
			return MOSQ_ERR_INVAL;
		}
		umask((mode_t)mask);
	}
#endif
	return MOSQ_ERR_SUCCESS;
}


/* coverity[ +tainted_string_sanitize_content : arg-0 ] */
static int check_uid(const char *s, const char *name)
{
	char *endptr = NULL;
	long id;

	errno = 0;
	id = strtol(s, &endptr, 10);
	if(errno || endptr == s || *endptr != '\0'){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: %s not a valid ID '%s'", name, s);
		return -1;
	}
	if(id < 0){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: %s must not be negative", name);
		return -1;
	}
	if(id > INT_MAX){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: %s must not be less than %d", name, INT_MAX);
		return -1;
	}
	return (int)id;
}


/* Prints the name of the process user from its user id if not found
 * it simply prints out the user id
 */
static void print_pwname(void)
{
#ifndef WIN32
	struct passwd *pwd = getpwuid(geteuid());
	if(!pwd){
		log__printf(NULL, MOSQ_LOG_INFO, "Info: running mosquitto as user id: %i.", geteuid());
	}else{
		log__printf(NULL, MOSQ_LOG_INFO, "Info: running mosquitto as user: %s.", pwd->pw_name);
	}
#endif
}


/* mosquitto shouldn't run as root.
 * This function will attempt to change to an unprivileged user and group if
 * running as root. The user is given in config->user.
 * Returns 1 on failure (unknown user, setuid/setgid failure)
 * Returns 0 on success.
 * Note that setting config->user to "root" does not produce an error, but it
 * strongly discouraged.
 */


static int drop_privileges(struct mosquitto__config *config)
{
#if !defined(__CYGWIN__) && !defined(WIN32)
	struct passwd *pwd;
	char *err;
	int rc;
	const char *puid_s, *pgid_s;
	int puid;
	int pgid;

	const char *snap = getenv("SNAP_NAME");
	if(snap && !strcmp(snap, "mosquitto")){
		/* Don't attempt to drop privileges if running as a snap */
		return MOSQ_ERR_SUCCESS;
	}

	/* PUID and PGID are docker custom user mappings */
	puid_s = getenv("PUID");
	pgid_s = getenv("PGID");

	if(geteuid() == 0){
		if(puid_s || pgid_s){
			if(pgid_s){
				pgid = check_uid(pgid_s, "PGID");
				if(pgid < 0){
					return MOSQ_ERR_INVAL;
				}else if(pgid > 0){
					rc = setgid((gid_t)pgid);
					if(rc == -1){
						err = strerror(errno);
						log__printf(NULL, MOSQ_LOG_ERR, "Error setting gid whilst dropping privileges: %s.", err);
						return MOSQ_ERR_ERRNO;
					}
				}
			}
			if(puid_s){
				puid = check_uid(puid_s, "PUID");
				if(puid < 0){
					return MOSQ_ERR_INVAL;
				}else if(puid > 0){
					rc = setuid((uid_t)puid);
					if(rc == -1){
						err = strerror(errno);
						log__printf(NULL, MOSQ_LOG_ERR, "Error setting uid whilst dropping privileges: %s.", err);
						return MOSQ_ERR_ERRNO;
					}
				}
			}
		}else if(config->user && strcmp(config->user, "root")){
			pwd = getpwnam(config->user);
			if(!pwd){
				if(strcmp(config->user, "mosquitto")){
					log__printf(NULL, MOSQ_LOG_ERR, "Error: Unable to drop privileges to '%s' because this user does not exist.", config->user);
					return MOSQ_ERR_INVAL;
				}else{
					log__printf(NULL, MOSQ_LOG_ERR, "Warning: Unable to drop privileges to '%s' because this user does not exist. Trying 'nobody' instead.", config->user);
					pwd = getpwnam("nobody");
					if(!pwd){
						log__printf(NULL, MOSQ_LOG_ERR, "Error: Unable to drop privileges to 'nobody'.");
						return MOSQ_ERR_ERRNO;
					}
				}
			}
			if(initgroups(config->user, pwd->pw_gid) == -1){
				err = strerror(errno);
				log__printf(NULL, MOSQ_LOG_ERR, "Error setting groups whilst dropping privileges: %s.", err);
				return MOSQ_ERR_ERRNO;
			}
			rc = setgid(pwd->pw_gid);
			if(rc == -1){
				err = strerror(errno);
				log__printf(NULL, MOSQ_LOG_ERR, "Error setting gid whilst dropping privileges: %s.", err);
				return MOSQ_ERR_ERRNO;
			}
			rc = setuid(pwd->pw_uid);
			if(rc == -1){
				err = strerror(errno);
				log__printf(NULL, MOSQ_LOG_ERR, "Error setting uid whilst dropping privileges: %s.", err);
				return MOSQ_ERR_ERRNO;
			}
		}
		print_pwname();
		if(geteuid() == 0 || getegid() == 0){
			log__printf(NULL, MOSQ_LOG_WARNING, "Warning: Mosquitto should not be run as root/administrator.");
		}
	}else{
		print_pwname();
	}

#else
	UNUSED(config);
#endif
	return MOSQ_ERR_SUCCESS;
}


static void mosquitto__daemonise(void)
{
#ifndef WIN32
	char *err;
	pid_t pid;

	pid = fork();
	if(pid < 0){
		err = strerror(errno);
		log__printf(NULL, MOSQ_LOG_ERR, "Error in fork: %s", err);
		exit(1);
	}
	if(pid > 0){
		exit(0);
	}
	if(setsid() < 0){
		err = strerror(errno);
		log__printf(NULL, MOSQ_LOG_ERR, "Error in setsid: %s", err);
		exit(1);
	}

	if(!freopen("/dev/null", "r", stdin)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error whilst daemonising (%s): %s", "stdin", strerror(errno));
		exit(1);
	}
	if(!freopen("/dev/null", "w", stdout)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error whilst daemonising (%s): %s", "stdout", strerror(errno));
		exit(1);
	}
	if(!freopen("/dev/null", "w", stderr)){
		log__printf(NULL, MOSQ_LOG_ERR, "Error whilst daemonising (%s): %s", "stderr", strerror(errno));
		exit(1);
	}
#else
	log__printf(NULL, MOSQ_LOG_WARNING, "Warning: Can't start in daemon mode in Windows.");
#endif
}


static int pid__write(void)
{
	FILE *pid;

	if(db.config->pid_file){
		pid = mosquitto_fopen(db.config->pid_file, "wt", false);
		if(pid){
			fprintf(pid, "%d", getpid());
			fclose(pid);
		}else{
			log__printf(NULL, MOSQ_LOG_ERR, "Error: Unable to write pid file.");
			return MOSQ_ERR_ERRNO;
		}
	}
	return MOSQ_ERR_SUCCESS;
}


static void report_features(void)
{
#ifdef WITH_BRIDGE
	log__printf(NULL, MOSQ_LOG_INFO, "Bridge support available.");
#else
	log__printf(NULL, MOSQ_LOG_INFO, "Bridge support NOT available.");
#endif
#ifdef WITH_PERSISTENCE
	log__printf(NULL, MOSQ_LOG_INFO, "Persistence support available.");
#else
	log__printf(NULL, MOSQ_LOG_INFO, "Persistence support NOT available.");
#endif
#ifdef WITH_TLS
	log__printf(NULL, MOSQ_LOG_INFO, "TLS support available.");
#else
	log__printf(NULL, MOSQ_LOG_INFO, "TLS support NOT available.");
#endif
#ifdef FINAL_WITH_TLS_PSK
	log__printf(NULL, MOSQ_LOG_INFO, "TLS-PSK support available.");
#else
	log__printf(NULL, MOSQ_LOG_INFO, "TLS-PSK support NOT available.");
#endif
#ifdef WITH_WEBSOCKETS
	log__printf(NULL, MOSQ_LOG_INFO, "Websockets support available.");
#else
	log__printf(NULL, MOSQ_LOG_INFO, "Websockets support NOT available.");
#endif
	if(getenv("MOSQUITTO_UNSAFE_ALLOW_SYMLINKS")){
		log__printf(NULL, MOSQ_LOG_NOTICE, "MOSQUITTO_UNSAFE_ALLOW_SYMLINKS is set, loading of sensitive files through symbolic links is allowed.");
	}
}


static void post_shutdown_cleanup(void)
{
	struct mosquitto *ctxt, *ctxt_tmp;

	/* FIXME - this isn't quite right, all wills with will delay zero should be
	 * sent now, but those with positive will delay should be persisted and
	 * restored, pending the client reconnecting in time. */
	HASH_ITER(hh_id, db.contexts_by_id, ctxt, ctxt_tmp){
		context__send_will(ctxt);
	}
	will_delay__send_all();

	/* Set to true only after persistence events have been processed */
	db.shutdown = true;
	log__printf(NULL, MOSQ_LOG_INFO, "mosquitto version %s terminating", VERSION);

	broker_control__cleanup();

#ifdef WITH_PERSISTENCE
	persist__backup(true);
#endif
	session_expiry__remove_all();

	listeners__stop();

	HASH_ITER(hh_id, db.contexts_by_id, ctxt, ctxt_tmp){
#if defined(WITH_WEBSOCKETS) && WITH_WEBSOCKETS == WS_IS_LWS
		if(!ctxt->wsi)
#endif
		{
			ctxt->is_persisted = false; /* prevent persistence removal */
			context__cleanup(ctxt, true);
		}
	}
	HASH_ITER(hh_sock, db.contexts_by_sock, ctxt, ctxt_tmp){
		ctxt->is_persisted = false; /* prevent persistence removal */
		context__cleanup(ctxt, true);
	}
#ifdef WITH_BRIDGE
	bridge__db_cleanup();
#endif
	context__free_disused();
	keepalive__cleanup();

#ifdef WITH_TLS
	mosquitto_FREE(db.tls_keylog);
#endif
	db__close();

	plugin__unload_all();
	mosquitto_security_cleanup(false);

	if(db.config->pid_file){
		(void)remove(db.config->pid_file);
	}

	mux__cleanup();
	region__stop();

	log__close(db.config);
	config__cleanup(db.config);
	net__broker_cleanup();
}


static void cjson_init(void)
{
	cJSON_Hooks hooks = {mosquitto_malloc, mosquitto_free};
	cJSON_InitHooks(&hooks);
}

static int front0_up = 0;

static int front_main(int argc, char *argv[])
{
	struct mosquitto__config config;
	int rc;

	mosquitto_time_init();
	cjson_init();

#if defined(WIN32) || defined(__CYGWIN__)
	if(argc == 2){
		if(!strcmp(argv[1], "run")){
			service_run(argv[0]);
			return 0;
		}else if(!strcmp(argv[1], "install")){
			service_install(argv[0]);
			return 0;
		}else if(!strcmp(argv[1], "uninstall")){
			service_uninstall(argv[0]);
			return 0;
		}
	}
#endif


#ifdef WIN32
	if(_setmaxstdio(8192) != 8192){
		/* Old limit was 2048 */
		if(_setmaxstdio(2048) != 2048){
			log__printf(NULL, MOSQ_LOG_WARNING, "Warning: Unable to increase maximum allowed connections. This session may be limited to 512 connections.");
		}
	}
#endif

	memset(&db, 0, sizeof(struct mosquitto_db));
	db.now_s = mosquitto_time();
	db.now_real_s = time(NULL);
	mosquitto_broker_node_id_set(0);

	net__broker_init();

	db.config = &config;
	config__init(&config);
	rc = config__parse_args(&config, argc, argv);
	if(rc == MOSQ_ERR_UNKNOWN){
		post_shutdown_cleanup();
		return MOSQ_ERR_SUCCESS;
	}else if(rc != MOSQ_ERR_SUCCESS){
		post_shutdown_cleanup();
		return rc;
	}

	if(config.test_configuration){
		if(!db.config_file){
			log__printf(NULL, MOSQ_LOG_ERR, "Please provide a configuration file to test.");
			post_shutdown_cleanup();
			return MOSQ_ERR_INVAL;
		}else{
			log__printf(NULL, MOSQ_LOG_INFO, "Configuration file is OK.");
			post_shutdown_cleanup();
			return MOSQ_ERR_SUCCESS;
		}
	}

	rc = keepalive__init();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}

	/* Drop privileges permanently immediately after the config is loaded.
	 * This requires the user to ensure that all certificates, log locations,
	 * etc. are accessible my the `mosquitto` or other unprivileged user.
	 */
	rc = drop_privileges(&config);
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}
	/* Set umask based on environment variable */
	rc = set_umask();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}


	if(g_front_count > 1){
		/* [FRONT_THREADS_V1] Mosquitto's plugin contract is one broker thread: a plugin keeps its own state with no
		 * locks, and two front threads would each initialise it over the same globals. And a persistence file written
		 * by one front would hold only that front's sessions. Both are refused here, loudly: run more fronts as
		 * processes on one region (MQTT_REGION) for those. */
		bool plugins = db.plugin_count > 0;     /* config__read records each configured plugin here */
		if(plugins || config.persistence){
			log__printf(NULL, MOSQ_LOG_ERR, "Error: MQTT_THREADS=%d cannot be used with %s: run separate front processes on one region (MQTT_REGION) instead.",
					g_front_count, plugins ? "plugins" : "persistence");
			post_shutdown_cleanup();
			return MOSQ_ERR_NOT_SUPPORTED;
		}
	}
	if(g_front_index > 0){
		/* [FRONT_THREADS_V1] what a process has one of stays with the first front thread: the bridges (started below),
		 * $SYS, daemonising */
		config.sys_interval = 0;
	}
	if(config.daemon && g_front_count > 1){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: MQTT_THREADS > 1 cannot be combined with daemon mode.");
		post_shutdown_cleanup();
		return MOSQ_ERR_INVAL;
	}
	if(config.daemon){
		mosquitto__daemonise();
	}

	rc = pid__write();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}

	rc = region__start();
	if(rc != MOSQ_ERR_SUCCESS){
		post_shutdown_cleanup();
		return rc;
	}

	rc = db__open(&config);
	if(rc != MOSQ_ERR_SUCCESS){
		log__printf(NULL, MOSQ_LOG_ERR, "Error: Couldn't open database.");
		post_shutdown_cleanup();
		return rc;
	}

	/* Initialise logging only after initialising the database in case we're
	 * logging to topics */
	rc = log__init(&config);
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}
	log__printf(NULL, MOSQ_LOG_INFO, "mosquitto version %s starting", VERSION);
	if(db.config_file){
		log__printf(NULL, MOSQ_LOG_INFO, "Config loaded from %s.", db.config_file);
	}else{
		log__printf(NULL, MOSQ_LOG_INFO, "Using default config.");
	}
	report_features();

	rc = plugin__load_all();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}
	rc = mosquitto_security_init(false);
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}

	plugin_persist__handle_restore();
	session_expiry__check();
	retain__expire(&db.retains);
	db__msg_store_compact();

#ifdef WITH_SYS_TREE
	sys_tree__init();
#endif

	rc = mux__init();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}
	rc = region__add_to_mux();
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}

	rc = listeners__start();
	if(rc == MOSQ_ERR_NOT_FOUND && g_front_index > 0){
		post_shutdown_cleanup();
		return MOSQ_ERR_SUCCESS;        /* [FRONT_THREADS_V1] nothing for this front to serve */
	}
	if(rc){
		post_shutdown_cleanup();
		return rc;
	}

	signal__setup();

#ifdef WITH_BRIDGE
	if(g_front_index == 0){
		bridge__start_all();
	}
#endif

	broker_control__init();

	log__printf(NULL, MOSQ_LOG_INFO, "mosquitto version %s running", VERSION);
#ifdef WITH_SYSTEMD
	sd_notify(0, "READY=1");
#endif

	/* [STOP_IS_FOR_EVERY_FRONT_V1] a SIGTERM that arrived while this front was still starting stopped the process:
	 * this front must not set it running again (it would serve forever, and front 0 would wait for it forever) */
	g_run = 1;
	if(__atomic_load_n(&g_stop_requested, __ATOMIC_SEQ_CST)){
		g_run = 0;      /* checked after: a stop between the two is seen here */
	}
	if(g_front_index == 0){
		__atomic_store_n(&front0_up, 1, __ATOMIC_RELEASE);
	}
	rc = mosquitto_main_loop(g_listensock, g_listensock_count);

	post_shutdown_cleanup();

	return rc;
}

/* [FRONT_THREADS_V1] MQTT_THREADS=N (default 1): N fronts in this process, each a whole Mosquitto loop on its own
 * thread with its own db, listening on the same ports (SO_REUSEPORT), all sharing the region resident in the process
 * (or the one MQTT_REGION names). Lock-free between them: the only thing they share is the region. The first front
 * comes up alone (privileges, pid file, persistence restore); the others start once it is running. */
struct front_arg { int argc; char **argv; int index; int rc; pthread_t th; };

static int fronts_started = 0, fronts_done = 0;
static void *front_thread(void *p)
{
	struct front_arg *a = p;
	g_front_index = a->index;
	if(a->index > 0){
		mosq_db = calloc(1, sizeof(struct mosquitto_db));   /* this front's own */
		if(!mosq_db){
			a->rc = MOSQ_ERR_NOMEM;
			g_run = 0;
			return NULL;
		}
	}
	a->rc = front_main(a->argc, a->argv);
	if(a->rc){
		g_run = 0;      /* one front failing to start stops the process: no partial broker */
	}
	__atomic_add_fetch(&fronts_done, 1, __ATOMIC_ACQ_REL);
	/* [ONE_OWN_HOST_V1] Front 0's thread started the region's host, and Linux sends a child its parent-death signal
	 * when the THREAD that forked it ends: front 0's thread outlives every other front, so the host they share does */
	if(a->index == 0){
		while(__atomic_load_n(&fronts_done, __ATOMIC_ACQUIRE) < __atomic_load_n(&fronts_started, __ATOMIC_ACQUIRE)){
			struct timespec ts = {0, 10000000};
			nanosleep(&ts, NULL);
		}
	}
	return NULL;
}

#ifdef WITH_FUZZING
int mosquitto_fuzz_main(int argc, char *argv[])
#else
int main(int argc, char *argv[])
#endif
{
	const char *e = getenv("MQTT_THREADS");
	int n = e ? atoi(e) : 1;
	struct front_arg *fa;

	if(n <= 1){
		return front_main(argc, argv);
	}
	g_front_count = n;
	fa = calloc((size_t)n, sizeof(struct front_arg));
	if(!fa){
		return MOSQ_ERR_NOMEM;
	}
	for(int i=0; i<n; i++){
		fa[i].argc = argc;
		fa[i].argv = argv;
		fa[i].index = i;
	}
	__atomic_store_n(&fronts_started, 1, __ATOMIC_RELEASE);
	if(pthread_create(&fa[0].th, NULL, front_thread, &fa[0])){
		free(fa);
		return MOSQ_ERR_ERRNO;
	}
	while(!__atomic_load_n(&front0_up, __ATOMIC_ACQUIRE)){
		struct timespec ts = {0, 1000000};
		if(pthread_tryjoin_np(fa[0].th, NULL) == 0){
			int rc = fa[0].rc;     /* it finished without serving (--help, a config test, an error): its answer */
			free(fa);
			return rc;
		}
		nanosleep(&ts, NULL);
	}
	for(int i=1; i<n; i++){
		if(pthread_create(&fa[i].th, NULL, front_thread, &fa[i]) == 0){
			__atomic_add_fetch(&fronts_started, 1, __ATOMIC_ACQ_REL);
		}else{
			fa[i].th = 0;
		}
	}
	for(int i=0; i<n; i++){
		if(fa[i].th){
			pthread_join(fa[i].th, NULL);
		}
	}
	region__host_stop();    /* [ONE_OWN_HOST_V1] after every front has finished with it */
	{
		int rc = fa[0].rc;
		free(fa);
		return rc;
	}
}

#ifdef WIN32


int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow)
{
	char **argv;
	int argc = 1;
	char *token;
	char *saveptr = NULL;
	int rc;

	UNUSED(hInstance);
	UNUSED(hPrevInstance);
	UNUSED(nCmdShow);

	argv = mosquitto_malloc(sizeof(char *)*1);
	argv[0] = "mosquitto";
	token = strtok_r(lpCmdLine, " ", &saveptr);
	while(token){
		argc++;
		argv = mosquitto_realloc(argv, sizeof(char *)*argc);
		if(!argv){
			fprintf(stderr, "Error: Out of memory.\n");
			return MOSQ_ERR_NOMEM;
		}
		argv[argc-1] = token;
		token = strtok_r(NULL, " ", &saveptr);
	}
	rc = main(argc, argv);
	mosquitto_FREE(argv);
	return rc;
}
#endif
