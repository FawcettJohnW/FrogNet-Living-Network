"""
healthcheck.py - run_tunnel_health_check + _health_probe_tunnel, ported from
sync_interfaces.sh. This is the probe that PRODUCES the DEAD_IFACES set the sim
previously injected by hand.

Per tunnel (from active/*.json: interface, channel_name, remote_subnets[0]):
  peer_ip = <subnet0 base>.2        [HEALTH_ON_DOT2_V1] - the DISCOVERY plane
  install peer_ip/32 dev iface metric PROBE_METRIC; :9009 ping-pong to peer_ip
  pinned to iface; delete the probe; classify:
    RTT_LOOP                          -> dead: loop_9009
    no pong                           -> dead: no_pong
    float rtt                         -> healthy
Dead ifaces are removed from ALL_DEVS (the active-device list discovery walks).

[HEALTH_ON_DOT2_V1] John 2026-09-25: ALL discovery happens on .2. This probe used
to target the peer's PRODUCTION .1: its metric-6 /32 overrode the installed /24 for
every tunnel peer at the start of every merge, so production traffic to each peer
.1 rode a probe route until it was deleted. The .2 alias is bound on every node for
discovery and health and carries no production traffic; the daemon binds 0.0.0.0,
so peer.2:9009 answers on the same tunnel. A peer whose .2 does not pong over its
tunnel is DEAD with the reason named - there is no .1 retry.
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
            return (f"iface={iface} ch={ch} peer_ip={peer_ip} "
                    f"reason=route_install_failed rc={rc}")
        # [PINGPONG_HEALTH_V1] Tunnel carries iff the peer's daemon answers a
        # :9009 ping-pong over THIS iface. SO_BINDTODEVICE-pinned, so the probe
        # rides this tunnel; a path that bends back answers RTT_LOOP (dead, not a
        # usable carrier); no answer = dead; a float rtt = healthy.
        verdict = self.verify.measure_or_loop(peer_ip, iface)
        self.r.rtmut("route", "del", f"{peer_ip}/32", "metric", str(PROBE_METRIC),
                     caller="_health_probe_tunnel")
        if verdict == "LOOP":
            return f"iface={iface} ch={ch} peer_ip={peer_ip} reason=loop_9009"
        if not isinstance(verdict, float):
            # verdict None (no answer) or "REFUSED" (nothing listening): name which.
            return (f"iface={iface} ch={ch} peer_ip={peer_ip} reason=no_pong "
                    f"verdict={verdict}")
        return None

    def run(self, active_states, all_devs):
        """active_states: list of (iface, channel, subnet0). Returns
        (dead_dict iface->reason, filtered_devs)."""
        dead = {}
        for iface, ch, subnet0 in active_states:
            if not (iface and ch and subnet0 and subnet0.endswith(".0/24")):
                continue
            base = subnet0[:-len(".0/24")]
            peer_ip = f"{base}.2"          # [HEALTH_ON_DOT2_V1] never the production .1
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
