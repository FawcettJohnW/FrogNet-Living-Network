# The FrogNet Communicator -- specification for the rewrite (2026-10-01)

A new implementation, replacing everything that exists. C++ first, then Python, both built to this document and
interoperating on the wire and in the memory. A flagship for the Comcast contacts: it must look good and it must work.

## Requirements (John, 2026-10-01)

1. **Unattended feed.** A remote machine runs a feed with no UI at all: started from the command line or a service,
   it captures camera and microphone and publishes them until stopped.
2. **Lobby.** A waiting room lists the other people in the room and offers to open a chat with any of them.
3. **Live video in a chat, through a media server.** When a chat is running, a live video channel runs to the other
   side through a media server. The media server sets each viewer's output rate individually, from that viewer's
   download capability. A publisher's upload capability is derived, not configured, and changes as bandwidth changes.
4. **Low-speed operation.** The effective bandwidth of the video stream(s) can be adjusted so that it works even at
   low speeds.
5. **Audio and video interleaved** over the TCP link.
6. **No UDP.** Every byte travels over TCP.

## Architecture

**State is tuples in the Communicator's region; streams go through the media server.**

| part | role |
|---|---|
| `comms-ram` | the region (a `ribbit::RamHost`, vendor `comms`): presence, chat, call state, the media server's address, each participant's measured capabilities |
| `libcomms` | the client library (C++, C ABI): presence, lobby, chat, calls, media session -- one implementation, used by every front end |
| `comms-feed` | requirement 1: headless publisher. Presence as a feed; capture, encode, publish; no UI |
| `comms-media` | requirement 3: the media server. Publishes its address as a tuple; receives each publisher's stream; serves each viewer at its own rate |
| the app | lobby, chat, call view: one UI over `libcomms` |

**Presence and lobby (2).** Each participant writes its own presence cell (name, kind: person or feed, status);
the lobby is a held read of the room's presence group. No server tells anyone who is here.

**Chat (2).** Per pair, each side writes its own messages; each reads the other's. Opening a chat is writing a call
request the other side reads; accepting is its answer. All state, no signalling protocol.

**Media (3, 4, 5, 6).** Two TCP connections per participant to `comms-media`, one for audio and one for video, as
fnav does (decided 2026-10-01: the Communicator derives from the platform's base classes, and its transport is its
own). Both share the participant's one link, interleaved:
- **Framing:** every unit on the connection is a small frame -- kind (audio, video segment, control), stream,
  sequence, timestamp, length. Video frames are cut into segments so an audio frame never waits behind a whole
  video frame; audio is always sent first when both are ready.
- **Rate ladder (4):** each published stream is encoded at a set of rungs (resolution, frame rate, quality),
  down to rungs usable at very low speeds, with audio-only as the floor. A viewer can also cap its own rung.
- **Per-viewer streams (3):** the sender uploads one stream at its own upload capability; no download is faster than
  that upload. A viewer whose download can take it gets it passed through; for a viewer that cannot, the media server
  decodes it and re-encodes the lower rung -- once per distinct rung, shared by every viewer at that rung. One slow
  viewer never lowers anyone else.
- **Download capability (3):** the media server measures, per viewer, how fast that viewer's connection actually
  drains (bytes leaving the send buffer per second, and when the buffer backs up), and chooses that viewer's rung
  from it, with hold-down so it does not oscillate.
- **Upload capability (3):** derived the same way at the publisher's end, from how fast its connection to the media
  server drains, and published as a tuple; the publisher drops rungs it cannot sustain.
- **No UDP (6):** no RTP, no WebRTC transport, no UDP anywhere.

## Decisions (John, 2026-10-01)

1. **UI: Qt (Qt Quick).** It reaches Android and iOS later. Licence to settle before release: open-source Qt is
   LGPLv3/GPLv3 and the repo is GPL-2.0-only (commercial Qt, or the Communicator under a compatible licence).
2. **Platforms: Linux and Windows today; Android and iOS later.** The core (state, calls, the fnav wire, the ladder,
   rate derivation) is portable C++. Capture, playback and hardware encoding sit in a thin per-platform layer:
   V4L2 and ALSA/PulseAudio (Linux), Media Foundation and WASAPI (Windows); later Camera2 (Android) and
   AVFoundation (iOS). The unattended feed (requirement 1) is a Linux/Windows role: iOS does not allow unattended
   background capture with open sockets.
3. **Media: fnav.** "We did fnav for a reason." The C++ media path implements fnav -- its wire, its segmentation
   (KIND_VSEG), its rungs and its rate derivation, with the August adaptive-bitrate lessons. `fnav.py` is the
   specification for that layer and is read completely, codecs included, before any media code is written.

## The tuple contract (service `communicator`, read from comms_control.py and frognet_tuples.py, 2026-10-01)
Both implementations read and write exactly these, so a C++ and a Python participant meet in one lobby and one call.
| var | scope | value | written by |
|---|---|---|---|
| presence | `host:<ip>:presence:<me_id>` | name, status, caps, ts | each participant, on a heartbeat; stale = gone, no delete |
| chat | `session:chat:<session>:<msg_id>` | from_id, from_name, text, ts (ms) | the sender; one row per line, lossless |
| MediaSpeed | `session:<session>:viewer:<me_id>` | session, viewer, bps (0 = no cap), addr, ts | each viewer; the media server applies it to that viewer only |
| call | `session:<session>` | the call's own row | the originator (the headless feed originates its own) |
| MediaHold | `host:<ip>` | per viewer: unanchored, held keyframes, inters shed, cap, frames aimed/delivered | the media server, every 2 s |
`me_id` is the name plus a short random suffix per launch (fnav: four hex digits); `ip` is the address the tuples record
for this machine. **The media server matches a MediaSpeed row to a viewer by identity, never by address** (changed from
fnav's relay, 2026-10-01): its name, or fnav's name + four hex digits. Matched by address, every viewer behind one
address (NAT, an office, a test on one machine) shared one cap -- measured: one viewer's 120 kb/s capped all three.

## The main window (mock approved by John, 2026-10-01)

One window, three columns:
- **Left, the lobby:** people in the room with their status (in a call, available, away) and a chat button; unattended
  feeds in their own group with a Watch button.
- **Centre, the call:**
  - The remote video, with a live stats overlay: rung (n of 6), resolution, frame rate, video and audio bitrate,
    measured jitter against the jitter buffer, A/V skew, dropped frames.
  - Self-view in the corner, showing the derived upload rate.
  - Controls: microphone, camera, stats panel, hang up.
  - **Two sliders -- demo link conditions, as fnav has them (corrected 2026-10-01 from fnav.py):**
    - Bandwidth: this participant's link, ONE number applied to BOTH directions (John, 2026-10-01: "Things are
      not asymmetric in the real world"; as fnav's MediaSpeed, "applied to BOTH legs because a client's link is
      symmetric"). Upload: paced to it (fnav `set_throttle`), and the ladder discovers the smaller wire. Download:
      written as this viewer's MediaSpeed, and the media server serves this viewer at it. Unlimited = 0.
    - Jitter (fnav `set_jitter`): injects up to N ms of random latency per frame on the uplink.
  - A stats strip: your upload, the other side's download (as the media server measures it), round trip, audio health.
- **Right, the chat** with the person in the call.

The stats button opens a stats panel with the same values plotted over time.

## Proof

- An oracle per requirement, red before and green after, in the platform's style.
- Low speed and changing bandwidth on a shaped link (TCP only), with the rung each viewer gets and the audio's
  continuity measured, not assumed.
- Calls in every mix once Python exists: C++ to C++, C++ to Python, Python to Python.
- Live calls on the real machines (Seattle, New York, the 900 MHz link) before it is shown.

## What to read first in the new chat

- This document.
- `fnav.py` (8,240 lines), completely: it is the media layer's specification.
- "Communicator stream display issue" (June): the proven lineage -- comms_control, comms_server, fnav as the
  relay -- and the adaptive-bitrate and audio lessons (in /areas/frognet-communicator memory).
- "Reading handoff documentation": `comms-ram`, `RamHost`, `TuplesClient`, the platform.
- `opt/frognet_semantic/ribbit/examples/communicator` in the repo: what exists today, for its lessons, not its code.
