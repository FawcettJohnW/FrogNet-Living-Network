# A clean machine joining a FrogNet: what was run, what was found, what was changed (28 November 2026)

Branch `clean-install-discovery-20261128`, made from `homa-under-croakos-20261116` at `42830a6`. Nothing is merged to
`main`.

## The question

A clean machine is installed and runs its merge while it is directly connected to one FrogNet node. The connection is
either that node's LAN (the node is upstream of the new machine) or the new machine's LAN (the node is downstream of it).
Is the new machine then a full member of the whole network?

## The answer

With the four changes on this branch, yes, for LAN-only nodes: six clean machines were installed one after another and
every one became a full member of the whole network without a command being run by hand. On the code as it was, the
fifth join was lost: its neighbour never learned the new node was there.

This covers a node that is LAN-only joining nodes that are LAN-only. A node that belongs to a region does not run the
walk (`[NO_CRAWL_IN_REGION_V1]`, `discovery/orchestrate.py`): it learns its members from the region, and a machine
joins a region by invitation. That was not run here.

## Terms used below

- **Node**: a machine with FrogNet installed. It serves its own /24 (`10.X.Y.0/24`) and is `10.X.Y.1` on it.
- **Merge**: one run of `runMerge.bash`. It walks the directly connected nodes, installs routes, writes `/etc/hosts`,
  publishes this node's capability tuple and elects the roles.
- **SystemRam, ControlRam**: the two roles every node must agree on, named `databasehost.frognet` and
  `databasehost_control.frognet` in `/etc/hosts`. Each is one node's `api.php`.
- **Held read**: a program waiting at a role for a tuple to be written; the write makes it run.
- **Merge watcher**: `frognet-merge-watcher.sh`, the service that starts a merge when something asks for one.
- **FrogNetHost Admin**: `frognet-host serve`, each node's own service that applies its configuration tuple.

## Where it ran

Docker containers on one x86-64 machine, each a debootstrapped Ubuntu 24.04 with systemd, the same kind of container
the October gates used (`docs/cleanup-2026-10/T4a.md`). Each install was the real installer from the checkout:
`frognet_install.sh --from-repo --name NAME --ip 10.X.Y.1 --lan-iface eth2 --db-pass ... --no-ssid-projection
--no-reboot`, then a reboot.

Two things differ from the October two-node gate, to match a real machine:

- A machine that joins another node's LAN has **only** that cable. It takes its address from that node's DHCP (dnsmasq),
  reaches the Internet through that node, and is installed that way. Docker assigns it nothing on that interface
  (`tools/cable.sh` plugs a veth into the LAN's bridge).
- Before FrogNet, such a machine has NetworkManager managing its ethernet, as Raspberry Pi OS does.

Not covered here: Raspberry Pi OS on aarch64, a Wi-Fi uplink or a projected SSID, a region.

## What "full member" was checked to mean

`tools/check.sh`, for every node against every other node:

- it names every node in `/etc/hosts`, and resolves `FrogNetHost.<name>` to that node's `.1`;
- it has the same SystemRam and ControlRam as every other node;
- it has a route to every node's /24, pings every node's `.1` and `.2`, and reads every node's echo through the proxy;
- it reads every node's capability tuple in the ControlRam;
- it writes its own tuple into the SystemRam;
- a read it holds in the SystemRam ends with every node's tuple, written after the hold began.

## The joins

| # | the clean machine | connected to | network after | all nodes agree, after its reboot | check |
|---|---|---|---|---|---|
| 1 | H2 | H1's LAN (H1 upstream) | H2 - H1 | under 2 min | 26 of 26 |
| 2 | H3 | H1 is on H3's LAN (the existing network downstream) | H3 - H1 - H2 | under 2 min 30 s | 60 of 60 |
| 3 | H4 | H2's LAN, three hops from H3 | H4 - H2 - H1 - H3 | 4 min 36 s | 108 of 108 |
| 4 | H5 | H3's LAN, four hops from H4 | H5 - H3 - H1 - H2 - H4 | **never** on the code as it was; 4 min 47 s with the changes | 170 of 170 |
| 5 | H6, installed from this branch | H1's LAN | six nodes | 3 min 41 s | 246 of 246 |

Each hop costs the watcher's 30 s window plus one merge (14 to 55 s here), which is where the minutes come from.
`runMerge.bash` run by hand on the clean nodes afterwards exits 0 and changes nothing (`logs/after-manual-merge-check.txt`).
The check outputs are in `logs/`.

## What was found, and what was changed

### 1. A merge started by a DHCP lease was killed by its own restart of dnsmasq

`dhcp_tracking.bash` started `runMerge.bash` itself. dnsmasq runs that script inside `dnsmasq.service`'s control group, and
`setsid` does not leave it. When such a merge had something to tell dnsmasq (a node had joined), its own
`systemctl restart dnsmasq` stopped the unit the merge was running in. H3's journal, H5 joining:

    10:10:22 dnsmasq-dhcp: DHCPACK(eth1) 10.73.73.40            H5's lease; the script starts runMerge 38263
    10:10:55 systemd: Stopping dnsmasq.service ...               that merge's own restart of dnsmasq
    10:11:27 GET /propogateNotification.php?event=f7ec...        H5 says it is here
    10:11:58 [MERGE-ACCUM] fire decision=set_runAgain reason=merge_running
    10:12:25 systemd: dnsmasq.service: State 'final-sigterm' timed out. Killing.
             Killing process 38263 (runMerge.bash) with signal SIGKILL.

Three of seven lease merges ended this way (H1 09:24:18, H2 09:44:46, H3 10:12:25). Each time the node served no DHCP
and no DNS for 90 s, and the merge died before its tail (default route, `resolv.conf`, propagation). The script sent the
merge's output to `/dev/null`, so none of it was in a log.

**Change** `[LEASE_ASKS_THE_WATCHER_V1]`: the lease script writes a request for the merge watcher, as the NetworkManager
hook already does. After the change: 4 leases, 0 merges killed (`logs/after-fix-kills-and-waits.txt`).

### 2. A request that arrived while a merge held the lock could get no merge

When its 30 s window expired and the merge lock was held, the watcher consumed its requests and wrote `runAgain`.
`runMerge.bash` reads `runAgain` once, straight after its discovery stage, and holds the lock for 0.8 to 2.2 s more
(measured on 16 merges). A request that landed in that stretch, or on a lock held by a merge that was already dead
(finding 1), left a flag nothing read. That is the 10:11:58 line above: H3 never ran a merge for H5's notification.

**Change** `[REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1]`: the requests stay queued; the watcher waits on the lock (a blocking
`flock`, no polling) and starts one merge for them when the running one ends.

### 3. The FrogNetHost Admin was enabled on a LAN-only node with nothing to run

`frognet-host.service` is in the node file set and the installer enables it, but its program is
`/opt/fdf/bin/frognet-host`, which only a region join laid. On every clean LAN-only node:
`python3: can't open file '/opt/fdf/bin/frognet-host'`, restarted every 2 s (1,808 restarts on H1 in 70 minutes), and no
FrogNetHost state tuple in the SystemRam.

**Change** `[FROGNETHOST_ON_EVERY_NODE_V1]`, installer `frognet-install-2026-11-28-r29`: `--from-repo` lays the program
and its Python library from the checkout's source (`opt/fdf/src/fdf-source/fdf`). A release that does not carry the
program does not enable the unit, and says so. On H6: `NRestarts=0`, active, listed by `frognet-host status`
(`logs/h6-frognet-host.txt`).

### 4. The same hosts in another order counted as a change

`_topology_changed` (`discovery/live.py`) compared `/etc/sentinels/frognet_hosts` line by line. The walk writes peers in
the order they answered, so two nodes behind one neighbour could swap places between merges. A merge that changed
nothing then said `sync_required set reason=hosts_changed` and notified its neighbours, and each ran a merge (26 s and
29 s on H1) to find nothing.

**Change** `[HOSTS_ORDER_IS_NOT_A_CHANGE_V1]`: the lines are compared as a set. On H3, four merges by hand, the order
changed twice, no notification, H1 did not merge.

## Oracles (each fails on the code as it was)

- `discovery/test_merge_request_kept_oracle.py`: runs the real `frognet-merge-watcher.sh` against a stand-in controller on
  the same lock (findings 1 and 2).
- `discovery/test_hosts_order_not_a_change_oracle.py` (finding 4).
- `discovery/test_clean_install_oracle.py`, PLANE 1f: every unit the installer enables has its program on a clean
  LAN-only node (finding 3). PLANE 3: in the simulator, a clean node joins an existing network above it, below it, and
  at the end of the same five-node line, converging by the neighbours' notifications.

Discovery gate (`python3 -m discovery.run_discovery_oracles`): 86 pass, 0 regression.

## The simulator

On commit `0988731`, in this workspace (2 cores), after `simulation/setup.sh --yes`:

- Discovery gate, `python3 -m discovery.run_discovery_oracles`: **86 pass, 0 regression**. `python3 -m discovery selfcheck`:
  **14/14**.
- `simulation/run_all.py` **did not complete here**. It passed 20 tiers (the model suite, the real engine over 23
  topologies, failure modes, regression, S scale, the 20 hardware topologies with events and slow links, IS-IS loop
  persistence, the proxy and transport tiers, the codex, four Homa tiers), then spent over 35 minutes in "Homa driver
  between machines, every hardware topology" with the load average at 138 and the workspace no longer answering, and
  was stopped by hand. Not run: that tier and the five `roles_exercise_tier` runs between machines. None of them runs a
  file this branch changes (they write the hosts file with their own `the_merge`).
- Run one at a time afterwards, all pass: the held-sockets oracle (6), the setting-from-behind oracle (22), DAF
  discovery (20 topologies, 3 failure scenarios).
- Three tiers failed in the stopped run. Ribbit-LISP against lispers.net needs `setup.sh --lisp`, which was not run.
  `door_semantic_tier` and `proxy/test_homa_ordering_oracle.py` fail the same way, in under 3 s, on `42830a6` before
  these changes and on this branch (`logs/two-failed-tiers-before-and-after.txt`); `door_semantic_tier` unpacks an
  earlier commit from git history, and this checkout is a depth-1 clone.

**The whole gate still has to be run on a machine that can carry it.**

## An installed node

`frognet-update-clean-join-20261128a.sh` (delivered beside this branch, not in the tree) changes the three run-time
files in place on a node that is already installed, saves the old ones under `/var/backups/frognet`, and restarts the
merge watcher when no merge is running. It was run on a node holding the GitHub versions (all three end byte-identical
to this branch), a second time (nothing to do), and on a node whose copies were not GitHub's
(`logs/update-script-tests.txt`).

## Running it again

`tools/` holds what was added to the October harness (`docs/cleanup-2026-10/t4a-tools`): `mknode.sh` (a node with its
own uplink), `mkclient.sh` (a clean machine whose only cable is into a node's LAN), `cable.sh`, `reboot-cabled.sh`,
`check.sh` with `member.py`, and the two relays this sandbox needed to reach the package mirrors. They are sandbox
tools; nothing in them is installed on a node.
