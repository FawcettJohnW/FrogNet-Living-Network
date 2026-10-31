# kartmem.py -- one service of one memory, followed: what the kart service, the host agents and the games share.
# [KART_HELD_READS_V1] A Region holds one session and keeps a view of the whole service: read once, then held, every
# tuple written coming back once ({} = gone). on_change runs on the follower thread after each answer is applied.
# A broken session is said, and a new one opened a second later (gen changes: the owner writes its truths again).
import threading, time, sys
from ribbit import Memory, RibbitError

_say_lock = threading.Lock()
def say(who, m):
    with _say_lock: sys.stdout.write(f"{who}: {m}\n"); sys.stdout.flush()

class Region:
    def __init__(self, name, host, port, api, service, log, ends=None, on_change=None):
        self.name, self.host, self.port, self.api, self.service = name, host, port, api, service
        self.log, self.ends, self.on_change = log, ends, on_change
        self.mem = None; self.gen = 0; self.view = {}; self.lock = threading.Lock(); self.bad = None
        self.writes = 0; self.stopped = False; self.refused_said = 0; self.up = threading.Event()
        threading.Thread(target=self._follow, daemon=True).start()
    def failing(self, e):
        if self.bad != str(e): self.log(f"{self.name} FAILING: {e}"); self.bad = str(e)
    def write(self, v, i, bag, own=True, permanent=False):
        m = self.mem
        if m is None or self.stopped: return False
        try:
            m.write(self.service, v, i, bag, own=own, permanent=permanent); self.writes += 1
            r = m.refused()
            if r != self.refused_said: self.log(f"{self.name} refused {r - self.refused_said} write(s)"); self.refused_said = r
            return True
        except RibbitError as e: self.failing(e); return False
    def _follow(self):
        retired = []
        while not self.stopped:
            try: m = Memory(self.host, self.port, api=self.api)
            except RibbitError as e: self.failing(e); time.sleep(1.0); continue
            first = True
            try:
                for cells in m.follow(self.service, ""):
                    if self.stopped: break
                    now = time.time()
                    with self.lock:
                        if first: self.view = {}
                        for c in cells:
                            k = (c["variable"], c["instance"])
                            if c["bag"]: self.view[k] = (c["bag"], now)
                            else: self.view.pop(k, None)
                    if self.ends and self.ends(self.view): self.stop(); break
                    if first:
                        self.mem = m; self.gen += 1; first = False; self.up.set()
                        if self.bad is not None: self.log(f"{self.name} working again (a new session)"); self.bad = None
                        for r in retired: r.close()
                        retired = []
                    if self.on_change: self.on_change()
            except RibbitError as e:
                if not self.stopped: self.failing(e)
            if self.mem is m: self.mem = None
            retired.append(m); time.sleep(1.0)
    def stop(self): self.stopped = True; self.mem = None
    def family(self, variable):
        with self.lock: return {i: b for (v, i), (b, _) in self.view.items() if v == variable}
    def family_t(self, variable):
        with self.lock: return {i: (b, t) for (v, i), (b, t) in self.view.items() if v == variable}

def hp(s, p=8799):
    if ":" in s: h, pp = s.rsplit(":", 1); return h, int(pp)
    return s, p
