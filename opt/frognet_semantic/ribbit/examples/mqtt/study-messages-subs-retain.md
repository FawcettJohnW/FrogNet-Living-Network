# Mosquitto 2.1.2 broker: shared state for messages, subscriptions and retained messages

Source tree: `scratchpad/inc/mosquitto-2.1.2`. All references are `file:line` into that tree. Only executable code was used. Where something was not read, the text says so.

Files read in full: `src/database.c`, `src/subs.c`, `src/retain.c`, `src/topic_tok.c`, `src/sys_tree.c`, `src/mosquitto_broker_internal.h`, `src/handle_publish.c`, `src/handle_subscribe.c`, `src/property_broker.c`.
Files read in part: `src/handle_unsubscribe.c` (120-175), `src/handle_connect.c` (95-170, 235-320), `src/context.c` (150-260), `src/loop.c` (60-300), `src/plugin_public.c` (205-300, 395-415, 640-860), `src/plugin_message.c`, `src/http_api.c` (236-460), `src/websockets.c` (200-230), `src/persist_read.c` (140-160, 350-370), `src/sys_tree.h`, `src/signals.c` (grep only), `src/conf.c` (defaults only), `lib/send_publish.c` (40-340), `lib/packet_mosq.c` (120-265), `lib/util_mosq.c` (124-245), `lib/handle_pubackcomp.c`, `lib/handle_pubrec.c`, `lib/handle_pubrel.c` (90-140), `lib/pthread_compat.h`, `lib/mosquitto_internal.h` (struct fields), `include/mosquitto/broker.h` (struct defs).
Not read: `src/persist_write*.c`, `src/plugin_persist.c`, `src/bridge.c` (only grep hits), `src/session_expiry.c`, `src/will_delay.c`, `src/mux*.c`, `src/net.c`, `libcommon/topic_common.c` (the broker's own matching does not call it; see §2.4), `lib/alias_mosq.c`, any plugin under `plugins/`.

---

## 1. Structs that hold shared state

### 1.1 The one global: `struct mosquitto_db db`
Defined `src/mosquitto.c:57`, declared `extern` at `src/mosquitto_broker_internal.h:638`, type at `mosquitto_broker_internal.h:464-513`.

| Field | Line | Meaning / owner |
|---|---|---|
| `last_db_id` | 465 | Last store id issued; RMW in `db__new_msg_id` (`database.c:976-979`) |
| `node_id_shifted` | 466 | Top 10 bits of store ids (`database.c:957`) |
| `normal_subs` | 467 | uthash root of the normal subscription tree (`struct mosquitto__subhier`) |
| `shared_subs` | 468 | uthash root of the `$share` subscription tree |
| `retains` | 469 | uthash root of the retained tree (`struct mosquitto__retainhier`) |
| `contexts_by_id` / `contexts_by_sock` | 470-471 | client hash tables (`hh_id`, `hh_sock`); read by sys_tree for client counts (`sys_tree.c:256-259`) |
| `msg_store` | 481 | uthash table of all `struct mosquitto__base_msg`, keyed by `data.store_id` |
| `now_s`, `now_real_s` | 482-483 | monotonic / wall clock seconds; `now_real_s` is the expiry clock |
| `next_event_ms` | 485 | mux timeout; RMW in `loop__update_next_event` (`loop.c:167-172`) |
| `msg_store_count`, `msg_store_bytes` | 486-487 | store gauges |
| `subscription_count`, `shared_subscription_count`, `retained_count` | 493-495 | `$SYS` gauges (only with `WITH_SYS_TREE`) |
| `persistence_changes` | 497 | autosave counter |
| `plugin_msgs` | 505 | DL list of `struct mosquitto__message_v5` queued by plugins via `mosquitto_broker_publish` |
| `config` | 489 | `struct mosquitto__config *`; read-only on the hot path (options listed in §4.4) |

### 1.2 Message store entry: `struct mosquitto__base_msg` (`mosquitto_broker_internal.h:426-435`)
- `UT_hash_handle hh` (427): membership in `db.msg_store`.
- `struct mosquitto_base_msg data` (428; type at `include/mosquitto/broker.h:90-105`): `store_id`, `expiry_time` (absolute, `now_real_s` based), `topic`, `payload`, `source_id`, `source_username`, `properties` (only the forwarded set, see §2.1), `payloadlen`, `source_mid`, `source_port`, `qos`, `retain`.
- `source_listener` (429): pointer into `db.config->listeners`.
- `dest_ids` / `dest_id_count` (430-431): heap array of client-id strings this message was already sent to (only grown when `allow_duplicate_messages == false`, `database.c:700-719`).
- `ref_count` (432): the only lifetime control (see §3.1).
- `origin` (433): `mosq_mo_client` or `mosq_mo_broker`.
- `stored` (434): set only by `mosquitto_persist_base_msg_add` (`plugin_public.c:825`). No reader found in the files read.

Ownership: the store owns the struct and all its heap fields. Freed only by `db__msg_store_free` (`database.c:266-280`), which frees `source_id`, `source_username`, every `dest_ids[i]`, `topic`, `properties`, `payload`, then the struct.

### 1.3 Per-client message reference: `struct mosquitto__client_msg` (`mosquitto_broker_internal.h:437-442`)
- `data` (`include/mosquitto/broker.h:107-120`): `cmsg_id`, `store_id` (not set by the insert functions), `subscription_identifier`, `mid`, `qos` (effective, after downgrade), `retain` (effective), `dup`, `direction`, `state`.
- `prev`/`next`: utlist DL links.
- `base_msg`: counted reference into the store.
Owned by exactly one list in one context; freed with `mosquitto_FREE` in the remove functions.

### 1.4 Per-client lists and counters: `struct mosquitto_msg_data` (`lib/mosquitto_internal.h:234-255`, broker branch)
Each `struct mosquitto` has `msgs_in` and `msgs_out` (`lib/mosquitto_internal.h:389-390`).
- `inflight`, `queued`: DL lists of `mosquitto__client_msg`.
- `inflight_bytes`, `inflight_bytes12`, `inflight_count`, `inflight_count12`, `queued_bytes`, `queued_bytes12`, `queued_count`, `queued_count12`: running totals ("12" = QoS 1 and 2 only).
- `inflight_quota`, `inflight_maximum`: receive-maximum flow control. Initialised from `max_inflight_messages` (`context.c:116-119`); `msgs_out.inflight_maximum` overwritten from client CONNECT Receive Maximum (`property_broker.c:45-50`).

Other per-context fields used in this area (`lib/mosquitto_internal.h`): `last_cmsg_id` (313), `last_mid` (324), `id` (319), `username` (320), `protocol` (317), `state` (325), `out_packet`/`out_packet_last`/`out_packet_count`/`out_packet_bytes` (331, 393, 340-341), `is_dropping` (385), `is_persisted` (387), `clean_start` (380), `bridge` (388), `listener` (392), `subs`/`subs_capacity`/`subs_count` (394-397), `max_qos` (452), `stats` (470; `struct client_stats` 294-298: `messages_received`, `messages_sent`, `messages_dropped`).

### 1.5 Subscription tree
- `struct mosquitto__subhier` (`mosquitto_broker_internal.h:390-398`): `hh` (uthash in parent's `children` or in `db.normal_subs`/`db.shared_subs`), `parent`, `children` (uthash head), `subs` (DL list of normal leaves), `shared` (uthash of `mosquitto__subshared` by share name), `topic_len`, `topic[]` (one level).
- `struct mosquitto__subshared` (384-388): `hh` (in `subhier->shared`), `subs` (DL list of leaves — the round-robin ring), `name[]`.
- `struct mosquitto__subleaf` (400-409): `prev`/`next`, `context` (raw pointer to the subscribing client, not counted), `hier` (back-pointer), `shared` (back-pointer or NULL), `identifier` (MQTT5 subscription identifier), `subscription_options` (QoS bits + NL/RAP/RH), `topic_filter[]` (full original filter string incl. `$share/...`).
- Each leaf is referenced twice: from the tree list and from `context->subs[i]`. The `context->subs` array is the client's index into the tree; it is a sparse array (NULL holes are reused, `subs.c:243-250`, `294-301`).
- Tree roots created at init (`subs.c:832-846`): `""` in `shared_subs`; `""` and `"$SYS"` in `normal_subs`.
- Tree shape: `sub__topic_tokenise` prepends `""` for non-`$` topics (`topic_tok.c:91-94`). `sub__add` finds the root node by `topics[0]` (`subs.c:589-611`) and then `sub__add_context` walks `subhier->children` starting again at `topics[0]` (`subs.c:336-351`). So topic `a/b` lives at `normal_subs[""]->children[""]->children["a"]->children["b"]`. `sub__messages_queue`/`sub__search` use the same double-level convention (`subs.c:677-679`, `479`), so they agree.
- Static file-scope globals: `hashv_plus`, `hashv_hash` (`subs.c:64-65`), set once in `sub__init` (`subs.c:834-835`).

### 1.6 Retained tree: `struct mosquitto__retainhier` (`mosquitto_broker_internal.h:417-424`)
`hh`, `parent`, `children` (uthash), `retained` (counted reference to a `base_msg`), `topic_len`, `topic[]`. Roots `""` and `"$SYS"` created by `retain__init` (`retain.c:56-71`). Same double-first-level shape as the sub tree (`retain.c:153-171`).
Static: `next_expire_check` (`retain.c:31`).

### 1.7 `$SYS` metrics (`src/sys_tree.c`)
- `struct metric metrics[mosq_metric_max]` (`sys_tree.c:54-112`; type 40-45): `current`, `next`, `topic`, `topic_alias`, `is_max`. Global (non-static) and also read by `http_api.c:53,244-247`.
- `struct metric_load metric_loads[]` (`sys_tree.c:114-142`): exponentially decayed loads, `current`.
- Statics: `start_time`, `last_update` (`sys_tree.c:144-145`), function-static `last_update_real` (`sys_tree.c:229`).
- Enum of metric indexes: `src/sys_tree.h` (`mosq_metric_type`, `mosq_metric_load_type`).

### 1.8 Plugin publish queue: `struct mosquitto__message_v5` (`mosquitto_broker_internal.h:451-462`), list head `db.plugin_msgs`.

---

## 2. Functions that read/write this state

### 2.1 PUBLISH in -> store -> fan-out (control flow)

1. **`handle__publish`** (`src/handle_publish.c:205-399`), called from the packet dispatcher (not read).
   - RMW `context->stats.messages_received++` (224).
   - Allocates a fresh `base_msg` (226), fills `qos`, `retain`, `topic`, `source_mid` (232-281).
   - MQTT5 properties: `property__process_publish` (`property_broker.c:144-204`) moves only Content-Type, Correlation-Data, Payload-Format, Response-Topic, User-Property into `base_msg->data.properties` (155-175); reads Topic Alias (177-181) and Message-Expiry (183-187); rejects Subscription-Identifier unless from a bridge (189-195). The rest are freed (`handle_publish.c:298`).
   - Topic alias: `alias__add_r2l` / `alias__find_by_alias` (305, 311) — per-context alias table (not read).
   - Mount point prefix (337-349); `metrics__int_inc(mosq_counter_pub_bytes_received)` (336).
   - ACL WRITE check (371-384). `$CONTROL/` diverted (388-396).
   - -> `handle__accepted_publish`.
2. **`handle__accepted_publish`** (`handle_publish.c:67-202`)
   - `plugin__handle_message_in` (75; `plugin_message.c:108-126`) may replace topic/payload/properties/retain in place.
   - QoS>0: `db__message_store_find(context, source_mid)` (95; `database.c:1041-1066`) scans `msgs_in.inflight` then `msgs_in.queued` for a duplicate mid. Mismatch -> `db__message_remove_incoming` (105).
   - New message: `db__message_store` (127; `database.c:986-1038`): strdup `source_id`/`source_username`, set `source_listener`, `origin`, `expiry_time = now_real_s + interval` (1014-1020), RMW `db.msg_store_count++`, `db.msg_store_bytes += payloadlen` (1024-1025), `store_id = db__new_msg_id()` (1027-1029), `db__msg_store_add` -> `HASH_ADD` into `db.msg_store` (`database.c:250-263`). **ref_count is 0 at this point.**
   - Duplicate QoS2: free the new one, reuse stored, RMW `cmsg_stored->data.dup++` (142).
   - QoS0: `sub__messages_queue` (148).
   - QoS1: `util__decrement_receive_quota` (154; `lib/util_mosq.c:224-229`), `sub__messages_queue` (155), PUBACK (158/163).
   - QoS2: `db__message_insert_incoming` (175) (takes a ref) then PUBREC (185). Fan-out deferred to PUBREL.
   - Always `db__message_write_queued_in` (200; `database.c:1523-1551`).
3. **PUBREL** -> `handle__pubrel` (`lib/handle_pubrel.c:121`) -> **`db__message_release_incoming`** (`database.c:1221-1281`): finds mid in `msgs_in.inflight`, calls `sub__messages_queue(source_id, topic, 2, retain, &client_msg->base_msg)` (1253), then `db__message_remove_inflight` (1255); then promotes queued incoming QoS2 (1264-1275, sends PUBREC, `db__message_dequeue_first`).
4. **`sub__messages_queue`** (`subs.c:653-713`)
   - Tokenise topic (665).
   - `db__msg_store_ref_inc(*stored)` (673) — temporary guard ref.
   - Lookup root in `db.normal_subs`, `sub__search` (677-684); then same in `db.shared_subs` (686-693).
   - If `retain`: `retain__store(topic, *stored, split_topics, true)` (699-704). **Retain store happens after live fan-out.**
   - `db__msg_store_ref_dec(stored)` (710) — frees if nobody else took a ref (QoS0/1 message with no subscribers and not retained).
5. **`sub__search`** (`subs.c:470-539`), recursive: literal child (479), `+` child (499), `#` child with no children (520-532). Calls `subs__process` at the terminal level.
6. **`subs__process`** (`subs.c:133-158`): first `subs__shared_process` (139), then each normal leaf: skip if `!leaf->context->id` or no-local and `leaf->context->id == source_id` (143); `subs__send` (147).
7. **`subs__shared_process`** (`subs.c:111-130`): for each share group, sends to the **head** leaf, then **writes the list**: `DL_DELETE` + `DL_APPEND` the head to the tail (121-122). This is the round-robin state.
8. **`subs__send`** (`subs.c:68-108`): ACL READ check (76); QoS = sub QoS if `upgrade_outgoing_qos` else `min(pub, sub)` (82-90); `mosquitto__mid_generate(leaf->context)` if QoS>0 (92; RMW `context->last_mid`, `lib/util_mosq.c:124-145`); retain flag = publish retain only if RAP set (96-100); **`db__message_insert_outgoing(..., leaf->identifier, update=true, persist=true)`** (101).
9. **`db__message_insert_outgoing`** (`database.c:558-745`)
   - RMW `context->stats.messages_sent++` (575) — incremented before any drop decision.
   - Duplicate suppression for non-MQTT5 when `allow_duplicate_messages == false` and not retain: scans `base_msg->dest_ids` (586-596).
   - Offline: QoS0 dropped unless `queue_qos0_messages` (597-611).
   - Online: `db__ready_for_flight` (`database.c:37-88`) -> state `publish_qosN`; else `db__ready_for_queue` (`database.c:99-137`) -> `queued`; else drop: `context->is_dropping = true`, `metrics__int_inc(mosq_counter_mqtt_publish_dropped)` (613-653).
   - `db.persistence_changes++` when queued (657-659).
   - Allocates client_msg, `cmsg_id = ++context->last_cmsg_id` (670-671), **`db__msg_store_ref_inc`** (674), QoS capped to `context->max_qos` (679-683).
   - `DL_APPEND` to `msgs_out.queued` or `msgs_out.inflight` + stats (687-693).
   - Persist plugin hooks (695-698).
   - `dest_ids` realloc + strdup (700-719) — writes the shared `base_msg`.
   - Bridge lazy reconnect flag (721-726).
   - `util__decrement_send_quota` if QoS>0 and not queued (729-731).
   - If `update`: `db__message_write_inflight_out_latest` then `db__message_write_queued_out` (733-742).
10. **`db__message_write_inflight_out_latest`** (`database.c:1476-1520`): walks back from tail over messages in `publish_qos*` state, then forward calling `db__message_write_inflight_out_single`.
11. **`db__message_write_inflight_out_single`** (`database.c:1356-1454`): expiry check (1374-1386; expired -> `util__increment_send_quota`, remove, refill); computes remaining expiry interval; `send__publish` (1399/1408/1421); QoS0 -> remove from inflight (ref_dec) on success/oversize (1400-1402); QoS1 -> `dup=1`, `state=wait_for_puback` (1410-1411); QoS2 -> `state=wait_for_pubrec` (1423-1424); `resend_pubrel` -> PUBREL, `state=wait_for_pubcomp` (1433-1440).
12. **`send__publish`** (`lib/send_publish.c:44-207`): `plugin__handle_message_out` on a stack copy (67-118; may change topic/payload/qos/retain for this send only); clears retain if `!mosq->retain_available` (121-123); strips mount point (126-134); bridge remap (136-184); `metrics__int_inc(mosq_counter_pub_bytes_sent)` (177/187). `send__real_publish` (210-...): MQTT5 topic alias L2R (230-240, per-context), builds Message-Expiry, Topic-Alias, Subscription-Identifier properties (254-282), copies `store_props` (319), `metrics__int_inc(mosq_counter_mqtt_publish_sent)` (334).
13. **`packet__queue` / `packet__queue_append`** (`lib/packet_mosq.c:176-...`, `146-174`): drop if `out_packet_count >= max_queued_messages` (149-159, `metrics publish_dropped`); append to `out_packet` list, RMW `out_packet_count++`, `out_packet_bytes +=` and `metrics__int_inc(mosq_gauge_out_packets / bytes)` (162-172). Broker then calls `packet__write` immediately (210-211; not read).

### 2.2 Acks from subscriber (outgoing completion)
- PUBACK/PUBCOMP: `handle__pubackcomp` (`lib/handle_pubackcomp.c:41-...`): `util__increment_send_quota` (64), then `db__message_delete_outgoing(mid, wait_for_pubcomp, qos)` (145).
- PUBREC: `handle__pubrec` (`lib/handle_pubrec.c:121-125`): reason <0x80 -> `db__message_update_outgoing(mid, wait_for_pubcomp, 2, true)`, then PUBREL (148). Reason >=0x80 -> `db__message_delete_outgoing(..., wait_for_pubrec, 2)`. In the broker branch no `util__increment_send_quota` is called on the >=0x80 path (the increment at 137 is in the `#else` client branch).
- **`db__message_delete_outgoing`** (`database.c:419-464`): find mid in `msgs_out.inflight` (QoS/state checks) -> `db__message_remove_inflight`; else in `queued` -> `db__message_remove_queued`; then `db__fill_inflight_out_from_queue`, `db.persistence_changes++`, `db__message_write_inflight_out_latest`.
- **`db__message_update_outgoing`** (`database.c:748-780`): sets `state`; searches queued only when `persist == false` (plugin restore path).
- **`db__message_remove_inflight` / `_queued`** (`database.c:336-369`): persist hook, `DL_DELETE`, stats decrement, `db__msg_store_ref_dec`, free.
- **`db__fill_inflight_out_from_queue`** (`database.c:372-398`): while `db__ready_for_flight`, set state from QoS, drop expired, `db__message_dequeue_first` (`401-416`: move head of queued to tail of inflight, `inflight_quota--`, move stats).

### 2.3 SUBSCRIBE -> subscription tree -> retained delivery
1. **`handle__subscribe`** (`src/handle_subscribe.c:30-295`)
   - Reads Subscription-Identifier (87-95) — one per packet, applied to every filter (103).
   - Per filter: `$share` + no-local is a protocol error (133-138, 150-154). For 3.1/3.1.1, options reset; bridges get RAP|NL (140-145). MQTT5 retain-handling extracted (155). QoS capped to `context->max_qos` (170-172).
   - Mount point (176-190); ACL SUBSCRIBE (193); `plugin__handle_subscribe` (217).
   - **`sub__add`** (224).
   - Retained: 3.1/3.1.1 always on SUCCESS or SUB_EXISTS (230-237); MQTT5 per retain-handling: 0 always, 1 only if new (`rc2 == MOSQ_ERR_SUCCESS`), 2 never (239-247) -> **`retain__queue`**.
   - Persist hook (251). SUBACK built and sent after all filters (274). `db.persistence_changes++` (280).
   - Then, only if `context->out_packet == NULL`: `db__message_write_queued_out` and `db__message_write_inflight_out_latest` (283-292).
2. **`sub__add`** (`subs.c:565-618`): tokenise (577), find/create root (589-611), `sub__add_context` (612; 329-363) walks/creates hier nodes via `sub__add_hier_entry` (542-562, `HASH_ADD`).
3. **`sub__add_normal`** (`subs.c:278-326`) / **`sub__add_shared`** (`205-275`): `sub__add_leaf` (161-192) — if this client already has a leaf at that node, **overwrite `identifier` and `subscription_options`** and return `MOSQ_ERR_SUB_EXISTS` (168-176); else allocate leaf, `DL_APPEND` (188). New leaf -> slot into `context->subs` (realloc if full), RMW `context->subs_count++`, `db.subscription_count++` or `db.shared_subscription_count++` (263-265, 314-316). Shared: share group created on demand with `HASH_ADD` into `subhier->shared` (216-227). Return value: for protocol 3.1.1 (not 3.1, not 5) SUB_EXISTS is mapped to SUCCESS (268-274, 319-325).
4. **`retain__queue`** (`retain.c:349-377`): returns immediately for `$share/` filters (359-361). Tokenise, find root, `retain__search`.
5. **`retain__search`** (`retain.c:290-346`): `#` iterates all children recursively; `+` iterates children; literal does `HASH_FIND`. Calls `retain__process` on matching nodes with `retained != NULL`.
6. **`retain__process`** (`retain.c:233-287`): `retain__delete_expired_msg` (240; 218-230: if expired -> persist delete hook, `ref_dec`, `retained = NULL`, `db.retained_count--`); ACL READ for subscriber (246); if `check_retain_source`, ACL WRITE for original publisher using a stack context built from `source_id`/`source_username`/`source_listener` (255-270); QoS = sub QoS or `min(retained.qos, sub)` (272-280); `mosquitto__mid_generate` (282); **`db__message_insert_outgoing(..., retain=true, sub->identifier, update=false, persist=true)`** (286).

### 2.4 Topic tokenising and matching
- `sub__topic_tokenise` (`topic_tok.c:57-126`): strdup the topic into `local_sub`, array of pointers into it; prepends `""` for non-`$` topics (91-94); for `$share/<name>/...` strips the two leading tokens and returns the share name (103-124). `strtok_hier` (31-54) splits on `/` in place.
- Matching in the broker hot path is done by the tree walks in `sub__search` and `retain__search`; neither calls `libcommon/topic_common.c`. `mosquitto_topic_matches_sub` is called in `bridge_topic.c:270`, `plugin_acl_check.c:82` and `lib/send_publish.c:142` (bridge remap), none on the fan-out path. `libcommon/topic_common.c` was not read.

### 2.5 UNSUBSCRIBE and session cleanup
- `handle__unsubscribe` (`handle_unsubscribe.c:124-150`): ACL UNSUBSCRIBE, `plugin__handle_unsubscribe`, **`sub__remove`**, persist hook; `db.persistence_changes++` (171-173).
- **`sub__remove`** (`subs.c:621-650`) -> `sub__remove_recurse` (446-467) -> `sub__remove_normal` (366-396) / `sub__remove_shared` (399-443): `DL_DELETE` leaf, RMW `db.(shared_)subscription_count--`, find matching `context->subs[i]` by `hier` (and `shared`), free it, `subs_count--`. Empty share group freed (429-432). Empty hier nodes freed bottom-up during recursion unwind (461-464).
- **`sub__clean_session`** (`subs.c:747-800`): for every `context->subs[i]`, persist delete hook, unlink leaf from hier/shared, counter `--`, free, then prune empty ancestors via `tmp_remove_subs` (717-742). Frees `context->subs`. Callers: `context__cleanup` when `force_free` (`context.c:182-184`), `handle_connect.c:108` (clean start takeover), `bridge.c:283,521`.
- `plugin_public.c:395-411`: re-checks ACL of every `context->subs[i]` and `sub__remove`s those now denied.

### 2.6 Session takeover (`handle_connect.c:95-170`)
On reconnect with `clean_start == false` and session not expired: `memcpy` of `msgs_in` / `msgs_out` from the old context into the new one, then zero the old (124-128); keep the new context's quota/maximum (130-133); copy `last_cmsg_id` (125) and `last_mid` (147); `db__message_reconnect_reset` (135). `context->subs` array moved (141-146) and **every leaf's `context` pointer rewritten** from old to new by walking `hier->subs` and `shared->subs` (150-170). With `clean_start == true`: `sub__clean_session(found_context)` (108).
Afterwards (235-320): `db__check_acl_of_all_messages` (246; `database.c:1318-1353`, drops client_msgs now denied, ref_dec), `db__expire_all_messages` (310; `database.c:1284-1315`), `db__message_write_queued_out` (311), `db__message_write_inflight_out_all` (315; `database.c:1457-1473`).
**`db__message_reconnect_reset`** (`database.c:1185-1194`): outgoing (1071-1120) zeroes and recomputes all stats by walking lists, resets `inflight_quota = inflight_maximum` then decrements per QoS>0 inflight; inflight states reset to `publish_qosN`, or `resend_pubrel` if it was `wait_for_pubcomp`; then `db__fill_inflight_out_from_queue`. Incoming (1124-1182): recompute stats; QoS<2 inflight removed; QoS2 kept with `dup=0`; queued promoted while ready.
**`db__messages_delete`** (`database.c:838-855`) on disconnect: deletes incoming if `force_free || clean_start || bridge clean_start`; outgoing if `force_free || bridge clean_start_local || (!bridge && clean_start)`. `db__messages_delete_list` (783-793) ref_decs and frees each item.

### 2.7 Retained-message store
- **`retain__store`** (`retain.c:144-215`). Callers: `sub__messages_queue` (`subs.c:700`, persist=true), `persist_read.c:365` (persist=true), `mosquitto_persist_retain_msg_set` (`retain.c:91`, persist=false), `mosquitto_persist_retain_msg_delete` (`retain.c:119`, persist=false, stack `base_msg` with `ref_count = 10`, 111-112).
  - Find/create path nodes (153-171).
  - `db.persistence_changes++` unless topic starts with `$SYS` (173-178).
  - Existing `retained`: if same pointer, return (184-187); persist delete hook only when the new payload is empty (189-192); **`db__msg_store_ref_dec(&retainhier->retained)`** (193); `db.retained_count--` (195); if new payload empty: `retained = NULL`, `retain__clean_empty_hierarchy` (197-200).
  - New payload non-empty: `retained = base_msg`, **`db__msg_store_ref_inc`** (203-204), persist add + set hooks for non-`$` topics (205-208), `db.retained_count++` (210).
  - Observed in code: when there was no existing retained message and the new payload is empty, the path nodes created at 161-171 are not pruned (pruning only happens in the `if(retainhier->retained)` branch at 199, and in `retain__expire`).
- **`retain__expiry_check`** (`retain.c:411-417`), called every loop iteration (`loop.c:199`): when `retain_expiry_interval > 0` and `now_s > next_expire_check`, runs `retain__expire(&db.retains)` (380-392) and RMW `next_expire_check`. Also run once at startup (`mosquitto.c:545`).
- **`retain__clean`** (395-408): shutdown, ref_dec all and free nodes (`db__close`, `database.c:243`).

### 2.8 Broker-originated messages
- **`db__messages_easy_queue`** (`database.c:858-918`): builds `base_msg` (retain forced 0 if `retain_available == false`, 880-884), takes ownership of `*properties` (903-906), `db__message_store`, `sub__messages_queue(source_id = context->id or "")`. Callers: `$SYS` (`sys_tree.c:164,181,244,287,290`), wills (`context.c:234`), log topic (`logging.c:374`), plugin messages (`loop.c:156`), bridge notifications (`bridge.c:313,332,550,570,692,710`).
- **`queue_plugin_msgs`** (`loop.c:139-162`), each loop iteration: drains `db.plugin_msgs`; client-targeted ones via `single_publish` (`loop.c:69-107`: `db__message_store` + `db__message_insert_outgoing` directly, retain=0, bypassing the tree and ACL); others via `db__messages_easy_queue`.
- **`mosquitto_broker_publish`** (`plugin_public.c:213-264`): `DL_APPEND(db.plugin_msgs, msg)` and `loop__update_next_event(1)`. No lock.

### 2.9 Persistence plugin API that writes the same state (`plugin_public.c`)
`mosquitto_persist_client_msg_add` (640-669: `db__message_insert_outgoing`/`_incoming`, sets `context->last_mid`), `_delete` (672-695), `_update` (698-718), `_clear` (721-740), `mosquitto_subscription_add` (743-757 -> `sub__add`), `mosquitto_subscription_delete` (760-776), `mosquitto_persist_base_msg_add` (779-834 -> `db__message_store`, ref 0), `mosquitto_persist_base_msg_delete` (837-856: removes only if `ref_count == 0`). Startup `db__msg_store_compact` (`database.c:324-333`, called `mosquitto.c:546`) removes every store entry with `ref_count < 1`.

---

## 3. Data relationships

### 3.1 Reference counting on `base_msg` (the only shared-ownership mechanism)
Increments (`db__msg_store_ref_inc`, `database.c:308-311`, plain `ref_count++`):
- each `client_msg` created: `database.c:525` (incoming), `674` (outgoing), `persist_read.c:155` (restore)
- the retained slot: `retain.c:204`
- the fan-out guard: `subs.c:673`

Decrements (`db__msg_store_ref_dec`, `database.c:314-321`, plain `ref_count--`; at 0 -> `db__msg_store_remove(base_msg, notify=true)` which `HASH_DELETE`s, decrements `msg_store_count`/`msg_store_bytes`, calls persist delete hook, frees):
- `database.c:347` (remove_inflight), `365` (remove_queued), `789` (delete_list), `1340` (ACL recheck)
- `subs.c:710` (end of fan-out)
- `retain.c:193` (replace/delete), `222` (expiry), `401` (shutdown)

Lifecycle of one PUBLISH:
1. Created at ref 0 and inserted into `db.msg_store` (`db__message_store`).
2. QoS2 only: +1 for the incoming client_msg.
3. `sub__messages_queue`: +1 guard; +1 per subscriber client_msg created; +1 if retained (old retained -1); -1 guard.
4. Each per-client delivery drops its ref on: QoS0 send (`database.c:1401`), PUBACK/PUBCOMP (`delete_outgoing`), expiry, ACL recheck, oversize, session delete.
5. When it reaches 0 it is removed from the hash and freed. A QoS0/1 message with no subscribers and not retained is freed at `subs.c:710`.

Direct frees bypassing refcount: `db__msg_store_free` on rejected messages before storing (`handle_publish.c` many lines, `loop.c:85`), on store failure (`database.c:999,1006,1033`), and `db__msg_store_remove(.., false)` from `db__msg_store_clean` (shutdown) and `mosquitto_persist_base_msg_delete` (ref 0 only).

### 3.2 Pointers that are not counted
- `subleaf->context` — raw pointer to the client. Kept valid across takeover by rewriting (`handle_connect.c:150-170`); removed by `sub__clean_session`. `subs__process` checks `leaf->context->id` before use (`subs.c:143`).
- `client_msg` lists are embedded in `struct mosquitto`; on takeover the list heads are moved by `memcpy` (`handle_connect.c:124-125`).
- `base_msg->source_listener` points into `db.config->listeners`.
- `subleaf->hier`, `subleaf->shared`, `subhier->parent`, `retainhier->parent` back-pointers.

### 3.3 Diagram (text)
```
db.msg_store (uthash by store_id) ──owns──> base_msg {ref_count, dest_ids, data{topic,payload,props,expiry}}
     ^ counted ref                          ^ counted ref                    ^ counted ref (temp)
     |                                      |                                |
client_msg (ctx->msgs_out.inflight/queued,  retainhier.retained             sub__messages_queue guard
            ctx->msgs_in.inflight/queued)   (db.retains tree)

db.normal_subs / db.shared_subs (uthash tree of subhier)
   subhier.subs ──DL──> subleaf ──context──> struct mosquitto  ──subs[]──> same subleaf
   subhier.shared (uthash by name) ──> subshared.subs ──DL ring──> subleaf
```

---

## 4. Read-modify-write sites, counters, globals

### 4.1 Global / static RMW
| State | Writes | Reads |
|---|---|---|
| `db.last_db_id` | `database.c:976-979` | same |
| `db.msg_store_count` | `++` `database.c:1024`; `--` `database.c:289` | `sys_tree.c:247` |
| `db.msg_store_bytes` | `+=` `database.c:1025`; `-=` `database.c:290` | `sys_tree.c:248` |
| `db.subscription_count` | `++` `subs.c:315`; `--` `subs.c:374,775` | `sys_tree.c:249` |
| `db.shared_subscription_count` | `++` `subs.c:264`; `--` `subs.c:409,763` | `sys_tree.c:250` |
| `db.retained_count` | `++` `retain.c:210`; `--` `retain.c:195,225` | `sys_tree.c:251` |
| `db.persistence_changes` | `++` `database.c:460,509,658`; `retain.c:177`; `handle_subscribe.c:280`; `handle_unsubscribe.c:172`; `handle_connect.c:251`; reset `loop.c:230` | `loop.c:228` |
| `db.next_event_ms` | `loop.c:170`, `loop.c:203` | mux (not read) |
| `db.plugin_msgs` | append `plugin_public.c:260`; drain `loop.c:146` | — |
| `db.msg_store` hash | add `database.c:258`; delete `database.c:288` | `retain.c:85`, `persist_read.c:359`, `plugin_public.c` `find_store_msg` (not read) |
| `db.normal_subs/shared_subs` trees | `subs.c` add/remove/clean, `database.c:219-236` | `subs.c` search |
| `db.retains` tree | `retain.c` | `retain.c` |
| `metrics[i].next` | `metrics__int_inc/dec` (`sys_tree.c:201-214`) from many sites (below); overwritten `sys_tree.c:247-259` | `sys_tree.c:281-285` |
| `metrics[i].current` | `sys_tree.c:284` | `sys_tree.c:281`, `http_api.c:246` (other thread) |
| `metric_loads[i].current` | `sys_tree.c:166` | `sys_tree.c:161` |
| `start_time`, `last_update`, `last_update_real` | `sys_tree.c:183-184, 295-296` | `sys_tree.c:150, 242, 262`; `http_api.c:249` via `broker_uptime` |
| `next_expire_check` | `retain.c:415` | `retain.c:413` |
| `hashv_plus`, `hashv_hash` | `subs.c:834-835` (init only) | `subs.c:499,520` |

`metrics__int_inc/dec` call sites in scope: `database.c:499,637,644` (publish_dropped); `handle_publish.c:336` (pub_bytes_received); `lib/packet_mosq.c:128-129,157,171-172,257-258,306,332,373,590,627`; `lib/send_publish.c:177,187,334`; `lib/send_mosq.c` ack counters (77,91,105,121). `messages_sent`/`messages_received` metrics are counted per packet in `packet_mosq.c:332,627`, not per PUBLISH.

### 4.2 Per-context RMW
- `stats.messages_received++` (`handle_publish.c:224`), `stats.messages_sent++` (`database.c:575`, before the drop decision), `stats.messages_dropped++` (`database.c:500`, incoming path only).
- `last_cmsg_id` pre-increment (`database.c:522,671`; `persist_read.c:145`).
- `last_mid` (`lib/util_mosq.c:137-141`, wraps skipping 0).
- `msgs_*.inflight_quota` (`lib/util_mosq.c:208-237`; `database.c:410-412`; reset `1083,1136`; `property_broker.c:50`).
- All eight `msgs_*` byte/count fields: `database.c:140-181` (add/remove helpers), zeroed `804-811, 825-832, 1075-1082, 1128-1135`.
- `is_dropping` set `database.c:493-494,631-632,645-646`; `lib/packet_mosq.c:151-152`; cleared on connect `handle_connect.c:242`.
- `out_packet_count/bytes` (`lib/packet_mosq.c:169-170, 252-253, 130-131`).
- `client_msg->data.dup++` (`handle_publish.c:142`), `state` writes throughout `database.c`.
- `subs_count`, `subs_capacity`, `subs[]` (`subs.c` add/remove/clean).

### 4.3 RMW on shared message objects (written after creation, while shared)
- `base_msg->dest_ids` / `dest_id_count` grown per recipient (`database.c:708-712`).
- `base_msg->ref_count` (§3.1).
- Shared-subscription ring order `DL_DELETE`/`DL_APPEND` (`subs.c:121-122`).
- `subleaf->identifier` / `subscription_options` overwritten on re-subscribe (`subs.c:173-174`).
- `subleaf->context` rewritten on takeover (`handle_connect.c:155,164`).
- `plugin__handle_message_in` rewrites `topic/payload/properties/retain` of the not-yet-stored message (`plugin_message.c:52-78`). `plugin__handle_message_out` works on a stack copy (`send_publish.c:68-93`), so stored data is not modified.

### 4.4 Config values read on this path (`struct mosquitto__config`, defaults from `conf.c`)
`allow_duplicate_messages` (default true, `conf.c:287`), `max_inflight_messages` (20, 339), `max_queued_messages` (1000, 340), `max_inflight_bytes` (0, 341), `max_queued_bytes` (0, 342), `queue_qos0_messages` (false, 347), `retain_available` (true, 348), `retain_expiry_interval` (0, 349; minutes×60 when set, `conf.c:2563-2575`), `upgrade_outgoing_qos` (false, 352), `check_retain_source`, `message_size_limit`, `sys_interval`.

---

## 5. Ordering and semantic guarantees as implemented

**Single-threaded core.** Every function above runs on the main loop thread (§6), so each PUBLISH fan-out (`sub__messages_queue`) and each SUBSCRIBE (`handle__subscribe`) runs to completion without interleaving with another.

**Per-client order.** Outgoing messages for one client are appended in call order to `msgs_out.inflight` or `msgs_out.queued` (`database.c:687-693`); queue head moves to inflight tail (`database.c:401-416`); `write_inflight_out_latest` sends pending ones in list order (`database.c:1495-1519`). So messages of QoS>0 reach one client in the order they were fanned out. Exceptions visible in code:
- QoS0 uses a different admission test (`database.c:53-74`, compares `out_packet_count` with `max_queued_messages`) and goes straight to inflight; it is never queued while online. A QoS0 message can therefore be sent ahead of earlier QoS1/2 messages still sitting in `msgs_out.queued`.
- On reconnect, inflight messages are resent first (`handle_connect.c:315` after `311`), states reset in `database.c:1085-1107`.
- Messages that fail admission are dropped, not delayed (`database.c:629-652`).

**Across subscriptions of one client.** One PUBLISH matching several of a client's filters produces one delivery per matching leaf (each `subs__send`), for MQTT5 always; for 3.1/3.1.1 only the first if `allow_duplicate_messages == false` (`database.c:586-596`). Default is true (duplicates delivered). Each delivery carries that leaf's single `identifier`; the broker never aggregates multiple subscription identifiers into one PUBLISH (`send_publish.c:273-281` writes at most one).

**Retained before live.** Within `handle__subscribe`, the leaf is added (`224`) and then `retain__queue` inserts retained copies into the client's lists (`232`/`242`) in the same call, before any later PUBLISH can be processed. Retained copies are inserted with `update=false` (`retain.c:286`), the SUBACK is queued after all filters (`handle_subscribe.c:274`), and the retained PUBLISHes are written after the SUBACK only if `context->out_packet == NULL` at that point (`283-292`). Otherwise they stay in `publish_qos*` state in `msgs_out.inflight` until the next call to `db__message_write_inflight_out_latest` for that client; the callers I found are `database.c:463` (an ack), `database.c:734` (next insert with update=true), `handle_subscribe.c:288`, `websockets.c:219` (LWS writable), and `handle_connect.c:315`/`handle_connack.c:163` (all variant). For a single PUBLISH, live fan-out to existing subscribers happens before the retained slot is updated (`subs.c:677-704`).

**QoS downgrade/upgrade.** Subscription QoS is capped to `context->max_qos` (listener max_qos) at subscribe (`handle_subscribe.c:170-172`). Delivered QoS = `min(publish, subscription)` or subscription QoS if `upgrade_outgoing_qos` (`subs.c:82-90`; retained `retain.c:272-280`). Then capped again to `context->max_qos` (`database.c:679-683`). PUBLISH with QoS above `max_qos` is rejected (`handle_publish.c:245-250`). QoS2 incoming is fanned out with qos=2 at PUBREL (`database.c:1253`). `plugin__handle_message_out` may change QoS of the outgoing packet only (`plugin_message.c:79-81`), not the stored state.

**Message expiry.** Absolute `expiry_time = now_real_s + interval` at store (`database.c:1014-1020`); `MSG_EXPIRY_INFINITE` or 0 -> 0 (never). Checked: on promotion from queue (`database.c:391-394`), immediately before each send (`database.c:1374-1386`; expired inflight QoS>0 returns send quota), on reconnect for all four lists (`database.c:1284-1315`), for retained on subscribe (`retain.c:240`) and periodically if `retain_expiry_interval` set (`retain.c:411-417`). Outgoing packet carries the remaining interval (`database.c:1384`, `send_publish.c:254-262`). Messages in `wait_for_puback/pubrec/pubcomp` state are not resent until reconnect, so expiry only applies to them on reconnect.

**No-local.** `subs.c:143`: normal leaf skipped when NL is set and `strcmp(leaf->context->id, source_id) == 0`. Shared subs never check NL (combining them is rejected at `handle_subscribe.c:133-138`). For 3.1/3.1.1 bridges NL is forced on (`handle_subscribe.c:143-145`). Broker-originated messages have `source_id == ""` (`database.c:898-902`) and so never match NL. `subs__process` loops only `while(source_id && leaf)` (`subs.c:142`); every caller I found passes non-NULL.

**Retain-as-published.** Live delivery: retain bit = publish retain only if RAP set, else 0 (`subs.c:96-100`). Retained-on-subscribe delivery: always retain=1 (`retain.c:286`). Outgoing retain forced 0 if the connection's `retain_available` is false (`send_publish.c:121-123`).

**Retain-handling (MQTT5).** 0 = send always, 1 = only for a new subscription, 2 = never (`handle_subscribe.c:239-247`). 3.1/3.1.1: sent on every (re)subscribe (`230-237`). Never for `$share` (`retain.c:359-361`).

**Shared subscriptions.** One delivery per share group per PUBLISH, to the head of `shared->subs`, then that leaf rotates to the tail regardless of whether the send succeeded or was dropped (offline client, full queue) (`subs.c:117-127`). No check that the chosen member is connected beyond what `db__message_insert_outgoing` does (offline QoS0 dropped, QoS>0 queued to that offline member).

**Persistence plugin ordering.** Hooks fire inline: `base_msg_add` before `client_msg_add` (`database.c:695-698`), `retain_msg_set` after `base_msg_add` (`retain.c:205-208`).

---

## 6. Threads

- **Broker builds with `WITH_BROKER`** (`src/CMakeLists.txt:176`, `src/Makefile:7`). In `lib/pthread_compat.h:4` all `COMPAT_pthread_mutex_*` macros expand to nothing when `WITH_BROKER` is defined (`pthread_compat.h:16-25`). So the `mid_mutex` (`util_mosq.c:136-142`), `out_packet_mutex` (`packet_mosq.c:162,173,250`), `msgs_out.mutex` (`handle_pubackcomp.c:63-65`) and `state_mutex` calls compile to no locking in the broker. Threads are linked (`src/CMakeLists.txt:295-300`) but no `pthread_create` exists in `src/` or `plugins/` (grep).
- **Main loop thread** (`loop.c:175-268`, loop body from 198): runs `retain__expiry_check`, `queue_plugin_msgs`, `sys_tree__update`, `mux__handle` (all packet handlers -> everything in §2), persistence backup, `signal__flag_check`, and for the libwebsockets build `lws_service` (`loop.c:247-260`), so LWS callbacks such as `websockets.c:210-224` (`db__message_write_*`) run on this thread. The default build uses builtin websockets (`config.mk:273-275`), also in the main loop.
- **libmicrohttpd thread(s)** (only `WITH_HTTP_API`, default yes `config.mk:156`): `MHD_start_daemon` with `MHD_USE_AUTO_INTERNAL_THREAD` (`http_api.c:451,521-539`), started from `listeners.c:247,299`. `http_api_handler` (`http_api.c:380-408`) runs on MHD's thread and, without any lock, reads `metrics[i].current` and `metrics[i].topic` (`http_api.c:244-247`), `db.now_s` and `start_time` via `broker_uptime` (`249`, `sys_tree.c:148-151`), `db.config->listeners[]` (`181-219`), and calls `mosquitto_basic_auth` and `mosquitto_acl_check` with a stack context (`check_access`, `350-378`), which read `db.config->security_options` and call plugin callbacks (`plugin_acl_check.c:163-178`). It does **not** touch `db.msg_store`, the subscription tree, the retained tree, or any client lists in the code read. `metrics[i].current` is written by the main thread at `sys_tree.c:284`, so that field is read concurrently without synchronisation.
- **Signal handler** (`signals.c:45-68`): only sets static bool flags; the work (`sub__tree_print`, persistence backup, reload) runs in the main loop via `signal__flag_check` (`signals.c:95-147`, `loop.c:241`).
- **Plugins**: `mosquitto_broker_publish` appends to `db.plugin_msgs` with no lock (`plugin_public.c:260`). Safe only if plugins call it from the main thread. I did not read plugin sources for their own threads; grep found no `pthread_create` under `plugins/`.
- Windows `SigThreadProc` is declared (`mosquitto_broker_internal.h:958`); not read.

Conclusion from the code read: all message, subscription and retained state is owned and mutated by the single main-loop thread; the only cross-thread access in this area is the HTTP API's unsynchronised read of the `$SYS` `metrics[]` array, `start_time` and `db.now_s`.
