# AI_README — node tooling and the installer

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

221 files: the installer, the merge controller, node tools, hooks. Shell and
Python, mostly shell.

| | |
|---|---|
| `runMerge.bash` | the merge controller — `flock`, `runAgain`, the whole pass |
| `installer/frognet_install.sh` | phases A–F; writes the dnsmasq hook, allocates identity |
| `frognet-node-guid.sh` | identity at `/etc/fnid`, mode 0444, allocated once |
| `dhcp_tracking.bash` | the dnsmasq lease hook |
| `frognet_capability_probe.sh` | what a node advertises about itself |
| `frognet_build_release.sh` | the release tarball, from an explicit path manifest |

## Traps

- **`dhcp_tracking.bash` must detach the merge, not `exec` it.** dnsmasq runs
  `dhcp-script` synchronously and waits. `exec`ing `runMerge` held that slot for
  the whole merge and stalled DHCP for every other client on the segment. It uses
  `setsid` — a bare `&` is not enough, because dnsmasq may reap the process group.
- **`probe_orig.sh` is not deadwood.** It is the pre-fix probe kept as the failing
  control for `oracle_avcap_cache.sh`. Deleting it makes that oracle
  unfalsifiable.
- **`frognet_build_release.sh` uses an explicit `WORLD_PATHS` manifest**, not a
  glob. If you ever change that to a glob, exclude the four repository documents
  at `/` or they will land on every node.
- **Identity is allocated once and never regenerated automatically.** `--ensure`
  is generate-if-absent. Rotation is a deliberate act that must retire the old
  GUID at the broker first.
