# RangerTrak app (desktop and phone) -- rt-app-2026-11-04-r29

The demo John set on 2026-11-01 (`opt/frognet_semantic/ribbit/examples/rangertrak/PLAN.md`, its first decision): the EOC
on a Windows app, a first responder on a phone, presence with GPS, chat, a pre-populated moving world, in an app and not
a browser. Three views: **EOC** (the big map, everything on it, chat beside it), **Team** (teams colour-coded; double-click
a member, or tap it and Chat, to talk to that one or to the whole team) and **Public** (hospitals, fires, outages; no chat).

## An app is the base and its layers ([RT_LAYERS_V1], r29)

John 2026-11-04: "go all OO ... A top level presentation and a low level base layer, and each role adds a ui to the base
user and admin apps" -- for adding new screens and apps. The **base** is what every app has: the connect screen, the map,
the chat, the mission, the radio log, sensors, Call, follow-me, the no-GPS address, Back (`ui/base.js`); presence and
positions, chat, the team RAMs and hosting a team, the mission, Call, the radio log, sensors (`core/.../AppCore.java`).
A **layer** adds views (roles), screens, map kinds, header buttons, sections of another layer's screen, the core's events it
shows, and its own ops and families -- on the page (`ui/layers/<name>.js`) and in the core (`core/.../layer/<Name>Layer.java`),
by the same name. It never edits the base or another layer; the one interface it builds on is `RT` (page: `RT.role`,
`RT.screen`, `RT.section`, `RT.on`, `RT.kind`, `RT.button`, `RT.hook`; documented at the top of `base.js`) and
`Layer` / `Layer.Here` (core: `roles()`, `start(here)`, `handle(op, m, here)`; `here.follow(...)` for its families).

| layer | views | what it adds |
| --- | --- | --- |
| `help` | -- | help requests and reports to the EOC: on the map, the Requests screen (On the way, Resolved); ops `help`, `helpAction`, `report` |
| `field` | -- | team actions: status, sensor platforms, service requests, hazards, searched areas (Actions screen; service requests on Requests); ops `field`, `fieldAction` |
| `eoc` | EOC (login) | every team; reports on Requests and a notice for each; compliance (status line, map, Sensors) |
| `team` | Team | the team picked at connect (the admin's), its own team and chat first; Host team, Team on databasehost, Team members, the lead's card |
| `medical` | Hospital, Ambulance | the hospital's desk, Inbound, the unit panel, Hospitals; the hospital's status in its popup; transports and diversions on the EOC's Requests; ops `facility`, `transport`, `transportAction` |
| `units` | Water, Power, Shelter, Phone | each unit's own kind, hazards, requests; the field's Actions and Requests |
| `public` | Public | Home (I need help, I'm safe, Find someone, Report, Hospitals & services, the survival library, FEMA); the restricted map; no chat |

The apps (Android: each module's `build.gradle.kts` `rtLayers`, copied into that app's own assets and said in its
manifest's `LAYERS`; Windows: `--layers=` on the launcher, the zip carries those layers only):

| app | layers |
| --- | --- |
| RangerTrak (every view; Windows client, Android `app`) | help, field, eoc, team, medical, units, public |
| RangerTrak EOC (`eoc`, `RangerTrakEOC.cmd`) | help, field, eoc, medical |
| RangerTrak Team (`team`) | help, field, team |
| RangerTrak Public (`public`) | help, public |
| RangerTrak Hospital (`hospital`) | help, medical |
| RangerTrak Ambulance (`ambulance`) | help, field, medical |
| RangerTrak Water / Power / Shelter / Phone | help, field, units |

**A new screen** for a view: in its layer, `RT.screen('name', () => sheet('name', 'Title', html))`, and a way to it (the
role's `act`/`req`, or `RT.button`). **A new view**: `RT.role(...)` in a layer's page and a `Role` in its core's `roles()`.
**A new app**: an Android module (copy `water/`: its id, label, `ROLE`, `rtLayers`) or a Windows launcher line with
`--lock=` and `--layers=`. **The admin app**: a layer's own panel is `ui-admin/layers/<name>.js` calling
`RTA.panel(title, fn)` (none yet). Gates: `test/test_app.py` (32 checks; the Team and Public apps run with their own
layers only), `test/test_team_host.py`, `android/typecheck.sh`.

## How it is built

| piece | where | what it is |
| --- | --- | --- |
| the RAM library | `../frognet-ram-java` | the FNW1 session, the follower (held reads), JSON; abstracted from the kart app. A write with `own=true` goes with its session |
| the core | `core/` (`AppCore`, the base; `Layer`, `layer/*Layer.java`) | the node's door (`/dc/ram.php`: `op=rams` where the app's RAMs are served now, `op=login` for the EOC), the followers, this device's own writes. Java 8: the same code on both platforms |
| the page | `ui/` (`base.js`, the base; `layers/*.js`) | the screens (Leaflet 1.9.4 from npm, beside them). Talks to the core only by JSON lines: `rtNative.send(line)` out, `rtDeliver(line)` in |
| desktop | `desktop/` | a JavaFX window around the page; `package_windows.sh` makes `RangerTrak-windows-x64-<build>.zip` (its own Java runtime and JavaFX; unzip and run `RangerTrak.cmd`) |
| phone | `android/` | a WebView around the same page, the phone's GPS (every fix is the device's own position). Android Studio, or `./gradlew :app:assembleDebug` |
| the world | `../fdf-source/fdf/bin/rt-sim` | 80 simulated instances (teams, ambulances, boats, victims, hospitals, fires, outages, hosts) in the Seattle metro area and Vashon, each on its own session |

The cells (all in RangerTrak's AppRams, at their AppHosts in `/etc/hosts` and their ports in the configuration tuple):

| cell | written by | kind |
| --- | --- | --- |
| `(rt-who, <mission>, <dev>)` | each device, each simulated instance | latest, own: present while its writer's session is |
| `(rt-pos, <mission>, <dev>)` | the same, on every fix | latest, own; `via` = the node it came in through |
| `(rt-msg, <mission>, <dev>.<ms>)` | anyone, to everyone | distinct |
| `(rt-team, <mission>, <dev>.<ms>)` | anyone, to a team | distinct, in the team's own RAM (`rangertrak.<team>`) |
| `(rt-dm, <mission>.<to>, <from>.<ms>)` | anyone, to one device | distinct, in the addressee's own variable: only the addressee reads it |
| `(rt-mission, settings, <mission>)` | whoever started the exercise (rt-sim) | latest |
| `(rt-facility, <mission>, <facility>)` | the hospital's desk (a Hospital client; rt-sim for the rest) | latest, not own: it stays |
| `(rt-transport, <mission>, <dev>.<ms>)` | an ambulance: a patient on the way to a hospital | distinct |
| `(rt-transport, <mission>.act, <id>~<dev>)` | the hospital's desk: acknowledged, arrived | latest |
| `(rt-call, <mission>.<to>, <from>.<ms>)` | a caller, in the callee's own variable: a ring (with the caller's node) | distinct |

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

## r6

- **Hospital** (connect as Hospital, pick the facility): Status, the desk -- beds available by type (emergency, ICU,
  med/surg, pediatric, burn, operating rooms), ambulance traffic (open, ED diversion, trauma diversion, closed), power,
  water, oxygen, staffing, needs -- published as `rt-facility` ([RT_FACILITY_V1]); Inbound, the patients on the way
  there with ETA and triage, Acknowledge and Arrived, and a notice for each new one. The map: hospitals, ambulances,
  hazards. A hospital's popup on any map shows its beds and diversion.
- **Ambulance** (connect as Ambulance, the unit): Unit -- status (Available ... Out of service), Transport a patient
  (the hospitals with beds or their diversion, patients, triage, ETA, chief complaint, age/sex), Assignments (the teams'
  ambulance / medical-evacuation requests, the public's medical requests, Take it); Hospitals, every desk's status.
- **EOC**: Requests adds the transports under way and the hospitals not open to ambulances.
- **Call** ([RT_CALL_V1]): a person's popup has Call beside the chat. The caller picks camera, microphone and speakers
  (the desktop asks `comms-app --list-devices`; the phone lists its front/back camera, microphones and call outputs),
  then the Communicator opens and calls; the callee's app rings, and Answer opens its Communicator to answer, on the
  caller's node's Communicator servers (`<node>:8800`, `<node>:8994`). Desktop: comms-app beside RangerTrak, or chosen
  once (kept in settings.json). Phone: FrogNet Member's Communicator (`net.frognet.communicator.CALL`; that app must be
  the build with [COMMS_OPENED_FOR_A_CALL_V1]).
- rt-sim r8: every hospital's desk (three under strain), and each ambulance transport and its arrival.

## r7: the admin app, standalone ([ADMIN_APP_V1])

`RangerTrak-Admin-windows-x64-<build>.zip` (`desktop/package_windows.sh OUTDIR admin`): its own window
(`AdminMain`), page (`ui-admin/`) and core (`core/.../admin/AdminCore`, Java 8: a phone can carry it too). It does what
`fdf-app config` does on a member's command line, for the whole network, through any member's door:

- Teams and roles (a team is a role with its own RAM; "no login" is a default screen), users (role, new password: the
  verifier is made on the admin's computer), login required, relocate or fail, other RAMs.
- Members: every member with the app, whether its configuration is the current one, and its answer to the last change.
- The admin's password is checked on the admin's computer: PBKDF2 over the salt in the configuration tuples gives the
  admin key, and HMAC(key, "fdf-admin-check") must be the check value there. A change is the whole configuration as
  text, a seq and HMAC(key, text + "\n" + seq), written by the door as `(fdf-app, admin, <app>)`. Every member's
  `fdf-app serve` checks the mac with its own copy of the key (from `fdf-app config ... --admin USER:PASSWORD`, kept
  only in `/opt/fdf/apps/<app>.json`), adopts a newer seq as its configuration and says so in
  `(fdf-app, applied, <app>.<guid>)`; a member with no key says that. Neither the key nor any password is in the
  network RAM.
- The client's connect screen offers the teams the admin defined (door `op=roles`).

Door (`/dc/ram.php`): `op=roles`, `op=admin` (GET: configurations, answers, RAMs served; held with after/wait; POST:
the signed configuration, relayed). `fdf-app` r4: `--admin`, adoption, `seq` (an install line is newer than any earlier
admin change). Tests: `rinab/tools/test_fdf_app.py` 8-9 (adopted by every member; a wrong password's mac refused),
`test/test_admin.py` 1-4 (the admin app against real members and the real door under PHP).

## r8: the Team app ([RT_TEAM_APP_V1])

`android/team` is a second Android app in the same Gradle project, `RangerTrak Team` (`net.frognet.rangertrak.team`):
the same shell, core and page; its manifest's `net.frognet.rangertrak.ROLE = team` makes it a team member only.
`./gradlew :team:assembleDebug`, or `./build_apk.sh OUTDIR team`. In every view (desktop too):

- [RT_FOLLOW_ME_V1] the map is centred on you, north up, and follows you; moving the map stops that, ⦿ resumes it.
- [RT_NO_GPS_V1] the phone's position is its GPS only. A device with no GPS (or not allowed to use it) asks for the
  street address once (Android's Geocoder looks it up; "lat, lon" is taken as typed), keeps it, and publishes it as
  its position with `"src":"address"`.
- [RT_CALL_V1] the chat's Call beside Send when the To is a person: anyone calls anyone.
- [RT_MISSION_V1] Mission: the mission's settings (name, event, operational period, start, end, commander,
  objectives, area, notes), Update (the whole settings written again), Start a new mission (a new id; "at" = now: the
  mission now is the one started last, on every device). `(rt-mission, settings, <mission>)`.
- [DOOR_SAYS_ITS_BUILD_V1] the door says its build (`X-FDF-Door`, and `door` in its admin and roles answers); the admin
  app says which door answered.

## r9: RangerTrak's own radio log in the EOC and team views ([RT_RADIO_LOG_IN_THE_APP_V1])

The radio log app (`fdf-source/fdf/www/rt`, RangerTrak's own screens converted to the RAM) and these apps are on the
same general RAM, mission, presence and positions. Radio log (EOC, Team) shows its entries,
`(rt-log, <mission>.<opPeriod>, <dev>.<seq>)`, newest first, as the scribes type them; read only (the ICS-309 message
numbers are the posts').

## r10: the Public app, and the phone ([RT_PUBLIC_APP_V1])

`android/public`: RangerTrak Public (`net.frognet.rangertrak.pub`), ROLE = public, opens on the public's home. The
Android project now builds three apps from one copy of the code: `:app` (every view), `:team`, `:public`
(`./gradlew :team:assembleDebug :public:assembleDebug`, or `build_apk.sh OUTDIR team|public`). On the phone:
[APP_SAYS_ITS_BUILD_V1] the build on the first screen; [PAGE_ERRORS_ARE_SAID_V1] a script error on the status line
(and in `adb logcat -s RangerTrak`); [BACK_CLOSES_FIRST_V1] Back closes what is open, then Home on the public view;
the keyboard resizes the screen instead of covering the chat.

## r27: every app is self-contained ([APP_IS_SELF_CONTAINED_V1])

John 2026-11-04: "we aren't going to have the user install 2 things, on any platform. All apps are fully self contained
entities." r20-r26's hidden Disaster Area base ([BASE_APP_V1]) is gone: each app is its own FrogNet member again, as r15
(`shared/.../Member.java` over frognet-android's member, MemberService, BridgeVpn, fdf-tun). The update zip's
REMOVED.txt lists the `base/` module and `Base.java`: delete them. The membership is a FrogNet network, routed through the
WAN role when one is present -- not a VPN. Its TUN is made with Android's VpnService (BridgeVpn), the only way Android
gives an app a TUN, and Android keeps one VpnService TUN at a time: off a node's LAN, two apps joined on one phone would take
the TUN from each other -- so they don't ([SHARED_TUNNEL_V1], r28): an app that finds another app's tunnel to the
FrogNet host up (a TUN routing 10.0.0.0/8) brings up none of its own; the phone's routes carry it. When that tunnel goes
(Android's network callback), it brings up its own. On a node's LAN there is no TUN at all ([ON_THE_LAN_NO_TUNNEL_V1]). [DOOR_REFUSAL_LOGGED_V1] every door refusal is said on the
status line and in Logcat (tag RangerTrak) with the door's reason, the op and the HTTP status.

## The Android project stays where it is opened ([RT_ANDROID_UPDATE_IN_PLACE_V1])

The project in Android Studio is the export `android/studio_project.sh` made (`RangerTrak-Android-Studio-<build>`, the
`shared` module with its own copies of the core, the RAM library and the page). A change comes as
`RangerTrak-Android-Studio-update-<build>.zip` (`android/studio_update.sh BASE_ZIP OUTDIR`: the files that differ from
the export the user has open), unzipped in that project's own folder (`unzip -o`). No new directory, no new import.
(2026-11-02: update zips made for the repository's paths landed in a new opt/ folder inside the open project and were
never built; updates are now made against the project as delivered.)

## r15: the app is the FrogNet member ([RT_IS_A_MEMBER_V1])

As the kart app ([KART_IS_A_MEMBER_V1]): `android/shared` compiles in frognet-android's member (`member/`,
`app/src/main/kotlin`: MemberService, BridgeVpn, Identity), its fdf-tun jniLibs, the WireGuard crypto and its
Communicator. Connect starts the membership with the invitation (`fdf-invite NAME --leaf`, pasted once on the connect
screen and kept), waits for the tunnel (Android says when the VPN network is up), then connects; the RAMs (10.x) are
reached through the tunnel, wherever the phone is. Android asks its VPN question once. Call opens the Communicator in the
app. The member logs to Logcat tag FDF, the app to RangerTrak. minSdk 29 (the member's). The Java-only `build_apk.sh`
no longer builds the apps; Android Studio / Gradle does. (r12-r14's network binding is gone: with the tunnel up the
phone's routing does it.)

## r16: on a node's LAN no tunnel ([ON_THE_LAN_NO_TUNNEL_V1])

A phone on a FrogNet node's own LAN (a 10.x address in the /24 a node's .1 serves) connects directly: no membership, no
tunnel, no invitation. Off it, the app is the member, as r15.

## r17: teams in the field (EOC > Team > Team Member)

John 2026-11-02. A team is one more piece of the network that splits and re-forms: its lead's phone is its host.

- **The EOC QR is the join line** ([LOGIN_IS_AN_INVITATION_V1]). The admin's `fdf-org line LEVEL` (on the RAM host) goes in
  the invitation field, or comes in a lead's card. The phone logs in at the line's RAM host (the door, then its own
  tunnel, then the level's login; frognet-android `OrgLogin.admit`) and uses what the login gives exactly as an
  invitation: the hub's tunnel with the phone's own key and the edge the door gave, the region at the level's session
  port inside it. Nothing is kept but the line (and the level's user and password when it has one); every start logs in
  again and is given the same edge. The app still finds its RAMs where it found them before: the node's door.
  Gate: `fdf-source/rinab/tools/netns-admit-kt-test.sh` (the same Kotlin on the JVM: wrong password refused, admitted,
  the tunnel and the region through it, the announcement, the same edge again).
- **The team's RAM on the lead's phone** ([TEAM_RAM_V1]). `android/teamram/team_ram.cpp`: the Ribbit platform's RAM host
  (`opt/frognet_semantic/ribbit/cpp`, `ribbit::RamHost`) with no region, answering the FD&F endpoint, with FD&F's write
  contract (`[OWN_FLAG_DECIDES_V1]`: only an `own:true` write goes with its session). `android/teamram/build.sh` builds it
  static (musl, zig 0.13) for arm64-v8a, armeabi-v7a and x86_64 into `android/shared/src/main/jniLibs/<abi>/libteamram.so`;
  `TeamHostService` runs it from nativeLibraryDir on port 21800. Gates: `test/test_app.py <team-ram>` (32 checks, the
  incumbent test, with it as every RAM; also the arm64 build under qemu) and `test/test_team_host.py`.
- **Host team** ([TEAM_HOST_V1]). On a connected Team phone. Android lets no app turn the hotspot on, so Host team opens
  the settings it is in and continues when the user comes back with it on. The team's RAM starts on the phone; the core
  writes the lead's card into it (team, the node, the EOC's join line, how the lead reaches the node) and the lead's cell
  `(rt-team-host, host, <team>)` into the general RAM (own: gone with the lead's session). The EOC follows those cells:
  a hosted team's RAM is the lead's phone.
- **Scan team** (connect screen), replaced in r18 by Scan to join: a member already on the lead's hotspot still reads
  the lead's card at the Wi-Fi's gateway; the hotspot's own Wi-Fi QR path is gone.
- **Splits** ([TEAM_SPLIT_V1]). A connected phone follows its networks (Android's network callback, not a poll): on
  another node's LAN that node is its node (`{"op":"door"}`); off any node's LAN its own membership (the tunnel) carries
  it, or it is an island. A lead says where its team is again; its members follow its card: to the lead's node, and to
  a membership of their own only when the lead is on its tunnel ("via").

Not yet: on an island the general RAM is out of reach, so positions and presence (in the general RAM) are not shared
inside the team; the team's chat and its RAM are.

## r18: enrollment, the frog QR, leads and their members ([ENROLL_V1], [FROG_QR_V1], [ASSIGNMENTS_SAVE_V1])

John 2026-11-02: one QR, one password, and the phone is in. The workflow (WAN region, EOC, lead, members) is
`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`, "Enrollment". A device has one tunnel, to the WAN hub; everything else is
routed.

- **Admin app**: a **Leads** column (a user who leads its team's role), **QR** per user (its enrollment code, drawn by
  `ui/frogqr.js`), and **Save / Restore** of the assignments to files (`~/FrogNet-assignments`), by hand only.
- **The door** (`dc-door-2026-11-02-r3`): `op=enroll` (user and password → role, lead, the device's own WAN invitation
  from `fdf-identity <app>-<user> --leaf --again --line`) and `op=identity` (a lead asks for a member's invitation,
  `<app>-<team>-<name>`).
- **The phone**: **Scan to join** on the first screen scans a frog QR (`fdfenroll1 URL APP USER`), asks for that user's
  password, enrolls (`frognet-ram-java`'s `Enroll`), keeps the invitation for the member, and connects as the user in
  its role. A lead gets **Team members**: names and passwords (kept as verifiers), each member's frog QR at the address
  a member can reach (hotspot, Wi-Fi, or the lead's FrogNet edge), and the team's sponsor on port 21801
  (`TeamHostService`, `Enroll.Sponsor`), which asks the door for each member's invitation as the lead.
- **A computer** (the Windows bridge, `rt-admin-2026-11-04-r7` / `fdf-kt-2026-11-04-a26`): enrolls the same way. The
  admin's QR dialog shows the enrollment line as text beside the code; `setup.cmd fdfenroll1 URL APP USER` asks for the
  password (not echoed, never kept or logged), POSTs `op=enroll`, and keeps the answered invitation (`fdf-member-kt
  enroll`). Give the computer its own user: a second enrollment of a user replaces that user's key.
- **The frog QR** (`ui/frogqr.js`, also `ui-admin/`): lily-pad modules joined by the logo's lines, eyes for finder
  patterns, the logo's frog in the middle. `test/test_frogqr.py`: zxing core 3.4.1 (the phone's) reads it wherever a
  plain code of the same text reads, over 5 codes and 11 camera-like degradations.

Gates: `fdf-source/rinab/tools/netns-enroll-test.sh` (7: the hub, fdf-identity, fdf-app, the door, a lead enrolling and
joining, the lead's sponsor enrolling a member who joins with her own key), `test/test_admin.py` (7),
`test/test_enroll_page.py` (5), `test/test_frogqr.py`, `frognet-ram-java/test/EnrollTest.java`, `android/typecheck.sh`.

## Build and test

    ./build.sh                                   # build/*.jar (JavaFX for the desktop from Maven Central)
    javac -cp build/frognet-ram.jar:build/core -d build/test test/DevHost.java
    python3 test/test_app.py <rinab build>/fdf-ram [SHOTS_DIR]
    javac -cp build/frognet-ram.jar:build/core -d build/test test/CoreDriver.java
    python3 test/test_team_host.py <rinab build>/fdf-ram android/shared/src/main/jniLibs/x86_64/libteamram.so
    android/teamram/build.sh                     # libteamram.so for every Android ABI
    android/typecheck.sh                         # the Android sources against android-all 14 (no SDK)
    python3 test/test_admin.py                   # the admin app: leads, the QR, save and restore
    python3 test/test_frogqr.py                  # the frog QR reads (zxing core 3.4.1)
    python3 test/test_enroll_page.py             # the phone's enrollment page
    ./desktop/package_windows.sh OUTDIR          # the Windows zip

`test/test_app.py` drives the real core and page (through `test/DevHost.java`) against real fdf-ram RAMs and rt-sim:
23 checks (the EOC's world moving, announcements, team and direct messages, a GPS fix on the EOC's map, the public view,
everything gone when rt-sim stops and drawn as last known positions, a wrong password refused). `test/world.py` starts
the same world alone, to run a desktop app against.

## Not in this build

- Video inside RangerTrak's own window: a call is the Communicator's window.
- The desktop's answer is in the Communicator (it rings there too): comms-app has no "answer this person" option yet.
- Windows' own location service: a desktop writes no position.
- The APK: `android/build_apk.sh OUTDIR` builds it with Debian/Ubuntu's own Android tools (no Gradle, no Google
  download), signed with `android/debug.keystore`; Android Studio / Gradle builds the same sources. FrogNet Member (the
  phone's Communicator, `frognet-android`) still needs Gradle: it has native parts and Google-hosted dependencies.
- The kart app still has its own copy of the session code; it moves onto `frognet-ram-java` when next built.
- Map tiles are OpenStreetMap's, over the Internet; without it the map is plain and everything is still on it.
