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
propagate.py - propagation layer, ported from addHostAndPropogate.bash and
propogateNotificationInternal.

These don't change the durable artifacts (route table / /etc/hosts) but ARE in
the oracle, so their decisions are proven:

addHostAndPropogate (walk's ADD_HOST):
  dedup on FORWARDED key "<host> <ip> <dev>"; if unseen -> addHost (host-file
  accumulation) + PROPAGATE (suppress if discovery_pending else fire) + record.
  On a fresh merge the discovery cache is flushed so discovery_pending is always
  false -> every first-seen key fires.

propogateNotificationInternal (peer fan-out):
  from /etc/hosts take each 10.A.B.1 (excl transit 10.253.253), sort -u, skip
  local addresses, fork a notify to each remaining peer's .2 admin.
"""
from __future__ import annotations


class Propagator:
    """addHostAndPropogate. Wraps the host-file accumulator (HostStore) and adds
    the forward-dedup + PROPAGATE decision, emitting the oracle's lines."""

    def __init__(self, hoststore, logger=lambda s: None, discovery_pending=lambda ip: False):
        self.hosts = hoststore
        self.log = logger
        self.discovery_pending = discovery_pending
        self.forwarded: set[str] = set()

    def add_host_and_propagate(self, host: str, host_path: str, dev: str,
                               authoritative: bool = False) -> None:
        key = f"{host} {host_path} {dev}"
        if key in self.forwarded:
            self.log(f'DEDUP decision=skip reason=already_forwarded key=_="{key}"')
            return
        self.log(f'DEDUP decision=proceed key=_="{key}"')
        # addHost.bash - local host-file accumulation
        self.hosts.add_host(host, host_path, dev, authoritative)
        if self.discovery_pending(host_path):
            self.log(f"PROPAGATE decision=suppress reason=discovery_pending ip={host_path}")
        else:
            self.log(f"PROPAGATE decision=fire reason=not_pending ip={host_path}")
        self.forwarded.add(key)
        self.log(f'FORWARDED_record entry=_="{key}"')
        self.log(f"COMPLETE hostname={host} ip={host_path}")


def propagate_notification(etc_hosts: list[str], local_ips, *, notify=lambda ip: 0,
                           logger=lambda s: None) -> dict:
    """propogateNotificationInternal peer fan-out. Returns dict(launched, skipped).
    notify(peer_ip) -> rc (the curl to peer .2; injected; sim returns 0)."""
    local = set(local_ips)
    # PEER_IPS: 10.A.B.1 from /etc/hosts, exclude transit 10.253.253, sort -u
    peers = set()
    for line in etc_hosts:
        if not line or line.startswith("#"):
            continue
        ip = line.split()[0]
        o = ip.split(".")
        if len(o) == 4 and ip.startswith("10.") and o[3] == "1" and not ip.startswith("10.253.253."):
            peers.add(ip)
    peer_list = sorted(peers)   # sort -u (lexical)
    logger(f"peer_scan admin_targets_found={len(peer_list)}")
    logger("peer_list ips=" + "".join(f"{p}," for p in peer_list))

    launched = skipped = 0
    results = []
    for ip in peer_list:
        if ip in local:
            logger(f"peer_skip ip={ip} reason=local_address")
            skipped += 1
            continue
        logger(f"peer_fork ip={ip}")
        launched += 1
        results.append((ip, notify(ip)))
    logger(f"peer_launched count={launched} skipped={skipped}")
    for ip, rc in results:
        logger(f"peer_result ip={ip} rc={rc}")
    return dict(launched=launched, skipped=skipped, results=results)
