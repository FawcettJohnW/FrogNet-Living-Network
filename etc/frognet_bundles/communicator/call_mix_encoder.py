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
call_mix_encoder.py - the PRODUCTION all-feeds mix encoder (streaming ffmpeg per rung).

WHY THIS IS A SEPARATE STREAMING COMPONENT
  call_mediahost's mix() abstraction is a per-TICK fake (latest-frame dict -> one encoded
  frame), good enough to prove ROUTING / per-destination independence / backpressure with
  injected fakes. Real ffmpeg is NOT per-tick: it is a long-lived pipeline you FEED
  continuously (one input per source) and READ continuously. So the production encoder is
  a streaming process per demanded rung; integrating it changes call_mediahost.pump from
  per-tick-pull to continuous-pipe (the routing logic is unchanged - only the encode seam).

WHAT IT DOES (John's >2-party directive: mux ALL feeds, one encode per rung, shared)
  For ONE demanded rung, one ffmpeg process:
    inputs : one VIDEO input + one AUDIO input PER SOURCE (2N inputs for N sources)
    filter : build_mix_filtergraph(ALL video labels, ALL audio labels) - xstack grid of
             every source's video + amix of every source's audio (NOT minus-self)
    output : encode the mixed program ONCE at the rung's (bitrate, scale) -> read from
             the process; that single mixed stream is fanned to every recipient at this rung.

RUNG MAPPING - aligned to the CANONICAL ladder (sotf_ladder, 0..7; John confirmed).
  frognet_mediahost.LADDER (5 entries, 0..4) + rung_output_args are STALE against this
  space (rung_output_args would IndexError on the video rungs 5..7). We reuse LADDER's
  actual bitrate/scale VALUES but remap them onto the canonical rung semantics:
    L0 PULSE / L1 BEACON / L2 WHISPER -> presence/text: NO media (no encoder runs)
    L3 VOICE / L4 SOLO                -> audio only (amix, no video map)
    L5 DUET                           -> video+audio, (600 kbps, 320x240)   [LADDER[2]]
    L6 ENSEMBLE                       -> video+audio, (1200 kbps, 640x480)  [LADDER[3]]
    L7 CHORUS                         -> video+audio, (2500 kbps, 640x480)  [LADDER[4]]
  Proven in-container (ffmpeg 6.1.1, libvpx+libopus+xstack+amix): 2 VP8 + 2 Opus sources
  -> xstack 320x120 + amix -> valid VP8/Opus muxed output.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import threading
from typing import Dict, List, Optional, Tuple

try:
    from frognet_mediahost import build_mix_filtergraph as _legacy_mix_filtergraph
except Exception:  # pragma: no cover
    _legacy_mix_filtergraph = None


# Canvas/tile geometry for box placement. Boxes sit side-by-side at EXACT (x,y) on a
# fixed base canvas - placement, NOT compositing/merging. A missing/late source just
# leaves its box black instead of stalling the stream (the xstack weakness we avoid).
_TILE_W, _TILE_H = 320, 240


def _grid_cols(n: int) -> int:
    return 1 if n <= 1 else 2 if n <= 4 else 3


def placement_filtergraph(video_inputs: List[int], audio_inputs: List[int],
                          tile_w: int = _TILE_W, tile_h: int = _TILE_H
                          ) -> Tuple[str, str, str, int, int]:
    """Build a filter_complex that PLACES each source's video into a box at a specific
    (x,y) on a fixed black canvas (overlay, no blending), and amixes the audio. Returns
    (filter_complex, vout_label, aout_label, canvas_w, canvas_h). `video_inputs`/
    `audio_inputs` are ffmpeg input indices. The base canvas input is index -1 sentinel
    '[0:v]' - the CALLER must put a `color` base as input 0 when video is present."""
    parts: List[str] = []
    vout = aout = ""
    n = len(video_inputs)
    if n:
        cols = _grid_cols(n)
        rows = (n + cols - 1) // cols
        cw, ch = cols * tile_w, rows * tile_h
        # scale each source to a tile
        for k, vin in enumerate(video_inputs):
            parts.append(f"[{vin}:v]scale={tile_w}x{tile_h}[t{k}]")
        # overlay each tile onto the running canvas at its exact grid position
        cur = "[0:v]"                                    # input 0 = the color base canvas
        for k in range(n):
            x = (k % cols) * tile_w
            y = (k // cols) * tile_h
            nxt = "[vout]" if k == n - 1 else f"[c{k}]"
            parts.append(f"{cur}[t{k}]overlay=x={x}:y={y}{nxt}")
            cur = nxt
        vout = "[vout]"
    else:
        cw = ch = 0
    m = len(audio_inputs)
    if m == 1:
        parts.append(f"[{audio_inputs[0]}:a]anull[aout]"); aout = "[aout]"
    elif m > 1:
        parts.append("".join(f"[{a}:a]" for a in audio_inputs)
                     + f"amix=inputs={m}:normalize=0[aout]"); aout = "[aout]"
    return ";".join(parts), vout, aout, cw, ch


# rung -> (video?, audio?, kbps_or_None, scale_or_None). Audio bitrate is fixed (Opus 32k).
# Values for the video rungs reuse frognet_mediahost.LADDER tiers, remapped onto sotf_ladder.
_RUNG: Dict[int, Tuple[bool, bool, Optional[int], Optional[str]]] = {
    0: (False, False, None, None),     # PULSE   - presence
    1: (False, False, None, None),     # BEACON  - text
    2: (False, False, None, None),     # WHISPER - text
    3: (False, True,  None, None),     # VOICE   - audio only
    4: (False, True,  None, None),     # SOLO    - audio only
    5: (True,  True,  600,  "320x240"),  # DUET
    6: (True,  True,  1200, "640x480"),  # ENSEMBLE
    7: (True,  True,  2500, "640x480"),  # CHORUS
}
_AUDIO_KBPS = 32


def rung_carries_video(level_idx: int) -> bool:
    return _RUNG.get(level_idx, (False, False, None, None))[0]


def rung_carries_audio(level_idx: int) -> bool:
    return _RUNG.get(level_idx, (False, False, None, None))[1]


def build_mix_cmd(level_idx: int, sources: List[str],
                  ffmpeg: Optional[str] = None) -> Optional[List[str]]:
    """Build the ffmpeg argv for the all-feeds mix at one rung. Returns None if the rung
    carries no media (presence/text). Video boxes are PLACED on a fixed black canvas at
    exact (x,y) (overlay, not merged); audio is amixed. Input layout:
        [0] color base canvas (only when video)   then per source: [-i V] [-i A]
    """
    has_v, has_a, kbps, scale = _RUNG.get(level_idx, (False, False, None, None))
    if not (has_v or has_a):
        return None
    ff = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    n = len(sources)
    cols = _grid_cols(n) if has_v else 0
    rows = (n + cols - 1) // cols if cols else 0
    cw, ch = (cols * _TILE_W, rows * _TILE_H) if has_v else (0, 0)

    cmd = [ff, "-hide_banner", "-loglevel", "error"]
    vinputs: List[int] = []
    ainputs: List[int] = []
    idx = 0
    if has_v:
        # input 0 = the black base canvas the boxes are placed onto
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s={cw}x{ch}:r=10"]
        idx = 1
    for s in sources:
        if has_v:
            cmd += ["-f", "ivf", "-i", f"{{V:{s}}}"]
            vinputs.append(idx); idx += 1
        if has_a:
            cmd += ["-f", "ogg", "-i", f"{{A:{s}}}"]
            ainputs.append(idx); idx += 1

    fc, vout, aout, _cw, _ch = placement_filtergraph(vinputs, ainputs)
    if fc:
        cmd += ["-filter_complex", fc]
    if has_v and vout:
        cmd += ["-map", vout, "-c:v", "libvpx", "-deadline", "realtime",
                "-cpu-used", "8", "-b:v", f"{kbps}k", "-g", "30", "-keyint_min", "30"]
    if has_a and aout:
        cmd += ["-map", aout, "-c:a", "libopus", "-b:a", f"{_AUDIO_KBPS}k"]
    # one mixed program out to stdout. webm (NOT matroska) with -live streams cleanly
    # over a pipe - matroska needs seekable output for cues and stalls on pipe:1.
    cmd += ["-f", "webm", "-live", "1", "pipe:1"]
    return cmd


class MixEncoder:
    """A long-lived ffmpeg mixing ALL sources at ONE rung. The host feeds each source's
    demuxed video/audio into per-source FIFOs; a reader thread pulls the single mixed
    program off stdout and hands it to `on_mixed(bytes)`. One MixEncoder per demanded rung,
    shared by every recipient at that rung (encode cost = #rungs, not #recipients)."""

    def __init__(self, level_idx: int, sources: List[str], on_mixed,
                 workdir: Optional[str] = None, ffmpeg: Optional[str] = None):
        self.level_idx = level_idx
        self.sources = list(sources)
        self.on_mixed = on_mixed
        self.proc: Optional[subprocess.Popen] = None
        self.fifos: Dict[Tuple[str, str], str] = {}     # (kind, who) -> fifo path
        self._workdir = workdir or f"/tmp/frognet_mix_{level_idx}_{os.getpid()}"
        self._ffmpeg = ffmpeg
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None

    def _materialize_cmd(self) -> Optional[List[str]]:
        tmpl = build_mix_cmd(self.level_idx, self.sources, ffmpeg=self._ffmpeg)
        if tmpl is None:
            return None
        os.makedirs(self._workdir, exist_ok=True)
        out: List[str] = []
        for tok in tmpl:
            if tok.startswith("{V:") and tok.endswith("}"):
                who = tok[3:-1]; p = os.path.join(self._workdir, f"v_{who}.ivf")
                self._mkfifo(p); self.fifos[("V", who)] = p; out.append(p)
            elif tok.startswith("{A:") and tok.endswith("}"):
                who = tok[3:-1]; p = os.path.join(self._workdir, f"a_{who}.ogg")
                self._mkfifo(p); self.fifos[("A", who)] = p; out.append(p)
            else:
                out.append(tok)
        return out

    @staticmethod
    def _mkfifo(path: str):
        try:
            if not os.path.exists(path):
                os.mkfifo(path)
        except (OSError, AttributeError):
            pass        # non-POSIX: caller must use a different feed mechanism

    def start(self) -> bool:
        cmd = self._materialize_cmd()
        if cmd is None:
            return False        # presence/text rung - nothing to encode
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Open a persistent blocking writer per FIFO in its own thread. ffmpeg blocks
        # opening each input FIFO until a writer attaches; a held-open writer lets the
        # stream flow continuously instead of EOF-ing after one write.
        self._writers: Dict[Tuple[str, str], "object"] = {}
        for key, path in self.fifos.items():
            t = threading.Thread(target=self._open_writer, args=(key, path), daemon=True)
            t.start()
        return True

    def _open_writer(self, key, path):
        try:
            # blocking open: returns once ffmpeg's read end is attached
            fh = open(path, "wb", buffering=0)
            self._writers[key] = fh
        except OSError:
            pass

    def _read_loop(self):
        # webm -live streams over the pipe; read chunks and hand them on. The downstream
        # sender ships opaque bytes; the consumer's ffmpeg demuxes the webm stream.
        while not self._stop.is_set() and self.proc and self.proc.stdout:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            self.on_mixed(chunk)

    def feed(self, who: str, kind: str, data: bytes):
        """Write a source's demuxed video ('V') or audio ('A') bytes into its FIFO via the
        held-open writer. Drop-don't-stall: if the writer isn't ready or the pipe is full,
        the write is skipped rather than blocking the host."""
        fh = getattr(self, "_writers", {}).get((kind, who))
        if fh is None:
            return
        try:
            fh.write(data)
        except (OSError, ValueError):
            pass        # pipe full/closed - drop (drop-don't-stall)

    def close(self):
        self._stop.set()
        if self.proc:
            try: self.proc.terminate()
            except Exception: pass
        for p in self.fifos.values():
            try: os.remove(p)
            except OSError: pass
