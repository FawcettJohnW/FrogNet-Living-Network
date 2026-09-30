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
frognet_host_reset.py - the ONE reconcile path. Memory, not messages.

Doctrine (case 1; case 2 == case 1, so there is only one path): something changed the
ground under us - joined a greater network and the merge completed, or the databasehost
floated, or anything at all. What was may not be; what is may be yet to be discovered.
So every module runs the SAME trigger-agnostic reconcile:

    for each module:  reset world -> ensure MY consistency -> write MY memory
    then once:        read the SHARED memory by tuple vector (re-establish relationships)
    culminating in:   render the Communicator UI - the truth behind the living network.

Per-module reset/consistency/write is each module's hostReset() (WorkingMemory /
Channel bases). This dispatcher owns the shared-vector read and the UI culmination, so
they happen ONCE over everyone's freshly re-asserted memory - not N times mid-reassert.

The trigger is the existing change detector, never a new message: on a frognet the
merge knows the databasehost line moved (live._write_if_changed verdict / somethingChanged
$db_fp); off-mesh, the nslookup IP-delta. Either just calls host_reset_all().
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional


def host_reset_all(modules: List[Any],
                   read_shared_vector: Optional[Callable[[], List[Any]]] = None,
                   render_ui: Optional[Callable[[List[Any]], Any]] = None,
                   logger: Callable[[str], Any] = lambda s: None) -> Dict[str, Any]:
    """Run the canonical reconcile across all modules, then read shared memory and
    render the living network. Order is load-bearing: ALL modules re-assert their own
    memory FIRST (consistency-then-write, each), and only THEN do we read the shared
    vector - so relationships are established over a transient every module has already
    repopulated, never over a half-reasserted one."""
    reports = []
    for m in modules:
        hr = getattr(m, "hostReset", None)
        if not callable(hr):
            continue                                   # a module may legitimately hold nothing
        try:
            rep = hr() or {"module": type(m).__name__}
        except Exception as e:                         # one module must not sink the reconcile
            rep = {"module": type(m).__name__, "error": str(e)}
            logger(f"HOSTRESET module={rep['module']} error={e}")
        reports.append(rep)
        logger(f"HOSTRESET module={rep.get('module')} "
               f"reset={len(rep.get('reset', []))} written={len(rep.get('written', []))} "
               f"consistency={rep.get('consistency', 'ok')}")

    # read shared memory BY TUPLE VECTOR - the relationships with people/services now
    # on the network. Caller supplies the read (roster/presence/service tuples).
    vector: List[Any] = []
    if read_shared_vector:
        try:
            vector = list(read_shared_vector() or [])
        except Exception as e:
            logger(f"HOSTRESET shared_vector_read error={e}")

    # culminate: the Communicator UI - the living network made visible.
    if render_ui:
        try:
            render_ui(vector)
        except Exception as e:
            logger(f"HOSTRESET render_ui error={e}")

    logger(f"HOSTRESET done modules={len(reports)} vector={len(vector)}")
    return {"modules": reports, "vector": vector,
            "ui_rendered": render_ui is not None}


class HostResetWatcher:
    """Per-process float detector + reconcile trigger. A node sees a databasehost(_control)
    float the same trivial way any node can - the resolved IP changes between ticks
    (nslookup-delta, in-process). On a delta it runs the one reconcile over THIS process's
    live modules. Every module-holding process (the Communicator shell, each bundle app)
    ticks one of these in its existing loop; nothing is pushed - the shared truth moved and
    we noticed. First observation primes the baseline (the process already did its startup
    render); reconcile fires on subsequent deltas."""

    def __init__(self, resolve_ip: Callable[[], Optional[str]],
                 get_modules: Callable[[], List[Any]],
                 read_shared_vector: Optional[Callable[[], List[Any]]] = None,
                 render_ui: Optional[Callable[[List[Any]], Any]] = None,
                 logger: Callable[[str], Any] = lambda s: None):
        self._resolve = resolve_ip
        self._modules = get_modules
        self._read = read_shared_vector
        self._render = render_ui
        self._log = logger
        self._last: Optional[str] = None

    def tick(self) -> bool:
        """Resolve the host; on a change since last tick, reconcile. Returns True iff a
        reconcile fired. Never raises - a watcher must not break its host loop."""
        try:
            ip = self._resolve()
        except Exception as e:
            self._log(f"HOSTRESET_WATCH resolve_err={e}")
            return False
        if not ip or ip == self._last:
            return False
        first = self._last is None
        self._last = ip
        if first:
            self._log(f"HOSTRESET_WATCH baseline host={ip}")
            return False
        self._log(f"HOSTRESET_WATCH float -> {ip}; reconciling")
        try:
            host_reset_all(self._modules(), self._read, self._render, self._log)
        except Exception as e:
            self._log(f"HOSTRESET_WATCH reconcile_err={e}")
        return True
