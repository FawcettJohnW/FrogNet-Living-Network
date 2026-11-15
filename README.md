# FrogNet Living Network

**Stop building a distributed system out of computers.
Build a computer out of a distributed system.**

A computer has permanent and temporary storage, I/O, and cores. So does a
network. That leaves one property a network lacks: RAM.

FrogNet gives it RAM. One shared memory, directly addressable, that every
machine on the network reads and writes. Programs stop sending each other
messages and start using memory.

**CroakOS** is the distributed operating system that does it. The distributed collection is the computer.
Programs use its memory and its scheduling; the threads happen to run on different machines. PyTorch trained the
same model to the same loss 329× faster than stock DDP on a five-node Internet mesh, and lispers.net's map-server,
rebuilt on it, served 17× the lookups over the Internet
([PyTorch](opt/frognet_semantic/ribbit/examples/pytorch/README.md),
[Ribbit-LISP](opt/frognet_semantic/ribbit/examples/lispers.net/evidence/2026-11-14/RESULTS.md)).

*Free software under the GNU General Public License, version 2 only. A commercial licence is available to companies
whose policy rules out the GPL.*

---

## Get started

**New here? Read [START-HERE.md](START-HERE.md)** first: what this is in plain words, what it feels like to use, and
one command that shows it working.

**Watch it run, on one machine.** `bash opt/frognet_semantic/simulation/quickstart.sh` builds what it needs and
shows CroakOS at work in plain words. The full gate runs the production code (the
discovery, the route planner, the CroakOS binaries) across simulated networks:
200 members, the hardware topologies, slow links, splits and merges, and
lispers.net under Ribbit-LISP. On Linux, from the root of the checkout:

```
cd opt/fdf/src/fdf-source/rinab && bash tools/build.sh && bash tools/build_static_host.sh && bash tools/build_static_clients.sh
cd ../../../../frognet_semantic
PYTHONPATH=$PWD FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=$PWD/../../usr/local/bin python3 simulation/run_all.py
```

`simulation/SIM_STATUS.md` says what each tier exercises and what the last run showed.

**Install it on your own machines.** [docs/OPERATING.md](docs/OPERATING.md):
one LAN, then a region across sites.

**Convert something.** `opt/frognet_semantic/ribbit/docs/CONVERTING-TO-RIBBIT.md`
is the method, with PyTorch and lispers.net as the worked examples. The examples
are in `opt/frognet_semantic/ribbit/examples/`.

**Work with me.** I take consulting on converting a system to Ribbit, on
deploying FrogNet and CroakOS, and on the architecture. A commercial licence is
available to companies whose policy rules out the GPL. Write to
john@fawcettinnovations.com, or see
[fawcettinnovations.com](https://fawcettinnovations.com).

## What this repository is

The whole of it, in one tree: the node software (installed onto ordinary Linux
machines), the Ribbit platform and its examples, CroakOS (the FD&F region software), the
Android app and the Windows bridge sources, the simulator and the tests, the
book, and the website.

The repository is laid out as a filesystem. `opt/frognet_semantic/core/` here is
`/opt/frognet_semantic/core/` on a node, and the installer copies the node's
files from these paths.

**To run it, read [docs/OPERATING.md](docs/OPERATING.md).** It is the one
operator guide: what runs where, installing a node LAN-only or into a region,
creating a region, phones and Windows PCs, upgrade, leave, uninstall, what is
running, the limits, and troubleshooting from the logs.

## Three specifications

The book, *Magnum Croakus*, is the umbrella specification for three separate
architectures that meet at explicit boundaries. None is an implementation detail
of another.

**Ribbit is the programming surface.** It defines shared Regions, tuple-shaped
state, truth ownership, held reads, and the contract by which programs interact
with shared memory: determine your truth, write your truth, read the other
truths, go. A participant writes the facts it owns; others read them, query
them, or hold a read until they change. Ribbit does not route packets and does
not decide how its memory is reached. Its platform, default API, documents and
examples (chat, the Communicator, lispers.net as Ribbit-LISP, Redis, MQTT, PyTorch, OpenEOC, FrogTastic-RAM
for Meshtastic nodes) are in
`opt/frognet_semantic/ribbit/`.

**FrogNet is a transport and operating environment.** Nodes on connected LANs
discover each other, name each other, elect a database host and route to each
other's /24s, and split, heal and merge as links come and go, with no external
service. This is the LAN-only mode of a node. The node software is
`opt/frognet_semantic/`, `usr/local/bin/`, `var/www/html/` and `etc/`.

**FD&F (Farinacci, Day & Fawcett) is a different transport, and CroakOS is its
implementation.** FD&F is the architecture; CroakOS is the distributed operating
system that implements it, offering memory and scheduling (held reads) instead of
messaging between computers, across the Internet. The code keeps the `fdf` name in
its paths and commands (`opt/fdf`, `fdf-app`, `fdf-region-join`). It joins machines
at separate sites into one network. Members attach at the region's droplet, and
attaching is the droplet's only special job. Between sites, the fabric carries
IP traffic over WireGuard through the region's hub (fdf-hub), which runs on that
host. Nodes on one LAN still reach each other directly. Routing is the fabric's
and is transparent to every server, the droplet included. Applications meet in
their own RAM, served by a droplet that each app's selection places on a member
(`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`). This is the region mode of a node. The CroakOS sources, the built binaries and the
installers are in `opt/fdf/`.

A Ribbit program that behaves the same over FrogNet and over FD&F shows that the
programming model is not tied to one network.

## Where to start

| | |
|---|---|
| [docs/spec/](docs/spec/README.md) | the FrogNet specification, §1–§22: the normative clauses (§1–§18) and the Ribbit reference (§19–§22), the same text as the site's specification pages |
| [docs/how-to-think-like-a-frog.md](docs/how-to-think-like-a-frog.md) | the argument in plain English, with the family scenario and what it costs; the Word edition beside it |
| [docs/OPERATING.md](docs/OPERATING.md) | install, run, upgrade, remove; what the logs say |
| `opt/frognet_semantic/docs/magnum_croakus/Magnum Croakus.html` | the book: the three specifications (Part 0 is normative), every mechanism, and what broke along the way. Part "Connecting Across the Internet" (chapters 19–22) and the "From Clean to the Communicator" walkthrough describe the internet path as it was before October 2026; a region (FD&F) replaces it |
| [CLAIMS.md](CLAIMS.md) | each claim with the test or log that shows it, or marked not shown |
| [AI_READ_FIRST.md](AI_READ_FIRST.md) | read before letting an AI work in this tree |
| `docs/cleanup-2026-10/` | the October 2026 cleanup: what each tranche removed and changed, its gates and their logs |

## The tree

```
etc/                      systemd units, Apache vhosts, sysctl, sudoers, logrotate, the NetworkManager hook
usr/local/bin/            node scripts: the installer (installer/), runMerge.bash, the monitor launcher, tooling
usr/local/lib/            shell libraries: logging, the node file set (frognet_world_manifest.sh)
var/www/html/             the node's web face: landing page, getSnapshot.php, getHosts.php, the dashboard, api.php
opt/frognet_semantic/     the node engine
  core/                   memory, codec, wire identity, handlers
  discovery/              the LAN walk, routing, the election, and the discovery oracles
  proxy/  daemon/         the semantic proxy (:80) and the data daemon (FNWP on :9009)
  internet_tunnels_v3/    the tunnel daemon: LAN-only, or region (the hub link, WireGuard)
  frognet_route/          the route planner and committer
  simulation/             the simulator and its tiers
  ribbit/                 Ribbit: platform, default API, documents, examples
  docs/                   the book (magnum_croakus/) and engineering notes
opt/fdf/                  CroakOS, the implementation of FD&F
  bin/  lib/  www/        what a region node or region host runs (laid down by the .run / fdf-droplet.tgz)
  dist/                   the node installers fdf-install-{x86_64,aarch64}.run and the region host's fdf-droplet.tgz
  src/fdf-source/         CroakOS sources: rinab/ (C++ and its netns tests), fdf/ (scripts), docs/
  src/frognet-android/    the Android app, the member protocol in Kotlin, fdf-tun
  windows/                the Windows bridge (bridge/) and the older Windows client
docs/                     OPERATING.md, and cleanup-2026-10/ (the cleanup's records, logs and test harness)
```

What lands on a node is the node file set (`frognet_node_files` in
`usr/local/lib/frognet_world_manifest.sh`): not `opt/fdf`, the book or
`docs/`. A region node gets `/opt/fdf` from the FD&F `.run`. The installer lays the licence and this file,
`AI_READ_FIRST.md` and `CLAIMS.md` into `/usr/local/share/frognet` from the one copy at the root.

## Running the gates

The checks, from the checkout's root. `opt/frognet_semantic` is the Python root. All pass
(run of 2026-11-15; `opt/frognet_semantic/simulation/SIM_STATUS.md` has the simulator's tiers and what a full run
needs: the CroakOS binaries, the Ribbit-LISP package with lispers.net, and MariaDB for the door tier).

```
cd opt/frognet_semantic
PYTHONPATH=$PWD PYTHONUTF8=1 python3 -m discovery selfcheck
PYTHONPATH=$PWD PYTHONUTF8=1 python3 discovery/run_discovery_oracles.py
PYTHONPATH=$PWD PYTHONUTF8=1 FROGNET_PROXY_ROOT=$PWD FROGNET_BIN_ROOT=$PWD/../../usr/local/bin python3 simulation/run_all.py
cd ../..
bash docs/cleanup-2026-10/static-no-broker.sh
bash docs/cleanup-2026-10/static-no-fallback.sh
bash docs/cleanup-2026-10/check-docs.sh
```

The network-namespace tests run as root on Linux, from `opt/fdf/src/fdf-source/rinab`, against the built
binaries in `rinab/build` (not tracked in git; `opt/fdf/src/fdf-source/fdf/README.md`, "Build from source", says how it is built):

```
cd opt/fdf/src/fdf-source/rinab
bash tools/netns-region-loop-test.sh
NOKEY=a bash tools/netns-region-loop-test.sh
bash tools/netns-lan-only-test.sh
bash tools/netns-ponds-daemon-test.sh
```

`netns-reach-test.sh` also needs the Kotlin member and fdf-tun built from `opt/fdf/src/frognet-android`
(`BRIDGE_KT=<.../member-cli/build/install/fdf-member-kt/bin/fdf-member-kt> FDF_TUN=<.../tun/out/fdf-tun-x86_64>`).
The results of every gate, tranche by tranche, are in `docs/cleanup-2026-10/T*.md`.

## Working with an AI in here

Read `AI_READ_FIRST.md` first. There is also an `AI_README.md` in the main
directories, listing the traps in that one (`find . -name AI_README.md`).

**A docstring in this tree is a claim, not a fact.** Fixes have landed in code
while the comment above kept describing the old behaviour. **Cite the code,
never the comment.** Comments carry doctrine tags like `[ONE_STATE_V1]`; each
records a decision made once, usually after a failure. Read the comment before
changing what a tag governs.

## Talk to me

[Discussions](https://github.com/FawcettJohnW/FrogNet-Living-Network/discussions).
Ask anything. Tell me it will not work and why. Post the log where it fell over
on your hardware. Issues for defects; Discussions for everything else.

What I would most like: somebody other than me installing it and saying where
it broke; a second implementation of the wire protocol, however partial; the
specification read against the code by somebody with no stake in it being
right.

## Licence

**GPL-2.0-only.** Version 2 of the GNU General Public License, and no other
version. Not "or later", and not v3. See `COPYRIGHT` for why the version is
fixed, and `LICENSE` for the text.

Free for any use including commercial and production. What you distribute
carries the GPL too.

## Status

Experimental. One author wrote all of it, and nobody outside has reproduced it.

Fawcett Innovations LLC · CAGE 1A5Y5 · Burien, Washington ·
john@fawcettinnovations.com
