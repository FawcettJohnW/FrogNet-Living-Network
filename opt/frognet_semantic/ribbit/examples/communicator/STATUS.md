## 2026-11-27 (later): the secret by QR code; reset for a demonstration (comms-app r7, Android r8)

John: "Import secret kinda sucks. Let's have the Windows app display a QR code for the Android to import from. Either
the QR code or a file." ... "I also need a way to reset both the Android and Windows machines so I can do a demo" ...
"The QR code should be a FrogNet QR code".

- **Security tab, "Show the secret as a QR code..."** (`src/comms/secret_qr.cpp`, [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1]):
  this machine's secret for the Role as a QR code in a dialog, with its hash; it closes itself after 60 s. The code
  holds a FrogNet line, as an invitation and a join line are: `fdfsec1.<base64url of {"v":1,"name","step","secret"}>`.
  Never printed or logged. The generator is Project Nayuki's (MIT), unmodified, in `third_party/qrcodegen`.
- **The phone (r8), Security tab:** **Scan secret...** (the back camera, `SecretScanActivity`, ZXing core from Maven
  Central) keeps what it reads as that machine keeps it (the default, or a Role's own at its step) and says its hash;
  **Import file...** is what Import secret was; **Reset this phone** erases its secrets and clears the Communicator's
  settings ([THE_PHONE_IS_RESET_V1]).
- **Security tab, "Reset the channel (tcp, not sealed)"** ([THE_CHANNEL_IS_RESET_V1]): two writes, the second when the
  first has been answered. The machine's secret stays.
- Proof (frognet-android `communicator-test/run_tests.sh` step 6, and step 5 for the reset): the desktop's code for a
  default secret and for a Role's own at step 7, and a picture of the dialog itself, are read by the phone's reader
  out of a camera frame; the phone then says the hash the desktop says. `--security "qr FILE;reset"` presses both
  buttons unattended.
- Not done: the phone's scanner has not run on a phone (the camera and its preview are not something Robolectric
  has); r7 has not been built with MSVC (it compiles with MinGW-w64).

## 2026-11-27: first run on Windows and on the phone; r6

John built r5 with Visual Studio (one fault: the frame's DLL import library had the static library's name; fixed in
`CMakeLists.txt`) and ran it against the phone (Android r7) through `comms-ram` and `comms-media` on Seattle7.

- **Nothing arrived under the frame.** Seattle7's servers were built before 2026-11-24 and do not speak the frame;
  with both ends not under the frame the call worked. The node's static servers are built by
  `opt/fdf/src/fdf-source/rinab/tools/build_comms_static.sh`, whose source list did not have the frame: it has now,
  and it also builds `comms-channel`. Both servers say their build first (`[media] build comms-media-2026-11-27-r6`,
  `[ram] build comms-ram-2026-11-27-r6`). The aarch64 and x86_64 static servers pass
  `tests/py/test_the_whole_cycle.py` with the desktop windows (aarch64 under qemu: 18 of 18 changes, 121 ms).
- **The Windows window ended when the node's comms-ram went away during a call** ("Qt has caught an exception thrown
  from an event handler"). `comms-app-2026-11-27-r6`: a step that fails in a handler is said on the console and tried
  again on the next event ([A_FAILED_STEP_DOES_NOT_END_THE_WINDOW_V1]); and the room is joined again when its memory
  comes back ([THE_ROOM_IS_JOINED_AGAIN_V1], `src/comms/room.cpp`): before, the heartbeat failed for ever and the
  window was in nobody's room. `tests/py/test_the_memory_goes_and_comes_back.py` (new): servers killed for 8 s during
  a call, both windows stay up, are back in the room, and call again.
- **Seattle7's comms-media went away twice during a call** (09:09: Windows saw both planes "forcibly closed by the
  remote host" seconds after the server began converting its picture to 160x120 for the phone; 07:4x the same, and
  port 8800 refused after it, which is fdf-comms.sh ending comms-ram when comms-media ends). Why is NOT established:
  nothing from Seattle7 was seen. Servers r7 say why they ended ([A_SERVER_SAYS_WHY_IT_ENDED_V1], `src/comms/fatal.hpp`):
  the signal or the uncaught exception, the thread, and the return addresses, which
  `addr2line -Cfpe build/comms-<arch>/unstripped/comms-media <addresses>` names (the build script keeps that copy).
- Not done: r6 has not been built on Windows; the new servers have not run on Seattle7; the Python window's room has
  not been given the same rejoin.

## 2026-11-26: switch it from the call; the overlay shows the channel; the whole cycle, again and again

`comms-app-2026-11-26-r5` (the C++ window). John: "I need to be able to see who is there, to call them, to answer the
call, stay in the call, and hang up, then do it all over again. While it is running I want to be able to switch
ciphers, keys, protocols, etc. from the app in real time. The perf overlay should show all."

- [SWITCH_IT_FROM_THE_CALL_V1] Under the picture on the Call tab: Cipher (not sealed, aes-256-gcm, chacha20-poly1305),
  New key, Protocol (tcp, chorus, openqasm) and Memory (host:port, Move). A pick takes effect as it is picked; it is
  the same one write the Security tab makes, by the same code, and its answer is shown under the row. Both tabs'
  pickers follow the setting whoever changed it.
- [THE_OVERLAY_SHOWS_THE_CHANNEL_V1] The overlay on the picture now carries everything: who the call is with and
  which call of this run it is; what is received (rung, size, fps, kb/s) and sent (rung, size, kb/s, ceiling); audio,
  shed, key requests; the link (cap, jitter) and what the media server does for this viewer; then the channel: the
  Role, protocol, cipher, 8 characters of a hash of the key this end sends under (never the key), where the memory
  is, kB under the frame each way, the switch calls sent and received, frames sent again and not opened, and the
  last change taken with its time and how long ago. `Channel::totals()` keeps the counts of planes that have closed,
  so the numbers span every call of the run.
- [THE_CALL_ENDS_WHEN_THE_OTHER_LEAVES_V1] A call with one other person ends at this end when that person leaves it.
  Before, the window stayed in the call waiting for a picture, and "do it all over again" needed both to hang up.
- [THE_WHOLE_USE_AS_A_SCRIPT_V1] `--script "step;step;..."` and `--auto-answer` (main.cpp `run_script`): wait-for,
  call, answer, stay, hang, wait-idle, cipher, key, protocol, ram, bandwidth, jitter, shot, quit. Each step says what
  it measured: pictures received since the step before and the longest time between two.
- **Found by the new test and fixed** ([A_FAILED_WATCH_OPENS_ITS_SESSION_AGAIN_V1]): after the memory moved a second
  time, one window's watch of the room failed once a second for ever and that window never saw another call ring.
  `Room::attach` said "moved" and then ended the watch session's read; when the watcher had already opened its new
  session in between, the new one was ended. A read that fails now opens its session again (C++ and Python); the
  phone's `Room.attach` ends the session it names, the old one ([THE_READ_THAT_IS_ENDED_IS_THE_OLD_ONE_V1]).
- Proof: `python3 tests/py/test_the_whole_cycle.py build 3` -- two windows, two comms-ram, three calls one after
  another; in each call nine changes from the window (cipher x3, key x2, protocol x3, the memory moved) and the link
  narrowed to 300 kb/s and opened again. 83 script steps done; 27 of 27 changes answered by every end running them
  (median 5 ms after the write, longest 60 ms); the other window answered all three calls and ended each by itself;
  longest time between two pictures across any change 85 ms (the window paints every 40 ms). The earlier tests
  (test_ring, test_room, test_channel, test_call, test_call_audio, test_the_memory_moves.py) pass unchanged.
- **The Python window has all of it too** (`comms-app-py-2026-11-26-r5`): the row under the picture, the overlay, the
  call ending when the other leaves, `--script` and `--auto-answer`. The frame library's C binding gained
  `cf_key_tag` (8 characters of a hash of a sender's key, for a screen) in the tree's copy and the vendored one.
  Bringing it level found that the Python room never had the C++ room's r3 rules: it now has `leave_call`
  ([LEAVE_WITHDRAWS_THE_OFFER_V1]) and `ringing` ([RINGS_ONLY_WHILE_OFFERED_V1]), and the window uses them. Before,
  a Python caller that hung up was still "on" the call for the other end, and its next call was not answered.
- Proof, three ways (`test_the_whole_cycle.py build N OUT JOHN,DONNA`): C++ calling C++, 3 calls, 27 of 27 changes;
  C++ calling Python, 2 calls, 18 of 18, longest time between two pictures 120 ms; Python calling C++, 2 calls, 18 of
  18, longest 90 ms. The phone's core on the JVM (`communicator-test/run_tests.sh`, steps 1 to 4) passes with its fix.
- The phone (`comms-android-2026-11-26-r7`, frognet-android `docs/COMMUNICATOR-ANDROID-r7.md`): a **Channel** panel on
  the call screen (cipher, new key, protocol, the RAM host moved), the Stats show the call, the link and the channel,
  and a call ends when the other person leaves. Proof: the app's own activity under Robolectric
  (`communicator-test/run_tests.sh` step 5): Home calls, the phone answers, eight changes from the call each answered
  by all 3 ends (367 to 750 ms from the tap), Home hangs up and the phone is back in the room, the phone calls again,
  two more changes, hangs up.
- Not done: no APK was built (no Android SDK here) and r7 has not run on a phone. Not run on separate machines, on
  Windows, or with a camera and sound (the desktop test's windows send a file and a drawn picture, without audio).

## 2026-11-25: the Security tab; the memory moves; the phone is under the frame

[THE_MEMORY_MOVES_V1] `comms-app-2026-11-25-r4`, `comms-android-2026-11-25-r6`. Every process holds the read on the
Role's setting and reports. The setting's `ram_host`/`ram_port` say where the memory is; `comms-channel move` and a
window's "Change RAM host" move it under a running call (room.cpp `Room::attach`, speed.cpp, ram_at.hpp; the same in
Python and on the phone). The owner's work moved into the library (channel.cpp `channel_show`, `channel_change`).
Both desktop windows and the phone have a Security tab. The phone has the frame in Java. Proofs:
`tests/py/test_the_memory_moves.py`, and frognet-android's `communicator-test/run_tests.sh` steps 3 and 4.
Not built or run: the Android APK, Windows. `docs/CHANNEL-DESIGN-AND-IMPLEMENTATION.md` is the record.

## 2026-11-24: the call is under the frame; one writer per plane

[THE_CALL_IS_UNDER_THE_FRAME_V1] [ONE_WRITER_PER_PLANE_V1] [THE_FRAME_IS_A_LIBRARY_V1] `docs/CHANNEL.md` is the record:
the frame library (`ribbit/cpp/src/channel_frame.cpp`, vendored), the frame under both Communicators' planes, the
channel and its reports, `comms-channel`, and the measurements (a real 720p call, nine switches, no interruption).
Each plane now has one writer thread and no lock around its socket; `Plane::send` still returns what became of the
frame. Two fnav changes that are not about the frame: [THE_LENGTH_IS_NEVER_PADDED_V1] (abort padding no longer
overwrites a unit's length) and [A_BROKEN_PLANE_IS_CLOSED_V1] (a plane whose writer fails closes the connection and says why). Not built or run: Windows, Android. Not run: separate machines.

# The C++ Communicator -- status (2026-10-01)

**Re-verified 2026-10-01, clean rebuild (-Werror), every test run: all PASS.** How each is run:
- `python3 tools/gen_frames.py FNAV_DIR SEED 400 | build/test_frame` (and gen_segment/test_segment, gen_bearer/test_bearer,
  gen_mixer/test_audio the same way); `build/test_plane FNAV_DIR`; `python3 tests/test_media_fan.py FNAV_DIR build/comms-media`.
- `build/test_transcode`, `test_media_rates`, `test_media_link`, `test_call`, `test_call_audio`: no arguments.
- `test_room RAM_PORT` and `test_feed_view RAM_PORT NAME` need a running `comms-ram` (and, for the feed, `comms-media`
  and `comms-feed --synthetic 1280x720`) started in the SAME shell: a background process does not outlive the command.
- Configure with `-DRIBBIT_ROOT=<repo>/opt/frognet_semantic/ribbit` until this tree sits at ribbit/examples/communicator.

Built to COMMUNICATOR-SPEC.md and FNAV-SPEC.md. Every part is proven against fnav.py's own code where fnav has it.

| part | where | proof |
|---|---|---|
| fnav frame layer | src/fnav/frame.* | tests/test_frame + tools/gen_frames.py: byte-identical to fnav.py, 4,800 cases over 3 seeds |
| data plane (send-or-drop, room check, abort) | src/fnav/plane.* | tests/test_plane: 200 frames C++ -> fnav.py -> C++; an aborted C++ frame recognised by fnav.py's own receiver, next frame intact |
| segmentation (rate meter, 20 ms ceiling, reassembly) | src/fnav/segment.* | tests/test_segment + tools/gen_segment.py: 8,000 steps equal to fnav's _note_sent/whole_frame_max; 7 reassembly cases |
| media server: sessions, audio fan, keyframe anchoring, teardown | src/media/server.* | tests/test_media_fan.py with fnav.py clients: all cases |
| transcoding, once per size | src/media/transcode.* | tests/test_transcode: 640x360 300 kb/s and 160x120 60 kb/s against their budgets |
| per-viewer rates (decision 2) | src/media/server.* | tests/test_media_rates: upload passed through byte for byte; 160x120 at 120 kb/s; 854x480 at 1 Mb/s; never larger than the upload; audio only at 20 kb/s |
| the bearer (John's rung rule, 2026-08-03) | src/fnav/bearer.* | tests/test_bearer + tools/gen_bearer.py: the same rung as fnav.py's own Bearer.sample at every one of 24,000 samples, 151 rung changes |
| a participant's call: two planes, video send with the bearer, KEYREQ, receive per source, demo throttle/jitter | src/comms/call.* | tests/test_call: John sees Donna at 1280x720, Donna sees John at 640x360, through comms-media; throttled to 400 kb/s, Donna's bearer stepped her to L5 (640x360) by itself and John saw the smaller picture |
| audio: mixer, echo ducker, Opus, payload format | src/comms/audio.* | tests/test_audio + tools/gen_mixer.py: 3,337 mixed blocks byte-identical to fnphone_pa.py's own Mixer, every damage counter equal (cushions 0/640/8000 B); C++ Opus read by libavcodec's own libopus decoder; 20 ms by the TOC; 16 kHz out, not the 48 kHz trap |
| the call's audio plane: Opus send, per-source decode, mixer, playout; audio shed feeds the bearer | src/comms/call.* | tests/test_call_audio: Donna 440 Hz, John 660 Hz through comms-media -- each speaker carries the other's tone ~120 dB and its own 42-56 dB; 249-250/250 blocks in 5 s, no pads or trims, video alongside |
| the room on comms-ram: lobby (presence), chat, MediaSpeed; the media server's MediaSpeed poller | src/comms/room.*, the platform's TuplesClient, comms-ram built from the repo | tests/test_room on a real comms-ram: the lobby lists all three; 3 chat lines in order, another call's line excluded; Julie's MediaSpeed (120 kb/s) written to memory -> she sees John at 160x120, Donna at 1280x720. MediaSpeed matched by identity, not address (by address, one viewer's cap capped all three) |
| per-viewer rate from the MEASURED link (requirement 3) | src/media/server.cpp control_tick | tests/test_media_link: a viewer reading ~320 kb/s stepped 720p -> 854x480 (17% delivered) -> 640x360 (61%), then 100%; the free viewer stayed on the upload, 532/532 |

| real sources: video file (looped) and camera (v4l2 / dshow, MJPEG asked for); PortAudio mic and speaker | src/comms/devices.* | file: the stock video (media/stock-720p.mp4) fed through the whole path, watched in the window at 1280x720, 23.9 fps. Camera and PortAudio: built, NOT run (no devices in the build environment) |
| the window (Qt): lobby, call, colour video + stats overlay, self-view, bandwidth and jitter sliders, download cap (MediaSpeed), stats strip, chat, incoming-call answer | src/app/main.cpp | offscreen screenshots: watching the feed; a call between two windows (each sees the other) |
| picture edges at every send size | tests/test_edges | 1920x1080 .. 160x120: decoded right edge continuous |

Found and fixed in the window: a strip of the viewer's OWN colours down the right edge of an 854x480 picture -- the
scaler left the last pixels of unaligned rows unwritten in a QImage that reused memory ([SCALER_WRITES_ALIGNED_ROWS_V1]).

| the region used the Ribbit way ([HOLD_THE_READ_V1]): each participant keeps a copy of the Communicator's state by ONE held read ("wake me when anything newer than write-order id N exists"); lobby, chat, offers, invitations answered from it; a full read only after 15 s of quiet (removals, ages). The media server holds a read on MediaSpeed. The window redraws on change, no per-second rewrite of call rows | src/comms/room.*, src/app/main.cpp | tests/test_room_watch on a real comms-ram: a chat line in the other participant's copy in 0.5-1.1 ms (was up to 1 s by polling); a new participant in both lobbies in 7 ms including her connect; an idle room 8-20 requests over 21 s (was 4 reads a second per window, the whole chat history each time). test_room, test_feed_view, the window at 128 kb/s: unchanged |
| the bandwidth slider is the participant's link, BOTH directions ([THE_LINK_IS_SYMMETRIC_V1]): uplink paced to it, and written as the viewer's MediaSpeed | src/app/main.cpp | the window at 128 kb/s: receives 160x120 gray at 24 fps, audio continuous; its own sender walks down the ladder |

Found and fixed: closing a room waited up to 15 s on its held read; shutting the session's sockets now ends the read at
once ([A_HELD_READ_ENDS_AT_ONCE_V1]).

| the bottom walk ([BOTTOM_RUNG_SHRINKS_V1]): below L5 the sender's picture gets smaller -- 640x360, 480x360, 320x240, 160x120 -- shrinking only when the wire refuses frames, growing back after 3 clean windows; audio only only after 160x120 is refused, and then until the ladder is back at L5 | src/comms/call.cpp video_tx, Bearer::resume_at | tests/test_bottom: at 128 kb/s 640x360 -> 320x240 -> 160x120, never audio only in 40 s, earned back to 320x240; unthrottled, back to 640x360 within ~10 s and the ladder climbing |

Found on the Pi and fixed: under a narrow link the sender went audio only and never came back -- the port mapped every
rung below L5 to "no video", where fnav keeps sending a smaller picture.

| audio devices at their OWN rate ([ASK_THE_DEVICE_NOT_THE_DEFAULT_V1]): mic and speaker opened at the device's rate, mono, and resampled to and from the 16 kHz wire; the rate the mic actually delivers is measured and logged once | src/comms/devices.* | tests/test_resample: 44.1/48 kHz -> 16 kHz and back, 440 Hz kept (439.9), 5 s kept. The devices themselves: not run here (no audio hardware) |

Found on the Pi: the eMeet C950's microphone is raw ALSA (hw:3,0, 44.1 kHz) with no sound server, so asking it for
16 kHz could only fail; the program now does the conversion itself.

| camera MJPEG in full range ([FULL_RANGE_IS_SAID_NOT_ASSUMED_V1]): yuvj formats mapped to plain ones with the source marked full range, so levels convert to what encoders expect | src/comms/devices.cpp | tests/test_fullrange: yuvj422p MJPEG white -> luma 235, black -> 16, no warnings. The eMeet C950 on the Pi: opened at mjpeg 1280x720 |

Found on the Pi: the eMeet's MJPEG produced a deprecation warning per frame and would have been washed out.

| Windows native (comms-app, comms-feed) via MSYS2: sockets ported (src/fnav/net.*: Winsock, WSAPoll, ioctlsocket), DirectShow camera, cameras listed with their exact --camera value, the media server split out of the shared libraries (Linux only), two Windows gaps in the vendored platform patched ([COMMS_WINDOWS_PATCH]) | build-windows.sh, WINDOWS.md | every non-GUI source compiles for Windows with MinGW-w64 against the Windows headers. NOT linked or run on Windows: MSYS2's repository is unreachable from the build environment. Linux unchanged (test_plane vs fnav.py, test_call, test_feed_view, test_room PASS) |

Known Windows gap: no SIOCOUTQ, so the room check before a frame cannot run; the socket's "would block" decides and
the send buffer is 64 KiB there ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]).

Found under WSL and fixed: a camera that would not open threw out of a Qt event handler and aborted the window; it is
now reported and the call goes on receiving only ([A_DEVICE_IS_NOT_THE_CALL_V1]).

Found on the Pi and fixed, 2026-10-01: the eMeet C950's microphone delivers ~16000 samples/s while claiming 44100
(as fnav recorded in August). The feed sent 16 audio blocks/s instead of 50. A microphone now measures what it really
delivers (0.5-2.5 s after start) and, when that is more than 10% from its claim, resamples from the nearest standard
rate (true_rate(); tests/test_resample with the Razer's and the eMeet's real measurements).

Found on the Pi and fixed, 2026-10-01: the eMeet's video ran ~2 s behind its audio. The camera was read frame after
frame, so whenever it delivered faster than the sender consumed, frames queued in the driver and FFmpeg. A live input
now has its own reader thread that decodes every frame and keeps only the newest; the sender takes the newest
([A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]). tests/test_live: after an 800 ms pause a file gives the next frame
(42 ms), a live input its newest (11750 ms, 281 superseded).

2026-10-01: the bandwidth slider is continuous and logarithmic (32 kb/s .. 4 Mb/s, unlimited at the right), applied
once it rests 200 ms ([THE_SLIDER_IS_SMOOTH_V1]). Jitter is the link, both directions ([JITTER_IS_THE_LINK_V1]): a
DelayLine per receive plane delays each arriving frame 0..N ms without reordering and without accumulating
(tests/test_jitter: spread 2.4 -> 49 ms at 300 ms, 143 of 144 video and 300 of 300 audio frames). comms-feed takes
--bandwidth and --jitter. Known: the UPLOAD side of jitter is still fnav's sleep-before-send, which also lowers the
frame rate (200 ms: ~8 fps).

2026-10-01: video controls ([REAL_SIZE_UNLESS_TOO_BIG_V1]): a picture is drawn at its own size, centred, scaled only
DOWN when bigger than the display (or up, with Fit); a control bar on hover -- pause (display only), mute, volume
(0-150%, applied in playout), real size, 1:1/Fit, full screen; keys F/Esc/Space/M, double-click for full screen.
Checked by screenshot: 160x120 shown small and centred; 1280x720 scaled down to fit.

2026-10-01: the diagnostics window, after the Python Communicator's comms_diag.py (button on the video bar, or D): the
bearer (rung, ceiling, stressed n/3, clean n/3; spans 30 s / 60 s / 2 m), the WIRE figures large (send and receive fps
and KB/s, video and audio; picture sizes; pass-through or transcoded; drops; what the media server is doing for this
viewer), three panels on one time axis (RUNG under its ceiling with every step marked, THROUGHPUT against the budget,
REFUSED video and audio), the law, and the hysteresis lag measured in the call where the bearer runs. The media server
publishes MediaHold per viewer every 2 s ([MEDIAHOLD_IS_MEMORY_V1]). Gaps are gaps ([DIAG_IS_UNGATED_V1]).

2026-10-02: no frozen last frame ([NO_VIDEO_IS_A_STATE_V1]): 2 s without a picture in a call and the display shows
"No video" with the reason from the media server's MediaHold row (audio only / waiting for a keyframe / nothing
arriving); the server now publishes audio_only. Checked: a 40 kb/s viewer sees "No video -- Audio only", audio at 50
blocks/s.

Fixed against fnav.py: abort padding now always ends in the sentinel ([ABORT_PAD_ENDS_WITH_THE_SENTINEL_V1]).

Found and fixed while building the media server (both from the link test, which crashed before them):
- Transcoding never runs on the thread that reads an upload: each sender has a worker that decodes every frame and
  encodes only the newest picture (fewer frames under load, never more delay); 2 s behind, it skips to a keyframe.
- A viewer never makes a sender's thread wait: viewer connections run in debt mode ([DROP_THE_FRAME_NOT_THE_CALLER_V1]):
  a part-written frame's remainder is owed as abort padding and paid as the socket takes it; new frames are shed
  meanwhile. A viewer whose connection breaks is closed; the sender is untouched.

Next: run camera and PortAudio on real machines; Windows (send_room without SIOCOUTQ, Winsock).
To test: TESTING.md.

Build: cmake -B build && cmake --build build. Needs libavcodec/libavutil/libswscale, libvpx, libopus, portaudio, Qt 6.


## The Python implementation (python/frogcomms) -- 2026-10-02

A second implementation of the same contract: it shares nothing with the C++ code but the tuples in comms-ram, the
fnav wire to the same media server, and the rules. The memory is reached through libcomms_tuples (a C ABI over the
platform's TuplesClient, with the held read's "after"), as every Ribbit Python participant reaches it.

| part | files | proven |
|---|---|---|
| memory + room (presence, lobby, chat, calls, MediaSpeed, MediaHold; one held read) | memory.py, room.py, src/pyabi | tests/py/test_room_interop.py: Python and C++ in one comms-ram see each other, read each other's chat; a C++ invitation reaches Python intact; the Python room closes in 0.09 s |
| fnav wire: frames, data plane, segmentation, bearer | fnav.py | tests/py/test_fnav.py, the SAME fnav.py vectors as the C++: 4800 frame cases, 4000 rate steps, 9000 bearer samples (72 rung changes), echo 200 frames and an abort over TCP against fnav.py's own peer |
| call: two planes, delay lines, bearer + bottom walk, VP8 (PyAV), Opus (PyAV), mixer | call.py, media.py, audio.py | tests/py/test_call_interop.py through the C++ media server: C++ feed -> Python viewer 119 frames/5 s at 1280x720, 250 audio blocks, the tone at 440.0 Hz; Python feed -> C++ viewer 1280x720, 118 frames/5 s |
| the feed (requirement 1) | python/comms_feed.py | watched by the C++ test_feed_view: 1280x720, 119 frames/5 s, audio flowing |
| the window (PySide6): lobby, call, real-size video + controls + full screen, smooth bandwidth (both directions), jitter, stats, chat, No video, diagnostics | python/comms_app.py | watching the C++ feed: 1280x720, 24.0 fps, audio 50 blocks/s (screenshot). A C++ window and a Python window in one call, each receiving the other at 1280x720, 23 fps |

Found and fixed: PyAV's Opus decoder decodes at 48 kHz whatever rate is asked; a 440 Hz tone arrived at 146.7 Hz
until every decoded frame went through a resampler to 16 kHz.


## 2026-10-03: the Internet, and reading your own writes

- Call announcements are keyed to the participant (`CallsAnnounce/user:<name>`), never to an address
  ([ONE_ANNOUNCEMENT_PER_PARTICIPANT_V1]): the old comms_control's [FOUR_TUPLES_V1] keyed it `host:<ip>`, right on a
  FrogNet mesh where every host has its own address and a host offered calls; in the rewrite a participant offers, and
  on the Internet two people behind home routers can both be 192.168.1.10. An offer now reports who offered it.
- [READ_YOUR_OWN_WRITES_V1] every write goes to memory and into the participant's own copy at once (C++ and Python);
  others' writes arrive through the held read a few ms later, and tests wait for them as the window does.
- test_room under deliberate load: its fixed 7 s read a stale pre-cap picture once (a capped viewer's picture changes
  with its transcoded stream's first keyframe; the server never sends a capped viewer the uncapped stream). Now waits
  up to 20 s and checks pass-through against what the sender actually sends: 6/6 PASS alongside the interop test.

## r3 (2026-10-10): ringing, hang-up, the stamped picture, build tags -- `comms-app-2026-10-10-r3`

John, on the Windows app: "thinks it always has a call from the phone". Root cause, two parts:
- **The invitation was treated as the call.** An invitation is a CallsInvitations row the caller writes own=false at
  `user:<callee>`; nobody removes it and it reads fresh for 120 s. The window showed Answer for any invitation it had
  not seen and hid it only when a call started -- so a call the caller gave up on stayed "ringing" forever.
  `[RINGS_ONLY_WHILE_OFFERED_V1]` Room::ringing(): an invitation rings only while it is <= 60 s old AND its caller
  still offers that session in CallsAnnounce. The window re-checks every tick (1 s) and hides Answer when it stops.
  The phone had the same hole once it started ringing (it rang until tapped); it now applies the same rule each tick.
- **The desktop never withdrew its own offers.** There was no leave_call in the C++ Room: hang_up stopped the call but
  every session the window ever started stayed in its announce row (refresh_calls rewrote it every 5 s).
  `[LEAVE_WITHDRAWS_THE_OFFER_V1]` Room::leave_call (as the Java Room's leaveCall) on hang-up: the session leaves
  offered_ and joined_, and both rows are rewritten without it.
- Proof: `tests/test_ring RAM_PORT` on a real comms-ram -- nothing rings before a call; the callee rings while the
  caller offers; the caller hangs up before the answer -> it stops ringing although the invitation row is still there;
  the next call rings, and only it; the reverse direction; a hung-up session is no longer offered. PASS 9/9.
  test_call, test_call_audio, test_room: PASS unchanged. Phone: the Android suite (vectors, core, Robolectric app
  flow) PASS. John, 2026-10-10: on a physical phone the Communicator "was responsive and worked properly".

`[STAMP_THE_PICTURE_V1]` (John: "having a counting number there would be useful", after a call that started far
behind and caught up slowly on the emulator). Each desktop sender burns `#<frame> HH:MM:SS.mmm` -- its frame count and
its wall clock at capture -- into the bottom-left of the picture before encoding (src/comms/stamp.hpp; 5x7 digits,
6 px a dot at 720p). It is part of the picture, so it survives comms-media's transcode and every hop. The viewer shows
its own clock: the window as the first line of the stats overlay ("here HH:MM:SS.mmm", read at paint, 25 Hz), the
phone as a "here" line under the caller's name (30 Hz). Lag = here - stamped, read off the screen (one clock when the
emulator runs on the Windows machine; otherwise as good as the two clocks agree). Measured here, feed -> comms-media
-> window on one machine: 64 ms. `COMMS_STAMP=0` turns it off. The phone's own sender does not stamp (its picture is
GL-rendered into the encoder's surface).

Build tags (the software says what runs): comms-app and comms-feed print `[app] build comms-app-2026-10-10-r3` /
`[feed] build ...` first, and the window title carries it. The phone: `comms-android-2026-10-11-r5` (r4's camera
logging + r2's call screen: full screen, draggable inset, fading controls, fill/fit), a screen of the FrogNet member
app (repo opt/fdf/src/frognet-android, docs/COMMUNICATOR-ANDROID-r5.md); there is no separate Android Communicator.

Open: the emulator call's start-up lag is not explained -- no frame carried a time, so nothing could tell how far behind
it was. The places it can sit: the sender's capture and encoder, its socket send buffer (on Windows not measurable,
see WINDOWS.md) and the tunnel, comms-media's per-viewer queue, the viewer's socket, the viewer's DelayLine (bounded
at 4000 frames, never sheds), the decoder. Next if it recurs: the stamp's numbers at the start and once caught up,
then `[DIAG-LAG]` depth and age at each of those queues.
