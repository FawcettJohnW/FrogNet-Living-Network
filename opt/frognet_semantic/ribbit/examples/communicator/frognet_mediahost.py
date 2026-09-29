#!/usr/bin/env python3
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
frognet_mediahost.py - the FrogNet media host as a RECONCILIATION LOOP.

UMS technique (ffmpeg orchestration + serve-at-scale fan-out), lifted as TECHNIQUE, wired
into UnREST memory-not-messages. Nobody commands this host. It reads the desired media
state from the shared space and reconciles reality (ffmpeg pipelines) to match. The plan
lives in the transient, NOT in this process - so when the media host floats (re-elects to
another node), the new host re-reads the same tuples and rebuilds the identical plan. The
float is free, exactly like the databasehost float, because the plan was never in the host.

ffmpeg is invoked as a SUBPROCESS (never linked) using libvpx (VP8, BSD) + Opus (BSD), so
this code stays Apache-2.0/OSS and the node's own installed ffmpeg carries its own terms.
No ffmpeg binary is bundled. License posture clean by construction.

Four memory surfaces (no messages), all on the SD: coordination plane:
  SD:avhost              - this host's endpoint (floats to highest-IP capable node)
  SD:stream.<id>         - a SOURCE declaration: who's sourcing, format, level cap, endpoint
  SD:watch.<id>.<viewer> - a viewer's subscription: render caps AND its requested ladder level
  SD:plan.<id>           - the host's reconciled plan: which rungs are LIVE, who gets what
                           (written by host, read by all)

ON-DEMAND RUNGS (John's refinement): the host does NOT pre-encode every ladder level. It
encodes ONLY the levels consumers are actually requesting, read from their watch tuples. A
rung with no subscriber is not running. When a client switches to a level not yet being
transcoded, the host starts/enables that output; when the last subscriber leaves a rung, it
stops. The plan = the set of demanded rungs, reconciled every tick.

The MIX, per recipient (minus-self):
  audio = sum of every OTHER participant's uplink audio   (ffmpeg amix)
  video = grid of every OTHER participant's thumbnail      (ffmpeg xstack)
Each personalized program is then encoded at exactly the demanded rungs.
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from typing import Any, Dict, List, Optional, Tuple

try:
    import frognet_tuples as FT
except Exception:
    FT = None

SD = "SD"                                   # coordination service (SD: plane)
DBHOST_CONTROL = "databasehost_control.frognet"

# The ladder: index -> (bitrate kbps, scale). The host encodes only the INDICES in use.
LADDER = [
    (150, "160x120"),
    (300, "320x240"),
    (600, "320x240"),
    (1200, "640x480"),
    (2500, "640x480"),
]


def _ffmpeg() -> str:
    from shutil import which
    return which("ffmpeg") or "ffmpeg"


# ===========================================================================
# ffmpeg technique (lifted from UMS as technique, libvpx/Opus, subprocess only)
# ===========================================================================
def build_mix_filtergraph(other_video_labels: List[str],
                          other_audio_labels: List[str]) -> Tuple[str, str, str]:
    """Per-recipient minus-self mix:
      audio = amix sum of the OTHERS' audio
      video = xstack grid of the OTHERS' video thumbnails
    Returns (filter_complex, vout_label, aout_label). Labels are ffmpeg input pad refs
    like '[0:v]'. With one other, the grid is just that one tile; with N, an xstack grid."""
    parts = []
    # --- video grid (xstack) ---
    n = len(other_video_labels)
    if n == 0:
        vout = ""                                        # no video to composite
    elif n == 1:
        parts.append(f"{other_video_labels[0]}scale=320x240[vout]")
        vout = "[vout]"
    else:
        cols = 1 if n == 1 else 2
        rows = (n + cols - 1) // cols
        scaled = []
        for i, lbl in enumerate(other_video_labels):
            parts.append(f"{lbl}scale=160x120[g{i}]")
            scaled.append(f"[g{i}]")
        # xstack layout string: positions in a cols x rows grid
        layout = "|".join(
            f"{(i % cols)}*w0_{(i // cols)}*h0".replace("w0", "160").replace("h0", "120")
            for i in range(n))
        parts.append(f"{''.join(scaled)}xstack=inputs={n}:layout={layout}[vout]")
        vout = "[vout]"
    # --- audio sum (amix) ---
    m = len(other_audio_labels)
    if m == 0:
        aout = ""
    elif m == 1:
        parts.append(f"{other_audio_labels[0]}anull[aout]")
        aout = "[aout]"
    else:
        parts.append(f"{''.join(other_audio_labels)}amix=inputs={m}:normalize=0[aout]")
        aout = "[aout]"
    return ";".join(parts), vout, aout


def rung_output_args(level_idx: int, vout: str, aout: str, port: int) -> List[str]:
    """Encode the mixed program at ONE ladder rung to a TCP listener the consumer attaches
    to (the established-socket data plane). libvpx + Opus, subprocess-only."""
    kbps, scale = LADDER[level_idx]
    args: List[str] = []
    if vout:
        args += ["-map", vout, "-c:v", "libvpx", "-deadline", "realtime",
                 "-cpu-used", "8", "-b:v", f"{kbps}k", "-g", "30", "-keyint_min", "30",
                 "-vf", f"scale={scale}"]
    if aout:
        args += ["-map", aout, "-c:a", "libopus", "-b:a", "32k"]
    # one rung -> one output endpoint; consumers at this level attach here
    args += ["-f", "ivf" if vout else "ogg", f"tcp://0.0.0.0:{port}?listen=1"]
    return args


# ===========================================================================
# THE PLAN: read declarations + subscriptions, compute demanded rungs per recipient
# ===========================================================================
def read_space(session: str, dbhost: str) -> Tuple[List[dict], List[dict]]:
    """Read SD:stream.* and SD:watch.* for this session from the transient."""
    if FT is None:
        return [], []
    streams = [r["value"] for r in FT.get_all(SD, dbhost=dbhost)
               if isinstance(r.get("value"), dict)
               and r["value"].get("type") == "stream"
               and r["value"].get("session") == session]
    watchers = [r["value"] for r in FT.get_all(SD, dbhost=dbhost)
                if isinstance(r.get("value"), dict)
                and r["value"].get("type") == "watch"
                and r["value"].get("session") == session]
    return streams, watchers


def compute_plan(streams: List[dict], watchers: List[dict]) -> dict:
    """The reconciled plan. For each recipient (a watcher), the program is the minus-self
    mix of the OTHER sources; the RUNG is whatever level THAT watcher requested. The set of
    distinct (recipient) programs and their demanded rungs is the plan. Critically: a rung
    is in the plan ONLY if a watcher requested it - on-demand, nothing pre-encoded."""
    sources = {s["who"]: s for s in streams}          # who is putting media on their uplink
    plan_recipients = {}
    demanded_levels = set()
    for w in watchers:
        me = w["who"]
        level = int(w.get("ladder_level", 2))
        demanded_levels.add(level)
        others = [who for who in sources if who != me]    # minus-self
        plan_recipients[me] = {
            "recipient": me,
            "level": level,                                # the rung THIS consumer demands
            "others": others,                              # who's in their mix/grid
            "audio_possible": True,                        # set false if even rung 0 won't fit
            "video_possible": bool(others),
        }
    return {
        "recipients": plan_recipients,
        "demanded_levels": sorted(demanded_levels),        # the ONLY rungs to encode
        "sources": list(sources.keys()),
    }


# ===========================================================================
# THE RECONCILER: drive ffmpeg to match the plan; start/stop rungs on demand
# ===========================================================================
class MediaHost:
    def __init__(self, session: str, dbhost: str = DBHOST_CONTROL, base_port: int = 9100):
        self.session = session
        self.dbhost = dbhost
        self.base_port = base_port
        # running ffmpeg pipelines, keyed by (recipient, level) -> Popen + port
        self.pipelines: Dict[Tuple[str, int], dict] = {}

    def _port_for(self, recipient: str, level: int) -> int:
        # deterministic port per (recipient,level) so consumers can find their rung
        h = abs(hash((recipient, level))) % 400
        return self.base_port + h

    def _start_pipeline(self, recipient: str, level: int, others: List[str]) -> None:
        """Start the ffmpeg process for ONE recipient at ONE rung - only called when a
        watcher demands a (recipient,level) not already running. This IS the on-demand
        rung start."""
        key = (recipient, level)
        if key in self.pipelines:
            return
        port = self._port_for(recipient, level)
        # inputs: each OTHER source's uplink (here each is a tcp:// pull from that source's
        # uplink endpoint; in the box these are the established sockets). Build labels.
        vlabels = [f"[{i}:v]" for i in range(len(others))]
        alabels = [f"[{i}:a]" for i in range(len(others))]
        fc, vout, aout = build_mix_filtergraph(vlabels, alabels)
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error"]
        for who in others:
            # placeholder source input; on the box this is the source's uplink endpoint
            cmd += ["-i", f"tcp://{who}.uplink.frognet?timeout=2000000"]
        if fc:
            cmd += ["-filter_complex", fc]
        cmd += rung_output_args(level, vout, aout, port)
        # NOTE: not launched in-container (no real uplinks); record the intent + cmd.
        self.pipelines[key] = {"port": port, "cmd": cmd, "proc": None,
                               "others": others, "started": time.time()}

    def _stop_pipeline(self, key: Tuple[str, int]) -> None:
        p = self.pipelines.pop(key, None)
        if p and p.get("proc"):
            try: p["proc"].terminate()
            except Exception: pass

    def reconcile(self) -> dict:
        """One tick: read space -> compute plan -> start demanded rungs, stop undemanded
        ones -> write SD:plan. Idempotent. This is the whole host."""
        streams, watchers = read_space(self.session, self.dbhost)
        plan = compute_plan(streams, watchers)

        # desired (recipient, level) set from the plan
        desired = set()
        for rec in plan["recipients"].values():
            desired.add((rec["recipient"], rec["level"]))

        # START rungs newly demanded (on-demand activation)
        for (recipient, level) in desired:
            if (recipient, level) not in self.pipelines:
                others = plan["recipients"][recipient]["others"]
                self._start_pipeline(recipient, level, others)

        # STOP rungs no longer demanded (last subscriber left)
        for key in list(self.pipelines.keys()):
            if key not in desired:
                self._stop_pipeline(key)

        # publish the plan: which rungs are live + each recipient's endpoint/possible flags
        live = {}
        for (recipient, level), p in self.pipelines.items():
            live.setdefault(recipient, {})[str(level)] = {
                "port": p["port"], "others": p["others"]}
        plan_tuple = {
            "type": "plan", "session": self.session,
            "live_rungs": live,
            "demanded_levels": plan["demanded_levels"],
            "recipients": {r: {"level": v["level"],
                               "others": v["others"],
                               "audio_possible": v["audio_possible"],
                               "video_possible": v["video_possible"]}
                           for r, v in plan["recipients"].items()},
            "sources": plan["sources"],
            "ts": time.time(),
        }
        if FT is not None:
            FT.put(SD, "plan", self.session, plan_tuple, dbhost=self.dbhost)
        return plan_tuple

    def run(self, interval: float = 1.0):
        """Reconcile forever. Stateless across ticks - re-reads the space each time, so a
        float (this process dies, another node elects) loses nothing: the new host reads
        the same tuples and rebuilds the same pipelines."""
        # publish our endpoint as the floating avhost
        if FT is not None:
            FT.put(SD, "avhost", "current",
                   {"type": "avhost", "endpoint": os.environ.get("FROGNET_AVHOST", "")},
                   dbhost=self.dbhost)
        while True:
            try:
                self.reconcile()
            except Exception as e:
                sys.stderr.write(f"[mediahost] reconcile error: {e}\n")
            time.sleep(interval)


if __name__ == "__main__":
    sess = sys.argv[1] if len(sys.argv) > 1 else "demo"
    MediaHost(sess).run()
