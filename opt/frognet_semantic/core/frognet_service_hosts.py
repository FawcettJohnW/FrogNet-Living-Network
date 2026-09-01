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
import sys
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


# [EVERY_FROGNETHOST_IS_A_CANDIDATE_V1] A FrogNetHost IS a candidate for every role,
# by definition. Membership in the pool is NOT earned by publishing a capability row:
# a machine that has not published has UNKNOWN capability, not absent existence. The
# pool is therefore the live .1 FrogNetHost set from the converged hosts block, which
# always contains at least this node -- so "nobody is on this LAN" cannot arise from a
# missed publish, an unreachable store, or an aged-out row.
#
# What CAN legitimately happen is that nobody on the LAN MEETS THE ROLE'S CRITERIA (a
# LAN with no ffmpeg/libvpx anywhere has no media host). That is a real answer, it is
# not the same as an empty pool, and it must be LOUD.

ROLE_NOUN = {
    "mediahost": "media",
    "databasehost": "database",
    "boardgame": "boardgame",
}


def no_host_marker(role: str) -> str:
    """The commented line written into /etc/hosts when no machine on this LAN meets
    the role's criteria. A comment cannot resolve, so nothing is misdirected -- but
    the hosts file itself now SAYS why the name is missing, which is where anyone
    debugging an NXDOMAIN looks first."""
    return "#No %s servers on this LAN" % ROLE_NOUN.get(role, role)


def _alive_ones(etc_hosts) -> List[str]:
    """The live .1 FrogNetHost addresses in the converged block. Same rule
    role_barrier_ready uses, so the pool and the barrier count the same machines."""
    out = []
    for l in etc_hosts or []:
        p = l.split()
        if (len(p) >= 2 and p[0].endswith(".1")
                and any(t.startswith("FrogNetHost.") for t in p[1:])):
            if p[0] not in out:
                out.append(p[0])
    return out


def _slash24_of(ip: str) -> str:
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else ""


def service_host_lines(dbhost: str = "databasehost_control.frognet", logger=None,
                       reachable_subnets=None, self_ip=None, lan_subnets=None,
                       local_ips=None, etc_hosts=None) -> List[str]:
    """POST-merge role determination. For every live service role, return the
    `<role>.frognet <ip>` line for the committed /etc/hosts block. Each role's host is
    its UnREST callback's pick over the capability tuples (the LAN/WAN split is read
    from the reach_plane tuple, not passed in) - a PURE function of the shared converged
    memory, so every node computes the same winner. Returns [] on any failure. This is a
    read run AFTER the merge: it never triggers a re-run (only a route change does).

    `self_ip` (this node's .1): accepted for caller-signature compatibility and NOT
    used to name a winner. [NO_LOCAL_BY_DEFINITION_V1] retired local-by-definition: a
    node that names itself when the pool is empty diverges from every other node doing
    the same, and hides a failed read behind a plausible answer. A role with no
    candidates gets no line.

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
            hosts_list, lan_list = gather_candidates(h, dbhost=dbhost,
                                                     lan_subnets=lan_subnets,
                                                     local_ips=local_ips)
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
        # [EVERY_FROGNETHOST_IS_A_CANDIDATE_V1] Every live .1 FrogNetHost that did not
        # publish a capability row for this role enters the pool as a BARE candidate.
        # It exists; we simply do not know what it can do. The role's own score()
        # decides whether unknown capability is good enough -- mediahost's hard
        # ffmpeg/libvpx gate will refuse it, boardgame's flat score will not. That
        # judgement belongs to the role, not to whether a publish happened to land.
        _published = {c.get("lan_ip") for c in hosts_list}
        _lan_subs = set(lan_subnets or ())
        for _ip in _alive_ones(etc_hosts):
            if _ip in _published:
                continue
            _bare = {"lan_ip": _ip, "_unpublished": True}
            hosts_list.append(_bare)
            if not _lan_subs or _slash24_of(_ip) in _lan_subs:
                lan_list.append(_bare)
            log(f"SERVICE_CAND role={role} ip={_ip} UNPUBLISHED "
                f"(no {role}/capability row on {dbhost}; entering the pool by "
                f"definition -- every FrogNetHost is a candidate)")

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
        # [NO_FLOOR] No winner means no host. We do NOT fabricate a local winner when
        # the callback named none or evaluate raised - a fabricated winner is the floor
        # that makes databasehost/mediahost diverge per node and hides the real failure
        # (e.g. a malformed capability blob). Absence is reported as absence.
        #
        # [NO_HOST_MEETS_CRITERIA_V1] With every FrogNetHost in the pool by definition,
        # "no winner" can no longer mean "nobody is out there". It means NOBODY ON THIS
        # LAN MEETS THIS ROLE'S CRITERIA -- for mediahost, that ffmpeg and libvpx are
        # present, which score() gates hard at -1.
        #
        # That is a legitimate answer and the line is correctly skipped: naming a media
        # host that cannot encode is worse than naming none. But it must be LOUD, and
        # it must be loud in the place people look. So: stderr (the journal), the merge
        # log, AND a commented marker in /etc/hosts itself, which is the first file
        # anyone opens when a .frognet name does not resolve. A comment cannot resolve,
        # so nothing is misdirected by its presence.
        if wip is None:
            _n_pub = len([c for c in hosts_list if not c.get("_unpublished")])
            _msg = (f"SERVICE_ELECT role={role} winner=NONE reason=no_host_meets_criteria "
                    f"-- {len(lan_list)} machine(s) on this LAN, {len(hosts_list)} in the "
                    f"pond ({_n_pub} with a published capability row), and NOT ONE meets "
                    f"the {role} criteria. {role}.frognet will NOT exist until one does. "
                    f"dbhost={dbhost}")
            log(_msg)
            # Loud, in the journal, not only in the merge log a person has to go find.
            try:
                print("[SERVICE_ELECT] " + _msg, file=sys.stderr, flush=True)
            except Exception:
                pass
            lines.append(no_host_marker(role))
        log(f"SERVICE_ELECT role={role} hosts={len(hosts_list)} "
            f"lan={len(lan_list)} bad={len(bad)} winner={wip or 'none'} "
            f"dbhost={dbhost}")
        # [ELECT_EVIDENCE_V1] The verdict without the venue and the ballots is
        # uninvestigable (proven 2026-07-06: an election counted 8 candidates
        # from a store that held 2, and the log could not say where it read).
        # One line per candidate: identity, capability-blob ts age, and the
        # writer address the DB recorded. Everything the decision saw.
        try:
            import time as _t
            _now = int(_t.time())
            for _c in hosts_list:
                # [ELECT_EVIDENCE_V1 fix] candidates are FLAT capability blobs
                # (gather_candidates), not tuple rows - the first cut printed
                # '?' for every field by assuming a shape never read.
                log(f"ELECT_CANDIDATE role={role} dbhost={dbhost} "
                    f"lan_ip={_c.get('lan_ip','?')} "
                    f"ts_age={_now - int(_c.get('_ts', 0) or 0)}s "
                    f"writer_scope={_c.get('_row_name','?')} "
                    f"writer_addr={_c.get('_row_addr','?')}")
        except Exception as _e:
            log(f"ELECT_CANDIDATE role={role} dump_failed err={_e}")
        if wip:
            lines.append(f"{wip} {role}.frognet")
    return lines


def apply_to_etc_hosts(etc_hosts: List[str],
                       dbhost: str = "databasehost_control.frognet",
                       logger=None, reachable_subnets=None, self_ip=None,
                       lan_subnets=None) -> List[str]:
    """Given the assembled etc_hosts block, drop any existing <role>.frognet lines and
    append freshly-determined ones - for EVERY live service role uniformly,
    databasehost included. Each role's host is its UnREST callback's pure pick over the
    capability tuples (LAN/WAN split read from the reach_plane tuple). There is NO floor
    and NO per-node reachability cull: the determination is identical on every node by
    construction. `self_ip` is accepted and unused for winner selection
    ([NO_LOCAL_BY_DEFINITION_V1]: an empty pool names nobody). reachable_subnets is
    accepted (ignored) for caller compatibility. Idempotent; safe every converged merge. This is a post-merge read
    and never triggers a re-run."""
    role_lines = service_host_lines(dbhost=dbhost, logger=logger, etc_hosts=etc_hosts,
                                    reachable_subnets=reachable_subnets, self_ip=self_ip,
                                    lan_subnets=lan_subnets)
    if not role_lines:
        return etc_hosts
    role_names = {ln.split()[1] for ln in role_lines}
    out = [l for l in etc_hosts
           if not (len(l.split()) >= 2 and l.split()[1] in role_names)]
    out.extend(role_lines)
    return out


def commit_service_lines(etc_hosts: List[str], role_lines: List[str],
                         ready_roles, control_ip: Optional[str],
                         logger=None) -> List[str]:
    """Commit each elected role line, per that role's own barrier.

    [ROLE_ELECTION_UNCOUPLED_V1] Every registered service's platform-selection callback
    runs at the end of every converged runMerge.

    [ROLE_BARRIER_PER_ROLE_V1] Whether its result may be committed is a separate
    question, answered per role by role_barrier_ready over that role's OWN capability
    records - never over another role's.

    [BARRIER_DEFERS_CHANGE_NOT_FIRST_V1] And the barrier may defer a CHANGE. It may not
    prevent the FIRST assignment. A role with no line at all is broken - the
    Communicator dials mediahost.frognet and gets NXDOMAIN - whereas a role whose line
    might move later is merely unsettled, and the next merge settles it. Proven on a
    live pond: four machines in the hosts block had not published an admissible record
    in anywhere from 34,000 to 330,000 seconds, so every barrier was shut and stayed
    shut, and mediahost.frognet never existed at all even though the election was
    naming a healthy winner on every pass. A gate that can wait forever must not be the
    only thing standing between a working election and a hosts line.

    So: barrier open -> commit. Barrier shut and a line already stands -> keep it,
    which is the anti-flap the barrier exists for. Barrier shut and NO line stands ->
    commit anyway and say so. databasehost keeps its pin at the control while shut,
    because the control is deterministic (highest .1) and always valid, so the
    coordination plane is never nameless.
    """
    log = logger or (lambda s: None)
    ready = set(ready_roles or ())
    out = list(etc_hosts)
    committed, held, provisional, nohost = [], [], [], []
    # [NO_HOST_MEETS_CRITERIA_V1] Any marker from a PREVIOUS pass is dropped first,
    # unconditionally. It is a statement about one election; carrying a stale one
    # forward would leave "#No media servers on this LAN" sitting under a working
    # mediahost.frognet line, which is worse than saying nothing.
    _markers = {no_host_marker(r) for r in set(list(ROLE_NOUN) + list(ready))}
    out = [l for l in out if l.strip() not in _markers]

    for ln in role_lines or []:
        # [NO_HOST_MEETS_CRITERIA_V1] A marker is a COMMENT, not an assignment. It
        # replaces any standing line for that role -- a name that no longer has a
        # qualifying host must stop resolving, or callers keep dialing a machine the
        # election has just ruled unfit. The barrier does not apply: it exists to damp
        # a winner CHANGING, and this is not a winner.
        if ln.startswith("#"):
            _role = None
            for _r, _n in ROLE_NOUN.items():
                if ln.strip() == no_host_marker(_r):
                    _role = _r
                    break
            if _role:
                _name = f"{_role}.frognet"
                out = [l for l in out
                       if not (len(l.split()) >= 2 and l.split()[1] == _name)]
                nohost.append(_role)
            out.append(ln)
            continue
        name = ln.split()[1]                       # "<role>.frognet"
        role = name[:-len(".frognet")] if name.endswith(".frognet") else name
        standing = any(len(l.split()) >= 2 and l.split()[1] == name for l in out)
        if role not in ready and standing:
            held.append(role)                      # defer the change, keep the line
            continue
        if role not in ready:
            provisional.append(role)               # first assignment: never defer it
        else:
            committed.append(role)
        out = [l for l in out if not (len(l.split()) >= 2 and l.split()[1] == name)]
        out.append(ln)
    if "databasehost" not in ready and control_ip:
        out = [l for l in out if not l.endswith(" databasehost.frognet")]
        out.append(f"{control_ip} databasehost.frognet")
        log(f"SERVICE_COMMIT databasehost barrier shut -> pinned at {control_ip}")
    if provisional:
        log(f"SERVICE_COMMIT provisional={sorted(provisional)} "
            f"(barrier shut but no line stood - first assignment is never deferred)")
    if held:
        log(f"SERVICE_COMMIT held={sorted(held)} (barrier shut; prior line stands)")
    if committed:
        log(f"SERVICE_COMMIT committed={sorted(committed)}")
    if nohost:
        _m = (f"SERVICE_COMMIT no_host_meets_criteria={sorted(nohost)} "
              f"-- name(s) REMOVED from /etc/hosts and a commented marker written in "
              f"their place. Nothing on this LAN qualifies for these role(s).")
        log(_m)
        try:
            print("[SERVICE_COMMIT] " + _m, file=sys.stderr, flush=True)
        except Exception:
            pass
    return out


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "databasehost_control.frognet"
    _log = lambda s: print(s, file=sys.stderr)   # diagnostics -> stderr, lines -> stdout
    for ln in service_host_lines(db, logger=_log):
        print(ln)
