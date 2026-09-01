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
CLI entry points for frognet_route - invoked from sync_interfaces.sh
and from the post-discovery reconcile in poll.py.

Commands:

  record <dest> <host_path> <dev> <via> <rtt_ms> [kind] [source] [ch_name]
      Append one observation to the observations file.  Called by
      sync_interfaces.sh for each successful /32 probe.

  commit-provisional
      Install wave-1 winners; skip removals and teardowns.  Called by
      sync_interfaces.sh between wave 1 and wave 2.

  commit-final
      Install winners, remove losers, tear down unused tunnels, sweep
      leaked probes.  Called at the end of sync_interfaces and again by
      poll.py after tunnel observations are added.

  dump
      Print the current observations file to stdout.  Diagnostic only.

  sweep-probes
      Sweep leaked .2/32 routes.  Diagnostic/operator command.

All commands exit 0 on success, 2 on argument error, 1 on runtime error.
Normal stdout is reserved for machine-readable output (dump);
diagnostics go to stderr / syslog.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from .committer import (OBSERVATIONS_PATH, SNAPSHOT_PATH,
                        append_observation, commit_final,
                        commit_provisional)
from .iproute import RealIPRoute
from .observation import (FAILURES_PATH, Failure, Observation,
                          read_observations, write_failures)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _cmd_record(args) -> int:
    try:
        obs = Observation(
            dest=args.dest,
            host_path=args.host_path,
            dev=args.dev,
            via=args.via,
            rtt_ms=int(args.rtt_ms),
            kind=args.kind,
            source=args.source,
            ch_name=args.ch_name,
        )
    except ValueError as e:
        print(f"record: {e}", file=sys.stderr)
        return 2
    append_observation(obs, args.obs_path)
    return 0


def _cmd_record_failure(args) -> int:
    """[PROBE_FAILURE_REMOVAL_V1] Append one terminal probe failure.

    Called by sync_interfaces.sh from the INGEST loop when an entry
    arrives with no valid echo body - every retry was rejected.  The
    failure is consumed by the next commit_final, which removes any
    current kernel route for `dest` that has no winning observation.
    """
    try:
        f = Failure(
            dest=args.dest,
            dev=args.dev,
            via=args.via,
            kind=args.kind,
            ch_name=args.ch_name,
            source=args.source,
        )
    except ValueError as e:
        print(f"record-failure: {e}", file=sys.stderr)
        return 2
    write_failures(args.failures_path, [f])
    return 0


def _cmd_commit_provisional(args) -> int:
    ipr = RealIPRoute()
    commit_provisional(ipr, args.obs_path,
                       owned_subnets=args.owned_subnets or [])
    return 0


def _cmd_commit_final(args) -> int:
    ipr = RealIPRoute()
    # Read channel->iface mapping from a JSON file the tunnel daemon
    # maintains (one entry per active channel).  Optional - if missing,
    # tear_down for any channel is a no-op.
    ch_to_iface: dict[str, str] = {}
    if args.channel_map:
        try:
            with open(args.channel_map) as f:
                ch_to_iface = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"commit-final: channel_map load failed: {e}",
                  file=sys.stderr)
    # [BROKER_TEARDOWN_V1] Read broker channel list from JSON file the
    # tunnel daemon writes alongside channel_map.json.  Format:
    # ["ChannelName1", "ChannelName2", ...].  When present, the planner
    # tears down only orphans (active - broker) instead of using the
    # legacy observation-winners rule.  When absent, legacy rule applies.
    broker_channels: Optional[list] = None
    if args.broker_channels_path:
        try:
            with open(args.broker_channels_path) as f:
                broker_channels = json.load(f)
            if not isinstance(broker_channels, list):
                print(f"commit-final: broker_channels not a list, ignoring",
                      file=sys.stderr)
                broker_channels = None
        except (OSError, json.JSONDecodeError) as e:
            print(f"commit-final: broker_channels load failed: {e}",
                  file=sys.stderr)
    # [TRACEROUTE_V1] Read per-observation traceroute hop counts from
    # the TSV sync_interfaces writes alongside discovery_observations.tsv.
    # Format per line: dest_subnet\tprobe_ip\tdev\tadditional_253_hops
    # Loaded as {(dest, dev): int}.  Multiple rows for the same
    # (dest, dev) (probe_ip differs across waves): keep the MIN hop
    # count - a successful zero-additional probe in any wave proves
    # the path is direct.
    traceroute_hops: Optional[dict[tuple, int]] = None
    if args.traceroutes_path:
        try:
            traceroute_hops = {}
            with open(args.traceroutes_path) as f:
                for ln in f:
                    parts = ln.rstrip("\n").split("\t")
                    if len(parts) != 4:
                        continue
                    dest, _probe_ip, dev, add253_s = parts
                    try:
                        add253 = int(add253_s)
                    except ValueError:
                        continue
                    key = (dest, dev)
                    prior = traceroute_hops.get(key)
                    if prior is None or add253 < prior:
                        traceroute_hops[key] = add253
        except OSError as e:
            print(f"commit-final: traceroutes load failed: {e}",
                  file=sys.stderr)
            traceroute_hops = None
    commit_final(
        ipr,
        args.obs_path,
        active_tunnel_channels=list(ch_to_iface.keys()),
        channel_to_iface=ch_to_iface,
        owned_subnets=args.owned_subnets or [],
        snapshot_path=args.snapshot_path,
        broker_channels=broker_channels,
        traceroute_hops=traceroute_hops,
    )
    return 0


def _cmd_dump(args) -> int:
    for o in read_observations(args.obs_path):
        print(f"{o.dest}\t{o.host_path}\t{o.dev}\t{o.via}\t"
              f"{o.rtt_ms}\t{o.kind}\t{o.source}\t{o.ch_name}")
    return 0


def _cmd_sweep_probes(args) -> int:
    from .committer import _sweep_stale_probes
    ipr = RealIPRoute()
    n = _sweep_stale_probes(ipr)
    print(f"swept {n} leaked probe routes")
    return 0


def main(argv: list[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="frognet_route")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--obs-path", default=OBSERVATIONS_PATH,
                        dest="obs_path")
    parser.add_argument("--failures-path", default=FAILURES_PATH,
                        dest="failures_path")
    parser.add_argument("--owned-subnet", action="append",
                        dest="owned_subnets",
                        help="/24 this node owns; can be repeated")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="append one observation")
    p.add_argument("dest")
    p.add_argument("host_path")
    p.add_argument("dev")
    p.add_argument("via")
    p.add_argument("rtt_ms")
    p.add_argument("--kind", default="lan",
                   choices=["lan", "tunnel", "transit"])
    p.add_argument("--source", default="")
    p.add_argument("--ch-name", default="", dest="ch_name")
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("record-failure",
                       help="append one terminal probe failure "
                            "[PROBE_FAILURE_REMOVAL_V1]")
    p.add_argument("dest", help="/24 CIDR the failed probe targeted")
    p.add_argument("dev", help="kernel iface the probe went out")
    p.add_argument("--via", default="",
                   help="next-hop the probe used (LAN relay or empty)")
    p.add_argument("--kind", default="tunnel",
                   choices=["lan", "tunnel", "transit"])
    p.add_argument("--ch-name", default="", dest="ch_name")
    p.add_argument("--source", default="",
                   help="wave label (e.g. sync_wave1)")
    p.set_defaults(func=_cmd_record_failure)

    p = sub.add_parser("commit-provisional",
                       help="install wave-1 winners only")
    p.set_defaults(func=_cmd_commit_provisional)

    p = sub.add_parser("commit-final", help="full commit + snapshot")
    p.add_argument("--channel-map", default="",
                   help="JSON path: {channel_name: iface_name}")
    p.add_argument("--broker-channels-path", default="",
                   help="JSON path: [channel_name, ...] from broker my-channels."
                        " When set, teardown rule is active-minus-broker"
                        " instead of legacy active-minus-winners.")
    p.add_argument("--traceroutes-path", default="",
                   help="TSV path: dest\\tprobe_ip\\tdev\\tadditional_253_hops"
                        " (written by sync_interfaces.sh).  When set, an"
                        " observation with 0 additional 253 hops beats any"
                        " observation with more, regardless of RTT.")
    p.add_argument("--snapshot-path", default=SNAPSHOT_PATH,
                   dest="snapshot_path")
    p.set_defaults(func=_cmd_commit_final)

    p = sub.add_parser("dump", help="print observations file")
    p.set_defaults(func=_cmd_dump)

    p = sub.add_parser("sweep-probes",
                       help="delete leaked .2/32 routes")
    p.set_defaults(func=_cmd_sweep_probes)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except Exception as e:
        logging.exception("unhandled error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
