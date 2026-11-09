# OpenEOC UI preview

A clickable preview of the new OpenEOC apps, for evaluating the UI. It runs in a browser from a folder: no install, no server, no network. Unzip it, open `index.html`, and open each app from there in its own tab.

## The apps

- **EOC** (`eoc/`). The FEMA EOC function, live:
  - activation level, incident type, EOC structure (ICS-like, Incident Support Model, Departmental or ESF) and operational periods;
  - staffing by the chosen structure's sections and positions;
  - the eight community lifelines;
  - the organizations on the network, grouped by category;
  - assignments (ICS 204), resource requests (ICS 213RR, from request through demobilization), objectives (ICS 202), significant events and messages.

  It also has **Authorizations**, where the admin gives each organization a login and password. In OpenEOC that is EOCCommandAdmin's job; it is here so the whole flow runs in one place.
- **Field** (`field/`). The app an organization runs: Team, Hospital, Ambulance, Shelter, Fire, Law Enforcement, Power Utility, Water Utility, Public Works or Communications (ARES/RACES).
  - The app is the organization's category at the EOC. The EOC allocates no categories; they appear as apps join.
  - A lead claims the organization's login. If the organization is not on the network, the lead's device hosts it. If it is already on, the claim is refused; there is no replacement.
  - Members join their lead with the team password. A lead can give a member an individual password instead.
  - Each category has its own status report. A Hospital reports beds, a Shelter capacity, an Ambulance its status and destination.
- **EOCAdmin** (`eocadmin/`). The EOC's paperwork, offline:
  - ICS 201, 202, 203, 204, 205, 206, 207, 208, 209, 211, 213, 213RR, 214, 215 and 221, and a SitRep;
  - each form opens filled in from what the EOC and the organizations recorded, can be kept as a draft or made final, and prints;
  - the IAP for an operational period;
  - activity logs (ICS 214);
  - the HSEEP after-action report and improvement plan;
  - the offline package, one file that carries everything to another device.

## How the preview works

**Each browser tab is one device.** All tabs in one browser share one set of records. What one tab writes, the others show at once.

**Records are tuples.** They are (family, instance, bag), as the FrogNet RAM keeps them (`shared/model.js` lists every family). Binding the apps to a real network means replacing `shared/store.js` and nothing else. Its `on()` is where the held read goes.

**Passwords are never kept, only verifiers.** The preview's verifier is a stand-in; the real apps use the door's.

**The QR scans are a pick from a list.** The EOC's QR carries the login, and the lead's QR carries the organization. Neither carries a password.

## A run through

The launcher page (`index.html`) has these steps. In short:

1. **Load the demo.** EOC › Incident › *Load the demo: authorized, no one joined*.
2. **A lead claims a Team.** In a Field tab, pick **Team** and claim `alpha` with password `alpha1`.
3. **A member joins.** In Team Alpha › Members, set a team password. In a second Field tab, join as a member with that password.
4. **A Hospital joins.** In another Field tab, pick **Hospital**, claim `cascade-general` with `cgh1`, and report Divert. Health and Medical turns yellow at the EOC.
5. **The other demo logins:**

   | Login | Password |
   |---|---|
   | `medic-12`, `medic-14` | `medic1` |
   | `island-shelter` | `shelter1` |
   | `cascade-pud` | `pud1` |
   | `ares` | `ares1` |
   | `bravo` | `bravo1` |

   Or authorize your own in EOC › Authorizations.
6. **The EOC works the incident.** Assign, take requests through their steps, set objectives, fill positions.
7. **EOCAdmin does the paperwork.** Open the 209 or the SitRep, make it final, print the IAP.

**Record what you think as you go.** **✎ Note**, on every screen, records your view of that screen. EOCAdmin › Offline package lists your notes and saves everything, notes included, as one file to send back.

**To start over:** EOC › Incident › Close the incident, or EOCAdmin › Offline package › Erase everything here.

## Files

| File | What it is |
|---|---|
| `shared/store.js` | the record store (the RAM's stand-in) |
| `shared/fema.js` | the FEMA/NIMS reference as data: activation levels, structures and positions, lifelines, the apps (categories) and their status fields, RTLT resource names, request states |
| `shared/forms.js` | the forms, as data: fields plus a prefill from the records |
| `shared/model.js` | the record families and the demo incident |
| `shared/shell.js` | the frame: header, navigation, one dialog at a time, status line, form renderer, notes |
| `shared/views.js` | the message view the apps share |
| `eoc/`, `field/`, `eocadmin/` | the apps, one module per screen (`App.module`) |

A new category is an entry in `FEMA.apps`, a new form an entry in `FORMS`, and a new screen an `App.module` call.

The test is `../test/test_ui_preview.py`. It runs the flow above in Chromium, across tabs, and checks every screen at phone width.
