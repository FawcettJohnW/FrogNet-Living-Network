#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_region_migrate.sh -- [REGIONS_PER_MACHINE_V1] `fdf-region-create --migrate` on a region holder an earlier build
# made (/opt/fdf/hub/hub.env, /opt/fdf/hub/admitted, /opt/fdf/var/org-key, fdf-hub.service ...), in a private mount
# namespace (/opt/fdf and /etc/systemd/system are tmpfs there; systemctl is a stand-in that records what it is asked).
# Checks: the region is in /opt/fdf/hub/<AREA>/ with its members, its key, FDF_IFACE and FDF_KEY (the key it had);
# the old units are stopped and removed, the per-region ones enabled and started; a second --migrate changes nothing.
# [MIGRATE_ONLY_OWN_REGION_V1] first, a hub.env naming another machine (Seattle7 held a copy of StreamingFrog's): refused,
# nothing moved, no unit touched.
#   Needs root, unshare.  usage: tools/test_region_migrate.sh
H=$(cd "$(dirname "$0")/.." && pwd); F=$H/../fdf
exec unshare -m bash -s "$F" <<'IN'
F=$1; FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
mount -t tmpfs tmpfs /opt && mkdir -p /opt/fdf/bin /opt/fdf/var /opt/fdf/hub/admitted
mkdir -p /etc/systemd/system && mount -t tmpfs tmpfs /etc/systemd/system
cp $F/bin/fdf-region-create /opt/fdf/bin/; S=$(mktemp -d); printf '#!/bin/sh\necho "systemctl $*" >> %s/calls\n' $S > $S/systemctl; chmod +x $S/systemctl; export PATH=$S:$PATH
printf 'RINA_RAM=10.253.253.254:51888\nFDF_AREA=frognet\nFDF_HUB_NAME=StreamingFrog\nFDF_ENDPOINT=165.232.153.144:50820\nFDF_LISTEN=50820\n' > /opt/fdf/hub/hub.env
/opt/fdf/bin/fdf-region-create --migrate > $S/out0 2>&1; RC=$?
check $([ $RC != 0 ] && [ -f /opt/fdf/hub/hub.env ] && [ ! -e /opt/fdf/hub/frognet ] && [ ! -e $S/calls ] && grep -q "not this machine's" $S/out0 && echo 0 || echo 1) \
  "0 another machine's hub.env is refused, nothing moved or started: $(cat $S/out0)"
printf 'RINA_RAM=10.253.253.254:51888\nFDF_AREA=frognet\nFDF_HUB_NAME=%s\nFDF_ENDPOINT=10.170.170.1:43354\nFDF_LISTEN=43354\n' "$(hostname -s)" > /opt/fdf/hub/hub.env
echo '{"name":"pi2","edge":"10.253.1.1"}' > /opt/fdf/hub/admitted/pi2.json; echo k > /opt/fdf/var/org-key
touch /etc/systemd/system/fdf-hub.service /etc/systemd/system/fdf-identity.service /etc/systemd/system/fdf-org.service
/opt/fdf/bin/fdf-region-create --migrate > $S/out 2>&1; RC=$?
D=/opt/fdf/hub/frognet
check $([ $RC = 0 ] && [ -f $D/hub.env ] && [ ! -e /opt/fdf/hub/hub.env ] && [ -f $D/admitted/pi2.json ] && [ "$(cat $D/org-key)" = k ] && echo 0 || echo 1) \
  "1 region frognet is in $D: $(cd $D && find . -type f | sort | tr '\n' ' ') (rc $RC)"
check $(grep -q '^FDF_IFACE=wg-frognet$' $D/hub.env && grep -q '^FDF_KEY=/etc/wireguard/fdf-hub.key$' $D/hub.env && grep -q '^FDF_LISTEN=43354$' $D/hub.env && echo 0 || echo 1) "1 its hub.env keeps its settings and names its interface and the key it had: $(tr '\n' ' ' < $D/hub.env)"
check $(grep -q 'disable --now fdf-hub.service fdf-identity.service fdf-org.service' $S/calls && [ ! -e /etc/systemd/system/fdf-hub.service ] && grep -q 'start fdf-hub@frognet.service fdf-identity@frognet.service' $S/calls && grep -q 'start fdf-org@frognet.service' $S/calls && echo 0 || echo 1) \
  "2 the old units stopped and removed, the region's own started: $(grep -E 'disable|start' $S/calls | tr '\n' ';')"
/opt/fdf/bin/fdf-region-create --migrate > $S/out2 2>&1
check $([ $? = 0 ] && grep -q "nothing to move" $S/out2 && echo 0 || echo 1) "3 again: $(cat $S/out2)"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
IN
