# T4c brief — the last chains and the upgrade clean-up

Tree /home/claude/clean (git; T0..T4b committed). Read docs/cleanup-2026-10/T4b.md ("Found, not done"). Edit in place,
do NOT commit. John's rule: one mechanism per job; a failure is logged and retried on the next event, never a second
mechanism. Code is the truth.

## Do
1. "One setting, several sources" — make each ONE source, logged when absent (do not touch live._dns_upstream: that
   one waits on John's answer):
   - the tunnel daemon's region settings (tunnel.conf / environment / join.env / site.env — the REGION switch line names
     four): one file is the switch (join.env, which fdf-region-join writes and fdf-leave removes); the others are not
     read for it. Old RINA_RAM in tunnel.conf: the upgrade step (frognet_prune_removed T3 section) removes it.
   - getEth0Address: the LAN dev's 10.x only. If F6 needs the address before the LAN is up, have F6 read what the
     installer itself set (it knows the IP it chose), not a second runtime source.
   - iproute.tear_down_wg: one way (`ip link del` of a wg device the daemon made; if it was made by wg-quick, that is a
     different owner — find out which the daemon uses to bring up and use the matching teardown only).
   - the merge hook: a request to the merge watcher only; when the watcher is not running (region mode stops it via
     fdf-region-join), the hook must not run runMerge itself — decide from what region mode needs at boot/interface
     events (T3a: runMerge stays for boot/interface change). One owner: either the watcher always runs in both modes, or
     the hook always runs runMerge; pick the one the code supports with fewer moving parts and say why.
   Extend docs/cleanup-2026-10/static-no-fallback.sh with patterns for these.
2. Upgrade clean-up of files the tree no longer ships: the installer's upgrade step runs ALL of
   usr/local/bin/frognet_prune_removed.sh (every section, not only T3), and frognet_prune_removed.sh gains a T1 section
   listing every file T1/T2/T3/T4 deleted from usr/local/bin, usr/local/sbin, etc/systemd/system, var/www/html and
   opt/frognet_semantic (take the lists from `git diff --name-status 42465dd HEAD | awk '$1=="D"'` limited to paths a
   node install would have had). The 9 pip console scripts in /usr/local/bin (cbor2, dotenv, gpsdclient, gunicorn,
   gunicornc, httpx, idna, normalizer, cffi-gen-src): the old installer's A3 put them there; remove the console scripts
   only if their package is not also an OS package (check with dpkg -S), and do not pip-uninstall anything.
3. Re-run G4 (upgrade over a T0-baseline install, reuse docs/cleanup-2026-10/t4a-tools) and show /usr/local/bin after
   upgrade contains only files the tree ships plus OS-owned files. Re-run G1 and G3 (region) to show nothing broke, the
   static checks, selfcheck, discovery oracles, the netns region-loop + LAN-only tests (same temporary DOCKER-USER
   allowance as T4a/T4b if needed).
Write docs/cleanup-2026-10/T4c.md (each change one line with why, gates with command + result). Final message: 5 lines.
