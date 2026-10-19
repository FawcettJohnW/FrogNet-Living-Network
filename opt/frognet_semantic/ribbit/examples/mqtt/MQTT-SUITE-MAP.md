# MQTT on Ribbit — suite map (first pass, for review before any code)

Incumbent: **Eclipse Mosquitto 2.1.2** (latest tag, `eclipse-mosquitto/mosquitto`), EPL-2.0 OR BSD-3-Clause.
Oracle: its own broker suite, `test/broker/test.py` — 278 entries, each a Python script that speaks raw MQTT
packets (v3.1, v3.1.1, v5) at `src/mosquitto -c <conf> -p <port>` and compares bytes. Same trick as Redis:
`src/mosquitto` becomes a wrapper that starts the front (and a host) with the test's config. Nothing of
Mosquitto's broker is used. Stock clients (`mosquitto_pub`/`mosquitto_sub`, paho) are the clients; 30 of
`test/client`'s scripts also start the broker and are a second, incidental oracle. `test/lib` (65) tests the
client library against fake brokers — not applicable, the client is untouched.

Method carried from Redis: the suite is the roadmap; every red gets the three questions (implementation
wrong / contract misread / question 3); nothing of Mosquitto's structure is kept because Mosquitto has it.

## Shape (same as Redis on Ribbit)

    stock MQTT client --MQTT--> front (participant) --FNW1--> RAM host: the MQTT region

Fronts hold no data; start ten, they serve one topic space. A broker is the thing that disappears: there is no
routing table, no subscriber list, no per-client queue, no bridge.

## The mapping (proposed — mine, not yet agreed)

| MQTT | Ribbit |
|---|---|
| retained message on topic T | **one cell per topic** — a coordinate holds one value, writing replaces it. Zero-length retained = withdrawal (inactive state). This is the RAM contract verbatim |
| non-retained PUBLISH | **distinct state**: one cell per message, write-order id; every subscriber sees each once, in order |
| SUBSCRIBE / filter | a **held read** past the session's watermark on the matching partial index; `+`/`#` is a region API read over topic levels (one variable per level + manifest, §3d), never a scan |
| QoS 0 | `write_nowait` |
| QoS 1/2 client→region | PUBACK / PUBREC once **the write has an id**; the PUBREL/PUBCOMP handshake is per-connection front state |
| QoS 1/2 region→client | session's **delivered-through mark cell** (participant mark, §7); inflight window / receive-maximum at the front |
| persistent session (clean start 0) | `Session.<clientid>` cells: subscriptions (one cell each), marks, will. Reconnecting to a **different front** resumes — the session is not in any process |
| offline queue | **none**. The messages are already cells; an offline session's mark simply doesn't move |
| session takeover | contested truth: claim cell per connection, write-order decides, the loser's front disconnects its client |
| `$share/group/filter` | **claims** — the XREADGROUP pattern that went green in Redis |
| will | a cell the session writes ahead; presence is freshness; published when presence lapses or on abnormal close |
| message expiry | reader arithmetic (`written_ms + interval` against now); the reduced interval on republish is derived |
| `$SYS` counts, max clients/connections | derived at read time from presence cells, never maintained |
| bridges | gone — one region, fronts anywhere (06-*, 13 tests + 2) |
| topic alias | per-connection wire compression at the front; FNWP already does this between front and host |

## Counts

| class | n | meaning |
|---|---|---|
| A | 90 | applicable: region semantics or front protocol, must go green |
| Q | 18 | applicable and a question-3 candidate (below) |
| F | 48 | applicable at the front only: TLS 16, PROXY protocol 17, websockets, unix socket, operator config, mount point, topic alias |
| D | 59 | needs a ruling from you (below) |
| N | 63 | not-applicable-by-construction: bridges (27), Mosquitto's C plugin ABI (36) |

Layers (build order): **L0** wire/CONNECT/limits (25 A) → **L1** QoS0 pub/sub, wildcards, retained (11 A, 2 Q, 1 F)
→ **L2** QoS 1/2 flows (20 A) → **L3** sessions, takeover, offline delivery (5 A, 5 Q) → **L4** will, expiry,
v5 properties (20 A, 9 Q) → **L5** shared subs, topic alias (2 Q, 3 F) → **L6** auth/ACL (9 A, 24 D)
→ **L7** persistence (25 D) → **L8** listener transports and config (44 F).

## Question-3 candidates, named in advance

1. **Time-triggered actions.** Session expiry (05-*) and will delay (07-will-delay*) require something to
   *happen* at time T with no client present. "Nothing expires anything" covers believing; it does not cover
   *publishing* a will at T. Candidate answer: the region owner's participant that reads what is dead (§5 —
   the one sanctioned server-side reclaimer) also publishes due wills. 11 tests.
2. **max_queued_messages / bytes** — limits on a queue that does not exist. As arithmetic: unread cells past a
   session's mark, counted at delivery. Whether "dropped" means skipped-and-marked is the open part. 2 tests.
3. **Takeover** — claim vs Mosquitto's "newest wins and kicks the old". Write-order on the claim gives the same
   answer; the will-on-takeover ordering is the test. 2 tests.
4. **$share** — claims; nolocal-on-shared is front validation. 2 tests.
5. **Wildcards / subscription hierarchy** — no tree exists; 03-pattern-matching is the proof the level index
   answers `+`/`#`/`$`-rules exactly. 2 tests.
6. **Message expiry on retained and will** — stale cell not believed vs. Mosquitto actively dropping. 3 tests.

## Scope (your ruling, 2026-10-13)

"The interface contract is solid and the tests must all pass. Everything else is open." Every row below is
in scope, D, F and N included: persistence (Permanent cells), dynsec, extended auth, Sparkplug, `$CONTROL`,
the HTTP API, TLS/websockets/PROXY at the front, bridges (a bridge is a participant connecting out to a
foreign broker), and Mosquitto's plugin ABI (the front hosts the plugin API). The N/D classes record what
each test exercises, not whether it is built.

## Not decided here (follows from settled rules, stated so you can veto)

C++, built from `ribbit/cpp` (one platform), host on streamingfrog, fronts anywhere; no mutex in the memory or
the front; no fallbacks; incumbent's suite + Redis-style concurrency audit as oracles; one tarball per machine
type. Lands as `examples/mqtt`.

---

## Full classification (generated by `tools_classify_suite.py`; every row says which rule fired)

| test | class | layer | why | incumbent's description |
|---|---|---|---|---|
| msg_sequence_test.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a valid CONNECT results in the correct CONNACK packet. |
| 01-bad-initial-packets.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether non-CONNECT packets as an initial packet can cause excess memory use |
| 01-connect-575314.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Check for performance of processing user-property on CONNECT |
| 01-connect-accept-protocol.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test accept_protocol_version option |
| 01-connect-allow-anonymous.py | A | L6 | auth / ACL from the host operator's files | Test whether an anonymous connection is correctly denied. |
| 01-connect-auto-id.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) |  |
| 01-connect-disconnect-v5.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | loop through the different v5 DISCONNECT reason_code/properties options. |
| 01-connect-global-max-clients.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether global_max_clients works |
| 01-connect-global-max-connections.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether global_max_connections works |
| 01-connect-listener-allow-anonymous.py | A | L6 | auth / ACL from the host operator's files | Test whether an anonymous connection is correctly denied. |
| 01-connect-max-connections.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether max_connections works with repeated connections |
| 01-connect-max-keepalive.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether max_keepalive violations are rejected for MQTT < 5.0. |
| 01-connect-take-over.py | Q | L3 | session takeover = contested truth; claim cell per connection, write-order decides | MQTT v5 session takeover test |
| 01-connect-uname-no-password-denied.py | A | L6 | auth / ACL from the host operator's files | Test whether a connection is denied if it provides just a username when it |
| 01-connect-uname-or-anon.py | A | L6 | auth / ACL from the host operator's files | Test whether an anonymous connection is correctly denied. |
| 01-connect-uname-password-denied-no-will.py | A | L6 | auth / ACL from the host operator's files | Test whether a connection is denied if it provides a correct username but |
| 01-connect-uname-password-denied.py | A | L6 | auth / ACL from the host operator's files | Test whether a connection is denied if it provides a correct username but |
| 01-connect-unix-socket.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether connections to a unix socket work |
| 01-connect-windows-line-endings.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether config files with windows line endings are accepted. |
| 01-connect-zero-length-id.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a CONNECT with a zero length client id results in the correct behaviour. |
| 01-plugin-connect-uname-password-denied.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is denied if it provides a correct username but |
| 02-shared-nolocal.py | Q | L5 | $share option rules (front validation) on top of claims | No local option is not allowed on shared subscriptions |
| 02-shared-qos0-v5.py | Q | L5 | $share = claims (as XREADGROUP in Redis) | Test whether shared subscriptions work |
| 02-subhier-crash.py | Q | L1 | subscription tree edge case: there is no subscription tree, a subscription is a held read | Test related to https://github.com/eclipse/mosquitto/issues/505 |
| 02-subpub-b2c-topic-alias.py | F | L5 | per-connection topic alias: wire compression at the front | Test whether "topic alias" works from the broker |
| 02-subpub-qos0-long-topic.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a client subscribed to a topic receives its own message sent to that topic, for long topics. |
| 02-subpub-qos0-oversize-payload.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether message size limits apply. |
| 02-subpub-qos0-queued-bytes.py | Q | L3 | max_queued_*: there is no queue; a limit on a derived count of unread cells |  |
| 02-subpub-qos0-retain-as-publish.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether a client subscribed to a topic with retain-as-published set works as expected. |
| 02-subpub-qos0-send-retain.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether "send retain" subscribe options work |
| 02-subpub-qos0-subscription-id.py | A | L4 | MQTT v5 options / properties carried in the bag | Does setting and updating subscription identifiers work as expected? |
| 02-subpub-qos0-topic-alias-unknown.py | F | L5 | per-connection topic alias: wire compression at the front | Test whether "topic alias" works to the broker |
| 02-subpub-qos0-topic-alias.py | F | L5 | per-connection topic alias: wire compression at the front | Test whether "topic alias" works to the broker |
| 02-subpub-qos1-message-expiry-retain.py | Q | L4 | retained + expiry: a stale cell is simply not believed | Test whether the broker reduces the message expiry interval when republishing |
| 02-subpub-qos1-message-expiry-will.py | Q | L4 | will + expiry | Test whether the broker reduces the message expiry interval when republishing a will. |
| 02-subpub-qos1-message-expiry.py | Q | L4 | expiry is reader arithmetic; the republished interval is derived | Test whether the broker reduces the message expiry interval when republishing. |
| 02-subpub-qos1-nolocal.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether a client subscribed to a topic does not receive its own message |
| 02-subpub-qos1-oversize-payload.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether message size limits apply. |
| 02-subpub-qos1.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 02-subpub-qos2-1322.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test for issue 1322: |
| 02-subpub-qos2-max-inflight-bytes.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Does the broker respect max_inflight_bytes? |
| 02-subpub-qos2-pubrec-error.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBREC with reason code >= 0x80 is handled correctly |
| 02-subpub-qos2-receive-maximum-1.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Does the broker respect receive maximum==1? |
| 02-subpub-qos2-receive-maximum-2.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Does the broker respect receive maximum==2? |
| 02-subpub-qos2.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 02-subpub-recover-subscriptions.py | A | L3 | sessions: subscriptions and delivered-through marks are cells | Check whether a durable client keeps its subscriptions on reconnecting. |
| 02-subscribe-dollar-v5.py | A | L1 | publish = distinct-state cell; subscribe = held read on the matching partial index | Test whether a SUBSCRIBE to $SYS or $share succeeds |
| 02-subscribe-invalid-utf8.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a SUBSCRIBE to a topic with an invalid UTF-8 topic fails |
| 02-subscribe-long-topic.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a SUBSCRIBE to a topic with 65535 hierarchy characters fails |
| 02-subscribe-persistence-flipflop.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test switching between persistence and a clean session. |
| 03-publish-qos1-queued-bytes.py | Q | L3 | max_queued_*: offline "queue" is a watermark, the limit is arithmetic | Test whether a PUBLISH to a topic with an offline subscriber results in a queued message |
| 03-pattern-matching.py | Q | L1 | wildcards +/# = partial index over levels: region API read, not a scan |  |
| 03-publish-bad-flags.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether the broker handles malformed packets correctly - PUBLISH |
| 03-publish-b2c-disconnect-qos1.py | A | L3 | sessions: subscriptions and delivered-through marks are cells | Does an interrupted QoS 1 flow from broker to client get handled correctly? |
| 03-publish-b2c-disconnect-qos2.py | A | L3 | sessions: subscriptions and delivered-through marks are cells | Does an interrupted QoS 1 flow from broker to client get handled correctly? |
| 03-publish-b2c-qos1-len.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Check whether the broker handles a v5 PUBACK with all combinations |
| 03-publish-b2c-qos2-len.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Check whether the broker handles a v5 PUBREC, PUBCOMP with all combinations |
| 03-publish-c2b-disconnect-qos2.py | A | L3 | sessions: subscriptions and delivered-through marks are cells |  |
| 03-publish-c2b-qos2-len.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Check whether the broker handles a v5 PUBREL with all combinations |
| 03-publish-dollar-v5.py | A | L1 | publish = distinct-state cell; subscribe = held read on the matching partial index | Test whether a PUBLISH to $ topics QoS 1 results in the expected PUBACK packet. |
| 03-publish-dollar.py | A | L1 | publish = distinct-state cell; subscribe = held read on the matching partial index | Test whether a PUBLISH to a topic starting with $ succeeds |
| 03-publish-invalid-utf8.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a PUBLISH to a topic with an invalid UTF-8 topic fails |
| 03-publish-long-topic.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a PUBLISH to a topic with 65535 hierarchy characters fails |
| 03-publish-qos1-max-inflight-expire.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 1 results in the correct packet flow for a subscriber. |
| 03-publish-qos1-max-inflight.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 1 results in the correct packet flow. |
| 03-publish-qos1-no-subscribers-v5.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 1 results in the correct PUBACK |
| 03-publish-qos1-retain-disabled.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH with a retain set when retains are disabled results in |
| 03-publish-qos1.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 1 results in the correct PUBACK packet. |
| 03-publish-qos2-dup.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front |  |
| 03-publish-qos2-max-inflight-exceeded.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | What does the broker do if an MQTT v5 client doesn't respect max_inflight_messages? |
| 03-publish-qos2-max-inflight.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 2 results in the correct packet flow. |
| 03-publish-qos2-reuse-mid.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test what happens if a client reuses an in-use mid with a different message. |
| 03-publish-qos2.py | A | L2 | QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front | Test whether a PUBLISH to a topic with QoS 2 results in the correct packet flow. |
| 04-retain-check-source-persist.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test for CVE-2018-12546, with the broker being stopped to write the persistence file. |
| 04-retain-check-source.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test for CVE-2018-12546 |
| 04-retain-clear-multiple.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Exercise multi-level retain clearing |
| 04-retain-qos0-clear.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH is cleared when a zero length retained |
| 04-retain-qos0-fresh.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH to a topic with QoS 0 is sent with |
| 04-retain-qos0-repeated.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH to a topic with QoS 0 is actually retained |
| 04-retain-qos0.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH to a topic with QoS 0 is actually retained. |
| 04-retain-qos1-qos0.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH to a topic with QoS 1 is retained. |
| 04-retain-upgrade-outgoing-qos.py | A | L1 | retained = one cell per topic; writing replaces; empty payload withdraws | Test whether a retained PUBLISH to a topic with QoS 0 is sent with subscriber QoS |
| 04-retain-check-source-persist-diff-port.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test for CVE-2018-12546, with the broker being stopped to write the persistence file, plus subscriber on different port. |
| 05-clean-session-qos1.py | A | L3 | sessions: subscriptions and delivered-through marks are cells | Test whether a clean session client has a QoS 1 message queued for it. |
| 05-session-expiry-v5.py | Q | L3 | session expiry acts at a time: who acts, with no timer above the memory | MQTT v5. Test whether session expiry interval works correctly. |
| 05-session-expiry-kick.py | Q | L3 | session expiry acts at a time | MQTT v5. Test whether session expiry interval works correctly. |
| 06-bridge-b2br-disconnect-qos1.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge resend a QoS=1 message correctly after a disconnect? |
| 06-bridge-b2br-disconnect-qos2.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge resend a QoS=1 message correctly after a disconnect? |
| 06-bridge-b2br-late-connection-retain.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge queue up retained messages correctly if the remote broker starts up late? |
| 06-bridge-b2br-late-connection.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge queue up messages correctly if the remote broker starts up late? |
| 06-bridge-b2br-remapping.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test remapping of topic name for incoming message |
| 06-bridge-br2b-disconnect-qos1.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge resend a QoS=1 message correctly after a disconnect? |
| 06-bridge-br2b-disconnect-qos2.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge resend a QoS=1 message correctly after a disconnect? |
| 06-bridge-br2b-remapping.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test remapping of topic name for outgoing message |
| 06-bridge-clean-session-csF-lcsF.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-clean-session-csF-lcsN.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-clean-session-csF-lcsT.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-clean-session-csT-lcsF.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-clean-session-csT-lcsN.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-clean-session-csT-lcsT.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a broker handles cleansession and local_cleansession correctly on bridges |
| 06-bridge-fail-persist-resend-qos1.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a bridge can cope with an unknown PUBACK |
| 06-bridge-fail-persist-resend-qos2.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a bridge can cope with an unknown PUBACK |
| 06-bridge-no-local.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Check whether an incoming bridge connection receives its own messages. It |
| 06-bridge-outgoing-retain.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge with bridge_outgoing_retain set to false not set the retain bit |
| 06-bridge-per-listener-settings.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test remapping of topic name for incoming message |
| 06-bridge-reconnect-local-out.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a bridge topics work correctly after reconnection. |
| 06-bridge-remote-shutdown.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Test whether a bridge topics work correctly after reconnection. |
| 06-bridge-config-reload.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | tests that bridge configuration is reloaded on signal |
| 06-bridge-remap-receive-wildcard.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Does a bridge resend a QoS=1 message correctly after a disconnect? |
| 07-will-control.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a client setting a will with $CONTROL in is denied |
| 07-will-delay-invalid-573191.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test for https://bugs.eclipse.org/bugs/show_bug.cgi?id=573191 |
| 07-will-delay-reconnect.py | Q | L4 | will delay cancelled by reconnect: reconnect is a newer truth than the pending will | Test whether a client with a will delay handles correctly on the client reconnecting |
| 07-will-delay-recover.py | Q | L4 | will delay + recovered session | Test whether a client with a will delay recovers on the client reconnecting |
| 07-will-delay-session-expiry.py | Q | L4 | will delay vs session expiry: whichever time comes first | Test whether a client that connects with a will delay that is longer than |
| 07-will-delay-session-expiry2.py | Q | L4 | will delay vs session expiry | Test whether a client that connects with a will delay that is shorter than |
| 07-will-delay.py | Q | L4 | will delay acts at a time | Test whether a client will is transmitted with a delay correctly. |
| 07-will-disconnect-with-will.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a client will is transmitted when a client disconnects with DISCONNECT with will. |
| 07-will-invalid-utf8.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a will topic with invalid UTF-8 fails |
| 07-will-no-flag.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a connection is disconnected if it sets the will flag but does |
| 07-will-null-topic.py | A | L4 | will = a cell the session writes ahead; published when presence lapses |  |
| 07-will-null.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a client will is transmitted correctly with a null payload. |
| 07-will-oversize-payload.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether a client will that is too large is handled |
| 07-will-per-listener.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a client will is transmitted correctly, with per_listener_settings enabled |
| 07-will-properties.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test for bug #1244. This occurs if a V5 will message is used where the first |
| 07-will-qos0.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a client will is transmitted correctly. |
| 07-will-reconnect-1273.py | A | L4 | will = a cell the session writes ahead; published when presence lapses | Test whether a persistent client that disconnects with DISCONNECT has its |
| 07-will-takeover.py | Q | L4 | takeover + will: the losing claim publishes the will | Test whether a will is published when a client takes over an existing session that has a will set. |
| 08-ssl-bridge.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere |  |
| 08-ssl-connect-cert-auth-crl.py | F | L8 | TLS on the listener |  |
| 08-ssl-connect-cert-auth-expired.py | F | L8 | TLS on the listener | Test whether a valid CONNECT results in the correct CONNACK packet using an |
| 08-ssl-connect-cert-auth-expired-allowed.py | F | L8 | TLS on the listener | Check the `disable_client_cert_date_checks` option. |
| 08-ssl-connect-cert-auth-revoked.py | F | L8 | TLS on the listener |  |
| 08-ssl-connect-cert-auth-without.py | F | L8 | TLS on the listener | Test whether a client can connect without an SSL certificate if one is required. |
| 08-ssl-connect-cert-auth.py | F | L8 | TLS on the listener | Test whether a valid CONNECT results in the correct CONNACK packet using an SSL connection. |
| 08-ssl-connect-dhparam.py | F | L8 | TLS on the listener | Test whether a valid CONNECT results in the correct CONNACK packet using an SSL connection. |
| 08-ssl-connect-identity.py | F | L8 | TLS on the listener | Client connects with a certificate to a server that has use_identity_as_username=true. Shouldn't be rejected. |
| 08-ssl-connect-no-auth-wrong-ca.py | F | L8 | TLS on the listener | Test whether a valid CONNECT results in the correct CONNACK packet using an SSL connection. |
| 08-ssl-connect-no-auth.py | F | L8 | TLS on the listener | Test whether a valid CONNECT results in the correct CONNACK packet using an SSL connection. |
| 08-ssl-connect-no-identity.py | F | L8 | TLS on the listener | Client connects without a certificate to a server that has use_identity_as_username=true. Should be rejected. |
| 08-ssl-hup-disconnect.py | F | L8 | TLS on the listener | Test whether a client connected with a client certificate when |
| 08-tls-psk-pub.py | F | L8 | TLS on the listener |  |
| 08-tls-psk-bridge.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere |  |
| 09-acl-access-variants.py | A | L6 | auth / ACL from the host operator's files | Check access |
| 09-acl-change.py | A | L6 | auth / ACL from the host operator's files | Check whether messages deliver or not after some access is revoked. |
| 09-acl-empty-file.py | A | L6 | auth / ACL from the host operator's files | Test for CVE-2018-xxxxx |
| 09-auth-bad-method.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether sending an Authentication Method produces the correct response |
| 09-extended-auth-change-username.py | D | L6 | MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any? | Check whether an extended auth plugin can change the username of a client. |
| 09-extended-auth-multistep-reauth.py | D | L6 | MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any? | First auth |
| 09-extended-auth-multistep.py | D | L6 | MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any? | Server to client |
| 09-extended-auth-reauth.py | D | L6 | MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any? | First authentication succeeds |
| 09-extended-auth-single.py | D | L6 | MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any? | Multi tests for extended auth with a single step. |
| 09-plugin-acl-access-variants.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Check access |
| 09-plugin-acl-change.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | A clean start=False client connects, and publishes to a topic it has access |
| 09-plugin-auth-acl-pub.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Bug specific test - if a QoS2 publish is denied, then we publish again with |
| 09-plugin-auth-acl-pub-prop.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Bug specific test - if a QoS2 publish is denied, then we publish again with |
| 09-plugin-auth-acl-sub-denied.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test topic subscription. All SUBSCRIBE requests are denied. Check this |
| 09-plugin-auth-acl-sub.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test topic subscription. All topic are allowed but not using wildcard in subscribe. |
| 09-plugin-auth-context-params.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether message parameters are passed to the plugin acl check function. |
| 09-plugin-auth-defer-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection fail when using a auth_plugin that defer authentication. |
| 09-plugin-auth-defer-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-msg-params.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether message parameters are passed to the plugin acl check function. |
| 09-plugin-auth-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v2-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v2-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v3-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v3-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v4-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v4-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v5-unpwd-fail.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-auth-v5-unpwd-success.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a connection is successful with correct username and password |
| 09-plugin-bad.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Check whether incomplete plugins are handled ok |
| 09-plugin-change-id.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-evt-client-offline.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-evt-message-in.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-evt-message-out.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-evt-psk-key.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the tick event |
| 09-plugin-evt-reload.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the reload event |
| 09-plugin-evt-subscribe.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-evt-tick.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the tick event |
| 09-plugin-evt-unsubscribe.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-delayed-auth.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether message parameters are passed to the plugin acl check function. |
| 09-plugin-load-acl.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the tick event |
| 09-plugin-load-basic-auth.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the tick event |
| 09-plugin-load-extended-auth.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Test whether a plugin can subscribe to the tick event |
| 09-plugin-publish.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests |  |
| 09-plugin-unsupported.py | N | - | Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests | Check whether unsupported plugin versions are handled ok |
| 09-pwfile-parse-invalid.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test for CVE-2018-xxxxx. |
| 10-listener-mount-point.py | F | L1 | mount_point: listener topic prefix at the front |  |
| 11-message-expiry.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test whether the broker reduces the message expiry interval when republishing. |
| 11-persistence-autosave-changes.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 11-persistent-subscription.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 11-persistent-subscription-no-local.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 11-pub-props.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Does a persisted PUBLISH keep its properties? |
| 11-subscription-id.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Test whether a client message maintains its subscription id when persisted and restored. |
| 12-prop-assigned-client-identifier.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether sending a non zero session expiry interval in DISCONNECT after |
| 12-prop-maximum-packet-size-broker.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Check whether the broker disconnects a client nicely when they send a too large packet. |
| 12-prop-maximum-packet-size-publish-qos1.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether maximum packet size is honoured on a PUBLISH to a client |
| 12-prop-maximum-packet-size-publish-qos2.py | A | L0 | wire / CONNECT / limits (front, counts derived from presence) | Test whether maximum packet size is honoured on a PUBLISH to a client |
| 12-prop-response-topic-correlation-data.py | A | L4 | MQTT v5 options / properties carried in the bag | client 1 subscribes to normal-topic |
| 12-prop-response-topic.py | A | L4 | MQTT v5 options / properties carried in the bag | client 1 subscribes to normal-topic |
| 12-prop-server-keepalive.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether sending a non zero session expiry interval in DISCONNECT after |
| 12-prop-subpub-content-type.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 12-prop-subpub-payload-format.py | A | L4 | MQTT v5 options / properties carried in the bag | Test whether a client subscribed to a topic receives its own message sent to that topic. |
| 13-websocket-bad-origin.py | F | L8 | websockets on the listener |  |
| 14-dynsec-acl.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Test ACL for allow/deny. This does not consider ACL priority and the ACLs do not overlap. |
| 14-dynsec-allow-wildcard.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Test for allowwildcardsubs behaviour |
| 14-dynsec-anon-group.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Test the anonymous group support by adding a group, setting the anon group, adding a role to the group and checking a subscription. |
| 14-dynsec-auth.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | No password defined, this client should never be able to connect. |
| 14-dynsec-client-invalid.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Check invalid inputs for client commands |
| 14-dynsec-client.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-config-init-env.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-config-init-file.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-config-init-random.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-default-access.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | This tests the default ACL type access behaviour for when no ACL matches. |
| 14-dynsec-disable-client.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-group-invalid.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Check invalid inputs for group commands |
| 14-dynsec-group.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-modify-client.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-modify-group.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-modify-role.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 14-dynsec-plugin-invalid.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Check invalid inputs for plugin commands |
| 14-dynsec-role-invalid.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? | Check invalid inputs for role commands |
| 14-dynsec-role.py | D | L6 | Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope? |  |
| 15-persist-bridge-queue.py | N | - | bridging brokers copies a topic tree between processes; one region, fronts anywhere | Start two brokers one bridging forward to the second one (the bridge target). |
| 15-persist-client-msg-in-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, start a QoS 2 flow, disconnect, restore, carry on with the |
| 15-persist-client-msg-in-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, start a QoS 2 flow, disconnect, restore, carry on with the |
| 15-persist-client-msg-out-clear-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, send a message with a |
| 15-persist-client-msg-out-dup-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag |  |
| 15-persist-client-msg-out-queue-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, send a message with a |
| 15-persist-client-msg-out-v3-1-1-db.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, send a message with a |
| 15-persist-client-msg-out-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, send a message with a |
| 15-persist-client-msg-out-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, send a message with a |
| 15-persist-client-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, check it is restored, clear the client, check it is not there. |
| 15-persist-client-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, check it is restored, clear the client, check it is not there. |
| 15-persist-publish-properties-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Publish a retained messages, check they are restored, with properties attached |
| 15-persist-retain-clear.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Publish retained messages, check they are restored, delete one |
| 15-persist-retain-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Publish a retained messages, check they are restored |
| 15-persist-retain-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Publish a retained messages, check they are restored |
| 15-persist-subscription-v3-1-1.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, restore, reconnect, send a |
| 15-persist-subscription-v5-0.py | D | L7 | broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag | Connect a client, add a subscription, disconnect, restore, reconnect, send a |
| 16-cmd-args.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether command line args are handled |
| 16-config-huge.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test many different config options at once, and also reloading. This is |
| 16-config-includedir.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether include_dir works properly |
| 16-config-missing.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether config parse errors are handled |
| 16-config-parse-errors-tls.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether config parse errors are handled |
| 16-config-parse-errors-tls-psk.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether config parse errors are handled |
| 16-config-parse-errors-without-tls.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) | Test whether config parse errors are handled |
| 17-control-list-listeners.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? | Test $CONTROL/broker/v1 listListeners |
| 17-control-list-plugins.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? | Test $CONTROL/broker/v1 listListeners |
| 17-control-missing-endpoint.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? | Test $CONTROL/broker/v1 listListeners |
| 20-sparkplug-compliance.py | D | - | Sparkplug-aware plugin - in scope? |  |
| 20-sparkplug-aware.py | D | - | Sparkplug-aware plugin - in scope? |  |
| 21-proxy-bad-version.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v1-bad.py | F | L8 | PROXY protocol on the listener | Basic |
| 21-proxy-v1-success.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-bad-config.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-bad-header.py | F | L8 | PROXY protocol on the listener | Bad magic |
| 21-proxy-v2-local.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-ipv4.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-ipv6.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-unix.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-websockets.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-long-tlv.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-ssl-require-cert-failure.py | F | L8 | PROXY protocol on the listener | No SSL at all |
| 21-proxy-v2-ssl-require-cert-success.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-ssl-common-name-failure.py | F | L8 | PROXY protocol on the listener | No SSL at all |
| 21-proxy-v2-ssl-common-name-success.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-lost-connection.py | F | L8 | PROXY protocol on the listener | This test essentially should never fail, but it covers a code path that is |
| 21-proxy-v2-ssl-cipher.py | F | L8 | PROXY protocol on the listener |  |
| 21-proxy-v2-ssl-require-tls-failure.py | F | L8 | PROXY protocol on the listener | No SSL at all |
| 21-proxy-v2-ssl-require-tls-success.py | F | L8 | PROXY protocol on the listener |  |
| 22-http-api-acl.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? |  |
| 22-http-api-api.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? |  |
| 22-http-api-auth.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? |  |
| 22-http-api-file.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? |  |
| 22-http-api-tls.py | D | - | broker admin surface ($CONTROL/http_api) - in scope? |  |
| 23-security-acl-file-reload.py | F | L8 | operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS) |  |
