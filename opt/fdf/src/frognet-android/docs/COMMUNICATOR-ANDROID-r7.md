# Communicator for Android r7 (comms-android-2026-11-26-r7): the channel is switched from the call

John, 2026-11-26: "I want the communicator to demonstrate the full scale of the adaptive system. I need to be able to
see who is there, to call them, to answer the call, stay in the call, and hang up, then do it all over again. While
it is running I want to be able to switch ciphers, keys, protocols, etc. From the app in real time. The perf overlay
should show all." The desktop windows got this in `comms-app-2026-11-26-r5`; r7 is the phone's.

## What the phone does now

- **Channel**, a new button on the call screen, opens a panel over the bottom of the picture: **Cipher** (Not sealed,
  AES-256-GCM, ChaCha20), **Protocol** (tcp, chorus, openqasm), **New key**, and a `host:port` box with **Move** for
  the RAM host. A tap is one write of the Role's setting, the same write the Security tab makes
  (`core/Channel.java change`); the answer from every end appears under the buttons ("3 end(s) there: 3 running it").
  The call goes on while it happens. Opening Channel opens the Stats too, so each change is seen taking effect.
- **Stats** is the phone's overlay and shows all of it:
  - the call: who it is with, which call of this run (`call #2`), how long, what is received and sent (size, fps,
    kb/s, the rung and its ceiling, what the media server is doing for this phone), audio in and out, what was shed,
    key requests, the link's cap;
  - the channel: the Role, the protocol, the cipher, 8 characters of a hash of the key this phone sends under (never
    the key), where the memory is, kB sent and received under the frame, switch calls sent and received, frames sent
    again and frames that did not open at first, and the last change with its clock time and how long ago.
- **A call ends when the other person leaves it** ([THE_CALL_ENDS_WHEN_THE_OTHER_LEAVES_V1]): the phone is back in
  the room by itself and can call or be called again. Before, it stayed in a dead call.
- The call screen's buttons are in two rows: Chat, Stats, Channel; then Mute, Cam, Speaker, End.

The lobby (who is there, Call, Answer, Decline), the Security tab and Import secret are as r6 left them.

## What changed

| file | change |
|---|---|
| `CommunicatorActivity.java` | the Channel panel (`channelPanel`, `channelNow`), the overlay's text (`drawStats`, `channelLines`), the call ending when the other leaves (`onTick`), the call counter, two rows of buttons |
| `core/Channel.java` | `totals()` (what this app's planes have done under the frame, a closed plane's counts kept), `keyTag()`, `lastChange()`; the change's description is worked out where the setting is taken (`read`) |
| `Diag.java` | BUILD `comms-android-2026-11-26-r7` |
| `core/Room.java` | [THE_READ_THAT_IS_ENDED_IS_THE_OLD_ONE_V1] (2026-11-26, with the desktop's r5): `attach` closes the session it was holding, so a second move of the memory cannot leave the room's read dead |
| `communicator-test/robo/AppCycleTest.java`, `test_app_cycle.py` (new) | the activity through the whole cycle, on the Android framework's own code |
| `communicator-test/robo/setup.sh` (new), `run_tests.sh` | step 5 runs now, with no Android SDK: the app's Java is compiled against Robolectric's android-all; setup.sh fetches Robolectric once and compiles androidx.test's monitor from Google's source |

Nothing outside `app/src/main/java/net/frognet/communicator` and `communicator-test/` changed. The member (Kotlin)
is unchanged; `tools/app-typecheck.sh` passes (256 classes against the Android 14 framework).

## Proof

`bash communicator-test/run_tests.sh <communicator dir>`: steps 1 to 5 pass (2026-11-26). Step 5 is the app's own
`CommunicatorActivity`, its real widgets pressed, against two `comms-ram`, `comms-media` and the Python Communicator
("Home"), the Role sealed. `communicator-test/results/test_app_cycle-r7-20261126.txt` is its output:

- the room lists Home; Home's call rings; Answer brings up the call;
- eight changes from the Channel panel during call #1 (ChaCha20, New key, chorus, Not sealed, openqasm, AES-256-GCM,
  tcp, the memory moved to the other comms-ram): each answered by all 3 ends, each shown in the Stats, 308 to 740 ms
  from the tap to both; the key's hash on the screen changed with the key; Home's audio still arrived at 52 packets
  a second afterwards;
- Home hung up and the phone was back in the room by itself;
- the phone called Home (call #2), made two more changes, and hung up; Home saw the offer withdrawn;
- Home took all 12 settings and received the phone's audio under the frame in both calls.

Under Robolectric there is no camera, no codec and no real microphone (its stand-in never waits, so the phone sent
audio thousands of packets a second, under every setting). The picture, the camera and the sound are what a real
phone adds.

## Not done

No APK was built here: there is no Android SDK on this machine. `tools/make_apk.sh OUTDIR` builds it where the SDK
is (Android Studio's). r7 has not run on a phone.
