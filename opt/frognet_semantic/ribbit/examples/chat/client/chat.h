/* Copyright (C) 2016-2026 Fawcett Innovations LLC
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * chat.h -- the chat client library's C interface (libchat.so / chat.dll): chat::Client, callable from any language.
 *
 * Every call takes the handle chat_open returned. A call that fails returns NULL (or -1) and, if err is not NULL,
 * sets *err to a message the caller frees with chat_free. Strings a call returns are the caller's to free with
 * chat_free. The handle is safe to use from several threads at once (one may wait in chat_receive while another
 * sends).
 */
#ifndef RIBBIT_CHAT_H
#define RIBBIT_CHAT_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct chat_client chat_client;

/* Connects to a chat-ram at host:port. */
chat_client* chat_open(const char* host, int port, char** err);
void chat_close(chat_client* c);
/* Says text from `from` to `to`. Returns 0, or -1 with *err set. */
int chat_send(chat_client* c, const char* to, const char* from, const char* text, char** err);
/* Where me's buffer stands now (messages at or before it are not new). Returns -1 with *err set on failure. */
int64_t chat_position(chat_client* c, const char* me, char** err);
/* Messages written to me after *after, waiting up to wait_s seconds for the first. Returns a JSON array of
 * {"from","text","ts","id"} and moves *after past them; NULL with *err set on failure. */
char* chat_receive(chat_client* c, const char* me, int64_t* after, double wait_s, char** err);
void chat_free(char* s);

#ifdef __cplusplus
}
#endif
#endif
