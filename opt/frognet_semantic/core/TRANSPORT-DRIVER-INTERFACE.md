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
| `tcp` | two TCP connections the proxy opens to the daemon's port, one each way, held open | a 4-byte length; a reply carries the 4-byte number of its request; under a key the frame is sealed and the length word's top bit says so (section 9) | proxy end: `proxy/transport_semantic._DaemonWorker`; daemon end: `daemon/engine/session.TcpDaemonEnd` |
| `chorus` | one TCP connection to the daemon's port for each memory it has something for, held open; it begins `CHO\x02` and the memory's name (section 9) | `pack()` makes a Homa DATA frame in segments of at most 240,000 bytes; on the connection its header and the message's first bytes cross as a difference (section 7, item 8). Called soft Homa until 21 Nov 2026, when it stopped putting Homa's frames on the wire | `core/chorus.py` (`_Conn` is the six calls), under `proxy/transport_homa.py` and `daemon/engine/server_homa.py` |
| `homa` | one Homa socket (the kernel module) | the module's own: real Homa frames; it cuts, paces and resends | `core/homa_socket.HomaSocket`, under the same two files |
| `openqasm` | as chorus, on a connection of its own that begins `OQ3\x01` | chorus's, with every message sealed and the whole message a difference before it is sealed (section 9) | `core/openqasm.py` (`OpenQasmConn`), with the circuit codex `core/openqasm_handler.py` above it |

The same interface carries a second wire since 19 November: a rank's session with the FrogTorch RAM
(`agent_workload/tuplespace/frogtorch_ram_drivers.py`). There the proxy and daemon are not involved; the rank's end
connects and the RAM's end listens, with the same six calls and the same per-RAM word.

| word | connection | packing | where |
|---|---|---|---|
| `tcp` (and `auto`) | one TCP connection to the RAM's port P, held open | an 8-byte length and the frame: the bytes that session has always sent | `TcpEnd`, `TcpListener` |
| `chorus` | one TCP connection to P + 1, held open | the same packing and the same wire, as above | `ChorusEnd` (it is `core/chorus._Conn`), `ChorusListener` |
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

## 7. The channel's setting is a capability tuple

John, 19 November 2026: "It does not have to introduce anything. It is a tuple. A control tuple and part of the system
capability tuple. You do realize we have full control over the contents of the json that tuple holds, right? It does
not need to be standard in any way." And: "You can also introduce other capability tuples. There is not a fixed form,
and the consumer is the thing that wrote it."

The code is `core/channel_setting.py`. The tuple is (channel, setting, semantic) in the ControlRam. Its JSON is that
module's own: `driver` (a word a driver registered, item 5 below; "tcp" and "chorus" are in the tree: the driver a
new session opens on), `key` (the NAME of the key a message is sent under), `accept` (the names a message may arrive
under) and `protection` (the NAME of what is done to a message under a key, item 6; absent, aes-256-gcm). The key is never in the tuple; it is a 32-byte
file on the machine (`FROGNET_CHANNEL_KEYS`, default `/etc/frognet/channel_keys/<name>`). Every daemon and proxy
holds a read on the tuple. A process with no such tuple goes on as before, on its node's word.

1. **A change of driver moves no session.** New requests open a new session on the new driver at once. The old
   session finishes what is outstanding on it and ends after the new one is up. Nothing in flight is failed.
2. **A key is the chorus driver's.** `core/chorus._Conn` seals a whole message under the named key (item 6)
   before cutting it into segments, and marks it in the DATA header's reserved field. A reply goes under
   the key its request came under. The TCP driver carries no key yet.
3. **Accept first, then send, then take away.** The tuple is read over the channel it sets, so an end that is sent
   what it does not yet accept refuses it and cannot read the tuple either (found 19 Nov). The owner therefore widens
   `accept`, waits until every end says it runs that, changes `key`, waits again, and only then narrows `accept`.
   Each end says what it runs in a tuple of its own, (channel, at, its address and process), and the owner's wait is a
   held read on those.
4. **A machine on another carrier has not left.** When a machine's last session on one carrier ends and it has one
   on another carrier of the same daemon, its tuples stay (`homa_transport.on_another_carrier`).

5. **A driver registers its word** (`[A_DRIVER_REGISTERS_ITS_WORD_V1]`, `core/homa_transport.DRIVERS` and
   `register_driver(word, session_kind)`). The words the file and the tuple may use are the registered ones; the list
   `VALID` is no longer the gate. A word names which of the engine's two session kinds stands under it: `tcp`, or the
   message session of `transport_homa.py`. A process has ONE message carrier (`open_homa_socket`), so a new wire
   beside chorus in the same process still means changing that function. A word in the tuple that nobody
   registered is said in the log and the node's own word decides.
6. **A protection is registered by name** (`[A_PROTECTION_IS_REGISTERED_V1]`, `core/channel_protection.py`): two
   functions, `seal(key, nonce, associated data, frame)` and `open(...)`, and a name. `channel_setting.protect` and
   `unprotect`, which the driver's packing and unpacking call, use the one the tuple names. `aes-256-gcm` is the
   machine's own libcrypto through ctypes; the Python module `cryptography` is not needed and is not on the nodes.
   One protection at a time on a connection: the tuple names it, the message does not.
7. **One RAM's connection may have a setting of its own** (`[A_SETTING_PER_RAM_CONNECTION_V1]`). The tuple
   (channel, setting, <the RAM's role name>), for example (channel, setting, databasehost.frognet), is the setting of
   every node's connection to the machine that RAM is on now, in place of (channel, setting, semantic): for the nodes
   that open the connection, and on that machine for what arrives. The same JSON; an empty JSON says nothing for
   that RAM. Where the RAM is comes from this node's hosts file, read when the tuples change and at each hosts event,
   so the setting follows the role. `channel_setting.write(value, ram="databasehost.frognet")` writes it, and each
   end's (channel, at, ...) tuple carries what it runs for each RAM under `rams`.

8. **A driver's control crosses as a difference** (`[THE_HEADER_IS_A_DIFFERENCE_V1]`, 21 Nov; John: "The Homa
   header should diff out... All of the control should diff"). `pack()` makes a whole Homa DATA frame and `unpack()`
   is handed one, as the contract says. Between them, on the connection:
   - `send()` puts the 62-byte header as its difference from what is expected after the last one (2 or 3 bytes), and
     the segment's first 24 bytes as their difference from the segment before, which is where the layer above keeps
     its own control (the message session's magic, id and seq); `receive()` puts both back.
   - Under a key, those 24 bytes are made a difference in `pack()` BEFORE the message is sealed, and the nonce is 8
     bytes drawn once and a count. Secured after it is made small, opened before it is made whole.
   This is the pattern for any driver: the connection is ordered and is the driver's own, so the last unit each way
   is a reference both ends hold. The end that opens says first how it sends (`chorus.WIRE`); a node on this
   build and a node on an older one do not talk chorus to each other, and the connection says so. At 4,800 baud
   a request and reply: tcp 880 ms; chorus 1,121 ms before and 864 ms now; keyed 1,221 ms before and 934 ms now
   (the 16-byte tag each way is what the key still costs).

The test is `simulation/channel_swap_tier.py`; its results are in
`ribbit/examples/homa/REPORT-channel-setting-tuple-swap-under-load-20261119.md`.

## 9. One port, a connection for each memory, three drivers (21 November)

The design these were built for is "CroakOS Protocol Layer Demonstration: Requirements and Detailed Design" (version
0.1, 21 Nov 2026, https://claude.ai/artifact/KfJ8yFePmt15dK7FARWrri). The test is
`simulation/roles_exercise_tier.py`: `--step memories`, `--step protocols`, `--step pages`, and `--run 1|2|3|4|all`,
the exercise itself (seven Roles' services, `simulation/roles_exercise/`) on TCP, on Chorus, on OpenQASM, and mixed
with a channel's protocol, key and algorithm changed under way; the exercise record of every run is byte for byte the
same. The record is the exploration log's entries of 21 Nov.

1. **The setting is read by the memory's name** (`[THE_SETTING_IS_READ_BY_THE_MEMORYS_NAME_V1]`; John, 21 Nov: "The
   setting in a tuple"). Item 7 of section 7 said a RAM's tuple was the setting of the connection "to the machine that
   RAM is on". It is the setting of the connection FOR that RAM. The proxy takes the memory a request is for from the
   request's own Host (`channel_setting.ram_of`: a name that has a tuple of its own); `setting_for`, `here`, `driver`,
   `sending_key`, `protect` and `opened` take the memory. Which memory is at an address is never asked. A request
   that names no such memory (an address, or a name with no tuple) is under the channel's own setting.
2. **A connection is for a memory** (`[A_CONNECTION_IS_FOR_A_MEMORY_V1]`). A session is keyed by address, set and
   memory (`transport_semantic._worker_key`); chorus's carrier holds connections by address, memory and driver word.
   The end that opens says which memory: chorus and openqasm after their word (a length byte and the name), TCP in its
   HELLO after an "@" (`ip#set@memory`). Two memories on one machine are two connections under two settings. A
   request for a memory on the proxy's own machine crosses no connection.
3. **One port; the first bytes say the protocol** (`[ONE_PORT_THE_FRAME_SAYS_THE_PROTOCOL_V1]`; John, 21 Nov: "The
   receivers should be able to examine the received TCP frame and determine the underlying protocol by itself").
   Every driver arrives on the daemon's port. The listener looks at a connection's first four bytes and leaves them:
   a registered word (`core/chorus.WIRES`) and the connection is that driver's (`ChorusOnTcp.adopt`); anything else
   is the TCP driver's first frame (a length, then `FNW1` HELLO) or is closed with the reason in the log. The port,
   the sender and the setting are not consulted. Chorus has no port of its own any more.
4. **A driver built on chorus's connection registers its class** (`[A_CONNECTION_IS_OF_ITS_DRIVER_V1]`,
   `chorus.register_conn`): its `name` is the word a setting names and its `WIRE` the four bytes it begins with.
   Section 6 item 4 is narrower now: a process still has one message carrier, and that carrier holds a connection for
   each driver word. A change between two words of the message kind (chorus to openqasm) opens a new session on the
   new driver's connection; the old one finishes what it has and its connection is closed.
5. **The sealed message is the same for every driver, and says how it was sealed**
   (`[THE_ALGORITHM_IS_PART_OF_THE_SETTING_V1]`; John, 21 Nov: "The algorithm employed is part of the capability
   tuple"; "The keyed store, for example, can apply to all protocols"):

       1 byte     the algorithm's number (1 aes-256-gcm, 2 chacha20-poly1305; a registered one has its own)
       1 byte     the length of the key's name
       n bytes    the key's name
       12 bytes   the nonce: 8 bytes the sending process drew once, then a 4-byte count
       the rest   the sealed frame, ending in the algorithm's 16-byte tag

   The receiver opens it from what it says. The setting decides only whether that algorithm (`protections`; absent,
   only `protection`) and that key (`accept`) are accepted. A reply is sealed as its request arrived. The message's
   number, the algorithm's number and the key's name are bound in. Section 7 item 6's "the tuple names it, the message
   does not" is no longer so. An algorithm is replaced as a key is: widen `protections`, change `protection`, narrow,
   waiting on every end between; the key does not change.
6. **The TCP driver seals** (`[THE_TCP_DRIVER_SEALS_V1]`): at the proxy's end where a frame leaves and a reply
   arrives (`_DaemonWorker._packed`, the reader), at the daemon's in `TcpDaemonEnd.pack` and `unpack`. Request number
   n is message 2n to the seal and its reply 2n+1. Section 7 item 2's "the TCP driver carries no key yet" is no
   longer so.
7. **The OpenQASM driver** (`[OPENQASM_DRIVER_V1]`): every message sealed. `pack()` fails with the reason when the
   setting names no key; an unsealed message ends the connection, and says so. Because it always seals, it always
   makes a message small first, and it makes the WHOLE message a difference against the last one sealed on the
   connection (`[THE_WHOLE_MESSAGE_IS_A_DIFFERENCE_V1]`), where chorus makes the first 24 bytes one.
8. **The circuit codex is above the drivers** (`[OPENQASM_CODEX_V1]`, `core/openqasm_handler.py`, value type 12 in
   `core/codec.py`): a string value that begins `OPENQASM`, on a connection whose setting names `openqasm`
   (`SemanticCodec.qasm`, set by the proxy for a request), crosses as a bitmap of the parameters that changed against
   the value before it and those parameters, or as a loaded circuit's number and its parameters. Circuits are loaded
   from `/etc/frognet/openqasm_circuits` (`FROGNET_OPENQASM_CIRCUITS`), placed before a run as keys are.
9. **Measuring**: with `FROGNET_WIRE_BYTES=1` each driver says the bytes each message put on its connection
   (`[WIRE-BYTES] time driver memory req|rep bytes`). A repeated 174-byte circuit, 21 Nov, between machines: tcp 191,
   chorus 189, chorus under a key 211, openqasm 57.

Not so yet: a held read's fire goes to the holder's address, so it crosses on the connection that is for no memory
(the exploration log, 21 Nov, says what would change that and that it is John's to say).

## 10. Adding a driver

Read `ribbit/examples/homa/EXPLORATION-LOG.md`: section 9 for the procedure, section 4 for what went wrong the first
time, and the entries of 18 November for a driver built as an imitation of something else and then taken out.
