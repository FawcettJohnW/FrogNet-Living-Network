# T6a working notes

## Facts gathered (each from code or a record; source named)
- Installer build frognet-install-2026-10-12-t5 (frognet_install.sh INSTALL_BUILD). Log: /var/log/frognet_install.log (tee).
- Install time, x86-64 Ubuntu 24.04 containers (first log line -> last): T5b g1 6m03s, n1 6m46s; T5 g1 4m52s; T4c g1 9m21s.
  So "5-10 minutes" on that host. Pi not measured after the cleanup.
- E1 prompts: Node name, Gateway IP (10.X.Y.1), ping-conflict Continue?, SSID projection (only when the wired candidate
  has no carrier and an AP-capable wl* exists), AP iface choice, country, WiFi passphrase (when projection on), Confirm
  (skipped when --name and --ip both given). DB password collected before everything (prompt twice, hidden) unless --db-pass.
  --region without --site-key/--area prompts for them.
- F6 checks: 26 chk lines + echo + snapshot (t5b g1-install.txt shows them all "+").
- Phases list: D1 first, then A0..F8, R between F7 and F8.

## Progress
- docs/OPERATING.md drafted (sections 1-7). README.md rewritten. AI_READ_FIRST.md: item 11 (hub), rule 9 (FD&F note),
  limits (58-node ceiling gone), "message bus". CLAIMS.md rewritten: each claim -> test/log, or "not shown".
- usr/local/share/frognet/README.md: decision = root is the source, the share copy is a byte copy (as AI_READ_FIRST,
  CLAIMS, COPYRIGHT, LICENSE already are); check-docs.sh enforces cmp. Reason: full_tar.bash and the release's world
  tar read /usr/local/share/frognet on a live node; C1 copies files by tree path. Changing C1 = installer change.
- Walkthrough w1 started 04:54Z (t6a-logs/walk-lan-only.txt, walk-install.txt). git clone of the public repo works
  (gets e889208, pre-cleanup); install runs from /root/FrogNet (this tree).
- check (c) word list: the two literal survivors so far: `static-no-broker.sh` (file name) and the daemon's log line
  `old broker keys ignored` (config.py:242). Allow-list them with reasons.
- Moved to history (header line added, content unchanged): opt/frognet_semantic/{FROGNET_NOW,FROGNET_NOW_guid_identity,
  FROGNET_PRIMER_load_me_first}.md (one-line pointers left at the old, shipped paths), simulation/SIM_STATUS.md ->
  docs/history/SIM_STATUS-2026-06.md (new current SIM_STATUS.md written from this run), docs/AI_HANDOFF.md,
  docs/SOTF_HANDLER_INSTALL.md, var/www/html/README.txt (pointer left). fdf-source/docs: 18 docs + diffs/logs/pngs to
  docs/history/. Android dup docs: one copy each. hub/ copies of DEPLOY/HANDOFF/PLAN -> pointers (both trees).
- Gates on host: selfcheck 14/14; oracles 84 PASS; run_all 2 red (DDP, aiconnect) as T4b-T5; static-no-broker 209/0;
  static-no-fallback 43/0; check-docs 0 (planted errors caught: t6a-logs/check-docs-planted.txt).
- Walkthrough: all LAN-only commands worked (walk-lan-only.txt). Found: frognet-transit-watch restarts every 5 s
  (its script is `set -x; exit 0`); dbhost-elect.timer not enabled by the installer (fdf-leave enables it).
- Done 05:3xZ: T6a.md written; netns 19/19, 13+1, 17+1, 21; dist rebuilt; containers removed; nothing committed.
