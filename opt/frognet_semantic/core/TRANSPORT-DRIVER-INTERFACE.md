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
   opens a new connection and a new session. (Found 18 Nov on soft Homa: a proxy that kept its session across a
   reconnect talked to a new daemon that was waiting for that session's first frames.)
2. **Gone is gone.** What was outstanding on a connection that ended fails, is said, and is not sent again by the
   driver. Nothing is used in a driver's place.
3. **Nothing on a timer.** A connection is opened when there is something to send or at a network event.
4. **No machine talks to anything but the RAM host** on the wire. An event reaches a node through its held read.
5. **A node that was not told a driver's word does not run it.** `auto` on a node without Homa in its kernel is TCP.

## 5. The drivers

| word | connection | packing | where |
|---|---|---|---|
| `tcp` | two TCP connections the proxy opens, one each way, held open | a 4-byte length; a reply carries the 4-byte number of its request | proxy end: `proxy/transport_semantic._DaemonWorker`; daemon end: `daemon/engine/session.TcpDaemonEnd` |
| `softhoma` | one TCP connection to the daemon on port 9131, held open | Homa's DATA frame as his TCP hijacking puts it on the wire; segments of at most 240,000 bytes | `core/homa_frames.py` (`_Conn` is the six calls), under `proxy/transport_homa.py` and `daemon/engine/server_homa.py` |
| `homa` | one Homa socket (the kernel module) | the module's own: real Homa frames; it cuts, paces and resends | `core/homa_socket.HomaSocket`, under the same two files |

The same interface carries a second wire since 19 November: a rank's session with the FrogTorch RAM
(`agent_workload/tuplespace/frogtorch_ram_drivers.py`). There the proxy and daemon are not involved; the rank's end
connects and the RAM's end listens, with the same six calls and the same per-RAM word.

| word | connection | packing | where |
|---|---|---|---|
| `tcp` (and `auto`) | one TCP connection to the RAM's port P, held open | an 8-byte length and the frame: the bytes that session has always sent | `TcpEnd`, `TcpListener` |
| `softhoma` | one TCP connection to P + 1, held open | Homa's DATA frames, as above | `SoftHomaEnd` (it is `core/homa_frames._Conn`), `SoftHomaListener` |
| `homa` | not written | | a session told this word says so and stops |

There a frame may be handed to `pack()` as a tuple of parts that are the frame in order, so a tensor is not copied to
have its header put in front of it; `unpack()` gives one bytes-like frame back.

## 6. Where the code does not keep to this yet

1. The proxy end of the TCP driver (`_DaemonWorker`) is not written as the six calls; it does the same things under
   its own names. It is existing code and has not been restated.
2. HELLO and the RTT ping are handled once in each driver's session code, not once above the drivers.
3. The two Homa ends (`transport_homa.py`, `server_homa.py`) still call their carrier by the kernel socket's names
   (`send_request`, `send_reply`, `recv`) and still carry the machinery kernel Homa's message limit needs (pieces,
   spare RPCs). Soft Homa's carrier sets its message limit high, so on soft Homa that machinery does not run.
4. The words a setting may use are a fixed list in `core/homa_transport.py`; a driver cannot yet register its own.

## 7. Adding a driver

Read `ribbit/examples/homa/EXPLORATION-LOG.md`: section 9 for the procedure, section 4 for what went wrong the first
time, and the entries of 18 November for a driver built as an imitation of something else and then taken out.
