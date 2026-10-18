#!/usr/bin/env python3
# kartdemo.py -- shared memory creating and managing games (DESIGN.md is the truth table).  [KART_LOBBY_V1]
# Each game maps the MAIN memory and waits in its lobby until enough players are there or someone presses Start.
# Then a race server (kart_server.py) is started, already filled, on one of the machines; its tuple appears in the
# main memory, every racer's game sees it and maps the race memory, they race, and before the race server exits it
# writes the result and every racer's record back into the main memory, permanently.
#
#   python kartdemo.py --name red --fdf H:8840            the main memory is FD&F's RAM manager
#   python kartdemo.py --name red --memory H:P            the main memory is that rina-host
#   python kartdemo.py --name red --solo                  this machine alone: the game starts its own main memory
#   [--bots N] [--autodrive] [--headless] [--min-players N] [--laps N]
#
# Keys: Start (or S / Enter) in the lobby; arrows / WASD to drive; P toggles the autopilot. Deps: pygame, and
# libkartram (kartram.dll on Windows) beside ribbit.py. Hosting a race needs rina-host (beside this file or in
# /opt/fdf/bin, Linux); a machine without it plays, and someone else's machine hosts.
#
# The memory is memory:
# [KART_ON_THE_SESSION_V1] one permanent FNW1 session per memory and service; writes are posted, never waited for.
# [KART_HELD_READS_V1] each family is read once, then held: the memory answers when something is written to it.
# [KART_WRITES_ON_CHANGE_V1] a kart is written when it departs from where readers draw it; everything else when it changes.
# [OWNED_CELL_GOES_WITH_THE_SESSION_V1] what a game writes goes with its session: gone is gone, no timeouts.
# [PERMANENT_CELL_V1] results and records are written permanent by the race server, so they outlive the main memory.
# [KART_AUTODRIVE_V1] --bots N: robot karts this game owns and drives; --autodrive (or P): your own kart too.
import sys, os, time, math, random, argparse, threading, subprocess, json, socket

ap = argparse.ArgumentParser()
ap.add_argument("--name", default=None, help="your player id")
ap.add_argument("--fdf", default=None, help="FD&F RAM server host:port: the main memory, and who is a member")
ap.add_argument("--fdf-client", default=None, help="the FD&F client program (default: next to this script, or /opt/fdf/bin)")
ap.add_argument("--memory", default=None, help="the main memory, host:port of a rina-host (instead of --fdf)")
ap.add_argument("--solo", action="store_true", help="start a main memory on this machine (~/.frognet-kart/main.perm keeps it)")
ap.add_argument("--min-players", type=int, default=2, help="a race starts by itself when this many players are free")
ap.add_argument("--laps", type=int, default=3)
ap.add_argument("--bots", type=int, default=0, help="robot karts this game owns and drives")
ap.add_argument("--autodrive", action="store_true", help="your own kart starts on the autopilot (P toggles it)")
ap.add_argument("--headless", action="store_true", help="no window, no sound")
ap.add_argument("--rina-host", default=None, help="the rina-host a race server runs (default: beside this file, or /opt/fdf/bin)")
# [KART_CALLS_V1] the real Communicator
ap.add_argument("--comms-ram", default=None); ap.add_argument("--comms-media", default=None)
ap.add_argument("--comms-app", default="comms-app.exe" if os.name == "nt" else "comms-app")
ap.add_argument("--comms-args", default="")
a = ap.parse_args()
if a.headless: os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
if sum(bool(x) for x in (a.fdf, a.memory, a.solo)) != 1: sys.exit("kartdemo: say where the main memory is: --fdf H:P, --memory H:P or --solo")
import pygame
from ribbit import Memory, RibbitError

NAME = a.name or random.choice(["red","blue","green","yellow","pink","cyan"])
HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = os.environ.get("COMPUTERNAME") or socket.gethostname()
_say_lock = threading.Lock()
def say(m):
    with _say_lock: sys.stdout.write(f"kartdemo: {m}\n"); sys.stdout.flush()
def hp(s, p=8799):
    if ":" in s: h, pp = s.rsplit(":", 1); return h, int(pp)
    return s, p
def tool(base):
    exe = base + (".exe" if os.name == "nt" else "")
    for c in [os.path.join(HERE, exe), os.path.join("/opt/fdf/bin", exe)]:
        if os.path.exists(c): return c
    return None
# --rina-host, when given, is the one: if it is not there this machine does not host (said), nothing else is looked for
RINA_HOST = (a.rina_host if os.path.exists(a.rina_host) else None) if a.rina_host else tool("rina-host")
CAN_HOST = os.name != "nt" and RINA_HOST is not None

# ----------------------------------------------------------------------------- where the main memory is
FDF_API = "/FawcettInnovations.RINA.ram_interface.php"
if a.fdf: MAIN_H, MAIN_P = hp(a.fdf, 8840); MAIN_API = FDF_API
elif a.memory: MAIN_H, MAIN_P = hp(a.memory); MAIN_API = "/ram.php"
else:                                            # --solo: this machine's own main memory, kept across runs
    if not RINA_HOST: sys.exit("kartdemo: --solo needs rina-host beside kartdemo.py or in /opt/fdf/bin")
    keep = os.path.join(os.path.expanduser("~"), ".frognet-kart"); os.makedirs(keep, exist_ok=True)
    env = dict(os.environ, RINA_REGION_FILE=os.path.join(keep, "main.perm")); env.pop("RINA_MAP_AREA", None)
    solo = subprocess.Popen([RINA_HOST, "--listen", "127.0.0.1:0", "--api", "/ram.php"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    MAIN_P = None
    for line in solo.stderr:
        if "[PERMANENT_CELL_V1]" in line: say(line.strip())
        if "[FDF_VERSION_V1]" in line and "listening on" in line: MAIN_P = int(line.rsplit(":", 1)[1])
        if MAIN_P and "[PERMANENT_CELL_V1]" in line: break
    if not MAIN_P: sys.exit("kartdemo: the solo main memory did not start")
    threading.Thread(target=lambda: [None for _ in solo.stderr], daemon=True).start()
    MAIN_H, MAIN_API = "127.0.0.1", "/ram.php"
    say(f"solo: main memory on this machine, 127.0.0.1:{MAIN_P}, kept in {keep}/main.perm")
WHERE = {}
if a.fdf:
    try:
        f = Memory(MAIN_H, MAIN_P, api=FDF_API)
        for c in f.read("fdf", "where"): WHERE[c["instance"]] = c["bag"]
        f.close()
    except RibbitError as e: sys.exit(f"kartdemo: cannot read FD&F: {e}")
COMMS_RAM = a.comms_ram or (WHERE.get("comms", {}).get("ram")) or f"{MAIN_H}:8800"     # [KART_CALLS_V1]
COMMS_MEDIA = a.comms_media or (WHERE.get("comms", {}).get("media")) or f"{MAIN_H}:8994"
say(f"{NAME} on {MACHINE}: main memory {MAIN_H}:{MAIN_P}{MAIN_API}; {a.bots} robot(s); "
    f"{'can host races (' + RINA_HOST + ')' if CAN_HOST else ('cannot host races: ' + (a.rina_host + ' is not there' if a.rina_host else 'no rina-host here'))}")

# ----------------------------------------------------------------------------- a memory the game maps
running = True
class Region:
    """One service of one memory: one session, its family read once and then held. The view holds every tuple of the
    service, (variable, instance) -> (bag, when it arrived); {} is gone. A broken session is said and a new one opened
    a second later (and the game writes its own truths again: gen changes) -- until the region is stopped."""
    def __init__(self, name, host, port, api, service, ends=None):
        self.name, self.host, self.port, self.api, self.service = name, host, port, api, service
        self.ends = ends          # a rule on the view: when it holds, the memory has said this region is over
        self.mem = None; self.gen = 0; self.view = {}; self.lock = threading.Lock(); self.bad = None
        self.writes = 0; self.stopped = False; self.refused_said = 0
        threading.Thread(target=self.follower, daemon=True).start()
    def failing(self, e):
        if self.bad != str(e): say(f"{self.name} FAILING: {e}"); self.bad = str(e)
    def write(self, v, i, bag):
        m = self.mem
        if m is None or self.stopped: return False
        try:
            m.write(self.service, v, i, bag, own=True); self.writes += 1
            r = m.refused()
            if r != self.refused_said: say(f"{self.name} refused {r - self.refused_said} write(s)"); self.refused_said = r
            return True
        except RibbitError as e: self.failing(e); return False
    def follower(self):
        retired = []
        while running and not self.stopped:
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
                        self.mem = m; self.gen += 1; first = False
                        if self.bad is not None: say(f"{self.name} working again (a new session)"); self.bad = None
                        for r in retired: r.close()
                        retired = []
            except RibbitError as e:
                if not self.stopped: self.failing(e)
            if self.mem is m: self.mem = None
            retired.append(m); time.sleep(1.0)
    def stop(self): self.stopped = True; self.mem = None
    def family(self, variable):
        with self.lock: return {i: b for (v, i), (b, _) in self.view.items() if v == variable}
    def family_t(self, variable):
        with self.lock: return {i: (b, t) for (v, i), (b, t) in self.view.items() if v == variable}

main = Region("main memory", MAIN_H, MAIN_P, MAIN_API, "kart")     # lobby, servers, records, results
comm = Region("comm", MAIN_H, MAIN_P, MAIN_API, "comm")             # call tiles
race = None                                                          # the race memory, while racing

# ----------------------------------------------------------------------------- look
pygame.init()
W, H = 1180, 720; TRACK_W = 860
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption(f"FrogNet Kart -- {NAME}  (shared memory: lobby, race server, records)")
clock = pygame.time.Clock()
FONT = pygame.font.SysFont("Arial", 18, bold=True); BIG = pygame.font.SysFont("Arial", 30, bold=True)
SMALL = pygame.font.SysFont("Arial", 14)
COLORS = {"red":(222,67,67),"blue":(66,122,222),"green":(72,190,96),"yellow":(230,198,60),"pink":(224,96,180),"cyan":(70,196,208)}
BOT_COLORS = [(240,140,40),(150,90,220),(120,200,40),(40,170,150),(200,60,110),(110,110,240),(220,220,120),(170,120,80)]
def col(n): return COLORS.get(n, (200,200,200))
GRASS=(38,124,67); GRASS2=(44,138,76); TAR=(54,56,62); CURB_A=(214,70,70); CURB_B=(238,238,238); PANEL=(24,26,30)
CX, CY, RX, RY, ROAD = TRACK_W/2, H/2, TRACK_W/2-120, H/2-110, 62
def track_point(t): return CX + RX*math.cos(t), CY + RY*math.sin(t)
def nearest_t(x,y): return math.atan2((y-CY)/RY, (x-CX)/RX)
def draw_track(surf):
    surf.fill(GRASS)
    for i in range(0, W, 48):
        if (i//48)%2==0: pygame.draw.rect(surf,GRASS2,(i,0,48,H))
    for (x,y) in [track_point(2*math.pi*i/240) for i in range(240)]: pygame.draw.circle(surf,TAR,(int(x),int(y)),ROAD)
    for rx,ry in [(RX-ROAD,RY-ROAD),(RX+ROAD,RY+ROAD)]:
        last=None
        for i in range(0,241):
            t=2*math.pi*i/240; x=CX+rx*math.cos(t); y=CY+ry*math.sin(t)
            if last: pygame.draw.line(surf,CURB_A if (i//4)%2==0 else CURB_B,last,(x,y),4)
            last=(x,y)
    sx,sy=track_point(-math.pi/2)
    for o in range(-ROAD,ROAD,10): pygame.draw.rect(surf,(240,240,240) if (o//10)%2==0 else (40,40,40),(sx+o-3,sy-5,6,10))
TRACK=pygame.Surface((W,H)); draw_track(TRACK)
def kart_sprite(color):
    s=pygame.Surface((34,22),pygame.SRCALPHA)
    pygame.draw.rect(s,(20,20,24),(0,3,34,16),border_radius=5); pygame.draw.rect(s,color,(4,0,26,22),border_radius=6)
    pygame.draw.rect(s,(245,245,250),(22,5,8,12),border_radius=3)
    for r in [(0,0,5,6),(0,16,5,6),(29,0,5,6),(29,16,5,6)]: pygame.draw.rect(s,(15,15,18),r)
    return s
SPRITES={}
def sprite(n, color=None):
    c=tuple(color) if color else col(n)
    if c not in SPRITES: SPRITES[c]=kart_sprite(c)
    return SPRITES[c]
def text(s, x, y, f=None, c=(235,235,240)): screen.blit((f or FONT).render(s, True, c), (x, y))

# ----------------------------------------------------------------------------- driving (unchanged rules)
VMAX=240.0; ACC=260.0; TURN=2.8; FRICT=0.92
TOL_XY, TOL_H = 2.0, 0.05      # [KART_WRITES_ON_CHANGE_V1]
def carried(b, dt):
    x,y,h,v,w=b.get("x",0),b.get("y",0),b.get("h",0),b.get("v",0),b.get("w",0)
    if abs(w) < 1e-6: return x+math.cos(h)*v*dt, y+math.sin(h)*v*dt, h
    h2=h+w*dt; return x+v/w*(math.sin(h2)-math.sin(h)), y-v/w*(math.cos(h2)-math.cos(h)), h2
class Owned:
    """A kart this game drives and is the only writer of: race.kart.<name> in the race memory."""
    def __init__(self, name, color, robot, skill=1.0):
        self.name=name; self.color=color; self.robot=robot; self.skill=skill
        self.k=dict(x=0,y=0,h=0,v=0.0,w=0.0,lap=0); self.lane=0; self.dodge=0.0
        self.wander=random.uniform(0,6.28); self.wander_rate=random.uniform(0.3,0.7); self.w=None; self.w_at=0; self.w_gen=-1
    def place(self, g):
        t0=g["t0"]; lane=g["lane"]; sx,sy=track_point(t0); nx,ny=math.cos(t0+math.pi/2),math.sin(t0+math.pi/2)
        self.k=dict(x=sx+nx*lane, y=sy+ny*lane, h=t0+math.pi/2, v=0.0, w=0.0, lap=0); self.lane=lane
        self.last_t=nearest_t(self.k["x"],self.k["y"]); self.unwrapped=0.0; self.w=None; self.w_gen=-1
    def publish(self):
        k=self.k; now=time.time(); w=self.w
        if w is not None and self.w_gen == race.gen:
            px,py,ph=carried(w, now-self.w_at)
            if math.hypot(k["x"]-px,k["y"]-py) <= TOL_XY and abs(math.remainder(k["h"]-ph,2*math.pi)) <= TOL_H and k["lap"] == w["lap"]: return
        bag={"x":k["x"],"y":k["y"],"h":k["h"],"v":k["v"],"w":k["w"],"lap":k["lap"],"name":self.name,"machine":MACHINE,
             "color":list(self.color),"robot":self.robot,"owner":NAME}
        gen=race.gen
        if race.write("kart",self.name,bag): self.w=bag; self.w_at=now; self.w_gen=gen
def drive(o, steer, gas, dt):
    k=o.k; k["v"]+=gas*ACC*dt
    if gas==0: k["v"]*=FRICT
    if gas==0 and abs(k["v"])<1.0: k["v"]=0.0
    k["v"]=max(-VMAX*0.4,min(VMAX,k["v"])); k["w"]=steer*TURN*(k["v"]/VMAX if abs(k["v"])>5 else 0); k["h"]+=k["w"]*dt
    k["x"]+=math.cos(k["h"])*k["v"]*dt; k["y"]+=math.sin(k["h"])*k["v"]*dt
    k["x"]=max(10,min(TRACK_W-10,k["x"])); k["y"]=max(10,min(H-10,k["y"]))
    tt=nearest_t(k["x"],k["y"]); d=math.remainder(tt-o.last_t,2*math.pi); o.unwrapped+=d; o.last_t=tt
    if o.unwrapped>=2*math.pi: o.unwrapped-=2*math.pi; k["lap"]+=1
def autopilot(o, dt, others_xy):
    k=o.k; x,y,h=k["x"],k["y"],k["h"]; fx,fy=math.cos(h),math.sin(h); blocked=0.0
    for (ox,oy) in others_xy:
        dx,dy=ox-x,oy-y; ahead=dx*fx+dy*fy; side=-dx*fy+dy*fx
        if 0<ahead<70 and abs(side)<24: blocked=-1.0 if side>0 else 1.0
    o.dodge+=(blocked*30.0-o.dodge)*min(1.0,dt*4.0) if blocked else -o.dodge*min(1.0,dt*1.5)
    o.wander+=dt*o.wander_rate
    lane=max(-(ROAD-16),min(ROAD-16, o.lane+14*math.sin(o.wander)+o.dodge)); tt=nearest_t(x,y)+0.32
    tx,ty=CX+(RX+lane)*math.cos(tt), CY+(RY+lane)*math.sin(tt); err=math.remainder(math.atan2(ty-y,tx-x)-h,2*math.pi)
    want=o.skill*VMAX*(1.0-0.35*min(1.0,abs(err)))*(0.8 if blocked else 1.0)
    return max(-1.0,min(1.0,err*3.0)), (1 if k["v"]<want else 0)

seed=sum(ord(c) for c in NAME)
me=Owned(NAME, col(NAME), False)
bots=[Owned(f"{NAME}-bot{i+1}", BOT_COLORS[(seed+i)%len(BOT_COLORS)], True, skill=random.uniform(0.72,0.95)) for i in range(max(0,a.bots))]
MINE={o.name: o for o in [me]+bots}
autodrive=a.autodrive

# ----------------------------------------------------------------------------- the lobby: my truth, the rules
lobby_written=None; lobby_gen=-1; my_intent=None; ready_at=0.0   # back from a race: counted for an automatic start from ready_at
def publish_lobby():
    global lobby_written, lobby_gen
    bag={"name":NAME,"machine":MACHINE,"bots":len(bots),"can_host":CAN_HOST,"ready_at":ready_at}
    if my_intent: bag["start"]=my_intent
    if bag == lobby_written and lobby_gen == main.gen: return
    gen=main.gen
    if main.write("lobby",NAME,bag): lobby_written=bag; lobby_gen=gen
def running_servers(): return {r: b for r, b in main.family("server").items()}
def busy_players(servers): return {p for b in servers.values() for p in b.get("players", [])}
def free_players(lob, servers):
    busy=busy_players(servers); return sorted(n for n, b in lob.items() if n not in busy and member(b.get("machine","")))
def winning_intent(lob):
    ins=[b["start"] for b in lob.values() if b.get("start")]
    return min(ins, key=lambda i: i["race"]) if ins else None
def make_intent(lob, servers):
    free=free_players(lob, servers)
    if NAME not in free: free=sorted(free+[NAME])
    hosts=([NAME] if CAN_HOST else [])+[n for n in free if n != NAME and lob.get(n,{}).get("can_host")]
    if not hosts: return None, "no machine in the lobby can host a race (it needs rina-host, on Linux)"
    roster=[{"name":n, "bots":int(lob.get(n,{}).get("bots",0)) if n != NAME else len(bots)} for n in free]
    return {"race": f"{int(time.time()*1000):013d}-{NAME}", "host": hosts[0], "roster": roster, "laps": a.laps}, ""
launched=set(); server_procs=[]
def my_address():
    """the address the racers reach this machine at: FD&F's own record of it, or what the main memory sees, or (solo) here"""
    if a.solo: return "127.0.0.1"
    if a.fdf:
        for p in fdf_participants:                              # the addresses this machine announced to FD&F
            if p.get("name") == MACHINE and p.get("alive") and p.get("ips"): return p["ips"][0]
        raise RibbitError(f"FD&F has no address for {MACHINE}")
    r=main.mem.call("POST", MAIN_API+"?op=rina", json.dumps({"op":"seen"})) if main.mem else {}
    if not r.get("ok"): raise RibbitError(f"the main memory did not say where it sees this machine: {r}")
    return r["addr"].split(":")[0]
def host_race(intent):
    """[KART_RACE_SERVER_V1] the winning intent names this game host: start the race server here, once"""
    launched.add(intent["race"])
    try: addr=my_address()
    except RibbitError as e: say(f"cannot host race {intent['race']}: {e}"); return
    cmd=[sys.executable, os.path.join(HERE,"kart_server.py"), "--main", f"{MAIN_H}:{MAIN_P}", "--main-api", MAIN_API,
         "--intent", json.dumps(intent), "--advertise", addr, "--rina-host", RINA_HOST]
    try: server_procs.append(subprocess.Popen(cmd, start_new_session=True)); say(f"hosting race {intent['race']} here, at {addr}")
    except OSError as e: say(f"cannot start the race server: {e}")

# ----------------------------------------------------------------------------- calls (unchanged)
calling=""; answered={}; call_id=""; comms_proc=None; call_buttons={}
def comms_running(): return comms_proc is not None and comms_proc.poll() is None
def launch_comms(callee=""):
    global comms_proc, calling, call_id
    if comms_running(): return
    import shlex
    cmd=[a.comms_app,"--ram",COMMS_RAM,"--media",COMMS_MEDIA,"--name",NAME]+(["--auto-join",callee] if callee else [])+shlex.split(a.comms_args)
    try: comms_proc=subprocess.Popen(cmd); calling=callee; call_id=f"{time.time():.3f}" if callee else ""
    except OSError as e: say(f"cannot start the Communicator ({a.comms_app}): {e}")
tile_written=None; tile_gen=-1
def publish_tile():
    global tile_written, tile_gen
    up=comms_running(); st=("calling "+calling) if (calling and up) else ("in a call" if up else ("racing" if race else "in the lobby"))
    bag={"name":NAME,"color":list(col(NAME)),"status":st,"calling":calling if up else "","call_id":call_id if up else "","machine":MACHINE}
    if bag == tile_written and tile_gen == comm.gen: return
    gen=comm.gen
    if comm.write("tile",NAME,bag): tile_written=bag; tile_gen=gen

# ----------------------------------------------------------------------------- FD&F membership (held)
fdf_alive=set(); fdf_seen=False; fdf_participants=[]
def fdf_reader():
    global fdf_alive, fdf_seen, fdf_participants
    me_proc=f"kart:{MACHINE}:{NAME}:{os.getpid()}"; bad=None
    while running:
        try:
            f=Memory(MAIN_H, MAIN_P, api=FDF_API)
            r=f.call("POST", FDF_API+"?op=rina", json.dumps({"op":"watch_participants","proc":me_proc}))
            if not r.get("ok"): raise RibbitError(f"FD&F refused the participant watch: {r}")
            after=0
            while running:
                ps=f.call("POST", FDF_API+"?op=rina", json.dumps({"op":"participants"})).get("participants",[])
                fdf_participants=ps; fdf_alive={q.get("name","") for q in ps if q.get("alive")}; fdf_seen=True
                if bad is not None: say("FD&F participant watch working again"); bad=None
                ensure_fdf_member()
                t=f.call_text("POST", FDF_API+"?op=rina_block", json.dumps({"op":"wait","proc":me_proc,"after":after}), 3.0e8)
                for line in t.split("\n")[1:]:
                    if "\t" in line: after=max(after, int(line.split("\t",1)[0]))
        except (RibbitError, ValueError) as e:
            if bad != str(e): say(f"FD&F participant watch FAILING: {e}"); bad=str(e)
        time.sleep(1.0)
def member(machine): return (not a.fdf) or (machine in fdf_alive)
fdf_client_proc=None; fdf_client_t=0.0
def ensure_fdf_member():      # [FDF_AUTOCLIENT_V1]
    global fdf_client_proc, fdf_client_t
    if not fdf_seen or member(MACHINE): return
    if fdf_client_proc and fdf_client_proc.poll() is None: return
    if fdf_client_proc is False or time.time()-fdf_client_t < 10: return
    fdf_client_t=time.time(); exe=a.fdf_client or tool("fdf-client") or "fdf-client"   # given: that one, exactly
    try:
        flags=subprocess.CREATE_NEW_CONSOLE if os.name=="nt" else 0
        fdf_client_proc=subprocess.Popen([exe,f"{MAIN_H}:{MAIN_P}"],creationflags=flags) if os.name=="nt" else subprocess.Popen([exe,f"{MAIN_H}:{MAIN_P}"],start_new_session=True)
        say(f"{MACHINE} was not an FD&F member: started {exe} {MAIN_H}:{MAIN_P}")
    except OSError as e: fdf_client_proc=False; say(f"{MACHINE} is not an FD&F member, and the FD&F client could not start ({exe}): {e}")
if a.fdf: threading.Thread(target=fdf_reader,daemon=True).start()

# ----------------------------------------------------------------------------- the strip: tiles, calls
def draw_tile(x, y, w, h, name, bag, live):
    pygame.draw.rect(screen,(16,17,20),(x,y,w,h),border_radius=10)
    c=col(name); fr=int(time.time()*8)%1000; inner=pygame.Surface((w-10,h-34))
    for yy in range(0,h-34,4):
        sh=0.5+0.5*math.sin((yy+fr*3)*0.08); inner.fill((int(c[0]*sh),int(c[1]*sh),int(c[2]*sh)),(0,yy,w-10,4))
    pygame.draw.circle(inner,(245,230,215),((w-10)//2,(h-34)//2+4),22); pygame.draw.circle(inner,(245,230,215),((w-10)//2,(h-34)//2+34),30)
    screen.blit(inner,(x+5,y+5)); pygame.draw.circle(screen,(72,200,96) if live else (120,120,120),(x+16,y+h-16),6)
    text(name, x+30, y+h-26); st=(bag or {}).get("status","")
    if st: text(st, x+w-100, y+h-24, SMALL, (170,170,175))
    if name != NAME and live and member(MACHINE):
        r=pygame.Rect(x+w-72,y+10,60,26); call_buttons[name]=r
        pygame.draw.rect(screen,(36,140,72) if not comms_running() else (90,90,95),r,border_radius=6); text("Call", r.x+16, r.y+5, SMALL)
def draw_strip(tiles):
    sx0=TRACK_W; pygame.draw.rect(screen,PANEL,(sx0,0,W-TRACK_W,H)); text("Communicator", sx0+16, 12)
    if a.fdf:
        if member(MACHINE): text(f"FD&F: {MACHINE} is a member ({len(fdf_alive)} alive)", sx0+16, 34, SMALL, (110,220,130))
        else: text(f"FD&F: {MACHINE} NOT a member" if fdf_seen else "FD&F: no answer yet", sx0+16, 34, SMALL, (240,90,80))
    else: text("tiles: the main memory (comm/*)", sx0+16, 34, SMALL, (150,150,155))
    tw=W-TRACK_W-32; th=120; ty=58; call_buttons.clear()
    for n in sorted(set(tiles) | {NAME}):
        draw_tile(sx0+16, ty, tw, th, n, tiles.get(n), n in tiles); ty+=th+10
        if ty>H-th: break

# ----------------------------------------------------------------------------- the lobby screen
start_btn=pygame.Rect(40, H-110, 200, 56); lobby_msg=""; last_result=None
def draw_lobby(lob, servers, recs, results):
    screen.fill((30,34,40)); text("Lobby", 40, 24, BIG)
    text(f"main memory {MAIN_H}:{MAIN_P}   -   a race starts at {a.min_players} free players, or on Start", 40, 64, SMALL, (180,180,190))
    y=100; text("in the lobby", 40, y, FONT, (200,220,255)); y+=28
    busy=busy_players(servers)
    for n in sorted(lob):
        b=lob[n]; st="racing" if n in busy else ("wants to start" if b.get("start") else "waiting")
        text(f"{n:12s} {b.get('machine','')[:16]:16s} robots {b.get('bots',0)}   {'can host' if b.get('can_host') else '         '}   {st}", 40, y, SMALL); y+=20
    y+=10; text("races running", 40, y, FONT, (200,220,255)); y+=28
    for r, b in sorted(servers.items()):
        text(f"{r}  on {b.get('host')} ({b.get('addr')}:{b.get('port')})  {', '.join(b.get('players',[]))}", 40, y, SMALL); y+=20
    if not servers: text("none", 40, y, SMALL, (150,150,155)); y+=20
    x2=470; y2=100; text("records (permanent)", x2, y2, FONT, (200,220,255)); y2+=28
    for n, r in sorted(recs.items(), key=lambda kv: (-kv[1].get("wins",0), kv[1].get("best_lap") or 1e9))[:12]:
        bl=r.get("best_lap"); text(f"{n:12s} races {r.get('races',0):3d}  wins {r.get('wins',0):3d}  best lap {bl if bl else '-'}", x2, y2, SMALL); y2+=20
    y2+=10
    if last_result:
        text(f"last race {last_result.get('race')}", x2, y2, FONT, (200,220,255)); y2+=24
        text(f"on {last_result.get('host')}: {last_result.get('why','')}", x2, y2, SMALL, (170,170,180)); y2+=22
        for i, s in enumerate(last_result.get("order", [])[:10]):
            text(f"{i+1:2d}. {s['kart']:14s} laps {s['laps']}  time {s['time'] if s['time'] else '-'}  best {s['best_lap'] if s['best_lap'] else '-'}", x2, y2, SMALL); y2+=20
    pygame.draw.rect(screen,(36,140,72),start_btn,border_radius=10); text("Start", start_btn.x+70, start_btn.y+16, FONT)
    if lobby_msg: text(lobby_msg, 260, H-96, SMALL, (240,200,120))

# ----------------------------------------------------------------------------- the main loop
state="lobby"; my_race=None; joined_at=0; placed=False; start_at=0; trail=[]
fps_t=time.time(); fps_n=0; fps=0.0; w_n0=0; T0=time.time(); want_start=False
while running:
    dt=clock.tick(60)/1000.0; now=time.time()
    for e in pygame.event.get():
        if e.type==pygame.QUIT: running=False
        if e.type==pygame.KEYDOWN and e.key==pygame.K_p: autodrive=not autodrive
        if e.type==pygame.KEYDOWN and e.key in (pygame.K_s, pygame.K_RETURN) and state=="lobby": want_start=True
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
            if state=="lobby" and start_btn.collidepoint(e.pos): want_start=True
            for who,r in list(call_buttons.items()):
                if r.collidepoint(e.pos): launch_comms(who); break
    if os.environ.get("KART_TEST_START") == NAME and now-T0 > float(os.environ.get("KART_TEST_START_AFTER","3")) and state=="lobby" and not my_intent:
        want_start=True                                         # test only: press Start
    lob={n: b for n, b in main.family("lobby").items() if member(b.get("machine",""))}
    servers=running_servers(); tiles={n: b for n, b in comm.family("tile").items() if n==NAME or member(b.get("machine",""))}
    for who,b in tiles.items():                                 # someone is calling ME
        if who!=NAME and b.get("calling")==NAME and answered.get(who)!=b.get("call_id") and member(MACHINE):
            answered[who]=b.get("call_id"); launch_comms("")

    if state=="lobby":
        mine=[r for r, b in servers.items() if NAME in b.get("players",[])]
        if mine:                                                # my race is up: map the race memory
            my_race=min(mine); b=servers[my_race]
            race=Region("race memory", b["addr"], int(b["port"]), b.get("api","/ram.php"), "race",
                        ends=lambda v: ("meta","info") not in v)   # the race memory says the race is over: meta {}
            state="racing"; joined_at=now; placed=False; start_at=float(b.get("start_at", now)); trail=[]
            say(f"race {my_race} is up on {b['host']} at {b['addr']}:{b['port']}: racing")
            if my_intent and my_intent["race"]==my_race: my_intent=None
        else:
            win=winning_intent(lob)
            if my_intent and (not win or win["race"]!=my_intent["race"] or my_intent["host"] not in lob): my_intent=None   # lost, or its host left
            if win and win["host"]==NAME and win["race"] not in launched and win["race"] not in servers: host_race(win)
            free=free_players(lob, servers); ready=[n for n in free if now >= float(lob[n].get("ready_at",0))]
            auto=len(ready)>=a.min_players and not win and ready[0]==NAME
            if (want_start or auto) and not win and not my_intent:
                my_intent, lobby_msg = make_intent(lob, servers)
                if my_intent: say(f"start: race {my_intent['race']}, host {my_intent['host']}, {len(my_intent['roster'])} player(s)" + (" (enough players)" if auto and not want_start else " (Start)"))
            want_start=False
            if win: lobby_msg=f"race {win['race']} starting on {win['host']}'s machine..."
            elif not my_intent and not lobby_msg.startswith("no machine"): lobby_msg=f"waiting: {len(ready)} of {a.min_players} players ready"
        publish_lobby(); publish_tile()
        if state=="lobby":
            recs=main.family("record"); res=main.family("result")
            if res and (not last_result or max(res)!=last_result.get("race")): last_result=res[max(res)]
            draw_lobby(lob, servers, recs, res); draw_strip(tiles); pygame.display.flip()
    if state=="racing":
        b=servers.get(my_race)
        if not b:                                               # the race server wrote its tuple {} or is gone
            race.stop(); race=None; state="lobby"; res=main.family("result")
            if my_race in res: last_result=res[my_race]
            say(f"race {my_race} is over ({last_result.get('why') if last_result and last_result.get('race')==my_race else 'its server is gone, no result'}): back in the lobby")
            my_race=None; ready_at=time.time()+10.0; publish_lobby(); continue
        grid=race.family("grid"); meta=race.family("meta").get("info")
        if not placed and meta and all(k in grid for k in MINE):
            for k, o in MINE.items(): o.place(grid[k])
            placed=True; laps_total=int(meta["laps"])
            say(f"placed on the grid from the race memory: " + ", ".join(f"{k} slot {grid[k]['slot']}" for k in MINE) + f"; {laps_total} lap(s)")
        drawn={}
        for n,(kb,rt) in race.family_t("kart").items():
            if n in MINE: continue
            x,y,h=carried(kb, now-rt); drawn[n]=(x,y,h,kb.get("color"))
        go = placed and now >= start_at
        if go:
            k=pygame.key.get_pressed()
            for o in [me]+bots:
                if o.k["lap"] >= laps_total: o.k["v"]*=0.9; o.k["w"]=0.0; continue          # finished: roll to a stop
                near=[(p[0],p[1]) for p in drawn.values()]+[(q.k["x"],q.k["y"]) for q in [me]+bots if q is not o]
                if o is me and not autodrive:
                    s=(1 if (k[pygame.K_RIGHT] or k[pygame.K_d]) else 0)-(1 if (k[pygame.K_LEFT] or k[pygame.K_a]) else 0)
                    g=(1 if (k[pygame.K_UP] or k[pygame.K_w]) else 0)-(1 if (k[pygame.K_DOWN] or k[pygame.K_s]) else 0)
                else: s,g=autopilot(o,dt,near)
                drive(o,s,g,dt)
            if abs(me.k["v"])>120: trail.append((me.k["x"],me.k["y"]))
            if len(trail)>60: trail.pop(0)
        if placed:
            for o in [me]+bots: o.publish()
        publish_tile()
        screen.blit(TRACK,(0,0))
        for (x,y) in trail: pygame.draw.circle(screen,(30,30,34),(int(x),int(y)),3)
        for n,(x,y,h,c) in drawn.items():
            spr=pygame.transform.rotate(sprite(n,c),-math.degrees(h)); screen.blit(spr,spr.get_rect(center=(x,y))); text(n, x-10, y-26, SMALL)
        for o in bots+[me]:
            spr=pygame.transform.rotate(sprite(o.name,o.color),-math.degrees(o.k["h"])); screen.blit(spr,spr.get_rect(center=(o.k["x"],o.k["y"])))
            if o.robot: text(o.name, o.k["x"]-10, o.k["y"]-26, SMALL)
        if placed:
            text(f"Lap {min(me.k['lap']+ (0 if me.k['lap']>=laps_total else 1), laps_total)}/{laps_total}" if me.k['lap']<laps_total else "FINISHED", 16, 14, BIG)
            if not go: text(f"{math.ceil(start_at-now)}", TRACK_W//2-10, H//2-20, BIG, (255,230,120))
        rec=race.family("record").get(NAME, {})
        text(f"race {my_race} on {b.get('host')}   |   my record: races {rec.get('races',0)}, wins {rec.get('wins',0)}, best lap {rec.get('best_lap') or '-'}   |   {fps:.0f} fps",
             16, H-24, SMALL, (210,210,210))
        draw_strip(tiles); pygame.display.flip()
    if os.environ.get("KART_TEST_SHOT") and now-T0 > float(os.environ.get("KART_TEST_SHOT_AT","6")) and not getattr(sys.modules[__name__], "_shot", False):   # test only
        pygame.image.save(screen, os.environ["KART_TEST_SHOT"]); _shot = True
    fps_n+=1
    if now-fps_t>=1.0:
        fps=fps_n/(now-fps_t)
        if os.environ.get("KART_TEST_FPS"):
            n_seen=len(race.family("kart"))-len([k for k in race.family("kart") if k in MINE]) if race else 0
            w=main.writes+comm.writes+(race.writes if race else 0)
            say(f"[KART_TEST_FPS] {NAME} {state} {fps:.1f} fps, {n_seen} other kart(s) seen, {(w-w_n0)/(now-fps_t):.1f} writes/s")
            w_n0=w
        fps_n=0; fps_t=now
pygame.quit()
# The sessions end with the process: the memory writes this game's lobby tuple, tile and karts {} for everyone.
