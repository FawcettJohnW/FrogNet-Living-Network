/* Copyright (C) 2016-2026 Fawcett Innovations LLC
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * lisper.h -- the LISP client library's C interface (liblisper.so / lisper.dll): lisper::Client from any language.
 *
 * A call that fails returns NULL (or -1) and, if err is not NULL, sets *err to a message the caller frees with
 * lisper_free. Strings a call returns are the caller's to free with lisper_free.
 */
#ifndef RIBBIT_LISPER_H
#define RIBBIT_LISPER_H
#ifdef __cplusplus
extern "C" {
#endif
typedef struct lisper_client lisper_client;

lisper_client* lisper_open(const char* host, int port, char** err);   /* connects to a lisper-ram at host:port */
void lisper_close(lisper_client* c);
/* any LISP region operation: name, a JSON object of arguments -> the result as JSON text */
char* lisper_operation(lisper_client* c, const char* name, const char* args_json, char** err);
/* a Map-Register (hex) from source -> the Map-Notify to send back (hex), "" when none */
char* lisper_register(lisper_client* c, const char* packet_hex, const char* source, char** err);
/* what a lookup for prefix resolves to (JSON), "[]" when nothing does */
char* lisper_resolve(lisper_client* c, const char* iid, const char* prefix, const char* group, char** err);
int lisper_add_site(lisper_client* c, const char* iid, const char* prefix, const char* group, int accept_more_specifics,
                    int key_id, const char* password, char** err);
int lisper_delete_site(lisper_client* c, const char* iid, const char* prefix, const char* group, char** err);
void lisper_free(char* s);

#ifdef __cplusplus
}
#endif
#endif
