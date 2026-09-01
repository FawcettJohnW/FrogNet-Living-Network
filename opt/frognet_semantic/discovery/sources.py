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
sources.py - the injected edges of discovery. walk() in the bash original calls
out to: frognet_echo.php (echo_probe), frognet_alive (measure_rtt), getHosts.php
(children), the broker handshake_rtts.json (broker_for_peer_ip), the active/*.json
(channel_for_iface), and addHostAndPropogate / discovery_cache (side
effects). Those are the kernel-edge functions the primer says are mocked in the
sim and covered only by the live-hardware tier.

Each is a tiny Protocol with a Fake implementation the simulator scripts from the
oracle. Real implementations (HTTP / subprocess) are stubs here - they mirror the
bash calls and are exercised only on the live box.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# --- Fakes (sim) -----------------------------------------------------------

@dataclass
class FakeEcho:
    """address -> echo CSV ("name,<own .1>,<upstream>,<2nd-upstream>") or None.

    Models frognet_echo.php: whoever OWNS/answers-for the queried address returns
    its self-describing CSV (field 2 = the node's own .1). Unknown address -> None
    (the NOT_FROGNET / on-segment-client-no-echo case)."""
    answers: dict[str, str] = field(default_factory=dict)

    def echo_probe(self, query_ip: str) -> Optional[str]:
        return self.answers.get(query_ip)


@dataclass
class FakeRtt:
    """(dev, pip) -> FIFO of ms values (frognet_alive). Same target probed N
    times yields N distinct rtts (e.g. 10.28.28.2 on eth0: 55,56,89)."""
    table: dict = field(default_factory=dict)   # (dev,pip) -> list[int]

    def measure_rtt(self, dev: str, pip: str) -> str:
        q = self.table.get((dev, pip))
        if not q:
            return ""
        v = q.pop(0)
        return str(v) if v else ""


@dataclass
class FakeGetHosts:
    """host_path (.1) -> list of child IPs that node vouches for (getHosts.php)."""
    children: dict[str, list[str]] = field(default_factory=dict)

    def get_hosts(self, host_path: str, pip: str) -> list:
        # entries may be bare "ip" (legacy oracle setups -> name "") or (ip, name)
        return [c if isinstance(c, tuple) else (c, "")
                for c in self.children.get(host_path, [])]


@dataclass
class FakeBroker:
    """peer .1 -> (channel, subnet, peer_one) from handshake_rtts.json. Only the
    DIRECT-tunnel peers are present; relayed peers return None (no override)."""
    by_one: dict[str, tuple] = field(default_factory=dict)   # ip -> (ch,sub,one)
    iface_channel: dict[str, str] = field(default_factory=dict)  # wgN -> channel
    # [VOUCH_TRANSIT_GATE_V1] relay /24-prefix ("10.160.160") -> reachable /24
    # CIDRs (owned + transit). Empty dict = map absent => transits() inert (True).
    node_transit: dict[str, list] = field(default_factory=dict)

    def broker_for_peer_ip(self, dest1: str) -> Optional[tuple]:
        return self.by_one.get(dest1)

    def channel_for_iface(self, dev: str) -> str:
        return self.iface_channel.get(dev, "")

    def transits(self, relay_one: str, dest24: str) -> bool:
        # No map loaded -> inert (allow all, = pre-gate behaviour).
        if not self.node_transit:
            return True
        prefix = relay_one.rsplit(".", 1)[0]
        if prefix in self.node_transit:
            return dest24 in self.node_transit[prefix]
        # Relay is not a registered transit node (e.g. an unregistered LAN
        # leaf like New-York-2). It cannot legitimately relay a remote /24.
        return False


@dataclass
class FakeVerify:
    """[VERIFY_ROUTE_V1] post-finalize targeted alive (frognet_alive / ping). `dead`
    holds dest .1's whose FINALIZED route does not actually carry (e.g. return-path
    broken even though the pre-promote probe answered)."""
    dead: set = field(default_factory=set)        # dest .1's, devs, or (dest1,dev) pairs that fail verify
                                                   # (timeout-like: no answer - per [NF_CONTRACT_V1] this
                                                   # must NEVER mark not_frognet)
    refused: set = field(default_factory=set)      # [NF_CONTRACT_V1] definitive structural absence
                                                   # (ECONNREFUSED-class): the ONLY thing that marks
    loops: set = field(default_factory=set)        # [LOOP_DETECT_9009_V1] targets (or (target,dev)) whose
                                                   # candidate route hairpins back through us
    # [PROVE_DOT1_V1] Opt-in per-avenue model. When `reach` is provided, the :9009
    # ping-pong verdict depends on the (target, dev, via) actually installed - a .1
    # answers ONLY through the next hop that forwards to it (e.g. Seattle2's .1
    # pongs via 10.250.250.191, NOT via .134). This mirrors FakeReflect: read the
    # live <target>/32 probe route _prove_dot1 just installed and match the avenue.
    # `reach is None` (the default) keeps the legacy dead/refused/loops behaviour so
    # every existing verify-wired oracle is unaffected.
    kernel: object = None
    reach: object = None                            # set[(target, dev, via)] or None

    def _installed_avenue(self, target: str):
        if self.kernel is None:
            return ("", "")
        try:
            shown = self.kernel.route_show(target) or ""
        except Exception:
            shown = ""
        dev = via = ""
        for line in shown.replace(";", "\n").splitlines():
            if not line.startswith(target):
                continue
            parts = line.split()
            if "dev" in parts:
                i = parts.index("dev")
                if i + 1 < len(parts):
                    dev = parts[i + 1]
            if "via" in parts:
                i = parts.index("via")
                if i + 1 < len(parts):
                    via = parts[i + 1]
            break
        return (dev, via)

    def alive(self, dest1: str, dev: str) -> bool:
        # [FROGNET_ALIVE_9009_V1] reachability is per candidate route: the same
        # dest .1 can answer :9009 over one dev (e.g. wg2) and not another (e.g. a
        # LAN vouch that black-holes). Support (dest1,dev) pairs as well as bare
        # dest1/dev for coarse cases.
        return (dest1 not in self.dead and dev not in self.dead
                and (dest1, dev) not in self.dead)

    def measure_or_loop(self, target: str, dev: str):
        # [LOOP_DETECT_9009_V1] mirrors RealVerify: "LOOP" if the candidate route
        # to `target` hairpins back through us (our own daemon answers RTT_LOOP),
        # a float rtt if alive over this route, else None. Stateless - the verdict
        # is re-derived every merge, never remembered.
        if target in self.loops or (target, dev) in self.loops:
            return "LOOP"
        if (target in self.refused or dev in self.refused
                or (target, dev) in self.refused):
            return "REFUSED"        # mirrors RealVerify [NF_CONTRACT_V1]
        if (target in self.dead or dev in self.dead
                or (target, dev) in self.dead):
            return None
        if self.reach is not None:
            # per-avenue: pong ONLY through the next hop that actually forwards.
            rdev, rvia = self._installed_avenue(target)
            return 5.0 if (target, rdev, rvia) in self.reach else None
        return 5.0


@dataclass
class FakeReflect:
    """Models the shipped reflect vhost (proxy FrogNetReflectHandler) as seen in the
    real node logs, so the sim EXERCISES descend._probe_through's reflect path
    instead of falling through to the direct-measure fallback.

    The real reflect verdict depends on the route CURRENTLY INSTALLED to the target:
    _probe_through does probe_install(target.2, dev, via) THEN reflect.probe(o,
    target.2). Reflect answers OK only if that installed avenue actually forwards to
    the target. So this fake reads the kernel's live <target>/32 probe route (the one
    probe_install just wrote) and classifies the exact (dev, via) it finds:

      reach:  set of (target, dev, via) avenues that genuinely reach -> "OK"
      loops:  set of (target, dev, via) (or (target,dev)) that hairpin  -> "LOOP"
      else:                                                             -> None

    `via` is "" for a tunnel scope-link avenue, the neighbour address for a LAN one -
    matching what probe_install recorded. Keyed on the installed route (not the call
    args) so it tests the real install->reflect->delete sequence end to end."""
    kernel: object = None                              # FakeKernel to read live routes
    reach: set = field(default_factory=set)            # (target, dev, via) that reach
    loops: set = field(default_factory=set)            # (target, dev, via) / (target,dev) hairpins

    def _installed_avenue(self, target: str):
        """Read the live <target>/32 probe route from the kernel -> (dev, via)."""
        if self.kernel is None:
            return ("", "")
        try:
            shown = self.kernel.route_show(target) or ""
        except Exception:
            shown = ""
        dev = via = ""
        for line in shown.replace(";", "\n").splitlines():
            if not line.startswith(target):
                continue
            parts = line.split()
            if "dev" in parts:
                i = parts.index("dev")
                if i + 1 < len(parts):
                    dev = parts[i + 1]
            if "via" in parts:
                i = parts.index("via")
                if i + 1 < len(parts):
                    via = parts[i + 1]
            break
        return (dev, via)

    def probe(self, o, target, counter=0):
        dev, via = self._installed_avenue(target)
        if ((target, dev, via) in self.loops or (target, dev) in self.loops
                or target in self.loops):
            return "LOOP"
        if (target, dev, via) in self.reach:
            return "OK"
        return None


@dataclass
class HostStore:
    """Records addHostAndPropogate / discovery_cache side effects so the
    /etc/hosts + databasehost proof can be built later. Order-preserving,
    deduped on (host, host_path) like addHostAndPropogate's DEDUP."""
    added: list[tuple] = field(default_factory=list)      # (host, host_path, dev, authoritative)
    _seen: set = field(default_factory=set)
    cache: list[tuple] = field(default_factory=list)

    def add_host(self, host: str, host_path: str, dev: str,
                 authoritative: bool = False) -> None:
        # [HOSTS_AUTHORITATIVE_NAME_V1] authoritative=True means `host` is the
        # name the node returned for ITSELF via echo (or the broker override) -
        # the source of truth. authoritative=False is a relayed vouch name (a
        # neighbor's getHosts entry), which may be the generic "FrogNetHost".
        # Reconciliation (orchestrate.merge) lets an authoritative name win per
        # IP; the vouch name is a fallback only when no echo named the peer.
        key = (host, host_path, dev)
        if key in self._seen:
            return
        self._seen.add(key)
        self.added.append((host, host_path, dev, authoritative))

    def cache_success(self, ip: str, dev: str, host: str, host_path: str, rtt: str) -> None:
        self.cache.append((ip, dev, host, host_path, rtt))
