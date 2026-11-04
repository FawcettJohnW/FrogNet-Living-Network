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


SYSTEM_RAM_PORT = int(os.environ.get("FDF_LAN_RAM_PORT", "8841"))


def role_ram(name, env_var):
    """the RAM host of the node /etc/hosts names for `name` now (its address, port FDF_LAN_RAM_PORT): every FrogNetHost runs
    one ([LAN_RAM_ALWAYS_V1], [SYSTEM_RAM_ON_EVERY_FROGNETHOST_V1]) and the elections say whose is which.
    `env_var` (host:port) overrides, for a test."""
    w = os.environ.get(env_var, "")
    if w:
        return w
    hosts = os.environ.get("FDF_HOSTS", "/etc/hosts")
    try:
        for line in open(hosts):
            p = line.split("#", 1)[0].split()
            if len(p) >= 2 and name in p[1:]:
                return "%s:%d" % (p[0], SYSTEM_RAM_PORT)
    except FileNotFoundError:
        pass
    raise RamError("%s names no %s yet (the election has not run on this node)" % (hosts, name))


def system_ram():
    """[SYSTEM_RAM_IS_DATABASEHOST_V1] the SystemRam (John 2026-11-04: "SystemRam IS the RAM host"): the RAM host of the
    node the databasehost election names, databasehost.frognet. The WAN hub is a conduit, never the SystemRam."""
    return role_ram("databasehost.frognet", "RINA_SYSTEM_RAM")


def control_ram():
    """databasehost-control: the RAM host of the highest .1 in the current view (databasehost_control.frognet), where
    capability tuples and configuration tuples live and every election reads."""
    return role_ram("databasehost_control.frognet", "RINA_CONTROL_RAM")


def where(env=True):
    """the RAM manager this machine's member session is on: RINA_RAM from the environment, else where the session is
    NOW (system-ram.env: the tunnel daemon walks the system space's list, the WAN conduit's RAM manager first, then
    the LAN's [SYSTEM_SPACE_V1]), else /opt/fdf/var/join.env (written by fdf-join). The members' own cells are there.
    App configuration is not: that is the SystemRam (system_ram()). env=False: the files only."""
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
