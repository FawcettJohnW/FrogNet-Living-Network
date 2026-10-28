# fdfram.py -- the RAM manager's plain-HTTP front ([HTTP_FRONT_V1]), standard library only, for the FD&F tools.
#   write  POST ?op=write  {service,variable,instance,bag} -> {"ok":true,"id":N}   (same coordinates: replaced, new id)
#   read   GET  ?op=read&service=&variable=&instance=&after=&wait_s=   -> {"ok":true,"cells":[{id,instance,bag,...}]}
#          with after=N and wait_s>0 it is HELD until a row with id > N is written (the server caps wait_s at 30)
#   remove DELETE ?op=remove&id=N -> {"ok":true,"removed":0|1}   (needs the rina-host with "DELE" in the HTTP front)
#   rina   POST ?op=rina {"op":"participants"} -> {"ok":true,"participants":[...]}
# Errors are raised, never swallowed: the caller says what failed.
import json, os, urllib.parse, urllib.request

API = "/FawcettInnovations.RINA.ram_interface.php"
HOLD_S = 30.0


class RamError(Exception):
    pass


def where():
    """RINA_RAM (host:port) from the environment, else from /opt/fdf/var/join.env (written by fdf-join)."""
    w = os.environ.get("RINA_RAM", "")
    if not w:
        try:
            for line in open("/opt/fdf/var/join.env"):
                line = line.strip()
                if line.startswith("export RINA_RAM="):
                    w = line.split("=", 1)[1]
        except FileNotFoundError:
            pass
    if not w:
        raise RamError("no RAM manager: run fdf-join HOST:PORT, or set RINA_RAM=host:port")
    return w


class Ram:
    def __init__(self, hostport=None):
        self.hostport = hostport or where()
        self.base = "http://%s%s" % (self.hostport, API)

    def _do(self, method, query, body=None, timeout=10.0):
        url = self.base + "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
        except OSError as e:
            raise RamError("%s %s: %s" % (method, self.hostport, e))
        if not raw:
            raise RamError("%s %s op=%s: empty reply" % (method, self.hostport, query.get("op")))
        j = json.loads(raw)
        if not j.get("ok"):
            raise RamError("%s op=%s refused: %s" % (method, query.get("op"), j.get("error")))
        return j

    def write(self, service, variable, instance, bag, permanent=False):
        body = {"service": service, "variable": variable, "instance": instance, "bag": bag}
        if permanent:                                 # [PERMANENT_CELL_V1] kept by the region, not by this session
            body["permanent"] = True
        return self._do("POST", {"op": "write"}, body)["id"]

    def read(self, service, variable="", instance="", after=-1, wait_s=0.0):
        q = {"op": "read", "service": service}
        if variable:
            q["variable"] = variable
        if instance:
            q["instance"] = instance
        if after >= 0:
            q["after"] = after
        if wait_s:
            q["wait_s"] = min(wait_s, HOLD_S)
        return self._do("GET", q, timeout=10.0 + (wait_s or 0))["cells"]

    def remove(self, row_id):
        return self._do("DELETE", {"op": "remove", "id": row_id})["removed"]

    def participants(self):
        return self._do("POST", {"op": "rina"}, {"op": "participants"})["participants"]
