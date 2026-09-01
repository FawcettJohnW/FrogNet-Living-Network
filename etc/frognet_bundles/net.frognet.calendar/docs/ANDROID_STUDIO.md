# Running & testing the Family Calendar Android app in Android Studio

The app (`android/`) is a plain HTTP client for the codex contract — no codec, no
third-party libraries. It builds with the Android Gradle plugin.

## 1. Open the project
1. Android Studio → **File → Open** → select the bundle's `android/` folder.
2. Let Gradle sync. The module is `app`; sources are under
   `app/src/main/java/net/frognet/calendar/`.
3. If prompted, accept the suggested Gradle/AGP versions; nothing else to configure.

## 2. Point the app at a codex
The client defaults to the well-known name:
```kotlin
CalendarClient(base = "http://family-calendar.frognet", ...)
```
- **On the box (real):** leave the default. The device must be on the mesh so its node
  resolves `family-calendar.frognet`. Cleartext HTTP is already allowed in the manifest
  (the mesh is plain HTTP inside the WireGuard envelope).
- **Local dev (mock, no box):** run the dev mock on your machine
  (`python3 dev/mock_codex_server.py`, serves `127.0.0.1:8800`) and point the emulator
  at it. From the Android **emulator**, the host machine is `10.0.2.2`, so construct:
  ```kotlin
  CalendarClient(base = "http://10.0.2.2:8800", suffix = "/unrest/family-calendar")
  ```
  (A physical device on the same LAN: use your machine's LAN IP instead of 10.0.2.2.)

## 3. Run it
1. Pick an emulator (Device Manager → create a Pixel / API 34) or plug in a device with
   USB debugging on.
2. **Run ▶** (`app`). The agenda screen launches: status line, presence line, an
   add-event form (summary + start/end as `2026-06-10T18:00`), and the event list.

## 4. Test the behaviours
- **Add / see:** add an event; it posts to the codex and the list refreshes. With the
  mock running, also open the web UI against the same mock — the event appears in both;
  that's the one shared element.
- **Delete:** tap an event row to delete it.
- **Presence:** the presence line shows other viewers within the 60s window (open the
  web UI too and you'll see both identities).
- **Fail-clean:** stop the mock (or take the device off the mesh). The status line
  switches to "can't reach calendar — contact admin"; it does not show stale data.
- **Split detection:** if the codex serves an older `ts` than the app has already seen
  (a stranded/re-elected host), the app shows "disconnected (split detected)" and stops
  polling rather than forking.

## 5. Instrumented test (optional)
`CalendarClient` is plain Kotlin/JVM with no Android deps, so its parsing/contract logic
can be unit-tested under `app/src/test/` with a local mock URL — mirror the assertions in
`tests/test_calendar_codex.py` (per-reader behaviour is proven server-side there).

> Remember to revert any local `base`/`10.0.2.2` change before shipping — on the box the
> app uses the well-known name and the device's own node resolves it.
