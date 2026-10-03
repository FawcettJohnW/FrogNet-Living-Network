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
# oracle_opts_only_interface_lines.sh  --  [DNSMASQ_TEMPLATE_IS_THE_SPEC_V1]
#
# Seattle2, 2026-08-13 11:02:54. setup_lillypad.bash (v3) wrote
# /etc/dnsmasq.d/opts_only.conf from an 8-line heredoc with NO `interface=`
# lines. dnsmasq restarted 8 seconds later and from then on refused DNS from
# every node not on a directly-connected subnet, silently -- query in, no reply,
# no ICMP. `brokerhost.seattle2` resolved from Seattle6 (10.160.160.1, on its
# wlan1 subnet) and from nowhere else.
#
# Two parts, both must pass:
#   BEHAVIOUR  a real dnsmasq 2.90, real netns, real queries: prove that an
#              opts_only.conf without interface= lines leaves --local-service in
#              force and that adding them lifts it. Not a claim about the man
#              page -- an observation of the binary.
#   STRUCTURE  no provisioner leaves an opts_only.conf that lacks interface=
#              lines, and the setup helper does not invoke a retired one.
#
# Run:  bash oracle_opts_only_interface_lines.sh [TREE_ROOT]
# =============================================================================
set -u
TREE="${1:-/}"
FAIL=0
note() { printf '  %-6s %s\n' "$1" "$2"; }
ok()   { note "ok" "$1"; }
bad()  { note "FAIL" "$1"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------- BEHAVIOUR
behaviour() {
  command -v dnsmasq >/dev/null 2>&1 || { note "skip" "dnsmasq not installed"; return; }
  command -v dig     >/dev/null 2>&1 || { note "skip" "dig not installed"; return; }
  ip netns list >/dev/null 2>&1     || { note "skip" "netns unavailable"; return; }

  # 0755, not mktemp's 0700: dnsmasq drops privileges to the `dnsmasq` user
  # before reading --addn-hosts, so a private temp dir yields
  # "failed to load names from ...: Permission denied" and the rig silently
  # answers nothing.
  local D; D="$(mktemp -d)"; chmod 755 "$D"
  cleanup_b() {
    [ -s "$D/pid" ] && kill "$(cat "$D/pid")" 2>/dev/null
    ip netns del o_srv 2>/dev/null; ip netns del o_cli 2>/dev/null
    rm -rf "$D"
  }
  # NOTE: no `trap cleanup_b RETURN` here. A RETURN trap fires when ANY nested
  # function returns, so start()/probe() would tear the namespaces down in the
  # middle of the test. Cleanup is explicit at every exit path below.
  # stale namespaces from an aborted run, WITHOUT touching $D
  ip netns del o_srv 2>/dev/null; ip netns del o_cli 2>/dev/null

  ip netns add o_srv; ip netns add o_cli
  ip netns exec o_srv ip link set lo up; ip netns exec o_cli ip link set lo up
  ip link add o_lan type veth peer name o_lan_p
  ip link set o_lan netns o_srv; ip link set o_lan_p netns o_cli
  # server: its pond .1 ; client: on the pond AND carrying a foreign source
  ip netns exec o_srv ip addr add 10.120.120.1/24 dev o_lan
  ip netns exec o_srv ip link set o_lan up
  ip netns exec o_cli ip addr add 10.120.120.50/24 dev o_lan_p
  ip netns exec o_cli ip link set o_lan_p up
  ip netns exec o_cli ip addr add 10.250.250.1/32 dev lo
  ip netns exec o_srv ip route add 10.250.250.1/32 via 10.120.120.50 dev o_lan

  echo "10.120.120.63 brokerhost.seattle2" > "$D/hosts"; chmod 644 "$D/hosts"

  probe() {   # $1 = source address to query from
    ip netns exec o_cli dig +time=2 +tries=1 +short -b "$1" \
        @10.120.120.1 brokerhost.seattle2 2>/dev/null | tail -1
  }
  stop() { [ -s "$D/pid" ] && kill "$(cat "$D/pid")" 2>/dev/null; sleep 0.4; }
  start() {
    stop
    : > "$D/log"
    ip netns exec o_srv dnsmasq --conf-file="$1" \
        --log-facility="$D/log" --pid-file="$D/pid" --no-resolv --no-hosts \
        --addn-hosts="$D/hosts" --local-service >/dev/null 2>&1
    sleep 1.0
  }

  # (a) Seattle2's shape: no interface= lines
  cat > "$D/without.conf" <<'EOF'
domain=Seattle2
local=/Seattle2/
expand-hosts
dhcp-authoritative
no-dhcp-interface=o_none
EOF
  start "$D/without.conf"
  local loc_a for_a; loc_a="$(probe 10.120.120.50)"; for_a="$(probe 10.250.250.1)"

  # (b) the template's shape: interface= present, nothing else changed
  cat > "$D/with.conf" <<'EOF'
domain=Seattle2
local=/Seattle2/
expand-hosts
dhcp-authoritative
interface=o_lan
EOF
  start "$D/with.conf"
  local loc_b for_b; loc_b="$(probe 10.120.120.50)"; for_b="$(probe 10.250.250.1)"
  stop

  # The rig is only meaningful if the on-subnet query works in both.
  if [ "$loc_a" != "10.120.120.63" ] || [ "$loc_b" != "10.120.120.63" ]; then
    bad "rig invalid: on-subnet query did not answer (a='$loc_a' b='$loc_b')"
    cleanup_b; return
  fi
  ok "control: on-subnet query answers in both configs"

  # dig prints "no servers could be reached" on STDOUT, so the variable is not
  # empty on refusal -- test against the expected answer, not against emptiness.
  [ "$for_a" != "10.120.120.63" ] \
    && ok  "no interface= lines -> foreign source REFUSED (the Seattle2 bug)" \
    || bad "no interface= lines -> foreign source answered ('$for_a'); bug not reproduced"

  [ "$for_b" = "10.120.120.63" ] \
    && ok  "interface= present  -> foreign source ANSWERED (the fix)" \
    || bad "interface= present  -> foreign source still refused ('$for_b')"

  grep -q "non-local network" "$D/log" 2>/dev/null \
    && ok "dnsmasq logged 'ignoring query from non-local network'" \
    || note "note" "log line not captured (dnsmasq logs it once per process)"

  cleanup_b
}

# ---------------------------------------------------------------- STRUCTURE
structure() {
  local helper="$TREE/usr/local/bin/frognet_setup_helper.bash"
  if [ -r "$helper" ]; then
    grep -qE '^SETUP_LILLYPAD=.*setup_lillypad_v4\.bash' "$helper" \
      && ok  "setup helper defaults to setup_lillypad_v4.bash" \
      || bad "setup helper still defaults to a retired provisioner"
    grep -q 'refusing to run retired provisioner' "$helper" \
      && ok  "setup helper refuses a pinned retired provisioner" \
      || bad "setup helper will run whatever FROGNET_SETUP_LILLYPAD names"
  else
    bad "missing $helper"
  fi

  # No provisioner may leave opts_only.conf as a heredoc: the file must come
  # from the template renderer, which is the only thing that emits interface=.
  local hits=0 f
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    # a heredoc write that is NOT followed by a render call in the same file
    if grep -q 'cat > /etc/dnsmasq.d/opts_only.conf' "$f" \
       && ! grep -q 'frognet_render_dnsmasq_opts' "$f"; then
      bad "writes opts_only.conf inline with no template render: ${f#$TREE}"
      hits=$((hits+1))
    fi
  done <<EOF
$(grep -rl 'cat > /etc/dnsmasq.d/opts_only.conf' "$TREE/usr/local/bin" \
      "$TREE/opt/frognet_semantic" 2>/dev/null)
EOF
  [ "$hits" -eq 0 ] && ok "every opts_only.conf writer goes through the renderer"

  # And the renderer must actually emit a live interface= line for a served dev.
  local scope="$TREE/usr/local/bin/frognet_dhcp_scope.sh"
  if [ -r "$scope" ]; then
    grep -q "printf 'interface=%s" "$scope" \
      && ok  "renderer emits live interface= for served devices" \
      || bad "renderer no longer emits a live interface= line"
  else
    bad "missing $scope"
  fi
}

echo "=== BEHAVIOUR (real dnsmasq, real netns) ==="; behaviour
echo
echo "=== STRUCTURE (tree at $TREE) ==="; structure
echo
if [ "$FAIL" -eq 0 ]; then
  echo "PASS - [DNSMASQ_TEMPLATE_IS_THE_SPEC_V1]"; exit 0
fi
echo "FAILED $FAIL check(s)"; exit 1
