# frognet-mergechurn-20260912

Untar at `/`, then run the post-install. Both steps, on every node.

    sudo tar xzvf frognet-mergechurn-20260912.tar.gz -C /
    sudo /usr/local/bin/frognet_postinstall_20260912.sh

The post-install is required. The tarball only writes files; every removal,
in-place edit and service restart is in the script. It does not run a merge.

## What it restarts

`dnsmasq` (conf.d changed and SIGHUP cannot re-read the conf-dir),
`frognet-proxy` and `frognet-daemon` (both import `core/frognet_tuples.py`,
which changed, and both are long-lived). Plus one `daemon-reload`. Nothing
else — `discovery/` is re-execed by every merge and the NM dispatcher is read
per event.

## Why merges were constant

Five independent causes, all of them firing on things that were not topology
changes.

1. **`core/frognet_tuples.py`** — every store read or write in the proxy and
   the daemon fired `runMerge` on failure, and a client-side timeout counted as
   a failure meaning "the host is gone". `_values_raw` defaults to `timeout=4.0`
   against a mesh with a ~97ms / ~0.5 MB/s link. Timeouts are now `StoreSlow`
   and do not trigger. Unreachable now needs 3 consecutive failures for the same
   store, rate-limited to one merge per 600s per node, shared between the proxy
   and daemon through `/run/frognet/last_store_merge_trigger`.

   The old code had no limiter on purpose, reasoning that `runMerge`'s flock
   bails on `lock_held`. It does not discard the request — it touches
   `runAgain`, so the merge already running re-invokes, up to
   `MAX_MERGE_DEPTH=10`. The lock is an amplifier.

2. **`90-frognet-merge`** — ran `runMerge.bash &` on every interface up and
   down. The signature block that was supposed to gate it computed a value,
   wrote it to a file, and never read it back; the comparison was never
   written. Only `wg0` is unmanaged, so every `wg1`+ transition dispatched a
   merge. Now fires only when the FrogNet `/24`s or the default route actually
   changed, and pokes the accumulator so a burst coalesces into one merge.

3. **`frognet-pond-bootstrap.sh`** — `systemctl restart frognet-tunnel-daemon-v3`
   on every merge and every 120s timer tick, and it only wrote its sentinel if
   `is-active` came back true, so a node that never got there restarted the
   daemon forever. Each restart rebuilt every wg iface, which moved every
   channel `/24`, which is a real routing change, which propagated to every
   peer. That is what drove `10.120.120.0/24` and `10.130.130.0/24` in and out
   of the MacBook's table all hour. Now starts the daemon only if it is down,
   and the sentinel is keyed on enrolment, which is what the script is
   responsible for.

4. **`frognet_up_clean.sh`** — restarted NetworkManager and then dnsmasq when
   `FROGNET_RESTART_NETWORK=1`, reached from `frognet-transit-boot.service` at
   boot. Block removed.

5. **`discovery/routes.py` + `live.py` + `runmerge.py` + `discovery.py`** —
   `runAgain` was set from `route_table_mutated`, which is true whenever a write
   returns rc=0, including `ip route replace` on an already-correct route and a
   `/24` that reap removed and promote re-installed in the same pass. `runAgain`
   is now external-only: the merge never writes the sentinel, so a re-run means
   something arrived from outside while the pass was in flight. The propagate
   gate (`sync_required`) is a real before/after comparison of the routing
   table, taken after the fixDefault/manageResolv tail.

   `rtmut` also compares before writing now, so no route is added, replaced or
   deleted unless it is actually changing, and the probe walk no longer deletes
   a `/32` between candidates when the next candidate replaces it anyway.

## Other fixes in here

- **The plane is `10.` minus `10.253.` and `10.254.`**, everywhere. Seven files
  still tested for `10.10*` or `10.101.`, which no current node matches:
  `dhcp_tracking.bash` ignored every lease, `frognet-wg-cleanup` marked every
  live tunnel DEAD, `frognet-roam` rejected every valid echo,
  `frognet-tunnel` and the legacy daemon died at identity detection.
  **Consequence:** DHCP now fires merges it has not been firing. One per lease
  add and delete, which is correct, and is new load.

- **No DNS fallback.** `no-resolv` + `server=8.8.8.8` removed from
  `upstream_fallback.conf` and `fix_unbound_bypass.sh`. `no-resolv` was
  disabling `resolv-file`, so the merge maintained an upstream file dnsmasq
  never read and external DNS went to Google instead of the next hop.

- **`dnsmasq.service.d/override.conf`** — `After=` was split across two lines,
  so systemd logged `Missing '=', ignoring line` on every reload and the
  ordering dependency did not exist. One line now, in the shipped file and in
  the installer heredoc.

- **DHCP scoping** — `no-dhcp-interface` was built from the interfaces that
  existed when setup ran, so tunnels created later got DHCP served across them.
  Generator fixed; the post-install patches the file already on disk.

- **Deleted**: the legacy tunnel daemon and its orphan `.service.d`,
  `setup_lillypad.bash` / `_v3` in all three locations, `ham_concentrator_up.sh`,
  `dnsmasq_merge_trigger.sh`, `forward_to_unbound.conf`. Build excludes updated
  in `make_tar.bash` and `frognet_build_release.sh` — they only excluded the
  `usr/local/bin` paths, which is how the `internet_tunnels_v3` copies kept
  shipping.

## Not included, on purpose

`etc/dnsmasq.d/opts_only.conf` carries this node's `domain=` and `dhcp-range=`.
Shipping one node's copy would repoint every node's identity. The post-install
edits whatever is already on disk.

## Known open items

- `/etc/sentinels/nm_dispatch_sig`, the dispatcher's signature file, is wiped by
  `runMerge.bash`'s `rm -rf /etc/sentinels/*`. The first dispatcher event after
  each merge will therefore always merge. You said leave it; `/run/frognet/`
  is the one-line fix when you want it.
- `internet_tunnels_v3/peer.py` holds a dead duplicate of `poll.py`'s reconcile
  and poll loop, including its own `runMerge` forks. Only `Peer` and `registry`
  are imported from it.
- `10.199.199.47` never completes its RETURN; `10.253.203.89` advertises a
  different `return_ip` every session. Still undiagnosed.
- Proxy/daemon runtime errors, ~58/hr each: `routing_resilience_snapshot` and
  `pipeline_stats` NameErrors, `core.frognet_tuples has no attribute heartbeat`,
  and `upsert_batch` returning 500.
- `test_license_oracle`: 31 files without GPL headers. Pre-existing, unrelated.
