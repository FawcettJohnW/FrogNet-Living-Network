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
for b in python3 wg systemctl tar; do command -v $b >/dev/null || { echo "fdf-install: $b is required and missing"; exit 1; }; done
[ -d /opt/frognet_semantic/internet_tunnels_v3 ] || { echo "fdf-install: /opt/frognet_semantic/internet_tunnels_v3 not found: this is not a FrogNet node (install FrogNet first)"; exit 1; }
echo "== unpacking the node bundle into /"
LINE=$(awk '/^__PAYLOAD_BELOW__$/ {print NR + 1; exit 0}' "$0")
tail -n +$LINE "$0" | tar xzf - -C / || { echo "fdf-install: unpack failed"; exit 1; }
cat /opt/fdf/BUILD
# [WG_IMPL_RECORDED_V1 - T4b] the WireGuard implementation (the FrogNet installer recorded it; an older node records it here, once)
/opt/fdf/bin/fdf-wg-impl --record || { echo "fdf-install: no usable WireGuard implementation (above)"; exit 1; }
echo "== joining the region at $RAM (area $AREA)"
exec /opt/fdf/bin/fdf-region-join "$KEY" "$RAM" "$AREA"
__PAYLOAD_BELOW__
