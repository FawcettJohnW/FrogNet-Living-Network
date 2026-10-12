#!/bin/bash
# fdf-mapping.sh -- run on the machine that serves the area's MAPPING system (it stays up while the RAM manager
# moves). Binds the address computed from the area's name (rina-lisp mr-address) on lo (anycast:
# the network's route to 198.18.0.0/15 brings it here), creates the area's site with
# the area's password (RINA_AREA_PASSWORD in /opt/fdf/fdf.env), and runs the mapping host there in the foreground. Everything prints to this terminal.
set -eu; . /opt/fdf/fdf.env
[ -n "$RINA_AREA_PASSWORD" ] || { echo "set RINA_AREA_PASSWORD in /opt/fdf/fdf.env (the area's password; how to make one is there)"; exit 1; }
API=/FawcettInnovations.RINA.ram_interface.php; mkdir -p /opt/fdf/var
MR=$(rina-lisp mr-address "$RINA_AREA" | head -1)
echo "area '$RINA_AREA': mapping system at $MR (computed from the name), on lo"
ip -4 -o addr show dev lo | grep -q " $MR/" || ip addr add "$MR/32" dev lo
( for i in $(seq 100); do (exec 3<>/dev/tcp/$MR/4342) 2>/dev/null && break; sleep 0.05; done; rina-lisp map-site "$RINA_AREA" "$RINA_AREA_PASSWORD" "$MR" ) &
exec env RINA_HOST_ROLE=mapping RINA_REGION_FILE=/opt/fdf/var/map.perm rina-host --listen "$MR:4342" --api $API
