# fnav -- the media layer, as fnav.py defines it (read from the code, 2026-10-01)

Source: `opt/frognet_semantic/ribbit/examples/communicator/fnav.py` (8,240 lines), with `sotf_ladder.py` (201) and
`fnphone_pa.py` (744). All three read, 2026-10-01: every section below names the lines it comes from, and the index
at the end lists all 161 tagged decisions.

## Frame layer
- Every frame on a connection: `[len u32 BE][kind u8][srclen u16 BE][src utf-8][payload]`. TCP only.
- Kinds:
  | kind | name | direction | payload |
  |---|---|---|---|
  | 0 | AUDIO | sender -> relay -> viewers | audio block (Opus by default; 16 kHz mono s16le PCM as an operator override) |
  | 1 | VIDEO | sender -> relay -> viewers | `[level u32: rung 0..7, high bit = keyframe][codec u8][codec packet]` |
  | 2 | BACKPRESSURE | relay -> sender | u32: video frames shed for a viewer (the drop-count feed is withdrawn) |
  | 3 | PLANE | first frame on each connection | b"A" or b"V": which plane this connection is |
  | 4 | KEYREQ | relay -> sender | viewers that lost their reference: emit a keyframe now |
  | 5 | AUDIO_BACKPRESSURE | relay -> sender | u32: viewers whose audio is being starved |
  | 6 | VSEG | sender -> relay -> viewers | `[frame_id u16][index u16][flags u8: 1 LAST, 2 ABORT]` + up to 1368 bytes |
- The relay is content-blind: it forwards frames unchanged and reads only the keyframe bit by offset.
- Video frames over 8 KiB (VSEG_WHOLE_MAX) go out as 1,368-byte segments (the tunnels' MSS), so the shared uplink
  drains between pieces and audio is serviced in the gaps. Measured: 512 B segments, 10.4 ms worst audio latency;
  16 KiB (unsegmented), 142 ms.
- Send-or-drop: non-blocking sockets, no queue. A frame is written whole or dropped whole; audio gets a short
  writability grace. A frame cut off partway is completed with the sentinel `DE AD BE EF`, so the receiver
  discards it and resyncs on the next length prefix.
- Newest wins everywhere: the camera keeps only the freshest frame; the receiver keeps only the latest decoded frame
  per source; the audio mixer trims to a bounded cushion.
- Codecs: Opus audio (24 kb/s default); VP8 or H.264 video through PyAV; OpenCV capture (DirectShow on Windows).

## Send path and data plane (lines 380-1395)
- **Whole frames only** ([WHOLE_FRAME_SEND_V1], [ALL_NONBLOCKING_V1]): every socket is non-blocking. Nothing written
  yet means the frame is dropped cleanly; a partial write commits it, and it is finished within a bounded time or
  completed with the abort sentinel, never left to desynchronise the stream.
- **Check room before the first byte** ([DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1]): room = what the kernel will
  hold minus what it already holds. On Linux, SO_SNDBUF reads back doubled and TIOCOUTQ counts the overhead too --
  the two are not the same units and do not simply subtract ([THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1]).
- **The guard must work on Windows** ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]): Windows has no TIOCOUTQ, and
  without the guard a buffer full of stale 1080p refused even 3 KB frames for ~10 s (measured 2026-08-11). fnav tracks
  outstanding bytes itself (handed to send() minus drained). For C++: evaluate SIO_TCP_INFO (Windows 10+) as a real
  per-connection figure; measure, do not assume.
- **The buffer is the latency** ([THE_BUFFER_IS_THE_LATENCY_V1]): SNDBUF 128 KiB (Linux reads back 256), because the
  send buffer is how much stale picture the kernel may hold.
- **Retries:** audio is retried at least 2 times, 8 ms apart (protected); a keyframe 3 times
  ([ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]); an ordinary video frame is simply dropped.
- **Audio intent is shared between the planes** ([AUDIO_INTENT_IS_SHARED_V1]): the video plane yields between segments
  when the audio plane has declared it is waiting; the gate is one object both planes hold.
- **The segmentation ceiling is a time, not a size** ([THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]): a write may own the
  uplink for at most one audio block (20 ms) at the rate the socket is actually accepting; larger frames are segmented.
- **A frame is one shed** ([A_FRAME_IS_ONE_SHED_V1]): a dropped frame counts once, however many segments it had.
- **Rung costs are measured, not assumed** ([RUNG_IS_MEASURED_V1]): what each rung actually costs on this wire is
  learned; an unvisited rung is estimated from a measured one by pixel ratio, erring safe
  ([KEYFRAME_SCALES_BY_PIXELS_V1]).
- **The video floor is measured** ([VIDEO_FLOOR_IS_MEASURED_V1], [FLOOR_IS_PROVEN_NOT_GLIMPSED_V1]): several keyframes
  spaced across a second with audio untouched, not one keyframe the buffer absorbed; below L5 the picture turns 4:3
  and keeps shrinking ([CONSTRAINED_GOES_4_3_V1], [BOTTOM_RUNG_SHRINKS_V1]).

## Receive and stats (lines 1395-1689)
- **WireStats** (2 s rolling window): frames sent, received and dropped per plane; real bytes on the wire (frame plus
  framing: 12 bytes per video frame); keyframe and inter sizes kept apart. Headline: frames per second actually getting
  through, against a 30 fps reference.
- **Receive is measured in bytes, both planes** ([RECEIVE_IS_MEASURED_V1]): `v_rx_kbps`, `a_rx_kbps`, audio frames
  received. Below L5 audio is all the inbound traffic.
- **A window knows its rung(s)** ([WINDOW_KNOWS_ITS_RUNG_V1]): each published window names the rung(s) it covered,
  both ends if it straddled a change; a window with no video is labelled with the rung in effect.
- **Audio completeness is measured in seconds, not packets** ([AUDIO_SECONDS_NOT_FRAMES_V1]): each Opus packet's
  duration from its TOC byte (config 0-11 SILK 10/20/40/60 ms, 12-15 hybrid 10/20, 16-31 CELT 2.5/5/10/20), summed
  to audio-seconds per second. An unreadable packet raises and is counted, never read as 0 ([NO_FALLBACK_V1]).
- **Receive reads exactly the declared length**, always, so an aborted frame costs one frame and the next read is on
  a length prefix. A close between frames (hang-up) and a close mid-frame (truncation) are reported as different
  faults ([RX_SAYS_WHERE_V1]).
- **Close without lingering** ([SHUTDOWN_NOT_CLOSE_V1]): SO_LINGER off, so closing never parks a thread on a peer
  that is gone.

## The relay (lines 1689-3276) -- what the new media server keeps, and what it replaces
The new media server transcodes per rung (decision 2), so fnav's FORWARDING model is replaced. Everything it learned
about connections, accounting and protecting audio carries over:
- **Connections:** every socket non-blocking, accept included ([ALL_NONBLOCKING_V1]). The first frame on a connection
  declares its plane, an 8-byte teardown tag and the call session ([TWO_PLANES_V1], [TEARDOWN_IS_PER_SESSION_V1],
  [FAN_IS_PER_SESSION_V1]); a connection with no session can neither send nor receive. Losing one plane tears down
  the other plane of the SAME session only, by shutdown(), never close() on a socket another thread is reading
  ([SHUTDOWN_NOT_CLOSE_V1]).
- **One write lock per peer socket** ([SOCKET_WRITE_LOCK_V1]): any channel back to a sender needs it, or frames
  interleave and the length prefix desynchronises.
- **Readiness first** ([EWOULDBLOCK_FIRST_V1]): a zero-timeout readiness check before anything is written. Audio gets
  two tries; a frame that will not fit is dropped and the connection is kept ([DROP_THE_FRAME_NOT_THE_CALLER_V1]); a
  part-written frame becomes debt paid in sentinel padding before anything else goes to that socket.
- **Keyframes are not shed** ([KEYFRAME_SURVIVES_EWOULDBLOCK_V1]): a keyframe that hits a full socket is held and the
  frames behind it are dropped; a newer keyframe replaces the held one; a hold lasts at most 3 s
  ([A_HOLD_IS_NOT_FOREVER_V1]). A viewer that lost its reference is "unanchored" and gets no inter frames until a
  keyframe goes ([KEYFRAME_ANCHOR_V1]); keyframes are charged to a viewer's budget even if that overdraws it.
- **Keyframe requests** to the sender at most one per 3 s per sender ([KEYREQ_NEEDS_ROOM_V1]). With transcoding, the
  media server's own per-rung encoders answer most of these locally; a request upstream is needed only for the
  pass-through rung.
- **Per-viewer cap from MediaSpeed** ([MEDIASPEED_V1]): read from memory, per viewer, a token bucket per viewer;
  a cap older than 120 s is dropped. In the new design the cap selects the viewer's rung (encoded once per rung).
- **Accounting, per viewer and per plane, over 2 s windows** ([DELIVERED_RATE_FEEDBACK_V1],
  [AUDIO_IS_ACCOUNTED_SEPARATELY_V1], [WIRE_ACCOUNTING_IS_BOTH_PLANES_V1]): frames aimed vs delivered (counted once,
  at the top of the per-viewer loop -- [AIM_IS_COUNTED_ONCE_V1]); ingress per sender and kind, with the rung read from
  the frame header and audio in seconds ([VIDEO_IN_CARRIES_THE_RUNG_V1], [AUDIO_SECONDS_NOT_FRAMES_V1]). A viewer
  getting under half of what is aimed at it is starved (BP_STARVED 0.5).
- **Audio drives the resolution** ([AUDIO_DRIVES_THE_RESOLUTION_V1]): no audio frame is shed while any video is still
  being delivered to that viewer; doing so is logged as a doctrine violation.
- **Published state** ([MEDIAHOLD_IS_MEMORY_V1]): every 2 s, per viewer, what the media server is holding and why
  (unanchored, held keyframes, inters shed, cap, frames aimed and delivered) as a MediaHold tuple -- what the stats
  overlay and panel display.
- **The media server reaps dead calls** ([RELAY_REAPS_DEAD_CALLS_V1]): it holds the sockets, so it removes call rows
  for its own calls that no longer have connections. A sender alone in its session is told so, not silently drained
  ([ALONE_IS_NOT_SILENT_V1]).
- **Its own wire log** (CSV, [RELAY_WIRE_LOG_V1]): per leg and window, in, aimed, delivered, cap.

## Rungs and codecs (lines 3276-3885)
**The ladder.** L0/L1 text floor, always available; L3/L4 audio only; L5-L8 video. Video geometry per aspect:

| rung | 16:9 | 4:3 | bitrate | gray |
|---|---|---|---|---|
| L8 | 1920x1080 | 1600x1200 | 3.0 Mb/s | no |
| L7 | 1280x720 | 1280x1024 | 1.2 Mb/s | no |
| L6 | 854x480 | 1024x768 | 600 kb/s | no |
| L5 | 640x360 | 800x600 | 300 kb/s | yes |
| below L5 (always 4:3) | 640x480 / 480x360 / 320x240 / 160x120 | | 400 / 250 / 150 / 60 kb/s | yes |

- One geometry list, largest first, used by BOTH ends, so a size a receiver names is one the producer can make. Below
  L5 the picture goes 4:3 ([CONSTRAINED_GOES_4_3_V1]): a face is taller than wide.
- Gray at the bottom rungs ([SMALLEST_WIRE_WINS_V1]) because flat chroma costs almost nothing; fnav notes this is
  NOT yet measured on its own wire -- measure it in C++.
- A rung's rate means nothing until 15 frames and 2 s at that rung (WARMUP).
- The fields that reach the encoder: w, h, bitrate, gray, fps ([GEOMETRY_IS_NOT_A_RUNG_V1]); what the encoder was
  actually handed is what is reported, not the table ([ENCODED_SIZE_IS_MEASURED_V1]). A new size needs a new keyframe.

**Video encoders** (codec id in every VIDEO frame): 0 VP8 (libvpx, the default), 1 H.264 software (libx264),
2 H.264 hardware (h264_v4l2m2m: the Pi). Settings, exactly:
- VP8: `deadline=realtime, cpu-used=8, lag-in-frames=0, g=fps*2`, plus `maxrate`/`bufsize`.
- x264: `preset=ultrafast, tune=zerolatency, bf=0, g=fps*2`, plus `maxrate`/`bufsize`; pixel format yuv420p.
- Hardware H.264: nv12, `g=fps*2`, bit_rate only.
- **VBV or the budget is a wish** ([VBV_OR_THE_BUDGET_IS_A_WISH_V1]): bit_rate alone is an average over the whole
  stream; maxrate and bufsize bound it over a window a person or a ladder would notice. Measured: 320x240 asked for
  150 kb/s and overshot without them.
- **Keyframe interval bounded at fps*2** ([KEYFRAME_INTERVAL_IS_BOUNDED_V1]): libvpx otherwise chose 128 frames
  (5.3 s at 24 fps). A forced keyframe ([KEYFRAME_ON_REQUEST_V1]) is advisory: read the returned key flag, never
  assume.
- An encoder is proven at startup with the real rate-control options, by encoding: creating a codec context succeeds
  even for a hardware encoder that does not work.

**Audio:** Opus, 24 kb/s by default, mono at AUDIO_RATE, one codec per direction ([AUDIO_IS_OPUS_V1]); no silent PCM
fallback -- PCM is an operator decision (--pcm-audio). Decoded planes are padded by the library: slice to
samples*2 bytes.

## The bearer: how a sender picks its rung (lines 3885-4100) -- John's rule, 2026-08-03 ([LADDER_RATE_V2])
Starts at the ceiling and floats. Sampled once per video frame.
- **Down at once** on any of: more than 3 consecutive samples with drops (BAD_READS); more than 5 drops in one
  second (SEC_DROPS); audio shed by the wire; the frame rate below 10 fps or below 75% of target for more than
  3 consecutive samples (FPS_DOWN, FPS_DOWN_FRAC, FPS_DOWN_READS).
- **Up only when both:** 3 whole seconds at zero drops (CLEAN_SECS), AND the sender is making full frame rate at the
  current rung (98% of target, FULL_RATE_FRAC). Clean seconds alone only prove the wire refuses nothing; a box making
  13 fps would climb into a rung it cannot hold.
- **Both a run count and a per-second rate:** the run count catches a short burst before the second ends; the
  one-second window makes a rate visible to a per-frame sampler (run rule alone oscillated L6/L7 at 10-13 drops/s).
- **Probe back-off** ([PROBE_BACKOFF_V1]): a rung that just failed is held off 15 s, doubling per consecutive
  failure to at most 300 s; 60 s clean at a rung forgives its record.
- **Protected floor:** congestion steps down to the audio rung and stops there; the call never dies.
- In the rewrite, the sender's bearer governs only its UPLOAD rung. Each viewer's rung is the media server's
  (decision 2), driven by that viewer's own link by the same rule.

## The call (lines 4100-7997): controls
- **set_throttle(bps)** -- DEMO: caps the uplink to bps (0 = unlimited) on both planes. A link condition, not a ladder
  input: the writer paces the wire, video is shed, the bearer walks the rung down -- 720p, 480p, 360p, voice. The
  dial simulates a smaller wire; discovering what it carries is the ladder's job. The old shortcut that computed a
  ceiling from the dial ([SELF_CAP_FROM_LINK_V1], via VIDEO_SHARE 0.60) is gone: it turned a 408 kb/s dial into
  audio-only with no frame ever sent.
- **set_jitter(ms)** -- DEMO: injects up to ms of random latency per frame on the uplink, both planes.
- **cap_to_link / level_for_link** remain for the VIEWER path: a viewer's declared receive capacity.

## The call: audio path (lines 6567-6925; engine in fnphone_pa.py, 744 lines -- to read)
- **Ask the device, not the default** ([ASK_THE_DEVICE_NOT_THE_DEFAULT_V1]): the rate PortAudio advertises is not
  always what the hardware clocks at (an eMeet C950 advertised 44100 and delivered 17.7 blocks/s of 20 ms). Open at the
  advertised rate first, measure what arrives, resample to the wire rate.
- 20 ms blocks; capture and playback each resampled to the wire rate.
- **Audio drop is loud** ([AUDIO_DROP_IS_LOUD_V1]): blocks delivered, blocks dropped on a full queue and blocks the
  driver flagged are all counted, per window (never a since-start average), and the driver's flag is named once.
- **The mic is not the link** ([THE_MIC_IS_NOT_THE_LINK_V1]): the device's own capture rate is kept, so "the network
  did not carry my audio" is distinguishable from "I never captured any".
- **Mixer damage is counted** ([MIXER_DAMAGE_IS_COUNTED_V1]): playout padding (underrun) and cushion trims are reported
  when non-zero.
- **A missing audio stack degrades to video** ([AUDIO_DEGRADES_TO_VIDEO_V1]) and the error says what was found
  ([AUDIO_ERROR_NAMES_THE_CAUSE_V1]).
- Optional raw taps of what is captured and what plays ([TAP_THE_THING_THAT_PLAYS_V1], [LIVE_TAP_V1]).

## The call: video send path (lines 6925-7549)
- **Ask the camera for MJPEG** ([ASK_THE_CAMERA_FOR_MJPEG_V1]): otherwise V4L2 hands back uncompressed YUYV (~3.1 MB
  per 1080p frame); report what was actually agreed.
- **One-frame capture buffer** ([A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]): read() must return the freshest frame;
  where a backend ignores the setting, frames queue and arrive stale-then-flood, which looks like a failing link.
- **A failed read is counted** ([A_FAILED_READ_IS_NOT_NOTHING_V1]), never turned into a silent gap.
- **No catch-up burst** ([NO_CATCH_UP_BURST_V1]): the next frame's deadline is set from now, so an overrunning encode
  delays the next frame instead of firing several back to back.
- **A rung is judged on frames sent at that rung** ([RATE_IS_MEASURED_AT_THIS_RUNG_V1]) after a warm-up
  ([SOURCE_CANNOT_SUSTAIN_V1]), not on a rolling meter full of zeros from before.
- **Evidence beats arithmetic** ([KEYFRAME_BACKLOG_V1]): a table said 93 kb/s cannot carry video; no keyframe backlog
  said the link was carrying it. The link estimate is only the opening guess.
- **The relay's drops do not steer the sender** ([DOWNLINK_BACKPRESSURE_V1]): in fnav's own words, "the transmitter
  should send its best and let the relay serve each client what that client's link allows. It cannot do that yet."
  The rewrite's media server does exactly this (decision 2).
- **Read counters nobody else drains** ([READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1]): cumulative counters, diffed by each
  reader, never a counter one reader zeroes under another.
- **A wire log row every second** ([WIRE_LOG_V1]) with every input the decision saw and what it produced, including
  in audio-only.

## The call: receive and display (lines 7549-7997)
- **One receive loop per plane** ([TWO_PLANES_V1]), both feeding the same decoder and the same playback jitter buffer:
  the split is about who waits on whom, not about keeping the streams apart once they arrive. Each loop's end says
  which plane and why ([RX_SAYS_WHERE_V1]).
- **One frame's failure is that frame's problem** ([DROP_THE_FRAME_NOT_THE_CALL_V1]): handling is per frame; a decoder
  refusing one packet never ends the loop. Aborted frames are counted (1, 10, 100, then every 1,000 logged).
- **Arrivals counted per source and kind, before the decoder** ([RX_IS_PER_SOURCE_V1], [RECEIVE_IS_MEASURED_V1]), so a
  muted, tapped or undisplayed stream still reports what arrived.
- **Unknown audio format is refused, not guessed** ([AUDIO_IS_OPUS_V1]): decoding Opus as PCM is noise at full volume.
- **No display is not no decoder** ([NO_DISPLAY_IS_NOT_NO_DECODER_V1]).
- **Overlay** (`_draw_overlay`): the stats drawn on the video.

## Requirement 1: the headless publisher (lines 8031-8115, [HEADLESS_PUBLISHER_V1])
An unattended sender keeps itself visible with two tuples, re-asserted every 5 s (both age out):
- **presence:** puts it in the lobby; its display name starts with `*` so a person can tell an unattended camera from
  someone at a keyboard; mic reported False.
- **call:** originates a session at the media server it is streaming to, so a viewer has something to join.
A failure to publish never stops the stream, and is logged every time: a sender that is streaming and invisible looks
exactly like one that is not running.

## The ladder (sotf_ladder.py, 201 lines)
| rung | name | carries | needs |
|---|---|---|---|
| L8 | BULLFROG | audio + 1080p | camera |
| L7 | CHORUS | audio + 720p | camera |
| L6 | ENSEMBLE | audio + 480p | camera |
| L5 | DUET | audio + 360p grayscale | camera |
| L4 | SOLO | audio only | mic |
| L3 | VOICE | audio only, reduced | mic |
| L2 | WHISPER | plain text on the wire | -- |
| L1 | BEACON | token vocabulary (contested-link floor) | -- |
| L0 | PULSE | implicit presence | -- |
- Total wire cost never increases from L8 down to L0; a step down always sheds load.
- **Ceiling** (what this machine may send): camera and mic -> L8; camera, no mic -> L8 video only (a video rung needs
  a camera and ONLY a camera, [VIDEO_DOES_NOT_NEED_A_MIC_V1]); no camera -> L4; neither -> L2. L0/L1 are always
  allowed, so a call never dies: the medium changes instead.
- **send_level** = the highest allowed rung no higher than the ceiling and no higher than the bearer permits.
- A rung promises which tracks and what video size, not sample rate or channels.
- Conflict inside fnav's lineage: this module keeps the bottom rungs 16:9 ([STANDARD_SIZES_WIDESCREEN_V1]); fnav.py
  later moved them to 4:3 ([CONSTRAINED_GOES_4_3_V1]). The later decision, in running code, is the one specified.

## The audio engine (fnphone_pa.py, 744 lines)
- In-process audio through PortAudio: no ffmpeg/ffplay subprocesses, whose pipes and player queue put a ~100 ms floor
  on latency. Mouth-to-ear in the tens of milliseconds.
- **Wire format:** 16 kHz mono s16le (32,000 B/s) before Opus; 20 ms blocks (`--block-ms`); capture and playback each
  resampled between the device rate and the wire rate (StreamResampler).
- **Mixer** -- the playback jitter buffer: one buffer per remote source with a playout cushion (`--jitter-ms`, default
  40 ms). Playback waits for the cushion to fill per source, then pulls one block from each source and sums them with
  clipping; a dry source contributes silence, never a stall. The backlog is capped at 2x the cushion (at least
  240 ms), because a buffer allowed to grow is audio drifting behind video; bytes trimmed to hold the cap are counted.
- **The two places the mixer damages the signal**, both counted ([MIXER_DAMAGE_IS_COUNTED_V1]): a trim (a hard
  splice) and a short block zero-padded to full (a hard step to silence).
- **Echo ducker** (soft suppression, not cancellation): while the far side plays out of the speaker, the local mic
  gain is ducked toward 0.15. Fast attack (envelope 0.5/0.5), slow release (0.92/0.08); full duck at a speaker peak
  of 400; no duck below an envelope of 30.

## Measuring a viewer (the call's consumer rules, lines 4487-4723) -- in the rewrite, the media server's per-viewer rules
fnav's consumer measured what reached it and reported; the producers moved
([PRODUCER_LEADS_CONSUMERS_REPORT_V1]). In the rewrite the same rules decide each viewer's rung at the media server:
- **Measure long enough to be a measurement** ([MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1]): frames counted over a
  6 s horizon (RATE_HORIZON_S), not one 2 s window -- a camera that stalls and catches up reads 0.5 fps in one window.
- **Absence is not a measurement** ([ABSENCE_IS_NOT_A_MEASUREMENT_V1]): zero frames means nothing was sent, not that
  the viewer is slow. A viewer learns "too much for me" only by being sent too much and failing to take it.
- **Judge against something** ([JUDGE_AGAINST_SOMETHING_V1]): no claim of what was sent, no verdict.
- **Happy means stable** ([THE_FLAG_MEANS_STABLE_V1], [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1]): receiving at least 80% of
  what is being sent (HAPPY_FRACTION) for 3 consecutive windows (HAPPY_WINDOWS); not-yet-stable is a different state
  from overloaded.
- **Down:** 2 consecutive shortfall windows (BAD_WINDOWS). Inbound thresholds: under 15 fps for 2 windows is not
  keeping up; 22 fps for 3 windows is (INBOUND_DOWN/UP_FPS, _WINDOWS).
- **Settle before climbing** ([SETTLE_BEFORE_YOU_CLIMB_V1]): 45 s at a rate before proposing to leave it upward
  (SETTLE_S; 20 s measured as still switching a lot, 2026-08-12); 15 s after a rate change before judging it
  (RATE_SETTLE_S); 4 s after a drop (DROP_SETTLE_S).
- **Say it even when nothing changed:** re-report every 8 s (REPORT_EVERY_S). Report only what is fresh: a cap or
  treatment older than 120 s is ignored; capacity older than 30 s is stale.
- **Alone is not silent** ([ALONE_IS_NOT_SILENT_V1]): sending into a session nobody else is in looks healthy from the
  socket; the distinguishing fact is that nothing comes back. Warn every 15 s.
- **A hang is the worst report** ([A_HANG_IS_THE_WORST_REPORT_V1]): connecting to the media server is bounded at 8 s.
- **Keyframe probing up** (when nothing has been published): backlog-free 8 s before trying a rung up, watch 2 s
  before believing it, back off to at most 600 s (KF_PROBE_*).
- **The bottom rungs** step every 2 s, and grow back after 3 clean windows (BOTTOM_STEP_S, BOTTOM_CLEAN_WINDOWS).

## What survives from the control plane (lines 4723-6204; the derivation itself is replaced by decision 2)
- **Everyone publishes their own capability** ([EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1]): the best geometry this
  machine and camera can make, a fact known only here; what a producer starts at.
- **A new size needs a new keyframe** ([A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1]): changing geometry rebuilds the encoder;
  without a keyframe the far end shows an old picture at the old size.
- **Walk the keyframe down until it fits** ([WALK_THE_KEYFRAME_DOWN_UNTIL_IT_FITS_V1]): a keyframe that will not go
  means the size is wrong now -- try the next size down, one frame each, a fraction of a second in all.
- **Shrinking only helps if the wire is the limit** ([SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1]): a smaller
  picture makes fewer bytes, not a faster encoder. A sender at 1-2.5 fps with no drops is CPU-bound, and the answer is
  a cheaper encode, not a smaller rung. Measure which limit is binding.
- **Growth is earned** ([GROWTH_IS_EARNED_V1]): credit toward climbing does not survive a step.
- **The bitrate belongs to the picture** ([THE_BITRATE_BELONGS_TO_THE_PICTURE_V1]): bitrate and gray come from the
  geometry actually being sent, not from the rung label.
- **The socket knows before the drop** ([THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1]): uplink headroom under 25% of the send
  buffer (UPLINK_TIGHT) is a warning before anything is shed.
- **Playback cushion:** 120 ms by default in a call (`jitter_ms`), the real jitter buffer; distinct from the demo
  jitter slider, which injects latency on the uplink.

## Measured in the C++ build
- **Transcoding holds its budgets on camera-like content** (test_transcode, 96 frames of a moving subject on a room
  gradient, 1280x720 VP8 upload): 640x360 gray 300 kb/s against 300; 160x120 gray 60 kb/s against 60; the 1280x720
  upload 1,354 kb/s against 1,200. With maxrate = bitrate and bufsize = bitrate/2, as fnav sets them.
- **Full-frame noise does not** (HARSH=1): about 2x budget at every size, the upload included -- VP8 runs out of
  quantizer before it reaches the rate. Encoder physics, not a fault in the rate control; no camera produces it.

## Defects found in fnav.py while porting
- **The abort padding does not always end with the sentinel.** send_frame pads a part-written frame with
  `(ABORT_SENTINEL * k)[:remaining]`, aligned from where the write stopped; recv_frame recognises an abort by the LAST
  four bytes. When `remaining` is not a multiple of 4 the tail is e.g. `BE EF DE AD`, the abort is not recognised, and
  a frame of real bytes followed by sentinel fragments goes to the decoder. The stream stays framed; the frame is
  wrong. The C++ sender pads so the tail is always the sentinel ([ABORT_PAD_ENDS_WITH_THE_SENTINEL_V1]); the
  receiver's rule is unchanged, so it interoperates with fnav.py both ways.

## Two conflicts with the requirements -- John's decision needed

1. **Planes -- DECIDED (John, 2026-10-01): two TCP sockets, audio and video, as fnav does.** "This is one of the
   reasons we DERIVE the code from the base": the Communicator derives from the platform's base classes and its
   media transport is its own. Requirement 5 is met within that: the two planes are interleaved over the one link
   the participant has, with video segmented so audio is serviced between the pieces.
2. **Per-viewer rates -- DECIDED (John, 2026-10-01): requirement 3.** "The only reason the download is limited is
   by the slowest link" -- each viewer's own. As John set it in August (NY2 hung call loop): the download slider
   writes MediaSpeed for that viewer, "the media server reads and uses [it] to adjust the download speed for that
   user only. The download speed of other users is not impacted." fnav's relay could only drop frames for a capped
   viewer (a slideshow), so the shedding was fed back and the sender's single rung fell for everyone
   ([SLOWEST_VIEWER_COMMANDS_V1]) -- a workaround this rewrite replaces:
   - **Corrected by John, 2026-10-01:** "The download cannot be faster than the upload. If the upload is 160x120@10fps,
     that's the download. However, if the upload is 1024x720@24fps but the download can only handle 160x120, that
     conversion happens in the media server."
   - The sender uploads ONE stream, at its own upload capability (derived; or its transmit slider).
   - A viewer's rung is the lower of the upload stream's and what that viewer can take: what its own link actually
     drains (measured at the media server) and its download slider (MediaSpeed).
   - A viewer at the upload's rung gets the stream passed through unchanged.
   - For a viewer below it, the media server decodes the upload and encodes the lower rung -- once per distinct rung
     in use, shared by every viewer at that rung, not once per viewer. The media server is therefore no longer
     content-blind for video: it carries decoders and encoders, and its CPU is part of its capability.
   - A viewer that changes rung starts on a keyframe of the new rung's stream.

## The decision index (161 tagged decisions in fnav.py, first line of each)
- line 74 [HEADLESS_PUBLISHER_V1]
- line 115 [DOWNLINK_BACKPRESSURE_V1]
- line 125 [TWO_PLANES_V1]
- line 141 [VIDEO_IS_SEGMENTED_V1]
- line 173 [SEGMENT_ONLY_WHAT_WOULD_NOT_FIT_V1]
- line 183 [KEYFRAME_ON_REQUEST_V1]
- line 200 [SOCKET_WRITE_LOCK_V1]
- line 206 [AUDIO_BACKPRESSURE_V1]
- line 223 [MEDIAHOLD_IS_MEMORY_V1]
- line 231 [WIN_DSHOW_V1]
- line 261 [MEDIA_DEPS_ARE_CHECKED_V1]
- line 321 [KEYFRAME_FLAG_V1]
- line 362 [ABORT_SENTINEL_V1]
- line 383 [WHOLE_FRAME_SEND_V1]
- line 392 [ALL_NONBLOCKING_V1]
- line 400 [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1]
- line 435 [SAY_WHY_IT_ABORTED_V1]
- line 482 [THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1]
- line 533 [THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]
- line 587 [RUNG_IS_MEASURED_V1]
- line 602 [KEYFRAME_SCALES_BY_PIXELS_V1]
- line 698 [VIDEO_FLOOR_IS_MEASURED_V1]
- line 729 [ASPECT_IS_CHOSEN_V1]
- line 730 [CONSTRAINED_GOES_4_3_V1]
- line 735 [PROBE_IS_GRADED_ON_FRAMES_V1]
- line 744 [FLOOR_IS_PROVEN_NOT_GLIMPSED_V1]
- line 781 [BOTTOM_RUNG_SHRINKS_V1]
- line 906 [NONBLOCKING_SEND_V1]
- line 931 [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]
- line 934 [THE_BUFFER_IS_THE_LATENCY_V1]
- line 976 [SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1]
- line 984 [AUDIO_FIRST_V1]
- line 997 [AUDIO_INTENT_IS_SHARED_V1]
- line 1020 [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]
- line 1038 [A_FRAME_IS_ONE_SHED_V1]
- line 1147 [NO_FALLBACK_V1]
- line 1214 [SNDBUF_IS_DOUBLED_V1]
- line 1238 [AUDIO_RETRIES_V1]
- line 1318 [PARTIAL_WRITE_MUST_COMPLETE_V1]
- line 1413 [RECEIVE_IS_MEASURED_V1]
- line 1424 [WINDOW_KNOWS_ITS_RUNG_V1]
- line 1560 [RX_SAYS_WHERE_V1]
- line 1599 [AUDIO_SECONDS_NOT_FRAMES_V1]
- line 1650 [SHUTDOWN_NOT_CLOSE_V1]
- line 1690 [MEDIASPEED_V1]
- line 1701 [DELIVERED_RATE_FEEDBACK_V1]
- line 1708 [KEYREQ_NEEDS_ROOM_V1]
- line 1729 [KEYFRAME_ANCHOR_V1]
- line 1739 [KEYFRAME_BACKLOG_V1]
- line 1757 [TEARDOWN_IS_PER_SESSION_V1]
- line 1761 [FAN_IS_PER_SESSION_V1]
- line 1765 [DROP_THE_FRAME_NOT_THE_CALLER_V1]
- line 1771 [EWOULDBLOCK_FIRST_V1]
- line 1779 [KEYFRAME_SURVIVES_EWOULDBLOCK_V1]
- line 1783 [A_HOLD_IS_NOT_FOREVER_V1]
- line 1798 [AUDIO_IS_ACCOUNTED_SEPARATELY_V1]
- line 1817 [VIDEO_IN_CARRIES_THE_RUNG_V1]
- line 1847 [MEDIASPEED_FRESHEST_V1]
- line 1876 [RELAY_REAPS_DEAD_CALLS_V1]
- line 2455 [RELAY_WIRE_LOG_V1]
- line 2553 [PENDING_KEY_LEAK_V1]
- line 2637 [WIRE_LOG_V1]
- line 2731 [AUDIO_DRIVES_THE_RESOLUTION_V1]
- line 2762 [WIRE_ACCOUNTING_IS_BOTH_PLANES_V1]
- line 2773 [ALONE_IS_NOT_SILENT_V1]
- line 2974 [AIM_IS_COUNTED_ONCE_V1]
- line 3271 [RATE_IS_MEASURED_AT_THIS_RUNG_V1]
- line 3280 [STANDARD_SIZES_WIDESCREEN_V1]
- line 3334 [SLOWEST_VIEWER_COMMANDS_V1]
- line 3385 [AUDIO_IS_OPUS_V1]
- line 3444 [NO_NDARRAY_IN_THE_HOT_PATH_V1]
- line 3479 [LIVE_TAP_V1]
- line 3572 [VBV_OR_THE_BUDGET_IS_A_WISH_V1]
- line 3627 [KEYFRAME_INTERVAL_IS_BOUNDED_V1]
- line 3653 [GEOMETRY_IS_NOT_A_RUNG_V1]
- line 3711 [SMALLEST_WIRE_WINS_V1]
- line 3724 [TILE_TAKES_WHAT_IT_IS_GIVEN_V1]
- line 3738 [ENCODED_SIZE_IS_MEASURED_V1]
- line 3786 [STALE_TILE_V1]
- line 3792 [VDEC_DIAG_V1]
- line 3863 [BEARER_DIAG_OPT_IN_V1]
- line 3888 [LADDER_RATE_V2]
- line 3924 [PROBE_BACKOFF_V1]
- line 4101 [PRODUCER_LEADS_CONSUMERS_REPORT_V1]
- line 4138 [THE_SOCKET_KNOWS_BEFORE_THE_DROP_V1]
- line 4159 [SENDER_READS_MEDIASPEED_V1]
- line 4186 [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1]
- line 4191 [RX_IS_PER_SOURCE_V1]
- line 4206 [RECEIVE_RATE_IS_LINK_EVIDENCE_V1]
- line 4217 [THE_MIC_IS_NOT_THE_LINK_V1]
- line 4223 [BACKING_DOWN_IS_BOTH_ENDS_V1]
- line 4244 [CEILING_IS_A_MINIMUM_V1]
- line 4261 [PROBE_BACKS_OFF_V1]
- line 4294 [QUALITY_CAP_V1]
- line 4480 [A_HANG_IS_THE_WORST_REPORT_V1]
- line 4513 [MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1]
- line 4563 [ABSENCE_IS_NOT_A_MEASUREMENT_V1]
- line 4572 [JUDGE_AGAINST_SOMETHING_V1]
- line 4588 [SAY_WHICH_GATE_STOPPED_THE_REPORT_V1]
- line 4607 [THE_FLAG_MEANS_STABLE_V1]
- line 4615 [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1]
- line 4638 [SETTLE_BEFORE_YOU_CLIMB_V1]
- line 4701 [A_STEP_NEEDS_TIME_TO_BE_FELT_V1]
- line 4705 [ONE_STEP_PER_SIZE_V1]
- line 4724 [EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1]
- line 4746 [A_SIZE_THAT_FAILED_STAYS_FAILED_V1]
- line 4855 [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1]
- line 4867 [A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1]
- line 4875 [ONE_DERIVATION_V1]
- line 4877 [NO_SYNC_JUST_STATE_V1]
- line 4878 [ONE_RATE_FOR_THE_NETWORK_V1]
- line 4880 [THE_RATE_IS_A_FUNCTION_OF_THE_ROWS_V1]
- line 4883 [A_PRODUCER_S_RATE_BOUNDS_THE_CALL_V1]
- line 4885 [SAY_WHAT_YOU_ARE_SENDING_UNCONDITIONALLY_V1]
- line 4922 [A_SENDER_IS_NOT_A_CONSUMER_OF_ITSELF_V1]
- line 5000 [A_SHRINKING_READ_IS_NOT_NEWS_V1]
- line 5073 [A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1]
- line 5100 [MEDIACONTROL_BELONGS_TO_THE_CALL_V1]
- line 5119 [THE_PIPE_IS_SHARED_V1]
- line 5306 [SELF_CAP_FROM_LINK_V1]
- line 5363 [CAP_LIFT_ANNOUNCES_ITSELF_V1]
- line 5415 [BACKLOG_IS_A_REPORT_NOT_A_LATCH_V1]
- line 5547 [SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1]
- line 5592 [GROWTH_IS_EARNED_V1]
- line 5648 [THE_BITRATE_BELONGS_TO_THE_PICTURE_V1]
- line 5672 [GRAY_FOLLOWS_THE_PICTURE_V1]
- line 5715 [THE_SENDER_SENDS_AT_THE_RECEIVE_RATE_V1]
- line 5733 [SEND_AT_WHAT_YOU_RECEIVE_V1]
- line 5792 [THE_CONTRACT_IS_FPS_AND_RESOLUTION_V1]
- line 5996 [ONE_STEP_PER_HOLD_V1]
- line 6060 [BACKLOG_CLEAR_IS_THE_ABSENCE_OF_A_REPORT_V1]
- line 6132 [THE_RECEIVERS_DECIDE_V1]
- line 6299 [VIDEO_DOES_NOT_NEED_A_MIC_V1]
- line 6307 [LADDER_CONSECUTIVE_V1]
- line 6346 [REFUSED_AND_TIMED_OUT_ARE_DIFFERENT_FAULTS_V1]
- line 6485 [RECV_VIDEO_NOT_GATED_ON_TX_V1]
- line 6531 [HANG_UP_IS_NOT_A_FAULT_V1]
- line 6632 [AUDIO_ERROR_NAMES_THE_CAUSE_V1]
- line 6674 [AUDIO_DEGRADES_TO_VIDEO_V1]
- line 6683 [AUDIO_DROP_IS_LOUD_V1]
- line 6759 [MIXER_DAMAGE_IS_COUNTED_V1]
- line 6775 [TAP_THE_THING_THAT_PLAYS_V1]
- line 6788 [QUIET_MODE_V1]
- line 6870 [CAPTURE_DOES_NOT_NEED_A_SPEAKER_V1]
- line 6929 [ASK_THE_CAMERA_FOR_MJPEG_V1]
- line 6971 [A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]
- line 7002 [A_FAILED_READ_IS_NOT_NOTHING_V1]
- line 7028 [SOURCE_CANNOT_SUSTAIN_V1]
- line 7030 [REASON_IS_FOR_A_CHANGE_V1]
- line 7040 [NO_CATCH_UP_BURST_V1]
- line 7115 [THE_CONSUMER_OWNS_THE_RATE_V1]
- line 7140 [READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1]
- line 7245 [THE_RATE_IS_COMMANDED_NOT_NEGOTIATED_V1]
- line 7281 [THE_CLIMB_BACK_IS_A_CLIMB_V1]
- line 7399 [WALK_THE_KEYFRAME_DOWN_UNTIL_IT_FITS_V1]
- line 7415 [SHRINKING_A_BLOCKED_SOCKET_IS_NOT_A_FIX_V1]
- line 7682 [DROP_THE_FRAME_NOT_THE_CALL_V1]
- line 7793 [AUDIO_TAP_V1]
- line 7925 [NO_DISPLAY_IS_NOT_NO_DECODER_V1]
- line 8116 [VERBOSE_V1]
- line 8223 [MEDIASPEED_CLI_V1]
