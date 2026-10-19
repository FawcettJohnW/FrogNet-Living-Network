#!/usr/bin/env python3
# kart_host.py -- a machine that can serve a game's memory. [KART_HOST_AGENT_V1]  (DESIGN.md)
# It writes (kart, gamehost, <machine>) -- what it is (dedicated, or a player's machine), the address racers reach it
# at -- and holds a read on the kart memory. When the kart service's (kart, launch, <race>) names this machine, it
# writes (kart, hosting, <race>) "starting" and starts the race server here (kart_server.py), which fills the game's
# memory and announces it. If the race server cannot start, or ends before its server tuple appears, it writes
# "failed" with why, and the service names another machine.
#
#   kart_host.py --memory H:P [--api /ram.php] --kind dedicated|player [--machine NAME] [--advertise ADDR | --fdf H:P]
#                [--rina-host PATH]
# A player's game runs one for its own machine (--kind player); fdf-kart-host.sh runs a dedicated one.
import argparse, json, os, signal, socket, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kartmem import Region, say, hp
from ribbit import Memory, RibbitError

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--memory", required=True); ap.add_argument("--api", default="/ram.php")
ap.add_argument("--kind", choices=["dedicated", "player"], required=True)
ap.add_argument("--machine", default=os.environ.get("COMPUTERNAME") or socket.gethostname())
ap.add_argument("--advertise", default=None, help="the address racers reach this machine at")
ap.add_argument("--fdf", default=None, help="FD&F RAM server: the address is the one this machine announced to FD&F")
ap.add_argument("--rina-host", default=None, help="default: beside this file, else /opt/fdf/bin/rina-host")
ap.add_argument("--player", default="", help="(a player's machine) whose game this agent belongs to")
a = ap.parse_args()
ME = a.machine
def log(m): say(f"kart_host[{ME}]", m)
if os.name != "nt" and a.kind == "player":                     # a player's agent goes with its game, however the game ends
    import ctypes; ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)
RH = a.rina_host if a.rina_host else next((c for c in (os.path.join(HERE, "rina-host"), "/opt/fdf/bin/rina-host") if os.path.exists(c)), None)
if not RH or not os.path.exists(RH): sys.exit(f"kart_host[{ME}]: no rina-host ({a.rina_host or 'beside kart_host.py or in /opt/fdf/bin'}): this machine cannot serve a game")
from ribbit import elf_arch, this_arch
if os.name != "nt" and elf_arch(RH) not in (None, this_arch()):        # [KART_SAYS_THE_ARCH_V1]
    sys.exit(f"kart_host[{ME}]: {RH} is built for {elf_arch(RH)}; this machine is {this_arch()}: this machine cannot serve a game with it")
H, P = hp(a.memory)

FAPI = "/FawcettInnovations.RINA.ram_interface.php"
def address():
    if a.advertise: return a.advertise
    m = Memory(H, P, api=a.api)                                 # what the kart memory sees this machine as
    try: r = m.call("POST", a.api + "?op=rina", json.dumps({"op": "seen"}))
    finally: m.close()
    if not r.get("ok"): raise RibbitError(f"the kart memory did not say where it sees this machine: {r}")
    return r["addr"].split(":")[0]

def fdf_follow():
    """[KART_HOST_AGENT_V1] with --fdf: this machine serves games only while FD&F lists it alive, at the address it
    announced. A held participant wait: nothing is asked again until the participants change."""
    fh, fp = hp(a.fdf, 8840); proc = f"kart_host:{ME}:{os.getpid()}"; bad = None
    while True:
        try:
            f = Memory(fh, fp, api=FAPI)
            r = f.call("POST", FAPI + "?op=rina", json.dumps({"op": "watch_participants", "proc": proc}))
            if not r.get("ok"): raise RibbitError(f"FD&F refused the participant watch: {r}")
            after = 0
            while True:
                ps = f.call("POST", FAPI + "?op=rina", json.dumps({"op": "participants"})).get("participants", [])
                if bad is not None: log("FD&F participant watch working again"); bad = None
                mine = next((p for p in ps if p.get("name") == ME and p.get("alive") and p.get("ips")), None)
                addr = mine["ips"][0] if mine else None
                if addr != FDF["addr"]:
                    FDF["addr"] = addr
                    log(f"FD&F lists this machine alive at {addr}: it can serve games" if addr else
                        "FD&F does not list this machine alive: it serves no game until it does")
                    on_change()
                t = f.call_text("POST", FAPI + "?op=rina_block", json.dumps({"op": "wait", "proc": proc, "after": after}), 3.0e8)
                for line in t.split("\n")[1:]:
                    if "\t" in line: after = max(after, int(line.split("\t", 1)[0]))
        except (RibbitError, ValueError) as e:
            if bad != str(e): log(f"FD&F participant watch FAILING: {e}"); bad = str(e)
        time.sleep(1.0)
FDF = {"addr": None}

handled = set(); lock = threading.Lock(); ADDR = None; written = {"g": -1}
def on_change():
    if mem is None: return
    with lock:
        addr = FDF["addr"] if a.fdf else ADDR
        if (written["g"], written.get("a")) != (mem.gen, addr):  # a new session, or a new address: say what this machine is
            mem.write("gamehost", ME, {"machine": ME, "kind": a.kind, "addr": addr, "player": a.player, "rina_host": RH} if addr else {},
                      own=True)
            written["g"] = mem.gen; written["a"] = addr
        if not addr: return
        servers = mem.family("server")
        for race, lb in mem.family("launch").items():
            if lb.get("host") != ME or race in handled or race in servers: continue
            handled.add(race); start(race, lb)

def start(race, lb):
    mem.write("hosting", race, {"machine": ME, "state": "starting", "at": time.time()})
    cmd = [sys.executable, os.path.join(HERE, "kart_server.py"), "--main", f"{H}:{P}", "--main-api", a.api,
           "--intent", json.dumps({"race": race, "host": a.player or ME, "roster": lb["roster"], "laps": lb["laps"]}),
           "--advertise", FDF["addr"] if a.fdf else ADDR, "--rina-host", RH]
    try: p = subprocess.Popen(cmd, start_new_session=True)
    except OSError as e:
        mem.write("hosting", race, {"machine": ME, "state": "failed", "why": f"cannot start kart_server.py: {e}"}); log(f"race {race}: FAILED to start: {e}"); return
    log(f"race {race}: serving it here ({FDF['addr'] if a.fdf else ADDR}), {len(lb['roster'])} player(s), {lb['laps']} lap(s)")
    def watch():
        rc = p.wait()
        if race not in mem.family("server") and rc != 0 and race not in mem.family("result"):
            mem.write("hosting", race, {"machine": ME, "state": "failed", "why": f"its race server exited ({rc}) before it was up"})
            log(f"race {race}: its race server exited ({rc}) before it was up")
        else:
            mem.write("hosting", race, {})                     # done: the race server said everything itself
    threading.Thread(target=watch, daemon=True).start()

mem = None
if not a.fdf:
    try: ADDR = address()
    except RibbitError as e: sys.exit(f"kart_host[{ME}]: cannot tell where racers reach this machine: {e}")
mem = Region("kart memory", H, P, a.api, "kart", log, on_change=on_change)
mem.up.wait()
log(f"can serve games ({a.kind}), " + (f"at {ADDR}" if not a.fdf else f"at the address FD&F ({a.fdf}) lists for it") + f", with {RH}; kart memory {H}:{P}")
if a.fdf: threading.Thread(target=fdf_follow, daemon=True).start()
try:
    while True: time.sleep(3600)
except KeyboardInterrupt:
    log("stopped")
