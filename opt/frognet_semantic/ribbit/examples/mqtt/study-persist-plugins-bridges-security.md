# Mosquitto 2.1.2 — persistence, plugins, bridges, security, listener transports

Source: `scratchpad/inc/mosquitto-2.1.2` (read only; nothing built or run). All references are `file:line` into that tree. Line numbers are of executable code (a comment-stripping viewer was used; line numbers are the original file's).

Coverage note — what I did NOT read line-by-line and therefore make no claims about beyond what is stated:
- `src/websockets.c` (LWS-only backend; default build uses `WS_IS_BUILTIN`, `config.mk:273-275`) — outlined only.
- `lib/net_ws.c`, `lib/net_mosq.c` (TLS read/write/handshake in the shared lib) — not read.
- `src/conf.c` — read only the option branches listed in §6 plus the check functions; not the whole 3200 lines.
- dynamic-security `clients.c/groups.c/roles.c` command bodies — read only the dispatcher, the persistence/kick call sites and the config-load keys (via grep), not every command body.
- persist-sqlite `bind` sequences were read via grep, not every bind argument.
- libmicrohttpd itself (threading semantics of `MHD_USE_AUTO_INTERNAL_THREAD` are by flag name, not by reading MHD source).

---

## 0. The one global: `db`

Everything below reads/writes the single global `struct mosquitto_db db` (zeroed `src/mosquitto.c:447`). Relevant members touched in scope:

| member | what | writers in scope |
|---|---|---|
| `db.config` (`struct mosquitto__config`) | parsed config incl. `security_options` (global) and `listeners[i].security_options` | conf.c; reload `signals.c:111` |
| `db.contexts_by_id` (uthash `hh_id`) | all sessions keyed by client id (online + offline persisted) | persist restore `persist_read.c:73`, `plugin_public.c:541`, bridge `bridge.c:91`, `mosquitto_set_clientid` `plugin_public.c:376-383` |
| `db.contexts_by_sock` (`hh_sock`) | connected sockets | bridge connect `bridge.c:389,608` |
| `db.contexts_by_id_delayed_auth` | clients waiting on `mosquitto_complete_basic_auth` | `handle_connect.c:1260`, `plugin_public.c:870` |
| `db.msg_store` (uthash keyed by `store_id`) | base messages (payload/topic/props, ref-counted) | `database.c:1028` (id), persist restore, `plugin_public.c:826` |
| `db.retains` (retain tree) | retained msg pointer per topic node | `retain.c` |
| `db.normal_subs`, `db.shared_subs` | subscription trees | `sub__add/sub__remove` |
| `db.plugin_msgs` | queue of plugin-originated publishes | `plugin_public.c:260` → drained `loop.c:139-164` |
| `db.bridges[]`, `db.bridge_count` | live bridge contexts | `bridge.c:139-143`, `bridge.c:848-860` |
| `db.last_db_id`, `db.node_id(_shifted)` | store-id generator state | `database.c:957-979`, `plugin_public.c:888-896`, restore `persist_read.c:501` |
| `db.persistence_changes` | autosave-on-changes counter | `database.c:460,509,658`, `handle_connect.c:251`, `handle_subscribe.c:280`, `handle_unsubscribe.c:172`, `retain.c:177` |
| `db.shutdown` | suppresses all persist plugin events | `mosquitto.c:349` |
| `db.plugins[]`, `db.plugin_count` | loaded plugin ids | conf.c (`config__plugin_load`) |

Per-context (`struct mosquitto`) fields in scope: `id, username, password, auth_method, clean_start, session_expiry_interval/time, will_delay_interval/time, will, listener, bridge, is_bridge, is_persisted, last_mid, last_cmsg_id, msgs_in/msgs_out (inflight/queued lists + quotas), subs[], max_qos, maximum_packet_size, retain_available, ssl, address, remote_port, proxy.*, transport`.

Store id format: `db.node_id_shifted` (top 10 bits, `plugin_public.c:894`) | 31-bit seconds since `MOSQ_UUID_EPOCH` <<23 | ~100ns fraction; monotonic forced by `if(id <= db.last_db_id) id = last+1` (`database.c:957-979`). Only assigned if `store_id == 0` (`database.c:1027-1029`), so restored ids are preserved.

---

## 1. Startup / shutdown ordering (where cross-boundary state is loaded/saved)

`main()` (`src/mosquitto.c`):
1. `config__parse_args` `:456` → conf.c, computes `persistence_filepath` = `persistence_location/persistence_file` or `persistence_file` (default `"mosquitto.db"`) `conf.c:820-842`.
2. `db__open` `:510` → `sub__init`, `retain__init`, then **built-in `persist__restore()`** `database.c:210` (only `#ifdef WITH_PERSISTENCE`).
3. `plugin__load_all` `:532` → each plugin's init (may register callbacks, may call broker API).
4. `mosquitto_security_init(false)` `:537` → v2-4 `security_init`, then default security (password/acl/psk files) `plugin_init.c:169-181`.
5. **`plugin_persist__handle_restore()`** `:543` → fires `MOSQ_EVT_PERSIST_RESTORE` (global options only).
6. `session_expiry__check` `:544`, `retain__expire` `:545`, `db__msg_store_compact` `:546` (frees base msgs with `ref_count < 1`, `database.c:324-332`).
7. `listeners__start` `:558`, `signal__setup` `:564`, `bridge__start_all` `:567`, `broker_control__init` `:570`, main loop `:578`.

Shutdown `post_shutdown_cleanup` (`mosquitto.c:336-397`):
- send wills for every context `:343-345`, `will_delay__send_all` `:346`
- `db.shutdown = true` `:349` (from here all `plugin_persist__*` return early)
- `broker_control__cleanup` `:352`
- **`persist__backup(true)`** `:355` (built-in file, `shutdown=1` written into CFG chunk)
- `session_expiry__remove_all`, `listeners__stop`, `context__cleanup(ctxt,true)` for all with `is_persisted=false` `:361-373`, `bridge__db_cleanup` `:375`, `db__close` `:383`
- `plugin__unload_all` `:385` (calls each plugin cleanup, then `mosquitto_security_cleanup(false)`), then `mosquitto_security_cleanup(false)` again `:386`.
- Note: `LIB_CLOSE` on unload is commented out (`plugin_cleanup.c:76-78`) — shared objects stay mapped.

---

## 2. Persistence

Two independent mechanisms; they are mutually exclusive: `mosquitto_callback_register` refuses any `MOSQ_EVT_PERSIST_*` event when `db.config->persistence` is true (`plugin_callbacks.c:219-237`, log "`persistence true` cannot be used with a persistence plugin").

### 2.1 Built-in snapshot file (`persistence true`)

**When written** (`persist__backup(bool shutdown)` `persist_write.c:344-359`; no-op if `persistence==false` `:349`):
- autosave in main loop `loop.c:225-238`: if `autosave_on_changes` → when `db.persistence_changes >= autosave_interval` then reset counter; else time-based every `autosave_interval` seconds (default 1800, `conf.c:298`; negative clamped to 0 `conf.c:1268`; 0 disables both).
- `SIGUSR1` → `flag_db_backup` → `persist__backup(false)` at next loop pass `signals.c:56-59,97-101` (Windows: event `mosq<pid>_backup` `signals.c:205-209`).
- shutdown `mosquitto.c:355` with `shutdown=true`.
- `persistence_changes` increments on: queued-state out/in message insert `database.c:507-511, 656-660`; `db__message_*` path `database.c:460`; CONNECT with `clean_start==false` `handle_connect.c:250-252`; every SUBSCRIBE `handle_subscribe.c:280`; UNSUBSCRIBE `handle_unsubscribe.c:172`; any non-`$SYS` retain store `retain.c:174-178`.

**Atomic write**: `mosquitto_write_file` (`libcommon/file_common.c:336-447`): write to `<path>.new` (stale one unlinked `:371`), `fflush` + `fsync` `:410-418`, `fclose`, `rename(tmp, target)` `:435`. On error tmp is unlinked `:441-446`. File opened with restricted read perms (`restrict_read=true`, `persist_write.c:358`).

**Format v6** (`persist.h:24` `MOSQ_DB_VERSION 6`). Header: 15-byte magic `00 B5 00 "mosquitto db"` (`persist_read.c:41`), 4-byte CRC (always written as 0, never checked: `persist_write.c:366,373`; read and ignored `persist_read.c:458`), 4-byte big-endian version. Then chunks: `PF_header {uint32 chunk; uint32 length}` big-endian (`persist_write_v5.c:46-48`). Fixed structs are written raw (host layout + padding), with multibyte integer fields individually `htonl/htons`'d — except `int64` fields (`session_expiry_time`, `expiry_time`, `store_id`, `last_db_id`) which are written in host byte order (no conversion in `persist_write_v5.c:58-246`; read back raw `persist_read_v5.c`). `PF_cfg.dbid_size` is checked on restore `persist_read.c:495-500`.

Write order (`persist__write_data` `persist_write.c:362-393`):

| chunk | id | contents | selection rule |
|---|---|---|---|
| CFG | 1 | `last_db_id`, `shutdown`, `dbid_size` | always (`:376-382`) |
| BASE_MSG | 2 | `PF_base_msg{store_id, expiry_time, payloadlen, source_mid, source_id_len, source_username_len, topic_len, source_port, qos, retain}` + source_id + source_username + topic + payload + MQTT property bytes (`persist_write_v5.c:140-204`) | every `db.msg_store` entry with `ref_count>=1` and topic; `$SYS` msgs skipped if `ref_count<=1 && dest_id_count==0`, else written with `retain=0` (`persist_write.c:93-112`). `source_port` = listener port (`:136-140`) |
| CLIENT | 6 | `PF_client{session_expiry_time, session_expiry_interval, last_mid, id_len, listener_port, username_len}` + id + username | contexts in `contexts_by_id` with `session_expiry_interval != IMMEDIATE` and (`clean_start==false`, or for bridges `bridge->clean_start_local==false`) (`persist_write.c:166-174`). If interval≠NEVER and expiry_time==0 → written as `now_real_s + interval` (`:176-182`). Zero-length ids skipped `:195-199` |
| CLIENT_MSG | 3 | `PF_client_msg{store_id, mid, id_len, qos, state, retain_dup (retain<<4 | dup), direction}` + id + optional Subscription-Identifier property (`persist_write_v5.c:88-137`) | immediately after each client: `msgs_in.inflight`, `msgs_in.queued`, `msgs_out.inflight`, `msgs_out.queued` in that order (`persist_write.c:206-217`); `$SYS` with `ref_count<=1 && dest_id_count==0` skipped `:50-58` |
| SUB | 5 | `PF_sub{identifier, id_len, topic_len, qos, options & 0xFC}` + id + full topic filter | walk of `normal_subs` then `shared_subs` children (`:278-295`); leaf written iff owner has interval≠IMMEDIATE, `clean_start==false`, id set (`:247-249`). Shared-sub topic is rebuilt from the tree path (`:239-243`) |
| RETAIN | 4 | `{store_id}` | every retain node with a message whose topic is not `$SYS…` (`:304-313`) |

Note: built-in format has **no will chunk**; wills are not persisted by the file backend (wills are sent at shutdown anyway, `mosquitto.c:343-346`). Bridge contexts with `clean_start_local==false` are persisted as clients (`persist_write.c:169-170`).

**Restore** (`persist__restore` `persist_read.c:419-574`, called from `db__open`, i.e. before plugins load):
- No file → success (`:444-447`); empty → warning, success (`:449-452`); bad magic → error `:553-556`. Versions 2-6 accepted (`:464-477`); v2-4 parsed by `persist_read_v234.c`, v5/v6 by `persist_read_v5.c` (v5 client struct lacks listener_port/username `persist_read_v5.c:72-80`). Unknown chunk types are skipped by length `:544-550`.
- CFG → `db.last_db_id` (`:501`).
- BASE_MSG (`:257-337`): `topic_len==0` → error. `source_port` mapped back to a listener by port number (`:281-288`). If `expiry_time>0`, remaining = `expiry_time - time(NULL)`; if `<0` the message is **dropped** (`:290-296`), else passed as message-expiry so it is re-armed. Stored via `db__message_store(&chunk.source, …, mosq_mo_client)` (`:319`) keeping the original `store_id`; origin is `mosq_mo_client`, which matters for `check_retain_source` (§2.3). `source_id/source_username` copied into the base msg by `db__message_store` (from a stack `struct mosquitto` holding id/username, then freed `:322-323`).
- CLIENT (`:178-228`): find-or-create context (`persist__find_or_add_context` `:50-79` → `context__init`, `clean_start=false`, add to `contexts_by_id`, set `last_mid`). Sets `session_expiry_time/interval`, username (if none yet), listener by port only when `per_listener_settings` (`:209-216`), then `session_expiry__add_from_persistence` (`:217`).
- CLIENT_MSG (`:118-175`): base msg looked up by `store_id`; missing → silently skipped (`:125-129`). Context found-or-created (so order of CLIENT vs CLIENT_MSG doesn't matter). New `cmsg_id = ++context->last_cmsg_id` (`:145`) — ids are renumbered. Placed in `queued` if `state==mosq_ms_queued` or (qos>0 and inflight quota exhausted), else `inflight` with quota decrement (`:163-172`). `ref_count++` (`:155`).
- RETAIN (`:340-373`): looks up base msg by id, `retain__store(topic, msg, split, persist=true)`.
- SUB (`:376-406`): `sub.options = qos | options` (`:395`), `persist__restore_sub` → find-or-create context, `sub__add` (`:577-595`).
- After all plugins and persist plugin restore: `session_expiry__check`, `retain__expire`, `db__msg_store_compact` (`mosquitto.c:544-546`) drop expired sessions/retains and orphan base msgs.

### 2.2 Persistence plugin (event stream) — `plugin_persist.c` + `persist-sqlite`

Broker-side emitters (all dispatch **only on `db.config->security_options` (global)**, never per-listener; all return early when `db.shutdown`):

| event | emitter | guard | fired from |
|---|---|---|---|
| `PERSIST_RESTORE` | `plugin_persist.c:33-45` | — | `mosquitto.c:543` |
| `PERSIST_CLIENT_ADD` (+will add) | `:48-85` | skip if `is_persisted`; sets `is_persisted=true` | CONNACK success with interval≠IMMEDIATE `handle_connect.c:320-322`; bridge with `clean_start_local==false` `bridge.c:129-131` |
| `PERSIST_CLIENT_UPDATE` (+will add/delete) | `:88-128` | — | `session_expiry__add` `session_expiry.c:93` (i.e. on disconnect of persistent session), `will_delay__add` `will_delay.c:57` |
| `PERSIST_CLIENT_DELETE` (+will delete) | `:131-158` | skip if `id==NULL`, state `duplicate`; only if `is_persisted` and interval==IMMEDIATE | `context.c:286` (disconnect with interval 0), `handle_connect.c:110` (clean-start takeover), `session_expiry.c:201` (expiry) |
| `PERSIST_SUBSCRIPTION_ADD` | `:161-181` | `is_persisted` | `handle_subscribe.c:251` |
| `PERSIST_SUBSCRIPTION_DELETE` | `:184-205` | `is_persisted` | `handle_unsubscribe.c:148`, `subs.c:757` (clean session) |
| `PERSIST_CLIENT_MSG_ADD/DELETE/UPDATE` | `:223-292` | `is_persisted`; QoS0 skipped unless `queue_qos0_messages` | add: `database.c:547-548,696-697`; delete: `database.c:342,360,1339`; update: `database.c:395,762,1106,1177,1273,1412,1425,1437,1543,1545` |
| `PERSIST_BASE_MSG_ADD` | `:295-329` | skip if `base_msg->stored`; then sets `stored=true` | before each client-msg add `database.c:547,696`; retain set `retain.c:206` |
| `PERSIST_BASE_MSG_DELETE` | `:332-351` | only if `stored`; sets `stored=false` | `database.c:292` (store removal) |
| `PERSIST_RETAIN_MSG_SET` | `:354-373` | — | `retain.c:207` (only when `persist` arg true and topic not `$…`) |
| `PERSIST_RETAIN_MSG_DELETE` | `:376-394` | — | `retain.c:191` (empty-payload clear, non-`$`), `retain.c:221` (retain expiry) |
| `PERSIST_WILL_ADD` | `:397-422` | `context->will` set | from client add/update; `handle_connect.c:323` (non-persistent session with will) |
| `PERSIST_WILL_DELETE` | `:425-443` | — | from client update/delete |

Bug observed in code: `plugin_persist__handle_will_delete` invokes callbacks registered for `persist_will_delete` but passes event id `MOSQ_EVT_PERSIST_WILL_ADD` (`plugin_persist.c:440`). persist-sqlite ignores the event argument (`will.c:66`), so it still deletes.

Broker functions a persistence plugin calls during restore (`plugin_public.c`, `retain.c`): see §3.3.

**persist-sqlite plugin** (`plugins/persist-sqlite`):
- Options: `plugin_opt_db_file` (ignored if `persistence_location` set — then `<location>/mosquitto.sqlite3`, dir created 0770) `plugin.c:70-100`; `sync` (extra/full/normal/off → PRAGMA synchronous 3/2/1/0, default 1) `:118-130`; `flush_period` seconds (default 5) `:131-135`; `page_size` (default 4096) `:136-140`. No db_file → plugin inactive, success `:143-146`.
- Registers every PERSIST event + `MOSQ_EVT_TICK` (`plugin.c:154-217`). Cleanup forgets to unregister `RETAIN_MSG_SET`, `CLIENT_UPDATE`, `WILL_ADD/DELETE` (`plugin.c:239-252`), harmless because `plugin__callback_unregister_all` runs after (`plugin_cleanup.c:68`).
- DB open: WAL, `foreign_keys=ON`, `synchronous`, `page_size` (`init.c:473-501`), schema create/migrate (`init.c:85-274`): tables `base_msgs, retains, clients, subscriptions, client_msgs, version_info, wills`; schema version 1.0→1.1 adds `wills` (`init.c:53-82`); unknown version → error (tested by `15-persist-migrate-db.py`). Then `BEGIN;` (`init.c:513`) — **everything runs inside one long transaction**.
- Commit cadence: tick callback does `END; BEGIN;` if `event_count>0`, sets `next_s = flush_period` (`tick.c:29-45`). Every write handler increments `event_count`. So durability lag ≤ flush_period (tests use `plugin_opt_flush_period 0` for will test, `15-persist-client-will.py:106`). Cleanup does `END;`, finalizes, `wal_checkpoint(TRUNCATE)`, close (`init.c:521-563`).
- Write mapping: client add → `INSERT OR REPLACE clients(... connection_time=time(NULL) ...)` (`clients.c:28-72`, `init.c:310-317`); client update → only `session_expiry_time, will_delay_time` (`init.c:330-334`); client remove → delete subscriptions, delete base_msgs referenced only by this client and not retained (`init.c:403-411`), delete client_msgs, delete client (`clients.c:75-114`); sub add `INSERT OR REPLACE` (options, identifier); client_msg add `INSERT` (with broker's `cmsg_id`); client_msg delete by `(client_id, store_id, direction)` (`common.c:26-46`); update `state, dup` by `(client_id, store_id)`; base msg add with properties serialized to JSON (`util.h:52-65`); retain set `INSERT OR REPLACE retains(topic, store_id)`; will add `INSERT OR REPLACE wills` (`init.c:446-451`).
- Restore order (`restore.c:521-547`): base_msgs → retains → clients → subscriptions → client_msgs (`ORDER BY rowid`) → wills. Each row goes through the public broker API (§3.3). Will restore: `mosquitto_client_will_set`; if client not found → publish will immediately via `mosquitto_broker_publish`; if found but `session_expiry_time==0 && will_delay_interval==0` → also publish immediately (`restore.c:464-518`).

### 2.3 Restore semantics the tests depend on

Built-in file (11-*, 04-*): `terminate()` = SIGTERM → `g_run=0` (`signals.c:49-50`) → shutdown backup.
- `11-persistent-subscription.py`: client `clean_session=False, session_expiry=60` subscribes `subpub/qos1` q1, broker restarted; reconnect gets CONNACK session-present=1 and receives its own publish (mid 1). Needs CLIENT+SUB chunks and `last_mid` (mid numbering starts at 1 after restore).
- `11-persistent-subscription-no-local.py`: subscription options (No-Local bit, `options & 0xFC`) survive restart; after restart the outgoing mid continues at 2 (`publish2b mid=2`) → `last_mid` restored (`persist_read.c:75-77`).
- `11-subscription-id.py`: Subscription Identifier 53 survives both on the SUB chunk (`PF_sub.identifier`) and on the queued client message (CLIENT_MSG property) — queued msg delivered after restart with sub-id 53.
- `11-message-expiry.py`: two queued msgs with expiry 5 s and 100 s plus one without; broker restarted, wait 7 s: only msg2 (with reduced expiry interval, test accepts 2..100) and msg3 delivered. Depends on absolute `expiry_time` stored and the `<0 → drop` / re-arm logic (`persist_read.c:290-300`) plus expiry on delivery.
- `11-pub-props.py`: retained v5 message with payload-format, content-type, response-topic, correlation-data, user-property survives restart with all properties (properties written as raw MQTT property bytes).
- `11-persistence-autosave-changes.py`: `autosave_interval 1` + `autosave_on_changes true`; one retained publish → file must exist within 0.5 s (retain store increments `persistence_changes`, `retain.c:177`; loop writes when ≥1).
- `04-retain-check-source-persist(-diff-port).py`: `check_retain_source true`, ACL file grants `readwrite test/topic`; retained message published; broker stopped; ACL rewritten to `read` only; broker restarted; new subscriber must NOT receive the retained message. Mechanism: base msg restored with `source_id/source_username/source_port→listener` and origin `mosq_mo_client`; `retain__process` builds a fake context `{id, username, listener=source_listener}` and runs `MOSQ_ACL_WRITE` check (`retain.c:254-269`). Diff-port variant: subscriber on a second listener; with `per_listener_settings true` the ACL comes from the *source* listener restored by `source_port` (`persist_read.c:281-288`). Test also asserts the db file exists after terminate.

persist-sqlite (15-*, run as `./15-x.py persist_sqlite`, `test.py:247-263`; DB at `<port>/mosquitto.sqlite3` via `plugin_opt_db_file`, `persist_sqlite.py:23-32`). Tests inspect DB rows directly after `terminate_broker` (SIGTERM + wait, `test/mosq_test.py:176-183`), except the will test which uses `broker.kill()` (SIGKILL) and relies on `flush_period 0` commits.
- `15-persist-client-v5-0/v3-1-1`: after a persistent connect then terminate: `clients=1`, row values (`max_packet_size=10000`, `max_qos=2`, `retain_available=1`, `session_expiry_interval=60`, `will_delay=0`); reconnect gets session-present; a clean-start connect deletes the row → counts all zero.
- `15-persist-subscription-*`: `clients=1, subscriptions=2` rows, restored subs deliver after restart; clean connect removes.
- `15-persist-client-msg-out-*`: queued outbound QoS1/2 msgs: `client_msgs_out`, `base_msgs` counts, row fields (`cmsg_id` 1.., `store_id`, `dup=0`, `direction=1`, `mid`, `state=ms_queued(11)`); after delivery+ack counts return to zero (delete events). `-clear-`: reconnect with clean start → nothing received, rows removed. `-dup-`: dup flag persisted on resend.
- `15-persist-client-msg-in-*`: QoS2 inbound after PUBREC (state wait_for_pubrel): `client_msgs_in=2, base_msgs=2`; after restart PUBREL completes delivery to a helper subscriber; then rows gone.
- `15-persist-retain-*`, `-publish-properties-`: `base_msgs` + `retains` counts; properties round-trip through JSON; `15-persist-retain-clear`: empty retained publish deletes retain row and base msg.
- `15-persist-client-expired-session`: test edits `clients.session_expiry_time` in the DB (`persist_sqlite.py:modify_client`) between runs; on restart the broker must expire the session (via `mosquitto_persist_client_add` → `session_expiry__add_from_persistence` then `session_expiry__check` at `mosquitto.c:544`) and drop its messages.
- `15-persist-client-drop-expired-messages`: message-expiry on queued msgs after restart.
- `15-persist-client-msg-modify-acl`: ACL changed between runs to `pattern deny`; on reconnect queued messages are removed via `db__check_acl_of_all_messages` (`handle_connect.c:246`) → `database.c:1332-1339` ACL READ check → client-msg delete event.
- `15-persist-client-will`: wills persisted (`wills` table count), and after kill/restart either delivered or not according to session-expiry/will-delay (restore rules in `restore.c:495-504`).
- `15-persist-bridge-queue`: bridge broker with persistence: 100 QoS1 msgs queued for an unreachable bridge are in DB (client_msgs for the bridge's local client id); after restart of both, all forwarded; DB ends with zero messages. Depends on bridge context created in `bridge__new` with `clean_start_local==false` → `plugin_persist__handle_client_add` (`bridge.c:129-131`).
- `15-persist-migrate-db`: schema version upgrade 0.9/1.0 → 1.1, unknown version fails with logged message (`init.c:263-266`).

---

## 3. Plugin ABI

### 3.1 Loading and versions

- `plugin`/`auth_plugin`/`global_plugin` (conf.c `:1184-1201`): `config__plugin_load(NULL, path)` then `config__plugin_add_secopt` to current security options (global, or listener when `per_listener_settings`; `global_plugin` always global). `plugin_load <name> <path>` + `plugin_use <name>` per listener (`conf.c:1213-1247`). `plugin_opt_*`/`auth_opt_*` → `(key,value)` appended to `cur_plugin->config.options` (`conf.c:1134-1183`); `auth_plugin_deny_special_chars` → `config.deny_special_chars` (`:1202-1212`). All plugin options are skipped on reload (`if(reload) continue`).
- Load (`plugin_init.c:48-118`): skip if `security_option_count==0`; `dlopen`; prefer `mosquitto_plugin_version(count, {5,4,3,2})`, else `mosquitto_auth_plugin_version()`. Dispatch to `plugin__load_v5/v4/v3/v2`.
- v5 (`plugin_v5.c:26-61`): requires `mosquitto_plugin_init`; optional `mosquitto_plugin_cleanup`; init called with `(identifier, &user_data, options, count)`; non-zero → load failure.
- v4 (`plugin_v4.c:176-281`): requires `auth_plugin_init/cleanup`, `auth_security_init/cleanup`, `auth_acl_check`; optional `auth_unpwd_check`, `auth_psk_key_get`, `auth_start`+`auth_continue` (start without continue = error). Wrapped into v5 callbacks: RELOAD (= security_cleanup(reload=true) + security_init(true)), BASIC_AUTH, ACL_CHECK, EXT_AUTH_START/CONTINUE, PSK_KEY. Missing optional → `PLUGIN_DEFER`.
- v3 (`plugin_v3.c:135-212`) and v2 (`plugin_v2.c:126-207`): all of unpwd/acl/psk required; wrappers return `MOSQ_ERR_INVAL` if missing. v2 receives `(clientid, username, topic, access)` / `(username, password)` / `(hint, identity, key, len)` without the client pointer.
- `acl__pre_check` (`plugin_acl_check.c:31-67`) for v2-v4 only: `deny_special_chars` (username or clientid contains `+#/` → deny); v3/v4 auto-allow UNSUBSCRIBE; v2 auto-allows SUBSCRIBE and UNSUBSCRIBE.
- `mosquitto_security_init/cleanup` call v2-v4 `security_init/cleanup` for every plugin, then default security (`plugin_init.c:169-181`, `plugin_cleanup.c:129-136`).

### 3.2 Callback registration

`mosquitto_callback_register(id, event, cb, event_data, userdata)` (`plugin_callbacks.c:204-291`):
- persistence events refused when built-in persistence on (`:219-237`).
- `MOSQ_EVT_CONTROL` → `control__register_callback` with `event_data` = topic string (`:239-241`, §5.4).
- else appended (DL list, registration order) to `plugin_callbacks.<event>` of **every** security-options block the plugin is attached to (`:257-280`); duplicate (same id+cb) → `MOSQ_ERR_ALREADY_EXISTS`. Plugin with zero attachments → `MOSQ_ERR_INVAL` (`:251-255`).
- `mosquitto_callback_unregister` (`:311-334`), and `plugin__callback_unregister_all` on unload (`:294-308`).
- Every dispatcher runs **global callbacks first, then the context's listener callbacks** (for per-listener attachment).

### 3.3 Event table (v5)

All callbacks have signature `int cb(int event, void *event_data, void *userdata)`.

| event | fired when (call site) | event_data fields | plugin result / mutation semantics |
|---|---|---|---|
| `RELOAD` (1) | SIGHUP: `signal__flag_check` → `plugin__handle_reload` `signals.c:113`, after `config__read(reload=true)`. Global opts then each listener (`plugin_reload.c:50-71`) | `options, option_count` are **not filled** (memset only, `plugin_reload.c:33`) | non-zero aborts the reload chain and is returned from `signal__flag_check` → main loop exits (`loop.c:241-244`) |
| `ACL_CHECK` (2) | `mosquitto_acl_check` from: PUBLISH in (WRITE) `handle_publish.c:371`; delivery (READ) `subs.c:76`; retained delivery `retain.c:246` + source check WRITE `retain.c:263`; SUBSCRIBE `handle_subscribe.c:193`; UNSUBSCRIBE `handle_unsubscribe.c:125`; will (context.c:224); queued-message recheck `database.c:1332`; kick ACL recheck `plugin_public.c:399`; HTTP API `http_api.c:363` | `client, topic, payload, payloadlen, qos, retain, properties, access` (READ=1,WRITE=2,SUBSCRIBE=4,UNSUBSCRIBE=8) | Return SUCCESS/ACL_DENIED/other stops the chain; `PLUGIN_DEFER` and `PLUGIN_IGNORE` continue (`plugin_acl_check.c:131-134`). Pre-checks before plugins: no client id → deny; `context->bridge` → allow (bridges bypass ACL); `$SYS` WRITE denied except `$SYS/broker/connection/+/state`; `$share` only SUB/UNSUB (`:70-102,146-156`). Final: any DEFER and nobody decided → DENIED; no plugins at all → SUCCESS (`:161-200`). No listener + per_listener_settings → DENIED |
| `BASIC_AUTH` (3) | CONNECT `handle_connect.c:1254` (skipped if TLS cert identity/subject is username, `:1249`); SIGHUP re-auth of all connected clients `security_default.c:277`; HTTP API `http_api.c:361` | `client, username, password` (pointers to context fields) | SUCCESS/AUTH/other stop chain; `MOSQ_ERR_AUTH_DELAYED` puts client into `delayed_auth` (`handle_connect.c:1258-1261`) until `mosquitto_complete_basic_auth`. If all IGNORE → allow_anonymous decides; if any DEFER and none decided → allowed only if `username==NULL` and anonymous allowed (`plugin_basic_auth.c:56-113`) |
| `EXT_AUTH_START` (4) | CONNECT with Authentication Method `handle_connect.c:1221`; AUTH re-auth `handle_auth.c:121` | `client, auth_method, data_in, data_in_len`; plugin sets `data_out` (malloc'd), `data_out_len` | Non DEFER/IGNORE result returned along with data_out (`plugin_extended_auth.c:48-57`). Nobody handled → `MOSQ_ERR_NOT_SUPPORTED` (`:104`). Requires `context->listener` and `auth_method` |
| `EXT_AUTH_CONTINUE` (5) | AUTH packet `handle_auth.c:126` | same | same; `MOSQ_ERR_AUTH_CONTINUE` presumably drives another AUTH round (not traced) |
| `CONTROL` (6) | PUBLISH to `$CONTROL/...` after ACL WRITE passes, `handle_publish.c:389-396` → `control__process` | `client, topic, payload, payloadlen, qos, retain, properties`; plugin may set `reason_string` (malloc'd) / `reason_code` | One handler per exact topic (hash), global first then listener when per_listener (`control.c:61-73`). No handler → publishes `{"error": "endpoint not available"}` to `<topic>/response` (only to that client id) `control.c:31-46`. PUBACK/PUBREC always `SUCCESS`, plus reason-string property if callback rc≠0 (v5) (`control.c:86-108`). Message is **not** routed to subscribers |
| `MESSAGE_IN` (7) | first step of `handle__accepted_publish` `handle_publish.c:75` (after topic-alias, mount point, ACL WRITE) | `client, topic, payload, payloadlen, qos, retain, properties` | Non-SUCCESS stops chain: ACL_DENIED → NOT_AUTHORIZED, QUOTA_EXCEEDED → QUOTA_EXCEEDED, else message dropped (`handle_publish.c:75-91`). Plugin may replace `topic`, `payload`(+len), `properties` with new mosquitto_malloc'd buffers; broker frees old ones; `retain` change applied; `qos` change ignored for IN (`plugin_message.c:31-84`) |
| `TICK` (9) | every main loop pass `loop.c:216` | in: `now_s, now_ns`; out: plugin sets `next_s, next_ms` | Per-callback rate limit: callback only invoked when now > its `next_tick`; next_tick = now + next; also feeds `loop__update_next_event` (`plugin_tick.c:28-55`). Return value ignored |
| `DISCONNECT` (10) | `context__disconnect` when `session_expiry_interval == IMMEDIATE` `context.c:272-273` | `client, reason` | ignored; skipped if `id==NULL` |
| `CONNECT` (11) | after CONNACK + queued/inflight flush, success only `handle_connect.c:318` | `client` | ignored |
| `SUBSCRIBE` (12) | per filter after ACL SUBSCRIBE passes `handle_subscribe.c:217` | `client`, `data.{topic_filter, options, identifier, properties}` | non-SUCCESS stops chain and fails that filter (exact SUBACK mapping not traced). Plugin may replace `topic_filter` (broker frees old) and change `options` (`plugin_subscribe.c:26-53`) |
| `UNSUBSCRIBE` (13) | after ACL UNSUBSCRIBE `handle_unsubscribe.c:141` | `client`, `data.{topic_filter, properties}` | may replace topic_filter |
| `PERSIST_*` (14-26, 29-30) | §2.2 | §2.2 / structs `broker.h:314-376` | return ignored by broker |
| `MESSAGE_OUT` (27) | every outgoing PUBLISH in `send__publish` `lib/send_publish.c:76` (before mount-point strip and bridge remap) | same as MESSAGE_IN | non-SUCCESS → message silently not sent but returns SUCCESS (`lib/send_publish.c:95-117`); topic/payload/properties replaceable (freed after send), `qos` and `retain` changes honoured (`plugin_message.c:78-81`) |
| `CLIENT_OFFLINE` (28) | `context__disconnect` when session persists (interval≠IMMEDIATE) `context.c:274-275` | `client, reason` | ignored |

Note: `get_event_name(MOSQ_EVT_CLIENT_OFFLINE)` returns `"connect"` (`plugin_callbacks.c:53-54`) — logging only.

### 3.4 Broker functions callable by plugins (`include/mosquitto/broker.h`, implemented `src/plugin_public.c` unless noted)

| function | reads / writes |
|---|---|
| `mosquitto_plugin_set_info` `:34` | id->plugin_name/version (shown in `listPlugins`) |
| `mosquitto_callback_register/unregister` | §3.2 |
| `mosquitto_log_printf` (`logging.c:436`) | log |
| `mosquitto_client(clientid)` `:63` | lookup `contexts_by_id` |
| `mosquitto_client_address/port/clean_session/id/id_hashv/keepalive/certificate/protocol/protocol_version/sub_count/username` `:53-210` | read-only context getters; `certificate` returns `SSL_get_peer_certificate` (caller must free); `username` returns `bridge->local_username` for bridge contexts |
| `mosquitto_set_username` `:309` | replaces `context->username` (UTF-8 validated) |
| `mosquitto_set_clientid` `:338` | replaces `context->id`, re-keys `contexts_by_id` if present; rejects duplicates |
| `mosquitto_kick_client_by_clientid(id|NULL, with_will)` `:430` | for each match: v5 DISCONNECT 0x98, if `!with_will` state=disconnecting (suppresses will), if session persists re-run SUBSCRIBE ACL on all subs and remove failing ones, `do_disconnect(ADMINISTRATIVE_ACTION)` (`:415-427`). NULL → all sockets |
| `mosquitto_kick_client_by_username(user|NULL, with_will)` `:451` | same over sockets with matching username (NULL = anonymous) |
| `mosquitto_apply_on_all_clients(fn, ctx)` `:472` | iterate `contexts_by_id` |
| `mosquitto_broker_publish(clientid|NULL, topic, len, payload, qos, retain, props)` `:213` | takes ownership of payload/props; appends to `db.plugin_msgs`, sets next event 1 ms. Drained at the top of the next loop pass (`loop.c:139-164`): with clientid → `single_publish` directly to that client's outgoing queue (no ACL, no subscription match, retain forced 0, `loop.c:69-107`); without → `db__messages_easy_queue(NULL, …)` normal fan-out (retain honoured). Message-Expiry property extracted and removed from props (`loop.c:110-136`) |
| `mosquitto_broker_publish_copy` `:267` | copies payload then as above |
| `mosquitto_complete_basic_auth(clientid, result)` `:860` | resumes delayed auth: success → `connect__on_authorised`; failure → CONNACK not authorised, clean session, clear will, disconnect |
| `mosquitto_broker_node_id_set(id≤1023)` `:888` | `db.node_id`, store-id top bits |
| `mosquitto_persistence_location` `:900` | config string |
| `mosquitto_client_will_set` `:906` | `will__set` on an existing context; properties force protocol v5 if not connected |
| `mosquitto_persist_client_add(struct mosquitto_client*)` `:488` | creates offline context (`clean_start=false`, `is_persisted=true`), takes ownership of clientid/username/auth_method, listener by port only if per_listener_settings, adds to `contexts_by_id`, `session_expiry__add_from_persistence`. Fails if id exists |
| `mosquitto_persist_client_update` `:553` | overwrites username, expiry/delay times & intervals, max_qos, max packet size, retain_available on existing context |
| `mosquitto_persist_client_delete(clientid)` `:603` | removes from session/will-delay lists, clears will, forces IMMEDIATE expiry, state duplicate, `do_disconnect` |
| `mosquitto_persist_client_msg_add` `:640` | looks up context + base msg by store_id; OUT → `db__message_insert_outgoing(context, cmsg_id, mid, qos, retain, base, sub_id, false,false)` and sets `last_mid`; IN → `db__message_insert_incoming` |
| `mosquitto_persist_client_msg_delete` `:674` | OUT → `db__message_delete_outgoing(mid, any, qos)`; IN → `db__message_remove_incoming(mid)` |
| `mosquitto_persist_client_msg_update` `:698` | OUT only: `db__message_update_outgoing(mid, state, qos)`; IN is a no-op |
| `mosquitto_persist_client_msg_clear` `:722` | delete all in or out messages. Note: condition order means `mosq_bmd_all` only clears incoming (`:735-739`) |
| `mosquitto_persist_base_msg_add` `:781` | builds base msg with given `store_id`, takes ownership of topic/payload/props, `source_port`→listener, `stored=true` (so no echo event), `db__message_store(..., mosq_mo_broker)` — note origin **broker**, so restored-from-plugin retains bypass `check_retain_source` (`retain.c:255`) unlike the built-in file restore |
| `mosquitto_persist_base_msg_delete(store_id)` `:839` | removes only if `ref_count==0` |
| `mosquitto_subscription_add(const struct mosquitto_subscription*)` `:744` | `sub__add` on existing context |
| `mosquitto_subscription_delete(clientid, topic)` `:762` | `sub__remove` |
| `mosquitto_persist_retain_msg_set(topic, store_id)` (`retain.c:74`) | `retain__store(topic, base, split, persist=false)` |
| `mosquitto_persist_retain_msg_delete(topic)` (`retain.c:100`) | stores a zero-length dummy base msg to clear the node |
| `mosquitto_control_command_reply / send_response / generic_callback` (`control_common.c`) | §5.4 |

---

## 4. Bridges

Config (`conf.c:1704-1761` `connection <name>` defaults): keepalive 60, notifications on, start_type automatic, idle_timeout 60, restart_timeout 0 (then backoff base 5 s / cap 30 s), threshold 10, try_private true, attempt_unsubscribe true, protocol mqtt311, outgoing_retain true, `clean_start_local=-1` (inherit), reload_type lazy, max_topic_alias 10. `config__check_bridges` (`conf.c:3030-3077`): default `remote_clientid = <hostname>.<name>`, `local_clientid = local.<remote_clientid>`, local ids must be unique.

Topic remapping:
- `topic <pattern> [out|in|both] [qos] [local_prefix] [remote_prefix]` (`conf.c:2713-2810`), `""` = empty. `bridge__add_topic` (`bridge_topic.c:161-255`): prefixes must be valid publish topics; empty pattern requires both prefixes; duplicates skipped; `local_topic = local_prefix+pattern`, `remote_topic = remote_prefix+pattern`; sets `topic_remapping` when any prefix.
- Inbound (remote→local): `bridge__remap_topic_in` called from PUBLISH handling `handle_publish.c:322`: first `in/both` entry whose `remote_topic` matches: strip `remote_prefix` if present, prepend `local_prefix` (`bridge_topic.c:258-312`).
- Outbound (local→remote): in `send__publish` `lib/send_publish.c:135-180`: first `out/both` entry whose `local_topic` matches: strip `local_prefix`, prepend `remote_prefix`.

Lifecycle:
- `bridge__start_all` (`bridge.c:159-187`) per configured bridge: `bridge__new` (`:66-149`) reuses an existing context with the local client id (e.g. restored from persistence) or creates one; sets `bridge`, `is_bridge`, `username/password = remote_*`, TLS fields from bridge config; `clean_start_local` defaults to `clean_start`; if `!clean_start_local` → `session_expiry_interval=NEVER`, `plugin_persist__handle_client_add`, remove from session expiry list. Appended to `db.bridges`. Ownership of the config struct moves (`db.config->bridges[i]=NULL`).
- `bridge__connect` (non-ADNS, `:482-661`): state new, keepalive, `clean_start = bridge->clean_start`, drop pending packets, `db__message_reconnect_reset`, `db__messages_delete(context,false)` (exact semantics of the false flag not traced), **`sub__clean_session` then re-add local subscriptions** for each out/both topic with options `NO_LOCAL|RETAIN_AS_PUBLISHED|qos` (`:521-539`); notifications: queue local `'0'` once (`initial_notification_done`) and set the will to retained `'0'` on `notification_topic` or `$SYS/broker/connection/<remote_clientid>/state` (`:541-582`); `net__socket_connect` (to `addresses[cur_address]`, bind_address); add to `contexts_by_sock`; TCP keepalive/user-timeout; CONNECT with Receive-Maximum, Session-Expiry, Topic-Alias-Maximum props (v5) (`:619-644`). Protocol byte gets `0x80` bridge bit for v3 if `try_private` (`lib/send_connect.c:151-152`); the receiving broker marks `is_bridge` (`handle_connect.c:535-543`).
- ADNS variant (`#if __GLIBC__ && WITH_ADNS`, off by default `config.mk:107`) splits into step1/2/3 (`:251-478`) and additionally queues retained msgs on local subscribe (`:300`).
- CONNACK: only valid for bridges (`handle_connack.c:114-117`); v5 rejected with protocol-version code → downgrade to 3.1.1 and reconnect (`:129-141`); success → `bridge__on_connect` then `active`, flush queued/inflight (`:149-164`).
- `bridge__on_connect` (`bridge.c:665-759`): publish `'1'` notification to remote (unless `notifications_local_only`) and queue locally; SUBSCRIBE remote for in/both topics (v5 opts `NO_LOCAL|RAP|SEND_RETAIN_ALWAYS`); for out-only topics optionally UNSUBSCRIBE (`attempt_unsubscribe`); then `retain__queue` local retained msgs for out/both topics to the bridge.
- Reconnect (`bridge_check` `:1022-1179`, once per second): connected → keepalive + pending-connect check + primary-address probe when not round-robin (`:1048-1090`). Not connected → `reload_if_needed` (lazy reload) else backoff: first pass sets `restart_t = now*1000 + restart_timeout` (backoff via `bridge__update_backoff` `:954-974`: reset if connection lasted ≥ `stable_connection_period`, else random in `[base, 3*prev]` capped), rotates `cur_address`; second pass connects when time reached (automatic) or `lazy_reconnect` set (lazy start: set when queued+inflight ≥ `threshold`, `database.c:720-726`).
- Reload (SIGHUP, `bridge__reload` `:779-826`): bridges removed from config destroyed; new ones created; `brt_immediate` → destroy+recreate now; `brt_lazy` → replaced on next disconnect (`reload_if_needed`).
- Queued messages: bridge context is an ordinary client context; outbound messages for bridged topics are queued on it via its local subscriptions; with `clean_start_local=false` they persist across broker restarts (file: `persist_write.c:169-170`; plugin: `bridge.c:131`). `local_cleansession` vs `cleansession` matrix is exercised by `06-bridge-clean-session-*` (`06-bridge-clean-session-core.py:8-15`).
- ACL: `mosquitto_acl_check` returns SUCCESS for any `context->bridge` (`plugin_acl_check.c:149-151`). `$CONTROL` from a bridge with per_listener_settings is refused with a warning (`control.c:65-70`).

---

## 5. Security

### 5.1 Default (built-in) security — `security_default.c`

`mosquitto_security_init_default` (`:32-74`): creates a pseudo plugin id `"builtin-security"` per listener (per_listener_settings) or global, attaches it to that security options block, then:
- `broker_password_file__init` (`password_file.c:29-65`): for each `password_file`, parse (`plugins/password-file/password_parse.c:29-129`: `user:hash` lines, `#` comments, lines without `:` ignored, duplicate user → error, password decoded by `mosquitto_pw_decode`) and register `MOSQ_EVT_BASIC_AUTH` → `password_file__check` (`password_check.c:27-55`: no username → IGNORE; user found with pw → `mosquitto_pw_verify`; found without pw → SUCCESS; else AUTH).
- `broker_acl_file__init` (`acl_file.c:32-70`): parse (`acl_parse.c:174-311`: `user <name>`, `topic [read|write|readwrite|deny] <t>`, `pattern [...] <t>`; topic before any `user` → anonymous list; `deny` entries are prepended, others appended; patterns with `%c`/`%u` counted) and register `ACL_CHECK` → `acl_file__check` (`acl_check.c:29-141`: SUB/UNSUB always SUCCESS; user list (or anon list) matched first; `$`-topics only match `$`-rules; then patterns (deny if clientid/username contains `+#`; patterns with `%u` skipped for anonymous); no match → DENIED).
- `psk_file__init` (`psk_file.c:42-71`): `identity:hexkey` into `psk_id` hash; non-hex → error. Not a callback: used directly by `mosquitto_psk_key_get_default` (`:244-272`) after plugin PSK callbacks (`plugin_psk_key.c:97-104`).
- Cleanup `:77-105` unregisters and frees.
- These same parse/check sources are also built as standalone v5 plugins `plugins/acl-file`, `plugins/password-file` (register ACL_CHECK/BASIC_AUTH + RELOAD → cleanup+reparse, e.g. `acl-file/plugin.c:39-76`, `acl_parse.c:346-355`).

`mosquitto_security_apply_default` (SIGHUP, `:128-286`): reload TLS ctx for listeners with crlfile+require_certificate; for every non-bridge context in `contexts_by_id`: disconnect if anonymous now disallowed; for cert-identity listeners recompute username from cert (CN or RFC2253 subject) or disconnect; otherwise re-run `mosquitto_basic_auth` and disconnect on failure. (Iterates `contexts_by_id`, so offline persisted sessions too; `do_disconnect` on a disconnected context returns early `loop.c:278-280`.)

SIGHUP order (`signals.c:108-138`): `config__read(reload)` → `listeners__reload_all_certificates` → `plugin__handle_reload` → `mosquitto_security_cleanup(true)` → `mosquitto_security_init(true)` → `mosquitto_security_apply_default` → log reopen → keepalive reinit → `broker_control__reload` → `bridge__reload`.

### 5.2 TLS / identity (listener side, `src/net.c`, `handle_connect.c`)

- `net__socket_accept` (`net.c:115-277`): optional tcp-wrappers; `set_tcp_nodelay`; context bound to listener; proxy transport selection (`enable_proxy_protocol` 1/2) or `mp_mqtt`/`mp_websockets` (builtin `http__context_init`); max_connections / global_max_connections; TLS: `SSL_new(listener->ssl_ctx)` with ex-data = context and listener, accept state.
- Server ctx (`net.c:405-512`): PSK file present → TLSv1.3 disabled; `tls_version` (default ≥1.2); no compression, server cipher preference, DH auto, no renegotiation, keylog option, cipher lists. `net__load_certificates` (`:550-…`): `require_certificate` → `VERIFY_PEER|FAIL_IF_NO_PEER_CERT`; verify callback allows expired/not-yet-valid when `disable_client_cert_date_checks` (`:282-301`); CRL file loaded (`:523-548`).
- PSK (`net.c:307-363`): `mosquitto_psk_key_get(context, hint, identity)` → plugin `PSK_KEY` events then psk_file; hex→bin; `use_identity_as_username` → `context->username = identity`.
- CONNECT (`handle_connect.c:975-1025`): with TLS and `use_identity_as_username`/`use_subject_as_username`: CONNECT username/password discarded; PSK listener requires identity; else username = cert CN / subject. Without TLS but proxy-v2 TLS info and `use_identity_as_username`+`require_certificate`: username must already be set from PROXY TLV CN. Basic auth is skipped for cert-identity listeners (`:1249-1251`).

### 5.3 PROXY protocol (`proxy_v1.c`, `proxy_v2.c`)

- v1: reads up to 108 bytes one byte at a time until CRLF; `TCP4`/`TCP6`/`UNKNOWN`; validates ports 1-65535 and addresses with `inet_pton`; sets `context->address`, `remote_port`; UNKNOWN → real socket peer (`proxy_v1.c:58-162`).
- v2: 16-byte header with 12-byte signature, version 2, cmd LOCAL/PROXY, families IPv4/IPv6/UNIX, length ≤ 500; reads address block; TLVs: `PP2_TYPE_SSL` → client flags (`have_certificate` if SSL+CERT_SESS and verify==0), sub-TLVs SSL_VERSION, SSL_CIPHER, SSL_CN (→ `context->username` if `use_identity_as_username`); `proxy_protocol_v2_require_tls` rejects non-TLS; `require_certificate` rejects without certificate (`proxy_v2.c:89-343`). LOCAL with fam 0 → `MOSQ_ERR_PROXY` after address read (`:284-291`). Config check: proxy v2 forbids `use_subject_as_username` and certfile/keyfile (`conf.c:2975-2996`).

### 5.4 `$CONTROL` (control.c, control_common.c, broker_control.c)

- Endpoint registry: `opts->plugin_callbacks.control` is a **hash keyed by exact topic**, always stored in the *global* security options (`control.c:134-153`); topic must start `$CONTROL/` and be ≥ `$CONTROL/A/v1` length; duplicates → ALREADY_EXISTS. Plugin id keeps `control_endpoints` list for `listPlugins`.
- Flow: client PUBLISH `$CONTROL/...` → normal ACL WRITE check (`handle_publish.c:371`) → `control__process` (`control.c:51-112`) → handler. Generic handler `mosquitto_control_generic_callback` (`control_common.c:94-151`): parse JSON `{"commands":[{"command":..., "correlationData":"..."}...]}`; per-command callback; replies collected into `{"responses":[...]}` and published with `mosquitto_broker_publish(NULL, response_topic, …)` (qos 0, not retained) → normal fan-out to subscribers of the response topic (so the response is subject to READ ACL of subscribers).
- Broker endpoint `$CONTROL/broker/v1` (only if `enable_control_api`, `broker_control.c:221-228`): `listPlugins` (name, version, port, control-endpoints) and `listListeners` (port, protocol, bind-address, socket-path, tls) (`:79-208,251-268`). Response topic `$CONTROL/broker/v1/response`.

### 5.5 Dynamic security plugin (`plugins/dynamic-security`)

- Options: `plugin_opt_config_file` (required, else inactive), `plugin_opt_password_init_file` (`plugin.c:56-81`). Registers CONTROL `$CONTROL/dynamic-security/v1`, BASIC_AUTH, ACL_CHECK, TICK (`plugin.c:88-128`). Only one instance (CONTROL topic uniqueness).
- Data model (`dynamic_security.h:58-156`): clients (username key; `pw`, `clientid` binding, text name/desc, `disabled`, rolelist, grouplist), groups (rolelist, clientlist), roles (6 ACL hashes: `publishClientSend`, `publishClientReceive`, `subscribeLiteral`, `subscribePattern`, `unsubscribeLiteral`, `unsubscribePattern`; `allowwildcardsubs`), anonymous group, default access {publishClientSend, publishClientReceive, subscribe, unsubscribe}, `changeIndex`, kicklist. Rolelists/grouplists kept ordered by descending priority (`HASH_ADD_INORDER`, `rolelist.c:38-44,137`); ACLs ordered by descending priority (`roles.c:202-205,258,703`); roles hash ordered by name (`roles.c:45-50,440`).
- Config file JSON keys (load): top `changeIndex`, `defaultACLAccess{…}`, `clients[]{username, disabled, iterations, salt, password, clientid, textname, textdescription, roles[]{rolename, priority}}` (`clients.c:121-223`), `groups[]{groupname, textname, textdescription, roles[], clients[]{username, priority}}`, `anonymousGroup` (`groups.c:210-315`), `roles[]{rolename, textname, textdescription, allowwildcardsubs (default true), acls[]{acltype, topic, priority, allow}}` (`roles.c:218-326`). Load order: general, roles, clients, groups (`config.c:84-88`).
- Missing config file → generate default (`config_init.c:503-553`): admin password from init file, `MOSQUITTO_DYNSEC_PASSWORD` env (≥12 chars), or random (written to `<config>.pw`, plus a `democlient`); default ACL access defaults; `unauthenticated` anonymous group; written with plain `fopen/fprintf` (not atomic).
- Save: commands that mutate call `dynsec__config_batch_save` (changeIndex++, need_save) and the CONTROL callback saves once after the whole command batch with atomic `mosquitto_write_file` (`control.c:121-135`, `config.c:211-222`).
- Auth (`auth.c:36-69`): needs username+password else DEFER; unknown user → DEFER; disabled → AUTH; clientid binding mismatch → AUTH; `mosquitto_pw_verify`.
- ACL (`acl.c:215-306`): by access type → check client's roles, then each group's roles (priority order), anonymous group for no-username; first matching ACL decides allow/deny; subscribe with wildcard and `allowwildcardsubs=false` → deny; literal sub/unsub exact hash then patterns via `mosquitto_sub_matches_acl_with_pattern`; publish via `mosquitto_topic_matches_sub_with_pattern` (`%c/%u`). No match → default access: false → DEFER; true → SUCCESS except `$CONTROL…` → DEFER. Unknown username → DEFER.
- Kicks: role/group/client changes add usernames (or NULL = anonymous) to `kicklist` (`clients.c:476,514,615,…`, `groups.c:62,612,…`, `roles.c:118,465,478`, `clientlist.c:68`); TICK drains it via `mosquitto_kick_client_by_username(name, false)` (`kicklist.c:52-65`, `tick.c:24-31`). `modifyClient` password failure kicks immediately (`clients.c:837`).
- Commands (dispatcher `control.c:34-118`): setDefaultACLAccess, getDefaultACLAccess, getDetails, createClient, deleteClient, getClient, listClients, modifyClient, setClientPassword, setClientId, addClientRole, removeClientRole, enableClient, disableClient, addGroupClient, createGroup, deleteGroup, getGroup, listGroups, modifyGroup, removeGroupClient, addGroupRole, removeGroupRole, setAnonymousGroup, getAnonymousGroup, createRole, getRole, listRoles, modifyRole, deleteRole, addRoleACL, removeRoleACL. Response topic `$CONTROL/dynamic-security/v1/response`.

### 5.6 sparkplug-aware plugin

Registers `MESSAGE_IN` only (`sparkplug-aware/plugin.c:19-36`). On topics matching `spBv1.0/+/NBIRTH/+` or `spBv1.0/+/DBIRTH/+/+` it republishes a copy, **retained**, to `$sparkplug/certificates/<original topic>` with the same qos and properties via `mosquitto_broker_publish_copy` (`on_message.c:14-37`). Original message is untouched (returns SUCCESS). Note it passes `ed->properties` (owned by the in-flight message) to a function that takes ownership (`plugin_public.c:258` stores pointer; freed in `loop.c:160`) — I did not trace whether that causes a double free; flagging only.

### 5.7 HTTP API (`src/http_api.c`, 2.1)

- Started per `listener` with `protocol http_api` (`listeners.c:299`) or an implicit local listener 127.0.0.1:9883 (`listeners.c:247` → `http_api__start_local` `http_api.c:411-446`). libmicrohttpd daemon with **`MHD_USE_AUTO_INTERNAL_THREAD`** (`:451`), optional TLS from certfile/keyfile (`:468-480`).
- Request (`:380-408`): only GET; `check_access` (`:350-378`) builds a stack context `{listener, id="http-api", username/password from HTTP Basic}` and calls **`mosquitto_basic_auth`** then **`mosquitto_acl_check(url, READ)`** → this runs plugin BASIC_AUTH/ACL_CHECK callbacks on the MHD thread.
- `/api/v1/systree` (metrics + uptime), `/api/v1/listeners` (port/path, protocol, tls, mtls, allow_anonymous), `/api/v1/version`; else static files from `http_dir` with `realpath` canonicalisation and prefix check against http_dir (`:70-135,285-333`).

### 5.8 WebSockets / unix sockets

- Builtin websockets (default): `http__read` (`http_serv.c:52-…`): picohttpparser request, GET/HEAD only, requires `Sec-WebSocket-Protocol` `mqtt` or `mqttv3.1`, `Sec-WebSocket-Version`, checks `Origin` against `websockets_origin` list (mismatch → `MOSQ_ERR_HTTP_BAD_ORIGIN`), sends 101 with accept key, then `ws__context_init` (framing in `lib/net_ws.c`, not read).
- LWS backend (`src/websockets.c`, compile-time alternative) serviced from the main loop via `lws_service` (`loop.c:246-264`) — outline only.
- Unix sockets: `listener 0 <path>` / `socket_domain`; `unlink(path)`, bind under `umask(0007)`, listen backlog 10 (`net.c:934-989`).

---

## 6. Config options used by `test/broker` and where parsed (`src/conf.c`)

Gathered from every `f.write(...)` / config list in `test/broker/*.py` and `*.conf`.

General / listener: `listener` 1926, `port` 2414, `protocol` 2437, `socket_domain` 2612, `bind_interface` 1293, `mount_point` 2292, `max_connections` 2202, `global_max_connections` 1812, `global_max_clients` 1808, `max_keepalive` 2239, `max_packet_size` 2248, `max_qos`/`maximum_qos` 2210, `max_inflight_messages` 2228, `max_inflight_bytes` 2220, `max_queued_messages` 2265, `max_queued_bytes` 2257, `max_topic_alias` 2814, `max_topic_alias_broker` 2824, `message_size_limit` 2283, `memory_limit` 2273, `retain_available` 2559, `upgrade_outgoing_qos` 2843, `queue_qos0_messages` 2502, `allow_duplicate_messages` 1123, `accept_protocol_versions` 1030, `set_tcp_nodelay` 2586, `sys_interval` 2628, `persistent_client_expiration` 2372, `check_retain_source` 1647, `connection_messages` 1762, `pid_file` 2407, `include_dir` 1861, `log_dest` 2066, `log_type` 2169, `log_timestamp` 2161, `log_timestamp_format` 2165, `clientid_prefixes` 1698, `enable_control_api` 1787, `http_dir` 1816, `websockets_origin` 2902, `websockets_headers_size` 2893, `websockets_log_level` 2887, `enable_proxy_protocol` 1795, `proxy_protocol_v2_require_tls` 2466.

Persistence: `persistence` 2360, `persistence_file` 2364, `persistence_location` 2368, `autosave_interval` 1264, `autosave_on_changes` 1271.

Security: `per_listener_settings` 2347, `allow_anonymous` 1116, `listener_allow_anonymous` 2020, `allow_zero_length_clientid` 1128, `auto_id_prefix` 1248, `listener_auto_id_prefix` 2025, `password_file` 2340, `acl_file` 1053, `psk_file` 2475, `psk_hint` 2490, `plugin`/`auth_plugin`/`global_plugin` 1184, `plugin_load` 1213, `plugin_use` 1227, `plugin_opt_*`/`auth_opt_*` 1134 (tests: `plugin_opt_config_file`, `plugin_opt_password_init_file`, `plugin_opt_db_file`, `plugin_opt_flush_period`, `plugin_opt_acl_file`, `plugin_opt_password_file`, `plugin_opt_test`), `auth_plugin_deny_special_chars` 1202, `use_identity_as_username` 2847, `use_subject_as_username` 2856, `use_username_as_clientid` 2873, `user` 2865.

TLS: `cafile` 1609, `capath` 1623, `certfile` 1633, `keyfile` 1916, `crlfile` 1766, `dhparamfile` 1776, `require_certificate` 2506, `disable_client_cert_date_checks` 1778, `tls_version` 2701, `ciphers` 1651, `ciphers_tls1.3` 1661.

Bridge: `connection` 1704, `address`/`addresses` 1061, `topic` 2713, `cleansession` 1680, `local_cleansession` 1689, `remote_clientid` 1671, `local_clientid` 2039, `remote_username` 2878, `remote_password` 2331, `local_username` 2057, `local_password` 2048, `notifications` 2304, `notifications_local_only` 2313, `notification_topic` 2322, `keepalive_interval` 1898, `restart_timeout` 2515, `idle_timeout` 1848, `start_type` 2590, `threshold` 2636, `round_robin` 2577, `try_private` 2834, `retry_interval` 2575, `bridge_protocol_version` 1460, `bridge_attempt_unsubscribe` 1306, `bridge_bind_address` 1352, `bridge_cafile` 1315, `bridge_capath` 1361, `bridge_certfile` 1371, `bridge_keyfile` 1449, `bridge_alpn` 1325, `bridge_ciphers` 1334, `bridge_ciphers_tls1.3` 1343, `bridge_identity` 1381, `bridge_psk` 1479, `bridge_insecure` 1391, `bridge_require_ocsp` 1403, `bridge_max_packet_size` 1412, `bridge_max_topic_alias` 1425, `bridge_outgoing_retain` 1440, `bridge_receive_maximum` 1489, `bridge_reload_type` 1506, `bridge_session_expiry_interval` 1523, `bridge_tcp_keepalive` 1540, `bridge_tcp_user_timeout` 1573, `bridge_tls_use_os_certs` 1591, `bridge_tls_version` 1600.

Parse-error tests (`16-config-parse-errors*.py`) additionally exercise: unknown option, empty/duplicate values, options requiring a preceding `listener`/bridge/plugin, wildcard in `mount_point`, `listener 0` without path, port > 65535.

---

## 7. Threads and locks in this scope

- The broker is a single-threaded event loop (`mosquitto_main_loop` `loop.c:175-268`); all plugin callbacks, persistence writes (built-in file and sqlite), bridge I/O, `$CONTROL` handling and `mosquitto_broker_publish` queueing run on it. No mutexes are taken in `src/` (only Coverity comments asserting single-threading, `bridge.c:502-504`, `keepalive.c:242`).
- `lib/` mutexes compile to no-ops in the broker: `COMPAT_pthread_mutex_*` are real only when `WITH_THREADING && !WITH_BROKER` (`lib/pthread_compat.h:4-26`; broker built with `-DWITH_BROKER`, `src/Makefile:7`).
- POSIX signals: handler only sets flags / `g_run` (`signals.c:45-71`), processed in loop (`signals.c:95-152`). Flags are plain `static bool` (not `sig_atomic_t`/atomic).
- Windows: `SigThreadProc` thread created in `signal__setup` (`signals.c:90,175-228`) sets the same flags (no locking).
- **HTTP API**: libmicrohttpd daemon started with `MHD_USE_AUTO_INTERNAL_THREAD` (`http_api.c:451`) — the request handler (`http_api_handler`) therefore runs outside the main loop thread (per flag semantics; MHD source not read). It reads `db.config->listeners[]`, metrics, and calls `mosquitto_basic_auth`/`mosquitto_acl_check`, i.e. iterates `plugin_callbacks` lists and invokes plugin BASIC_AUTH/ACL_CHECK callbacks (password-file hash, acl-file data, dynsec data) concurrently with the main loop, without any lock. SIGHUP reload frees/rebuilds those structures on the main thread.
- persist-sqlite: one SQLite connection, used only from broker callbacks (main thread); commit in TICK.
- LWS websockets (if built) serviced synchronously in the loop (`loop.c:246-264`).
