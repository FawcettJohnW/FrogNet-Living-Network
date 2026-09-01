# FrogNet Living Network

**Stop building a distributed system out of computers.
Build a computer out of a distributed system.**

A computer has permanent and temporary storage, I/O, and cores. So does a
network. That leaves one property a network lacks: RAM.

FrogNet gives it RAM. One shared memory, directly addressable, that every
machine on the network reads and writes. Programs stop sending each other
messages and start using memory.

*Free software under the GNU General Public License, version 2 only.*

---

## 1. What a FrogNet is

A mesh network installed on ordinary Linux machines. It forms itself, plans its
own routes, and repairs itself as links appear and disappear. Transports are
whatever is available: Ethernet, Wi-Fi, WireGuard over the internet, 900 MHz
radio.

Above that, every machine shares one memory. A program writes a value. A program
on another machine reads it. Neither sends a message and neither knows the other
exists.

Two things at once, then. A network that replaces routing, VPN and
service-discovery work, and a programming surface that replaces endpoint, queue
and retry work. The first exists to make the second possible.

**node.** One machine running the stack. Any Linux box. A single node is already
a complete network. Nothing in the programming model needs a second machine.

**tunnel.** How nodes reach each other across sites. WireGuard, one interface
per peer, built and torn down as machines come and go. On a LAN there is no
tunnel. Over radio the radio carries it. Nothing above cares which.

**broker.** Two machines behind separate routers cannot find each other. The
broker sits somewhere reachable, introduces them, and carries traffic between
sites that cannot route directly. A switchboard. It holds no identity in the
network and no part of the memory, and though it sees the bytes it forwards it
has no context to interpret them.

**pond.** The nodes that can reach each other. The word covers two objects. To a
node it is not declared and nothing records it: it is whoever is reachable now,
recomputed as that changes. At a broker it is an administrative record. An
operator creates it, names it, sets a member limit, issues a group token, and the
broker allocates member addresses from that pond's block.

**store.** One per pond, held by whichever node is currently best suited. Every
node reads and writes the same shared memory. Nothing is replicated.

```
        pond
  ┌───────────────────────────────────────────┐
  │   node        node        node       node │
  │     │           │           │          │  │
  │     └───────────┴─────┬─────┴──────────┘  │
  │                       │                   │
  │              one shared memory            │
  │         held by the elected database host │
  └───────────────────────────────────────────┘
```

**discovery.** Each node observes what it can reach, trades observations with
neighbours, and derives its own view. No registry holds a membership list.

**election.** Every node runs the same scoring function over the same published
facts and reaches the same winner independently. Nothing is voted on, nothing
waits for agreement. The database host is one such role. When its holder goes
away, another node has it moments later.

**proxy and daemon.** Between a program and the store, talking FNWP-1 over
sockets that stay open. They learn the structure of what crosses and send only
what changed. An unchanged exchange costs 21 bytes.

### What it is for

Anywhere the network is the hard part rather than an assumption.

| | |
|---|---|
| **Links that come and go** | Vehicles, ships, field teams, anything moving between coverage. Nodes leave and rejoin without an application noticing, and a partition is not an error condition |
| **Links that are narrow** | A live application over 4800 baud, or an HD call under 150 kbit/s, because what crosses is the difference and not the payload |
| **No infrastructure to rely on** | Nothing to stand up first. Drop boxes at a command post, a field hospital and each vehicle, and they find each other |
| **Separate organisations, one picture** | Each publishes what it knows under its own name. Nobody has to agree a schema with anybody or run the shared server |
| **Anything currently held together by plumbing** | If a design's real weight is queues, retries, service discovery and reconnection logic, that weight is what this removes |

It is a poor fit for a single-datacentre application with reliable links and a
database that is already working. There is nothing here you need.

---

## 2. The programming model

```python
from core.frognet_tuples import put, get_all, node_scope

# a producer writes what only it knows, naming no consumer
put("sensors", "temperature", node_scope(), {"celsius": 21.4})

# somewhere else, whenever it likes, a consumer reads the set
for row in get_all("sensors"):
    print(row["var"], row["value"], "from", row["addr"], row["age_s"], "s old")
```

Neither program knows the other exists. No endpoint, no connection to hold open,
nothing to retry against, no session, no service discovery, no broker, no
database to stand up.

Because the memory is directly addressable, no coordinating or resolution server
is required. There is no store-and-forward and no consensus derivation from
incomplete data. **Each node writes its own truth in its own time, and consumers
directly read that data when they require it.**

Three operations: `put`, `get`, `get_all`. Nothing subscribes, nothing publishes,
nothing is notified when a value changes. No locks, no transactions, no merge
law. The model is contention-free. Last write wins, and no writer blocks, waits or
retries because of another.

An application resolving write contention or coordinating competing writers is
using the wrong model. Give each producer its own key and let the consumer
interpret the set. The analogue is lockless multithreaded programming on one
machine: the pattern, not the memory consistency semantics.

`get_all` is one exchange, and an unchanged exchange is 21 bytes regardless of
the size of the value. Reading the whole set is cheap.

The module is `opt/frognet_semantic/core/frognet_tuples.py`. Readable now,
runnable only against an installed node, because the store is on the network.

---

## 3. The network is the computer

The specification carries this table in §1a.

| A computer has | A FrogNet has |
|---|---|
| cores | nodes, executing independently and concurrently |
| RAM | one shared store, directly addressable, per connected pond |
| permanent storage | each node's own disk, and the store's durable tables |
| I/O | the transports: Ethernet, Wi-Fi, WireGuard, 900 MHz radio |
| a memory controller | the elected database host |
| hardware enumeration at boot | discovery |
| an interconnect | routes each node plans and commits for itself |

Two things follow, and both get read wrong.

The elected host holds the memory. It does not decide, arbitrate or grant
permission. A write goes there for the same reason a store instruction goes to a
memory controller: that is where the location is.

Adding a machine adds a core, not a client. The machine-level question is not how
fast a node runs but how many shared-memory operations the machine services per
second. A large elected host is a more capable memory subsystem, not a bigger
server the others depend on.

Nothing in the design asks one participant to agree with another before acting.
A node derives topology from what it observed rather than from a description
handed to it. It computes an election winner rather than negotiating one. It
plans and commits its own routes rather than receiving a table. A program writes
what it knows rather than agreeing with other writers on a shared answer.

**Publish what you know, do not coordinate what you do not have to, and derive
the answer from shared state.** The same idea at four layers, which is why there
is no coordinating server anywhere in it.

---

## 4. Is any of this real

| | |
|---|---|
| **Watch it** | [demonstrations](https://fawcettinnovations.com/demonstrations.html). A live HD call held over 900 MHz radio while the link was deliberately starved. Nobody redialed. Unedited first-run capture |
| **Read the contract** | [the specification](https://fawcettinnovations.com/technicalhome.html). Twenty normative sections, 476 MUST-level statements, constants cited to source |
| **Check the evidence** | [CLAIMS.md](https://fawcettinnovations.com/CLAIMS.md). Every claim sorted by what supports it: demonstrated on hardware, measured and reproducible, argued from architecture, or not demonstrated |
| **Measure it** | `pipe_workload.py` is in this tree. It produced the published compression figures |

What is wrong with it. One author wrote all of it. Nobody outside has reproduced
any of it. There is no second implementation, and a protocol only one
implementation speaks has not been shown to be a protocol. The simulator
reproduces what hardware did rather than predicting what it will do. Four values
the specification could not verify are marked `PENDING SOURCE` rather than filled
in.

Published limits are §17, with the arithmetic.

---

## 5. What it takes to run

Network infrastructure, not an application. Installing reconfigures the host:
NetworkManager, dnsmasq, routing, iptables, Apache. No `make install`, no
`docker compose up`, no useful single-machine demo.

**Two or more hosts you can afford to reconfigure, and about an afternoon.**

Per host: Debian Bookworm (a Pi 4 or 5 with 2 GB is the reference, and laptops
and mini-PCs work), NetworkManager, dnsmasq, Python 3, one external interface.

| | |
|---|---|
| [run it](https://fawcettinnovations.com/run-it.html) | what you need in front of you, and two recorded installs |
| [installation steps](https://fawcettinnovations.com/frognet-install-steps.html) | the seven phases |
| [download](https://fawcettinnovations.com/download.html) | what it changes on the host |

The installer is `usr/local/bin/installer/frognet_install.sh`. Run the simulator
against the node afterwards, before trusting the install.

**Virtual machines expected to work, containers cannot be supported.** This has
not been confirmed, but FrogNet restricts itself to existing common tooling and a
hypervisor guest has its own kernel, routing table, netfilter and interfaces, so
we expect proper functionality on KVM, Proxmox, VMware and VirtualBox. Two guests
on separate virtual networks should make a reasonable two-host test bed. If you
try it, say what happened.

A container shares the host kernel. `ip route` works inside its namespace with
`NET_ADMIN`, but WireGuard interfaces need the module on the host, network
namespaces need `SYS_ADMIN` or `--privileged`, and NetworkManager and dnsmasq
expect to own the host’s networking rather than a container’s. Running
`--privileged --network host` reconfigures the host anyway and gains nothing
over installing on it. This is the same reason simulator tiers 4 and 5 fall back
to a dry run in a container.

### Building a release

One healthy node builds the installer for every other.

```
sudo bash usr/local/bin/frognet_build_release.sh [--output /path/to/dir]
```

Produces `frognet_release_YYYYMMDD_HHMMSS.tgz` containing `frognet_install.sh`
and a filesystem snapshot. The recipient runs:

```
tar -xzf frognet_release_*.tgz
sudo bash frognet_install.sh
```

This is also how to assess it. Standard Linux machines: add whatever
instrumentation, hardening or attack tooling an assessment needs, build a release
from that node, and walk it around a network you control.

---

## 6. Going deeper

Two books, free, generated from source so they track the implementation.

| | |
|---|---|
| [**Magnum Croakus**](https://fawcettinnovations.com/magnum-croakus.html) · [.docx](https://fawcettinnovations.com/magnum-croakus.docx) | The build manual. Every mechanism, why each decision was taken, and what broke when it was taken differently |
| [**How to Think Like a Frog**](https://fawcettinnovations.com/how-to-think-like-a-frog.html) · [.docx](https://fawcettinnovations.com/how-to-think-like-a-frog.docx) | The argument for network shared memory, in plain English |

The simulator does not model FrogNet. It runs the real planner, committer, proxy
and daemon over modelled or namespaced environments.

Twenty topologies. Pairs, chains, rings, stars, multi-LAN meshes sharing a
WireGuard transit, asymmetric three-site ponds, a two-AP ham-radio link, and a
few genuinely funky ones. Plus captured real faults replayed offline. Each has a
baseline recorded from a real-kernel run and stamped `source: hardware`, and the
offline engine reproduces all twenty byte for byte. It has found ten real
defects.

Not covered, per `simulation/SIM_STATUS.md`: concurrency, and loss or jitter on
a topology edge. Agreement with hardware is not prediction.

---

# Working in this tree

Everything from here is for someone with the code open.

## What you should already know

FrogNet deliberately uses standard APIs, protocols, operating-system facilities
and tools wherever practical. Its dependencies therefore cross multiple
engineering layers and are dispersed throughout the system, and reading one
subsystem in isolation will not explain what you are looking at.

Required, at an advanced working level:

Linux · HTTPS proxies · distributed systems · shared memory · semantic
compression · custom wire protocols · Python

Recommended:

Conventional networking and routing · TCP/IP · DNS and DHCP · WireGuard · REST
and HTTP · relational databases and SQL · systemd · shell scripting · radio
networking · FFmpeg · real-time audio/video systems

Load this codebase into an AI capable of repository-wide analysis. Tracing those
cross-layer dependencies by hand is slow. See *Working with an AI in here*
below before you trust what it tells you.


## Where things are

**The repository is rooted at `/`.** `opt/frognet_semantic/core/` here is
`/opt/frognet_semantic/core/` on a node.

FrogNet is not an application with configuration around it. It spans the system.
Discovery is triggered by a NetworkManager dispatcher hook and a dnsmasq lease
script. The store's face is `api.php` under a web root. Node identity lives at
`/etc/fnid` because that is outside what a reinstall wipes. Routing is committed
to the kernel. Tunnels are WireGuard interfaces. The merge controller is a shell
script holding a lock in `/var/run`. **These are not scaffolding around the code.
They are the code**, and several are specified normatively. Dispatcher hooks
are §4a, the identity file §3.2.1.

```
opt/frognet_semantic/     the engine
  core/                   memory, codec, wire identity, handlers, sizing
  discovery/              the walk, routing, election, oracles (81 of them)
  proxy/  daemon/         the semantic proxy and daemon, FNWP-1 on :9009
  internet_tunnels_v3/    broker registration, WireGuard peering
  simulation/             the harness
  broker/                 full_broker.tgz, the broker ships as an archive
etc/frognet_bundles/      applications: communicator, boardgame
usr/local/bin/            installer, node tooling, runMerge
var/www/html/             api.php, the store's HTTP face
```

The four documents at this level (this README, `AI_READ_FIRST.md`, `LICENSE`,
`COPYRIGHT`) are repository files. A release is built from an explicit path
manifest, so they are never laid down on a node.

| | |
|---|---|
| `opt/frognet_semantic/README_FIRST.md` | engineering posture |
| `opt/frognet_semantic/FROGNET_PRIMER_load_me_first.md` | the architecture primer |
| `opt/frognet_semantic/DOCTRINE.txt` | the doctrine tags and what they bind |
| `opt/frognet_semantic/simulation/SIM_STATUS.md` | what the simulator covers |

## Doctrine tags

Comments carry markers like `[ONE_STATE_V1]` or `[DBHOST_STATIC_RANK_V1]`. Each
records a decision made once, usually after a failure, that must not be quietly
reversed.

```
grep -rn "\[.*_V[0-9]\]" opt/frognet_semantic/ | less
```

If you are about to change behaviour a tag governs, read its comment first. It
generally explains what broke last time. Some of them read like a man arguing
with himself at two in the morning, which is roughly the truth of it, but the
history in them is groovy and it will save you a day.

## Running the checks

From a clone, paths repo-relative:

```
cd opt/frognet_semantic
PYTHONPATH=. python3 -m discovery selfcheck     # the oracle suite
cd simulation && python3 run_all.py             # the simulator tiers
```

On a node, the same from `/opt/frognet_semantic`. Tiers 4 and 5 need a real
kernel and root. In a container they degrade to a dry run rather than failing.

Oracles are in several places: `discovery/` holds the largest set, with others
under `simulation/`, `oracles/`, `proxy/` and `core/`.

## Working with an AI in here

Read `AI_READ_FIRST.md` first. Each rule in it names a failure that has actually
happened. Models read this project and get it wrong in the same specific ways.

There is also an `AI_README.md` in every significant directory, listing the traps
in that one:

```
find . -name AI_README.md
```

They open with the same working model: **an assistant is fast, tireless, and not
to be trusted.** It reads a directory quicker than you can and is wrong in ways
that look right. Use it to find, draft and check. Not as a source.

**A docstring in this tree is a claim, not a fact.** Fixes have landed in code
while the comment above kept describing the old behaviour. A flush with no
caller. A reachability probe the same function had removed. A route-flap
threshold documented as 10% where the code applies 50%. A scoring docstring
naming the three inputs its own doctrine tag forbids. **Cite the code, never the
comment.**

The normative specification is not in this repository. It is on the website.
Where it and this tree disagree, one has a defect, and which it is has to be
established rather than assumed.

## Known gaps, so you do not go looking

- Two of three falsification challenges have no oracle: credential manufacture,
  and what an owned broker can do (spec §18.5).
- Four values could not be located in this tree and are marked `PENDING SOURCE`
  rather than filled in.
- Electing a node that *fronts* an external database is not supported: the
  eligibility gate and every scoring term are local to the candidate (§17.3.1).
- The node ceiling is 58 per pond, as few as 16 where one broker carries a
  second. A fix is planned, and the arithmetic is published.

---

## Talk to me

[Discussions](https://github.com/FawcettJohnW/FrogNet-Living-Network/discussions).
Ask anything. Tell me it will not work and why. Post the log where it fell over
on your hardware. That is more useful than a compliment and I will not be
precious about it.

What I would most like:

- Somebody other than me installing it and saying where it broke.
- A second implementation of the wire protocol, however partial.
- The specification read against the code by somebody with no stake in it being
  right.
- The two falsification challenges in §18.5 that still have no oracle, taken
  seriously.

Issues for defects. Discussions for everything else, including the question you
think is too basic to ask. It is not.

---

## Licence

**GPL-2.0-only.** Version 2 of the GNU General Public License, and no other
version. Not "or later", and not v3. See `COPYRIGHT` for why the version is
fixed, and `LICENSE` for the text.

Free for any use including commercial and production. Internal use triggers
nothing. What you distribute carries the GPL too. There is nothing to buy and
nothing to ask for.

## Status

Specification Draft 0.9, revision 2026-08-31. Experimental. No registry assignment is claimed.

Fawcett Innovations LLC · CAGE 1A5Y5 · Burien, Washington ·
john@fawcettinnovations.com
