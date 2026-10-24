# FrogNet Kart for Android  [KART_ANDROID_V1] [KART_HOME_V1] [KART_CALLS_IN_THE_GAME_V1]

A player of the kart service's games, the same game as kartdemo.py: lobby, chat, Ready/Start, laps, races, records.
It never serves a race. Android 10+ (minSdk 29), target 34; rotates. Permissions: camera, microphone, notifications, VPN.

Install: copy `FrogNetKart.apk` to the phone and open it (allow "Install unknown apps" for the app you open it with),
or `adb install -r FrogNetKart.apk`.

Start: Settings (name, kart memory, Communicator) and Connect. The home screen holds the kart memory (the players) and
the Communicator's memory (comms-ram, default the kart memory's host :8800, media :8994) and shows who is here; choose
**Communicator** or **Kart racing**; each opens the other. A race is a third memory, only while it runs: a call goes on
across races, and rings on every screen, racing too. From a call, **Race!** invites the people in it who run FrogNet
Kart [KART_RACE_INVITE_V1]: (kart, invite, YOU) {members, at, go}; each answers Yes/No in its own (kart, answer, NAME)
{invite, at, yes}; your game's held read brings the answers (30 s timeout); all yes, `go`, and each game launches itself
into the lobby, ready, and yours presses Start. kartdemo.py (Windows, Linux) shows the same invitation and answers it.

Run: one app, self-contained [KART_IS_A_MEMBER_V1]. It is a FrogNet member itself -- frognet-android's MemberService,
BridgeVpn and fdf-tun compiled in -- so Connect also joins FrogNet (the pond `frognet`, 165.232.153.144:51888) and
brings up the tunnel the race memories (game-serving machines' region addresses, 10.x) are reached through. Android
asks its VPN question once, the first time; nothing else is asked. The member logs to logcat tag `FDF`. The kart
memory (default 165.232.153.144:8799) is reached directly.
Settings (home screen) [SETTINGS_ARE_TUPLES_V1]: the pages keep nothing of their own; each shows a tuple in the forward
pond's memory and Save writes it there, permanent: **Device** (pond, cap, w:<guid>) {name, telem_on, telem_gps,
telem_period_s} -- followed by the member's telemetry -- with what the device reports, (pond, telem, w:<guid>), shown
as every monitor reads it; **Kart** (pond, cap, w:<guid>:kart) {laps, auto, bots, kart}; **Communicator** (pond, cap,
w:<guid>:comms) {comms_ram, comms_media}. The device and the app follow their tuples with a held read, whoever writes
them: `fdf-monitor --set NAME telem_period_s=60`, `fdf-monitor --set NAME:kart laps=5` do the same from any machine
(fdf-client on Windows and Linux follows its (pond, cap, <ns>) too). A tuple not there yet is written once from what
the device or app has. Disconnect (on Settings) goes back to the connect screen.
Connect screen: name, kart memory, robots, laps, autopilot (what is needed before this device is a member). In the lobby: Ready, Start, - / +, Chat, Comms, Log, Home.
Racing: left / right (bottom left), GAS / BRAKE (right, multi-touch), Autopilot, Leave. A red banner says what is
not reachable. The game logs to logcat under the tag `FrogNetKart`.

Android Studio: open this directory (`kart/android`) as the project. `build.gradle.kts` compiles `src/` and the
Communicator's code from `opt/fdf/src/frognet-android/app/src/main/java` (it stays there: one source), with
`AndroidManifest.xml`; AGP 8.5.2 and Gradle 8.9, as frognet-android. Debug builds are signed with Studio's own
debug key (as frognet-android's); release with `tools/frognet-kart.p12`, build_apk.sh's key. Switching between a debug
build and a sideloaded APK needs `adb uninstall net.frognet.kart` first.

Build without Android Studio (a JDK and Maven Central only; no Android SDK): `./build_apk.sh` -> `build/FrogNetKart.apk`
(the same sources and the same AndroidManifest.xml, written in binary by tools/axml.py). It signs with
`tools/frognet-kart.p12` (password frognet); keep that key, or the next build will not install over this one.

Test (no phone): `test/phone-test.sh [SHOTS_DIR]` starts a kart memory, the kart service, a dedicated kart_host and a
Python player on this machine, then
1. races the game core (`src/net/frognet/kart/core`) on a JVM (`test/Headless.java`);
2. runs the app's own MainActivity and GameView under Robolectric (Android 14 framework), tapping through setup, Join,
   Chat, laps, Ready, Autopilot, a whole race, Log and Leave, and writes screenshots of what the view draws.
Robolectric needs androidx.test, which is on Google's Maven only; `test/robo/androidx-test.sh` compiles it from
Google's source on GitHub.
