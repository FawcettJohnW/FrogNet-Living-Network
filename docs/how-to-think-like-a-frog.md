<!-- How to Think Like a Frog, by John W. Fawcett and Daniel Tone. Generated 2026-11-15 from the site edition (https://fawcettinnovations.com/how-to-think-like-a-frog.html); the Word edition is how-to-think-like-a-frog.docx beside this file. -->

The FrogNet Living Network

# How to Think Like a Frog

The argument for network shared memory, in plain English

John W. Fawcett · Fawcett Innovations LLC

## The model now constructs the network itself

## Address the truth first.

A cell has three coordinates: `service.variable.instance`. Its value is a JSON object, the bag. A write replaces that value and receives a write-order id from memory. Within a variable, use that id for ordering; a participant’s wall clock is not the ordering authority.

A rewrite does not merge competing versions. If independent participants write the same truth cell, fix the ownership or the address. Claims from competing participants belong in different cells, so every participant can derive the winner from the same published claims.

| Truth          | Addressing                | Reader behavior                                                         |
|----------------|---------------------------|-------------------------------------------------------------------------|
| Latest value   | One owned cell, rewritten | A slow reader receives the latest value, not a backlog.                 |
| Distinct state | One cell for each record  | Read records past the reader’s write-order mark.                        |
| Collection     | One cell per element      | A partial address selects the table; the instance names the element.    |
| Container      | Part cells, then a header | The owner publishes the visibility marker after the parts.              |
| Reference      | A tuple name in a cell    | Read the referenced state instead of making a second copy of its truth. |

## Five verbs, one memory surface.

| Verb       | Meaning                                                                              |
|------------|--------------------------------------------------------------------------------------|
| Write      | Publish a cell and receive its write-order id.                                       |
| Read       | Read one cell or a partially addressed table.                                        |
| Held read  | Park until state newer than an after-id is available, subject to the requested wait. |
| Read-fresh | Select cells within a reader-specified age, using the memory’s clock.                |
| Remove     | Remove by write-order id.                                                            |

A non-waiting write is also exposed. Vendors can add operations in their own RAM host, reached through the library’s call surface. API names differ by binding; use the binding’s documentation rather than translating these conceptual verbs into guessed function names. The supplied JavaScript programming guide describes `write`, `writeNowait`, `read`, `remove` and `call`.

``` code
// JavaScript binding: hold the table after the last observed id.
const cells = await ram.read('Retail', 'order', '', { after, waitS: 3600 });
// Process the state, then advance your own mark.
```

## Hold a view instead of fetching on every request.

Read the state once, build a snapshot, and hold a read for subsequent changes. Publish a new immutable snapshot when it changes. The application’s query path reads the snapshot; it need not perform a network operation for each query.

A participant can publish its own applied cell: “I hold everything through id N.” A test or dependent participant waits on that state. Publication makes a parked reader runnable; it does not mean the reader has completed its work when the writer returns.

The memory library’s semantic cache handles unchanged answers. A held application view has a different purpose: it is the participant’s present knowledge, available directly to its computation.

## Coordination is state.

For a scarce item, every contender publishes its own claim. Every contender reads the claims and performs the same write-order arithmetic. For work whose result does not yet exist, a participant publishes a Want and holds the result address. A participant able to supply it holds the Wants. A failure can be a published result.

A foreign source has one participant responsible for reading it and publishing its state with provenance. Other participants use that state. Read-modify-write across several participants is a signal to revisit who owns the truth.

A participant writes its progress mark after the work. Rewriting the same owned result cells after a restart can be idempotent. That pattern does not automatically make an external payment or another irreversible side effect exactly once; its contract still has to support the recovery behavior.

## Recovery changes the world you are reading.

When a memory host changes or a network splits or joins, previously held state may belong to an earlier world. In the Living Network model, participants reassert what they know and rediscover what they depend on. Replacement RAM is not a promise to restore the old contents.

Recovery hooks are implementation-specific. The October 11 engineering record still lists the Python wrapper’s missing `heartbeat` and `on_database_change` integration as open. Do not invent those APIs in an example. Region hosts and applications must define their own lifetime and restart contracts.

## The vendor shapes the API.

The five verbs are the floor. A vendor can change the schema, instance encoding and host operations to fit the domain: selection, counts, joins, aggregates or other operations supported by its implementation. A well-known host location gets the application to the region; it does not dictate the region’s policy.

The current material includes C++, Python, a Kotlin member implementation and a separately supplied JavaScript binding guide. Kotlin’s FD&F member intentionally uses RAW requests; it is not evidence of semantic compression in that client. Anyone allowed to read a region may see its readable cells. Keep secrets out of shared application state.

## The network is one of the applications.

The hybrid grew out of converting lispers.net and RINA to the shared-memory programming model. It is an implementation built from those ideas and algorithms. It does not claim that unmodified LISP and RINA stacks were merely connected together.

Network Shared RAM remains the programming proposition. FD&F shows that network construction itself can use it: publish current truth, derive the plan, apply your part, and repeat when the state changes.

| Component                  | Owns                                                | Published or consumed contract                                                   |
|----------------------------|-----------------------------------------------------|----------------------------------------------------------------------------------|
| RINA lineage               | Names, scope, enrollment and scoped relationships   | A named pond and its membership context.                                         |
| LISP lineage               | Identity separated from location; mapping concepts  | Identity resolution and signed mappings in the hybrid implementation.            |
| Ribbit / RAM manager       | Shared state, member sessions and region operations | Participants, facts, mappings, world and event watches.                          |
| fdf-facts + fdf-client     | A node’s own observations and publication           | Addresses, DHCP lease pairs, uplink and tunnel facts, capability and generation. |
| fdf_graph + FrogNet daemon | Derived topology and local application of the plan  | Routes, defaults, names, elections and the node’s announced plan state.          |
| fdf-hub                    | Packet relay and present peer configuration         | WireGuard allowed-ips from the graph and verified mappings.                      |
| Windows / Android bridge   | Device membership, placement, reach and aliases     | One session per pond; a forward pond; one packet device per reached scope.       |

![FD&F responsibility and contract flow: scoped membership and owned facts enter the RAM manager; nodes derive local plans and the hub derives current packet peers.](https://fawcettinnovations.com/assets/diagrams/fdf-state-flow.svg)

Control state and packet carriage have separate roles. Colors identify RINA, LISP and FrogNet/Ribbit lineage.

## One world read, then the local plan.

Each node publishes its own facts. The region’s `world` operation returns participants and mappings together. A watch is registered before the initial world read, so a join during startup cannot disappear in a gap between read and watch.

Nodes run the same graph rules over the published rows. The daemon applies the slice for its own identity in-process. The hub computes the peers and allowed prefixes it needs now. It does not keep an absent participant parked as a remembered route.

In r19, member aliveness comes from the RAM session. Lease pairs supply LAN adjacency; the applier does not echo-probe a member that the region already identifies as alive. Hub usability still depends on the tunnel’s handshake, and loss of the region withdraws its routes, names and tunnels.

## Several ponds on one device.

A device keeps a session to every configured pond. One pond is forward. Other ponds may permit reach under their own policy. Bringing a pond forward changes the announced roles on the open sessions and moves the forward path.

Each reached pond has its own packet device. The bridge maps overlapping private destinations to 1:1 aliases from 100.64.0.0/10. Its TUN address stays fixed; the address assigned by a pond is used on packets inside that pond. Reach is not an instruction to merge the address spaces.

Resolve by identity rather than by choosing a network in application code. Exactly one pond may answer. Two answers are refused and named as ambiguous. Released aliases are reserved for the same use during a 600-second quarantine.

## Membership and packet carriage have different jobs.

A member connects out to the RAM manager. Shared RAM gives it membership and the information needed to construct its path. For an off-mesh device, the current bridge carries ordinary IP traffic through a WireGuard hub path. On Windows it uses Wintun; on Android it uses VpnService and protects the WireGuard sockets from the tunnel.

The application does not configure the tunnel’s address, key or peer. That is the useful difference demonstrated here. The implementation nevertheless uses WireGuard, a TUN, routing and a relay hub. “No VPN technology” would be an inaccurate description of the current packet path.

## What has actually been exercised.

The r19-a14 acceptance record reports fdf-tun 20/20, reach 68/68, region-loop 20/20, graph 17/17, hub 25/25, leaf-tunnel 10/10 and placement 18/18. These are separate suites with their own topologies, not one total test count.

r19-a15 adds a red-first region-loop check that a runMerge does not remove leaf names from /etc/hosts. The October 12 Windows demo records membership, ping, SSH, the monitor and live topology map, plus a Windows–Android call. The observed call reports 1280×720 at 20 fps; the displayed receive rate is roughly 1.2–1.5 Mb/s.

[Read the qualification boundaries →](https://fawcettinnovations.com/evidence.html#fdf-evidence)

**HOW TO THINK LIKE A FROG**

*Networking for the 21st Century*

*FrogNet combines fifty years of lessons in networking, programming, and hardware engineering to move the science into this century. From the user's perspective, nothing changes — and a great deal more becomes possible.*

John W. Fawcett, CEO · Daniel Tone, VP of Engineering

The FrogNet Living Network · Fawcett Innovations LLC

Revised August 2026 · updated for the region, October 2026

## **Contents**

## **What FrogNet Does, In Plain English**

*Network engineers can skip this section. The technical material starts at the Introduction.*

The rest of this document is for network engineers. This section is for everyone else. It is shorter than the rest on purpose — you do not need the technical detail to understand what FrogNet does or why you might want one.

### **A Small Box That Changes What Your Family Can Do**

Imagine a small box — about the size of a deck of cards — plugged in at your house. Your mother has one at her house. Your sister has one at her flat in London. Each box costs about what you'd spend on a nice dinner out.

Plug them in and they find each other. You do not configure anything. You do not set up accounts. You do not pick a service plan. The boxes form a private, encrypted network that runs a photo gallery, a family chat, a shared calendar, a bulletin board for sticky notes, a family map that shows where everyone is, and video calling — all on hardware that belongs to your family and nobody else.

Your photos are not on iCloud. Your messages are not on WhatsApp. Your location is not sold to data brokers. Everything stays on your boxes. The only thing that crosses the internet is encrypted traffic that nobody — not your internet provider, not a hacker, not a government — can read, and that does not look like FrogNet to anyone watching.

The ongoing cost is a few dollars a month — a small region-host subscription (one cloud machine) that lets your phones reach the network when you're away from home. That is all. No per-person fees. No storage tiers. No annual price increases. No surprise changes to the terms of service. A small region-host subscription, and your family has its own private internet — internationally — for as long as the boxes are plugged in.

The actual cost depends on where the region host runs. A typical family network with its region host on a rented cloud machine is expected to run a few dollars a month — primarily data transfer for the traffic that crosses the internet between your boxes. A family running the region host on their own hardware pays essentially nothing beyond electricity. Larger or more active networks cost more; the current FrogNet development network, running heavy testing traffic across Seattle and New York, runs about forty dollars a month. A typical Mom-and-family deployment will be on the low end of that range, not the high.

### **What You Pay Today for the Same Things**

Most people are already paying for all of this. They are just paying a different company for each piece, and their data is spread across those companies. Here is what a typical family pays today for the services a single FrogNet box would replace, and what each service costs on FrogNet.

| What You Do                 | What You Use Today                 | Typical Cost                                                 | How FrogNet Does It                              | FrogNet Cost                             |
|-----------------------------|------------------------------------|--------------------------------------------------------------|--------------------------------------------------|------------------------------------------|
| Store family photos         | iCloud+, Google One, Amazon Photos | \$3–\$10 / mo                                                | Private gallery on your box                      | Included                                 |
| Know where family is        | Life360 (Gold), Find My            | \$15 / mo                                                    | GPS map in the FrogNet app                       | Included                                 |
| Monitor your home           | Ring, Nest, SimpliSafe             | \$10–\$20 / mo                                               | Sensors on your box, no cloud                    | Included                                 |
| Check on an aging parent    | Life Alert, medical alert services | \$30–\$60 / mo                                               | Passive sensors + family alerts                  | Included                                 |
| Share files with family     | Dropbox, Google Drive, OneDrive    | \$10 / mo                                                    | Peer-to-peer, on your hardware                   | Included                                 |
| Message your family         | WhatsApp, iMessage, Signal         | Free but with tradeoffs                                      | FrogChat on your box                             | Included                                 |
| Video call family           | FaceTime, Zoom, Meet               | Free with limits                                             | The Communicator — peer-to-peer, no call service | Included                                 |
| Shared calendar             | Google Calendar, Apple Calendar    | Free, but your schedule trains AI                            | Calendar on your box                             | Included                                 |
| Typical family total, today |                                    | \$60–\$120 / mo, plus unlimited access to your personal data | Total on FrogNet                                 | A few \$ / mo, and your data stays yours |

Look at the bottom row. The right side is the whole bill on FrogNet — one region-host subscription, for every person in your family, everywhere you live, for as long as the boxes stay plugged in. Your data stays on hardware you own.

The left side is what you already pay — money leaving your household every month, plus a second price that never shows up on the bill: unlimited access to your personal data. The companies in that column read your messages, scan your photos, sell your location, and train their AI on your schedule. You pay them, and then they monetize you on top. FrogNet is the offer to stop paying both prices.

### **Mom Living Alone**

If there is one scenario that explains why this matters, it is this one.

An elderly parent lives alone. You want to know she is okay. The options on the market are not great — cameras she will hate, wearables she will forget, services that charge sixty dollars a month to watch her.

A FrogNet box at her house, with a few small passive sensors, learns her daily patterns. When Mom gets up. When she makes coffee. When she moves through the house. It does not watch her. It does not record her. It notices when the pattern changes. If Mom does not get up at her usual time, the system alerts the family — through the private FrogNet, not through a company's servers. No cameras. No surveillance. Her dignity stays intact. Your peace of mind goes up.

That one use alone is worth the cost of the box.

### **It Still Works When the Internet Does Not**

Every app on your phone assumes the internet is there. When it is not — a storm, a blackout, a rural road with no bars — your apps are useless. You cannot call, text, or check on anyone.

FrogNet does not assume the internet is there. Each box is a complete, self-contained network. If the internet disappears, the box keeps running. If two boxes can see each other by WiFi or Ethernet, they mesh together and each can read what the other knows: photos, messages, sensor data, GPS positions, with no internet required. When the internet comes back, the wider network is reachable again.

This matters most in the moments you cannot plan for: when a hurricane takes out the cell towers, when your kid is at a camp with no coverage, when an ambulance drives through a dead zone with a patient in the back. The same box that handles your family photos in good weather keeps working in bad weather. That is not a feature anyone else offers.

For a longer look at how FrogNet performs in emergencies, see Appendix A.

### **A Word About Privacy**

People ask how FrogNet compares to Signal, or to a VPN. The short answer is that Signal is excellent at what it does, and a VPN is useful for what it does, but neither one does what FrogNet does.

Signal protects the content of your messages from the people carrying them. FrogNet eliminates the carrier. There is nobody in the middle to trust, compromise, or compel. No servers to subpoena. No phone numbers. No accounts. No metadata. And it keeps working when the internet does not.

A VPN is an encrypted tunnel to someone else's building — the cloud, Google, Apple, Meta. Your data still ends up on their servers. A FrogNet is not a tunnel. It is your own building. The services run on your hardware. There is no "other end" where a company has your data. There is no company at all.

### **You Can Watch All Of It**

Everything in this document has been recorded. There is a video series that goes from a bare Linux box to a transcontinental video call, and a set of deeper recordings that open the hood on each layer. Where a section of this document describes something that was filmed, the recording is linked in the margin. Nothing here asks you to take a claim on faith that you could instead watch.

**WATCH** **Welcome** — the origin, and the network running now

**WATCH** **What It Is** — the whole proposition in eight minutes

### **What Follows**

Everything described above is operational today. FrogNet runs across nodes in Seattle and New York, over Ethernet, WiFi, WireGuard tunnels, and a 900 MHz radio link, with more locations coming online. On April 17, 2026, a collaborator in New York completed the first independent end-to-end closed-loop demonstration — a web application, a sensor, and a physical actuator, all on a WiFi network in New York, coordinating through shared memory 2,400 miles away in Seattle, entirely over FrogNet's semantic protocol. It is not a prototype. It is not a simulation. It is a production system built over more than a decade by a network architect with fifty years of experience at companies including Boeing, Electronic Arts, Sierra On-Line, Microsoft, and NanoString Technologies.

The rest of this document explains how it works. It describes thirteen paradigm shifts — foundational assumptions about how networks have worked since 1969 that FrogNet replaces — followed by a comprehensive inventory of everything FrogNet is and does. If you are not a network engineer, you do not need to read it. If you want to understand why the claims in this section are architectural truths rather than marketing promises, read on.

**COMPANION** This document is the *why*. Its companion, *The Magnum Croakus*, is the *how* — the complete build and reference manual, with the source-level detail, the worked examples, and the parts that are not built yet named honestly. Where a shift below has a chapter that goes deeper, that chapter is named.

## **Introduction**

Every network in existence today was built on a set of assumptions that made sense in 1969. Connectivity is rare and expensive, so design for disconnection tolerance. Memory is scarce, so keep the network stateless. Administrators are available, so require human configuration. The internet is reachable, so treat it as the backbone.

Those assumptions have calcified into orthodoxy. Fifty years of protocol design, tooling, and engineering education have been built on top of them. Nobody questions them because everybody learned from people who learned from people who learned from people who never questioned them either.

FrogNet questions all of them.

This document describes thirteen paradigm shifts — foundational assumptions about how networks work that FrogNet replaces with better answers for the environments it actually operates in. It then describes, in detail, everything FrogNet is and does.

Reading this document will not make you a FrogNet engineer. But it will make you think differently about what a network can be — and that is the prerequisite for understanding why FrogNet matters.

## **The Paradigm Shifts**

FrogNet did not make incremental improvements to existing networking. It went back to first principles and replaced thirteen foundational assumptions with designs that match the actual constraints of the environments it operates in.

The paradigm shifts in this document are not theoretical, and the clearest evidence for them is a fact about the schedule. The work spans more than a decade — most of it spent inside the old assumptions, making slow progress against problems that would not yield. The system described here — the transport-abstracted fabric, the semantic compression engine, the discovery and routing layer, the shared memory, the sensor and AI platform, and a real-time adaptive media plane — came together in under a year, and it came together only after those assumptions were set down.

That is not a claim about unusual talent. It is a claim about the leverage of the ideas. Most of the effort in conventional networked systems goes into building and debugging the very machinery these shifts delete: the message-passing, the session state, the consensus, the per-application protocols. Remove the machinery and one person can hold the whole system in their head. The proof of a paradigm is not that it can be argued for — it is that it lets a small number of people build what used to take many. The decade is what it cost to find the shifts. The year is what they were worth.

### **1. Transport Abstraction — Protocol Independent of Physical Layer**

Every network stack in existence is built around a specific transport assumption. TCP/IP assumes reliable-ish packet delivery. Radio protocols assume unreliable, low-bandwidth, high-latency links. WiFi assumes high-bandwidth local area. The entire OSI model is built around the idea that you choose your transport and build your protocol for it.

FrogNet separates transport from processing completely. The application layer has no idea what transport is underneath. The same code runs identically over fiber, WiFi, Ethernet, WireGuard tunnel, LoRa, satellite, or a narrowband RF link. You don't configure for the transport. You don't optimize for it. You don't even know it's there. The semantic compression layer adapts automatically to whatever the link gives it.

This isn't just transport agnostic as a marketing claim. It's an architectural decision that eliminates an entire category of protocol design work. It also means that a single deployment can span WiFi, Ethernet, and RF simultaneously — the same network, the same code, the same behavior, regardless of what the physical layer is doing underneath.

One line worth stating explicitly: the radio is just a pipe. Which radio? Whichever one is there. FrogNet does not care, and FrogNet is not a radio protocol. The conclusions in this document about performance, compression, and survivability are consequences of what happens above the transport, not of any particular choice of transport below it.

**CROAKUS** Part III, *Hooking Up the Network* — the three honest ways to attach a node, and why the differentiator is transports multiplexed in one topology.

**WATCH** **Hardware** — the same claim, demonstrated on a bench with three radios

### **2. Offline-First — Disconnection Is the Baseline, Not the Error**

Every networked application ever built assumes connectivity as the baseline and treats disconnection as an error condition to be handled. Offline mode is a feature you add later, grudgingly, when users complain. Progressive Web Apps, service workers, local storage — these are all retrofits on top of an online-first architecture.

FrogNet inverts this. Offline is the default. Every node operates fully standalone as the baseline condition. Connectivity is an enhancement that arrives when available and disappears without consequence. Applications built on FrogNet don't handle disconnection — they never assumed connection in the first place. The architecture matches the physical reality of the environments FrogNet operates in, where connectivity is intermittent by nature.

### **3. /etc/hosts vs. DNS — Ground Truth Over Administered Namespace**

DNS was designed for a world where networks are large, stable, and administered by professionals. You configure a nameserver, you delegate zones, you manage TTLs, you wait for propagation. The assumption underneath all of it is that the network topology is known, relatively static, and has a central authority responsible for keeping the namespace consistent.

FrogNet operates in the opposite environment. Nodes appear and disappear. Topology changes without warning. There is no central authority. There is no administrator. The network manages itself or it doesn't work.

DNS fails in this environment — not because it's poorly implemented, but because it was designed for a different problem. A nameserver that goes offline takes its zone with it. A TTL that hasn't expired serves stale data. Split-brain scenarios during network partitions produce inconsistent resolution across the mesh.

FrogNet resolves mesh names through /etc/hosts — the oldest, simplest, most reliable name resolution mechanism in existence. Every node maintains its own hosts file, and the merge rewrites it every time the topology changes. There is no TTL to expire, no zone to delegate, no nameserver to go offline. Name resolution order is files first, then DNS: mesh names are answered locally and instantly, and anything not on the mesh goes upstream over the untouched default route. Each node also runs dnsmasq so the phones and laptops on its own segment get the same names by DHCP without any client configuration.

The service names — databasehost.frognet and mediahost.frognet among them — are rewritten into every hosts file on every merge. No node ever holds a stale address for a service. The moment the topology changes, the hosts file changes with it, and a program that asked for the name gets wherever the role is running now.

DNS is the right answer for a stable, administered network. Local ground truth is the right answer for a self-forming mesh where the only thing you can rely on is what the node can directly observe.

**CROAKUS** Part V, *Discovery* — the walk, and why .2 proves while .1 carries.

**WATCH** **Discovery** — the host table deleted on a live node, and rebuilt by the merge

**WATCH** **Discovery, Deeper** — echo, getHosts, and the resolver chain in detail

### **4. Sovereign Networking — No Dependency Chain, No External Authority**

Every network you use today depends on someone else's infrastructure. Your WiFi depends on your ISP. Your ISP depends on backbone providers. Your applications depend on cloud providers. Your DNS depends on root servers. At every layer, someone else is in the chain — someone who can fail, someone who can be compromised, someone who can cut you off, someone who can surveil you, someone who can monetize your traffic.

FrogNet has no such dependencies. The network is yours. The data is yours. The infrastructure is yours. No ISP, no cloud provider, no DNS authority, no certificate authority, no backbone provider is in the chain. You are not a tenant in someone else's infrastructure — you are the infrastructure.

This matters differently in different contexts. For a family it means privacy. For a business it means control. For a military unit it means survivability. For a community it means resilience. But the underlying shift is the same: from dependent networking, where you operate at the sufferance of others, to sovereign networking, where you operate on your own terms.

### **5. Emergent Internet — Self-Assembled, Not Designed**

The internet was designed. IANA allocates address space. ICANN manages domains. ISPs negotiate peering agreements. BGP routes are configured by human administrators. The entire global routing infrastructure is the product of deliberate human decisions made by known authorities.

FrogNet's internet emerges. No allocation authority. No routing administrator. No peering negotiation. Nodes discover each other, calculate topology, write routes, and form a coherent network — automatically, repeatedly, every time the topology changes. The internet that results wasn't designed by anyone. It assembled itself from the nodes that happened to be reachable at that moment.

This is a different model of what a network is. The traditional internet is a constructed artifact. A FrogNet is a living system.

### **6. Stateless vs. Stateful — The Network Carries Context**

Every network built since the early days of the internet is stateless by design. Each request arrives with no memory of what came before. The server reconstructs context from scratch — parsing headers, re-establishing session state, re-transmitting data the other end almost certainly already has. This was a reasonable choice in 1969 when memory was scarce and connections were unreliable. It has been the default assumption ever since, and nobody questioned it.

FrogNet questions it.

The semantic compression engine introduces state at the network level. The fabric remembers what it has already seen. Templates are learned from the structure of real traffic and cached on both ends. When an exchange matches a known template, only the values that changed cross the wire — not the structure, not the unchanged fields, not the overhead. The network itself carries the context so the application doesn't have to re-transmit it.

This is not delta encoding. Delta encoding sends the difference between two versions of a document. BLDC-1 — the Bullfrog Long Distance Communications Protocol — goes further: it separates structure from value, learns the structure once, and then transmits only values. An exchange whose content has not changed collapses to a twenty-one-byte frame. A large structured payload with a handful of moving fields collapses to those fields and a map saying where they go. The application never changes. The protocol never changes. The wire changes completely.

The stateless model was a constraint imposed by 1960s hardware. FrogNet removes the constraint.

**CROAKUS** Chapter 26, *The Accelerant — Semantic Compression*, and the appendix *Inside the Compression Engine* — including the section called *Twenty-one bytes*, which prints every one of them.

**WATCH** **Semantic Compression** — SAME, DIFF and FULL explained, with the wire watched live

### **7. Infrastructure Compression — Transparent, Automatic, Zero Application Changes**

Every compression system ever built is an application-level decision. You choose to compress a file. You choose to gzip an HTTP response. You configure your CDN to minify assets. Compression is something developers do deliberately to specific data when they decide it's worth the effort.

FrogNet makes compression an infrastructure property. The developer writes REST. The network compresses everything, automatically, transparently, using a codec that learns the structure of the actual traffic flowing through it. There is no configuration, no decision, no per-endpoint optimization. Every HTTP exchange between nodes is compressed. The developer never thinks about it.

This is the same paradigm shift that happened when hardware TCP offloading moved network stack processing from software to silicon — except FrogNet does it for semantic content, not just packets. All system-level coordination within FrogNet uses REST, which means the coordination overhead itself is compressed. On a steady-state network, management traffic approaches zero wire cost.

The mechanism has two halves and they are worth separating, because they are separate technologies that happen to run together. BLDC-1 decides what the smallest honest representation of an exchange is — SAME when nothing moved, DIFF plus a map when a few fields did, FULL when the template has not been learned yet. FNWP-1, the FrogNet Wire Protocol, is what carries those frames over a socket the proxy and daemon hold open permanently, so there is no connection setup or teardown to pay for. Neither one is the other, and the deep-dive recording separates them explicitly.

**WATCH** **The Proxy and the Daemon** — the proxy, the daemon, the permanent socket, and byte accounting on a live run

**WATCH** **Semantic Message Handlers** — adding a format the codec does not speak yet — two functions, no schema

### **8. Serial vs. Parallel — Physics as the Only Constraint**

Traditional request/response networking is serial at its core. A request goes out. The sender waits. The response comes back. The next request goes out. Even with pipelining — which overlaps the sending of requests — processing remains sequential and the wire sits idle between transactions.

On a constrained link, idle time is catastrophic. Every microsecond of dead air is bandwidth you cannot recover. A 4800-baud link is already at the edge of what's usable. Wasting half of it on dead air between serial transactions makes it unusable.

FrogNet eliminates the dead air entirely.

A pool of worker threads means many requests are in flight simultaneously — not sequentially overlapped, but genuinely parallel, each on its own thread, each with its own database connection, all interleaved onto the one standing socket. By the time the wire finishes transmitting one frame, the next is already queued and waiting. The transmitter never starves. The link runs at or near 100% duty cycle continuously.

There is a second effect that matters as much as the first, and it only becomes visible under load. When many callers ask for the same thing at once, the proxy notices that an identical request is already in flight and makes the later callers wait for the answer the first one is going to get. The daemon does the same on its side: one execution against the local web server, one answer, distributed to everyone waiting. Three hundred simultaneous requests become one round trip and one execution. That is why the burst figures in the measurements below are not simply the serial figures multiplied.

The result is that effective throughput is not limited by processing speed — it is limited only by physics. You cannot move bits faster than the channel allows. FrogNet makes sure every bit the channel allows is carrying useful payload.

**CROAKUS** Chapter 41, *Measuring It on Your Own Box* — the pipe_workload tool, its four phases, and why the echo workload is the ceiling rather than the typical case.

### **9. Networked Intelligence — Distributed AI with Shared Real-Time World State**

Every AI deployment ever built follows the same model. Data is collected, sent to a central location, processed by a model, and results are returned. The AI lives in the cloud. The sensors live at the edge. The network is the pipe between them. The AI is smart but blind — it can only see what gets sent to it, with whatever latency the pipeline introduces.

This model has three fundamental problems. It requires connectivity. It introduces latency between observation and action. And it concentrates intelligence in one place, which means a single point of failure, a single point of compromise, and a single point of control.

FrogNet dissolves all three.

Every node can run its own AI. Every AI has direct, real-time, read-write access to FrogNet Memory — which means every AI can see every sensor on the entire mesh, not just the sensors on its own node. Multiple AIs can run simultaneously on different nodes, observing the same data, writing to the same scratchpad, coordinating through shared memory without any message-passing infrastructure, without any API contracts between them, without any orchestration layer. They share a world. They act in it.

Multiple dashboards can do the same thing — any node, any browser, any connected device sees the same real-time network state simultaneously. There is no master dashboard and no replica. There is one shared memory and every observer reads it directly.

The sensor-to-actuator loop — sensor writes a value, AI reads it, AI writes a control value, actuator reads it — runs entirely within the mesh. It survives complete internet loss. It survives the loss of any individual node. It survives network partitions, because each fragment continues operating on the sensors it can reach. The first independent end-to-end demonstration of that loop was completed on April 17, 2026 — a button press in a web UI on a WiFi network in New York, routing through shared memory in Seattle, activating a physical actuator back in New York.

The traditional model puts intelligence in the cloud and data at the edge, connected by a fragile pipe. FrogNet puts intelligence at the edge with the data, connected by a mesh that heals itself. That is not an improvement on centralized AI. It is a different theory of where intelligence lives in a network.

### **10. Network as Memory — Data in the Network, Not Behind It**

Every distributed system ever built treats the network as a pipe — data lives in databases, the network moves it between them. You have servers with databases, clients that query them, and a network that connects them. The database and the network are separate concerns.

FrogNet collapses that distinction. FrogNet Memory isn't behind the network — it is the network. Every node reads and writes it. Every sensor writes to it. Every dashboard reads from it. Every AI queries it and writes back. There is no client-server distinction because there is no separate tier for data storage — the data is in the network itself, always available, always current, always at the same name regardless of which physical node is holding it at any given moment.

The addressing model is Linda's tuple space, developed by David Gelernter and Nicholas Carriero at Yale in 1985: a value is located by a small number of coordinates, and any program that can name those coordinates can read or write it without knowing who else is doing the same. FrogNet uses three coordinates. That is FrogNet's convention rather than a requirement of Linda, and it is the right size for the job: a sensor name, a sensor type, and a location will identify a reading; a program name, a variable, and a user identifier will identify a shared variable between two copies of a program on opposite sides of a continent.

Underneath, the store is an ordinary relational database on the node currently holding the role, and the tables are visible to anyone who wants to open them and look. That is deliberate. Shared memory that cannot be inspected acquires magical properties in the reader's imagination, and there is nothing magical here — there is a table, there are rows, and the reason so little crosses the wire is that almost nothing in a row changes between one write and the next.

**CROAKUS** Part VI, *The DatabaseHost*, and the appendix *REST, the Drop-In, and UnREST*. A note on names: what earlier drafts called the *transient database* is called *FrogNet Memory* throughout the current material. The mechanism did not change; the name stopped describing it as a database, because programs use it as memory.

**WATCH** **Intro to Tuples** — three coordinates, and a two-player game lobby with no messages in it

**WATCH** **The FrogNet Monitor** — the same record read from two nodes on opposite sides of the continent

### **11. Living Topology — Derived from Reality, Never from a Plan**

Every network you administer has a topology you designed, documented, and maintain. Subnets are planned. Routes are configured. VLANs are assigned. When something changes you update the documentation, push new configs, and hope nothing breaks. The network is a designed artifact that requires human maintenance to stay consistent with reality.

FrogNet's topology is never designed and never documented because it doesn't need to be. It is calculated fresh from observed reality every time anything changes. Routes are written to match whatever interfaces are actually up. Hosts files reflect what the node can actually reach. The network's representation of itself is always derived from ground truth, never from a plan that may have drifted from reality.

Two details keep that from being romantic. First, the service roles are not assigned by address. Every node writes its own capability — cores, memory, storage, whether it is running the database, what encoders it has — as a tuple into a control plane that sits at a deterministic, arithmetic address, so that every node reads the same inputs from the same place without having to know the answer first. Then every node runs the same scoring against those same inputs and arrives at the same winner independently. Nobody is told. Nobody votes. The election is a pure function of facts everyone can already see, which is why it produces the same answer on every node and why it is safe to run it again the moment anything changes.

Second, recalculating is not the same as churning. A route that is already installed and whose destination is alive and healthy over its own path is left alone, even when a challenger measures faster. Re-ranking on every walk would move routes whenever two paths measured close together, and every move tears down live traffic — an open database connection, a call in progress, a transfer halfway done. Discovery state starts clean on every merge; installed routes do not.

Network engineers spend enormous effort keeping designed topology consistent with actual topology. FrogNet eliminates that gap by never having a designed topology in the first place.

The clearest day-to-day consequence is something administrators of conventional networks would call impossible: changing what a node is connected to without editing the node. A node's picture of its region is not configuration it was handed; it is derived, every time, from what the region's members are saying right now. When a member joins, leaves or moves, the region raises one event, and each node's daemon diffs the new state against the old, tears down what's gone, brings up what's new, and is on the new network. No script ran. No configuration file was edited. Admission is one record on the region host (`fdf-invite NAME`; `--revoke` removes it and the hub drops the peer at once), and a node that belongs to more than one region brings one forward with `fdf-forward AREA` — the daemon withdraws the old pond and joins the new one in place, with no restart. (Until 12 October 2026 this paragraph described the broker and the node's poll of it; the region has no poll — the node holds a session and is told.)

**CROAKUS** Chapter 15, *Selecting the Service Hosts*; chapter 17, *Two Names, Two Planes — Why one is elected and the other is not*; and chapter 14 for the route-incumbency rule and the worked example behind it.

**WATCH** **Installation, Part 2 — First Boot** — a node booting knowing nothing, and finding the network

### **12. Security — Architecture Instead of a Perimeter**

Every traditional security model assumes a hostile perimeter. The internet is outside, attackers are out there, and you build walls to keep them away from your services. Every service behind those walls still needs its own authentication layer because the perimeter is assumed to be already breached.

FrogNet's position is different, and the precise version is the one that survives a scan.

There are no discoverable application endpoints. The applications on a FrogNet are not published to the internet, do not advertise themselves, and are not reachable from it. To reach them you have to be on the mesh, and to get on the mesh you have to be physically near it — breaking the WiFi, taking the hardware, being in the room. That is not a firewall rule. It is a property of where the services live.

There is one thing on the internet that answers, and pretending otherwise would be dishonest. A node getting its first tunnel cannot ride a tunnel that does not yet exist, so the region host has an address and a port that accept a packet: the hub's WireGuard port, UDP 50889, and nothing else of the region's (the RAM manager, TCP 51888, listens only inside the tunnel). The address is chosen by the operator — not a well-known service, not a registered name — so there is nothing to enumerate toward. And a WireGuard endpoint does not answer an unauthenticated probe at all: scan it and you learn nothing is listening. The node's first packet to the internet is a handshake with the key its invitation carried; the region's records, facts, mappings and held reads all travel inside the tunnel that handshake opens. Confidentiality across the internet comes from WireGuard. (Until 12 October 2026 the broker answered here on an HTTP port carrying FNWP-1 frames; that port is gone with it.)

Network membership is the trust boundary, and membership is not address space. Being a 10-network machine is not enough — a candidate has to answer the liveness gate when discovery calls, and one that does not never becomes a neighbour and never gets a route. This is why FrogNet can be inserted into a corporation that already lives in 10-space without anyone renumbering anything.

The practical consequence is that the complexity, latency, and overhead of PKI, TLS handshakes, session tokens, and credential stores are not required at the application layer — overhead that can consume twenty-five seconds of a 4800-baud link just for a certificate chain. A node is also an ordinary Linux machine, so every conventional tool you want on top of that is available: firewalls, certificates, key rotation, whatever your policy requires.

The honest summary is that the vulnerabilities of a FrogNet are largely human. Social engineering, a shared WiFi password that never gets rotated, a machine left logged in. Those are the attacks worth defending against, because the architecture has already taken the remote ones off the table.

**CROAKUS** The Security part — chapters 35 through 38, beginning with *Nothing on the Internet Answers*, which records the scan of the broker that answered there until the region replaced it on 12 October 2026.

### **13. Memory, Not Messages — The Network Is a Shared Memory You Read and Write**

This is the shift that underlies the other twelve, and the one that took the longest to see — because it is the hardest to unlearn. Every networked system in common use is built on messages: one party sends, another receives, and the entire discipline of networking is the management of that exchange — requests, replies, acknowledgments, retries, sessions, subscriptions, ordering, delivery guarantees. FrogNet does not send messages between applications. It exposes a shared memory, and the only two things a program does are write a value where it computes one and read a value where it needs one. There is no sender and no receiver, because there is nothing in flight — there is only memory that several machines can see, and which is kept current by FrogNet beneath the application without the application orchestrating the exchange.

Be precise about the scope of that claim, because the precise version is stronger. REST does not disappear. The call a program makes to read or write shared memory is an ordinary HTTP call to an ordinary endpoint, and underneath it the proxy, the daemon, and FNWP-1 are moving bytes. What disappears is the part programmers actually spend their lives on: designing a message format, writing a handler on each side, sequencing the exchange, handling the retry, versioning the protocol when the fields change. A C compiler did not abolish assembly language; it wrote the assembly for you and moved the surface you think at. This is the same move, applied to the network.

The distinction is not cosmetic. Most systems that claim to simplify networking only hide the message behind a nicer surface — remote procedure calls, message queues, web APIs all still have a send and a receive in the programmer's model, and that model reasserts itself the moment something fails. FrogNet removes the category from the programmer's model entirely. There is no degraded message path waiting to leak through, because at the level where you are working there is no message.

The elegance is in what the shift lets you stop writing. A programmer describes a piece of shared data with a few declarations: what its fields are, how fresh each field must be, where authority over it lives if anywhere, and what a received value means. From those declarations, an entire category of hand-written code simply ceases to exist — the retry loops, the version counters, the acknowledgment handling, the deduplication, the jitter buffers, the backpressure protocols. In the adaptive media system, "video gives way before audio on a failing link" is not an algorithm anyone wrote; it is two freshness declarations, one marking audio as the protected stream and one marking video as droppable. The hard behavior became a property you declare once rather than logic you build and debug.

And because it is one mechanism, it spans the entire system with no seams. Presence, a multiplayer game, a sensor-fusion loop, a live video call, a control setpoint driving hardware — all of them are the same handful of declarations over the same shared memory. In a conventional stack each of those is a different subsystem with its own protocol: a publish-subscribe bus, game netcode, an industrial control bus, a media stack, a messaging layer, each with its own failure modes and its own glue. Collapsing them onto a single coordination model is what makes possible the programming that was previously out of reach for a small team — not because any one of those capabilities was impossible elsewhere, but because composing all of them on one substrate, without an army of integration code, was.

There is an honest boundary that is itself part of the design, and naming it is what keeps the claim credible. A shared memory of this kind gives you bounded-stale current state — the best available answer right now — but it does not give consensus, a total ordering of all writes, or atomic changes across many values at once. FrogNet does not pretend otherwise. For the few things that genuinely require an order — whose turn it is, who holds a lease, which of two commands won — it elects a single authority and routes those decisions through it. The discipline is simple to state: design for merge, elect for order, and never let the shared picture pretend to be the order. The reason the model is so light is precisely that it refuses to impose the heavy distributed-systems guarantees on the overwhelming majority of coordination that never needed them. Treating all coordination as if it required consensus is the original mistake that made conventional networked software so heavy; declining to make that mistake is as much a part of this shift as the shared memory itself.

**CROAKUS** Part *The Programming Surface* — chapters 40a through 40f, which take this shift apart properly: fifty years of moving the line, what distributed systems missed, and the test that settles it.

## **What FrogNet Is and Does**

The following is a description of FrogNet's architecture, capabilities, and measured performance as of August 2026.

### **Network Foundation**

- Transport-agnostic fabric — WiFi, Ethernet, WireGuard, 900 MHz HaLow, LoRa, narrowband RF, any combination
- Self-forming — nodes discover each other automatically, no configuration required
- Self-organizing — topology managed without human intervention
- Self-healing — network fragments operate independently and rejoin without reconciliation
- Network-to-network architecture — not device-to-device; entire networks federate
- No central authority — no company, no cloud, no administered namespace. A connected FrogNet does elect one node to serve Memory; when that node goes, the network elects another, which is a recovery rather than an absence
- Internet-optional — uses the internet when available, operates fully without it
- Federation — even very low-speed links cause independent networks to join and behave as one system

### **Semantic Compression Engine — BLDC-1 and FNWP-1**

- BLDC-1, the Bullfrog Long Distance Communications Protocol — decides the smallest honest representation of an exchange: SAME, DIFF plus a map, or FULL when no template exists yet
- FNWP-1, the FrogNet Wire Protocol — carries those frames over a permanently held socket pair between a proxy and a daemon, so no connection is ever established or torn down
- Handlers for HTML, XML, JSON, CSV and plain text ship with the engine
- Extensible by registering a type and supplying two functions — bytes in, bytes out; no schema, no negotiation, no application change
- Template learning — the network learns the structure of real traffic automatically
- An unchanged exchange collapses to a twenty-one-byte frame
- Coalescing — identical requests in flight are answered once and the answer distributed to every waiting caller, in both the proxy and the daemon
- Applied at the infrastructure layer — zero application changes required
- REST is the native protocol; coordination and data ride the same compressed transport

### **Measured Performance**

These are figures read off narrated runs on the live network, not a tuned rig. Where a measurement is a best case, it is labelled as one.

| Measurement                      | Result                                                               | Conditions                                                                                                                                                                           |
|----------------------------------|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Live HD call over radio          | 1280×720 at 22 fps, bidirectional, no drops, 89% of bytes never sent | 900 MHz HaLow link with a 1 Mbit ceiling, Seattle to New York. The 89% is a did-versus-would estimate against published WebRTC bandwidth for the same session, not a packet capture. |
| Call held under starvation       | audio unbroken at 450 kbps; picture thins, conversation continues    | bandwidth driven down by hand from 987 to 637 to 450 kbps                                                                                                                            |
| Repeated identical exchange      | 1.0 KB sent where 39 KB would have been                              | echo workload — the best case, and named as one                                                                                                                                      |
| Mixed exchange, ~20% new content | 6.4 KB sent where 414 KB would have been                             | chat workload, 25 requests, serial                                                                                                                                                   |
| Large structured payload         | 38 KB sent where 347 MB would have been                              | megabyte workload, 300 concurrent, six dynamic fields                                                                                                                                |
| Throughput on a constrained link | 44× improvement                                                      | Ethernet shaped to 4800 baud with added jitter and 20% loss                                                                                                                          |

The two figures worth understanding together are the last two. The wire saving comes from BLDC-1, and it is enormous on structured data because structured data is mostly structure. The throughput improvement comes from never letting the link go idle, and from answering a repeated question once instead of three hundred times. They are different mechanisms and they compound.

**WATCH** **Proof It Works** — the 900 MHz call, with the bytes-saved counter on screen

**WATCH** **Bandwidth Experiments** — the link starved by hand while the call stays up

### **Node Architecture**

- Each node is a complete sovereign network
- Runs a local web server, a local database, DHCP and local name service
- Operates fully standalone with zero connectivity
- Meshes automatically when other nodes come into range
- Standard hardware — Raspberry Pi, x86, laptops, anything that runs a modern Linux; Debian-family is what the installer is tested against
- Python, PHP, bash — no exotic dependencies
- Remains a general-purpose computer: no image to flash, no firmware, nothing to commit — install it and use the machine for whatever else you want

**WATCH** **Installation, Part 1** — the installer run end to end, prompts and all

### **Pond and Chorus — Scope as Architecture**

FrogNet's scope hierarchy answers a problem that conventional networking solves with much heavier machinery: how do you segment a network without designing the segmentation in advance, and how do you change membership without reconfiguring the members?

Four scopes, nested:

- **Self** — the node itself. Everything it can do standalone with no other node reachable. Local web server, local database, local AI, local sensors and actuators. This is the offline-first baseline; every other scope is an enhancement.
- **Lillypad** — the node plus everything directly attached to it. The sensors on its LAN, the boards talking WiFi or Bluetooth to a worker hanging off the node, the laptops and phones using it as their gateway. The lillypad is what an operator thinks of as "their FrogNet" in the small.
- **Chorus** — a visibility and routing group inside a pond. Nodes in the same chorus can reach each other; nodes in different choruses on the same pond cannot. Membership is dynamic — a node can be in many choruses at once. Choruses can be public or invisible. A chorus is the right shape for a task force: a named group that needs to see each other, formed and dissolved by membership change rather than reconfiguration.
- **Pond** — the full network. Across sites a pond is a region: it has a name (its area), a region host, and a membership — the members whose sessions are up. A node joins by invitation: `fdf-invite NAME` on the region host prints one line, and the node is installed or joined with it. (Choruses went with the broker on 12 October 2026.)

The region host keeps the membership — who is admitted, and whose session is up — and it carries the inter-site data path: every cross-site packet transits the tunnels its hub terminates. That is a stronger claim than “the region host is hard to attack,” so assume it is owned. Two things still hold. It holds no member's private key and no pond secret, so it cannot mint a token a member will accept (it does hold the site key, which signs the mappings its own RAM manager accepts). Inter-site traffic is in the clear at that seam, so assume the region host can see what it carries. It runs none of the semantic stack, and forwards in the kernel, so nothing at the application layer parses or stores the payload. And it is not itself a FrogNet node. Its picture of the region is its members' sessions — a member's facts go when its session ends, and come back when it does. A pond whose region host goes dark loses every cross-site link, because they all end there; each node withdraws what the region gave it, keeps its own LAN running, and asks again every five seconds until the region answers. Owning the transit point gives you the transit point, the site key, and nothing else.

Two consequences fall out. Membership is data, not configuration — admitting or removing a node is one record on the region host (`fdf-invite NAME`, `fdf-invite NAME --revoke`), because there is no config to push and the hub acts on the record at once. And sensors and actuators live in lillypads while their data lives in the pond: a board reading a light level writes into shared memory through its host node and becomes visible across the entire pond immediately.

**WATCH** **Standing Up a Broker** — a broker stood up from nothing, and a pond created (the broker era; the region replaced it on 12 October 2026 — see [Run a region host](https://fawcettinnovations.com/broker.html))

### **FrogNet Memory**

The design lineage traces to the real-time data buses used in commercial aviation, specifically ARINC 429 — the broadcast bus that has carried flight-control, navigation, and instrumentation data on commercial aircraft for nearly five decades. ARINC 429 is simple: no store-and-forward, no reconciliation, no message addressing. A transmitter broadcasts current state, and any subscriber that needs a given parameter reads it. There is no notion of delivery, because the bus is always carrying the current truth — if you missed it, the next sample is on its way.

FrogNet Memory is that idea projected onto a network. Any node writes its current state. Any reader sees it. No history, no reconciliation on rejoin after a split, no delivery semantics to negotiate. Whether a value is too old to use is the reading application's decision and not the fabric's — the fabric's job is to make the current value available and to say when it was written.

- Lives on the node elected to hold the role, and is reached by name rather than by address
- The role is elected on capability, from facts every node reads out of a deterministic control plane — not by address, and not by vote
- Always available anywhere on the mesh, at the same name, regardless of which node is holding it
- Stores values as JSON against three tuple coordinates — any sensor, any application, any type
- Intentionally ephemeral — no reconciliation on split or rejoin, which matches the physics
- A network-wide scratchpad visible to every node simultaneously
- Supports real-time dashboards and AI inference at the same time, with no master and no replica

**A NOTE ON NAMES** The tables underneath are called *sensors* and *sensor_data* because FrogNet Memory began as a place to put sensor readings. They are the tuple tables for everything — a game lobby, a call roster, a program's shared variables. The name is legacy; the mechanism is general. Anyone reading the API will meet that within a minute, so it is said here rather than left as a surprise.

### **AI Platform**

- Local inference — no cloud, no backhaul required
- Reads live sensor values from shared memory and writes control values back the same way
- Sensor-to-actuator loops operate entirely offline
- Behaves identically with or without internet
- Multiple AIs run simultaneously across nodes, sharing the same world state
- Multiple dashboards see the same real-time data simultaneously

### **Sensor and Actuator Platform**

- Sensor and actuator boards via Bluetooth, WiFi, or Ethernet
- Small concentrators aggregate and write into shared memory
- External mesh radio technologies integrate as data endpoints — their RF bridges write directly into shared memory
- FrogNet can also ride those same mesh radio bearers as its transport
- Any sensor type — environmental, health, telemetry, structural, agricultural
- Bidirectional actuator control validated end to end on April 17, 2026 across a 2,400-mile WAN
- Real-time ingestion with immediate mesh-wide visibility

### **Region and Tunnel System**

- Carries the inter-site data path — cross-site traffic transits the tunnels its hub terminates — while holding no member's private key, no pond secret and no application state; traffic is in the clear at that seam

&nbsp;

- Runs on any machine every member can reach — a small cloud instance, a corporate server, or a box on your own LAN; a region needs no internet
- One host runs it: the RAM manager (`rina-host`, TCP 51888, inside the tunnel only) and the WireGuard hub (`fdf-hub`, UDP 50889); `fdf-region-create AREA PUBLIC_IP` makes both
- Members are admitted by invitation: `fdf-invite NAME` prints one line, the node joins with it; `--revoke` removes a member's peer at once
- One WireGuard tunnel per member, to the hub; one edge address per member, 64,008 per region
- Replaced the broker on 12 October 2026; the broker's pond and chorus administration has no counterpart

### **Security Model**

- No discoverable application endpoints, no exposed application protocols, no remotely reachable application interfaces
- Reaching the applications means being near them — on the mesh, which means physically in range
- Membership is the trust boundary, and membership is not address space: a candidate that does not answer the liveness gate never becomes a neighbour
- One port answers on the internet, on a number the operator picked, so a node getting its first tunnel has something to call
- What crosses that connection is FNWP-1 carrying HTTP semantics, not stateless HTTP — a capture yields codec frames, which is a statement about the wire and not a confidentiality claim
- WireGuard provides confidentiality across the internet; its endpoint does not answer unauthenticated probes
- Standard Linux underneath — the full ecosystem of security tooling is available to anyone who wants it
- Signed script distribution across the mesh, with trust pinned on first contact
- Not deployed on amateur radio bands — FCC Part 97.113(a)(4) prohibits obscured communications, and template-based compression is indistinguishable from a cipher to a listener without the templates. A deliberate constraint, not a limitation.

### **Wire Protocol — FNWP-1**

- Opcodes for the three codec states and the exchanges around them — FULL, DIFF, SAME, and the request and reply forms of each
- Length-prefixed frames with a sequence tag, carried on the standing socket
- Interleaved across a worker pool so many exchanges are in flight at once on one connection
- Media rides the same wire under its own codex, with audio protected and video droppable

### **Management and Operations**

- frognet_monitor — real-time view of every node, its services, and its sensors, in terminal and browser forms
- Force a merge, restart a daemon, or join, forward and leave a region from the command line on any node (`fdf-region-join`, `fdf-forward`, `fdf-leave`)
- Re-identify a node in place without reinstalling it
- Unified logging with selectable level across proxy, daemon, and tunnel daemon
- A complete simulator that runs the real code against a simulated environment, with an oracle suite as the gate

### **Applications Built on the Platform**

- The Communicator — the reference application: presence, call setup, and adaptive audio and video, with the entire control plane in shared memory and not one message sent between the participants
- FrogChat — a single-file chat application with no dependencies, running in any browser
- FrogNet Family — mobile client with GPS presence, chat, and notes
- Real-time GPS presence map, visible mesh-wide
- Family calendar, shared photo gallery, wiki, and file sync — all self-hosted
- Wellness monitoring — passive activity signals and anomaly detection for elder care
- Emergency broadcast — mesh-wide alerting that works without internet
- Board games, as the worked example of a handler with its own rules and its own authority

### **Proven in Production**

- Nodes across Seattle and New York, over Ethernet, WiFi, WireGuard, and a 900 MHz HaLow link
- First independent end-to-end closed-loop demonstration completed April 17, 2026 — a web UI in New York driving a physical actuator in New York, with all coordination passing through shared memory in Seattle, 2,400 miles away
- Live bidirectional 1280×720 video call at 22 fps over a 1 Mbit 900 MHz radio; the counter reported 89% of bytes never sent, a did-versus-would estimate rather than a packet capture
- A call held through deliberate starvation to 450 kbps with audio unbroken
- Sub-two-second transaction round trip over a 4800-baud link with 20% loss
- Mobile HaLow link validated through dense urban driving in Queens, New York, with no observed link drops
- Emergency-management coordination at emergency operations centres, over WiFi, LoRa, and WireGuard
- TRL 6–7

### **Intellectual Property and Licensing**

- Three issued US patents — storage virtualization, pop-up networking, and a physical product
- The pop-up networking patent is a direct architectural precursor to mesh formation — lineage, not just a filing date
- A dozen or more FrogNet inventions have deliberately not been filed. That was a decision, not an oversight: the spec is meant to be open and so is the engine, and a thicket of filings would work against both
- FrogNet is free software under version 2 of the GNU General Public License
- Free for any use — personal, hobby, educational, research, production, commercial
- The one condition is copyleft: what you distribute carries the GPL
- NSF Phase I SBIR invitation in hand — Wireless Technologies topic
- CAGE code 1A5Y5, SAM registered, full awards authority

## **Appendix A: How FrogNet Handles Emergencies**

These scenarios were referenced in the opening section and are reproduced here for readers who want concrete examples of how FrogNet performs when conventional infrastructure fails. Each one is a deployment pattern the current system supports today, not a future roadmap.

### **Summer Camp**

Your twelve-year-old is at a camp in the mountains. There is no cell service and barely any electricity. The camp does not have commercial internet, but the ranger's station has a long-range RF link to a gateway at the trailhead, and the trailhead has a satellite uplink that comes up for a few hours each evening.

The camp has a FrogNet box. So does your house. For most of the day, the camp's box operates on its own — a local network inside the camp for the staff and kids. When the evening uplink comes up, the camp's box and yours can reach each other again, and each side can read what the other has been writing all day. Your kid's check-ins, their photos, their GPS dots on the family map — all of it arrives in a single batch that the semantic compression engine sends over the narrow link in a fraction of the bandwidth it would normally take.

You leave a sticky note that says "Grandma says hi." The camp's box picks it up the next evening. Your kid sees it when they wake up.

Nothing goes through any company's servers. No cell service is required at the camp. The link between camp and home is a few hours of narrowband a day, and FrogNet treats that as perfectly sufficient — because for this kind of family communication, it is.

### **The Ambulance**

A paramedic crew responds to an emergency. They load the patient and begin treatment. Between the scene and the hospital, they pass through areas with no cell coverage. In a conventional system, the data they are collecting — vitals, treatment notes, patient status — cannot reach the hospital until they drive back into cell range. The emergency room gets no warning, no preparation time, no head start on the case.

With FrogNet, the ambulance has a box. The hospital has a box. The boxes connect through whatever path is available — cellular, WiFi along the route, a commercial narrowband data radio, or a relay. Patient data flows to the hospital in real time when connectivity exists. When it does not, the ambulance's box holds everything locally and transmits the moment a link reappears. The ER sees vitals updating as the ambulance approaches. They are ready before the doors open.

This is not hypothetical architecture. The semantic compression engine was designed to deliver full web applications over links as narrow as 4800 baud — slower than a 1990s dialup modem — and has been validated at that rate. On a steady-state feed like patient vitals, most of each update is structure that has not changed, so almost nothing crosses the wire. That is small enough to send over almost anything that carries IP.

### **The Disaster**

A hurricane hits the Gulf Coast. Cell towers are down. Power is out. The internet does not exist. First responders from six agencies converge on the area with no way to coordinate because every system they use depends on infrastructure that is no longer there.

Each FrogNet box is a sovereign network. Drop one at the command post, one at the field hospital, one at the staging area, one in each search-and-rescue vehicle. They mesh automatically over WiFi when they are near each other. When they are out of WiFi range, they connect by whatever IP-capable radio is in the truck. Sensor data from the field flows into a shared memory every node can see. An AI running on one of the boxes watches the feeds and flags anomalies. Dashboards at the command post show real-time positions of every unit.

None of this requires the internet. None of it requires a cell tower. None of it requires any infrastructure the hurricane could take out. The FrogNet is the infrastructure, and it came in the back of a truck.

The network grows. When two independent FrogNets come within range of each other, they discover each other and merge into a single, larger network — automatically, with no human intervention. A rescue team from Texas arrives with their own FrogNet, drives within WiFi range of the command post, and the two networks see each other, exchange what they know, and become one. Every node on both sides can now reach every other node.

The same merging works across different transport types simultaneously. The command post connects to the field hospital by Ethernet. The field hospital connects to a remote unit by narrowband radio. A supply convoy connects through a satellite uplink. From the application's perspective it is all one network. The applications do not know and do not care whether the bytes travel by wire, WiFi, radio, or satellite.

### **Small Business, Farm, and Other Everyday Uses**

A small business with three locations and ten remote employees replaces its cloud suite with FrogNet boxes at each office and each home. Internal files, messaging, a company wiki, and sensor monitoring at the warehouse — all running on their own hardware, all private, no monthly per-seat fees, no corporate data passing through anyone else's servers.

A family farm deploys sensor nodes in the fields and a FrogNet box at the farmhouse. Soil moisture, temperature, humidity — all flowing into a local dashboard through an RF bridge. A local AI makes irrigation recommendations. None of this requires cell coverage in the field. None of it requires a cloud subscription to view your own sensor data.

And a network that already exists does not have to be replaced to get any of this. A FrogNet node inserted just below the point where a site reaches the internet puts the existing machines on the mesh without an agent on any of them — the printer, the badge reader, the controller nobody will touch. They keep their addresses, their gateway, their firmware. What changed is the box their traffic already passed through. It is not a migration. It is an insertion.

## **Appendix B: The Song of the Frogs — Communication That Does Not Stop**

SotF-ACP — the Song of the Frogs Adaptive Communications Protocol — is the media plane of the Communicator, and it is described separately for two reasons.

The first is the protocol itself: a communications protocol that does not stop, built for the case that matters most when everything else fails — two people trying to talk across a link that is degrading toward zero.

The second is broader. SotF-ACP rides an extension point in the semantic compression engine that allows entirely new wire protocols to be plugged in as infrastructure, with no application changes, no schema declarations, and no per-endpoint configuration. SotF-ACP is the first non-trivial use of that extension point. Its story is therefore also the story of a generic capability with implications well beyond adaptive media.

Conventional communication has two states: working, or dropped. A video call is up, or it ends. A voice call is up, or it ends. A text message goes through, or it queues forever waiting for connectivity that may never come. The user experiences a binary outcome.

SotF-ACP has eight.

### **The Idea**

If you have ever stood by a frog pond at dusk, you have heard it. One frog calls. Another answers. A third joins. Within seconds the pond is a chorus of dozens of voices, overlapping, layered, continuous. The song is the multitude.

Take any one frog out. The song continues. Take most of them out, and the song continues quieter. Take all but one, and that one still sings — and if anyone is listening, the song is still there. The medium changes. The meaning does not.

That is the shape SotF-ACP is built around. Two users have a conversation. The protocol underneath decides, in real time, what shape of message the current link can carry — full bidirectional video at one end of the spectrum, a single byte-scale heartbeat at the other — and renders it appropriately on the other end. The users experience one continuous conversation. The wire carries whatever it can. When the link improves, the protocol climbs back up. When it gets worse, it slides down. Neither end has to know what level the other is at.

### The Nine Rungs

The ladder spans roughly six orders of magnitude of bandwidth. Every rung has a name, because on a live network you end up saying them out loud. The four video rungs need a camera and nothing else — a webcam with no microphone is a perfectly reasonable thing to be. The two audio rungs need a microphone. The bottom three need no hardware at all, which is why the floor of a call is never the floor of the network. The rungs, from richest to leanest:

- **L8 — Bullfrog.** Mono audio + 1080p colour video. The top rung. Added above CHORUS for links that can carry it — and nothing decides whether they can except sending frames and watching.
- **L7 — Chorus.** Mono audio + 720p colour video. The link is healthy.
- **L6 — Ensemble.** Mono audio + 480p colour video. The picture is smaller; nothing else has changed.
- **L5 — Duet.** Mono audio + 360p grayscale video. Colour goes before the picture does.
- **L4 — Solo.** Mono audio (video shed). The top rung that carries no picture, and the rung a machine with no camera starts on.
- **L3 — Voice.** Mono audio. Speech intact.
- **L2 — Whisper.** Plaintext text. Real-time typed exchange; the conversation becomes written.
- **L1 — Beacon.** Token vocabulary. Pre-defined short tokens — OK, wait, help, no, yes, moving, stopped. A handful of bytes per exchange.
- **L0 — Pulse.** Presence. A single periodic byte that says only: I am here, I am listening, I am alive.

Even at L0 the connection is not dropped. It is the smallest possible song. If anything more is possible, the protocol climbs back. If even L0 fails — total link loss, full jamming, the radio physically destroyed — that is the only condition under which the conversation actually ends.

One detail of the ladder is worth stating because it is counter-intuitive and it is deliberate: frame rate steps before geometry. A picture that updates less often is easier to watch than a picture that has gone small, so the rate comes down first and the resolution only follows when the rate has run out of room.

### **How This Differs from Every Other Streaming Protocol**

Every real-time media protocol in widespread use — RTP, WebRTC, SIP, the voice and video stacks that ship in every consumer app — handles a degrading link the same way: fire packets into the network as fast as you can, hope most of them arrive, and patch up the wreckage on the receiving end. UDP is the substrate. Packets are independent. The sender does not know which ones got through. The receiver assembles whatever shows up, runs jitter buffers and packet-loss concealment over the gaps, and renders the result. When the link is good this works well. When it degrades, the receiver renders the corruption: blocky video, audio that warbles and drops out, partial frames sewn together from packets that arrived and packets that did not.

Call it the shotgun approach. The sender sprays. The receiver salvages.

SotF-ACP inverts every part of this.

The substrate underneath is TCP — reliable, in-order delivery. Frames arrive complete or they do not arrive at all. There is no such thing as half of a frame reaching the receiver. There is no packet-loss concealment, because there is no packet loss; there is only frame delivery or frame omission, and both are clean.

This is the part that sounds wrong to anyone who has built a media stack, and the objection is a good one: a large keyframe on a slow link will block the socket, and while it blocks, the audio stops. That is exactly what happens if you hand a whole keyframe to the socket. So the Communicator does not. It owns the wire, so it breaks a keyframe into segments, sends them one at a time, and interleaves audio between them. The receiver reassembles the keyframe and paints it when it is whole. That is why audio survives at bandwidths where the picture has nearly stopped — the frame that would have starved it is no longer a single indivisible write.

The decision about what to drop is made at the sender, on evidence it already has: the operating system telling it the outbound buffer is filling, the frame rate it is actually achieving, the drops it is seeing in a one-second window. Whole frames are dropped before they cross the wire — not pieces of frames. The receiver never has to know.

The user-visible effect on a degrading call is therefore not corruption. It is a lower frame rate, then a smaller picture, then audio alone — each a clean rendering of the same conversation at a level the link can carry. A brain can fill in a missing frame but cannot unscramble a half-pixelated one. It tolerates audio that pauses and resumes; it fights audio that warbles and clicks. Conventional protocols deliver exactly the degradation human perception handles worst. SotF-ACP delivers the kind it handles best.

Coming back up is deliberately slower than going down. A step down is taken on the first good evidence, because the cost of staying too high is a call that breaks. A step up requires several consecutive clean seconds, because grabbing the bandwidth back all at once is how you lose it again. Roughly seconds to fall, roughly fifteen to climb a rung. That asymmetry is the design, not a limitation of it.

### **What Makes This Possible**

SotF-ACP is not a clever protocol bolted onto a fragile stack. It is the direct beneficiary of everything in the main body of this document.

- **Transport abstraction (shift 1)** means SotF-ACP does not care what carries its frames. A rung can fall back to a narrowband bearer the moment WiFi dies, then climb when WiFi returns.
- **Offline-first (shift 2)** means presence at L0 is not a special offline mode — it is the lowest rung of the same protocol.
- **Semantic compression (shifts 6 and 7)** is what makes L2 and L1 possible at bandwidths where conventional protocols pass nothing at all.
- **Parallel processing (shift 8)** keeps the wire full. Even at low rungs the link runs at near-total duty cycle.
- **Memory, not messages (shift 13)** is what makes the coordination free. Neither end asks the other for anything.

That last point deserves precision, because it is the one people assume must be wrong. There is no negotiation between the endpoints about what rate to run. Each participant publishes what only it knows — a sender what it is sending, a receiver what it is getting and whether it is keeping up — and each participant independently derives the same rate from those published values. No request, no reply, no acknowledgment, no round trip anyone waits on. The rate is not agreed; it is derived, in the same way and from the same shared memory as everything else in FrogNet. This is the hardest real-time problem in the system, solved with the same mechanism as a chat message.

### **Beyond SotF — The Custom Protocol Architecture**

SotF-ACP was possible to build because the semantic compression engine is not a single fixed codec. It is a host for wire protocols, and BLDC-1 is the default one that rides it. The extension point BLDC-1 uses is the one SotF-ACP uses. Anything else that wants to define its own wire shape can use it too.

The interface is two functions. That is the entire contract:

- **encode(payload) → wire_bytes.** The proxy hands the protocol the payload bound for the wire. The protocol returns whatever bytes should actually be transmitted. It can compress, encrypt, transform, replace, fragment, batch, or invent anything it wants.
- **decode(wire_bytes) → payload.** The proxy hands the protocol what it read from the wire. The protocol returns the payload to be delivered into the local stack. Any transformation it applied is invisible above and below.

No schema to declare. No callback to register beyond those two. No protocol-aware framing the proxy needs to know about. No negotiation handshake the proxy mediates. The custom protocol sees bytes in and produces bytes out. The application never knows it exists.

What can ride that interface is limited only by what someone wants to build. Domain-specific compression for medical imaging or instrument telemetry, where a tuned codec beats a general one. Application-layer encryption on top of the tunnel, so payloads are opaque even to other endpoints in the mesh. Protocol mimicry, shaping wire bytes to resemble something else entirely. Format translation, so the application keeps speaking whatever is convenient while the wire sees whatever is efficient. Carrying a foreign protocol — an industrial control bus, a messaging protocol — through FrogNet as a generic data link. Or something that does not have a name yet.

The comparable extension architectures are all positioned somewhere this one is not. Application-layer interceptors require the application to opt in and impose a schema. Custom transport options require kernel support and collide with middleboxes the moment they leave a controlled environment. Service meshes offer per-service shaping but require sidecars, configuration, and a control plane that is itself a protocol. This one is application-transparent, schema-free, and runs as infrastructure rather than middleware.

### **The Call From The Cabin**

For a family network, this is a feature people will use rarely and value when they do — the call from the cabin that degrades to text instead of dying, the message from the ambulance that gets through as a token when video cannot.

For a business or institutional deployment, it is the difference between "the link went down" and "the link is degraded but the conversation continues." Operational coordination does not stop when bandwidth drops.

For deployments where lives depend on the link — first responders, field medics, disaster zones, units operating in contested environments — it is an architectural answer to a problem the industry has not solved. Every other system in this space treats degradation as a sequence of failure modes to be handled. SotF-ACP treats degradation as a continuum along which communication never stops.

There is one condition under which SotF-ACP cannot help: no path of any kind between the two endpoints. No protocol delivers a byte across a link carrying zero bytes. But the door at which conventional systems give up is several orders of magnitude above the floor SotF-ACP operates at, and the space between those two points is enormous.

### **Status, Honestly**

The protocol runs a full adaptive loop with recovery, end to end, on real hardware over real links. Degradation is bidirectional and self-healing: each side steps its own quality down when its link congests and back up after the link has been clean for long enough, with deliberate hysteresis so it settles rather than oscillates.

Two things now run on real hardware rather than in a model. A bidirectional 1280×720 call at 22 frames per second held over a 900 MHz radio with a one-megabit ceiling, coast to coast — the call is a field measurement; the 89% of bytes never sent that the counter reported alongside it is a did-versus-would estimate against published WebRTC bandwidth for the same session, and is labelled that way wherever it appears. And a call driven down by hand — 987 kbps, then 637, then 450 — with the picture thinning at each step and the audio never breaking, then climbing back to full quality when the bandwidth was returned. That one is a field measurement end to end.

What remains is specific and worth naming. The relay forwards one stream, so the whole call runs at one rate and the slowest participant sets it. That is honest and defensible — nobody sends faster than the slowest end can take — but it means a fast viewer cannot be served a better picture than a struggling one, and fixing it requires transcoding at the relay rather than forwarding: decode once, encode per client at that client's budget. That is a real capability and a real cost, and it is the difference between a relay that forwards and a relay that serves. It is named here, and named in the Croakus, rather than left for someone to discover.

**CROAKUS** Chapters 45 through 51 for the Communicator and the wire under the call; chapter 50, *How the Ladder Actually Decides*, for the rules; and chapters 52 through 54, *What Is Not Built Yet*, for the rest of the honest list.

Fawcett Innovations LLC · unixpro@ufie.org · (206) 335-9639 · CAGE 1A5Y5
