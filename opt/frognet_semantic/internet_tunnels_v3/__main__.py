################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
frognet-tunnel-daemon v3 entry point.

Default invocation (no args): run the long-lived daemon with its poll
loop - the passive broker-channel-list watcher that fires runMerge on
change.

Subcommands:
  reconcile         One-shot: bring-up + commit in a single flock pass.
                    Preserved for back-compat; not used by the current
                    mergeHostsAndResolv pipeline, which prefers the
                    split verbs below.

  bring-up-only     Fetch broker list, tear down orphans/removed
                    channels, bring up missing channels in parallel.
                    No observations, no committer, no state reconcile.
                    Exits after signaling /etc/sentinels/tunnel_bringup_ready
                    so sync_interfaces's wave-2 barrier can proceed.

  commit-only       Write channel_map.json, run the committer (which
                    consumes observations sync_interfaces emitted),
                    reconcile Python state with the kernel.
"""

import os
import sys
import time

from . import config
from .config import load_config


def _preflight():
    """Root check + required binaries.  Shared by every invocation path."""
    if os.geteuid() != 0:
        config.log.error("Must run as root")
        sys.exit(1)

    for cmd in ("wg", "wg-quick", "ip"):
        if not any(os.access(os.path.join(p, cmd), os.X_OK)
                   for p in os.environ.get("PATH", "/usr/bin:/usr/sbin").split(":")):
            config.log.error("Required command '%s' not found", cmd)
            sys.exit(1)


def _run_daemon():
    """Long-lived poll loop.  Default invocation."""
    from .poll import run_poll_loop, shutdown_all
    from .wg import startup_reconcile

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.write_text(str(os.getpid()))

    startup_reconcile()

    config.log.info("Starting frognet-tunnel-daemon v%s (poll_interval=%ds)",
                    config.VERSION, config.POLL_INTERVAL_SEC)

    try:
        run_poll_loop()
    except KeyboardInterrupt:
        pass

    config.log.info("Shutting down...")
    shutdown_all()
    time.sleep(2)
    config.PID_FILE.unlink(missing_ok=True)
    config.log.info("Goodbye.")


def _run_reconcile():
    """One-shot tunnel reconciliation (back-compat single-verb).
    Runs bring-up then commit inside a single flock."""
    from .poll import reconcile_tunnels, _load_local_state

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    _load_local_state()

    try:
        reconcile_tunnels()
    except Exception as e:
        config.log.exception("reconcile: unhandled error: %s", e)
        sys.exit(1)

    sys.exit(0)


def _run_bringup_only():
    """Broker-authoritative tunnel reconcile.  mergeHostsAndResolv
    fires this synchronously at the start of the merge cycle, before
    sync_interfaces runs.  Despite the historical name, this is now
    the full broker-side reconcile: kernel wg ifaces are made to
    match the broker's response exactly.  If broker is unreachable
    or this node has no own WAN uplink, all wg ifaces are torn down.
    """
    from .poll import reconcile_bringup_only

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        reconcile_bringup_only()
    except Exception as e:
        config.log.exception("bring-up-only: unhandled error: %s", e)
        sys.exit(1)

    sys.exit(0)


def _run_commit_only():
    """Commit phase only.  mergeHostsAndResolv fires this at the end
    of the merge cycle after sync_interfaces has emitted all
    observations.  Reads per-tunnel JSON written to disk by the
    bring-up-only subprocess earlier in THIS merge cycle - the disk
    is intra-cycle plumbing between two short-lived Python processes,
    not a cross-restart cache.  Each merge cycle starts fresh: bring-up
    overwrites the cache from broker state, commit-only consumes it,
    then the next cycle does it again.
    """
    from .poll import reconcile_commit_only, _load_local_state

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    _load_local_state()

    try:
        reconcile_commit_only()
    except Exception as e:
        config.log.exception("commit-only: unhandled error: %s", e)
        sys.exit(1)

    sys.exit(0)


def _run_broker_reachable():
    """Exit 0 if the broker is reachable, 1 if not. This is the single
    authoritative 'online' test for the Internet<->LAN auto-switch: online
    means the broker answers, NOT that the node has direct Internet. Reuses
    the daemon's own broker client (same URL, pubkey, timeouts) so the
    definition lives in exactly one place. No side effects: it only asks
    my-channels and reports reachability.
    """
    from .poll import broker_get
    try:
        broker_get("/api/v4/my-channels", {"pubkey": config.PUBKEY})
    except Exception as e:
        config.log.info("broker-reachable: NO (%s)", e)
        sys.exit(1)
    config.log.info("broker-reachable: YES")
    sys.exit(0)


def main():
    load_config()
    _preflight()

    argv = sys.argv[1:]
    if not argv:
        _run_daemon()
        return

    cmd = argv[0]
    if cmd == "reconcile":
        _run_reconcile()
        return
    if cmd == "bring-up-only":
        _run_bringup_only()
        return
    if cmd == "commit-only":
        _run_commit_only()
        return
    if cmd == "broker-reachable":
        _run_broker_reachable()
        return

    config.log.error(
        "unknown subcommand: %s "
        "(known: reconcile, bring-up-only, commit-only, broker-reachable)",
        cmd)
    sys.exit(2)


if __name__ == "__main__":
    main()
