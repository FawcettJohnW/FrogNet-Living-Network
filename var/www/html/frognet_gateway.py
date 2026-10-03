#!/usr/local/bin/frognet_env/bin/python3
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
import os
import socket
import threading
import json
import requests
from urllib.parse import urlencode
import urllib.request
from typing import Tuple, Dict

# -------------------- Config -------------------------

GATEWAY_LISTEN_HOST = os.environ.get("FROGNET_GATEWAY_LISTEN_HOST", "0.0.0.0")
GATEWAY_LISTEN_PORT = int(os.environ.get("FROGNET_GATEWAY_LISTEN_PORT", "43278"))

TEMPLATE_API_URL = os.environ.get(
    "FROGNET_TEMPLATE_API",
    "https://databasehost.frognet/frognet_templates.php"
)

# -----------------------------------------------------
# Helper: HTTP GET JSON from PHP template API
# -----------------------------------------------------

def api_get_json(params: dict) -> dict:
  qs = urlencode(params)
  url = TEMPLATE_API_URL + "?" + qs
  with urllib.request.urlopen(url) as resp:
    body = resp.read().decode("utf-8")
    return json.loads(body)

# -----------------------------------------------------
# Unpack FrogNet binary = reverse of build_packed_binary()
# -----------------------------------------------------

def unpack_packed_binary(data: bytes) -> Tuple[str, Dict[str, str]]:
    """
    V1 packed binary:

      [1] 'F'
      [1] version (0x01)
      [1] len(templateId) = N
      [N] templateId
      [1] field count = K
      For each field:
        [1] Ln
        [Ln] name
        [2] Lv
        [Lv] value
    """
    pos = 0
    if len(data) < 4:
        raise ValueError("packet too short")

    magic = data[pos]
    pos += 1
    if magic != ord('F'):
        raise ValueError("invalid magic")

    version = data[pos]
    pos += 1
    if version != 0x01:
        raise ValueError(f"unsupported version {version}")

    tid_len = data[pos]
    pos += 1
    if pos + tid_len > len(data):
        raise ValueError("bad templateId length")

    template_id = data[pos:pos+tid_len].decode("utf-8")
    pos += tid_len

    if pos >= len(data):
        raise ValueError("missing field count")
    field_count = data[pos]
    pos += 1

    params = {}
    for _ in range(field_count):
        if pos >= len(data):
            raise ValueError("truncated at field name length")
        name_len = data[pos]
        pos += 1
        if pos + name_len > len(data):
            raise ValueError("truncated at field name")
        name = data[pos:pos+name_len].decode("utf-8")
        pos += name_len

        if pos + 2 > len(data):
            raise ValueError("truncated at value length")
        val_len = (data[pos] << 8) | data[pos+1]
        pos += 2
        if pos + val_len > len(data):
            raise ValueError("truncated at value")
        value = data[pos:pos+val_len].decode("utf-8")
        pos += val_len

        params[name] = value

    return template_id, params

# -----------------------------------------------------
# Build & send upstream HTTP request using template
# -----------------------------------------------------

def call_upstream(template: dict, params: dict) -> str:
    method = template["method"].upper()
    action_url = template["action_url"]
    body_type = template.get("bodyType", "urlencoded")
    content_type = template.get("contentType", "")

    if method == "GET":
        from urllib.parse import urlparse, urlunparse, parse_qs
        parsed = urlparse(action_url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in params.items():
            qs[k] = [v]
        new_qs = urlencode(qs, doseq=True)
        new_parsed = parsed._replace(query=new_qs)
        full_url = urlunparse(new_parsed)
        resp = requests.get(full_url)
        return resp.text

    else:
        if body_type == "json":
            # Send JSON body
            resp = requests.request(
                method,
                action_url,
                json=params  # requests sets Content-Type automatically
            )
        else:
            # Default to urlencoded form
            headers = {}
            if content_type:
                headers["Content-Type"] = content_type
            resp = requests.request(
                method,
                action_url,
                data=params,
                headers=headers
            )
        return resp.text

# -----------------------------------------------------
# TCP framing: read exactly N bytes
# -----------------------------------------------------

def recv_exact(conn: socket.socket, n: int) -> bytes:
  buf = bytearray()
  while len(buf) < n:
    chunk = conn.recv(n - len(buf))
    if not chunk:
      # connection closed
      return b""
    buf.extend(chunk)
  return bytes(buf)

# -----------------------------------------------------
# Handle a single TCP client
# -----------------------------------------------------

def handle_client(conn: socket.socket, addr):
  print(f"[FrogNet Gateway] Connected from {addr}")
  try:
    while True:
      # Read 4-byte length prefix
      header = recv_exact(conn, 4)
      if not header:
        break
      msg_len = int.from_bytes(header, "big")
      if msg_len <= 0:
        break

      payload = recv_exact(conn, msg_len)
      if not payload:
        break

      try:
        template_id, params = unpack_packed_binary(payload)
        print(f"[FrogNet Gateway] Got templateId={template_id}, params={params}")
      except Exception as e:
        print("[FrogNet Gateway] unpack error:", e)
        break

      # Look up request template via PHP API
      try:
        resp_json = api_get_json({
          "action": "get_request",
          "templateId": template_id
        })
      except Exception as e:
        print("[FrogNet Gateway] template API error:", e)
        # Send empty response to client
        conn.sendall((0).to_bytes(4, "big"))
        continue

      if resp_json.get("status") != "ok":
        print("[FrogNet Gateway] template API status not ok:", resp_json)
        conn.sendall((0).to_bytes(4, "big"))
        continue

      template = resp_json["template"]

      # Call upstream server
      try:
        upstream_body = call_upstream(template, params)
      except Exception as e:
        print("[FrogNet Gateway] upstream call error:", e)
        upstream_body = ""

      # Send back response body with length prefix
      body_bytes = upstream_body.encode("utf-8", errors="replace")
      conn.sendall(len(body_bytes).to_bytes(4, "big"))
      conn.sendall(body_bytes)

  finally:
    conn.close()
    print(f"[FrogNet Gateway] Connection closed from {addr}")


# -----------------------------------------------------
# Main TCP accept loop
# -----------------------------------------------------

def main():
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((GATEWAY_LISTEN_HOST, GATEWAY_LISTEN_PORT))
  server.listen(5)
  print(f"[FrogNet Gateway] Listening on {GATEWAY_LISTEN_HOST}:{GATEWAY_LISTEN_PORT}")

  while True:
    conn, addr = server.accept()
    t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
    t.start()

if __name__ == "__main__":
  main()
