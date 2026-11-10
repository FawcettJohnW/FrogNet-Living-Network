# OpenEOC app (desktop and phone) -- openeoc-app-2026-11-09-r59 / openeoc-admin-2026-11-09-r17

The demo John set on 2026-11-01 (`opt/frognet_semantic/ribbit/examples/openeoc/PLAN.md`, its first decision): the EOC
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
| `units` | Water, Power, Shelter, Telecom | each unit's own kind, hazards, requests; the field's Actions and Requests |
| `public` | Public | Home (I need help, I'm safe, Find someone, Report, Hospitals & services, the survival library, FEMA); the restricted map; no chat |

The apps (Android: each module's `build.gradle.kts` `rtLayers`, copied into that app's own assets and said in its
manifest's `LAYERS`; Windows: `--layers=` on the launcher, the zip carries those layers only):

| app | layers |
| --- | --- |
| OpenEOC (every view; Windows client, Android `app`) | help, field, eoc, team, medical, units, public |
| OpenEOC Command Center (`eoc`, `OpenEOC-CommandCenter.cmd`) | help, field, eoc, medical, ics, fema |
| OpenEOC Team (`team`) | help, field, team |
| OpenEOC Public (`public`) | help, public |
| OpenEOC Hospital (`hospital`) | help, medical |
| OpenEOC Ambulance (`ambulance`) | help, field, medical |
| OpenEOC Water / Power / Shelter / Telecom | help, field, units |

**A new screen** for a view: in its layer, `RT.screen('name', () => sheet('name', 'Title', html))`, and a way to it (the
role's `act`/`req`, or `RT.button`). **A new view**: `RT.role(...)` in a layer's page and a `Role` in its core's `roles()`.
**A new app**: an Android module (copy `water/`: its id, label, `ROLE`, `rtLayers`) or a Windows launcher line with
`--lock=` and `--layers=`. **The admin app**: a layer's own panel is `ui-admin/layers/<name>.js` calling
`RTA.panel(title, fn)` (none yet). Gates: `test/test_app.py` (32 checks; the Team and Public apps run with their own
layers only), `test/test_team_host.py`, `android/typecheck.sh`.

## r79: the list of apps scrolls ([PICKER_SCROLLS_V1])

With the seven role Admins, "Which app?" lists 17 apps, taller than a window or a phone. The list's card is at most the
window's height and scrolls (checked in Chromium at 600 px: 17 apps, the last reached by scrolling).

## r78: each role's Admin; instances hold a read on it ([ROLE_ADMIN_V1]); a held read ends with the rows now ([HOLD_ENDS_WITH_NOW_V1]); door dc-door-2026-11-10-roleadmin1

John 2026-11-10: "Create -Admin roles for everything that has a non-admin version. For now, it's just the RAM host, but
add 'Reserved for future customization' below that"; "the Roles are installed and become services across the network
... they have a held read on the configuration tuples of the current <App>Admin ... When that happens, they need to update
their own tuples to match. There may be many instances of a role on the network. One Admin configures them all."

- **The Admins:** OpenEOC -- TeamAdmin, -- HospitalAdmin, -- AmbulanceAdmin, -- ShelterAdmin, -- PowerAdmin,
  -- WaterAdmin, -- TelecomAdmin (page `ui/layers/roleadmin.js`, core `RoleAdminLayer`; Windows `OpenEOC-<Role>Admin.cmd`;
  on Android in the OpenEOC app's first screen). A Command Center user logs in. Its one screen: **RAM host** (each
  instance on its own device / the SystemRam / another machine (Not Implemented) / no host or RAM), Save; below it
  **Reserved for future customization**. (The Command Center's Admin is OpenEOC -- CommandCenterAdmin, the admin tool.)
- **The role's one configuration:** `(fdf-app, roleconf, openeoc.<role>) {role, ramhost, by, seq, at}` in
  databasehost-control, through the door's new `op=roleconf` (GET; GET held with after/wait; POST by a Command Center user).
- **Every instance holds a read on it:** a Team or unit app's core holds `op=roleconf` from its connect (ev roleConf on
  every change). A Team's lead brings its own tuples to match: the SystemRam is a place it writes itself; its own device
  needs its hotspot, which is never started unasked ([NOTHING_UNTIL_ASKED_V1]), so that is said. A unit's instance is told
  and says so; moving a unit's RAM is not built.
- **[HOLD_ENDS_WITH_NOW_V1]:** the door's held read, at the end of its wait, handed back the rows it had when it began, so
  a write in between whose fire did not arrive waited one more hold. It now reads again at the end. (Found by 17c: in the
  test's stand-in node the fires to the door did not arrive; the instance was told at the end of the wait, 17 s.)
- **Tests:** `test_leads` 17c (TeamAdmin's screen, Save, the door, Dave's instance told), 17d (a team's user cannot set
  it); `test_app`, `test_fema`, `test_no_mission`, the page tests, the Android type-check.

## r77: names follow the convention ([NAMES_ARE_THE_CONVENTION_V1])

John 2026-11-10: "There is a convention: OpenEOC -- EOCAdmin, OpenEOC -- CommandCenterAdmin, OpenEOC -- TeamAdmin,
OpenEOC -- HospitalAdmin"; the roles' instances across the network are configured by their one Admin. The windows and
the Windows launchers now say:
- `OpenEOC -- CommandCenterAdmin` (`OpenEOC-CommandCenterAdmin.cmd`): the admin tool (Teams, Command Center users,
  Members; was "OpenEOC Admin (EOCCommandAdmin)", `OpenEOC-Admin.cmd`);
- `OpenEOC -- EOCAdmin` (`OpenEOC-EOCAdmin.cmd`): the forms, IAP, after-action report;
- `OpenEOC -- CommandCenter` (`OpenEOC-CommandCenter.cmd`); `OpenEOC -- Team`, `OpenEOC -- Hospital`, ... and
  `OpenEOC -- Public` for the role views; `OpenEOC` (`OpenEOC.cmd`) asks which.
TeamAdmin and HospitalAdmin are not apps yet: a team's or a hospital's configuration is its lead's (op=place).

## r76: the Communicator this computer has is used ([ONE_COMMUNICATOR_V1])

r73 to r75 had the Windows apps use only the bundle's `comms\comms-app.exe` and dropped the path each app had been
given, so a computer whose Communicator was elsewhere lost its calls (John: "It has been working"). Now one Communicator
per computer, for every OpenEOC app on it: the bundle's `comms\` when it carries one; else the one this computer was told,
kept in `%USERPROFILE%\.openeoc\communicator.json` (one file for every app; an app's saved `commsApp` is taken into
it); else asked once and kept there.

## r75: the Node field is not a home router ([NODE_IS_THE_ASKED_GATEWAY_V1])

r72 to r74 put any 10.x Wi-Fi gateway that is its network's .1 in the Node field at start (r72 stopped asking the
gateway at start): a home router's 10.101.113.1 became the Node, and a phone off the node's network connected there.
Now the Node field is the node used last (or the joined one), as before r72; at the connect the gateway is asked, and on
a node's LAN a connect that names no node goes to that node. Android type-check passes; not run on a phone here.

## r74: the lead's host choices; teams on the SystemRam in Diagnostics; the door asked again when a lead's cell changes; no Sensors in EOCAdmin

- **The lead's choices** ([LEAD_HOST_CHOICES_V1], John 2026-11-10), a second after the lead connects, nothing done
  before the answer:
  - **Host the hotspot and RAM on this phone** (Host team: the hotspot turned on if it is off, the team's RAM here);
  - **Host the hotspot on this phone and System RAM** (the hotspot, and Use SystemRam);
  - **Host the role on another machine** (Not Implemented: shown, disabled);
  - **Do not provide a host or RAM for this instance** (nothing is started).
- **Team chat in John's demo** (Flying Monkeys on the SystemRam: its messages reached Dave and the phone, not the
  Command Center). Not reproduced here: `test_leads` 17b (the lead's app stopped, a member connecting afterwards; the
  Command Center connected before the team existed) passes with and without the change below, so the cause is not
  established. Two changes:
  - [TEAMS_FROM_THE_DOOR_V1] a lead's host cell coming or going makes the core ask the door again what it serves (the
    Command Center's served list was the one from its connect, before the team existed);
  - [SYSTEMRAM_TEAMS_IN_DIAG_V1] Diagnostics lists every team the device follows and how: on its RAM, or on the
    SystemRam with its tuples, reads and FAILING reason. A team on the SystemRam has no follower, so Diagnostics had
    nothing on it. The Command Center's Diag says whether it follows a team and what its reads say.
- **EOCAdmin has no Sensors** ([EOCADMIN_NO_SENSORS_V1], John: "get rid of the Sensor menu item").
- Tests: `test_enroll_page` 3b (the four choices, the third disabled, each one's effect), `test_connect_page` 3,
  `test_leads` 17b (new), `test_app`, `test_fema`, the page tests.

## r73: one Windows bundle, one Communicator; Call in the chat ([ONE_BUNDLE_V1], [ONE_COMMUNICATOR_V1], [CHAT_CALL_TAB_V1])

John 2026-11-10: "the comms-app is part of the bundle thrown down, but also such that there is only one on the device"
(he chose one bundle for every app); "make Call available under the Chat menu as well. For now, just a single person".

- **One bundle.** `desktop/package_windows.sh OUTDIR [COMMS_DIST]` makes `OpenEOC-windows-x64-<build>.zip`: one runtime,
  one lib, `ui`, `ui-admin`, `comms\` and a launcher per app (`OpenEOC-CommandCenter.cmd`, `OpenEOC-EOCAdmin.cmd`,
  `OpenEOC.cmd`, `OpenEOC-Admin.cmd`). It replaces the four zips (`admin`, `eoc`, `eocadmin` and the client). Each app
  keeps its own settings and loads its own layers (`--layers`), as before.
- **One Communicator.** `COMMS_DIST` is the Communicator's Windows `dist\` (`build-windows.ps1`: `comms-app.exe`, its DLLs
  and Qt plugins), copied whole into `comms\`; without it nothing is made. Every app calls with that one
  (`comms\comms-app.exe`, beside the `ui` folder): no file dialog, and the path r71 and before saved (`commsApp`) is
  no longer read or kept. A bundle without it says so at Call.
- **Call in the chat.** The chat has a **Call** tab: every person present now, each with **Call**, one person a call
  (the To list's Call beside Send stays).
- Tests: `test_connect_page` 7 (the Call tab: its people, Call sends the call for that one, Everyone gives the message
  box back); the bundle built with a stand-in `comms-app.exe` and its layout checked.

## r72: the phone does nothing until told ([NOTHING_UNTIL_ASKED_V1])

John 2026-11-10: "on the phone, what I want is for it NOT to ... start the hotspot or do any connections or login or
anything like that until I tell it to ... When the phone comes up, I should have the option to start as a team,
ambulance, etc. before anything else. If I start as a team, it should ask me if I want to connect as the existing team
or start a new one."

- **The first screen is which app** (OpenEOC: Team, Hospital, Ambulance, …; an app built for one view goes on to that
  view). Nothing is sent before it: no connect, no roles asked of the node, and the phone no longer asks its gateway
  at start (`[GATEWAY_IS_A_NODE_V1]` asks it at the connect the user makes). The Node field takes a gateway shaped as a
  node's (10.x, its .1) without asking it.
- **A team or a unit (Hospital, Ambulance, …) then asks:**
  - **Connect as the existing team**: the one this phone joined as, named with its user (and "its lead"), connects
    when pressed; a phone that has joined none is shown the scan for the team's QR.
  - **Start a new team**: the scan is offered even on a phone that has joined, for the lead QR the Command Center
    shows; then the team's name and the password, Create the Team.
  - **Other app** goes back to the first screen.
- **Replaced:** `[JOINED_IS_DONE_V1]` (a joined phone connected by itself 1.8 s after start).
- **Unchanged:** the Command Center, EOCAdmin and Public views go to their connect screen; a computer asks the node for
  the admin's teams at start, as before; after a lead connects, its phone still asks (host / Use SystemRam / hotspot /
  Not now) and does nothing before the answer.
- **Tests:** `test_connect_page` 1 (nothing sent at start or after the pick; the question; the existing team connects;
  not joined: the scan; a new one: the scan), `test_enroll_page`, `test_header_page`, `test_kinds_page`,
  `test_nogps_page`, `test_admin_page`, `test_app` (36), `test_no_mission`, the Android type-check.

## r72: a user who is not a Command Center user is told the admin tool's fix ([EOC_USER_SAYS_THE_FIX_V1])

Found 2026-11-10: "connect: user EOCLead has role 'eochq' (category 1794191697412), not the EOCAdmin's: in the admin
app, put eochq in the EOC category". The admin tool has had no categories since admin r16 (Teams and Command Center
users only), so the message named a fix nobody could make. Now, for the Command Center and EOCAdmin: remove the user
from its role (its ✕ under Teams), Apply, add it under Command Center users with its password, Apply. Test:
`test_no_mission.py` step 5.

## r71: the update applier is in settings.gradle.kts ([UPDATE_IN_SETTINGS_V1])

Found 2026-11-10: the full project opened in Android Studio stopped at "Could not read script ...\OpenEOC-Android-Studio\rt-update.settings.gradle.kts as it does not exist". The file was in the zip, but not in the folder Android Studio opened, so the build depended on a second file arriving.

`settings.gradle.kts` now carries the update applier itself, and `rt-update.settings.gradle.kts` is gone. An r71 update zip applied to an older project removes it through REMOVED.txt, after that old file has applied the update.

Checked with Gradle 8.9 on the exported project:
- the r71 settings say "OpenEOC: building openeoc-app-2026-11-10-r71";
- an r70 project with the r71 update zip beside it applies the update (73 files) and then builds from the new settings.

## r70: a deleted team is refused; a lead removed takes its team

John 2026-11-09: "I already deleted the leads. I need to remove it so that I get an error on the phone when it tries to use it ... say that we are deleting the current lead for a role." This is part of [TEAM_DELETE_V1], with door dc-door-2026-11-09-teamdel2.

**A lead removed takes its team.** Removing a lead (Leads › Remove) marks every team it created removed, with that team's place.

**A deleted team is refused.** A device connecting as a team (a lead or a member) asks the door for its roles. A team the door does not name is refused, and the device says: "team X is not on this network: the Command Center deleted it, or it was never created".

**A team whose lead was removed earlier.** Leads removed with an older door left their teams on the network. Those teams are listed under "Teams created on the network", where Delete removes them.

**Tests.**
- `test_leads.py` step 20.
- `test_team_host.py`'s door stand-in now answers `op=roles`, as the real door does.

## r69: delete a team ([TEAM_DELETE_V1], door dc-door-2026-11-09-teamdel2)

John 2026-11-09: "I need a way to delete a team."

**Where.** The Command Center's Leads screen lists the teams created on the network: each one's kind, its lead, and where it lives. **Delete**, then **Delete X: sure?**, deletes one.

**What the door does** (`op=teamdel`, for a Command Center user only):
- marks the team's record (`fdf-app child`) removed;
- marks its place (Use SystemRam) removed;
- leaves the lead's login, so the lead can create a team again.

**What it refuses.** A Team the admin configured is the admin tool's to remove (its ✕, then Apply), and the door says so.

**Not deleted.** Tuples the team wrote to the SystemRam stay until their writer is gone.

**Tests.** `test_leads.py` steps 18 and 19.

## r68: the Leads screen says its answer ([LEAD_SAYS_ITS_ANSWER_V1])

Found by John on 2026-11-09: "I hit Create The Lead in the Command Center tool and nothing happened."

Two places could say nothing:
- **The form's own `required` fields.** A desktop web view blocks the submit without a word. They are gone; the page checks the name, the password (4 characters or more) and the roles itself.
- **The core's answer.** It reached only the status line.

Now every answer is said under the button on the Leads screen: "creating lead X…", then "lead X created", or why not. A door without Leads answers "op is read, write, …"; the screen says so, and that dc-door-2026-11-09-cc1 goes on the node.

`test_leads.py` steps 2b and 3b check it.

## r67 / admin r20: the EOC is the Command Center ([COMMAND_CENTER_V1], door dc-door-2026-11-09-cc1)

John 2026-11-09: "the thing we are calling the EOC is really the Command Center. Rename it across the board so it shows up correctly."

Renamed, everywhere the view shows (the logged-in `eoc` view):
- its name, title and window title: OpenEOC Command Center;
- its map kind, its people in the To list, and its header button (**Command**, the FEMA function's screen);
- the boards' and the lifelines' wording, and the public's "Report to the Command Center";
- the admin tool's "Command Center users", and the EOC category as it is shown;
- the core's, the phone's and the door's messages;
- the Android EOC app's label: OpenEOC Command Center;
- the Windows zip and launcher: `OpenEOC-CommandCenter-windows-x64-<build>.zip`, `OpenEOC-CommandCenter.cmd`.

Not renamed:
- the view's id `eoc`, the Android module `eoc` and its application id, so the installed app updates in place;
- the admin's category value `EOC`, which the configuration on the nodes holds;
- the code's names (`EOC_*` tags);
- EOCAdmin;
- FEMA/NIMS terms: EOC Director, EOC Management, EOC structure, EOC activation level, EOC position.

## r66: the lead is asked at start; the hotspot is seen ([LEAD_STARTS_ASKED_V1], [HOTSPOT_SEEN_V1])

**The lead is asked at start.** John 2026-11-09: "On the phone, when it starts, it needs to wait a second to see if I want to start the hotspot and if I want to host the system RAM."

A second after the lead's phone connects, it asks; nothing is done before the answer. The choices:
- **Host the team on this phone** and **Use SystemRam**, only when its team is served nowhere;
- **Turn on the hotspot**;
- **Not now**.

This replaces r64's hosting at once and the hotspot asked of Android on every connect.

**The hotspot is seen.** Found in John's Logcat the same day: the hotspot was on (swlan0 10.143.246.81), but Host team said "turn it on". His Samsung lists its own hotspot interface among Android's networks, and hotspotAddr skipped every listed interface. Now swlan, ap and softap interfaces count as the hotspot even when listed.

**Checked and left alone.** The same Logcat's "roles: cannot reach the node at 10.170.170.1" came before "FrogNet tunnel up" (the roles call at start, made before the tunnel was up). The connect that followed reached the door, and that address is Seattle7's own. Nothing changed for it.

**Tests:** `test_enroll_page` step 3b and `test_connect_page` step 3.

## r65 / admin r19: Use SystemRam; the window says its view ([TEAM_ON_SYSTEMRAM_V1], [WINDOW_SAYS_ITS_VIEW_V1], door dc-door-2026-11-09-sysram1)

**Use SystemRam.** John 2026-11-09: "Add an option to the Team config that says Use SystemRam. If that is set, the role does not have its own RAM and runs everything through databasehost.frognet, using the default api.php." The option is on the lead's Team screen, in place of the former "Team on the SystemRam".

1. **The lead sets it.** The button (`teamPlace` at `systemram`) records the team's place, `(fdf-app, config, openeoc.<team>) {at: systemram}`, at the door's `op=place`. A team a lead created may do this too. It also writes the lead's host cell with ram `systemram`, so every device follows the change at once.
2. **The door names it.** Its `op=rams` names the team `systemram`.
3. **Every device follows it there.** In place of a Follower on a team RAM, the core uses `TeamStore.OnSystemRam`. That reads and writes the team's service at databasehost.frognet's api.php, as `openeoc-team@<team>`, through the door's two new ops:
   - `op=team`: a held read; the door holds at api.php with `role_hold_wait`, as its admin read does;
   - `op=teamwrite`.
4. **What the lead stops doing:** it stops hosting.
5. **What changes in the core:** team names from the door are lower case, as the lead cells say them, so one team is one store.

**The window says its view.** John: "The Windows tools need to say what UI is running in the title bar."
- The desktop window's title is the page's. That is the view the app opens as (OpenEOC EOC, OpenEOC EOCAdmin) or connected as, with its team or unit: OpenEOC Team · Lee Rescue.
- The admin tool's window title is "OpenEOC Admin (EOCCommandAdmin)".

**Not yet:** the node's own `/openeoc/` pages (the radio log) read team families through `op=read` and `op=watch`, which go to a team RAM. A team on the SystemRam is not on those pages yet.

**Tests.** `test_leads.py` steps 15 to 17:
15. Use SystemRam, as the door says it, and the window title.
16. A team message reaches the EOC through api.php.
17. The EOC's reply reaches the team.

## r64: the lead's phone hosts its team ([LEAD_HOSTS_ITS_TEAM_V1])

Found on John's phone on 2026-11-09: team messages said "say: team Bigdogs's RAM is not served on this network". The team BigDogs was created by its lead, but nothing served its RAM until someone pressed Host team.

The rule (John): "If the team is not currently active on the network, a device joining and starting the team will be the host."

Now, when the lead connects and the door's RAMs do not name its team, the lead's phone hosts it at once, as Host team does. It turns the hotspot on through Android if it is off, and hosts when it is on.

A team that is served already is not replaced. `test_enroll_page.py` step 3b checks both cases.

## r63: a claim made off the node's Wi-Fi says so ([CLAIM_ON_THE_NODES_WIFI_V1])

Found on John's phone on 2026-11-09: it scanned the lead QR for JohnPhone, and Create the Team (BigDogs) timed out twice ("failed to connect to /10.170.170.1 (port 80) from /10.101.113.61 ... after 8000ms").

The phone was on the home router's Wi-Fi, 10.101.113.x. Its gateway, 10.101.113.1, is not a FrogNet node, and the phone has not joined FrogNet yet. The EOC's door, 10.170.170.1, is reached only from a node's Wi-Fi until the phone has its invitation, and the claim is how it gets that invitation.

Now a claim that cannot reach the door says all of that: where the phone is, that its gateway is not a node, and that it has not joined FrogNet. It says to join the EOC node's Wi-Fi and scan again.

## r62 / admin r18: the EOC app logs in from an EOC user's QR ([EOC_USER_QR_V1])

The admin tool's EOC users each have a QR, as its Teams do. The QR is the door and the user, never the password (John 2026-11-09: "the Admin tool has to project the QR code for the EOC app").

An app that opens as a logged-in view, the EOC or EOCAdmin, scans it and shows the user filled in and its password, with **Log in**. There is no team's name. It enrolls as that user (`op=enroll`, a configuration user) and connects as the EOC.

Every other app reads the same kind of QR as a lead's: the user filled in, the team's name blank, Create the Team.

`test_enroll_page.py` step 8 and `test_admin_page.py` step 5b check it.

## r61: the EOC's Leads ([EOC_LEADS_V1], door dc-door-2026-11-09-leads1)

John's words (2026-11-09):
- "The EOC should just have a way for me to add users. When I scan the QR code it should take me to a filled in screen with my username filled in, but the team name blank. I fill in the team name, add my password, and hit the button to create the Team."
- "The EOC needs to have a menu named Leads ... the name, password, and list of Roles the name can handle. The list of roles may be * for all roles, or a hand-entered list."

How it works:

1. **The EOC creates the lead.** **Leads** in the EOC app: the lead's name, a password, and its roles. The roles are `*`, or a list from team, hospital, ambulance, shelter, power, water and phone.
   - The core sends the door only the password's verifier (`Enroll.verifier`).
   - The door's `op=lead` checks the EOC user's own password, kept in the core's memory for that session only. It writes `(fdf-app, lead, openeoc.<name>) {name, verifier, roles, by, at}` in databasehost-control.
2. **The EOC shows the lead's QR:** `fdfenroll1 http://<node>/dc/ram.php openeoc <name>`, with no password in it.
3. **The lead scans it.** The connect screen shows the user filled in, the team's name blank and the password. **Create the Team** sends `op=enroll` with `{user, password, team, kind}`; `kind` is the view the app runs as.
4. **The door creates the team**, a child `{name, kind, lead}`, and asks the region for its identity.
   - A kind outside the lead's roles is refused.
   - A team another lead created is refused: there is no replacement.
   - The same lead again is the same team.
5. **Afterwards:**
   - The lead's login is its team's role (`op=login`).
   - The lead's members are invited on the lead's own user and password (`op=identity`).
   - Leads lists each lead with its roles and the team it created; Remove takes a lead away.
6. **With no QR at hand:** **Create a team without the EOC's QR code**, under Manual login, asks for the same three things, with the user typed in.

**Node update:** only `opt/fdf/www/dc/ram.php` changed (`dc-door-2026-11-09-leads1`).

**Tests:**
- `test/test_leads.py NODE_ROOT` runs the real EOC core and page and the Team page, against a node package's api.php and `fdf-app`, with this door, as root with MariaDB. It has 14 checks.
- `test_enroll_page.py` steps 1, 2, 3 and 7 now check this flow. They replace the "Start a team" join-password flow.

## r60: the FEMA EOC function and EOCAdmin ([FEMA_EOC_V1])

The EOC function as FEMA and NIMS describe it, and EOCAdmin, are built into the apps on the RAM (John 2026-11-09: "It needs to be an app, like it would in the real world"). They are not a separate preview: the r60 commit removed the browser-only `ui-preview/`.

**`ui/layers/fema.js`** (the EOC app, the Team app and every unit app) adds the EOC function on the boards engine (`ics.js`):
- **Community lifelines.** Each of the eight shows the EOC's call (the `lifelines` board) beside what the network reports into it:
  - hospital desks: diversion, power, oxygen, staffing;
  - the Shelters board;
  - the Road closures board;
  - each unit's presence status.
- **Organizations.** Everyone on the network, grouped by the app they joined with: Teams, Hospitals, Ambulances, Shelters, Power, Water, Communications. The categories come from the apps; the EOC allocates none.
- **Staffing.** The positions of the incident's NIMS structure: ICS-like, Incident Support Model, Departmental or ESF. The Incident board now records the structure, the operational period number and the incident type.
- **Objectives (ICS-202).**
- **Assignments (ICS-204).** The EOC assigns an organization from Organizations. That Team or unit gets a notice and sees the assignment under **Assignments**, where it acknowledges it and reports progress.

The EOC's header button **EOC** opens all of this on one screen.

**EOCAdmin** (`ui/layers/paper.js`, core `PaperLayer`, view `eocadmin`) is the EOC's paperwork. It runs as its own app: the Android module `eocadmin` and `OpenEOC-EOCAdmin-windows-x64-<build>.zip`.
- **Who can open it.** An EOC user logs in: role `eoc`, or any role the admin put in the EOC category.
- **The forms.** ICS 201, 202, 203, 204, 205, 206, 207, 208, 209, 211, 213, 213RR, 214, 215 and 221, and the Situation Report. Each one is filled in from the boards and the organizations on the network.
- **Where forms are kept.** As a draft or made final (read only) on the `forms` board, so every EOCAdmin and the EOC see them.
- **The Incident Action Plan** for the period: ICS 202, 203, 204, 205, 206 and 208, each the final kept, else the newest draft, else filled from the records.
- **The after-action report and improvement plan** (HSEEP).
- **Printing.** The native print: Android's print service, or the desktop's printer dialog (`rtNative.print`). The page's print rules show only the sheet.

**Two field names fixed** (found by `test_fema.py`). The core puts the filer's name in `name` and their role in `position`, so a board field of either name was overwritten:
- the Incident board's name is now `incName`;
- the Sign in / out board's position is now `post`.

**Tests.** `test/test_fema.py FDF_RAM [SHOTS]` runs the real core and page through DevHost with each app's own layers: the EOC, Team Alpha, a hospital, an ambulance and three EOCAdmins. Its 14 checks:
1. organizations by app;
2. the hospital's diversion turning Health and Medical to Stabilizing;
3. the EOC's call on Energy, seen on Alpha's app;
4. the incident, staffing and an objective;
5. an assignment to Alpha, acknowledged;
6. the 209 filled from all of it, kept final, and read only on another EOCAdmin;
7. the 204 and the IAP;
8. Print;
9. a team's login refused by EOCAdmin;
10. no page errors.

`test_app.py` (36 checks), the page tests, `test_no_mission`, `test_team_host` and the Android type-check pass.

## r59: one team password by default, a member's own password if the lead says so ([MEMBER_PASSWORDS_OPTIONAL_V1])

John, 2026-11-09: members should "default to one password for the team, with the option of individual passwords if
the lead says so". The lead's Team members screen: the team's password and the team's QR first (every member scans it
and gives a call sign and that password); under it, closed, **Individual passwords**: a call sign and a password the
lead gives one member, and that member's own QR (`fdfenroll1 URL APP <call sign> <key>`). The lead's sponsor already
took both (`Enroll.Sponsor`: a member's own verifier, else the team's for a call sign that has none); r55 to r58
showed only the team's. The phone tells the page which members have their own password (`own`). A member scanning
their own QR sees "Join as <call sign>" and is asked for that password only. Test: `test_enroll_page.py` 4b, 4c.

## r58 / admin r17: the app's tuples are the nodes' (`openeoc-*`), and the two renames are one tree

Two threads renamed RangerTrak to OpenEOC on 2026-11-08, each on its own branch: this tree (r55, then r56 and r57)
and the node side's (`openeoc-rename-20261108`, the r52 node package). r55 to r57 kept the tuple families `rt-*`
(`rt-who`, `rt-pos`, `rt-msg`, `rt-team`, `rt-team-host`, `rt-dm`, `rt-mission`, `rt-call`, `rt-log`, `rt-field`,
`rt-report`, `rt-ics`, `rt-facility`, `rt-transport`); the nodes on r52 write and read `openeoc-*` (`openeoc-sim`,
`openeoc-post309`, the pages at `/openeoc/`, `openeoc.js`). So an r55 to r57 app and an r52 node did not see each
other's tuples: the node's pages, its radio log and `openeoc-sim` on one side, the apps on the other (the apps saw
each other). r58 uses the nodes' names, everywhere the app reads or writes; the merge (`merge-20261109`) takes the
node side (`fdf-source`, the docs, `opt/frognet_semantic`) from `openeoc-rename-20261108` as it is, and this tree's
app with these names.

- The phone's own preferences are `openeoc-enroll` (were `rt-enroll`): a phone that joined on r55 to r57 joins again
  once after r58.
- Kept (until r71, which folded it into settings.gradle.kts): `android/rt-update.settings.gradle.kts` kept its name (the other branch named it `openeoc-update...`). It is the
  script an open Android Studio project runs first to apply an update; renaming it from inside an update is a change
  that has not been tried.
- History keeps its names: builds made as `rt-app-...` and `rt-admin-...` are named so where the record says them.

## r57 / admin r16: the admin authorizes Teams and EOC users ([TEAMS_AUTHORIZED_V1])

The phone (r57): a scanned QR with a login is a Team's: "Claim echo", "echo's password: this device claims it".

John, 2026-11-09: "we are authorizing Teams, and that's the trust boundary. EOC allocates Team login and PW, lead
connects to the EOC LAN and claims the Team on their Pi or phone"; "Categories are added by virtue of their apps being
connected to the EOC. The EOC does not allocate"; a Hospital "joins the EOC in the same manner a Team does, but with
the Hospital category"; a Team not active on the network is hosted by the device that claims it, and "if the team
already has a server, no replacement".

The admin's screen is now:
- **Teams**: each Team's login (its name) and password, and its QR. In the configuration a Team is a role with its own
  RAM and no category (the claiming app's), and a user of the Team's name that leads it, with the password's verifier.
  A Team from an older configuration shows its category, and any other users it has ("also signed in by"), which can
  be removed; one with no lead gets its login when given a password.
- **EOC users**: the users of the EOC's role (the role in category EOC; made as `eoc` with the first one).
- Gone: the Categories cards and their pop-up (groups, user lists, leads), and the EOC's one teams password. AdminCore
  writes no `join` verifier any more, so the door's teams-password join ([CHILDREN_JOIN_V1]) ends at the next Apply;
  teams that joined that way still show under Groups now and Connected now.

The claim works through the door's enroll as it is: the Team's QR (or Manual login) and its password enroll the device
as the Team's lead. Still the door's to do (`dc/ram.php`): refuse a claim while the Team is served; record the claiming
app's view as the Team's category (op=roles says Teams for a Team with none until then); and accept the claiming
device on its FrogNet identity, so the phone keeps no password.

Tests: `test/test_admin_page.py` (rewritten for this screen). `test/test_admin.py` drives the real AdminCore against
the fdf-ram model of the SystemRam, which `fdf-app` no longer uses (it now needs databasehost_control.frognet in
/etc/hosts): its setup fails before and after this change; its steps are rewritten for this screen, and the
api.php harness it needs is the roles-on-api work's.

## r56: Exit, one dialog at a time, the legend is the map, the QR code first, a joined phone is done

John's changes of 2026-11-09, before the Android app goes to others. Tests: `test/test_connect_page.py` (new),
`test/test_enroll_page.py` (steps 1, 6b and 7 to the new screen), `test/test_app.py` (Connect clicked by its type: Scan
is the connect screen's first button now).

- **Exit** (`[APP_EXIT_V1]`): on the connect screen and in the header (under ☰ on a phone). The phone stops everything the
  app runs (its FrogNet membership and tunnel, TeamHostService: the team's RAM and sponsor, the core) and closes. Only
  where the native side has `rtNative.exit` (Android); the desktop's window closes as before.
- **One dialog at a time** (`[ONE_DIALOG_V1]`): Layers, the chat, a sheet (Sensors, Diag, Mission, the radio log, a
  layer's screen) and the person pop-up: opening one closes the other.
- **The legend is the map** (`[LEGEND_IS_THE_MAP_V1]`): made again on every redraw from the markers drawn. Measured on
  r55, it listed every team the page had ever heard of: a team gone, one switched off in Layers, and on a member's map
  the teams it does not draw all stayed in it.
- **The QR code first** (`[CONNECT_QR_FIRST_V1]`): on a phone, *Scan a QR code* is at the top; everything else is under
  *Manual login* (closed; it opens itself when Connect needs one of its fields). The team's QR asks the team password
  only: the call sign is *Your name*, when the user typed one (`nameGiven`) and it is a call sign; else it asks one
  as well. *Join EOC* is *Start a team without the EOC's QR code*, under Manual login. The desktop has no scan: no
  Manual login heading, the fields as before.
- **A joined phone is done** (`[JOINED_IS_DONE_V1]`): a phone that has joined (`cfg.enrolled`) opens on the map: no
  picker, no connect screen, the splash saying who is connecting and what the connect is doing. It connects as what
  joining made it (its user, its team or its app's view, the node it joined through), with no password: a Team or unit
  view's connect asks none. If that connect fails, the connect screen shows, saying why, without the scan.
- **The lead's hotspot** (`[LEAD_HOTSPOT_V1]`): a lead's every connect makes sure the team's Wi-Fi is on
  (`rtNative.ensureHotspot`): off, Android's Hotspot & tethering screen opens (an app cannot turn it on).

Not in r56: the Team authorization John set out the same day (the admin allocates each Team a login and password; a
lead's device on the EOC's LAN claims its Team, when no device holds it, in its app's category: Team, Ambulance,
Hospital ...; the Categories section leaves the admin app) is the door's (`dc/ram.php`) and the admin's, and waits
on who changes the door. The phone still keeps its user's password (Enrolled.java) for the lead's door calls until
the door takes the device's FrogNet identity instead.

## r55 / admin r15: RangerTrak is OpenEOC ([OPENEOC_BRAND_V1])

John, 2026-11-08: "Rebrand the entire RangerTrak product to OpenEOC", internals included. The logo is
`brand/openeoc-logo.svg` (his file); `brand/openeoc-mark.svg` is its mark alone, square, for icons.
- **Names:** every app, window, page, splash, Windows zip and launcher (`OpenEOC.cmd`, `OpenEOC-EOC.cmd`,
  `OpenEOC-Admin.cmd`), the Android Studio project (`OpenEOC-Android-Studio\`) and the docs say OpenEOC.
- **Internals:** this directory is `opt/fdf/src/openeoc-app`; the Java packages are `net.frognet.openeoc.*`; the Android
  applicationIds are `net.frognet.openeoc.<app>`; the app id is `openeoc` (`fdf-app install openeoc`, `app=openeoc` at the
  door, `(fdf-app, config, openeoc)`, `openeocHost.frognet`, `<team>Host.openeoc`); builds are `openeoc-app-...` and
  `openeoc-admin-...`. The tuple families are `openeoc-*` since r58 (r55 to r57 kept `rt-*`; see r58). The `RT_*`
  markers, the page's `RT` object and `RtTheme` stay.
- **The logo:** on both splash screens (as given, on its white), as each page's icon, as every Android app's launcher icon
  (`android/shared/src/main/res/drawable/ic_openeoc.xml`, drawn from the mark), as the Windows and Linux window and tray
  icon (`ui/openeoc-mark-256.png`), and as `openeoc.ico` in each Windows zip, for a shortcut.
- **What it costs on machines already running RangerTrak:** a new applicationId is a new app, so phones install the
  OpenEOC apps and remove the RangerTrak ones; nodes install the app again under its new id
  (`fdf-app config openeoc ...` then `fdf-app install openeoc`, or, from each machine's own copy,
  `fdf-app rename rangertrak openeoc --admin USER:PASSWORD` [APP_RENAMED_V1], the admin's machine first: the RAMs and
  ports, roles, users and the teams-join verifier carried over, the admin key derived again for openeoc), since nothing
  reads the `rangertrak` tuples any more;
  Android Studio opens the new `OpenEOC-Android-Studio` project once. Builds through r54 were delivered under the
  RangerTrak names (`RangerTrak-*.zip`, `RangerTrak-Android-Studio-*.zip`); the sections below keep their history but
  say OpenEOC.
- **What stays RangerTrak:** John Cornelison's app, which this one is measured against (`ribbit/examples/openeoc`:
  `RANGERTRAK-SUITE-MAP.md`, `RESULTS.md`, the provenance lines in `www/rt`).

## r54: the joiner-to-lead hop is sealed ([SEALED_ENROLL_V1])

The lead's hotspot is shared by everyone with the team's Wi-Fi password, so a member's team password going up and its
invitation coming back are sealed (frognet-enroll r5, `Enroll.Seal`): an ephemeral P-256 key, ECDH with the lead's
sponsor key, HKDF-SHA256 (salt SHA-256(epk || key), info `fdfenroll1 seal <app>`), AES-256-GCM (AAD `req <app>` /
`ans <app>`). Nothing changes for the user: scan, call sign, team password.
- The team's QR is `fdfenroll1 URL APP * FP`: FP is the first 16 bytes of SHA-256 of the sponsor key's compressed point,
  base64url, 22 characters. The device asks the lead's phone for its key (`op=key`), takes it only when its fingerprint
  is the QR's, and seals to it. (The key itself in the QR, 44 characters compressed or 122 as X.509, made the code QR
  version 9 or 10, where the frog missed turned, tilted, small or blurred codes a plain code of the same line read; at
  version 8, which the fingerprint keeps even at a 15-character address, it misses none.)
- The lead's key is made the first time it sponsors and kept (`openeoc-enroll`: `sponsorPub`, `sponsorPriv` as PKCS8): a QR
  already shown stays good. The Sponsor takes sealed requests only (an unsealed one: 400, said), seals every answer after
  opening one, refusals too, and refuses a request more than 300 s off its clock or whose epk it saw in the last 10 min.
- Door lines (four parts: the EOC's own LAN, the lead's Join EOC) are as they were. fdf-member-kt refuses a team lead's
  QR ("join with the phone app").
- Tests: EnrollTest (sealed round trip; a relay between device and lead catches neither the password, the call sign nor
  the invitation; unsealed refused; another key's fingerprint refused; a replay refused, the door not asked);
  netns-enroll-test (ann and dan join by sealed team QRs); test_enroll_page (five parts, kept through the call sign);
  test_frogqr (the fingerprinted lines at 600 px and at 340 CSS px on a 3x screen).

## r53: teams join the EOC; every connected kind is shown ([CHILDREN_JOIN_V1], [ADMIN_TREE_V1], [MAP_KINDS_DISCOVERED_V1])

John 2026-11-06: "The teams aren't allocated beforehand. They are discovered" and "Creating a team is a matter of
installing the team app, firing up the admin console for it, and hitting the Join EOC button, with the team name. It
needs the eoc teams password, set up in the eoc admin page, and it becomes an entity."
- The EOC's admin (rt-admin r13) sets **the EOC's teams password** ("Teams join the EOC") and shows **the EOC's QR**
  (`fdfenroll1 http://<door>/dc/ram.php openeoc *`). Only its verifier is kept (`join` in the app's configuration;
  `fdf-app config --join PASSWORD` on a node, fdf-app r17).
- A lead's phone: **Join EOC** on the first screen (the Node field's door), or Scan to join the EOC's QR: the team's name
  (anything: letters, digits, space . _ -, 40) and the teams password. The door (dc-door r18) writes the team as an
  entity, `(fdf-app, child, openeoc.<name>)`, makes the lead's invitation, and from then lists it in op=roles
  (category Teams) and op=admin (`children`). The lead logs in with the team's name and the teams password; the team
  lives on its lead's phone (it cannot be placed on databasehost). Its members join it by the team's QR and password.
- The admin's **Connected now** is a tree: the mission, each category, each group (the admin's and the joined teams), and
  every device on now in it of whatever kind it says; then anyone in no group. The lead's Team members sheet lists who is
  on now in the team, any kind.
- The map draws **any kind** that appears in the world, known to a layer or not (a plain marker, its initial, its own
  colour, on), and Layers and the legend are built from what is present. Team names are capitalized per word (Red Cross).
- Tests: netns-enroll-test 10-12 (the real door, fdf-identity, a sponsor); test_enroll_page 7; test_admin_page 7;
  test_kinds_page (new).

## r52: the team's Wi-Fi is the lead's hotspot, and the lead's QR signs a member in

- [TEAM_WIFI_V1] the lead's phone said "hosting the team on this phone's hotspot" with the hotspot off: the hotspot was
  taken to be any interface Android does not list. It is a tethering interface by name now (swlan, ap, softap, wlan1-9,
  wigig, rndis, ncm, usb, bt-pan). Host team opens the Hotspot & tethering screen itself (an app cannot turn the hotspot
  on). While the phone hosts the team and the hotspot goes off, it says "Team Wi-Fi is OFF". Diag: Team Wi-Fi, hosting,
  the other interfaces.
- [ENROLL_KEY_V1] the lead's Team members QR carries a one-time sign-in key ("fdfenroll1 URL APP USER KEY"; 15 minutes,
  once; a new QR a new key; the lead's phone keeps its verifier, never the key). The member joins the lead's hotspot
  (Android's own hotspot QR), scans it, and is signed in with nothing typed: the sponsor checks the key, clears it, and
  gives the member its own FrogNet invitation (its WireGuard key: its tunnel through the hotspot). A used or expired code
  is refused ("ask the lead for a new one"); the password still works. frognet-enroll r3. Gates: EnrollTest (10:
  there and back with the key, signed in by it, refused again and expired, the password still), test_enroll_page 6.

## r51: one root for every Android zip ([RT_ANDROID_STABLE_ROOT_V1])

The full project unzipped to OpenEOC-Android-Studio-<build>\ (a new folder each build) and an update zip had no root
at all, so unzipped beside the project it spilled app\, shared\, settings.gradle.kts ... into the folder above (John
2026-11-05). Every Android zip now has the one root OpenEOC-Android-Studio\, as the Windows zips have theirs
(OpenEOC\, OpenEOC-EOC\); the zip's name carries the build. An update unzipped next to the project lands in it;
the build's self-apply ([RT_ANDROID_UPDATE_APPLIES_ITSELF_V1]) takes both the rooted zips and the bare r42..r50 ones.
Tested with Gradle 8.14 on stand-ins: a rooted r52 "(1)" zip over r41 (REMOVED.txt applied); an r51 update unzipped by
hand next to the project. A project carrying the r42..r50 script cannot take a rooted zip (it would write it under
OpenEOC-Android-Studio\ inside itself): the r51 script must come in by hand once.

## r50: ☰ on a phone ([RT_HEADER_MENU_V1])

A phone's header cut its buttons off (John 2026-11-05: no Chat). The header measures itself: the buttons that do not
fit go under ☰ (last first), Chat and ☰ stay; again when a layer shows or hides a button and on every resize. A desktop
has no ☰. "Team on databasehost" is "Team on the SystemRam". Gates: `test/test_header_page.py` (2: a 360-px phone as a
team lead, Chat in the header, the rest under ☰, Diag and Layers from it; a desktop with every button). The page tests
reach a button under ☰ as a person does (`test/header_menu.py`). test_app 36/36.

## r49 / dc-door r17: the sensor store by the door's address ([SENSORS_BY_THE_DOOR_V1])

Off the LAN, through the tunnel, the phone could not reach the sensors (John 2026-11-05 Logcat: "Unable to resolve host
databasehost.frognet"): the phone's DNS is its carrier's, and .frognet names are the nodes' (/etc/hosts). The door says
where the sensor store is, `sensors` in op=rams (http://<databasehost.frognet's address on that node>/api.php; the
default vhost serves api.php by address), and the core uses it. A Sensors sheet waiting on an answer shows the error
instead of "finding the node" for ever. Through the tunnel that address must route (fdf-tun drops a destination no scope
routes, and says so in Logcat: "no scope routes A.B.C.D"). Node update: dc/ram.php r17 (with fdf-app r16). Gate:
test_no_mission.py 7. A device told it has no GPS while connected is asked at once (test_app 26).

## r48: a phone that leaves the node's LAN brings up its tunnel ([TEAM_SPLIT_V1])

John's Logcat (2026-11-05): connected on the node's LAN (no tunnel, [ON_THE_LAN_NO_TUNNEL_V1]), the phone moved to
another network (10.101.113.x); the gateway was asked ("not a FrogNet node ... through its own tunnel") and then nothing
for 40 minutes: "the door at 10.250.250.1: this device cannot reach the node". netChanged measures a change against the
network the phone connected on, and that was set by the first network event, not by the connect: a phone whose first
event came after it had moved took the new network for its own. startMember now sets it (lastLan) at the connect.
r47: Diag on the phone has a FrogNet tunnel section (rtNative.tunnelState()). A phone with no invitation (a new install
has none) off the LAN cannot join: it enrolls on the node's LAN first (scan the QR there).

## r45 / dc-door r16: a computer finds its node by name ([DOOR_SAYS_ITS_NODE_V1])

r44 matched a computer to its node by the door's address against the GPS sensor's SensorAddress; every node's LAN door
is 10.170.170.1 and the sensor's address is the node's FrogNet host path (or eth0), so it never matched: Seattle7's GPS
had a fix and the Windows apps said they were on no map. The door (dc/ram.php, dc-door r16) says which node it is,
`node` (its hostname, the name its sensors are filed under) in op=rams and op=roles; the core passes it on
(`connected`, Diag) and the page matches the node by that name. A door that does not say it is said so, with the nodes
that have a fix. Node update: /opt/fdf/www/dc/ram.php (with fdf-app r16). Gate: test_nogps_page.py 4.

## r44 / admin r11: a computer is on everyone's map; the admin says who is on now

- [RT_NO_GPS_V1] [RT_AT_ITS_NODE_V1] the Windows app never said it had no GPS (only Android did): it sent no position,
  and a Team on Windows was on no map but its own (John 2026-11-05: Bravo on the phone showed; Alpha on Windows did
  not). Now the desktop says `nogps` at load (with the address saved in its settings, `addr`); a device with no GPS
  and no address is at its node -- the GPS fix of the node its door is ("src":"node") -- and is asked Where are you?
  (an address, or "latitude, longitude"; saved, `rtNative.saveAddress`). Diag's "Where am I?" asks again. Gate:
  `test/test_nogps_page.py` (3).
- [ADMIN_ON_NOW_V1] the admin follows the general RAM as the apps do (the newest mission, its openeoc-who) and Groups now
  has On now per group (a device's team or unit), with the devices in no group (and the group they say) under it.
  Gates: test_no_mission.py 6 (the admin core sees Pat and Lee, team Alpha, in the mission now: `test/AdminOnNow.java`);
  test_admin_page.py 6.

## r42: the Android project applies its own updates ([RT_ANDROID_UPDATE_APPLIES_ITSELF_V1])

Twice an update zip was unzipped and the phone ran the old build (r29 after r36's zip; r37 after r38's and r40's): the
files were not where Android Studio builds from (Windows' Extract All makes a new folder named after the zip), and
the phone ran the `phone` module, not `app`. Nothing said either. Now `settings.gradle.kts` applies
`rt-update.settings.gradle.kts`, which every build runs first: it says "OpenEOC: building <build> in <folder>" in
Studio's Build output, and applies the newest update zip newer than the project (from the project folder, the one above
it, or Downloads; its build read from its own AppCore.java, so "(1)" copies count) before anything compiles, deleting
its REMOVED.txt and saying so. An update made over a newer project than this one (BASE.txt) stops the build and says
which project it needs. Update zips (studio_update.sh) are made over the last full project delivered and carry
BASE.txt. Tested with Gradle 8.14 on a stand-in project: applied the newest of an r42 and an r43 "(1)" zip over r41,
deleted the removed file, applied nothing the second time, refused r43 over an r40 project.

## r41: the Android module `phone` is `telecom`

John's phone ran `net.frognet.openeoc.phone` (Logcat, 2026-11-05): the Telecom crews' app, module `phone`, picked
as "the phone app". The module is `telecom` now (applicationId net.frognet.openeoc.telecom; its view is still
`phone`, labelled Telecom). The app that asks which app is the `app` module, "OpenEOC", net.frognet.openeoc.

## r40: compliance by whole words ([RT_COMPLIANCE_WORDS_V1])

The compliance check matched "temp" anywhere in a reading's name, so the semantic proxy's `template_hits`,
`template_misses` and `template_coverage` put every node out of compliance (John 2026-11-05). A reading is
environmental when a word of its name (split at `_`, `-`, `.` and case changes) is temp, temperature, humid(ity), rh,
pressure, baro, pm1, pm2, pm25, pm10, aqi, co2, eco2, voc, tvoc, gas, radiation, cpm, usv, water, smoke or lux. r39: a
refused EOC login says the role's category (or why it is not known) and the build. Gates: test_app 36/36 (19: New-York-2
files the template counters; only Seattle7, its SenseHat, is out of compliance; 10 leaves the nodes out of its kinds).

## r38: the EOC's user is any in the EOC category ([RT_CATEGORY_IS_THE_VIEW_V1])

The EOC's login took only a user whose role is named `eoc` ("EOCLead has role 'eochq', not the EOC's"). A user is the
EOC's when its role is `eoc` or the admin put its role in the EOC category (the door's op=roles says the category); any
other is refused with the role, its category and where to put it. Gate: test_no_mission.py 5 (eochq in EOC logs in;
medic1 in Ambulance is told to put it in the EOC category).

## r37: a network with no mission still works ([RT_STANDING_MISSION_V1])

Everything a device follows -- presence, positions, messages, direct messages, calls, the radio log, the layers' families
-- is in the mission, and the core started none of it until a mission tuple `(openeoc-mission, settings, <id>)` was in the
general RAM. Only openeoc-sim (the tests) and an EOC's Mission > Start wrote one; on John's network (2026-11-05, Diag:
"mission none", only the mission, team-hosts and team followers) nobody had, so no device wrote or read a position or
a message: no markers, chat going nowhere. Now a core whose mission follower is up and holds no mission writes the
standing mission, `(openeoc-mission, settings, standing)` {name "Standing operations", at -1}, the same tuple from every
device, and says so; any mission an EOC starts is newer and every device moves to it. Gate: `test/test_no_mission.py
FDF_RAM` (4: no mission, the standing one followed by two cores; each sees the other's position and presence; a team
message arrives; a mission started afterwards is the mission on both). test_app 36/36.

## r36: the nodes always on the map; a marker says what it is and opens its node's Sensors

- [RT_HOSTS_ALWAYS_V1] Hosts (the nodes, from their GPS sensors) is on in every view from the start, and in every
  view's Layers (a view that shows only its own kinds too).
- [RT_MARKER_INFO_V1] a marker, selected: its name, team, status, what it is, where (and ±m), since when, through which
  node; **Sensors** on a node opens that node's sensors; **Sensors of its node** on anyone else's opens the sensors of
  the node it is on (`op=sensorsAt`: the node whose System sensor says that address).
- Gates: test_app 36/36 (36: Seattle7 on the Team member's map from the start, selected, its Sensors listed). Checks
  16, 10, 4 and 30 leave the nodes out of their people counts; 31 reads the page error from the said list.

## r35: Telecom, not Phone ([RT_TELECOM_NOT_PHONE_V1])

The telephone company's crews' view was "Phone" and its Android app "OpenEOC Phone": read as the app for a phone. It
is Telecom (view label, its unit's label and hint, the Android app's name "OpenEOC Telecom"); its role and module are
still `phone`. The Android app that asks which app is the `app` module, "OpenEOC".

## r34: the teams are the network's; a category is a view

- [RT_TEAMS_ARE_THE_NETWORKS_V1] the page had the teams as a list of its own (Alpha, Bravo, Charlie, Delta): an EOC drew
  no other team's members, and had no Layers line or chat tab for one. The teams are now the admin's Teams groups (the
  door's op=roles) and every team a position names; a team with no colour of its own gets one from its name.
- [RT_CATEGORY_IS_THE_VIEW_V1] the admin's categories are the views: the Team view's list on the connect screen is the
  groups in Teams (with a RAM), not every group with a RAM (it offered the Ambulance category's groups as teams); the
  Ambulance, Hospital, Power, Water, Shelter and Telecom views offer their own category's groups as units (a category's
  name, any case, its plural s dropped, is the view's name or label), the built-in names when the admin has none.
- Gates: test_app 35/35 (35: teams Echo and Zulu, in no list of the page's, on the EOC's map, Layers and chat tabs).

## r33: each Windows app its own settings ([RT_APP_SETTINGS_APART_V1])

The EOC app (`--lock=eoc --layers=...`) wrote its lock and layers into `~/.openeoc/settings.json`, which the
OpenEOC app read too: it then opened as the EOC, with the EOC's layers, and never asked which app. Now the
OpenEOC app keeps `settings.json`, the EOC app `settings-eoc.json` (its own device id: both may run on one
computer), and only what the computer remembers is read or written (dev, door, name, role, team, unit, comms,
commsApp); the lock and the layers are the app's own, from its command line. An old settings.json with the EOC's lock
in it is read without it.

## r32: which app, first; Diagnostics on the screen

- **Which app?** ([RT_APP_PICKER_V1]) The OpenEOC app (every view: the Windows OpenEOC app, the phone's "app"
  module) opens on a pop-up, one button per view (OpenEOC EOC, Team, Hospital, Ambulance, Water, Power, Shelter,
  Telecom, Public), the one used last marked. The connect screen is then that app's (its title, its fields); Other app on
  it asks again. The EOC, Team and Public apps (locked to one view) do not ask. Connecting by an enrolled phone or a
  lead's card closes it.
- **Diag** ([RT_DIAG_V1]) in the header: the core's account (`op=diag` → `ev diag`) and the page's, on one sheet, again
  on every world, message or error while it is open: the build, view, mission, door, the RAMs the door says and the
  ones in use; every follower (its RAM's host:port, up or DOWN and why, sessions, writes, the tuples it holds by
  variable); the phone's GPS (permission, GPS on, fixes, the last, from `rtNative.gpsState()`); the last position sent
  and whether it was written and is in the positions RAM; whether this device is present; the last message and whether
  it came back; the map's line ([RT_MAP_SAYS_V1]); the layers on; the nodes' GPS answer (databasehost.frognet's
  sensors, or its error); what went wrong lately. A position that could not be written, and a message its RAM did not
  give back in 5 s, are said on the status line. `Follower.counts()` (frognet-ram-java) gives the held tuples.
- Gates: test_app 34/34 (33: the OpenEOC app asks, the Team and Public apps do not; 34: the Team member's Diagnostics
  say its followers up, its position in the positions RAM, its message came back); test_enroll_page, test_admin_page,
  typecheck PASS.

## r31 / admin r10: categories as pop-ups, groups added with their leads, QR codes saved

- **The admin app** ([CATEGORY_DIALOG_V1], rt-admin r10): a card per category; a category opens as a pop-up with its
  groups. Each group: own RAM, no login, Remove group, and its users with Lead, a new password, QR, remove, and an Add
  user row (name, password, lead). **Add group** takes the group's lead and the lead's password (or names a user there
  is, who moves to it): the lead comes with the group. Add category opens its pop-up. There is no Group dropdown on a
  user any more; a user whose group was removed is listed under "Users with no group", moved to a group from there or
  removed. "Other RAMs" is gone from the screen (a configuration's RAMs that are no group are kept as they are).
- **QR save** ([QR_SAVE_V1]): Save image on a user's QR makes a PNG (the code, the user's name, its group and the door
  under it) and hands it to the app, `rtNative.saveFile(name, base64)`: a save dialog on Windows (Pictures first),
  Pictures/OpenEOC on the phone (MediaStore, no permission).
- Gates: `test/test_admin_page.py` (5: the cards; Power with crew1 and its lead pat applied; lou added with a
  password and lead; a removed group's users moved; the saved PNG read by zxing as kim's enrollment line).
  `test/test_admin.py` is stale since r47 (its members are installed the pre-r47 way). test_app 32/32; typecheck PASS.
- rt-app r31 carries the ics layer (r30's boards) and `[RT_MAP_SAYS_V1]` in the installed builds.

## r30: the gateway is a node when its door answers ([GATEWAY_IS_A_NODE_V1])

A phone on a home router that hands out 10.x with a .1 gateway (10.101.113.x, John 2026-11-04) was taken for a node's
LAN by its address alone, so it never brought up its own tunnel. Now the gateway is asked once (its `/dc/ram.php`
answers with `X-FDF-Door`); until it has answered, the phone is not on a node's LAN, and it is asked again when a network
goes. The unit field's names and hint are each view's own (a Phone crew is no longer offered hospitals and Medic units).

## How it is built

| piece | where | what it is |
| --- | --- | --- |
| the RAM library | `../frognet-ram-java` | the FNW1 session, the follower (held reads), JSON; abstracted from the kart app. A write with `own=true` goes with its session |
| the core | `core/` (`AppCore`, the base; `Layer`, `layer/*Layer.java`) | the node's door (`/dc/ram.php`: `op=rams` where the app's RAMs are served now, `op=login` for the EOC), the followers, this device's own writes. Java 8: the same code on both platforms |
| the page | `ui/` (`base.js`, the base; `layers/*.js`) | the screens (Leaflet 1.9.4 from npm, beside them). Talks to the core only by JSON lines: `rtNative.send(line)` out, `rtDeliver(line)` in |
| desktop | `desktop/` | a JavaFX window around the page; `package_windows.sh` makes `OpenEOC-windows-x64-<build>.zip` (its own Java runtime and JavaFX; unzip and run `OpenEOC.cmd`) |
| phone | `android/` | a WebView around the same page, the phone's GPS (every fix is the device's own position). Android Studio, or `./gradlew :app:assembleDebug` |
| the world | `../fdf-source/fdf/bin/openeoc-sim` | 80 simulated instances (teams, ambulances, boats, victims, hospitals, fires, outages, hosts) in the Seattle metro area and Vashon, each on its own session |

The cells (all in OpenEOC's AppRams, at their AppHosts in `/etc/hosts` and their ports in the configuration tuple):

| cell | written by | kind |
| --- | --- | --- |
| `(openeoc-who, <mission>, <dev>)` | each device, each simulated instance | latest, own: present while its writer's session is |
| `(openeoc-pos, <mission>, <dev>)` | the same, on every fix | latest, own; `via` = the node it came in through |
| `(openeoc-msg, <mission>, <dev>.<ms>)` | anyone, to everyone | distinct |
| `(openeoc-team, <mission>, <dev>.<ms>)` | anyone, to a team | distinct, in the team's own RAM (`openeoc.<team>`) |
| `(openeoc-dm, <mission>.<to>, <from>.<ms>)` | anyone, to one device | distinct, in the addressee's own variable: only the addressee reads it |
| `(openeoc-mission, settings, <mission>)` | whoever started the exercise (openeoc-sim) | latest |
| `(openeoc-facility, <mission>, <facility>)` | the hospital's desk (a Hospital client; openeoc-sim for the rest) | latest, not own: it stays |
| `(openeoc-transport, <mission>, <dev>.<ms>)` | an ambulance: a patient on the way to a hospital | distinct |
| `(openeoc-transport, <mission>.act, <id>~<dev>)` | the hospital's desk: acknowledged, arrived | latest |
| `(openeoc-call, <mission>.<to>, <from>.<ms>)` | a caller, in the callee's own variable: a ring (with the caller's node) | distinct |

Gone is gone: a device or instance whose session ended has no cells. What the app draws for it after that, a last known
position (a translucent, dashed avatar, 10 minutes), is the app's own choice and is written nowhere.

Hosts (the nodes things connect through): a node with a GPS records it with the established sensor pattern
(`gpsd_reporter.bash` -> `metric_upsert.sh GPS Position`, gpsd's TPV, in `sensor_data`). The app reads those rows
(`SensorType=GPS`) from `databasehost.frognet` through the node when the Hosts layer is turned on; a row with no fix
is not drawn. Each device's `via` matches a host's `SensorAddress`. openeoc-sim's simulated hosts are `openeoc-pos` cells of kind
`host`.

## The screens (r4)

- **Public** opens on a home, not the map: I need help, I'm safe, Find someone, Report to the EOC, Hospitals &
  services, Map. Help and safe are DisasterComm's help records, `(help, request, <rid>)` in the general RAM (not own: a
  request stays when the phone is gone); Find someone searches them by name. A report is `(openeoc-report, <mission>,
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
  `(openeoc-field, <mission>, <dev>.<ms>)`, the EOC's response `(openeoc-field, <mission>.act, <item>~<dev>)` ([RT_FIELD_V1]).
- **EOC**: starts on the map with teams, resources and hosts; chat is a button away. Requests has the teams' service
  requests (Acknowledge, Dispatched, Done) with the public's requests and reports.
- **Compliance** ([RT_COMPLIANCE_V1]): every node is asked once at the EOC's connect; a node with a working
  environmental sensor (a reading with temperature, humidity, pressure, air quality, gas, radiation, water...) is shown
  OUT OF COMPLIANCE on the status line, as a notice, at the top of Sensors, and as a red host on the map.
- At connect the app says whether it reaches the network's sensor store and how many nodes report there.

## r6

- **Hospital** (connect as Hospital, pick the facility): Status, the desk -- beds available by type (emergency, ICU,
  med/surg, pediatric, burn, operating rooms), ambulance traffic (open, ED diversion, trauma diversion, closed), power,
  water, oxygen, staffing, needs -- published as `openeoc-facility` ([RT_FACILITY_V1]); Inbound, the patients on the way
  there with ETA and triage, Acknowledge and Arrived, and a notice for each new one. The map: hospitals, ambulances,
  hazards. A hospital's popup on any map shows its beds and diversion.
- **Ambulance** (connect as Ambulance, the unit): Unit -- status (Available ... Out of service), Transport a patient
  (the hospitals with beds or their diversion, patients, triage, ETA, chief complaint, age/sex), Assignments (the teams'
  ambulance / medical-evacuation requests, the public's medical requests, Take it); Hospitals, every desk's status.
- **EOC**: Requests adds the transports under way and the hospitals not open to ambulances.
- **Call** ([RT_CALL_V1]): a person's popup has Call beside the chat. The caller picks camera, microphone and speakers
  (the desktop asks `comms-app --list-devices`; the phone lists its front/back camera, microphones and call outputs),
  then the Communicator opens and calls; the callee's app rings, and Answer opens its Communicator to answer, on the
  caller's node's Communicator servers (`<node>:8800`, `<node>:8994`). Desktop: comms-app beside OpenEOC, or chosen
  once (kept in settings.json). Phone: FrogNet Member's Communicator (`net.frognet.communicator.CALL`; that app must be
  the build with [COMMS_OPENED_FOR_A_CALL_V1]).
- openeoc-sim r8: every hospital's desk (three under strain), and each ambulance transport and its arrival.

## r7: the admin app, standalone ([ADMIN_APP_V1])

`OpenEOC-Admin-windows-x64-<build>.zip` (`desktop/package_windows.sh OUTDIR admin`): its own window
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

`android/team` is a second Android app in the same Gradle project, `OpenEOC Team` (`net.frognet.openeoc.team`):
the same shell, core and page; its manifest's `net.frognet.openeoc.ROLE = team` makes it a team member only.
`./gradlew :team:assembleDebug`, or `./build_apk.sh OUTDIR team`. In every view (desktop too):

- [RT_FOLLOW_ME_V1] the map is centred on you, north up, and follows you; moving the map stops that, ⦿ resumes it.
- [RT_NO_GPS_V1] the phone's position is its GPS only. A device with no GPS (or not allowed to use it) asks for the
  street address once (Android's Geocoder looks it up; "lat, lon" is taken as typed), keeps it, and publishes it as
  its position with `"src":"address"`.
- [RT_CALL_V1] the chat's Call beside Send when the To is a person: anyone calls anyone.
- [RT_MISSION_V1] Mission: the mission's settings (name, event, operational period, start, end, commander,
  objectives, area, notes), Update (the whole settings written again), Start a new mission (a new id; "at" = now: the
  mission now is the one started last, on every device). `(openeoc-mission, settings, <mission>)`.
- [DOOR_SAYS_ITS_BUILD_V1] the door says its build (`X-FDF-Door`, and `door` in its admin and roles answers); the admin
  app says which door answered.

## r9: RangerTrak's own radio log in the EOC and team views ([RT_RADIO_LOG_IN_THE_APP_V1])

The radio log app (`fdf-source/fdf/www/rt`, RangerTrak's own screens converted to the RAM) and these apps are on the
same general RAM, mission, presence and positions. Radio log (EOC, Team) shows its entries,
`(openeoc-log, <mission>.<opPeriod>, <dev>.<seq>)`, newest first, as the scribes type them; read only (the ICS-309 message
numbers are the posts').

## r10: the Public app, and the phone ([RT_PUBLIC_APP_V1])

`android/public`: OpenEOC Public (`net.frognet.openeoc.pub`), ROLE = public, opens on the public's home. The
Android project now builds three apps from one copy of the code: `:app` (every view), `:team`, `:public`
(`./gradlew :team:assembleDebug :public:assembleDebug`, or `build_apk.sh OUTDIR team|public`). On the phone:
[APP_SAYS_ITS_BUILD_V1] the build on the first screen; [PAGE_ERRORS_ARE_SAID_V1] a script error on the status line
(and in `adb logcat -s OpenEOC`); [BACK_CLOSES_FIRST_V1] Back closes what is open, then Home on the public view;
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
status line and in Logcat (tag OpenEOC) with the door's reason, the op and the HTTP status.

## The Android project stays where it is opened ([RT_ANDROID_UPDATE_IN_PLACE_V1])

The project in Android Studio is the export `android/studio_project.sh` made (`OpenEOC-Android-Studio-<build>`, the
`shared` module with its own copies of the core, the RAM library and the page). A change comes as
`OpenEOC-Android-Studio-update-<build>.zip` (`android/studio_update.sh BASE_ZIP OUTDIR`: the files that differ from
the export the user has open), unzipped in that project's own folder (`unzip -o`). No new directory, no new import.
(2026-11-02: update zips made for the repository's paths landed in a new opt/ folder inside the open project and were
never built; updates are now made against the project as delivered.)

## r15: the app is the FrogNet member ([RT_IS_A_MEMBER_V1])

As the kart app ([KART_IS_A_MEMBER_V1]): `android/shared` compiles in frognet-android's member (`member/`,
`app/src/main/kotlin`: MemberService, BridgeVpn, Identity), its fdf-tun jniLibs, the WireGuard crypto and its
Communicator. Connect starts the membership with the invitation (`fdf-invite NAME --leaf`, pasted once on the connect
screen and kept), waits for the tunnel (Android says when the VPN network is up), then connects; the RAMs (10.x) are
reached through the tunnel, wherever the phone is. Android asks its VPN question once. Call opens the Communicator in the
app. The member logs to Logcat tag FDF, the app to OpenEOC. minSdk 29 (the member's). The Java-only `build_apk.sh`
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
  `(openeoc-team-host, host, <team>)` into the general RAM (own: gone with the lead's session). The EOC follows those cells:
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
    ./desktop/package_windows.sh OUTDIR COMMS_DIST   # the Windows bundle (COMMS_DIST: the Communicator's Windows dist\)

`test/test_app.py` drives the real core and page (through `test/DevHost.java`) against real fdf-ram RAMs and openeoc-sim:
23 checks (the EOC's world moving, announcements, team and direct messages, a GPS fix on the EOC's map, the public view,
everything gone when openeoc-sim stops and drawn as last known positions, a wrong password refused). `test/world.py` starts
the same world alone, to run a desktop app against.

## Not in this build

- Video inside OpenEOC's own window: a call is the Communicator's window.
- The desktop's answer is in the Communicator (it rings there too): comms-app has no "answer this person" option yet.
- Windows' own location service: a desktop writes no position.
- The APK: `android/build_apk.sh OUTDIR` builds it with Debian/Ubuntu's own Android tools (no Gradle, no Google
  download), signed with `android/debug.keystore`; Android Studio / Gradle builds the same sources. FrogNet Member (the
  phone's Communicator, `frognet-android`) still needs Gradle: it has native parts and Google-hosted dependencies.
- The kart app still has its own copy of the session code; it moves onto `frognet-ram-java` when next built.
- Map tiles are OpenStreetMap's, over the Internet; without it the map is plain and everything is still on it.
