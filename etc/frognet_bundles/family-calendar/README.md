# Family Calendar — UnREST bundle (pluggable unit)

First module of the **core family distro**. Installs as one unit: the OSS calendar
backend (perm authority) + the UnREST codex (live shared element) + web and Android
clients + tests.

## What it is
- **Perm authority:** Radicale (OSS), unmodified, holds the durable copy on a
  well-known perm host. Standard CalDAV clients can still talk to it directly.
- **Live shared element:** `family-calendar.events` in the transient DB. Every
  household reads/writes this one element → one calendar, no drift. The codex writes
  through to perm and refaults the element from perm on host re-election.
- **New over native (Radicale unchanged):** one live element all households see update
  in real time (no CalDAV two-copy merge), `family-calendar.presence` shows who's
  viewing, and clients fail clean / self-terminate on split instead of stale-syncing.

## Install (single tar)
The bundle ships as one tar that lays the code into `/etc/frognet_bundles` and carries
the web assets with it:
```
sudo tar xzf family-calendar_bundle.tar.gz -C /          # → /etc/frognet_bundles/family-calendar
sudo /etc/frognet_bundles/family-calendar/install_family_calendar.sh
```
The installer puts the OSS backend + codex in place, copies the web UI from the bundle
to the Apache docroot, wires the suffix route, and registers the well-known name.

## Run & test
- Web UI in a browser: see `docs/WEB_TEST.md`
- Android app in Android Studio: see `docs/ANDROID_STUDIO.md`
- Dev mock (no box needed): `python3 dev/mock_codex_server.py` (serves the five verbs
  on `127.0.0.1:8800` for both clients)

## Codex verbs (HTTP behind local Apache at `/unrest/family-calendar/<verb>`)
- `GET  list`               → `{version, ts, events:[…]}`  (refaults from perm if cold)
- `POST create  {event}`    → event  (perm-first, then live element)
- `POST update  {uid,…}`    → event
- `POST delete  {uid}`      → ok
- `POST presence {who}`     → `{who_is_here:[…]}`

## Files
```
install_family_calendar.sh          pluggable installer (OSS + codex + web + Apache + name)
codex/calendar_codex.py             the codex (CalendarCodex + injectable stores)
tests/test_calendar_codex.py        unit + integration (real-codec per-reader DIFF)
web/index.html                      single-file web UI (no browser storage; fail-clean)
android/…/CalendarClient.kt         Android contract client (plain HTTP; no codec in app)
android/…/MainActivity.kt           minimal agenda UI
android/…/AndroidManifest.xml, build.gradle
```

## Tests
```
python3 tests/test_calendar_codex.py
```
Runs here with in-memory stores; on the box swap in the api.php TransientStore and the
Radicale PermStore. Integration test (`I1`) exercises the REAL SemanticCodec.

## Box wiring TODO (VERIFY-AGAINST-INSTALL — do not invent)
1. **TransientStore → api.php.** Confirm the timestamp field (server column vs. inside
   `jsonData`) and exact `upsert_by_name` params against the live endpoint, then
   implement `TransientStore.get/upsert` over `http://databasehost.frognet/api.php`.
2. **PermStore → Radicale.** Implement `load_all/put/remove` over local CalDAV
   (127.0.0.1:5232). Confirm collection path/config on the box.
3. **WSGI bridge.** Wire `codex/wsgi.py` verbs → `CalendarCodex` and enable the Apache
   suffix route (mechanism per box: mod_wsgi or proxy to a local worker).
4. **Name registration.** Register `family-calendar.frognet` via the box's standard
   service-registration path (renewing lease). Never hardwire an IP.
5. **Per-device identity.** Set `who` for presence per device.

## Conventions honored
- No `pipefail`. Licensing referred to as open source only. Resolve `databasehost.frognet`
  by name, never hardwired. Clients are plain HTTP; the fabric does the codec.
- Wire-bound timestamps are **strings**, not int32 (the codec's int field overflows on
  epoch-millis — proven by the integration test; carry `ts` as a string).
