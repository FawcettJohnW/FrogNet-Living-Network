# The FrogNet Communicator for Android

The Communicator on an Android phone: the same room, lobby, calls, chat, audio and video as the desktop Communicator
(`comms-app`), through the same Communicator memory (`comms-ram`, port 8800) and media server (`comms-media`, port
8994). Nothing on the servers changes, and nothing outside this directory changes. Build `comms-android-2026-10-10-r1`
(shown on the Settings screen, and as the first line of the app's Log).

## Install

`FrogNetCommunicator.apk` — Android 10 or later (minSdk 29, targetSdk 33). It is signed with this directory's own key
(`tools/frognet.p12`, password `frognet`); later builds signed with the same key install over it.

- On the phone: open the APK (from the Claude app's download, Files, or Drive), allow "Install unknown apps" for
  whatever opened it when Android asks, Install.
- Or over USB: `adb install -r FrogNetCommunicator.apk`.

## Use

1. First start: your name, the Communicator memory (default `165.232.153.144:8800`, StreamingFrog) and the media
   server (default `165.232.153.144:8994`). **Join the room.** Settings changes them later.
2. The lobby lists everyone in the room: **Call** a person, **Watch** an unattended feed (`*name`).
3. When someone calls you the phone rings: **Answer** or **Decline**. A call rings only while its caller is in the room
   and for at most 60 s.
4. In a call: the other side's picture fills the screen (aspect kept); your camera in the corner (tap it to switch
   cameras). **Mute**, **Camera off/on**, **Speaker/Earpiece**, **Chat**, **End**. Tap the picture for the stats panel:
   what is received and sent (size, fps, kb/s, whether the media server passes your picture through or made a smaller
   one for you), audio packets each way, and the **Link** slider — the same one number as the desktop's bandwidth
   slider (32 kb/s .. 4 Mb/s, unlimited at the right), applied to both directions: your upload is paced to it and it is
   written as your MediaSpeed so the media server serves you at it.
5. **Log** (lobby, or the stats panel): everything the app did, newest first, with the call's counters — no adb needed.

The other end needs nothing new: the desktop `comms-app --ram 165.232.153.144:8800 --media 165.232.153.144:8994
--name <name>` (or the kart demo's Call button) sees the phone in its lobby and calls it or is called by it.
StreamingFrog must be running the Communicator services (`fdf_service start` — comms 8800 and 8994), with TCP 8800
and 8994 open.

## What it does on the wire

- **The room** (`core/RamSession`, `core/Room`): FNW1 to `comms-ram` — request and return sockets, `REQ_RAW` /
  `RESP_RAW`, replies matched by sequence — the contract `ram_host.cpp` answers for a client that opens no data
  socket. The rows are the C++ and Python rooms' rows exactly: presence (own), chat (lossless), CallsAnnounce /
  CallsJoined / CallsInvitations keyed to the participant, MediaSpeed, MediaHold; one held read keeps the copy
  ([HOLD_THE_READ_V1]); own rows are removed when the app leaves. A lost session is rejoined: retried every 3 s and
  logged, nothing substituted.
- **The call** (`core/CallCore`, `core/Plane`, `core/Fnav`, `core/Bearer`): two TCP planes to `comms-media` (video,
  audio) opened with the PLANE frame (plane, one 8-byte teardown tag, session); fnav frames, VSEG segmentation at the
  20 ms ceiling, whole-frame send-or-drop with the abort sentinel padding, the bearer (John's rung rule) and the bottom
  walk deciding the size sent. The phone's ceiling is L7, 1280x720.
- **Video** (`CameraGl`, `VideoEncoder`, `RemoteVideo`): Camera2 into GL, drawn upright for how the phone is held into
  a VP8 encoder (MediaCodec) at the ladder size the bearer chose — always a landscape ladder size, because the media
  server scales uploads to the ladder's fixed landscape sizes: held sideways the picture fills it, held upright it is
  pillarboxed, never stretched. The front camera is un-mirrored for the far end. Bottom sizes are gray. A KEYREQ is
  answered with a keyframe at once. Received VP8 is decoded by MediaCodec straight to the screen; a keyframe of a new
  size starts a new decoder.
- **Audio** (`AudioEngine`): VOICE_COMMUNICATION capture at 16 kHz with the phone's echo canceller and noise suppressor
  -> Opus 24 kb/s (MediaCodec) in 20 ms packets; per-source Opus decoders -> 16 kHz -> the fnphone mixer (120 ms
  cushion, trims and pads counted) -> the voice-call stream.

## Proven (this build, `test/run_tests.sh`; logs in `test/results/`)

| test | what | result |
|---|---|---|
| vectors | the Java bearer, abort pad and reassembler against the Python ones (which equal fnav.py) | 27,095 of 27,095 decisions equal |
| core A | phone + C++ + Python in one comms-ram: lobby, chat, invitations both ways; the phone's presence gone from the memory when it leaves; close 0.006 s | PASS |
| core B | the phone watches the C++ comms-feed | 143 frames / 6 s, keyframe 1280x720; 300 Opus packets, libopus decodes the 440 Hz tone at 440.0 Hz |
| core C | the C++ viewer and a Python viewer watch the phone | C++: 1280x720, 119 frames / 5 s; Python: 1280x720, 96 / 4 s, tone 660.5 Hz |
| core D | the phone calls a Python participant | both ways 1280x720 passthrough (MediaHold aimed 106 / delivered 106), 440 and 660 Hz, chat both ways |
| core E | the C++ window calls the phone, the phone answers | screenshot `test/results/cpp-window-calling-phone.png`: the phone's 720p at 24.0 fps, shed 0 |
| app | the app's MainActivity on the Android framework's own code (Robolectric, SDK 33) against the live servers and a Python "Home": joins, lists Home, rings, Answer, Home's media arrives (50 audio packets/s, video passed through), chat both ways, End -> "online"; then the phone presses Call: Home is invited, joins, the offer is withdrawn when the phone hangs up | PASS |

Found while proving it, and fixed:
- A sender that ignores KEYREQ leaves each new viewer unanchored; the media server scored the viewer at ~44 % delivered
  and transcoded it down to 854x480. Answering KEYREQ: 1280x720 passthrough, 119 frames / 5 s.
- The microphone path assumed an encoder input buffer holds a whole 20 ms block (BufferOverflowException under the
  framework); PCM is now fed in as many buffers as the codec's size takes, and both decoders refuse an oversize packet
  whole and log it.
- An independent review of the device-only code (camera, GL, codecs, audio routing, lifecycle) found 12 issues; 11 are
  fixed (a hidden self-view could stall the camera thread; the camera was not reopened after the app returned to the
  foreground; a direct 90 -> 270 degree turn was missed; TextureView surfaces were released while still drawn into;
  an exception on the camera thread could end the app; hang-up blocked the UI thread; and smaller ones). The twelfth
  — whether the phone's software VP8 encoder holds 720p at 24 fps — is a per-phone measurement: the Log shows camera
  frames vs encoded frames, and the bearer steps the size down by itself if it cannot.

## Not proven here

There is no Android device or emulator in the build environment. Camera2, GL, the MediaCodec VP8/Opus codecs and audio
routing are the parts that have not run on a phone; the review above checked them against the API contracts. The first
run on your phone is their first run: the Log screen says what each did (camera size and fps range, encoder name and
mode, decoder, audio devices and effects).

Known limits: the call is for the app in the foreground (Android takes the camera and silences the microphone of an
app in the background; the camera is reopened when the app returns). Java cannot read the kernel's send-queue size
(SIOCOUTQ), so — as on Windows — the socket's "would block" decides and the send buffer is 64 KiB.

## Build

No Android SDK needed (Google's repositories were not reachable when this was built). `./build_apk.sh` fetches from
Maven Central `org.robolectric:android-all` (the API 33 framework, to compile against), `dalvik-dx` (dex) and `apksig`
(v2 signing and its verifier); `tools/apkres.py` writes the binary manifest and resources.arsc; `tools/make_icon.py`
draws the icon. Output `build/FrogNetCommunicator.apk`, signed and verified. Java 8 language level; JDK 17+ to build.

## r3/r4 in app a8/a9 (2026-10-10): on hardware; ringing; the "here" clock; which camera

- **Hardware:** John, on a physical phone: "responsive and worked properly". The camera, the codecs and the audio
  path had not run on hardware before this.
- **Ringing** (`[RINGS_ONLY_WHILE_OFFERED_V1]`, CommunicatorActivity.onTick + offered()): an invitation rings only while
  it is <= 60 s old AND the caller still offers that session in CallsAnnounce (Room.offers()). It is re-checked every
  tick (500 ms): a ring whose caller hung up or went away stops by itself ("<name> is no longer calling" in the log) and
  that session never rings again. Before, once ringing it rang until Answer or Decline. The roster-presence check is
  replaced by the offer check (a caller still in the lobby who hung up offers nothing). The desktop app has the same
  rule (Room::ringing), and its hang-up now withdraws its offer (`[LEAVE_WITHDRAWS_THE_OFFER_V1]`).
- **"here" clock** (`[STAMP_THE_PICTURE_V1]`): a monospace line under the caller's name, this phone's clock to the
  millisecond, 30 Hz while in a call (clockTick). A desktop sender burns `#frame HH:MM:SS.mmm` into the picture's
  bottom-left; lag = here - stamped. The phone's own sender does not stamp.
- Build: Diag.BUILD `comms-android-2026-10-10-r4` (r3 + the camera line), in the member app `fdf-kt-2026-10-10-a9`.
- Proof here: the Android suite against comms-ram + comms-media + the C++ and Python Communicators -- vectors (27,095
  decisions equal), the Java core, the Robolectric app flow (called and calling, chat, End -> online, offer withdrawn):
  PASS. Desktop: test_ring PASS 9/9.
- Open: the emulator call's start-up lag (no cause found; measure with the stamp if it recurs); the emulator's picture
  "reversed" (un-mirroring applies only to a camera reporting LENS_FACING_FRONT). r4 says which, on opening:
  `[camera] opened camera <id>: facing front|back|external, sensor N deg, WxH; un-mirrored for the far end | sent as
  the camera gives it` (`[CAMERA_SAYS_WHICH_V1]`). Suite rerun on r4: PASS.
