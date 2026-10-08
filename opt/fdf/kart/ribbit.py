# ribbit.py -- a tiny pure-Python Ribbit client. Standard library only (urllib).
#
# The RAM server speaks plain HTTP at /ram.php:
#   write: POST ...?op=write   body {service,variable,instance,bag}   -> {ok,id}
#   read:  GET  ...?op=read&service=..&variable=..&instance=..&fresh_s=..&after=..
# That's the whole contract. No binary wire, no lz4, no deps -- so this runs anywhere Python does.
#
# A Session is just a base URL. A Memory is the read/write surface over one region. An application
# maps SEVERAL memories at once simply by making several Memory objects at different addresses --
# which is exactly what the demo does (the race region AND the communicator region).
import json, urllib.request, urllib.parse

class Memory:
    def __init__(self, host, port, api="/ram.php", timeout=5.0):
        self.base = f"http://{host}:{port}{api}"
        self.timeout = timeout

    def write(self, service, variable, instance, bag):
        body = json.dumps({"service": service, "variable": variable,
                           "instance": instance, "bag": bag}).encode()
        req = urllib.request.Request(self.base + "?op=write", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except Exception:
            return None   # a demo never dies on a dropped write; the next frame writes again

    def read(self, service, variable="", instance="", after=-1, fresh_s=0, wait_s=0.0):
        q = {"op": "read", "service": service}
        if variable: q["variable"] = variable
        if instance: q["instance"] = instance
        if after >= 0: q["after"] = after
        if fresh_s: q["fresh_s"] = fresh_s
        if wait_s: q["wait_s"] = wait_s
        url = self.base + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout + wait_s) as r:
                data = json.loads(r.read())
        except Exception:
            return []
        # server returns {"ok":true,...,"cells_json":"[...]"} or a cells array; normalize to list of dicts
        cells = data.get("cells") if isinstance(data, dict) else None
        if cells is None and isinstance(data, dict) and "cells_json" in data:
            try: cells = json.loads(data["cells_json"])
            except Exception: cells = []
        return cells or []
