# MQTT on Ribbit — the question-3 register

What Mosquitto's contract, as its own suite tests it, assumes that a shared region answers differently. Two kinds of
entry: **eliminations** (Mosquitto needed machinery for a property; the region meets it with state, and the tests are
green) and **disagreements** (kept, with the reason). Nothing here is a bug list.

## Eliminations

1. **The subscription trees and the fan-out.** Mosquitto's `db.normal_subs` / `db.shared_subs` with raw
   `context*` leaves, rewritten on every takeover. Here: lock-free tries in the region keyed by client id; a takeover
   rewrites nothing because nothing points at a connection. Mosquitto's walk (`sub__search`, `subs__process`) is
   ported exactly, so who receives what, and in what order, is Mosquitto's.
2. **Retained messages.** One cell per topic; writing replaces; empty withdraws. Retained-before-live holds because the
   retained copies are inserted in the same SUBSCRIBE handling, before any later delivery reaches the client's lists.
3. **Session takeover across fronts.** Mosquitto finds the old context in its table. Here the connecting front claims
   the client (one exchange of the client's owner word -- the newer claim wins, nobody arbitrates), writes that it
   wants the session, and parks the CONNECT where Mosquitto parks a delayed authentication. The holding front does the
   old context's half of Mosquitto's takeover (its will, its DISCONNECT 0x8E), writes the session whole and lets it go;
   Mosquitto's own takeover code then runs unchanged on the handed-over session. Deliveries that reached the old front
   after the claim go on to the new one; deliveries that reach the new one while it waits are kept and given after.
4. **Global limits** (`global_max_clients`, `global_max_connections`, `max_connections`). Mosquitto counts its own
   tables. Here each front publishes its own counts (one writer per slot) and a limit reads the others' and adds its
   own exact count -- derived, never a shared counter.
5. **Signals** (`SIGHUP` reload, `SIGUSR1` backup, ...). Mosquitto's handler sets a flag the one loop clears. Here a
   generation each front follows, and every front's loop is woken -- a flag one thread clears reaches only that thread.
6. **$SYS metrics.** Mosquitto's one table, maintained by its one thread. Here each front counts into its own row
   and writes its own gauges there ([METRIC_ROWS_V1]); the first front sums the rows when it publishes. The retained
   count is derived in the region (slots filled minus slots emptied, per thread). One writer per cell; no counter is
   shared on the hot path (the first threaded build had one atomic table: 3% of the profile in cache-line traffic).
7. **Pacing.** Mosquitto's publisher is paced by construction: the thread that reads a publish also delivers it. Across
   fronts, a publisher could outrun a subscriber's front without limit. Here a front, before reading more input,
   gives up its core while a front it just delivered to has more than 1024 deliveries undrained (at most 2 ms per
   loop; 50 ms past half that front's hold limit) ([PACED_PUBLISH_V1]) -- read from that front's own counts (pushed
   by each thread, taken by it, held by it), no lock. A QoS 1/2 delivery to a client with a full queue is held, not
   taken, so it paces the publisher instead of being dropped by the queue limit ([HOLD_FOR_ROOM_V1]). On the same
   front the publisher's socket is paused until its held deliveries have room. Stock drops an acknowledged QoS 1/2
   message when an online subscriber's queue is full. Here that is deferred, not abolished: a delivery is held at
   most 1 s and at most 16384 are held per front; past either bound it is delivered and Mosquitto's own queue limit
   applies, exactly as in stock (harness/hold_limits.py drives both bounds; RESULTS.md has the counts). The suite is
   unchanged by it.

## Disagreements and limits (kept)

1. **Time-triggered actions stay with the session's holder.** Session expiry and will delay need something to *act* at
   a time with no client present. They stay with the front holding the session (Mosquitto's own timers, unchanged):
   the holder is the participant responsible for that truth. If that front dies, its sessions go with it, as a
   Mosquitto process's do. No clock participant was added to the region.
2. **The offline queue is the holder's.** Queued messages for an offline session live in its holding front's lists
   (Mosquitto's `msgs_out`), and move whole when the client reconnects elsewhere. `max_queued_*` therefore behaves
   exactly as Mosquitto's.
3. **Shared subscriptions rotate by a counter.** Mosquitto moves the chosen member to the tail of a list; the region
   takes `turn++ % members` in join order. Same distribution while membership is stable; after a member leaves the
   next pick may differ from Mosquitto's. The suite's `$share` tests are green.
4. **An expired retained message stays in the region until replaced.** Mosquitto's periodic expiry removes it; here the
   reader's arithmetic simply does not deliver it. Not visible to a client.
5. **Plugins are one per process.** Mosquitto's plugin ABI assumes one broker thread (plugins keep state with no locks).
   With `MQTT_THREADS > 1` a config with plugins, or with `persistence true`, is refused at start-up, loudly; the shape
   for those is several front *processes* on one region (`MQTT_REGION=host:port`), each with its own plugin instance.
6. **Remote fronts' limits lag by one round trip.** Over FNW1 (`MQTT_REGION=host:port` or `own`) a front's counts
   ride its pipelines into its slot, and each reply returns the other fronts' sums ([FRONT_COUNTS_V1]): `global_max_*`
   is enforced against this front's exact count plus the others' as of its last reply.
10. **A remote publish completes with its own answer.** Mosquitto queues a publish's deliveries and sends its PUBACK /
   PUBCOMP before it reads the next packet. A remote front sends them when that publish's answer comes back from
   mqtt-ram (one round trip, overlapped with the loop), in the same order -- own deliveries, then the ack. One client's
   publishes complete in its order; different clients' complete as each finishes ([FAN_OUT_V1]). A packet from the same
   client that needs no region (PINGREQ) can be answered before an earlier publish's ack.
8. **QoS 0 to a backlogged front is dropped before it is queued.** Mosquitto drops a QoS 0 message for a subscriber
   whose outgoing packets number `max_queued_messages` (`db__ready_for_flight`); that check still runs, unchanged, in
   the subscriber's front. In addition, a QoS 0 delivery bound for another front whose undrained deliveries
   exceed `max_queued_messages` x its clients is dropped in the region, counted in
   `$SYS/broker/publish/messages/dropped`, and logged once per episode ([QOS0_BACKLOG_V1]). Without it a slow front's
   backlog grew without bound (measured: 364 MB RSS and seconds of latency under QoS 0 load at 2 threads; 17 MB after).
   `max_queued_messages 0` turns it off, as it turns Mosquitto's off. Remote fronts are bounded the same way: their counts reach the region with their pipelines.
9. **Cross-front order during a handover.** A delivery forwarded from the old front can arrive after one sent straight to
   the new front by a later publish. MQTT orders messages per publisher per topic, and a publisher's messages go
   through one front in order; within one front order is Mosquitto's.
