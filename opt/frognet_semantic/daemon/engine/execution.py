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
# daemon/engine/execution.py
#!/opt/frognet_semantic/venv/bin/python3
"""
daemon/engine/execution.py

Execute a semantic packet by:
  1) Decoding the semantic request (normalized packet; trailer already stripped)
  2) Resolving the explicit destination host (NO inference)
  3) Reconstructing HTTP (path/query/body/headers via templates)
  4) Executing either:
       - locally (Apache :8080) ONLY if dest resolves to a local interface
       - via local proxy (:80) preserving the explicit Host identity
  5) Encoding semantic reply using the reply template

ABSOLUTE INVARIANTS (ENFORCED):
  - dest_host MUST be explicit for non-control-plane requests
  - NO inference of destination host
  - NO fallback of unknown paths to databasehost.frognet
  - FAIL CLOSED on violations

DIFF ENCODING:
  - Incoming requests may have FLAG_DIFF set (only changed fields present)
  - We merge with stored request reference to reconstruct full request
  - Response is returned FULL-encoded (for stable hashing by session layer)
  - Response dynamic values are ALSO returned so session can diff-encode
    before sending on the wire
  - Request references are maintained per (peer_ip, opcode)
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
from typing import Any, Dict, List, Tuple, Optional

from core.codec import SemanticCodec
from core.tokens import TokenStore
from core.semcache_wire import wrap_resp_raw

# [DATA_CACHE_GEN_V2] Read-only materialization of Sensor + SensorData, proven
# current against a MySQL-maintained generation counter on every request. It
# falls through to Apache whenever currency cannot be proven or the filter
# cannot be expressed. See daemon/engine/data_cache.py.
# [NO_FALLBACK_V1] This was:
#     try:
#         from daemon.engine.data_cache import try_intercept as _cache_intercept
#         _HAS_CACHE = True
#     except ImportError:
#         _HAS_CACHE = False
#         def _cache_intercept(path, body_text): return None
#
# which is the exact shape the transition primer records as having already cost
# a day once: an ImportError guard on a FIRST-PARTY module turns the whole layer
# into a no-op that returns the "nothing to do" answer. [DATA_CACHE_GEN_V2]
# would silently stop existing, every request would go to Apache, the node would
# be slower for no visible reason, and nothing in the log would say the module
# had failed to load. The stub was indistinguishable from a genuine cache miss.
#
# A node either has its own modules or it is broken. There is no third state, so
# there is no flag: _HAS_CACHE is gone along with the guard, and every reference
# to it. If data_cache cannot be imported, the daemon does not start.
from daemon.engine.data_cache import try_intercept as _cache_intercept
from core.semcache_id import same_id as compute_same_id

from daemon.util.log import trace
from daemon.upstream.client import HttpClient
from daemon.engine.resolver import DestinationResolver
from daemon.templates.loader import TemplateLoader


def _serialize_headers(headers: Dict[str, str]) -> bytes:
    return json.dumps(headers, separators=(",", ":")).encode("utf-8")


SEM_HDR_V1_LEN = 8

# ====================================================================
# REQUEST REFERENCE CACHE (module-level, survives session reconnections)
# ====================================================================
# Keyed by (peer_ip, opcode) -> Optional[Dict[str, Any]] of last decoded values.
# None = never seen.  {} = seen with zero dynamic fields.
# When a diff-encoded request arrives with only N of M fields, the missing
# fields are filled from this reference.
#
# This MUST be module-level, not per-session, because the proxy may
# reconnect (new session) but keep sending diff-encoded requests that
# depend on the reference built up in the previous session.
_request_refs: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
_request_refs_lock = threading.RLock()


def _get_request_ref(peer_ip: str, opcode: int) -> Optional[Dict[str, Any]]:
    with _request_refs_lock:
        ref = _request_refs.get((peer_ip, opcode))
        return ref.copy() if ref is not None else None


def _set_request_ref(peer_ip: str, opcode: int, ref: Dict[str, Any]) -> None:
    with _request_refs_lock:
        _request_refs[(peer_ip, opcode)] = ref.copy()


def get_request_ref(peer_ip: str, opcode: int) -> Optional[Dict[str, Any]]:
    """Return a copy of the fully-merged request reference for (peer_ip, opcode), or None."""
    with _request_refs_lock:
        ref = _request_refs.get((peer_ip, opcode))
        return ref.copy() if ref is not None else None


def has_request_ref(peer_ip: str, opcode: int) -> bool:
    """Check whether a request reference exists for this (peer, opcode).

    Used by the session layer to decide whether a REQ_DIFF can be decoded.
    If False, the daemon must respond REQ_MISS so the proxy falls back
    to REQ_FULL.
    """
    with _request_refs_lock:
        return _request_refs.get((peer_ip, opcode)) is not None


class ExecutionEngine:
    def __init__(self, resolver=None, http_client=None) -> None:
        self.codec = SemanticCodec()
        self.loader = TemplateLoader()
        self.resolver = resolver if resolver is not None else DestinationResolver()
        self.http = http_client if http_client is not None else HttpClient()

    def execute(
        self,
        *,
        normalized_packet: bytes,
        peer_ip: str,
        origin_ip: str,
        dest_host: str,
    ) -> Tuple[bytes, int, int, Optional[int], Optional[List[Tuple[str, Any]]], Optional[Dict[str, str]]]:
        """
        Execute one semantic request packet and return:
          (semantic_reply_bytes, upstream_http_status, reply_len_bytes,
           opcode, dynamic_vals, type_map)

        The last three are returned so the session layer can diff-encode
        the response before sending on the wire. They are None on error paths.

        dest_host MUST be provided by the packet trailer (session extracts it).
        """
        if not normalized_packet or len(normalized_packet) < SEM_HDR_V1_LEN:
            rep = self.codec.encode_error_reply(400, "short semantic packet")
            return rep, 400, len(rep), None, None, None

        try:
            version, flags, opcode, nfields = struct.unpack("<BBIH", normalized_packet[:SEM_HDR_V1_LEN])
        except Exception as e:
            rep = self.codec.encode_error_reply(400, f"bad header: {e!r}")
            return rep, 400, len(rep), None, None, None

        # Lookup request+reply templates by opcode
        req_tpl, resp_tpl = self.loader.lookup_by_opcode(opcode)
        if not req_tpl or not resp_tpl:
            rep = self.codec.encode_error_reply(503, f"no templates for opcode={opcode}")
            return rep, 503, len(rep), opcode, None, None

        # Decode request values, merging with reference if FLAG_DIFF is set
        try:
            req_ref = _get_request_ref(peer_ip, opcode)
            url_vals, json_vals = self.codec.decode_request(
                normalized_packet,
                req_tpl,
                TokenStore(req_tpl.tokens),
                reference=req_ref if req_ref else None,
            )
            # [REQUEST_REF_COMPLETE_V1] Every declared url_query_key must have a
            # value after the merge. Proxy and daemon hold separate references for
            # (peer_ip, opcode); when they diverge the proxy omits a field it thinks
            # unchanged, the daemon fills it from a reference that never had it, and
            # build_url() silently drops it -- executing a DIFFERENT query with a 200.
            # Measured: fresh_s=30 and fresh_s=99999 both returned all 28 rows.
            #
            # This runs BEFORE build_url() and therefore before the data_cache
            # intercept, which keys on the built path: an incomplete reference would
            # otherwise be served a cached answer to the wrong question.
            _url_keys = getattr(req_tpl, "url_query_keys", []) or []
            _have = {k for k, v in url_vals if v is not None}
            _missing = [k for k in _url_keys if k not in _have]
            if _missing:
                # Clear it: it cannot satisfy this template, and keeping it would
                # fail every subsequent request the same way.
                _set_request_ref(peer_ip, opcode, {})
                # [REF_INCOMPLETE_IS_A_REQ_MISS_V1] Signal this with REQ_MISS,
                # not a 409.
                #
                # This used to return `encode_error_reply(409, "... resend as
                # FULL")`. The instruction was correct and nobody could obey it:
                # the proxy has exactly one branch for "clear your reference and
                # resend full", and it keys on OP_REQ_MISS
                # (transport_semantic.py:2306 and :2475). A 409 is not a
                # REQ_MISS, so it fell through to "process response" and was
                # relayed to the HTTP caller as a status. urllib turns 4xx into
                # HTTPError, and on the Windows Communicator that killed
                # App.__init__ at the first _values_raw() call:
                #     urllib.error.HTTPError: HTTP Error 409: Conflict
                # Repeatedly, because we cleared OUR reference while the proxy
                # kept its own - so the two stayed diverged and the next request
                # arrived incomplete again.
                #
                # REQ_MISS already means precisely this and is already handled on
                # both sides, with a retry budget. Two in-band signals for one
                # condition, and the second one was invented for this single call
                # site with the other end left unwritten. One signal, one handler.
                #
                # The FRAME is built by the session, which owns wire framing and
                # holds req_hash; this layer owns execution and only names the
                # condition. Same shape as the existing [RAW_SIGNAL_V1] hand-off.
                trace(f"[REF_INCOMPLETE_IS_A_REQ_MISS_V1] opcode={opcode} "
                      f"peer={peer_ip} missing={_missing} declared={_url_keys} "
                      f"- ref cleared, REQ_MISS (was: 409, which nothing obeyed)")
                return (b"", 200,
                        {"req_miss": True, "opcode": opcode,
                         "missing": list(_missing), "declared": list(_url_keys)},
                        opcode, None, None)

            # Update request reference with all decoded values
            new_req_ref = {}
            for k, v in url_vals:
                new_req_ref[k] = v
            for k, v in json_vals:
                new_req_ref[k] = v
            _set_request_ref(peer_ip, opcode, new_req_ref)

        except Exception as e:
            trace(f"[DAEMON DECODE ERROR] opcode={opcode} err={e!r}")
            rep = self.codec.encode_error_reply(400, f"decode error: {e}")
            return rep, 400, len(rep), opcode, None, None

        # Resolve destination STRICTLY from explicit dest_host (except control-plane paths)
        try:
            dest = self.resolver.resolve(
                req_tpl,
                peer_ip=peer_ip,
                origin_ip=origin_ip,
                dest_host=(dest_host or "").strip(),
            )
        except Exception as e:
            trace(f"[DAEMON RESOLVE ERROR] opcode={opcode} err={e!r}")
            rep = self.codec.encode_error_reply(502, f"resolve error: {e}")
            return rep, 502, len(rep), opcode, None, None

        # Rebuild HTTP request
        try:
            path = req_tpl.build_url(url_vals)
            body_text = req_tpl.rebuild_body(json_vals)
            add_hdrs = req_tpl.build_headers(body_text)

            # Preserve any per-template headers; Host will be set by HttpClient
            headers = {}
            if isinstance(add_hdrs, dict):
                headers.update({str(k): str(v) for k, v in add_hdrs.items() if k})

        except Exception as e:
            trace(f"[DAEMON REBUILD ERROR] opcode={opcode} err={e!r}")
            rep = self.codec.encode_error_reply(502, f"rebuild error: {e}")
            return rep, 502, len(rep), opcode, None, None

        # -- Read materialization --
        # [DATA_CACHE_GEN_V2] Reads only, and only when RAM is proven equal to
        # disk for THIS request. Anything else returns None and goes to Apache.
        # [NO_FALLBACK_V1] The `if _HAS_CACHE:` gate is gone with the import
        # guard that set it. try_intercept() returning None is the only "no"
        # this layer has, and it means "currency could not be proven" - a fact
        # about this request. It never meant "the module is missing", and the
        # two must not share a branch.
        _cached = _cache_intercept(path, body_text)
        if _cached is not None:
            try:
                resp_vals = resp_tpl.extract_dynamic(_cached)
                resp_pairs = [(k, v) for k, v in (resp_vals or [])]
                sem_reply = self.codec.encode_reply(
                    opcode=opcode,
                    dynamic_vals=resp_pairs,
                    type_map=resp_tpl.type_map or {},
                    tokens=TokenStore(resp_tpl.tokens),
                    compress=True,
                )
                trace(f"[DAEMON EXTRACT] opcode={opcode} status=200 src=data_cache "
                      f"resp_text_len={len(_cached)} resp_text={_cached[:120]!r}")
                return sem_reply, 200, len(sem_reply), opcode, resp_pairs, resp_tpl.type_map
            except Exception:
                # [RAW_SIGNAL_V1] extraction failed - hand the body back raw
                # rather than wrapping an FNW1 frame inside another one.
                raw_info = {
                    "raw":           True,
                    "status":        200,
                    "headers_bytes": b'{"Content-Type":"application/json"}',
                    "body":          _cached.encode("utf-8"),
                }
                return b"", 200, raw_info, opcode, None, None

        # Execute HTTP via local loopback (Apache:8080 if local, else proxy:80)
        try:
            status, resp_headers, resp_body_bytes, resp_text = self.http.request(
                peer_ip=peer_ip,
                upstream_port=dest.upstream_port,
                method=str(req_tpl.method or "GET"),
                path=str(path or "/"),
                body_text=str(body_text or ""),
                headers=headers,
                host_header=dest.host_header,
            )
        except Exception as e:
            trace(f"[DAEMON HTTP ERROR] opcode={opcode} err={e!r}")
            rep = self.codec.encode_error_reply(502, f"http exec error: {e!r}")
            return rep, 502, len(rep), opcode, None, None

        # Encode semantic reply - FULL encoding for stable hashing
        # Session layer uses sha256(sem_reply) to detect SAME vs DIFF.
        # We also return the dynamic values so session can diff-encode
        # before sending on the wire.
        try:
            trace(f"[DAEMON EXTRACT] opcode={opcode} status={status} resp_text_len={len(resp_text or chr(0))} resp_text={repr((resp_text or chr(0))[:120])}")
            dyn = resp_tpl.extract_dynamic(resp_text or "")

            # If extraction returns empty on a 200, the response template has
            # no extractable fields (e.g. HTML with no id-tagged elements, or
            # a mode mismatch).  Fall back to raw-body SAME/DIFF: hash the body
            # bytes for SAME detection, send RESP_RAW on the wire.
            # This is far better than poisoning with 502 which causes permanent
            # re-bootstrap storms.
            if not dyn:
                if status == 200:
                    frag_fields = resp_tpl.fragment.get("fields", []) if hasattr(resp_tpl, "fragment") else []
                    frag_mode   = resp_tpl.fragment.get("mode", "?") if hasattr(resp_tpl, "fragment") else "?"
                    trace(f"[DAEMON EXTRACT EMPTY] opcode={opcode} mode={frag_mode} fields={frag_fields} resp_text_len={len(resp_text or '')} - raw body fallback")
                    # [RAW_SIGNAL_V1] Return raw materials in the 3rd slot
                    # (formerly an unused length int).  Session layer wraps
                    # via wrap_resp_raw, caches via upsert_raw.  Do NOT wrap
                    # here - wrapping then re-wrapping in session.py was
                    # the version-70 bug.
                    raw_info = {
                        "raw":           True,
                        "status":        int(status),
                        "headers_bytes": _serialize_headers(resp_headers if isinstance(resp_headers, dict) else {}),
                        "body":          resp_body_bytes,
                    }
                    return b"", int(status), raw_info, opcode, None, None
                # Non-200 (error responses): fallback is acceptable
                dyn = [("value", [])]

            sem_reply = self.codec.encode_reply(
                opcode=int(resp_tpl.opcode),
                dynamic_vals=dyn,
                type_map=resp_tpl.type_map or {},
                tokens=TokenStore(resp_tpl.tokens),
            )
            return (sem_reply, int(status), len(sem_reply),
                    opcode, dyn, resp_tpl.type_map or {})

        except Exception as e:
            trace(f"[DAEMON REPLY ERROR] opcode={opcode} err={e!r}")
            rep = self.codec.encode_error_reply(502, f"reply encode error: {e!r}")
            return rep, 502, len(rep), opcode, None, None

# ---- DEPLOYMENT MARKER ----
print("[execution] BUILD=2026-03-24-v1", flush=True)
