#!/bin/bash
# fdf-install -- [FDF_INSTALL_V1] one file, one command: joins THIS FrogNet node to the FD&F region.
#   usage:  sudo ./fdf-install-ARCH.run <SITE KEY> <RAM host:port> <area>
# All three are required: the region's site key, its RAM manager's address as this node reaches it, and its area
# name (fdf-region-create AREA PUBLIC_IP printed all three). There is no built-in region. The FrogNet installer runs
# this file itself with --region (frognet_install.sh ... --region RAM --site-key KEY --area AREA).
# What it does, in order, and says so: checks root/arch/python3/wg; unpacks the embedded node bundle to /
# (/opt/fdf, the FrogNet region patch under /opt/frognet_semantic, propogateNotificationInternal); runs
# /opt/fdf/bin/fdf-region-join (site.env, join.env, daemon restart, fdf-facts + fdf-client units, 15 s report).
# Re-runnable. The FrogNet install itself is left as it is except for the patched modules listed by the unpack.
set -u
# [UMASK_IS_OURS_V1 - T4a] what this lays down under /etc, /opt and /usr must not take its modes from the caller's
# umask (a shell with umask 0000 made /opt/fdf and its bin/ world-writable, in the T4a container).
umask 022
[ "$(id -u)" = 0 ] || { echo "fdf-install: run as root (sudo)"; exit 1; }
[ $# -eq 3 ] || { echo "usage: $0 <SITE KEY> <RAM host:port> <area>   (all three required; fdf-region-create printed them)"; exit 2; }
KEY=$1; RAM=$2; AREA=$3
WANT_ARCH="__ARCH__"; M=$(uname -m)
[ "$M" = "$WANT_ARCH" ] || { echo "fdf-install: this file is for $WANT_ARCH, this machine is $M"; exit 1; }
# inotifywait (inotify-tools): the FrogNet merge watcher waits with it, and runs in region mode too since T4c (the
# NetworkManager hook's merges are requests to it); a node installed before T4c may not have it [WATCHER_HAS_INOTIFY_V1]
for b in python3 wg systemctl tar inotifywait apache2ctl curl; do command -v $b >/dev/null || { echo "fdf-install: $b is required and missing$( [ $b = inotifywait ] && echo ' (apt-get install inotify-tools)')"; exit 1; }; done
[ -d /opt/frognet_semantic/internet_tunnels_v3 ] || { echo "fdf-install: /opt/frognet_semantic/internet_tunnels_v3 not found: this is not a FrogNet node (install FrogNet first)"; exit 1; }
# [NODE_HAS_THE_VENV_V1] the bundle's FrogNet files (runMerge.bash, the tunnel daemon's region code) run on the venv the
# FrogNet installer builds ([SERVICES_ON_THE_VENV_V1]). A node installed before that has none: unpacking would leave its
# merges failing "status=no_python". Stop before anything is written, and say what to do.
[ -x /opt/frognet_semantic/venv/bin/python3 ] || { echo "fdf-install: /opt/frognet_semantic/venv/bin/python3 not found: this node's FrogNet predates the venv its files now run on. Nothing was changed. Upgrade FrogNet first (sudo bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve, from a checkout of the repository), then run this again."; exit 1; }
echo "== unpacking the node bundle into /"
LINE=$(awk '/^__PAYLOAD_BELOW__$/ {print NR + 1; exit 0}' "$0")
tail -n +$LINE "$0" | tar xzf - -C / || { echo "fdf-install: unpack failed"; exit 1; }
cat /opt/fdf/BUILD
# [ONE_MONITOR_TRUTH_V1 - T5] the bundle's sudoers fragment: getSnapshot.php (www-data) reads the hub link's handshake
# with exactly `wg show all latest-handshakes`. sudo wants it 0440; one that visudo refuses is taken out again (a bad
# fragment would stop sudo for everyone) and this install stops.
chmod 0440 /etc/sudoers.d/frognet-snapshot && visudo -cf /etc/sudoers.d/frognet-snapshot >/dev/null \
  || { rm -f /etc/sudoers.d/frognet-snapshot; echo "fdf-install: /etc/sudoers.d/frognet-snapshot refused by visudo; removed"; exit 1; }
# [FDF_WEB_UNDER_APACHE_V1 - T5b] the bundle's vhost (the tree's 000-default.conf) serves /opt/fdf/www/fdf at /fdf/ on
# the node's Apache: checked, reloaded, and asked once. Any step that fails stops this install, and says which.
apache2ctl configtest >/dev/null 2>&1 || { echo "fdf-install: apache2ctl configtest refuses the vhost:"; apache2ctl configtest; exit 1; }
systemctl reload apache2 || { echo "fdf-install: systemctl reload apache2 failed (systemctl status apache2)"; exit 1; }
C=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/fdf/fdf_dashboard.html)
[ "$C" = 200 ] || { echo "fdf-install: http://127.0.0.1:8080/fdf/fdf_dashboard.html answered HTTP $C, not 200"; exit 1; }
echo "== FD&F region view: http://<this node>/fdf/ (Apache, /opt/fdf/www/fdf)"
# [WG_IMPL_RECORDED_V1 - T4b] the WireGuard implementation (the FrogNet installer recorded it; an older node records it here, once)
/opt/fdf/bin/fdf-wg-impl --record || { echo "fdf-install: no usable WireGuard implementation (above)"; exit 1; }
echo "== joining the region at $RAM (area $AREA)"
exec /opt/fdf/bin/fdf-region-join "$KEY" "$RAM" "$AREA"
__PAYLOAD_BELOW__
