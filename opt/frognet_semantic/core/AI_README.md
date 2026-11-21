# AI_README — memory, codec, handlers

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model (the holistic invariant, the checksum,
and what to do before reporting a defect); this file lists only the hazards in this directory.

## What is here

The parts every node runs. 38 Python files, no subdirectories.

| | |
|---|---|
| `frognet_tuples.py` | the Memory API: `put`, `get`, `get_all`, the scope constructors |
| `codec.py` | BLDC-1 — template learning, diffing, the wire types |
| `semcache_id.py` | the SAME identifier: HMAC-SHA-256 truncated to 16 bytes |
| `frognet_secret.py` | the shared secret the identifier is keyed on |
| `database_handler.py` | the role handler (databasehost): its scoring function |
| `sizing.py` | DB concurrency from three ceilings |
| `store.py` `not_frognet.py` | the negative cache and its `[ONE_STATE_V1]` rules |

## Traps

- **`database_handler.py`'s scoring is static by doctrine.**
  `[DBHOST_STATIC_RANK_V1]` forbids any term that moves with load. If you are
  adding a term, it must be a property of the hardware or its configuration.
  The docstring above `score()` was stale for a long time and named the three
  inputs the doctrine forbids — read the code, not the docstring.
- **`frognet_secret.py` falls back to a published constant** when the secret file
  is missing. It now prints CRITICAL on every derivation. Do not quiet that.
- **The three-coordinate key composes into two columns.**
  `SensorName = "SD:" + var + "." + scope`, `SensorType = service`. The `SD:`
  prefix marks a tuple reapable; observed sensor data carries no prefix and is
  never reaped.

## Homa (`[HOMA_TRANSPORT_V1]`, 2026-11-16)

- **`homa_socket.py` is the only file that knows Homa's system calls**, and its layouts are typed from `homa.h` at
  one commit (`HOMA_PINNED_COMMIT`). They are NOT the same on every HomaModule branch: `HOMAIOCABORT` differs between
  `main` at 07ce63e and the `linux_6.13.9` and `net-next` branches. Run `test_homa_layout_oracle.py <HomaModule dir>`
  against the header of the module actually built for a node before trusting it there.
- **Buffers:** one receive region per socket, registered once; bpages go back on the same thread's next `recvmsg`;
  messages are copied out. Do not hand a view of the region to anything outside the file.
- **`homa_sim.py` is the Homa simulator** (`[HOMA_SIM_V1]`): Homa's protocol from `protocol.md` in user space over
  UDP, behind the same calls as `HomaSocket`, chosen with the word `homasim`. It exists so the Homa drivers and their
  oracles run in the sandbox and the simulator, where the module cannot load. Three things to keep straight: no
  number from it is a Homa number; its tick is 10 ms where the module's is 1 ms (at 1 ms Python re-sent bytes that
  were only in its own queues and timed RPCs out with no loss), so its times are 10 x the module's and its tick
  counts are the module's; and its timer rule is `homa_timer_check_rpc`'s, read from the code -- change it only
  against that function. `test_homa_sim_oracle.py` holds it to the document, rule by rule.
- **The simulator is also where Homa is changed.** The work's thesis is that Homa can stand outside the datacenter,
  as it is or once made to; CroakOS's methods are applied to it one switch at a time, each measured against Homa as
  shipped on the same simulator: `held_waits` (`[HELD_WAITS_V1]`, a waiting request is a held read, not a RESEND and
  a BUSY every resend_interval), `rpc_timeout=0` (`[NO_RPC_TIMEOUT_V1]`, the per-RPC timeout removed: a peer leaving is
  membership's one event, `peer_gone`) and `adaptive` (`[TIMERS_FROM_THE_MEASURED_PATH_V1]`, each socket times its own round
  trips and sets its timers from them). Rules for any further one: each end writes only its own state and reads the
  other's; nothing is copied; no new conversation between the two ends is added to make an old one cheaper.
- **`homa_liveness.py`: whether a peer is there is a held read on its capability tuple**
  (`[LIVENESS_IS_A_HELD_READ_V1]`). Each END of the driver writes its own `(homa, can, <its .1>:<proxy|daemon>)`
  in the ControlRam (`databasehost_control.frognet`, the highest .1: its address is arithmetic, so it is known
  before anything is selected, and a carrier's capability is needed to reach everything else, the SystemRam
  included), with an `inst` that is new every start, and holds one read on the family: a peer end whose tuple is no longer in
  the memory, or is marked stopped, or has another `inst`, is gone. It also puts a file watcher (inotify) on
  `/etc/hosts` (`[HOSTS_IS_WATCHED_V1]`, John 2026-11-16: "You don't need to read hosts; you put a file watcher on
  it"): the merge rewriting it is the network event, and the machines that were in the block (`_alive_ones`, the
  role selection's own rule) and are not now have left. The file is not read to find out whether something happened.
  A machine going down is a network event, so nothing here watches a socket, times an RPC or asks the peer
  anything. Do not look for a transport-level replacement for the TCP close: there is none to find. Hazards:
  - **The stored name carries `SD:`.** `frognet_tuples.put` stores `SD:<var>.<scope>` and api.php matches
    `SensorName__like` against the stored name. A hold on `can.%` reads nothing and never fires. Use
    `frognet_tuples.var_name(var, "%")`. (The example in `frognet_held.py`'s docstring, `caps.%`, has this wrong.)
  - **`frognet_held` imports `frognet_tuples` by bare name.** A process that imports `core` as a package must put
    `core/` on `sys.path` before its first hold (`[CORE_ON_THE_PATH_V1]`), or the hold raises ModuleNotFoundError.
  - **api.php's delete fires no hold; a write does.** An end that stops writes its tuple once more, marked
    `stopped`, before `frognet_tuples`' atexit removes it. That write is what its peers' holds fire on.
  - **The driver removes the closed connection** (John, 2026-11-16). On TCP the socket closing is the close and the
    daemon's driver calls `remove_writer` (`[WRITER_GONE_V1]`). On Homa the watcher is the close: the daemon's
    driver, told a machine left, ends its sessions and makes the same call. Nothing else removes a dead machine's
    tuples, and a hold on its tuple alone never fires.
  - **A read that shows this end's own tuple missing says nothing about any peer's tuple** (the RAM was rebuilt;
    everyone is writing again). Calling peers gone on that read was a defect once.
  - **Two live processes of one role on one node** would write over each other without end; the second write of a
    foreign `inst` is said and not answered.
  - **Not covered:** a peer process killed without stopping, on a machine that stays, and not started again.
  Gates: `test_homa_liveness_oracle.py` (the rule, on a stand-in store) and `test_homa_liveness_ram_oracle.py`
  (the same rule on the real api.php, `frognet_held` and MariaDB; it needs root, php and port 80, and skips without).
- **`homa_transport.py` chooses the carrier per hop** (`[CARRIER_PER_HOP_V1]`; read its docstring). With `auto`
  (the default) kernel Homa is detected by opening an `IPPROTO_HOMA` socket, each end says what it has in its
  capability tuple in the ControlRam, a daemon listens on every carrier it has, and a proxy opens each session on
  the carrier with the lowest measured round trip among those both ends have
  (`docs/spec/spec-05-routing.md` 4.1). Nothing is preferred by kind. A node with no Homa is exactly as it was.
  Detection is not a fallback: a session whose carrier fails ends. A node told `homa` without the module stops with
  `HomaUnavailable`. Gate: `test_homa_carrier_oracle.py`.
- **`[WE_OWN_THE_WIRE_V1]` a transport driver is six calls** (`core/transport_driver.py`, read it): `connect`,
  `send`, `receive`, `pack`, `unpack`, `close`, and `listen` on the end that is connected to. John, 18 Nov: "We own
  the wire. It is whatever connection the driver chooses to make, but it is a permanently open connection ... The
  data on the socket is exactly that -- data ... The packing and unpacking methods are part of the driver."
  Nothing above a driver touches a socket; nothing below it knows what the bytes are. The engine's side of a session
  is `daemon/engine/session.EngineSession` (no socket); the TCP driver's daemon end is `TcpDaemonEnd` in the same file.
- **Chorus is its own carrier, `chorus`: Homa's frames on a connection the driver holds open**
  (`core/chorus.py`). Homa's DATA frame as his TCP hijacking lays it over TCP's header, segments of at most
  240,000 bytes, one TCP connection per daemon on port 9131, opened when there is first something to send and held
  open. It needs nothing built, loaded or told but the word `chorus`, and it is NOT taken on `auto`: a node left on
  `auto` without Homa in its kernel is on TCP exactly as it was. **A session ends with its connection, at both ends**
  (`[A_SESSION_ENDS_WITH_ITS_CONNECTION_V1]`). DO NOT put a memory process, cells or an imitation socket under
  FrogNet's wire: that was built first (17 Nov), measured, and taken out on John's word ("You are trying to mimic a
  message passing system. Dont do that"); `ribbit/examples/homa/EXPLORATION-LOG.md` has what it cost and why. The
  library in `ribbit/examples/homa` (`libsofthoma.so`, `softhoma-ram`) is his API for his own unchanged programs
  (`cp_node`) and no proxy or daemon opens it. With the kernel module the contract is the same and "those functions
  are now done in hardware and use real Homa frames instead of tcp hijack frames".
- **`[FRAMES_GO_IN_PIECES_V1]` a frame larger than one message goes in pieces.** The driver
  (`proxy/transport_homa.py`, `daemon/engine/server_homa.py`) asks the carrier what one message may be
  (`homa_socket.max_message()`: 1,000,000 on kernel Homa; chorus's carrier cuts for itself) and cuts a larger frame or reply
  into numbered pieces, each a whole Homa message, rebuilt before the engine sees it. Nothing is refused for size
  and nothing is shed. The format and the gate are in `ribbit/examples/homa/DESIGN.md` section 5. Do not put a
  size limit back above the driver.
- **No address is kept in a setting.** Never put an address, a RAM host or a node's own address in a setting or an
  environment variable for a service: roles move. A driver connects to the address the hosts file gives a name now.
- **Adding another transport: read `ribbit/examples/homa/EXPLORATION-LOG.md` first**, section 9 for the procedure
  and section 4 for the mistakes already made once. It is the record of how the Homa driver was brought up, wrong
  turns included.
- **`[THE_WIRE_MANAGER_SENDS_V1]` only the wire manager calls the carrier's send.** A thread that has a frame makes
  the wire bytes and queues them (`_ClientMux.send`, the daemon session's writer); `WireManager`'s one thread per
  end sends, whole messages ahead of waiting pieces. Do not send from the thread that has the frame.

## The semantic wire's transport is a setting per RAM connection (17 Nov 2026)

John: "It is a Setting per ram connection, which may be on different machines." `/etc/frognet/semantic_transport`:
the first line is the node's word (`auto`, `tcp`, `homa`, `chorus`); each line after it is a RAM's role name and
the word for this node's connection to that RAM, for example `databasehost_control.frognet tcp`. A RAM is named by
its role, never by an address. `core/homa_transport.py` `[A_WORD_PER_RAM_CONNECTION_V1]`. Rules that came with it:

- No call a driver makes into a library may let an exception out; a library must not be able to end a proxy or a
  daemon (`[THE_MEMORY_CAN_COME_BACK_WHERE_IT_WAS_V1]`).
- A listener that is down waits for an event, every event it can see wakes it, and it says in its log which one.
  Do not arrange the order things start in. John: "the programming model changes from lock-ordered sequential
  messaging to event driven."
- A held read returns when what it holds changes. Writing the same tuple again wakes nobody.
- Before changing what handles an event, log which event it was.

## What a transport driver must implement

`core/TRANSPORT-DRIVER-INTERFACE.md`: the contract, the calls on each side, where the driver sits (below semantic
compression), and the five places the code does not keep to it yet. Read it before writing or changing a driver.

## A semantic request says which field order it was encoded in (18 Nov 2026)

`core/template_order.py`, `[THE_FRAME_SAYS_ITS_FIELD_ORDER_V1]`. Values travel by position and each machine learns
its own templates, so two machines can hold different field orders under one opcode. Every REQ_FULL, REQ_DIFF and
REQ_REPEAT ends with the asker's request-template and reply-template fingerprints. A daemon whose request order
differs does not decode or run the request (the proxy then sends it raw); one whose reply order differs answers
raw. Do not add a positional encode or decode path that skips this comparison.
