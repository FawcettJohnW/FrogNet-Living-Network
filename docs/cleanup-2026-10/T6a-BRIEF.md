# T6a brief — the documentation an operator and a developer read, matching the tree as it now is

Tree /home/claude/clean (git; T0..T5b committed). Read docs/cleanup-2026-10/T3a.md, T3b.md, T4a.md, T4b.md, T4c.md,
T5.md first: they say what the system does now. Edit in place, do NOT commit. Rules: code is the truth (read it before
writing any sentence about it); no invented numbers or test claims; every command you write must run as written on the
platform it names; plain declarative voice, short sentences, no hype; commands given to an operator never redirect
output away from the terminal.

## Write / rewrite
1. ONE operator guide: docs/OPERATING.md (repo root docs/), the only place an operator needs:
   - what FrogNet is in 5 lines; the two modes (LAN-only; region: RAM manager + fdf-hub; a region needs no internet);
     what runs where (a table of units per role: node LAN-only, node region, region host).
   - install a node LAN-only (frognet_install.sh options, what it asks, what it checks at F6, how long, what to look
     at after); install with --region; create a region (fdf-region-create, the firewall lines it prints); add phones
     (the FrogNet Android app: where it is in the tree, how it is built — say plainly no APK ships) and Windows PCs
     (opt/fdf/windows/bridge: wintun.dll from wintun.net, ponds.txt example, run as Administrator); start the
     Communicator servers on a node (fdf-comms.sh) and the desktop/phone clients find them.
   - upgrade (--preserve; what it removes), leave (fdf-leave), uninstall (--uninstall / --keep-identity), reinstall.
   - see what is running: the node landing page, getSnapshot.php, frognet_monitor (--once), the dashboard, /fdf/,
     journalctl units; every component's build line and where it is logged.
   - requirements and limits: supported OS (what was actually tested: Ubuntu 24.04 x86-64 in containers; Raspberry Pi
     OS on John's hardware per prior field use — say "not re-qualified after this cleanup" where true), WireGuard
     choice recorded at install, DNS upstream = default route's next hop ([DNS_NEXTHOP_ONLY_V1]; the edge router must
     serve DNS) — unless the tree now does otherwise, the pond cap / known limits from the code.
   - troubleshooting by symptom, from the logs the code writes (grep the log strings you cite).
2. README.md (root): what this repo is, the three specifications (Ribbit, FrogNet, FD&F) in a paragraph each, the tree
   map (top-level dirs and what each holds, including site/ and docs/cleanup-2026-10), where to start (OPERATING.md,
   the Croakus), licence GPL-2.0-only, how to run the gates (the T0 table's commands, as they now are). Remove the
   broker, the old install story, "seven phases" etc. usr/local/share/frognet/README.md: same file (one copy, or make
   the installer install the root one — pick one source).
3. AI_READ_FIRST.md, CLAIMS.md, opt/frognet_semantic/{AI_README.md, README_FIRST.md, FROGNET_NOW*.md,
   FROGNET_PRIMER_load_me_first.md}, internet_tunnels_v3/AI_README.md, simulation/SIM_STATUS.md, core/AI_README.md,
   usr/local/bin/AI_README.md and other AI_README files: make every statement about how the system works now true;
   history that is labelled as history may stay; claims in CLAIMS.md each with the test or log that shows it, or
   marked not shown. Remove dev paths (/home/claude, /mnt/user-data, /tmp/claude-0) from shipped docs.
4. opt/fdf/README.md and opt/fdf/src/fdf-source/fdf/README.md (one content), opt/fdf/hub/README.md, the DEPLOY-REGION
   docs (opt/fdf/hub/DEPLOY-REGION-2026-10-09.md and its copies): DEPLOY-REGION is superseded by OPERATING.md — move
   it under opt/fdf/src/fdf-source/docs/history/ with a one-line pointer, or fold its still-true parts into
   OPERATING.md. The status matrix in opt/fdf/README.md: update to what the gates in docs/cleanup-2026-10 now show.
5. opt/fdf/src/fdf-source/docs/: put superseded transition/handoff/merge docs under docs/history/ (keep, do not edit
   their content beyond a header line "history: superseded by docs/OPERATING.md"); keep current design docs
   (PLAN-BRIDGE, PLAN-PONDS, WINDOWS-BRIDGE, ANDROID-HANDOFF, COMMUNICATOR-ANDROID-r5) and make their "current state"
   lines true. Same for frognet-android/docs duplicates (one copy each: pick the android tree for android docs).
6. opt/fdf/windows/bridge/README.txt (a12/b8 vs BUILD a15/b11 — read BUILD.txt), opt/fdf/windows/README-windows.txt
   (8840 demo region): current or history.
7. A docs link check: script docs/cleanup-2026-10/check-docs.sh that (a) finds every relative path / file reference in
   the docs you touched and checks it exists, (b) extracts every shell command in OPERATING.md code blocks and checks
   each named program/script exists in the tree at the path a node would have (or is an OS command), (c) greps the
   docs you touched for broker/passcode/GROUP_TOKEN/mediahost/165.232.153.144/StreamingFrog outside history/. Run it:
   0 problems.
Gates: check-docs.sh 0; both static checks 0; selfcheck 14/14 (docs only, but run it). Then, in a fresh container
(docs/cleanup-2026-10/t4a-tools; dockerd: `(dockerd > /tmp/dockerd.log 2>&1 &)`), follow OPERATING.md's LAN-only
install section literally, command by command, and record that each worked (or fix the doc). Remove containers.
Write docs/cleanup-2026-10/T6a.md. Final message: 4 lines.
