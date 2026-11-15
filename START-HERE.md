# Start here

This is the introduction for people who have never seen this project. Engineers who want the layout, the gates and
the specifications go to [README.md](README.md).

## What it is, in one paragraph

This project turns ordinary devices (a box at your house, a laptop, phones, small computers on radios, servers in
other cities) into one private network that you own. Programs on that network share what they know instead of
sending messages to each other, and they keep working when the Internet doesn't. The software that does this is
**CroakOS**, a distributed operating system: the machines on the network become one computer.

## What it feels like

Imagine a small box — about the size of a deck of cards — plugged in at your house. Your mother has one at her house.
Your sister has one at her flat in London. Each box costs about what you'd spend on a nice dinner out.

Plug them in and they find each other. You do not configure anything. You do not set up accounts. You do not pick a
service plan. The boxes form a private, encrypted network that runs a photo gallery, a family chat, a shared calendar,
a bulletin board for sticky notes, a family map that shows where everyone is, and video calling — all on hardware that
belongs to your family and nobody else.

Your photos are not on iCloud. Your messages are not on WhatsApp. Your location is not sold to data brokers.
Everything stays on your boxes.

## One emergency, start to finish: the whole computer at work

A command post, a hospital and a field team, each with ordinary devices, run OpenEOC. Follow it from switching on to losing the connection and getting it back. Each step says what CroakOS does underneath, and links to the test that shows it.

1. **Five devices switch on: a laptop at the command post, a PC at the hospital, two phones, and a small computer on a radio with the field team.** Each machine writes its own facts (its addresses, its links, its uplink) into the shared memory. Nothing is configured. ([netns-lan-only-test.sh, 19/19](opt/fdf/src/fdf-source/rinab/tools/netns-lan-only-test.sh))
2. **They find each other and become one network. Nobody configures a server or types an address.** Discovery: every machine reads the same facts and derives the same graph, so routes agree everywhere without a coordinator. ([the simulator: 40,000 pairs at 200 members](opt/frognet_semantic/simulation/SIM_STATUS.md))
3. **The network picks which machine keeps the shared memory, and which one runs the radio log. Everyone reaches them by name.** Each machine announces what it can do; the same selection, run on every machine, gives the same answer. The chosen host is bound to a name, so programs never need to know which machine it is. ([test_fdf_app.py, steps 3–6](opt/fdf/src/fdf-source/rinab/tools/test_fdf_app.py))
4. **Everyone logs. An entry typed at the command post is on the hospital’s screen almost at once.** Each device writes its own entries; every screen holds a read on the log, and the write wakes it. No messages, no polling: 209 ms from one post to the other. ([OpenEOC results](opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md))
5. **The team drives out of coverage and the link drops. Both sides keep working: the team’s side now keeps its own memory and log.** Each side recomputes what it can reach and runs the same selection over the machines it has, so each side gets a memory host of its own. The team keeps logging on it. ([OpenEOC: post B’s uplink cut](opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md))
6. **Back in coverage, the two sides become one network again, and the team’s entries appear at the command post within seconds.** The selection runs over the reunited machines and names one memory host. Each device writes its own entries again where they are missing. Nothing is copied or reconciled: each owner reasserts its own truth. ([OpenEOC: the cut-off entries back in 7.1–7.5 s](opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md))
7. **Work carries on. A phone that has never joined before opens the board and sees all 58 entries, newest first. Nobody restarted, resynced or re-entered anything.** A new device is just another reader of the same memory: no setup, no sync. ([OpenEOC: the board on a fresh phone](opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md))

The emergency application implements none of this: not discovery, not placement, not naming, not recovery. It writes its own entries and reads the others. The same holds for a family's private network.

## Why an engineer should care

The same idea runs serious software, each piece checked against the original's own tests:

- **AI training.** PyTorch trained the same model to the same result 329× faster than stock PyTorch DDP, on five
  ordinary machines in two cities
  ([`examples/pytorch`](opt/frognet_semantic/ribbit/examples/pytorch/README.md)).
- **An Internet protocol.** lispers.net's LISP map-server, rebuilt on CroakOS, served 17× the lookups over the
  Internet. It failed none of 78 tests, against the original's 14 failures
  ([`examples/lispers.net`](opt/frognet_semantic/ribbit/examples/lispers.net/evidence/2026-11-14/RESULTS.md)).
- **Less to maintain.** The rebuilt systems do the same job with much less code: 68% less for LISP, about a tenth of
  the server code for Redis.

## See it working: one command, one Linux machine

```
git clone https://github.com/FawcettJohnW/FrogNet-Living-Network
cd FrogNet-Living-Network
bash opt/frognet_semantic/simulation/quickstart.sh
```

It builds the CroakOS programs the first time (you need g++, liblz4-dev and libssl-dev). Then it runs real CroakOS
processes, one for each simulated machine, and tells you in plain words what happened:

- three sites become one network, reaching each other through the region hub;
- a network loses a link, loses its exit to the Internet and gets it back;
- machines move, leave and rejoin, and one machine's link goes dark and comes back.

After every change, it checks every path between every pair of machines.

## Where to go next

| if you want to | read |
|---|---|
| understand how it works, part by part | [fawcettinnovations.com/croakos.html](https://fawcettinnovations.com/croakos.html) |
| install it on two machines | [docs/OPERATING.md](docs/OPERATING.md) |
| write a program for it | [`opt/frognet_semantic/ribbit/RIBBIT.md`](opt/frognet_semantic/ribbit/RIBBIT.md) |
| convert a system you already run | [`opt/frognet_semantic/ribbit/docs/CONVERTING-TO-RIBBIT.md`](opt/frognet_semantic/ribbit/docs/CONVERTING-TO-RIBBIT.md) |
| read the specification | [docs/spec/](docs/spec/README.md) |
| read the whole argument in plain English | [docs/how-to-think-like-a-frog.md](docs/how-to-think-like-a-frog.md) |
| see every result and what supports it | [CLAIMS.md](CLAIMS.md) |
| read the whole engineering record | `opt/frognet_semantic/docs/magnum_croakus/Magnum Croakus.html` |
| work with the author | john@fawcettinnovations.com · [fawcettinnovations.com/consulting.html](https://fawcettinnovations.com/consulting.html) |

Free software under the GNU GPL, version 2 only. A commercial licence is available to companies whose policy rules
out the GPL.
