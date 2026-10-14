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

Default invocation (no args): the long-lived daemon. Region mode (RINA_RAM set): run_region_loop, one held region
session, hub channels and FD&F's plan applied per region event. LAN-only: run_lan_only_loop, frognet0 and nothing
else [NO_BROKER_V1].

Subcommand:
  bring-up-only     The merge's bring-up stage (discovery.live). LAN-only: tear down any wg iface, write an empty
                    handshake_rtts.json, touch /etc/sentinels/tunnel_bringup_ready [LAN_ONLY_NO_REGION_V1]. Region:
                    reconcile the hub channels (discovery.live does not call it on a region; the daemon owns them).
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
    """Long-lived loop for this node's mode.  Default invocation."""
    from .poll import run_daemon_loop, shutdown_all
    from .wg import startup_reconcile

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.write_text(str(os.getpid()))

    # [BUILD_IN_THE_LOG_V1] what is running, in its own log
    from core.build_id import base_build              # [BASE_BUILD_V1] the FrogNet base this daemon belongs to
    config.log.warning("Starting frognet-tunnel-daemon v%s build %s (mode %s) base %s",
                       config.VERSION, config.BUILD, config.MODE, base_build())

    startup_reconcile()

    try:
        run_daemon_loop()
    except KeyboardInterrupt:
        pass

    config.log.info("Shutting down...")
    shutdown_all()
    time.sleep(2)
    config.PID_FILE.unlink(missing_ok=True)
    config.log.info("Goodbye.")


def _run_bringup_only():
    """The merge's bring-up stage: discovery.live runs it synchronously before discovery. LAN-only: every wg iface
    comes down. Region: the hub channels are reconciled (the daemon does this per event; discovery.live skips the
    stage on a region)."""
    from .poll import reconcile_bringup_only

    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        reconcile_bringup_only()
    except Exception as e:
        config.log.exception("bring-up-only: unhandled error: %s", e)
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
    if cmd == "bring-up-only":
        _run_bringup_only()
        return

    config.log.error("unknown subcommand: %s (known: bring-up-only)", cmd)
    sys.exit(2)


if __name__ == "__main__":
    main()
