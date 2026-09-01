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
orchestrate.py - the mergeHostsAndResolv stage chain over the ported discovery.

Ordering preserved from mergeHostsAndResolv.bash:
  clear_working_files -> local_identity -> reconcile_tunnels (bringup register)
  -> sync_interfaces (descend_downstream/upstream + promote) -> stage_hosts
  -> selectNewDatabaseHost -> (fixDefaultRoute / manageResolv / commit_only /
  propogate / emit_routes_snapshot - wired later).

This produces the two provable artifacts: the final route table (kernel.table())
and /etc/hosts. fixDefaultRoute/manageResolv/commit/propagate are side-channels
that don't change either artifact in the oracle run; ported next.
"""
from __future__ import annotations

import os

from .hosts import (addhost_lines, local_lines, build_etc_hosts,
                    select_database_host, set_control_host, derive_domain,
                    reconcile_added, control_host_ip, CONTROL_NAME)


def merge(disc, kernel, seeds, upstream_seed, *, local, bringup_peers,
          prior_names=None, logger=lambda s: None):
    """Run the merge. Returns dict(final_table, etc_hosts, frognet_hosts).

    local        = (eth0_ip, domain)
    bringup_peers= list of (host, host_path) registered by daemon bringup
                   (BRINGUP_HOSTS_REGISTER -> addHost), BEFORE discovery.
    prior_names  = {host_path(.1): domain} parsed from the existing /etc/hosts,
                   for HOSTS_NAME_CHANGE_RUNAGAIN_V1. None/empty skips the check.
    """
    eth0_ip, domain = local

    # clear_working_files + local_identity (mergeHostsAndResolv 75-105)
    frognet_hosts: list[str] = []
    frognet_hosts += local_lines(eth0_ip, domain)

    # reconcile_tunnels: daemon registers direct-channel peers into hosts
    # (BRINGUP_HOSTS_REGISTER). These addHost calls happen before sync_interfaces.
    for host, host_path in bringup_peers:
        frognet_hosts += addhost_lines(host, host_path)

    # sync_interfaces: discovery (foreground) - populates routes + discovered hosts
    # bash runs the transient .2/32 metric-5 sweep at ENTRY and EXIT. The entry
    # sweep clears stale admin aliases from a prior topology so this run's probes
    # are authoritative (a stale lower-metric alias would otherwise hijack the
    # probe target and force a false FAIL_ECHO).
    disc.r.sweep_probe_routes()
    disc.descend_downstream(**seeds)
    disc.descend_upstream(seeds["active_devs"], upstream_seed)
    disc.promote()
    disc.r.sweep_probe_routes()

    # discovery's addHost calls (walk -> addHostAndPropogate). Per-IP name
    # reconciliation + deprecated-name detection in reconcile_added.
    _peer_lines, host_name_changed = reconcile_added(
        disc.hosts.added, prior_names=prior_names, logger=logger)
    frognet_hosts += _peer_lines

    # [HOSTS_NEED_ROUTE_V1] A vouched/propagated/bringup-registered name means
    # some node KNOWS the subnet - it is NOT proof THIS node can reach it. Gate
    # the host table on an actual route: keep a peer line only if its /24 is an
    # installed winner in the kernel table (or our own connected subnet). Without
    # this, /etc/hosts accumulates mesh-wide names a node has no route to - e.g.
    # New-York-1/BABox/BAMacBook/New-York-2 on Seattle3, which routes only
    # 120/130/160/250; or a bringup direct-channel peer whose tunnel is dead and
    # whose /24 was reaped (BABox over a dead wg0). Mirrors the route-side alive
    # gate one layer up: no route, not in the table. Self/own subnet, localhost,
    # and the databasehost line are always kept (added separately below).
    routable = {tok[0].split("/")[0].rsplit(".", 1)[0]
                for ln in kernel.table()
                for tok in [ln.split()]
                if tok and tok[0].endswith("/24")}
    self_pref = eth0_ip.rsplit(".", 1)[0] if eth0_ip else ""
    kept, dropped = [], 0
    for ln in frognet_hosts:
        f = ln.split()
        ip = f[0] if f else ""
        pref = ip.rsplit(".", 1)[0] if ip.count(".") == 3 else ""
        if pref and pref != self_pref and pref not in routable:
            dropped += 1
            continue
        kept.append(ln)
    if dropped:
        logger(f"HOSTS_GATE dropped={dropped} reason=no_route_to_subnet "
               f"kept={len(kept)}")
    frognet_hosts = kept

    # [TUNNEL_PEER_IS_KNOWN_AT_WALK_TIME_V1] Which machine is at the end of
    # which tunnel, written down here because here is where it is known.
    #
    # The walk records (host, host_path, dev, authoritative) per peer: the
    # machine's own name, its .1, the interface it answered on, and whether it
    # named ITSELF via echo. That is the join manageResolv needs to put each
    # tunnel peer's primary in resolv.conf, and it is thrown away one line
    # later when only the host lines are kept.
    #
    # Reconstructing it afterwards from the kernel table takes three guesses
    # and gets two of them wrong -- AllowedIPs is 10.0.0.0/8 on every tunnel
    # and carries no pond, and an interface's several /24 routes are mostly
    # TRANSIT (ponds reachable THROUGH it) rather than the pond AT THE END of
    # it. Both were measured on live nodes, 2026-08-12. The walk has no such
    # ambiguity: the peer answered on that dev and said its own name.
    #
    # Only authoritative entries, and only over a wg device. A vouched name is
    # a neighbour's opinion, and a peer reached over eth0 is not at the end of
    # a tunnel.
    tunnel_peers = []
    _seen_dev: set = set()
    for _entry in getattr(disc.hosts, "added", []):
        try:
            _host, _host_path, _dev, _auth = _entry[0], _entry[1], _entry[2], _entry[3]
        except (IndexError, TypeError):
            continue
        if not _auth or not _dev or not str(_dev).startswith("wg"):
            continue
        if _dev in _seen_dev:
            continue
        _seen_dev.add(_dev)
        tunnel_peers.append(f"{_dev}\t{_host_path}\t{_host}")
    try:
        _tp = "/etc/sentinels/tunnel_peers.tsv"
        os.makedirs(os.path.dirname(_tp), exist_ok=True)
        with open(_tp + ".tmp", "w") as _f:
            _f.write("# dev<TAB>primary_ip<TAB>name -- written by the merge\n")
            for _ln in tunnel_peers:
                _f.write(_ln + "\n")
        os.replace(_tp + ".tmp", _tp)
        logger(f"TUNNEL_PEERS n={len(tunnel_peers)}")
    except OSError as _e:
        logger(f"TUNNEL_PEERS write failed: {_e!r}")

    # [HOSTS_DEDUP_V1] With the merge as the single authoritative writer, also
    # collapse identical lines: a bringup peer (broker handshake) and the same
    # peer discovered in the walk both produce the same host line. Order-
    # preserving so the assembled block stays stable across passes.
    seen_ln: set = set()
    deduped = []
    for ln in frognet_hosts:
        if ln not in seen_ln:
            seen_ln.add(ln)
            deduped.append(ln)
    frognet_hosts = deduped

    # stage_hosts (147-201) + selectNewDatabaseHost (256-258)
    etc_hosts = build_etc_hosts(frognet_hosts, self_ip=eth0_ip)
    # [DBHOST_FLOOR_CONTROL_IP_V1] Read candidate capability + select from the
    # deterministic control host (highest .1) computed from THIS merge's block, not
    # via the databasehost_control.frognet NAME - set_control_host runs below, so the
    # name would resolve through the prior merge's /etc/hosts and lag. This is the
    # same fresh-control-IP the merge-end SERVICE election uses; without it the floor
    # and the service election can read different control hosts and databasehost splits.
    _ctl = control_host_ip(etc_hosts) or CONTROL_NAME
    etc_hosts = select_database_host(etc_hosts, dbhost=_ctl, logger=logger)
    # [DBHOST_CONTROL_V1] write the deterministic control line (highest .1). The SD:
    # coordination plane resolves databasehost_control.frognet; the merge-end election
    # reads candidates FROM it and repoints databasehost.frognet to the elected data
    # host. On cold start (no capability) select_database_host's floor already made
    # databasehost.frognet == highest .1 == control, so the data host defaults to control.
    etc_hosts = set_control_host(etc_hosts)
    # databasehost capability election now happens uniformly in the generic
    # merge-end service-host loop (live.py SERVICE_HOSTS_ELECTION_V1); this line is
    # just the highest-IP FLOOR that guarantees a databasehost line always exists.

    # sync_required is set when /etc/hosts or dnsmasq forwarders changed
    # (mergeHostsAndResolv stage_hosts/stage_dnsmasq). On a fresh merge the
    # hosts file goes from empty -> populated, so it always changes here.
    sync_required = bool(etc_hosts)

    return {
        "final_table": kernel.table(),
        "etc_hosts": etc_hosts,
        "frognet_hosts": frognet_hosts,
        "sync_required": sync_required,
        "host_name_changed": host_name_changed,
    }
