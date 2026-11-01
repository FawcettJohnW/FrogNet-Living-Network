# RangerTrak app (desktop and phone) -- rt-app-2026-11-01-r3

The demo John set on 2026-11-01 (`opt/frognet_semantic/ribbit/examples/rangertrak/PLAN.md`, its first decision): the EOC
on a Windows app, a first responder on a phone, presence with GPS, chat, a pre-populated moving world, in an app and not
a browser. Three views: **EOC** (the big map, everything on it, chat beside it), **Team** (teams colour-coded; double-click
a member, or tap it and Chat, to talk to that one or to the whole team) and **Public** (hospitals, fires, outages; no chat).

## How it is built

| piece | where | what it is |
| --- | --- | --- |
| the RAM library | `../frognet-ram-java` | the FNW1 session, the follower (held reads), JSON; abstracted from the kart app. A write with `own=true` goes with its session |
| the core | `core/` (`AppCore`) | the node's door (`/dc/ram.php`: `op=rams` where the app's RAMs are served now, `op=login` for the EOC), the followers, this device's own writes. Java 8: the same code on both platforms |
| the page | `ui/` | the screens (Leaflet 1.9.4 from npm, beside them). Talks to the core only by JSON lines: `rtNative.send(line)` out, `rtDeliver(line)` in |
| desktop | `desktop/` | a JavaFX window around the page; `package_windows.sh` makes `RangerTrak-windows-x64-<build>.zip` (its own Java runtime and JavaFX; unzip and run `RangerTrak.cmd`) |
| phone | `android/` | a WebView around the same page, the phone's GPS (every fix is the device's own position). Android Studio, or `./gradlew :app:assembleDebug` |
| the world | `../fdf-source/fdf/bin/rt-sim` | 80 simulated instances (teams, ambulances, boats, victims, hospitals, fires, outages, hosts) in the Seattle metro area and Vashon, each on its own session |

The cells (all in RangerTrak's RAMs, where `app-where.json` says they are served):

| cell | written by | kind |
| --- | --- | --- |
| `(rt-who, <mission>, <dev>)` | each device, each simulated instance | latest, own: present while its writer's session is |
| `(rt-pos, <mission>, <dev>)` | the same, on every fix | latest, own; `via` = the node it came in through |
| `(rt-msg, <mission>, <dev>.<ms>)` | anyone, to everyone | distinct |
| `(rt-team, <mission>, <dev>.<ms>)` | anyone, to a team | distinct, in the team's own RAM (`rangertrak.<team>`) |
| `(rt-dm, <mission>.<to>, <from>.<ms>)` | anyone, to one device | distinct, in the addressee's own variable: only the addressee reads it |
| `(rt-mission, settings, <mission>)` | whoever started the exercise (rt-sim) | latest |

Gone is gone: a device or instance whose session ended has no cells. What the app draws for it after that, a last known
position (a translucent, dashed avatar, 10 minutes), is the app's own choice and is written nowhere.

Hosts (the nodes things connect through): a node with a GPS records it with the established sensor pattern
(`gpsd_reporter.bash` -> `metric_upsert.sh GPS Position`, gpsd's TPV, in `sensor_data`). The app reads those rows
(`SensorType=GPS`) from `databasehost.frognet` through the node when the Hosts layer is turned on; a row with no fix
is not drawn. Each device's `via` matches a host's `SensorAddress`. rt-sim's simulated hosts are `rt-pos` cells of kind
`host`.

## Build and test

    ./build.sh                                   # build/*.jar (JavaFX for the desktop from Maven Central)
    javac -cp build/frognet-ram.jar:build/core -d build/test test/DevHost.java
    python3 test/test_app.py <rinab build>/rina-host [SHOTS_DIR]
    ./desktop/package_windows.sh OUTDIR          # the Windows zip

`test/test_app.py` drives the real core and page (through `test/DevHost.java`) against real rina-hosts and rt-sim:
8 checks (the EOC's world moving, announcements, team and direct messages, a GPS fix on the EOC's map, the public view,
everything gone when rt-sim stops and drawn as last known positions, a wrong password refused). `test/world.py` starts
the same world alone, to run a desktop app against.

## Not in this build

- Video in the member chat box (it comes with the media library, abstracted from the Communicator).
- Windows' own location service: a desktop writes no position.
- The Android APK is built in Android Studio: this build machine has no Android SDK and no access to Google's Maven.
- The kart app still has its own copy of the session code; it moves onto `frognet-ram-java` when next built.
- Map tiles are OpenStreetMap's, over the Internet; without it the map is plain and everything is still on it.
