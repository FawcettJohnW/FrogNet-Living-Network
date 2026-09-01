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
  reconcile     One-shot: run the full tunnel reconciliation against
                the current broker channel list and the latest
                sync_interfaces discovery output.  This is what
                mergeHostsAndResolv.bash invokes at the end of its
                pass so that tunnel creation happens AFTER discovery
                has populated /etc/sentinels/discovered_hosts.  Exits
                with status 0 on success (whether or not anything
                changed), 1 on error.
"""

import os
import sys
import time

from . import config
from .config import load_config


def _preflight():
    """Root check + required binaries.  Shared by both invocation paths."""
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
    """One-shot tunnel reconciliation.  Called by mergeHostsAndResolv at
    the end of runMerge so tunnel create/teardown decisions take into
    account what sync_interfaces just discovered.

    Loads local state first (so _active_tunnels reflects on-disk truth),
    holds a flock to serialize against a concurrent daemon poll or a
    concurrent second runMerge, does the four-way diff, returns.

    No process management, no signal handlers, no poll loop."""
    from .poll import reconcile_tunnels, _load_local_state

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    _load_local_state()

    try:
        reconcile_tunnels()
    except Exception as e:
        config.log.exception("reconcile: unhandled error: %s", e)
        sys.exit(1)

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

    config.log.error("unknown subcommand: %s (known: reconcile)", cmd)
    sys.exit(2)


if __name__ == "__main__":
    main()
