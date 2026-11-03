################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdfspace.py -- [SPACE_FAILOVER_V1][SPACE_BY_CAPABILITY_V1] one RAM space for one application: which RAM host it is
on, and moving it when that changes (opt/fdf/docs/ORGANIZATIONS.md sections 4 and 6). The application only receives
the change.

    sp = fdfspace.Space("First Responders", managers, on_change=fn)
    sp.start()            # sp.ram is an fdfram.Ram on the RAM host the space is on (None: on nothing)
    fn(old, new)          # the RAM host changed: re-arm held reads, write your own truth again

managers  the RAM hosts the space is on, from the device's logins: [{"name", "ram": "addr:port", "iface": "wg-own0"}, ...];
          each reached through the device's own tunnel to it (fdf-login made it), bound to that interface

[SPACE_BY_CAPABILITY_V1] (John 2026-11-03: "There is no fallback list. Fallback is determined by capability." "Designation
and failover/failup are automatic." "A torn network is worse than a disconnected one.") It replaces [NO_FLAP_V1]
(2026-11-01: a list order, "never back up while it lives", and a fallback to the LAN's or the machine's own RAM).
  standing   one session to every RAM host, all the time: a held read on its capability tuple, (fdf-space, can, AREA),
             which that host's fdf-org writes into the space's RAM there when it starts. It says, at the moment it is
             needed, which hosts are alive and what each can do: nothing is asked then.
  selection  on every event (a host's session opens or closes, a capability is written), the space is on the best of the
             alive hosts by capability (fdf_select: the default ranking, or the application's `select`), the guid last.
             Every device that sees the same hosts is on the same one. A better host coming back takes the space again.
  nothing    no host alive: the space is on nothing (sp.ram None) and says so; there is no fallback.
No memory is moved.

[SPACES_V1] an application in several spaces holds one Space per space:
    for name in ("First Responders", "EOC Admin"):
        fdfspace.from_login(name, on_change=fn).start()
"""
import queue, threading, time
import fdfram
import fdf_select

RECONNECT_S = 2                               # a candidate that does not answer is asked again this often


def _log(*a):
    print("[SPACE] %s %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


class Space:
    def __init__(self, name, managers, on_change=None, me="", select=None):
        self.name, self.managers = name, list(managers)
        self.on_change, self.me, self.select = on_change, me, select or {}
        self.cands = [(m["name"], fdfram.Ram(m["ram"], device=m.get("iface"))) for m in self.managers]
        self.alive = [False] * len(self.cands)
        self.cap = [{} for _ in self.cands]       # each host's capability tuple, as its space RAM says it
        self.current = None                       # index into self.cands
        self.ram = None
        self.events = queue.Queue()
        self.switched_at = None                   # time.monotonic() of the last switch
        self._tried = [False] * len(self.cands)   # each host has had its first answer or its first failure

    def candidates(self):
        return self.cands

    def start(self):
        threading.Thread(target=self._merge_loop, name="space-merge-" + self.name, daemon=True).start()
        for i in range(len(self.cands)):
            threading.Thread(target=self._standing, args=(i,), name="space-stand-%s-%d" % (self.name, i), daemon=True).start()
        t0 = time.monotonic()                     # the first choice once each host has answered or failed (3 s at most)
        while time.monotonic() - t0 < 3 and not all(self._tried):
            time.sleep(0.02)
        self.events.put(("start", None))
        for _ in range(200):
            if self.ram is not None:
                return self
            time.sleep(0.05)
        _log(self.name, ": no RAM host answered yet; the space is on nothing until one does")
        return self

    def _standing(self, i):
        """this host's standing session: a held read on its capability tuple, for as long as it answers. A host that
        does not answer is asked again every RECONNECT_S."""
        name, ram = self.cands[i]
        while True:
            last = -1
            try:
                rows = ram.read("fdf-space", "can")
                self.cap[i] = (rows[-1].get("bag") or {}) if rows else {}
                last = max([r["id"] for r in rows] or [-1])
                self.alive[i] = True; self._tried[i] = True
                self.events.put(("up", i))
                while True:
                    rows = ram.read("fdf-space", "can", after=last, wait_s=fdfram.HOLD_S)
                    if rows:
                        last = max(r["id"] for r in rows)
                        self.cap[i] = rows[-1].get("bag") or {}
                        self.events.put(("capability", i))
            except Exception as e:                # any failure of this session: a thread that died here would leave
                was = self.alive[i]               # the host never asked again
                self.alive[i] = False; self._tried[i] = True
                if was:
                    _log(self.name, ": %s closed: %s" % (name, e))
                    self.events.put(("down", i))
                elif not isinstance(e, (fdfram.RamError, OSError)):
                    _log(self.name, ": %s: %s %s (asked again in %d s)" % (name, type(e).__name__, e, RECONNECT_S))
            time.sleep(RECONNECT_S)

    def best(self):
        """the alive host the space is on: the best by capability (fdf_select), the guid (else the host's name) last"""
        alive = [((self.cap[j].get("guid") or self.cands[j][0]), self.cap[j], j) for j in range(len(self.cands)) if self.alive[j]]
        g = fdf_select.select(self.select, self.name, [(k, b) for k, b, _ in alive])
        return next((j for k, _, j in alive if k == g), None)

    def _merge_loop(self):
        started = False
        while True:
            ev, i = self.events.get()
            if ev == "start":
                started = True
            if not started:
                continue                          # before the first choice: start makes it, once all have answered
            pick = self.best()
            if pick is None:
                if self.current is not None:
                    _log(self.name, ": lost %s and no other RAM host is alive: on nothing" % self.cands[self.current][0])
                    self.current, self.ram = None, None
                elif ev == "start":
                    _log(self.name, ": no RAM host answers: on nothing")
                continue
            if pick == self.current:
                continue
            old = None if self.current is None else self.cands[self.current][0]
            name, ram = self.cands[pick]
            self.current, self.ram = pick, ram
            self.switched_at = time.monotonic()
            _log(self.name, ": %s: on %s (%s), the best alive by capability (%s)%s" % (ev, name, ram.hostport,
                 fdf_select.explain(self.select, self.name, [((self.cap[j].get("guid") or self.cands[j][0]), self.cap[j]) for j in range(len(self.cands)) if self.alive[j]]),
                 "" if old is None else ", was " + old))
            if self.on_change:
                try:
                    self.on_change(old, name)
                except Exception as e:            # the application's error is the application's, and is said
                    _log(self.name, ": the application's change handler failed: %s" % e)


def from_login(name, state=None, on_change=None, me="", select=None):
    """[SPACES_V1] the Space `name` from fdf-login's state (FDF_LOGIN_STATE, default /opt/fdf/var/login.json): one entry
    per RAM host whose answer listed the space, in the join line's order, each on that RAM host's tunnel."""
    import json, os
    path = state or os.environ.get("FDF_LOGIN_STATE", "/opt/fdf/var/login.json")
    st = json.load(open(path))
    managers = []
    for mname, m in sorted(st.get("managers", {}).items(), key=lambda kv: kv[1].get("order", 0)):
        for sp in m.get("spaces") or []:
            if isinstance(sp, dict) and sp.get("name") == name:
                managers.append({"name": mname, "ram": sp["ram"], "iface": m.get("iface")})
    if not managers:
        raise ValueError("space %r: this device's level joins it at no RAM host (%s)" % (name, path))
    return Space(name, managers, on_change=on_change, me=me or st.get("device", ""), select=select)
