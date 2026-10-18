#!/usr/bin/env python3
# kartdemo.py -- a playable top-down kart racer whose multiplayer is Ribbit shared memory,
# AND which simultaneously maps a SECOND, independent region (the Communicator) for a video call.
#
# This is the architecture made visible: ONE application, TWO independently-governed memories.
#   * the RACE region  (race/kart/<id>)  -- karts write their pose, read everyone else's
#   * the COMM region  (comm/tile/<id>)  -- presence + a call tile per player
# The two regions know nothing about each other. The app maps both and composes them on screen.
#
#   python kartdemo.py --name red --race-host H:PORT --comm-host H:PORT [--bots N] [--autodrive] [--headless]
#
# Controls: arrow keys / WASD to drive, P toggles the autopilot on your own kart.  Deps: pygame, and libkartram
# (kartram.dll on Windows) beside ribbit.py.
#
# The memory is memory (DESIGN.md is the truth table):
# [KART_ON_THE_SESSION_V1] one permanent FNW1 session per region. A write is posted -- it goes out and the game carries
#   on; nothing waits for it. The frame loop never waits on the network.
# [KART_WRITES_ON_CHANGE_V1] a kart is written when it departs from where readers will draw it (its last pose carried
#   forward along its heading); a tile when what it says changes. Nothing is written to stay fresh.
# [KART_HELD_READS_V1] each family is read once, then held: the region answers when something is written to it, and
#   only what was written comes back. That is the game's callback; nothing polls.
# [OWNED_CELL_GOES_WITH_THE_SESSION_V1] every tuple the game writes is its session's: when the game's session ends the
#   region writes it {}, the other games' holds fire, and it is gone. No timeout decides who is still here.
# [KART_AUTODRIVE_V1] --bots N adds N robot karts owned by this game (<name>-bot1..N); --autodrive (or P) puts your own
#   kart on the same autopilot. To anyone reading the race region a robot is a kart like any other.
import sys, os, time, math, random, argparse, threading, subprocess, json

# ----------------------------------------------------------------------------- args
ap = argparse.ArgumentParser()
ap.add_argument("--name", default=None, help="your kart/player id")
ap.add_argument("--race-host", default=None, help="race region host:port (default: from the FD&F region with --fdf, else 127.0.0.1:8799)")
ap.add_argument("--fdf", default=None, help="FD&F RAM server host:port: players and locations come from FD&F")
ap.add_argument("--fdf-client", default=None, help="the FD&F client program (default: next to this script, or /opt/fdf/bin)")
ap.add_argument("--comm-host", default=None, help="communicator region host:port (default: same as race)")
ap.add_argument("--laps", type=int, default=3)
ap.add_argument("--bots", type=int, default=0, help="robot karts this game owns and drives ([KART_AUTODRIVE_V1])")
ap.add_argument("--autodrive", action="store_true", help="start with your own kart on the autopilot (P toggles it)")
ap.add_argument("--headless", action="store_true", help="no window, no sound: for a machine that only runs karts")
# [KART_CALLS_V1] the real Communicator: its memory and media server, and the program each player launches
ap.add_argument("--comms-ram", default=None, help="Communicator memory host:port (default: race host, port 8800)")
ap.add_argument("--comms-media", default=None, help="Communicator media server host:port (default: race host, port 8994)")
ap.add_argument("--comms-app", default="comms-app.exe" if os.name == "nt" else "comms-app", help="path to the Communicator program")
ap.add_argument("--comms-args", default="", help='more options for comms-app, e.g. "--camera video=<name> --mic 1" or "--synthetic 640x360"')
a = ap.parse_args()
if a.headless:
    os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
import pygame
from ribbit import Memory, RibbitError

NAME = a.name or random.choice(["red","blue","green","yellow","pink","cyan"])
HERE = os.path.dirname(os.path.abspath(__file__))
def hp(s, d="127.0.0.1", p=8799):
    if ":" in s: h, pp = s.rsplit(":",1); return h, int(pp)
    return s, p
_say_lock = threading.Lock()
def say(m):                                   # one line at a time: the region threads and the frame loop all report
    with _say_lock: sys.stdout.write(f"kartdemo: {m}\n"); sys.stdout.flush()

# [FDF_GATED_V1] with --fdf, FD&F is the source of truth: where the race and the Communicator are (fdf.where.*,
# published by the droplet's fdf-services.sh), and who is here (FD&F's participants: machines whose FD&F client is up).
FDF_API = "/FawcettInnovations.RINA.ram_interface.php"
FDF = hp(a.fdf, p=8840) if a.fdf else None
MACHINE = os.environ.get("COMPUTERNAME") or __import__("socket").gethostname()   # the name this machine's FD&F client announces
WHERE = {}
if FDF:
    try:
        f = Memory(FDF[0], FDF[1], api=FDF_API)
        for c in f.read("fdf", "where"): WHERE[c.get("instance")] = c.get("bag", {})
        f.close()
    except RibbitError as e:
        sys.exit(f"kartdemo: cannot read where the race is from FD&F: {e}")
    if "race" not in WHERE and not a.race_host:
        sys.exit(f"kartdemo: the FD&F region at {a.fdf} does not say where the race is (run fdf-services.sh start on the droplet)")
if not a.race_host: a.race_host = f'{WHERE["race"]["host"]}:{WHERE["race"]["port"]}' if "race" in WHERE else "127.0.0.1:8799"
if FDF and "comms" in WHERE and not a.comms_ram: a.comms_ram = WHERE["comms"].get("ram")
if FDF and "comms" in WHERE and not a.comms_media: a.comms_media = WHERE["comms"].get("media")
RACE_H, RACE_P = hp(a.race_host)
COMM_H, COMM_P = hp(a.comm_host) if a.comm_host else (RACE_H, RACE_P)
COMMS_RAM = a.comms_ram or f"{RACE_H}:8800"          # [KART_CALLS_V1]
COMMS_MEDIA = a.comms_media or f"{RACE_H}:8994"
say(f"{NAME} on {MACHINE}: race region {RACE_H}:{RACE_P}, comm region {COMM_H}:{COMM_P}, "
    f"{a.bots} robot kart(s), autopilot {'on' if a.autodrive else 'off'}")

# ----------------------------------------------------------------------------- the regions
running = True
class Region:
    """One region the game maps: one session, the family it follows, and the view of it the game holds.
    The follower thread is the only reader: it reads the family once, then holds; every cell written comes back once
    and goes into the view ({} = gone). A session that breaks is said, and a new one is opened a second later; on a
    new session the game writes its own truths again (gen changes) and the view is read whole again."""
    def __init__(self, name, host, port, family):
        self.name, self.host, self.port, self.family = name, host, port, family
        self.mem = None; self.gen = 0; self.view = {}; self.lock = threading.Lock(); self.bad = None; self.writes = 0
        self.refused_said = 0
        threading.Thread(target=self.follower, daemon=True).start()
    def failing(self, e):
        if self.bad != str(e): say(f"{self.name} region FAILING: {e}"); self.bad = str(e)
    def write(self, s, v, i, bag):
        m = self.mem
        if m is None: return False
        try:
            m.write(s, v, i, bag, own=True); self.writes += 1
            r = m.refused()
            if r != self.refused_said: say(f"{self.name} region refused {r - self.refused_said} write(s) on this session"); self.refused_said = r
            return True
        except RibbitError as e: self.failing(e); return False
    def follower(self):
        retired = []
        while running:
            try:
                m = Memory(self.host, self.port)
            except RibbitError as e:
                self.failing(e); time.sleep(1.0); continue
            first = True
            try:
                for cells in m.follow(*self.family):
                    now = time.time()
                    with self.lock:
                        if first: self.view = {}
                        for c in cells:
                            if c["bag"]: self.view[c["instance"]] = (c["bag"], now)
                            else: self.view.pop(c["instance"], None)
                    if first:
                        self.mem = m; self.gen += 1; first = False
                        if self.bad is not None: say(f"{self.name} region working again (a new session)"); self.bad = None
                        for r in retired: r.close()
                        retired = []
            except RibbitError as e:
                self.failing(e)
            if self.mem is m: self.mem = None
            retired.append(m)             # closed once the next session is up: the frame loop may still be posting on it
            time.sleep(1.0)
    def snapshot(self):
        with self.lock: return dict(self.view)

race = Region("race", RACE_H, RACE_P, ("race", "kart"))     # the race world state
comm = Region("comm", COMM_H, COMM_P, ("comm", "tile"))     # the communicator: presence + call tiles

# ----------------------------------------------------------------------------- look
pygame.init()
W, H = 1180, 720
TRACK_W = 860                            # the race area; the rest is the call strip
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption(f"FrogNet Kart -- {NAME}  (race + communicator, two Ribbit regions)")
clock = pygame.time.Clock()
FONT  = pygame.font.SysFont("Arial", 18, bold=True)
BIG   = pygame.font.SysFont("Arial", 30, bold=True)
SMALL = pygame.font.SysFont("Arial", 14)

# palette per kart colour
COLORS = {"red":(222,67,67),"blue":(66,122,222),"green":(72,190,96),
          "yellow":(230,198,60),"pink":(224,96,180),"cyan":(70,196,208)}
BOT_COLORS = [(240,140,40),(150,90,220),(120,200,40),(40,170,150),(200,60,110),(110,110,240),(220,220,120),(170,120,80)]
def col(n): return COLORS.get(n, (200,200,200))

GRASS=(38,124,67); GRASS2=(44,138,76); TAR=(54,56,62); TAREDGE=(226,226,230)
CURB_A=(214,70,70); CURB_B=(238,238,238); INK=(28,30,34); PANEL=(24,26,30)

# an oval track centre-line + width, in race-area pixel space
CX, CY, RX, RY, ROAD = TRACK_W/2, H/2, TRACK_W/2-120, H/2-110, 62
def track_point(t):  # t in radians -> centre-line point
    return CX + RX*math.cos(t), CY + RY*math.sin(t)
def nearest_t(x,y):  return math.atan2((y-CY)/RY, (x-CX)/RX)

def draw_track(surf):
    surf.fill(GRASS)
    # grass stripes
    for i in range(0, W, 48):
        if (i//48)%2==0: pygame.draw.rect(surf,GRASS2,(i,0,48,H))
    # tarmac ring (draw many circles along the oval)
    pts=[track_point(2*math.pi*i/240) for i in range(240)]
    for (x,y) in pts:
        pygame.draw.circle(surf,TAR,(int(x),int(y)),ROAD)
    # inner/outer edges + curbs
    for k,(rx,ry) in enumerate([(RX-ROAD,RY-ROAD),(RX+ROAD,RY+ROAD)]):
        last=None
        for i in range(0,241):
            t=2*math.pi*i/240; x=CX+rx*math.cos(t); y=CY+ry*math.sin(t)
            if last:
                c=CURB_A if (i//4)%2==0 else CURB_B
                pygame.draw.line(surf,c,last,(x,y),4)
            last=(x,y)
    # start/finish line
    sx,sy=track_point(-math.pi/2)
    nx,ny=math.cos(-math.pi/2+math.pi/2),math.sin(-math.pi/2+math.pi/2)
    for o in range(-ROAD,ROAD,10):
        x=sx+nx*o; y=sy+ny*o
        c=(240,240,240) if (o//10)%2==0 else (40,40,40)
        pygame.draw.rect(surf,c,(x-3,y-5,6,10))

TRACK=pygame.Surface((W,H)); draw_track(TRACK)

def kart_sprite(color):
    s=pygame.Surface((34,22),pygame.SRCALPHA)
    pygame.draw.rect(s,(20,20,24),(0,3,34,16),border_radius=5)      # tyres band
    pygame.draw.rect(s,color,(4,0,26,22),border_radius=6)           # body
    pygame.draw.rect(s,(245,245,250),(22,5,8,12),border_radius=3)   # windshield
    pygame.draw.rect(s,(15,15,18),(0,0,5,6)); pygame.draw.rect(s,(15,15,18),(0,16,5,6))
    pygame.draw.rect(s,(15,15,18),(29,0,5,6)); pygame.draw.rect(s,(15,15,18),(29,16,5,6))
    return s
SPRITES={}
def sprite(n, color=None):
    c=tuple(color) if color else col(n)
    if c not in SPRITES: SPRITES[c]=kart_sprite(c)
    return SPRITES[c]

# ----------------------------------------------------------------------------- the karts this game owns
VMAX=240.0; ACC=260.0; TURN=2.8; FRICT=0.92
# [KART_WRITES_ON_CHANGE_V1] how far the truth may depart from what readers draw before the owner writes again
TOL_XY, TOL_H = 2.0, 0.05      # a change of speed or turn rate shows up here, in position and heading, as soon as it matters
def carried(b, dt):
    """Where a kart written as b is dt seconds later: along its heading at its speed, turning at its turn rate. The
    owner and every reader compute the same thing, so the owner knows exactly what the others are drawing."""
    x,y,h,v,w=b.get("x",0),b.get("y",0),b.get("h",0),b.get("v",0),b.get("w",0)
    if abs(w) < 1e-6: return x+math.cos(h)*v*dt, y+math.sin(h)*v*dt, h
    h2=h+w*dt
    return x+v/w*(math.sin(h2)-math.sin(h)), y-v/w*(math.cos(h2)-math.cos(h)), h2
class Owned:
    """A kart this game drives and is the only writer of: its truth is race.kart.<name>."""
    def __init__(self, name, color, robot, t0, lane, skill=1.0):
        self.name=name; self.color=color; self.robot=robot; self.skill=skill
        sx,sy=track_point(t0); nx,ny=math.cos(t0+math.pi/2),math.sin(t0+math.pi/2)
        self.k=dict(x=sx+nx*lane, y=sy+ny*lane, h=t0+math.pi/2, v=0.0, w=0.0, lap=0)
        self.last_t=nearest_t(self.k["x"],self.k["y"]); self.unwrapped=0.0
        self.lane=lane; self.dodge=0.0; self.wander=random.uniform(0,6.28); self.wander_rate=random.uniform(0.3,0.7)
        self.w=None; self.w_at=0.0; self.w_gen=-1
    def publish(self):
        """Write my truth if it has departed from where every reader is drawing it (my last write carried forward)."""
        k=self.k; now=time.time(); w=self.w
        if w is not None and self.w_gen == race.gen:
            px,py,ph=carried(w, now-self.w_at)
            if (math.hypot(k["x"]-px,k["y"]-py) <= TOL_XY and abs(math.remainder(k["h"]-ph,2*math.pi)) <= TOL_H
                    and k["lap"] == w["lap"]): return
        bag={"x":k["x"],"y":k["y"],"h":k["h"],"v":k["v"],"w":k["w"],"lap":k["lap"],"name":self.name,
             "machine":MACHINE,"color":list(self.color),"robot":self.robot,"owner":NAME}
        gen=race.gen
        if race.write("race","kart",self.name,bag): self.w=bag; self.w_at=now; self.w_gen=gen

def drive(o, steer, gas, dt):
    k=o.k
    k["v"]+=gas*ACC*dt
    if gas==0: k["v"]*=FRICT
    if gas==0 and abs(k["v"])<1.0: k["v"]=0.0          # at rest is at rest: a kart standing still has nothing to write
    k["v"]=max(-VMAX*0.4,min(VMAX,k["v"]))
    k["w"]=steer*TURN*(k["v"]/VMAX if abs(k["v"])>5 else 0)       # the turn rate: part of the kart's truth
    k["h"]+=k["w"]*dt
    k["x"]+=math.cos(k["h"])*k["v"]*dt
    k["y"]+=math.sin(k["h"])*k["v"]*dt
    # keep roughly on the island
    k["x"]=max(10,min(TRACK_W-10,k["x"])); k["y"]=max(10,min(H-10,k["y"]))
    # laps
    tt=nearest_t(k["x"],k["y"]); d=math.remainder(tt-o.last_t,2*math.pi); o.unwrapped+=d; o.last_t=tt
    if o.unwrapped>=2*math.pi: o.unwrapped-=2*math.pi; k["lap"]+=1

LOOK=0.32                                  # how far ahead along the track the autopilot aims (radians of the oval)
def autopilot(o, dt, others_xy):
    """[KART_AUTODRIVE_V1] follow the track in its own lane, wander a little, and step aside for a kart just ahead."""
    k=o.k; x,y,h=k["x"],k["y"],k["h"]
    fx,fy=math.cos(h),math.sin(h)
    blocked=0.0
    for (ox,oy) in others_xy:
        dx,dy=ox-x,oy-y; ahead=dx*fx+dy*fy; side=-dx*fy+dy*fx
        if 0<ahead<70 and abs(side)<24: blocked=-1.0 if side>0 else 1.0
    o.dodge+=(blocked*30.0-o.dodge)*min(1.0,dt*4.0) if blocked else -o.dodge*min(1.0,dt*1.5)
    o.wander+=dt*o.wander_rate
    lane=max(-(ROAD-16),min(ROAD-16, o.lane+14*math.sin(o.wander)+o.dodge))
    tt=nearest_t(x,y)+LOOK
    tx,ty=CX+(RX+lane)*math.cos(tt), CY+(RY+lane)*math.sin(tt)
    err=math.remainder(math.atan2(ty-y,tx-x)-h,2*math.pi)
    steer=max(-1.0,min(1.0,err*3.0))
    want=o.skill*VMAX*(1.0-0.35*min(1.0,abs(err)))*(0.8 if blocked else 1.0)
    gas=1 if k["v"]<want else 0
    return steer,gas

t0=-math.pi/2
seed=sum(ord(c) for c in NAME)
me=Owned(NAME, col(NAME), False, t0, ((seed%5)-2)*18)
autodrive=a.autodrive
bots=[Owned(f"{NAME}-bot{i+1}", BOT_COLORS[(seed+i)%len(BOT_COLORS)], True,
            t0-0.14*(i+1), (-30 if i%2 else 30)+random.uniform(-6,6), skill=random.uniform(0.72,0.95))
      for i in range(max(0,a.bots))]
MINE={o.name for o in [me]+bots}
trail=[]

# ----------------------------------------------------------------------------- comm region: a call tile
# Each player's tile says who it is, its status and whom it is calling. The moving pattern on it is drawn locally
# from the clock: it is not anyone's truth, so it is not written.
# [KART_CALLS_V1] calls through the REAL Communicator. Calling: my tile says whom I call ("calling") and I launch
# comms-app --auto-join <them>, which rings them as soon as they are in its lobby. Being called: my game sees a tile
# whose "calling" is me and launches comms-app WITHOUT auto-join, so the call rings in my window and I answer it.
calling = ""            # whom I am calling now ("" = nobody)
answered = {}          # caller -> the id of their call I last opened a window for
call_id = ""            # the id of my current call (when I pressed Call)
comms_proc = None       # my running Communicator, if any
call_buttons = {}       # name -> the Call button's rectangle on their tile, this frame
def comms_running(): return comms_proc is not None and comms_proc.poll() is None
def launch_comms(callee=""):
    global comms_proc, calling
    if comms_running(): return
    import shlex
    cmd = [a.comms_app, "--ram", COMMS_RAM, "--media", COMMS_MEDIA, "--name", NAME] + (["--auto-join", callee] if callee else []) \
          + shlex.split(a.comms_args)
    global call_id
    try: comms_proc = subprocess.Popen(cmd); calling = callee; call_id = f"{time.time():.3f}" if callee else ""
    except OSError as e: say(f"cannot start the Communicator ({a.comms_app}): {e}")

tile_written=None; tile_gen=-1
def publish_tile():
    """[KART_WRITES_ON_CHANGE_V1] my tile is written when what it says changes (or on a new session)."""
    global tile_written, tile_gen
    up=comms_running()
    st = ("calling " + calling) if (calling and up) else ("in a call" if up else "on call")
    bag={"name":NAME,"color":list(col(NAME)),"status":st,"calling":calling if up else "","call_id":call_id if up else "","machine":MACHINE}
    if bag == tile_written and tile_gen == comm.gen: return
    gen=comm.gen
    if comm.write("comm","tile",NAME,bag): tile_written=bag; tile_gen=gen

# ----------------------------------------------------------------------------- FD&F membership
fdf_alive=set()          # [FDF_GATED_V1] FD&F machine names alive now (only with --fdf)
fdf_seen=False           # FD&F has answered at least once
def fdf_tool(base):
    exe=base+(".exe" if os.name=="nt" else "")
    for c in [os.path.join(HERE,exe), os.path.join("/opt/fdf/bin",exe)]:
        if os.path.exists(c): return c
    return exe                                                   # on the PATH, or said when it cannot start
def fdf_reader():
    """[KART_HELD_READS_V1] FD&F's own participant watch, on this game's own session to FD&F: watch_participants once,
    then a held wait that FD&F answers when the participant set changes; the set is read again on each answer."""
    global fdf_alive, fdf_seen
    me=f"kart:{MACHINE}:{NAME}:{os.getpid()}"; bad=None
    while running:
        try:
            f=Memory(FDF[0], FDF[1], api=FDF_API)
            r=f.call("POST", FDF_API+"?op=rina", json.dumps({"op":"watch_participants","proc":me}))
            if not r.get("ok"): raise RibbitError(f"FD&F refused the participant watch: {r}")
            after=0
            while running:
                ps=f.call("POST", FDF_API+"?op=rina", json.dumps({"op":"participants"})).get("participants",[])
                fdf_alive={q.get("name","") for q in ps if q.get("alive")}; fdf_seen=True
                if bad is not None: say("FD&F participant watch working again"); bad=None
                ensure_fdf_member()
                text=f.call_text("POST", FDF_API+"?op=rina_block", json.dumps({"op":"wait","proc":me,"after":after}), 3.0e8)
                for line in text.split("\n")[1:]:                 # "E" then "<n>\t<event>" lines
                    if "\t" in line: after=max(after, int(line.split("\t",1)[0]))
        except (RibbitError, ValueError) as e:
            if bad != str(e): say(f"FD&F participant watch FAILING: {e}"); bad=str(e)
        time.sleep(1.0)                                            # a new session, a second later
def member(machine): return (not FDF) or (machine in fdf_alive)
# [FDF_AUTOCLIENT_V1] this machine is not an FD&F member: start the FD&F client (it IS the machine's membership, so it
# keeps running after the game). Once per game: if it is already running for another game, the machine is a member.
fdf_client_proc=None
fdf_client_t=0.0
def fdf_client_path():
    return a.fdf_client if a.fdf_client and os.path.exists(a.fdf_client) else fdf_tool("fdf-client")
def ensure_fdf_member():
    global fdf_client_proc
    global fdf_client_t
    if not fdf_seen or member(MACHINE): return
    if fdf_client_proc and fdf_client_proc.poll() is None: return          # running: it is joining
    if fdf_client_proc is False or time.time()-fdf_client_t < 10: return  # cannot start, or started <10 s ago
    fdf_client_t=time.time()                                               # ended (e.g. the FD&F server restarted): start it again
    cmd=[fdf_client_path(), f"{FDF[0]}:{FDF[1]}"]
    try:
        flags=subprocess.CREATE_NEW_CONSOLE if os.name=="nt" else 0      # its own window on Windows: you see it join
        fdf_client_proc=subprocess.Popen(cmd, creationflags=flags) if os.name=="nt" else subprocess.Popen(cmd, start_new_session=True)
        say(f"{MACHINE} was not an FD&F member: started {cmd[0]} {cmd[1]}")
    except OSError as e:
        fdf_client_proc=False; say(f"{MACHINE} is not an FD&F member, and the FD&F client could not start ({cmd[0]}): {e}")
if FDF: threading.Thread(target=fdf_reader,daemon=True).start()

# ----------------------------------------------------------------------------- comm tile rendering
def draw_tile(surf, x, y, w, h, name, bag, live):
    pygame.draw.rect(surf,(16,17,20),(x,y,w,h),border_radius=10)
    # synthetic "video": moving bands in the player's colour (stand-in for a webcam frame), from the local clock
    c=col(name); fr=int(time.time()*8)%1000
    inner=pygame.Surface((w-10,h-34))
    for yy in range(0,h-34,4):
        shade=0.5+0.5*math.sin((yy+fr*3)*0.08)
        inner.fill((int(c[0]*shade),int(c[1]*shade),int(c[2]*shade)),(0,yy,w-10,4))
    # a simple "face" blob so it reads as a person on a call
    pygame.draw.circle(inner,(245,230,215),((w-10)//2,(h-34)//2+4),22)
    pygame.draw.circle(inner,(245,230,215),((w-10)//2,(h-34)//2+34),30)
    surf.blit(inner,(x+5,y+5))
    dot=(72,200,96) if live else (120,120,120)
    pygame.draw.circle(surf,dot,(x+16,y+h-16),6)
    surf.blit(FONT.render(name,True,(240,240,240)),(x+30,y+h-26))
    st=(bag or {}).get("status","")
    if st: surf.blit(SMALL.render(st,True,(170,170,175)),(x+w-90,y+h-24))
    if name != NAME and live and member(MACHINE):      # [KART_CALLS_V1] a Call button on everyone else's tile ([FDF_GATED_V1]: members only)
        r=pygame.Rect(x+w-72,y+10,60,26); call_buttons[name]=r
        pygame.draw.rect(surf,(36,140,72) if not comms_running() else (90,90,95),r,border_radius=6)
        surf.blit(SMALL.render("Call",True,(255,255,255)),(r.x+16,r.y+5))

def draw_kart(n, x, y, h, color):
    spr=pygame.transform.rotate(sprite(n,color),-math.degrees(h))
    screen.blit(spr,spr.get_rect(center=(x,y)))
    screen.blit(SMALL.render(n,True,(240,240,240)),(x-10,y-26))

# ----------------------------------------------------------------------------- main loop
T0=time.time(); fps_t=time.time(); fps_n=0; fps=0.0; w_n0=0
while running:
    dt=clock.tick(60)/1000.0
    for e in pygame.event.get():
        if e.type==pygame.QUIT: running=False
        if e.type==pygame.KEYDOWN and e.key==pygame.K_p: autodrive=not autodrive      # [KART_AUTODRIVE_V1]
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:      # [KART_CALLS_V1] Call: open the Communicator to them
            for who,r in list(call_buttons.items()):
                if r.collidepoint(e.pos): launch_comms(who); break
    now=time.time()
    tsnap={n:b for n,(b,_) in comm.snapshot().items() if n==NAME or member(b.get("machine",""))}   # [FDF_GATED_V1]
    # everyone else's karts: their last write carried forward (heading, speed, turn rate) for the time since it arrived
    # (rendering between writes, not reconciliation: the owner writes again whenever this departs from its truth)
    drawn={}
    for n,(b,rt) in race.snapshot().items():
        if n in MINE or not member(b.get("machine","")): continue                                  # [FDF_GATED_V1]
        x,y,h=carried(b, now-rt)
        drawn[n]=(x, y, h, b.get("color"))

    # physics: me, then my robots
    k=pygame.key.get_pressed()
    if autodrive:
        steer,gas=autopilot(me,dt,[(p[0],p[1]) for p in drawn.values()]+[(o.k["x"],o.k["y"]) for o in bots])
    else:
        steer=(1 if (k[pygame.K_RIGHT] or k[pygame.K_d]) else 0)-(1 if (k[pygame.K_LEFT] or k[pygame.K_a]) else 0)
        gas=(1 if (k[pygame.K_UP] or k[pygame.K_w]) else 0)-(1 if (k[pygame.K_DOWN] or k[pygame.K_s]) else 0)
    drive(me,steer,gas,dt)
    for o in bots:
        near=[(p[0],p[1]) for p in drawn.values()]+[(q.k["x"],q.k["y"]) for q in [me]+bots if q is not o]
        s,g=autopilot(o,dt,near); drive(o,s,g,dt)
    # drift trail
    if abs(me.k["v"])>120: trail.append((me.k["x"],me.k["y"]))
    if len(trail)>60: trail.pop(0)

    # WRITE: my truths, when they change -- posted, never waited for
    for o in [me]+bots: o.publish()
    publish_tile()

    # ---- draw ----
    screen.blit(TRACK,(0,0))
    for (x,y) in trail: pygame.draw.circle(screen,(30,30,34),(int(x),int(y)),3)
    if os.environ.get("KART_TEST_CALL") in tsnap and not comms_running():   # test only: press Call (again once a window ends)
        launch_comms(os.environ["KART_TEST_CALL"])
    call_buttons.clear()                                       # [KART_CALLS_V1]
    for who,bag in tsnap.items():                              # someone is calling ME: open my Communicator to answer
        calling_me = who != NAME and bag.get("calling") == NAME
        cid = bag.get("call_id", "")
        if calling_me and answered.get(who) != cid and member(MACHINE):   # each NEW call (its own id) opens a window, once
            answered[who] = cid; launch_comms("")
    # other karts (from the race region), my robots, then me
    for n,(x,y,h,c) in drawn.items(): draw_kart(n,x,y,h,c)
    for o in bots: draw_kart(o.name,o.k["x"],o.k["y"],o.k["h"],o.color)
    spr=pygame.transform.rotate(sprite(NAME),-math.degrees(me.k["h"]))
    screen.blit(spr,spr.get_rect(center=(me.k["x"],me.k["y"])))

    # HUD
    fps_n+=1
    if now-fps_t>=1.0:
        fps=fps_n/(now-fps_t)
        if os.environ.get("KART_TEST_FPS"):                      # test only
            say(f"[KART_TEST_FPS] {NAME} {fps:.1f} fps, {len(drawn)} other kart(s) seen, "
                f"{(race.writes+comm.writes-w_n0)/(now-fps_t):.1f} writes/s for {1+len(bots)} kart(s) and a tile")
        fps_n=0; fps_t=now; w_n0=race.writes+comm.writes
    screen.blit(BIG.render(f"Lap {min(me.k['lap'],a.laps)}/{a.laps}",True,(255,255,255)),(16,14))
    screen.blit(FONT.render(f"{int(abs(me.k['v']))} px/s" + ("   AUTOPILOT (P)" if autodrive else ""),True,(230,230,230)),(16,52))
    n_race=len(drawn)+1+len(bots); n_comm=len(tsnap)
    screen.blit(SMALL.render(f"race region: {n_race} kart(s), {len(bots)} of them my robots   |   comm region: {n_comm} on call   |   {fps:.0f} fps",
               True,(210,210,210)),(16,H-24))

    # ---- the communicator strip (fed by the SECOND region) ----
    sx0=TRACK_W
    pygame.draw.rect(screen,PANEL,(sx0,0,W-TRACK_W,H))
    screen.blit(FONT.render("Communicator",True,(240,240,240)),(sx0+16,12))
    if FDF:                                                      # [FDF_GATED_V1] the proof, on screen
        if member(MACHINE): msg,colr=f"FD&F: {MACHINE} is a member  ({len(fdf_alive)} alive)",(110,220,130)
        else: msg,colr=(f"FD&F: {MACHINE} NOT a member" if fdf_seen else f"FD&F: no answer from {a.fdf}"),(240,90,80)
        screen.blit(SMALL.render(msg,True,colr),(sx0+16,34))
    else:
        screen.blit(SMALL.render("second Ribbit region (comm/*)",True,(150,150,155)),(sx0+16,34))
    tw=W-TRACK_W-32; th=150; ty=58
    names=sorted(set(list(tsnap.keys()) or [NAME]))
    for n in names:
        live = n in tsnap                                        # there while its session is up
        draw_tile(screen,sx0+16,ty,tw,th,n,tsnap.get(n),live)
        ty+=th+12
        if ty>H-th: break

    pygame.display.flip()
    if os.environ.get("KART_TEST_SHOT") and time.time()-T0 > 6 and not getattr(sys.modules[__name__], "_shot", False):   # test only
        pygame.image.save(screen, os.environ["KART_TEST_SHOT"]); _shot = True

running=False
pygame.quit()
# The process ends here, and with it the sessions: the region writes my karts and my tile {} for everyone. (They are not
# closed by hand: a follower may be inside a held read on its session, and the session must outlive that call.)
