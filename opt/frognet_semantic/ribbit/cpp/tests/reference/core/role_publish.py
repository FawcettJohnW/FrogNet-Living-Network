#!/usr/bin/env python3
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
core/role_publish.py - drive the role handlers' capability publish in ALL contexts.

`publish_all()` probes this node once and asks every role handler to `advertise()` its own
<role>/capability to BOTH databases (control = authoritative election input, data = mirror).
It is the single call the daemon, the discovery merge, and the communicator each make -
wherever it runs, every role this node can serve is published. Election is therefore
independent of any one app: it reads what publish_all() wrote, no matter who ran it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .role_registry import ROLE_HANDLERS
from .unrest_handler import CAPABILITY_PROBE, UnRESTHandler


def publish_all(blob: Optional[Dict[str, Any]] = None,
                probe: str = CAPABILITY_PROBE,
                control: Optional[str] = None,
                logger=lambda s: None) -> List[Dict[str, Any]]:
    """Probe once, then every role handler advertises its capability to control + data.
    Sharing one probe across roles is why the per-handler advertise() accepts a blob.
    `control`, when given, overrides the CONTROL_DBHOST name so the caller can publish
    to the SAME freshly-resolved control IP the merge-end election reads from (the name
    lags a merge until /etc/hosts is committed)."""
    if blob is None:
        blob = UnRESTHandler._probe_capability(probe, logger)
    reports: List[Dict[str, Any]] = []
    for handler in ROLE_HANDLERS.values():
        try:
            _kw = {"blob": blob, "logger": logger}
            if control:
                _kw["control"] = control
            reports.append(handler.advertise(**_kw))
        except Exception as e:                       # one role must not sink the publish
            reports.append({"handler": type(handler).__name__, "error": str(e)})
            logger(f"PUBLISH_ALL {type(handler).__name__} err={e}")
    return reports


def host_reset_all_roles(logger=lambda s: None) -> List[Dict[str, Any]]:
    """Trigger-agnostic reconcile across role handlers - re-publish on boot/merge/float."""
    return publish_all(logger=logger)


class RolePublishReconcile:
    """A HostResetWatcher module: on reconcile (boot/float), re-publish every role's
    capability to both DBs. `hostReset()` is the trigger-agnostic entry the watcher calls,
    so role publish rides the ONE reconcile path like every other module."""

    def __init__(self, logger=lambda s: None):
        self._log = logger

    def hostReset(self) -> Dict[str, Any]:
        return {"role_publish": publish_all(logger=self._log)}


def _resolve_dbhost_ip(host: str = "databasehost_control.frognet") -> Optional[str]:
    """[HOSTS_ONLY_V1] /etc/hosts, never the resolver.

    This drives the publish watcher's "has the database host moved" check. Asking DNS
    meant the answer could differ from the file the rest of the node routes by --
    measured on a node whose resolv.conf is `nameserver 127.0.0.1`: the file said
    10.250.250.1 and gethostbyname said 10.130.130.1. A watcher that resolves
    differently from the writer re-publishes at the wrong moments, or not at all.
    """
    try:
        from core.hosts_only import try_resolve
        return try_resolve(host)
    except Exception:
        return None


def start_publish_watcher(interval: float = 15.0, logger=lambda s: None):
    """Headless float-publish for the daemon/proxy (no communicator needed): publish all
    roles once at boot, then a background thread that detects a databasehost(_control) IP
    delta and re-publishes via the one reconcile path. Returns the thread (or None)."""
    import threading
    import time as _time
    try:
        from core.frognet_host_reset import HostResetWatcher
    except Exception as e:
        logger(f"publish_watcher unavailable (no host_reset): {e}")
        publish_all(logger=logger)            # at least boot-publish
        return None
    publish_all(logger=logger)                # boot
    watcher = HostResetWatcher(
        resolve_ip=_resolve_dbhost_ip,
        get_modules=lambda: [RolePublishReconcile(logger)],
        logger=logger)

    def _loop():
        while True:
            try:
                watcher.tick()
            except Exception:
                pass
            _time.sleep(interval)

    t = threading.Thread(target=_loop, name="role-publish-watch", daemon=True)
    t.start()
    return t
