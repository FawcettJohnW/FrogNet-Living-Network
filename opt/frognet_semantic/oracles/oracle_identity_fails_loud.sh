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
# oracle_identity_fails_loud.sh  --  [IDENTITY_FAILS_LOUD_V1]
#
# Every FrogNetHost answers to the same hostname. A name built from it is not an
# identity, and a line carrying one is indistinguishable from a good line to
# every reader in the pond.
#
# getFrogNet.bash used to substitute `hostname -s` for the domain when
# getOurDomain timed out, print it, and refuse to cache it -- guarding the cache
# on read AND on write against a value its own compute path produced. Peers read
# that name, treated it as no identity, and the merge livelocked; the cure was
# deleting the cache by hand. metric_upsert.sh then rebuilt the same fallback one
# layer up behind `|| true`, so every node would publish sensors under the SAME
# names and overwrite each other in the shared memory.
#
# The contract now: four fields and exit 0, or nothing on stdout and non-zero.
#
# Runs the REAL scripts against stub helpers on PATH. No mocks of the code under
# test -- only of the four helpers it shells out to.
#
# Run: bash oracle_identity_fails_loud.sh [TREE_ROOT]
# =============================================================================
set -u
TREE="${1:-/}"
GF="$TREE/usr/local/bin/getFrogNet.bash"
MU="$TREE/usr/local/bin/metric_upsert.sh"
FAIL=0
ok()  { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL+1)); }

for f in "$GF" "$MU"; do
  [ -r "$f" ] || { echo "FATAL: missing $f"; exit 1; }
done

RIG="$(mktemp -d)"; chmod 755 "$RIG"
trap 'rm -rf "$RIG"' EXIT
mkdir -p "$RIG/bin" "$RIG/sentinels"

# --- stub helpers -----------------------------------------------------------
# getOurDomain is the switch: DOMAIN_OK=1 answers, 0 times out (empty).
cat > "$RIG/bin/getOurDomain" <<'EOF'
#!/bin/bash
[ "${DOMAIN_OK:-1}" = "1" ] && echo "Seattle2.testpond"
exit 0
EOF
# Seattle2 genuinely has no carrier on eth0: an empty interface field is DATA.
cat > "$RIG/bin/getEth0Address" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$RIG/bin/getWlan0IP" <<'EOF'
#!/bin/bash
echo "10.120.120.1"
EOF
cat > "$RIG/bin/getWlan1IP" <<'EOF'
#!/bin/bash
echo "10.160.160.47"
EOF
cat > "$RIG/bin/mapInterfaces" <<'EOF'
# sourced by getFrogNet.bash; nothing needed for this oracle
EOF
# `hostname` must look like a real FrogNet box: the generic every node carries.
cat > "$RIG/bin/hostname" <<'EOF'
#!/bin/bash
echo "FrogNetHost"
EOF
chmod +x "$RIG"/bin/getOurDomain "$RIG"/bin/getEth0Address \
         "$RIG"/bin/getWlan0IP "$RIG"/bin/getWlan1IP "$RIG"/bin/hostname

# Point the scripts' absolute helper paths at the stubs.
sed -e "s#/usr/local/bin/getOurDomain#$RIG/bin/getOurDomain#g" \
    -e "s#/usr/local/bin/getEth0Address#$RIG/bin/getEth0Address#g" \
    -e "s#/usr/local/bin/getWlan0IP#$RIG/bin/getWlan0IP#g" \
    -e "s#/usr/local/bin/getWlan1IP#$RIG/bin/getWlan1IP#g" \
    -e "s#/usr/local/bin/mapInterfaces#$RIG/bin/mapInterfaces#g" \
    -e "s#/etc/sentinels#$RIG/sentinels#g" \
    "$GF" > "$RIG/bin/getFrogNet.bash"
chmod +x "$RIG/bin/getFrogNet.bash"

sed -e "s#/usr/local/bin/getFrogNet.bash#$RIG/bin/getFrogNet.bash#g" \
    -e "s#/usr/local/bin/getOurDomain#$RIG/bin/getOurDomain#g" \
    -e "s#/usr/local/bin/getEth0Address#$RIG/bin/getEth0Address#g" \
    "$MU" > "$RIG/bin/metric_upsert.sh"
chmod +x "$RIG/bin/metric_upsert.sh"

fresh(){ rm -f "$RIG/sentinels/getFrogNet.cache" "$RIG/sentinels/getFrogNet.lock"; }

echo "=== identity available: the good path still works ==="
fresh
OUT="$(DOMAIN_OK=1 PATH="$RIG/bin:$PATH" bash "$RIG/bin/getFrogNet.bash" 2>/dev/null)"; RC=$?
[ "$RC" -eq 0 ] && ok "exit 0" || bad "exit $RC, expected 0"
[ "$(echo "$OUT" | awk -F',' '{print NF}')" = "4" ] \
  && ok "four fields: $OUT" || bad "expected 4 fields, got: $OUT"
[ "$(echo "$OUT" | cut -d, -f1)" = "Seattle2.testpond" ] \
  && ok "field 1 is the real domain" || bad "field 1 wrong: $OUT"
[ -z "$(echo "$OUT" | cut -d, -f2)" ] \
  && ok "empty eth0 field preserved (no carrier is DATA, not failure)" \
  || bad "empty interface field was not preserved"

echo
echo "=== identity unavailable: fails, and says nothing ==="
fresh
OUT="$(DOMAIN_OK=0 PATH="$RIG/bin:$PATH" bash "$RIG/bin/getFrogNet.bash" 2>/dev/null)"; RC=$?
[ "$RC" -ne 0 ] && ok "non-zero exit ($RC)" || bad "exit 0 on failed identity"
[ -z "$OUT" ] && ok "stdout empty" || bad "printed a line anyway: '$OUT'"
case "$OUT" in
  *FrogNetHost*) bad "emitted the generic hostname as a name" ;;
  *)             ok "no generic hostname anywhere in output" ;;
esac
[ ! -s "$RIG/sentinels/getFrogNet.cache" ] \
  && ok "nothing cached" || bad "cached: $(cat "$RIG/sentinels/getFrogNet.cache")"

echo
echo "=== the failure is not cured by asking twice ==="
OUT2="$(DOMAIN_OK=0 PATH="$RIG/bin:$PATH" bash "$RIG/bin/getFrogNet.bash" 2>/dev/null)"; RC2=$?
[ "$RC2" -ne 0 ] && [ -z "$OUT2" ] \
  && ok "second call also fails silently" || bad "second call rc=$RC2 out='$OUT2'"

echo
echo "=== metric_upsert refuses to publish without an identity ==="
fresh
RESP="$(DOMAIN_OK=0 PATH="$RIG/bin:$PATH" \
        bash "$RIG/bin/metric_upsert.sh" System Perf '{"cpu":1}' 2>"$RIG/err.txt")"; RC=$?
[ "$RC" -ne 0 ] && ok "non-zero exit ($RC)" || bad "published anyway (exit 0)"
grep -qi "identity\|refusing" "$RIG/err.txt" \
  && ok "names the reason on stderr" || bad "no explanation: $(cat "$RIG/err.txt")"
case "$RESP$(cat "$RIG/err.txt")" in
  *"FrogNetHost.System.Perf"*) bad "built a sensor name from the generic hostname" ;;
  *)                           ok "no generic-hostname sensor name constructed" ;;
esac

echo
echo "=== the deleted machinery stays deleted ==="
grep -q "SELF_GENERIC" "$GF" && bad "SELF_GENERIC is back in getFrogNet.bash" \
                             || ok "no SELF_GENERIC"
grep -q "domain_ok" "$GF"    && bad "domain_ok flag is back" || ok "no domain_ok flag"
grep -qE 'getFrogNet\.bash[[:space:]]*\|\|[[:space:]]*true' "$MU" \
  && bad "metric_upsert swallows the exit status again" \
  || ok "metric_upsert does not swallow the exit status"
grep -vE '^\s*#' "$MU" | grep -qE 'hostname( -s)?' \
  && bad "metric_upsert rebuilds a name from hostname (in code, not a comment)" \
  || ok "metric_upsert has no hostname fallback in code"

echo
if [ "$FAIL" -eq 0 ]; then echo "PASS - [IDENTITY_FAILS_LOUD_V1]"; exit 0; fi
echo "FAILED $FAIL check(s)"; exit 1
