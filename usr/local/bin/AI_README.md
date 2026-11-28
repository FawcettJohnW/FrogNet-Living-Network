# AI_README — node tooling and the installer

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

**Read `AI_READ_FIRST.md` at the repository root first.** It owns the model (the holistic invariant, the checksum,
and what to do before reporting a defect); this file lists only the hazards in this directory.

## What is here

The installer, the merge controller, node tools, hooks. Shell and Python,
mostly shell. How to install and operate a node: `docs/OPERATING.md` at the
repository root.

| | |
|---|---|
| `runMerge.bash` | the merge controller — `flock`, `runAgain`, the whole pass |
| `installer/frognet_install.sh` | phases A–F; writes the dnsmasq hook, allocates identity |
| `frognet-node-guid.sh` | identity at `/etc/fnid`, mode 0444, allocated once |
| `dhcp_tracking.bash` | the dnsmasq lease hook |
| `frognet_capability_probe.sh` | what a node advertises about itself |
| `frognet_build_release.sh` | the release tarball, from an explicit path manifest |

## Traps

- **`dhcp_tracking.bash` asks the merge watcher; it must not run `runMerge.bash`** (`[LEASE_ASKS_THE_WATCHER_V1]`).
  Whatever dnsmasq's `dhcp-script` starts runs in `dnsmasq.service`'s control group, and `setsid` does not leave it:
  a merge started there is killed by its own `systemctl restart dnsmasq` (90 s of no DHCP and no DNS, then SIGKILL).
  It writes one file in `/run/frognet/merge_requests`, as the NetworkManager hook does, and returns.
- **The merge watcher keeps a request until a merge is started for it** (`[REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1]`).
  Do not make it consume the batch and write `runAgain` when the lock is held: the running merge may already have
  read `runAgain`, and the event then gets no merge. Oracle: `discovery/test_merge_request_kept_oracle.py`.
- **`frognet_build_release.sh` uses an explicit `WORLD_PATHS` manifest**, not a
  glob. If you ever change that to a glob, exclude the four repository documents
  at `/` or they will land on every node.
- **Identity is allocated once and never regenerated automatically.** `--ensure`
  is generate-if-absent. Rotation (`frognet-node-guid.sh regenerate`) is a
  deliberate act: it writes a fresh GUID and logs the one it replaced.
