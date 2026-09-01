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
"""
proxy/proxy_main.py - FrogNet Semantic Proxy (SAME/DIFF everywhere)

All requests now go through daemon:9009 for SAME/DIFF caching:
- Semantic (templated) requests: use semantic encoding
- Raw HTTP requests: pass through with raw caching

Hop-by-hop forwarding and decision logic preserved.

v3.3 - Removed per-request shell-fork emitters (linkstate_emit, linkquality_emit).
        Topology and link quality are now emitted periodically by proxy_metrics.py
        from in-process state.  No subprocess.Popen on the hot path.
"""

from __future__ import annotations
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event

import sys
sys.dont_write_bytecode = True

import os
import threading
import time
import traceback
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, Set

from proxy.constants import (
    DEBUG,
    DAEMON_PORT_DEFAULT,
    REFLECT_PORT_DEFAULT,
    DEMO_RUN_ID_HEADER,
    TELEMETRY_ENABLED_DEFAULT,
    TELEMETRY_INTERVAL_DEFAULT,
    MAX_ACTIVE_REQUESTS,
    DecisionPath,
    debug,
    decide_semantic_mode,
)

from proxy.netutil import (
    DEFAULT_UPSTREAM_IFACES,
    target_host_and_ip,
    is_local_ip,
    NetutilFailure,
)

from proxy.decision import decide_path_for_target
from proxy.origin import is_control_plane_path
from proxy import local_read_cache  # [LOCAL_READ_CACHE_V1]
from proxy.templates import canonical_semantic_key, learn_templates_from_real, get_template_store, fail_closed_template_missing
from proxy.transport_real import real_upstream_local, real_upstream_remote_80, MarkedHTTPConnection
from proxy.transport_semantic import handle_request  # unified handler
from proxy.proxy_metrics import (start_flusher, bump_real, observe_peer,
                                 live_endpoint_stats)
from core.game_origin import GameOrigin, looks_like_game
GAME_ORIGIN = GameOrigin()


_STRIP_HDRS = {"transfer-encoding", "connection", "content-length", "server", "date"}

_ACTIVE = threading.BoundedSemaphore(MAX_ACTIVE_REQUESTS)
_REMOTE_APACHE_SLOTS = threading.BoundedSemaphore(
    int(os.environ.get("FROGNET_REMOTE_APACHE_SLOTS", "64"))
)

# ------------------------------------------------------------
# Emit diagnostics (file-based, no subprocess)
# ------------------------------------------------------------
_EMIT_DIAG_PATH = "/tmp/frognet_emit_diag.tsv"


def _emit_diag_enabled() -> bool:
    trace_enter('proxy_main._emit_diag_enabled')
    v = os.environ.get("FROGNET_EMIT_DIAG", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _emit_diag_line(fields: list[str]) -> None:
    trace_enter('proxy_main._emit_diag_line', fields=repr(fields))
    if not _emit_diag_enabled():
        return
    try:
        line = "\t".join([str(x).replace("\n", "\\n") for x in fields])
        with open(_EMIT_DIAG_PATH, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass


def _emit_diag(tag: str, ctx: Dict[str, Any], *, adj_ip: str, kind: str, dev: str, via: str, reason: str, extra: str = "") -> None:
    trace_enter('proxy_main._emit_diag', tag=repr(tag), ctx=repr(ctx))
    if not _emit_diag_enabled():
        return
    _emit_diag_line([
        str(int(time.time())),
        tag,
        str(ctx.get("client_ip") or ""),
        str(ctx.get("target_ip") or ""),
        str(adj_ip or ""),
        str(kind or ""),
        str(dev or ""),
        str(via or ""),
        str(reason or ""),
        str(ctx.get("method") or ""),
        str(ctx.get("raw_path") or ""),
        str(ctx.get("semantic_path") or ""),
        str(extra or ""),
    ])


def _relay_headers(dst: BaseHTTPRequestHandler, src_headers: Dict[str, str], body_len: int) -> None:
    trace_enter('proxy_main._relay_headers', dst=repr(dst), src_headers=repr(src_headers), body_len=repr(body_len))
    for k, v in (src_headers or {}).items():
        if k is None:
            continue
        lk = k.lower()
        if lk in _STRIP_HDRS:
            continue
        dst.send_header(k, v)
    dst.send_header("Content-Length", str(body_len))
    dst.send_header("Connection", "close")



# ----------------------------------------------------------------------
# [PROXY-ERR] non-200 logging helper (added by patch_proxy_nonok_logging.py)
# Always emits (no DEBUG gate). Absence of a line for a given reply means
# that reply was 200.
# ----------------------------------------------------------------------
def _log_nonok(where: str, status: int, msg: str,
               ctx: "Dict[str, Any] | None" = None,
               extras: "Dict[str, Any] | None" = None) -> None:
    trace_enter('proxy_main._log_nonok', where=repr(where), status=repr(status), msg=repr(msg), ctx=repr(ctx), extras=repr(extras))
    try:
        c = ctx or {}
        parts = [
            f"status={status}",
            f"where={where}",
            f"tid={threading.get_ident()}",
            f"method={c.get('method','-')}",
            f"raw_path={c.get('raw_path','-')!r}",
            f"semantic_path={c.get('semantic_path','-')!r}",
            f"target_ip={c.get('target_ip','-')}",
            f"target_host={c.get('target_host','-')!r}",
            f"client_ip={c.get('client_ip','-')}",
            f"origin_local={c.get('origin_local','-')}",
            f"via={c.get('via','-')}",
            f"nh_ewma_ms={c.get('nh_ewma_ms','-')}",
            f"body_len={len(c.get('body') or b'')}",
            f"msg={(msg or '')[:240]!r}",
        ]
        if extras:
            for k, v in extras.items():
                try:
                    parts.append(f"{k}={v}" if isinstance(v, (int, float, bool, str)) else f"{k}={v!r}")
                except Exception:
                    parts.append(f"{k}=<unrepr>")
        print("[PROXY-ERR] " + " ".join(parts), flush=True)
    except Exception as _e:
        try: print(f"[PROXY-ERR] log_failed: {_e!r}", flush=True)
        except Exception: pass


def send_error_reply(handler: BaseHTTPRequestHandler, status: int, msg: str,
                     *, ctx: "Dict[str, Any] | None" = None,
                     where: str = "unspecified",
                     extras: "Dict[str, Any] | None" = None) -> None:
    trace_enter('proxy_main.send_error_reply', handler=repr(handler), status=repr(status), msg=repr(msg))
    if status != 200:
        _log_nonok(where, status, msg, ctx=ctx, extras=extras)
    body = (msg or "").encode("utf-8", "replace")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if body:
            handler.wfile.write(body)
    except BrokenPipeError:
        pass
    finally:
        handler.close_connection = True


def _is_async_header(headers: Dict[str, str] | None) -> bool:
    trace_enter('proxy_main._is_async_header', headers=repr(headers))
    if not isinstance(headers, dict):
        return False
    v = headers.get("X-FrogNet-Async") or headers.get("x-frognet-async")
    return (v is not None) and (str(v).strip() == "1")


def _is_write_method(method: str) -> bool:
    trace_enter('proxy_main._is_write_method', method=repr(method))
    m = (method or "").upper().strip()
    return m in ("POST", "PUT", "DELETE")


def proxy_dispatch(
    self,
    ctx: Dict[str, Any],
    *,
    upstream_host: str,
    upstream_port: int,
    daemon_port: int,
    eligible_ifaces: Set[str],
) -> None:

    trace_enter('proxy_main.proxy_dispatch', ctx=repr(ctx))
    method        = ctx["method"]
    raw_path      = ctx["raw_path"]
    body          = ctx["body"]
    headers       = ctx["headers"]
    semantic_path = ctx["semantic_path"]
    target_ip     = ctx["target_ip"]
    target_host   = ctx["target_host"]
    client_ip     = ctx.get("client_ip") or ""

    if not target_ip:
        return send_error_reply(self, 400, "Missing/invalid Host header", ctx=ctx, where="missing_host_header", extras={"host_header": repr(headers.get("Host") if headers else "")})

    if raw_path.split("?", 1)[0] == "/game" or (body and looks_like_game(
            body.decode("utf-8","replace") if isinstance(body,(bytes,bytearray)) else str(body))):
        code, _resp = GAME_ORIGIN.serve(body)
        _data = _resp.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(_data)))
            self.send_header("Connection","close")
            self.end_headers(); self.wfile.write(_data)
        except BrokenPipeError:
            pass
        finally:
            self.close_connection = True
        return

    # ------------------------------------------------------------------
    # EXPLICIT ASYNC GATE (HEADER-BASED)
    # ------------------------------------------------------------------
    is_async = _is_async_header(headers)

    if is_async:
        if not _is_write_method(method):
            return send_error_reply(self, 400, "X-FrogNet-Async is only allowed for write methods (POST/PUT/DELETE)", ctx=ctx, where="async_on_nonwrite", extras={"method": method})

        try:
            msg = b"ACCEPTED\n"
            self.send_response(202)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
        except BrokenPipeError:
            pass
        finally:
            self.close_connection = True
        return

    sem_mode = decide_semantic_mode()
    store = get_template_store()

    have_tpl = True
    if sem_mode in (1, 2):
        req_row, resp_row = store.lookup_by_path(method, semantic_path)
        have_tpl = bool(req_row and resp_row)

    train_on_miss = (sem_mode == 1 and not have_tpl)
    always_train  = (sem_mode == 2)

    try:
        path, dev, subnet, reason, _rtt, via = decide_path_for_target(target_ip, daemon_port, eligible_ifaces)
    except Exception as e:
        return send_error_reply(self, 503, str(e),
            ctx=ctx, where="hop_decide_raised",
            extras={"exc": repr(e), "eligible_ifaces": sorted(eligible_ifaces),
                    "daemon_port": daemon_port, "sem_mode": sem_mode,
                    "have_tpl": have_tpl})

    debug(f"[HOP DECISION] dest={target_ip} path={path} reason={reason} via={via or '-'} dev={dev} sem_mode={sem_mode} have_tpl={have_tpl}")

    # Adjacent hop identity for transport
    adj_ip = via if via else target_ip
    ctx["via"] = adj_ip
    ctx["nh_ewma_ms"] = _rtt or 0

    # -------------------------
    # HAM = non-frognet target -> real outbound on default route
    # -------------------------
    if path == DecisionPath.HAM:
        # [TEN_NET_IS_SEMANTIC_V1] Rule: anything on the 10. network is
        # semantic; only non-10. targets are raw. HAM is the raw/out-of-frognet
        # dispatch (real_upstream_remote_80, plain :80, no SAME/DIFF). A 10.
        # address must NEVER land here - if one does (stale classification,
        # resolution quirk), route it to the semantic transport instead of
        # forwarding it raw. Non-10. targets fall through to HAM as before.
        if str(target_ip).startswith("10."):
            debug(f"[TEN_NET_IS_SEMANTIC_V1] 10. target {target_ip} reached HAM; "
                  f"redirecting to semantic (raw is for non-10. only)")
            return handle_request(
                self, ctx, target_ip=target_ip, method=method, path=raw_path,
                headers=dict(headers) if headers else {}, body=body,
                is_fast_path=True,
            )
        # [DIAG-HAM-DECISION-V1] Capture every dispatch input that led
        # us onto the HAM branch.  Pairs with [DIAG-RU80-ENTRY-V1] from
        # transport_real.py - the (target_host, target_ip, adj_ip, via,
        # dev, reason) tuple here should match what the next log line
        # reports as arguments to real_upstream_remote_80, and the
        # `reason` tells us WHY HAM was chosen for this request.  A
        # FrogNet IP showing up here would be a routing-classification
        # bug.
        try:
            print(f"[DIAG-HAM-DECISION-V1] tid={threading.get_ident()} "
                  f"client_ip={getattr(self, 'client_address', ('?',))[0]} "
                  f"raw_path={raw_path!r} "
                  f"target_host={target_host!r} target_ip={target_ip} "
                  f"adj_ip={adj_ip} via={via!r} dev={dev!r} "
                  f"reason={reason!r} sem_mode={sem_mode} "
                  f"have_tpl={have_tpl} path={path!r} "
                  f"semantic_path={semantic_path!r} "
                  f"host_header={(headers or {}).get('Host')!r}",
                  flush=True)
        except Exception:
            pass
        upstream, _rtt_ms = real_upstream_remote_80(
            method, raw_path, body, headers,
            target_host or target_ip, target_ip,
            origin_local=ctx.get("origin_local", False),
            control_plane=is_control_plane_path(semantic_path),
        )
        status = int(upstream.get("status", 502))
        resp_body = upstream.get("body") or b""
        self.send_response(status)
        _relay_headers(self, upstream.get("headers") or {}, len(resp_body))
        self.end_headers()
        try:
            if resp_body:
                self.wfile.write(resp_body)
        except BrokenPipeError:
            pass
        self.close_connection = True
        return

    # ------------------------------------------------------------------
    # Observe peer for periodic topology emission (replaces fork-bomb)
    # Skip for origin-local requests (our own telemetry) to prevent
    # feedback loops.
    # ------------------------------------------------------------------
    if path == DecisionPath.SEMANTIC:
        kind = "SEMANTIC"
    elif path == DecisionPath.FAST:
        kind = "FAST"
    else:
        kind = "LOCAL"

    adj_for_obs = str(target_ip) if kind == "LOCAL" else str(adj_ip)

    if not ctx.get("origin_local"):
        _emit_diag("observe", ctx, adj_ip=adj_for_obs, kind=kind, dev=str(dev or ""), via=str(via or ""), reason=str(reason or ""))

        observe_peer(
            peer_ip=adj_for_obs,
            peer_name=str(target_host or target_ip),
            dev=str(dev or ""),
            via=str(via or ""),
            kind=kind,
            reason=str(reason or ""),
        )

    # LOCAL - go direct to Apache, but still learn templates
    if path == DecisionPath.LOCAL:
        # [LOCAL_READ_CACHE_V1] Serve repeated local-origin api.php reads from
        # the proxy's own RAM instead of forwarding every poll to Apache->MySQL.
        # Only local-origin reads are cached; writes invalidate per SensorName
        # (see below). Collapses the game/convergence poll storm.
        _lrc_key = (local_read_cache.read_key(method, raw_path)
                    if ctx.get("origin_local") else None)
        if _lrc_key is not None:
            _lrc_hit = local_read_cache.get(_lrc_key)
            if _lrc_hit is not None:
                _hb = _lrc_hit.get("body") or b""
                self.send_response(int(_lrc_hit.get("status", 200)))
                _relay_headers(self, _lrc_hit.get("headers") or {}, len(_hb))
                self.end_headers()
                try:
                    if _hb:
                        self.wfile.write(_hb)
                except BrokenPipeError:
                    pass
                return  # served from proxy RAM; no backend, no learn, no bump

        # Remote-origin requests must acquire a slot so they can't
        # starve Apache for local traffic during merges.
        _need_slot = not ctx.get("origin_local")
        if _need_slot:
            if not _REMOTE_APACHE_SLOTS.acquire(blocking=True, timeout=5.0):
                return send_error_reply(self, 503, "Apache busy (remote backpressure)", ctx=ctx, where="apache_backpressure", extras={"slots_max": int(os.environ.get("FROGNET_REMOTE_APACHE_SLOTS","64")), "acquire_timeout_s": 5.0, "path": str(path), "reason": reason})
        try:
            upstream, rtt_ms = real_upstream_local(
                method, raw_path, body, headers,
                upstream_host, upstream_port,
                origin_local=True,
                control_plane=is_control_plane_path(semantic_path),
            )
        finally:
            if _need_slot:
                try:
                    _REMOTE_APACHE_SLOTS.release()
                except Exception:
                    pass
        status = int(upstream.get("status", 502))
        resp_body = upstream.get("body") or b""

        # [LOCAL_READ_CACHE_V1] populate cache on a fresh read; invalidate the
        # affected cached reads on a write - both only for local-origin api.php.
        if ctx.get("origin_local"):
            if _lrc_key is not None and status == 200:
                local_read_cache.put(_lrc_key, {"status": status,
                                                "headers": upstream.get("headers") or {},
                                                "body": resp_body})
            elif method == "POST" and status == 200:
                local_read_cache.invalidate_for_write(method, raw_path, body)

        if status != 200:
            _log_nonok("apache_relay", status,
                       (resp_body[:240].decode("utf-8","replace") if resp_body else ""),
                       ctx=ctx,
                       extras={"upstream_host": upstream_host,
                               "upstream_port": upstream_port,
                               "rtt_ms": round(float(rtt_ms), 1),
                               "upstream_status_missing": "status" not in upstream,
                               "resp_len": len(resp_body),
                               "upstream_headers": dict(upstream.get("headers") or {})})

        # Track real HTTP bytes for LOCAL path
        req_size = len(body) if body else 0
        resp_size = len(resp_body)
        bump_real(peer_ip=target_ip, wan_req=req_size, wan_resp=resp_size)

        if (always_train or train_on_miss) and status < 400:
            learn_templates_from_real(
                method=method,
                semantic_path=semantic_path,
                req_headers=headers,
                req_body=body,
                upstream=upstream,
                store=store,
                raw_path=raw_path,
            )

        self.send_response(status)
        _relay_headers(self, upstream.get("headers") or {}, len(resp_body))
        self.end_headers()
        try:
            if resp_body:
                self.wfile.write(resp_body)
        except BrokenPipeError:
            pass
        self.close_connection = True
        return

    # ------------------------------------------------------------------
    # FAST and SEMANTIC both go through daemon with SAME/DIFF caching
    # ------------------------------------------------------------------
    if path in (DecisionPath.FAST, DecisionPath.SEMANTIC) or sem_mode == 0:
        return handle_request(
            self,
            ctx,
            target_ip=target_ip,
            method=method,
            path=raw_path,
            headers=dict(headers) if headers else {},
            body=body,
            is_fast_path=(path == DecisionPath.FAST),
        )

    return send_error_reply(self, 503, f"Unhandled path: {path}", ctx=ctx, where="unhandled_path", extras={"path": str(path), "reason": reason, "sem_mode": sem_mode, "have_tpl": have_tpl})


class FrogNetProxyHandler(BaseHTTPRequestHandler):
    server_version = "FrogNetProxy/3.3"
    protocol_version = "HTTP/1.1"

    _up_host: str = "127.0.0.1"
    _up_port: int = 8080
    _daemon_port: int = DAEMON_PORT_DEFAULT
    _eligible_ifaces: Set[str] = set(DEFAULT_UPSTREAM_IFACES)

    def do_GET(self):     return self._do_any("GET")
    def do_POST(self):    return self._do_any("POST")
    def do_PUT(self):     return self._do_any("PUT")
    def do_DELETE(self):  return self._do_any("DELETE")
    def do_HEAD(self):    return self._do_any("HEAD")
    def do_OPTIONS(self): return self._do_any("OPTIONS")

    def _do_any(self, method: str):
        trace_enter('proxy_main.FrogNetProxyHandler._do_any', method=repr(method))
        if self.headers.get("X-FrogNet-Origin-Local") == "1":
            return self._do_any_inner(method, origin_local=True)

        if not _ACTIVE.acquire(blocking=False):
            return send_error_reply(
                self, 503, "Proxy overloaded (too many active requests)",
                ctx={"method": method, "raw_path": self.path,
                     "client_ip": self.client_address[0],
                     "target_host": self.headers.get("Host","")},
                where="max_active_requests",
                extras={"max_active": MAX_ACTIVE_REQUESTS})
        try:
            return self._do_any_inner(method, origin_local=False)
        finally:
            try:
                _ACTIVE.release()
            except Exception:
                pass

    def _do_any_inner(self, method: str, origin_local: bool = False):
        trace_enter('proxy_main.FrogNetProxyHandler._do_any_inner', method=repr(method), origin_local=repr(origin_local))
        raw_path = self.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""

        client_ip = self.client_address[0]
        host_header = self.headers.get("Host", "")

        # [COUNTERS_ARE_READABLE_WHEN_ASKED_V1] /_frognet/wire -- what the
        # engine has saved on this box, now, without waiting for a flush.
        #
        # The metrics thread publishes these to a sensor about every thirty
        # seconds. That is right for a mesh watching itself and wrong for
        # anybody measuring a workload: a run of a few seconds sits inside one
        # interval and has nothing to diff, and polling for a flush that will
        # not arrive looks exactly like a hang.
        #
        # LOOPBACK ONLY. These are this node's own counters and there is no
        # reason for anything off-box to read them; a proxy that answers
        # questions about itself to the network is a proxy with a surface it did
        # not need. It publishes nothing and resets nothing -- asking cannot
        # perturb what is being measured.
        if raw_path.split("?")[0] == "/_frognet/wire":
            if client_ip not in ("127.0.0.1", "::1"):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"loopback only\n")
                return
            try:
                import json as _json
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(raw_path).query)
                want = (q.get("path") or [""])[0]
                payload = _json.dumps(
                    live_endpoint_stats(want), indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                # Named, not swallowed: a stats endpoint that fails silently is
                # indistinguishable from one that says there is nothing to
                # report, which is the confusion this endpoint exists to end.
                msg = f"{type(e).__name__}: {e}\n".encode()
                self.send_response(500)
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            return

        # [NO_FALLBACK_V1] netutil no longer answers "unresolved" when what
        # actually happened is "/etc/hosts could not be read". The two used to
        # be the same value (None), and the proxy reported the second as
        # "Missing/invalid Host header" - a message about the request, for a
        # fault in the file.
        #
        # This is not a recovery path. Nothing is substituted and nothing
        # continues: the request fails with the real cause named, at the one
        # place in the process that still has a client to tell.
        try:
            target_host, target_ip = target_host_and_ip(host_header)
        except NetutilFailure as e:
            return send_error_reply(
                self, 503, f"Node environment unreadable: {e}",
                ctx={"method": method, "raw_path": raw_path,
                     "client_ip": client_ip, "target_host": host_header},
                where="netutil_environment_read",
                extras={"exc_type": type(e).__name__, "host_header": repr(host_header)})

        semantic_path = canonical_semantic_key(method, raw_path, body)

        ctx = {
            "method": method,
            "raw_path": raw_path,
            "semantic_path": semantic_path,
            "headers": dict(self.headers),
            "body": body,
            "client_ip": client_ip,
            "target_host": target_host,
            "target_ip": target_ip,
            "run_id": self.headers.get(DEMO_RUN_ID_HEADER, "") or "",
            "origin_local": origin_local,
        }

        # The dispatch path reaches is_local_ip() and route_get() as well.
        # Same rule, same boundary: report the failing environment read, do not
        # continue on a substituted answer.
        try:
            return proxy_dispatch(
                self,
                ctx,
                upstream_host=self._up_host,
                upstream_port=self._up_port,
                daemon_port=self._daemon_port,
                eligible_ifaces=self._eligible_ifaces,
            )
        except NetutilFailure as e:
            return send_error_reply(
                self, 503, f"Node environment unreadable: {e}",
                ctx=ctx, where="netutil_environment_read",
                extras={"exc_type": type(e).__name__})


class FrogNetReflectHandler(BaseHTTPRequestHandler):
    """[REFLECT_PROBE_V1] Loop/reflection detector - its own vhost on
    FROGNET_REFLECT_PORT, completely separate from the :80 semantic path.

    Every hop traps this request (iptables REDIRECT on the reflect port) and
    runs exactly this logic on /reflect?o=<origin_ip>&c=<counter>:

      * Host target is one of our IPs  -> we are the destination -> 200.
      * o is one of our IPs and c > 0  -> a probe WE emitted has transited
        back through us -> reflection -> 508.  (At our own emission c==0, so
        this is false and we forward normally.)
      * otherwise                      -> c++ and forward to <target>:<port>
        with SO_MARK=1 so our own REDIRECT does not re-trap our forward.

    A foreign hop never matches is_local_ip(o), so transit through other
    gateways just increments and passes - only reflection through US is
    rejected.  A hop cap is a safety net for runaway loops among other nodes.
    """
    server_version = "FrogNetReflect/1.0"
    protocol_version = "HTTP/1.1"

    _reflect_port: int = REFLECT_PORT_DEFAULT
    _max_hops: int = int(os.environ.get("FROGNET_REFLECT_MAX_HOPS", "32"))
    _timeout: float = float(os.environ.get("FROGNET_REFLECT_TIMEOUT", "10"))

    def do_GET(self):  return self._handle()
    def do_HEAD(self): return self._handle()

    def log_message(self, *_a):  # quiet; we emit our own [REFLECT] diag
        return

    def _reply(self, code: int, msg: str):
        body = (msg + "\n").encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True

    def _handle(self):
        from urllib.parse import urlsplit, parse_qs, urlencode
        try:
            q = parse_qs(urlsplit(self.path).query)
        except Exception:
            return self._reply(400, "REFLECT_BAD_QUERY")
        o = (q.get("o", [""])[0] or "").strip()
        try:
            c = int((q.get("c", ["0"])[0] or "0").strip())
        except ValueError:
            return self._reply(400, "REFLECT_BAD_COUNTER")

        # [NO_FALLBACK_V1] is_local_ip() is what makes reflection detection
        # work. When _local_ipv4_set() swallowed its failure it returned only
        # loopback, so every is_local_ip() below answered False and loop
        # detection went blind - silently, and exactly when the machine was
        # already sick enough that `ip addr` failed. A 503 that names the read
        # is the honest answer; 400 REFLECT_MISSING_HOST would blame the probe.
        try:
            target_host, target_ip = target_host_and_ip(self.headers.get("Host", ""))
            if not target_ip:
                return self._reply(400, "REFLECT_MISSING_HOST")

            # Destination: the probe reached its target without looping through us.
            if is_local_ip(target_ip):
                return self._reply(200, f"REFLECT_OK {target_ip}")

            reflected = bool(o) and is_local_ip(o)
        except NetutilFailure as e:
            print(f"[REFLECT] environment read failed: {e}", flush=True)
            return self._reply(503, f"REFLECT_ENV_UNREADABLE {type(e).__name__}")

        # Reflection: a probe we originated has come back through us.
        if reflected and c > 0:
            print(f"[REFLECT] reject reason=reflection o={o} c={c} dest={target_ip}",
                  flush=True)
            return self._reply(508, f"REFLECT_LOOP o={o} dest={target_ip} c={c}")

        # Safety net: a loop among other nodes that never re-enters us.
        if c >= self._max_hops:
            print(f"[REFLECT] reject reason=maxhops o={o} c={c} dest={target_ip}",
                  flush=True)
            return self._reply(508, f"REFLECT_MAXHOPS c={c} dest={target_ip}")

        # Forward one hop onward with c+1, SO_MARK=1 (bypass our own REDIRECT).
        path = "/reflect?" + urlencode({"o": o, "c": c + 1})
        try:
            conn = MarkedHTTPConnection(target_ip, self._reflect_port,
                                        timeout=self._timeout)
            conn.request("GET", path,
                         headers={"Host": target_host or target_ip,
                                  "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read()
            code = resp.status
            conn.close()
        except Exception as e:
            print(f"[REFLECT] forward_fail dest={target_ip} c={c+1}: {e!r}",
                  flush=True)
            return self._reply(502, f"REFLECT_FORWARD_FAIL {e!r}")

        # Relay the downstream verdict (200 / 508 / 502) straight back up.
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True


class FrogNetProxyServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _ensure_admin_alias_bound():
    """Bind the FrogNetAdmin probe alias (.2) on whichever interface
    holds our served identity (.1).

    Peers probe us at .2 during discovery (sync_interfaces.sh ->
    /frognet_echo.php).  If .2 isn't bound, their ARP for it goes
    unanswered, and we look invisible - silent failure mode that
    breaks the entire discovery cascade.

    Runs once at proxy startup.  Idempotent: skips if already bound.
    Independent of HOW .1 got there (frognet-netstart, gateways.conf
    fixup, manual setup, anything) - this hook applies uniformly.

    Address-agnostic across 10/8 except reserved 10.253. (WG transit)
    and 10.254. (chorus virtual) ranges.
    """
    trace_enter('proxy_main._ensure_admin_alias_bound')
    import re
    import subprocess
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True, timeout=2, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[Proxy] admin-alias: ip addr show failed: {e!r}", flush=True)
        return

    # One-line-per-entry from `ip -4 -o addr show`:
    #   "2: eth0    inet 10.101.10.1/24 brd ... scope global eth0"
    # We want the iface and any 10.x.y.1/24 in non-reserved 10/8.
    served_iface = None
    served_prefix = None
    bound_addrs = set()
    for line in out.splitlines():
        m = re.search(
            r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/24\b", line)
        if not m:
            continue
        iface, addr = m.group(1), m.group(2)
        bound_addrs.add((iface, addr))
        if not addr.startswith("10."):
            continue
        if addr.startswith("10.253.") or addr.startswith("10.254."):
            continue
        if served_iface is None and addr.endswith(".1"):
            served_iface = iface
            served_prefix = addr.rsplit(".", 1)[0]   # "10.x.y"

    if not served_iface or not served_prefix:
        print("[Proxy] admin-alias: no served 10.x.y.1/24 found, skipping",
              flush=True)
        return

    admin_ip = f"{served_prefix}.2"
    if (served_iface, admin_ip) in bound_addrs:
        print(f"[Proxy] admin-alias: {admin_ip}/24 already on {served_iface}",
              flush=True)
        return

    try:
        subprocess.check_call(
            ["ip", "addr", "add", f"{admin_ip}/24", "dev", served_iface],
            timeout=2)
        print(f"[Proxy] admin-alias: bound {admin_ip}/24 on {served_iface}",
              flush=True)
    except subprocess.CalledProcessError as e:
        # rc=2 from `ip addr add` typically means "File exists" - already
        # bound by something else racing us.  Treat as success.
        if e.returncode == 2:
            print(f"[Proxy] admin-alias: {admin_ip}/24 already on "
                  f"{served_iface} (race-OK)", flush=True)
        else:
            print(f"[Proxy] admin-alias: bind {admin_ip}/24 on "
                  f"{served_iface} failed: rc={e.returncode}", flush=True)
    except Exception as e:
        print(f"[Proxy] admin-alias: bind {admin_ip}/24 on "
              f"{served_iface} failed: {e!r}", flush=True)


def run_proxy(listen: str, upstream_host: str, upstream_port: int, daemon_port: int, eligible_ifaces: Set[str]):
    trace_enter('proxy_main.run_proxy', listen=repr(listen), upstream_host=repr(upstream_host), upstream_port=repr(upstream_port), daemon_port=repr(daemon_port), eligible_ifaces=repr(eligible_ifaces))
    host, pstr = listen.split(":")
    srv = FrogNetProxyServer((host, int(pstr)), FrogNetProxyHandler)

    FrogNetProxyHandler._up_host = upstream_host
    FrogNetProxyHandler._up_port = int(upstream_port)
    FrogNetProxyHandler._daemon_port = int(daemon_port)
    FrogNetProxyHandler._eligible_ifaces = set(eligible_ifaces)

    print(
        f"[Proxy] BUILD=2026-03-24-v1 listening on {listen} upstream={upstream_host}:{upstream_port} "
        f"daemon_port={daemon_port} eligible={sorted(eligible_ifaces)}",
        flush=True,
    )

    # ---- Ensure the FrogNetAdmin .2 probe alias is bound ----
    # Must run before serve_forever() so the very first inbound probe
    # finds a kernel that can ARP-answer for our .2.
    _ensure_admin_alias_bound()

    # ---- Initialize persistent caches before accepting connections ----
    # [NO_FALLBACK_V1] WARNING-and-continue meant the proxy served traffic with
    # no cache table and no eviction thread. Every semantic lookup then missed,
    # every reply was re-fetched whole, and the only symptom was "the semantic
    # layer isn't saving anything" - a performance mystery with a one-line cause
    # printed once at boot and scrolled away.
    from proxy.cache import semcache_db as _proxy_cache
    _proxy_cache.ensure_table()
    _proxy_cache.start_eviction()   # [SEMCACHE_PROXY_LRU_V1] bound table growth

    if TELEMETRY_ENABLED_DEFAULT:
        start_flusher(TELEMETRY_INTERVAL_DEFAULT)

    # [PUBLISH_IN_ALL_CONTEXTS_V1] Boot-publish this node's <role>/capability to
    # control + data so a proxy-bearing node is visible to service-host election even
    # before the daemon watcher's first float tick. Idempotent dual-write; cheap.
    # [NO_FALLBACK_V1] Same as the daemon side: a node that cannot publish its
    # capability is invisible to service-host election, and a WARNING at boot is
    # not how that should be discovered.
    from core.role_publish import publish_all
    publish_all()

    # ---- Reflect vhost (loop/reflection detector) on its own port ----
    # Isolated second listener; never touches the :80 semantic path, so a
    # failure here cannot affect normal proxy traffic.
    reflect_port = int(os.environ.get("FROGNET_REFLECT_PORT", str(REFLECT_PORT_DEFAULT)))
    FrogNetReflectHandler._reflect_port = reflect_port
    try:
        reflect_srv = FrogNetProxyServer((host, reflect_port), FrogNetReflectHandler)
        threading.Thread(target=reflect_srv.serve_forever,
                         name="reflect-vhost", daemon=True).start()
        print(f"[Proxy] reflect vhost listening on {host}:{reflect_port}", flush=True)
    except Exception as e:
        print(f"[Proxy] WARNING: reflect vhost bind failed on {reflect_port}: {e!r}",
              flush=True)

    srv.serve_forever()


def main():
    trace_enter('proxy_main.main')
    import argparse
    p = argparse.ArgumentParser(description="FrogNet Proxy")
    p.add_argument("--listen", default=os.environ.get("FROGNET_PROXY_LISTEN", "0.0.0.0:80"))
    p.add_argument("--upstream-host", default=os.environ.get("FROGNET_UPSTREAM_HOST", "127.0.0.1"))
    p.add_argument("--upstream-port", type=int, default=int(os.environ.get("FROGNET_UPSTREAM_PORT", "8080")))
    p.add_argument("--daemon-port", type=int, default=int(os.environ.get("FROGNET_DAEMON_PORT", str(DAEMON_PORT_DEFAULT))))
    p.add_argument("--eligible-ifaces", default=os.environ.get("FROGNET_SEMANTIC_IFACES", ",".join(sorted(DEFAULT_UPSTREAM_IFACES))))
    p.add_argument("--telemetry", default="on")
    p.add_argument("--telemetry-interval", type=float, default=5.0)
    args, _ = p.parse_known_args()

    eligible: Set[str] = {x.strip() for x in (args.eligible_ifaces or "").split(",") if x.strip()}
    if not eligible:
        eligible = set(DEFAULT_UPSTREAM_IFACES)

    run_proxy(args.listen, args.upstream_host, args.upstream_port, args.daemon_port, eligible)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    main()
