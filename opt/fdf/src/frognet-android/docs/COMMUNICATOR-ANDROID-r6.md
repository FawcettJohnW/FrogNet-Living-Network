# Communicator for Android r6 (comms-android-2026-11-25-r6): calls under the protocol layer's frame, a Security tab

John, 2026-11-25: "I need the Android code updated so it will run." "On the UI, add a new tab named Security. On that
tab, add the options for the cipher, key, protocol, and a button to change the RAM host."

r5 runs today in a call that is not sealed (its own tests pass against the current servers). In a sealed call the
media server closes its planes, because r5 has no frame. r6 has it. The whole design is in the Communicator's
`docs/CHANNEL-DESIGN-AND-IMPLEMENTATION.md` (in `opt/frognet_semantic/ribbit/examples/communicator`); this file is
the phone's part.

## What changed

| file | change |
|---|---|
| `core/ChannelFrame.java` (new) | the protocol layer's frame in Java, from `ribbit/cpp/src/channel_frame.cpp`: the Role's protocol (tcp, chorus, openqasm), the key computed from the Role's secret, AES-256-GCM and ChaCha20-Poly1305, the switch in the stream, NOT OPENED and AGAIN, `undo`, the setting's check, a Role's own secret moved forward. `javax.crypto` only; no native code |
| `core/Channel.java` (new) | the app's channel: the read it holds on `(channel, setting, mediahost)`, the check, its report `(channel, at, mediahost:<end>)`, and the owner's work (`show`, `change`) that the Security tab calls |
| `core/RamAt.java`, `core/Tuples.java` (new) | where the Communicator's memory is and who is told when it moves; the tuples API for the channel service |
| `core/Plane.java` | one writer thread per plane and no lock around the socket ([ONE_WRITER_PER_PLANE_V1]); `setChannel` puts the frame under every body; a unit under the frame ends in one byte, 0 ([A_WHOLE_UNIT_SAYS_SO_V1]); abort padding never overwrites a unit's length ([THE_LENGTH_IS_NEVER_PADDED_V1]); a plane whose writer fails is closed ([A_BROKEN_PLANE_IS_CLOSED_V1]) |
| `core/Room.java` | `attach()`: when the memory moves, the room ends its sessions, attaches to the new address, asserts presence, calls and its download cap there, and holds its read again ([THE_MEMORY_MOVES_V1]) |
| `core/CallCore.java` | a call's two planes are put under the frame when the app has a framed channel |
| `CommunicatorActivity.java` | the lobby has two tabs, **Room** and **Security**; the channel's read is made before the room is joined; the screen follows the memory's address; the Log screen says what the channel holds |
| `Diag.java` | BUILD `comms-android-2026-11-25-r6` |
| `communicator-test/` | `FrameProbe.java` and `test_android_frame.py` (the Java frame against the C++ library); `Probe.java` gained `watchlog` and `owner`; `test_android_secure.py` (the core in a sealed call); `run_tests.sh` runs them as steps 3 and 4 |

Nothing in the member (`app/src/main/kotlin`, `member/`, `member-cli/`) changed. The kart app compiles this
Communicator in and makes no channel, so its calls are as they were; `GameCalls.java` still compiles against r6's core.

## The Security tab

- **What it shows:** the Role (`mediahost`), the cipher, which secret the key is computed from (8 characters of a
  hash, never the secret or a key), the protocol, the RAM host, what this phone sends as, and every end with what it
  is running.
- **Cipher:** Not sealed, AES-256-GCM, ChaCha20-Poly1305. **Key:** New key, and Import secret…. **Protocol:** tcp,
  chorus, openqasm. **RAM host:** type `host:port` of another comms-ram that is running, then Change RAM host.
- Each change is one write of the Role's setting, with a check made with this phone's secret, and its answer is shown:
  for example "a new key: 3 end(s) there: 3 running it (14 ms after the write)".
- **Import secret…** opens the system's file chooser. A file of 32 bytes to 64 KiB becomes this phone's default secret
  (`files/channel_secrets/default` in the app's private storage). It must be the same file the other machines hold.
- **This phone speaks the frame** is on unless turned off there. Turn it off only for a media server older than
  2026-11-24, which does not speak the frame.
- ChaCha20-Poly1305 needs Android 9 (API 28). On an older phone the tab says so, and a Role sealed with it does not
  open there.

## What was run (2026-11-25, on the JVM: the same core classes the app runs)

`communicator-test/run_tests.sh COMMS_DIR`:

| step | result |
|---|---|
| 1 vectors | Java equals Python on 27,095 decisions (as r5) |
| 2 core | the core against comms-ram, comms-media and the C++ and Python Communicators, not sealed: pass (as r5), on the one-writer plane |
| 3 frame | the Java frame against the C++ library, both directions and Java at both ends: 32 checks pass |
| 4 secure | the core in a sealed call: see below |

Step 4, the phone watching the C++ feed's 720p video and 440 Hz tone, two runs:

| | video frames/s | audio blocks/s | longest time with no new frame | with no new audio block |
|---|---|---|---|---|
| 6 s, nothing changed | 23.9 to 24.0 | 50.1 to 50.2 | 61 to 73 ms | 31 to 34 ms |
| 14 s, nine switches made by the phone's owner code | 23.9 | 49.9 to 50.0 | 73 to 82 ms | 34 to 41 ms |
| 7 s, the phone's owner code moves the memory, then the old one is killed | 23.8 to 23.9 | 50.0 to 50.1 | 84 to 85 ms | 31 to 35 ms |

A phone with another secret got no frame and no block. A phone that does not speak the frame had its planes closed.
The phone as a feed, under the frame and sealed, was watched by the C++ viewer: 119 frames in 5 s.

The whole `net.frognet.communicator` package compiles against the Android 14 framework (android-all 14,
`javac --release 8`): 93 classes.

## Not run

- **The app build and the phone.** No APK was built (no Android SDK here), and nothing ran on a phone or an emulator.
  The Security screen has not been seen on a device. The first run on a phone proves `javax.crypto`'s AES-GCM and
  ChaCha20-Poly1305 there, the file chooser, and the screen's layout.
- The Robolectric app flow (step 5), as before.
