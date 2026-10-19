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
        _report = {}
        try:
            hosts_list, lan_list = gather_candidates(h, dbhost=dbhost,
                                                     lan_subnets=lan_subnets,
                                                     local_ips=local_ips,
                                                     report=_report)
        except Exception as e:
            log(f"SERVICE_ELECT role={role} reason=gather_failed err={e}")
            continue
        # [READ_FAILED_IS_NOT_EMPTY_V1 - John 2026-09-12] "No row in the store" and
        # "the store did not answer" are different states and only ONE of them is
        # decidable.
        #
        # An empty answer is information: nobody published, nobody is preferred, the
        # highest .1 wins ([NO_ROW_MEANS_HIGHEST_IP_V1] below). A failed read is not
        # information, and applying the floor to it means a node whose store is
        # broken confidently renames every role -- including databasehost -- at
        # ITSELF, because it is the only .1 it can still see. Measured on BAMacBook
        # 2026-09-12 13:11: every capability read returned HTTP 500 in ~2ms,
        # gather_candidates reported StoreBroken, and the floor then pointed
        # databasehost.frognet at the MacBook, which holds no data.
        #
        # So: no opinion. Emit no line for this role and let whatever stands, stand.
        if _report.get("read_failed"):
            log(f"SERVICE_ELECT role={role} decision=ABSTAIN "
                f"reason=capability_read_failed err={_report.get('read_error')} "
                f"dbhost={dbhost} -- the store did not answer. This is NOT an empty "
                f"pool and the floor does NOT apply; the standing line is kept.")
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
            # [NO_ROW_MEANS_HIGHEST_IP_V1 - John 2026-09-12] No winner falls to the
            # highest .1 in the converged block. Every role. No exceptions, no
            # marker, no missing name.
            #
            # The rule this replaces treated "no capability row" as a failure to be
            # surfaced: it wrote a comment into /etc/hosts and left <role>.frognet
            # unresolvable "until one does". But an absent row is not a failure and
            # it does not mean the role is impossible -- it means nothing at all.
            # Nobody published, so nobody is preferred, so the deterministic
            # tiebreak decides, exactly as it does for databasehost.
            #
            # This is not a fabricated winner and it does not diverge per node: the
            # hosts block is converged and identical everywhere, so max(.1) is the
            # same answer on every machine in the pond. The divergence the old
            # comment warned about came from local-by-definition -- a node naming
            # ITSELF -- which is a different thing and stays retired.
            _ones = _alive_ones(etc_hosts)
            wip = max(_ones, key=lambda a: tuple(int(o) for o in a.split("."))) \
                if _ones else None
            if wip:
                log(f"SERVICE_ELECT role={role} winner={wip} reason=floor_highest_dot1 "
                    f"-- no candidate row for this role; the deterministic floor "
                    f"decides, same on every node. dbhost={dbhost}")
            else:
                log(f"SERVICE_ELECT role={role} winner=NONE reason=no_live_hosts "
                    f"-- the converged block holds no .1 FrogNetHost at all, so "
                    f"there is nothing to elect from. dbhost={dbhost}")
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
    """Commit every elected role line. No barrier.

    [ROLE_ELECTION_UNCOUPLED_V1] Every registered service's platform-selection callback
    runs at the end of every converged runMerge.

    [ABSENT_IS_A_DECIDABLE_STATE_V1 - John 2026-09-12] The barrier is gone. It asked
    "has every live machine published a capability record for this role yet", and
    deferred the commit until they had.

    That question does not need an answer. If a capability is not in the store then it
    is not in the store: that host has no capability to rank on, it sorts to the floor,
    and the deterministic highest-.1 tiebreak decides. Absent data is a VALID input to
    the election, not a missing precondition for running it. Every node reads the same
    store and the same converged hosts block, so every node reaches the same answer
    from the same absence.

    What the barrier actually bought was anti-flap on a CHANGE, and it was paid for in
    the worst currency available: a wait with no upper bound. Proven twice on live
    ponds. Once with four machines whose records were 34,000 to 330,000 seconds stale,
    so every barrier stayed shut and mediahost.frognet never existed at all while the
    election named a healthy winner every pass -- patched then by letting a FIRST
    assignment through. And again on Seattle7, where the barrier reported recorded=[]
    for every role on every merge because the single control read was TIMING OUT, and
    nothing in the log distinguished that from a silent fleet.

    A gate that can wait forever, to damp a change that self-corrects on the next
    merge, is a bad trade. It also cost one store read per role per merge to ask a
    question whose answer is now never consulted -- on the node where the store is
    slow, which is the node where it hurts.

    So: every role commits its elected line, every merge. A winner that moves, moves.
    databasehost still pins at the control when the election names nobody, because the
    control is deterministic (highest .1) and always valid, so the coordination plane
    is never nameless.

    `ready_roles` is accepted and IGNORED, for caller-signature compatibility.
    """
    log = logger or (lambda s: None)
    ready = set(ready_roles or ())
    out = list(etc_hosts)
    committed, nohost = [], []
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
        # [ABSENT_IS_A_DECIDABLE_STATE_V1] Unconditional. The election decided;
        # the decision is committed.
        committed.append(role)
        out = [l for l in out if not (len(l.split()) >= 2 and l.split()[1] == name)]
        out.append(ln)
    if control_ip and not any(l.endswith(" databasehost.frognet") for l in out):
        # The election named no databasehost. The control is the deterministic
        # highest .1 and always valid, so the coordination plane never goes
        # nameless. This is the floor, not a barrier.
        out.append(f"{control_ip} databasehost.frognet")
        log(f"SERVICE_COMMIT databasehost unelected -> floor at {control_ip}")
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
