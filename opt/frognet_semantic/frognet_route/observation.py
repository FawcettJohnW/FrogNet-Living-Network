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
"""
Observation - the unit of evidence the committer operates on.

An Observation says, in effect:
    "I measured a path to <dest>'s .2 probe alias by forcing traffic
     through (<dev>, <via>), and the full-stack frognet_echo round-trip
     was <rtt_ms> milliseconds.  The echo came back identifying the
     peer as <host_path>."

Observations are written to /etc/sentinels/discovery_observations.tsv
by sync_interfaces.sh and poll.py; the committer reads the file.
TSV, not JSON, because bash writes it and bash is bad at JSON.

Fields and their meanings:

  dest        /24 CIDR, e.g. "10.x.y.0/24".  The subnet the peer
              advertises (derived from host_path's /24).

  host_path   the peer's canonical .1 identity returned by its echo.
              Stored because it's what gets used as the `via` on the
              committed /24 - see planner.py.

  dev         kernel interface the probe went out on, e.g. "eth0",
              "wlan1", "wg3".

  via         next-hop IP used for the /32 probe.  For on-link probes
              (directly-attached subnet) this is the empty string.
              For transit or wg tunnels, the /30 peer or tunnel edge.

  rtt_ms      integer milliseconds.  Only successful observations are
              emitted; a failed probe produces no observation at all.

  kind        "lan" | "tunnel" | "transit".  Used by the committer
              only for tunnel-teardown bookkeeping; it is NOT a
              tiebreaker in the winner selection.

  source      free-form label, e.g. "sync_wave1", "sync_wave2",
              "reconcile".  Informational.

  ch_name     for kind="tunnel", the broker channel name so the
              committer knows which channel a winning tunnel belongs
              to.  Empty for LAN/transit.

The file format is one observation per line, tab-separated in the
order above.  Unknown fields (for forward compatibility) are ignored.
Comment lines (starting with '#') and blank lines are skipped.
"""
from __future__ import annotations

from frognet_trace import trace_enter, trace_event

from dataclasses import dataclass, astuple
from typing import Iterable


_FIELDS = ("dest", "host_path", "dev", "via", "rtt_ms", "kind",
           "source", "ch_name")


@dataclass(frozen=True)
class Observation:
    dest: str
    host_path: str
    dev: str
    via: str
    rtt_ms: int
    kind: str = "lan"
    source: str = ""
    ch_name: str = ""

    def __post_init__(self):
        trace_enter('observation.Observation.__post_init__')
        if self.kind not in ("lan", "tunnel", "transit"):
            raise ValueError(f"bad kind: {self.kind!r}")
        if self.rtt_ms < 0:
            raise ValueError(f"negative rtt_ms: {self.rtt_ms}")
        if not self.dest.endswith("/24"):
            raise ValueError(f"dest must be /24 CIDR: {self.dest!r}")
        if not self.host_path:
            raise ValueError("host_path is required (echo response field 2)")
        if not self.dev:
            raise ValueError("dev is required")


def _tsv_escape(s: str) -> str:
    # No tab, no newline, no hash in the fields we expect.  If someone
    # feeds us garbage, fail loud rather than silently corrupting the file.
    trace_enter('observation._tsv_escape', s=repr(s))
    if "\t" in s or "\n" in s:
        raise ValueError(f"field contains tab or newline: {s!r}")
    return s


def write_observations(path: str, obs: Iterable[Observation]) -> None:
    """Append observations to `path`.  Creates the file if missing.

    Append (not truncate) because sync_interfaces wave 1 and wave 2
    both write observations, and wave 2's writes must not clobber
    wave 1's.  The committer truncates explicitly when it's done.
    """
    trace_enter('observation.write_observations', path=repr(path), obs=repr(obs))
    lines = []
    for o in obs:
        row = [
            _tsv_escape(o.dest),
            _tsv_escape(o.host_path),
            _tsv_escape(o.dev),
            _tsv_escape(o.via),
            str(int(o.rtt_ms)),
            _tsv_escape(o.kind),
            _tsv_escape(o.source),
            _tsv_escape(o.ch_name),
        ]
        lines.append("\t".join(row))
    if not lines:
        return
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def read_observations(path: str) -> list[Observation]:
    """Read every observation from `path`.  Missing file -> empty list.

    Malformed lines are logged via stderr (print) but skipped - we
    never crash the committer over a single bad line from bash.
    """
    trace_enter('observation.read_observations', path=repr(path))
    out: list[Observation] = []
    try:
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 5:
                    # Need at least the required fields.
                    continue
                # Pad out missing optional fields.
                while len(parts) < len(_FIELDS):
                    parts.append("")
                try:
                    out.append(Observation(
                        dest=parts[0],
                        host_path=parts[1],
                        dev=parts[2],
                        via=parts[3],
                        rtt_ms=int(parts[4]),
                        kind=parts[5] or "lan",
                        source=parts[6],
                        ch_name=parts[7],
                    ))
                except (ValueError, TypeError) as e:
                    import sys
                    print(f"observation line {lineno}: {e}", file=sys.stderr)
    except FileNotFoundError:
        return []
    return out


def truncate(path: str) -> None:
    """Zero out the observations file."""
    trace_enter('observation.truncate', path=repr(path))
    open(path, "w").close()


# ---------------------------------------------------------------------------
# Failures: terminal probe failures, parallel structure to Observation
# ---------------------------------------------------------------------------
# A Failure records that sync_interfaces' echo through (dev, via) for
# `dest` failed terminally (every attempt rejected - empty body, HTTP
# 503, or curl error).  Failures drive committer route removal for
# dests with no winning observation this run - including routes
# bringup installed from a broker-advertised remote_subnet that the
# peer cannot actually forward.
#
# Lifecycle is identical to Observation: append-only across waves of
# one merge run, truncated by the committer after commit_final.
#
# Separate file (not a sentinel field on Observation) because:
#   1. Observation's __post_init__ requires host_path and rtt_ms,
#      which a failed echo doesn't have.
#   2. Read paths and write paths cross-cut Observation; mixing
#      success and failure rows in one file requires every reader
#      to filter, every writer to remember which it's writing.

FAILURES_PATH = "/etc/sentinels/discovery_failures.tsv"

_FAILURE_FIELDS = ("dest", "dev", "via", "kind", "ch_name", "source")


@dataclass(frozen=True)
class Failure:
    """One terminal probe failure for (dest, dev, via).

    A Failure is recorded when the wave's echo through (dev, via) for
    `dest` returned no valid body across every attempt (final state
    of the retry loop in sync_interfaces.sh:run_one_probe).  No
    rtt_ms and no host_path because there was no response to measure
    or parse.
    """
    dest: str
    dev: str
    via: str = ""
    kind: str = "tunnel"
    ch_name: str = ""
    source: str = ""

    def __post_init__(self):
        trace_enter('observation.Failure.__post_init__')
        if self.kind not in ("lan", "tunnel", "transit"):
            raise ValueError(f"bad kind: {self.kind!r}")
        if not self.dest.endswith("/24"):
            raise ValueError(f"dest must be /24 CIDR: {self.dest!r}")
        if not self.dev:
            raise ValueError("dev is required")


def write_failures(path: str, failures: Iterable[Failure]) -> None:
    """Append failures to `path`.  Creates the file if missing.  Same
    O_APPEND semantics as write_observations so concurrent probe
    workers don't clobber each other's lines.
    """
    trace_enter('observation.write_failures', path=repr(path),
                failures=repr(failures))
    lines = []
    for f in failures:
        row = [
            _tsv_escape(f.dest),
            _tsv_escape(f.dev),
            _tsv_escape(f.via),
            _tsv_escape(f.kind),
            _tsv_escape(f.ch_name),
            _tsv_escape(f.source),
        ]
        lines.append("\t".join(row))
    if not lines:
        return
    with open(path, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def read_failures(path: str) -> list[Failure]:
    """Read every failure from `path`.  Missing file -> empty list.
    Malformed lines are reported on stderr and skipped.
    """
    trace_enter('observation.read_failures', path=repr(path))
    out: list[Failure] = []
    try:
        with open(path) as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                while len(parts) < len(_FAILURE_FIELDS):
                    parts.append("")
                try:
                    out.append(Failure(
                        dest=parts[0],
                        dev=parts[1],
                        via=parts[2],
                        kind=parts[3] or "tunnel",
                        ch_name=parts[4],
                        source=parts[5],
                    ))
                except (ValueError, TypeError) as e:
                    import sys
                    print(f"failure line {lineno}: {e}", file=sys.stderr)
    except FileNotFoundError:
        return []
    return out


def truncate_failures(path: str) -> None:
    """Zero out the failures file."""
    trace_enter('observation.truncate_failures', path=repr(path))
    open(path, "w").close()
