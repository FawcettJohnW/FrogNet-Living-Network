#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# matrix.sh [MINUTES] -- the whole observer-visibility matrix, as root: every condition (A B Bt C D Dall) and traffic type
# (chat, telemetry) for seeds 1-10 (Bt: B with Nagle off in Mosquitto and the clients, baseline only [OBSVIS_BT_V1]), then the stress phases (chat, seeds 7-10: netem-delay netem-loss server move
# revoke) on B C D Dall (A has no server to change, move behind or revoke from; B no tunnel to revoke), then the
# analysis and the report.
# MINUTES: each run's length (default 30, the brief's). Seeds 7-10 are the held-out ones: the hypotheses in
# HYPOTHESES.md are recorded before they run (this script refuses to start if that file is missing).
set -u
cd "$(dirname "$0")"
M=${1:-30}
[ "$(id -u)" = 0 ] || { echo "matrix.sh: as root"; exit 1; }
[ -s HYPOTHESES.md ] || { echo "matrix.sh: HYPOTHESES.md (the pre-registered hypotheses) is missing"; exit 1; }
[ -x build/chat-ram ] || ./build.sh
fail=0
for s in 1 2 3 4 5 6 7 8 9 10; do for t in chat telemetry; do for c in A B Bt C D Dall; do
  [ -f results/$c-$t-s$s/controller.json ] && grep -q '"ok": true' results/$c-$t-s$s/controller.json && continue
  python3 run.py $c $t $s --minutes $M || fail=$((fail+1))
done; done; done
for st in netem-delay netem-loss server move revoke; do for s in 7 8 9 10; do
  cs="B C D Dall"; [ $st = revoke ] && cs="C D Dall"      # revocation is the server's WireGuard dropping a peer: B has none
  for c in $cs; do
  [ -f results/$c-chat-s$s-$st/controller.json ] && grep -q '"ok": true' results/$c-chat-s$s-$st/controller.json && continue
  python3 run.py $c chat $s --minutes $M --stress $st || fail=$((fail+1))
done; done; done
python3 analyze.py results --json results/analysis.json
python3 report.py results --out results/REPORT.md
echo "matrix.sh: $fail run(s) failed"
