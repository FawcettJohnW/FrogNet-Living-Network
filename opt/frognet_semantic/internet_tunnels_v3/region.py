################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""region.py -- [REGION_V1] (PLAN-FROGNET-ON-FDF Stage 2) the tunnel daemon's side of an FD&F region.

  register_node()               -> this node's facts (role=node, name, guid, subnet, pubkey, pond = RINA_AREA) written
                                   to the facts file; the node's fdf-client announces them through ITS session
                                   ([IPCP_FACTS_V1]). The session is enrollment; the guid is the identity.
  my_channels()                 -> ONE channel per live hub (participants with facts.role == "hub" in this area) that
                                   holds this node's key: edge_ip = 10.253.A.B for our 10.A.B.0/24, edge_mask 16,
                                   droplet_* = the hub's pubkey/endpoint/addr, allowed_ips 10.0.0.0/8, remote_subnets =
                                   every prefix whose VERIFIED mapping (map_list) names that hub, except ours.
  publish_plan_maps(prefixes)   -> facts.maps = the plan's prefixes with the locator set of every hub whose tunnel
                                   handshook, prio from the measured handshake RTT (lower first); fdf-client registers
                                   them, signed and session-bound ([MAP_FROM_FACTS_V1]).
  events()                      -> "ready", then "p" per change of the members' own cells (pond, member) -- one held
                                   read; a member's mappings change with them -- and "gone": the region is gone. No timer.

Config: RINA_RAM=host:8840 (region mode on) from /opt/fdf/var/join.env and RINA_AREA=<area name> from site.env, one
file each [REGION_SWITCH_ONE_FILE_V1] (config.load_config). Facts file: FDF_FACTS (default /opt/fdf/var/facts.json). Tools: FDF_BIN (default
/opt/fdf/bin).
"""
import hashlib
import ipaddress
import json
import os
import socket
import sys
import subprocess
import urllib.error
import urllib.request

from . import config

API = "/FawcettInnovations.RINA.ram_interface.php"
BUILD_TAG = "fdf-2026-11-04-r45"      # [FDF_VERSION_V1] keep equal to src/fdf_version.hpp
HUB_MASK = 16
ALLOWED = "10.0.0.0/8"


def enabled() -> bool:
    return bool(getattr(config, "REGION_RAM", ""))


def own_subnet() -> str:
    """[OWN_SUBNET_IS_DOT1_V1] this node's own /24: the one where it holds the .1 (a FrogNet node is the .1 of the
    /24 it serves; any other 10.x address it holds is a guest lease on someone else's LAN -- the discovery rule
    [UPSTREAM_LEASE_DOT1_V1]). config.LOCAL_SUBNET takes the FIRST 10.x address `ip addr` lists, which flips when a
    lease sorts first (Seattle7 claimed 10.250.250.0/24, Seattle5's, on 2026-10-09). Transit/chorus ranges excluded.
    Falls back to config.LOCAL_SUBNET and says so."""
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True)
    except Exception:
        out = ""
    dot1 = []
    for ln in out.splitlines():
        p = ln.split()
        if "inet" in p:
            a = p[p.index("inet") + 1].split("/")[0]
            o = a.split(".")
            if a.startswith("10.") and o[1] not in ("253", "254") and o[3] == "1":
                dot1.append(a.rsplit(".", 1)[0] + ".0/24")
    if dot1:
        if len(set(dot1)) > 1:
            config.log.warning("REGION: this node holds the .1 of several /24s %s; using the first", sorted(set(dot1)))
        sub = dot1[0]
        if sub != config.LOCAL_SUBNET:
            config.log.warning("REGION: own subnet is %s (where this node is .1), not config.LOCAL_SUBNET=%s (first 10.x address listed: a lease)",
                               sub, config.LOCAL_SUBNET)
        return sub
    config.log.warning("REGION: no .1 address held; using config.LOCAL_SUBNET=%s", config.LOCAL_SUBNET)
    return config.LOCAL_SUBNET


def _facts_path() -> str:
    return os.environ.get("FDF_FACTS", "/opt/fdf/var/facts.json")


def read_facts() -> dict:
    try:
        with open(_facts_path()) as f:
            out = json.load(f)
    except (OSError, ValueError):
        out = {}
    pend = getattr(_batch, "pending", None)
    if pend:
        out.update(pend)                        # inside a facts_batch: what this event already decided is visible to it
    return out


import threading as _threading
_batch = _threading.local()


class facts_batch:
    """[ONE_WRITE_PER_EVENT_V1] `with region.facts_batch():` every write_facts inside (maps, view, gen) lands in ONE file
    write at exit, so fdf-client re-announces once and the fleet gets ONE `p` event for this event, not three (each
    announcement is a region read and a rebuild on every member). Thread-local: the election thread writes directly."""
    def __enter__(self):
        _batch.pending = {}
        return self
    def __exit__(self, *exc):
        pend = getattr(_batch, "pending", None)
        _batch.pending = None
        if pend:
            write_facts(pend)
        return False


def write_facts(update: dict) -> dict:
    """Merge `update` into the facts file atomically (tmp + rename, which is what fdf-client's watch reacts to)."""
    pend = getattr(_batch, "pending", None)
    if pend is not None:                        # inside a facts_batch: merged, written once at its end
        pend.update(update)
        f = read_facts(); f.update(pend)
        return f
    # [FACTS_ONE_WRITER_AT_A_TIME_V1] the daemon (loop + election thread) and fdf-facts both merge into this file: the
    # read-merge-rename is held under flock(<facts>.lock) and each writer renames its OWN tmp. One shared "facts.json.tmp"
    # let a second writer truncate the first's tmp before its rename -- the file read back as {} and the next merge
    # wrote only its own keys (name, guid, subnet, pubkey, role gone; the hub then held no peer for this node).
    import fcntl
    path = _facts_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        f = read_facts()
        f.update(update)
        tmp = "%s.tmp.%d.%d" % (path, os.getpid(), _threading.get_ident())
        with open(tmp, "w") as out:
            out.write(json.dumps(f, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return f


def _call(body: dict) -> dict:
    url = "http://" + config.REGION_RAM + API + "?op=rina"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:                       # the region says why; say it, not just the code
        raise RuntimeError("region %s: HTTP %d for op %s: %s" % (config.REGION_RAM, e.code, body.get("op"), e.read().decode(errors="replace")[:200]))


def version() -> str:
    """The region's build tag, or why there is none. Logged by the daemon at start."""
    try:
        return _call({"op": "version"}).get("server", "(older than fdf-2026-10-09-r16: no version op)")
    except Exception as e:
        return "(no answer: %s)" % e


# [WORLD_READ_ONCE_PER_EVENT_V1] the daemon reads the world once per event (`world`); everything else that asks the
# region inside that event -- my_channels for the hub channel (bringup asked it twice: reachability, then the list),
# transit_map -- answers from that same read. Four region round trips per event (two participants + two map_list) on
# top of `world`, each a WAN RTT on a node far from the droplet. Outside world_in_hand() every call goes to the region.
_held = {"world": None}


class world_in_hand:
    def __init__(self, parts: list, maps: list):
        self.w = (parts, maps)

    def __enter__(self):
        _held["world"] = self.w
        return self

    def __exit__(self, *a):
        _held["world"] = None
        return False


def participants() -> list:
    if _held["world"] is not None:
        return _held["world"][0]
    return _call({"op": "participants"}).get("participants", [])


def mappings() -> list:
    if _held["world"] is not None:
        return _held["world"][1]
    return _call({"op": "map_list", "site": getattr(config, "REGION_AREA", "")}).get("mappings", [])


def world() -> tuple:
    """[WORLD_IN_ONE_READ_V1] (participants, mappings) in ONE round trip (region op `world`, build r14+). The daemon's
    per-event read. A region without the op answers an error and that is an error: the droplet is behind the nodes."""
    w = _call({"op": "world", "site": getattr(config, "REGION_AREA", "")})
    if not w.get("ok"):
        raise RuntimeError("region %s has no `world` op (%s): region build %s is older than this node" % (config.REGION_RAM, w.get("error"), w.get("server", "?")))
    return w.get("participants", []), w.get("mappings", [])


def edge_of(subnet: str):
    n = ipaddress.ip_network(subnet, strict=False)
    o = n.network_address.packed
    if n.prefixlen != 24 or o[0] != 10 or o[1] in (253, 254):
        return None
    return "10.253.%d.%d" % (o[1], o[2])


def _hubs(parts: list) -> list:
    area = getattr(config, "REGION_AREA", "")
    out = []
    for p in parts:
        f = p.get("facts") or {}
        if p.get("alive") and f.get("role") == "hub" and f.get("pubkey") and f.get("endpoint") \
                and (not area or f.get("area", area) == area):
            out.append(f)
    return sorted(out, key=lambda f: f.get("name", ""))


def _reach(maps: list) -> dict:
    """hub name -> set of prefixes whose verified mapping names it."""
    out = {}
    for m in maps:
        for loc in m.get("rloc", "").split(","):
            hub = loc.split(":")[0]
            if hub:
                out.setdefault(hub, set()).add(m["eid"])
    return out


# ---------------------------------------------------------------- this node in the region
def register_node() -> dict:
    """This node's facts: who it is, its /24, its WireGuard public key (the daemon's: config.ensure_node_key) and the
    pond it is in (the region's area). fdf-client announces them; the hub adds the peer from facts.pubkey."""
    return write_facts({"role": "node", "name": config.NODE_NAME, "guid": config.NODE_GUID,
                        "subnet": own_subnet(), "pubkey": config.PUBKEY, "pond": getattr(config, "REGION_AREA", "")})


HUB_JSON = os.environ.get("FDF_HUB_JSON", "/opt/fdf/var/hub.json")


def invitation() -> dict:
    """[ADMITTED_BY_INVITE_V1] the hub this node was invited to (fdf-region-join wrote it from the fdf-invite line):
    {"area", "ram", "hub": {name, pubkey, endpoint, addr}, "member": {name, edge, kind}}; {} if none."""
    try:
        with open(HUB_JSON) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- the system space: which RAM manager  [SYSTEM_SPACE_V1]
# opt/fdf/docs/ORGANIZATIONS.md section 4 (John): the system space has its own ordered list of RAM managers; after the
# list it falls back the way every space does: the LAN's machines from the highest IP (databasehost-control) down to this
# node's own. A node is never without a RAM manager. Each node walks the list for itself; a merge is a node applying one
# RAM event, there is no discovery. Failover from a WAN RAM manager to the LAN is the removal of the WG tunnels (John):
# a LAN entry has no hub channel.
SYSTEM_SPACE = os.environ.get("FDF_SYSTEM_SPACE", "/opt/fdf/var/system-space.json")
LAN_RAMS = os.environ.get("FDF_LAN_RAMS", "/opt/fdf/var/lan-rams.json")
LAN_RAM_PORT = int(os.environ.get("FDF_LAN_RAM_PORT", "8841"))
_cur = {"entry": None}


def system_managers() -> list:
    """The system space's configured RAM managers, in order: [{"name", "ram", "hub": {...}, "member": {"edge"}}].
    system-space.json (fdf-login --node writes it, one entry per RAM manager on the join line) or, for a node joined by
    invitation, the one hub hub.json names."""
    try:
        with open(SYSTEM_SPACE) as f:
            ms = json.load(f).get("managers") or []
        return [m for m in ms if m.get("ram") and (m.get("hub") or {}).get("pubkey") and (m.get("member") or {}).get("edge")]
    except (OSError, ValueError):
        pass
    # one RAM manager, as configured before system-space.json: the forward pond's (ponds/<forward>.env), else join.env's;
    # its hub channel is the invitation's (hub.json), or none for a region joined directly (fdf-join RAM)
    inv = invitation()
    h, m = inv.get("hub") or {}, inv.get("member") or {}
    fwd = read_word("forward")
    ram = (pond_env(fwd).get("RINA_RAM") if fwd else "") or read_env_ram(os.path.join(FDF_VAR, "join.env"))
    if not ram:
        return []
    if h.get("pubkey") and h.get("endpoint") and m.get("edge"):
        return [{"name": h.get("name", "hub"), "ram": ram, "hub": h, "member": m}]
    return [{"name": "region " + ram, "ram": ram}]


def own_dot1() -> str:
    sub = own_subnet()
    return sub.rsplit(".", 1)[0] + ".1" if sub else ""


def _ipkey(ip: str):
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return -1


def lan_fallback() -> list:
    """The LAN's machines from the highest IP (databasehost-control) down to this node's own, each its LAN RAM manager
    (fdf-ram on its .1, port FDF_LAN_RAM_PORT). Known from lan-rams.json (save_lan) plus this node itself; nothing
    is discovered."""
    try:
        with open(LAN_RAMS) as f:
            ips = set(json.load(f).get("dot1s") or [])
    except (OSError, ValueError):
        ips = set()
    me = own_dot1()
    if me:
        ips.add(me)
    mine = _ipkey(me)
    out = sorted((ip for ip in ips if _ipkey(ip) >= mine), key=_ipkey, reverse=True)
    return [{"name": "lan " + ip + ("" if ip != me else " (own)"), "ram": "%s:%d" % (ip, LAN_RAM_PORT), "lan": True} for ip in out]


def system_order() -> list:
    return system_managers() + lan_fallback()


def save_lan(dot1s, replace: bool) -> None:
    """The LAN's .1s as this node's plan has them (LAN routes, not through a hub). On a WAN RAM manager the plan sees the
    whole network and replaces the list; on a LAN one it only adds (a node alone on its own RAM sees nobody, and must
    still try the higher ones). Written only when it changed."""
    try:
        with open(LAN_RAMS) as f:
            have = set(json.load(f).get("dot1s") or [])
    except (OSError, ValueError):
        have = set()
    want = set(dot1s) if replace else have | set(dot1s)
    want.discard(own_dot1())
    if want != have:
        tmp = LAN_RAMS + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps({"dot1s": sorted(want, key=_ipkey, reverse=True)}) + "\n")
        os.replace(tmp, LAN_RAMS)
        config.log.warning("REGION: the LAN's RAM managers (highest first): %s [SYSTEM_SPACE_V1]", sorted(want, key=_ipkey, reverse=True))


def use(entry: dict) -> None:
    """This node's system space is on `entry` now: the region calls go to it, and my_channels builds its hub channel
    (none for a LAN entry)."""
    _cur["entry"] = entry
    config.REGION_RAM = entry["ram"]


def current() -> dict:
    return _cur["entry"] or {}


def answers(entry: dict, timeout: float = 5.0) -> bool:
    """Does this RAM manager answer (op version) right now? A LAN entry and the CURRENT WAN entry are asked directly
    (the current one through its hub channel, which bring-up made). Any other WAN entry is asked through a probe tunnel
    of its own for this one question ([SPACE_PROBE_TUNNEL_V1]: fdfsp<N>, this node's key, its edge at that RAM host,
    bound with SO_BINDTODEVICE, no route; down again after), so a standby is never reached through the current one's
    tunnel and nothing on the node is routed differently."""
    import http.client, socket
    host, port = entry["ram"].rsplit(":", 1)
    dev = ""
    if entry.get("hub") and entry.get("name") != (_cur["entry"] or {}).get("name"):
        dev = _probe_up(entry)
        if not dev:
            return False
    try:
        class _B(http.client.HTTPConnection):
            def connect(self):
                s = socket.socket()
                if dev:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
                s.settimeout(self.timeout)
                s.connect((self.host, self.port))
                self.sock = s
        c = _B(host, int(port), timeout=timeout)
        c.request("POST", API + "?op=rina", body=json.dumps({"op": "version"}), headers={"Content-Type": "application/json"})
        r = c.getresponse()
        ok = r.status == 200 and json.loads(r.read().decode() or "{}").get("ok", True) is not False
        c.close()
        return ok
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        if dev:
            subprocess.run(["ip", "link", "del", dev], capture_output=True)


class Standing:
    """[NO_FLAP_V1] (John, 2026-11-01: "You know within milliseconds that the socket to upstream has closed. You should
    immediately switch to the backup ... Then we don't do shit to go back up until this one dies. No flapping around.")
    A standing session to every system-space entry but the current one: a held read on each (a WAN entry through its own
    probe tunnel, fdfsp<N>, bound, no route). Its state is what the loop needs at a loss: the first live entry in list
    order is taken at once, nothing asked then. An entry coming back is said (on_up) and changes nothing while the current
    one lives; the loop takes it only when the node is on the LAN (its last resort, not a peer)."""
    def __init__(self):
        import threading
        self.alive, self.devs, self.gen, self.on_up, self.lock = {}, {}, 0, None, threading.Lock()

    def run(self, order: list, current: dict) -> None:
        import threading
        self.stop()
        with self.lock:
            self.gen += 1; g = self.gen
        self.alive = {}
        for e in order:
            if e["name"] != current.get("name"):
                threading.Thread(target=self._one, args=(g, e), name="standing-" + e["name"], daemon=True).start()

    def stop(self) -> None:
        with self.lock:
            self.gen += 1
            devs, self.devs = dict(self.devs), {}
        for d in devs.values():                     # the probe tunnels go now: the next current one's channel follows
            subprocess.run(["ip", "link", "del", d], capture_output=True)

    def first_alive(self, order: list, exclude: str = ""):
        return next((e for e in order if e["name"] != exclude and self.alive.get(e["name"])), None)

    def _one(self, g: int, e: dict) -> None:
        import sys, time
        sys.path.insert(0, os.environ.get("FDF_PY", "/opt/fdf/lib/python"))
        import fdfram
        name = e["name"]
        while g == self.gen:
            dev = ""
            try:
                if e.get("hub"):
                    with self.lock:
                        if g != self.gen:
                            return
                        dev = _probe_up(e)
                        if dev:
                            self.devs[name] = dev
                    if not dev:
                        raise OSError("no probe tunnel")
                r = fdfram.Ram(e["ram"], device=dev or None)
                r.version()
                if g != self.gen:
                    return
                if not self.alive.get(name):
                    self.alive[name] = True
                    config.log.warning("REGION: standing: %s is alive [NO_FLAP_V1]", name)
                    if self.on_up:
                        self.on_up(e)
                last = 0
                while g == self.gen:
                    rows = r.read("fdf-space", "alive", "node", after=last, wait_s=fdfram.HOLD_S)
                    if rows:
                        last = max(x["id"] for x in rows)
            except Exception as ex:
                if g == self.gen and self.alive.get(name):
                    config.log.warning("REGION: standing: %s closed (%s)", name, ex)
                self.alive[name] = False
            if g == self.gen:
                time.sleep(2)
        with self.lock:
            d = self.devs.pop(name, None) if g == self.gen else None
        if d:
            subprocess.run(["ip", "link", "del", d], capture_output=True)


standing = Standing()


def _probe_up(entry: dict) -> str:
    names = [m.get("name") for m in system_managers()]
    dev = "fdfsp%d" % (names.index(entry["name"]) if entry["name"] in names else len(names))
    h, m = entry["hub"], entry["member"]
    if not config.PRIVKEY:
        return ""
    subprocess.run(["ip", "link", "del", dev], capture_output=True)
    try:
        if os.environ.get("FDF_WG_IMPL", "") == "wireguard-go":
            subprocess.run(["wireguard-go", dev], capture_output=True, check=True)
        else:
            subprocess.run(["ip", "link", "add", dev, "type", "wireguard"], capture_output=True, check=True)
        import tempfile
        fd, kf = tempfile.mkstemp(prefix="fdfsp-", suffix=".key")
        with os.fdopen(fd, "w") as f:
            f.write(config.PRIVKEY + "\n")
        try:
            subprocess.run(["wg", "set", dev, "private-key", kf, "peer", h["pubkey"], "endpoint", h["endpoint"],
                            "allowed-ips", h.get("addr", "10.253.253.254") + "/32"], capture_output=True, check=True)
        finally:
            os.remove(kf)
        subprocess.run(["ip", "addr", "add", m["edge"] + "/32", "dev", dev], capture_output=True, check=True)
        subprocess.run(["ip", "link", "set", dev, "up"], capture_output=True, check=True)
        return dev
    except (subprocess.CalledProcessError, OSError) as e:
        config.log.warning("REGION: probe tunnel %s toward %s not made: %s", dev, entry.get("name"), e)
        subprocess.run(["ip", "link", "del", dev], capture_output=True)
        return ""


_lan_ram = {"proc": None, "addr": ""}


def ensure_lan_ram() -> str:
    """[LAN_RAM_ALWAYS_V1] this node's own LAN RAM manager: fdf-ram on its .1, port FDF_LAN_RAM_PORT, a child of the
    daemon, so the last entry of every node's system space always answers. Its Permanent records are lan-ram.perm; the
    members on it announce again after its restart. Returns its address, or "" (said) when it could not start."""
    me = own_dot1()
    if not me:
        return ""
    addr = "%s:%d" % (me, LAN_RAM_PORT)
    p = _lan_ram["proc"]
    if p is not None and p.poll() is None and _lan_ram["addr"] == addr:
        return addr
    if p is not None and p.poll() is None:
        p.kill()
    env = dict(os.environ)
    env.pop("RINA_MAP_AREA", None)
    env["RINA_REGION_FILE"] = os.path.join(FDF_VAR, "lan-ram.perm")      # its own Permanent records (fdf-ram needs a file)
    exe = os.path.join(os.environ.get("FDF_BIN", "/opt/fdf/bin"), "fdf-ram")
    try:
        _lan_ram["proc"] = subprocess.Popen([exe, "--listen", addr, "--api", API], env=env, stdout=subprocess.DEVNULL,
                                            stderr=open(os.path.join(FDF_VAR, "lan-ram.log"), "a"))
        _lan_ram["addr"] = addr
        import socket, time as _t
        for _ in range(40):                                   # listening before the walk asks it (it binds in ~0.1 s)
            try:
                socket.create_connection((me, LAN_RAM_PORT), timeout=0.2).close()
                break
            except OSError:
                if _lan_ram["proc"].poll() is not None:
                    break
                _t.sleep(0.05)
        if _lan_ram["proc"].poll() is not None:
            config.log.error("REGION: this node's own LAN RAM manager on %s exited at start (rc %s): %s/lan-ram.log says why",
                             addr, _lan_ram["proc"].returncode, FDF_VAR)
            return ""
        config.log.warning("REGION: this node's own LAN RAM manager on %s (pid %d) [LAN_RAM_ALWAYS_V1]", addr, _lan_ram["proc"].pid)
        return addr
    except OSError as e:
        config.log.error("REGION: this node's own LAN RAM manager did not start (%s %s): %s", exe, addr, e)
        return ""


def follow(entry: dict) -> bool:
    """[SYSTEM_SPACE_V1] this node's own session (fdf-client) goes where the system space is. join.env and the ponds'
    env files stay what was configured (join.env is the region switch, [REGION_SWITCH_ONE_FILE_V1]); where the session
    is NOW is one more file its unit reads after them: system-ram.env, or ponds/<forward>.now for the forward pond.
    Written, and the session restarted, only when that differs from what the session has. Returns True if it did."""
    ram = entry["ram"]
    fwd = read_word("forward")
    if fwd:
        conf, now, unit = pond_env(fwd).get("RINA_RAM", ""), os.path.join(FDF_VAR, "ponds", fwd + ".now"), "fdf-client@%s.service" % fwd
    else:
        conf, now, unit = read_env_ram(os.path.join(FDF_VAR, "join.env")), os.path.join(FDF_VAR, "system-ram.env"), "fdf-client.service"
    has = read_env_ram(now) or conf
    if has == ram:
        return False
    tmp = now + ".tmp"
    open(tmp, "w").write("RINA_RAM=%s\n" % ram)
    os.replace(tmp, now)
    cmd = os.environ.get("FDF_CLIENT_RESTART") or "systemctl restart " + unit
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    (config.log.warning if r.returncode == 0 else config.log.error)(
        "REGION: this node's session follows the system space to %s (%s, was %s): %s%s", entry["name"], ram, has, cmd,
        "" if r.returncode == 0 else " FAILED: " + (r.stderr or r.stdout).strip()[:200])
    return True


def read_env_ram(path: str) -> str:
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln.startswith("export "):
                ln = ln[7:]
            if ln.startswith("RINA_RAM="):
                return ln.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def my_channels() -> dict:
    """[RAM_IN_THE_TUNNEL_V1] the hub channel comes from the invitation, not from the region: the region's RAM manager
    listens only inside this tunnel, so the tunnel is up first and the region is reached through it. The prefixes the
    channel carries for others (remote_subnets) are the verified mappings naming that hub, from the event's world read;
    outside an event (the bring-up before joining) none: the region itself is on the hub's address, inside the
    tunnel's 10.0.0.0/8, and the event that follows the join brings them.
    [SYSTEM_SPACE_V1] the ONE hub channel is the current system RAM manager's (use()); a LAN entry has none."""
    cur = _cur["entry"]
    if cur is not None and cur.get("lan"):
        config.log.warning("REGION: my-channels: the system space is on %s: no hub channel", cur["name"])
        me = read_facts()
        payload = json.dumps({"channels": []})
        return {"node_name": me.get("name", config.NODE_NAME), "node_id": me.get("guid", config.NODE_GUID),
                "digest": hashlib.sha256(payload.encode()).hexdigest()[:16], "channels": []}
    if cur is not None:
        inv = {"hub": cur.get("hub") or {}, "member": cur.get("member") or {}}
    else:
        inv = invitation()
    me = read_facts()
    my_sub = me.get("subnet") or own_subnet()
    channels = []
    h, m = inv.get("hub") or {}, inv.get("member") or {}
    if not (h.get("pubkey") and h.get("endpoint") and m.get("edge")):
        config.log.warning("REGION: no invitation in %s (hub pubkey, endpoint, edge): no hub channel -- join with "
                           "fdf-region-join fdfinv1.<...> [ADMITTED_BY_INVITE_V1]", HUB_JSON)
    else:
        if _held["world"] is None:
            # outside a region event (the bring-up before joining): the region is reached THROUGH this channel, so it is
            # not asked first -- a read toward 10.253.253.254 before the tunnel exists is a timeout, not an answer
            reach = {}
        else:
            reach = _reach(mappings())
            for p in participants():                  # the hub's own /24, if it is a node too
                f = p.get("facts") or {}
                if p.get("alive") and f.get("role") == "hub" and f.get("name") == h.get("name") and f.get("subnet"):
                    reach.setdefault(h.get("name", ""), set()).add(f["subnet"])
        mine = {str(ipaddress.ip_network(my_sub, strict=False))} if my_sub else set()
        remote = sorted(set(reach.get(h.get("name", ""), set())) - mine)
        channels.append({
            "channel_name": "hub-" + h.get("name", "hub"),
            "node_name": h.get("name", "hub"), "label": "hub", "subnet": "", "transit_subnets": [],
            "tunnel_name": "region-" + h.get("name", "hub"),
            "wg_config": {
                "edge_ip": m["edge"], "edge_mask": HUB_MASK,
                "droplet_ip": h.get("addr", "10.253.253.254"),
                "droplet_pubkey": h["pubkey"], "droplet_endpoint": h["endpoint"],
                "allowed_ips": ALLOWED, "remote_subnets": remote,
            },
        })
    config.log.warning("REGION: my-channels: hub=%s edge=%s channels=%d remote=%s",
                       h.get("name"), m.get("edge"), len(channels), sorted({p for c in channels for p in c["wg_config"]["remote_subnets"]}))
    payload = json.dumps({"channels": channels}, sort_keys=True, separators=(",", ":"))
    return {"node_name": me.get("name", config.NODE_NAME), "node_id": me.get("guid", config.NODE_GUID),
            "digest": hashlib.sha256(payload.encode()).hexdigest()[:16],
            "channels": channels}


# ---------------------------------------------------------------- map-register through the node's own session
def publish_plan_maps(prefixes: list) -> None:
    """[MAPS_FROM_GRAPH_V1] what this node registers = the plan's maps (its own /24s plus those of every host whose
    exit it is), toward every hub whose tunnel handshook; [] withdraws everything (a child registers nothing)."""
    f = read_facts()
    live = getattr(config, "live_hubs", {}) or {}
    ranked = sorted(live.items(), key=lambda kv: (kv[1] if kv[1] is not None else 10 ** 9, kv[0]))
    rlocs = ",".join("%s:%d:50" % (name, i + 1) for i, (name, _rtt) in enumerate(ranked))
    maps = [{"eid": p, "rlocs": rlocs} for p in sorted(set(prefixes))] if rlocs and prefixes else []
    if (f.get("maps") or []) != maps:
        write_facts({"maps": maps})
        config.log.warning("REGION: maps -> %s", [m["eid"] for m in maps])


# ---------------------------------------------------------------- the forward pond  [FORWARD_SWITCH_IN_PLACE_V1]
# PLAN-PONDS §3.2: /opt/fdf/var/ponds/<area>.env says where each pond is; `forward` names the one whose graph this daemon
# applies; `fdf-forward` writes `forward.want` and this daemon makes the switch, in an order that leaves nothing behind:
# its maps cleared, the old pond withdrawn, THEN `forward` written (each fdf-client@<area> follows it), then the new pond.
FDF_VAR = os.environ.get("FDF_VAR", "/opt/fdf/var")
_events_conn = None
_events_end = False


def pond_env(area: str) -> dict:
    """ponds/<area>.env as {RINA_RAM, RINA_AREA, RINA_SITE_KEY}; {} if there is no such pond."""
    out = {}
    try:
        for ln in open(os.path.join(FDF_VAR, "ponds", area + ".env")):
            ln = ln.strip()
            if ln.startswith("export "):
                ln = ln[7:]
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def read_word(name: str) -> str:
    try:
        return open(os.path.join(FDF_VAR, name)).read().strip()
    except OSError:
        return ""


def write_word(name: str, word: str) -> None:
    path = os.path.join(FDF_VAR, name); tmp = path + ".tmp"
    open(tmp, "w").write(word + "\n"); os.replace(tmp, path)


def end_events() -> None:
    """End the held read on the members' family: the loop's events() returns "gone" and the loss path runs."""
    global _events_end
    _events_end = True
    c = _events_conn
    if c is not None and c.sock is not None:
        try:
            c.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ---------------------------------------------------------------- change: the members' own cells
def events():
    """Yields "ready" once the members' family is read (its newest id known), then "p" each time a held read on it
    returns rows -- a member joining, its facts changing (fdf-client writes them AFTER its mappings), or gone (its
    session ended, and its mappings with it) -- and "gone" when the read fails: the region is gone.
    [MEMBER_IS_A_TUPLE_V1][EVENTS_NOT_TIMERS_V1] (John 2026-11-03: "no notifications ... not like a messaging thing")
    each member's own truth is (pond, member, <its ns>), written on its session; this is one held read on that family,
    over HTTP. (Was fdf-events: the region's participant and mapping notices relayed as lines by a child process.)
    [WATCH_BEFORE_READ_V1] "ready" comes after the family's newest id is taken: the caller reads the world after it,
    so a change between the two is past that id and returns the next held read at once."""
    global _events_conn, _events_end
    _events_end = False
    host, _, port = config.REGION_RAM.rpartition(":")
    sys.path.insert(0, os.environ.get("FDF_PY", "/opt/fdf/lib/python"))
    import fdfram                                        # its connection: TCP keepalive, a vanished RAM said in ~5 s
    def read(after, wait_s):
        global _events_conn
        c = fdfram._Bound(host, int(port), None, 15 + wait_s)   # [KEEPALIVE_5S_V1] not a 40 s HTTP timeout
        _events_conn = c
        try:
            q = "?op=read&service=pond&variable=member" + ("&after=%d&wait_s=%g" % (after, wait_s) if after >= 0 else "")
            c.request("GET", API + q)
            r = c.getresponse(); body = r.read()
            if r.status != 200:
                raise RuntimeError("HTTP %d: %s" % (r.status, body.decode(errors="replace")[:160]))
            return json.loads(body.decode())["cells"]        # plain HTTP: the RAM names them cells
        finally:
            _events_conn = None
            c.close()
    try:
        last = max([int(c.get("id", 0)) for c in read(-1, 0)] + [0])
    except Exception as e:
        config.log.warning("REGION: the members' family at %s: %s", config.REGION_RAM, e)
        yield "gone"
        return
    yield "ready"
    while not _events_end:
        try:
            rows = read(last, 25)
        except Exception as e:
            if not _events_end:
                config.log.warning("REGION: the held read on the members' family at %s ended: %s", config.REGION_RAM, e)
            break
        if _events_end:
            break
        if rows:
            last = max([last] + [int(c.get("id", 0)) for c in rows])
            yield "p"
    yield "gone"
