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
  events()                      -> lines from fdf-events (one held FNWP session): "p" participants changed, "m" a
                                   mapping changed, "gone" the region is gone. No timer.

Config: RINA_RAM=host:8840 (region mode on) from /opt/fdf/var/join.env and RINA_AREA=<area name> from site.env, one
file each [REGION_SWITCH_ONE_FILE_V1] (config.load_config). Facts file: FDF_FACTS (default /opt/fdf/var/facts.json). Tools: FDF_BIN (default
/opt/fdf/bin).
"""
import hashlib
import ipaddress
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import config

API = "/FawcettInnovations.RINA.ram_interface.php"
BUILD_TAG = "fdf-2026-10-27-r34"      # [FDF_VERSION_V1] keep equal to src/fdf_version.hpp
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


def my_channels() -> dict:
    """[RAM_IN_THE_TUNNEL_V1] the hub channel comes from the invitation, not from the region: the region's RAM manager
    listens only inside this tunnel, so the tunnel is up first and the region is reached through it. The prefixes the
    channel carries for others (remote_subnets) are the verified mappings naming that hub, from the event's world read;
    outside an event (the bring-up before joining) none: the region itself is on the hub's address, inside the
    tunnel's 10.0.0.0/8, and the event that follows the join brings them."""
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
_events_proc = None


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
    """End the held fdf-events session: the loop's events() returns and the loss path runs."""
    p = _events_proc
    if p is not None:
        try:
            p.kill()
        except OSError:
            pass


# ---------------------------------------------------------------- change notification
def events():
    """Yields "ready" once fdf-events has registered its watches, then "p", "m" or "gone" as the region reports them.
    Runs fdf-events; its exit = the region is gone.
    [WATCH_BEFORE_READ_V1] "ready" is yielded, not swallowed: the caller reads the world only AFTER it, so a change
    between the read and the watch cannot be missed (it arrives as an event, queued in the pipe while the read runs)."""
    fdf_bin = os.environ.get("FDF_BIN", "/opt/fdf/bin")
    env = dict(os.environ, RINA_RAM=config.REGION_RAM)
    p = subprocess.Popen([os.path.join(fdf_bin, "fdf-events")], stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
    global _events_proc
    _events_proc = p                    # [FORWARD_SWITCH_IN_PLACE_V1] end_events() ends this session
    try:
        for line in p.stdout:
            # [EVENT_KIND_FIRST_FIELD_V1] fdf-events says "<ready|p>\t<ms>\t<snapshot>" since r32; the kind is yielded
            # (the callers compare it to "ready", "p", "m", "gone" and read the world themselves)
            line = line.strip().split("\t", 1)[0]
            yield line
            if line == "gone":
                return
    finally:
        try:
            p.kill()
        except OSError:
            pass
    yield "gone"
