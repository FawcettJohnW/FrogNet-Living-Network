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
media_stream_rungs.py - the deployment ladder that PROGRESSIVELY proves the SotF
media pathway. Each rung flips exactly ONE seam from simulated to real, running the
SAME media_stream orchestration + metrics. A failure localizes to the seam you just
flipped.

THE THREE SEAMS (the only things that change across rungs):
  TRANSPORT  data plane: connect_pair(mode) - sim | loopback | remote(real daemon :9009)
  TUPLES     control/status/metrics: MockSpace | FrognetTuplesBackend(frognet_tuples)
  DATA       frames: generated VP8 (deterministic) | real ffmpeg capture

THE LADDER:
  R0  sandbox, me alone   TRANSPORT=sim       TUPLES=mock   DATA=generated   driver=harness
  R1  real hw, sim data   TRANSPORT=loopback* TUPLES=real   DATA=generated   driver=harness
  R2  real everything     TRANSPORT=remote    TUPLES=real   DATA=capture      driver=harness
  R3  final               TRANSPORT=remote    TUPLES=real   DATA=capture      driver=client+server app
  (* R1 on a single box uses loopback = real TCP sockets + a real daemon process,
     i.e. real INTERFACES with simulated data. Across two boxes use remote.)

What each rung PROVES, and where a failure points:
  R0  the orchestration + codec + metrics are correct (no hardware in the loop).
  R1  real FNWP-1 sockets + real transient carry the proven payload - a failure here
      is the transport/tuple binding, NOT the logic (R0 already cleared that).
  R2  real capture/transcode rides the proven transport - a failure is capture/encode.
  R3  the real client+server app drives it - a failure is app integration, nothing below.

Run a rung:
  python3 media_stream_rungs.py R0
  python3 media_stream_rungs.py R1 --remote-addr <peer-or-127.0.0.1> [--dbhost databasehost.frognet]
  python3 media_stream_rungs.py R2 --remote-addr <peer> --camera "video=USB Video Device"
Each rung PRECHECKS its real dependencies and fails with the EXACT missing seam,
so you never debug logic when the real problem is "daemon not reachable" or "no ffmpeg".
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# media_stream + its sim space live in the communicator bundle; the SotF transport
# + codec live in the source tree's simulation/ + core/. Both are on PYTHONPATH on a
# box. The source tree (core/codec, simulation/transport) is located relative to
# this bundle dir when present, and otherwise expected on PYTHONPATH.
_BUNDLE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.normpath(os.path.join(_BUNDLE, "..", "..", "..", "opt", "frognet_semantic"))
for _p in (_BUNDLE, _TREE, os.path.join(_TREE, "simulation"), os.path.join(_TREE, "core")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import media_stream as M

# logging spine - ride frognet_log when present, stdlib fallback otherwise. The
# rung runner keeps human-readable PASS/FAIL on stdout (it's an operator tool), but
# all diagnostics (faults, per-frame trace, compression) go through the logger so a
# single FROGNET_LOG_LEVEL controls them and they interleave with the rest of FrogNet.
try:
    from frognet_log import get_logger          # type: ignore
    _rlog = get_logger("media.rung")
except Exception:  # pragma: no cover
    import logging as _logging
    _rlog = _logging.getLogger("frognet.media.rung")
    if not _rlog.handlers:
        _h = _logging.StreamHandler()
        _h.setFormatter(_logging.Formatter("[frognet:%(name)s] %(levelname)s %(message)s"))
        _rlog.addHandler(_h)
    _lvl = os.environ.get("FROGNET_LOG_LEVEL", "WARNING").upper()
    _rlog.setLevel(5 if _lvl == "TRACE" else _lvl)


# -- seam: DATA SOURCE --------------------------------------------------------
def data_generated(n_frames: int, fps: int = 15) -> List[Tuple[int, str, bytes]]:
    """Deterministic VP8-ish frames: 1 keyframe/sec (AUDIO_PROTECTED stands in for
    the protected gate on the keyframe cadence), the rest droppable deltas. Real
    codec bytes come from the generator in sotf_video_stream_test when present;
    here we synthesize stable bytes so R0/R1 are reproducible without ffmpeg."""
    import hashlib
    out = []
    for seq in range(n_frames):
        is_key = (seq % fps == 0)
        kind = M.AUDIO_PROTECTED if is_key else M.VIDEO_DROPPABLE
        size = 4000 if is_key else 600
        payload = hashlib.blake2b(f"frame-{seq}".encode(), digest_size=32).digest()
        payload = (payload * ((size // 32) + 1))[:size]
        out.append((seq, kind, payload))
    return out


def data_capture(n_frames: int, camera: str, fps: int = 15) -> List[Tuple[int, str, bytes]]:
    """Real ffmpeg capture -> VP8 IVF -> (seq, kind, payload). Requires ffmpeg + a
    camera. Reuses parse_ivf from sotf_video_stream_test so the frame split matches
    the proven path."""
    import shutil, subprocess, tempfile
    if not shutil.which("ffmpeg"):
        raise RungPrecheckError("DATA", "ffmpeg not in PATH (needed for real capture)")
    import sotf_video_stream_test as svt
    dur = max(1, n_frames // fps + 1)
    with tempfile.TemporaryDirectory() as td:
        ivf = os.path.join(td, "cap.ivf")
        # platform input differs (dshow on Windows, v4l2 on Linux); caller's camera
        # string carries it. Fail loudly if capture produces nothing.
        cmd = ["ffmpeg", "-y", "-i", camera, "-t", str(dur), "-c:v", "libvpx",
               "-f", "ivf", ivf]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0 or not os.path.exists(ivf):
            raise RungPrecheckError("DATA", "ffmpeg capture failed: %s"
                                    % r.stderr.decode("utf-8", "replace")[-200:])
        _meta, frames = svt.parse_ivf(ivf)
        out = []
        for vf in frames[:n_frames]:
            kind = M.AUDIO_PROTECTED if vf.is_keyframe else M.VIDEO_DROPPABLE
            out.append((vf.seq, kind, vf.payload))
        return out


# -- seam: TUPLES -------------------------------------------------------------
def tuples_mock():
    import mock_space as ms
    return ms.MockSpace()


def tuples_real(dbhost: str):
    try:
        import frognet_tuples as T  # noqa
    except Exception as e:
        raise RungPrecheckError("TUPLES", "frognet_tuples not importable: %s" % e)
    backend = M.FrognetTuplesBackend(T, dbhost=dbhost)
    # verify the real transient actually round-trips (resolve + api.php reachable),
    # so R1 fails cleanly on "can't reach the tuple plane" instead of a urllib stack.
    probe_scope = "rung:precheck:%d" % (int(time.time()) % 100000)
    try:
        backend.put("mediastream", "precheck", probe_scope, {"ok": 1}, own=True)
        rows = backend.get("mediastream", "precheck", fresh_s=0)
    except Exception as e:
        raise RungPrecheckError("TUPLES", "cannot reach the transient at %s (%s)" % (dbhost, e))
    if not any(r.get("scope") == probe_scope for r in rows):
        raise RungPrecheckError("TUPLES",
                                "wrote a probe tuple to %s but could not read it back" % dbhost)
    return backend


# -- seam: TRANSPORT (producer -> server data plane, real FNWP-1) -------------
class _SotFLink:
    """One producer->server FNWP-1 link via the proven SotF transport. Wraps
    SotFSender (encode RAW -> FNWP-1, ship over connect_pair proxy) and a
    SotFReceiver-fed sink so decoded RAW lands in the server's ingestion queue.
    mode selects the seam: sim | loopback | remote(real daemon)."""

    def __init__(self, mode: str, session_id: str, local_gw: str = "10.0.0.1",
                 remote_addr: Optional[str] = None, remote_port: int = 9009,
                 remote_role: Optional[str] = None):
        self.mode = mode
        self.session_id = session_id
        self.local_gw = local_gw
        self.remote_addr = remote_addr
        self.remote_port = remote_port
        self.remote_role = remote_role     # set when running a cross-box --role process
        self._sender = None
        self._receiver = None
        self._proxy = self._daemon = self._h1 = self._h2 = None
        self.on_decoded: Optional[Callable[[int, bool, bytes], None]] = None

    def open(self):
        import hashlib
        import sotf_video_stream_test as svt
        from transport_factories import connect_pair
        if self.mode == "remote":
            # REAL cross-box: the receiver + ingestion queue live on the FAR box.
            # A single process can't hold both ends of a cross-machine hop, so remote
            # is only valid under the role split (producer / server / consumer as
            # separate processes). We do NOT silently fall back to loopback.
            if self.remote_role is None:
                raise RungPrecheckError(
                    "TRANSPORT",
                    "remote is cross-box: run with --role producer|server|consumer on each "
                    "machine (single-process 'all' supports sim/loopback only)")
            if not self.remote_addr:
                raise RungPrecheckError("TRANSPORT", "remote mode needs --remote-addr")
            _precheck_tcp(self.remote_addr, self.remote_port, "TRANSPORT",
                          "daemon (frognet-daemon-v3)")
            self._open_remote(svt)
            return
        # sim / loopback: both ends in this process over (real, for loopback) sockets.
        req_hash = hashlib.blake2b(self.session_id.encode(),
                                   digest_size=svt.REQ_HASH_LEN).digest()
        session_init = {"session_id": self.session_id, "codec": "vp8",
                        "sr": 0, "layout": "video", "level_idx": 0}
        handler = svt.SotFMediaHandler()
        fragment = handler.extract_template(session_init)
        self._proxy, self._daemon, self._h1, self._h2 = connect_pair(
            local_gw=self.local_gw, mode=self.mode, default_fragment=fragment)
        self._sender = svt.SotFSender(self._proxy, self.session_id, fragment, req_hash)
        self._receiver = svt.SotFReceiver(self._daemon, fragment, req_hash)

    def _open_remote(self, svt):
        """Real cross-box sender half: connect a proxy to the far daemon via
        RemoteTransportFactory. The matching receiver runs in the peer's --role
        process on the far box (TestDaemonProcess/production daemon + SotFReceiver
        feeding the ingestion queue). Box-verified, not runnable in one process."""
        import hashlib
        from transport_factories import RemoteTransportFactory
        from transport_sim_tier import SimulatedProxyWorker
        req_hash = hashlib.blake2b(self.session_id.encode(),
                                   digest_size=svt.REQ_HASH_LEN).digest()
        factory = RemoteTransportFactory(self.remote_addr, self.remote_port)
        send_ep, recv_ep = factory.connect_proxy(self.local_gw)
        self._proxy = SimulatedProxyWorker(local_gw=self.local_gw,
                                           send_ep=send_ep, recv_ep=recv_ep)
        session_init = {"session_id": self.session_id, "codec": "vp8",
                        "sr": 0, "layout": "video", "level_idx": 0}
        fragment = svt.SotFMediaHandler().extract_template(session_init)
        self._sender = svt.SotFSender(self._proxy, self.session_id, fragment, req_hash)
        self._receiver = None   # lives on the far box

    def send(self, seq: int, is_key: bool, payload: bytes):
        """Encode RAW -> FNWP-1 -> wire. Returns the transport outcome."""
        vf = _as_videoframe(seq, is_key, payload)
        outcome = self._sender.send_frame(vf)
        # pull what the receiver decoded for this seq (byte-for-byte off the wire)
        got = self._receiver.received_bytes.get(seq)
        if self.on_decoded and got is not None:
            self.on_decoded(seq, is_key, got)
        return outcome

    def close(self):
        for x in (self._h1, self._h2):
            try: x and x.close()
            except Exception: pass
        try: self._proxy and self._proxy.close()
        except Exception: pass
        try: self._daemon and self._daemon.stop()
        except Exception: pass


def _as_videoframe(seq: int, is_key: bool, payload: bytes):
    import sotf_video_stream_test as svt
    return svt.VideoFrame(seq=seq, pts=seq, payload=payload, is_keyframe=is_key)


# -- prechecks ----------------------------------------------------------------
class RungPrecheckError(RuntimeError):
    def __init__(self, seam: str, msg: str):
        super().__init__("[%s] %s" % (seam, msg))
        self.seam = seam


def _precheck_tcp(addr: str, port: int, seam: str, what: str):
    try:
        with socket.create_connection((addr, port), timeout=3.0):
            return
    except Exception as e:
        raise RungPrecheckError(seam, "cannot reach %s at %s:%d (%s)" % (what, addr, port, e))


# -- rung definitions ---------------------------------------------------------
@dataclass
class Rung:
    name: str
    transport: str        # sim | loopback | remote
    tuples: str           # mock | real
    data: str             # generated | capture
    note: str


RUNGS: Dict[str, Rung] = {
    "R0": Rung("R0", "sim",      "mock", "generated", "sandbox, me alone"),
    "R1": Rung("R1", "loopback", "real", "generated", "real interfaces + real tuples, simulated data"),
    "R2": Rung("R2", "remote",   "real", "capture",   "real everything, harness-driven"),
    "R3": Rung("R3", "remote",   "real", "capture",   "final - driven by the real client+server app"),
}


# -- the rung runner ----------------------------------------------------------
def run_rung(rung: Rung, *, n_frames: int = 60, dbhost: str = "databasehost.frognet",
             remote_addr: Optional[str] = None, camera: Optional[str] = None,
             session_id: Optional[str] = None) -> int:
    session_id = session_id or ("sotf-%s-%d" % (rung.name.lower(), int(time.time())))
    print("== %s - %s ==" % (rung.name, rung.note))
    print("   TRANSPORT=%s  TUPLES=%s  DATA=%s  session=%s"
          % (rung.transport, rung.tuples, rung.data, session_id))

    # 1) DATA seam (precheck capture early so we don't stand up sockets for nothing)
    if rung.data == "capture":
        if not camera:
            print("   FAIL [DATA] capture rung needs --camera"); return 1
        frames = data_capture(n_frames, camera)
    else:
        frames = data_generated(n_frames)

    # 2) TUPLES seam
    backend = tuples_mock() if rung.tuples == "mock" else tuples_real(dbhost)
    sctl = M.TupleControl(backend, session_id, addr=(remote_addr or "10.0.0.9"))

    # 3) bootstrap: producer create-stream -> server creates + publishes conn-info
    M.TupleControl(backend, session_id).request_create(session_id=session_id, codec="vp8")
    server = M.MediaStreamServer(sctl, node="ffmpeg")
    server.tick()
    if not server.created:
        print("   FAIL [TUPLES] server did not create from the create-stream tuple"); return 1

    # 4) TRANSPORT seam: real FNWP-1 producer->server hop; decoded frames -> ingest
    link = _SotFLink(rung.transport, session_id, remote_addr=remote_addr)
    received_ok = {"n": 0, "exact": 0}
    sent_payloads: Dict[int, bytes] = {}

    def into_server(seq: int, is_key: bool, payload: bytes):
        # decoded-off-the-wire RAW lands in the ingestion queue (the real seam point)
        kind = M.AUDIO_PROTECTED if is_key else M.VIDEO_DROPPABLE
        server.ingest(M.MediaFrame(seq=seq, kind=kind, payload=payload))
        received_ok["n"] += 1
        if sent_payloads.get(seq) == payload:
            received_ok["exact"] += 1
    link.on_decoded = into_server
    link.open()

    # 5) consumer (rx) - its delivery is a REAL second FNWP-1 hop: the server
    #    re-encodes each pumped frame and ships it over a per-consumer SotF link;
    #    the consumer decodes off the wire and plays. RAW-in-FNWP-1 BOTH directions.
    consumer = M.MediaConsumer(M.TupleControl(backend, session_id),
                               open_rx=lambda a, p, sink: None, cid="player1")
    csink = consumer._meter(lambda fr: None)
    rx_exact = {"n": 0, "exact": 0}
    rx_sent: Dict[int, bytes] = {}
    rx_link = _SotFLink(rung.transport, session_id + ":rx", remote_addr=remote_addr)

    def to_player(seq: int, is_key: bool, payload: bytes):
        # frame decoded off the rx wire at the consumer -> play (metered)
        kind = M.AUDIO_PROTECTED if is_key else M.VIDEO_DROPPABLE
        csink(M.MediaFrame(seq=seq, kind=kind, payload=payload))
        rx_exact["n"] += 1
        if rx_sent.get(seq) == payload:
            rx_exact["exact"] += 1
    rx_link.on_decoded = to_player
    rx_link.open()

    producer = M.MediaProducer(sctl, open_tx=lambda a, p: (lambda fr: _tx(link, fr, sent_payloads, producer)),
                               node="producer1")
    producer.create_and_connect(codec="vp8")

    # 6) stream: producer -> (FNWP-1) -> server queue -> (FNWP-1) -> consumer
    for (seq, kind, payload) in frames:
        producer.send(M.MediaFrame(seq=seq, kind=kind, payload=payload))
        for got in server.queue.drain_all():
            server.metrics.on_sent_on()
            rx_sent[got.seq] = got.payload
            rx_link.send(got.seq, got.kind == M.AUDIO_PROTECTED, got.payload)
    for ep in (server, producer, consumer):
        ep.publish_metrics()
    link.close(); rx_link.close()

    # 7) verdict + metrics - BOTH hops must be byte-exact end to end
    dash = M.read_stream_dashboard(sctl, fresh_s=0)
    print(dash.render())
    n = received_ok["n"]; exact = received_ok["exact"]
    rn = rx_exact["n"]; rex = rx_exact["exact"]
    print("   tx hop (producer->server): %d/%d decoded, %d/%d byte-exact"
          % (n, len(frames), exact, len(frames)))
    print("   rx hop (server->consumer): %d/%d decoded, %d/%d byte-exact"
          % (rn, len(frames), rex, len(frames)))
    ok = (n == len(frames) and exact == len(frames)
          and rn == len(frames) and rex == len(frames))
    print("   %s: %s" % (rung.name, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _tx(link: "_SotFLink", fr, sent_payloads: Dict[int, bytes], producer=None):
    sent_payloads[fr.seq] = fr.payload
    outcome = link.send(fr.seq, fr.kind == M.AUDIO_PROTECTED, fr.payload)
    # account the compression this frame achieved on the wire (the improvement metric)
    if producer is not None and outcome is not None:
        wb = getattr(outcome, "wire_bytes", None)
        rb = getattr(outcome, "raw_payload_bytes", None)
        if wb is not None and rb is not None:
            producer.metrics.on_wire(raw=rb, wire=wb)
        if getattr(outcome, "success", True) is False:
            _rlog.error("tx_fault seq=%d kind=%s wire=%s raw=%s attempts=%s "
                        "(frame did not get a reply on the wire)",
                        fr.seq, fr.kind, wb, rb, getattr(outcome, "attempts", "?"))
    return outcome


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="media_stream_rungs",
                                 description="Progressively prove the SotF media pathway.")
    ap.add_argument("rung", choices=list(RUNGS.keys()))
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--dbhost", default="databasehost.frognet")
    ap.add_argument("--remote-addr", default=None, help="peer/daemon address for real transport")
    ap.add_argument("--camera", default=None, help="ffmpeg input for capture rungs")
    ap.add_argument("--session", default=None)
    args = ap.parse_args(argv)
    rung = RUNGS[args.rung]
    try:
        return run_rung(rung, n_frames=args.frames, dbhost=args.dbhost,
                        remote_addr=args.remote_addr, camera=args.camera,
                        session_id=args.session)
    except RungPrecheckError as e:
        print("   PRECHECK FAILED %s" % e)
        print("   -> the failing SEAM is %s; everything below it is already proven by the lower rung."
              % e.seam)
        return 2


if __name__ == "__main__":
    sys.exit(main())
