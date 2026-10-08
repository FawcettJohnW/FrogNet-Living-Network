#!/bin/bash
# fdf-ram-server.sh -- [FDF_DIRECT_V1] the area's RAM server when members are given its address directly (a droplet):
# members set RINA_RAM=<this machine's public address>:8840 in /opt/fdf/fdf.env. No mapping system, no password, no
# registration. Listens on every address, port 8840, in the foreground; everything prints to this terminal.
# (It says once that the area's name is not registered: there is no mapping system in this setup.)
set -eu; . /opt/fdf/fdf.env; mkdir -p /opt/fdf/var
echo "RAM server on port 8840 (members: RINA_RAM=<this machine's address>:8840)"
exec env -u RINA_MAP_AREA RINA_REGION_FILE=/opt/fdf/var/area.perm rina-host --listen 0.0.0.0:8840 --api /FawcettInnovations.RINA.ram_interface.php
