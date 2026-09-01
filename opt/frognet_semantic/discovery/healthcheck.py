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
healthcheck.py - run_tunnel_health_check + _health_probe_tunnel, ported from
sync_interfaces.sh. This is the probe that PRODUCES the DEAD_IFACES set the sim
previously injected by hand.

Per tunnel (from active/*.json: interface, channel_name, remote_subnets[0]):
  peer_ip = <subnet0 base>.1
  install peer_ip/32 dev iface metric PROBE_METRIC; echo peer_ip (Host: peer_ip);
  delete the probe; classify:
    code != 200 or empty body            -> dead: echo_failed
    body ok but self_ip(field2) != peer  -> dead: wrong_peer_self_ip=<v|none>
    self_ip == peer_ip                    -> healthy
Dead ifaces are removed from ALL_DEVS (the active-device list discovery walks).

Injected health_echo(peer_ip) -> (code:str, body:str) mirrors the curl to
frognet_echo.php with Host: peer_ip (-w http_code). RealHealthEcho wraps the same
RealEcho transport on the box.
"""
from __future__ import annotations

from .routes import PROBE_METRIC


class HealthCheck:
    def __init__(self, routes, verify, logger=lambda s: None,
                 dev_src=None):
        self.r = routes
        # [PINGPONG_HEALTH_V1] Liveness backend: a :9009 ping-pong probe
        # (RealVerify) with measure_or_loop(peer_ip, iface) -> float|"LOOP"|None.
        # Replaces the HTTP frognet_echo.php gate, which false-negatived healthy
        # tunnels (curl code=000 on a transient proxy/src condition) and never
        # caught a hairpin.
        self.verify = verify
        self.log = logger
        self.dev_src = dict(dev_src or {})

    def _probe(self, iface: str, peer_ip: str, ch: str):
        """Return a dead-reason string, or None if healthy."""
        rc = self.r.rtmut("route", "replace", f"{peer_ip}/32", "dev", iface,
                          "metric", str(PROBE_METRIC), caller="_health_probe_tunnel")
        if rc != 0:
            return f"iface={iface} ch={ch} reason=route_install_failed"
        # [PINGPONG_HEALTH_V1] Tunnel carries iff the peer's daemon answers a
        # :9009 ping-pong over THIS iface. SO_BINDTODEVICE-pinned, so the probe
        # rides this tunnel; a path that bends back answers RTT_LOOP (dead, not a
        # usable carrier); no answer = dead; a float rtt = healthy.
        verdict = self.verify.measure_or_loop(peer_ip, iface)
        self.r.rtmut("route", "del", f"{peer_ip}/32", "metric", str(PROBE_METRIC),
                     caller="_health_probe_tunnel")
        if verdict == "LOOP":
            return f"iface={iface} ch={ch} reason=loop_9009"
        if not isinstance(verdict, float):
            return f"iface={iface} ch={ch} reason=no_pong"
        return None

    def run(self, active_states, all_devs):
        """active_states: list of (iface, channel, subnet0). Returns
        (dead_dict iface->reason, filtered_devs)."""
        dead = {}
        for iface, ch, subnet0 in active_states:
            if not (iface and ch and subnet0 and subnet0.endswith(".0/24")):
                continue
            base = subnet0[:-len(".0/24")]
            peer_ip = f"{base}.1"
            reason = self._probe(iface, peer_ip, ch)
            if reason:
                dead[iface] = reason
                self.log(f"TUNNEL_DEAD {reason}")
            else:
                self.log(f"TUNNEL_HEALTHY iface={iface} ch={ch} peer_ip={peer_ip}")
        filtered = [d for d in all_devs if d not in dead]
        if dead:
            self.log("Active interfaces (after health filter): " + " ".join(filtered))
        return dead, filtered
