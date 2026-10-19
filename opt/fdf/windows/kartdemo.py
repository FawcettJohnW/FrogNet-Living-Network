#!/usr/bin/env python3
# kartdemo.py -- a player of shared-memory-managed games (DESIGN.md is the truth table).  [KART_LOBBY_V1]
# Every game connects first to the kart memory, where the kart service is (kart_service.py: the lobby's
# intelligence). The game writes its own truths there -- here, ready, the laps it wants, its chat lines -- and reads
# the service's: the agreed parameters, the lobby's state, and which machine serves each game. When the service has
# launched a race with this player in it and the race server announces itself, the game maps that race's memory and
# races; afterwards it is back in the lobby. If this machine can serve games, the game runs a host agent for it
# (kart_host.py --kind player), so the service can choose it when no dedicated machine is up.
#
#   python kartdemo.py --name red --fdf H:8840     FD&F says where the kart memory is (fdf.where "race"), and who is a member
#   python kartdemo.py --name red --kart H:8799    the kart memory is there (the kart service must be beside it)
#   python kartdemo.py --name red --solo           this machine alone: the game starts the kart memory and the kart service
#   [--bots N] [--laps N] [--autodrive] [--headless] [--min-players N (solo)]
#
# In the lobby: type and Enter to chat; Ready; Start (launches the ready players now); - / + the laps you want.
# Racing: arrows / WASD; P toggles the autopilot. Deps: pygame, and libkartram (kartram.dll on Windows) beside ribbit.py.
#
# [KART_ON_THE_SESSION_V1] one permanent FNW1 session per memory and service; writes are posted, never waited for.
# [KART_HELD_READS_V1] each service is read once, then held: the memory answers when something is written to it.
# [KART_WRITES_ON_CHANGE_V1] a kart is written when it departs from where readers draw it; everything else when it changes.
# [OWNED_CELL_GOES_WITH_THE_SESSION_V1] what a game writes goes with its session: gone is gone, no timeouts.
# [KART_AUTODRIVE_V1] --bots N: robot karts this game owns and drives; --autodrive (or P): your own kart too.
import sys, os, time, math, random, argparse, threading, subprocess, json, socket, signal

ap = argparse.ArgumentParser()
ap.add_argument("--name", default=None, help="your player id")
ap.add_argument("--fdf", default=None, help="FD&F RAM server host:port: where the kart memory is, and who is a member")
ap.add_argument("--fdf-client", default=None, help="the FD&F client program (default: next to this script, or /opt/fdf/bin)")
ap.add_argument("--kart", default=None, help="the kart memory, host:port (the kart service is beside it)")
ap.add_argument("--solo", action="store_true", help="start the kart memory and the kart service here (~/.frognet-kart keeps the memory)")
ap.add_argument("--min-players", type=int, default=1, help="--solo: the kart service launches by itself at this many ready players")
ap.add_argument("--laps", type=int, default=3, help="the laps you want (the service agrees the laps the most players want)")
ap.add_argument("--bots", type=int, default=0, help="robot karts this game owns and drives")
ap.add_argument("--autodrive", action="store_true", help="your own kart starts on the autopilot (P toggles it)")
ap.add_argument("--headless", action="store_true", help="no window, no sound")
ap.add_argument("--window", default=None, help="the window's size, WxH (e.g. 590x360): the game is drawn at 1180x720 and scaled into it; an X display over the network sends far fewer pixels a frame")
ap.add_argument("--rina-host", default=None, help="what a race server on this machine runs (default: beside this file, or /opt/fdf/bin)")
ap.add_argument("--comms-ram", default=None); ap.add_argument("--comms-media", default=None)      # [KART_CALLS_V1]
ap.add_argument("--comms-app", default="comms-app.exe" if os.name == "nt" else "comms-app")
ap.add_argument("--comms-args", default="")
a = ap.parse_args()
if a.headless: os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
if sum(bool(x) for x in (a.fdf, a.kart, a.solo)) != 1: sys.exit("kartdemo: say where the kart memory is: --fdf H:P, --kart H:P or --solo")
if os.name == "nt":                                       # [KART_WINDOW_V1] Windows: the window is the size asked, in real pixels.
    import ctypes                                          # Without this, display scaling (125%, 150%) stretches the window:
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)    # 590x360 would show as 885x540. Per-monitor DPI aware (Windows 8.1+).
    except (AttributeError, OSError) as _e: print(f"kartdemo: could not make the window DPI-aware ({_e}): Windows display scaling will stretch it", flush=True)
import pygame
from ribbit import Memory, RibbitError
from kartmem import Region, say as _say, hp
import ribbit as _rb                                       # [KART_SAYS_THE_ARCH_V1] the library, loaded once, here: a wrong one stops the game and says why
try: _rb._lib = _rb._load()
except (RibbitError, OSError) as _e: sys.exit(f"kartdemo: {_e}")

NAME = a.name or random.choice(["red","blue","green","yellow","pink","cyan"])
HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = os.environ.get("COMPUTERNAME") or socket.gethostname()
def say(m): _say("kartdemo", m)
def tool(base):
    exe = base + (".exe" if os.name == "nt" else "")
    for c in [os.path.join(HERE, exe), os.path.join("/opt/fdf/bin", exe)]:
        if os.path.exists(c): return c
    return None
# --rina-host, when given, is the one: if it is not there this machine does not host (said), nothing else is looked for
RINA_HOST = (a.rina_host if os.path.exists(a.rina_host) else None) if a.rina_host else tool("rina-host")
if RINA_HOST and os.name != "nt" and _rb.elf_arch(RINA_HOST) not in (None, _rb.this_arch()):   # [KART_SAYS_THE_ARCH_V1]
    print(f"kartdemo: {RINA_HOST} is built for {_rb.elf_arch(RINA_HOST)}; this machine is {_rb.this_arch()}: this machine does not serve games", flush=True)
    RINA_HOST = None
CAN_HOST = os.name != "nt" and RINA_HOST is not None
children = []                                            # what this game started; they end with it

# ----------------------------------------------------------------------------- where the kart memory is
FDF_API = "/FawcettInnovations.RINA.ram_interface.php"; WHERE = {}
FDF_H, FDF_P = hp(a.fdf, 8840) if a.fdf else (None, None)
if a.fdf:
    try:
        f = Memory(FDF_H, FDF_P, api=FDF_API)
        for c in f.read("fdf", "where"): WHERE[c["instance"]] = c["bag"]
        f.close()
    except RibbitError as e: sys.exit(f"kartdemo: cannot read FD&F: {e}")
    if "race" not in WHERE: sys.exit(f"kartdemo: FD&F at {a.fdf} does not say where the kart memory is (fdf.where race: run fdf-services.sh start on the droplet)")
    KART_H, KART_P = WHERE["race"]["host"], int(WHERE["race"]["port"])
elif a.kart: KART_H, KART_P = hp(a.kart)
else:                                                    # --solo: the kart memory and the kart service, here
    if not RINA_HOST: sys.exit("kartdemo: --solo needs rina-host beside kartdemo.py or in /opt/fdf/bin")
    keep = os.path.join(os.path.expanduser("~"), ".frognet-kart"); os.makedirs(keep, exist_ok=True)
    env = dict(os.environ, RINA_REGION_FILE=os.path.join(keep, "main.perm")); env.pop("RINA_MAP_AREA", None)
    import ctypes; _libc = ctypes.CDLL("libc.so.6")
    def with_the_game(): _libc.prctl(1, signal.SIGTERM)    # PR_SET_PDEATHSIG: the solo memory and service end with this game, even killed -9
    solo = subprocess.Popen([RINA_HOST, "--listen", "127.0.0.1:0", "--api", "/ram.php"], env=env, preexec_fn=with_the_game,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True); children.append(solo)
    KART_P = None
    for line in solo.stderr:
        if "[FDF_VERSION_V1]" in line and "listening on" in line: KART_P = int(line.rsplit(":", 1)[1])
        if "[PERMANENT_CELL_V1]" in line: say(line.strip())
        if KART_P and "[PERMANENT_CELL_V1]" in line: break
    if not KART_P: sys.exit("kartdemo: the solo kart memory did not start")
    threading.Thread(target=lambda: [None for _ in solo.stderr], daemon=True).start()
    KART_H = "127.0.0.1"
    children.append(subprocess.Popen([sys.executable, os.path.join(HERE, "kart_service.py"), "--memory", f"127.0.0.1:{KART_P}",
                                      "--min-players", str(a.min_players)], preexec_fn=with_the_game))
    say(f"solo: the kart memory on this machine, 127.0.0.1:{KART_P}, kept in {keep}/main.perm, and the kart service beside it")
KART_API = "/ram.php"
COMMS_RAM = a.comms_ram or (WHERE.get("comms", {}).get("ram")) or f"{KART_H}:8800"     # [KART_CALLS_V1]
COMMS_MEDIA = a.comms_media or (WHERE.get("comms", {}).get("media")) or f"{KART_H}:8994"
say(f"{NAME} on {MACHINE}: kart memory {KART_H}:{KART_P}; {a.bots} robot(s); "
    f"{'this machine can serve games (' + RINA_HOST + ')' if CAN_HOST else ('this machine does not serve games: ' + (a.rina_host + ' is not there' if a.rina_host else 'no rina-host here'))}")

running = True
main = Region("kart memory", KART_H, KART_P, KART_API, "kart", say)     # lobby, chat, the service's truths, servers, records
comm = Region("comm", KART_H, KART_P, KART_API, "comm", say)            # call tiles
race = None                                                            # the race memory, while racing
if CAN_HOST:                                                           # [KART_HOST_AGENT_V1] this machine, for the service to choose
    cmd = [sys.executable, os.path.join(HERE, "kart_host.py"), "--memory", f"{KART_H}:{KART_P}", "--kind", "player",
           "--machine", MACHINE, "--player", NAME, "--rina-host", RINA_HOST]
    cmd += ["--advertise", "127.0.0.1"] if a.solo else (["--fdf", f"{FDF_H}:{FDF_P}"] if a.fdf else [])
    children.append(subprocess.Popen(cmd))
# ----------------------------------------------------------------------------- look
pygame.init()
W, H = 1180, 720; TRACK_W = 860
# [KART_WINDOW_V1] the game is laid out at W x H; --window shows it scaled into a smaller (or larger) window
if a.window:
    try: WIN_W, WIN_H = (int(v) for v in a.window.lower().split("x"))
    except ValueError: sys.exit(f"kartdemo: --window {a.window}: give it as WxH, e.g. 590x360")
else: WIN_W, WIN_H = W, H
if (WIN_W, WIN_H) == (W, H):
    screen = pygame.display.set_mode((W, H))
    def show(): pygame.display.flip()
else:
    _window = pygame.display.set_mode((WIN_W, WIN_H)); screen = pygame.Surface((W, H))
    def show(): pygame.transform.smoothscale(screen, (WIN_W, WIN_H), _window); pygame.display.flip()
if not a.headless: print(f"kartdemo: window {pygame.display.get_window_size()[0]}x{pygame.display.get_window_size()[1]} (asked {WIN_W}x{WIN_H}; the game is laid out at {W}x{H})", flush=True)
def layout_pos(p): return (p[0] * W // WIN_W, p[1] * H // WIN_H)     # a click in the window, in the game's layout
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


# ----------------------------------------------------------------------------- the lobby: my truths
ready=False; start=False; my_laps=max(1,a.laps); lobby_written=None; lobby_gen=-1; chat_n=0
def publish_lobby():
    """[KART_WRITES_ON_CHANGE_V1] here, ready, Start pressed, the laps I want -- written when they change"""
    global lobby_written, lobby_gen
    if not member(MACHINE): return                       # [FDF_GATED_V1] not an FD&F member: not in the lobby
    bag={"name":NAME,"machine":MACHINE,"bots":len(bots),"ready":ready,"start":start,"laps":my_laps}
    if bag == lobby_written and lobby_gen == main.gen: return
    gen=main.gen
    if main.write("lobby",NAME,bag): lobby_written=bag; lobby_gen=gen
def send_chat(text):
    global chat_n
    chat_n+=1; main.write("chat", f"{NAME}#{chat_n % 40}", {"who":NAME,"text":text,"at":time.time()}, own=False)
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
            f=Memory(FDF_H, FDF_P, api=FDF_API)
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
        fdf_client_proc=subprocess.Popen([exe,f"{FDF_H}:{FDF_P}"],creationflags=flags) if os.name=="nt" else subprocess.Popen([exe,f"{FDF_H}:{FDF_P}"],start_new_session=True)
        say(f"{MACHINE} was not an FD&F member: started {exe} {FDF_H}:{FDF_P}")
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
ready_btn=pygame.Rect(40, H-70, 130, 44); start_btn=pygame.Rect(180, H-70, 130, 44)
minus_btn=pygame.Rect(330, H-70, 36, 44); plus_btn=pygame.Rect(440, H-70, 36, 44)
chat_text=""; last_result=None
def button(r, label, on=True, lit=False):
    pygame.draw.rect(screen,(36,140,72) if lit else ((60,100,160) if on else (70,70,75)),r,border_radius=8)
    text(label, r.x+(r.w-FONT.size(label)[0])//2, r.y+12, FONT)
def draw_lobby(lob, servers, recs, agreed, chats):
    screen.fill((30,34,40)); text("Lobby", 40, 20, BIG)
    text(f"kart memory {KART_H}:{KART_P}   -   {agreed.get('state','the kart service has not spoken yet') if agreed else 'the kart service has not spoken yet'}", 40, 58, SMALL, (240,200,120))
    if agreed: text(f"agreed: {agreed.get('laps')} lap(s)    game-serving machines: " +
                    (", ".join(f"{m} ({k})" for m, k in sorted(agreed.get('hosts',{}).items())) or "none"), 40, 76, SMALL, (180,200,220))
    y=104; text("players", 40, y, FONT, (200,220,255)); y+=26
    busy={p for b in servers.values() for p in b.get("players", [])}
    for n in sorted(lob):
        b=lob[n]; st="racing" if n in busy else ("ready" if b.get("ready") else "not ready")
        text(f"{n:12s} {b.get('machine','')[:14]:14s} robots {b.get('bots',0)}  wants {b.get('laps',3)} lap(s)  {st}", 40, y, SMALL); y+=19
    y+=8; text("races running", 40, y, FONT, (200,220,255)); y+=26
    for r, b in sorted(servers.items()): text(f"{r} on {b.get('host')} ({b.get('addr')}:{b.get('port')})", 40, y, SMALL); y+=19
    if not servers: text("none", 40, y, SMALL, (150,150,155))
    x2=470; y2=104; text("chat with everyone in the lobby", x2, y2, FONT, (200,220,255)); y2+=28
    # [KART_CHAT_BOX_V1] the box you type into is at the top, lit, and says what to do while it is empty
    box=pygame.Rect(x2, y2, TRACK_W-x2-20, 30); pygame.draw.rect(screen,(14,16,20),box,border_radius=6)
    pygame.draw.rect(screen,(110,170,255),box,width=2,border_radius=6)
    cursor="|" if int(time.time()*2)%2 else " "
    if chat_text: text(chat_text[-50:]+cursor, x2+10, y2+7, SMALL)
    else: text(cursor+" type here, Enter sends it to everyone", x2+10, y2+7, SMALL, (140,150,170))
    y2+=40
    for c in chats[-12:]: text(f"{c.get('who','?')}: {c.get('text','')}"[:52], x2, y2, SMALL, (200,200,170) if c.get('who')=='kart service' else (230,230,235)); y2+=19
    y2+=10
    text("records (permanent)", x2, y2, FONT, (200,220,255)); y2+=24
    for n, r in sorted(recs.items(), key=lambda kv: (-kv[1].get("wins",0), kv[1].get("best_lap") or 1e9))[:6]:
        text(f"{n:12s} races {r.get('races',0):3d}  wins {r.get('wins',0):3d}  best lap {r.get('best_lap') or '-'}", x2, y2, SMALL); y2+=19
    if last_result:
        text(f"last race {last_result.get('race')}: " + ", ".join(s['kart'] for s in last_result.get('order',[])[:4]), x2, y2+6, SMALL, (170,170,180))
    button(ready_btn, "Ready" if not ready else "Ready ✓", lit=ready); button(start_btn, "Start", on=ready)
    button(minus_btn, "-"); button(plus_btn, "+"); text(f"{my_laps} lap(s)", 372, H-58, SMALL)

# ----------------------------------------------------------------------------- the main loop
state="lobby"; my_race=None; placed=False; start_at=0; trail=[]
fps_t=time.time(); fps_n=0; fps=0.0; w_n0=0; frame_said=0.0; T0=time.time(); test_chat_sent=False
while running:
    dt=clock.tick(60)/1000.0; now=time.time()
    for e in pygame.event.get():
        if e.type==pygame.QUIT: running=False
        if e.type==pygame.KEYDOWN and e.key==pygame.K_p and state=="racing": autodrive=not autodrive
        if e.type==pygame.KEYDOWN and state=="lobby":
            if e.key==pygame.K_RETURN:
                if chat_text.strip(): send_chat(chat_text.strip())
                chat_text=""
            elif e.key==pygame.K_BACKSPACE: chat_text=chat_text[:-1]
            elif e.unicode and e.unicode.isprintable() and len(chat_text)<120: chat_text+=e.unicode
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
            pos=layout_pos(e.pos)
            if state=="lobby":
                if ready_btn.collidepoint(pos): ready=not ready; start=start and ready
                if start_btn.collidepoint(pos) and ready: start=True
                if minus_btn.collidepoint(pos): my_laps=max(1,my_laps-1)
                if plus_btn.collidepoint(pos): my_laps=min(20,my_laps+1)
            for who,r in list(call_buttons.items()):
                if r.collidepoint(pos): launch_comms(who); break
    if state=="lobby":                                       # test only: what a player's hands would do
        if os.environ.get("KART_TEST_READY_AFTER") and now-T0 > float(os.environ["KART_TEST_READY_AFTER"]) and not ready and not my_race: ready=True
        if os.environ.get("KART_TEST_START_AFTER") and now-T0 > float(os.environ["KART_TEST_START_AFTER"]) and ready: start=True
        if os.environ.get("KART_TEST_CHAT") and now-T0 > 2 and not test_chat_sent: send_chat(os.environ["KART_TEST_CHAT"]); test_chat_sent=True
    lob={n: b for n, b in main.family("lobby").items() if member(b.get("machine",""))}
    servers=main.family("server"); tiles={n: b for n, b in comm.family("tile").items() if n==NAME or member(b.get("machine",""))}
    for who,b in tiles.items():                                 # someone is calling ME
        if who!=NAME and b.get("calling")==NAME and answered.get(who)!=b.get("call_id") and member(MACHINE):
            answered[who]=b.get("call_id"); launch_comms("")
    if state=="lobby":
        mine=[r for r, b in servers.items() if NAME in b.get("players",[])]
        if mine:                                                # the service launched my race and its server is up
            my_race=min(mine); b=servers[my_race]
            race=Region("race memory", b["addr"], int(b["port"]), b.get("api","/ram.php"), "race", say,
                        ends=lambda v: ("meta","info") not in v)   # the race memory says the race is over: meta {}
            state="racing"; placed=False; start_at=float(b.get("start_at", now)); trail=[]
            ready=False; start=False; publish_lobby()             # ready means ready for the NEXT race: spent on joining this one
            say(f"race {my_race} is up on {b['host']} at {b['addr']}:{b['port']}: racing")
        publish_lobby(); publish_tile()
        if state=="lobby":
            res=main.family("result")
            if res and (not last_result or max(res)!=last_result.get("race")): last_result=res[max(res)]
            chats=sorted(main.family("chat").values(), key=lambda c: c.get("at",0))
            draw_lobby(lob, servers, main.family("record"), main.family("agreed").get("lobby"), chats); draw_strip(tiles); show()
    if state=="racing":
        b=servers.get(my_race)
        if not b:                                               # the race server wrote its tuple {} or is gone
            race.stop(); race=None; state="lobby"; res=main.family("result")
            if my_race in res: last_result=res[my_race]
            say(f"race {my_race} is over ({last_result.get('why') if last_result and last_result.get('race')==my_race else 'its server is gone, no result'}): back in the lobby")
            my_race=None; publish_lobby(); continue
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
        draw_strip(tiles); show()
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
        elif state=="racing" and now-frame_said>=5.0:          # [KART_FRAME_V1] said in every race, every 5 s, without a test switch
            frame_said=now; n_seen=len([k for k in race.family("kart") if k not in MINE]) if race else 0
            say(f"[KART_FRAME_V1] {NAME} {fps:.1f} fps, {n_seen} other kart(s) seen" + ("" if fps>=20 else
                f" -- the frame loop is slow: drawing this {WIN_W}x{WIN_H} window takes {1000/max(fps,0.01):.0f} ms a frame"
                + (f" (DISPLAY={os.environ['DISPLAY']}: an X display over the network; a smaller --window sends fewer pixels)" if ":" in os.environ.get("DISPLAY","") and not os.environ.get("DISPLAY","").startswith(":") else "")))
        fps_n=0; fps_t=now
pygame.quit()
# The sessions end with the process: the memory writes this game's lobby tuple, tile and karts {} for everyone.
for c in children:                         # the solo kart memory and service, and this machine's host agent, end with the game
    try: c.terminate()
    except OSError: pass
