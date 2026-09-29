/* Copyright (C) 2016-2026 Fawcett Innovations LLC
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * tuples.h -- the default Ribbit API's C interface (libtuples.so / tuples.dll): ribbit::TuplesClient from any
 * language. The vendor is named when the client opens: a vendor's library is this one opened with its own id.
 *
 * A call that fails returns NULL (or -1) and, if err is not NULL, sets *err to a message the caller frees with
 * tuples_free. Strings a call returns are the caller's to free with tuples_free. A client may be used from several
 * threads at once.
 */
#ifndef RIBBIT_TUPLES_H
#define RIBBIT_TUPLES_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct tuples_client tuples_client;

/* connects to <vendor_id>-ram at host:port (its endpoint /<vendor_id>-api) */
tuples_client* tuples_open(const char* vendor_id, const char* host, int port, char** err);
void tuples_close(tuples_client* c);                  /* removes the tuples it put with own=1, then closes */
/* (service, var, scope) = value (a JSON object); own=1: removed when this client closes. Returns the write's id. */
int64_t tuples_put(tuples_client* c, const char* service, const char* var, const char* scope, const char* value_json,
                   int own, char** err);
/* a service's tuples (of one var when var is not NULL or ""), written within fresh_s seconds (0: any age); with
 * wait_s > 0, held until min_rows exist. A JSON array of {var, scope, name, addr, value, ts_env, age_s, id}. */
char* tuples_get_all(tuples_client* c, const char* service, int fresh_s, double wait_s, int min_rows, const char* var,
                     char** err);
int tuples_remove(tuples_client* c, const char* service, const char* var, const char* scope, char** err);  /* 1, 0, -1 */
int tuples_remove_id(tuples_client* c, int64_t id, char** err);                                           /* 0, -1 */
char* tuples_my_ip(tuples_client* c, char** err);
void tuples_free(char* s);

#ifdef __cplusplus
}
#endif
#endif
