#!/bin/bash
# fdf-ram-manager.sh [ROUTER] -- run on the machine that is the area's RAM manager. Runs it in the foreground, port 8840,
# on the address this machine reaches the mapping system FROM (its route's source address); it registers its own address with the area's mapping system (found from the area's
# name), signed with the area's password (RINA_AREA_PASSWORD in /opt/fdf/fdf.env). ROUTER: on a machine other than the mapping's, where it reaches 198.18.0.0/15 (the mapping machine's IP).
# To MOVE it: Ctrl-C here, start it on another machine or interface; members follow by themselves.
set -eu; . /opt/fdf/fdf.env
[ -n "$RINA_AREA" ] || { echo "set RINA_AREA in /opt/fdf/var/area.env (the area's name; /opt/fdf/fdf.env says how)"; exit 1; }
[ -n "$RINA_AREA_PASSWORD" ] || { echo "set RINA_AREA_PASSWORD in /opt/fdf/var/area.env (the area's password; how to make one is in /opt/fdf/fdf.env)"; exit 1; }
API=/FawcettInnovations.RINA.ram_interface.php; mkdir -p /opt/fdf/var
[ -n "${1:-}" ] && { ip -4 route show 198.18.0.0/15 | grep -q . || ip route add 198.18.0.0/15 via "$1"; }
MR=$(rina-lisp mr-address "$RINA_AREA" | head -1)
IP=$(ip -4 route get "$MR" | grep -o 'src [0-9.]*' | cut -d' ' -f2)
if ip -4 route get "$MR" | grep -q '^local '; then     # the mapping is on THIS machine: register this machine's own address
    IP=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2)       # the default route's source, if any
    [ -n "$IP" ] || IP=$(ip -4 -o addr show scope global | grep -v ' lo ' | awk '{print $4}' | cut -d/ -f1 | head -1)
fi
[ -n "$IP" ] || { echo "no route to the mapping system at $MR (give ROUTER: the mapping machine's IP)"; exit 1; }
echo "area '$RINA_AREA': RAM manager at $IP:8840"
exec env RINA_MAP_AREA="$RINA_AREA" RINA_AREA_PASSWORD="$RINA_AREA_PASSWORD" RINA_REGION_FILE=/opt/fdf/var/area.perm rina-host --listen "$IP:8840" --api $API
