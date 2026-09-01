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
"""bg_space.py -- the tuple-space the governor and clients share, two backends:

  TupleSpace  : the REAL substrate. Game objects are tuples in api.php / MySQL under
                SensorType 'gameserver', SensorName 'SD:<module>/<variable>.<gid>'.
                This is the network shared memory; engine and every player meet here.
  HttpSpace   : same wire shape against a plain api.php-compatible endpoint (used to
                prove multi-process play without MySQL; identical code path).

Both honor the governor's get/put/vars(TYPE, module, var) trio contract.
"""
import json, urllib.request, urllib.parse
from typing import Optional, List

# SensorType is the user-named service, supplied per instance as .typ

def _sname(module, var): return f"SD:{module}/{var}"   # SensorName; .<gid> scope appended by caller

class TupleSpace:
    """Backed by the production frognet_tuples (resolves dbhost, upsert_by_name)."""
    def __init__(self, gid, dbhost=None, typ="gameserver"):
        import frognet_tuples as FT
        self.FT = FT; self.gid = gid; self.typ = typ
        self.dbhost = dbhost or "databasehost.frognet"   # game state is DATA: floating data host, NEVER _control
    def get(self, typ, module, var):
        name = f"{_sname(module, var)}.{self.gid}"
        for row in self.FT._values_raw(typ, dbhost=self.dbhost, name_like=name):
            if row.get("SensorName") == name:
                return row.get("data")
        return None
    def put(self, typ, module, var, obj):
        # own=False: game state must outlive the writing process (a move, then exit)
        self.FT.put(typ, module + "/" + var, self.gid, obj, dbhost=self.dbhost, own=False)
    def vars(self, typ, module):
        pre = f"SD:{module}/"; suf = f".{self.gid}"; out = []
        for row in self.FT._values_raw(typ, dbhost=self.dbhost):
            n = row.get("SensorName", "")
            if n.startswith(pre) and n.endswith(suf):
                out.append(n[len(pre):-len(suf)])
        return out

class HttpSpace(TupleSpace):
    """Same SensorName scheme, but talks raw api.php at a fixed base (no dbhost resolve).
    Lets the engine + clients run as separate processes against one shared store in test."""
    def __init__(self, gid, base, typ="gameserver"):
        self.gid = gid; self.base = base.rstrip("/"); self.typ = typ
    def _rows(self, typ, name=None):
        q = f"{self.base}/api.php?entity=sensors&action=values&parse=1&SensorType={typ}"
        if name: q += "&SensorName=" + urllib.parse.quote(name)
        with urllib.request.urlopen(q, timeout=4) as r:
            rows = json.loads(r.read().decode()).get("rows", [])
        for row in rows:
            if not isinstance(row.get("data"), dict):
                try: row["data"] = json.loads(row.get("jsonData") or "null")
                except Exception: row["data"] = None
        return rows
    def get(self, typ, module, var):
        name = f"{_sname(module, var)}.{self.gid}"
        for row in self._rows(typ, name):
            if row.get("SensorName") == name: return row.get("data")
        return None
    def put(self, typ, module, var, obj):
        name = f"{_sname(module, var)}.{self.gid}"
        body = json.dumps({"SensorName": name, "SensorType": typ, "jsonData": obj}).encode()
        url = f"{self.base}/api.php?entity=sensor_data&action=upsert_by_name"
        urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST",
            headers={"Content-Type": "application/json"}), timeout=4).read()
    def vars(self, typ, module):
        pre = f"SD:{module}/"; suf = f".{self.gid}"; out = []
        for row in self._rows(typ):
            n = row.get("SensorName", "")
            if n.startswith(pre) and n.endswith(suf): out.append(n[len(pre):-len(suf)])
        return out
