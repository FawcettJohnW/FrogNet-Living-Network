#!/opt/frognet_semantic/venv/bin/python3
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
daemon/daemon_main.py

FrogNet Semantic Daemon - engine-based implementation (authoritative).

Why this replacement is required:
- It preserves your invariant: execute locally ONLY if dest_host resolves to a local interface.
- It requires explicit dest_host (fail closed).
- It uses daemon/upstream/client.py, which marks SO_MARK before connect for upstream :80,
  preventing nat OUTPUT recursion storms.

Timeout policy:
- Allows slow links up to 45 seconds end-to-end (env override supported).
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import os
import sys
import argparse

_THIS_FILE = os.path.abspath(__file__)
_THIS_DIR = os.path.dirname(_THIS_FILE)
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from daemon.engine.server import DaemonServer
from daemon.engine.resolver import DestinationResolver
# from daemon.daemon_metrics import start_daemon_metrics_flusher
from daemon.daemon_metrics import start_daemon_flusher
from daemon.cache import semcache_db as _daemon_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="FrogNet Semantic Daemon")
    parser.add_argument("--listen", default=os.environ.get("FROGNET_DAEMON_LISTEN", "0.0.0.0:9009"))
    args = parser.parse_args()

    try:
        host, port_str = args.listen.rsplit(":", 1)
        port = int(port_str)
    except Exception:
        raise SystemExit(f"Invalid --listen value '{args.listen}'. Expected host:port")

    # Start metrics flusher ONCE (daemon_metrics is fail-safe)
    # start_daemon_metrics_flusher()
    # Start metrics flusher
    start_daemon_flusher(interval=30.0)

    # [PUBLISH_IN_ALL_CONTEXTS_V1] A headless node (no communicator) still publishes its
    # own <role>/capability to control + data - at boot and on every databasehost float -
    # so the election can see this node's perf. Without this, only the communicator ever
    # published and a daemon-only node would be invisible to service-host election.
    # [NO_FALLBACK_V1] This was a WARNING-and-continue. The consequence is not
    # cosmetic: without the publish watcher this node never writes its
    # <role>/capability tuple, so service-host election cannot see it and every
    # other node elects around it. That is a silent, node-shaped hole in the
    # election - the class of fault the transition primer records as nodes
    # disagreeing about who the databasehost is.
    #
    # core.role_publish is first-party. A daemon that cannot publish its own
    # capability must not pretend to be a participating member.
    from core.role_publish import start_publish_watcher
    start_publish_watcher(logger=lambda s: None)

    # Start cache eviction (idempotent)
    try:
        _daemon_cache.start_eviction()
    except Exception:
        pass

    # ---- Initialize persistent reference caches before accepting connections ----
    try:
        _daemon_cache.ensure_table()
    except Exception as e:
        print(f"[Daemon] WARNING: cache init failed: {e!r}", flush=True)

    # [TEMPLATE_STORE_ENSURE_V1] The daemon reads templates by opcode, so it
    # needs the same tables the proxy does and can be started first. Idempotent
    # with the proxy's call.
    #
    # WARN-and-continue, deliberately, matching the daemon cache init directly
    # above and NOT the proxy's fail-closed call. The two are different
    # situations. The proxy already refuses to boot without MySQL one line
    # earlier, at semcache_db.ensure_table(), so failing closed there costs
    # nothing. The daemon does not: it boots and warns. An unguarded call here
    # would turn "MySQL is not up yet" from a warning into a daemon that
    # refuses to start -- a behaviour change on every running node whose
    # mariadb.service happens to come up after frognet-daemon. That is a
    # regression this fix must not introduce.
    #
    # Recovery is automatic: _tables_ensured stays False on failure, so the
    # next caller retries.
    try:
        from core.store import TemplateStore as _TplStore
        _TplStore().ensure_tables()
    except Exception as e:
        print(f"[Daemon] WARNING: template table init failed: {e!r}", flush=True)

    resolver = DestinationResolver()
    server = DaemonServer(host=host, port=port, resolver=resolver)

    # No extra behavior here. All semantics live in engine/session + engine/execution.
    server.run()


if __name__ == "__main__":
    main()
