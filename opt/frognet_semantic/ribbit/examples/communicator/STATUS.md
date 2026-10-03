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
