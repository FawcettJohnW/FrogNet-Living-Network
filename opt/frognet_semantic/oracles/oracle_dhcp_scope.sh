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
# oracle_dhcp_scope.sh - pins BOTH halves of the contract:
#
#  A. TEMPLATE FIDELITY. Every non-interface line of /etc/dnsmasq_conf_template
#     survives into opts_only.conf, in order, with only DomainHere/RangeHere
#     substituted. The heredoc in setup_lillypad_v4.bash dropped nine of them
#     and invented two, so this is checked line by line, not spot-checked.
#
#  B. THE RULES, in the template's own notation:
#       served     -> interface=<dev>
#       not served -> # interface=<dev> + no-dhcp-interface=<dev>
#     1. eth0 present -> served
#     2. wlan present AND hosting hostapd -> served
#     3. wlan present AND NOT hosting hostapd -> NOT served
#     4. anything else -> NOT served
#
# Red on the old code (heredoc + single-$SERVED_IF), green on the new.
# Fakes the box: temp /sys/class/net, fake ip/systemctl/mapInterfaces.
#
# Usage: ./oracle_dhcp_scope.sh [scope.sh] [template]
# =============================================================================

set -u
HERE="$(dirname "$(readlink -f "$0")")"
SCOPE="${1:-$HERE/frognet_dhcp_scope.sh}"
TPL="${2:-/etc/dnsmasq_conf_template}"
[ -f "$SCOPE" ] || { echo "FATAL: no scope helper at $SCOPE"; exit 2; }
[ -f "$TPL" ]   || { echo "FATAL: no template at $TPL"; exit 2; }

FAILS=0
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/etc/hostapd"

cat > "$WORK/bin/ip" <<'EOF'
#!/bin/bash
[ -f "$FAKE_LINKS" ] || exit 0
n=1; while read -r d; do [ -n "$d" ] || continue
  echo "$n: $d: <BROADCAST> mtu 1500"; n=$((n+1)); done < "$FAKE_LINKS"
EOF
cat > "$WORK/bin/systemctl" <<'EOF'
#!/bin/bash
[ "${1:-}" = "is-enabled" ] && { cat "$FAKE_HOSTAPD_STATE" 2>/dev/null || echo enabled; exit 0; }
exit 0
EOF
cat > "$WORK/bin/mapInterfaces" <<'EOF'
#!/bin/bash
export eth0Name="${FAKE_ETH0:-eth0}"
export wlan0Name="${FAKE_WLAN0:-wlan0}"
export wlan1Name="${FAKE_WLAN1:-wlan1}"
EOF
chmod +x "$WORK/bin/"*

SCOPE_T="$WORK/scope.sh"
sed -e "s|/usr/local/bin/mapInterfaces|$WORK/bin/mapInterfaces|g" \
    -e "s|/sys/class/net|$WORK/sys/class/net|g" \
    -e "s|/etc/hostapd/hostapd.conf|$WORK/etc/hostapd/hostapd.conf|g" \
    "$SCOPE" > "$SCOPE_T"

export PATH="$WORK/bin:$PATH"
export FAKE_LINKS="$WORK/links" FAKE_HOSTAPD_STATE="$WORK/hostapd_state"
export FROGNET_DNSMASQ_TEMPLATE="$TPL"
# shellcheck disable=SC1090
. "$SCOPE_T"

setup() {
    rm -rf "$WORK/sys"; mkdir -p "$WORK/sys/class/net"; : > "$FAKE_LINKS"
    for d in $1; do mkdir -p "$WORK/sys/class/net/$d"; echo "$d" >> "$FAKE_LINKS"; done
    if [ "$2" = "-" ]; then rm -f "$WORK/etc/hostapd/hostapd.conf"
    else printf 'interface=%s\nssid=Test\n' "$2" > "$WORK/etc/hostapd/hostapd.conf"; fi
    echo "$3" > "$FAKE_HOSTAPD_STATE"
    unset eth0Name wlan0Name wlan1Name
}
ck() { if [ "$2" = "$3" ]; then echo "  ok: $1"; else
        echo "  FAIL $1: got [$2] want [$3]"; FAILS=$((FAILS+1)); fi; }
ckhas(){ if grep -qxF "$2" "$1"; then echo "  ok: has '$2'"; else
          echo "  FAIL missing '$2'"; FAILS=$((FAILS+1)); fi; }
cknot(){ if grep -qxF "$2" "$1"; then echo "  FAIL present '$2'"; FAILS=$((FAILS+1));
        else echo "  ok: absent '$2'"; fi; }

OUT="$WORK/opts_only.conf"

echo "=== A. template fidelity ==="
setup "eth0 wlan0 wlan1 frognet0 wg0" "wlan0" "enabled"
: > "$OUT"
frognet_render_dnsmasq_opts "$OUT" "Seattle6" "10.160.160.3,10.160.160.254,24h" >/dev/null \
  || { echo "  FAIL renderer returned non-zero"; FAILS=$((FAILS+1)); }

MISSING=0; prev=0
while IFS= read -r line; do
    case "$line" in \#*interface=*|interface=*|no-dhcp-interface=*) continue ;; esac
    want="$(printf '%s\n' "$line" | sed -e 's/DomainHere/Seattle6/g' \
             -e 's|RangeHere|10.160.160.3,10.160.160.254,24h|g')"
    n="$(grep -nxF "$want" "$OUT" | head -1 | cut -d: -f1)"
    if [ -z "$n" ]; then
        echo "  FAIL template line lost: '$want'"; FAILS=$((FAILS+1)); MISSING=1
    elif [ "$n" -lt "$prev" ]; then
        echo "  FAIL out of order: '$want'"; FAILS=$((FAILS+1))
    else prev="$n"; fi
done < "$TPL"
[ "$MISSING" -eq 0 ] && echo "  ok: every template line present, substituted, in order"

for d in domain-needed bogus-priv clear-on-reload cache-size=5000 \
         dhcp-authoritative "local=/.Seattle6/" "local=/Seattle6/" \
         "dhcp-script=/usr/local/bin/dhcp_tracking.sh" "dhcp-option=19,1" \
         "domain=Seattle6" "expand-hosts"; do
    ckhas "$OUT" "$d"
done
cknot "$OUT" "dhcp-option=option:router,10.160.160.1"
cknot "$OUT" "dhcp-option=option:dns-server,10.160.160.1"

echo "=== B. rules ==="
srv() { frognet_dhcp_served_ifaces | sort | tr '\n' ' ' | sed 's/ *$//'; }

setup "eth0 wlan0 wlan1 frognet0 wg0" "wlan0" "enabled"
ck "eth0 + AP on wlan0 + managed wlan1 -> both served" "$(srv)" "eth0 wlan0"
setup "eth0 wlan0 wlan1 frognet0" "wlan0" "masked"
ck "hostapd masked -> wlan0 not served, eth0 still is" "$(srv)" "eth0"
setup "eth0 wlan0 wlan1" "wlan1" "enabled"
ck "AP on wlan1 -> eth0 + wlan1" "$(srv)" "eth0 wlan1"
setup "eth0 frognet0 wg0" "-" "enabled"
ck "wired only, no hostapd.conf -> eth0" "$(srv)" "eth0"
setup "wlan0 frognet0" "wlan0" "enabled"
ck "AP only, no eth0 -> wlan0" "$(srv)" "wlan0"
setup "eth0 wlan0 frognet0 wg0 tun0 eth1" "wlan0" "enabled"
ck "overlay/tunnel/extra uplink never served" "$(srv)" "eth0 wlan0"
setup "eth0 wlx00c0ca" "wlx00c0ca" "enabled"
ck "AP on a non-wlanN device is served" "$(srv)" "eth0 wlx00c0ca"
setup "frognet0 wg0" "-" "enabled"
ck "nothing servable -> empty" "$(srv)" ""

echo "=== C. rendered interface block ==="
setup "eth0 wlan0 wlan1 frognet0 wg0" "wlan0" "enabled"
: > "$OUT"; frognet_render_dnsmasq_opts "$OUT" "Seattle6" "10.1.1.3,10.1.1.254,24h" >/dev/null
ckhas  "$OUT" "interface=eth0"
ckhas  "$OUT" "interface=wlan0"
cknot  "$OUT" "no-dhcp-interface=eth0"
cknot  "$OUT" "no-dhcp-interface=wlan0"
ckhas  "$OUT" "no-dhcp-interface=wlan1"
ckhas  "$OUT" "no-dhcp-interface=frognet0"
ckhas  "$OUT" "no-dhcp-interface=wg0"

echo "=== D. truncates, never accumulates ==="
before="$(wc -l < "$OUT")"
frognet_render_dnsmasq_opts "$OUT" "Seattle6" "10.1.1.3,10.1.1.254,24h" >/dev/null
frognet_render_dnsmasq_opts "$OUT" "Seattle6" "10.1.1.3,10.1.1.254,24h" >/dev/null
ck "three renders, same length" "$(wc -l < "$OUT")" "$before"
ck "one no-dhcp-interface=wlan1, not three" \
   "$(grep -cxF 'no-dhcp-interface=wlan1' "$OUT")" "1"

echo "=== E. refuses rather than writing unscoped ==="
setup "frognet0 wg0" "-" "enabled"
OUT2="$WORK/opts2.conf"; : > "$OUT2"
if frognet_render_dnsmasq_opts "$OUT2" "X" "Y" >/dev/null; then
    echo "  FAIL returned success with nothing servable"; FAILS=$((FAILS+1))
elif [ -s "$OUT2" ]; then
    echo "  FAIL wrote to the conf when it should have refused"; FAILS=$((FAILS+1))
else echo "  ok: refuses, writes nothing"; fi

echo
[ "$FAILS" -eq 0 ] && { echo "RESULT: PASS"; exit 0; }
echo "RESULT: FAIL ($FAILS)"; exit 1
