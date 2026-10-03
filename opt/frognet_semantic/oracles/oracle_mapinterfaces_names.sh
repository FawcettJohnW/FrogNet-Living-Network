#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# =============================================================================
# oracle_mapinterfaces_names.sh - [LEGACY_NAMES_ARE_DETECTED_V1]
#
# eth0Name / wlan0Name / wlan1Name were three hard-coded constants. On a box
# whose kernel does not call the wired NIC "eth0" they named devices that do
# not exist:
#
#   setup_lillypad] Resolved eth0 interface: eth0
#   setup_lillypad] WARNING: Interface 'eth0' does not exist.
#
# on a MacBook whose LAN is ens9 (10.199.199.1 + .2), with a USB NIC
# enx0050b6246629 on an upstream 172.16.26.155 lease, a down wlan0, and a USB
# radio wlx90de80b193db.
#
# Drives the REAL detection functions out of mapInterfaces against a faked
# /sys/class/net and a faked `ip`. Red on the constants, green on detection.
#
# Usage: ./oracle_mapinterfaces_names.sh [path/to/mapInterfaces]
# =============================================================================

set -u
HERE="$(dirname "$(readlink -f "$0")")"
MI="${1:-/usr/local/bin/mapInterfaces}"
[ -f "$MI" ] || { echo "FATAL: no mapInterfaces at $MI"; exit 2; }

FAILS=0
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/sys/class/net" "$WORK/etc/hostapd"

# Fake `ip`: serves `-o link show` and `-4 [-o] addr show dev X` from the
# fixture tree.
cat > "$WORK/bin/ip" <<'EOF'
#!/bin/bash
args="$*"
case "$args" in
  *"link show"*)
    n=1
    for d in $(ls "$FAKE_NET"); do
      echo "$n: $d: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"; n=$((n+1))
    done ;;
  *"addr show dev "*)
    dev="${args##*addr show dev }"; dev="${dev%% *}"
    [ -f "$FAKE_NET/$dev/addrs" ] || exit 0
    n=1
    while read -r a; do
      [ -n "$a" ] || continue
      echo "$n: $dev    inet $a brd 10.255.255.255 scope global $dev"
    done < "$FAKE_NET/$dev/addrs" ;;
esac
exit 0
EOF
chmod +x "$WORK/bin/ip"

MI_T="$WORK/mapInterfaces"
sed -e "s|^IP=.*|IP=\"$WORK/bin/ip\"|" \
    -e "s|/sys/class/net|$WORK/sys/class/net|g" \
    -e "s|/etc/hostapd/hostapd.conf|$WORK/etc/hostapd/hostapd.conf|g" \
    "$MI" > "$MI_T"

export FAKE_NET="$WORK/sys/class/net"

mk() {   # mk <dev> <wireless:0|1> [addr ...]
    local d="$1" w="$2"; shift 2
    mkdir -p "$FAKE_NET/$d"
    echo up > "$FAKE_NET/$d/operstate"
    [ "$w" = 1 ] && mkdir -p "$FAKE_NET/$d/wireless"
    : > "$FAKE_NET/$d/addrs"
    for a in "$@"; do echo "$a" >> "$FAKE_NET/$d/addrs"; done
}
reset() { rm -rf "$FAKE_NET"; mkdir -p "$FAKE_NET"; rm -f "$WORK/etc/hostapd/hostapd.conf"; }
ap_on() { printf 'interface=%s\nssid=T\n' "$1" > "$WORK/etc/hostapd/hostapd.conf"; }

names() {  # echoes "eth0Name wlan0Name wlan1Name" as the real file computes them
    ( unset eth0Name wlan0Name wlan1Name
      # shellcheck disable=SC1090
      . "$MI_T" >/dev/null 2>&1
      printf '%s %s %s\n' "${eth0Name:-}" "${wlan0Name:-}" "${wlan1Name:-}" )
}
ck() { if [ "$2" = "$3" ]; then echo "  ok: $1"; else
        echo "  FAIL $1: got [$2] want [$3]"; FAILS=$((FAILS+1)); fi; }

echo "=== the MacBook that failed (BAMacBook) ==="
reset
mk ens9              0 10.199.199.1/24 10.199.199.2/24
mk enx0050b6246629   0 172.16.26.155/24
mk wlan0             1
mk wlx90de80b193db   1 172.16.26.183/24
mk frognet0          0 10.254.0.14/24
ck "ens9 detected as eth0Name, not the literal 'eth0'" "$(names | cut -d' ' -f1)" "ens9"

echo "=== wired selection ==="
reset
mk eth0 0 10.160.160.1/24
mk eth1 0 192.168.0.55/24
ck "the dev owning a served /24 wins" "$(names | cut -d' ' -f1)" "eth0"

reset
mk ens9 0
mk enx1 0 172.16.26.155/24
ck "fresh install: the wired dev with no upstream lease wins" "$(names | cut -d' ' -f1)" "ens9"

reset
mk enp3s0 0
ck "single wired dev with no addresses at all" "$(names | cut -d' ' -f1)" "enp3s0"

reset
mk ens9 0 10.199.199.1/24
mk frognet0 0 10.254.0.14/24
mk wg0 0 10.253.201.2/30
ck "overlay/tunnel devices are never eth0Name" "$(names | cut -d' ' -f1)" "ens9"

echo "=== radios ==="
reset
mk ens9 0 10.199.199.1/24
mk wlan0 1
mk wlx90de80b193db 1 172.16.26.183/24
ap_on wlx90de80b193db
ck "the hostapd radio is wlan0Name even when named wlx..." \
   "$(names | cut -d' ' -f2)" "wlx90de80b193db"
ck "the other radio falls to wlan1Name" "$(names | cut -d' ' -f3)" "wlan0"

reset
mk ens9 0 10.199.199.1/24
mk wlan0 1
mk wlan1 1
ap_on wlan0
ck "conventional names still resolve to themselves" \
   "$(names | cut -d' ' -f2) $(names | cut -d' ' -f3)" "wlan0 wlan1"

reset
mk ens9 0 10.199.199.1/24
ck "no radios: wlan names fall back to the constants" \
   "$(names | cut -d' ' -f2) $(names | cut -d' ' -f3)" "wlan0 wlan1"

echo "=== wireless is decided by the kernel, not the name ==="
reset
mk enx0050b6246629 0 172.16.26.155/24
mk ens9 0 10.199.199.1/24
ck "enx... USB ethernet is not treated as a radio" "$(names | cut -d' ' -f2)" "wlan0"

echo "=== pins ==="
# [PIN_MUST_NAME_A_REAL_DEVICE_V1] The exact BAMacBook situation: a July
# override from another machine pinning a device that does not exist here.
reset
mk ens9 0 10.199.199.1/24
mk enx0050b6246629 0 172.16.26.155/24
mk wlan0 1
mk wlx90de80b193db 1 172.16.26.183/24
mkdir -p "$WORK/etc/frognet"
printf '# AUTO-GENERATED by frognet_fixup.sh on Wed Jul 15\neth0Name="eth0"\nwlan0Name="wlan0"\nwlan1Name="wlan1"\n' \
    > "$WORK/etc/frognet/interfaces_override.conf"
printf 'export eth0Name="eth0"\n' > "$WORK/etc/frognet/active_interface"
sed -i "s|/etc/frognet/interfaces_override.conf|$WORK/etc/frognet/interfaces_override.conf|g; s|/etc/frognet/active_interface|$WORK/etc/frognet/active_interface|g" "$MI_T"
ck "a stale pin naming a nonexistent device is ignored" "$(names | cut -d' ' -f1)" "ens9"
ck "wlan0 pin IS honoured - that device exists here" "$(names | cut -d' ' -f2)" "wlan0"

# An operator pin naming a device that IS present must still win.
printf 'eth0Name="enx0050b6246629"\n' > "$WORK/etc/frognet/interfaces_override.conf"
: > "$WORK/etc/frognet/active_interface"
ck "a pin naming a real device still wins over detection" \
   "$(names | cut -d' ' -f1)" "enx0050b6246629"

rm -f "$WORK/etc/frognet/interfaces_override.conf" "$WORK/etc/frognet/active_interface"

echo "=== sourcing must never write to stderr ==="
# [NO_STDERR_FROM_A_SOURCED_FILE_V1] makeHostJson.bash sources this file as
# `. mapInterfaces 2>&1 > /dev/null`, which aims stderr at its JSON stdout.
# One "Permission denied" there corrupts the JSON, getHosts.php serves [] and
# the monitor shows no peers. Reproduce with an UNREADABLE hostapd.conf.
reset
mk ens9 0 10.199.199.1/24
mk wlan0 1
ap_on wlan0
# The read must happen as an UNPRIVILEGED user, exactly as it does inside
# getHosts.php (www-data). Root reads a 600 file regardless, and a directory
# at that path is skipped by the -f test, so neither reproduces it.
chmod 600 "$WORK/etc/hostapd/hostapd.conf"
_unpriv=""
for _u in www-data nobody ubuntu; do
    id "$_u" >/dev/null 2>&1 && { _unpriv="$_u"; break; }
done
if [ -z "$_unpriv" ]; then
    echo "  SKIP no unprivileged user available to reproduce the 600 read"
else
    chmod -R a+rX "$WORK/bin" "$WORK/sys" "$MI_T"
    chmod a+rx "$WORK" "$WORK/etc" "$WORK/etc/hostapd"
    _cmd="export FAKE_NET='$FAKE_NET'; export PATH='$WORK/bin:/usr/bin:/bin'; . '$MI_T' >/dev/null"
    _err="$(su "$_unpriv" -s /bin/bash -c "$_cmd" 2>&1)"
fi
chmod 644 "$WORK/etc/hostapd/hostapd.conf"
if [ -n "${_err:-}" ]; then
    echo "  FAIL sourcing wrote to stderr with an unreadable hostapd.conf:"
    echo "       $_err"
    FAILS=$((FAILS+1))
else
    echo "  ok: silent on stderr when hostapd.conf is unreadable"
fi
ck "and eth0Name is still detected" "$(names | cut -d' ' -f1)" "ens9"

echo
[ "$FAILS" -eq 0 ] && { echo "RESULT: PASS"; exit 0; }
echo "RESULT: FAIL ($FAILS)"; exit 1
