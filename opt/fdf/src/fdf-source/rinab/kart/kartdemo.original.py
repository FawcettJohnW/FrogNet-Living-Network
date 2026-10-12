#!/usr/bin/env python3
# kartdemo.py -- a playable top-down kart racer whose multiplayer is Ribbit shared memory,
# AND which simultaneously maps a SECOND, independent region (the Communicator) for a video call.
#
# This is the architecture made visible: ONE application, TWO independently-governed memories.
#   * the RACE region  (race/kart/<id>)  -- karts write their pose, read everyone else's
#   * the COMM region  (comm/*)          -- presence + a video-call tile per player
# The two regions know nothing about each other. The app maps both and composes them on screen.
#
#   python kartdemo.py --name red --race-host H:PORT --comm-host H:PORT
#
# Controls: arrow keys / WASD to drive.  Deps: pygame only.
import sys, os, time, math, random, argparse, threading
import pygame
from ribbit import Memory

# ----------------------------------------------------------------------------- args
ap = argparse.ArgumentParser()
ap.add_argument("--name", default=None, help="your kart/player id")
ap.add_argument("--race-host", default="127.0.0.1:8799", help="race region host:port")
ap.add_argument("--comm-host", default=None, help="communicator region host:port (default: same as race)")
ap.add_argument("--laps", type=int, default=3)
a = ap.parse_args()
NAME = a.name or random.choice(["red","blue","green","yellow","pink","cyan"])
def hp(s, d="127.0.0.1", p=8799):
    if ":" in s: h, pp = s.rsplit(":",1); return h, int(pp)
    return s, p
RACE_H, RACE_P = hp(a.race_host)
COMM_H, COMM_P = hp(a.comm_host) if a.comm_host else (RACE_H, RACE_P)

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
SPRITES={c:kart_sprite(col(c)) for c in COLORS}
def sprite(n): return SPRITES.get(n) or kart_sprite(col(n))

# ----------------------------------------------------------------------------- my state
# start near the line, nudged by name so a field doesn't overlap
seed=sum(ord(c) for c in NAME)
lane=((seed%5)-2)*18
t0=-math.pi/2
sx,sy=track_point(t0)
nx,ny=math.cos(t0+math.pi/2),math.sin(t0+math.pi/2)
me=dict(x=sx+nx*lane, y=sy+ny*lane, h=0.0, v=0.0, lap=0)
VMAX=240.0; ACC=260.0; TURN=2.8; FRICT=0.92
last_t=t0; unwrapped=0.0; trail=[]

# ----------------------------------------------------------------------------- comm region: a video tile
# Each player publishes a "video" tile to the comm region: presence + a synthetic colour pattern
# (placeholder for real webcam frames, which are the documented next step). Latest-value, droppable.
def publish_tile():
    comm.write("comm","tile",NAME, {"name":NAME,"color":list(col(NAME)),
               "t":time.time(),"status":"on call","frame":int(time.time()*8)%1000})

# ----------------------------------------------------------------------------- background readers
# Reading both regions off the render thread keeps the frame rate smooth. Each reader is just HTTP.
state_lock=threading.Lock()
others={}        # name -> kart pose  (race region)
tiles={}         # name -> comm tile  (comm region)
running=True
def race_reader():
    while running:
        rows=race.read("race","kart","", fresh_s=4)
        d={}
        for c in rows:
            b=c.get("bag",{}); inst=c.get("instance","")
            if inst and inst!=NAME:
                d[inst]=b
        with state_lock: others.clear(); others.update(d)
        time.sleep(0.04)
def comm_reader():
    while running:
        rows=comm.read("comm","tile","", fresh_s=6)
        d={}
        for c in rows:
            b=c.get("bag",{}); inst=c.get("instance","")
            if inst: d[inst]=b
        with state_lock: tiles.clear(); tiles.update(d)
        time.sleep(0.2)
threading.Thread(target=race_reader,daemon=True).start()
threading.Thread(target=comm_reader,daemon=True).start()

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

# ----------------------------------------------------------------------------- main loop
pub_t=0; last=time.time()
while running:
    dt=clock.tick(60)/1000.0
    for e in pygame.event.get():
        if e.type==pygame.QUIT: running=False
    k=pygame.key.get_pressed()
    steer=(1 if (k[pygame.K_RIGHT] or k[pygame.K_d]) else 0)-(1 if (k[pygame.K_LEFT] or k[pygame.K_a]) else 0)
    gas=(1 if (k[pygame.K_UP] or k[pygame.K_w]) else 0)-(1 if (k[pygame.K_DOWN] or k[pygame.K_s]) else 0)

    # physics
    me["v"]+=gas*ACC*dt
    if gas==0: me["v"]*=FRICT
    me["v"]=max(-VMAX*0.4,min(VMAX,me["v"]))
    me["h"]+=steer*TURN*dt*(me["v"]/VMAX if abs(me["v"])>5 else 0)
    me["x"]+=math.cos(me["h"])*me["v"]*dt
    me["y"]+=math.sin(me["h"])*me["v"]*dt
    # keep roughly on the island
    me["x"]=max(10,min(TRACK_W-10,me["x"])); me["y"]=max(10,min(H-10,me["y"]))
    # laps
    tt=nearest_t(me["x"],me["y"]); d=math.remainder(tt-last_t,2*math.pi); unwrapped+=d; last_t=tt
    if unwrapped>=2*math.pi: unwrapped-=2*math.pi; me["lap"]+=1
    # drift trail
    if abs(me["v"])>120: trail.append((me["x"],me["y"]))
    if len(trail)>60: trail.pop(0)

    # WRITE my pose to the RACE region (latest-value, droppable)
    race.write("race","kart",NAME, {"x":me["x"],"y":me["y"],"h":me["h"],
               "v":me["v"],"lap":me["lap"],"name":NAME,"t":time.time()})
    # publish my COMM tile a few times a second (the SECOND region)
    if time.time()-pub_t>0.15: publish_tile(); pub_t=time.time()

    # ---- draw ----
    screen.blit(TRACK,(0,0))
    for (x,y) in trail: pygame.draw.circle(screen,(30,30,34),(int(x),int(y)),3)
    with state_lock:
        snap=dict(others); tsnap=dict(tiles)
    # other karts (from the race region)
    for n,b in snap.items():
        spr=pygame.transform.rotate(sprite(n),-math.degrees(b.get("h",0)))
        screen.blit(spr,spr.get_rect(center=(b.get("x",0),b.get("y",0))))
        screen.blit(SMALL.render(n,True,(240,240,240)),(b.get("x",0)-10,b.get("y",0)-26))
    # me
    spr=pygame.transform.rotate(sprite(NAME),-math.degrees(me["h"]))
    screen.blit(spr,spr.get_rect(center=(me["x"],me["y"])))

    # HUD
    screen.blit(BIG.render(f"Lap {min(me['lap'],a.laps)}/{a.laps}",True,(255,255,255)),(16,14))
    screen.blit(FONT.render(f"{int(abs(me['v']))} px/s",True,(230,230,230)),(16,52))
    n_race=len(snap)+1; n_comm=len(tsnap)
    screen.blit(SMALL.render(f"race region: {n_race} kart(s)   |   comm region: {n_comm} on call",
               True,(210,210,210)),(16,H-24))

    # ---- the communicator strip (fed by the SECOND region) ----
    sx0=TRACK_W
    pygame.draw.rect(screen,PANEL,(sx0,0,W-TRACK_W,H))
    screen.blit(FONT.render("Communicator",True,(240,240,240)),(sx0+16,12))
    screen.blit(SMALL.render("second Ribbit region (comm/*)",True,(150,150,155)),(sx0+16,34))
    tw=W-TRACK_W-32; th=150; ty=58
    names=sorted(set(list(tsnap.keys()) or [NAME]))
    for n in names:
        live = n==NAME or (time.time()-(tsnap.get(n,{}).get("t",0))<6)
        draw_tile(screen,sx0+16,ty,tw,th,n,tsnap.get(n),live)
        ty+=th+12
        if ty>H-th: break

    pygame.display.flip()

running=False
time.sleep(0.2)
pygame.quit()
