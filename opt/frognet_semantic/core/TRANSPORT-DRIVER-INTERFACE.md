# What a transport driver is

John Fawcett, 18 November 2026: "We own the wire. It is whatever connection the driver chooses to make, but it is a
permanently open connection where we control what is sent and received on the socket. The data on the socket is
exactly that -- data, and the transport doesnt need to know anything about it. It was provided by the packing method
of the driver and on the other end will be handed unchanged to the unpacking method. The packing and unpacking
methods are part of the driver." And: "things like connection, send, and receive must be part of the driver".

The interface is in code: `core/transport_driver.py`. This file says what it means and where each driver stands.

## 1. Where a driver sits

Below the semantic engine and above the network. The proxy's engine makes a frame (FNW1: a templated request, a
difference, a reference); the driver carries it to the daemon on the RAM host; the daemon's engine gets the same
bytes. Compression, templates, references, fan-out and fan-in are all above the driver and do not know which driver
is under them. The transport word is a setting per RAM connection (`/etc/frognet/semantic_transport`).

## 2. The calls

| call | what it must do |
|---|---|
| `connect()` | Open this end's permanently open connection, by whatever means the driver chooses. Called again at a network event, or when there is next something to send, if the connection has gone. Never on a timer. |
| `send(data)` | Put one packed unit on the connection, exactly as `pack()` returned it. |
| `receive()` | The next packed unit, exactly as the other end's `send()` was given it. Blocks until one is there: a held read, not a poll. |
| `pack(n, frame)` | The driver's own framing of one frame. Returns one unit, or several when the driver cuts a frame. |
| `unpack(data)` | One received unit in; `(n, frame)` out when a frame is whole, the frame byte for byte what `pack()` was given. |
| `close()` | End the connection for good. |
| `listen()` | The end that is connected to: a driver object for each permanent connection as it arrives. |

`n` is the number that lets a reply find its request. What a driver may do with it is its own business (TCP counts
arrivals; Homa uses its RPC id).

## 3. What is not a driver's

Which frame answers which; the order frames are handed up; HELLO and the RTT ping (frames like any other); who is
alive (a held read on the capability tuple in the ControlRam); the contents of a frame.

## 4. Rules every driver keeps

1. **A session ends with its connection**, at both ends, whether or not anything was outstanding. The next request
   opens a new connection and a new session. (Found 18 Nov on chorus, then called soft Homa: a proxy that kept its session across a
   reconnect talked to a new daemon that was waiting for that session's first frames.)
2. **Gone is gone.** What was outstanding on a connection that ended fails, is said, and is not sent again by the
   driver. Nothing is used in a driver's place.
3. **Nothing on a timer.** A connection is opened when there is something to send or at a network event.
4. **No machine talks to anything but the RAM host** on the wire. An event reaches a node through its held read.
5. **A node that was not told a driver's word does not run it.** `auto` on a node without Homa in its kernel is TCP.

## 5. The drivers

| word | connection | packing | where |
|---|---|---|---|
| `tcp` | on a node NOT told of chorus: two TCP connections the proxy opens to the daemon's port, one each way, held open. On a node told of chorus, under a setting: a protocol under the frame, on the one connection to its destination (section 7) | a 4-byte length; a reply carries the 4-byte number of its request. Under the frame: the message as it is | proxy end: `proxy/transport_semantic._DaemonWorker`; daemon end: `daemon/engine/session.TcpDaemonEnd`; under the frame: `core/channel_frame.py` |
| `chorus` | the semantic wire: a protocol under the frame, on the one connection to its destination (section 7). FrogTorch's RAM sessions: a connection of chorus's own, which begins `CHO\x01` | under the frame: a message's first 24 bytes as a difference against the message before it for the same Role and direction. On its own connections: Homa DATA frames in segments of at most 240,000 bytes, the header and first bytes crossing as a difference (section 6a). Called soft Homa until 21 Nov 2026 | `core/chorus.py`: `_frame_pack`, `_frame_unpack`; `_Conn` for its own connections |
| `homa` | one Homa socket (the kernel module) | the module's own: real Homa frames; it cuts, paces and resends | `core/homa_socket.HomaSocket`, under the same two files |
| `openqasm` | a protocol under the frame, on the one connection to its destination (section 7) | the whole message as a difference against the message before it for the same Role and direction; the circuit codex `core/openqasm_handler.py` stands above it in the codec | `core/openqasm.py` |

The same interface carries a second wire since 19 November: a rank's session with the FrogTorch RAM
(`agent_workload/tuplespace/frogtorch_ram_drivers.py`). There the proxy and daemon are not involved; the rank's end
connects and the RAM's end listens, with the same six calls and the same per-RAM word.

| word | connection | packing | where |
|---|---|---|---|
| `tcp` (and `auto`) | one TCP connection to the RAM's port P, held open | an 8-byte length and the frame: the bytes that session has always sent | `TcpEnd`, `TcpListener` |
| `chorus` | the semantic wire: a protocol under the frame, on the one connection to its destination (section 7). FrogTorch's RAM sessions: a connection of chorus's own, which begins `CHO\x01` | under the frame: a message's first 24 bytes as a difference against the message before it for the same Role and direction. On its own connections: Homa DATA frames in segments of at most 240,000 bytes, the header and first bytes crossing as a difference (section 6a). Called soft Homa until 21 Nov 2026 | `core/chorus.py`: `_frame_pack`, `_frame_unpack`; `_Conn` for its own connections |
| `homa` | not written | | a session told this word says so and stops |

There a frame may be handed to `pack()` as a tuple of parts that are the frame in order, so a tensor is not copied to
have its header put in front of it; `unpack()` gives one bytes-like frame back.

## 6. Where the code does not keep to this yet

1. The proxy end of the TCP driver (`_DaemonWorker`) is not written as the six calls; it does the same things under
   its own names. It is existing code and has not been restated.
2. HELLO and the RTT ping are handled once in each driver's session code, not once above the drivers.
3. The two Homa ends (`transport_homa.py`, `server_homa.py`) still call their carrier by the kernel socket's names
   (`send_request`, `send_reply`, `recv`) and still carry the machinery kernel Homa's message limit needs (pieces,
   spare RPCs). Chorus's carrier sets its message limit high, so on chorus that machinery does not run.
4. A driver registers its word (section 7, item 5), and a process still has one message carrier: a registered word
   of the message kind rides chorus's or the kernel's connection, whichever the process has.

## 6a. Chorus (21 November)

John, 21 Nov 2026: "We are no longer faithful to Homa in softhoma... call the protocol 'chorus'." And, of a network
of sites: "each Role is its own ram, etc., coordinated through Chorus."

Chorus is the name of the driver that was soft Homa, and of what it is for: every role has its own RAM, and what is
written in one reaches the machines that hold a read on it through this driver's connections. `core/chorus.py` has
the origin and what of Homa remains inside it. The word in a setting is `chorus`. The old word `softhoma` is refused
at start with that said; it is not kept as a second name. Homa's socket API on CroakOS memory
(`ribbit/examples/homa/src/softhoma.cpp`) keeps its name, and so does the kernel module's driver, `homa`.

## 7. A Role's channel: one frame under every protocol, one connection to each destination (21 November)

The design is `docs/PROTOCOL-LAYER-DEMONSTRATION-WALKTHROUGH-AND-DESIGN-v0.4.md` (sections 4 to 7 and 10), with
`docs/PROTOCOL-LAYER-DEMONSTRATION-REQUIREMENTS-AND-DESIGN-v0.2.md` for the exercise it is demonstrated on. This
section says where each part of it is in the code. Sections 7 and 9 of this file as they stood on 19 and 21 November
described the mechanism that design replaced (a key named in the setting, an accept list, three writes for a key,
a connection for each driver and memory, drivers that sealed); it is no longer in the tree.

| Part | Where | What it is |
| --- | --- | --- |
| The setting | `core/channel_setting.py` | (channel, setting, `<Role>`) in the ControlRam: `driver`, `seed`, `protection`, `switches`, `check`. The held read on it; `role_of(name)`; the setting's proof and its two rules; this end's report, (channel, at, ...). |
| The frame | `core/channel_frame.py` | `Link`: one end of one connection. A frame is its length, its packet ID and its data, sealed under the Role's current key or not; the data opened begins with the protocol's word. The two calls, `SWP` and `SWE`. `key_number(seed, n, size)`. One writer and one reader for each connection. |
| The carrier | `core/channel_carrier.py` | `FrameCarrier`: the calls the message session's two ends make on a carrier (`send_request`, `send_reply`, `recv`, `abort`, `peer_gone`), with one `Link` to each destination. It tells every `Link` when the settings moved. |
| The protocols | `channel_frame` (tcp), `core/chorus.py` (chorus), `core/openqasm.py` (openqasm) | Each is `pack(state, packet_id, message)` and `unpack(state, packet_id, data)`, registered with `channel_frame.register_driver(name, word, pack, unpack)`. `state` is the protocol's own, for one Role in one direction of one connection, empty after a switch of protocol. None seals. |
| Which session | `core/homa_transport.carrier_for`, `proxy/transport_semantic._get_worker` | A setting whose `driver` is a protocol under the frame puts the session on the frame carrier, whatever the word. A change of word does not move a session. |
| Arriving | `daemon/engine/server.py` | The daemon's port takes every connection. The first four bytes say whose it is: the frame carrier's word (`FRM` and a version) or the TCP session's own first frame. |

**What a driver may rely on, and must not do.**

1. The connection is ordered and its wire has one owner: the writer. A protocol's `pack` is called only by that
   writer, in the order frames go on the wire, and its `unpack` only by the reader, in the order they arrive. So the
   last message each way, for a Role, is a reference both ends hold: send differences against it and keep no lock.
2. A protocol does not seal, and does not require or refuse a key. Key and protocol are independent.
3. A protocol does not know the Role's setting, the count of switches or the key. It is handed a message and gives
   back the data that goes inside the frame.
4. A protocol's word is four bytes and is the first thing in the opened data. A receiver knows the protocol by that
   word and by nothing else, which is why a protocol is switched only while the encryption holds still.
5. Do not add to the frame what it already carries. The packet ID says the Role; the frame says the length.

**What crosses for an unsealed frame**: 2 bytes (length and packet ID as small numbers), the 4-byte word, the
protocol's data. Sealed: 28 more (a 12-byte nonce and a 16-byte tag).

**Measured between three simulated machines, fast link, the Roles exercise, 60 steps, 3,482 reads and writes a run**
(21 November, before each connection had one writer):

| | Run 1, tcp | Run 2, chorus | Run 3, openqasm | Run 4, mixed with 7 changes |
| --- | --- | --- | --- | --- |
| Bytes on the seven Roles' channels | 1,266,071 | 1,216,224 | 768,788 | 1,039,302 |
| A step, median / 95th ms | 381 / 465 | 391 / 479 | 388 / 459 | 383 / 635 |
| The exercise's record | 46,957 bytes | identical | identical | identical |

Each of run 4's changes took 119 to 170 ms from the owner's one write to every end sending the new way.

## 8. Adding a driver

Read `ribbit/examples/homa/EXPLORATION-LOG.md`: section 9 for the procedure, section 4 for what went wrong the first
time, and the entries of 18 November for a driver built as an imitation of something else and then taken out.
