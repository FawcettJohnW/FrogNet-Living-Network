# Running the Backgammon Android app

1. Android Studio → Open → select the bundle's `android/` folder; let Gradle sync.
   Sources: `app/src/main/java/net/frognet/backgammon/` (BackgammonClient, BoardView,
   MainActivity). No third-party deps (org.json ships with Android).
2. Point the client:
   - Mesh (real): leave `BackgammonClient(base="http://family-backgammon.frognet")`;
     the phone's FrogNet node resolves the name. Cleartext HTTP is allowed in the
     manifest (mesh is plain HTTP inside WireGuard).
   - Local test against the dev server: from the emulator the host machine is `10.0.2.2`,
     so use `BackgammonClient(base="http://10.0.2.2:8888")` with
     `python3 dev/serve_web_test.py` running.
3. Run. Tap a checker (legal sources glow) -> tap a highlighted point to move; Roll /
   Double / New game in the bar; tap the glowing tray to bear off. Fail-clean and
   split-detected states show in the status line.
