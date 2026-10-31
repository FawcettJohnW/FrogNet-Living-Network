# Running the remote-configuration test on real machines

One tarball per machine type. Every role is in each tarball; `senz` picks the role on each node.

| Tarball | Roles |
|---|---|
| `senz-node-x86_64-<build>.tgz` | ops, ram, device |
| `senz-node-aarch64-<build>.tgz` | ops, ram, device |

Everything installs under `/opt/senz` (nothing in /usr/local/bin). Binaries need glibc 2.17 or later;
their libraries ship in `/opt/senz/lib`, and the ops runtime (JDK 8 and MongoDB) in `/opt/senz/env`.
MongoDB is 5.0 in the x86_64 tarball and 4.2 in the aarch64 one: 5.0 and later need ARMv8.2, which a
Pi 4 does not have. The backend's driver speaks to both.

## Roles

- **ram**: the RAM memory, port 18884. Any node every other node can reach.
- **ops**: one node, either architecture (a Pi needs about 1 GB free for it). Runs MongoDB on 127.0.0.1:27027, Mosquitto on 0.0.0.0:18883 and the backend
  on 127.0.0.1:18080, and is where you run the test.
- **device**: as many nodes as you like, either architecture. Each is one test device; its ID is the
  node's FrogNet name (FDF_NAME, else facts.json's name, else the hostname) unless you give one.

If ufw is active and would block a port that a role needs, `senz` asks before opening it. If systemd is not
running, it asks before running the role in the background under the same restart rule instead.

## Install and start (as root on each node)

Every node, with its tarball copied to /tmp:

    cd / && tar xvzf - < /tmp/senz-node-$(uname -m)-<build>.tgz

The RAM node:

    /opt/senz/bin/senz ram

The ops node (RAM_IP is the RAM node's address; it can be the same node as the RAM):

    /opt/senz/bin/senz ops RAM_IP:18884

Each device node (OPS_IP is the ops node's address):

    /opt/senz/bin/senz device RAM_IP:18884 OPS_IP:18883

Check any node: `/opt/senz/bin/senz status` (shows the build and the last log lines of each role).

## Run the test (on the ops node, once the backend is up, about 30 s after `senz ops`)

    /opt/senz/bin/senz test

The test collects the devices that advertised themselves in the RAM, then sends each one a new setting every
5 s for 60 rounds (`--rounds`, `--every` change that; `--path mqtt` runs the same test with the switch OFF).
Every applied result is printed as it arrives. While it runs, reboot device nodes or pull their network,
as often as you like. A device that is gone keeps receiving changes; when it comes back it must apply
only the latest change, once.

At the end it prints one line per device and PASS or FAIL:

- **I1** no UPDATED entry in the operator's view for a value the device never applied
- **I2** no value physically applied twice within one device boot
- **I3** a returning device's first application is the latest change made before it returned
- **I4** nothing lost: every change was applied, or replaced by a later change that was applied

Everything is kept in `/opt/senz/var/hwtest-<run>/` (result.json, events.log, one file per device), and
the run's instructions and results are tuples in the RAM (`senz-test` space).

## Stop

    /opt/senz/bin/senz stop device     (or ram, or ops)
