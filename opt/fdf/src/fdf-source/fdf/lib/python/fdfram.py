# fdfram.py -- the RAM manager's plain-HTTP front ([HTTP_FRONT_V1]) for the services (AppRams, a region's RAM
# manager), and RoleRam: the SystemRam and the ControlRam on their api.php ([ROLES_ON_API_V1]). Standard library only.
#   write  POST ?op=write  {service,variable,instance,bag} -> {"ok":true,"id":N}   (same coordinates: replaced, new id)
#   read   GET  ?op=read&service=&variable=&instance=&after=&wait_s=   -> {"ok":true,"cells":[{id,instance,bag,...}]}
#          with after=N and wait_s>0 it is HELD until a row with id > N is written (the server caps wait_s at 30)
#   remove DELETE ?op=remove&id=N -> {"ok":true,"removed":0|1}   (needs the fdf-ram with "DELE" in the HTTP front)
#   rina   POST ?op=rina {"op":"participants"} -> {"ok":true,"participants":[...]}
# Errors are raised, never swallowed: the caller says what failed.
import http.client, json, os, socket, sys, urllib.parse, urllib.request

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


# [ROLES_ON_API_V1 - John 2026-11-07] The SystemRam and the ControlRam are ROLES, not RAM hosts on a port.
# databasehost.frognet (the SystemRam) and databasehost_control.frognet (the ControlRam) are each one IP address,
# chosen by the election and named in /etc/hosts, and each is served by that host's default api.php on port 80 --
# the same calls core/frognet_tuples.py makes. When the control name points at the DatabaseHost's IP, one machine
# is both. There is no RAM host on a FrogNetHost's .1 and no RAM port to configure (8841, /etc/frognet/ram_port
# and [RAM_PORT_CONFIGURABLE_V1] are gone). The AppRams and a region's RAM manager are services on their own ports
# and stay on Ram below.
#
# A cell (service, variable, instance) is the api.php tuple SensorType=<service>, SensorName=<variable>.<instance>,
# SensorAddress=<the writer's address>, jsonData=<the bag>. A held read is api.php's (entity=held): the holder's
# side is core/frognet_held.py, one socket and one thread per process.
FROGNET_CORE = os.environ.get("FROGNET_CORE", "/opt/frognet_semantic/core")
if FROGNET_CORE not in sys.path:
    sys.path.insert(0, FROGNET_CORE)


class RoleRam:
    """a role's memory: the api.php at the role's address, port 80. The same calls as Ram."""
    BUILD = "fdfram.RoleRam 20261107-roles1 [ROLES_ON_API_V1]"

    def __init__(self, host):
        self.host = host                        # an address (or host:port for a test), never a RAM port
        self.hostport = host                    # what the tools print
        self.base = "http://%s/api.php" % host
        self._seen = {}                         # query -> signature of the rows last handed out

    def __repr__(self):
        return "RoleRam(%s)" % self.host

    def _req(self, method, query, body=None, timeout=10.0):
        url = self.base + "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode())
        except Exception as e:
            raise RamError("%s %s %s: %s" % (method, self.host, query.get("action"), e))
        if not j.get("ok"):
            raise RamError("%s %s %s refused: %s" % (method, self.host, query.get("action"), j.get("error") or j))
        return j

    @staticmethod
    def _filter(service, variable, instance):
        f = {"SensorType": service}
        if variable and instance:
            f["SensorName"] = "%s.%s" % (variable, instance)
        elif variable:
            f["SensorName__like"] = variable.replace("%", "\\%").replace("_", "\\_") + ".%"
        elif instance:
            f["SensorName__like"] = "%." + instance.replace("%", "\\%").replace("_", "\\_")
        return f

    @staticmethod
    def _cells(rows):
        out = []
        for r in rows:
            var, _, inst = str(r.get("SensorName") or "").partition(".")
            out.append({"id": int(r.get("SensorID") or 0), "service": r.get("SensorType"), "variable": var,
                        "instance": inst, "bag": r.get("data") if r.get("data") is not None else {},
                        "writer": r.get("SensorAddress"), "updated": int(r.get("UpdatedAtEpoch") or 0)})
        out.sort(key=lambda c: (-c["updated"], -c["id"]))      # the newest first
        return out

    def version(self, timeout=4.0):
        return {"ok": True, "build": self.BUILD, "host": self.host}

    def write(self, service, variable, instance, bag, permanent=False):
        """write this writer's cell; api.php keeps it (a MySQL table), so permanent needs nothing more"""
        import frognet_held
        body = {"SensorType": service, "SensorName": "%s.%s" % (variable, instance),
                "SensorAddress": frognet_held._source_ip_toward(self.host), "jsonData": bag}
        return int(self._req("POST", {"entity": "sensor_data", "action": "upsert_by_name"}, body)["sensor_id"])

    def read(self, service, variable="", instance="", after=-1, wait_s=0.0):
        """the cells now. after >= 0 with wait_s > 0 is a held read: it returns when the cells are not the ones this
        RoleRam last handed out (or at wait_s, the caller's bound). A cell holds one value; there is nothing to catch
        up on, so `after` only says "hold" -- there are no write ids to count."""
        f = self._filter(service, variable, instance)
        key = json.dumps(f, sort_keys=True)
        if after >= 0 and wait_s:
            import frognet_held
            try:
                rows = frognet_held.wait_change(service, self.host, self._seen.get(key),
                                                name_like=f.get("SensorName__like"), name=f.get("SensorName"),
                                                wait_s=wait_s)
            except (frognet_held.HoldError, OSError) as e:    # the role's api.php did not take the hold: the caller's
                raise RamError(str(e)) from e                 # event (/etc/hosts) is when it is held again
        else:
            q = {"entity": "sensors", "action": "values", "parse": "1"}
            q.update(f)
            rows = self._req("GET", q)["rows"]
        import frognet_held
        self._seen[key] = frognet_held.signature(rows)
        return self._cells(rows)

    def remove(self, row, variable=None, row_id=None):
        if isinstance(row, dict):
            row_id = row["id"]
        j = self._req("POST", {"entity": "sensors", "action": "delete", "SensorID": int(row_id)})
        return int(j.get("affected") or 0)


def role_ram(name, env_var):
    """the role /etc/hosts names for `name` now: its api.php, port 80. `env_var` (an address, or host:port) overrides,
    for a test."""
    w = os.environ.get(env_var, "")
    if w:
        return RoleRam(w)
    hosts = os.environ.get("FDF_HOSTS", "/etc/hosts")
    try:
        for line in open(hosts):
            p = line.split("#", 1)[0].split()
            if len(p) >= 2 and name in p[1:]:
                return RoleRam(p[0])
    except FileNotFoundError:
        pass
    raise RamError("%s names no %s yet (the election has not run on this node)" % (hosts, name))


def system_ram():
    """the SystemRam: databasehost.frognet, the role the databasehost election names, served by its api.php"""
    return role_ram("databasehost.frognet", "RINA_SYSTEM_RAM")


def control_ram():
    """the ControlRam: databasehost_control.frognet (the highest .1 in the current view), served by its api.php;
    capability tuples and configuration tuples live there and every election reads it"""
    return role_ram("databasehost_control.frognet", "RINA_CONTROL_RAM")


def where(env=True):
    """the RAM manager this machine's member session is on: RINA_RAM from the environment, else where the session is
    NOW (system-ram.env: the tunnel daemon walks the system space's list, the WAN conduit's RAM manager first, then
    the LAN's [SYSTEM_SPACE_V1]), else /opt/fdf/var/join.env (written by fdf-join). The members' own cells are there.
    App configuration is not: that is the ControlRam (control_ram()), an api.php. env=False: the files only."""
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
