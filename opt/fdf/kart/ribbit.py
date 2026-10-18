# ribbit.py -- a tiny pure-Python Ribbit client. Standard library only (urllib).
#
# The RAM server speaks plain HTTP at /ram.php:
#   write: POST ...?op=write   body {service,variable,instance,bag}   -> {ok,id}
#   read:  GET  ...?op=read&service=..&variable=..&instance=..&fresh_s=..&after=..   -> {ok,cells:[...]}
# That's the whole contract. No binary wire, no lz4, no deps -- so this runs anywhere Python does.
#
# A Memory is the read/write surface over one region. An application maps SEVERAL memories at once simply by making
# several Memory objects at different addresses -- which is exactly what the demo does (the race region AND the
# communicator region).
#
# [RIBBIT_PY_SAYS_WHY_V1] A failed call raises RibbitError naming the region, the call and the cause. It is never
# turned into an empty answer: the caller says it and does the next write or read when its next event comes.
import json, urllib.request, urllib.parse


class RibbitError(Exception):
    pass


class Memory:
    def __init__(self, host, port, api="/ram.php", timeout=5.0):
        self.base = f"http://{host}:{port}{api}"
        self.timeout = timeout

    def _call(self, what, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception as e:
            raise RibbitError(f"{self.base} {what}: {type(e).__name__}: {e}") from e
        if not isinstance(data, dict) or not data.get("ok"):
            raise RibbitError(f"{self.base} {what}: the region answered {str(data)[:200]}")
        return data

    def write(self, service, variable, instance, bag):
        body = json.dumps({"service": service, "variable": variable,
                           "instance": instance, "bag": bag}).encode()
        req = urllib.request.Request(self.base + "?op=write", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        return self._call(f"write {service}.{variable}.{instance}", req, self.timeout)

    def read(self, service, variable="", instance="", after=-1, fresh_s=0, wait_s=0.0):
        q = {"op": "read", "service": service}
        if variable: q["variable"] = variable
        if instance: q["instance"] = instance
        if after >= 0: q["after"] = after
        if fresh_s: q["fresh_s"] = fresh_s
        if wait_s: q["wait_s"] = wait_s
        data = self._call(f"read {service}.{variable or '*'}.{instance or '*'}",
                          self.base + "?" + urllib.parse.urlencode(q), self.timeout + wait_s)
        if "cells" not in data:
            raise RibbitError(f"{self.base} read {service}: the answer has no cells: {str(data)[:200]}")
        return data["cells"]
