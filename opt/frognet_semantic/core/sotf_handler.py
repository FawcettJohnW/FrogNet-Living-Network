#!/opt/frognet_semantic/venv/bin/python3
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
# core/sotf_handler.py
#
# SotF-ACP media codex - installable alongside the json/xml/html/text/raw
# handlers in core/. Matches the production FormatHandler interface used by
# core/json_handler.py and core/text_handler.py:
#
#   learn_request_template(body_text: str)  -> Dict[str, Any]
#   learn_reply_template(body_text: str)    -> Dict[str, Any]
#   extract_request_dynamic(body_text, fragment) -> List[Tuple[str, Any]]
#   extract_reply_dynamic(body_text, fragment)   -> List[Tuple[str, Any]]
#   rebuild_reply(fragment, values)         -> str
#
# Wire body format
# ----------------
# The handler claims HTTP bodies whose JSON top-level dict carries
# the marker `"_sotf": 1`. This lets the existing sniffer-driven dispatch
# in format_registry.py route media frames to this handler without any
# Content-Type trust.
#
# Example body:
#   {"_sotf":1,"session_id":"sotf-abc123","codec":"opus","sr":48000,
#    "layout":"stereo","level_idx":4,"seq":42,"payload":"<base64 bytes>"}
#
# The five session-scoped fields (session_id / codec / sr / layout /
# level_idx) become the per-session template. The two per-frame fields
# (seq / payload) carry the actual media. After the first REQ_FULL
# establishes the session template at the daemon, subsequent frames diff
# against the per-peer reference so only seq + payload travel as REQ_DIFF.
# Identical content collapses to REQ_REPEAT (25 wire bytes; the codex
# floor).
#
# Payload wire type (FINDING-1 RESOLVED)
# --------------------------------------
# core/codec.py's _decode_fieldblock now returns native bytes for TYPE_RAW
# (the [TYPERAW_NATIVE_BYTES] branch - TYPE_RAW no longer shares the
# .decode("utf-8","replace") path with TYPE_STR). So `payload` rides as
# TYPE_RAW native bytes on the inter-Host wire - no base64/TYPE_STR ~33%
# tax. The JSON envelope still carries payload as a base64 string (JSON
# can't hold raw bytes); _coerce_payload_for_wire decodes that to bytes for
# the codec, and _coerce_payload_from_wire returns bytes off the wire.
# NOTE: every node in a media session must run this (raw) handler - a peer
# still on the old base64 TYPE_STR handler will mismatch the payload field
# in reference/diff.

from __future__ import annotations

import base64
import json
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# -- live-AV frame codec (subsumed from sotf_media_codex) ----------------------
# The hot DATA plane: raw AV frames, fire-and-forget, NOT through the convergence
# codec - live AV never repeats tick to tick, so diffing it is pure cost. seq +
# is_key + audio + video interleaved behind one header. The SAME SotF handler that
# converges the control envelope owns this: one codec, invoked like every handler.
_FRAME_HDR = struct.Struct("!IBII")   # seq, is_key, audio_len, video_len


def pack_frame(seq: int, is_key: bool, audio: bytes, video: bytes) -> bytes:
    return _FRAME_HDR.pack(seq, 1 if is_key else 0, len(audio), len(video)) + audio + video


def unpack_frame(buf: bytes) -> Tuple[int, bool, bytes, bytes]:
    seq, key, al, vl = _FRAME_HDR.unpack_from(buf, 0)
    off = _FRAME_HDR.size
    return seq, bool(key), buf[off:off + al], buf[off + al:off + al + vl]


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class SotFMediaHandler(UnRESTHandler):
    """Codex for SotF-ACP media frames. Installable alongside the other
    core/ FormatHandlers. See module docstring for the wire format."""

    # Marker the sniffer looks for to route a body to this handler
    MARKER_FIELD = "_sotf"
    MARKER_VALUE = 1

    SESSION_FIELDS = ("session_id", "codec", "sr", "layout", "level_idx")
    FRAME_FIELDS = ("seq", "payload")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return "sotf_media"

    def _parse(self, body_text: Union[str, bytes, None]) -> Dict[str, Any]:
        """Defensive JSON parse. Returns {} on any failure - callers fall
        back to baseline values from the fragment."""
        if not body_text:
            return {}
        if isinstance(body_text, (bytes, bytearray)):
            try:
                body_text = bytes(body_text).decode("utf-8", "replace")
            except Exception:
                return {}
        try:
            d = json.loads(body_text)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _coerce_payload_for_wire(v: Any) -> bytes:
        """Return NATIVE BYTES for the codec wire (TYPE_RAW). The JSON envelope
        carries payload as a base64 string (JSON can't hold raw bytes), so
        decode it here; the constrained inter-Host wire then carries the raw
        binary, not the ~33%-inflated base64. Already-bytes pass through;
        empty/None -> b''. A non-base64 string is encoded as UTF-8 bytes so
        nothing is silently dropped."""
        if v is None or v == "":
            return b""
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, str):
            try:
                return base64.b64decode(v.encode("ascii"), validate=True)
            except Exception:
                return v.encode("utf-8", "replace")
        return b""

    @staticmethod
    def _coerce_payload_from_wire(v: Any) -> Union[bytes, str]:
        """Native TYPE_RAW arrives as bytes -> pass through. A base64 str (legacy
        envelope path) decodes to bytes; a non-base64 str is returned as-is for
        the caller to decide. Empty/None passes through as b''."""
        if v is None or v == "":
            return b""
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, str):
            try:
                return base64.b64decode(v.encode("ascii"), validate=True)
            except Exception:
                return v
        return b""

    # ------------------------------------------------------------------
    # Template learning - request and reply use the same shape
    # ------------------------------------------------------------------

    def _build_template(self, body_text: str) -> Dict[str, Any]:
        d = self._parse(body_text)
        return {
            "mode": self.mode,
            "fields": list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS),
            "field_order": list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS),
            "type_map": {
                "session_id": "string",
                "codec":      "string",
                "sr":         "int",
                "layout":     "string",
                "level_idx":  "int",
                "seq":        "int",
                # codec TYPE_RAW is native-bytes (codec.py [TYPERAW_NATIVE_BYTES]):
                # payload rides raw on the wire, no base64/TYPE_STR ~33% tax.
                "payload":    "raw",
            },
            "baseline": {
                "session_id": d.get("session_id", ""),
                "codec":      d.get("codec", "opus"),
                "sr":         int(d.get("sr", 48000) or 0),
                "layout":     d.get("layout", "stereo"),
                "level_idx":  int(d.get("level_idx", 4) or 0),
                "seq":        0,
                "payload":    b"",
            },
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        return self._build_template(body_text)

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        return self._build_template(body_text)

    # ------------------------------------------------------------------
    # Dynamic extraction - request and reply share the body shape
    # ------------------------------------------------------------------

    def _extract(self, body_text: str,
                  fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        d = self._parse(body_text)
        field_order: List[str] = fragment.get("field_order") or (
            list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS))
        baseline: Dict[str, Any] = fragment.get("baseline") or {}
        out: List[Tuple[str, Any]] = []
        for path in field_order:
            v = d[path] if path in d else baseline.get(path)
            if path == "payload":
                v = self._coerce_payload_for_wire(v)
            out.append((path, v))
        return out

    def extract_request_dynamic(self, body_text: str,
                                 fragment: Dict[str, Any]
                                 ) -> List[Tuple[str, Any]]:
        return self._extract(body_text, fragment)

    def extract_reply_dynamic(self, body_text: str,
                               fragment: Dict[str, Any]
                               ) -> List[Tuple[str, Any]]:
        return self._extract(body_text, fragment)

    # ------------------------------------------------------------------
    # Rebuild - returns the reconstituted JSON envelope as a string,
    # matching the production rebuild_reply signature (-> str)
    # ------------------------------------------------------------------

    def rebuild_reply(self, fragment: Dict[str, Any],
                      values: List[Any]) -> str:
        """Reconstruct the on-the-wire JSON envelope from the decoded
        values list. payload comes off the codec as native bytes (TYPE_RAW);
        re-encode it to a base64 string so the rebuilt body is valid JSON.
        The consumer (e.g. the MediaBridge) gets the bytes back via
        decode_payload(rebuilt_body)."""
        out: Dict[str, Any] = {self.MARKER_FIELD: self.MARKER_VALUE}

        # values can be List[Tuple[str, Any]] (preferred) or List[Any] in
        # field_order - handle both.
        if values and isinstance(values[0], (tuple, list)) and len(values[0]) == 2:
            for k, v in values:
                out[k] = v
        else:
            field_order = fragment.get("field_order") or (
                list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS))
            for i, v in enumerate(values):
                if i < len(field_order):
                    out[field_order[i]] = v

        # Fill any missing keys from baseline so the rebuilt body is a
        # complete envelope.
        baseline = fragment.get("baseline") or {}
        for k in (list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS)):
            out.setdefault(k, baseline.get(k))

        # payload is bytes on the wire - JSON can't hold bytes, so base64 it
        # back into the envelope string (the inverse of _coerce_payload_for_wire).
        pv = out.get("payload")
        if isinstance(pv, (bytes, bytearray)):
            out["payload"] = base64.b64encode(bytes(pv)).decode("ascii")
        elif pv is None:
            out["payload"] = ""

        return json.dumps(out, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Convenience helpers for callers that want the bytes back from a
    # rebuilt envelope (MediaBridge, video sink, etc.)
    # ------------------------------------------------------------------

    def decode_payload(self, rebuilt_body: str) -> bytes:
        """Given a body string produced by rebuild_reply, return the
        raw payload bytes (base64-decoded)."""
        d = self._parse(rebuilt_body)
        return bytes(self._coerce_payload_from_wire(d.get("payload", "")))

    # ------------------------------------------------------------------
    # Live-AV codec slot - the handler IS the media-frame codec (subsumed
    # from sotf_media_codex). The hot data plane: raw frames, no convergence.
    # ------------------------------------------------------------------

    @staticmethod
    def pack_frame(seq: int, is_key: bool, audio: bytes, video: bytes) -> bytes:
        """Pack one live AV unit raw (no convergence) for the video vector."""
        return pack_frame(seq, is_key, audio, video)

    @staticmethod
    def unpack_frame(buf: bytes) -> Tuple[int, bool, bytes, bytes]:
        return unpack_frame(buf)

    def open_stream(self, transient, perm, session: str, who: str,
                    send_frame: Callable[[bytes], None], role: Optional[str] = None):
        """Factory for one participant's CONTROL-plane media codec - the cold plane:
        the LiveStream control bag that converges over tuple space (store/read/ffmpeg
        options). Subsumed from sotf_media_codex; the UnREST substrate is imported
        lazily (in the guarded block below) so this core handler stays import-light in
        role-only contexts. Raises if the media substrate isn't on path."""
        if not _HAVE_MEDIA_CODEX:
            raise RuntimeError("SotF control-plane codec needs substrate/working_memory "
                               "on path (mediahost/communicator context)")
        if role is None:
            role = sotf_metrics.ROLE_SENDER
        return SotFMediaCodex(transient, perm, session, who, send_frame, role)


    # ------------------------------------------------------------------
    # Role evaluation - the media-host election CRITERIA live on the handler
    # that owns the media wire format. PURE: given the LAN candidate list
    # (already gathered from capability memory by the election machinery),
    # return the candidate that best meets this role's requirements. No I/O,
    # no probes, no spawning - a function of its input.
    # ------------------------------------------------------------------
    ROLE_NAME = "mediahost"
    CANDIDATE_TYPE = "MediaCandidate"
    DEFAULT_PORT = 9000

    @staticmethod
    def _ip_to_int(ip):
        try:
            a, b, c, d = (int(x) for x in ip.split("."))
            return (a << 24) | (b << 16) | (c << 8) | d
        except Exception:
            return 0

    def score(self, cand):
        """Media-host fitness from MEASURED signal. ffmpeg+libvpx is the hard gate.
        Software VP8 is CPU-bound, so the ceiling is the measured cross-arch CPU
        benchmark (cpu_bench_total) - a 1.5 GHz ARM and 1.5 GHz x86 differ greatly
        and nominal MHz lies. A real hardware encoder (hw_encoder set AND, for
        vaapi, a confirmed gpu_render_node) is a large bonus. Tiny RAM, thermal
        throttle, and live load (cand['_perf']) discount."""
        if not (cand.get("ffmpeg") and cand.get("libvpx")):
            return -1.0
        cores = max(1, int(cand.get("cores", 1)))

        # ceiling: measured benchmark if present, else cores*clock proxy
        bench = float(cand.get("cpu_bench_total", 0.0))
        ceiling = (bench / 1000.0) if bench > 0 else (cores * max(1, int(cand.get("cpu_mhz", 1000))) / 1000.0)

        # hardware encoder bonus - confirm a real device, not just an ffmpeg flag
        hw = cand.get("hw_encoder", "none")
        if hw not in ("none", "", None):
            if hw == "vaapi" and not cand.get("gpu_render_node", False):
                pass                                 # claimed vaapi but no render node
            else:
                ceiling *= 2.5
        if int(cand.get("mem_total_kb", 0)) / (1024 * 1024) < 0.5:
            ceiling *= 0.5

        # [MEDIAHOST_STATIC_RANK_V1] mediahost is a DURABLE role assignment, not a
        # per-frame scheduler: transient load (loadavg) and instantaneous temperature
        # must not move the election - the same principle DBHOST_NO_LOAD applies to the
        # database role. Scoring on them made a node's own tuple non-static write-to-write
        # (an 11x score swing on load+temp), so the elected mediahost thrashed. Runtime
        # load and thermals are absorbed by the SotF adaptive ladder on the media plane,
        # NOT by re-electing the host. Rank on static capability only (measured CPU bench,
        # hardware encoder, installed RAM gate).
        return ceiling

    def evaluate(self, hosts_list, lan_list):
        """Media host is elected over the LAN-only lan_list - A/V is bandwidth-heavy
        and must stay local, off the tunnel, so the WAN-inclusive hosts_list (which
        carries the remote hosts reached over the wg overlay, dev wgx) is accepted for
        signature uniformity and ignored here. Pure: score each LAN candidate, drop
        ineligible, pick best, tiebreak by highest IP. The handler chooses from what it
        is handed; it does not gather."""
        best = None
        for cand in lan_list or []:
            ip = cand.get("lan_ip", "")
            if not ip:
                continue
            sc = self.score(cand)
            if sc < 0:
                continue
            key = (sc, self._ip_to_int(ip))
            if best is None or key > best[0]:
                best = (key, cand)
        return best[1] if best else None


# --------------------------------------------------------------------------
# Sniffer hook - drop into format_registry.sniff_body_mode().
# Provided here as a free function so callers can import it directly:
#
#   from .sotf_handler import looks_like_sotf
#
# Returns True iff the body is JSON with `"_sotf": 1` at the top level.
# Cheap pre-check on the first 200 bytes avoids a full parse on non-SotF
# bodies.
# --------------------------------------------------------------------------

def looks_like_sotf(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if not t.startswith("{"):
        return False
    if '"_sotf"' not in t[:200]:
        return False
    try:
        d = json.loads(t)
        return isinstance(d, dict) and d.get(SotFMediaHandler.MARKER_FIELD) == SotFMediaHandler.MARKER_VALUE
    except Exception:
        return False


# ==========================================================================
# Subsumed control plane - per-participant LiveStream codec over tuple space.
# Cold plane: mostly-stable structured control (conn-info, ffmpeg options,
# status) that converges SAME/DIFF in the shared transient. Needs the UnREST
# substrate (WorkingMemory/Codex/Freshness) + metrics, which ship with the media
# bundle. GUARDED so this core handler imports cleanly in role-only contexts
# (discovery election) where substrate is absent: there only the AV codec, the
# envelope codec, and the election criteria above are needed. The singleton
# handler hands out per-stream instances via SotFMediaHandler.open_stream().
# ==========================================================================
try:
    from substrate import Codex, Freshness
    from working_memory import WorkingMemory, TransientStore, PermStore
    import sotf_metrics
    _HAVE_MEDIA_CODEX = True
except Exception:
    _HAVE_MEDIA_CODEX = False

if _HAVE_MEDIA_CODEX:
    LIVESTREAM_TYPE = "LiveStream"
    CONTROL_FIELDS = ["conn_info", "ffmpeg_options", "status"]
    CONTROL_FRESHNESS = {
        "conn_info":      Freshness.RESIDENT_ONCE,   # set once -> SAME forever after
        "ffmpeg_options": Freshness.LATEST_ONLY,     # the encoder options the producer runs
        "status":         Freshness.LATEST_ONLY,
    }

    def _flag_key(session: str, who: str) -> str:
        return f"FfmpegFlag.{session}.{who}"

    def _ls_key(session: str, who: str) -> str:
        return f"{LIVESTREAM_TYPE}.{session}.{who}"

    class SotFMediaCodex(WorkingMemory):
        """One participant's media codex: owns its LiveStream control instance (convergent
        JSON) in the transient, ships AV frames raw out the video vector."""

        def __init__(self, transient: TransientStore, perm: PermStore,
                     session: str, who: str,
                     send_frame: Callable[[bytes], None],
                     role: str = sotf_metrics.ROLE_SENDER):
            super().__init__(transient, perm)
            self.session = session
            self.who = who
            self.send_frame = send_frame
            self._ctl = Codex(None, "POST", f"sotf/ctl/{session}",
                              CONTROL_FIELDS, CONTROL_FRESHNESS, mode="json")
            self._ctl_ref: Optional[Dict[str, Any]] = None
            self.metrics = sotf_metrics.StreamMetrics(session, who, role)

        def _owned_keys(self):
            return [_ls_key(self.session, self.who)]

        def _consistency_check(self) -> None:
            key = _ls_key(self.session, self.who)
            st = self.p.load(key)
            if st is not None:
                st["type"] = LIVESTREAM_TYPE; st["session"] = self.session; st["who"] = self.who
                self.p.save(key, st)

        # ===== DATA PLANE - raw frames, video vector, fire-and-forget. No codec. =====
        def send_av(self, seq: int, is_key: bool, audio: bytes, video: bytes) -> None:
            """Ship one AV unit RAW out the video vector. No convergence - live AV never
            repeats, so diffing it is pure cost. ffmpeg produced these bytes; we move them."""
            self.send_frame(pack_frame(seq, is_key, audio, video))
            self.metrics.on_sent()

        def recv_av(self, buf: bytes) -> Tuple[int, bool, bytes, bytes]:
            seq, key, audio, video = unpack_frame(buf)
            self.metrics.on_received()
            return seq, key, audio, video

        # ===== CONTROL PLANE - JIT store/read of the LiveStream bag. SAME/DIFF here. =====
        def store_control(self, **bag: Any) -> str:
            """At the point you CALCULATE a control value, store it. Returns the convergence
            kind (full/diff/same); an unchanged store is SAME and counts ~0 wire bytes."""
            key = _ls_key(self.session, self.who)
            st = self._live(key) or {"type": LIVESTREAM_TYPE,
                                     "session": self.session, "who": self.who}
            st.update(bag)
            ctl_vals = {f: st.get(f) for f in CONTROL_FIELDS}
            frame, new_ref, kind = self._ctl.encode(ctl_vals, self._ctl_ref)
            self._ctl_ref = new_ref
            raw = len(json.dumps(ctl_vals, separators=(",", ":")).encode())
            self.metrics.on_ctl(kind=kind, wire=len(frame), raw=raw)
            self._commit(key, st)
            return kind

        def read_control(self, who: str) -> Optional[Dict[str, Any]]:
            return self._live(_ls_key(self.session, who))

        def read_livestreams(self) -> List[Dict[str, Any]]:
            getter = getattr(self.t, "all_by_type", None)
            if getter is None:
                return []
            return [v for v in getter(LIVESTREAM_TYPE) if isinstance(v, dict)]

        # ---- ffmpeg options as shared memory: the producer reads what to run ----
        def set_ffmpeg_options(self, options: Dict[str, Any], for_who: Optional[str] = None) -> str:
            """Write the ffmpeg options a producer should run, into ITS LiveStream control,
            and trip its flag. Scale-to-fit, EITHER DIRECTION: lower bitrate when a link is
            drowning, higher when it recovers - same path, the producer just runs whatever
            is in the bag. Called by whoever decides policy (a consumer, the server, the
            producer itself); for_who defaults to me, a consumer passes the producer's id.
            The producer doesn't know or care who called this. (One-writer-per-tuple is the
            caller's contract; this code does not guard it - misuse is meant to crash.)"""
            target = for_who or self.who
            key = _ls_key(self.session, target)
            st = self._live(key) or {"type": LIVESTREAM_TYPE,
                                     "session": self.session, "who": target}
            st["ffmpeg_options"] = options
            ctl_vals = {fld: st.get(fld) for fld in CONTROL_FIELDS}
            frame, new_ref, kind = self._ctl.encode(ctl_vals, self._ctl_ref)
            self._ctl_ref = new_ref
            raw = len(json.dumps(ctl_vals, separators=(",", ":")).encode())
            self.metrics.on_ctl(kind=kind, wire=len(frame), raw=raw)
            self._commit(key, st)
            self.t.upsert(_flag_key(self.session, target), {"type": "FfmpegFlag",
                          "session": self.session, "who": target, "changed": True})
            return kind

        def conditions_changed(self) -> bool:
            """JIT, every loop: cheap boolean read. SAME (weightless) until someone trips it.
            This is the producer's only steady-state control cost."""
            fl = self.t.get(_flag_key(self.session, self.who))
            return bool(fl and fl.get("changed"))

        def take_ffmpeg_options(self) -> Optional[Dict[str, Any]]:
            """Producer: flag was tripped -> read the options bag (pay the cost ONCE),
            clear the flag, return what ffmpeg should now run. Write-agnostic: never reads
            who set it."""
            st = self._live(_ls_key(self.session, self.who)) or {}
            self.t.upsert(_flag_key(self.session, self.who), {"type": "FfmpegFlag",
                          "session": self.session, "who": self.who, "changed": False})
            return st.get("ffmpeg_options")

        def hostReset(self) -> Dict[str, Any]:
            rep = super().hostReset()
            self._ctl_ref = None
            rep["control_refulled"] = True
            return rep
