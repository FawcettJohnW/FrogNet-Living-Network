# What a transport driver for the semantic wire must implement

17 November 2026. Derived from the two drivers that exist: TCP (`proxy/transport_semantic.py` `_DaemonWorker`,
`daemon/engine/server.py` `DaemonServer`, `daemon/engine/session.py` `SemanticSession`) and Homa
(`proxy/transport_homa.py` `_MsgWorker`, `daemon/engine/server_homa.py` `MsgDaemonServer` and `_MsgSession`).
John asked for it: "Now that you have two examples, you should be able to define the abstract interface required
for a transport. These are the calls each driver must implement for the system to work. Verify that this is
correct."

This file states the interface as the code uses it today, what was checked, and where the code does not yet keep
to it. It is a description. No abstract class exists in the tree yet; section 5 says what declaring one would
change.

## 1. Where the driver sits

The driver is below the semantic compression layer. Checked in the code:

- The proxy's request path (`_handle_semantic_request` and what it calls) builds a finished frame: REQ_RAW,
  REQ_FULL, REQ_DIFF, REQ_REPEAT, REQ_SAME_MISS, with templates, instance references and SAME/DIFF already
  applied. It then calls `worker.call(frame)`, or `worker.submit(frame, gate)` and later `worker.wait(rpc)`. Those
  are the only calls it makes with a frame (4 call sites). What comes back is a finished reply frame, which the
  same layer decodes.
- The daemon's engine receives a whole frame through `SemanticSession._submit_frame(frame, seq, wire_in)` and puts
  a whole reply frame on the session's reply queue. Everything from `_submit_frame` up is one body of code for
  both drivers (`_MsgSession` inherits it and replaces only `__init__` and `_write_loop`).
- Nothing above the seam names a carrier. `daemon/engine/session.py`, `daemon/engine/execution.py`,
  `daemon/engine/server.py`, `core/semcache_wire.py` and `proxy/proxy_main.py` contain no mention of Homa or of a
  carrier. In `proxy/transport_semantic.py` the only mentions are the seam in `_get_worker` and one import.

So normal operations do not know which driver carried a frame, and cannot find out.

## 2. The contract (what the engine assumes of any driver)

1. **Whole frames.** A frame handed in arrives as that frame or not at all. Size is the driver's problem (the Homa
   driver cuts a frame larger than its carrier's message into pieces and rejoins them; TCP streams).
2. **In order, each way, within a session.** Frames reach the daemon's engine in the order the proxy submitted
   them. Replies may complete in any order.
3. **Many in flight.** Any number of requests outstanding on one session at once.
4. **A reply finds its request.** `wait(rpc)` returns the reply to that request and no other.
5. **Broken is broken.** When the session ends, every outstanding request fails with the reason, and the session
   is not quietly remade under a request. The next request opens a new session.
6. **A session that is starting is not dead.** A request that arrives while the session is being set up is queued
   under the driver's own clock for that state; the caller's wait decides. (The TCP driver broke this until
   17 November: `[A_SESSION_STARTING_IS_NOT_DEAD_V1]`.)
7. **Each end says who it is.** The first frame of a session is HELLO carrying the sender's own .1, and the reply
   to it carries the other end's.

## 3. Proxy side: a session to one peer machine

One object for each (peer machine, port, channel set). The engine gets it from `_get_worker(host, port, set_id)`.

| The engine uses | Meaning | TCP | Homa |
|---|---|---|---|
| `Driver(host, port, set_id)` | Open (or begin opening) the session | yes | yes |
| `submit(frame, gate=None) -> rpc` | Queue a frame; returns the handle of that request | yes | yes |
| `wait(rpc) -> bytes` | Block until that request's reply frame, or raise why not | yes | yes |
| `call(frame) -> bytes` | `submit` then `wait` | yes | yes |
| `stop(err=None)` | End the session; outstanding requests fail with `err` | yes | yes |
| `is_stopped() -> bool` | The session has ended | yes | yes |
| `stopped_reason() -> str` | Why, for the log | yes | yes |
| `_stopped_at` | When it ended (the engine prints its age) | yes | yes |
| `_peer_latency.retry_budget()` | How long the engine may wait on this peer | yes | yes |
| nothing outstanding? | Whether the session may be retired | `_pending`, `q` | `_out` |
| `carrier` | The driver's name | absent (read as "tcp") | `"homa"` |

The handle `rpc` is the engine's own `_Rpc`; `gate` is the engine's fan-out gate, stamped by the driver when the
frame is taken for the wire.

## 4. Daemon side: a listener, and a session for each peer

| The system uses | Meaning | TCP | Homa |
|---|---|---|---|
| `Listener(host, port, resolver)` | Made by `daemon_main` | `DaemonServer` | `MsgDaemonServer` |
| `run()` | Listen and serve until the process ends | yes | yes |
| hand a frame up | `_submit_frame(frame, seq, wire_in)`, in the order the proxy submitted | `_read_loop` | `arrive`, `piece`, `_hand_up` |
| send a reply down | take `(request, reply frame)` from the session's reply queue and deliver it to that request | `_write_loop` | `_write_loop` |
| HELLO | learn the peer's .1, answer with this end's | in the session | in `_hand_up` |
| RTT_PING | answer with RTT_PONG and the two times | in the session | in `_hand_up` |
| a peer is gone | end its session and tell the engine (`_writer_gone`) | socket closes | liveness (`core/homa_liveness.py`) |

Beside the two there is the choice of driver, which is not a driver's call: `core/homa_transport.py`
`carrier_for(host)` (which driver a session to that machine opens on, from the setting per RAM connection) and
`daemon_server(...)` (a listener on every carrier the setting names).

## 5. Where the code does not keep to this yet

Found while verifying. None of it changes what a request sees; all of it is why this is not yet an interface a
third driver could be written against without reading the other two.

1. **It is not declared.** Both proxy-side classes have the same method names by copying, not by a base class.
   Introspection on 17 November: `call`, `submit`, `wait`, `stop`, `is_stopped`, `stopped_reason` are on both with
   matching signatures; `stats` is on Homa only; `carrier` is on Homa only.
2. **The engine reads private fields of the session:** `_peer_latency`, `_stopped_at`, and for "nothing
   outstanding" `core/homa_transport.worker_idle` looks at `_out` on one driver and `_pending` and `q` on the
   other. These should be three calls: `retry_budget()`, `stopped_at()`, `idle()`.
3. **The Homa driver reaches into the engine:** `TS._Rpc`, `TS._PeerLatency`, `TS._BUDGET_FLOOR`, `_BUDGET_MULT`,
   `_BUDGET_CEILING`, `TS._gate_stamp`, `TS._get_local_gw`; on the daemon side it subclasses `SemanticSession`
   and uses its private fields (`_reply_q`, `_recv_sock`, `_send_sock`, `_preread_frame`, `_request_seq`,
   `_encode_lock`), plus `S._get_executor`, `S._get_db_executor`, `S._is_fast_frame`, `S.bump_semantic`.
4. **On the daemon side TCP is not a driver.** `SemanticSession` is the engine and the TCP session in one class:
   its two sockets, `_read_loop` and `_write_loop` are built in. The Homa session exists by subclassing it and
   replacing the socket parts. A clean cut would leave an engine session that knows no socket and two drivers
   that feed it.
5. **Each driver looks inside a frame in three places:** HELLO, RTT_PING (daemon side), and the error wrapper for a
   malformed frame. These are the session's control frames, not compression, but they are parsed in each driver
   separately (`proxy/transport_homa.py` line 471, `daemon/engine/server_homa.py` lines 195 to 213). One place
   above the drivers would remove the drivers' need to know the frame format at all.

Declaring it means: two abstract classes (a proxy-side session, a daemon-side listener with its session), the
three private reads in item 2 turned into calls, the engine session split from the TCP sockets (item 4), and HELLO
and RTT_PING handled once above the drivers (item 5). Items 2, 4 and 5 change the TCP driver and
`daemon/engine/session.py`, which are working code, so they wait for John's word.
