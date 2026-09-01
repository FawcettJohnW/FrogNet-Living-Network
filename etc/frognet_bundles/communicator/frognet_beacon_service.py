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
frognet_beacon_service.py - plants bundle discovery beacons on the transient DB.

The design: discovery is associative read, not a registry and not a folder scan.
A service announces itself by writing an `installed_family_plugin` beacon to the
transient DB; the Communicator app discovers bundles by querying that SensorType
and grouping by hub. There is no central list - a bundle exists to the app exactly
as long as its beacon is fresh.

Why a heartbeat and not an install-time write: the transient DB is the FLOATING
role. `databasehost.frognet` re-elects to the highest IP on the known network, so
the holder can change at any time. A beacon written once to the old holder is gone
after a float. So the service:

  - re-resolves databasehost.frognet EVERY cycle (follows the float), and
  - re-plants the beacon on startup and every INTERVAL (default 300 s) thereafter.

This also makes beacons self-expiring: a service that dies stops heartbeating, its
beacon's `ts` goes stale, and the reader drops it past the freshness window. No
teardown, no dangling registry entries.

Run on the Host (it knows what is installed under /etc/frognet_bundles):
    python3 frognet_beacon_service.py
    python3 frognet_beacon_service.py --dbhost databasehost.frognet --interval 300

It reads each <bundle>/bundle.json and plants one beacon per runnable bundle.
Writes go to the transient via api.php (entity=sensor_data, action=upsert_by_name).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request

from frognet_tuples import my_ip

SENSOR_TYPE = "installed_family_plugin"
DEFAULT_DBHOST = "databasehost_control.frognet"   # SD: coordination plane (control), not the data host
DEFAULT_BUNDLES = "/etc/frognet_bundles"
DEFAULT_INTERVAL = 300                            # 5 minutes

def read_bundles(root: str) -> list:
    """Each <root>/<dir>/bundle.json is one installable bundle."""
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        man = os.path.join(root, name, "bundle.json")
        if not os.path.isfile(man):
            continue
        try:
            with open(man) as f:
                b = json.load(f)
        except Exception:
            continue
        if not b.get("app"):                      # only announce runnable bundles
            continue
        b["_dir"] = name                          # relative; reader resolves locally if installed
        out.append(b)
    return out


def beacon_payload(b: dict, addr: str) -> dict:
    """The tuple the app reads back. Carries everything the launcher needs + freshness ts."""
    return {
        "id": b.get("id", b.get("module", b.get("name", "?"))),
        "module": b.get("module", b.get("name", "")),
        "title": b.get("title", b.get("module", "?")),
        "hub": b.get("hub", "more"),
        "function": b.get("function", []),
        "app": b.get("app"),
        "dir": b.get("_dir", ""),
        "version": b.get("version", ""),
        "summary": b.get("summary", ""),
        "host": addr,                             # where this service is
        "ts": int(time.time()),                   # freshness; reader drops stale beacons
    }


def plant(dbhost: str, sensor_name: str, payload: dict, timeout: float = 4.0) -> bool:
    """upsert_by_name the installed_family_plugin beacon onto the current transient holder."""
    url = (f"http://{dbhost}/api.php?entity=sensor_data&action=upsert_by_name")
    body = json.dumps({
        "SensorName": sensor_name,
        "SensorType": SENSOR_TYPE,
        "SensorAddress": payload.get("host", "0.0.0.0"),
        "jsonData": payload,                      # api.php accepts an object here
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode() or "{}")
            return bool(j.get("ok", True))
    except Exception as e:
        print(f"  [beacon] plant {sensor_name} failed: {e}", flush=True)
        return False


def cycle(dbhost: str, bundles_root: str) -> int:
    addr = my_ip()                             # re-resolve identity each cycle too
    planted = 0
    for b in read_bundles(bundles_root):
        name = b.get("id", b.get("module", b.get("name")))
        if plant(dbhost, name, beacon_payload(b, addr)):
            planted += 1
            print(f"  [beacon] {name} -> {dbhost} (hub={b.get('hub')})", flush=True)
    return planted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="frognet_beacon_service")
    ap.add_argument("--dbhost", default=DEFAULT_DBHOST,
                    help="transient DB host (re-resolved each cycle; follows the float)")
    ap.add_argument("--bundles", default=DEFAULT_BUNDLES, help="installed bundles root")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help="re-plant interval seconds (default 300)")
    ap.add_argument("--once", action="store_true", help="plant once and exit (testing)")
    a = ap.parse_args(argv)

    print(f"FrogNet beacon service - planting {SENSOR_TYPE} on {a.dbhost} "
          f"every {a.interval}s", flush=True)
    while True:
        n = cycle(a.dbhost, a.bundles)
        print(f"  [beacon] cycle done: {n} beacon(s) fresh", flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
