# Communicator for Android r5 (comms-android-2026-10-11-r5): one Communicator, inside the FrogNet app

Two lines grew from r1 on 2026-10-10/11, and neither had the other's changes:

- **r4**, this app's Communicator (Android thread): `MainActivity` became `CommunicatorActivity`. Its servers come from
  the pond, from the intent the FrogNet member opens it with, with no typed defaults (`[COMMS_FROM_THE_POND_V1]`).
  It also gained the here-clock, rings only while offered, and logs which camera it opened and which way it faces
  (`[CAMERA_SAYS_WHICH_V1]`, CameraGl).
- **r2**, the standalone `ribbit/examples/communicator/android` (Communicator thread): the call screen. The far end
  fills the display (status and navigation bars hidden; a swipe shows them). Your camera is a bordered inset you drag
  to any corner (remembered); tapping it switches cameras. Tapping the picture brings back the controls, which fade
  after 4 s, and the controls now include **Stats**. Double-tap switches between filling the screen and showing the
  whole picture (remembered), and a long press shows the next person in a group call. `RemoteVideo` gained the fill
  mode.

r5 is r2's call screen on r4:

| file | from |
|---|---|
| `CommunicatorActivity.java` | 3-way merge (base r1 `MainActivity`, ours r4, theirs r2); no conflicts |
| `RemoteVideo.java` | r2 (r4 had not changed it) |
| `CameraGl.java` | r4 (r2 had not changed it) |
| `Diag.java` | r4 with BUILD `comms-android-2026-10-11-r5` |
| `core/*` | unchanged (neither line changed it) |
| `communicator-test/robo/AppFlowTest.java` | r2's change: the stats panel is opened with the **Stats** button (a tap on the picture now toggles the controls) |

Checked: the whole `net.frognet.communicator` package compiles against the API 33 framework (android-all 13,
`javac --release 8`), as r4 does. Not run here: the app build (Android Studio: AGP needs Google's Maven), the
Robolectric AppFlowTest, and the phone.

The standalone `ribbit/examples/communicator/android` is superseded by this one and is removed from the repo. One
Communicator on Android, opened by the FrogNet member with its pond's servers.
