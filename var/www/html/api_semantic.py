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
import os
import json
import time
import urllib.request
import urllib.parse

API_BASE = os.environ.get("FROGNET_API_BASE", "http://databasehost.frognet/api.php")
TIMEOUT = float(os.environ.get("FROGNET_API_TIMEOUT", "2.5"))

def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def latest_sensor_data(sensor_type: str, metric_name: str, limit: int = 50):
    # This assumes your api.php supports:
    #   entity=sensor_data&action=list&SensorType=...&MetricName=...&limit=...
    # If your parameters differ, adjust here only.
    q = {
        "entity": "sensor_data",
        "action": "list",
        "SensorType": sensor_type,
        "MetricName": metric_name,
        "limit": str(int(limit)),
        "order": "desc",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(q)
    data = fetch_json(url)
    # Expected: {"ok":true, "rows":[...]} or raw array
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    return []

def app(environ, start_response):
    path = environ.get("PATH_INFO","/") or "/"
    try:
        if path in ("/", "/summary"):
            proxy_rows = latest_sensor_data("SemanticProxy", "Bytes", limit=200)
            daemon_rows = latest_sensor_data("SemanticDaemon", "RPC", limit=200)

            body = json.dumps({
                "ok": True,
                "ts": time.time(),
                "proxy": proxy_rows,
                "daemon": daemon_rows,
            }, separators=(",",":")).encode("utf-8")

            start_response("200 OK", [
                ("Content-Type","application/json; charset=utf-8"),
                ("Cache-Control","no-store"),
                ("Content-Length", str(len(body))),
            ])
            return [body]

        start_response("404 Not Found", [("Content-Type","application/json")])
        return [b'{"ok":false,"error":"not found"}']

    except Exception as e:
        body = json.dumps({"ok":False,"error":repr(e)}, separators=(",",":")).encode("utf-8")
        start_response("500 Internal Server Error", [
            ("Content-Type","application/json; charset=utf-8"),
            ("Cache-Control","no-store"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

# WSGI entrypoint
application = app

if __name__ == "__main__":
    # simple CGI fallback
    print("Content-Type: application/json; charset=utf-8")
    print("Cache-Control: no-store")
    print()
    out = {"ok": False, "error": "run under WSGI"}
    print(json.dumps(out))
