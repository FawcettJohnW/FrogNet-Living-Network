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
frognet_service_hosts - generic merge-end service-host election.

At the end of a converged runMerge pass, derive the LIVE service set from the
transient (consolidating the individual <Role>Candidate tuples + the System sensor),
elect each role through its registry handler, and emit `<role>.frognet <winner>`
lines for the committed /etc/hosts block. ONE generic loop - no per-role hook.

Dynamic, real-time, derived: the service list is not maintained anywhere. A role is
live when it has BOTH a candidate tuple present in the transient AND a registered
handler in format_registry (candidate without a handler can't be scored; handler
without candidates has nobody to elect). Drop in the first AIHost candidate and
aihost.frognet appears; pull the last and it falls out.

Memory, not messages: reads tuples, never probes. Any error degrades to "emit no
role lines this pass" so a merge can never break on this.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from core import frognet_tuples as T


def _role_handlers(log=lambda s: None) -> Dict[str, object]:
    """role_name -> handler. Prefer the one registry in core; if importing it fails,
    log the REAL exception (don't guess) and rebuild ROLE_HANDLERS by importing the
    role-handler modules DIRECTLY. format_registry also imports the lxml-backed FORMAT
    handlers at module top and (on an older box) may predate ROLE_HANDLERS entirely -
    either would otherwise strand the role handlers, which carry no such dependency."""
    for modpath in ("core.role_registry", "role_registry",
                    "core.format_registry", "format_registry"):
        try:
            mod = __import__(modpath, fromlist=["ROLE_HANDLERS"])
            return dict(mod.ROLE_HANDLERS)
        except Exception as e:
            log(f"SERVICE_ELECT role_registry_import_failed path={modpath} err={e!r}")
    out: Dict[str, object] = {}
    for role, modname, clsname in (
            ("mediahost",    "sotf_handler",     "SotFMediaHandler"),
            ("databasehost", "database_handler", "DatabaseRoleHandler")):
        err = None
        for pkg in ("core.", ""):
            try:
                m = __import__(pkg + modname, fromlist=[clsname])
                out[role] = getattr(m, clsname)()
                break
            except Exception as e:
                err = e
        else:
            log(f"SERVICE_ELECT role={role} direct_load_failed err={err!r}")
    return out


def _live_roles(handlers: Dict[str, object], dbhost: str) -> List[str]:
    """Consolidate the individual service tuples into the live service set: a role is
    live iff its handler's CANDIDATE_TYPE has at least one tuple present, OR a .1 host
    advertises the role's capability via the System sensor. Derived from what's
    actually registered - nothing to maintain."""
    live = []
    for name, h in handlers.items():
        ctype = getattr(h, "CANDIDATE_TYPE", None)
        present = False
        if ctype:
            try:
                present = bool(T.get(ctype, "Candidate", dbhost=dbhost))
            except Exception:
                present = False
        # even with no specialist candidate, the .1 hosts are implicit candidates
        # (System sensor); keep the role live so a .1 can still win it.
        live.append(name)
    return live


def service_host_lines(dbhost: str = "databasehost_control.frognet", logger=None,
                       reachable_subnets=None, self_ip=None) -> List[str]:
    """POST-merge role determination. For every live service role, return the
    `<role>.frognet <ip>` line for the committed /etc/hosts block. Each role's host is
    its UnREST callback's pick over the capability tuples (the LAN/WAN split is read
    from the reach_plane tuple, not passed in) - a PURE function of the shared converged
    memory, so every node computes the same winner. Returns [] on any failure. This is a
    read run AFTER the merge: it never triggers a re-run (only a route change does).

    `self_ip` (this node's .1): used ONLY for local-by-definition - when a callback names
    no winner from the tuples, the local machine is the host (the asking node always
    qualifies; never invent an absent host).

    `reachable_subnets`: RETIRED. A per-node reachability cull made the winner depend on
    THIS box's routes, which split the network (a leaf keeps itself while its peers drop
    it). The determination is the callback over the SHARED tuples, with no per-node
    filter. Accepted and ignored for caller-signature compatibility. Liveness of a dead
    peer's still-fresh tuple is a tuple-lifecycle/reaper concern, not the determination's.

    `logger(str)` (optional) receives one-shot determination diagnostics so a merge log
    (or the `python3 frognet_service_hosts.py` dry-run) shows exactly what each role saw -
    every candidate's bucket(lan/wan) + ffmpeg/libvpx + score, and the winner (or
    local-by-definition). Pure read; no behaviour change when logger is None.
    """
    log = logger or (lambda s: None)
    try:
        try:
            from core.frognet_role_elect import gather_candidates, _slash24
        except Exception:
            from frognet_role_elect import gather_candidates, _slash24   # bundle-context fallback
    except Exception as e:
        log(f"SERVICE_ELECT skipped reason=role_elect_import_failed err={e}")
        return []
    handlers = _role_handlers(log)
    if not handlers:
        log("SERVICE_ELECT skipped reason=no_role_handlers "
            "(registry AND direct role-handler load both failed - see errs above)")
        return []
    lines = []
    for role in _live_roles(handlers, dbhost):
        h = handlers[role]
        try:
            hosts_list, lan_list = gather_candidates(h, dbhost=dbhost)
        except Exception as e:
            log(f"SERVICE_ELECT role={role} reason=gather_failed err={e}")
            continue
        # [NO_REACHABILITY_CULL_V1] The determination is the role's UnREST callback over
        # the SHARED capability tuples - a pure function of the converged memory, the same
        # on every node. We do NOT cull the pool by THIS node's routes: a per-node
        # reachability filter makes the winner depend on local kernel state, so a leaf
        # keeps itself while its peers drop it -> split databasehost. Liveness is not the
        # determination's job here (a dead host's tuple is a tuple-lifecycle/reaper
        # concern). reachable_subnets is accepted for caller-signature compatibility and
        # deliberately ignored.
        lan_ips = {c.get("lan_ip") for c in lan_list}
        bad = set()
        for c in hosts_list:
            ip = c.get("lan_ip", "?")
            sc = ""
            if hasattr(h, "score"):
                try:
                    sc = h.score(c)
                except Exception as e:
                    sc = f"err({e})"
                    bad.add(ip)
                    # [EVAL_ISOLATE_V2] One node's malformed capability blob must not deny
                    # the role to the whole pond. Exclude that candidate and name it LOUDLY
                    # so the bad publish is visible - this is surfacing, not a floor: nothing
                    # is fabricated, the election proceeds over the VALID candidates only.
                    log(f"SERVICE_CAND role={role} ip={ip} MALFORMED excluded err={e}")
            log(f"SERVICE_CAND role={role} ip={ip} "
                f"bucket={'lan' if ip in lan_ips else 'wan'} "
                f"ffmpeg={c.get('ffmpeg')} libvpx={c.get('libvpx')} score={sc}")
        clean_hosts = [c for c in hosts_list if c.get("lan_ip") not in bad]
        clean_lan   = [c for c in lan_list   if c.get("lan_ip") not in bad]
        try:
            winner = h.evaluate(clean_hosts, clean_lan)
        except Exception as e:
            log(f"SERVICE_ELECT role={role} reason=evaluate_failed err={e}")
            winner = None
        wip = winner.get("lan_ip") if winner else None
        # [NO_FLOOR] No winner means no host. We do NOT fabricate self as the host when
        # the callback named none or evaluate raised - a fabricated local winner is the
        # floor that makes databasehost/mediahost diverge per node and hides the real
        # failure (e.g. a malformed capability blob). Absence is reported as absence.
        log(f"SERVICE_ELECT role={role} hosts={len(hosts_list)} "
            f"lan={len(lan_list)} bad={len(bad)} winner={wip or 'none'}")
        if wip:
            lines.append(f"{wip} {role}.frognet")
    return lines


def apply_to_etc_hosts(etc_hosts: List[str],
                       dbhost: str = "databasehost_control.frognet",
                       logger=None, reachable_subnets=None, self_ip=None) -> List[str]:
    """Given the assembled etc_hosts block, drop any existing <role>.frognet lines and
    append freshly-determined ones - for EVERY live service role uniformly,
    databasehost included. Each role's host is its UnREST callback's pure pick over the
    capability tuples (LAN/WAN split read from the reach_plane tuple). There is NO floor
    and NO per-node reachability cull: the determination is identical on every node by
    construction. `self_ip` is this node's .1, used only for local-by-definition when a
    callback names no winner. reachable_subnets is accepted (ignored) for caller
    compatibility. Idempotent; safe every converged merge. This is a post-merge read
    and never triggers a re-run."""
    role_lines = service_host_lines(dbhost=dbhost, logger=logger,
                                    reachable_subnets=reachable_subnets, self_ip=self_ip)
    if not role_lines:
        return etc_hosts
    role_names = {ln.split()[1] for ln in role_lines}
    out = [l for l in etc_hosts
           if not (len(l.split()) >= 2 and l.split()[1] in role_names)]
    out.extend(role_lines)
    return out


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "databasehost_control.frognet"
    _log = lambda s: print(s, file=sys.stderr)   # diagnostics -> stderr, lines -> stdout
    for ln in service_host_lines(db, logger=_log):
        print(ln)
