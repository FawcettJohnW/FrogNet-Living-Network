# RangerTrak app (desktop and phone) -- rt-app-2026-11-01-r5

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

## The screens (r4)

- **Public** opens on a home, not the map: I need help, I'm safe, Find someone, Report to the EOC, Hospitals &
  services, Map. Help and safe are DisasterComm's help records, `(help, request, <rid>)` in the general RAM (not own: a
  request stays when the phone is gone); Find someone searches them by name. A report is `(rt-report, <mission>,
  <dev>.<ms>)`. The public's own location fills its forms; it is not published.
- **EOC and Team**: Requests (open requests, On the way / Resolved as `(help, action, <rid>~<dev>)`, and for the EOC the
  reports) and Sensors (every node that files its System sensor; a node's sensors; a reading, a GPS reading's position
  or "no fix"), read from the network's sensor store through the node (`databasehost.frognet`).
- Failover: a lost session makes the app ask its node where the RAMs are, until the answer changes or the old sessions
  come back ([FAILOVER_ASKS_UNTIL_ANSWERED_V1]).

## r5

- **Public home**: a banner (the exercise, hospitals with beds, hazards marked, people checked in safe), the 911 line,
  I need help and I'm safe as the two big actions, then Find someone, Report to the EOC, Hospitals & services, the
  Survival library (in the app: works with no network) and Contact FEMA (DisasterAssistance.gov, 1-800-621-3362, the
  FEMA app, Disaster Recovery Centers, from USA.gov), and the Map.
- **Team**: starts with only its own team on the map; Layers (in front of the chat) adds other teams and resources.
  Actions: my status (Available, En route, On scene, Need assistance, Out of service: part of my presence), deploy a
  sensor platform, request services (to the EOC, with priority), mark a hazard, mark an area searched -- each
  `(rt-field, <mission>, <dev>.<ms>)`, the EOC's response `(rt-field, <mission>.act, <item>~<dev>)` ([RT_FIELD_V1]).
- **EOC**: starts on the map with teams, resources and hosts; chat is a button away. Requests has the teams' service
  requests (Acknowledge, Dispatched, Done) with the public's requests and reports.
- **Compliance** ([RT_COMPLIANCE_V1]): every node is asked once at the EOC's connect; a node with a working
  environmental sensor (a reading with temperature, humidity, pressure, air quality, gas, radiation, water...) is shown
  OUT OF COMPLIANCE on the status line, as a notice, at the top of Sensors, and as a red host on the map.
- At connect the app says whether it reaches the network's sensor store and how many nodes report there.

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
