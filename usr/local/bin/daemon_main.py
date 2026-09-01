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
# /opt/frognet_semantic/daemon/daemon_main.py
#!/opt/frognet_semantic/venv/bin/python3
# daemon_main.py - FrogNet Semantic Daemon (bytes-only telemetry)
#
# This file is your current daemon_main.py with only correctness fixes:
#   - Fix upstream_port debug message
#   - Keep upstream_port selection: DB host -> 8080, else -> 80
#   - No other behavioral change
#
# NOTE: databasehost.frognet is canonically assigned by merge/selectNewDatabaseHost
#       on each machine. Daemon detection is best-effort (DNS/hosts), acceptable.

import os
import time
import socket
import struct
import traceback
import http.client
from typing import Dict, Any, List, Tuple, Optional

from core.store import TemplateStore
from core.codec import SemanticCodec, FLAG_COMPRESSED
from core.tokens import TokenStore
from core.blob_store import BlobStore

from daemon.daemon_metrics import (
    bump_http,
    bump_semantic,
    start_daemon_metrics_flusher,
)

DEBUG = os.environ.get("FROGNET_DEBUG", "0") == "1"
BLOB_THRESHOLD = int(os.environ.get("FROGNET_BLOB_THRESHOLD", "65535"))  # bytes


def debug(msg: str):
    if DEBUG:
        print(msg, flush=True)


def _read_hosts_map(path: str = "/etc/hosts") -> Dict[str, str]:
    """
    Strict /etc/hosts parser. Returns name->ip mapping.
    """
    m: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip = parts[0].strip()
                for name in parts[1:]:
                    m[name.strip()] = ip
    except Exception:
        pass
    return m


def _local_ipv4_set() -> set[str]:
    """
    Local IPv4 set from kernel. No DNS.
    """
    ips: set[str] = {"127.0.0.1"}
    try:
        import subprocess
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split()
            if "inet" in parts:
                i = parts.index("inet")
                addr = parts[i + 1]
                ip = addr.split("/", 1)[0].strip()
                if ip:
                    ips.add(ip)
    except Exception:
        pass
    return ips


def is_local_ip(ip: str) -> bool:
    if not ip:
        return False
    ip = ip.strip()
    if ip.startswith("127."):
        return True
    return ip in _local_ipv4_set()


def detect_this_is_databasehost() -> bool:
    """
    AUTHORITATIVE databasehost detection:
      - databasehost.frognet MUST be resolved from /etc/hosts only
      - then compare to local IP set from kernel
    """
    hosts_map = _read_hosts_map("/etc/hosts")
    db_ip = hosts_map.get("databasehost.frognet", "").strip()
    if not db_ip:
        return False
    return is_local_ip(db_ip)


def http_upstream_local(
    method: str,
    path: str,
    body_text: str,
    headers: Dict[str, str],
    upstream_port: int,
) -> Tuple[int, Dict[str, str], bytes, str]:
    proxy_host = "127.0.0.1"
    conn = http.client.HTTPConnection(proxy_host, upstream_port, timeout=10)

    body_bytes = (body_text or "").encode("utf-8", "replace")

    fwd: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if k.lower() == "host":
            continue
        fwd[k] = v

    fwd["Host"] = f"{proxy_host}:{upstream_port}"
    fwd["Accept-Encoding"] = "identity"

    try:
        conn.request(method, path, body_bytes, fwd)
        resp = conn.getresponse()
        data = resp.read()
        hdrs = dict(resp.headers)
        status = resp.status
        conn.close()

        bump_http(up_in=len(body_bytes), up_out=len(data))

        enc = (hdrs.get("Content-Encoding") or "").lower()
        is_gzip = (enc == "gzip") or (len(data) >= 3 and data[0] == 0x1f and data[1] == 0x8b and data[2] == 0x08)
        if is_gzip:
            import gzip
            import io
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
            hdrs.pop("Content-Encoding", None)

        text = data.decode("utf-8", "replace")
        return status, hdrs, data, text

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass

        bump_http(up_in=len(body_bytes), up_out=0)
        raise RuntimeError(f"http_upstream_local({upstream_port}) failed: {repr(e)}") from e


def compute_reply_fieldblock_len(
    codec: SemanticCodec,
    dynamic_vals: List[Tuple[str, Any]],
    type_map: Dict[str, str],
) -> int:
    fb_len = 0
    for _idx, (field, value) in enumerate(dynamic_vals):
        t = (type_map.get(field) or "str").lower()
        type_id = codec._type_to_id(t)
        payload = codec._encode_value(type_id, value)
        fb_len += 3 + len(payload)
    return fb_len


def run_daemon(listen_host: str = "0.0.0.0", listen_port: int = 9009):
    store = TemplateStore()
    codec = SemanticCodec()

    start_daemon_metrics_flusher(5)

    this_is_db = detect_this_is_databasehost()
    upstream_port = 8080 if this_is_db else 80

    if DEBUG:
        if this_is_db:
            debug("[Daemon DEBUG] This machine IS databasehost.frognet → upstream to 127.0.0.1:8080")
        else:
            debug("[Daemon DEBUG] This machine is NOT databasehost.frognet → upstream to 127.0.0.1:80")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_host, listen_port))
    srv.listen(64)

    debug(f"[Daemon] listening on {listen_host}:{listen_port}")

    while True:
        conn, addr = srv.accept()
        conn.settimeout(15.0)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        wire_in = 0
        opcode = 0

        try:
            buf = bytearray()
            while True:
                blk = conn.recv(4096)
                if not blk:
                    break
                buf.extend(blk)
            packet = bytes(buf)
            wire_in = len(packet)

            if wire_in < 6:
                continue

            _version, _flags, opcode, _field_count = struct.unpack("<BBHH", packet[:6])
            if DEBUG:
                debug(f"[Daemon DEBUG] connection from {addr}, opcode={opcode}, len={wire_in}")

            req_row, resp_row = store.lookup_by_opcode(opcode)
            if not req_row or not resp_row:
                msg = f"no templates for opcode={opcode}"
                if DEBUG:
                    debug(f"[Daemon DEBUG] {msg}")
                err_pkt = codec.encode_error_reply(502, msg)
                conn.sendall(err_pkt)
                bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
                continue

            req_tpl = store.build_request_template(req_row)
            resp_tpl = store.build_reply_template(resp_row)

            tokens_req = TokenStore(req_tpl.tokens)
            url_vals, json_vals = codec.decode_request(packet, req_tpl, tokens_req)

            base_path = req_tpl.url_static or "/"
            from urllib.parse import urlencode

            q_pairs = [(k, str(v)) for (k, v) in (url_vals or []) if v is not None]
            if q_pairs:
                sep = "&" if "?" in base_path else "?"
                full_path = base_path + sep + urlencode(q_pairs)
            else:
                full_path = base_path

            body_text = ""
            if json_vals:
                try:
                    body_text = req_tpl.rebuild_body(json_vals)
                except Exception as e:
                    if DEBUG:
                        debug(f"[Daemon DEBUG] rebuild_body failed: {e}")
                    body_text = ""

            try:
                req_headers = req_tpl.build_headers(body_text)
            except Exception:
                req_headers = {}

            try:
                status, up_headers, _up_body_bytes, up_body_text = http_upstream_local(
                    req_tpl.method,
                    full_path,
                    body_text,
                    req_headers,
                    upstream_port=upstream_port,
                )
            except RuntimeError as e:
                msg = str(e)
                if DEBUG:
                    debug(f"[Daemon DEBUG] upstream error: {msg}")
                err_pkt = codec.encode_error_reply(504, msg)
                conn.sendall(err_pkt)
                bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
                continue

            if DEBUG:
                debug(f"[Daemon DEBUG] upstream status={status}, body_len={len(up_body_text)}")

            if status >= 400:
                msg = f"upstream HTTP {status}"
                err_pkt = codec.encode_error_reply(status, msg)
                conn.sendall(err_pkt)
                bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
                continue

            try:
                dyn_pairs = resp_tpl.extract_dynamic(up_body_text)
                if dyn_pairs and dyn_pairs[0][0] == "__FROGNET_TOO_MANY_ITEMS__":
                    n = int(dyn_pairs[0][1] or 0)
                    msg = f"semantic json array too large: {n} > {resp_tpl.fragment.get('max_items')}"
                    err_pkt = codec.encode_error_reply(413, msg)
                    conn.sendall(err_pkt)
                    bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
                    continue
            except Exception as e:
                msg = f"resp_tpl.extract_dynamic failed: {repr(e)}"
                err_pkt = codec.encode_error_reply(500, msg)
                conn.sendall(err_pkt)
                bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
                continue

            resp_frag = resp_tpl.fragment or {}
            baseline_blobs = resp_frag.get("baseline_blobs") or {}
            if not isinstance(baseline_blobs, dict):
                baseline_blobs = {}

            type_map: Dict[str, str] = {}
            final_pairs: List[Tuple[str, Any]] = []

            content_type = (up_headers.get("Content-Type") or "text/plain").split(";")[0]
            tokens_resp = TokenStore(resp_tpl.tokens)
            mode = (resp_tpl.mode or "raw").lower()

            for field, val in dyn_pairs:
                if mode in ("raw", "text") and isinstance(val, str):
                    data = val.encode("utf-8", "replace")
                    if len(data) > BLOB_THRESHOLD:
                        blob_id = BlobStore.store(data)
                        try:
                            store.update_baseline_blob(resp_tpl.template_id, field, blob_id, len(data), content_type)
                        except Exception:
                            pass

                        baseline_blobs[field] = {
                            "blob_id": blob_id,
                            "length": len(data),
                            "content_type": content_type,
                        }
                        type_map[field] = "str"
                        final_pairs.append((field, None))
                        continue

                final_pairs.append((field, val))
                t = (resp_frag.get("type_map") or {}).get(field, "str")
                type_map[field] = t

            resp_frag["baseline_blobs"] = baseline_blobs
            if "baseline" in resp_frag:
                resp_frag.pop("baseline", None)
            resp_tpl.fragment = resp_frag

            raw_fb_len = compute_reply_fieldblock_len(codec, final_pairs, type_map)

            reply_packet = codec.encode_reply(
                opcode=opcode,
                dynamic_vals=final_pairs,
                type_map=type_map,
                tokens=tokens_resp,
                compress=True,
            )

            out_flags = 0
            if len(reply_packet) >= 6:
                _, out_flags, _, _ = struct.unpack("<BBHH", reply_packet[:6])

            compressed = bool(out_flags & FLAG_COMPRESSED)
            packed_fb_len = max(0, len(reply_packet) - 6)

            conn.sendall(reply_packet)

            if compressed:
                bump_semantic(
                    wire_in=wire_in,
                    wire_out=len(reply_packet),
                    raw_out=raw_fb_len,
                    packed_out=packed_fb_len,
                    compressed=True,
                    opcode=opcode,
                )
            else:
                bump_semantic(
                    wire_in=wire_in,
                    wire_out=len(reply_packet),
                    opcode=opcode,
                )

        except Exception as e:
            msg = f"daemon error: {repr(e)}"
            debug("[Daemon ERROR] " + msg)
            debug(traceback.format_exc())

            try:
                err_pkt = codec.encode_error_reply(500, msg)
                conn.sendall(err_pkt)
                bump_semantic(wire_in=wire_in, wire_out=len(err_pkt), opcode=opcode)
            except Exception:
                pass

        finally:
            try:
                conn.shutdown(socket.SHUT_WR)
                conn.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def main():
    import argparse
    p = argparse.ArgumentParser(description="FrogNet Semantic Daemon")
    p.add_argument("--listen", default="0.0.0.0:9009", help="host:port to listen on")
    args = p.parse_args()

    host, port_str = args.listen.split(":")
    run_daemon(listen_host=host, listen_port=int(port_str))


if __name__ == "__main__":
    main()
