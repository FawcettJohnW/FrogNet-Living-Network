# CroakOS Protocol Layer Demonstration: Requirements and Detailed Design

Nov 21, 2026 · @John

|  |  |
| --- | --- |
| Document | Requirements and Detailed Design |
| Subject | Demonstration of the CroakOS protocol layer: three protocols on one mesh, with protocol, key and algorithm changed under a running exercise |
| Version | 0.2, draft for review |
| Date | November 21, 2026 |
| Author | John W. Fawcett |
| Organization | Fawcett Innovations, LLC (CAGE 1A5Y5), Burien, Washington |
| Classification | Unclassified. Built only from open-source code and public references. |
| Status | Design complete. Construction not started. |

**Revision history**

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | November 21, 2026 | First issue |
| 0.2 | November 21, 2026 | A channel has one current key, changed by one write, with retry under the current key; the accept list and the three-write exchange are removed. The algorithm is one field of the setting. A sealed message no longer names its key. Owners write their own channel settings. Injection is a tuple the Instructor writes in its own memory and the Role applies; no tuple is written by anyone but its owner. The TCP run sets the performance thresholds. The assessment circuit is corrected and given reference values. The exercise record is captured step by step. A machine holds keys only for its own channels. Wording corrected on harvesting and on the simulated quantum processors. |

## Executive summary

Defense systems must move to post-quantum cryptography between 2027 and 2033, and the algorithms chosen now will not be the last. A system fielded in that period will have to change its cryptography repeatedly over its service life, without stopping.

CroakOS treats the protection on a communications channel as a setting. The protocol, the key and the algorithm are each values that the channel's owner writes, and every end of the channel moves to the new value while traffic continues. Keys are placed at each end in advance, so nothing is exchanged on the link that a recorder could attack later.

This document specifies a demonstration of that capability and the design of everything that must be built for it. The demonstration is a training exercise across three simulated sites. Seven Roles, each with its own memory and its own channel, play a warning-and-response game under an Instructor who injects failures. Two simulated quantum processors at different sites share work across the wide-area link.

The exercise is run four times. Three runs put every channel on one protocol: TCP, Chorus, and a channel built for quantum-circuit traffic. In the fourth run all three protocols operate together on the same mesh, and two channels change protocol, then key, then algorithm, while the exercise continues.

The demonstration passes if the record of the exercise is identical in all four runs, no request fails, every value the Instructor injected stays in place through the changes, and no process restarts. It is repeated with one site on a 4,800-baud radio link. Each run also reports its performance.

The demonstration is unclassified and can be shown to any audience. It answers three current areas of federal guidance: the post-quantum transition, defense of the software environment, and the use of AI-written software. It does not replace a program's software or the work of its prime contractor, and it is designed to be evaluated in a training and simulation environment where nothing operational is touched.

Part of the protocol layer exists and has been measured: 14 changes of protocol and key under load, with no failed request, on a fast link and on a 4,800-baud link. The Roles, the third protocol, the algorithm change and the run harness are specified here and are not yet built. The estimate for construction is six to eight working sessions.

## Contents

| Part | Sections |
| --- | --- |
| Requirements | 1. Purpose · 2. Implications · 3. The exercise · 4. The protocol layer · 5. Methodology · 6. Requirements |
| Plan | 7. What exists and what is built · 8. Limits |
| Detailed design | 9. Memory and data · 10. The exercise · 11. The protocol layer · 12. Software · 13. Test procedure |
| Reference | 14. Terms · 15. References |

## The rules of the system

CroakOS is built from a small number of rules. Every mechanism in this document follows from them. A reader who assumes the mechanisms of a conventional distributed system (negotiation, locks, leases, access lists, rotation windows) will misread the design, because none of those exist here.

| Rule | What it means | What follows in this design |
| --- | --- | --- |
| 1. Every value has one owner, and only the owner writes it. | A tuple is written by the Role that owns it and by no one else. Everyone else reads. | A Role's admin sets its own channel's protocol and encryption. The Instructor publishes injections in its own memory and the Role concerned applies them. There is no access list to administer, because there is no second writer. |
| 2. Coordination is a held read. | A reader asks for a value and is woken when it changes. Nothing polls and nothing is negotiated. | Every step of the exercise, every change of setting and every assignment of work is one write that wakes the readers who hold a read on it. |
| 3. Protocol and encryption are separate. | Encryption is applied to the frame. The protocol is the data inside the frame. | A protocol change alters what is inside the frame and is made while the encryption stays as it is, so the receiver can always open the frame and see what it holds. An encryption change leaves the protocol alone. The two are never changed in one step. |
| 4. There is only the current value. | No history is kept and no earlier value is consulted. A switch is a switch. | A channel has one current key. Both ends select it with the same deterministic rule. The sender marks the switch in the stream with a call that says only that a switch is happening, and everything after that call is new at both ends. Nothing is accepted from an earlier setting. |
| 5. A thread with work in flight retries. | Every request is on its own thread. A thread whose request was in flight across a change asks again. | The thread sends CACHE_MISS, which names the request it already sent, and the other side returns the right answer from its cache without running the request again. |
| 6. Nothing competes. | Work is assigned by its owner from published values. Nothing is claimed, raced for or locked. | Compute's planner assigns each job. The same inputs give the same assignments and the same record on any protocol. |
| 7. The wire belongs to the driver. | The connection is permanently open. What crosses it is whatever the driver puts in the frame. | Three protocols share one mesh. A protocol is replaced on the open connection, without closing it. |

One more point governs scope. Each Role has a capability tuple, and that tuple says what protocol the Role uses and seeds its encryption. It is per Role. The choice can be made more narrowly still, by application name or by instance, and nothing prevents a Role from having several in use at once. None of these choices is site-wide. Two Roles at one site can run different protocols and keys, and one Role runs its own wherever it reaches.

## 1. Purpose

This demonstration shows that a network can make the post-quantum transition, and every cryptographic change after it, as a change of setting made while the system runs. The protocol, the algorithm and the keys that protect a channel are each replaced under a running exercise, and nothing above the channel is affected.

It does this by demonstrating the versatility and adaptability of the CroakOS protocol layer, including key exchange from stored keys. Keys are placed at each end in advance and replaced during the run, and nothing is negotiated on the link for a quantum computer to attack later. The stored keys serve every protocol.

The demonstration is a training exercise run across three simulated sites. Seven Roles play a warning-and-response game. Each Role has its own memory and its own channel, and each channel runs the protocol its owner chose.

Three protocols are used.

- **TCP** is the ordinary transport.
- **Chorus** is CroakOS's own transport. Its control information crosses the link as a difference from the previous message.
- **OpenQASM** is a channel built for quantum-computing traffic. OpenQASM is the open language in which quantum circuits are written.

The exercise is run four times with the same scenario. The first three runs put every channel on one protocol. The final run is the objective: all three protocols carry different Roles across the same mesh at the same time, two channels change protocol while the exercise is running, and those channels have their keys and then their algorithm replaced.

Three protocols at once is the point of the final run. A mesh that works with any mixture of protocols can be updated one channel at a time, in any order, without stopping. That is a rolling update, and it is how a fielded system would move to each new mandate.

### 1.1 What the demonstration is for

The demonstration has five uses, and it is designed to serve all of them from one set of runs.

1. **It answers the current federal references** on quantum, on defending the software environment and on AI-written software, with evidence for each. The table below maps them.
2. **It is unclassified and can be shown to anyone.** It is built from open-source code and public references. Elected officials, program staff, contractors and researchers can all watch the same runs.
3. **It measures performance.** Every run reports time, traffic and the cost of each change, side by side, on fast, long-distance and 4,800-baud links.
4. **It joins quantum machines across a wide-area network.** Two simulated quantum processors at different sites work as one resource over the OpenQASM channel, sharing jobs, calibration and results.
5. **It is a working model of linked training.** Simulators for different skills at separated sites run in one exercise, with an Instructor who injects failures.

| Reference area | What the demonstration shows | What remains |
| --- | --- | --- |
| Quantum: the post-quantum deadlines of 2027 to 2033 | Protocol, key and algorithm replaced on a running system. Stored keys, so no key exchange on the link. A channel built for quantum-computing traffic. | The CNSA 2.0 key-establishment and signature algorithms in the channel. The same runs on real machines. |
| Defending the software environment: zero trust | A channel for each Role. Only members connect. Every end reports what it is running. Updates roll one channel at a time. | Independent evaluation. |
| AI security: AI-written code is unverified input | The software is written with AI and accepted only when it passes tests between machines. The protocol layer under it was built in days. | A record of human review for the security-critical files. |

The result to be shown is a single comparison. The record of the exercise is identical in all four runs, and no request fails, in spite of the protocol and key changes underneath.

This document is both the requirements and the detailed design. Sections 1 to 6 say what is demonstrated, how it is judged and what is required. Sections 7 and 8 give the plan and the limits. Sections 9 to 13 are the design at the level it will be built.

## 2. Implications

If the demonstration passes, six things follow for a network of linked training sites.

1. **A post-quantum mandate becomes a configuration change.** The same mechanism replaces a key, an algorithm or a whole protocol. A system fielded in the early 2030s will meet the first deadlines and then every change of algorithm that follows over its life. Each one is a write to a setting, not a migration project.
2. **No key exchange is left on the link to harvest.** With stored keys there is no key exchange on the link. A recording made today holds no exchange to break later.
3. **Updates roll.** A site that has not yet moved to a new protocol, key or algorithm works in the same exercise as sites that have. A change is made one channel at a time, with the exercise still running, and no cutover date.
4. **The protocol belongs to the channel's owner.** The owner chooses the protocol and the keys. The applications are not rebuilt, restarted or told.
5. **Different kinds of traffic can each have a channel that suits them.** The OpenQASM channel shows a channel built for quantum-computing traffic sharing the mesh with general-purpose channels.
6. **The training audience sees nothing.** Crews at their consoles, and the Instructor injecting failures, work the same way whatever is underneath.

None of this replaces a program's software or the work of its prime contractor. CroakOS is added at a site's existing network connection and carries the applications that are already there. Design authority stays with the prime contractor, and integration with other command-and-control systems stays where it is. A training and simulation environment is the natural place to evaluate it, because nothing operational depends on the result.

The demonstration does not show security strength. It shows that protection can be chosen and changed freely. Section 8 lists what remains to be done before a security claim can be evaluated.

## 3. The exercise

### 3.1 Roles

The whole system is Roles. A Role is a service. A Role may need other Roles in order to work, and it uses them by reading what they publish. Each Role can be in a different memory, with different encryption.

- **The Role is the service.** In the exercise it is the simulator for one function. It publishes its own values and reads the values of the Roles it needs.
- **It lives in a memory.** Each Role's values are held in a memory, which can be a different one from every other Role's.
- **It is reached its own way.** The capability tuple for a Role says what protocol is used to reach it and seeds the encryption. A Role that needs three other Roles may be using three protocols and three encryptions at once, one for each, and none of them is its own concern.
- **It has an admin tool and a view.** The admin tool is an external application that sets up and administers the Role. The view is part of it, and shows the exercise from that Role's position and nothing else.

Roles are coordinated through Chorus, the CroakOS distribution model, in which each Role holds a read on the memory it depends on and is woken when that memory changes. No Role polls.

| Role | What it stands for | Hardware it simulates | Memory it owns | Memory it consumes |
| --- | --- | --- | --- | --- |
| Warning | Sensor operators | Sensor stations: status, coverage, quality | Detections | Ground truth from Exercise |
| Assessment | Analysts | An analysis workstation | Circuits sent to Compute, and the confidence in each detection | Detections, and results from Compute |
| Compute | Two quantum processors at different sites, working as one | Two processors, each with its own calibration values, sharing one job queue | Job results | Circuits from Assessment |
| Command | The decision cell | A command console | Alert level, and orders | Confidence from Assessment, and unit status |
| Units | Response crews | Response units: readiness, communications, stores | Unit status, and actions taken | Orders from Command |
| Exercise | The simulated world | None | The exercise clock, ground truth, the score | Actions from Units |
| Instructor | The exercise director | None | The scenario, and the list of injections | Every Role's memory |

The Roles are generic on purpose. They follow the division of work found at a facility that warns, assesses, decides and responds, without modelling any particular one.

### 3.2 The game

The game is played on a 16 by 16 grid. A scenario script launches objects that cross the grid toward protected cells. The game proceeds in numbered steps.

1. Exercise moves the objects and publishes ground truth for the step.
2. Warning looks at ground truth through its sensors and publishes detections. A degraded sensor produces poorer detections or none.
3. Assessment writes a circuit for each detection, with the circuit's angles set from the detection's quality and from agreement between sensors, and sends it to Compute.
4. Compute runs the circuit and returns the measurement counts. Compute assigns each job to one of its two processors by a fixed rule, from the availability and calibration each processor has published. Assessment turns the counts into a confidence figure.
5. Command sets the alert level from the confidence figures and issues orders.
6. Units carry out the orders that their readiness allows, and publish what they did.
7. Exercise applies the actions, scores the step and advances the clock when every Role has published for the step.

The game is deterministic. The same scenario, the same console inputs and the same injections give the same record, byte for byte. That property is what allows runs on different protocols to be compared.

The circuit in step 3 is small. Its purpose is to put real OpenQASM programs with changing parameters on a channel. Compute runs them on a simulator of a few qubits.

### 3.3 The Instructor

The Instructor works from the Instructor console in four phases.

1. **Setup.** The Instructor sets the terms of the run and loads a scenario. Every Role is initialized and reports that it is at initial conditions.
2. **Activation.** On the Instructor's signal the exercise clock starts.
3. **Run.** The Instructor publishes injections in its own memory. Each names a Role and a value. The Role concerned reads the injection and applies it to its own memory. Examples are a failed sensor, a false detection, a drifted calibration value on Compute, a unit that has lost communications, and data inserted when Command changes the alert level.
4. **Review.** The Instructor freezes the exercise and reads the record.

An injected value stays. The Role keeps it in its own memory for as long as the injection stands, and returns to its own value when the Instructor releases it.

The Instructor does not write into another Role's memory. Every tuple in the system is written only by its owner, and injection keeps that rule: the Instructor writes its own tuples, and the Roles that are interested read them. The alternative, an Instructor that writes directly into other Roles' tuples, would be the one exception to the rule and is not used.

## 4. The protocol layer

### 4.1 Where a protocol sits

A message passes through three stages on its way to the link.

1. **The semantic layer makes it small.** Both ends hold a template of each kind of message. Only what differs from the template crosses.
2. **The driver frames it and seals it.** A driver is the code for one protocol. It owns the connection and decides what the bytes on it are. Sealing with the channel's key happens here, after the message is small and immediately before it goes on the link.
3. **The link carries it.**

The receiving end reverses the stages. A Role's service sits above stage 1 and sees none of them.

A driver implements six calls: connect, send, receive, pack, unpack and close. Anything that implements them can be a channel's protocol.

### 4.2 The channel's setting

Each Role has a capability tuple. It says what protocol the Role's channel uses, and it seeds the channel's encryption. It is written by the Role's admin and by no one else.

Every end holds a read on the setting and is woken when it changes. Each end then publishes what it is running, so the owner can see when every end has moved.

- **A change of protocol** changes the data inside the frame, on the connection that is already open. The encryption is not changed in the same step.
- **A change of encryption** selects a new key at both ends at the same time. It is described in 4.4.
- **A thread with a request in flight** across either change retries by sending CACHE_MISS.

### 4.3 The three protocols

|  | TCP | Chorus | OpenQASM |
| --- | --- | --- | --- |
| Status | Exists | Exists | To be built |
| Intended traffic | Any | Any | Quantum circuits and their results |
| Control information on the link | A fixed header on each message | A difference from the previous message | A difference from the previous message |
| What crosses for a repeated circuit | The changed characters of the program | The changed characters of the program | A circuit number and the changed parameter values |

Key and protocol are independent. The table has no row for encryption because encryption is not a property of a protocol. Any of the three runs under any key, or under none, and either one is switched without touching the other.

The OpenQASM protocol has two parts that are selected together by one setting.

- **The circuit codex.** A circuit's structure is held as a template at both ends. When a circuit is sent again with different parameters, only the template's number and the changed values cross, as numbers. Templates can be loaded at both ends before the run, exactly as keys are.
- **The driver.** It has its own connection, and its first frame carries its own word, which is how a receiver recognizes it. It does no sealing of its own.

The codex belongs in the semantic layer and not in the driver. This was measured on 21 November. A 171-byte circuit inside a 350-byte request was written repeatedly between two simulated machines over Chorus. The driver was handed a 170-byte message in which the circuit had already been reduced to its changed characters. The driver never sees a whole circuit, so it cannot be the part that understands one.

That measurement is also the baseline. The codex is judged on how far below 170 bytes it takes the same message. An estimate of 97 to 99 percent reduction for this kind of traffic was made in March 2026. It was never measured, and this demonstration produces the first measured figure.

### 4.4 Stored-key exchange

Keys are placed on each machine before the run, each under a name. A key never crosses the link. Only its name appears in the channel's setting.

There is one key store on each machine, and it serves every protocol. A key belongs to a channel and stays with it when the channel changes protocol.

A channel has one key at a time. It is one of a set of keys that were provided offline and placed at every end before use: twenty, fifty, or however many the owner chooses. The Role's capability tuple carries a seed, and with it the same deterministic algorithm at every end picks which key of the set is current and which is next. Every end therefore arrives at the same key without anything being exchanged.

The switch is made in the stream itself. The sender makes a call that says *switch encryption*, or *switch protocol*. The call says only that. It does not say what is being switched from or to. Once it is sent, everything the sender sends after it goes out the new way. Once it is received, every read after it is done the new way.

The connection is an ordered stream, so the call marks one exact place in it. Everything before that place is opened the old way and everything after it the new way. The two ends never disagree about any message, however slow the link, and nothing fails to open because of the switch. There is no handshake, and no end keeps the earlier key once it has passed the call.

What causes a switch is the owner's choice. In the demonstration it is the Role's admin giving a command. It can equally be a schedule, a count of messages, or an event. Whatever the cause, it only has to occur at the sender. The receiver follows the call in the stream, so the two ends need no shared clock and no agreement in advance about when.

A thread with a request in flight when the change happens retries its operation. It sends CACHE_MISS, a short message that names the request it already sent, and the other side returns the right answer from its cache. The request is not run a second time. If the other side never received the original, it says so, and the thread sends the request in full.

The semantic layer has this message today for a related case, under the name REQ_SAME_MISS: a proxy that lacks an answer the daemon holds asks for it, and the daemon answers from its cache and runs nothing. Today it names the answer. For a request in flight the thread has no answer to name, so the message is extended to name the request.

Protocol and encryption are changed separately. Changing the protocol changes the data inside the frame. It works because the encryption has not changed: the receiver opens the frame exactly as before and finds the new protocol's data inside. Changing the encryption leaves the protocol as it is. The two are never changed in the same step.

A switch of encryption moves both ends to the next key the algorithm picks from the set. The receiver needs nothing but the call in the stream, because it already holds the set and the seed. A new set of keys is provided offline, in the same way as the first.

The protection algorithm is AES-256-GCM from the system's own cryptographic library. The algorithm is itself a named entry in the setting and is replaced the same way, by one write. Run 4 replaces it with a second algorithm from the same library, ChaCha20-Poly1305, which stands in for a future mandated algorithm.

### 4.5 What has already been measured

Changing a channel's driver and key under load has been tested with the real network software on three simulated machines.

| Link | Changes made | Requests | Failed | Wrong answers |
| --- | --- | --- | --- | --- |
| Fast link | 14 | 44,536 | 0 | 0 |
| 4,800-baud radio link | 14 | 1,839 | 0 | 0 |

On the 4,800-baud link the median time for a request and its reply was 880 ms on TCP, 864 ms on Chorus, and 934 ms on Chorus under a key.

Those tests used a synthetic load of reads and writes. This demonstration replaces that load with Roles doing recognizable work, adds the third protocol, and runs all three at once.

## 5. Methodology

### 5.1 The network

The exercise runs under the CroakOS simulator. Each site is a simulated machine with its own network stack, running the real network software. The sites discover each other, elect the hosts of each memory, and route between themselves as real nodes do. Nothing in the network software is replaced by a stand-in.

| Site | Roles hosted | Link |
| --- | --- | --- |
| A | Warning, Exercise | Fast link to B |
| B | Assessment, Compute (first processor), Instructor | Fast link to A, long-distance link to C |
| C | Command, Units, Compute (second processor) | Long-distance link to B |

Every run is made twice: once with the links above, and once with site C on a 4,800-baud radio link.

### 5.2 The four runs

All four runs use one scenario of 60 steps, one set of console inputs and one set of Instructor injections, applied at the same steps.

| Run | Protocols | Changes during the run |
| --- | --- | --- |
| 1 | Every channel on TCP | None |
| 2 | Every channel on Chorus | None |
| 3 | Every channel on OpenQASM | None |
| 4 | Mixed, as below | Two changes of protocol, two key replacements and two changes of algorithm |

Run 4 is the objective. Run 1, on TCP, is the reference: its exercise record is what the other runs must match, and its measured performance sets the thresholds the other runs must meet. In runs 1 to 3 every channel is sealed under a stored key, so the three protocols are compared like for like.

| Role's channel | At the start of run 4 | Changes during run 4 |
| --- | --- | --- |
| Warning | TCP | None |
| Exercise | Chorus | None |
| Command | Chorus under a key | None |
| Compute | OpenQASM | None |
| Assessment | Chorus under a key | Step 25: changes to OpenQASM. Step 35: its key is replaced. Step 50: its algorithm is replaced. |
| Units | TCP | Step 15: changes to Chorus. Step 30: a key is introduced. Step 45: the key is replaced. Step 55: its algorithm is replaced. |
| Instructor | Reaches each Role over that Role's channel | Follows each Role's change |

The Instructor's injections fall at steps 10, 20 and 40, so that injected values are in place before, between and after the changes.

### 5.3 What is recorded

- **The exercise record.** The scenario's inputs, each Role's decisions and results at every step, and the final score. This is the record compared between runs.
- **Requests.** For each Role's channel: the number made, the number that failed and the number that returned a wrong value.
- **Step time.** How long each step took from the clock advancing to the last Role publishing.
- **Bytes on the link.** For each Role's channel, taken from the simulated links themselves.
- **Circuit size.** Bytes on the link per circuit sent, on each protocol.
- **Changes.** For each change: when the owner wrote it, when the last end reported it, and the longest silence on that channel before and after.
- **Injections.** Each injected value, read back from the Role's memory at every later step until release.
- **Processes.** Whether any process restarted.

Everything after the first item is the engineering record. It describes how the run was carried: timings, traffic, processor assignments and changes. It is reported and compared as measurement, and it is kept apart from the exercise record so that a difference in timing is never mistaken for a difference in the exercise.

### 5.4 Pass criteria

1. The exercise record of runs 2, 3 and 4 is identical to that of run 1.
2. No request is lost and none returns a wrong value, in any run. A request retried across a change counts as delivered, and retries are counted in the engineering record.
3. Every injected value is present at every step from its injection to its release, including the steps where the channel under it changed.
4. No process restarts.
5. In run 4, every end reports each change, and the three protocols are seen carrying traffic on the simulated links at the same time.
6. Criteria 1 to 5 hold on the 4,800-baud link as well.

**Criterion 7: performance.** The TCP run sets the thresholds. On each link, run 1's measured request time (median and 95th percentile), step time and bytes on the link are the limits. Runs 2, 3 and 4 pass when each of those figures is no greater than run 1's on the same link. No threshold is chosen in advance, and none is taken from anywhere but the TCP run.

A run counts only when it is made between machines under the simulator. A test inside one process does not count toward any criterion.

### 5.5 Performance report

Each run produces one table, and the four tables are set side by side. Every figure is given for the fast link, the long-distance link and the 4,800-baud link.

- Time for a request and its reply on each Role's channel: median and 95th percentile.
- Time for one step of the exercise.
- Bytes on the link for each Role's channel, for the whole run.
- Bytes on the link for one circuit, and for one result.
- For each change of protocol, key or algorithm: the time from the owner's write to the last end reporting it.
- Jobs completed by each quantum processor, and the time a job waits, with both processors up and with one taken out of calibration by the Instructor.

The figures are taken from the simulated links and from the Roles' own records. None is estimated.

Run 1's figures in this report are the thresholds for criterion 7.

## 6. Requirements

### 6.1 Roles and memory

- **R1.** Each Role has its own memory, hosted at one site and elected by the network as its other memory hosts are.
- **R2.** A Role's service writes only to its own memory. It learns of other Roles' values by a held read on their memory.
- **R3.** No Role service, console or test polls. Every wait is a held read.
- **R4.** Each Role has an admin tool, external to the service, that sets up the Role and shows the Role's view.
- **R5.** A Role's view shows only what that Role's memory and its held reads contain.
- **R6.** Role names and screens are generic. They name no real facility, system or program.

### 6.2 The exercise

- **R7.** The game is deterministic: one scenario, one set of console inputs and one set of injections give one record.
- **R8.** The exercise clock advances only when every Role has published for the current step.
- **R9.** A scenario, its console inputs and its injections are data in a file. A new scenario needs no code.
- **R10.** The game, its names and its screens are original work.

### 6.3 The Instructor

- **R11.** The Instructor console performs setup to initial conditions, activation, freeze, resume and review.
- **R12.** The Instructor can inject any value into any Role by publishing an injection in its own memory. The Role named applies it to its own memory.
- **R13.** An injected value stays until the Instructor releases it. The Role writes the injected value for as long as the injection is active.
- **R14.** An injected value survives a change of protocol, key or algorithm on the Role's channel and on the Instructor's channel.
- **R15.** Each Role's view shows which of its values are under injection only when the Instructor chooses to reveal it.

### 6.4 The protocol layer

- **R16.** Protocol and encryption are chosen per Role. They can be chosen more narrowly, by application name or by instance, and a Role can have more than one in force at once. None is site-wide.
- **R17.** TCP, Chorus and OpenQASM channels run on one mesh, and on one machine, at the same time.
- **R18.** A channel's protocol is changed by one write to its setting, on the open connection, while the encryption is unchanged. A thread with a request in flight retries with CACHE_MISS. No request is lost.
- **R19.** A channel has only its current key. Every end selects it with the same deterministic rule, from the seed in the Role's capability tuple, and all ends switch at the same time. No request is lost.
- **R20.** Keys are placed on each machine before the run and never cross a link.
- **R21.** The OpenQASM driver implements the same six calls as every driver and registers its own setting word.
- **R22.** Data that begins with no registered protocol word is refused.
- **R23.** The OpenQASM codex returns every circuit byte for byte as it was written.
- **R24.** Circuit templates can be loaded at both ends before the run.
- **R25.** No Role service or scenario file names a protocol or a key. A channel's setting is written by its owner, through that Role's admin tool.

* **R31.** Stored keys seal messages on every protocol, TCP included. One key store on each machine serves all of them.
* **R32.** A channel's protection algorithm is replaced by one write to the setting, with the same retry rule as a key. No request is lost.
* **R33.** The mesh works with any mixture of protocols, keys and algorithms across its channels, so that channels can be changed one at a time in any order.

- **R34.** Compute has two processors at different sites. They share one job queue, their calibration values and their results through Compute's memory, over the OpenQASM channel across the wide-area link.
- **R35.** When one processor is out of calibration or unreachable, the other takes its jobs. No job's result is lost. A job that is reassigned may finish on both processors; the two results are identical, and the planner's current assignment decides which one is accepted.
- **R36.** Every run writes the performance report of section 5.5 without further steps.
- **R37.** The demonstration is unclassified. Its code, scenario, data and documents come only from open-source code and public references.

* **R38.** Compute's planner assigns jobs to processors by a fixed rule from published values. Processors do not compete for work. The assignments are the same in all four runs.

- **R39.** A receiver determines the protocol by examining the frame it receives: it opens the frame and reads the protocol's word at the front of the data. It does not rely on the port or the sender.
- **R40.** The protection algorithm is part of the channel's setting. Each end seals and opens with the setting's current key and algorithm, and keeps no earlier ones.

* **R41.** A thread with a request in flight across a change retries by sending CACHE_MISS, and the other side returns the right answer from its cache without running the request again. Every retry is logged with its request number.
* **R42.** A machine holds keys only for the channels it is a member of.

- **R43.** A channel's setting is a tuple owned by the Role whose memory the channel serves. Its owner is its only writer, as with every tuple. The Instructor does not write another Role's channel setting.
- **R44.** Performance thresholds are the TCP run's measured figures on the same link. Each other run's request time, step time and bytes on the link are no greater than them.

* **R45.** Protocol and encryption are changed separately. A protocol change is made only while the encryption is unchanged, and an encryption change leaves the protocol alone.

- **R46.** A switch of protocol or of encryption is marked in the stream by a call from the sender that says only which of the two is switching. Everything sent after the call is sent the new way, and every read after the call is received is done the new way.

* **R47.** A switch can be caused by an admin's command, by a schedule or by an event. The cause acts at the sender only. The receiver needs no clock and no notice beyond the call in the stream.

- **R48.** Key and protocol are independent. Any protocol runs under any key or none. Each is switched by its own call, without the other. Encryption is applied to the frame by one layer outside every driver.
- **R49.** The current key is always one of a set provided offline and placed at every end before use. A switch of encryption moves to the next key the deterministic algorithm picks from that set.

### 6.5 Construction rules

- **R26.** No fallbacks. A step that fails is logged and tried again at the next event. A channel whose driver fails ends with the reason.
- **R27.** Discovery and routing code are not changed. A diff confirms it.
- **R28.** No new software dependency is added without approval. The circuit simulator in Compute is written in the repository.
- **R29.** Every build identifies itself in its log, so a run's log says what ran.
- **R30.** The four runs are started by one command each, and each writes its record and measurements to one file.

## 7. What exists and what is built

Paths are relative to `opt/frognet_semantic` in the repository, on the branch `homa-under-croakos-20261116`. New file names are proposals.

### 7.1 What exists

| Part | Where | State |
| --- | --- | --- |
| The six-call driver contract | `core/transport_driver.py` | Built |
| Chorus driver | `core/chorus.py` | Built, tested between simulated machines |
| Channel setting, with a setting per memory | `core/channel_setting.py` | Built, tested between simulated machines |
| Sealing with AES-256-GCM, and registered protections | `core/channel_protection.py` | Built |
| Registered driver words | `core/homa_transport.py` | Built |
| Templates for JSON, XML, HTML and text | `core/format_registry.py` and the handlers beside it | Built |
| Simulated machines running the real network software | `simulation/wire_between_machines_tier.py` | Built |
| Driver and key changes under load | `simulation/channel_swap_tier.py` | Built, 14 changes pass |
| Slow-link profiles, including 4,800 baud | `simulation/slowlink.py` | Built |

### 7.2 What the existing code does not yet allow

Three facts about the code as it stands shape the work.

1. **One process holds one message carrier.** TCP is a separate kind of session, which is why TCP and Chorus already run side by side. Chorus and OpenQASM are the same kind, so the carrier must hold connections of either driver and choose by the destination's channel setting. All three arrive on one port, and the receiving end tells them apart by examining the first frame.
2. **The simulator elects two memory hosts.** It elects the system memory and the control memory. The Roles' memories are application memories, which the network already elects. The simulator tier has to run that election for them.
3. **A circuit is one string to the JSON template.** When a parameter changes, the changed characters cross. The codex replaces that with a circuit number and values.

### 7.3 What is built, in order

| Step | Work | New or changed | Proves |
| --- | --- | --- | --- |
| 1 | Role memories elected in the simulator, each with its own channel setting | The tier's role election; a role name per Role | R1, R16 |
| 2 | The OpenQASM codex as a format handler | `core/openqasm_handler.py`, registered in `core/format_registry.py` | R23, R24 |
| 3 | The OpenQASM driver, and a carrier that holds both drivers | `core/openqasm.py`; `core/chorus.py`; `core/homa_transport.py` | R17, R21, R22 |
| 4 | The seven Role services, the scenario file and the circuit simulator | A new directory under `simulation/` | R2, R3, R7 to R10 |
| 5 | The Instructor console and injections | Same directory | R11 to R14 |
| 6 | The run harness: four runs, the record comparison, the measurements | `simulation/roles_exercise_tier.py` | Section 5.4 |
| 7 | Admin tools and views, one per Role | Pages, following the existing emergency-operations application | R4, R5, R15 |
| 8 | A short brief of the results that stands on its own | A document |  |

Step 3 also moves sealing out of the Chorus driver into one layer that serves every protocol, and registers the second algorithm (R31, R32).

Steps 1 to 6 produce the demonstration as measurements. Step 7 makes it something to watch. The estimate for all eight is six to eight working sessions.

### 7.4 Summary of the new parts

**Role memory.** Each Role's memory is hosted by the node that the network elects for that Role's name. A service finds a Role's memory by name, and never by address or site. When the host moves, held reads follow it, as they do for the system memory today.

**The step.** Exercise owns a clock value. Each Role holds a read on it. When the clock advances, a Role reads what it consumes, computes, writes what it owns, and writes a done marker for the step. Exercise holds a read on the done markers and advances the clock when all seven are present. The Instructor is one of the seven, which places each injection at an exact step.

**Injection.** An injection is one tuple the Instructor writes in its own memory, naming a Role and a value. Each Role holds a read on those tuples and applies the ones that name it to its own memory. Release is a second write to the same tuple. No one writes into another Role's memory. Section 10.7 gives the detail.

**The OpenQASM codex.** The codex recognizes a value as an OpenQASM program by its opening statement. It splits the program into its fixed text and its numeric literals. The fixed text is the template, identified by a hash and numbered once both ends hold it. A repeat sends the number and only the literals that changed, as binary values. A program the codex cannot return byte for byte is handled as text, as it is today. A file of circuits loaded at both ends before the run seeds the templates.

**The OpenQASM driver.** The driver is a class with the six calls. Its data inside the frame begins with the driver's own word. Control information crosses as a difference, as in Chorus. It has no part in encryption.

**The circuit simulator.** Compute runs each circuit on a state-vector simulator of up to eight qubits, written for this purpose. It supports the gates the scenario's circuits use. Its random numbers are seeded from the step number and the circuit, so results repeat.

**Two processors, one Role.** Each processor is a service at its own site. It publishes its own availability and calibration values in Compute's memory and never chooses its own work. Compute's planner holds a read on those values and on the incoming circuits. At each step it assigns every circuit to a processor and publishes the assignment. Each processor holds a read on the assignments and runs the jobs that carry its name.

The assignment rule is fixed. A processor is qualified when it is available and its calibration is within limits, as published at the start of the step. Jobs are taken in order of circuit number and given in turn to the qualified processors, lowest processor number first. The rule uses only values published for the step, so the same scenario gives the same assignments on any protocol and any link. When a processor is taken out of calibration, by drift or by the Instructor, the planner stops assigning to it at the next step and its unfinished jobs are assigned again.

**The record.** Each Role's published values are captured as they appear, by a process that holds a read on every Role memory, and are written in step order to one file. The comparison between runs is a byte comparison of those files.

### 7.5 Design risks

1. **Selecting the codex by channel.** The codex should act on channels set to OpenQASM and leave other channels as they are, so that runs 1 and 2 remain true baselines. The semantic layer chooses a handler by looking at the content today, not by channel. Step 2 begins by confirming in the code that the channel is known at that point.
2. **Seven elections on three machines.** The election code is not changed (R27). If it cannot elect seven named memories as it stands, the work stops for a decision before anything in it is touched.
3. **Determinism on a slow link.** The step rule removes timing from the record. Any value that depends on arrival order within a step would break criterion 1, and the 4,800-baud run is where it would show.

## 8. Limits

- **Simulated machines.** Every result comes from the real network software on simulated machines. None of it has yet been repeated on real nodes or a real radio link.
- **Algorithms.** Sealing uses AES-256 with stored keys. The CNSA 2.0 key-establishment and signature algorithms are not in the channel yet. The cryptographic library on the test system, OpenSSL 3.0.13, does not provide them.
- **Security evaluation.** No independent security evaluation has been done. This demonstration shows that protection can be chosen and changed. It does not measure how strong the protection is.
- **Quantum computing.** Compute is a small simulator on an ordinary machine. The channel carries the ordinary information around a quantum computer: circuits, settings and results. It does not carry quantum states.
- **The game.** The game is a workload that looks like linked training, chosen so the result can be seen. It models no real procedure, system or facility.
- **Scale.** Three sites and seven Roles. Larger meshes are a separate measurement.

## 9. Detailed design: memory and data

Sections 9 to 13 are the design at the level it will be built. Where a name or call exists in the code today it is marked *existing*. Everything else is new and is named here for the first time.

### 9.1 What a memory is

A memory is a store of tuples held by one node and found by a host name. A tuple has three parts and a value.

| Part | Meaning | Example |
| --- | --- | --- |
| Service | The Role that owns the tuple | `warning` |
| Variable | What the value is | `sensor` |
| Scope | Which one | `2` |
| Value | A JSON object | `{"status": "ok", "x": 3, "y": 12, "range": 5, "quality": 900}` |

The calls are *existing*, in `core/frognet_ram.py` and `core/frognet_held.py`.

```
RAM.upsert(service, var, scope, value, RAM.DURABLE, dbhost=<memory's host name>)
RAM.read(service, var, dbhost=<memory's host name>)
holder().wait_until(service, <memory's host name>, done, name_like=..., wait_s=...)
```

`wait_until` is the held read. It registers a hold at the memory, returns the current rows when `done(rows)` is true, and otherwise sleeps until the memory fires the hold because a matching tuple was written. Every wait in this design is this call. A hold whose time bound passes is registered again; it carries no work of its own.

Published values are written `DURABLE`. A service's presence is written `EPHEMERAL` and disappears when the service stops.

### 9.2 The Role memories

A Role's memory is an application memory. The system has these today (`core/app_hosts.py`, *existing*). An application's general memory is named `<app>Host.frognet`, and a Team's memory is named `<team>Host.<app>`. The network elects the host of each from the capability tuples its machines publish, and writes the line into every node's hosts file.

The exercise is one application, named `exercise` here, and each Role is a Team of it. No new kind of memory and no new election is introduced.

| Role | Memory's name | Site in the demonstration |
| --- | --- | --- |
| Exercise | `exerciseHost.frognet` (the application's general memory) | A |
| Warning | `warningHost.exercise` | A |
| Assessment | `assessmentHost.exercise` | B |
| Compute | `computeHost.exercise` | B |
| Instructor | `instructorHost.exercise` | B |
| Command | `commandHost.exercise` | C |
| Units | `unitsHost.exercise` | C |

The simulator tier used so far places the lines for the system memory and the control memory itself and does not run the application election. Running the existing application election inside the tier is the first piece of work in build step 1.

A service names a memory only by its host name. When a role moves, the hosts file is rewritten, `wait_until` returns, and the next hold goes to the new host. That behaviour is *existing*.

### 9.3 The tuples

Every tuple below is written only by its owner, except where the Instructor is named.

**Exercise memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(exercise, clock, run)` | `{step, state}` where state is `setup`, `running`, `frozen` or `ended` | Every Role |
| `(exercise, truth, <step>)` | `{step, objects: [{id, x, y, dx, dy}]}` | Warning |
| `(exercise, score, run)` | `{step, score, intercepted, reached, false_responses}` | Instructor, Command |

**Warning memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(warning, sensor, <n>)` | `{x, y, range, status, quality}` | Warning's view, Instructor |
| `(warning, phantom, <n>)` | `{x, y, quality}`, written by Warning when an injection calls for one | Warning's service |
| `(warning, detection, <step>)` | `{step, tracks: [{track, x, y, quality, sensors}]}` | Assessment |

**Assessment memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(assessment, circuit, <track>)` | `{step, track, qasm}` where `qasm` is the OpenQASM program | Compute |
| `(assessment, confidence, <step>)` | `{step, tracks: [{track, x, y, confidence}]}` | Command |

**Compute memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(compute, processor, <n>)` | `{available, error_rate, limit}` written by processor n | Compute's planner, Instructor |
| `(compute, assignment, <step>)` | `{step, jobs: [{track, processor}]}` written by the planner | The processors |
| `(compute, result, <track>)` | `{step, track, shots, ones}` written by the assigned processor | Assessment |

**Command memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(command, alert, run)` | `{step, level}` where level is 5 (normal) to 1 (highest) | Units, Instructor |
| `(command, order, <step>)` | `{step, orders: [{unit, action, track, x, y}]}` | Units |

**Units memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(units, unit, <n>)` | `{x, y, reach, readiness, comms, stores}` | Command, Instructor |
| `(units, action, <step>)` | `{step, actions: [{unit, track, x, y, done}]}` | Exercise |

**Instructor memory**

| Tuple | Value | Read by |
| --- | --- | --- |
| `(instructor, scenario, run)` | The loaded scenario file, whole | Every Role |
| `(instructor, control, run)` | `{command}` where command is `setup`, `start`, `freeze`, `resume` or `end` | Exercise |
| `(instructor, injection, <n>)` | `{role, var, scope, value, from_step, state, by}` where state is `active` or `released` | The Role it names; review |
| `(instructor, applied, run)` | `{step}`: the injections for this step have been written | Exercise |

**In every Role's memory**

| Tuple | Value | Written by |
| --- | --- | --- |
| `(<role>, ready, run)` | `{scenario, step: 0}`: the Role is at initial conditions | The Role |
| `(<role>, present, <address>)` | `{build}`, ephemeral | The Role's service |

No tuple in any Role's memory is written by anyone but that Role.

### 9.4 The scenario file

A scenario is one JSON file. It is the only input to a run besides the run's protocol plan.

```
{
  "name": "first",
  "steps": 60,
  "grid": 16,
  "protected": [[2, 2], [13, 3]],
  "sensors": [{"n": 1, "x": 3, "y": 12, "range": 5, "quality": 900}, ...],
  "units": [{"n": 1, "x": 2, "y": 4, "reach": 4, "stores": 3}, ...],
  "processors": [{"n": 1, "error_rate": 10, "limit": 50}, {"n": 2, "error_rate": 12, "limit": 50}],
  "launches": [{"step": 3, "id": 1, "x": 15, "y": 15, "dx": -1, "dy": -1}, ...],
  "console": [{"step": 22, "role": "command", "input": {"level": 2}}],
  "injections": [
    {"step": 10, "memory": "warningram.frognet", "service": "warning", "var": "sensor", "scope": "2",
     "value": {"status": "failed"}, "release": 30},
    {"step": 20, "memory": "warningram.frognet", "service": "warning", "var": "phantom", "scope": "1",
     "value": {"x": 9, "y": 9, "quality": 700}, "release": 26},
    {"step": 40, "memory": "computeram.frognet", "service": "compute", "var": "processor", "scope": "1",
     "value": {"error_rate": 80}, "release": 52},
    {"when": {"alert_at_most": 2}, "memory": "unitsram.frognet", "service": "units", "var": "unit", "scope": "3",
     "value": {"comms": "lost"}, "release_after": 6}
  ]
}
```

All quantities are integers. Quality and confidence are in thousandths, and error rate in ten-thousandths. No Role does floating-point arithmetic on a value that enters the exercise record, so every machine computes the same result.

An injection with `when` is applied at the step after its condition first holds. The fourth one above is data inserted when a mode changes.

## 10. Detailed design: the exercise

### 10.1 One step

One step is a chain of held reads. Each Role wakes on the value it consumes for the step, and publishes the value the next Role is waiting on.

| Order | Who | Wakes on | Does | Publishes |
| --- | --- | --- | --- | --- |
| 1 | Instructor | `(exercise, clock, run)` showing step s | Writes and releases the injections due at s, in its own memory | `(instructor, applied, run) = {step: s}` |
| 2 | Exercise | `applied` showing s | Moves each object by its dx, dy. Adds the launches due at s. | `(exercise, truth, s)` |
| 3 | Warning | `truth` for s | Runs the detection rule of 10.2 | `(warning, detection, s)` |
| 4 | Assessment | `detection` for s | Writes one circuit for each track | `(assessment, circuit, <track>)` with step s |
| 5 | Compute's planner | Circuits with step s for every track in `detection` | Runs the assignment rule of 10.4 | `(compute, assignment, s)` |
| 6 | Each processor | `assignment` for s | Runs the circuits that carry its number | `(compute, result, <track>)` with step s |
| 7 | Assessment | Results with step s for every track | Converts counts to confidence | `(assessment, confidence, s)` |
| 8 | Command | `confidence` for s | Runs the alert and order rules of 10.5 | `(command, alert, run)`, `(command, order, s)` |
| 9 | Units | `order` for s | Runs the action rule of 10.6 | `(units, unit, <n>)`, `(units, action, s)` |
| 10 | Exercise | `action` for s | Scores the step | `(exercise, score, run)`, then `(exercise, clock, run) = {step: s+1}` |

A step with no tracks still passes through every Role, each publishing an empty list.

The Instructor's `freeze` is honoured by Exercise at order 10: the clock value is written with state `frozen` and the same step, and nothing wakes until `resume`.

### 10.2 Warning's rule

For each object in `truth`, and each phantom present, take every sensor whose status is `ok` and whose distance to it is at most the sensor's range. Distance is the larger of the differences in x and in y. A sensor's reading has quality `sensor.quality * (range + 1 - distance) / (range + 1)`, integer division. A phantom is read by every working sensor at its stated quality. The track's quality is the highest reading, and `sensors` is the number of sensors that read it. Objects read by no sensor are not reported. Tracks are listed in order of object id, phantoms after objects.

### 10.3 Assessment's circuit

Each track gets this program, with two angles computed from the detection.

```
OPENQASM 3.0;
include "stdgates.inc";
qubit[3] q;
bit[1] c;
ry(<a>) q[0];
ry(<b>) q[1];
ccx q[0], q[1], q[2];
c[0] = measure q[2];
```

`<a>` is `quality * 3141593 / 1000000` thousandths of a radian, written with three decimal places. `<b>` is `(4 + min(sensors, 4)) * 392699 / 1000`, written the same way. The circuit's structure never changes, and only the two angles do. That is the case the codex is built for.

The measured qubit is 1 only when both of the others are, so the probability of measuring 1 is the product of sin squared of half of each angle. Both inputs move the result: quality through the first angle, and agreement between sensors through the second.

| Quality | Sensors | Probability of 1 |
| --- | --- | --- |
| 900 | 1 | 0.674 |
| 900 | 2 | 0.833 |
| 900 | 4 | 0.975 |
| 500 | 2 | 0.426 |
| 0 | any | 0 |

These values are reference vectors. The simulator is tested against them before it is used in a run.

Confidence is `ones * 1000 / shots`, where `ones` is the number of shots that measured 1. Shots are 1024.

### 10.4 Compute

**Processors.** Each processor publishes `(compute, processor, <n>)` at the start of each step with its availability and its error rate. The error rate starts at the scenario's value and does not change unless the Instructor injects one. A processor is qualified when `available` is true and `error_rate` is at most `limit`.

**Assignment.** The planner lists the step's tracks in order of track number and the qualified processors in order of processor number. Track i in the list goes to processor `i mod (number qualified)` in the list. If no processor is qualified, the planner publishes an assignment with no jobs and a reason, Assessment publishes confidence 0 for every track, and the exercise continues.

**Running a circuit.** The processor parses the program, builds the state vector of 2 to the power of the qubit count complex amplitudes, applies each gate, and reads the probability that the measured qubit is 1. It then draws 1024 shots from a generator seeded with the SHA-256 of the step number and the program text. The result is the same on either processor. The error rate is published but is not applied to the result, so an injected drift changes where work runs and not what the exercise computes.

The simulator supports `h`, `x`, `ry`, `rz`, `rx`, `cx`, `ccx` and `measure`, on up to eight qubits.

### 10.5 Command's rules

The alert level is set from the highest confidence among tracks, and the shortest distance from any track at confidence 600 or more to a protected cell.

| Level | Condition |
| --- | --- |
| 5 | No tracks |
| 4 | Tracks, none at confidence 600 or more |
| 3 | A track at 600 or more, further than 8 cells from every protected cell |
| 2 | A track at 600 or more within 8 cells |
| 1 | A track at 800 or more within 4 cells |

A console input in the scenario for this step replaces the computed level.

At level 3 every unit is ordered `ready`. At levels 2 and 1, each track at 600 or more, nearest to a protected cell first, is given to the nearest unit that is `ready`, has `comms` of `ok`, has stores left and has not been given a track this step. Ties go to the lower unit number. That unit is ordered `respond`.

### 10.6 Units' rule and scoring

A unit ordered `ready` sets its readiness to `ready` at the next step. A unit ordered `respond` acts if its comms are `ok`: it uses one store, and the action is `done` if the track is within its reach. A unit whose comms are `lost` does not see the order and publishes nothing for it.

Exercise removes each object whose track has a `done` action. The score changes by +5 for an intercepted object, -2 for a `done` action against a phantom, and -10 for an object that reaches a protected cell.

### 10.7 Injection and release

An injection is one write by the Instructor, in the Instructor's own memory:

```
(instructor, injection, <n>) = {"role": "warning", "var": "sensor", "scope": "2",
                                "value": {"status": "failed"},
                                "from_step": 10, "state": "active", "by": "<who>"}
```

Every Role service holds a read on the Instructor's injection tuples. When it is woken for a step, it takes the injections that name it and are active. Before it writes a tuple it owns, it applies them: for each field an injection sets, the injected value is what the Role writes. The value is therefore in the Role's own memory, written by the Role, and every other Role that reads it sees the injected value.

To release, the Instructor writes the same tuple with state `released`. The Role writes its own value at its next step.

There is one write per injection, so there is no moment at which a value is in place without its hold. The injection tuple is also the record of who injected what and when.

The step order makes this exact. The Instructor writes the injections due at a step and then publishes `applied` for that step, and Exercise does not publish ground truth until it has seen `applied`. No Role computes the step before its injections are in place.

During run 4 the injected values sit in memories whose channels change protocol, key and algorithm, and the injections themselves are read over the Instructor's channel.

### 10.8 Setup, start and end

1. The Instructor's console writes the scenario to `(instructor, scenario, run)` and `(instructor, control, run) = {command: setup}`.
2. Every Role wakes on the scenario, writes its initial tuples from it, and writes `(<role>, ready, run)`.
3. Exercise wakes on the `ready` tuples. When all are present it writes the clock with step 0 and state `setup`.
4. The console shows every Role ready. The Instructor gives `start`. Exercise writes the clock with step 1 and state `running`.
5. After the last step Exercise writes state `ended`. Every Role's service stays up for review.

## 11. Detailed design: the protocol layer

### 11.1 The channel's setting

The Role's capability tuple says what protocol to use and seeds the encryption. It is per Role. The code that holds a read on it and applies it exists in `core/channel_setting.py`, where it is kept in the control memory under the names below.

The Role and the Role's communications are two different things, and this document keeps them apart.

- **The Role** is the service. It may need other Roles in order to work. Sections 9 and 10 describe the Roles. Nothing in them depends on how any Role is reached.
- **The Role's communications** are how that Role is reached: which memory it is in, which protocol carries its traffic, and what encryption protects it. Each Role can be in a different memory with different encryption. This section describes them. They are set by the capability tuple for the Role and can be changed without any Role knowing.

| Tuple | Meaning |
| --- | --- |
| `(channel, setting, <Role>)` | The capability tuple for the Role's communications: the protocol, and the seed for the encryption. It applies to that Role's traffic on whatever connection carries it. |
| `(channel, at, <address>:<process>.<pid>)` | Written by each end: what it is running now |

The code on the branch keys this tuple by the machine that hosts a memory and applies it to the whole connection to that machine. That ties a Role's communications to a host and a connection. In this design the tuple is keyed by the Role and applies to that Role's messages, so two Roles whose memories are on one machine can use different protocols and different encryption on the same connection.

The value of a setting:

```
{"driver": "chorus", "seed": "<the seed>", "protection": "aes-256-gcm"}
```

| Field | Meaning | State |
| --- | --- | --- |
| `driver` | The protocol: a word a driver has registered, `tcp`, `chorus` or `openqasm` | *Existing*; the word `openqasm` is new |
| `seed` | The seed for the encryption. With it, the same deterministic algorithm at every end picks which key of the stored set is current, and which is next at each switch. Empty means unsealed. | New. It replaces the field `key`, which names a key. |
| `protection` | The algorithm, including how a seed becomes a key | *Existing* as the cipher's name |

The code today also has an `accept` field, a list of key names a request may arrive under, and a key change made in three writes. Both are removed. There is only the current key.

Every process that carries channel traffic holds a read on the `setting` tuples and applies a change when the read returns. It then rewrites its `at` tuple. The owner's tool holds a read on the `at` tuples and knows the change is complete when every end shows the new value.

**Scope.** A setting applies to what its scope names, and the scope is its owner's choice. It can name a Role's memory, an application, or one instance of an application. A Role can have more than one setting in force at once: one protocol and key for one application's traffic and another for a second application's, or a custom encryption for a single instance. No setting is site-wide.

### 11.2 The key store

The key store is a directory on each machine, `/etc/frognet/channel_keys` (*existing*). For each Role it holds a set of keys, each a file of 32 bytes. The set is provided offline and placed there before use by whoever administers the machine. Nothing in it ever crosses a link. The current key is always one of the set, and each switch of encryption moves to another of the set.

The store is not tied to a driver. A key named in a channel's setting seals that channel's messages under whichever driver the setting names.

For the demonstration each machine is given the stored keys of each Role whose channel it is a member of, and no others.

### 11.3 A sealed message

The layout is *existing* except for the change noted under it, and is common to all drivers.

```
12 bytes   nonce: 8 bytes drawn once by the sending process, then a 4-byte count
the rest   the sealed message, ending in the algorithm's 16-byte tag
```

A sealed message does not name its key or its algorithm. Both ends take them from the channel's setting, and there is only one of each. Today's layout also carries the key's name ahead of the nonce; that is removed, which takes several bytes off every sealed message.

The request number is bound into the seal as associated data, so a sealed reply cannot be moved to another request. The nonce's first eight bytes are drawn fresh each time a process starts and again when its count runs out, and are never stored, so a restarted or copied process does not repeat a nonce.

Two algorithms are registered: `aes-256-gcm` (*existing*) and `chacha20-poly1305` (new). Both come from the system's cryptographic library, through `core/channel_protection.py`.

### 11.4 The three changes, write by write

**Changing the driver.** One write.

```
{"driver": "tcp"}  ->  {"driver": "chorus"}
```

The connection stays open. From its next message each end puts the new protocol's data inside its frames. The encryption on the frame is unchanged, so the receiver opens the frame as before, reads the protocol's word at the front of what is inside, and hands the data to that driver. A thread with a request in flight sends CACHE_MISS.

**Replacing a key.** One call in the stream.

```
sender:    ... frame, frame, SWITCH ENCRYPTION, frame under the next key, frame ...
receiver:  ... read,  read,  sees the call,     reads with the next key,  read  ...
```

The call says only that the encryption is switching. Both ends hold the stored set and the seed, so both know which key is next. Nothing fails to open because of the switch. A thread that does lose a request in flight, for example because the connection itself was lost, does this:

1. Its reply does not arrive, or does not open. The thread is still waiting, and it knows what it asked.
2. It sends CACHE_MISS under the current key, naming the request it already sent by its hash.
3. If the other side ran that request, it holds the answer. It returns that answer under the current key and runs nothing.
4. If the other side does not hold it, because the original never opened there, it says so, and the thread sends the request in full.
5. The retry is logged with the request number.

The owner's tool knows the change is complete when every end's `at` tuple shows the new key.

**Replacing the algorithm.** The algorithm is part of the Role's capability tuple. The Role's admin writes the new algorithm there, and the sender makes the same switch call. The switch moves to the next key of the set as any switch does, so no key is used under two algorithms.

```
{..., "protection": "aes-256-gcm"}  ->  {..., "protection": "chacha20-poly1305"}
```

### 11.5 The drivers

A driver is a class with six calls, defined in `core/transport_driver.py` (*existing*).

| Call | Contract |
| --- | --- |
| `connect()` | Open this end's permanent connection. Called once, and again at a network event when the connection has gone. |
| `send(data)` | Put one packed unit on the connection |
| `receive()` | The next packed unit. It blocks until one is there. |
| `pack(n, frame)` | The driver's own framing of one message numbered n. Returns the units to send. |
| `unpack(data)` | One received unit in; the message out when it is whole |
| `close()` | End the connection |

A driver registers its word with `register_driver(word, session_kind)` in `core/homa_transport.py` (*existing*).

|  | TCP | Chorus | OpenQASM |
| --- | --- | --- | --- |
| Module | `proxy/transport_semantic.py`, `daemon/engine/session.py` | `core/chorus.py` | `core/openqasm.py` (new) |
| Identified by | Its word at the front of the data inside the frame | `CHO` and a version byte, in the same place | `OQ3` and a version byte, in the same place |
| Data inside the frame | A 4-byte length, the message number, the message | A header difference, a first-bytes difference, the rest of the message | The same as Chorus, with the circuit codex above it |
| Largest unit | The message | 240,000 bytes; longer messages are cut and joined | 240,000 bytes |

**Telling the protocols apart.** A receiver determines the protocol from the frame it receives. It opens the frame with the channel's current key, and the data inside begins with the protocol's own word. The receiver hands the data to the driver registered for that word. This is why a protocol can be changed on an open connection, and why it can only be changed while the encryption is left alone: the receiver must be able to open the frame to see what is in it. Data that begins with no registered word is refused, with the reason in the log.

The code on the branch today does this differently. It opens a separate connection, on a separate port, for each driver, lets the old connection finish what it has, and changes a key with an accept list in three writes. This design replaces all three.

**Chorus on the link** (*existing*). The message is framed with a 62-byte header of 20 fields. What crosses is a mask saying which fields differ from what the receiver expects, the differing fields as variable-length numbers, then the first 24 bytes of the message as a bitmap of changed bytes and the changed bytes, then the rest. The receiver expects the message number to rise by two and the length fields to agree, so a typical header crosses in a few bytes.

**OpenQASM** (new). The same difference encoding as Chorus. What sets it apart is above it: the circuit codex of 11.7.

**Encryption is not part of any driver** (new). Sealing is applied to the frame after a driver has packed it, and removed before a driver unpacks it, by one piece of code that serves every protocol. No driver seals, and no driver requires or refuses a key. In the code today the sealing sits inside the Chorus driver's `pack` and `unpack`. It is moved out.

### 11.6 Holding more than one driver

A process has one carrier for message sessions, `ChorusOnTcp` in `core/chorus.py` (*existing*). It creates a Chorus connection for each destination. It is changed in three ways.

1. One connection is held for each destination, whatever protocol is in use on it.
2. When a message is sent, the carrier asks `channel_setting.driver(host)` (*existing*) for the word and uses the driver registered for it to pack the data that goes inside the frame.
3. When a frame is received and opened, the carrier reads the word at the front of the data and uses the driver registered for it to unpack.

A change of protocol therefore needs no new connection and no second port.

### 11.7 The OpenQASM codex

The codex is `core/openqasm_handler.py` (new). It acts on a string value inside a request or reply when the value begins with `OPENQASM`.

**Splitting.** The program text is scanned once. Every numeric literal is lifted out: an optional sign, digits, an optional decimal point and digits, an optional exponent. What remains, with a marker byte where each literal stood, is the fixed text. Two programs with the same fixed text are the same circuit.

**Templates.** Each end keeps a table of fixed texts for the connection, numbered in the order first sent. Circuits loaded before the run from `/etc/frognet/openqasm_circuits` take the first numbers, in order of file name, identically at both ends.

**What crosses.**

```
First time:   0x00, the literal count, the whole program text
Repeat:       0x01, the template's number, a bitmap of which literals changed,
              then each changed literal
A literal:    0x00 + a signed variable-length integer        when it is an integer
              0x01 + decimal places + a signed integer        when it is a fixed-point decimal
              0x02 + length + the literal's characters        otherwise
```

A decimal such as `2.827` crosses as its places (3) and the integer 2827. The receiver writes it back with the same number of places, so the program is returned byte for byte. Before sending the short form, the sender rebuilds the program from it and compares. If the rebuilt text differs in any byte, the program is sent whole as a first-time program.

**Size.** For the circuit of 10.3 a repeat is one form byte, a template number, a one-byte bitmap and two literals of three bytes each: nine bytes in place of the whole program.

**Where it is called.** The JSON template code in `core/json_handler.py` (*existing*) extracts the values that differ from a request's template. The codex is applied to a differing string value at that point, on connections whose setting names the `openqasm` driver. The exact call site is confirmed in the code as the first act of build step 2.

## 12. Detailed design: software

### 12.1 Files

All paths are under `opt/frognet_semantic`.

| File | Contents | State |
| --- | --- | --- |
| `core/openqasm.py` | The OpenQASM driver | New |
| `core/openqasm_handler.py` | The circuit codex | New |
| `core/chorus.py` | The carrier holds connections by driver | Changed |
| `core/channel_setting.py` | The `protections` map | Changed |
| `core/channel_protection.py` | Registers `chacha20-poly1305` | Changed |
| `core/homa_transport.py` | Registers the word `openqasm` | Changed |
| `core/json_handler.py` | Calls the codex for OpenQASM values | Changed |
| `proxy/transport_semantic.py`, `daemon/engine/session.py` | Sealing in the TCP driver | Changed |
| `core/role_registry.py` | Not needed. Role memories are application memories elected by core/app_hosts.py, which is used as it is. | Changed |
| `simulation/roles_exercise/role_service.py` | The loop every Role service shares: hold, compute, check holds, publish | New |
| `simulation/roles_exercise/warning.py`, `assessment.py`, `command.py`, `units.py`, `exercise.py` | One Role's rules each | New |
| `simulation/roles_exercise/compute_planner.py`, `compute_processor.py`, `statevector.py` | Compute | New |
| `simulation/roles_exercise/instructor.py` | The Instructor's service and console | New |
| `simulation/roles_exercise/record.py` | Writes the exercise record and the engineering record | New |
| `simulation/roles_exercise/scenarios/first.json` | The scenario | New |
| `simulation/roles_exercise_tier.py` | Builds the machines, starts the Roles, makes the changes, checks the criteria | New |

Discovery, routing and the election code are not in this table and are not changed.

### 12.2 A Role service

Every Role service is one process running one loop.

```
read the scenario; write initial tuples; write (role, ready, run)
loop:
    rows = wait_until(<the value this Role consumes>, done = "it carries the next step")
    injections = the Instructor's injection tuples that name this Role and are active
    values = the Role's rule applied to rows
    for each tuple this Role owns:
        set every field an injection names to the injected value
        upsert it, DURABLE
```

A service does not import a driver, name a key or read the channel setting. It calls the memory by host name. This is what requirement R25 tests: the string `tcp`, `chorus`, `openqasm` or any key name appearing in `simulation/roles_exercise/` outside the tier and the admin tools fails the build.

### 12.3 Admin tools and views

Each Role has a page named `<Role>Admin`: WarningAdmin, AssessmentAdmin, ComputeAdmin, CommandAdmin, UnitsAdmin, ExerciseAdmin and InstructorAdmin. A page reads its Role's memory through held reads and redraws when one returns.

| Page | Shows | Lets the user |
| --- | --- | --- |
| WarningAdmin | The grid with sensor coverage and current tracks; each sensor's status | Set a sensor in or out of service |
| AssessmentAdmin | Each track's circuit angles and confidence | Nothing in this version |
| ComputeAdmin | Each processor's availability, error rate and jobs this step | Take a processor out of service |
| CommandAdmin | Tracks with confidence, the alert level, units' status, the score | Set the alert level |
| UnitsAdmin | Each unit's position, readiness, comms and stores; current orders | Nothing in this version |
| ExerciseAdmin | Ground truth and the clock | Nothing in this version |
| InstructorAdmin | Every Role's ready state, the clock, the injection list, and any Role's view | Load a scenario, start, freeze, resume, inject, release |

InstructorAdmin also shows, for each Role's channel, the driver, key name and algorithm every end reports. Each Role's own page is where that Role's owner sets its channel's protocol, key and algorithm. InstructorAdmin shows all of them together, and it is where a viewer watches the changes of run 4 happen.

## 13. Test procedure

### 13.1 Starting a run

```
simulation/roles_exercise_tier.py --run 1|2|3|4 --links fast|radio-4800 --out <file>
```

The tier performs these steps and stops with the reason at the first that fails.

1. Builds three machines with separate network stacks and the links of 5.1.
2. Places the keys and the circuit file on each machine.
3. Starts the network software. Waits, by held read, for the machines to find each other and for the role lines to reach each hosts file.
4. Writes each channel's starting setting for the run. Waits for every end's `at` tuple.
5. Starts the Role services. Loads the scenario through the Instructor. Waits for every `ready` tuple.
6. Gives `start`.
7. In run 4, holds a read on the clock and when a change of 5.2 reaches its step, has the owning Role's admin tool write it, and waits for every end to report it.
8. On `ended`, closes the two records. The record process has held a read on every Role's memory since setup and has written each published value as it appeared, so a value that is later overwritten is still in the record.
9. Evaluates the criteria of 13.2 and prints each as passed or failed.

### 13.2 Checks

| Check | How it is made | Criterion |
| --- | --- | --- |
| The exercise record matches run 1 | Byte comparison of the record files | 1 |
| No failed or wrong request | Each service counts its calls and failures; every value read is compared with the value written | 2 |
| Injected values stayed | The tier reads each injected value from the target Role's memory at every step between injection and release | 3 |
| No process restarted | Process identifiers at the end equal those at the start | 4 |
| Every end reported each change | The `at` tuples after each write | 5 |
| Three protocols at once | A capture on the simulated links shows connections that opened with each of the three protocol words carrying traffic within the same step | 5 |
| Key and protocol are independent | Each protocol is run under a key and under none, and a key switch and a protocol switch are each made without the other, with the same exercise record every time | R22 |
| An unknown protocol is refused | A connection whose first frame carries no registered protocol word is closed, with the reason in the log | R22 |
| No protocol named above the channel | A search of `simulation/roles_exercise/` | R25 |
| Discovery and routing untouched | A diff of those directories against the starting commit is empty | R27 |

### 13.3 Output

Each run writes one file holding the exercise record, the engineering record, the performance report of 5.5, the result of each check, and the build identifier of every process that took part.

## 14. Terms

| Term | Meaning |
| --- | --- |
| Channel | The connections that carry one memory's traffic, with the setting that governs them |
| Chorus | The CroakOS transport whose control information crosses as differences. Also the model by which Roles coordinate through held reads on shared memory. |
| Codex | Code that understands one kind of content well enough to send only what changed |
| Control memory | The memory that holds the network's own settings, including channel settings |
| Driver | The code for one protocol: six calls |
| Held read | A read that returns when the data it asked for changes |
| Injection | A value the Instructor publishes in its own memory, which the Role it names applies to its own memory and keeps until released |
| Memory | A store of tuples held by one node and found by name |
| OpenQASM | The open language for describing quantum circuits |
| Role | A service. It may need other Roles to work. Each Role can be in a different memory with different encryption. |
| Stored-key exchange | Moving a channel from one pre-placed key to another by one write to its setting |
| Tuple | One named value in a memory: service, variable, scope and a JSON value |

## 15. References

1. Department of War, *Post-Quantum Cryptography Strategy*, June 2026.
2. National Security Agency, *Commercial National Security Algorithm Suite 2.0* and its FAQ.
3. Office of Management and Budget, [Memorandum M-26-15, *Execution of the Migration to Post-Quantum Cryptography*](https://www.whitehouse.gov/wp-content/uploads/2026/06/M-26-15-Execution-of-the-Migration-to-Post-Quantum-Cryptography.pdf), June 24, 2026.
4. Department of War, [Instruction 8430.01, *Accelerated Mission Software*](https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodi/843001p.pdf), effective September 8, 2026.
5. Department of Defense, *Zero Trust Reference Architecture*, version 2.0, July 2022.
6. National Institute of Standards and Technology, Special Publication 800-218, *Secure Software Development Framework*.
7. Fawcett Innovations, *CroakOS Secure Channels: Changing Protection Without Changing Applications*, November 19, 2026.
8. Fawcett Innovations, *Transport Driver Interface*, `core/TRANSPORT-DRIVER-INTERFACE.md` in the FrogNet Living Network repository.
