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

[NO_FLAP_V1] (John, 2026-11-01: "You know within milliseconds that the socket to upstream has closed. You should
immediately switch to the backup ... Then we don't do shit to go back up until this one dies. No flapping around.")
  standing   one session to EVERY candidate, all the time: a held read on each. It is how the space knows, at the
             moment it needs to, which RAM managers are alive -- nothing is asked then, nothing waited for.
  lost       the current one's held read fails: the socket closed (milliseconds when its RAM manager or its host goes;
             TCP keepalive, about 5 s, when the path goes silent). The space moves AT ONCE to the first candidate, in
             list order, whose own standing session is alive.
  returned   a candidate's standing session comes back: nothing happens while the current one is alive. It counts only
             at the next loss. The exceptions: a space on nothing takes the first that comes, and a space on its
             fallback (the LAN's, the machine's own last resort, not a peer) takes a configured RAM manager that comes.
No memory is moved.

[SPACES_V1] an application in several spaces holds one Space per space, each with its own list and its own moves:
    for name in ("First Responders", "EOC Admin"):
        fdfspace.from_login(name, fallback=[...], on_change=fn).start()
from_login reads the device's login state (fdf-login: where each of its level's spaces is on each RAM host, in the
line's order) and builds the space's list from it.
"""
import queue, threading, time
import fdfram

RECONNECT_S = 2                               # a candidate that does not answer is asked again this often


def _log(*a):
    print("[SPACE] %s %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


class Space:
    def __init__(self, name, managers, fallback=(), on_change=None, me=""):
        self.name, self.managers, self.fallback = name, list(managers), list(fallback)
        self.on_change, self.me = on_change, me
        self.cands = [(m["name"], fdfram.Ram(m["ram"], device=m.get("iface"))) for m in self.managers] + \
                     [("fallback %s" % f, fdfram.Ram(f)) for f in self.fallback]
        self.alive = [False] * len(self.cands)
        self.current = None                       # index into self.cands
        self.ram = None
        self.events = queue.Queue()
        self.switched_at = None                   # time.monotonic() of the last switch
        self._tried = [False] * len(self.cands)   # each candidate has had its first answer or its first failure
        self._first_done = False

    def candidates(self):
        return self.cands

    def start(self):
        threading.Thread(target=self._merge_loop, name="space-merge-" + self.name, daemon=True).start()
        for i in range(len(self.cands)):
            threading.Thread(target=self._standing, args=(i,), name="space-stand-%s-%d" % (self.name, i), daemon=True).start()
        # the first choice: the list's order, once each candidate has had its first answer (or its first failure)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3 and not all(self._tried):
            time.sleep(0.02)
        self.events.put(("start", None))
        for _ in range(200):
            if self.ram is not None:
                return self
            time.sleep(0.05)
        _log(self.name, ": no RAM manager answered yet; the space takes the first that does")
        return self

    def _standing(self, i):
        """[NO_FLAP_V1] this candidate's standing session: a held read on it, for as long as it answers. Its state is
        all the merge needs at a loss. A candidate that does not answer is asked again every RECONNECT_S."""
        name, ram = self.cands[i]
        while True:
            last = 0
            try:
                ram.version()
                self.alive[i] = True; self._tried[i] = True
                self.events.put(("up", i))
                while True:
                    rows = ram.read("fdf-space", "alive", self.me or "-", after=last, wait_s=fdfram.HOLD_S)
                    if rows:
                        last = max(r["id"] for r in rows)
            except (fdfram.RamError, OSError, ValueError) as e:
                was = self.alive[i]
                self.alive[i] = False; self._tried[i] = True
                if was:
                    _log(self.name, ": %s closed: %s" % (name, e))
                    self.events.put(("down", i))
            time.sleep(RECONNECT_S)

    def _merge_loop(self):
        while True:
            ev, i = self.events.get()
            if ev == "down" and i != self.current:
                continue                          # a standby went: it counts at the next loss
            on_fallback = self.current is not None and self.current >= len(self.managers)
            if ev == "up" and self.current is not None and not (on_fallback and i < len(self.managers)):
                continue                          # [NO_FLAP_V1] never back up while the current one lives
            # (the fallback is the machine's own last resort, not a peer: a configured RAM manager coming back is taken)
            if ev == "up" and self.current is None and not self._first_done:
                continue                          # before the first choice: start makes it, in list order
            pick = next((j for j in range(len(self.cands)) if self.alive[j] and j != (i if ev == "down" else None)), None)
            if ev == "start":
                self._first_done = True
            if pick is None:
                if self.current is not None:
                    _log(self.name, ": lost %s and nothing else is alive; the first that comes back is taken" % self.cands[self.current][0])
                    self.current, self.ram = None, None
                else:
                    _log(self.name, ": nothing on the list answers, nor the fallback; the first that does is taken")
                continue
            if pick == self.current:
                continue
            old = None if self.current is None else self.cands[self.current][0]
            name, ram = self.cands[pick]
            self.current, self.ram = pick, ram
            self.switched_at = time.monotonic()
            _log(self.name, ": %s: on %s (%s)%s" % (ev, name, ram.hostport, "" if old is None else ", was " + old))
            if self.on_change:
                try:
                    self.on_change(old, name)
                except Exception as e:            # the application's error is the application's, and is said
                    _log(self.name, ": the application's change handler failed: %s" % e)


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
