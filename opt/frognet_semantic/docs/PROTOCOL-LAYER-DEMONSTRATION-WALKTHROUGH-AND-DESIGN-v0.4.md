# CroakOS Protocol Layer Demonstration: Walkthrough and Detailed Design

Nov 21, 2026 · @John

|  |  |
| --- | --- |
| Document | Walkthrough and Detailed Design |
| Subject | How the CroakOS protocol layer is set up and administered: three protocols on one mesh, with protocol, key and algorithm switched by each Role's owner under a running exercise |
| Version | 0.4, draft for review |
| Date | November 21, 2026 |
| Author | John W. Fawcett |
| Organization | Fawcett Innovations, LLC (CAGE 1A5Y5), Burien, Washington |
| Classification | Unclassified. Built only from open-source code and public references. |
| Status | Design complete. Construction under way; section 11 says what is built. |

**Revision history**

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | November 21, 2026 | First issue of the requirements and detailed design |
| 0.2 | November 21, 2026 | One current key, switched in the stream. Protocol and encryption separated. Owners write their own settings. Injection by the Instructor's own tuple. The TCP run sets the performance thresholds. |
| 0.3 | November 21, 2026 | Rewritten around the people who operate the system. Sections 2 to 8 walk through setup and each kind of switch. The setting gains a count of switches. The rules of the exercise itself are unchanged from 0.2 and are not repeated here. |
| 0.4 | November 21, 2026 | A setting proves itself with a check made from the Role's set of keys, and an end ignores a setting whose count is lower than the one it is running (10.1). A frame that does not open is tried under every key of the set and every algorithm, and the pair that opens is the current one (10.5). A connection's wire has one owner (10.5). Section 11.3 brought up to the code. |

## 1. Executive summary

A defense system must be able to change how its sites talk to each other without stopping. A new encryption algorithm is mandated, a key has been in use long enough, or a link needs a different protocol. Today each of those is a program of work with an outage in it. This document shows them done by one person, at a screen, while the system keeps running.

The demonstration is a warning-and-response exercise run across three simulated sites. Seven Roles take part. Each Role is a service with its own memory, and each Role's traffic has its own protocol and its own encryption, chosen by that Role's owner and by no one else.

The owner can switch two things, and they are different things.

- **A protocol switch** changes the data inside the frame. The encryption on the frame is not touched.
- **A key switch** changes the encryption on the frame. The data inside is not touched.

Each switch is one command from the owner. The sender marks the switch at one exact place in the stream, and everything after that place is new at both ends. Nothing is negotiated, no connection is closed, and no process restarts. A request that was in flight asks again and is answered from the other side's cache without being run twice.

The exercise is run four times: on TCP, on Chorus, on OpenQASM, and with all three mixed while owners switch protocols, keys and an algorithm under way. The demonstration passes when the record of the exercise is the same, byte for byte, all four times, on a fast link and at 4,800 baud.

Sections 2 to 8 walk through the system as its people use it. Sections 9 and 10 are the rules and the design at the level it is built. Section 11 says how it is proved and where the build stands.

## 2. The people and what each one does

Four kinds of people touch the system. Each has one job, and no job overlaps another.

| Person | Tool | What they do | What they never do |
| --- | --- | --- | --- |
| A machine's administrator | The machine itself | Installs the software. Places each Role's set of keys on the machines that carry that Role's traffic. This is done offline, before use. | Chooses a protocol or commands a switch |
| A Role's owner | That Role's admin page, `<Role>Admin` | Chooses the protocol, the encryption and the algorithm for the Role's own traffic. Commands each switch. | Sets anything for another Role |
| The Instructor | `InstructorAdmin` | Loads the scenario, starts and ends the exercise, publishes injections. Sees every channel's state. | Changes any channel's setting, or writes into another Role's memory |
| An operator at a Role | The view in that Role's admin page | Watches the exercise from that Role's position. Enters that Role's own inputs. | Sees or sets how the Role is reached |

There are seven Roles. A Role is a service. It may need other Roles in order to work, and it uses them by reading what they publish.

| Role | What it does in the exercise | Site |
| --- | --- | --- |
| Exercise | Keeps the clock and the ground truth, and scores the result | A |
| Warning | Reports detections from its sensors | A |
| Assessment | Turns each detection into a circuit and a confidence | B |
| Compute | Runs the circuits on two simulated quantum processors, one at B and one at C | B |
| Instructor | Directs the exercise and publishes injections | B |
| Command | Sets the alert level and issues orders | C |
| Units | Carries out orders | C |

The Role and the way the Role is reached are kept apart throughout. An operator at Units sees units and orders. Nothing on that view says whether Units is reached by TCP or Chorus, or under which key, and nothing in the Units service knows either.

## 3. Walkthrough: setting the system up

Setup is five steps. The first two are done by the machines' administrators, the next two by the Roles' owners, and the last by the Instructor.

### Step 1. The keys are placed, offline

For each Role there is a set of keys. In the demonstration a set is twenty keys, each a file of 32 bytes. A set can be fifty, or however many its owner chooses.

The administrator of each machine places a Role's set in that machine's key store, `/etc/frognet/channel_keys`, if that machine carries the Role's traffic. A machine that does not carry a Role's traffic does not hold that Role's keys. The sets are carried to the machines by hand. Nothing in a set ever crosses a link, at setup or afterwards.

### Step 2. The software is started

The administrator starts the network software on each machine. The machines find each other and form the mesh. The network elects which machine hosts the system memory, the control memory, and each Role's memory. Nobody enters an address, and no Role is told where another Role is. A service finds a Role's memory by its name.

In the demonstration the election places the Roles' memories at the sites shown in section 2.

### Step 3. Each owner sets their Role's channel

Each Role's owner opens that Role's admin page. The page has a Channel panel with three choices.

| Choice | What the owner picks | In the demonstration |
| --- | --- | --- |
| Protocol | One of the protocols the system has registered | `tcp`, `chorus` or `openqasm` |
| Encryption | Off, or on with a seed | A seed the owner types or has the page generate |
| Algorithm | One of the algorithms the system has registered | `aes-256-gcm` or `chacha20-poly1305` |

The seed is not a key. It decides the order in which the stored keys are used. Every end that holds the same set and the same seed arrives at the same key, with nothing exchanged.

When the owner presses Set, the page makes one write. It writes the Role's channel setting, a tuple the owner owns, into the control memory:

```
(channel, setting, units) = {"driver": "chorus", "seed": "<the seed>",
                             "protection": "aes-256-gcm", "switches": 0}
```

### Step 4. Every end takes the setting and reports it

Every machine that carries the Role's traffic holds a read on that tuple. The write wakes them. Each one takes the setting, selects the key the seed picks from its stored set, and writes a report of what it is now running:

```
(channel, at, <address>:<process>.<pid>) = {"units": {"driver": "chorus", "switches": 0,
                                                      "protection": "aes-256-gcm"}}
```

The owner's page holds a read on those reports and lists them. The channel is set when every end shows the same line. A report never contains a key or a seed.

For the mixed run the seven owners set their channels like this.

| Role | Protocol | Encryption |
| --- | --- | --- |
| Warning | TCP | Off |
| Exercise | Chorus | Off |
| Assessment | Chorus | On |
| Compute | OpenQASM | On |
| Instructor | Chorus | Off |
| Command | Chorus | On |
| Units | TCP | Off |

No two of these had to agree. Two Roles whose memories are on the same machine, such as Assessment and Compute at site B, run different protocols and different keys over the same connection.

### Step 5. The Instructor loads the scenario and starts

The Instructor opens `InstructorAdmin`, loads the scenario file and presses Start. Each Role's service reads the scenario, publishes its starting values and reports ready. When all seven are ready the clock starts and the exercise runs, one step at a time.

`InstructorAdmin` also shows every channel's reports together. The Instructor can watch every channel and can change none of them.

## 4. A protocol and a key are different things

Everything that crosses a link is a frame. A frame has an outside and an inside, and the two belong to different layers.

```
on the link   [ length ][ packet ID ][ sealed under the Role's current key .... ]

opened        [ length ][ packet ID ][ protocol word ][ the protocol's data ... ]
```

- **The encryption is the outside.** One layer seals every frame after it is packed and opens it before it is unpacked. That layer serves every protocol and belongs to none of them.
- **The protocol is the inside.** The data inside the frame begins with the protocol's own word, and the rest is whatever that protocol's driver put there.

A receiver learns the protocol by opening the frame and reading the word. It is not told the protocol by a port, by the sender, or by the setting. That one fact gives the rule that governs every switch:

**A protocol is switched only while the encryption holds still, and a key is switched only while the protocol holds still.** The receiver must be able to open the frame to see what is in it. The two are never switched in one step.

|  | Protocol switch | Key switch |
| --- | --- | --- |
| What changes | The data inside the frame | The key the frame is sealed under |
| What does not change | The key and the algorithm | The protocol |
| What the owner does | Picks another protocol and presses Switch protocol | Presses Switch key |
| What the owner's page writes | The setting, with the new protocol | The setting, with its count of switches raised by one |
| How the sender marks it | A call in the stream that says only *switch protocol* | A call in the stream that says only *switch encryption* |
| What the receiver needs | Nothing new. It opens the next frame as before and reads a different word. | Nothing sent to it. It holds the set and the seed, and takes the next key. |
| What happens to the connection | It stays open | It stays open |
| What an application sees | Nothing | Nothing |

A change of algorithm is a key switch with one more thing in the write: the new algorithm's name. It moves to the next key like any other switch, so no key is ever used under two algorithms.

Key and protocol are independent. Any of the three protocols runs under any key, or under none.

## 5. Walkthrough: the owner switches a protocol

The exercise is at step 15. Units is on TCP with encryption off. The owner of Units decides to move it to Chorus. The exercise does not pause.

**What the owner does**

1. Opens `UnitsAdmin` and goes to the Channel panel. It shows `tcp`, and one line for each end, all the same.
2. Picks `chorus` from the list of registered protocols.
3. Presses Switch protocol.
4. Watches the lines. Each end's line changes from `tcp` to `chorus`. When the last one has changed, the panel says the switch is complete.

That is the whole of the owner's part. The owner does not name the machines, does not schedule a window, and does not tell the other Roles.

**What the system does**

1. The page writes the Units setting with `"driver": "chorus"`. Nothing else in the setting changes.
2. Every end that carries Units traffic holds a read on that tuple, and the write wakes it.
3. Each end, as a sender, puts one call into its stream on the open connection. The call says *switch protocol* and nothing more. It does not say from what or to what.
4. From its next frame that end sends Chorus data inside the frame. The frame is sealed, or not, exactly as it was.
5. The other end reads the call, then opens the next frame as before and finds the word `CHO` at the front. It hands the data to the Chorus driver.
6. Each end writes its report with `chorus`, and the owner's page shows it.

```
sender     ... TCP frame, TCP frame, SWITCH PROTOCOL, Chorus frame, Chorus frame ...
receiver   ... read,      read,      sees the call,   reads Chorus, read         ...
```

The connection is an ordered stream, so the call sits at one exact place in it. Everything before that place is TCP and everything after is Chorus. The two ends cannot disagree about any frame, however slow the link.

A connection has two directions, and each is its own stream. Each end switches its own sending when its held read wakes it. For a moment one direction can be Chorus while the other is still TCP. That is correct, because each frame says what it is.

**What the Roles see**

Nothing. The Units service wrote the same tuples before, during and after. The services that read from Units got the same values. The operator's view did not flicker. The only place the switch is visible is the Channel panel, and on `InstructorAdmin`, where every channel's reports are shown together.

## 6. Walkthrough: the owner switches a key, and an algorithm

### 6.1 Switching a key

The exercise is at step 35. Assessment is on OpenQASM, sealed with AES-256-GCM under the first key its seed picked. Its owner decides the key has been used long enough.

**What the owner does**

1. Opens `AssessmentAdmin` and goes to the Channel panel. Each end's line shows `openqasm`, `aes-256-gcm` and a count of switches, all the same.
2. Presses Switch key.
3. Watches the lines. Each end's count goes up by one. When the last one has, the panel says the switch is complete.

The owner does not choose the new key and never sees it. The page does not hold keys.

**What the system does**

1. The page writes the Assessment setting with its count of switches raised by one. Nothing else changes.
2. The write wakes every end that carries Assessment traffic.
3. Each end, as a sender, puts one call into its stream, sealed under the key in use. The call says *switch encryption* and nothing more.
4. That end then works out the next key. It applies the same fixed rule to the seed and the count, and takes that key from its stored set. Every frame it sends after the call is sealed under the next key.
5. The other end reads the call under the key in use, works out the same next key by the same rule, and opens every frame after the call with it.
6. Each end drops the earlier key. There is only the current key.
7. Each end writes its report with the new count.

```
sender     ... frame, frame, SWITCH ENCRYPTION, frame under the next key, frame ...
receiver   ... read,  read,  sees the call,     opens with the next key,  read  ...
```

No frame fails to open because of the switch. The call marks the exact frame at which the key changes, and both ends pass that mark in the same order. There is no window in which both keys are accepted, because none is needed.

Nothing about the key crosses the link: not the key, not its name, not its place in the set. The call says only that a switch is happening.

### 6.2 Turning encryption on

At step 30 the owner of Units turns encryption on for a channel that had none. It is the same act. The owner sets Encryption to on, the page writes the seed into the setting and raises the count, each sender sends *switch encryption*, and every frame after the call is sealed. Before the call the frames were not sealed, and after it they are.

### 6.3 Replacing the algorithm

At step 50 the owner of Assessment replaces AES-256-GCM with ChaCha20-Poly1305. This stands in for a future mandated algorithm.

The owner picks the algorithm in the Channel panel and presses Switch algorithm. The page writes the setting with the new algorithm's name and the count raised by one. Each sender sends the same *switch encryption* call. Every frame after the call is sealed with the new algorithm under the next key of the set.

It differs from a key switch in one way. The key follows from a rule both ends hold, but the algorithm's name is in the setting, and the call can reach a receiver before its own held read on the setting has woken it. The receiver does not wait. The next frame does not open under the algorithm it held, so it runs through the Role's set under every registered algorithm, takes the pair that opens, and continues.

### 6.4 What causes a switch

In the demonstration the cause is an owner pressing a button. The cause can equally be a schedule, a count of messages, or an event. Whatever it is, it acts at the sender only. The receiver follows the call in the stream, so the two ends need no shared clock and no agreement in advance about when.

## 7. A request in flight across a switch

Every request is on its own thread, and the thread knows what it asked. A switch does not lose a request, because the call marks one place in the stream and both ends pass it in order.

A request can still be lost for another reason while a switch is under way, for example if the connection itself drops. The thread then retries, and the retry is the same in every case.

1. The thread's reply does not arrive, or does not open.
2. The thread sends CACHE_MISS under the current key. It is a short message that names the request already sent, by its hash.
3. If the other side ran that request, it holds the answer. It returns the answer under the current key and runs nothing.
4. If the other side never received the request, it says so, and the thread sends the request in full.
5. The retry is written to the log with the request's number.

A request is therefore never run twice and never lost. A retried request counts as delivered, and the number of retries in each run is reported.

The system has this message today for a related case. A sender that lacks an answer the other side holds asks for it by naming the answer, and is served from the cache. For a request in flight the thread has no answer to name, so the message is extended to name the request.

## 8. Walkthrough: the mixed run, change by change

The fourth run is the objective. It starts with the seven channels set as in section 3, three protocols on one mesh. The exercise runs for 60 steps. During it, two owners make seven changes, and the Instructor makes four injections. Neither knows what the other is doing.

| Step | Who | What they do | Kind of switch |
| --- | --- | --- | --- |
| 10 | Instructor | Injects a failed sensor at Warning, until step 30 |  |
| 15 | Owner of Units | Switches Units from TCP to Chorus | Protocol |
| 20 | Instructor | Injects a false detection at Warning, until step 26 |  |
| 25 | Owner of Assessment | Switches Assessment from Chorus to OpenQASM | Protocol |
| 30 | Owner of Units | Turns encryption on for Units | Key |
| 35 | Owner of Assessment | Switches Assessment's key | Key |
| 40 | Instructor | Injects a drifted calibration on processor 1 at Compute, until step 52 |  |
| 45 | Owner of Units | Switches Units' key | Key |
| 50 | Owner of Assessment | Replaces Assessment's algorithm with ChaCha20-Poly1305 | Key, with a new algorithm |
| 55 | Owner of Units | Replaces Units' algorithm with ChaCha20-Poly1305 | Key, with a new algorithm |

The fourth injection is not tied to a step. When Command's alert level reaches 2 or lower, the Instructor injects lost communications on one unit, for six steps.

The order of the changes obeys the rule of section 4. Units changes its protocol at step 15 and its encryption at step 30, never both at once. Assessment does the same at steps 25 and 35.

**What the Instructor's injections show.** An injection is one tuple the Instructor writes in its own memory. It names a Role and a value. The Role named reads it and writes the injected value into its own memory for as long as the injection stands. The failed sensor of step 10 is still in force when Assessment, which reads Warning, switches protocol at step 25. The drifted calibration of step 40 is in force while Units and Assessment switch keys and algorithms around it. Injected values and switches do not interfere, because a Role's values and the way the Role is reached are separate things.

**What a viewer watches.** `InstructorAdmin` shows the seven channels side by side, each with its ends' reports. At step 15 the Units row turns from `tcp` to `chorus`, one end at a time. At step 35 the count on the Assessment row goes up by one. Beside it, the exercise's own view keeps stepping: detections, confidences, orders and the score.

**What must be true at the end.** The record of this run, step by step, is the same as the record of the run made entirely on TCP, byte for byte.

## 9. The rules of the system

CroakOS is built from a small number of rules, and every step of the walkthrough follows from them. A reader who expects the mechanisms of a conventional distributed system (negotiation, locks, leases, access lists, rotation windows) will not find them, because none of them exist here.

| Rule | What it means | Where it appeared in the walkthrough |
| --- | --- | --- |
| 1. Every value has one owner, and only the owner writes it. | A tuple is written by the Role that owns it. Everyone else reads. | A Role's owner sets that Role's channel. The Instructor injects by writing its own tuple. There is no access list, because there is no second writer. |
| 2. Coordination is a held read. | A reader asks for a value and is woken when it changes. Nothing polls and nothing is negotiated. | The owner's one write wakes every end. The owner's page is woken by each end's report. |
| 3. Protocol and encryption are separate. | Encryption is applied to the frame. The protocol is the data inside it. | A protocol is switched while the encryption holds still, and a key while the protocol holds still. |
| 4. There is only the current value. | No history is kept and no earlier value is consulted. A switch is a switch. | A channel has one key. Each end drops the earlier key when it passes the call. |
| 5. A thread with work in flight retries. | Every request is on its own thread, and the thread knows what it asked. | CACHE_MISS names the request, and the answer comes from the cache. |
| 6. Nothing competes. | Work is assigned by its owner from published values. Nothing is claimed, raced for or locked. | Compute's planner assigns each circuit to a processor. The same inputs give the same record on any protocol. |
| 7. The wire belongs to the driver. | The connection is permanently open. What crosses it is whatever the driver puts in the frame. | Three protocols share one mesh, and a protocol is replaced on the open connection. |

One more point governs scope. Protocol and encryption are chosen per Role. They can be chosen more narrowly still, by application name or by one instance of an application, and a Role can have more than one in force at once. None of these choices is site-wide.

## 10. Detailed design

Paths are relative to `opt/frognet_semantic` in the repository, on the branch `homa-under-croakos-20261116`. Where a name exists in the code today it is marked *existing*. Everything else is new, and new file names are proposals.

### 10.1 The channel setting

A Role's channel setting is one tuple in the control memory. Its owner is the Role, and the Role's admin page is its only writer.

| Tuple | Meaning |
| --- | --- |
| `(channel, setting, <Role>)` | How that Role's traffic is carried, on whatever connection carries it |
| `(channel, setting, semantic)` | *Existing.* The setting for traffic that belongs to no Role, such as reads of the system memory |
| `(channel, at, <address>:<process>.<pid>)` | *Existing.* Written by each end: what it is running now, for each Role it carries |

```
{"driver": "chorus", "seed": "<the seed>", "protection": "aes-256-gcm", "switches": 3}
```

| Field | Meaning | State |
| --- | --- | --- |
| `driver` | The protocol: a word a driver has registered | *Existing*; the word `openqasm` is new |
| `seed` | Decides the order in which the stored keys are used. Empty means the channel is not sealed. | New. Replaces `key`, which named a key. |
| `protection` | The algorithm frames are sealed with | *Existing* |
| `switches` | How many encryption switches the owner has commanded | New |
| `check` | Proves the setting was written by someone who holds the Role's set of keys | New |

The fields `accept` and `protections`, and the key change made in three writes, are removed. The seed crosses the network inside the setting. The keys never do. Knowing the seed tells which key of a set is in use and nothing about any key.

**The setting proves itself.** Nothing stops any member of the network writing any tuple, and that is not prevented. One forged write would matter: a setting with an empty seed turns a Role's encryption off. So a setting carries its own proof: one field, and two rules for the ends.

1. `check` is an HMAC-SHA256 over the Role's name and the setting's other fields. Its key is the SHA-256 of the Role's stored keys taken in file-name order. The key comes from the whole set, not from the seed or the count, so it works for a setting whose seed is empty. Only a machine that holds the Role's set can make it.
2. An end that holds a set for the Role ignores a setting whose check does not verify. It logs the reason and keeps running the setting it has. An end with no set for that Role has nothing to verify with and nothing to protect.
3. An end ignores a setting whose `switches` is lower than the count it is running, and logs it. Without this, an old valid setting, such as a Role's first unsealed one, could be written again. Turning encryption off is therefore a new write with a higher count.

There is no identity, no signature and no access list. The Role's admin page computes the check, so it is served from a machine that holds the Role's set. In the demonstration that is the site hosting the Role.

The text that is checked is the Role's name, then each other field as `name=value`, sorted by name, one to a line.

### 10.2 The key store and the selection rule

The key store is a directory on each machine, `/etc/frognet/channel_keys` (*existing*). A Role's set is the files in `channel_keys/<role>/`, each 32 bytes. Sorted by file name, they are numbered 0 to N-1. The set is placed offline. A machine holds a set only if it carries that Role's traffic.

The key in use after n switches is chosen by one rule, the same at every end. Let h(n) be the first eight bytes of SHA-256 of the seed followed by n, read as a number.

```
key(0) = h(0) mod N
key(n) = (key(n-1) + 1 + (h(n) mod (N-1))) mod N
```

The second line never returns the key already in use, so every switch changes the key. An end keeps only the key in use and the number n.

### 10.3 The frame

One layer builds and reads every frame, for every protocol. No driver seals, and no driver requires or refuses a key.

```
1 to 4 bytes   length of the body, as a small number
1 to 3 bytes   the packet ID, as what the far end does not already know of it
               the body, when the Role's channel is sealed:
12 bytes       nonce
n bytes        the sealed data
16 bytes       tag
               the body, when it is not sealed:
n bytes        the data
```

A packet ID is a counter, the Role's sequence (8 bits) and a reply bit. On the connection it crosses as one byte when the frame is the next request of the same Role as the frame before it. An unsealed frame therefore costs 2 bytes and the protocol's word.

The data, once opened, begins with the protocol's word.

| Protocol | Word | Then |
| --- | --- | --- |
| TCP | `TCP` and a version byte | The message. Its number is the frame's packet ID. |
| Chorus | `CHO` and a version byte | A header difference, a first-bytes difference, the rest of the message |
| OpenQASM | `OQ3` and a version byte | The same as Chorus, with the circuit codex above it |

The packet ID is what lets two Roles with different keys share one connection. It is *existing*: every packet already carries one, and the sender allocates it. A reply carries its request's ID, so the end that asked already knows which Role, and which key, the reply belongs to. For requests, the sender gives each Role its own sequence of IDs on the connection. The first packet of a sequence names the Role and states the sender's count of switches, in the clear, and carries no data. From then on the receiver reads a packet's ID, knows the Role, takes that Role's current key and opens the frame. Nothing is added to the frame for this. A call of 10.4 takes its ID from the Role's sequence like any other packet.

A sealed frame does not name its key or its algorithm. The packet ID is bound into the seal, so a sealed reply cannot be moved to another request or another Role. The first eight bytes of the nonce are drawn fresh when a process starts, and are never stored.

Data that begins with no registered word is refused, with the reason in the log.

### 10.4 The two calls

A switch is marked by a frame like any other, sealed like any other, whose data is one of two words and nothing else.

| Call | Data | Effect on the sender | Effect on the receiver |
| --- | --- | --- | --- |
| Switch protocol | `SWP` and a version byte | Its next frame for this Role carries the new protocol's data | None needed; each frame says what it is. The call is logged. |
| Switch encryption | `SWE` and a version byte | It raises its count for this Role by one and seals its next frame under key(n) | It raises its count for this direction by one and opens the next frame under key(n) |

Neither call says what is being switched from or to.

### 10.5 The connection

A process holds one connection to each destination, whatever protocols are in use on it. This replaces the code on the branch, which opens a connection for each driver and each memory and lets the old one finish.

Each direction of a connection is its own stream. For each Role on each direction, an end keeps the protocol word, the count of switches and the key in use. An end switches its sending when its held read on the setting wakes it, and switches its receiving when the call arrives.

**One owner for each direction.** A connection's wire has one owner. One writer for each connection takes messages from the request threads and alone gives packet IDs, sends switch calls, packs, seals, draws the nonce and writes. No request thread touches the socket, and there is no lock on sending. Reading has one owner as well. Everything that depends on the order of the wire is done by one of those two threads.

A connection opened after switches have been made starts from the setting's count. Its first frame for the Role states that count, so the receiver starts from the same key even if its own held read has not yet woken it.

**A frame that does not open.** When a Role's frame does not open under the key and algorithm an end holds, at connection or after, the end runs through every key of the Role's set under every registered algorithm. The pair that opens the frame is the current one. The end takes it, logs what it held and what opened, and carries on. Its count of switches is unchanged. If nothing opens, the frame is refused and logged.

A receiver therefore follows a sender with nothing but the Role's set. It needs neither the setting nor the count to be up to date, and there is no limit on how fast an owner may command switches. A receiver never reads the setting on the thread that reads the connection.

One change the search cannot find is encryption turned off, because there is nothing to open. An end takes unsealed frames for a Role only when the setting it holds, which has proved itself, shows no seed.

### 10.6 Reports

Each end writes `(channel, at, <address>:<process>.<pid>)` whenever what it runs changes. For each Role it carries, the report gives the protocol, the count of switches and the algorithm. The owner's page and `InstructorAdmin` hold reads on these tuples. A switch is complete when every end's report shows it.

### 10.7 CACHE_MISS

The message `REQ_SAME_MISS` is *existing*, in `core/semcache_wire.py`. It names an answer, and the receiving side returns that answer from its cache and runs nothing. CACHE_MISS is the same message extended to name a request by its hash. The receiving side keeps, for each request it has run on a connection, the hash of the request against its answer. It answers from that, or says it never received the request.

### 10.8 The admin page's Channel panel

Each `<Role>Admin` page gains a Channel panel. It shows the Role's setting and every end's report, and has four commands: Set, Switch protocol, Switch key and Switch algorithm. Each command is one write of the Role's setting through the page's door. The page refuses a command that would change the protocol and the encryption in one write. `InstructorAdmin` shows all seven panels, read-only.

### 10.9 What changes in the code

| File | Change |
| --- | --- |
| `core/channel_frame.py` | New. The frame, a sequence of packet IDs for each Role, sealing and opening, the two calls. |
| `core/channel_setting.py` | `seed` and `switches`; the selection rule; settings read by Role. `accept`, `protections` and the address rule are removed. |
| `core/channel_protection.py` | Unchanged in what it does: AES-256-GCM and ChaCha20-Poly1305 from the system's library. |
| `core/chorus.py`, `core/openqasm.py` | Sealing is taken out. Each packs and unpacks the data inside the frame, beginning with its word. |
| `proxy/transport_semantic.py`, `daemon/engine/session.py`, `daemon/engine/server.py` | One connection per destination. The TCP session becomes a driver under the same frame. |
| `core/semcache_wire.py`, `daemon/engine/session.py` | CACHE_MISS naming a request. |
| `simulation/roles_exercise_tier.py` | Runs the existing application election for the Roles' memories. Has each Role's admin write its own setting. Captures the record by held reads. |
| The seven `<Role>Admin` pages | The Channel panel. |

Discovery and routing are not touched. The election of Role memories uses `core/app_hosts.py` as it is.

## 11. How it is proved, and where the build stands

### 11.1 The four runs

| Run | Channels | Purpose |
| --- | --- | --- |
| 1 | All seven on TCP, sealed | The reference. Its record is what the others must match, and its measured performance sets the thresholds. |
| 2 | All seven on Chorus, sealed | The same exercise on a second protocol |
| 3 | All seven on OpenQASM, sealed | The same exercise on a third |
| 4 | Mixed, with the seven changes of section 8 | The objective |

The runs are made between simulated machines running the real network software, once on a fast link and once at 4,800 baud.

### 11.2 What passing means

1. The exercise record of runs 2, 3 and 4 is identical to run 1's, byte for byte.
2. No request is lost and none returns a wrong value. Retries are counted and reported.
3. Every injected value is in force at every step from its injection to its release.
4. No daemon, proxy or Role service restarts.
5. Every end reports every change.
6. All of the above hold again at 4,800 baud.
7. On each link, the request time, step time and bytes of runs 2, 3 and 4 are no greater than run 1's. No threshold comes from anywhere but the TCP run.

### 11.3 Where the build stands on November 21, 2026

| Part | State |
| --- | --- |
| The seven Role services, the scenario and the exercise's rules | Built. Run between three simulated sites. |
| The circuit simulator | Built. It returns the design's five reference values for the assessment circuit. |
| Injection by the Instructor's own tuple | Built. All four injections were in force at every step until released, in all four runs. |
| Election of the Roles' memories in the simulator | Built. Each of the three simulated machines ran the existing election unchanged and elected the seven memories the same, at the sites of section 2. |
| The frame, the two calls, the key rule, packet IDs by Role | Built, in `core/channel_frame.py`. 21 checks pass on a real connection between two ends. |
| One connection per destination | Built, in `core/channel_carrier.py`, and carrying all traffic between the simulated machines. Each site holds one connection to each other site, with every Role there on its own sequence of packet IDs. |
| The setting with `seed`, `switches` and `check`, and its two rules | Built. 11 checks pass. |
| A frame that does not open is tried under every key and algorithm | Built. Used twice in the mixed run, at the two algorithm switches. |
| One writer for each connection | Built for the frame layer and for Chorus's own connections. The daemon's and the proxy's reference handling still use their locks; that change is next. |
| Chorus and OpenQASM as protocols under the frame | Built. Neither seals. The drivers' own sealing code is removed. |
| The four runs on a fast link | Passing. The record is identical in all four runs, 46,957 bytes. Each run made 3,482 reads and writes and none failed. Each of run 4's seven changes took 119 to 170 ms from the owner's write to every end sending the new way. |
| Criterion 7 on a fast link | Not met. Runs 2 and 3 are under run 1 on bytes and 7 to 10 ms over it on the median step. Run 4's 95th-percentile step is over. |
| The four runs at 4,800 baud | Not run on this mechanism. |
| CACHE_MISS naming a request | Not built. |
| The record captured step by step by held reads | Not built. The record is still read at the end of a run. |
| The Channel panel | Not built. The test harness writes each Role's setting, from that Role's own site. |
| The seven admin pages | Built, without the Channel panel. Not yet checked in a browser. |

Measured on the fast link, between three simulated machines:

| | Run 1, TCP | Run 2, Chorus | Run 3, OpenQASM | Run 4, mixed |
| --- | --- | --- | --- | --- |
| The run, seconds | 25.3 | 25.7 | 25.3 | 26.8 |
| A step, median ms | 381 | 391 | 388 | 383 |
| A step, 95th percentile ms | 465 | 479 | 459 | 635 |
| Bytes on the seven Roles' channels | 1,266,071 | 1,216,224 | 768,788 | 1,039,302 |

These figures were measured before each connection was given one writer. The mixed run has been run again since and passes.

## 12. Glossary

| Term | Meaning |
| --- | --- |
| Call | A frame in the stream that says only *switch protocol* or *switch encryption* |
| Channel | How one Role's traffic is carried: its protocol and its encryption |
| Channel setting | The tuple in which a Role's owner states the protocol, seed, algorithm and count of switches |
| Chorus | A CroakOS protocol in which control information crosses as a difference from what both ends already hold |
| Control memory | The network's memory for settings and capability tuples |
| End | A process on a machine that carries a Role's traffic |
| Frame | One unit on the link: a length, a packet ID, and data that is sealed or not |
| Held read | A read that is answered when the value changes. It is how everything in the system is coordinated. |
| Injection | A value the Instructor publishes in its own memory, which the Role it names applies to its own memory until released |
| Key set | The keys placed offline on a machine for one Role |
| OpenQASM | An open language for quantum circuits. Here it is also the name of the protocol that carries them. |
| Owner | The one writer of a value. A Role's owner sets that Role's channel. |
| Report | The tuple each end writes to say what it is running |
| Role | A service. It may need other Roles to work. Each Role can be in a different memory, with different encryption. |
| Seed | A value in the setting that decides the order in which a set's keys are used. It is not a key. |
| Switch | A change of protocol or of encryption, marked at one place in the stream by a call |
