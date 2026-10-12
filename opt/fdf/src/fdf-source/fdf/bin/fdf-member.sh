#!/bin/bash
# fdf-member.sh ROUTER -- run on each member machine. ROUTER: where this machine reaches the reserved prefix
# 198.18.0.0/15 (in a real deployment, anycast routing; for this test, the RAM manager machine's IP). That route is the
# network's, not the area's: the member is still given only the area's name (RINA_AREA in /opt/fdf/fdf.env) and computes
# the Map-Resolver's address from it. Runs the agent in the foreground: it joins by name, announces itself, and follows
# the RAM manager if it moves. Everything prints to this terminal. Ctrl-C stops it.
set -eu; . /opt/fdf/fdf.env
if [ -n "${RINA_RAM:-}" ]; then                               # [FDF_DIRECT_V1] the RAM server is named outright (a droplet):
    echo "area '$RINA_AREA': RAM server at $RINA_RAM (given directly; no mapping system used)"   # connect straight to it
    exec rlite-uipcps
fi
ROUTER=${1:?usage: fdf-member.sh ROUTER   (or set RINA_RAM=<ram-server>:8840 in /opt/fdf/fdf.env)}
MR=$(rina-lisp mr-address "$RINA_AREA" | head -1)
ip -4 route show 198.18.0.0/15 | grep -q . || ip route add 198.18.0.0/15 via "$ROUTER"   # the reserved prefix, unless already routed
echo "area '$RINA_AREA': mapping system at $MR, reached $(ip -4 route get "$MR" | head -1)"
exec rlite-uipcps
