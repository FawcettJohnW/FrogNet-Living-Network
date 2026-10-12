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
poll.py - the tunnel daemon's loops and the bring-up reconcile [NO_BROKER_V1].

Two modes (config.MODE):

  region    run_region_loop(): one held FD&F region session (fdf-events); per region event the hub channels
            (region.my_channels) are reconciled in-process by _reconcile_bringup_phase -- orphan wgN torn down,
            channels whose hub is gone torn down, missing ones brought up in parallel -- then FD&F's plan is applied
            (graph_apply), the plan's mappings published, elections run. The daemon owns its WireGuard key
            (config.ensure_node_key) [DAEMON_OWNS_ITS_KEY_V1].
  LAN-only  run_lan_only_loop(): frognet0 and nothing else; FrogNet discovery and the elections are runMerge's.
            The merge's `bring-up-only` stage (discovery.live) comes here and, with no region, tears down any wg
            iface, writes an empty handshake_rtts.json and touches /etc/sentinels/tunnel_bringup_ready
            [LAN_ONLY_NO_REGION_V1].

Route installation for a hub channel's remote /24s at bring-up is _bringup_install_routes; everything else about
routes is the region applier's (graph_apply) or the merge's.
"""

import json
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from . import config
from .wg import (find_next_wg_iface, teardown_wg_iface,
                 wg_handshake_age)


# ---------------------------------------------------------------------------
# frognet0 - the node's virtual interface
# ---------------------------------------------------------------------------

FROGNET_IFACE = "frognet0"



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

    Skips the host's own served /24 if a channel lists it as remote
    (seen 2026-05-15: Seattle-1I claiming 10.250.250.0/24 as a
    remote_subnet). Skipping early avoids the
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
                "(the channel listed it as a remote_subnet - bug)",
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
        # bringup - they are unverified hints from the channel's
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
        # [REGION_HUB_ROUTES_V1] region mode: the channel is "hub-<name>", which has no "<peer>-<prefix>" to parse, so
        # peer_prefix is empty and every /24 the hub serves would be skipped here as "relayed", forever (the committer
        # needs a tunnel observation it never gets without a route). The hub's remote_subnets are verified mappings
        # (signed with the site key), not hints: install each as `dest dev wgN` and say so.
        # region mode: /24s through the hub are installed by the graph applier ([GRAPH_APPLY_V1]), not here
        region_hub = False
        if not (peer_prefix and _peer_one_inside_dest(peer_prefix, sn)):
            config.log.info(
                "BRINGUP_ROUTE_SKIP %s: relayed subnet "
                "(deferred to post-probe commit)", sn)
            continue

        # [TUNNEL_DEV_ROUTE_V1] Always `dest dev wgN`, no via, no
        # onlink.  The channel sets AllowedIPs=10.0.0.0/8, so
        # wg accepts any 10.x destination on the iface; the peer's
        # kernel routes the inner packet onward.  Same shape for
        # peer's own /24 and for relayed /24s.  See planner
        # _winning_via for the matching change on the committer side.
        via = ""
        onlink = False
        shape = "region-hub" if region_hub else "direct"

        ok = ipr.install(InstallRoute(
            dest=sn, dev=iface, via=via,
            onlink=onlink, kind="tunnel-bringup",
            rtt_ms=int(info.get("handshake_wall_ms", 0)),
            src="",
        ))
        if ok:
            installed.append(sn)
            (config.log.warning if region_hub else config.log.info)(
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



# ---------------------------------------------------------------------------
# Subnet helper
# ---------------------------------------------------------------------------

def _probe_ip_for_subnet(subnet: str) -> str:
    """'10.102.60.0/24' -> '10.102.60.2'.  Probe alias, not the gateway."""
    if "/" not in subnet:
        return ""
    parts = subnet.split("/", 1)[0].split(".")
    if len(parts) != 4:
        return ""
    return f"{parts[0]}.{parts[1]}.{parts[2]}.2"


# ---------------------------------------------------------------------------
# Local tunnel state
# ---------------------------------------------------------------------------

_active_tunnels: Dict[str, dict] = {}


def _tear_down_all_kernel_wg_ifaces(reason: str) -> int:
    """Tear down every wg iface in the kernel. Used when this node has no
    authority to hold any tunnel: LAN-only (no region), or the region
    session is gone ([GONE_IS_GONE_V1]). Returns count of ifaces torn down.
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
    """[TRANSIENT_GUARD_V1] Teardown for a *transient* bring-up failure: the
    hub channels could not be read this event (region.my_channels raised).

    That is evidence about OUR control plane this instant - one read -
    NOT proof that any peer became unreachable.  On Seattle5 (2026-06-05 21:34) a
    one-shot failure of one of these tore down wg0/wg1/wg2, three
    tunnels that had handshaked and carried data for 3h, cutting NY-1
    and 10.179.179 until a later merge rebuilt them.

    A tunnel whose last handshake is younger than HANDSHAKE_LIVE_SEC is
    live on the wire regardless of the failing check, so we DO NOT tear
    it down here; the blip is re-evaluated next pass.  Genuinely stale /
    never-handshook ifaces (which were going to fail anyway) are still
    cleaned.  Full unconditional teardown is reserved for LAN-only, the
    region gone, and a channel positively absent from the hub list
    (orphan / hub gone) - i.e. authority, not failure-to-reach.
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


def _rebuild_active_tunnels_from_kernel_and_hubs(
        hub_channels: Dict[str, dict]) -> None:
    """Populate _active_tunnels from kernel wg ifaces whose peer pubkey is
    a hub's (region.my_channels: one channel per live hub). The source of
    truth is (kernel, region), never a disk cache.

    Each kernel iface whose peer pubkey matches a hub channel becomes an
    entry in _active_tunnels, bound to that channel.  Ifaces that match
    no hub are NOT added - the orphan scan that runs next tears them down.
    """
    _active_tunnels.clear()
    if not hub_channels:
        return
    kernel_ifaces = _enumerate_kernel_wg_ifaces()  # {iface: pubkey}
    pubkey_to_ch: Dict[str, str] = {
        ch["wg_config"].get("droplet_pubkey", ""): ch_name
        for ch_name, ch in hub_channels.items()
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
        ch = hub_channels[ch_name]
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
        "REBUILD_ACTIVE_TUNNELS: adopted=%d from kernel matching a hub",
        adopted)


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
    from .wg import _kernel_dump                              # [KERNEL_READ_ONCE_PER_EVENT_V1]
    d = _kernel_dump()
    if d is not None:
        for iface in sorted(d):
            if not iface.startswith("wg"):
                continue
            peers = [pk for pk, _hs in d[iface]]
            if len(peers) == 1:
                out[iface] = peers[0]
            else:
                config.log.warning("ORPHAN_SCAN: %s has %d peers - not a FrogNet "
                                   "tunnel, skipping", iface, len(peers))
        return out
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

    Does NOT probe the far edge.  The only routes it installs are the
    channel's own (_bringup_install_routes, at the end); every other
    route is the region applier's (graph_apply)."""
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

        from .wg import kernel_changed                        # [KERNEL_READ_ONCE_PER_EVENT_V1]
        kernel_changed()
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
        time.sleep(0.1)                 # [HANDSHAKE_POLL_100MS_V1] the handshake is one RTT; 500 ms steps added up to 500 ms
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
    the hub list is authoritative.  Routes through this iface are dropped by the
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
# Reconcile helpers
# ---------------------------------------------------------------------------

_RECONCILE_LOCK_PATH = "/var/lock/frognet-tunnel-reconcile.lock"


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
    """Reconcile this node's WireGuard links with the region's hub channels (region.my_channels: one per live hub that
    holds this node's key): tear down orphan wgN, tear down channels whose hub is gone, bring up missing ones in
    parallel, repair their /24 routes.

    LAN-only (no region): there are no channels; every wg iface comes down [LAN_ONLY_NO_REGION_V1].

    Writes handshake_rtts.json and touches the BRINGUP_READY sentinel in every case.
    """
    t0 = time.monotonic()
    (config.log.warning if getattr(config, "REGION_RAM", "") else config.log.info)("BRINGUP_PHASE: status=enter [GRAPH_APPLY_V1: region routes come from the applier]")
    changed = False

    # [LAN_ONLY_NO_REGION_V1] no region: no authority to hold any tunnel, so every kernel wg iface comes down, an empty
    # handshake_rtts.json is written and the sentinel touched (what the merge's bring-up stage waits for).
    if not getattr(config, "REGION_RAM", ""):
        config.log.warning("BRINGUP_PHASE: LAN-only (no region): no WireGuard; tearing down any wg iface [LAN_ONLY_NO_REGION_V1]")
        n = _tear_down_all_kernel_wg_ifaces(reason="lan_only")
        _signal_bringup_ready()
        return n > 0

    # [DAEMON_OWNS_ITS_KEY_V1] the key is the daemon's to make (run_region_loop, before its first bring-up). A process
    # that finds none here says so and brings nothing up; the daemon's next event has it.
    if not config.PRIVKEY:
        config.log.error("BRINGUP_PHASE: no WireGuard key at %s yet (the tunnel daemon makes it); nothing brought up "
                         "[DAEMON_OWNS_ITS_KEY_V1]", config.PRIVKEY_FILE)
        _signal_bringup_ready()
        return False

    # Phase 1: the hub channels. [WORLD_READ_ONCE_PER_EVENT_V1] inside the daemon's event this answers from the
    # event's one `world` read.
    from . import region
    try:
        t_fetch0 = time.monotonic()
        resp = region.my_channels()
        fetch_ms = int((time.monotonic() - t_fetch0) * 1000)
    except Exception as e:
        config.log.warning(
            "BRINGUP_PHASE: hub channels unreadable err=%s - "
            "tearing down stale only; the next event reads them again", e)
        n = _tear_down_stale_only(reason="hub_channels_unreadable")
        _signal_bringup_ready()
        return n > 0
    channels = resp.get("channels", [])
    hub_channels: Dict[str, dict] = {
        ch["channel_name"]: ch for ch in channels
    }
    hub_names: Set[str] = set(hub_channels.keys())
    config.log.info("BRINGUP_PHASE: channels_fetched count=%d fetch_ms=%d names=%s",
                    len(hub_names), fetch_ms, sorted(hub_names))

    # Rebuild _active_tunnels from kernel state, scoped to ifaces whose peer is
    # one of the hubs' current pubkeys: source of truth is (kernel, region),
    # not an on-disk cache.  After this call, _active_tunnels reflects exactly
    # which kernel ifaces are currently bound to hub channels; downstream
    # orphan/duplicate logic operates on that ground truth.
    _rebuild_active_tunnels_from_kernel_and_hubs(hub_channels)

    # Orphan teardowns
    kernel_ifaces = _enumerate_kernel_wg_ifaces()
    hub_pubkey_to_name: Dict[str, str] = {
        ch["wg_config"].get("droplet_pubkey", ""): ch_name
        for ch_name, ch in hub_channels.items()
        if ch["wg_config"].get("droplet_pubkey", "")
    }
    known_ifaces = {info.get("interface", "")
                    for info in _active_tunnels.values()}
    known_ifaces.discard("")
    # [ORPHAN_DUP_BY_CHANNEL_V1] Map channel_name -> currently active iface.
    # Used below to identify duplicate kernel ifaces: ifaces whose pubkey
    # matches a hub channel but which are NOT the active iface for that
    # channel.  Such duplicates accumulate when find_next_wg_iface allocates
    # a fresh wgN for a channel without the previous one being torn down.
    # Every wgN ever brought up for the same channel shares the hub's
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
        ch_name = hub_pubkey_to_name.get(pubkey, "")
        if not ch_name:
            config.log.info("BRINGUP_PHASE: orphan_teardown iface=%s reason=pubkey_not_a_hub", iface)
            _tear_down_orphan_iface(iface, pubkey,
                                    reason="pubkey_not_a_hub")
            changed = True
            orphan_count += 1
            continue
        # [ORPHAN_DUP_BY_CHANNEL_V1] pubkey matches hub channel ch_name.
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
            config.log.debug("BRINGUP_PHASE: orphan_check iface=%s decision=keep reason=matches_hub ch=%s active_for_ch=%s",
                             iface, ch_name, active_for_ch or "<none>")
    config.log.info("BRINGUP_PHASE: orphan_scan_done teardowns=%d", orphan_count)

    # Phase 2: local channels whose hub is gone
    gone_hubs = sorted(set(_active_tunnels.keys()) - hub_names)
    config.log.info("BRINGUP_PHASE: hub_gone_channels count=%d list=%s",
                    len(gone_hubs), gone_hubs)
    for ch_name in gone_hubs:
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
    # hub is gone, or they were torn down above) so the file does not
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
    to_bring_up_raw = sorted(hub_names - current_local)

    # Filter: skip channels whose remote /24s are already reachable via
    # non-WG routes.  Primer rule: WG tunnels exist only when the
    # remote /24 has no other path.  See _channel_already_lan_reachable.
    to_bring_up: List[str] = []
    skipped_lan_reachable: List[str] = []
    for ch_name in to_bring_up_raw:
        ch = hub_channels.get(ch_name, {})
        rs = (ch.get("wg_config", {}) or {}).get("remote_subnets", []) or []
        if _channel_already_lan_reachable(rs):
            skipped_lan_reachable.append(ch_name)
            config.log.info(
                "BRINGUP_PHASE: skip ch=%s reason=remote_subnets_lan_reachable "
                "subnets=%s", ch_name, rs)
            continue
        to_bring_up.append(ch_name)

    (config.log.warning if getattr(config, "REGION_RAM", "") else config.log.info)(
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
                    _bring_up_tunnel(hub_channels[name])
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
        # merge already takes bringup peers (hub handshakes) and gates them
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
        ch = hub_channels.get(ch_name, {})
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


def _publish_region_maps() -> None:
    """[REGION_V1] map-register our prefixes with the locator set of every hub whose tunnel is up (prio by handshake
    RTT). Goes through the facts file; fdf-client signs and registers through its own session ([MAP_FROM_FACTS_V1])."""
    if not getattr(config, "REGION_RAM", ""):
        return
    from . import region
    live = {}
    for ch_name, info in _active_tunnels.items():
        if ch_name.startswith("hub-"):
            live[ch_name[4:]] = info.get("handshake_wall_ms") or None
    config.live_hubs = live
    plan_maps = getattr(config, "region_plan_maps", None)
    if plan_maps is None:
        # [NO_PLAN_NO_WITHDRAW_V1] this process never ran the graph (runMerge's bring-up-only reconcile): it has no
        # opinion on what to register and must not write [] over the daemon's registration (seen on Seattle3B 15:06:
        # "REGION: maps -> []" from the merge, withdrawing the databasehost's own /24 until the daemon's next event).
        config.log.warning("REGION: maps not computed in this process; registration left as the daemon set it")
        return
    try:                                   # [MAPS_FROM_GRAPH_V1] the prefixes are the plan's, kept by the region loop
        region.publish_plan_maps(plan_maps)
    except Exception as e:
        config.log.warning("REGION: publish_plan_maps failed: %s", e)


def _signal_bringup_ready() -> None:
    """Write the handshake-RTTs file, then touch the READY sentinel."""
    _write_handshake_rtts()
    _publish_region_maps()                                    # [REGION_V1]
    try:
        import pathlib
        p = pathlib.Path("/etc/sentinels/tunnel_bringup_ready")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        config.log.info("BRINGUP_READY: sentinel_touched path=%s", p)
    except OSError as e:
        config.log.warning("BRINGUP_READY: sentinel_write_FAILED err=%s", e)



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

# ---------------------------------------------------------------------------
# Main loops
# ---------------------------------------------------------------------------

def _write_view(phase: str, reason: str) -> None:
    """[REGION_V1] (plan 2.6) the node's own view of the world, for applications holding a read on it: phase recalc the
    instant the region is lost (before runMerge), steady again when runMerge commits. /var/lib/frognet/view.json."""
    import json as _j
    path = os.environ.get("FROGNET_VIEW", "/var/lib/frognet/view.json")
    try:
        cur = {}
        try:
            with open(path) as f:
                cur = _j.load(f)
        except (OSError, ValueError):
            pass
        dbhost, lines = "", []
        try:
            import hashlib as _h
            with open("/etc/hosts") as hf:
                for ln in hf:
                    t = ln.split()
                    if len(t) >= 2 and t[0].startswith("10."):
                        lines.append(" ".join(t))
                        if "databasehost.frognet" in t[1:]:
                            dbhost = t[0]
            hosts_hash = _h.sha256("\n".join(sorted(lines)).encode()).hexdigest()[:16]
        except OSError:
            hosts_hash = ""
        cur.update({"phase": phase, "reason": reason, "gen": int(cur.get("gen", 0)) + (1 if phase == "steady" else 0),
                    "dbhost": dbhost, "hosts_hash": hosts_hash, "at": time.time()})
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(_j.dumps(cur, sort_keys=True) + "\n")
        os.replace(tmp, path)
        config.log.warning("VIEW: phase=%s reason=%s gen=%s", phase, reason, cur["gen"])
        if getattr(config, "REGION_RAM", "") and phase == "steady":      # and into the region, as this node's fact (fn.view)
            # [VIEW_FACT_ON_SUBSTANCE_V1] only when the substance (dbhost, hosts_hash) changed -- not gen/at/reason. A
            # facts write is an announcement, an announcement is a `p` event on every member, and every member's
            # rebuild wrote its view again: the fleet-wide 200 ms storm seen 13:41.
            from . import region
            have = (region.read_facts().get("view") or {})
            if (have.get("dbhost"), have.get("hosts_hash")) != (cur["dbhost"], cur["hosts_hash"]):
                region.write_facts({"view": {k: cur[k] for k in ("phase", "dbhost", "hosts_hash")}})
    except OSError as e:
        config.log.warning("VIEW: write failed: %s", e)


def _service_host_ips() -> set:
    """The .1 addresses /etc/hosts currently names as a service host (databasehost.frognet, ...)."""
    out = set()
    try:
        for ln in open("/etc/hosts"):
            f = ln.split()
            if len(f) >= 2 and f[0].startswith("10.") and any(t.endswith(".frognet") for t in f[1:]):
                out.add(f[0])
    except OSError:
        pass
    return out


def run_region_loop():
    """[REGION_V1][GRAPH_LOOP_V1] region mode: FD&F owns discovery (PLAN-RAM-DISCOVERY §5). Per region event (p/m,
    from the held fdf-events session; never a timer):
      1. the hub channel is reconciled in-process (brought up if missing, torn down if the hub left) -- the
         tunnel to the hub is this node's locator;
      2. the applier takes FD&F's plan (fdf_graph over `participants`), admits LAN candidates with one probe each,
         gates hub routes on the handshake, installs the diff, holds down mere absences, writes region_hosts;
      3. the mappings this node registers are the plan's (what the hub may forward to it);
      4. no runMerge for a region event [NO_MERGE_ON_REGION_EVENTS_V1]: the facts carry what the graph needs and this
         loop applies all of it; the generation counter (`gen` fact) is the audit trail [GEN_V1].
    When the region is gone: view.json phase=recalc, every region route and name withdrawn now (positive failure),
    tunnels torn down, no merge [GONE_IS_GONE_V1]; then wait for fdf-events to reconnect and adopt."""
    from . import region
    from . import gen as _gen
    from .graph_apply import Applier, World
    _ensure_frognet0()
    config.log.warning("REGION: loop started (RINA_RAM=%s, no poll interval) [ELECT_IN_PROCESS_V1 VIEW_FACT_ON_SUBSTANCE_V1 GEN_V1 NO_MERGE_ON_REGION_EVENTS_V1 WATCH_BEFORE_READ_V1 WORLD_READ_ONCE_PER_EVENT_V1 KERNEL_READ_ONCE_PER_EVENT_V1 HOSTJSON_FROM_THE_REGION_V1 ALIVE_IS_THE_SESSION_V1]; region.py build %s; the region is build %s",
                    config.REGION_RAM, region.BUILD_TAG, region.version())
    # [DAEMON_OWNS_ITS_KEY_V1] the key before the facts: the facts announce its public half, and the hub adds the peer
    # from them. A failure is logged; every event tries again (below, in _one) and re-announces once it has one.
    config.ensure_node_key()
    def _announce():
        try:
            region.register_node()
            config.log.warning("REGION: facts written (%s %s %s)", config.NODE_NAME, region.read_facts().get("subnet"), config.PUBKEY[:12])
        except Exception as e:
            config.log.error("REGION: could not write facts: %s", e)
    _announce()
    try:                                                       # FD&F's writer, once, so the first plan sees this node's rows
        subprocess.run([os.path.join(os.environ.get("FDF_BIN", "/opt/fdf/bin"), "fdf-facts")], timeout=20, capture_output=True)
    except Exception as e:
        config.log.warning("REGION: fdf-facts not run: %s", e)
    me = region.read_facts().get("name") or config.NODE_NAME
    applier = Applier(World(config=config, log=config.log), me)

    # [CAPABILITY_IS_A_FACT_V1] this node's capability (frognet_capability_probe.sh: cores, memory, disk, mysql, ffmpeg,
    # load) is a fact in the region: facts.capability, at start and every FROGNET_CAP_REFRESH_SEC (600). The elections on
    # every member read it from there. The probe runs off the loop (it is ~1 s on x86, longer on a Pi).
    def _capability_publisher():
        import threading as _t
        every = float(os.environ.get("FROGNET_CAP_REFRESH_SEC", "600"))
        probe = os.environ.get("FROGNET_CAPABILITY_PROBE", "/usr/local/bin/frognet_capability_probe.sh")
        while config.running:
            try:
                import json as _j
                out = subprocess.run([probe], capture_output=True, text=True, timeout=60)
                if out.returncode != 0 or not out.stdout.strip():
                    raise RuntimeError("rc=%s %s" % (out.returncode, (out.stderr or "").strip()[:160]))
                blob = _j.loads(out.stdout)
                cap = blob.get("capability") or blob
                have = region.read_facts().get("capability") or {}
                keep = {k: v for k, v in cap.items() if k not in ("loadavg", "temps_c", "mem_available_kb", "disk_free_gb", "ts")}
                was = {k: v for k, v in have.items() if k not in ("loadavg", "temps_c", "mem_available_kb", "disk_free_gb", "ts")}
                if keep != was or not have:                 # the slow-moving part changed (or first time): announce it
                    region.write_facts({"capability": cap, "capability_ts": time.time()})
                    config.log.warning("REGION: capability published as a fact (mysql=%s cores=%s mem_kb=%s disk=%s)",
                                       cap.get("mysql_running"), cap.get("cores"), cap.get("mem_total_kb"), cap.get("disk_class"))
                else:
                    config.log.info("REGION: capability unchanged (load/free space not announced)")
            except Exception as e:
                config.log.error("REGION: capability probe FAILED: %s (again in %ds)", e, int(every))
            for _ in range(int(every)):
                if not config.running:
                    return
                time.sleep(1)
    import threading as _thr0
    _thr0.Thread(target=_capability_publisher, name="capability", daemon=True).start()

    last_sig = [None]

    def _one(why: str, failures=frozenset()):
        if not config.PRIVKEY and config.ensure_node_key():           # [DAEMON_OWNS_ITS_KEY_V1] made late: announce it
            _announce()
        _t = {"t0": time.monotonic()}                               # [EVENT_TIMED_V1] every phase of the event, in the log
        def _lap(name):
            now = time.monotonic(); _t[name] = int((now - _t["t0"]) * 1000)
        parts, maps = ([], []) if "region" in failures else region.world()        # [WORLD_IN_ONE_READ_V1] one round trip
        _lap("participants")
        # [EVENT_SUBSTANCE_GATE_V1] an event that changed nothing we plan from (alive members' facts, the mappings) is
        # not acted on: a monitor polling the RAM manager opens a session per poll and that is a `p` event for every
        # member -- 4 rebuilds a second fleet-wide, seen 13:41 and 13:49. One read, one hash, done.
        import hashlib as _hl, json as _js
        _lap("mappings")
        sig = _hl.sha256(_js.dumps([[p.get("name"), p.get("facts")] for p in parts if p.get("alive")] + [sorted(m.get("eid", "") + "|" + m.get("rloc", "") for m in maps)],
                                   sort_keys=True).encode()).hexdigest()
        if sig == last_sig[0] and not failures and why != "start":
            config.log.info("REGION: %s changed nothing (same members, facts and mappings); ignored (world read %dms)", why, _t["participants"])
            return {}
        last_sig[0] = sig
        changed_tunnels = False
        from . import wg as _wgm
        # [WORLD_READ_ONCE_PER_EVENT_V1][KERNEL_READ_ONCE_PER_EVENT_V1] bringup and the applier see this event's world
        # (the one `world` read above) and one WireGuard dump, until something changes the kernel.
        with region.world_in_hand(parts, maps), _wgm.kernel_read_once():
            if "region" not in failures:
                try:
                    changed_tunnels = _reconcile_bringup_phase()    # hub channel up/down, in-process (no runMerge fork)
                except Exception as e:
                    config.log.warning("REGION: bringup after %s failed: %s", why, e)
            _lap("bringup")
            # [ME_BY_GUID_V1] the member name the region shows for this node is whatever fdf-client announced (FDF_NAME,
            # facts.name, or the hostname if it started before the daemon wrote facts.name); the guid is the identity.
            my_guid = region.read_facts().get("guid") or config.NODE_GUID
            for p in parts:
                if p.get("alive") and (p.get("facts") or {}).get("guid") == my_guid and p.get("name") and p["name"] != applier.me:
                    config.log.warning("REGION: this node is announced as %r (guid %s); planning as that", p["name"], my_guid)
                    applier.me = p["name"]
            c = applier.apply(parts, positive_failures=set(failures))
            _lap("apply")
        # [CHILD_KEEPS_OWN_MAP_V1] the plan's maps already say what to register: everything behind me when I am the exit,
        # my own /24s when I am a child with a tunnel, nothing without one. Not gated on hub_active any more.
        # [LIVE_HUBS_FROM_TUNNELS_V1] the locator set = every hub whose channel is up right now (the old bringup set
        # this at its end; the in-process reconcile must too, or the maps are signed toward nobody and never land)
        config.live_hubs = {ch[4:]: (info.get("handshake_wall_ms") or None) for ch, info in _active_tunnels.items() if ch.startswith("hub-")}
        config.region_plan_maps = c.get("maps", [])
        # [OWN_MAP_WHILE_TUNNEL_UP_V1] my own /24s are registered whenever a hub channel is up, even if fdf-facts has not
        # yet written my hub row (Seattle7 16:09:52: gen 3, seven seconds after restart, no hub row yet -> maps=[] ->
        # withdrew the /24 fdf-client had registered at :45; the region then had no mapping for 10.170.170.0/24).
        # The rows say what I carry for others; whether I carry myself is decided by the channel being up.
        if config.live_hubs and c.get("own"):
            missing = [n for n in c["own"] if n not in config.region_plan_maps]
            if missing:
                config.region_plan_maps = sorted(set(config.region_plan_maps) | set(missing))
                config.log.warning("REGION: own %s kept registered (hub channel up, hub row not in my facts yet)", missing)
        if c.get("maps_changed") or changed_tunnels or (config.region_plan_maps and not (region.read_facts().get("maps") or [])):
            region.publish_plan_maps(config.region_plan_maps)
        # [SERVICE_HOST_LOSS_IS_A_FAILURE_V1] a member going dead is held down for routes and names, but NOT for the
        # elections: if the member that went is the databasehost (/etc/hosts says which .1 that is), the
        # merge runs NOW -- lose comms with the databasehost -> switch to local, elect the replacement. A member coming
        # back also runs the merge, so the election can consider it again.
        # [ELECT_IN_PROCESS_V1] no runMerge for the election: the daemon has the only input that changed (who is
        # alive). A dead service host is elected around at once; a member coming back is re-considered at once; the
        # app sees the new databasehost in view.json within the second. runMerge still runs after an applied plan for
        # what it alone does (capability publish, the rest), off the critical path.
        service_ips = _service_host_ips()
        lost_services = [(h, ip) for h, ip in c.get("went", []) if ip in service_ips]
        # [ELECTION_FROM_FACTS_V1] a member's capability fact changing (it published, mysql came up, load moved) is an
        # election input changing: re-elect, deterministically, from the facts. No store, nothing to abstain on.
        cap_sig = _hl.sha256(_js.dumps(sorted((p.get("name", ""), (p.get("facts") or {}).get("capability")) for p in parts if p.get("alive")),
                                       sort_keys=True, default=str).encode()).hexdigest()
        cap_changed = cap_sig != _elect_state.get("cap_sig")
        _elect_state["cap_sig"] = cap_sig
        if lost_services or c.get("came") or c.get("hosts_written") or cap_changed:
            if lost_services:
                _write_view("recalc", "lost:" + ",".join(h for h, _ in lost_services))
                config.log.warning("REGION: service host(s) gone %s - electing now, in-process", lost_services)
            # [ELECTION_OFF_THE_LOOP_V1] the election reads the store (a dozen GETs, 20 s timeout each). With the
            # databasehost's store hung (Seattle3B, three times on 2026-10-09) that stalled THIS loop for minutes per
            # event -- no routes applied, no mappings published, on every node, worst on 3B itself. The election now runs
            # on its own thread, one at a time, always with the newest alive set; the loop goes on applying routes.
            _elect_async(why + (" (capability changed)" if cap_changed and not (lost_services or c.get("came") or c.get("hosts_written")) else ""), c.get("alive_hosts"), parts)
        if c.get("applied"):
            _write_view("steady", "graph:" + why)
        if changed_tunnels and not c.get("impact"):
            try:
                _gen.bump("hub_channel_changed", "daemon", log=config.log.warning)
            except Exception as e:
                config.log.warning("GEN: bump failed: %s", e)
        # [GEN_V1][NO_MERGE_ON_REGION_EVENTS_V1] no runMerge is forked for a region event any more: the facts carry
        # what the graph needs and this loop applied it (routes, default, hosts, names, elections, maps, hub channel).
        # runMerge is for the LAN side -- boot and interface events (NetworkManager dispatcher) -- where the inputs are
        # local. The generation counter is the audit trail: world_gen = sum of every alive member's `gen`, read from
        # the region (the shared database; nothing is sent over a tunnel), so the log says who changed what and that
        # everyone saw it.
        _lap("publish")
        _world_gen(parts, why)
        _lap("total")
        config.log.warning("REGION: %s timed: world=%dms (participants+mappings, one read) +%dms bringup=%dms apply=%dms (probes=%d/%dms routes=%d/%dms) publish=%dms total=%dms",
                           why, _t["participants"], _t["mappings"] - _t["participants"], _t["bringup"] - _t["mappings"], _t["apply"] - _t["bringup"],
                           c.get("probes", 0), c.get("probes_ms", 0), c.get("route_cmds", 0), c.get("route_cmds_ms", 0),
                           _t["publish"] - _t["apply"], _t["total"])
        return c

    _gen_state = {"last_world": None}

    def _world_gen(parts, why):
        try:
            w = _gen.world(parts)
            w["members"][applier.me] = _gen.read()            # mine is authoritative locally (the announce may lag an event)
            w["world"] = sum(w["members"].values())
            _gen.write_world(w)
            prev = _gen_state["last_world"]
            if prev is not None and prev["world"] != w["world"]:
                moved = ["%s %d->%d" % (n, prev["members"].get(n, 0), g) for n, g in sorted(w["members"].items()) if prev["members"].get(n, 0) != g]
                config.log.warning("REGION: world gen %d -> %d (%s) after %s", prev["world"], w["world"], ", ".join(moved), why)
            _gen_state["last_world"] = w
            config.log.info("REGION: gen mine=%d world=%d members=%s", w["members"].get(applier.me, 0), w["world"],
                            " ".join("%s=%d" % kv for kv in sorted(w["members"].items())))
        except Exception as e:
            config.log.warning("REGION: gen bookkeeping failed after %s: %s", why, e)

    import threading as _thr
    _elect_lock = _thr.Lock(); _elect_pending = {}                          # pending: {"why":..., "alive":...}, newest wins

    _elect_state = {"running": False}

    def _elect_run():
        while True:
            with _elect_lock:
                job = dict(_elect_pending); _elect_pending.clear()
                if not job:
                    _elect_state["running"] = False
                    return
            t0 = time.monotonic()
            try:
                import sys as _sys
                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # /opt/frognet_semantic
                if _root not in _sys.path:
                    _sys.path.insert(0, _root)
                from discovery.elect import elect_now
                r = elect_now(logger=lambda m: config.log.warning("REGION: %s", m), alive_hosts=job["alive"], participants=job["parts"])
                if (r or {}).get("hosts_changed"):
                    _gen.bump("service_host_changed", "elect", log=config.log.warning)      # [GEN_V1]
                _write_view("steady", "elect:" + job["why"])
                config.log.warning("REGION: election after %s done in %.1fs (off the loop)", job["why"], time.monotonic() - t0)
            except Exception as e:
                # [NO_FALLBACK_V1] no runMerge in its place: the failure is said, the next event re-runs the election.
                config.log.error("REGION: election after %s FAILED after %.1fs: %s (re-run on the next event)", job["why"], time.monotonic() - t0, e)

    def _elect_async(why, alive, parts):
        with _elect_lock:
            _elect_pending.clear(); _elect_pending.update({"why": why, "alive": alive, "parts": parts})
            if _elect_state["running"]:
                config.log.warning("REGION: election after %s queued behind the one running (newest alive set wins)", why)
                return
            _elect_state["running"] = True
            _thr.Thread(target=_elect_run, name="region-elect", daemon=True).start()

    # [FORWARD_SWITCH_IN_PLACE_V1] (PLAN-PONDS §3.2) `fdf-forward <area>` writes forward.want; this thread wakes on it
    # (inotify, nothing polls) and, for a different pond with a ponds/<area>.env, ends the held event session: the loop
    # below runs its loss path for the old pond and then makes the switch, with no wait (a switch is not a retry).
    switch = {"to": None}
    def _forward_watch():
        import ctypes, ctypes.util, select, struct
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ifd = libc.inotify_init1(0o2000000)
        if ifd < 0 or libc.inotify_add_watch(ifd, region.FDF_VAR.encode(), 0x8 | 0x80 | 0x100) < 0:   # CLOSE_WRITE MOVED_TO CREATE
            config.log.warning("REGION: forward watch could not start on %s (errno %d): fdf-forward will not switch this daemon", region.FDF_VAR, ctypes.get_errno())
            return
        while config.running:
            select.select([ifd], [], [])
            buf = os.read(ifd, 4096); off = 0; mine = False
            while off < len(buf):
                _wd, _mask, _ck, ln = struct.unpack_from("iIII", buf, off); name = buf[off + 16: off + 16 + ln].rstrip(b"\0")
                mine = mine or name == b"forward.want"; off += 16 + ln
            if not mine:
                continue
            want = region.read_word("forward.want")
            if not want or want == (region.read_word("forward") or config.REGION_AREA):
                continue
            env = region.pond_env(want)
            if not env.get("RINA_RAM"):
                config.log.warning("REGION: forward %s refused: no RINA_RAM in %s/ponds/%s.env (fdf-pond-add it first)", want, region.FDF_VAR, want)
                continue
            switch["to"] = (want, env)
            config.log.warning("REGION: forward %s -> %s requested; ending the session to %s", config.REGION_AREA, want, config.REGION_RAM)
            region.end_events()
    _thr.Thread(target=_forward_watch, name="region-forward", daemon=True).start()

    def _do_switch():
        area, env = switch["to"]; switch["to"] = None; old = config.REGION_AREA; t0 = time.monotonic()
        # the old pond's fdf-client is still forward: clearing the maps withdraws this node's mappings there, and the new
        # pond's fdf-client will find none when it turns forward (the daemon publishes the new pond's own, after its plan)
        region.write_facts({"maps": []})
        config.REGION_RAM = env["RINA_RAM"]; config.REGION_AREA = env.get("RINA_AREA", area)
        open(os.path.join(region.FDF_VAR, "join.env.tmp"), "w").write("export RINA_RAM=%s\n" % config.REGION_RAM)
        os.replace(os.path.join(region.FDF_VAR, "join.env.tmp"), os.path.join(region.FDF_VAR, "join.env"))
        se = os.path.join(region.FDF_VAR, "site.env")
        open(se + ".tmp", "w").write("RINA_AREA=%s\nRINA_SITE_KEY=%s\n" % (config.REGION_AREA, env.get("RINA_SITE_KEY", "")))
        os.chmod(se + ".tmp", 0o600); os.replace(se + ".tmp", se)
        region.write_word("forward", area)                         # every fdf-client@<area> follows this word
        config.log.warning("REGION: forward %s -> %s (RINA_RAM=%s): old pond withdrawn, maps cleared, `forward` written in %d ms; joining now",
                           old, area, config.REGION_RAM, int((time.monotonic() - t0) * 1000))

    was_up = False                      # [REGION_LOSS_IS_A_TRANSITION_V1] "lost" is said and acted on once per loss
    backoff = 5
    while config.running:
        up = False
        evs = None
        try:
            # [WATCH_BEFORE_READ_V1] the watch first, then the read. It was the other way round: `_one("start")` read the
            # world, then brought up the hub tunnel (5 s in the netns test), and only then did fdf-events register --
            # every join, announce and mapping in between was never delivered. Both daemons in
            # tools/netns-region-loop-test.sh sat 61 s with a/y missing from their plan, until an unrelated change.
            # Now a change after the watch is queued (fdf-events' pipe) while the read runs, and is applied next.
            evs = region.events()
            first = next(evs, "gone")
            if first != "ready":
                raise RuntimeError("fdf-events registered no watch (%s)" % first)
            with region.facts_batch():                              # [ONE_WRITE_PER_EVENT_V1]
                _one("start")
            up = True
        except Exception as e:
            if evs is not None:
                evs.close()
            config.log.warning("REGION: not reachable (%s); %s", e,
                               "will keep trying every %ds, quietly" % backoff if not was_up else "was up: treating as lost")
        if up:
            was_up = True
            backoff = 5
            for ev in evs:
                if not config.running:
                    return
                if ev == "gone":
                    break
                try:
                    with region.facts_batch():                      # [ONE_WRITE_PER_EVENT_V1]
                        _one("event " + ev)
                except Exception as e:
                    config.log.warning("REGION: apply after event %s failed: %s", ev, e)
        if was_up:                      # up -> down: withdraw, tear down, one merge from the LAN; then wait to come back
            was_up = False
            _write_view("recalc", "lost:region")
            try:
                applier.apply([], positive_failures={"region"})
            except Exception as e:
                config.log.warning("REGION: withdraw on loss failed: %s", e)
            n = _tear_down_all_kernel_wg_ifaces("region lost: no session, no authority to hold tunnels")
            # [GONE_IS_GONE_V1] no merge in its place: the region's routes, names and tunnels are withdrawn, and that is
            # the state of the world until the region is back. Nothing is recomputed from elsewhere.
            config.log.warning("REGION: lost; withdrew its routes and names, tore down %d tunnel(s); waiting for the region", n)
        if switch["to"]:                # [FORWARD_SWITCH_IN_PLACE_V1] a switch: no wait, the new pond now
            _do_switch()
            continue
        for _ in range(backoff):
            if not config.running or switch["to"]:
                break
            time.sleep(1)
        if not config.running:
            return
        if switch["to"]:
            _do_switch()
            continue
        backoff = 5                     # [REJOIN_EVERY_5S_V1] one HTTP call per try; the doubling to 60 s made a region restart cost up to a minute
        _write_view("steady", "rejoin:attempt")


def run_lan_only_loop():
    """[LAN_ONLY_NO_REGION_V1] No region: FrogNet discovery and the elections are runMerge's (discovery.live), and the
    merge's bring-up stage keeps the node free of WireGuard. The daemon makes frognet0, takes down any wg iface left
    from a region this node was in (no region, no authority to hold one), says so, and waits for its stop. Joining a
    region (fdf-join / fdf-region-join) restarts it into run_region_loop; nothing is polled here."""
    _ensure_frognet0()
    n = _tear_down_all_kernel_wg_ifaces("lan_only: no region, no authority to hold tunnels")
    config.log.warning("LAN-only loop started: no region (RINA_RAM unset), %d wg iface(s) taken down; discovery and the "
                       "elections are runMerge's, this daemon has nothing else to do [LAN_ONLY_NO_REGION_V1]", n)
    while config.running:
        time.sleep(1)


def run_daemon_loop():
    """The daemon's loop for this node's mode (config.MODE)."""
    if getattr(config, "REGION_RAM", ""):                     # [REGION_V1]
        return run_region_loop()
    return run_lan_only_loop()


def shutdown_all():
    """Graceful exit: leave tunnels alone.  bulk_teardown = nuke_tunnels."""
    config.log.info("SHUTDOWN: leaving %d tunnels in place",
                    len(_active_tunnels))
