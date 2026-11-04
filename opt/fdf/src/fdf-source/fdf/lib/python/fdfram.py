# fdfram.py -- the RAM manager's plain-HTTP front ([HTTP_FRONT_V1]), standard library only, for the FD&F tools.
#   write  POST ?op=write  {service,variable,instance,bag} -> {"ok":true,"id":N}   (same coordinates: replaced, new id)
#   read   GET  ?op=read&service=&variable=&instance=&after=&wait_s=   -> {"ok":true,"cells":[{id,instance,bag,...}]}
#          with after=N and wait_s>0 it is HELD until a row with id > N is written (the server caps wait_s at 30)
#   remove DELETE ?op=remove&id=N -> {"ok":true,"removed":0|1}   (needs the fdf-ram with "DELE" in the HTTP front)
#   rina   POST ?op=rina {"op":"participants"} -> {"ok":true,"participants":[...]}
# Errors are raised, never swallowed: the caller says what failed.
import http.client, json, os, socket, urllib.parse, urllib.request

API = "/FawcettInnovations.RINA.ram_interface.php"
HOLD_S = 30.0


class RamError(Exception):
    pass


def _env_ram(path):
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("RINA_RAM="):
                return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return ""


def system_now_file():
    return os.path.join(os.environ.get("FDF_VAR", "/opt/fdf/var"), "system-ram.env")


def where(env=True):
    """RINA_RAM (host:port) from the environment, else where the SystemRam is NOW, else /opt/fdf/var/join.env (written by
    fdf-join). [SYSTEM_RAM_FOLLOWED_V1] the tunnel daemon walks the system space's list (the WAN RAM manager first, then
    the LAN's, highest first: [SYSTEM_SPACE_V1]) and writes where it is now in system-ram.env; join.env is what was
    configured. After a split the SystemRam is the LAN's, and whoever reads this follows it there. env=False: the files
    only (a process that put RINA_RAM in its own environment for its children asks where the SystemRam is now)."""
    w = (os.environ.get("RINA_RAM", "") if env else "") or _env_ram(system_now_file()) or _env_ram(os.path.join(os.environ.get("FDF_VAR", "/opt/fdf/var"), "join.env"))
    if not w:
        raise RamError("no RAM manager: run fdf-join HOST:PORT, or set RINA_RAM=host:port")
    return w


class _Bound(http.client.HTTPConnection):
    """[RAM_ON_ITS_OWN_TUNNEL_V1] a connection that leaves through one interface (SO_BINDTODEVICE), whatever the main
    routing table says: a device holds connections to several RAM managers that all live at 10.253.253.254, each
    through its own tunnel, and nothing else on the machine is routed differently. TCP keepalive: a RAM manager that
    vanishes is noticed in about 5 s ([KEEPALIVE_5S_V1]), not when a held read times out."""
    def __init__(self, host, port, device, timeout):
        super().__init__(host, port, timeout=timeout)
        self.device = device

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self.device:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.device.encode())
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 2)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        s.setsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_USER_TIMEOUT", 18), 5000)   # unacknowledged data too
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        self.sock = s


class Ram:
    def __init__(self, hostport=None, device=None):
        self.hostport = hostport or where()
        self.device = device
        self.base = "http://%s%s" % (self.hostport, API)

    def _do(self, method, query, body=None, timeout=10.0):
        # [KEEPALIVE_5S_V1] every connection keeps alive, bound to a device or not: a held read on a RAM manager that
        # vanished ends in about 5 s, not when its HTTP timeout runs out (was: urllib, no keepalive -- 40 s)
        return self._do_bound(method, query, body, timeout)

    def _do_bound(self, method, query, body, timeout):
        host, port = self.hostport.rsplit(":", 1)
        c = _Bound(host, int(port), self.device, timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, API + "?" + urllib.parse.urlencode(query), body=data,
                      headers={"Content-Type": "application/json"} if data else {})
            raw = c.getresponse().read()
        except OSError as e:
            raise RamError("%s %s%s: %s" % (method, self.hostport, " via " + self.device if self.device else "", e))
        finally:
            c.close()
        if not raw:
            raise RamError("%s %s op=%s: empty reply" % (method, self.hostport, query.get("op")))
        j = json.loads(raw)
        if not j.get("ok"):
            raise RamError("%s op=%s refused: %s" % (method, query.get("op"), j.get("error")))
        return j

    def version(self, timeout=4.0):
        return self._do("POST", {"op": "rina"}, {"op": "version"}, timeout=timeout)

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

    def remove(self, row, variable=None, row_id=None):
        """[FAMILY_LOG_V1] remove a cell: a row as read (its service, variable and id), or (service, variable, id). An id
        is a place in one family (its variable), so the variable is named with it."""
        if isinstance(row, dict):
            row, variable, row_id = row["service"], row["variable"], row["id"]
        return self._do("DELETE", {"op": "remove", "service": row, "variable": variable, "id": row_id})["removed"]

    def participants(self):
        return self._do("POST", {"op": "rina"}, {"op": "participants"})["participants"]
