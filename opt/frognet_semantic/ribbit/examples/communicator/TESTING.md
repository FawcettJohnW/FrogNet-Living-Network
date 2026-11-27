# Testing the C++ Communicator

## Build (Linux)

```
sudo apt install cmake g++ pkg-config libavcodec-dev libavformat-dev libavdevice-dev libavutil-dev \
    libswscale-dev libvpx-dev libopus-dev portaudio19-dev qt6-base-dev liblz4-dev libssl-dev
./build.sh
```

Self-contained: the Ribbit platform it compiles (frogram, RamClient, RamHost, the tuple API) is vendored in
`third_party/ribbit`. No FrogNet checkout is needed. `build.sh` installs any missing libraries.

Four programs:

| program | role |
|---|---|
| `comms-ram` | the Communicator's memory: presence, lobby, chat, call offers, MediaSpeed |
| `comms-media` | the media server: every participant's two TCP connections (audio, video); passes the upload through or transcodes it down per viewer |
| `comms-feed` | requirement 1: an unattended feed, no UI |
| `comms-app` | the window: lobby, call, sliders, stats, chat |

## 1. One machine, one command

```
./demo.sh            # memory, media server, the feed "Chapel camera" playing media/stock-720p.mp4, and a window as John
```

In the window: **Chapel camera** is under *Feeds*. Select it, press **Watch**. You should see the moving test pattern at
1280x720, about 24 fps in the overlay (top left), and hear a 440 Hz tone. Your own picture is in the corner.

A second window on the same machine, to call between people:

```
build/comms-app --ram 127.0.0.1:8800 --media 127.0.0.1:8994 --name Donna
```

In John's window select **Donna**, press **Call**; Donna's window shows **Answer John**. Both see each other; chat on
the right.

## 2. Real machines

Pick one machine for the memory and media server (any machine everyone can reach; open TCP 8800 and 8994 to them):

```
build/comms-ram   --listen 0.0.0.0:8800
build/comms-media --listen 0.0.0.0:8994 --ram 127.0.0.1:8800
```

On each participant (`RAM` and `MEDIA` are that machine's address):

```
build/comms-app --ram RAM:8800 --media MEDIA:8994 --name John --camera /dev/video0
build/comms-app --ram RAM:8800 --media MEDIA:8994 --name Donna --video somefile.mp4
```

The unattended feed (requirement 1), on a machine with a camera and no screen:

```
build/comms-feed --ram RAM:8800 --media MEDIA:8994 --name "Chapel camera" --camera /dev/video0 --mic -1
```

Picture sources, in both `comms-app` and `comms-feed`: `--camera /dev/videoN` (Linux; asked for MJPEG), `--video FILE`
(any file FFmpeg reads, looped), or `--synthetic WxH` (a moving shape; the default). `--size WxH` sets the sending
size for a camera or file. Audio: `comms-app --list-devices`, then `--mic N --speaker N`; `--no-audio` to run without.

## 3. What to try

| try | expected |
|---|---|
| **Bandwidth** slider down (this participant's link, BOTH directions) | upload: your ladder steps down by itself (*Sending* tile: L7 1280x720 -> ... -> audio only); download: the media server serves you at that rate (overlay: smaller, then the gray 4:3 bottom rungs). At 128 kb/s: 160x120 gray at 24 fps received, audio continuous, sending audio only. Other participants are unaffected |
| **Jitter** slider up on the sender | frames arrive unevenly; the overlay frame rate wobbles; audio continues |
| a viewer on a slow link (no settings) | the media server measures it and steps that viewer down; others unaffected |
| kill and restart `comms-feed` | it leaves the lobby within 15 s and returns when restarted |

## 4. What has and has not been proven here

- r3, 2026-10-10: `build/test_ring RAM_PORT` (a comms-ram on that port started in the same shell) -- a call rings
  only while its caller offers it; hang-up withdraws the offer; both directions. PASS 9/9. The stamped picture
  (`#frame HH:MM:SS.mmm` bottom-left, "here" clock in the overlay) seen in an offscreen screenshot of the window
  watching a feed: 64 ms on one machine. See STATUS.md "r3".

Proven in the build environment (`COMMS-CPP-STATUS.md` lists every test and how to run it): the frame layer, data plane,
segmentation, bearer and mixer against fnav.py's own code; the media server with fnav.py clients; per-viewer rates
(pass-through, transcoding per size, measured link, MediaSpeed for one viewer only); the call through the media server
both ways, video and audio; the lobby, chat and MediaSpeed on a real comms-ram; the feed watched from the window with the
stock video at 1280x720, 23.9 fps; a call between two windows, screenshots of both.

**Not proven here -- the build environment has no camera, microphone, speaker or screen, and one CPU core:**
- `--camera` (v4l2) and PortAudio microphone and speaker: written, compiled, not run. Their errors name the device
  and the cause.
- Frame rates on real hardware. On the one-core build machine a two-window call ran at 3-4 fps one way: two windows,
  two 720p encoders, a decoder and the media server on one core. The sender's ladder stepped itself down as designed.
- Chat typed in the window (the room's chat is tested; the window's text box is not).
- Windows: not built. The data plane uses Linux socket calls (SIOCOUTQ for the room check); the Windows port is next.

## The call under the frame (2026-11-24)

    build/test_channel                                         # planes and the media server under the frame; about 40 s
    python3 tests/py/test_call_under_the_frame.py build        # a real call across C++ and Python, nine switches; about 60 s
    (cd ../../cpp && g++ -std=c++17 -O2 -fPIC -fvisibility=hidden -Iinclude -shared src/channel_frame.cpp src/channel_frame_c.cpp -o bin/libchannel_frame.so -lcrypto \
       && python3 tests/test_channel_frame_oracle.py)          # the library against core/channel_frame.py; about 60 s

Each prints one PASS or FAIL line per check and `RESULT PASS`. `docs/CHANNEL.md` has what each one showed.

## The memory moves; the Security tab; the phone (2026-11-25)

    python3 tests/py/test_the_memory_moves.py build            # the memory moved twice under a real call; about 50 s
    python3 tests/py/test_the_memory_goes_and_comes_back.py build     # the servers killed during a call and started again: the windows stay up and rejoin; about 1 minute
    bash ../../../../fdf/src/frognet-android/communicator-test/run_tests.sh .   # the phone: its core (steps 1 to 4) and its own activity through the whole cycle (step 5, Robolectric); about 6 minutes

The Security tab's buttons, pressed by the window itself against a live call (offscreen, with a screenshot):

    QT_QPA_PLATFORM=offscreen build/comms-app --ram HOST:PORT --media HOST:PORT --name John --no-audio --channel mediahost \
        --auto-join "Chapel camera" --tab security --security "cipher chacha20-poly1305;key;protocol openqasm;ram OTHERHOST:PORT" \
        --screenshot security.png --after 14
    (python/comms_app.py takes the same options)

## The whole cycle, with every switch made from the window (2026-11-26)

    python3 tests/py/test_the_whole_cycle.py build [CALLS] [OUT_DIR]

Two comms-ram, comms-media and two comms-app windows (offscreen). John's window runs a script: it waits for Donna,
calls her, and during the call changes the cipher, the key, the protocol, where the memory is and the link, then
hangs up -- CALLS times (default 3). Donna's window answers what rings. It prints PASS or FAIL for each thing it
checks, the time each kind of change took, and the longest time between two pictures across each. The windows' logs
and a picture of John's window in each call are left in OUT_DIR.

To watch it instead of testing it, start the two windows without QT_QPA_PLATFORM=offscreen and give one of them
`--script "wait-for Donna;call Donna;stay 5;cipher chacha20-poly1305;stay 3;key;stay 3;protocol chorus;stay 3;hang"`.

