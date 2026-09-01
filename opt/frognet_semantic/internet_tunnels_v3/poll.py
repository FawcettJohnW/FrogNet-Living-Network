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
poll.py - tunnel-daemon event loop + post-discovery reconcile.

Two entry points:

  1. poll_once() / run_poll_loop()
     Passive broker-list watcher.  When the broker's channel set changes,
     fork runMerge to re-run discovery.  Unchanged from prior revisions.

  2. reconcile_tunnels() -> _reconcile_tunnels_inner()
     Invoked by runMerge at the end of its pass (after sync_interfaces
     has done its observations + commits).  Now MUCH smaller:

       Phase 0: internet check.
       Phase 1: orphan-iface teardowns (kernel wg interfaces whose pubkey
                isn't in the broker's list are stale - kill them).
       Phase 2: local-state-only teardowns (channels we had up that the
                broker has since removed).
       Phase 3: bring up broker channels we don't have yet.  Parallel.
                Each bring-up runs its own .2 aliveness probe; if it
                fails, the tunnel is NOT saved and the channel gets no
                observation emitted.
       Phase 4: emit a tunnel Observation per live broker-channel subnet
                into /etc/sentinels/discovery_observations.tsv.  The
                probe technique matches sync_interfaces: /32 to .2 on
                the channel's wgN at metric 1, curl echo through the
                full stack, measure RTT, delete /32.
       Phase 5: write /var/lib/frognet-tunnel/channel_map.json so the
                committer can translate channel names to iface names
                when it decides to tear one down.
       Phase 6: call the committer (python -m frognet_route commit-final).
                The committer installs winners, removes losers, tears
                down any tunnel channel that won zero subnets.
       Phase 7: reconcile Python-side state against the kernel - any
                channel whose iface disappeared (because the committer
                brought it down) loses its state file and _active_tunnels
                entry.

All /24 route installation and removal is done by the committer.  This
module brings up and verifies tunnels; that's it.

What was removed from the pre-refactor version of this file:
  _has_non_kernel_route, _replace_route, _install_tunnel_routes,
  _remove_tunnel_routes, _channel_is_lan_reachable,
  _find_lan_route_for_subnet, _measure_rtt_through_dev,
  _tear_down_lan_route, _list_all_routes_for_subnet,
  _remove_specific_route, _candidate_lan_paths_for_subnet,
  and the measurement/winner-picking/install bulk of
  _reconcile_tunnels_inner.  Those responsibilities belong to
  frognet_route.planner + frognet_route.committer now.
"""

import json
import os
import subprocess
import ssl
import threading
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Set, Tuple

from . import config
from .wg import (find_next_wg_iface, teardown_wg_iface,
                 wg_handshake_age, frognet_echo)


# ---------------------------------------------------------------------------
# frognet0 - virtual interface for chorus IPs (10.254.x.x)
# ---------------------------------------------------------------------------

FROGNET_IFACE = "frognet0"
OBSERVATIONS_PATH = "/etc/sentinels/discovery_observations.tsv"
CHANNEL_MAP_PATH = "/var/lib/frognet-tunnel/channel_map.json"
# [BROKER_TEARDOWN_V1] List of channel names the broker currently lists
# for this node.  Written by _write_committer_sidecars().  Used by the
# committer to drive teardown as active-minus-broker instead of the
# legacy observation-winners rule.
BROKER_CHANNELS_PATH = "/var/lib/frognet-tunnel/broker_channels.json"
# [TRACEROUTE_V1] Per-observation traceroute hop counts written by
# sync_interfaces.sh at OBSERVED ingest.  Schema per line:
#   dest_subnet\tprobe_ip\tdev\tadditional_253_hops
# Passed to the committer so a 0-additional-253 observation wins over
# any indirect path regardless of RTT.  Not written by poll.py itself.
TRACEROUTES_PATH = "/etc/sentinels/discovery_traceroutes.tsv"
# [BROKER_STATE_PERSIST_V1] Persisted snapshot of the latest broker
# fetch, written by bringup phase, read by commit-only phase.  Required
# because bring-up-only and commit-only run as SEPARATE Python
# processes spawned by mergeHostsAndResolv; they share no in-memory
# state.  Without this file, the commit-only process has no broker
# context - config.last_broker_names/channels are unset - and
# _write_committer_sidecars writes empty sidecars, which the committer
# interprets as "broker has no channels" -> tear down everything.
LAST_BROKER_STATE_PATH = "/var/lib/frognet-tunnel/last_broker_state.json"
FROGNET_ROUTE_CMD = ["/usr/bin/python3", "-m", "frognet_route"]


def _bringup_install_routes(channel_name: str, iface: str,
                            remote_subnets: List[str]) -> None:
    """[BRINGUP_INSTALL_ROUTES_V3] Install per-channel /24 routes in the
    same shape the planner/committer would for the eventual winner.

    Two shapes, matching planner._winning_via for tunnel observations:

      peer.1 INSIDE dest (common case, peer's own served /24):
        dest dev wgN metric 22 [src <local_gw>]
        - no via, no onlink. allowed_ips covers dest; wg encapsulates
        without any L2 resolution attempt for peer.1.

      peer.1 OUTSIDE dest (relay tunnel to a downstream /24):
        dest via <peer.1> dev wgN metric 22 onlink [src <local_gw>]
        - peer.1 is the peer's OWN served .1, derived from the channel
        name suffix. NOT the per-subnet .1, which was the prior bug:
        for a relay /24 like 10.130.130.0/24 served via a peer at
        10.133.144.1, the old code installed via=10.130.130.1 - a
        route the kernel accepts but no packet ever traverses, because
        peer.1 (10.133.144.1) is the actual forwarder.

    Skips the host's own served /24 if a peer's broker config lists it
    as remote - broker bug we saw on 2026-05-15 (Seattle-1I claiming
    10.250.250.0/24 as a remote_subnet). Skipping early avoids the
    BRINGUP_ROUTE_FAILED noise and is more honest than letting the
    kernel reject.

    Skips destinations that already have a route - preserves a LAN
    winner installed by an earlier merge cycle.

    Architectural exception to the "committer owns all routes" rule.
    Without a /24 in the kernel table at this point, the local proxy's
    `ip route get peer.2` falls back to the wlan0 default and the
    worker gate rejects the wave-2 probe with "10.253/16 is
    transit-only". No probe -> no observation -> committer tears the
    channel down with won_no_subnet -> route never gets installed.
    Chicken-and-egg.
    """
    from frognet_route.iproute import RealIPRoute as IPRoute
    from frognet_route.planner import InstallRoute
    from .names import parse_channel_name

    info = _active_tunnels.get(channel_name)
    if info is None:
        return
    ipr = IPRoute()
    existing = {r.dest for r in ipr.list_routes()}
    own_subnet = getattr(config, "LOCAL_SUBNET", "") or ""

    # [TUNNEL_SRC_HINT_REMOVED_20260518] No src on tunnel routes.  The
    # old design pinned src=LOCAL_GW (eth0's IP), which stamped an eth0
    # address onto packets leaving wgN.  Kernel accepted (IP is locally
    # assigned) but the far peer dropped post-decrypt because the eth0
    # IP wasn't in AllowedIPs for our peer entry.  Without src, the
    # kernel sources from wgN's transit /30, which the peer's AllowedIPs
    # for our /30 already covers.

    # Peer's own served .1, derived from channel-name suffix. For
    # "Seattle-1I-10.133.144" peer_prefix is "10.133.144" and peer_one
    # is "10.133.144.1". Used as the via for relay routes (dest /24
    # whose .1 isn't this peer).
    _peer_name, peer_prefix = parse_channel_name(channel_name)
    peer_one = f"{peer_prefix}.1" if peer_prefix else ""

    installed: List[str] = []
    for sn in remote_subnets:
        if own_subnet and sn == own_subnet:
            config.log.info(
                "BRINGUP_ROUTE_SKIP %s: dest is own served /24 "
                "(broker advertised it as peer's remote_subnet - bug)",
                sn)
            continue
        if sn in existing:
            config.log.info(
                "BRINGUP_ROUTE_SKIP %s: route already exists, "
                "leaving alone", sn)
            continue

        # [BRINGUP_OWN_SUBNET_ONLY_V1] Bringup installs ONLY the
        # channel's own /24 (peer.1 sits inside dest).  Routes for
        # subnets the peer claims to relay are NOT installed at
        # bringup - they are unverified hints from broker
        # remote_subnets, and a peer can advertise a relay it can't
        # actually forward (stale transit_subnets registration, or
        # the relay node's upstream went away).  Installing such
        # routes here strands traffic on a dev whose peer drops the
        # packet with No route to host.
        #
        # Instead, sync_interfaces wave-1 seeds remote_subnets into
        # the probe queue and exercises them via tmp /32 routes.
        # Successful echo -> observation -> planner winner ->
        # committer install.  Failed echo ->
        # [PROBE_FAILURE_REMOVAL_V1] failure record -> planner
        # removal of any stale kernel route for the dest.  The
        # channel's own /24 is the bootstrap exception: it must be
        # in the kernel for tunnel_health_check to reach peer.1 and
        # for wave-1 probes through this iface to even start.
        if not (peer_prefix and _peer_one_inside_dest(peer_prefix, sn)):
            config.log.info(
                "BRINGUP_ROUTE_SKIP %s: relayed subnet "
                "(deferred to post-probe commit)", sn)
            continue

        # [TUNNEL_DEV_ROUTE_V1] Always `dest dev wgN`, no via, no
        # onlink.  Broker sets AllowedIPs=10.0.0.0/8 per channel, so
        # wg accepts any 10.x destination on the iface; the peer's
        # kernel routes the inner packet onward.  Same shape for
        # peer's own /24 and for relayed /24s.  See planner
        # _winning_via for the matching change on the committer side.
        via = ""
        onlink = False
        shape = "direct"

        ok = ipr.install(InstallRoute(
            dest=sn, dev=iface, via=via,
            onlink=onlink, kind="tunnel-bringup",
            rtt_ms=int(info.get("handshake_wall_ms", 0)),
            src="",
        ))
        if ok:
            installed.append(sn)
            config.log.info(
                "BRINGUP_ROUTE_INSTALL %s dev %s shape=%s",
                sn, iface, shape)

            # [ADMIN_ALIAS_ROUTE_V1] Install the .2/32 alias paired with
            # this /24, with identical (dev, via, onlink).  Bringup
            # installs the /24 before sync_interfaces.sh runs, so this
            # is the first authoritative writer of the alias on a fresh
            # boot.  Same coupling as the committer (see
            # planner.ADMIN_ALIAS_METRIC).
            from frognet_route.planner import admin_alias_for_dest
            alias = admin_alias_for_dest(sn)
            if alias is not None:
                ok_alias = ipr.install_admin_alias(
                    InstallRoute(
                        dest=sn, dev=iface, via=via,
                        onlink=onlink, kind="tunnel-bringup",
                        rtt_ms=0, src="",
                    ),
                    alias,
                )
                if ok_alias:
                    config.log.info(
                        "BRINGUP_ADMIN_ALIAS_INSTALL %s via %s dev %s "
                        "onlink=%s (paired with %s)",
                        alias, via, iface,
                        "yes" if onlink else "no", sn,
                    )
                else:
                    config.log.warning(
                        "BRINGUP_ADMIN_ALIAS_INSTALL_FAILED %s via %s "
                        "dev %s - /24 installed but alias did not",
                        alias, via, iface,
                    )
        else:
            config.log.warning(
                "BRINGUP_ROUTE_FAILED %s via=%s dev %s shape=%s",
                sn, via or "<none>", iface, shape)
    info["installed_subnets"] = installed


def _peer_one_inside_dest(peer_prefix: str, dest_cidr: str) -> bool:
    """True if <peer_prefix>.1 sits inside dest_cidr (a /24 like
    10.x.y.0/24). Mirrors planner._dest_contains_host_path for the
    /24 case bringup actually encounters."""
    try:
        net, prefix_s = dest_cidr.split("/")
        if int(prefix_s) != 24:
            return False
        dest_octets = net.rsplit(".", 1)[0]
        return dest_octets == peer_prefix
    except (ValueError, IndexError):
        return False


def _onlink_required(dev: str, via: str) -> bool:
    """[SMART_ONLINK_V1] Decide whether `dest via <via> dev <dev>`
    needs the `onlink` keyword.

    Onlink is needed only when no existing route on <dev> covers
    <via>.  When the channel's own /24 is already up on a wg
    interface (e.g. 10.160.160.0/24 dev wg2 scope link) and we're
    installing a transitive /24 with via=10.160.160.1 dev wg2, the
    kernel resolves the via through the existing /24 via recursive
    lookup - onlink is unnecessary and actively harmful for
    downstream destinations (it tells the kernel to skip the
    recursive lookup, breaking forwarding for any address that
    needs the relay node's own routing decisions).

    Returns True only when no existing non-via route on <dev>
    covers <via>.  Failing safely closed: if `ip route show dev
    <dev>` errors, return True so the install still succeeds via
    onlink - same as the previous unconditional behavior."""
    if not via:
        return False
    try:
        ip_int = _ip_to_int(via)
    except ValueError:
        return True
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "route", "show", "dev", dev],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (subprocess.SubprocessError, OSError):
        return True
    if out.returncode != 0:
        return True
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or " via " in line:
            continue
        dest = line.split(None, 1)[0]
        if "/" in dest:
            net_s, plen_s = dest.split("/", 1)
            try:
                plen = int(plen_s)
            except ValueError:
                continue
        else:
            net_s = dest
            plen = 32
        try:
            net_int = _ip_to_int(net_s)
        except ValueError:
            continue
        mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF if plen else 0
        if (ip_int & mask) == (net_int & mask):
            return False  # covered, no onlink needed
    return True


def _ip_to_int(ip: str) -> int:
    parts = ip.split(".")
    if len(parts) != 4:
        raise ValueError(f"bad ip: {ip!r}")
    nums = [int(p) for p in parts]
    if any(n < 0 or n > 255 for n in nums):
        raise ValueError(f"bad ip: {ip!r}")
    return (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]


def _ensure_frognet0():
    r = subprocess.run(["ip", "link", "show", FROGNET_IFACE],
                       capture_output=True, check=False)
    if r.returncode != 0:
        subprocess.run(["ip", "link", "add", FROGNET_IFACE, "type", "dummy"],
                       check=False)
        subprocess.run(["ip", "link", "set", FROGNET_IFACE, "up"], check=False)
        config.log.info("Created %s virtual interface", FROGNET_IFACE)


def _live_chorus_ips() -> Set[str]:
    r = subprocess.run(["ip", "-4", "addr", "show", FROGNET_IFACE],
                       capture_output=True, text=True, check=False)
    ips = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("inet 10.254."):
            ips.add(line.split()[1].split("/")[0])
    return ips


def _add_chorus_ip(chorus_ip: str, subnet: str):
    prefix_len = subnet.split("/")[1]
    subprocess.run(["ip", "addr", "add", f"{chorus_ip}/{prefix_len}",
                    "dev", FROGNET_IFACE], capture_output=True, check=False)
    config.log.info("Added chorus IP %s/%s to %s",
                    chorus_ip, prefix_len, FROGNET_IFACE)


def _remove_chorus_ip(chorus_ip: str):
    subprocess.run(["ip", "addr", "del", f"{chorus_ip}/24", "dev", FROGNET_IFACE],
                   capture_output=True, check=False)
    config.log.info("Removed chorus IP %s from %s", chorus_ip, FROGNET_IFACE)


def _sync_chorus_ips(chorus_assignments: List[dict]):
    broker_ips: Dict[str, str] = {
        a["chorus_ip"]: a["subnet"] for a in chorus_assignments
    }
    live_ips = _live_chorus_ips()
    for ip in live_ips - set(broker_ips.keys()):
        _remove_chorus_ip(ip)
    for ip, subnet in broker_ips.items():
        if ip not in live_ips:
            _add_chorus_ip(ip, subnet)


# ---------------------------------------------------------------------------
# Subnet -> gateway helper (unchanged)
# ---------------------------------------------------------------------------

def _subnet_gateway(subnet: str) -> str:
    """'10.102.60.0/24' -> '10.102.60.1'.  Uses rsplit to stay safe on
    subnets containing '0' in higher octets."""
    net = subnet.split("/")[0]
    prefix = net.rsplit(".", 1)[0]
    return prefix + ".1"


def _probe_ip_for_subnet(subnet: str) -> str:
    """'10.102.60.0/24' -> '10.102.60.2'.  Probe alias, not the gateway."""
    if "/" not in subnet:
        return ""
    parts = subnet.split("/", 1)[0].split(".")
    if len(parts) != 4:
        return ""
    return f"{parts[0]}.{parts[1]}.{parts[2]}.2"


# ---------------------------------------------------------------------------
# HTTP client (unchanged)
# ---------------------------------------------------------------------------

_ssl_ctx = None
_opener = None


def _get_opener():
    global _ssl_ctx, _opener
    if _opener is None:
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        handler = urllib.request.HTTPSHandler(context=_ssl_ctx)
        _opener = urllib.request.build_opener(handler)
    return _opener


def broker_get(path: str, params: dict = None) -> dict:
    url = f"{config.BROKER_URL}{path}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}"
                      for k, v in params.items())
        url += ("&" if "?" in url else "?") + qs
    req = urllib.request.Request(url, headers={"Connection": "keep-alive"})
    with _get_opener().open(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def register_with_broker() -> bool:
    """[GUID_IDENTITY_V1] Self-register this node with the broker.

    The tunnel daemon historically never registered - registration was a one-
    shot install-time action, so a node the broker had retired/forgotten could
    only re-fetch /my-channels, get 404, tear down, and stop. This gives the
    running daemon the ability to re-assert its identity: it POSTs its install-
    time GUID (the sole identity key) so the broker REVIVES its row instead of
    leaving it dead.

    Returns True on a successful register, False otherwise. Never raises -
    callers fall through to their existing teardown path on False. Requires
    NODE_GUID and POND; without either there is no identity to register, so it
    logs and returns False rather than sending a guess."""
    if not config.NODE_GUID:
        config.log.error("REGISTER: no identity at /etc/fnid - run "
                         "`frognet-node-guid.sh --ensure` and restart "
                         "frognet-tunnel-daemon-v3; cannot self-register.")
        return False
    if not config.POND:
        config.log.error("REGISTER: no POND (NETWORK_NAME) on disk - cannot "
                         "self-register.")
        return False
    body = {
        "pond":      config.POND,
        "pubkey":    config.PUBKEY,
        "subnet":    config.LOCAL_SUBNET,
        "node_name": config.NODE_NAME,
        "guid":      config.NODE_GUID,
    }
    try:
        resp = broker_post("/api/v4/register", body)
        config.log.info("REGISTER: ok node=%s guid=%s pond=%s -> node_id=%s",
                        config.NODE_NAME, config.NODE_GUID, config.POND,
                        resp.get("node_id"))
        return True
    except Exception as e:
        config.log.error("REGISTER: failed node=%s guid=%s pond=%s: %s",
                         config.NODE_NAME, config.NODE_GUID, config.POND, e)
        return False


def broker_post(path: str, body: dict) -> dict:
    url = f"{config.BROKER_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Connection": "keep-alive"})
    with _get_opener().open(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Transit-subnet sync [TRANSIT_SUBNETS_SYNC_V1]
#
# Tell the broker which LAN /24s this node can forward to but does not
# own.  The broker uses this to install namespace routes for those
# subnets on every tunnel this node is part of, and surfaces them in
# peers' my-channels responses so remote nodes learn to route through
# us to reach our LAN-side peers.
#
# This replaces the intent of the dead advertise_thread websocket
# path.  Called on every poll cycle; cheap when the set hasn't changed.
# ---------------------------------------------------------------------------

def _read_transit_sentinel():
    """[TRANSIT_FROM_WINNERS_V1] Read winner-derived transit /24s from
    /etc/sentinels/transit_subnets.tsv (one /24 per line). None if absent."""
    try:
        with open("/etc/sentinels/transit_subnets.tsv") as f:
            return sorted({ln.strip() for ln in f if ln.strip()})
    except OSError:
        return None


def _sync_transit_subnets():
    """POST winner-derived transit /24s to broker if changed.
    Never raises - broker errors are logged and retried next poll."""
    transit = _read_transit_sentinel()
    if transit is None:
        try:
            transit = sorted(config.discover_transit_subnets(config.LOCAL_SUBNET))
        except Exception as e:
            config.log.warning("SYNC_TRANSIT: discover failed: %s", e)
            return

    last = getattr(config, "last_transit_subnets", None)
    if last == transit:
        # Steady state - no diff, no call.
        return

    try:
        resp = broker_post("/api/v4/update-subnets", {
            "pubkey":          config.PUBKEY,
            "transit_subnets": transit,
        })
    except Exception as e:
        # 404 (not registered), 409 (conflict), network errors - log and
        # keep last_transit_subnets unchanged so the next cycle retries.
        config.log.warning("SYNC_TRANSIT: broker update failed "
                           "(transit=%s): %s", transit, e)
        return

    status = resp.get("status", "?")
    added = resp.get("added", [])
    removed = resp.get("removed", [])
    tunnels = resp.get("tunnels_updated", 0)
    config.log.info("SYNC_TRANSIT: %s transit=%s added=%s removed=%s "
                    "tunnels_updated=%d",
                    status, transit, added, removed, tunnels)
    config.last_transit_subnets = transit


# ---------------------------------------------------------------------------
# Local tunnel state (unchanged)
# ---------------------------------------------------------------------------

_active_tunnels: Dict[str, dict] = {}


def _tear_down_all_kernel_wg_ifaces(reason: str) -> int:
    """[BROKER_AUTHORITATIVE_V1] Tear down every wg iface in the kernel.
    Used when this node has no authority to host any tunnel - broker
    unreachable, no internet, no own WAN uplink, or broker-disabled.
    Returns count of ifaces torn down.
    """
    kernel_ifaces = _enumerate_kernel_wg_ifaces()
    if not kernel_ifaces:
        return 0
    config.log.info(
        "TEAR_DOWN_ALL_WG: reason=%s ifaces=%s",
        reason, sorted(kernel_ifaces.keys()))
    n = 0
    for iface, pubkey in sorted(kernel_ifaces.items()):
        try:
            _tear_down_orphan_iface(iface, pubkey, reason=reason)
            n += 1
        except Exception as e:
            config.log.warning(
                "TEAR_DOWN_ALL_WG: failed iface=%s err=%r", iface, e)
    _active_tunnels.clear()
    return n


# A tunnel that handshook this recently is positive, on-wire proof the
# path works.  keepalive is 25s and rekey ~120s, so <180s means a live
# session.  Used to protect live tunnels from transient control-plane
# blips (see _tear_down_stale_only).
HANDSHAKE_LIVE_SEC = int(os.environ.get("FROGNET_HANDSHAKE_LIVE_SEC", "180"))


def _tear_down_stale_only(reason: str) -> int:
    """[TRANSIENT_GUARD_V1] Teardown for the *transient* bring-up
    short-circuits (no_internet / lan_only_node / broker_unreachable).

    Those signals are evidence about OUR control plane this instant - a
    single 8.8.8.8 ping, one uplink scan, one broker fetch - NOT proof
    that any peer became unreachable.  On Seattle5 (2026-06-05 21:34) a
    one-shot failure of one of these tore down wg0/wg1/wg2, three
    tunnels that had handshaked and carried data for 3h, cutting NY-1
    and 10.179.179 until a later merge rebuilt them.

    A tunnel whose last handshake is younger than HANDSHAKE_LIVE_SEC is
    live on the wire regardless of the failing check, so we DO NOT tear
    it down here; the blip is re-evaluated next pass.  Genuinely stale /
    never-handshook ifaces (which were going to fail anyway) are still
    cleaned.  Full unconditional teardown is reserved for operator
    BROKER_DISABLED and for positive broker channel-list omission
    (orphan / broker_removed) - i.e. authority, not failure-to-reach.
    Returns count of ifaces actually torn down.
    """
    kernel_ifaces = _enumerate_kernel_wg_ifaces()
    if not kernel_ifaces:
        return 0
    torn, protected = 0, []
    for iface, pubkey in sorted(kernel_ifaces.items()):
        age = wg_handshake_age(iface)
        if age is not None and age < HANDSHAKE_LIVE_SEC:
            protected.append((iface, int(age)))
            continue
        try:
            _tear_down_orphan_iface(iface, pubkey, reason=reason)
            _active_tunnels.pop(_active_tunnels and iface, None)
            torn += 1
        except Exception as e:
            config.log.warning(
                "TEAR_DOWN_STALE_ONLY: failed iface=%s err=%r", iface, e)
    config.log.info(
        "TEAR_DOWN_STALE_ONLY: reason=%s torn=%d protected=%s "
        "(live tunnels kept across transient control-plane failure)",
        reason, torn, protected)
    return torn


def _rebuild_active_tunnels_from_kernel_and_broker(
        broker_channels: Dict[str, dict]) -> None:
    """[BROKER_AUTHORITATIVE_V1] Populate _active_tunnels from kernel
    wg ifaces whose pubkeys match the broker's response.  Replaces
    _load_local_state for the bringup phase: the source of truth is
    (kernel, broker), never disk cache.

    Each kernel iface whose droplet_pubkey matches a broker channel
    becomes an entry in _active_tunnels, bound to that channel.  Ifaces
    that don't match anything in the broker response are NOT added -
    they'll be caught and torn down by the orphan scan that runs next.
    """
    _active_tunnels.clear()
    if not broker_channels:
        return
    kernel_ifaces = _enumerate_kernel_wg_ifaces()  # {iface: pubkey}
    pubkey_to_ch: Dict[str, str] = {
        ch["wg_config"].get("droplet_pubkey", ""): ch_name
        for ch_name, ch in broker_channels.items()
        if ch["wg_config"].get("droplet_pubkey", "")
    }
    adopted = 0
    for iface, pubkey in kernel_ifaces.items():
        ch_name = pubkey_to_ch.get(pubkey)
        if not ch_name:
            continue
        # Already-adopted channel?  Skip duplicates; the orphan scan
        # will deal with them.
        if ch_name in _active_tunnels:
            continue
        ch = broker_channels[ch_name]
        wgc = ch.get("wg_config", {})
        _active_tunnels[ch_name] = {
            "channel_name":     ch_name,
            "tunnel_name":      ch.get("tunnel_name", ""),
            "interface":        iface,
            "remote_subnets":   wgc.get("remote_subnets", []),
            "droplet_endpoint": wgc.get("droplet_endpoint", ""),
            "droplet_pubkey":   pubkey,
            "local_gateway":    getattr(config, "LOCAL_GW", ""),
            "handshake_wall_ms": 0,   # unknown for adopted ifaces
            "created":          time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "adopted":          True,
        }
        adopted += 1
    config.log.info(
        "REBUILD_ACTIVE_TUNNELS: adopted=%d from kernel matching broker",
        adopted)


def _load_local_state():
    _active_tunnels.clear()
    for sf_path in config.ACTIVE_DIR.glob("*.json"):
        try:
            with open(sf_path) as f:
                sf = json.load(f)
            ch = sf.get("channel_name", "")
            if ch:
                _active_tunnels[ch] = sf
            else:
                # Valid JSON but no channel_name - file is structurally wrong.
                # Log and skip; do NOT delete so we don't destroy state that
                # might be recoverable on a later pass.
                config.log.warning(
                    "_load_local_state: %s has no channel_name; skipping "
                    "(file preserved for inspection)", sf_path)
        except json.JSONDecodeError as e:
            # Empty or partially-written file.  Do NOT delete: the writer
            # may finish mid-cycle, or operator may want to inspect.  An
            # always-broken file is harmless beyond a log line per cycle.
            config.log.warning(
                "_load_local_state: %s malformed JSON (%s); skipping "
                "(file preserved)", sf_path, e)
        except OSError as e:
            config.log.warning(
                "_load_local_state: %s read failed (%s); skipping "
                "(file preserved)", sf_path, e)
    config.log.info("Loaded %d active tunnel(s) from disk",
                    len(_active_tunnels))
    # [BROKER_STATE_PERSIST_V1] Recover the last broker fetch so this
    # process has broker context even if it didn't run bringup itself
    # (commit-only is spawned separately from bring-up-only).
    _load_last_broker_state()


def _save_tunnel_state(channel_name: str, info: dict):
    """Write state atomically: tempfile in same dir, then rename.
    Prevents readers (or the daemon's own next-cycle load) from seeing
    a half-written file if we're killed mid-write."""
    import tempfile
    path = config.ACTIVE_DIR / f"{info['tunnel_name']}.json"
    data = json.dumps(info, indent=4)
    config.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{info['tunnel_name']}.", suffix=".json",
        dir=str(config.ACTIVE_DIR))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _remove_tunnel_state(tunnel_name: str):
    (config.ACTIVE_DIR / f"{tunnel_name}.json").unlink(missing_ok=True)


def _enumerate_kernel_wg_ifaces() -> Dict[str, str]:
    """Return {iface: remote_pubkey} for every live FrogNet-style wgN."""
    out: Dict[str, str] = {}
    try:
        r = subprocess.run(["wg", "show", "interfaces"],
                           capture_output=True, text=True, check=False)
        ifaces = r.stdout.strip().split() if r.stdout.strip() else []
    except Exception as e:
        config.log.warning("ORPHAN_SCAN: wg show interfaces failed: %s", e)
        return out
    for iface in ifaces:
        if not iface.startswith("wg"):
            continue
        try:
            pr = subprocess.run(["wg", "show", iface, "peers"],
                                capture_output=True, text=True, check=False)
            peers = pr.stdout.strip().split()
        except Exception as e:
            config.log.warning("ORPHAN_SCAN: wg show %s peers failed: %s",
                               iface, e)
            continue
        if len(peers) == 1:
            out[iface] = peers[0]
        else:
            config.log.warning("ORPHAN_SCAN: %s has %d peers - not a FrogNet "
                               "tunnel, skipping", iface, len(peers))
    return out


# ---------------------------------------------------------------------------
# LAN-reachable precondition (primer: WG only when remote /24 has no other path)
# ---------------------------------------------------------------------------

def _channel_already_lan_reachable(remote_subnets: List[str]) -> bool:
    """True iff every remote /24 in `remote_subnets` is already covered
    by a non-WG, non-default route in the kernel table.

    Primer rule: WG tunnels exist only when the remote /24 can't be
    reached any other way; never built for LAN-adjacent peers.  This
    is the bringup-side gate that enforces it.  Symmetric with the
    planner's teardown of channels that win zero /24s - together they
    keep the steady state at "WG exists iff at least one /24 has no
    non-WG alternative."

    Fail-open: if the route table can't be read, return False so we
    err toward building the tunnel rather than missing connectivity.
    """
    if not remote_subnets:
        return False
    try:
        out = subprocess.check_output(
            ["ip", "route", "show"], text=True, timeout=2)
    except Exception as e:
        config.log.warning(
            "LAN_REACH_CHECK: ip route show failed: %s (fail-open)", e)
        return False
    lines_by_dest: Dict[str, List[str]] = {}
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        cidr = parts[0]
        if cidr == "default":
            continue
        lines_by_dest.setdefault(cidr, []).append(line)
    for subnet in remote_subnets:
        lines = lines_by_dest.get(subnet, [])
        non_wg_found = False
        for line in lines:
            parts = line.split()
            if "dev" not in parts:
                continue
            i = parts.index("dev")
            if i + 1 >= len(parts):
                continue
            if not parts[i + 1].startswith("wg"):
                non_wg_found = True
                break
        if not non_wg_found:
            return False
    return True


# ---------------------------------------------------------------------------
# Tunnel bring-up (unchanged except for the route-install call at the end)
# ---------------------------------------------------------------------------

def _bring_up_tunnel(channel: dict) -> bool:
    """Bring up a WireGuard tunnel for a channel.  Returns True iff the
    tunnel came up and handshook with the droplet.

    Records the wall-clock time from `wg-quick up` to first successful
    handshake in the tunnel's info dict as `handshake_wall_ms`.  This
    is the tunnel's RTT for the LAN-vs-WG comparison that
    sync_interfaces does between wave 1 and wave 2: a LAN observation
    for the same /24 wins if its RTT is lower, otherwise the tunnel
    promotes its /24 at this handshake RTT and wave 2 probes it for a
    real frognet_echo RTT.

    Does NOT probe the far edge and does NOT install /24 routes.  The
    committer installs routes based on observations sync_interfaces
    and the commit-only phase accumulate."""
    wg_config = channel["wg_config"]
    tunnel_name = channel["tunnel_name"]
    remote_subnets = wg_config.get("remote_subnets", [])
    channel_name = channel["channel_name"]

    config.log.info("BRING_UP: %s tunnel=%s subnets=%s endpoint=%s",
                    channel_name, tunnel_name, remote_subnets,
                    wg_config.get("droplet_endpoint", ""))

    t0 = time.monotonic()

    from . import wg as wg_mod

    # Phase 1: create WG interface (serialized by lock)
    with wg_mod._wg_up_lock:
        iface = find_next_wg_iface()
        conf_path = config.CONF_DIR / f"{iface}.conf"
        conf_path.write_text(
            f"# FrogNet Tunnel - {tunnel_name}\n"
            f"[Interface]\n"
            f"Address = {wg_config['edge_ip']}/{wg_config['edge_mask']}\n"
            f"PrivateKey = {config.PRIVKEY}\n"
            f"Table = off\n"
            f"\n"
            f"[Peer]\n"
            f"PublicKey = {wg_config['droplet_pubkey']}\n"
            f"Endpoint = {wg_config['droplet_endpoint']}\n"
            f"AllowedIPs = {wg_config['allowed_ips']}\n"
            f"PersistentKeepalive = 25\n"
        )
        conf_path.chmod(0o600)

        r = subprocess.run(["wg-quick", "up", iface],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            config.log.error("BRING_UP: wg-quick up %s FAILED: %s",
                             iface, r.stderr.strip())
            conf_path.unlink(missing_ok=True)
            return False

    # Phase 2: wait for handshake
    handshake_ok = False
    handshake_wall_ms = 0
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        age = wg_handshake_age(iface)
        if age is not None and age < config.HANDSHAKE_DEAD_SEC:
            handshake_wall_ms = int((time.monotonic() - t0) * 1000)
            config.log.info(
                "BRING_UP: %s handshake OK on %s (age=%ds wall=%dms)",
                tunnel_name, iface, age, handshake_wall_ms)
            handshake_ok = True
            break
        time.sleep(0.5)
    if not handshake_ok:
        config.log.error("BRING_UP: %s no handshake after 30s on %s - tearing "
                         "down", tunnel_name, iface)
        teardown_wg_iface(iface)
        conf_path.unlink(missing_ok=True)
        return False

    # NOTE: NO far-edge .2 probe here, deliberately.
    #
    # A fresh WG tunnel's handshake with the droplet succeeds BEFORE
    # the far edge peer has brought its .2 alias up or started its
    # daemon.  Probing .2 at bring-up gives false negatives and tears
    # down tunnels that would work fine 30s later.  We chased this
    # exact bug across multiple sessions: discovery would never
    # converge because every tunnel got nuked right after coming up,
    # so by the time the measurement phase ran there were no tunnels
    # left to measure.
    #
    # The real liveness check is Phase 4 of _reconcile_tunnels_inner:
    # every surviving tunnel gets a .2 RTT probe there, an Observation
    # is emitted, and any tunnel that wins no destination in the
    # committer's plan gets torn down (directed, logged with reason).
    # If the far edge is genuinely unreachable the tunnel will lose
    # every race and die on the correct pass.  If it comes online
    # between bring-up and the next reconcile, it's already there,
    # ready to probe.
    #
    # Handshake-OK-means-usable is the bring-up contract.  No more,
    # no less.

    # Phase 3: save state.  No /24 route install - the committer does
    # that, based on the tunnel Observation Phase 4 of reconcile emits.
    info = {
        "channel_name":     channel_name,
        "tunnel_name":      tunnel_name,
        "interface":        iface,
        "remote_subnets":   remote_subnets,
        "droplet_endpoint": wg_config.get("droplet_endpoint", ""),
        "droplet_pubkey":   wg_config.get("droplet_pubkey", ""),
        "local_gateway":    config.LOCAL_GW,
        "handshake_wall_ms": handshake_wall_ms,
        "created":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _active_tunnels[channel_name] = info
    _save_tunnel_state(channel_name, info)
    _bringup_install_routes(channel_name, iface, remote_subnets)

    config.log.info("BRING_UP: %s UP on %s",
                    tunnel_name, iface)
    return True


def _tear_down_tunnel(channel_name: str):
    """Tear down a tunnel's kernel iface + Python state.  No /24 removal
    here - the committer's remove() handled the /24 removals if needed."""
    info = _active_tunnels.pop(channel_name, None)
    if not info:
        return
    iface = info.get("interface", "")
    tunnel_name = info.get("tunnel_name", "")
    config.log.info("TEAR_DOWN: %s on %s", tunnel_name, iface)
    if iface:
        teardown_wg_iface(iface)
    _remove_tunnel_state(tunnel_name)
    config.log.info("TEAR_DOWN: %s DOWN", tunnel_name)


def _tear_down_orphan_iface(iface: str, pubkey: str, reason: str):
    """Tear down a kernel wgN that isn't in _active_tunnels.  Orphan policy:
    broker-authoritative.  Routes through this iface are dropped by the
    kernel automatically when the iface is removed."""
    config.log.info("ORPHAN_TEARDOWN: iface=%s pubkey=%s reason=%s",
                    iface, pubkey[:16] + "...", reason)
    teardown_wg_iface(iface)
    conf_path = config.CONF_DIR / f"{iface}.conf"
    conf_path.unlink(missing_ok=True)
    for sf_path in config.ACTIVE_DIR.glob("*.json"):
        try:
            with open(sf_path) as f:
                sf = json.load(f)
            if sf.get("interface", "") == iface:
                sf_path.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            pass


# ---------------------------------------------------------------------------
# Poll cycle (unchanged - watches for broker-list changes only)
# ---------------------------------------------------------------------------

def _verify_one_tunnel(channel_name: str, info: dict) -> bool:
    iface = info.get("interface", "")
    subnets = info.get("remote_subnets", [])
    if not iface or not subnets:
        config.log.warning("VERIFY: %s incomplete state", channel_name)
        return False
    age = wg_handshake_age(iface)
    if age is None or age >= config.HANDSHAKE_DEAD_SEC:
        config.log.warning("VERIFY: %s handshake dead (age=%s)",
                           channel_name, age)
        return False
    probe_timeout = float(os.environ.get(
        "FROGNET_TUNNEL_VERIFY_TIMEOUT_SEC", "5.0"))
    gw = _subnet_gateway(subnets[0])
    rtt = frognet_echo(gw, timeout=probe_timeout)
    if rtt is None:
        config.log.warning("VERIFY: %s semantic path dead", channel_name)
        return False
    return True


def _verify_tunnels_parallel(channel_names) -> List[str]:
    names = [n for n in channel_names if n in _active_tunnels]
    if not names:
        return []
    results: Dict[str, bool] = {}
    results_lock = threading.Lock()
    done_sem = threading.Semaphore(0)

    def _worker(ch_name: str):
        info = _active_tunnels.get(ch_name)
        ok = False
        if info:
            try:
                ok = _verify_one_tunnel(ch_name, info)
            except Exception as e:
                config.log.exception("VERIFY: %s: %s", ch_name, e)
        with results_lock:
            results[ch_name] = ok
        done_sem.release()

    for ch_name in names:
        threading.Thread(target=_worker, args=(ch_name,),
                         name=f"verify-{ch_name}", daemon=True).start()
    for _ in range(len(names)):
        done_sem.acquire()
    return [ch for ch, ok in results.items() if not ok]


def poll_once() -> bool:
    """Passive watcher.  Forks runMerge when the broker's channel set
    changes.  Doesn't do any tunnel work itself."""
    # [TRANSIT_SUBNETS_SYNC_V1] push transit /24s before fetching our
    # channel list so the response we receive reflects any just-pushed
    # change on peers that include us as a host/joiner.
    _sync_transit_subnets()

    try:
        resp = broker_get("/api/v4/my-channels", {"pubkey": config.PUBKEY})
    except Exception as e:
        config.log.warning("POLL: broker unreachable: %s", e)
        return False

    digest = resp.get("digest", "")
    channels = resp.get("channels", [])
    chorus_assignments = resp.get("chorus_assignments", [])
    broker_names: Set[str] = {ch["channel_name"] for ch in channels}
    previous_names: Set[str] = getattr(config, "last_broker_names", set())

    # Diagnostic delta: what changed on the broker side this poll.
    broker_added   = broker_names - previous_names
    broker_removed = previous_names - broker_names

    # Self-heal delta: what diverges between broker truth and our
    # actual running tunnel state.  This is the authoritative trigger
    # for runMerge - fires whenever we need to bring something up or
    # tear something down, regardless of whether the broker JSON itself
    # changed this cycle.  Catches: orphan teardown by another path,
    # kernel iface vanished, daemon restart with empty in-memory
    # last_broker_names, etc.
    running_chs: Set[str] = set(_active_tunnels.keys())
    needs_bringup  = broker_names - running_chs
    needs_teardown = running_chs - broker_names

    config.log.info(
        "POLL: broker=%d running=%d broker_added=%d broker_removed=%d "
        "needs_bringup=%d needs_teardown=%d digest=%s",
        len(broker_names), len(running_chs),
        len(broker_added), len(broker_removed),
        len(needs_bringup), len(needs_teardown), digest)
    _sync_chorus_ips(chorus_assignments)
    config.last_broker_names = broker_names
    config.last_digest = digest
    if not needs_bringup and not needs_teardown:
        return False

    if broker_added:
        config.log.info("POLL: broker added channels=%s", sorted(broker_added))
    if broker_removed:
        config.log.info("POLL: broker removed channels=%s", sorted(broker_removed))
    if needs_bringup:
        config.log.info("POLL: needs bringup=%s", sorted(needs_bringup))
    if needs_teardown:
        config.log.info("POLL: needs teardown=%s", sorted(needs_teardown))
    config.log.info("POLL: firing runMerge")
    try:
        subprocess.Popen(
            ["/usr/local/bin/runMerge.bash"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True,
            start_new_session=True,
        )
    except Exception as e:
        config.log.error("POLL: failed to fork runMerge: %s", e)
        return False
    return True


# ---------------------------------------------------------------------------
# Reconcile helpers (observation + committer plumbing)
# ---------------------------------------------------------------------------

_RECONCILE_LOCK_PATH = "/var/lock/frognet-tunnel-reconcile.lock"


def _read_discovered_hosts() -> Set[str]:
    try:
        with open("/etc/sentinels/discovered_hosts") as f:
            return {line.strip() for line in f if line.strip()}
    except (OSError, IOError):
        return set()


def _broker_reachable() -> bool:
    """The ONLY definition of "online": the broker answers. Reuses the daemon's
    own broker client (same URL/pubkey/timeouts as every other call), so a node
    that reaches the broker over the LAN transit mesh counts as online exactly
    like one with a direct WAN. A 401/404 still means the server ANSWERED (it is
    reachable); only a transport failure means unreachable."""
    try:
        broker_get("/api/v4/my-channels", {"pubkey": config.PUBKEY})
        return True
    except urllib.error.HTTPError:
        return True   # broker answered (even if it 401/404s) => reachable
    except Exception:
        return False


def _have_internet() -> bool:
    """Ping 8.8.8.8. NOTE: no longer used as an online gate (online == broker
    reachable). Retained only for any diagnostic caller."""
    r = subprocess.run(
        ["ping", "-c", "1", "-W", "2", "-n", "8.8.8.8"],
        capture_output=True, check=False)
    return r.returncode == 0

def _node_has_own_uplink() -> bool:
    """True if this node has a non-FrogNet interface with a non-10.x
    IPv4 address - i.e., a real WAN of its own that can originate
    WireGuard handshakes to the broker.

    Distinct from _have_internet, which only proves that *something
    upstream* can reach 8.8.8.8.  A LAN-child like SeattleTwo passes
    _have_internet (its default route forwards through SeattleSix's
    WiFi to a NATing gateway) but has no IP a WireGuard endpoint can
    bind to: all its addresses are 10.x - its own served /24 plus a
    DHCP lease from the parent node's WiFi.

    Excludes lo, frognet*, wg*, and any interface whose only IPv4
    addresses are in 10.0.0.0/8 (FrogNet overlay space).  An interface
    with a 172.16/12 or 192.168/16 RFC1918 address still counts as a
    real uplink for this purpose - those nodes are doing standard NAT
    to the internet on their own, which is what the broker handshake
    needs.

    On a node that fails this check, attempting broker tunnels is
    guaranteed to fail: wg-quick brings the iface up, no UDP handshake
    packet can be sourced, all four channels time out after 30s and
    tear down.  Bringup wastes ~30s per channel and produces nothing.
    """
    try:
        r = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, check=False, timeout=2)
    except Exception as e:
        # Can't tell - fail-open so we don't accidentally block a
        # legitimate uplink node from bringing up tunnels.
        config.log.warning(
            "UPLINK_CHECK: ip addr show failed: %s (fail-open)", e)
        return True
    if r.returncode != 0:
        config.log.warning(
            "UPLINK_CHECK: ip addr show rc=%d stderr=%s (fail-open)",
            r.returncode, (r.stderr or "").strip())
        return True
    for line in r.stdout.splitlines():
        # Format: "<idx>: <iface>    inet <addr>/<prefix> ..."
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        if iface == "lo":
            continue
        if iface.startswith("frognet") or iface.startswith("wg"):
            continue
        addr_cidr = parts[3]
        addr = addr_cidr.split("/", 1)[0]
        if not addr.startswith("10."):
            return True
    return False

def _measure_tunnel_rtt(probe_ip: str, iface: str) -> Optional[int]:
    """Install /32 to probe_ip on iface at metric 1, curl the echo
    through the full stack, measure elapsed time, delete the /32.
    Returns integer ms on success, None on failure.

    This is the tunnel-side equivalent of what sync_interfaces does
    for LAN probes.  It forces the probe through a specific wgN even
    if a LAN /24 is already installed for the same dest."""
    timeout = float(os.environ.get("FROGNET_TUNNEL_RTT_TIMEOUT_SEC", "5.0"))
    # [DELETE_THEN_ADD_V1 2026-05-25] Was `ip route replace`.  Same
    # rationale as peer.py: metric=1 is reserved for these probe /32s,
    # so deleting unconditionally by (dest, metric=1) clears any prior
    # leak before we install fresh on this iface.
    subprocess.run(
        ["ip", "route", "del", f"{probe_ip}/32", "metric", "1"],
        capture_output=True, check=False)
    subprocess.run(
        ["ip", "route", "add", f"{probe_ip}/32",
         "dev", iface, "metric", "1"],
        capture_output=True, check=False)
    rtt_ms: Optional[int] = None
    try:
        t0 = time.monotonic()
        rc = subprocess.run(
            ["curl", "-fsS",
             "--connect-timeout", "2",
             "--max-time", str(timeout),
             "-H", f"Host: {probe_ip}",
             f"http://{probe_ip}/frognet_echo.php"],
            capture_output=True, text=True, check=False,
            timeout=timeout + 2)
        if rc.returncode == 0 and rc.stdout.strip():
            rtt_ms = max(1, int((time.monotonic() - t0) * 1000))
    except subprocess.TimeoutExpired:
        pass
    finally:
        subprocess.run(["ip", "route", "del", f"{probe_ip}/32"],
                       capture_output=True, check=False)
    return rtt_ms


# _emit_tunnel_observations was removed in the parallel refactor.
#
# Previously this function ran in the reconcile commit phase AFTER
# sync_interfaces had exited, probing each active tunnel's subnet .2
# for RTT and emitting observations.  In the new architecture, tunnels
# come up BEFORE sync_interfaces wave 2, and wave 2 probes every
# discovered host through every 10.x interface - including the freshly-
# up wg ifaces - as part of normal discovery.  The separate reconcile
# probe became redundant.
#
# A tunnel that sync_interfaces cannot probe contributes no observation,
# wins no dest, and is torn down by the committer via the planner's
# tear_down_tunnels list.  Same contract as before; one pipeline now
# instead of two.
#
# _measure_tunnel_rtt remains above for any caller that wants direct
# RTT measurement of a specific tunnel subnet (none in the current
# call graph; it's preserved for potential diagnostic use).


def _write_channel_map() -> None:
    """Write {channel_name: iface} for every active tunnel so the
    committer can translate channel names to iface names on teardown
    and the planner sees the channels as active_tunnel_channels.

    [COLD_STANDBY_REMOVED_20260518] The previous version excluded
    channels with handshake age < 180s on the theory that the old
    teardown rule (active - winning_channels) would tear them down
    before observations could arrive.  That rule is gone - replaced by
    BROKER_TEARDOWN_V1 (active - broker_channels) - so the broker now
    owns existence and there's no scenario where the committer
    spontaneously tears down a fresh-handshake channel.  Hiding the
    channels from the map was actively harmful: it made the planner
    treat them as nonexistent for winner selection.
    """
    ch_to_iface = {
        ch_name: info.get("interface", "")
        for ch_name, info in _active_tunnels.items()
        if info.get("interface")
    }
    try:
        os.makedirs(os.path.dirname(CHANNEL_MAP_PATH), exist_ok=True)
        with open(CHANNEL_MAP_PATH, "w") as f:
            json.dump(ch_to_iface, f, indent=2)
    except OSError as e:
        config.log.warning("channel_map write failed: %s", e)
    # Sidecars for the new planner rules.  Failures here are logged
    # but don't stop the committer from running - without these files
    # the planner falls back to its legacy rules.
    _write_committer_sidecars(ch_to_iface)


def _write_committer_sidecars(ch_to_iface: Dict[str, str]) -> None:
    """[BROKER_TEARDOWN_V1] Write the broker-channels sidecar the
    committer reads alongside channel_map.json.

    broker_channels.json - JSON list of channel names the broker
        currently has for this node.  Source: config.last_broker_names
        (Set[str]) populated by Phase 1 of run_bringup_phase.  The
        committer uses this to drive teardown as active-minus-broker
        (orphans only).  Cold-standby channels are INCLUDED here even
        though they were excluded from channel_map; the planner won't
        try to tear down a channel it can't see in active_tunnel_channels
        (which comes from channel_map), so cold-standby logic still
        holds.

    The traceroute TSV consumed by the committer is written by
    sync_interfaces.sh during discovery, not by poll.py.  See
    TRACEROUTES_PATH.
    """
    broker_names = sorted(getattr(config, "last_broker_names", set()))
    try:
        os.makedirs(os.path.dirname(BROKER_CHANNELS_PATH), exist_ok=True)
        with open(BROKER_CHANNELS_PATH, "w") as f:
            json.dump(broker_names, f, indent=2)
    except OSError as e:
        config.log.warning("broker_channels write failed: %s", e)


def _persist_last_broker_state(broker_names: Set[str],
                               broker_channels: Dict[str, dict]) -> None:
    """[BROKER_STATE_PERSIST_V1] Write the latest broker fetch to disk
    so the next process (commit-only, separately spawned by
    mergeHostsAndResolv) can recover it.  Without this, commit-only
    sees config.last_broker_* as unset and _write_committer_sidecars
    writes empty sidecars - leading the committer to tear down every
    active tunnel as an orphan.

    Schema:
      {"names":    ["ChannelA", "ChannelB", ...],
       "channels": {"ChannelA": {<broker channel dict verbatim>}, ...}}

    Read by _load_local_state() at process startup.
    """
    payload = {
        "names": sorted(broker_names),
        "channels": broker_channels,
    }
    try:
        os.makedirs(os.path.dirname(LAST_BROKER_STATE_PATH), exist_ok=True)
        # Atomic: tempfile in same dir, fsync, replace.  Same pattern as
        # _save_tunnel_state.  Half-written files would defeat the
        # purpose: commit-only would read garbage or empty.
        import tempfile
        dir_ = os.path.dirname(LAST_BROKER_STATE_PATH)
        fd, tmp = tempfile.mkstemp(prefix=".last_broker_state.", dir=dir_)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            os.replace(tmp, LAST_BROKER_STATE_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        config.log.debug("LAST_BROKER_STATE_WRITE: ok path=%s names=%d",
                         LAST_BROKER_STATE_PATH, len(broker_names))
    except OSError as e:
        config.log.warning("LAST_BROKER_STATE_WRITE FAILED: %s", e)


def _load_last_broker_state() -> None:
    """[BROKER_STATE_PERSIST_V1] Populate config.last_broker_names and
    config.last_broker_channels from disk.  Called by _load_local_state
    at process startup.  Silent on missing file - first run, or running
    on a node that hasn't completed a bringup phase yet."""
    try:
        with open(LAST_BROKER_STATE_PATH) as f:
            payload = json.load(f)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as e:
        config.log.warning("LAST_BROKER_STATE_LOAD failed: %s", e)
        return
    names = payload.get("names") or []
    channels = payload.get("channels") or {}
    if not isinstance(names, list) or not isinstance(channels, dict):
        config.log.warning(
            "LAST_BROKER_STATE_LOAD: schema mismatch in %s (names=%s channels=%s); ignoring",
            LAST_BROKER_STATE_PATH, type(names).__name__, type(channels).__name__)
        return
    config.last_broker_names = set(names)
    config.last_broker_channels = channels
    config.log.info("LAST_BROKER_STATE_LOAD: ok names=%d channels=%d",
                    len(names), len(channels))


def _run_committer() -> bool:
    """Run `python -m frognet_route commit-final`.  Returns True on
    success (rc=0).  Stdout/stderr are forwarded line-by-line to our
    log so planner/committer INFO/WARN/ERROR lines appear in the
    merge log alongside the tunnel daemon's own lines."""
    cmd = FROGNET_ROUTE_CMD + [
        "commit-final",
        "--channel-map", CHANNEL_MAP_PATH,
        "--broker-channels-path", BROKER_CHANNELS_PATH,
        "--traceroutes-path", TRACEROUTES_PATH,
    ]
    config.log.debug("COMMITTER_INVOKE: cmd=%s", cmd)
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    dur_ms = int((time.monotonic() - t0) * 1000)

    # Forward every non-empty line from committer's stdout/stderr to
    # our log.  Committer's own logging module uses stderr; we emit at
    # INFO/WARN based on a crude prefix check so interesting bits stand
    # out.
    for line in (r.stdout or "").splitlines():
        if line.strip():
            config.log.info("COMMITTER_stdout: %s", line)
    for line in (r.stderr or "").splitlines():
        if not line.strip():
            continue
        lv = line.lower()
        if "error" in lv or "failed" in lv:
            config.log.error("COMMITTER_stderr: %s", line)
        elif "warn" in lv:
            config.log.warning("COMMITTER_stderr: %s", line)
        else:
            config.log.info("COMMITTER_stderr: %s", line)

    if r.returncode != 0:
        config.log.error("COMMITTER_EXIT: rc=%d dur_ms=%d", r.returncode, dur_ms)
        return False
    config.log.info("COMMITTER_EXIT: rc=0 dur_ms=%d", dur_ms)
    return True


def _reconcile_local_state_with_kernel() -> None:
    """Any channel in _active_tunnels whose iface is gone from the
    kernel (because the committer brought it down) loses its Python
    state + state file.  Keeps the two in sync."""
    try:
        kernel_ifaces = set(
            _enumerate_kernel_wg_ifaces().keys()
        )
    except Exception as e:
        config.log.warning("STATE_RECONCILE: wg_enum_failed err=%s", e)
        return
    config.log.debug("STATE_RECONCILE: kernel_ifaces=%s python_state=%s",
                     sorted(kernel_ifaces),
                     sorted(_active_tunnels.keys()))
    cleaned = 0
    for ch_name in list(_active_tunnels.keys()):
        info = _active_tunnels[ch_name]
        iface = info.get("interface", "")
        if iface and iface not in kernel_ifaces:
            config.log.info("STATE_RECONCILE: clean ch=%s iface=%s reason=iface_gone_from_kernel",
                            ch_name, iface)
            _active_tunnels.pop(ch_name, None)
            _remove_tunnel_state(info.get("tunnel_name", ""))
            cleaned += 1
    config.log.info("STATE_RECONCILE: done cleaned=%d remaining_active=%d",
                    cleaned, len(_active_tunnels))


# ---------------------------------------------------------------------------
# Reconcile - the tail end of every runMerge pass
# ---------------------------------------------------------------------------

# _reconcile_tunnels_inner is defined below, after the new phase helpers.


# ---------------------------------------------------------------------------
# [HANDSHAKE_GRACE_V3 2026-06-06] Per-channel consecutive-failed-refresh grace.
#
# The teardown decision keys on the WireGuard handshake (L3 liveness), never on
# the L7 echo - a tunnel can be alive at L3 while frognet_echo.php blips, and
# tearing it down on the echo just churns a working tunnel (the 21:34 SeattleV
# failure).  When the handshake IS stale we try an in-place refresh; if that
# fails we do NOT tear down immediately.  We count consecutive failed cycles in
# a small JSON file and only tear down once the count reaches
# config.HANDSHAKE_GRACE_CYCLES, giving the peer "two check cycles to restore
# itself" before the destructive rebuild.  The count resets to 0 the moment the
# handshake is fresh or a refresh succeeds.
# ---------------------------------------------------------------------------
def _load_grace_counts() -> Dict[str, int]:
    try:
        with open(config.GRACE_STATE_PATH) as f:
            d = json.load(f)
        return {k: int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_grace_counts(counts: Dict[str, int]) -> None:
    # Drop zero/absent entries so the file stays small and a healthy fleet
    # converges to {}.  Atomic write (tmp + rename) like the other state files.
    pruned = {k: v for k, v in counts.items() if v > 0}
    try:
        import tempfile
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".handshake_grace.", suffix=".json",
                                   dir=str(config.STATE_DIR))
        with os.fdopen(fd, "w") as f:
            json.dump(pruned, f)
        os.replace(tmp, config.GRACE_STATE_PATH)
    except OSError as e:
        config.log.warning("HANDSHAKE_GRACE: could not persist counts: %s", e)


def _tunnel_health_verdict(ch_name, iface, *, age_fn, refresh_fn,
                           grace_counts, grace_cycles, dead_sec,
                           refresh_wait_sec, log):
    """Decide what to do with ONE recorded tunnel this cycle, keyed on the
    WireGuard handshake.  Pure decision + logging; the CALLER performs any
    teardown based on the returned action so this stays drivable from the
    simulator with injected age_fn / refresh_fn / log and no real kernel.

    age_fn(iface)            -> handshake age in seconds, or None
    refresh_fn(iface, wait)  -> fresh age after an in-place kick, or None
    grace_counts             -> dict ch->consecutive failed cycles (MUTATED)

    Returns one of:
      "keep_live"          handshake fresh; leave alone (echo blips are L7)
      "teardown_no_iface"  no iface string recorded; cannot refresh - teardown
      "refreshed"          stale, but in-place refresh produced a handshake
      "grace_hold"         refresh failed but still inside the grace window
      "teardown_dead"      refresh failed grace_cycles cycles running - teardown
    """
    if not iface:
        # Structurally broken: nothing to probe or refresh.  No grace can
        # apply to a channel with no interface.  Clear any stale counter.
        grace_counts.pop(ch_name, None)
        log.warning(
            "TUNNEL_HEALTH: ch=%s reason=no_iface_recorded action=teardown "
            "(no interface to probe or refresh; grace N/A)", ch_name)
        return "teardown_no_iface"

    age = age_fn(iface)
    prior = int(grace_counts.get(ch_name, 0))

    if age is not None and age < dead_sec:
        # Fresh handshake - tunnel is alive at L3 regardless of any echo
        # failure.  Reset the grace counter and leave the iface untouched.
        if prior:
            log.info("TUNNEL_HEALTH: ch=%s iface=%s age=%ds threshold=%ds "
                     "action=keep_live grace_reset_from=%d "
                     "(handshake fresh; prior failures cleared)",
                     ch_name, iface, int(age), dead_sec, prior)
        grace_counts.pop(ch_name, None)
        return "keep_live"

    # Stale handshake (or no handshake line at all).  Try in-place refresh
    # BEFORE counting a dead cycle - a successful kick saves the tunnel with
    # zero route disturbance.
    age_str = "none" if age is None else f"{int(age)}s"
    log.info(
        "TUNNEL_HEALTH: ch=%s iface=%s age=%s threshold=%ds wait=%ds "
        "prior_fail_cycles=%d grace_cycles=%d action=refresh_attempt "
        "(handshake stale; kicking persistent-keepalive in place)",
        ch_name, iface, age_str, dead_sec, refresh_wait_sec,
        prior, grace_cycles)
    new_age = refresh_fn(iface, refresh_wait_sec)
    if new_age is not None:
        log.info(
            "TUNNEL_HEALTH: ch=%s iface=%s age=%s new_age=%ds "
            "action=refreshed grace_reset_from=%d "
            "(in-place handshake restored; routes preserved, no teardown)",
            ch_name, iface, age_str, int(new_age), prior)
        grace_counts.pop(ch_name, None)
        return "refreshed"

    # Refresh failed this cycle.  Increment the consecutive-failure counter
    # and only tear down once it reaches the grace threshold.
    count = prior + 1
    if count < grace_cycles:
        grace_counts[ch_name] = count
        log.warning(
            "TUNNEL_HEALTH: ch=%s iface=%s age=%s action=grace_hold "
            "fail_cycle=%d/%d "
            "(refresh produced no handshake, but inside grace window - "
            "NOT torn down; route/host filter still excludes it this merge; "
            "will retry refresh next cycle)",
            ch_name, iface, age_str, count, grace_cycles)
        return "grace_hold"

    grace_counts.pop(ch_name, None)
    log.warning(
        "TUNNEL_HEALTH: ch=%s iface=%s age=%s action=teardown "
        "fail_cycle=%d/%d reason=refresh_failed_handshake_dead "
        "(no fresh handshake for %d consecutive cycles after "
        "persistent-keepalive kicks; treating as actually-dead, "
        "tearing down so bring-up rebuilds a fresh tunnel on a later merge)",
        ch_name, iface, age_str, count, grace_cycles, grace_cycles)
    return "teardown_dead"


def _reconcile_bringup_phase() -> bool:
    """Phase A of the split reconcile.  Fetches broker channel list,
    tears down orphan wgN, tears down channels the broker removed,
    brings up missing channels in parallel.

    Writes BRINGUP_READY sentinel after every bring-up thread has
    joined, so sync_interfaces's wave-2 barrier can proceed.
    """
    t0 = time.monotonic()
    config.log.info("BRINGUP_PHASE: status=enter")
    changed = False

    # [BROKER_AUTHORITATIVE_V1 2026-06-01] Four short-circuits below
    # all converge on the same outcome: this node cannot be a tunnel
    # endpoint right now, so every kernel wg iface must come down.
    # Previously these signaled "ready" and left existing ifaces
    # alone, which violated the principle that the broker's response
    # (or lack thereof) is the complete authoritative state.

    # LAN-only mode: configured to never speak to the broker.
    if getattr(config, "BROKER_DISABLED", False):
        config.log.info(
            "BRINGUP_PHASE: status=broker_disabled - "
            "tearing down all wg ifaces")
        n = _tear_down_all_kernel_wg_ifaces(reason="broker_disabled")
        _signal_bringup_ready()
        return n > 0

    # Phase 0: broker reachability is the ONLY online test. online == the
    # broker answers; NOT "has internet", NOT "has a non-10.x address". A node
    # can reach the broker over the LAN transit mesh with no internet at all,
    # and such a node is legitimately online and must get its channels. The
    # old _have_internet() (ping 8.8.8.8) and _node_has_own_uplink() (non-10.x
    # address) gates tore down and returned BEFORE the fetch, so a broker-
    # reachable node on the LAN side never built its tunnels on reconnect until
    # frognet-tunnel-setup was run by hand. They are gone. Whether any single
    # tunnel can actually handshake is proved per-tunnel in _bring_up_tunnel
    # (30s handshake wait, then teardown of that one tunnel) - that is the
    # correct place for "this endpoint can't handshake", not a blanket pre-gate.
    if not _broker_reachable():
        config.log.info(
            "BRINGUP_PHASE: status=broker_unreachable - "
            "tearing down stale (broker did not answer)")
        n = _tear_down_stale_only(reason="broker_unreachable")
        _signal_bringup_ready()
        return n > 0

    # Phase 1: fetch broker channel list
    try:
        t_fetch0 = time.monotonic()
        resp = broker_get("/api/v4/my-channels", {"pubkey": config.PUBKEY})
        fetch_ms = int((time.monotonic() - t_fetch0) * 1000)
    except urllib.error.HTTPError as he:
        # [GUID_IDENTITY_V1] A 404/401 here means the broker is REACHABLE and
        # says it does not know this pubkey - the node is retired/forgotten, not
        # offline. The old code bucketed this with broker_unreachable, tore down
        # all wg ifaces, and STOPPED - so a node could never come back to life.
        # Instead: re-register with our GUID (broker REVIVES the row), then retry
        # my-channels ONCE. Only if that still fails do we fall through to the
        # stale teardown. A genuine connection failure is NOT an HTTPError and
        # stays on the unreachable path below.
        if he.code in (401, 404):
            config.log.warning(
                "BRINGUP_PHASE: my-channels %d (broker does not know us) - "
                "self-registering with GUID and retrying", he.code)
            if register_with_broker():
                try:
                    t_fetch0 = time.monotonic()
                    resp = broker_get("/api/v4/my-channels",
                                      {"pubkey": config.PUBKEY})
                    fetch_ms = int((time.monotonic() - t_fetch0) * 1000)
                    config.log.info("BRINGUP_PHASE: re-register + retry "
                                    "succeeded - node is back in the mesh")
                except Exception as e2:
                    config.log.warning(
                        "BRINGUP_PHASE: retry after register failed err=%s - "
                        "tearing down stale only", e2)
                    n = _tear_down_stale_only(reason="broker_unreachable")
                    _signal_bringup_ready()
                    return n > 0
            else:
                config.log.warning(
                    "BRINGUP_PHASE: self-register failed - tearing down stale "
                    "only (node has no usable identity yet)")
                n = _tear_down_stale_only(reason="broker_unreachable")
                _signal_bringup_ready()
                return n > 0
        else:
            config.log.warning(
                "BRINGUP_PHASE: broker_unreachable err=%s - "
                "tearing down all wg ifaces", he)
            n = _tear_down_stale_only(reason="broker_unreachable")
            _signal_bringup_ready()
            return n > 0
    except Exception as e:
        config.log.warning(
            "BRINGUP_PHASE: broker_unreachable err=%s - "
            "tearing down all wg ifaces", e)
        n = _tear_down_stale_only(reason="broker_unreachable")
        _signal_bringup_ready()
        return n > 0
    channels = resp.get("channels", [])
    broker_channels: Dict[str, dict] = {
        ch["channel_name"]: ch for ch in channels
    }
    broker_names: Set[str] = set(broker_channels.keys())
    config.log.info("BRINGUP_PHASE: broker_fetched count=%d fetch_ms=%d names=%s",
                    len(broker_names), fetch_ms, sorted(broker_names))

    config.last_broker_names = broker_names
    config.last_broker_channels = broker_channels
    _persist_last_broker_state(broker_names, broker_channels)

    # [BROKER_AUTHORITATIVE_V1] Rebuild _active_tunnels from kernel
    # state, scoped to ifaces matching the broker's current pubkeys.
    # Replaces _load_local_state for this phase: source of truth is
    # (kernel, broker), not the on-disk JSON cache from a previous
    # cycle.  After this call, _active_tunnels reflects exactly which
    # kernel ifaces are currently bound to broker-known channels;
    # downstream orphan/duplicate logic operates on that ground truth.
    _rebuild_active_tunnels_from_kernel_and_broker(broker_channels)

    # Orphan teardowns
    kernel_ifaces = _enumerate_kernel_wg_ifaces()
    broker_pubkey_to_name: Dict[str, str] = {
        ch["wg_config"].get("droplet_pubkey", ""): ch_name
        for ch_name, ch in broker_channels.items()
        if ch["wg_config"].get("droplet_pubkey", "")
    }
    known_ifaces = {info.get("interface", "")
                    for info in _active_tunnels.values()}
    known_ifaces.discard("")
    # [ORPHAN_DUP_BY_CHANNEL_V1] Map channel_name -> currently active iface.
    # Used below to identify duplicate kernel ifaces: ifaces whose pubkey
    # matches a broker channel but which are NOT the active iface for that
    # channel.  Such duplicates accumulate when find_next_wg_iface allocates
    # a fresh wgN for a channel without the previous one being torn down.
    # Every wgN ever brought up for the same channel shares the broker's
    # droplet_pubkey, so a pubkey match alone is not sufficient to keep an
    # iface - it must also be the one currently bound to that channel.
    active_iface_by_ch: Dict[str, str] = {
        ch_name: info.get("interface", "")
        for ch_name, info in _active_tunnels.items()
        if info.get("interface")
    }
    config.log.debug("BRINGUP_PHASE: orphan_scan kernel_ifaces=%s known_ifaces=%s",
                     sorted(kernel_ifaces.keys()), sorted(known_ifaces))
    orphan_count = 0
    for iface, pubkey in sorted(kernel_ifaces.items()):
        if iface in known_ifaces:
            config.log.debug("BRINGUP_PHASE: orphan_check iface=%s decision=keep reason=in_known_ifaces", iface)
            continue
        ch_name = broker_pubkey_to_name.get(pubkey, "")
        if not ch_name:
            config.log.info("BRINGUP_PHASE: orphan_teardown iface=%s reason=pubkey_not_in_broker_list", iface)
            _tear_down_orphan_iface(iface, pubkey,
                                    reason="pubkey_not_in_broker_list")
            changed = True
            orphan_count += 1
            continue
        # [ORPHAN_DUP_BY_CHANNEL_V1] pubkey matches broker channel ch_name.
        # If we have an active record for ch_name and iface isn't it, this
        # is a duplicate from a prior bringup cycle - tear it down.
        # If we have NO active record for ch_name (e.g. active/ was wiped
        # and this is the first bringup after), be conservative and keep -
        # tearing down a live iface we don't have a record for would risk
        # disrupting in-flight traffic.
        active_for_ch = active_iface_by_ch.get(ch_name, "")
        if active_for_ch and iface != active_for_ch:
            config.log.info("BRINGUP_PHASE: orphan_teardown iface=%s ch=%s active=%s reason=duplicate_iface_for_channel",
                            iface, ch_name, active_for_ch)
            _tear_down_orphan_iface(iface, pubkey,
                                    reason="duplicate_iface_for_channel")
            changed = True
            orphan_count += 1
        else:
            config.log.debug("BRINGUP_PHASE: orphan_check iface=%s decision=keep reason=matches_broker ch=%s active_for_ch=%s",
                             iface, ch_name, active_for_ch or "<none>")
    config.log.info("BRINGUP_PHASE: orphan_scan_done teardowns=%d", orphan_count)

    # Phase 2: local channels no longer in broker
    removed_by_broker = sorted(set(_active_tunnels.keys()) - broker_names)
    config.log.info("BRINGUP_PHASE: broker_removed_channels count=%d list=%s",
                    len(removed_by_broker), removed_by_broker)
    for ch_name in removed_by_broker:
        config.log.info("BRINGUP_PHASE: teardown_removed ch=%s", ch_name)
        _tear_down_tunnel(ch_name)
        changed = True

    # [STALE_HANDSHAKE_PURGE_V2 2026-05-25] Before deciding which
    # channels are already_up, prove they actually ARE up.  A channel
    # in _active_tunnels has only "we created this iface and at some
    # point got a handshake" status - it doesn't mean the tunnel still
    # works.  The peer may have rotated keys, restarted, changed
    # endpoint, or just gone offline.  In all those cases the iface is
    # still up locally with stale crypto and every probe through it
    # times out.
    #
    # V1 unconditionally `wg-quick down`ed any stale iface, which made
    # the kernel drop every `dev wgN` route in one syscall.  On
    # forwarder nodes (NY-1, SeattleSix) the wg ifaces carry 4-9
    # per-peer scope-link routes each; tearing one down at the start
    # of every merge cycle emptied the route table mid-merge and
    # frognet_monitor went blank until commit-only ran at the END of
    # mergeHostsAndResolv.bash.
    #
    # V2 attempts an in-place handshake refresh first
    # (wg.refresh_handshake): toggle PersistentKeepalive to force the
    # kernel to initiate a new handshake on the EXISTING iface.  No
    # iface goes down, no routes are removed, no monitor blip.  Only
    # if the refresh fails to produce a fresh handshake within
    # REFRESH_HANDSHAKE_WAIT_SEC do we fall back to the V1 destructive
    # teardown - and that fallback is what's logged as "purged".
    #
    # Per-tunnel reason is appended to one of three lists:
    #   refreshed_in_place - refresh succeeded; no route disturbance
    #   purged             - fell back to destructive teardown
    #   skipped_unhealthy  - no iface recorded or iface vanished;
    #                        teardown was the only option
    #
    # All three are logged in the final summary so the operator can
    # see exactly which tunnels were touched and why.
    stale_purged: List[Tuple[str, str, str]] = []   # (ch, iface, reason)
    refreshed_in_place: List[Tuple[str, str, int]] = []  # (ch, iface, new_age_sec)
    skipped_unhealthy: List[Tuple[str, str]] = []   # (ch, reason)
    grace_held: List[Tuple[str, str, int]] = []     # (ch, iface, fail_cycle)

    # [HANDSHAKE_GRACE_V3] load the persisted consecutive-failed-refresh
    # counter so a stale tunnel gets config.HANDSHAKE_GRACE_CYCLES cycles to
    # self-restore before teardown.  Decision is factored into
    # _tunnel_health_verdict so the simulator can drive the real logic.
    from . import wg as wg_mod
    grace_counts = _load_grace_counts()
    seen_channels: Set[str] = set()

    for ch_name in sorted(_active_tunnels.keys()):
        seen_channels.add(ch_name)
        info = _active_tunnels.get(ch_name) or {}
        iface = info.get("interface", "")
        action = _tunnel_health_verdict(
            ch_name, iface,
            age_fn=wg_handshake_age,
            refresh_fn=wg_mod.refresh_handshake,
            grace_counts=grace_counts,
            grace_cycles=config.HANDSHAKE_GRACE_CYCLES,
            dead_sec=config.HANDSHAKE_DEAD_SEC,
            refresh_wait_sec=config.REFRESH_HANDSHAKE_WAIT_SEC,
            log=config.log)

        if action == "keep_live":
            continue
        if action == "refreshed":
            new_age = wg_handshake_age(iface)
            refreshed_in_place.append((ch_name, iface, int(new_age or 0)))
            continue
        if action == "grace_hold":
            grace_held.append((ch_name, iface, int(grace_counts.get(ch_name, 0))))
            continue
        if action == "teardown_no_iface":
            skipped_unhealthy.append((ch_name, "no_iface_recorded"))
            _tear_down_tunnel(ch_name)
            changed = True
            continue
        if action == "teardown_dead":
            stale_purged.append((ch_name, iface, "refresh_failed_handshake_dead"))
            _tear_down_tunnel(ch_name)
            changed = True
            continue

    # Drop grace counters for channels no longer in _active_tunnels (the
    # broker removed them, or they were torn down above) so the file does not
    # accumulate dead keys.
    for stale_key in [k for k in grace_counts if k not in seen_channels]:
        grace_counts.pop(stale_key, None)
    _save_grace_counts(grace_counts)

    # Summary: one log line covering all three outcomes for this pass.
    # Operators reading post-mortem journalctl can grep
    # PHASE3_TUNNEL_TEARDOWN_SUMMARY to see exactly what was touched
    # and why.
    if refreshed_in_place or stale_purged or skipped_unhealthy or grace_held:
        config.log.info(
            "PHASE3_TUNNEL_TEARDOWN_SUMMARY: refreshed=%d purged=%d "
            "skipped_unhealthy=%d grace_held=%d  refreshed_list=%s  "
            "purged_list=%s  skipped_list=%s  grace_list=%s",
            len(refreshed_in_place), len(stale_purged),
            len(skipped_unhealthy), len(grace_held),
            [{"ch": c, "iface": i, "new_age_s": a}
             for c, i, a in refreshed_in_place],
            [{"ch": c, "iface": i, "reason": r}
             for c, i, r in stale_purged],
            [{"ch": c, "reason": r} for c, r in skipped_unhealthy],
            [{"ch": c, "iface": i, "fail_cycle": n}
             for c, i, n in grace_held],
        )

    # Phase 3: bring up missing channels in parallel
    current_local: Set[str] = set(_active_tunnels.keys())
    to_bring_up_raw = sorted(broker_names - current_local)

    # Filter: skip channels whose remote /24s are already reachable via
    # non-WG routes.  Primer rule: WG tunnels exist only when the
    # remote /24 has no other path.  See _channel_already_lan_reachable.
    to_bring_up: List[str] = []
    skipped_lan_reachable: List[str] = []
    for ch_name in to_bring_up_raw:
        ch = broker_channels.get(ch_name, {})
        rs = (ch.get("wg_config", {}) or {}).get("remote_subnets", []) or []
        if _channel_already_lan_reachable(rs):
            skipped_lan_reachable.append(ch_name)
            config.log.info(
                "BRINGUP_PHASE: skip ch=%s reason=remote_subnets_lan_reachable "
                "subnets=%s", ch_name, rs)
            continue
        to_bring_up.append(ch_name)

    config.log.info(
        "BRINGUP_PHASE: to_bring_up count=%d list=%s already_up=%s "
        "skipped_lan_reachable=%d",
        len(to_bring_up), to_bring_up, sorted(current_local),
        len(skipped_lan_reachable))
    if to_bring_up:
        t_bu0 = time.monotonic()
        done_sem = threading.Semaphore(0)
        for ch_name in to_bring_up:
            def _worker(name: str):
                try:
                    _bring_up_tunnel(broker_channels[name])
                except Exception as e:
                    config.log.exception("BRING_UP %s unhandled: %s", name, e)
                finally:
                    done_sem.release()
            threading.Thread(target=_worker, args=(ch_name,),
                             name=f"bring-up-{ch_name}", daemon=True).start()
        for _ in to_bring_up:
            done_sem.acquire()
        bu_ms = int((time.monotonic() - t_bu0) * 1000)
        config.log.info("BRINGUP_PHASE: bring_up_done count=%d parallel_ms=%d",
                        len(to_bring_up), bu_ms)
        changed = True

        # [HOSTS_SINGLE_WRITER_V1] BRINGUP_HOSTS_REGISTER removed. The
        # route-gated merge (discovery.live -> orchestrate.merge, gated by
        # HOSTS_NEED_ROUTE_V1) is the SOLE author of
        # /etc/sentinels/frognet_hosts. Registering every brought-up channel
        # here wrote peers with no installed route (dead/unreachable tunnels -
        # e.g. BABox over a dead wg0, New-York-2 behind an unverified relay)
        # straight into the hosts file, bypassing the route gate and re-bloating
        # it every cycle; that stale full list (and the dead /24 it implied)
        # kept the merge reaping a non-winner /24 and never converging. The
        # merge already takes bringup peers (broker handshakes) and gates them
        # against the kernel table, so this write was redundant and wrong.

    # [BRINGUP_ROUTE_REPAIR_V1] For every channel that was already up at
    # the start of this phase (so _bring_up_tunnel did NOT run for it),
    # call _bringup_install_routes anyway.  Reason: a kernel route table
    # flush (operator action, network restart, anything that wipes /24
    # routes) leaves the wg iface itself live in `_active_tunnels` but
    # strips the /24 routes that point at it.  Without this pass the
    # daemon believes the channel is fine and the route never comes back
    # until the channel is torn down and rebuilt - which the daemon has
    # no reason to do, because the iface is still up.  _bringup_install_routes
    # is idempotent (BRINGUP_ROUTE_SKIP for existing routes) so this is a
    # no-op on healthy cycles and a repair on broken ones.
    for ch_name in sorted(current_local):
        info = _active_tunnels.get(ch_name)
        if info is None:
            continue
        iface = info.get("interface", "")
        if not iface:
            continue
        ch = broker_channels.get(ch_name, {})
        rs = (ch.get("wg_config", {}) or {}).get("remote_subnets", []) or []
        if not rs:
            continue
        try:
            _bringup_install_routes(ch_name, iface, rs)
        except Exception as e:
            config.log.exception(
                "BRINGUP_ROUTE_REPAIR: ch=%s iface=%s err=%s",
                ch_name, iface, e)

    _signal_bringup_ready()
    dur_ms = int((time.monotonic() - t0) * 1000)
    config.log.info("BRINGUP_PHASE: status=exit dur_ms=%d active_tunnels=%d changed=%s",
                    dur_ms, len(_active_tunnels), changed)
    return changed


HANDSHAKE_RTTS_PATH = "/var/lib/frognet-tunnel/handshake_rtts.json"


def _write_handshake_rtts() -> None:
    """Atomically write {channel_name: {iface, subnet, peer_dot_one,
    handshake_ms}} for every currently-active tunnel."""
    import json
    import os
    import tempfile

    data = {}
    skipped = []
    for ch_name, info in _active_tunnels.items():
        iface = info.get("interface", "")
        subnets = info.get("remote_subnets", [])
        if not iface or not subnets:
            skipped.append((ch_name, f"iface={iface} subnets={subnets}"))
            continue
        subnet = subnets[0]
        base = subnet.split("/", 1)[0]
        octets = base.split(".")
        if len(octets) != 4:
            skipped.append((ch_name, f"bad_subnet={subnet}"))
            continue
        peer_dot_one = ".".join(octets[:3]) + ".1"
        data[ch_name] = {
            "iface": iface,
            "subnet": subnet,
            "peer_dot_one": peer_dot_one,
            "handshake_ms": info.get("handshake_wall_ms", 0),
        }

    config.log.debug("HANDSHAKE_RTTS_WRITE: channels=%d skipped=%d path=%s",
                     len(data), len(skipped), HANDSHAKE_RTTS_PATH)
    if skipped:
        for ch, reason in skipped:
            config.log.warning("HANDSHAKE_RTTS_WRITE: skip ch=%s reason=%s", ch, reason)

    dir_ = os.path.dirname(HANDSHAKE_RTTS_PATH)
    try:
        os.makedirs(dir_, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".handshake_rtts.", dir=dir_)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, HANDSHAKE_RTTS_PATH)
            config.log.info("HANDSHAKE_RTTS_WRITE: ok path=%s channels=%d", HANDSHAKE_RTTS_PATH, len(data))
        except Exception as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            config.log.error("HANDSHAKE_RTTS_WRITE: FAILED path=%s err=%s", HANDSHAKE_RTTS_PATH, e)
            raise
    except OSError as e:
        config.log.warning("HANDSHAKE_RTTS_WRITE: OSError path=%s err=%s", HANDSHAKE_RTTS_PATH, e)


def _signal_bringup_ready() -> None:
    """Write the handshake-RTTs file, then touch the READY sentinel."""
    _write_handshake_rtts()
    try:
        import pathlib
        p = pathlib.Path("/etc/sentinels/tunnel_bringup_ready")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        config.log.info("BRINGUP_READY: sentinel_touched path=%s", p)
    except OSError as e:
        config.log.warning("BRINGUP_READY: sentinel_write_FAILED err=%s", e)


def _reconcile_commit_phase() -> bool:
    """Phase B: writes channel_map, runs committer, reconciles state."""
    t0 = time.monotonic()
    config.log.info("COMMIT_PHASE: status=enter active_tunnels=%d",
                    len(_active_tunnels))
    changed = False

    t_cm0 = time.monotonic()
    _write_channel_map()
    config.log.debug("COMMIT_PHASE: channel_map_write dur_ms=%d",
                     int((time.monotonic() - t_cm0) * 1000))

    t_c0 = time.monotonic()
    committer_ok = _run_committer()
    c_ms = int((time.monotonic() - t_c0) * 1000)
    if committer_ok:
        changed = True
        config.log.info("COMMIT_PHASE: committer_done rc=ok dur_ms=%d", c_ms)
    else:
        config.log.error("COMMIT_PHASE: committer_FAILED dur_ms=%d", c_ms)

    t_r0 = time.monotonic()
    _reconcile_local_state_with_kernel()
    config.log.debug("COMMIT_PHASE: state_reconcile dur_ms=%d",
                     int((time.monotonic() - t_r0) * 1000))

    dur_ms = int((time.monotonic() - t0) * 1000)
    config.log.info("COMMIT_PHASE: status=exit dur_ms=%d active_tunnels=%d changed=%s",
                    dur_ms, len(_active_tunnels), changed)
    return changed


def _reconcile_tunnels_inner() -> bool:
    """Back-compat single-shot: runs bring-up then commit in one
    flock-protected call.  Preserved for the daemon's poll loop and
    for anything still invoking `reconcile` as a single verb.
    mergeHostsAndResolv now calls bring-up-only and commit-only
    separately with discovery in between."""
    changed = _reconcile_bringup_phase()
    if _reconcile_commit_phase():
        changed = True
    return changed


def reconcile_tunnels() -> bool:
    """Public entry point; held by flock so concurrent invocations
    serialize."""
    import fcntl
    try:
        lock_f = open(_RECONCILE_LOCK_PATH, "w")
    except OSError as e:
        config.log.error("RECONCILE: cannot open lock: %s", e)
        return False
    try:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            config.log.info("RECONCILE: another reconcile is running - skip")
            return False
        try:
            return _reconcile_tunnels_inner()
        except Exception as e:
            config.log.exception("RECONCILE: unhandled: %s", e)
            return False
    finally:
        try:
            lock_f.close()
        except Exception:
            pass


def reconcile_bringup_only() -> bool:
    """Public entry point for bring-up-only phase.  Flock-protected.
    Called by mergeHostsAndResolv at the start of the merge cycle,
    concurrent with sync_interfaces's wave 1."""
    import fcntl
    try:
        lock_f = open(_RECONCILE_LOCK_PATH, "w")
    except OSError as e:
        config.log.error("BRINGUP: cannot open lock: %s", e)
        return False
    try:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            config.log.info("BRINGUP: another reconcile is running - skip")
            # Still signal ready so sync_interfaces doesn't block on us;
            # the other reconcile will do the bring-up.
            _signal_bringup_ready()
            return False
        try:
            return _reconcile_bringup_phase()
        except Exception as e:
            config.log.exception("BRINGUP: unhandled: %s", e)
            _signal_bringup_ready()  # let sync_interfaces unblock
            return False
    finally:
        try:
            lock_f.close()
        except Exception:
            pass


def reconcile_commit_only() -> bool:
    """Public entry point for commit-only phase.  Flock-protected.
    Called by mergeHostsAndResolv at the end of the merge cycle
    after sync_interfaces has emitted all observations."""
    import fcntl
    try:
        lock_f = open(_RECONCILE_LOCK_PATH, "w")
    except OSError as e:
        config.log.error("COMMIT: cannot open lock: %s", e)
        return False
    try:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            config.log.info("COMMIT: another reconcile is running - skip")
            return False
        try:
            return _reconcile_commit_phase()
        except Exception as e:
            config.log.exception("COMMIT: unhandled: %s", e)
            return False
    finally:
        try:
            lock_f.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop (unchanged)
# ---------------------------------------------------------------------------

def run_poll_loop():
    _ensure_frognet0()
    # [BROKER_AUTHORITATIVE_V1 2026-06-01] No _load_local_state() and
    # no STARTUP_COMMIT special case.  The daemon enters its poll loop
    # with empty _active_tunnels; the first poll_once() fetches the
    # broker and reconciles kernel ifaces accordingly.  If the broker
    # is reachable, kernel ifaces matching its response are adopted
    # (via _rebuild_active_tunnels_from_kernel_and_broker inside the
    # bringup phase); ifaces not in the response are torn down.  If
    # the broker is unreachable at startup, all wg ifaces are torn
    # down - this node has no authority to host tunnels without a
    # broker confirmation.
    config.log.info("Starting poll loop (interval=%ds)",
                    config.POLL_INTERVAL_SEC)

    # [TRANSIT_SUBNETS_SYNC_V1] push state once up-front so any change
    # that accumulated while the daemon was down is propagated without
    # waiting a full POLL_INTERVAL_SEC.
    _sync_transit_subnets()
    while config.running:
        changed = False
        try:
            changed = poll_once()
        except Exception as e:
            config.log.exception("POLL: unhandled: %s", e)
        if changed:
            config.log.info("POLL: changes - forking runMerge")
            try:
                subprocess.Popen(
                    ["/usr/local/bin/runMerge.bash"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                    start_new_session=True,
                )
            except Exception as e:
                config.log.error("POLL: fork failed: %s", e)
        for _ in range(config.POLL_INTERVAL_SEC):
            if not config.running:
                return
            time.sleep(1)


def shutdown_all():
    """Graceful exit: leave tunnels alone.  bulk_teardown = nuke_tunnels."""
    config.log.info("SHUTDOWN: leaving %d tunnels in place",
                    len(_active_tunnels))
