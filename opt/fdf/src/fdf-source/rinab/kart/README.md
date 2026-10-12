# FrogNet Kart -- a Ribbit demo in two regions

A playable top-down kart racer whose **multiplayer is Ribbit shared memory**, and which at the
same time maps a **second, independent region** -- the Communicator -- so players are on a video
call while they race. One application, two independently-governed memories, composed on screen.

- **race region** (`race/kart/<id>`): each kart writes its pose, reads everyone else's. The race.
- **comm region** (`comm/tile/<id>`): each player publishes a video-call tile (presence + a frame).
  The two regions know nothing about each other; the app maps both.

The video tiles are **synthetic placeholders** right now (a moving colour pattern + a face blob) --
enough to show the two-region architecture. Real webcam video + audio is the documented next step
(see "Next" below); it drops into the same `comm` region with no change to the race side.

## Run it (Windows, Mac, Linux -- needs only Python + pygame)

```
pip install pygame
python kartdemo.py --name red   --race-host 10.250.250.100:8799
python kartdemo.py --name blue  --race-host 10.250.250.100:8799   # second machine / window
```

`--comm-host` defaults to the same host as race; point it elsewhere to prove the two regions are
genuinely independent (different servers, even different machines):

```
python kartdemo.py --name red --race-host 10.250.250.100:8799 --comm-host 10.250.250.101:8799
```

Controls: arrow keys or WASD. Each player runs an instance (different `--name`); they see each
other's karts and each other's call tiles, all through the regions. No game server.

## Why this is the demo

- **No C++, no build, no DLLs.** The Ribbit client here is ~45 lines of pure Python over the RAM
  server's plain HTTP `/ram.php` endpoint (write=POST, read=GET). Runs anywhere Python does.
- **The multiplayer IS the memory.** `race.write(... my pose ...)` and `race.read(... everyone ...)`
  are the whole netcode. You can read it.
- **Two regions, one app.** `race = Memory(h1,p1)` and `comm = Memory(h2,p2)` -- the app maps both
  and composes them. That's the application-owned-Internet-memory architecture, playable.

## Files

- `kartdemo.py` -- the game (racer + communicator strip), ~230 lines.
- `ribbit.py`   -- the pure-Python Ribbit client (HTTP /ram.php), stdlib only.

## Server side (already running on the Linux box)

The race region and the comm region are each a `kart-ram` (the Ribbit RAM server):

```
kart-ram --listen 0.0.0.0:8799        # race region
kart-ram --listen 0.0.0.0:8800        # comm region (or reuse 8799 for both -- different services)
```

Both services (`race` and `comm`) can live in ONE region host (they're different service names) or
two -- the demo works either way, which is itself the point about independent governance.

## Next (real AV)

Replace the synthetic tile in `publish_tile()` with a real webcam frame: capture with OpenCV
(`cv2.VideoCapture`), JPEG-encode, base64 into the `comm` tile bag (or, for rate, a fast-plane
reflector). Audio rides the same region as a second variable. The race side does not change --
that's the separability the architecture promises.
