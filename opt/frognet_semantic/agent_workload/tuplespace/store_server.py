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
agent_workload/tuplespace/store_server.py -- the store, over real HTTP.

[THE_STORE_MUST_CROSS_A_PROCESS_V1]

list_store substitutes `_values_raw` inside one interpreter, which is enough
to develop against and not enough to prove anything: everything built on it
so far has been threads sharing a Python object. The claim under test is that
the same code runs whether participants are threads in one process or
processes on different machines, and a store that cannot leave the process
cannot test that claim.

This serves the two api.php endpoints frognet_tuples actually uses, over real
HTTP, backed by the same ListStore:

    GET  /api.php?entity=sensors&action=values&parse=1
                 [&SensorType=][&SensorName=][&fresh_s=N]
    POST /api.php?entity=sensor_data&action=upsert_by_name
                 {SensorName, SensorType, SensorAddress, jsonData}

Nothing in frognet_tuples is substituted. `_values_raw` builds its own URL,
opens its own socket, parses its own JSON, applies its own envelope rules and
its own SD: stripping. Point `dbhost` at "127.0.0.1:PORT" and the shipped read
path runs end to end -- which also means every failure mode it classifies
becomes reachable: stop this server and reads raise StoreUnreachable, return
a 500 and they raise StoreBroken.

The response shape is api.php's, taken from the SELECT in the values handler:

    {"ok": true, "rows": [{SensorID, FrogID, SensorAddress, SensorNetwork,
                           SensorName, SensorType, Tags, jsonData,
                           UpdatedAt, UpdatedAtEpoch, data}]}

`data` appears only with parse=1, which is what frognet_tuples always asks
for. UpdatedAtEpoch is what [ENVELOPE_TS_V1] judges freshness on, so it is
the store's clock here exactly as it is MySQL's clock there.

This is NOT a replacement for MariaDB and api.php. It is a second
implementation of the same contract, which is why the tuple-address oracle
should be run against both: two implementations that agree are evidence, and
where they disagree one of them is wrong.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .list_store import ListStore

# [IMPORT_GUARD_V2] 2026-09-25: diag is first-party and the tree is always on the path; a stub hid a broken install.
from core.frognet_diag import diag

def _apply_type_like(rows, type_like):
    """Honour a SensorType prefix filter.

    [AN_UNDERSTOOD_FILTER_OR_NONE_AT_ALL_V1] A store that ignores a filter
    it was given answers with MORE than was asked for, and that is the one
    failure a caller cannot detect. api.php has always supported
    `<column>__like`; this stand-in ignored it, so a prefix query api.php
    answers with six rows, this answered with every row in the store -- and
    a caller that trusted it archived and deleted rows belonging to
    something else.
    """
    if not type_like:
        return rows
    pat = type_like[:-1] if type_like.endswith("%") else type_like
    return [r for r in rows if (r.get("SensorType") or "").startswith(pat)]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # [A_KEPT_CONNECTION_MUST_NOT_WAIT_FOR_AN_ACK_V1]
    #
    # BaseHTTPRequestHandler runs with wbufsize = 0, so the headers and the
    # body leave as two separate sends. With Nagle on, the second waits for
    # the peer to acknowledge the first, and the peer's ACK is delayed --
    # 40 ms, every response.
    #
    # A fresh connection escapes it because TCP quick-ACK is still in effect
    # right after the handshake, which is why this was invisible while every
    # request opened its own connection and appeared the moment connections
    # were reused: 0.709 ms one-shot against 44.0 ms pooled, measured on the
    # loopback hop to the proxy. That looked like the pool being 60x slower.
    # It was the store holding its own second write.
    #
    # Measured here against a stand-in store, 150 requests, median:
    #     Nagle on   one-shot 43.994 ms   pooled 43.999 ms
    #     Nagle off  one-shot  0.137 ms   pooled  0.133 ms
    #
    # This is worth more than the pool it was found by. Every caller of the
    # store pays it on every response, pooled or not, and 40 ms per barrier
    # poll is why the wall of DIAG-STORESRV lines took as long as it did.
    disable_nagle_algorithm = True
    server_version = "FrogNetStoreServer/1.0"

    # Quiet: the access log is noise and the diag lines carry more.
    def log_message(self, *a):
        pass

    # -- helpers ----------------------------------------------------------

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bad(self, status, msg):
        # api.php's error shape. A caller that classifies failures needs the
        # difference between "this store said no" and "no store answered",
        # and it can only see that if this answers like the real one.
        self._json({"ok": False, "error": msg}, status)

    # -- routes -----------------------------------------------------------

    def do_GET(self):
        u = urlparse(self.path)
        if u.path != "/api.php":
            return self._bad(404, "not found: %s" % u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if q.get("entity") != "sensors" or q.get("action") != "values":
            return self._bad(400, "unsupported entity/action: %s/%s"
                             % (q.get("entity"), q.get("action")))

        st: ListStore = self.server.store
        service = q.get("SensorType", "")
        name = q.get("SensorName")
        # A filter this cannot honour must be an error, not a wider answer.
        type_like = q.get("SensorType__like")
        for _k in q:
            if _k.endswith("__like") and _k != "SensorType__like":
                self._json({"error": "unsupported filter %r" % _k}, 400)
                return
        try:
            fresh_s = int(q.get("fresh_s", 0) or 0)
        except ValueError:
            fresh_s = 0

        # [ASK_ONCE_AND_WAIT_V1] Blocking read.
        #
        # A reader waiting for peers had to keep asking, and every ask was a
        # round trip whose answer the store already knew. Measured: 2-5
        # polls per collective, 5-10 ms, a THIRD of a small all_reduce spent
        # on the question rather than the work.
        #
        # `wait_s` holds the request open until the answer is worth
        # returning. `min_rows` is what makes it a question rather than a
        # sleep: return as soon as that many rows exist, or at the deadline
        # with whatever there is -- the caller still decides whether the set
        # is complete, so a partial answer is not a wrong one.
        #
        # This is not a subscription. Nothing here remembers who is waiting;
        # there is no registry to go stale when a waiter dies, which is what
        # makes pub/sub brittle in a mesh that changes shape. The waiter
        # holds its own request and its own timeout, and a dead waiter is a
        # closed socket.
        #
        # A store that does not implement this returns immediately and the
        # caller's own loop still works, so the client degrades to polling
        # rather than breaking.
        try:
            wait_s = float(q.get("wait_s", 0) or 0)
        except ValueError:
            wait_s = 0.0
        try:
            min_rows = int(q.get("min_rows", 0) or 0)
        except ValueError:
            min_rows = 0

        raw = _apply_type_like(
                st.values_raw(service, "", name, 4.0, fresh_s),
                type_like)
        if wait_s > 0 and min_rows > 0 and len(raw) < min_rows:
            deadline = time.time() + min(wait_s, 30.0)
            nap = 0.0002
            while time.time() < deadline and len(raw) < min_rows:
                time.sleep(nap)
                nap = min(nap * 1.5, 0.005)
                raw = _apply_type_like(
                st.values_raw(service, "", name, 4.0, fresh_s),
                type_like)
            self.server.waits = getattr(self.server, "waits", 0) + 1
        parse = q.get("parse") in ("1", "true")
        rows = []
        for r in raw:
            row: Dict[str, Any] = {
                "SensorID": r["SensorID"],
                "FrogID": None,
                "SensorAddress": r["SensorAddress"],
                "SensorNetwork": None,
                "SensorName": r["SensorName"],
                "SensorType": r["SensorType"],
                "Tags": None,
                "jsonData": json.dumps(r["data"]),
                "UpdatedAt": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(r["UpdatedAtEpoch"])),
                "UpdatedAtEpoch": int(r["UpdatedAtEpoch"]),
            }
            if parse:
                row["data"] = r["data"]
            rows.append(row)
        self.server.gets += 1
        diag("DIAG-STORESRV", "values", service=service or "<any>",
             name=name, fresh_s=fresh_s, rows=len(rows),
             wait_s=wait_s or None, min_rows=min_rows or None)
        self._json({"ok": True, "rows": rows})

    def do_DELETE(self):
        """[THE_TEST_STORE_MUST_BE_ABLE_TO_FORGET_V1]

        This store answered only `values` and `upsert_by_name`, so
        _delete_by_id got a 400 and frognet_tuples swallowed it in
        `except Exception: pass`. Every study that ran against this fixture
        therefore could not express an ephemeral cell being reaped when its
        writer exits -- presence outliving the process, silently, in the one
        place a test would have caught it. The real api.php deletes by PK and
        SensorData cascades; ListStore.delete_by_id already exists. Only the
        route was missing.
        """
        u = urlparse(self.path)
        if u.path != "/api.php":
            return self._bad(404, "not found: %s" % u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if q.get("entity") != "sensors" or q.get("action") != "delete":
            return self._bad(400, "unsupported entity/action: %s/%s"
                             % (q.get("entity"), q.get("action")))
        try:
            sid = int(q.get("SensorID"))
        except (TypeError, ValueError):
            return self._bad(400, "SensorID is required and must be an int: "
                             "%r" % q.get("SensorID"))
        gone = self.server.store.delete_by_id(sid)
        self.server.deletes = getattr(self.server, "deletes", 0) + 1
        diag("DIAG-STORESRV", "delete", sensor_id=sid, existed=gone)
        self._json({"ok": True, "deleted": bool(gone)})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api.php":
            return self._bad(404, "not found: %s" % u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if q.get("entity") != "sensor_data" \
                or q.get("action") != "upsert_by_name":
            return self._bad(400, "unsupported entity/action: %s/%s"
                             % (q.get("entity"), q.get("action")))
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception as e:
            return self._bad(400, "bad body: %s: %s" % (type(e).__name__, e))

        name = body.get("SensorName")
        if not name:
            # api.php: 'Missing SensorName for upsert_by_name'
            return self._bad(400, "Missing SensorName for upsert_by_name")

        st: ListStore = self.server.store
        st.upsert_by_name(body.get("SensorType") or "", name,
                          body.get("SensorAddress") or "",
                          body.get("jsonData") or {}, "")
        self.server.posts += 1
        diag("DIAG-STORESRV", "upsert", name=name,
             service=body.get("SensorType"), addr=body.get("SensorAddress"))
        self._json({"ok": True})


class StoreServer:
    """The tuple store on a port. Backed by a ListStore, spoken like api.php.

    `dbhost` is what to hand frognet_tuples: "127.0.0.1:PORT". Point every
    participant at it -- in this process, in another process, on another
    machine -- and they share one space with nothing substituted anywhere.
    """

    def __init__(self, bind: str = "127.0.0.1", port: int = 0,
                 store: Optional[ListStore] = None):
        self.store = store or ListStore()
        self._srv = ThreadingHTTPServer((bind, port), _Handler)
        self._srv.store = self.store
        self._srv.gets = 0
        self._srv.posts = 0
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self.dbhost = "%s:%d" % (bind, self.port)
        self._t = threading.Thread(target=self._srv.serve_forever,
                                   daemon=True,
                                   name="store-server-%d" % self.port)
        self._t.start()
        diag("DIAG-STORESRV", "up", dbhost=self.dbhost)

    def stats(self) -> Dict[str, Any]:
        return {"dbhost": self.dbhost, "gets": self._srv.gets,
                "posts": self._srv.posts, "cells": self.store.count()}

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def serve(bind: str = "127.0.0.1", port: int = 0) -> StoreServer:
    return StoreServer(bind, port)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="tuple store over HTTP")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    s = serve(a.bind, a.port)
    print("dbhost=%s" % s.dbhost, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.close()
