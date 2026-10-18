#!/bin/bash
# fdf-kart-ram.sh -- the kart demo's own region (Ribbit: each application has its own memory), on the droplet beside
# FD&F's RAM server. Port 8799, API /ram.php. Each game holds one FNW1 session to it ([KART_ON_THE_SESSION_V1]) and its
# tuples go with that session ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]: needs this rina-host at fdf-2026-10-18-r21 or later).
# It can be the kart lobby's main memory: players run  python3 /opt/fdf/kart/kartdemo.py --name <name> --memory <droplet>:8799
# and its file (kart.perm) keeps their results and records across restarts ([PERMANENT_CELL_V1], r22 or later).
# Foreground; everything prints to this terminal. Open TCP 8799 to the players.
set -eu; . /opt/fdf/fdf.env; mkdir -p /opt/fdf/var
echo "kart region on port 8799 (players: --race-host <this machine's address>:8799)"
exec env -u RINA_MAP_AREA RINA_REGION_FILE=/opt/fdf/var/kart.perm rina-host --listen 0.0.0.0:8799 --api /ram.php
