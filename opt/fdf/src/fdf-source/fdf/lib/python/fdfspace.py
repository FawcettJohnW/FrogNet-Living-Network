################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdfspace.py -- [SPACE_FAILOVER_V1] one RAM space for one application: which RAM manager it is on, and moving it when
that changes (opt/fdf/docs/ORGANIZATIONS.md sections 4 and 6). The application only receives the change.

    sp = fdfspace.Space("First Responders", managers, fallback, on_change=fn)
    sp.start()            # walks the list; sp.ram is an fdfram.Ram on the RAM manager it chose
    ...                   # the application reads and writes through sp.ram
    fn(old, new)          # called when the RAM manager changed: re-arm held reads, write your own truth again

managers  the space's configured RAM managers, in order: [{"name", "ram": "addr:port", "iface": "wg-own0"}, ...]; each
          is reached through the device's own tunnel to it (fdf-login made it), bound to that interface
fallback  after the list, every space falls back the same way: the LAN's machines from the highest IP
          (databasehost-control) down to this one's own: ["addr:port", ...], no interface. A machine is never without a
          RAM manager.

Events, handled one at a time by one thread (the merge):
  lost        the held read on the current RAM manager failed (TCP keepalive: about 5 s after it went)
  returned    a standing connection to a RAM manager ABOVE the current one completed; one is kept to each of them,
              including when the space is on its fallback, with the region session's reconnect behaviour (5 s)
Each event walks the list top to bottom; if the answer is not the RAM manager the space had, it switches and says so.
No memory is moved.

[SPACES_V1] an application in several spaces holds one Space per space, each with its own list and its own moves:
    for name in ("First Responders", "EOC Admin"):
        fdfspace.from_login(name, fallback=[...], on_change=fn).start()
from_login reads the device's login state (fdf-login: where each of its level's spaces is on each RAM host, in the
line's order) and builds the space's list from it.
"""
import queue, threading, time
import fdfram

RECONNECT_S = 5


def _log(*a):
    print("[SPACE] %s %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


class Space:
    def __init__(self, name, managers, fallback=(), on_change=None, me=""):
        self.name, self.managers, self.fallback = name, list(managers), list(fallback)
        self.on_change, self.me = on_change, me
        self.current = None                       # index into self.candidates()
        self.ram = None
        self.events = queue.Queue()
        self._stand = {}                          # candidate index -> standing thread
        self._gen = 0                             # bumped on every switch: a watcher of an old RAM stops

    def candidates(self):
        return [(m["name"], fdfram.Ram(m["ram"], device=m.get("iface"))) for m in self.managers] + \
               [("fallback %s" % f, fdfram.Ram(f)) for f in self.fallback]

    @staticmethod
    def answers(ram):
        try:
            ram.version()
            return True
        except (fdfram.RamError, OSError, ValueError):
            return False

    def walk(self):
        for i, (name, ram) in enumerate(self.candidates()):
            if self.answers(ram):
                return i
        return None

    def start(self):
        threading.Thread(target=self._merge_loop, name="space-merge-" + self.name, daemon=True).start()
        self.events.put("start")
        for _ in range(200):
            if self.ram is not None:
                return self
            time.sleep(0.05)
        _log(self.name, ": no RAM manager answered yet; the space waits for one")
        return self

    def _merge_loop(self):
        while True:
            ev = self.events.get()
            i = self.walk()
            if i is None:
                _log(self.name, ": %s: nothing on the list answers, nor the fallback; the next event walks again" % ev)
                self._standing(None)
                continue
            if i == self.current:
                self._standing(i)
                continue
            old = None if self.current is None else self.candidates()[self.current][0]
            name, ram = self.candidates()[i]
            self.current, self.ram = i, ram
            self._gen += 1
            _log(self.name, ": %s: on %s (%s)%s" % (ev, name, ram.hostport, "" if old is None else ", was " + old))
            threading.Thread(target=self._watch, args=(self._gen, ram), daemon=True).start()
            self._standing(i)
            if self.on_change:
                try:
                    self.on_change(old, name)
                except Exception as e:            # the application's error is the application's, and is said
                    _log(self.name, ": the application's change handler failed: %s" % e)

    def _watch(self, gen, ram):
        """A held read on the current RAM manager: when it fails, the space lost it."""
        last = 0
        while gen == self._gen:
            try:
                rows = ram.read("fdf-space", "alive", self.me or "-", after=last, wait_s=fdfram.HOLD_S)
                if rows:
                    last = max(r["id"] for r in rows)
            except (fdfram.RamError, OSError, ValueError) as e:
                if gen == self._gen:
                    _log(self.name, ": lost %s: %s" % (ram.hostport, e))
                    self.events.put("lost")
                return

    def _standing(self, cur):
        """A standing connection attempt to every candidate above the current one (all of the list when on nothing)."""
        top = len(self.managers) if cur is None else min(cur, len(self.managers))
        for i in range(top):
            t = self._stand.get(i)
            if t is None or not t.is_alive():
                self._stand[i] = threading.Thread(target=self._stand_one, args=(i,), daemon=True)
                self._stand[i].start()

    def _stand_one(self, i):
        name, ram = self.candidates()[i]
        while self.current is None or i < self.current:
            if self.answers(ram):
                _log(self.name, ": %s answers again" % name)
                self.events.put("returned")
                return
            time.sleep(RECONNECT_S)


def from_login(name, state=None, fallback=(), on_change=None, me=""):
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
    return Space(name, managers, fallback, on_change=on_change, me=me or st.get("device", ""))
