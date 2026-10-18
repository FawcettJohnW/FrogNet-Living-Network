#!/usr/bin/env python3
# kartdemo.py -- a playable top-down kart racer whose multiplayer is Ribbit shared memory,
# AND which simultaneously maps a SECOND, independent region (the Communicator) for a video call.
#
# This is the architecture made visible: ONE application, TWO independently-governed memories.
#   * the RACE region  (race/kart/<id>)  -- karts write their pose, read everyone else's
#   * the COMM region  (comm/*)          -- presence + a video-call tile per player
# The two regions know nothing about each other. The app maps both and composes them on screen.
#
#   python kartdemo.py --name red --race-host H:PORT --comm-host H:PORT [--bots N] [--autodrive] [--headless]
#
# Controls: arrow keys / WASD to drive, P toggles the autopilot on your own kart.  Deps: pygame only.
#
# [KART_WRITER_THREAD_V1] Every kart this game owns -- yours and each robot's -- is its own participant: it writes its
# own cell from its own thread, always the newest pose. The frame loop never waits on the network, so the frame rate
# does not fall with the distance to the region (before: one blocking write per frame, 61 fps local, 27 fps at 30 ms
# RTT, 10 fps at 80 ms).
# [KART_AUTODRIVE_V1] --bots N adds N robot karts owned by this game (<name>-bot1..N); --autodrive (or P) puts your own
# kart on the same autopilot. A robot is not special to anyone reading the race region: it is a kart cell like any
# other, written by its owner.
import sys, os, time, math, random, argparse, threading, subprocess

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
def hp(s, d="127.0.0.1", p=8799):
    if ":" in s: h, pp = s.rsplit(":",1); return h, int(pp)
    return s, p

# [KART_REPORT_V1] a region call that fails is said when it starts failing (with why) and when it works again; the
# next write or read happens on its next event, as always.
class Reporter:
    def __init__(self, what): self.what = what; self.bad = None
    def fail(self, e):
        if self.bad != str(e): print(f"kartdemo: {self.what} FAILING: {e}", flush=True); self.bad = str(e)
    def ok(self):
        if self.bad is not None: print(f"kartdemo: {self.what} working again", flush=True); self.bad = None

# [FDF_GATED_V1] with --fdf, FD&F is the source of truth: where the race and the Communicator are (fdf.where.*,
# published by the droplet's fdf-services.sh), and who is here (FD&F's participants: machines whose FD&F client is up).
FDF_API = "/FawcettInnovations.RINA.ram_interface.php"
FDF = hp(a.fdf, p=8840) if a.fdf else None
MACHINE = os.environ.get("COMPUTERNAME") or __import__("socket").gethostname()   # the name this machine's FD&F client announces
WHERE = {}
if FDF:
    try:
        for c in Memory(FDF[0], FDF[1], api=FDF_API).read("fdf", "where"): WHERE[c.get("instance")] = c.get("bag", {})
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
print(f"kartdemo: {NAME} on {MACHINE}: race region {RACE_H}:{RACE_P}, comm region {COMM_H}:{COMM_P}, "
      f"{a.bots} robot kart(s), autopilot {'on' if a.autodrive else 'off'}", flush=True)

# TWO independent regions, mapped at once. This is the whole point of the demo.
race = Memory(RACE_H, RACE_P)            # the race world state
comm = Memory(COMM_H, COMM_P)            # the communicator: presence + video-call tiles

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
running=True
VMAX=240.0; ACC=260.0; TURN=2.8; FRICT=0.92
class Owned:
    """A kart this game drives and is the only writer of: its truth is race.kart.<name>. [KART_WRITER_THREAD_V1]"""
    def __init__(self, name, color, robot, t0, lane, skill=1.0):
        self.name=name; self.color=color; self.robot=robot; self.skill=skill
        sx,sy=track_point(t0); nx,ny=math.cos(t0+math.pi/2),math.sin(t0+math.pi/2)
        self.k=dict(x=sx+nx*lane, y=sy+ny*lane, h=t0+math.pi/2, v=0.0, lap=0)
        self.last_t=nearest_t(self.k["x"],self.k["y"]); self.unwrapped=0.0
        self.lane=lane; self.dodge=0.0; self.wander=random.uniform(0,6.28); self.wander_rate=random.uniform(0.3,0.7)
        self.pub=None; self.ev=threading.Event(); self.rep=Reporter(f"race write {name}")
        threading.Thread(target=self.writer, daemon=True).start()
    def publish(self):                     # the frame loop: hand the newest pose to the writer, never wait
        k=self.k
        self.pub={"x":k["x"],"y":k["y"],"h":k["h"],"v":k["v"],"lap":k["lap"],"name":self.name,"t":time.time(),
                  "machine":MACHINE,"color":list(self.color),"robot":self.robot,"owner":NAME}
        self.ev.set()
    def writer(self):                      # this kart's own participant: one write in flight, always the newest pose
        while running:
            self.ev.wait(); self.ev.clear()
            try: race.write("race","kart",self.name,self.pub); self.rep.ok()
            except RibbitError as e: self.rep.fail(e)

def drive(o, steer, gas, dt):
    k=o.k
    k["v"]+=gas*ACC*dt
    if gas==0: k["v"]*=FRICT
    k["v"]=max(-VMAX*0.4,min(VMAX,k["v"]))
    k["h"]+=steer*TURN*dt*(k["v"]/VMAX if abs(k["v"])>5 else 0)
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

# ----------------------------------------------------------------------------- comm region: a video tile
# Each player publishes a "video" tile to the comm region: presence + a synthetic colour pattern
# (placeholder for real webcam frames, which are the documented next step). Latest-value, droppable.
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
    except OSError as e: print(f"kartdemo: cannot start the Communicator ({a.comms_app}): {e}", flush=True)

def publish_tile():
    st = ("calling " + calling) if (calling and comms_running()) else ("in a call" if comms_running() else "on call")
    comm.write("comm","tile",NAME, {"name":NAME,"color":list(col(NAME)),
               "t":time.time(),"status":st,"frame":int(time.time()*8)%1000,"calling":calling if comms_running() else "","call_id":call_id if comms_running() else "","machine":MACHINE})
def tile_writer():                         # [KART_WRITER_THREAD_V1] the tile is written off the frame loop too
    rep=Reporter("comm tile write")
    while running:
        try: publish_tile(); rep.ok()
        except RibbitError as e: rep.fail(e)
        time.sleep(0.15)

# ----------------------------------------------------------------------------- background readers
# Reading both regions off the render thread keeps the frame rate smooth. Each reader is just HTTP.
state_lock=threading.Lock()
others={}        # name -> (kart pose, local time it was read)  (race region)
tiles={}         # name -> comm tile  (comm region)
def race_reader():
    rep=Reporter("race read")
    while running:
        try: rows=race.read("race","kart","", fresh_s=4); rep.ok()
        except RibbitError as e: rep.fail(e); time.sleep(0.5); continue
        now=time.time(); d={}
        for c in rows:
            b=c.get("bag",{}); inst=c.get("instance","")
            if inst and inst not in MINE and member(b.get("machine","")):   # [FDF_GATED_V1]; my own karts I draw from my own state
                d[inst]=(b,now)
        with state_lock: others.clear(); others.update(d)
        time.sleep(0.04)
def comm_reader():
    rep=Reporter("comm read")
    while running:
        try: rows=comm.read("comm","tile","", fresh_s=6); rep.ok()
        except RibbitError as e: rep.fail(e); time.sleep(0.5); continue
        d={}
        for c in rows:
            b=c.get("bag",{}); inst=c.get("instance","")
            if inst and (inst==NAME or member(b.get("machine",""))): d[inst]=b   # [FDF_GATED_V1]
        with state_lock: tiles.clear(); tiles.update(d)
        time.sleep(0.2)
fdf_alive=set()          # [FDF_GATED_V1] FD&F machine names alive now (only with --fdf)
fdf_seen=False           # FD&F has answered at least once
def fdf_reader():
    global fdf_alive, fdf_seen
    import urllib.request, json as _j
    url=f"http://{FDF[0]}:{FDF[1]}{FDF_API}?op=rina"
    rep=Reporter(f"FD&F participants read ({FDF[0]}:{FDF[1]})")
    while running:
        try:
            req=urllib.request.Request(url, data=b'{"op":"participants"}', headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r: ps=_j.loads(r.read()).get("participants",[])
            with state_lock: fdf_alive={p.get("name","") for p in ps if p.get("alive")}; fdf_seen=True
            rep.ok()
        except Exception as e: rep.fail(f"{type(e).__name__}: {e}")
        ensure_fdf_member()
        time.sleep(1.0)
def member(machine): return (not FDF) or (machine in fdf_alive)
# [FDF_AUTOCLIENT_V1] this machine is not an FD&F member: start the FD&F client (it IS the machine's membership, so it
# keeps running after the game). Once per game: if it is already running for another game, the machine is a member.
fdf_client_proc=None
fdf_client_t=0.0
def fdf_client_path():
    here=os.path.dirname(os.path.abspath(__file__)); exe="fdf-client.exe" if os.name=="nt" else "fdf-client"
    for c in [a.fdf_client, os.path.join(here,exe), "/opt/fdf/bin/fdf-client"]:
        if c and os.path.exists(c): return c
    return exe                                                   # on the PATH, or said when it cannot start
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
        print(f"kartdemo: {MACHINE} was not an FD&F member: started {cmd[0]} {cmd[1]}", flush=True)
    except OSError as e:
        fdf_client_proc=False; print(f"kartdemo: {MACHINE} is not an FD&F member, and the FD&F client could not start ({cmd[0]}): {e}", flush=True)
if FDF: threading.Thread(target=fdf_reader,daemon=True).start()
threading.Thread(target=race_reader,daemon=True).start()
threading.Thread(target=comm_reader,daemon=True).start()
threading.Thread(target=tile_writer,daemon=True).start()

# ----------------------------------------------------------------------------- comm tile rendering
def draw_tile(surf, x, y, w, h, name, bag, live):
    pygame.draw.rect(surf,(16,17,20),(x,y,w,h),border_radius=10)
    # synthetic "video": moving bands in the player's colour (stand-in for a webcam frame)
    c=col(name); fr=(bag or {}).get("frame",0)
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
T0=time.time(); fps_t=time.time(); fps_n=0; fps=0.0
while running:
    dt=clock.tick(60)/1000.0
    for e in pygame.event.get():
        if e.type==pygame.QUIT: running=False
        if e.type==pygame.KEYDOWN and e.key==pygame.K_p: autodrive=not autodrive      # [KART_AUTODRIVE_V1]
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:      # [KART_CALLS_V1] Call: open the Communicator to them
            for who,r in list(call_buttons.items()):
                if r.collidepoint(e.pos): launch_comms(who); break
    with state_lock:
        snap=dict(others); tsnap=dict(tiles)
    now=time.time()
    # where everyone else is drawn: their last write, carried forward along their heading for the time since it was read
    # (rendering between reads, not reconciliation: the owner's cell is the only truth about its kart)
    drawn={}
    for n,(b,rt) in snap.items():
        age=min(0.3,now-rt); v=b.get("v",0); h=b.get("h",0)
        drawn[n]=(b.get("x",0)+math.cos(h)*v*age, b.get("y",0)+math.sin(h)*v*age, h, b.get("color"))

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

    # WRITE: each kart I own hands its newest pose to its own writer (latest-value, droppable)
    for o in [me]+bots: o.publish()

    # ---- draw ----
    screen.blit(TRACK,(0,0))
    for (x,y) in trail: pygame.draw.circle(screen,(30,30,34),(int(x),int(y)),3)
    if os.environ.get("KART_TEST_CALL") in tsnap and not comms_running():   # test only: press Call (again once a window ends)
        launch_comms(os.environ["KART_TEST_CALL"])
    call_buttons.clear()                                       # [KART_CALLS_V1]
    for who,bag in tsnap.items():                              # someone is calling ME: open my Communicator to answer
        calling_me = who != NAME and (bag or {}).get("calling") == NAME and time.time()-(bag or {}).get("t",0) < 6
        cid = (bag or {}).get("call_id", "")
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
        fps=fps_n/(now-fps_t); fps_n=0; fps_t=now
        if os.environ.get("KART_TEST_FPS"): print(f"[KART_TEST_FPS] {NAME} {fps:.1f} fps, {len(snap)} other kart(s) seen", flush=True)   # test only
    screen.blit(BIG.render(f"Lap {min(me.k['lap'],a.laps)}/{a.laps}",True,(255,255,255)),(16,14))
    screen.blit(FONT.render(f"{int(abs(me.k['v']))} px/s" + ("   AUTOPILOT (P)" if autodrive else ""),True,(230,230,230)),(16,52))
    n_race=len(snap)+1+len(bots); n_comm=len(tsnap)
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
        live = n==NAME or (time.time()-(tsnap.get(n,{}).get("t",0))<6)
        draw_tile(screen,sx0+16,ty,tw,th,n,tsnap.get(n),live)
        ty+=th+12
        if ty>H-th: break

    pygame.display.flip()
    if os.environ.get("KART_TEST_SHOT") and time.time()-T0 > 6 and not getattr(sys.modules[__name__], "_shot", False):   # test only
        pygame.image.save(screen, os.environ["KART_TEST_SHOT"]); _shot = True

running=False
time.sleep(0.2)
pygame.quit()
