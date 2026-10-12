#!/bin/bash
# fdf-install -- [FDF_INSTALL_V1] one file, one command: joins THIS FrogNet node to the FD&F region.
#   usage:  sudo ./fdf-install-ARCH.run <SITE KEY> [RAM host:port] [area]
# What it does, in order, and says so: checks root/arch/python3/wg; unpacks the embedded node bundle to /
# (/opt/fdf, the FrogNet region patch under /opt/frognet_semantic, propogateNotificationInternal); runs
# /opt/fdf/bin/fdf-region-join (site.env, join.env, daemon restart, fdf-facts + fdf-client units, 15 s report).
# Re-runnable. The FrogNet install itself is left as it is except for the patched modules listed by the unpack.
set -u
[ "$(id -u)" = 0 ] || { echo "fdf-install: run as root (sudo)"; exit 1; }
KEY=${1:-${FDF_SITE_KEY:-}}; [ -n "$KEY" ] || { echo "usage: $0 <SITE KEY> [RAM host:port] [area]   (or FDF_SITE_KEY in the environment)"; exit 2; }
RAM=${2:-165.232.153.144:51888}; AREA=${3:-frognet}
WANT_ARCH="__ARCH__"; M=$(uname -m)
[ "$M" = "$WANT_ARCH" ] || { echo "fdf-install: this file is for $WANT_ARCH, this machine is $M"; exit 1; }
for b in python3 wg systemctl tar; do command -v $b >/dev/null || { echo "fdf-install: $b is required and missing"; exit 1; }; done
[ -d /opt/frognet_semantic/internet_tunnels_v3 ] || { echo "fdf-install: /opt/frognet_semantic/internet_tunnels_v3 not found: this is not a FrogNet node (install FrogNet first)"; exit 1; }
[ -f /etc/frognet/tunnel.conf ] && [ ! -f /etc/frognet/tunnel.conf.broker ] && cp /etc/frognet/tunnel.conf /etc/frognet/tunnel.conf.broker && echo "== saved /etc/frognet/tunnel.conf.broker (the way back: fdf-leave, restore it, restart the daemon)"
echo "== unpacking the node bundle into /"
LINE=$(awk '/^__PAYLOAD_BELOW__$/ {print NR + 1; exit 0}' "$0")
tail -n +$LINE "$0" | tar xzf - -C / || { echo "fdf-install: unpack failed"; exit 1; }
cat /opt/fdf/BUILD
echo "== joining the region at $RAM (area $AREA)"
exec /opt/fdf/bin/fdf-region-join "$KEY" "$RAM" "$AREA"
__PAYLOAD_BELOW__
