# Mosquitto 2.1.2 broker: per-client and per-session shared state

Source: `inc/mosquitto-2.1.2`. Only executable code was used. All paths are relative to that tree. `file:line` means the line in the shipped source.

## 0. What was and was not read

**Read in full (executable lines):** `src/context.c`, `src/handle_connect.c`, `src/handle_disconnect.c`, `src/handle_auth.c`, `src/handle_publish.c`, `src/handle_subscribe.c`, `src/handle_unsubscribe.c`, `src/handle_connack.c` (handle__connack only, lines 105-209), `src/read_handle.c`, `src/loop.c`, `src/mux.c`, `src/mux_epoll.c`, `src/keepalive.c`, `src/session_expiry.c`, `src/will_delay.c`, `src/property_broker.c`, `src/send_connack.c`, and `src/database.c` lines 37-1563 (all functions). Also from lib: `lib/packet_mosq.c` lines 100-693, `lib/send_publish.c`, `lib/send_mosq.c` lines 41-188, `lib/send_disconnect.c`, `lib/handle_pubackcomp.c`, `lib/handle_pubrec.c`, `lib/handle_pubrel.c`, `lib/handle_ping.c`, `lib/util_mosq.c` lines 55-286, `lib/alias_mosq.c`, `lib/will_mosq.c`, `lib/pthread_compat.h`, the struct definitions in `lib/mosquitto_internal.h` (lines 97-480) and `src/mosquitto_broker_internal.h`.

**Read in part:** `src/net.c` (lines 75-277, the accept path), `lib/net_mosq.c` (net__is_connected 197-203, net__socket_close 211-262), `src/mux_poll.c` (lines 57-367), `src/subs.c` (subs__send 68-130, sub__add counters 243-315, sub__remove counters 374-424, sub__clean_session 747-800), `src/bridge.c` (bridge_check 977-1179), `src/plugin_public.c` (set_clientid 340-386, kick 415-485, complete_basic_auth 860-885), `src/persist_read.c` (50-79), `src/mosquitto.c` (post_shutdown_cleanup 336-385), `src/signals.c`, `src/http_api.c` (160-410, 449-451), `src/sys_tree.c` (148-151, 201-259), `src/retain.c` (411-417), `src/watchdog.c`, `src/listeners.c` (27-61, plus the g_listensock grep), `src/conf.c` (defaults 325-359), `src/plugin_tick.c` (58-71).

**Not read:** `src/mux_kqueue.c` beyond a grep (it updates `db.now_s`/`now_real_s` after `kevent` at 173-174, the same as epoll), `src/websockets.c` and `src/http_serv.c` (only grep hits are used below), `src/send_suback.c`/`send_unsuback.c`/`send_auth.c` bodies, `lib/property_mosq.c`, `lib/packet_datatypes.c` (the primitive readers `packet__read_*`), `src/persist_write*.c`, `src/plugin_persist.c`, the `plugin_*.c` dispatchers, the full `src/subs.c`/`retain.c`/`bridge.c`/`sys_tree.c`, and `src/control.c`. Any statement that depends on those files says so.

---

## 1. `struct mosquitto` (the context)

Definition: `lib/mosquitto_internal.h:300-480`. One struct type serves both the client library and the broker. The fields below are the ones compiled under `WITH_BROKER` that matter to protocol behaviour. The struct is allocated by `context__init` (`src/context.c:61-126`) with `mosquitto_calloc`, so every field not set there starts at 0/NULL.

### How the session survives a reconnect

The broker **never reuses the old struct for the new connection.** Each accepted socket gets a fresh context (`net__socket_accept`, `src/net.c:187`). When a CONNECT names an id that already exists in `db.contexts_by_id`, `connect__on_authorised` (`src/handle_connect.c:96-192`) **moves** the session parts from the old struct (`found_context`) to the new one, then disconnects the old struct and marks it for freeing.

Between connections, the *old* struct is the session. It stays in `contexts_by_id` with `sock == INVALID_SOCKET` and `state == mosq_cs_disconnected`, and is optionally on the session-expiry list.

"Session" below means: carried across by the takeover code when `clean_start == false` and the old `session_expiry_interval != 0` (`handle_connect.c:115`), or kept on the offline struct.

| Field | Line in mosquitto_internal.h | Scope | Written at | Notes |
|---|---|---|---|---|
| `ident` | 303 | conn | context.c:78 | epoll/kqueue tag `id_client` |
| `sock` | 305 | conn | context.c:39, 83; net_mosq.c:257 | `INVALID_SOCKET` means offline (`net__is_connected`, net_mosq.c:197-203) |
| `maximum_packet_size` | 309 | conn | property_broker.c:54 | client's Maximum Packet Size. 0 means unlimited (packet_mosq.c:237). Used for outgoing packets only |
| `last_cmsg_id` | 313 | **session** | database.c:522, 671 (`++`); handle_connect.c:130 | per-client message id counter for persistence. Copied only when the old session had messages |
| `protocol` | 317 | conn (also stays on the offline struct) | handle_connect.c:516-546 | |
| `address`, `remote_port` | 318, 464 | conn | context.c:45-49 | |
| `id` | 319 | key of `contexts_by_id` | handle_connect.c:1192, 1035 | freed on auth failure: handle_connect.c:1235-1243, handle_auth.c:151-167 |
| `username`, `password` | 320-321 | conn (left on the offline struct, not transferred) | handle_connect.c:1021-1022, 915, 961 | freed in context__cleanup (context.c:178-179) |
| `id_hashv` | 322 | conn | context.c:355, 374 | |
| `keepalive` | 323 | conn | context.c:86 (default 20); handle_connect.c:614, 260 | |
| `last_mid` | 324 | **session** | util_mosq.c:137-139; handle_connect.c:148; persist_read.c:76 | outgoing packet-id generator |
| `state` | 325 | conn | `mosquitto__set_state` (util_mosq.c:240-252); cannot leave `mosq_cs_disused` (util_mosq.c:244) | |
| `transport` | 326 | conn | net.c:206-219 | |
| `last_msg_in` | 327 | conn | context.c:84; keepalive.c:243 | keepalive ring index |
| `next_msg_out` | 328 | conn | context.c:85; packet_mosq.c:347 | read only in `mosquitto__check_keepalive` (bridges) |
| `ping_t` | 329 | conn | handle_connect.c:241; send_mosq.c:52; handle_ping.c:77 | bridge pings |
| `in_packet` | 330 | conn | packet_mosq.c:471-642 | parser state plus `packet_buffer` (size `db.config->packet_buffer_size`, default 4096: context.c:70, conf.c:353) |
| `out_packet`, `out_packet_last`, `out_packet_count`, `out_packet_bytes` | 331, 393, 340-341 | conn | packet_mosq.c:146-174, 249-269; context.c:129-146 | outgoing packet FIFO |
| `will` | 332 | session when will-delay > 0, otherwise conn | handle_connect.c:1217; will_mosq.c:124-139 | |
| `aliases_l2r`, `aliases_r2l`, `alias_count_*` | 333-337 | conn | alias_mosq.c; freed at context.c:173, 298 | |
| `alias_max_l2r` | 338 | conn | property_broker.c:62-65 | 0 unless the client sent Topic Alias Maximum (calloc) |
| `will_delay_entry`, `will_delay_interval`, `will_delay_time` | 335, 339, 342 | session | property_broker.c:119; will_delay.c:51-52 | |
| `want_write` | 369 | conn | net.c:258 | TLS |
| `clean_start` | 380 | conn flag, read when the session ends | handle_connect.c:1216; context.c:87, 162 | |
| `session_expiry_time` | 381 | session | session_expiry.c:46-60, 124 | |
| `session_expiry_interval` | 382 | session | handle_connect.c:601/603; property_broker.c:41, 225; handle_connect.c:109, 186, 1312; session_expiry.c:155, 196 | |
| `in_by_id` | 384 | table membership | context.c:354, 375 | |
| `is_dropping` | 385 | conn | database.c:494, 632, 646; packet_mosq.c:152; reset at handle_connect.c:242 | |
| `is_bridge` | 386 | conn | handle_connect.c:536, 543 | |
| `is_persisted` | 387 | **session** | handle_connect.c:112-113 | |
| `bridge` | 388 | config (outgoing bridges) | bridge.c | |
| `msgs_in`, `msgs_out` (`struct mosquitto_msg_data`, mosquitto_internal.h:234-255) | 389-390 | **session** (lists and counters); `inflight_maximum`/`inflight_quota` are per connection | see §3 | |
| `acl_list` | 391 | conn | context.c:94 | |
| `listener` | 392 | conn | net.c:192 | |
| `subs`, `subs_capacity`, `subs_count` | 394, 396-397 | **session** | subs.c:243-312, 382-424, 747-797; handle_connect.c:142-147 | |
| `auth_method` | 395 | conn | handle_connect.c:1136; freed at context.c:177 | |
| `pollfd_index` | 399 | conn | mux_poll.c:126-181 | |
| `assigned_id` | 406 | conn | handle_connect.c:706 | |
| `max_qos` | 452 | conn | context.c:120; handle_connect.c:254 | |
| `retain_available` | 453 | conn | context.c:95 | |
| `hh_id`, `hh_sock` | 460-461 | table hooks | | |
| `for_free_next` | 462 | free list | context.c:313 | |
| `expiry_list_item` | 463 | session | session_expiry.c:89, 141 | |
| `keepalive_next`, `keepalive_prev`, `keepalive_add_time` | 466-468 | conn | keepalive.c:133-134, 227-229 | |
| `stats` (`messages_received/sent/dropped`) | 470 | conn (not transferred) | handle_publish.c:224; database.c:575, 500 | |
| `events` | 473 | conn | mux_epoll.c:103, 122, 140 | |
| `proxy` | 479 | conn | net.c:210 | |

### Exact list of what moves from the old struct to the new one (`src/handle_connect.c`)

- **Always, when the id exists:**
  - `is_persisted` (112-113)
  - the old session's will is published at 178 when the session is not resumable or the new connection asked for clean start
  - `session_expiry__remove`, `will_delay__remove` and `will__clear` on the old struct (181-183)
- **Only when `clean_start == false` and the old `session_expiry_interval != 0`:**
  - CONNACK session-present bit, for v3.1.1 and v5 only (117)
  - when any of the old struct's four message lists is non-empty: `msgs_in`, `msgs_out` and `last_cmsg_id` are copied with `memcpy` (128-130); the old ones are zeroed (132-133); the new connection's quota and maximum are restored (135-138); then `db__message_reconnect_reset` runs (140)
  - `subs`, `subs_capacity`, `subs_count` (142-147)
  - `last_mid` (148)
  - every `subleaf->context` pointer in the normal and shared hierarchies is rewritten from the old struct to the new one (150-170)
- **When `clean_start == true`:**
  - `sub__clean_session(found_context)`
  - the old struct's `session_expiry_interval = 0`
  - `plugin_persist__handle_client_delete` (107-111)

The new struct does not inherit `username`, `will`, `stats`, `aliases`, `keepalive` or `max_qos`.

---

## 2. Global context tables and the functions that use them

All globals live in `struct mosquitto_db db` (`src/mosquitto_broker_internal.h:464-513`; declared at line 638).

| Table | Kind | Key | Insert | Remove | Read / iterate |
|---|---|---|---|---|---|
| `db.contexts_by_id` | uthash `hh_id` | `id` string | `context__add_to_by_id` context.c:351-358 (guarded by `in_by_id`) ← handle_connect.c:247; persist_read.c:73; plugin_public.c:382, 541; bridge.c:91 | `context__remove_from_by_id` context.c:361-383 ← context.c:192 (cleanup), 311 (add_to_disused); loop.c:305 (LWS duplicate); plugin_public.c:377 | handle_connect.c:96 (takeover lookup), 194 (`HASH_CNT` vs `global_max_clients`); loop.c:151 (plugin messages addressed to one client); plugin_public.c:76, 360, 440, 477, 502, 567, 612, 649, 682, 706, 730, 752, 771; persist_read.c:59; sys_tree.c:256-259; mosquitto.c:343, 361 (shutdown) |
| `db.contexts_by_sock` | uthash `hh_sock` | `sock` int | context.c:56 (`context__init_sock`); bridge.c:389, 608; websockets.c:187 | net_mosq.c:248-251 (`net__socket_close`); loop.c:294 (LWS); websockets.c:198 | `HASH_CNT` vs `global_max_connections` net.c:236, websockets.c:175; keepalive.c:94 (reload), 150 (`if(db.contexts_by_sock)`), 198 (old keepalive); mux_poll.c:269, 323 (every poll iteration); plugin_public.c:435, 456, 462; bridge.c:766; websockets.c:612-635; sys_tree.c:258-259; mosquitto.c:370 |
| `db.contexts_by_id_delayed_auth` | uthash `hh_id` (same hook as by_id, so a context is in only one of them) | `id` | handle_connect.c:1260 (on `MOSQ_ERR_AUTH_DELAYED`) | plugin_public.c:870 (`mosquitto_complete_basic_auth`); context.c:379-382 | handle_connect.c:1210 (a second CONNECT with the same id while auth is pending is rejected with `MOSQ_ERR_UNKNOWN`); context.c:264 (disconnect while pending goes straight to the disused list) |
| `db.ll_for_free` | singly linked list via `for_free_next` | none | `context__add_to_disused` context.c:303-315 ← context.c:267, 290; session_expiry.c:202; will_delay.c:98 | `context__free_disused` context.c:318-348, called once per loop (loop.c:201) and at shutdown (mosquitto.c:377) | |
| `db.contexts_for_free` | declared | | set to NULL at database.c:192 only; nothing else uses it in src/ or lib/ (grep) | | |
| session-expiry list | `static struct session_expiry_list *expiry_list` (session_expiry.c:28), doubly linked, sorted by `session_expiry_time` (`DL_INSERT_INORDER`, :91, :131) | | `session_expiry__add` :64-96 ← context.c:293; `session_expiry__add_from_persistence` :99-134 ← persist_read.c:217, plugin_public.c:542 | `session_expiry__remove` :137-143 ← handle_connect.c:181, session_expiry.c:154, 188 | `session_expiry__check` :163-212 |
| will-delay list | `static struct will_delay_list *delay_list` (will_delay.c:27), sorted by `will_delay__cmp`, which compares **`will_delay_interval`**, not `will_delay_time` (:31-34) | | `will_delay__add` :37-60 ← context.c:220 | `will_delay__remove` :109-115 ← handle_connect.c:182, session_expiry.c:157, 199; deleted in place in `will_delay__check` :93, `will_delay__send_all` :69 | `will_delay__check` :78-106 |
| keepalive ring | `static struct mosquitto **keepalive_list`, `keepalive_list_max` (keepalive.c:62-63). Size is `max_keepalive*3/2+1`, or 98303 when `max_keepalive` is 0 (the default, conf.c:337) (:81-86) | slot `(last_msg_in + keepalive*3/2) % max` (:71) | `keepalive__add` :121-139 ← net.c:274, handle_connect.c:261, 619, keepalive.c:96, 244 | `keepalive__remove` :216-235 ← context.c:174, 296, handle_connect.c:259, 612, keepalive.c:241 | `keepalive__check` :145-179 |
| `g_listensock[]`, `g_listensock_count` | array | | listeners.c:88-102, 134-144 | listeners.c:336-342 | mux add/delete; mux_poll.c:235-248 |
| subscription tree | `db.normal_subs`, `db.shared_subs`; leaves point back to the context (`subleaf->context`) | | sub__add | sub__remove, sub__clean_session | subs__send (subs.c:68-108) |

Note on the delayed-auth path: there is no `in_by_id` flag for this table. `context__remove_from_by_id` falls through to it when `in_by_id == false` (context.c:379).

### 2.1 CONNECT flow (`handle__connect`, handle_connect.c:1051-1315)

1. **Accept** (net.c:115-277).
   - `accept`. On EMFILE/ENFILE it closes the spare socket, accepts and immediately closes the new one, then re-opens the spare (:133-150).
   - `metrics socket_connections++` (:154).
   - `context__init` (:187), `listener->client_count++` (:197).
   - `context__init_sock`, which inserts into `contexts_by_sock` (:200/:212).
   - Rejects when `listener->client_count > max_connections` or `HASH_CNT(contexts_by_sock) > global_max_connections`; cleanup decrements the count (:235-243).
   - TLS setup; `mux__new` (:273); `keepalive__add` with the default keepalive of 20 (:274).
2. `packet__read_single` rejects any first packet that is not CONNECT while `state == mosq_cs_new` (packet_mosq.c:490-493). Size limits are checked when the remaining length completes (packet_mosq.c:400-467): `packet_max_connect` defaults to 100000 (conf.c:356), and `max_packet_size` defaults to 2000000 (conf.c:338).
3. Checks the context state: a second CONNECT is a protocol error (1075-1080). Then:
   - protocol name and version (1091-1102)
   - flags (1104)
   - `clean_start` and the default `session_expiry_interval` (1109-1110: v3 with clean_start=0 gives NEVER (UINT32_MAX), otherwise IMMEDIATE (0); mqtt_protocol.h:298-299)
   - keepalive: remove, read, add (608-622)
   - v5 properties into `property__process_connect` (property_broker.c:32-75). These set `session_expiry_interval`, `msgs_out.inflight_maximum/quota` (Receive Maximum, 0 is a protocol error), `maximum_packet_size` (0 is a protocol error), and `alias_max_l2r` capped by `listener->max_topic_alias_broker` (default 10, listeners.c:35).
4. Will flags and options (1122-1134); `auth_method`/`auth_data` (1136-1146).
5. Client id (724-753).
   - **Auto id:** a zero-length id is refused for MQTT 3.1. For 3.1.1 it is refused with clean_start=0. It is also refused if `allow_zero_length_clientid` is false. Otherwise `clientid_gen` builds the listener's `auto_id_prefix` plus a 36-character UUID-like hex string from 16 bytes of `mosquitto_getrandom` (50-82), and `assigned_id = true` (706).
   - **Prefix check:** `clientid_prefixes` (712-721).
6. Will body (334-439, payload capped by `message_size_limit`); username and password (802-831); trailing-bytes check (834-847).
7. `context->id = clientid` (1192). Username from certificate (974-1029). `use_username_as_clientid` replaces the id (1032-1048).
8. If the id is already pending delayed auth, reject (1210-1214).
9. `clean_start`, `will` committed (1216-1217).
10. **Authentication.**
    - With `auth_method`: `mosquitto_security_auth_start`. On SUCCESS, go to `connect__on_authorised`. On AUTH_CONTINUE, `state = mosq_cs_authenticating` and send AUTH (1220-1229). Further rounds are in `handle__auth` (handle_auth.c:118-171), which calls `connect__on_authorised` when the state is still `authenticating` (130-131).
    - Without it: `mosquitto_basic_auth`. On AUTH_DELAYED, `state = mosq_cs_delayed_auth` and the context goes into `contexts_by_id_delayed_auth` (1258-1261). Completion is `mosquitto_complete_basic_auth` (plugin_public.c:860-885); on failure it sends CONNACK and calls `do_disconnect(MOSQ_ERR_AUTH)`.
    - Any error path resets `clean_start = true`, `session_expiry_interval = 0` and `will_delay_interval = 0` (1311-1313), so the failed context is freed rather than kept.
11. **`connect__on_authorised`** (85-331):
    - takeover / resume as described in §1 (96-192). The old struct gets `clean_start = true`, `session_expiry_interval = 0` and `state = mosq_cs_duplicate`; a v5 DISCONNECT 0x8E (session taken over) is sent; then `do_disconnect(MOSQ_ERR_SESSION_TAKEN_OVER)` (185-191). `do_disconnect` leads to `context__disconnect`, which with interval 0 calls `context__add_to_disused`, which takes the old struct out of `contexts_by_id` synchronously (context.c:311). With LWS, the duplicate is removed from by_id inside `do_disconnect` instead (loop.c:298-306).
    - `global_max_clients` check against `HASH_CNT(contexts_by_id)`, which counts offline sessions too (194-200).
    - `ping_t = 0`, `is_dropping = false` (241-242).
    - `db__check_acl_of_all_messages` (246; database.c:1347-1353): drops any queued or inflight message the new credentials may not access.
    - `context__add_to_by_id` (247); `db.persistence_changes++` when not clean start (250-252); `max_qos = listener->max_qos` (254).
    - `max_keepalive` enforcement (256-272). For v5 the keepalive is replaced and a Server Keep Alive property is added. For v3 the broker answers CONNACK "identifier rejected" and fails.
    - CONNACK properties (275-301, plus send_connack.c:49-77): Topic Alias Maximum (`listener->max_topic_alias`), Assigned Client Identifier, auth method and data, Retain Available, Maximum Packet Size (`db.config->max_packet_size`), Receive Maximum (`db.config->max_inflight_messages`, default 20), Maximum QoS.
    - `state = mosq_cs_active` (304); `send__connack` (305).
    - `db__expire_all_messages` (310); `db__message_write_queued_out` (311); `db__message_write_inflight_out_all` (315). This is the resend on reconnect.
    - plugin connect event; persist client add, or persist will add (317-324).

### 2.2 DISCONNECT, will, session-expiry change

- **`handle__disconnect`** (handle_disconnect.c:30-87). Reads the v5 reason code and properties. `property__process_disconnect` (property_broker.c:209-230) applies a new Session Expiry Interval; changing it from 0 to non-zero is a protocol error.
  - If the reason is 0x04 (`MQTT_RC_DISCONNECT_WITH_WILL_MSG`): `state = mosq_cs_disconnect_with_will`, and the will is kept.
  - Otherwise: `will__clear` and `state = mosq_cs_disconnecting`.
  - Then `do_disconnect(MOSQ_ERR_SUCCESS)`.
- **`do_disconnect`** (loop.c:271-410): logs the reason, then `mux__delete`, then `context__disconnect`.
- **`context__disconnect`** (context.c:248-300):
  - returns if already `disconnected`
  - if the context is in the delayed-auth table, closes the socket and adds it to the disused list (262-270)
  - plugin `disconnect` event when interval is 0, otherwise `client_offline` (272-276)
  - **`context__send_will`** (278)
  - `net__socket_close` (279), which removes the context from `contexts_by_sock` and decrements `listener->client_count` unless the state is disused (net_mosq.c:246-258)
  - when not an outgoing bridge: interval 0 means persist client delete, then disused if `will_delay_interval == 0`; non-zero means `session_expiry__add` (280-295)
  - `keepalive__remove`, `state = disconnected`, `alias__free_all`, out-packet cleanup (296-299)
- **`context__send_will`** (context.c:216-245): does nothing when `state == mosq_cs_disconnecting` (that is, after a normal DISCONNECT).
  - If `will_delay_interval > 0`: `will_delay__add` and return. The will is kept.
  - Otherwise: ACL check with WRITE access, then `db__messages_easy_queue(ctxt, topic, qos, payload, retain, will->expiry_interval, &will->properties)` (database.c:858-918: store the message, then `sub__messages_queue`), then `will__clear`, which also zeroes `will_delay_interval` (will_mosq.c:136).
- **Kick (plugin API):** `disconnect_client` (plugin_public.c:415-427) sends v5 DISCONNECT 0x98 (administrative action), sets `disconnecting` unless `with_will`, then `do_disconnect(MOSQ_ERR_ADMINISTRATIVE_ACTION)`.

### 2.3 Keepalive timeout

`keepalive__check` (keepalive.c:145-179) runs every loop iteration.
- Sets the next mux timeout to the nearest non-empty slot within 5 s (150-161).
- Walks every slot from `last_keepalive_check` to `now-1`. Calls `do_disconnect(MOSQ_ERR_KEEPALIVE)` for a connected context whose `keepalive_add_time <= last_keepalive_check` (162-176).
- Every fully read packet calls `keepalive__update`, which removes the context, sets `last_msg_in = now` and re-adds it (packet_mosq.c:635). A partial read with more than 1000 bytes still pending also calls it (packet_mosq.c:596-603).
- Clients with keepalive 0 and outgoing bridges are not in the ring (keepalive.c:124-130).
- The timeout is `keepalive * 3 / 2` seconds after `last_msg_in`.
- An alternative O(n) scan exists under `WITH_OLD_KEEPALIVE` (183-212).
- Bridges get their own PINGREQ/timeout handling in `mosquitto__check_keepalive` (util_mosq.c:55-121), called from `bridge_check` (bridge.c:1043).

### 2.4 Session expiry

- `session_expiry__add` (session_expiry.c:64-96) skips sessions with interval NEVER when there is no global `persistent_client_expiration` (74-80).
- `set_session_expiry_time` (44-61): `session_expiry_time = now_real_s + min(interval, persistent_client_expiration)`, where a `persistent_client_expiration` of 0 means "use the interval".
- `session_expiry__check` (163-212) runs at most once per wall-clock second (169-180). It walks the sorted list. For each item with `session_expiry_time < now_real_s` it:
  - removes the item
  - counts `clients_expired`
  - sets interval 0 and `will_delay_interval = 0`
  - calls `will_delay__remove`
  - calls `context__send_will`, so a pending delayed will is published immediately
  - persists the client delete
  - adds the context to the disused list
- At the first item not yet due it sets the next timeout and returns.

### 2.5 Will delay

- `will_delay__add` (will_delay.c:37-60) sets `will_delay_time = now_real_s + interval` and inserts the item in order of `will_delay_interval`.
- `will_delay__check` (78-106) runs once per wall-clock second (82-89). It walks the list. For each item with `will_delay_time <= now`: it deletes the item, sets `interval = 0`, calls `context__send_will` (now with interval 0, so the will is published), and adds the context to the disused list if the session interval is 0. At the first item not yet due it returns.
- The list is sorted by interval, but the check stops at the first item whose *time* has not arrived. That is what the code does.
- The delayed will is cancelled by a reconnect with the same id (handle_connect.c:182-183) or by session expiry (session_expiry.c:199-200).
- At shutdown, every will with delay 0 is sent, then `will_delay__send_all` sends the delayed ones (mosquitto.c:343-346).

---

## 3. QoS state machines

Storage: `struct mosquitto__client_msg` (mosquitto_broker_internal.h:437-442) holds `data` (a `struct mosquitto_client_msg`, defined in the public header, not read here; it has the fields `cmsg_id, mid, qos, retain, dup, state, direction, subscription_identifier`) plus a pointer to a refcounted `struct mosquitto__base_msg` (426-435) in `db.msg_store`. Each context has `msgs_in` and `msgs_out`, each with `inflight` and `queued` doubly linked lists and the counters `inflight_count/_count12/_bytes/_bytes12` and `queued_*` (the `12` variants count QoS>0 only), `inflight_quota` and `inflight_maximum` (mosquitto_internal.h:234-255).

State enum: mosquitto_internal.h:102-118.

### 3.1 Incoming (client to broker)

`handle__publish` (handle_publish.c:205-399) then `handle__accepted_publish` (67-202).
- **Duplicate detection:** `db__message_store_find` searches `msgs_in.inflight` and `msgs_in.queued` by `source_mid` (database.c:1041-1066). If a stored message with that mid differs in QoS, length, topic or payload, it is dropped as "reused mid" (handle_publish.c:98-107). If it matches, `dup++` and the stored copy is used (138-144).
- **Receive maximum:** when `msgs_in.inflight_quota == 0` the broker only logs a warning. The disconnect code is under `#if 0` (110-120). The message is then accepted if `db__ready_for_flight(md_in)` (database.c:37-88). Otherwise `process_bad_message(QUOTA_EXCEEDED)` sends PUBACK/PUBREC with 0x97.
- **QoS 0:** `sub__messages_queue`; nothing is stored per client.
- **QoS 1:** `util__decrement_receive_quota` (154), `sub__messages_queue`, then PUBACK. `send__puback` increments the receive quota (send_mosq.c:81). The incoming QoS1 message is not kept in `msgs_in`.
- **QoS 2:** `db__message_insert_incoming` (database.c:468-555) creates a client_msg in `msgs_in.inflight` with state `wait_for_pubrel`, or in `queued` (return value 2) if not ready for flight. The message is dropped when the queue is full (`is_dropping`, `stats.messages_dropped++`). It decrements the receive quota (552). PUBREC is sent when `dup` is 0 or 1; `dup >= 2` is a protocol error (handle_publish.c:184-192).
- **PUBREL:** `handle__pubrel` (lib/handle_pubrel.c:41-156) calls `db__message_release_incoming` (database.c:1221-1281). That fans the message out with `sub__messages_queue(..., qos 2, ...)` and removes it from `inflight` (ref-dec frees the base message when the count reaches 0). It then walks `msgs_in.queued`. The loop breaks as soon as `db__ready_for_flight(md_in)` is **true** (1265-1267). Otherwise, for QoS 2 items, it sends PUBREC, sets `wait_for_pubrel` and dequeues to inflight (1269-1274). So a queued item is promoted here only while the incoming quota is *not* available. The condition looks inverted, but that is what the code does. The normal promotion path is `db__message_write_queued_in`, described next. PUBCOMP is sent even when the mid is not found (handle_pubrel.c:122-129). `send__pubcomp` increments the receive quota (send_mosq.c:95).
- `db__message_write_queued_in` (database.c:1523-1551) runs after every accepted PUBLISH (handle_publish.c:200). It promotes queued incoming QoS 2 items while quota is available, sending PUBREC.

### 3.2 Outgoing (broker to client)

- **Insert:** `subs__send` (subs.c:68-108) computes `msg_qos` (`upgrade_outgoing_qos`, or min(publish QoS, subscription QoS)) and a mid from `mosquitto__mid_generate` (util_mosq.c:124-145: `last_mid++`, skipping 0, with no check against mids already in flight). It then calls `db__message_insert_outgoing(update=true)` (database.c:558-745), which:
  - skips duplicates for non-v5 clients via `base_msg->dest_ids` when `allow_duplicate_messages` is false (586-596)
  - when offline, drops QoS 0 unless `queue_qos0_messages` (597-611)
  - when online and `db__ready_for_flight`, sets state `publish_qosN`; when online and `db__ready_for_queue`, sets state `queued`; when offline and ready for queue, sets `queued`; otherwise drops
  - appends the client_msg; `++last_cmsg_id`; `ref_count++`
  - appends the client id to `dest_ids` (700-719)
  - decrements the send quota for QoS>0 not queued (729-731)
  - then calls `db__message_write_inflight_out_latest` and `db__message_write_queued_out` (733-742)
- **Send:** `db__message_write_inflight_out_single` (1356-1454) dispatches on state:
  - expired messages are removed and the send quota is restored (1374-1385)
  - `publish_qos0`: send, then remove
  - `publish_qos1`: send, then `dup = 1` and `wait_for_puback`
  - `publish_qos2`: send, then `dup = 1` and `wait_for_pubrec`
  - `resend_pubrel`: send PUBREL, then `wait_for_pubcomp`
  - other states: nothing
  - `MOSQ_ERR_OVERSIZE_PACKET` (the client's Maximum Packet Size, packet_mosq.c:233-247 / send_publish.c:293-300) removes the message
- **PUBACK / PUBCOMP:** `handle__pubackcomp` (lib/handle_pubackcomp.c:41-174) calls `util__increment_send_quota` *before* parsing the mid and for every PUBACK/PUBCOMP, including unknown mids (63-65). It then calls `db__message_delete_outgoing(mid, wait_for_pubcomp, qos)` (database.c:419-464). That function matches by mid in `inflight` and then `queued`, checks QoS (and state for QoS 2), removes the message, calls `db__fill_inflight_out_from_queue`, `db.persistence_changes++`, and `write_inflight_out_latest`.
- **PUBREC:** `handle__pubrec` (lib/handle_pubrec.c:41-155).
  - Reason < 0x80: `db__message_update_outgoing(mid, wait_for_pubcomp, 2, persist=true)` (database.c:771-780; it searches `inflight` only when `persist` is true), then `send__pubrel`.
  - Reason ≥ 0x80: `db__message_delete_outgoing(mid, wait_for_pubrec, 2)` and return. The broker path does **not** call `util__increment_send_quota` here (lines 121-125). The client-library path does (line 137).
- **Queue to inflight:** `db__fill_inflight_out_from_queue` (372-398) moves messages from the head of `queued` while `db__ready_for_flight`. `db__message_dequeue_first` (401-416) decrements `inflight_quota`.

### 3.3 Limits

- `db__ready_for_flight` (database.c:37-88):
  - QoS>0: `inflight_quota > 0`, and/or `inflight_bytes12 < max_inflight_bytes`
  - QoS 0 outgoing: `out_packet_count < max_queued_messages` and/or a bytes test
- `db__ready_for_queue` (99-137): `queued_count12 - inflight_maximum < max_queued_messages` (the `inflight_maximum` adjustment is 0 when offline) and/or a bytes test.
- Defaults (conf.c:339-342): `max_inflight_messages = 20`, `max_queued_messages = 1000`, both byte limits 0.
- `msgs_in.inflight_maximum` always comes from config (context.c:116-117). `msgs_out.inflight_maximum` comes from the client's Receive Maximum (property_broker.c:45).
- **Outgoing packet-queue cap:** `packet__queue_append` (packet_mosq.c:146-174) drops any packet, of any type, once `out_packet_count >= max_queued_messages`.
- `process_bad_message` returns `MQTT_RC_QUOTA_EXCEEDED` (0x97 = 151) when the same condition holds (handle_publish.c:60-62). That non-zero value reaches `packet__read` and causes `do_disconnect`.

### 3.4 Freeing

- client_msg: `db__message_remove_inflight/queued` (database.c:336-369), `db__messages_delete_list` (783-793), the ACL purge (1318-1344), and expiry (1284-1315).
- base_msg: `db__msg_store_ref_dec` frees it when `ref_count` reaches 0 (314-321); `db__msg_store_remove` also adjusts `msg_store_count/bytes` (283-295).
- **Per-context drop:** `db__messages_delete(context, force_free)` (838-855) is called from `context__cleanup`. Incoming messages are deleted when force_free, clean_start, or the bridge has clean_start. Outgoing messages are deleted when force_free, the bridge has clean_start_local, or (not a bridge and clean_start).

### 3.5 Resend on reconnect

There is no timer-based retry. `retry_interval` is `OPTION_UNAVAILABLE` (conf.c:2575-2576). Resend happens only on session resume:
- `db__message_reconnect_reset_outgoing` (database.c:1071-1120) zeroes the counters, sets `quota = maximum`, then re-counts every inflight message (decrementing quota for QoS>0) and resets state: QoS 1 goes to `publish_qos1`; QoS 2 goes to `resend_pubrel` if it was waiting for PUBCOMP, otherwise to `publish_qos2`. `dup` stays at 1. Then `fill_inflight_out_from_queue`.
- `db__message_reconnect_reset_incoming` (1124-1182) drops non-QoS 2 inflight messages, keeps QoS 2 with `dup = 0`, and promotes queued messages that are ready.
- After CONNACK, `write_queued_out` and `write_inflight_out_all` send them (handle_connect.c:311-315).
- `last_mid` continues from the old session (handle_connect.c:148).

### 3.6 Topic alias

- **Inbound (r2l):** the limit is `listener->max_topic_alias` (default 10). Alias 0 or above the limit gives `TOPIC_ALIAS_INVALID` (handle_publish.c:300-302). A topic plus alias calls `alias__add_r2l`, which replaces an existing mapping (alias_mosq.c:58-91). An empty topic plus alias calls `alias__find_by_alias`.
- **Outbound (l2r):** `send__real_publish` (send_publish.c:229-240) looks the topic up with `alias__find_by_topic`. If found, it sends no topic. Otherwise `alias__add_l2r` assigns the next number while `alias_count_l2r < alias_max_l2r` (alias_mosq.c:30-55). Mappings are never replaced.
- Both tables are freed on disconnect (context.c:298) and are not carried with the session.

---

## 4. Time-driven work in the main loop

`mosquitto_main_loop` (loop.c:175-268). Each iteration, in this order:

| # | Call | What it touches | Rate |
|---|---|---|---|
| 1 | `retain__expiry_check` (retain.c:411-417) | `db.retains` | every `retain_expiry_interval` (`db.now_s > next_expire_check`) |
| 2 | `queue_plugin_msgs` (loop.c:139-164) | `db.plugin_msgs`; for per-client messages, `contexts_by_id` then `single_publish`, which uses `mosquitto__mid_generate` and `db__message_insert_outgoing` | every iteration |
| 3 | `context__free_disused` (context.c:318-348) | `db.ll_for_free`, then `context__cleanup(force_free=true)` for each | every iteration |
| 4 | `db.next_event_ms = 86400000` (loop.c:203) | mux timeout reset | every iteration |
| 5 | `sys_tree__update(false)` (sys_tree.c:223-) | publishes `$SYS` messages via `db__messages_easy_queue`; reads `HASH_CNT` of by_id and by_sock | when `now_real_s % sys_interval == 0` (default 10 s, conf.c:351) |
| 6 | `keepalive__check` | keepalive ring, then `do_disconnect` | every iteration; walks every second elapsed |
| 7 | `watchdog__check` | systemd `sd_notify` | every `WATCHDOG_USEC/2` |
| 8 | `bridge_check` (bridge.c:1022-1179) | `db.bridges[]`, bridge keepalive (`mosquitto__check_keepalive`), primary-retry every 5 s, reconnect backoff | once per monotonic second (`static last_check`, :1024-1033; `last_check` is never assigned in the lines read) |
| 9 | `plugin__handle_tick` (plugin_tick.c:58-71) | plugin tick callbacks | every iteration (each plugin's own `next_tick` gating was not read) |
| 10 | `session_expiry__check` | expiry list | once per wall-clock second |
| 11 | `will_delay__check` | will-delay list | once per wall-clock second |
| 12 | `mux__handle`: `epoll_wait(..., db.next_event_ms)`, or a fixed 100 ms when `WITH_WEBSOCKETS` (mux_epoll.c:164-168) | then `db.now_s = mosquitto_time()` (monotonic: CLOCK_BOOTTIME or CLOCK_MONOTONIC, time_common.c:47-76) and `db.now_real_s = time(NULL)` (mux_epoll.c:170-171) | blocks |
| 13 | autosave (loop.c:225-239) | `persist__backup`; `db.persistence_changes` reset | after `autosave_interval` changes or seconds |
| 14 | `signal__flag_check` (signals.c:95-152) | backup, log rotate, reload (re-reads config, security, **`keepalive__cleanup`/`keepalive__init`**, which rebuilds the ring from `contexts_by_sock`, keepalive.c:94-98), tree print | when flags are set |
| 15 | `lws_service` for each websocket listener (LWS build only) | | every iteration |

`loop__update_next_event` (loop.c:167-172) only ever lowers `db.next_event_ms`.

Message expiry is lazy: it is checked when a message is sent (`write_inflight_out_single` database.c:1374), on connect (`db__expire_all_messages`), and in `fill_inflight_out_from_queue` (391).

---

## 5. Read-modify-write counters and globals

**`db` fields:**
- `last_db_id` (database.c:976-979)
- `msg_store_count`, `msg_store_bytes` (1024-1025 `++`/`+=`; 289-290 `--`/`-=`)
- `subscription_count`, `shared_subscription_count` (subs.c:264, 315, 374, 409, 763, 775)
- `retained_count` (retain.c, not read)
- `persistence_changes` (handle_connect.c:251; handle_subscribe.c:280; handle_unsubscribe.c:172; database.c:460, 509, 658; reset at loop.c:230)
- `next_event_ms` (loop.c:170, 203)
- `now_s`, `now_real_s` (mux_*.c, loop.c:188-189)
- `ll_for_free`; `shutdown` (mosquitto.c:349)

**Per listener:** `client_count` (`++` net.c:197, websockets.c:173; `--` net_mosq.c:241, 253, websockets.c:180).

**Per context:**
- `last_mid` (util_mosq.c:137); `last_cmsg_id` (database.c:522, 671)
- `msgs_*.inflight_quota` (util_mosq.c:208-237; database.c:410-411, 1083, 1136)
- `msgs_*.inflight_count/_count12/_bytes/_bytes12/queued_*` (database.c:140-181, 804-832, 1075-1082, 1128-1135)
- `out_packet_count`, `out_packet_bytes` (packet_mosq.c:169-170, 255-256; context.c:144-145)
- `stats.messages_received/sent/dropped` (handle_publish.c:224; database.c:575, 500)
- `subs_count`, `subs_capacity` (subs.c)
- `alias_count_l2r/r2l` (alias_mosq.c:41, 82)
- `in_packet.*` parser counters
- `keepalive_add_time`; `last_msg_in`

**Per message:** `base_msg->ref_count` (database.c:310, 316); `base_msg->dest_id_count` (711); `client_msg->data.dup++` (handle_publish.c:142).

**Metrics:** `metrics[m].next += / -=` (sys_tree.c:201-213), gauges `out_packets`/`out_packet_bytes` and counters (full list from grep: read_handle.c:43-100, net.c:154, database.c:499/637/644, packet_mosq.c:128-172/257-258/306/332/373/590/627, send_*.c, session_expiry.c:193, context.c:142-143). `metrics[i].current = next` happens in sys_tree.c:284.

**Derived counts (not stored):** `HASH_CNT(contexts_by_id)` (total clients, includes offline sessions) and `HASH_CNT(contexts_by_sock)` (connected), used for `global_max_clients`/`global_max_connections` and `$SYS` (sys_tree.c:256-259).

**File-scope statics:**
- keepalive.c:60-63 (`last_keepalive_check`, ring)
- session_expiry.c:28-29
- will_delay.c:27-28
- mux_epoll.c:43 (`ep_events`)
- mux_poll.c:59-60 (`pollfds`, `pollfd_max`, `pollfd_current_max`)
- net.c:75 (`spare_sock`)
- listeners.c:23 (`listensock_index`); `g_listensock`, `g_listensock_count`
- sys_tree.c:144-145, 229
- retain.c:31
- bridge.c:1024
- subs.c:64-65
- signals.c:36-42 (flags)
- mosquitto.c:62 `g_run`

---

## 6. Threads and locks

- **The broker core is single-threaded.** Every `COMPAT_pthread_mutex_lock/unlock` in shared lib code (`state_mutex`, `mid_mutex`, `out_packet_mutex`, `msgtime_mutex`, `msgs_out.mutex`) compiles to nothing when `WITH_BROKER` is defined (lib/pthread_compat.h:4, 22-25). The mutex fields themselves exist only under `WITH_THREADING && !WITH_BROKER` (mosquitto_internal.h:371-379). keepalive.c:242 carries the comment "broker is single threaded".
- **Main thread:** everything in §2 to §5. The accept, read, handle and write path for every client runs inside `mux__handle` on this thread. `packet__queue` writes synchronously (packet_mosq.c:211) and arms EPOLLOUT only when the socket would block (packet_mosq.c:294, 355-357).
- **Signal handlers** (signals.c:45-71) write `g_run` (plain `int`, mosquitto.c:62) and plain `static bool` flags. The main loop acts on them in `signal__flag_check`. There is no lock and no `sig_atomic_t`.
- **Windows only:** `SigThreadProc` (signals.c:90, 175-) is a second thread that writes the same flags and calls `handle_signal`.
- **HTTP API** (`WITH_HTTP_API`): libmicrohttpd starts with `MHD_USE_AUTO_INTERNAL_THREAD` (http_api.c:451). Its handler runs on MHD's thread(s), not the main loop, and without any lock it:
  - reads `db.config->listeners[]`, `db.config->security_options` and `metrics[i].current`
  - calls `broker_uptime()`, which reads `db.now_s` (sys_tree.c:148-151)
  - calls `mosquitto_basic_auth` and `mosquitto_acl_check` on a stack-built `struct mosquitto` (http_api.c:350-378, 165-265)

  The plugin and auth code those calls enter was not read, so I cannot say what shared state they reach.
- **Plugins:** `mosquitto_complete_basic_auth` (plugin_public.c:860-885) modifies `contexts_by_id_delayed_auth` and calls `connect__on_authorised` with no lock. Whether any plugin calls it from another thread depends on the plugin; plugin code was not read.
- **libwebsockets (LWS build):** `lws_service` is called from the main loop (loop.c:247-263), so its callbacks (websockets.c) run on the main thread. websockets.c itself was not read beyond grep.
