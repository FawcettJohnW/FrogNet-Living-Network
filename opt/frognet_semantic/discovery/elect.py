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
"""[ELECT_IN_PROCESS_V1] the merge-end service election, callable without runMerge.

In region mode FD&F owns membership, so the one input the election needs -- who is alive, with which .1 -- arrives
at the daemon as an event. A databasehost going dead must not wait for a 30-60 s merge to be noticed: the daemon
calls elect_now() on the transition and the app sees `databasehost` change in view.json within a second.

This is the same code path the merge runs (discovery.live, SERVICE_HOSTS_ELECTION_V1), with the same functions:
build_etc_hosts -> control host (highest .1) -> select_database_host floor -> set_control_host -> service_host_lines
(every role's pure pick over the capability tuples at the control host) -> commit_service_lines -> /etc/hosts and
the dnsmasq forwarders written only if changed -> dnsmasq restarted only on content change. Every node reaches the
same answer from the same hosts block and the same store.
"""
import re
import os
import subprocess


def _my_dot1_and_domain():
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True)
    except Exception:
        out = ""
    dot1, tens = [], []
    for ln in out.splitlines():
        p = ln.split()
        if "inet" in p:
            a = p[p.index("inet") + 1].split("/")[0]
            o = a.split(".")
            if a.startswith("10.") and o[1] not in ("253", "254"):
                tens.append(a)
                if o[3] == "1":
                    dot1.append(a)
    h = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    domain = h.split(".", 1)[1] if "." in h else h
    return (dot1[0] if dot1 else ""), domain, tens


def given_names(participants, alive_only=False):
    """[ELECT_NODES_ONLY_V1] {ip: name} of the region's leaves and reaching participants -- the members with a GIVEN
    /32 (facts.addr). Shared by the daemon's election and runMerge ([LEAVES_NAMED_BY_EVERY_WRITER_V1]): both write
    /etc/hosts, so both must name them, or the last writer drops them (found 2026-10-11: runMerge on Seattle5 took the
    phone and the PC out of /etc/hosts until the service restarted)."""
    given = {}
    for p in participants or []:
        f = p.get("facts") or {}
        if alive_only and not p.get("alive"):
            continue
        if f.get("role") in ("leaf", "participant") and f.get("addr"):
            nm = re.sub(r"[^A-Za-z0-9._-]", "-", str(p.get("name") or f.get("name") or ""))
            if nm:
                given[str(f["addr"]).split("/")[0]] = nm
    return given


def leaf_lines(etc_hosts, participants):
    """etc_hosts with one plain "<ip> <name>" line for each ALIVE leaf/reacher not already in it -- never a FrogNetHost
    line, never a .frognet role (a leaf is named, never chosen)."""
    have = {ln.split()[0] for ln in etc_hosts if ln.split()}
    return list(etc_hosts) + ["%s %s" % (ip, nm) for ip, nm in sorted(given_names(participants, alive_only=True).items()) if ip not in have]


def elect_now(region_hosts_path="/var/lib/frognet/region_hosts", logger=print, alive_hosts=None, participants=None,
              self_ip=None, hosts_path=None, apply=True) -> dict:
    """Returns {"databasehost": ip, "hosts_changed": bool, "etc_hosts": [...]}.
    alive_hosts: [(ip, name)] of the members alive RIGHT NOW (the daemon's plan, no hold-down). When given, the
    election's pool is exactly that; region_hosts (which keeps a vanished member for the hold-down) is not read.
    [ELECTION_SEES_NO_HOLD_DOWN_V1] a dead databasehost must not be the control host of its own replacement.
    participants: the region's members (name, alive, facts) -> [ELECTION_FROM_FACTS_V1] the candidates are their
    facts.capability; no store is read. self_ip/hosts_path/apply=False: the simulator electing as another node."""
    from discovery.hosts import (addhost_lines, local_lines, build_etc_hosts, control_host_ip, CONTROL_NAME,
                                 select_database_host, set_control_host)
    from discovery.live import _write_if_changed, _write_forwarders, _apply_dnsmasq
    from core.frognet_service_hosts import service_host_lines, commit_service_lines
    from core.role_registry import ROLE_HANDLERS
    from core import region_capability as _RC
    _self_ip, domain, _local_ips = _my_dot1_and_domain()
    if self_ip is None:
        self_ip = _self_ip
    n_cap = _RC.set_snapshot(participants)
    if participants is not None:
        logger("ELECT: candidates from the region's facts: %d member(s) with a capability" % n_cap)
    hosts_path = hosts_path or "/etc/hosts"
    lines = local_lines(self_ip, domain) if self_ip else []
    if alive_hosts is not None:
        rows = [(ip, nm) for ip, nm in alive_hosts]
    else:
        rows = []
        try:
            for ln in open(region_hosts_path):
                f = ln.split()
                if len(f) >= 2:
                    rows.append((f[0], f[1]))
        except OSError:
            pass
    # [ELECT_NODES_ONLY_V1] the pool is the region's NODES: a leaf's /32 (a phone) and a reaching participant's /32 are
    # given addresses, and the first one in every pond is 10.252.0.1 -- it ends in .1 and passed the node test below.
    # Found 2026-10-11 (netns region-loop and reach logs): the phone at 10.252.0.1 was elected the CONTROL host, and was
    # a databasehost candidate. Excluded by role, from the region's own facts.
    # They are still NAMED (the leaf row's promise, LEAF_ROW_FROM_ADDR_V1): one plain "<ip> <name>" line each, added
    # after control and the services are chosen -- never a FrogNetHost line, never with a FrogNetAdmin .2 (which is
    # another leaf's address).
    given = given_names(participants)
    named_given = sorted((ip, given[ip]) for ip, _nm in rows if ip in given and given[ip])
    if given:
        rows = [(ip, nm) for ip, nm in rows if ip not in given]
    for ip, nm in rows:
        if ip.endswith(".1") and ip != self_ip:
            nm = nm.split(".", 1)[1] if nm.startswith("FrogNetHost.") else nm
            lines += addhost_lines(nm, ip)
    logger("ELECT: pool=%s (alive now%s)" % (sorted(ip for ip, _ in rows), "" if alive_hosts is not None else ", from region_hosts"))
    etc = build_etc_hosts(lines, self_ip=self_ip)
    ctl = control_host_ip(etc) or CONTROL_NAME
    etc = select_database_host(etc, dbhost=ctl, logger=logger)
    etc = set_control_host(etc)
    role_lines = service_host_lines(dbhost=ctl, logger=logger, etc_hosts=etc, self_ip=self_ip)
    etc = commit_service_lines(etc, role_lines, set(ROLE_HANDLERS), ctl, logger=logger)
    etc = etc + ["%s %s" % (ip, nm) for ip, nm in named_given]     # [ELECT_NODES_ONLY_V1] leaves and reachers: named, never chosen
    changed = _write_if_changed(hosts_path, "\n".join(etc) + "\n", logger)
    if apply:
        try:
            _write_forwarders(etc, logger)
        except Exception as e:
            logger("ELECT: forwarders not written: %s" % e)
        try:
            _apply_dnsmasq(logger)
        except Exception as e:
            logger("ELECT: dnsmasq not applied: %s" % e)
    who = {}
    for ln in etc:
        f = ln.split()
        for t in f[1:]:
            if t.endswith(".frognet"):
                who[t[:-len(".frognet")]] = f[0]
    logger("ELECT: control=%s databasehost=%s hosts_changed=%s" % (ctl, who.get("databasehost"), changed))
    return {"databasehost": who.get("databasehost", ""), "hosts_changed": changed, "etc_hosts": etc}
