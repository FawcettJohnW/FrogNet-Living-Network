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
# oracle_default_route_resolver.sh - [DEFAULT_ROUTE_IS_ALWAYS_A_RESOLVER_V1]
#
# The gateway on the default route must reach /etc/resolv.conf, and must serve
# as the dnsmasq upstream when exit_host.tsv has not been written yet.
#
# Before: resolv.conf only fell back to the default route when exit_host.tsv
# was absent or had an empty field 5, so a stale exit_host entry naming an
# unreachable nameserver left the box unable to resolve despite a good default
# route. And NEXT_HOP (the dnsmasq upstream) had no fallback at all: no
# exit_host.tsv meant an empty upstream file, and since resolv.conf points
# every local client at 127.0.0.1, nothing external resolved.
#
# Runs the REAL manageResolv.bash against a faked `ip`, a temp /etc tree and a
# stub mapInterfaces. Red on the old script, green on the new.
#
# Usage: ./oracle_default_route_resolver.sh [path/to/manageResolv.bash]
# =============================================================================

set -u
HERE="$(dirname "$(readlink -f "$0")")"
MR="${1:-/usr/local/bin/manageResolv.bash}"
[ -f "$MR" ] || { echo "FATAL: no manageResolv.bash at $MR"; exit 2; }

FAILS=0
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/etc/sentinels" "$WORK/etc/dnsmasq.d"

cat > "$WORK/bin/ip" <<'EOF'
#!/bin/bash
case "$*" in
  *"addr show"*)
    # SELF_NETS comes from this; without it the self-exclusion cannot fire.
    echo "2: ens9    inet 10.199.199.1/24 brd 10.199.199.255 scope global ens9"
    echo "2: ens9    inet 10.199.199.2/24 brd 10.199.199.255 scope global ens9" ;;
  *"route show default"*) cat "$FAKE_DEFAULT" 2>/dev/null ;;
  r|route|"r "*|"route "*)  cat "$FAKE_ROUTES" 2>/dev/null ;;
  *) cat "$FAKE_ROUTES" 2>/dev/null ;;
esac
exit 0
EOF
cat > "$WORK/bin/hostname" <<'EOF'
#!/bin/bash
echo "FrogNetHost.BAMacBook"
EOF
cat > "$WORK/bin/debugTag" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$WORK/bin/mapInterfaces" <<'EOF'
#!/bin/bash
export eth0Name=ens9 wlan0Name=wlan0 wlan1Name=wlan1
EOF
cat > "$WORK/bin/pkill" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$WORK/bin/systemctl" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$WORK/bin/"*

MR_T="$WORK/manageResolv.bash"
sed -e "s|/usr/local/bin/mapInterfaces|$WORK/bin/mapInterfaces|g" \
    -e "s|/usr/local/bin/debugTag|$WORK/bin/debugTag|g" \
    -e "s|/etc/sentinels|$WORK/etc/sentinels|g" \
    -e "s|/etc/dnsmasq.d|$WORK/etc/dnsmasq.d|g" \
    -e "s|/etc/resolv.conf|$WORK/etc/resolv.conf|g" \
    -e "s|/sys/class/net|$WORK/sys/class/net|g" \
    "$MR" > "$MR_T"
chmod +x "$MR_T"

export PATH="$WORK/bin:$PATH"
export FAKE_ROUTES="$WORK/routes" FAKE_DEFAULT="$WORK/defaultroute"

printf 'domain=BAMacBook\n' > "$WORK/etc/dnsmasq.d/opts_only.conf"

tunnel() {  # tunnel <dev> <peer ip> <name> <up|down>
    mkdir -p "$WORK/sys/class/net/$1"
    # the real writer (orchestrate.py) leads with this header line
    [ -s "$WORK/etc/sentinels/tunnel_peers.tsv" ] || \
        printf '# dev\tprimary_ip\tname -- written by the merge\n' \
            > "$WORK/etc/sentinels/tunnel_peers.tsv"
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$WORK/etc/sentinels/tunnel_peers.tsv"
    echo "$4" > "$WORK/sys/class/net/$1/operstate"
}
setup() {   # setup "<default route line or ->" "<exit_host.tsv line or ->"
    rm -rf "$WORK/sys"; mkdir -p "$WORK/sys/class/net"
    rm -f "$WORK/etc/sentinels/tunnel_peers.tsv"
    rm -f "$WORK/etc/resolv.conf" "$WORK/etc/sentinels/exit_host.tsv" \
          "$WORK/etc/sentinels/dnsmasq_upstream.conf"
    if [ "$1" = "-" ]; then : > "$FAKE_DEFAULT"; else echo "$1" > "$FAKE_DEFAULT"; fi
    {
      echo "10.199.199.0/24 dev ens9 proto kernel scope link src 10.199.199.1"
      [ "$1" = "-" ] || echo "$1"
    } > "$FAKE_ROUTES"
    [ "$2" = "-" ] || printf '%b\n' "$2" > "$WORK/etc/sentinels/exit_host.tsv"
}
run() { bash "$MR_T" >/dev/null 2>&1; }
ckhas() { if grep -qxF "$2" "$1" 2>/dev/null; then echo "  ok: $3"; else
           echo "  FAIL $3 (missing '$2' in $(basename "$1"))"; FAILS=$((FAILS+1)); fi; }

DEFROUTE="default via 172.16.26.1 dev enx0050b6246629 proto dhcp metric 100"

echo "=== resolv.conf ==="
setup "$DEFROUTE" "-"
run
ckhas "$WORK/etc/resolv.conf" "nameserver 172.16.26.1" \
      "no exit_host.tsv: default gateway is a nameserver"

# The regression: exit_host.tsv names a nameserver, so the old fallback never
# ran and the default gateway never reached resolv.conf.
setup "$DEFROUTE" "peer\\t10.199.199.9\\tens9\\t10.199.199.9\\t10.9.9.9"
run
ckhas "$WORK/etc/resolv.conf" "nameserver 10.9.9.9" \
      "exit_host nameserver is still listed"
ckhas "$WORK/etc/resolv.conf" "nameserver 172.16.26.1" \
      "default gateway listed EVEN WITH a populated exit_host.tsv"

setup "$DEFROUTE" "-"
run
n="$(grep -c '^nameserver 172.16.26.1$' "$WORK/etc/resolv.conf")"
if [ "$n" = "1" ]; then echo "  ok: listed once, not duplicated"; else
  echo "  FAIL duplicated ($n times)"; FAILS=$((FAILS+1)); fi

ckhas "$WORK/etc/resolv.conf" "nameserver 127.0.0.1" \
      "local dnsmasq is still first"

echo "=== dnsmasq upstream ==="
setup "$DEFROUTE" "-"
run
ckhas "$WORK/etc/sentinels/dnsmasq_upstream.conf" "nameserver 172.16.26.1" \
      "no exit_host.tsv: upstream falls back to the default route"

setup "$DEFROUTE" "peer\\t10.199.199.9\\tens9\\t10.199.199.9\\t10.9.9.9"
run
ckhas "$WORK/etc/sentinels/dnsmasq_upstream.conf" "nameserver 10.199.199.9" \
      "exit_host.tsv still wins for the upstream next hop"

echo "=== no default route at all ==="
setup "-" "-"
run
if [ -s "$WORK/etc/sentinels/dnsmasq_upstream.conf" ]; then
    echo "  FAIL invented an upstream with no default route"; FAILS=$((FAILS+1))
else
    echo "  ok: no default route -> empty upstream (honest state)"
fi
if grep -q '^nameserver 172' "$WORK/etc/resolv.conf" 2>/dev/null; then
    echo "  FAIL invented a WAN nameserver"; FAILS=$((FAILS+1))
else
    echo "  ok: no default route -> no WAN nameserver invented"
fi

echo "=== tunnel peers are resolvers ==="
setup "$DEFROUTE" "-"
tunnel wg9 10.199.199.1 Self     up
tunnel wg2 10.250.250.1 Seattle5 up
tunnel wg3 10.130.130.1 Seattle3 up
run
ckhas "$WORK/etc/resolv.conf" "nameserver 10.250.250.1" \
      "wg2 peer (Seattle5) is a nameserver"
ckhas "$WORK/etc/resolv.conf" "nameserver 10.130.130.1" \
      "wg3 peer (Seattle3) is a nameserver"
# The sentinel is the answer: every row is listed, the merge owns what is in
# it. The one exclusion is ourselves - 127.0.0.1 already covers own-pond names
# and a duplicate burns a timeout slot ([PEER_PRIMARY_IS_A_RESOLVER_V1]).
if grep -qxF "nameserver 10.199.199.1" "$WORK/etc/resolv.conf"; then
    echo "  FAIL our own .1 was listed as a peer"; FAILS=$((FAILS+1))
else
    echo "  ok: our own .1 is not listed"
fi
if grep -q '^nameserver #' "$WORK/etc/resolv.conf"; then
    echo "  FAIL the header comment line was parsed as a peer"; FAILS=$((FAILS+1))
else
    echo "  ok: the sentinel header line is skipped"
fi
ckhas "$WORK/etc/resolv.conf" "nameserver 172.16.26.1" \
      "default gateway still listed alongside tunnel peers"

# The default route's gateway knows no pond. Ahead of a tunnel peer, every
# FrogNet name burns a timeout against the WAN before reaching a resolver that
# has it. It goes LAST.
_gw_line="$(grep -n '^nameserver 172.16.26.1$' "$WORK/etc/resolv.conf" | cut -d: -f1)"
_last_peer="$(grep -n '^nameserver 10\.' "$WORK/etc/resolv.conf" | tail -1 | cut -d: -f1)"
if [ -n "$_gw_line" ] && [ -n "$_last_peer" ] && [ "$_gw_line" -gt "$_last_peer" ]; then
    echo "  ok: default gateway is listed AFTER every tunnel peer"
else
    echo "  FAIL default gateway at line ${_gw_line:-?} is not after the last"
    echo "       peer at line ${_last_peer:-?}"
    FAILS=$((FAILS+1))
fi

echo "=== the python path (discovery/live.py) ==="
# runMerge only calls manageResolv.bash when LEGACY_TAIL=1; the live path is
# live.py. Both must obey the same rule, or fixing the bash fixes nothing.
LIVE="${FROGNET_LIVE_PY:-/opt/frognet_semantic/discovery/live.py}"
if [ ! -f "$LIVE" ]; then
    echo "  SKIP live.py not found at $LIVE (set FROGNET_LIVE_PY)"
else
    if grep -q '_wan_ns_from_exit_sentinel() or wan_ns_from_default' "$LIVE"; then
        echo "  FAIL live.py still uses 'or': the default gateway is dropped"
        echo "       whenever exit_host.tsv names any nameserver"
        FAILS=$((FAILS+1))
    else
        echo "  ok: live.py appends the default gateway rather than 'or'-ing it"
    fi
    if grep -A2 'wan_ns = ' "$LIVE" | grep -q '_tunnel_peer_resolvers(local_ips))$'; then
        echo "  FAIL live.py puts the default gateway BEFORE the tunnel peers"
        FAILS=$((FAILS+1))
    else
        echo "  ok: live.py orders the default gateway last"
    fi
    if grep -q '_tunnel_peer_resolvers' "$LIVE"; then
        echo "  ok: live.py adds tunnel-peer resolvers"
    else
        echo "  FAIL live.py does not add tunnel-peer resolvers"
        FAILS=$((FAILS+1))
    fi
    if grep -q '_write_dnsmasq_upstream' "$LIVE"; then
        echo "  ok: live.py writes the dnsmasq upstream file"
    else
        echo "  FAIL live.py never writes dnsmasq_upstream.conf - dnsmasq has"
        echo "       no upstream on the python path, so nothing resolves"
        FAILS=$((FAILS+1))
    fi
fi

echo
[ "$FAILS" -eq 0 ] && { echo "RESULT: PASS"; exit 0; }
echo "RESULT: FAIL ($FAILS)"; exit 1
