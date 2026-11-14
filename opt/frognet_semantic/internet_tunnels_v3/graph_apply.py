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
"""[GRAPH_APPLY_V1] turn one host's graph plan into the kernel and the files, by diff, with admission and hold-down.

The table decides (PLAN-RAM-DISCOVERY, contracts 3 and 5; [ALIVE_IS_THE_SESSION_V1] r18):
  - a planned route is installed at once: its member is alive (its session is up) and its link is a held/given lease
    pair both ends wrote. No echo probe (r5-r17 sent one per newly admitted route; RAM already says it). The hub
    link's handshake age is checked every rebuild; a dead handshake withdraws the hub routes at once.
  - a route/name whose row merely vanished from the RAM is kept for HOLD_DOWN_SEC (a session blip is 20 s) and
    removed only if still absent then (hold-down). A positive failure (hub handshake dead, region gone) is never
    held: removed now.
  - files are written only when their content differs; dnsmasq is restarted only then; runMerge is requested only
    when something was applied.

Everything that touches the machine goes through `World`, so tests/graph_apply_oracle.py drives this with a fake.
"""
import time
from typing import Dict, List, Optional, Set, Tuple

import os
import sys
FDF_PY = os.environ.get("FDF_PY", "/opt/fdf/lib/python")      # FD&F owns the graph; FrogNet applies it
if FDF_PY not in sys.path:
    sys.path.insert(0, FDF_PY)
from fdf_graph import Graph                                     # noqa: E402

HOLD_DOWN_SEC = 60.0
ROUTE_METRIC = 22                 # the tunnel-bringup metric the planner/committer use for /24s
ADMISSION_METRIC = 6              # the probe's temporary /32, same as discovery's probe_install


def _build() -> str:
    try:
        from fdf_graph import BUILD_TAG
        return BUILD_TAG
    except Exception:
        return "?"


class World:
    """What the applier is allowed to touch. Override in tests."""
    def __init__(self, config=None, log=None):
        self.config = config
        self.log = log
        # [REGION_SRC_IS_MY_DOT1_V1] my own .1: the source of everything this node itself sends into the region. Without
        # it the kernel sources hub-route traffic from wg0's 10.253 edge address, and a member whose /24 the hub has
        # placed behind another peer answers a 10.253 destination out of its own tunnel -- which the hub refuses
        # (HappyDog -> Seattle3B 15:40: pings and store reads dead from HappyDog only; NY2 behind it, sourced 10.28.28.1,
        # fine). Set by the applier from the plan; empty = no src.
        self.src_ip = ""
        self.stats = {"route_cmds": 0, "route_cmds_ms": 0, "probes": 0, "probes_ms": 0}     # [EVENT_TIMED_V1] per rebuild

    def _ip(self, cmd) -> bool:
        import subprocess
        t0 = time.monotonic()
        try:
            return subprocess.run(cmd, capture_output=True).returncode == 0
        finally:
            self.stats["route_cmds"] += 1; self.stats["route_cmds_ms"] += int((time.monotonic() - t0) * 1000)

    # ---- kernel
    def routes(self) -> Set[Tuple[str, str, str]]:
        """{(dest, via, dev)} of the routes we own (metric ROUTE_METRIC)."""
        import subprocess
        out = set()
        try:
            txt = subprocess.check_output(["ip", "-4", "route", "show"], text=True)
        except Exception:
            return out
        for ln in txt.splitlines():
            p = ln.split()
            if "metric" in p and p[p.index("metric") + 1] == str(ROUTE_METRIC) and p[0] != "default":
                via = p[p.index("via") + 1] if "via" in p else ""
                dev = p[p.index("dev") + 1] if "dev" in p else ""
                src = p[p.index("src") + 1] if "src" in p else ""
                if not via and self.src_ip and src != self.src_ip:
                    continue                      # a hub route without my src: not ours yet; the applier re-installs it
                out.add((p[0], via, dev))
        return out

    def route_replace(self, dest: str, via: str, dev: str, metric: int = ROUTE_METRIC) -> bool:
        import subprocess
        cmd = ["ip", "route", "replace", dest] + (["via", via] if via else []) + ["dev", dev, "metric", str(metric)]
        if not via and self.src_ip and metric == ROUTE_METRIC:
            cmd += ["src", self.src_ip]           # REGION_SRC_IS_MY_DOT1_V1
        return self._ip(cmd)

    def route_del(self, dest: str, via: str, dev: str, metric: int = ROUTE_METRIC) -> bool:
        import subprocess
        cmd = ["ip", "route", "del", dest] + (["via", via] if via else []) + ["dev", dev, "metric", str(metric)]
        return self._ip(cmd)

    def has_default(self) -> bool:
        import subprocess
        try:
            return bool(subprocess.check_output(["ip", "-4", "route", "show", "default"], text=True).strip())
        except Exception:
            return False

    def default_set(self, via: str, dev: str) -> bool:
        import subprocess
        return self._ip(["ip", "route", "replace", "default", "via", via, "dev", dev, "metric", str(ROUTE_METRIC)])

    def default_del(self) -> bool:
        import subprocess
        return self._ip(["ip", "route", "del", "default", "metric", str(ROUTE_METRIC)])

    def hub_alive(self, iface: str) -> bool:
        from .wg import wg_handshake_age
        age = wg_handshake_age(iface)
        dead = getattr(self.config, "HANDSHAKE_DEAD_SEC", 600)
        return age is not None and age < dead

    # ---- files
    def write_if_changed(self, path: str, content: str) -> bool:
        import os
        try:
            if open(path).read() == content:
                return False
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".graph.tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)
        return True

    def now(self) -> float:
        return time.monotonic()

    def say(self, msg: str) -> None:
        if self.log:
            self.log.warning(msg)
        else:
            print(msg)


class Applier:
    """Holds the last applied set and the hold-down timers between rebuilds (one instance per daemon)."""

    def __init__(self, world: World, me: str, hosts_sentinel: str = ""):
        self.w = world
        self.me = me
        self.host_facts: Dict[str, dict] = {}                            # .1 -> that member's last facts (hosts json)
        self.my_dot1 = ""
        self.hosts_sentinel = hosts_sentinel or os.environ.get("FROGNET_REGION_HOSTS", "/var/lib/frognet/region_hosts")   # NOT /etc/sentinels: runMerge wipes that dir at start
        self.applied_routes: Set[Tuple[str, str, str]] = set()       # (dest, via, dev) we installed
        self.applied_default: Optional[Tuple[str, str]] = None
        self.applied_hosts: Dict[str, str] = {}                        # ip -> name
        self.admitted: Dict[Tuple[str, str, str], bool] = {}           # (dest, via, dev) -> probe verdict
        self.missing_since: Dict[object, float] = {}                   # item -> when it first went missing (hold-down)
        self.gen = 0
        self.last_maps: List[str] = []
        self.alive: Dict[str, str] = {}                                # host -> its first .1, as of the last rebuild
        self.facts_sig: Dict[str, str] = {}                            # participant -> hash of its facts, to name who changed

    # ------------------------------------------------------------------ one rebuild
    def apply(self, participants: list, positive_failures: Set[str] = frozenset()) -> dict:
        """positive_failures: {"region"} (region gone), {"hub"} (hub link dead) -- withdrawn now, no hold-down."""
        g = Graph(participants)
        plan = g.plan(self.me) if self.me in g.hosts else {"routes": [], "default": None, "hosts": [], "maps": [],
                                                           "hub_routes": [], "conflicts": g.conflicts, "exit": None,
                                                           "hub_iface": "", "hub_active": False}
        own = plan.get("own") or []
        self.w.src_ip = (own[0].split("/")[0].rsplit(".", 1)[0] + ".1") if own else getattr(self.w, "src_ip", "")
        now = self.w.now()
        self.gen += 1
        if hasattr(self.w, "stats"):
            self.w.stats.update({"route_cmds": 0, "route_cmds_ms": 0, "probes": 0, "probes_ms": 0})
        changed = {"routes_added": [], "routes_removed": [], "routes_refused": [], "default": None,
                   "hosts_added": [], "hosts_removed": [], "held": [], "maps": plan["maps"], "hub_active": plan["hub_active"],
                   "own": list(plan.get("own") or [])}
        failures = set(positive_failures)
        if plan["hub_active"] and plan["hub_iface"] and not self.w.hub_alive(plan["hub_iface"]):
            failures.add("hub")

        # ---- the wanted set
        want_routes: Set[Tuple[str, str, str]] = set()
        for dest, via, dev in plan["routes"]:
            if dev:
                want_routes.add((dest, via, dev))
        if plan["hub_active"] and "hub" not in failures and "region" not in failures:
            for dest in plan["hub_routes"]:
                want_routes.add((dest, "", plan["hub_iface"]))
        want_default = plan["default"] if "region" not in failures else None
        # [DEFAULT_IS_FROGNETS_V1] FrogNet owns installed routes: its exit ladder (fixDefaultRoute) installs the default
        # toward the exit host it finds in the names we supply, and sweeps any other default toward that via. The
        # applier sets a default ONLY when the kernel has none at all (a child whose DHCP default is gone: bootstrap),
        # and leaves it alone once any default exists -- otherwise the two fight every merge (seen on Seattle7 13:02).
        if want_default is not None and self.applied_default is None and self.w.has_default():
            want_default = None
        want_hosts: Dict[str, str] = dict(plan["hosts"]) if "region" not in failures else {}

        # ---- admission  [ALIVE_IS_THE_SESSION_V1] (John 2026-10-10: "frognet_echo can be returned from RAM, and the
        # aliveness checks"). A LAN route is in the plan only because its member is alive in the region -- its session
        # is up, which is the liveness the whole system runs on -- and the link to it is a held lease and a given lease
        # agreeing on (ip, mac), written by both ends. The frognet_echo.php probe to its .1 through the next hop (3 s
        # ceiling, per newly admitted route) asked the network what RAM already says. Not run: every planned route is
        # admitted. Hub routes stay gated on the hub link's handshake (the kernel's, local).
        plan_wanted = set(want_routes)                    # [APPLIED_IS_THE_KERNEL_V1] before admission filters it
        for r in sorted(want_routes):
            if r not in self.applied_routes:
                self.admitted[r] = True
        want_routes = {r for r in want_routes if self.admitted.get(r, False)}
        # a host whose own .1 was refused gets no name either (HOSTS_GATE kept)
        refused_nets = {dest for dest, _v, _d in changed["routes_refused"]}
        want_hosts = {ip: n for ip, n in want_hosts.items() if ip.rsplit(".", 1)[0] + ".0/24" not in refused_nets}

        # [APPLIED_IS_THE_KERNEL_V1] what this applier installed is what the KERNEL still holds, not what it remembers:
        # a route goes from the kernel by itself when its interface goes (the hub channel torn down with this node's
        # session and built again on its return), and a remembered one is never re-installed -- found 2026-10-10
        # (netns-reach-test 2d): g's session ended and came back; g kept "holding" its hub route to a leaf's /32 that the
        # kernel had dropped with wg0, and never routed that leaf again. Each pass: a remembered route the kernel no
        # longer has is forgotten here, so a wanted one is installed again below, now.
        try:
            import ipaddress as _ia
            def _n(r): return (str(_ia.ip_network(r[0], strict=False)), r[1], r[2])
            kernel = {_n(r) for r in self.w.routes()}
            lost = {r for r in self.applied_routes if _n(r) not in kernel}
        except ValueError:
            lost = set()
        if lost:
            self.w.say("GRAPH kernel no longer holds %s (its interface went): forgotten, re-installed now if still wanted" % sorted(lost))
            self.applied_routes -= lost
            for r in lost: self.admitted.pop(r, None); self.missing_since.pop(("route", r), None)
            want_routes = {r for r in want_routes if r not in lost} | {r for r in lost if r in plan_wanted}
            for r in lost & plan_wanted: self.admitted[r] = True
        # ---- install what is new
        for r in sorted(want_routes - self.applied_routes):
            if self.w.route_replace(*r):
                self.applied_routes.add(r)
                changed["routes_added"].append(r)
                self.missing_since.pop(("route", r), None)
        if want_default != self.applied_default:
            if want_default is None:
                held = "region" not in failures and self._hold(("default", self.applied_default), now)
                if not held:
                    self.w.default_del()
                    changed["default"] = ("removed", self.applied_default)
                    self.applied_default = None
                else:
                    changed["held"].append(("default", self.applied_default))
            else:
                if self.w.default_set(*want_default):
                    changed["default"] = ("set", want_default)
                    self.applied_default = want_default
                    self.missing_since.pop(("default", want_default), None)
        else:
            self.missing_since.pop(("default", want_default), None)

        # ---- remove what is gone: now on a positive failure, after hold-down on mere absence
        for r in sorted(self.applied_routes - want_routes):
            is_hub = r[1] == ""
            forced = ("hub" in failures and is_hub) or "region" in failures or r in changed["routes_refused"]
            if forced or not self._hold(("route", r), now):
                self.w.route_del(*r)
                if True:
                    self.applied_routes.discard(r)
                    self.admitted.pop(r, None)
                    changed["routes_removed"].append(r)
                    self.missing_since.pop(("route", r), None)
            else:
                changed["held"].append(("route", r))

        # ---- hosts: added now, removed after hold-down (or now on region loss)
        new_hosts = dict(self.applied_hosts)
        for ip, name in want_hosts.items():
            if self.applied_hosts.get(ip) != name:
                new_hosts[ip] = name
                changed["hosts_added"].append((ip, name))
            self.missing_since.pop(("host", ip), None)
        for ip in list(self.applied_hosts):
            if ip in want_hosts:
                continue
            if "region" in failures or not self._hold(("host", ip), now):
                new_hosts.pop(ip, None)
                changed["hosts_removed"].append((ip, self.applied_hosts[ip]))
                self.missing_since.pop(("host", ip), None)
            else:
                changed["held"].append(("host", ip))
        hosts_written = False
        if new_hosts != self.applied_hosts:
            self.applied_hosts = new_hosts
            hosts_written = self.w.write_if_changed(self.hosts_sentinel, self._hosts_content(new_hosts))
        changed["hosts_written"] = hosts_written
        self._write_hosts_json(new_hosts, participants)

        # ---- who changed since the last rebuild (facts hash per live participant): names the source of an event storm
        import hashlib as _hl, json as _js
        sig = {}
        for p in participants:
            if p.get("alive") and p.get("name"):
                sig[p["name"]] = _hl.sha256(_js.dumps(p.get("facts") or {}, sort_keys=True).encode()).hexdigest()[:8]
        changed["changed_members"] = sorted(n for n, h in sig.items() if self.facts_sig.get(n) != h) + sorted("-" + n for n in self.facts_sig if n not in sig)
        self.facts_sig = sig
        # [GRAPH_SAYS_WHAT_CHANGED_V1] a name on the changed list is not a diagnosis. Per changed member: which facts
        # keys differ, and for addrs the rows added/removed (kind iface ip value) -- the storm names its own content.
        facts_now = {p["name"]: (p.get("facts") or {}) for p in participants if p.get("alive") and p.get("name")}
        diffs = []
        for n in changed["changed_members"]:
            if n.startswith("-"):
                continue
            old_f, new_f = getattr(self, "facts_last", {}).get(n, {}), facts_now.get(n, {})
            keys = sorted(k for k in set(old_f) | set(new_f) if old_f.get(k) != new_f.get(k))
            rowkey = lambda r: "%s %s %s %s" % (r.get("kind"), r.get("iface"), r.get("ip"), r.get("value"))
            o_rows = {rowkey(r) for r in old_f.get("addrs") or []}; n_rows = {rowkey(r) for r in new_f.get("addrs") or []}
            part = "%s keys=%s" % (n, keys)
            if n_rows - o_rows or o_rows - n_rows:
                part += " +%s -%s" % (sorted(n_rows - o_rows), sorted(o_rows - n_rows))
            diffs.append(part)
        self.facts_last = facts_now
        if diffs:
            self.w.say("GRAPH changed: " + " | ".join(diffs))
        # ---- membership transitions (who came, who went) -- what the merge's elections must see at once, hold-down or not
        alive_now = {h: (sorted(g.served.get(h, set()))[0].split("/")[0].rsplit(".", 1)[0] + ".1" if g.served.get(h) else g.leaf_ip.get(h, ""))
                     for h in g.hosts}
        changed["alive_hosts"] = sorted(want_hosts.items())           # the live .1s and names, NO hold-down: for elections
        changed["went"] = sorted((h, ip) for h, ip in self.alive.items() if h not in alive_now)
        changed["came"] = sorted((h, ip) for h, ip in alive_now.items() if h not in self.alive)
        self.alive = alive_now
        # ---- say it, once
        applied_something = bool(changed["routes_added"] or changed["routes_removed"] or changed["default"] or hosts_written)
        changed["applied"] = applied_something
        changed.update(getattr(self.w, "stats", {}))
        changed["maps_changed"] = plan["maps"] != self.last_maps
        self.last_maps = list(plan["maps"])
        # [GEN_V1] the generation counter moves only on an impactful change, named. One bump per rebuild, the first
        # reason wins the name; the rest are in the GRAPH line anyway. Announced as the `gen` fact (the region is the
        # shared database; nobody is notified over a tunnel, every member reads the world's gen from the region).
        impact = ([("routes_changed", changed["routes_added"] or changed["routes_removed"]),
                   ("default_changed", changed["default"]),
                   ("topology_hosts_changed", hosts_written),
                   ("member_came", changed["came"]), ("member_went", changed["went"]),
                   ("maps_changed", changed["maps_changed"] and self.gen > 1)])
        why = next((n for n, v in impact if v), None)
        changed["impact"] = [n for n, v in impact if v]
        if why:
            try:
                from . import gen as _gen
                changed["gen"] = _gen.bump(why, "applier", log=self.w.say)
            except Exception as e:
                self.w.say("GEN: bump failed: %s" % e)
        try:
            from . import gen as _gen
            gen_now = _gen.read()
        except Exception:
            gen_now = -1
        self.w.say("GRAPH build=%s rebuild=%d gen=%d %s me=%s exit=%s default=%s routes=+%d/-%d refused=%d hosts=+%d/-%d maps=%s held=%s changed=%s%s" % (_build(), 
            self.gen, gen_now, g.summary(), self.me, plan["exit"], want_default, len(changed["routes_added"]), len(changed["routes_removed"]),
            len(changed["routes_refused"]), len(changed["hosts_added"]), len(changed["hosts_removed"]), plan["maps"],
            changed["held"], changed["changed_members"], (" FAILURES=%s" % sorted(failures)) if failures else ""))
        if changed["went"] or changed["came"]:
            self.w.say("GRAPH membership: went=%s came=%s" % (changed["went"], changed["came"]))
        for r in changed["routes_refused"]:
            self.w.say("GRAPH admission refused: %s via %s dev %s (no echo through that next hop; retried next event)" % r)
        for ip, hs in plan["conflicts"]:
            self.w.say("GRAPH conflict: %s is claimed by %s -- routed by nobody, named by nobody" % (ip, hs))
        # [REACH_V1] what this machine reaches in the ponds it is a participant in was planned against THIS pond's
        # routes: when they change, what collides may have changed -- fdf-reach plans every reach again (and ends the
        # session of one that is now refused: a failed reach closes it)
        # Run beside this loop, never inside it, and never killed half-way (a slow pond would otherwise hold the daemon or
        # leave a reach half-built): fdf-reach serialises itself ([REACH_ONE_AT_A_TIME_V1]); its lines land in this log.
        if (changed["routes_added"] or changed["routes_removed"]) and self._reaching():
            import subprocess, threading
            try:
                pr = subprocess.Popen([os.path.join(os.environ.get("FDF_BIN", "/opt/fdf/bin"), "fdf-reach"), "replan"],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                def _said(pr=pr, say=self.w.say):
                    for ln in pr.stdout: say("REACH: " + ln.rstrip())
                    pr.wait()
                threading.Thread(target=_said, daemon=True).start()
            except OSError as e:
                self.w.say("REACH: replan failed to run: %s" % e)
        return changed

    def _reaching(self) -> bool:
        d = os.path.join(os.environ.get("FDF_VAR", "/opt/fdf/var"), "reach")
        try: return any(f.endswith(".json") for f in os.listdir(d))
        except OSError: return False

    # ------------------------------------------------------------------ helpers
    def _hold(self, item, now: float) -> bool:
        """True while the item is within its hold-down (keep it). Callers force removal on a positive failure."""
        t0 = self.missing_since.setdefault(item, now)
        return (now - t0) < HOLD_DOWN_SEC

    def _write_hosts_json(self, hosts: Dict[str, str], participants: list) -> None:
        """[HOSTJSON_FROM_THE_REGION_V1] <region_hosts>.json: what makeHostJson.bash built for getHosts.php --
        [{"ip","hostname","echo"}] -- from the region instead of /etc/hosts plus a frognet_echo.php curl per peer.
        The hosts are this node's applied plan (region_hosts) plus itself; each echo is that member's line from its
        facts, the same one RegionEcho and frognet_echo.php answer: "<name>,<.1>,<upstream1>,<upstream2>". A held-down host keeps
        the facts it last had. Written only when the content changes."""
        import json as _js
        def dot1s(f):                                       # every /24 a member serves has its own hosts line
            return [r["ip"] for r in f.get("addrs") or [] if r.get("kind") == "own" and str(r.get("ip", "")).endswith(".1")]
        alive_ips = set()                                   # [DEVICE_ROW_V1] whose member's session is up THIS event
        for p in participants:
            if p.get("alive"):
                f = p.get("facts") or {}
                ips = dot1s(f)
                if f.get("role") == "leaf" and f.get("addr"):  # a leaf is its addr ([LEAF_ROW_FROM_ADDR_V1])
                    ips = ips + [f["addr"].split("/")[0]]
                for ip in ips:
                    self.host_facts[ip] = f
                    alive_ips.add(ip)
                if ips and p.get("name") == self.me:
                    self.my_dot1 = ips[0]
        rows = dict(hosts)
        if self.my_dot1 and self.my_dot1 not in rows and participants:
            mf = self.host_facts.get(self.my_dot1, {})
            rows[self.my_dot1] = next((r.get("value") for r in mf.get("addrs") or [] if r.get("ip") == self.my_dot1), "") or self.me
        out = []
        for ip in sorted(rows, key=lambda s: tuple(int(x) for x in s.split("."))):
            f = self.host_facts.get(ip, {})
            name = rows[ip]
            # the echo exactly as the region answers it everywhere else ([ECHO_UPSTREAM_FROM_THE_ROWS_V1], the same rule
            # as discovery.real_backends.region_echo and frognet_echo.php): "<name>,<.1>,<upstream1>,<upstream2>" from
            # the facts' uplink rows, 0.0.0.0 where none; "" only for a host no member's facts ever named
            ups = [str(r.get("ip", "")) for r in f.get("addrs") or [] if r.get("kind") == "uplink" and r.get("ip")] + ["0.0.0.0", "0.0.0.0"]
            echo = "%s,%s,%s,%s" % (f.get("name") or name, ip, ups[0], ups[1]) if f else ""
            # role and alive: what the monitor shows for a device (a leaf: "DEVICE ONLINE|DOWN"); alive is this event's
            # session state, so a host in its hold-down reads alive=false while it is still listed
            # guid: the member's identity from its facts, for the monitor snapshot's member rows [ONE_MONITOR_TRUTH_V1]
            out.append({"ip": ip, "hostname": name, "echo": echo, "role": f.get("role", ""), "alive": ip in alive_ips,
                        "guid": f.get("guid", "")})
        self.w.write_if_changed(self.hosts_sentinel + ".json", _js.dumps(out, indent=1) + "\n")

    def _hosts_content(self, hosts: Dict[str, str]) -> str:
        """/var/lib/frognet/region_hosts: "<ip> <name>" per line. The merge (discovery.live, region mode) takes these
        as its peers instead of walking; it builds /etc/hosts and the forwarders from them as always."""
        return "".join("%s %s\n" % (ip, hosts[ip]) for ip in sorted(hosts, key=lambda s: tuple(int(x) for x in s.split("."))))
