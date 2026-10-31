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

## What this repository is

The whole of it, in one tree: the node software (installed onto ordinary Linux
machines), the Ribbit platform and its examples, the FD&F region software, the
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
examples (chat, lispers.net, Redis, the Communicator) are in
`opt/frognet_semantic/ribbit/`.

**FrogNet is a transport and operating environment.** Nodes on connected LANs
discover each other, name each other, elect a database host and route to each
other's /24s, and split, heal and merge as links come and go, with no external
service. This is the LAN-only mode of a node. The node software is
`opt/frognet_semantic/`, `usr/local/bin/`, `var/www/html/` and `etc/`.

**FD&F (Farinacci, Day & Fawcett) is a different transport.** It makes separate
machines one application network whose meeting point is a RAM manager every
participant reaches: each participant opens its own outbound session to the RAM
manager and never one to another member, and applications meet through shared
memory. When FrogNet nodes are members of an FD&F region, their IP traffic
between sites rides WireGuard hub-and-spoke through the region's hub (fdf-hub)
on the RAM manager's host; nodes on one LAN still reach each other directly.
This is the region mode of a node. The FD&F sources, the built binaries and the
installers are in `opt/fdf/`.

A Ribbit program that behaves the same over FrogNet and over FD&F shows that the
programming model is not tied to one network.

## Where to start

| | |
|---|---|
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
usr/local/share/frognet/  the copies of this README, AI_READ_FIRST.md, CLAIMS.md, COPYRIGHT and LICENSE a node carries
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
opt/fdf/                  FD&F
  bin/  lib/  www/        what a region node or region host runs (laid down by the .run / fdf-droplet.tgz)
  dist/                   the node installers fdf-install-{x86_64,aarch64}.run and the region host's fdf-droplet.tgz
  src/fdf-source/         FD&F sources: rinab/ (C++ and its netns tests), fdf/ (scripts), docs/
  src/frognet-android/    the Android app, the member protocol in Kotlin, fdf-tun
  windows/                the Windows bridge (bridge/) and the older Windows client
docs/                     OPERATING.md, and cleanup-2026-10/ (the cleanup's records, logs and test harness)
```

What lands on a node is the node file set (`frognet_node_files` in
`usr/local/lib/frognet_world_manifest.sh`): not `opt/fdf`, the book or
`docs/`. A region node gets `/opt/fdf` from the FD&F `.run`.

## Running the gates

The checks the cleanup ran at every tranche, from the checkout's root. `opt/frognet_semantic`
is the Python root. All pass except two tiers of `run_all.py`, red since before the cleanup
(`opt/frognet_semantic/simulation/SIM_STATUS.md`).

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
binaries in `rinab/build` (not tracked in git; `opt/fdf/README.md`, "Build from source", says how it is built):

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
