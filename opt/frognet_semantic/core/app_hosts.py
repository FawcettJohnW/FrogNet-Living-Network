################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/app_hosts.py -- [APP_HOST_ELECTION_V1] the AppHost of every AppRam, written into /etc/hosts by the election
(the doc "FrogNet SystemRam, FrogNetHost Admin and App Services -- Design", 2026-11-04, sections 5, 7 and 9).

Every AppRam is a service: the general one is `<app>Host.frognet`, a Team's is `<team>Host.<app>` (John 2026-11-04: scoped to its app, so two apps' Teams of one name never share one). Its AppHost is
one handler's pick per service (AppHostHandler), a pure function of the tuples, so every node writes the same line:

  * the configuration tuples, in databasehost-control (databasehost_control.frognet's RAM host): the app's one
    (fdf-app, config, <app>) -- {config: text (rams, ports, select ...), seq, mac, user, admin} -- and a Team lead's
    (fdf-app, config, <app>.<team>) -- {at: here|databasehost, guid, seq, user};
  * the capability tuples, in the SystemRam (databasehost.frognet's RAM host): (fdf-app, can, <app>.<ram>.<guid>),
    each held by its service's session (`ram hold`): a service that is gone is no candidate.

The candidates are the live .1 FrogNetHosts of the converged block whose service offers the RAM. A Team its lead put
"here" is served by the lead's device: its line names that device when its capability tuple is there (a Pi with a
router: a node, its own .1), and no line otherwise (a phone: the members select it from its capability tuple). A read
that fails is not an empty pool ([READ_FAILED_IS_NOT_EMPTY_V1]): the standing lines are kept.

[CONFIG_WRITTEN_AGAIN_BY_THE_MERGE_V1] (John 2026-11-04: "Rewriting the config tuple should be a part of the merge
process") when databasehost-control moves, the merge writes each configuration tuple into the new one, as it was
signed: from the copies the installed machines keep (FDF_APPS/<app>.json "signed", "places_signed"), only where the
new one holds none or an older one.
"""
import json
import os
import re
import sys

PORT = int(os.environ.get("FDF_LAN_RAM_PORT", "8841"))
APPS = os.environ.get("FDF_APPS", "/opt/fdf/apps")
CONTROL = "databasehost_control.frognet"
SYSTEM = "databasehost.frognet"
SVC = "fdf-app"


def _lib():
    p = os.environ.get("FDF_PY") or "/opt/fdf/lib/python"
    if p not in sys.path:
        sys.path.insert(0, p)
    import fdfram, fdf_select           # noqa: E401
    return fdfram, fdf_select


def service_name(app, ram):
    return app if ram == "general" else ram


def host_name(app, ram):
    """the general AppRam's <app>Host.frognet; a Team's <team>Host.<app>"""
    return "%sHost.frognet" % app if ram == "general" else "%sHost.%s" % (ram, app)


_APP_HOST = re.compile(r"^[A-Za-z0-9_-]+Host\.[A-Za-z0-9_-]+$")


def is_app_host_line(line):
    """a line the AppHost election writes: <app>Host.frognet or <team>Host.<app> (never a FrogNetHost.<node> line)"""
    p = line.split("#", 1)[0].split()
    return len(p) >= 2 and any(_APP_HOST.match(t) and not t.startswith("FrogNetHost.") for t in p[1:])


def ip_of(etc_hosts, name):
    for l in etc_hosts or []:
        p = l.split("#", 1)[0].split()
        if len(p) >= 2 and name in p[1:]:
            return p[0]
    return ""


def alive_ones(etc_hosts):
    out = []
    for l in etc_hosts or []:
        p = l.split()
        if len(p) >= 2 and p[0].endswith(".1") and any(t.startswith("FrogNetHost.") for t in p[1:]) and p[0] not in out:
            out.append(p[0])
    return out


def parse_config(bag):
    """the app's configuration from its one tuple's bag (the signed text), or None"""
    try:
        c = json.loads(str((bag or {}).get("config") or ""))
        return c if isinstance(c, dict) else None
    except ValueError:
        return None


class AppHostHandler:
    """one service's election: the general AppRam's (the app's selection) or a Team's (its lead's place, else the
    app's selection)"""
    def __init__(self, app, ram, cfg, place):
        self.app, self.ram, self.cfg, self.place = app, ram, cfg, place or {}

    def evaluate(self, cands, select):
        """cands: [(guid, bag)] -- the live service instances; the winning (guid, bag) or None"""
        if self.ram != "general" and self.place.get("at") == "here":
            g = self.place.get("guid", "")
            return next(((x, b) for x, b in cands if x == g), None)
        g = select(self.cfg.get("select") or {}, self.ram, cands)
        return next(((x, b) for x, b in cands if x == g), None) if g else None


def read_world(etc_hosts):
    """{app: (cfg, {team: place})}, {(app, ram): [(guid, bag)]} from databasehost-control and the SystemRam; raises
    fdfram.RamError when either does not answer"""
    fdfram, _ = _lib()
    ctl, sysr = ip_of(etc_hosts, CONTROL), ip_of(etc_hosts, SYSTEM)
    if not ctl or not sysr:
        raise fdfram.RamError("the block names no %s" % (CONTROL if not ctl else SYSTEM))
    apps, places = {}, {}
    for c in fdfram.Ram("%s:%d" % (ctl, PORT)).read(SVC, "config"):
        if c.get("gone") or not isinstance(c.get("bag"), dict) or not c["bag"]:
            continue
        i = str(c.get("instance", ""))
        if "." in i:
            a, t = i.split(".", 1)
            places.setdefault(a, {})[t] = c["bag"]
        else:
            cfg = parse_config(c["bag"])
            if cfg is not None:
                apps[i] = cfg
    cans = {}
    for c in fdfram.Ram("%s:%d" % (sysr, PORT)).read(SVC, "can"):
        b = c.get("bag")
        if c.get("gone") or not isinstance(b, dict) or not b:
            continue
        p = str(c.get("instance", "")).split(".")
        if len(p) >= 3:
            cans.setdefault((p[0], p[1]), []).append((b.get("guid") or ".".join(p[2:]), b))
    # [GROUP_PLACE_V1] a Team's place counts only as its lead's: the user is in the configuration as that Team's lead
    out = {}
    for a, cfg in apps.items():
        ok = {}
        for t, p in places.get(a, {}).items():
            u = (cfg.get("users") or {}).get(p.get("user", "")) or {}
            if u.get("lead") and u.get("role") == t and p.get("at") in ("here", "databasehost"):
                ok[t] = p
        out[a] = (cfg, ok)
    return out, cans


def app_host_lines(etc_hosts, logger=None):
    """the `<ip> <service>Host.frognet` lines for every AppRam of every app configured; None when a read failed"""
    log = logger or (lambda s: None)
    fdfram, fdf_select = _lib()
    try:
        world, cans = read_world(etc_hosts)
    except fdfram.RamError as e:
        log("APP_HOST_ELECT decision=ABSTAIN reason=read_failed err=%s -- the standing AppHost lines are kept" % e)
        return None
    live = set(alive_ones(etc_hosts))
    lines = []
    for app, (cfg, places) in sorted(world.items()):
        for ram in cfg.get("rams") or []:
            h = AppHostHandler(app, ram, cfg, places.get(ram))
            pool = [(g, b) for g, b in cans.get((app, ram), []) if str(b.get("addr", "")) in live]
            w = h.evaluate(pool, fdf_select.select)
            name = host_name(app, ram)
            if w and w[1].get("addr"):
                lines.append("%s %s" % (w[1]["addr"], name))
                log("APP_HOST_ELECT service=%s winner=%s (%s) candidates=%s" % (name, w[1]["addr"], w[0], sorted(g for g, _ in pool)))
            else:
                log("APP_HOST_ELECT service=%s winner=NONE candidates=%s%s" % (name, sorted(g for g, _ in pool),
                    " (its lead's device %s, not a node: its members select it from its capability tuple)" % h.place.get("guid")
                    if h.place.get("at") == "here" else ""))
    return lines


def commit(etc_hosts, hosts_path="/etc/hosts", logger=None):
    """etc_hosts with every <service>Host.frognet line elected again (the standing ones when a read failed)"""
    new = app_host_lines(etc_hosts, logger)
    if new is None:
        try:
            new = [l.rstrip("\n") for l in open(hosts_path) if is_app_host_line(l)]
        except OSError:
            new = []
    return [l for l in etc_hosts if not is_app_host_line(l)] + new


def write_configs_again(control_ip, logger=None):
    """[CONFIG_WRITTEN_AGAIN_BY_THE_MERGE_V1] the configuration tuples, as signed, into databasehost-control at
    control_ip: each one this machine keeps a copy of, where that databasehost-control holds none or an older one"""
    log = logger or (lambda s: None)
    fdfram, _ = _lib()
    rm = fdfram.Ram("%s:%d" % (control_ip, PORT))
    try:
        names = sorted(n for n in os.listdir(APPS) if n.endswith(".json"))
    except FileNotFoundError:
        return 0
    try:
        have = {c.get("instance", ""): c.get("bag") or {} for c in rm.read(SVC, "config") if not c.get("gone")}
    except fdfram.RamError as e:
        log("CONFIG_AGAIN not written: databasehost-control %s did not answer (%s); the next merge writes them" % (control_ip, e))
        return 0
    n = 0
    for nm in names:
        try:
            with open(os.path.join(APPS, nm)) as f:
                c = json.load(f)
        except (OSError, ValueError):
            continue
        app = nm[:-5]
        mine = [(app, c.get("signed"))] + [("%s.%s" % (app, t), b) for t, b in (c.get("places_signed") or {}).items()]
        for inst, bag in mine:
            if not isinstance(bag, dict) or not bag:
                continue
            if int((have.get(inst) or {}).get("seq") or 0) >= int(bag.get("seq") or 0):
                continue
            try:
                rm.write(SVC, "config", inst, bag)
                n += 1
                log("CONFIG_AGAIN (fdf-app, config, %s) seq %s written into databasehost-control %s, as signed" % (inst, bag.get("seq"), control_ip))
            except fdfram.RamError as e:
                log("CONFIG_AGAIN (fdf-app, config, %s) not written (%s); the next merge writes it" % (inst, e))
    return n
