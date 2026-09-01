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
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event
"""
Daemon threads: ADVERTISE (broker websocket), MERGE coordinator, METRICS.

The broker now drives tunnel creation via _pair_and_push - when a channel
connects, the broker creates tunnels to all other connected channels and
pushes tunnel_created (host side) and tunnel_request (joiner side) events.
The merge coordinator fires runMerge once after all tunnel transitions settle.
"""

import json
import socket
import subprocess
import threading
import time
from typing import Dict, Set

from . import config
from .broker import broker_get, broker_post
from .names import parse_channel_name
from .peer import Peer, registry
from .websocket import SimpleWebSocket


def advertise_thread():
    trace_enter('threads.advertise_thread')
    reconnect_delay = config.RECONNECT_BASE_SEC
    # Track inbound (hosted) peers - tear these down on disconnect.
    # Outbound (joined) peers are managed by the broker via tunnel_request.
    hosted: Set[str] = set()

    while config.running:
        ws = None
        try:
            ws_url = (config.BROKER_URL
                      .replace("https://", "wss://")
                      .replace("http://", "ws://")) + "/api/v1/advertise"

            config.log.info("ADVERTISE: connecting to %s", ws_url)
            ws = SimpleWebSocket(ws_url)
            ws.connect()
            config.log.info("ADVERTISE: connected - sending advertisement")
            registry.broker_ws = ws

            transit = config.discover_transit_subnets(config.LOCAL_SUBNET)
            ws.send_json({
                "group_token":     config.GROUP_TOKEN,
                "channel_name":    config.CHANNEL_NAME,
                "pubkey":          config.PUBKEY,
                "frognet_subnets": [config.LOCAL_SUBNET],
                "transit_subnets": transit,
                "label":           config.NODE_NAME,
            })

            # Drain until confirmation - discard pre-confirm events
            while True:
                msg = ws.recv_json()
                if "error" in msg:
                    raise ConnectionError(f"Broker rejected: {msg['error']}")
                if msg.get("status") == "advertised":
                    break
                if msg.get("event"):
                    config.log.info("ADVERTISE pre-confirm: discarding event=%r", msg["event"])

            config.log.info("ADVERTISE: channel '%s' live", config.CHANNEL_NAME)
            reconnect_delay = config.RECONNECT_BASE_SEC
            # Broker will push tunnel_created/tunnel_request via _pair_and_push.

            # Main event loop
            while config.running:
                try:
                    msg = ws.recv_json()
                except socket.timeout:
                    try:
                        ws.send_json({"type": "ping"})
                    except Exception:
                        break
                    continue

                event = msg.get("event") or msg.get("type", "")
                config.dbg(f"ADVERTISE: received event={event!r}")

                if event == "tunnel_created":
                    # Host side: broker tells us a joiner connected to our channel.
                    joiner_ch   = msg.get("joiner_channel", msg.get("tunnel_name", ""))
                    wg_config   = msg["wg_config"]
                    remote_subs = msg.get("joiner_subnets", wg_config.get("remote_subnets", []))

                    remote_name, remote_prefix = parse_channel_name(joiner_ch)
                    if not remote_name:
                        config.log.warning("ADVERTISE: unparseable joiner_channel %r", joiner_ch)
                        continue

                    config.log.info("ADVERTISE tunnel_created (host): %s endpoint=%s",
                             remote_name, wg_config.get("droplet_endpoint"))
                    peer = registry.get_or_create(remote_name, remote_prefix)
                    peer.connect(wg_config, remote_subs, joiner_ch, "in")
                    hosted.add(remote_name)

                elif event == "tunnel_request":
                    # Joiner side: broker tells us to connect to a host channel.
                    host_ch   = msg.get("host_channel", msg.get("tunnel_name", ""))
                    wg_config = msg["wg_config"]
                    host_subs = msg.get("host_subnets", wg_config.get("remote_subnets", []))

                    remote_name, remote_prefix = parse_channel_name(host_ch)
                    if not remote_name:
                        config.log.warning("ADVERTISE: unparseable host_channel %r", host_ch)
                        continue

                    config.log.info("ADVERTISE tunnel_request (joiner): %s endpoint=%s",
                             remote_name, wg_config.get("droplet_endpoint"))
                    peer = registry.get_or_create(remote_name, remote_prefix)
                    peer.connect(wg_config, host_subs, host_ch, "out")

                elif event == "tunnel_destroyed":
                    tname = msg.get("tunnel_name", "")
                    peer = registry.find_by_tunnel_name(tname)
                    if peer:
                        config.log.info("ADVERTISE tunnel_destroyed: %s", peer.remote_name)
                        peer.disconnect()
                        hosted.discard(peer.remote_name)
                    else:
                        config.log.info("ADVERTISE tunnel_destroyed: %r already gone", tname)

                elif event == "ping" or msg.get("type") == "ping":
                    ws.send_json({"type": "pong"})
                    config.dbg("ADVERTISE: sent pong")

        except ConnectionError as e:
            config.log.warning("ADVERTISE: connection lost: %s", e)
        except socket.timeout:
            config.log.warning("ADVERTISE: timed out")
        except Exception as e:
            config.log.exception("ADVERTISE: error: %s", e)
        finally:
            registry.broker_ws = None
            if ws:
                ws.close()
            config.log.info("ADVERTISE: disconnected - tearing down %d hosted peer(s): %s",
                     len(hosted), sorted(hosted))
            for rname in list(hosted):
                peer = registry.get(rname)
                if peer:
                    peer.disconnect()
            hosted.clear()

        if config.running:
            config.log.info("ADVERTISE: reconnecting in %ds...", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, config.RECONNECT_MAX_SEC)


def merge_coordinator_thread():
    """Event-driven runMerge trigger.  Peer workers signal registry when
    a tunnel transitions to UP or IDLE.  This thread debounces those
    signals - it waits for MERGE_SETTLE_SEC of quiet after the last
    signal, then fires runMerge exactly once if the set of UP peers
    has actually changed.  No polling, no redundant runs."""
    trace_enter('threads.merge_coordinator_thread')
    last_up_set: set = set()

    while config.running:
        # Block until a peer signals a state change (or shutdown).
        signaled = registry.wait_merge(timeout=5.0)
        if not signaled:
            continue

        # A peer transitioned.  More may follow (broker pushes tunnels
        # in bursts).  Debounce: keep resetting the settle timer as
        # long as new signals arrive within MERGE_SETTLE_SEC.
        while config.running:
            more = registry.wait_merge(timeout=config.MERGE_SETTLE_SEC)
            if not more:
                # No new signal for the full settle period - done.
                break

        if not config.running:
            return

        current_up = registry.up_peer_set()
        if current_up == last_up_set:
            config.log.info("MERGE: signaled but UP set unchanged (%s) - skip",
                            sorted(current_up))
            continue

        config.log.info("MERGE: UP set changed %s -> %s - running runMerge",
                        sorted(last_up_set), sorted(current_up))
        last_up_set = current_up
        subprocess.Popen(["/usr/local/bin/runMerge.bash"])


def metrics_thread():
    trace_enter('threads.metrics_thread')
    while config.running:
        for _ in range(config.METRICS_INTERVAL_SEC):
            if not config.running:
                return
            time.sleep(1)

        peers = registry.all_peers()
        total = hosted = joined = 0
        tunnel_list = []

        for peer in peers:
            info = peer.get_info()
            if info["state"] != Peer.UP:
                continue
            total += 1
            if info["direction"] == "in":
                hosted += 1
            else:
                joined += 1

            iface = info["iface"]
            stats = {}
            for counter in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                            "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
                try:
                    with open(f"/sys/class/net/{iface}/statistics/{counter}") as f:
                        stats[counter] = int(f.read().strip())
                except (OSError, ValueError):
                    stats[counter] = 0

            tunnel_list.append({
                "name": peer.tunnel_name, "iface": iface,
                "direction": info["direction"], "channel": info["channel_name"],
            })

            try:
                subprocess.run(
                    ["/usr/local/bin/metric_upsert.sh", "TunnelChannel",
                     peer.tunnel_name, json.dumps({
                         "tunnel_name": peer.tunnel_name, "interface": iface,
                         "channel": info["channel_name"], "direction": info["direction"],
                         "counters": stats, "ts": int(time.time()),
                     }, separators=(",", ":"))],
                    capture_output=True, timeout=15, check=False)
            except Exception as e:
                config.log.info("METRICS %s: %s", peer.tunnel_name, e)

        try:
            subprocess.run(
                ["/usr/local/bin/metric_upsert.sh", "TunnelSummary", "Aggregate",
                 json.dumps({"total": total, "hosted": hosted, "joined": joined,
                             "tunnels": tunnel_list, "ts": int(time.time())},
                            separators=(",", ":"))],
                capture_output=True, timeout=15, check=False)
        except Exception as e:
            config.log.info("METRICS summary: %s", e)
