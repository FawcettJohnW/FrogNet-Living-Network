# T6a — the documents an operator and a developer read, matching the tree (2026-10-13)

Not committed. Against T5b (581e89d): documents only, plus the three `opt/fdf/dist` files rebuilt (they carry
`opt/fdf/README.md` and the `hub/` documents) and one new script, `check-docs.sh`. No program changed. Working log:
`t6a-logs/NOTES.md`. The renames below are `git mv` (staged as renames, not committed).

## Written or rewritten (one line each, why)

- `docs/OPERATING.md` (new): the one operator guide. What FrogNet is, the two modes, the units per role (taken from a
  live node, below); install LAN-only (options, prompts, F6's 26 checks + echo + snapshot, time, what to look at);
  `--region`; `fdf-region-create` and its firewall lines; the region host's update; phones (the Android tree, built
  with an Android SDK: no APK ships); Windows PCs (the bridge, wintun.dll, ponds.txt, Administrator); the Communicator's
  servers (`fdf-comms.sh`) and how clients find them; upgrade (`--preserve`, what C1c removes), leave, uninstall,
  reinstall; what is running (landing page, getSnapshot.php, `frognet_monitor --once`, dashboard, `/fdf/`, every build
  line and its log); requirements and limits; troubleshooting by symptom from the log lines the code writes (each
  string grepped in the code).
- `README.md` (root): what the repo is, the three specifications (a paragraph each, from the Croakus Part 0), where to
  start, the tree map (site/ and docs/cleanup-2026-10 included), the licence, the gate commands as they now are. The
  old install story, "seven phases", Bookworm, the release-building story, the broker and the 58-node ceiling are gone.
- `usr/local/share/frognet/README.md`: the same file as the root's. **Choice: the root is the source; the share copy is
  a byte copy**, as `AI_READ_FIRST.md`, `CLAIMS.md`, `COPYRIGHT` and `LICENSE` there already were; `check-docs.sh` (d)
  fails when they differ. Not "the installer installs the root one": `full_tar.bash` and the release's world tar read
  `/usr/local/share/frognet` on a live node, and C1 copies files by their tree path, so that would be an installer
  change in a documents tranche.
- `AI_READ_FIRST.md`: checksum item 11 (the hub terminates the tunnels in a region, not the broker); rule 9 says what
  FD&F's meeting point is and that it is not a FrogNet controller; the 58-node limit replaced by what the code and the
  gates show; "a broker" (generic) -> "a message bus".
- `CLAIMS.md`: every claim names the test or log in this tree that shows it, or is in "recorded elsewhere; not shown
  here" or "not shown". The outage time is the log's (55 s between disconnect and reconnect), not T4b's prose (49 s).
- `opt/frognet_semantic/{AI_README.md, README_FIRST.md, core/, discovery/, internet_tunnels_v3/}AI_README.md`,
  `usr/local/bin/AI_README.md`, `var/www/html/AI_README.md`, `docs/ENGINEERING_POSTURE.md`: counts from the tree (38
  core files, 144 discovery files, 84 oracles run), the reading order (the moved primer is history), the mode switch,
  guid rotation as the code does it, the snapshot pages; the `_fix_meta/` trap removed (no such directory).
- `opt/frognet_semantic/simulation/SIM_STATUS.md`: new, the current tiers from this tranche's run; the 1,215-line June
  log moved to history.
- `opt/fdf/README.md` (= `src/fdf-source/fdf/README.md`): the status matrix from the gates in this directory (netns,
  containers, hardware-before-the-cleanup, each cell sourced); install pointer; the build as T4b ran it; the older
  8840 set-ups kept, with `/opt/fdf/var/area.env` (no default area since T4b) and no droplet address.
- `opt/fdf/hub/README.md` (= source copy): what fdf-hub does now (edge 10.253.A.B, one owner per /24, wg.impl),
  fdf-region-create, the oracle command (27 checks, run here: `t6a-logs/hub_oracle.txt`).
- `opt/fdf/windows/bridge/README.txt` (and its source, `src/frognet-android/windows/README.txt`, CRLF aside): header
  a12/b8 -> a15/b11 (BUILD.txt; the strings in fdf-tun.exe and member.jar agree); the ponds.txt example step.
- `opt/fdf/windows/README-windows.txt`: labelled history (the 8840 demo client folder), pointing at the bridge.
- Kept design docs, their state lines made true: `WINDOWS-BRIDGE.md` (a15/b11 shipped, a12/b8 was the hardware run;
  hub name generic), `ANDROID-HANDOFF.md` (state line: a15 in the tree, handset report, no APK, pond setup; the example
  log line generic), `PLAN-PONDS.md` (no mediahost election).
- `var/www/html/README.md`: labelled history (an older dashboard's notes).

## Moved to history (header line `history: superseded by docs/OPERATING.md`, content otherwise unchanged)

- `opt/fdf/src/fdf-source/docs/` -> `docs/history/`: DEPLOY-REGION-2026-10-09, HANDOFF-FDF-2026-10-08,
  HANDOFF-OPTIMIZATION-r19, HANDOFF-RAM-DISCOVERY-2026-10-09, HANDOFF-REGION-2026-10-09, HANDOFF, MERGE-ANDROID-r19-a11,
  MERGE-r19-a12..a15, PLAN-FROGNET-ON-FDF, PLAN-RAM-DISCOVERY, TRANSITION-ANDROID, TRANSITION-OPTIMIZATION,
  TRANSITION-README, WORKLOG-PONDS, WORKLOG-r17 (18 .md), and the four release diffs, five run logs and two dashboard
  PNGs they cite. Kept in docs/: PLAN-BRIDGE, PLAN-PONDS, WINDOWS-BRIDGE, EXP3-SIDE-BY-SIDE, FINDINGS-FOR-DINO.
- DEPLOY-REGION's still-true parts are in OPERATING.md: the 10.253/16 pre-check on the region host and the update
  command. `opt/fdf/hub/` and `src/fdf-source/fdf/hub/` keep one-line pointers at the old names of DEPLOY-REGION,
  HANDOFF-REGION and PLAN-FROGNET-ON-FDF, so an untar of the new fdf-droplet.tgz overwrites the old copies on a region
  host. (The hub copy of HANDOFF-REGION differed from the docs/ copy in one package hash; the docs/ copy is kept.)
- Duplicates, one copy each: the Android docs live in `src/frognet-android/docs` (ANDROID-HANDOFF,
  COMMUNICATOR-ANDROID-r5; the fdf-source copies deleted); PLAN-BRIDGE, PLAN-PONDS (the fdf-source one, which has the
  2026-10-10 correction), WINDOWS-BRIDGE live in fdf-source/docs (the Android copies deleted); MERGE-r19-a14/a15 and
  WORKLOG-PONDS only in fdf-source/docs/history. ANDROID-PROGRESS and COMMUNICATOR-ANDROID-r1 -> the Android tree's
  `docs/history/`.
- `opt/frognet_semantic/` -> `docs/history/`: FROGNET_NOW, FROGNET_NOW_guid_identity, FROGNET_PRIMER_load_me_first
  (one-line pointers left at the old paths: they ship to nodes), SIM_STATUS (as SIM_STATUS-2026-06.md), AI_HANDOFF
  (its read order named the primer as the architecture), SOTF_HANDLER_INSTALL (installs core/sotf_handler.py, removed
  in T2), `var/www/html/README.txt` (as www-html-README.txt: an older CLI bundle not in the tree; pointer left).
  `opt/frognet_semantic/docs` is not in the node file set, so the history does not reach nodes.

## check-docs.sh

`bash docs/cleanup-2026-10/check-docs.sh` (`CHECK_DOCS_LIST=1` lists each command it checked). Over 34 documents
(those above that are not history, every AI_README.md, the kept design docs):
(a) every path in backticks, markdown links and bare etc/ opt/ usr/ var/ docs/ site/ or /... paths exists: relative to
the document's directory or any above it, or one of the project roots (opt/frognet_semantic, opt/fdf,
fdf-source, rinab, frognet-android, docs/cleanup-2026-10, the bridge, the Communicator); a short path matches a tracked
file holding its directories in order; a run-time path (/var/log/..., /opt/fdf/var/...) or bare runtime file name
must be named by the tree's code (whole-path match); a 12-entry REF_ALLOW with reasons covers build outputs, files the
operator adds on Windows, website pages AI_READ_FIRST/CLAIMS cite, and URL paths on the node.
(b) OPERATING.md's 32 code-block commands (shlex-split, `cd` followed): each program is in the tree at the path a node
or the checkout has it, or an OS command. (c) WORDS = broker, passcode, GROUP_TOKEN, mediahost, 165.232.153.144,
StreamingFrog, /home/claude, /mnt/user-data, /tmp/claude-0: 3 matches, all allowed (the gate script's file name twice,
and the daemon's own log line `old broker keys ignored` quoted in OPERATING.md). (d) the copies above.
Result: **0 problems** (`t6a-logs/check-docs.txt`). Planted errors (a bogus path, a bogus program, a bogus command, the
word, a changed share copy) give 7 problems and exit 1 (`t6a-logs/check-docs-planted.txt`).

## Gates (logs in t6a-logs/)

| gate | command | result |
|---|---|---|
| check-docs | `bash docs/cleanup-2026-10/check-docs.sh` | **0 problems**, 34 documents, 32 commands |
| static-no-broker | `bash docs/cleanup-2026-10/static-no-broker.sh` | **209 allowed, 0 violations**; strict 4 allowed, 0 |
| static-no-fallback | `bash docs/cleanup-2026-10/static-no-fallback.sh` | **43 allowed, 0 violations** |
| selfcheck | README's `PYTHONPATH=$PWD PYTHONUTF8=1 python3 -m discovery selfcheck` in opt/frognet_semantic | **14/14** |
| discovery oracles | README's command | **PASS, 84 pass, 0 regression** |
| simulator | README's run_all command | 2 red, the same as T4b–T5 (collectives/DDP, aiconnect) |
| netns (README's four commands, from rinab, DOCKER-USER bridged-frames allowance while they ran) | lan-only / region-loop / NOKEY=a / ponds-daemon | **PASS 19/19 / 13 + 1 SKIP / 17 + 1 SKIP / 21** (region and NOKEY exit 1 after RESULT PASS, as before) |
| hub oracle | `FDF_PY=opt/fdf/lib/python python3 opt/fdf/hub/hub_oracle.py` | PASS, 27 |
| dist | `tools/make_deliverables.sh opt/fdf/dist` | x86_64.run 31,065,553 B be3bf4d88be5feec; aarch64.run 28,148,279 B 6a13a258993e34d7; droplet.tgz 23,216,980 B 0c4e6f85e661d14f (05:15Z). Against the T5b files only opt/fdf/BUILD (built=), README.md and hub/*.md differ |

### OPERATING.md section 2 followed literally (t6a-logs/walk-lan-only.txt, walk-install.txt, walk-lan-only-state.txt)

A fresh container `w1` (`t4a-tools/node.sh w1 FrogNetHost.NODE1 172.30.0.51 fn-lan1:10.61.61.1:eth1`: Ubuntu 24.04
x86-64 with systemd, uplink eth0, LAN eth1), then command by command:
- `sudo apt-get install -y git`: rc 0.
- `git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git`: rc 0, and `cd FrogNet-Living-Network` works;
  the clone is the public repo, e889208 (before this cleanup). The install then ran from `/root/FrogNet`, this tree as
  `put-tree.sh` copies it (what the clone will hold once this is pushed). Substitution, recorded.
- `sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --name NODE1 --ip 10.61.61.1 --no-ssid-projection`:
  asked the database password twice (typed on a pty), nothing else; 26 `+` + echo + snapshot, "All checks passed.",
  04:55:06 -> 05:00:35 (5 min 29 s), then "Rebooting." The container stopped on the reboot and was started again
  (`reboot.sh`).
- `journalctl -b -u frognet-tunnel-daemon-v3 --no-pager | grep -E '...'`: the three lines the doc names
  (`LAN-only mode: no region switch ...`, `Starting frognet-tunnel-daemon v3.0.0 build frognet-tunnel-2026-10-12-t5
  (mode lan-only) base fdf-2026-10-12-r20`, `LAN-only loop started ...`).
- `curl -s http://127.0.0.1:8080/getSnapshot.php | python3 -m json.tool`: `"mode": "lan-only"`, member NODE1 (self),
  elections databasehost 10.61.61.1, errors [].
- `/usr/local/bin/frognet_monitor --once`: the frame, rc 0.
- `systemctl --failed`: `frognet-dbhost-advertise.service` failed, as the doc says for the first minutes; 10 minutes
  later no failed unit, the advertiser finished, `databasehost.frognet` -> 10.61.61.1.
- From the same node, for the doc's tables: the enabled units, ports 80/8080/8443/9009, landing page 200 on :80 and
  :8080, dashboard 200, `/fdf/` 403, wg.impl `wireguard-go`, resolv.conf 127.0.0.1 + the gateway, the merge lines in
  /var/log/frognet-merge-watcher.log. Two rows of the units table were corrected from it: frognet-transit-watch
  (restarted every 5 s) and frognet-dbhost-elect.timer (not enabled on a fresh install). Container removed.

## Found, not done

- `frognet-transit-watch.service` restarts every 5 s for ever: `usr/local/bin/frognet_transit_watch.sh` is `set -x;
  exit 0` under `Restart=always` (13 restarts in the first minute on w1). Code, not documents.
- The installer never enables `frognet-dbhost-elect.timer` (F1 enables only the advertiser); `fdf-leave` enables it, so
  a node that left a region runs the elector timer and a fresh LAN-only node does not.
- `core/frognet_secret.py` still falls back to a published default secret (CRITICAL on every derivation); the core
  AI_README describes it truthfully.
- An upgrade (`--preserve`) of a region node was not gated; OPERATING.md says to re-run the `.run` after it. F1 enables
  the advertiser timer that fdf-region-join disabled.
- The phone build command (`./gradlew :app:assembleDebug`) was not run: no Android SDK here.
- Dev paths remain in recorded artefacts and dated notes that were not rewritten: `ribbit/examples/lispers.net/artifacts/*`
  (run outputs; they ship with opt/frognet_semantic), `ribbit/examples/lispers.net/HANDOFF-S3.md`, the Croakus
  `notes/VERIFY-*.md`, `site/CHANGES.txt`, and every file under a history/ directory.
- The Croakus (and site/) still describes the broker in its chapters 19–22 and the "From Clean to the Communicator"
  walkthrough; README.md says so. Its rewrite is not in this brief.
- A region host keeps old files a new fdf-droplet.tgz no longer has (no prune there); the three hub documents are
  overwritten by pointers for that reason.
- The Android app's built-in first pond is still John's droplet (`Identity.kt:36`; T4b).
