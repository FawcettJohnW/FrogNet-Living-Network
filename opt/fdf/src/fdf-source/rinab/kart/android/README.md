# FrogNet Kart for Android  [KART_ANDROID_V1] [KART_HOME_V1] [KART_CALLS_IN_THE_GAME_V1]

A player of the kart service's games, the same game as kartdemo.py: lobby, chat, Ready/Start, laps, races, records.
It never serves a race. Android 8.0+ (minSdk 26), landscape.

Install: copy `FrogNetKart.apk` to the phone and open it (allow "Install unknown apps" for the app you open it with),
or `adb install -r FrogNetKart.apk`.

Start: Settings (name, kart memory, Communicator) and Connect. The home screen holds the kart memory (the players) and
the Communicator's memory (comms-ram, default the kart memory's host :8800, media :8994) and shows who is here; choose
**Communicator** or **Kart racing**; each opens the other. A race is a third memory, only while it runs: a call goes on
across races, and rings on every screen, racing too. From a call, **Race!** invites the people in it who run FrogNet
Kart [KART_RACE_INVITE_V1]: (kart, invite, YOU) {members, at, go}; each answers Yes/No in its own (kart, answer, NAME)
{invite, at, yes}; your game's held read brings the answers (30 s timeout); all yes, `go`, and each game launches itself
into the lobby, ready, and yours presses Start. kartdemo.py (Windows, Linux) shows the same invitation and answers it.

Run: the FrogNet app must be running and joined. The kart memory (default 165.232.153.144:8799) is reached directly;
the race memories are on game-serving machines' region addresses (10.x) and are reached through the FrogNet tunnel.
Setup screen: name, kart memory, robots, laps, autopilot. In the lobby: Ready, Start, - / +, Chat, Log, Leave.
Racing: left / right (bottom left), GAS / BRAKE (right, multi-touch), Autopilot, Leave. A red banner says what is
not reachable. The game logs to logcat under the tag `FrogNetKart`.

Build (a JDK and Maven Central only; no Android SDK): `./build_apk.sh` -> `build/FrogNetKart.apk`. It signs with
`tools/frognet-kart.p12` (password frognet); keep that key, or the next build will not install over this one.

Test (no phone): `test/phone-test.sh [SHOTS_DIR]` starts a kart memory, the kart service, a dedicated kart_host and a
Python player on this machine, then
1. races the game core (`src/net/frognet/kart/core`) on a JVM (`test/Headless.java`);
2. runs the app's own MainActivity and GameView under Robolectric (Android 14 framework), tapping through setup, Join,
   Chat, laps, Ready, Autopilot, a whole race, Log and Leave, and writes screenshots of what the view draws.
Robolectric needs androidx.test, which is on Google's Maven only; `test/robo/androidx-test.sh` compiles it from
Google's source on GitHub.
