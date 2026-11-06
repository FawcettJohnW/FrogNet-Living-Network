#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# smoke.sh [COND] [TRAFFIC] [SEED] [MINUTES] -- one condition and seed, short, as root (default: D chat 1, 2 minutes).
set -u
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "smoke.sh: as root"; exit 1; }
[ -x build/chat-ram ] || ./build.sh
python3 run.py ${1:-D} ${2:-chat} ${3:-1} --minutes ${4:-2} --out results-smoke/${1:-D}-${2:-chat}-s${3:-1}
