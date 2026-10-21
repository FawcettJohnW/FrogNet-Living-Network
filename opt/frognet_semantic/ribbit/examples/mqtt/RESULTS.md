# MQTT on Ribbit — measured results

Machine: a 2-vCPU cloud sandbox (Intel Xeon @ 2.10 GHz). The load driver (`mqtt-load`, 2 threads) runs on the same
two cores as the broker, so every multithreaded number here is a broker sharing its cores with its own load.
Stock = Mosquitto 2.1.2 built from its release unmodified. t1/t2/t4 = this front with `MQTT_THREADS=1/2/4` and the
region resident. Files: `results/`.

## Mosquitto's suite (276 tests run; 2 more are commented out upstream)

| | passed | first attempt | wall vs stock | broker CPU user / sys vs stock |
|---|---|---|---|---|
| stock | 272/276 | 272 | — | — |
| ribbit, 1 thread, region resident | 272/276 | 271 | x0.99 | x1.03 / x1.12 |
| ribbit, region in `mqtt-ram` (`MQTT_REGION=own`), MQTT API on the permanent sockets | 272/276 | 272 | x1.01 | x1.61 / x2.15 (front and host together) |
| ribbit, `MQTT_THREADS=2` | 178/276 | 177 | | |
| ribbit, region in `mqtt-ram`, `MQTT_THREADS=2` | 178/276 | 177 | | |

The 4 failing everywhere are `22-http-api-{acl,api,auth,tls}`: they need IPv6, which this sandbox lacks; stock fails
them identically. At 2 threads, the other 94 not passing are configurations the threaded front refuses at start by
design — plugins (incl. dynamic security and the sqlite persistence plugin) and `persistence true`, whose ABI assumes
one broker thread (QUESTION-3 limit 5). Every test not refused passes.

`bytes test->broker x1.55` in suite-compare-resident.md is one test, `01-bad-initial-packets`: it pours 100 MB per
socket until the broker closes it, so the count is how much the kernel accepted before the close — timing, not
traffic. `15-persist-client-v5-0` (a client closes, then the test terminates the broker at once) needed a retry in some of ours
under the suite's parallel load; it passes alone, 5 of 5, on ours and on stock. Files (final build): `v14-res.jsonl`,
`v14-own.jsonl`, `v14-t2.jsonl`, `v14-own-t2.jsonl`; stock `v7-stock.jsonl`; `suite-compare-*.md|csv`.

## Load, head to head (`load-v11.jsonl`: every cell 3 runs of 5 s, median)

| scenario | stock | t1 | t2 | t4 | t1/stock | t2/stock | t4/stock |
|---|---|---|---|---|---|---|---|
| qos0-4x4 | 398.6k | 372.6k | 316.6k | 291.1k | 0.93 | 0.79 | 0.73 |
| qos1-4x4 | 129.3k | 122.9k | 139.8k | 184.2k | 0.95 | 1.08 | 1.42 |
| qos2-4x4 | 74.8k | 67.0k | 100.8k | 83.8k | 0.90 | 1.35 | 1.12 |
| qos1-1x1 | 131.3k | 121.9k | 110.6k | 123.9k | 0.93 | 0.84 | 0.94 |
| fanout-qos0-1x16 | 309.4k | 300.3k | 239.8k | 319.1k | 0.97 | 0.78 | 1.03 |
| fanin-qos1-16x1 | 15.9k | 15.5k | 17.1k | 5.5k | 0.97 | 1.07 | 0.35 |
| many-qos1-32x32 | 134.0k | 117.7k | 152.1k | 148.8k | 0.88 | 1.14 | 1.11 |
| latency-qos1-1k (p50 µs) | 160 | 152 | 152 | 176 | | | |

Received messages per second. Broker RSS: stock 8–9 MB; t1 11–12 MB; t2 12–18 MB; t4 14–25 MB. Full tables (latency,
CPU per 1k messages, RSS): `load-v11.md`.

Variance on this box is large for the threaded rows: the same t2 binary measured 145k, 317k, 329k (and 474k in a
probe) on qos0-4x4 in consecutive runs. The single-thread rows are stable to a few percent.

## What the measurements changed

Each step below was a measured problem, profiled with `perf`, fixed, and measured again.

| step | what was measured | change | after |
|---|---|---|---|
| lazy message ([LAZY_MESSAGE_V1]) | callgrind: malloc/free ~20%, a Msg and client-id copies per publish even when every subscriber is on the publishing front | own deliveries handed over inline under the epoch guard; message made only if another front or the retained slot keeps it; per-front message ids | t1/stock, each measured in the same run: qos0 0.78 → 0.91–0.93, qos1 0.82–0.86 → 0.95–0.98 |
| batched wakes ([BATCHED_WAKE_V1]) | t2 qos0: 144k received of 276k sent, 368 MB RSS, p50 2.75 s — one eventfd write and one epoll wake per cross-front message | a push only marks its target; one wake per target per loop iteration | probe: t2 qos0 270k, 46 MB; but the full matrix still showed 61–557 MB at t2/t4 — next row |
| metric rows ([METRIC_ROWS_V1]) | perf: 3.1% in `metrics__int_inc` — one atomic `$SYS` table every front fought over | each front counts in its own row; front 0 sums | removed from the profile |
| QoS 0 backlog bound ([QOS0_BACKLOG_V1]) | t2/t4 qos0: RSS 363–580 MB, latency in seconds — deliveries piling up before Mosquitto's own per-client drop could see them | drop QoS 0 to a co-resident front more than `max_queued_messages` x clients behind; counted as Mosquitto's dropped metric | qos0 RSS t2 14–16 MB, t4 22–23 MB (all 3 runs) |
| paced publishing ([PACED_PUBLISH_V1]) | fan-in at t4: 4.3k; publisher fronts outran the one subscriber front | a front yields its core while a front it delivered to is >4096 behind (≤2 ms) | t4 fan-in 4.3k → 5.5–8.0k; t2 fan-in 17.1–22.9k (stock 15.9–17.4k) |

What `perf` attributes to the kernel and not to us: 6% in the TCP socket lock inside `send()` (loopback softirq on the
other core) — stock pays it too.

## Back-pressure instead of drops (`load-v17.jsonl`: 2-core sandbox, every cell 3 runs of 5 s, median)

A QoS 1/2 delivery to a connected subscriber with a full queue is held, and the publisher that sent it is paused at its
socket -- on the same front, and across fronts when the target front is behind ([HOLD_FOR_ROOM_V1]). Stock Mosquitto
drops it instead, after acknowledging it to the publisher. In these runs every QoS 1/2 cell lost 0 except stock's, and
neither bound (16384 held, 1 s) was reached; past them loss returns (next sections).

**received msgs/s**

| scenario | stock | r1 | r2 | r4 | own1 | own2 | own4 | r1/stock | r2/stock | r4/stock | own1/stock | own2/stock | own4/stock |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qos0-4x4 | 509.9k | 488.4k | 511.5k | 466.6k | 335.8k | 283.3k | 201.5k | 0.96 | 1.00 | 0.92 | 0.66 | 0.56 | 0.40 |
| qos1-4x4 | 168.5k | 152.5k | 209.4k | 192.9k | 163.8k | 160.7k | 162.2k | 0.91 | 1.24 | 1.15 | 0.97 | 0.95 | 0.96 |
| qos2-4x4 | 89.1k | 85.8k | 119.2k | 134.7k | 93.7k | 91.7k | 93.8k | 0.96 | 1.34 | 1.51 | 1.05 | 1.03 | 1.05 |
| qos1-1x1 | 151.4k | 153.6k | 176.8k | 138.4k | 70.9k | 79.2k | 90.2k | 1.01 | 1.17 | 0.91 | 0.47 | 0.52 | 0.60 |
| fanout-qos0-1x16 | 314.9k | 368.1k | 344.5k | 395.9k | 354.3k | 356.8k | 376.8k | 1.17 | 1.09 | 1.26 | 1.13 | 1.13 | 1.20 |
| fanin-qos1-16x1 | 18.5k | 144.4k | 148.5k | 158.6k | 157.0k | 104.3k | 73.8k | 7.82 | 8.04 | 8.59 | 8.50 | 5.65 | 3.99 |
| many-qos1-32x32 | 170.8k | 150.8k | 197.0k | 252.0k | 156.3k | 90.2k | 214.7k | 0.88 | 1.15 | 1.47 | 0.91 | 0.53 | 1.26 |
| latency-qos1-1k | 1.0k | 1.0k | 1.0k | 1.0k | 1.0k | 1.0k | 1.0k | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

**lost: sent in the window, never delivered (% of expected)**

| scenario | stock | r1 | r2 | r4 | own1 | own2 | own4 |
|---|---|---|---|---|---|---|---|
| qos0-4x4 | 0.4% | 0.6% | 0.6% | 2.2% | 0.8% | 1.0% | 1.1% |
| qos1-4x4 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| qos2-4x4 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| qos1-1x1 | 8.3% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| fanout-qos0-1x16 | 3.6% | 3.1% | 6.3% | 3.2% | 3.2% | 3.1% | 2.8% |
| fanin-qos1-16x1 | 93.7% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| many-qos1-32x32 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| latency-qos1-1k | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

own2 many-qos1-32x32 was bimodal (165.1k, 90.2k, 86.1k; no loss in any run). Suite with this build:
272/276 resident and remote at 1 front (one own1 attempt set lost port 1888 to a concurrent test; alone, it passes),
178/276 at 2 fronts; failure sets identical to before (`v17-*.jsonl`). `$SYS/broker/ribbit/held/...` reports held,
paused and delivered-at-bound counts.

## Fan-out on the wire to mqtt-ram ([FAN_OUT_V1]; 2-core sandbox, `load-v19-workers3.jsonl`, 3 x 5 s, median)

Before: up to 4 batches in flight per front, the host running each batch's ops one after another on its socket thread,
results per batch in order, a CALL waiting until every earlier batch was answered. After: any number in flight (window
16384 ops), each op answered by id as it finishes, a CALL sent without waiting (the host runs it after everything that
reached it earlier), and the ops run either on the socket thread as read (`MQTT_RAM_WORKERS=0`) or on workers keyed by
client (`MQTT_RAM_WORKERS=N`, default one per core).

| received msgs/s | before (own1) | after, no workers | after, 1 worker | before (own2) | after, own2, no workers |
|---|---|---|---|---|---|
| qos0-4x4 | 294.5k | 482.6k (1.64) | 377.1k (1.28) | 264.7k | 314.8k |
| qos1-4x4 | 163.5k | 162.6k (0.99) | 146.2k (0.89) | 163.1k | 141.6k |
| qos1-1x1 | 70.8k | 85.5k (1.21) | 58.4k (0.83) | 81.3k | 92.9k |
| many-qos1-32x32 | 168.0k | 161.3k (0.96) | 156.7k (0.93) | 202.9k | 76.1k |

No loss at QoS 1/2 in any cell. On 2 cores the workers cost more than they give: the hand-off adds a thread switch to
every round trip and they compete with the front and the driver for the same two cores. Whether they pay is a question
for real cores: `tools/bench.sh` runs every remote shape both ways (`ownN` with workers, `ownN-w0` without). own2 on
32x32 is bimodal here (seen before the change too). A single QoS 1 publisher is bound by its own window (20
unacknowledged) over the round trip, whichever way the host runs its ops.

## Is it the queue size? Stock with comparable and unlimited queues (`load-v17-queues.jsonl`, 3 x 5 s, median)

Ours holds up to 16384 deliveries per front on top of Mosquitto's `max_queued_messages 1000`. Stock was given the same
room (`max_queued_messages 17384`) and no limit at all (`0`). Every broker is drained after the window before loss is
counted ([DRAIN_COUNT_V1]).

| fan-in, 16 publishers -> 1 subscriber, QoS 1 | stock | stock 17384 | stock unlimited | r1 | r2 | own1 |
|---|---|---|---|---|---|---|
| received msgs/s in the window | 20.4k | 20.0k | 7.6k | 145.0k | 147.8k | 160.3k |
| lost (acknowledged, never delivered) | 93.7% | 93.7% | 0% | 0% | 0% | 0% |
| latency p50 | 51 ms | 852 ms | 3.3 s | 9.7 ms | 7.4 ms | 8.2 ms |
| broker RSS | 8 MB | 16 MB | 383 MB | 12 MB | 14 MB | 20 MB |

| qos1-1x1 | stock | stock 17384 | stock unlimited | r1 | r2 | own1 |
|---|---|---|---|---|---|---|
| received msgs/s | 179.1k | 183.8k | 174.9k | 165.8k | 158.4k | 80.3k |
| lost | 11.2% | 2.6% | 0% | 0% | 0% | 0% |

A bigger queue does not stop stock dropping under fan-in; an unlimited one stops the drops by queueing everything:
memory grows without bound and delivery falls behind by seconds. Pausing the publisher keeps both bounded. Nothing here
shows stock could not do the same with the same change -- pausing a publisher's socket is a single-threaded idea; what
the region adds is that a front can see, without a lock, that a front it publishes into is behind.

## The bounds, driven on purpose (`harness/hold_limits.py`, `hold-limits-v18.jsonl`)

One QoS 1 subscriber that reads but does not acknowledge for a while (its 20 in-flight and 1000 queued fill), then
acknowledges and is drained; publishers at QoS 1, 20 in flight each; every payload unique. Acknowledged to the publisher
against eventually delivered:

| case | | stock | r1 | r2 | own1 | own2 |
|---|---|---|---|---|---|---|
| under-age: 1 x 3000, silent 0.5 s | lost | 1960 | 0 | 0 | 0 | 0 |
| age: 1 x 3000, silent 3 s (past the 1 s bound) | lost | 1960 | 21 | 21 | 24 | 1960 |
| | delivered at the bound | - | 21 | 21 | 24 | 1960 |
| count: 20 x 300, silent 0.5 s, RIBBIT_HOLD_MAX=100 | lost | 4960 | 4104 | 4106 | 3843 | 3921 |
| | delivered at the bound | - | 1700 | 3326 | 1700 | 3573 |

Inside the bounds nothing acknowledged is lost. Past them, loss returns and is at most stock's. In own2 the
publisher (on the other front, through mqtt-ram) is paused only once the host sees that front more than 1024 behind, so
about 2000 are held when the 1 s bound arrives -- as many as stock drops. Every number is acknowledged-versus-delivered
per message; `delivered at the bound` is the broker's own count (`$SYS/broker/ribbit/held/delivered-at-bound`).

## Lost messages: what the throughput tables did not show ([DRAIN_COUNT_V1], [HOLD_FOR_ROOM_V1])

The first run on real hardware (Raspberry Pi 5, 4 cores) showed r2/r4 receiving fewer QoS 1/2 messages than were sent
in the window. `mqtt-load` counted only what arrived inside the window, so lost and late looked the same. It now marks
every message sent in the window, drains after it (until 1 s of quiet), and reports `delivered`, `late` and `lost`.

Pi 5, before the fix, share of QoS 1/2 messages sent in the window and not received in it (first repeat):

| scenario | stock | r1 | r2 | r4 | own1 | own2 | own4 |
|---|---|---|---|---|---|---|---|
| qos1-4x4 | 0% | 0% | 14% | 35% | 0% | 2% | 0% |
| qos2-4x4 | 0% | 0% | 21% | 31% | 0% | 4% | 0% |
| qos1-1x1 | 1% | 1% | 1% | 78% | 0% | 0% | 5% |
| many-qos1-32x32 | 0% | 0% | 9% | 20% | 0% | 0% | 0% |

Cause, shown by changing one setting: r2 qos1-4x4 lost 409,062 of 874,165 acknowledged messages with the default
`max_queued_messages 1000` and 0 with `max_queued_messages 0`. Mosquitto's own queue limit dropped them in the
subscriber's front, because publishers on other fronts were not held back until that queue had room.

After (2-core sandbox, 5 s per cell, messages acknowledged and never delivered):

| scenario | stock | r2 | r4 | own1 | own2 | own4 |
|---|---|---|---|---|---|---|
| qos1-4x4 | 412 | 0 | 0 | 0 | 0 | 0 |
| qos2-4x4 | 0 | 0 | 0 | 0 | 0 | 0 |
| qos1-1x1 | 43,095 | 0 | 0 | 0 | 0 | 0 |
| many-qos1-32x32 | 0 | 0 | 0 | 0 | 0 | 0 |
| fanin-qos1-16x1 | 1,282,369 | 466,033 | 491,242 | 887,051 | 955,899 | 421,450 |

QoS 0 "lost" before v21 was overstated in every broker, stock included ([DRAIN_FLUSH_V1]): at the end of the window
each QoS 0 publisher stopped flushing with up to 256 KB still in its buffer (3,542 messages of 74 bytes), counted sent
and never transmitted -- 14,174 on qos0-4x4 (4 publishers) and about 56,700 deliveries on 1-to-16 in every broker.
The driver now flushes it during the drain. Re-measured (2-core sandbox, 1 x 5 s): stock, r1 and own1 lose 0 at QoS 0;
r4 loses 15% on qos0-4x4 and 23% on 1-to-16 -- real drops ([QOS0_BACKLOG_V1]), on a machine with fewer cores than
fronts plus driver. QoS 1/2 were not affected (at most 20 unacknowledged per publisher, nothing left in the buffer).

Stock itself drops under these loads (qos1-1x1, and 94% of fan-in: 16 publishers into one subscriber with a 1000-message
queue). The fan-in losses here are the same mechanism on a front delivering to its own client, which is Mosquitto's
behaviour unchanged. Suite after the change: 272/276 at 1 front and 178/276 at 2, both shapes, failure sets identical
to before (`v15-*.jsonl`).

## The remote shape: the MQTT API on the permanent sockets (`load-v14.jsonl`, final)

Region in `mqtt-ram`, `MQTT_REGION=own`. Received messages/s, medians of 3 runs (earlier columns: their own runs,
2 each). The final column is the MQTT front speaking its own API ([MQTT_STREAM_V1]) on the FNW1 session's permanent
sockets through the platform's new vendor stream ([VENDOR_STREAM_V1]): records past the semantic engine, payloads raw,
one session per front process with every front thread multiplexed on it, results and deliveries pushed by the host.

| scenario | stock | one request per op | JSON pipeline, synchronous | JSON pipeline, async | **MQTT API on the permanent sockets** | /stock | p50 µs | 2 fronts | 2 fronts /stock |
|---|---|---|---|---|---|---|---|---|---|
| qos0-4x4 | 402.4k | 2.2k | 24.4k | 10.9k | **224.9k** | 0.56 | 1048576 | 251.2k | 0.62 |
| qos1-4x4 | 132.9k | 2.6k | 26.1k | 14.5k | **152.8k** | 1.15 | 464 | 118.7k | 0.89 |
| qos2-4x4 | 76.9k | 2.4k | 20.1k | 14.7k | **73.1k** | 0.95 | 928 | 88.1k | 1.15 |
| qos1-1x1 | 129.6k | 2.3k | 16.5k | 9.2k | **64.3k** | 0.50 | 256 | 79.4k | 0.61 |
| fanout-qos0-1x16 | 302.2k | 32.4k | 128.5k | 40.7k | **309.0k** | 1.02 | 3014656 | 285.3k | 0.94 |
| fanin-qos1-16x1 | 16.8k | 0.1k | 1.9k | 20.9k | **53.2k** | 3.17 | 18432 | 56.7k | 3.38 |
| many-qos1-32x32 | 144.7k | 2.1k | 11.6k | 11.2k | **137.6k** | 0.95 | 3968 | 163.6k | 1.13 |
| latency-qos1-1k | 1.0k | 1.0k | 1.0k | 1.0k | **1.0k** | 1.00 | 288 | 1.0k | 1.00 |

Resident 1 thread in the same run, against the same stock: 0.90–1.02 of stock on every scenario (`load-v14.md`).

What the move to the permanent sockets fixed on the way, each reproduced first:

| finding | evidence | change |
|---|---|---|
| a nested call, made while a pushed record was being applied, waited for an answer in a record the outer loop had taken but not yet applied | `04-retain-clear-multiple` hung at 2 fronts; front 1 stuck in `rgn_give_session` with the host idle | taken records go to one per-front queue that nested readers continue from |
| the host received its parent-death signal when front 0's *thread* ended | `03-pattern-matching`: "broker not terminated", front 1 waiting for results from a dead host | front 0's thread outlives every other front; a front waiting on a dead session says so instead of waiting |
| the first front to finish stopped the shared host | 9 "region clean failed" lines at 2 fronts on shutdown | the host is stopped after every front has finished |

## Earlier: the remote shape over JSON on FNW1 (superseded)

Received messages per second, medians of 2 runs. Stock is from the final run; each remote column is the same driver
against the same build stage. "One request per op" is the shape before consolidation. "Pipelined (synchronous)" is the
first consolidated version: one pipeline per loop pass, the loop waiting for its reply. "Final" adds the asynchronous
pipeline (one in flight per front, the loop keeps running), the 1024-publish bound, pacing, per-front instances,
counts across processes, and forwards in the pipeline.

| scenario | stock | remote, one request per op | remote, pipelined (synchronous) | remote, final | final/stock | final p50 µs | 2 fronts, final |
|---|---|---|---|---|---|---|---|
| qos0-4x4 | 396.7k | 2.2k | 24.4k | 10.9k | 0.03 | 3014656 | 6.3k |
| qos1-4x4 | 133.7k | 2.6k | 26.1k | 14.5k | 0.11 | 5120 | 10.5k |
| qos2-4x4 | 81.4k | 2.4k | 20.1k | 14.7k | 0.18 | 5120 | 10.2k |
| qos1-1x1 | 121.6k | 2.3k | 16.5k | 9.2k | 0.08 | 1984 | 7.5k |
| fanout-qos0-1x16 | 304.7k | 32.4k | 128.5k | 40.7k | 0.13 | 3407872 | 48.0k |
| fanin-qos1-16x1 | 16.9k | 0.1k | 1.9k | 20.9k | 1.24 | 16128 | 19.3k |
| many-qos1-32x32 | 141.7k | 2.1k | 11.6k | 11.2k | 0.08 | 33792 | 19.7k |
| latency-qos1-1k | 1.0k | 1.0k | 1.0k | 1.0k | 1.00 | 688 | 1.0k |

The synchronous pipeline was faster on the 4x4 rows on this box, but it starved fan-in (1.9k: one subscriber can
receive only its in-flight window per round trip while the loop waits), and with two fronts it stalled -- a front
blocked behind deliveries it could not take. The final version holds no stall in repeated runs of every scenario
back to back, and fan-in beats stock. Its cost on the 4x4 rows is thread handoffs (the front's sender thread, the
session's send and read threads, the host): on two cores shared with the host and the driver, those handoffs are
what is measured.

What the remote work found and fixed, each measured or reproduced first:

| finding | evidence | change |
|---|---|---|
| every front thread opened its own two connections; one request per operation | baseline ~2k msgs/s, 194 µs call floor | one session per process, each front an instance on it; one pipeline per loop pass |
| a variable-length JSON array re-learns the semantic template every reply and goes to the on-disk blob store past 8 KB | 58 blobs, 2.6 MB written in 3 s; host `train_exception` without a writable blob root | the pipeline's bulk as one string field each way; per-run blob root (the Redis front's rule) |
| the loop blocked on every round trip | fan-in 1.9k received vs 26k acknowledged | asynchronous pipeline, one in flight per front |
| QoS 0 publishers outran the round trip | a 180,381-publish pipeline, reply timeout | pipeline bound (1024 / 512 KB), loop waits for the one in flight |
| remote fronts had no pacing and no QoS 0 bound | a front parsing a giant delivery batch for seconds | per-reply "most behind", pacing; counts ride pipelines, so the QoS 0 bound applies |
| a forward for a departed client was a synchronous round trip | thousands of `fwd` round trips in one loop pass | forwards ride the pipeline |
| `own` mode started one host per front thread | a later-forked host held a closed bridge socket open (`06-bridge-remap-receive-wildcard`) | one host per process; the host closes inherited descriptors |
| global limits lagged one round trip across processes | `01-connect-max-connections` over-admitted | admission by reservation: own slot first, then the others'; local slots when the host is the process's own |

## v28: boxes on the memory, ribbit-platform 0.64 (`load-v28-r1.jsonl`: 2-core sandbox, ONE run of 5 s per cell)
`tools/bench.sh` with FRONTS="1 2", stopped after the first repeat (the 1024- and 4096-client shapes take minutes a
run here): one run per cell, so read these as a check against v27, not as medians. ownN: N fronts in one box on its
own memory. received msgs/s; x stock.

| scenario | stock | own1 | own2 | own1/stock | own2/stock | lost stock / own1 / own2 | p50 us stock / own1 / own2 |
|---|---|---|---|---|---|---|---|
| qos0-4x4 | 450,225 | 251,313 | 194,535 | 0.56 | 0.43 | 0 / 0 / 0 | 524,288 / 950,272 / 1,245,184 |
| qos1-4x4 | 147,451 | 213,012 | 198,460 | 1.44 | 1.35 | 0 / 0 / 0 | 768 / 320 / 352 |
| qos2-4x4 | 87,880 | 148,228 | 169,183 | 1.69 | 1.93 | 0 / 0 / 0 | 1,856 / 512 / 448 |
| qos1-1x1 | 108,590 | 137,616 | 167,272 | 1.27 | 1.54 | 51,358 / 0 / 0 | 5,376 / 432 / 336 |
| 1to16-qos0 | 348,719 | 1,573,049 | 925,164 | 4.51 | 2.65 | 0 / 0 / 0 | 2,621,440 / 589,824 / 1,048,576 |
| 16to1-qos1 | 17,720 | 151,636 | 155,812 | 8.56 | 8.79 | 1,311,716 / 0 / 4,460 | 57,344 / 8,704 / 7,680 |
| many-qos1-32x32 | 144,640 | 221,272 | 248,017 | 1.53 | 1.71 | 0 / 0 / 0 | 4,608 / 3,456 / 3,328 |
| latency-qos1-1k | 1,000 | 1,000 | 1,000 | 1.00 | 1.00 | 0 / 0 / 0 | 144 / 124 / 124 |
| many-256 | 112,346 | 169,468 | 207,956 | 1.51 | 1.85 | 0 / 0 / 0 | 63,488 / 57,344 / 34,816 |
| many-1024 (51,200 offered) | 51,246 | 51,158 | 51,385 | 1.00 | 1.00 | 0 / 0 / 0 | 11,264 / 18,432 / 10,752 |

Against v27 (the same sandbox): qos1-1x1 0.79 -> 1.27 (own1) and 0.80 -> 1.54 (own2); 1to16-qos0 own2 lost 13.3% ->
0; 16to1-qos1 own2 lost 0.7% -> 0.3%; qos0-4x4 own2 0.71 -> 0.43 (one run). What changed underneath: publishes go as
binary writes even alone ([WRITES_IN_BINARY_V1]) and nothing answers a write ([NO_WRITE_ACK_V1]). Across two boxes
(`tools/two_boxes.sh`): 111k msgs/s, nothing lost, 7/7. Mosquitto's suite on own1: 272/276, the 4 HTTP-API tests
as stock.

## v29: every write and read on the semantic wire (`load-v29-sandbox.jsonl`: 2-core sandbox, 2 runs of 8 s per cell, median)

Same machine, same driver, interleaved: stock Mosquitto 2.1.2; v26 (operations in mqtt-ram); v28 (boxes on the memory,
publishes as whole binary frames); v29 (each publish its cell's own semantic DIFF in a moving window, nothing answered,
rows read back as DIFFs). One broker thread. received msgs/s, lost (acknowledged or sent and never delivered).

| scenario | stock | v26 | v28 | v29 |
|---|---|---|---|---|
| qos1-4x4 | 165,552 (lost 1,214 / 105) | 302,828 | 222,742 | 55,598 |
| qos2-4x4 | 92,606 (lost 0 / 1,961) | 211,441 | 165,669 | 48,460 |
| qos1-1x1 | 144,333 (lost 19,922 / 62,788) | 106,064 | 155,395 | 54,821 |
| 16to1-qos1 | 21,072 (lost 93%) | 247,254 | 158,201 | 54,475 |
| many-qos1-32x32 | 167,322 | 454,776 | 231,505 | 56,514 |
| qos0-4x4 | 305,214 | 446,959 | 201,842 | 35,308 |
| 1to16-qos0 | 311,222 | 2,747,609 | 1,424,428 | 669,446 |

v29 lost 0 in every cell at 1 and 2 threads (v28 at 2 threads: 8,000-8,460 on 16to1, up to 411,072 on 1to16-qos0).
Broker CPU per 1k messages: v29 17-19 ms (QoS 0: 27-30), v26/v28/stock 2-6. Memory, broker and mqtt-ram together: v29
63-73 MB, v26 31-59, v28 105-122, stock 8-10. On the wire, one 64-byte publish: 248 B as a whole binary frame (v28),
73 B as a window DIFF (v29); no write replies. The CPU is the engine's: parsing the body into fields, the difference,
proving the template, the location strings -- the next work.

## What is not measured here

Scaling. With 2 cores shared by the broker and its load, t4 is four front threads plus two driver threads on two
cores: the fan-in row at t4 measures the scheduler. The single-thread comparison is the honest head-to-head this box
can give: within 3–12% of stock on every scenario, latency at a fixed 1k/s equal. Threaded results need a machine with
cores for the fronts and separate cores (or a separate machine) for the driver — a 4-core Pi 5 or the i7.

## v30: two machines in FrogNet's simulator (`tools/bench_across.py`; `across-20261018-140900.jsonl`; 1 run of 4 s per cell)

Two network namespaces built by FrogNet's simulator (`netns_backend.NetnsProvisioner`), one veth link between them;
publishers on A, subscribers on B, every message crossing. Stock: Mosquitto on A and on B, B bridged to A (`topic #
both <qos>`). Ribbit: a box on A and a box on B, the memory on A. "Link B/msg" is every byte on the link, both
directions, headers included, from its interface counters, per message delivered.

**The link was not shaped.** This sandbox's kernel has no `sch_netem`; the simulator's `NetemShaper` failed without
saying so, and the first 4G/HaLow numbers were taken on an unshaped link. `bench_across.py` now checks `tc` after
shaping and stops if the link is not shaped. These rows are two namespaces on a plain veth, nothing more.

| load | arm | msgs/s | lost | p50 | link B/msg | CPU ms per 1k msgs (A / B / memory) |
|---|---|---|---|---|---|---|
| QoS 1, 4 pubs x 4 subs, flat out | stock | 20,398 | 904,492 | 38.9 ms | 212.4 | 58.7 / 16.3 / -- |
| | ribbit | 26,009 | 0 | 23.6 ms | 95.0 | 27.4 / 24.7 / 39.5 |
| QoS 0, flat out | stock | 135,517 | 1,549,341 | 655 ms | 122.8 | 5.8 / 5.9 / -- |
| | ribbit | 25,664 | 0 | 3.5 s | 136.4 | 39.9 / 29.7 / 56.9 |
| QoS 1, 2,000/s offered | stock | 2,000 | 0 | 168 us | 294.7 | 45.0 / 41.3 / -- |
| | ribbit | 2,000 | 0 | 640 us | 192.6 | 95.0 / 101.3 / 132.5 |

What moved it, measured on the same run at 2,000/s (539 -> 193 B a message): B wrote `applied` once a message
([APPLIED_IN_STEPS_V1]); B asked for rows once a message ([TRIGGERS_FOLLOW_V1]); rows were 147 B ([NUMBER_DELTA_V1],
[ROW_REFERENCE_OF_K_V1], [ROW_RECORD_COMPACT_V1]). QoS 0 went from 29,800 lost to 0 with the standing triggers.
Mosquitto's suite (region in `mqtt-ram`): 272/276, the 4 IPv6-only tests as stock. two_boxes 7/7. Redis 15/15, 39/39.

## v31: subscriptions addressed by filter, MQTT 5's answer off the front thread (2026-10-19)

`tools/bench_across.py`, veth, publishers on A, subscribers on B, 8 s per cell. Files: `results/across-20261019-080818.jsonl`
(v30 as received, 1 run), `results/across-20261019-084122.jsonl` (v31, 3 runs; median, range in brackets). The "v30
before the sub read" column is from the 2026-10-19 transition, measured in an earlier sandbox; it is quoted, not re-run.

| load | stock (v31 run, median) | v30 before the sub read | v30 as received (whole-family scan) | v31 |
|---|---|---|---|---|
| QoS 1 flat out, msgs/s (lost) | 23,427 (1,706,843) | 103,978 (49,758) | 6,012 (0) | 87,770 [62,959–99,160] (83,640) |
| QoS 0 flat out, msgs/s (lost) | 161,966 (4,186,120) | 105,876 (0) | 441 (0) | 104,202 [92,080–106,368] (0) |
| QoS 1 at 2,000/s, CPU ms/1k A/B/mem | 43.8 / 39.4 / – | 56 / 104 / 114 | 149 / 136 / 234 | 67.5 / 117.5 / 125.0 |
| QoS 1 flat out, MQTT 5, msgs/s (lost) | 21,055 (1,499,110) | not run | 3,398 (0) | 28,356 [28,125–28,451] (0) |

The MQTT 5 row is new: the bench's client spoke 3.1.1 only, which never consumes "no matching subscribers".

What changed:
- **[SUBS_BY_FILTER_V1]** `(mqtt.box, sub, <client>␟<filter>␟<box>)` is gone. A subscription is
  `(mqtt.subs, <filter>, <client>␟<box>␟<group>)`, and each filter shape a box uses goes once into
  `(mqtt.subs.shapes, all, <shape>␟<box>)`. A topic plus a shape gives one filter or none, so the lookup reads the
  manifest and then one family per shape that fits. `region/test_sub_address.cpp`: 758,200 filter/topic pairs agree with
  `mosquitto_topic_matches_sub`.
- **Measured after addressing alone** (two_boxes load, v3, `[DIAG-SUBLOOKUP]`): still 3,456/s. Each lookup made 2 reads,
  blocked the front thread for 283 µs on average, and added up to 5.8 s of front-thread time in the run. The cost was
  the synchronous read on the publish path, not the scan's size.
- **[ASYNC_PUBACK_V1]** The only consumer of the answer is the MQTT 5 QoS 1 PUBACK. `rgn_pub` asks nothing of other
  boxes. An MQTT 5 QoS 1 publish with no subscriber on this box asks the memory on the process's adaptive pool
  (`ribbit::pool_run`, [ADAPTIVE_POOL_V1]), and that client's PUBACK waits in an in-order list (MQTT 5 4.6). The front
  thread never waits.
  - Last v5 run (`results/across-20261019-084122/ribbit-A.log`): 270,531 asked, 270,531 answered, 0 failed, 541,062
    reads. Lookup 1,814 µs average and 78.5 ms max on the pool: 2 reads on a CPU-saturated 2-core box.
  - The v5 rate is the client's window (20 in flight × 4 publishers) over that latency.
- two_boxes: 13/13, including 5 new checks:
  - v5 PUBACK 0 with the subscriber on B (exact, `+`, `$share` `#`);
  - 16 with none anywhere;
  - 16 after B's subscriber leaves;
  - PUBACK order kept across remote/local/none answers on one connection.
- Suite 272/276 (the same 4 http-api); Redis two-box 15/15, concurrency 39/39; platform unit tests pass.

Not claimed:
- The QoS 1 flat-out loss (83,640 median) is B falling behind on a CPU-saturated 2-core box. It is the known open item,
  not addressed here.
- The QoS 1 flat-out spread (62,959–99,160) is run-to-run on this sandbox; that path did not change between runs.

## v32: the memory forwards its writers' changes (2026-10-19)

`tools/bench_across.py`, veth, publishers on A, subscribers on B, 8 s per cell, 3 runs each, median. Stock was run in the
same session: `results/across-20261019-111333.jsonl`. v31's numbers are from `results/across-20261019-084122.jsonl`.

| load | stock | v31 | v32 |
|---|---|---|---|
| QoS 1 flat out, msgs/s (lost) | 27,485 (1,728,573) | 87,770 (83,640) | 101,483 [100,473–103,243] (165,344) |
| QoS 0 flat out, msgs/s (lost) | 156,130 (4,587,081) | 104,202 (0) | 131,794 [122,330–134,593] (0) |
| QoS 0, memory CPU ms per 1k msgs | – | 9.32 | 6.15 |
| QoS 1 at 2,000/s, CPU ms/1k A/B/mem | 40.6 / 36.9 / – | 67.5 / 117.5 / 125.0 | 67.5 / 118.8 / 116.3 |
| QoS 1 flat out, MQTT 5, msgs/s (lost) | 23,624 (1,596,276) | 28,356 (0) | 29,521 [27,293–31,216] (0) |

Where the memory's CPU went before (perf with frame pointers, QoS 0, inclusive):
- writes in: 47% (`cellwire::decode` 26%);
- rows out: 40% (`encode_rows` 34%, of which copying every value 9%, the 8-reference search 11%, the diff encode 17%).

What changed:
- **[LOCKFREE_READS_V1]** A row's value is one immutable `RowVal` behind an atomic pointer, published before its
  window slot and retired to epoch reclamation. Reads take no lock. The writer keeps the row lock, and so does the one
  bounded rows walk ([ROW_SCAN_BOUND_V1]), which must wait for a write in flight. Measured alone: no change in QoS 0
  (104,038/s, memory 9.30 ms/1k); the read lock was 2.7%.
- **[LZ4_FLOOR_V1]** Nothing of 64 bytes or less is compressed. Measured first (`[DIAG-LZ4]`, QoS 0): inputs of 25–64
  bytes were tried 2,232,831 times and came out smaller 0 times. The same floor went into FrogNet's `core/codec.py`, so
  the C++ codec stays byte-identical to it.
- **[POSITIONAL_WRITE_V1]** The memory decodes a cell write by position: the chain's previous array is copied once and
  the changed fields are put in place by index. The chain's reference is the stored array, with no Dict. The decode
  also returns which indices changed.
- **[FORWARD_CHANGES_V1]** A stored row keeps its writer's reference array and its changed indices. The reader's
  session encoder keeps positional references (the arrays it gave that reader). When the writer's reference is one of
  them, the writer's changes ARE the row's difference: no compare and no copies. Otherwise it compares by position, in
  place. Measured: 273,414 rows forwarded and 52,920 compared in a v5 run; the wire is 1.5% larger in
  `tests/test_rows_forward.cpp` (351,222 B against 346,039 B).
- **[SHARED_ENCODE_V1]** A forwarded row's packet depends only on the row and its reference, so the first reader's
  encoder keeps it with the row and every other reader's reuses it. This bench has one reading box, so: 358 reused.
  `test_rows_forward`: 4,874 reused between two readers.
- **[ENCODE_CHANGED_V1]** `semcodec::detail::encode_diff` was split so that a known change list encodes to the same
  bytes. The codec tests' bytes per row are unchanged (43.6 B, 82.4 B).

Tests: platform units (rowcodec, window_wire, cellwire, ram_memory incl. the lock gate, row_scan_bound, write_window,
family_hooks, rows_forward), two_boxes 13/13, suite 272/276 (the same 4 http-api), Redis two-box 15/15 and
concurrency 39/39.

Also measured and reverted (front-thread send moved off the front):
- **Pool tasks:** QoS 0 85,600/s with 103,093 lost.
- **One send thread per front, disconnecting a full client:** QoS 0 20,560/s; the four subscribers were disconnected
  for full sockets, where stock keeps them and drops QoS 0.

The front thread's coalesced `writev` stays.

Not claimed:
- QoS 1 flat-out loss is B falling behind on 2 saturated cores, the known open item.
- The 2,000/s CPU is still 3.9× stock in total (302.6 against 77.5 ms per 1k).
