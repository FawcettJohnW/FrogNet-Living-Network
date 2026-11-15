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

## When the connection fails

A command post, a hospital and a field team share one radio log on ordinary laptops and phones. The team drives out of
coverage. They keep logging on their side of the network. When they come back into coverage, the two sides join up and
the entries come back together within seconds, and nobody has to do anything. That is OpenEOC, measured in
[`opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md`](opt/frognet_semantic/ribbit/examples/openeoc/RESULTS.md).

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
| see every result and what supports it | [CLAIMS.md](CLAIMS.md) |
| read the whole engineering record | `opt/frognet_semantic/docs/magnum_croakus/Magnum Croakus.html` |
| work with the author | john@fawcettinnovations.com · [fawcettinnovations.com/consulting.html](https://fawcettinnovations.com/consulting.html) |

Free software under the GNU GPL, version 2 only. A commercial licence is available to companies whose policy rules
out the GPL.
