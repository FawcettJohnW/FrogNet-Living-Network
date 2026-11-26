# WorldShepherd on FrogNet nodes

One tarball a machine type: `worldshepherd-node-<build>-aarch64.tgz` for the Pi nodes, `-x86_64.tgz` for the others.
A node needs no compiler and no git. Everything below is run on the node, as root.

`W` below is `/opt/frognet_semantic/ribbit/examples/worldshepherd/node/worldshepherd`.

## What the tarball puts on a node

| path | |
|---|---|
| `/opt/fdf/bin/fdf-app` | fdf-app r56: each app its own service (`fdf-app@APP.service`). **This replaces the node's fdf-app** |
| `/opt/fdf/bin/worldshepherd-ram` | the app's RAM host, static, for this machine type |
| `/opt/frognet_semantic/ribbit/examples/worldshepherd/` | the store, the harness, the session library for this machine type, the documents, `Sara_pro/` (Brandon Ray Davis's `worldshepherd_sara` package and tests at the pinned commit; `Sara_pro/SOURCE.txt`), and `pydeps/` (the Python packages his code imports, pure Python, `pydeps/VERSIONS.txt`) |

Nothing else on the node is written, nothing is installed with apt or pip, and a node needs no Internet: `pydeps/` is
on the path of the `worldshepherd` command only. `node/BUILD` says what the tarball is.

## Each app its own service: what happens to the apps already on the node

Before r56 one `fdf-app.service` served every app on a node. `W setup` asks, then restarts that service once: it
gives each installed app its own service, removes its own unit and ends. The RAMs that node was serving at that
moment are replaced with empty ones, and their programs write their own data into them again. After that, installing,
restarting or updating one app touches no other.

If `W setup` is not run, the same separation happens by itself the next time `fdf-app.service` starts (a reboot, an
update), because the node's fdf-app is now r56.

## Install

On every node, with the tarball for its machine type in `/tmp`:

    cd / && tar xvzf - < /tmp/worldshepherd-node-<build>-<arch>.tgz
    W setup

On ONE node, once (it writes the app's one configuration and picks its port):

    W config USER:PASSWORD

On every other node that may serve the RAM:

    W install USER:PASSWORD

Which nodes serve the RAM and which run SARAs is whatever you install and run where. `W where` prints where the RAM
is served now. `W status` prints this node's builds, its service's state and what its logs last said. The service is
`fdf-app@worldshepherd.service`; the RAM host's own log is `/opt/fdf/var/app-worldshepherd-general.log`, and it
begins with its build.

## Run

SARA's own 31 tests and the 8-process race, on one node alone (no network; about 15 s, longer on a Pi):

    W his-tests

The race across nodes. On every node that takes part (leave it running; `--name Seattle7` puts the node in each line):

    W claimant --name NODE

Then on one node:

    W race --claimants N --tickets 400

It prints one JSON line: `verdict`, tickets won once, twice and never, claims a second, the claim's median and worst
p99. `--store sql` runs Brandon's own `PostgresClaimStore` on the same memory. `W end` stops the claimants.

A SARA of your own on a node: `W python your_program.py`, in which `RibbitClaimStore()` with no address finds the RAM
and follows it when it moves.

## What is not in this tarball

The acceptance study's failure injections (the memory replaced, a link split, two memories) run in the simulator
(`ACCEPTANCE.md`). On nodes they are not automated yet: the race above keeps its own instructions in the app's RAM,
so it does not survive that RAM being moved or killed. A node study needs its instructions in the SystemRam.

## A node that already has an earlier build

    cd / && tar xvzf - < /tmp/worldshepherd-node-<build>-<arch>.tgz
    W setup

`W setup` sees a RAM host that was started before the tarball was unpacked, says so, asks, and restarts
`fdf-app@worldshepherd.service` on that node. The RAM is replaced with an empty one and each SARA writes its own
into it again. On a node that is not serving the RAM there is nothing to restart. `config` and `install` are not
run again. Stop the claimants of an earlier race first (`W end`, or Ctrl-C): a claimant is a program of the earlier
build until it is started again.

## Not yet done on hardware

Everything here was built and tested on one x86_64 machine. 0.11 has not run on a node. The aarch64 RAM host passed
SARA's 31 tests, the race and the acceptance study's LAN cases under emulation; the aarch64 session library passed
`make session-check` under emulation (it has not been loaded by an aarch64 Python: there is none here). The
separation of `fdf-app.service` was tested with fdf-app's own test (check 14) and with a stand-in for systemd; the
restart of an earlier build's RAM host by `W setup` was tested with a stand-in for systemd. Neither on a node.
