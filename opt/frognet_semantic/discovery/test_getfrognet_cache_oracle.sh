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
# [NO_CACHE_GARBAGE_V1] Oracle for the getFrogNet.cache poison.
# Garbage = name field is the generic `hostname -s` (FrogNetHost) instead of the
# real getOurDomain name (BABox). It happens when getOurDomain times out, and it
# gets CACHED, so every echo serves FrogNetHost until the cache is rm'd by hand.
# Asserts: OLD caches the garbage (the bug); NEW never caches it, returns it for
# the one call, and SELF-HEALS a cache that already holds it. Warm-good unaffected.
set -u
OLD="${1:?old script}"; NEW="${2:?new script}"
ok=1; chk(){ if [[ "$1" == "$2" ]]; then echo "  [PASS] $3"; else echo "  [FAIL] $3 (got '$1' want '$2')"; ok=0; fi; }
exists(){ [[ -e "$1" ]] && echo yes || echo no; }

render(){ # $1 src $2 dst : repoint absolute paths at the sandbox
  sed -e "s#/etc/sentinels#$T/sentinels#g" -e "s#/usr/local/bin#$T/bin#g" "$1" > "$2"; chmod +x "$2"; }

setup(){
  T="$(mktemp -d)"; mkdir -p "$T/bin" "$T/sentinels"
  # stubs
  cat > "$T/bin/mapInterfaces" << 'X'
#!/bin/bash
:
X
  cat > "$T/bin/getOurDomain" << 'X'
#!/bin/bash
printf '%s' "${FAKE_DOMAIN-}"   # empty => simulate timeout/failure
X
  printf '#!/bin/bash\nprintf 10.111.11.1\n'   > "$T/bin/getEth0Address"
  printf '#!/bin/bash\nprintf 172.16.26.199\n' > "$T/bin/getWlan0IP"
  printf '#!/bin/bash\nprintf 0.0.0.0\n'       > "$T/bin/getWlan1IP"
  cat > "$T/bin/hostname" << 'X'
#!/bin/bash
printf 'FrogNetHost'            # generic hostname every node carries
X
  chmod +x "$T"/bin/*
  render "$OLD" "$T/old.sh"; render "$NEW" "$T/new.sh"
  export PATH="$T/bin:$PATH"
}
teardown(){ rm -rf "$T"; }

run(){ FAKE_DOMAIN="$1" bash "$2"; }   # echoes the CSV; cache side-effect in $T/sentinels

# A) OLD, getOurDomain FAILS, cold cache -> caches the garbage (the bug)
setup; out=$(run "" "$T/old.sh"); cachce=$(cat "$T/sentinels/getFrogNet.cache" 2>/dev/null)
chk "$out" "FrogNetHost,10.111.11.1,172.16.26.199,0.0.0.0" "OLD returns generic-name line on getOurDomain fail"
chk "$cachce" "FrogNetHost,10.111.11.1,172.16.26.199,0.0.0.0" "OLD CACHES the garbage (reproduces the bug)"; teardown

# B) NEW, getOurDomain FAILS, cold cache -> returns it for this call but does NOT cache it
setup; out=$(run "" "$T/new.sh"); had=$(exists "$T/sentinels/getFrogNet.cache")
chk "$out" "FrogNetHost,10.111.11.1,172.16.26.199,0.0.0.0" "NEW still returns a line for this call (no breakage)"
chk "$had" "no" "NEW does NOT persist the garbage to cache"; teardown

# C) NEW, getOurDomain OK, cold cache -> caches the real name
setup; out=$(run "BABox" "$T/new.sh"); cachce=$(cat "$T/sentinels/getFrogNet.cache" 2>/dev/null)
chk "$out" "BABox,10.111.11.1,172.16.26.199,0.0.0.0" "NEW returns real name when getOurDomain works"
chk "$cachce" "BABox,10.111.11.1,172.16.26.199,0.0.0.0" "NEW caches the GOOD line"; teardown

# D) NEW, pre-existing GARBAGE cache + getOurDomain OK -> self-heals to real name
setup; printf 'FrogNetHost,10.111.11.1,172.16.26.199,0.0.0.0\n' > "$T/sentinels/getFrogNet.cache"
out=$(run "BABox" "$T/new.sh"); cachce=$(cat "$T/sentinels/getFrogNet.cache" 2>/dev/null)
chk "$out" "BABox,10.111.11.1,172.16.26.199,0.0.0.0" "NEW self-heals: rejects cached garbage, recomputes real name"
chk "$cachce" "BABox,10.111.11.1,172.16.26.199,0.0.0.0" "NEW rewrites cache with the good line"; teardown

# E) NEW, warm GOOD cache -> served from cache (no recompute, no regression)
setup; printf 'BABox,10.111.11.1,172.16.26.199,0.0.0.0\n' > "$T/sentinels/getFrogNet.cache"
out=$(run "SOMETHING_ELSE" "$T/new.sh")   # if it recomputed it'd say SOMETHING_ELSE
chk "$out" "BABox,10.111.11.1,172.16.26.199,0.0.0.0" "NEW serves a warm GOOD cache unchanged (fast path intact)"; teardown

echo "RESULT: $([[ $ok -eq 1 ]] && echo PASS || echo FAIL)"; exit $((1-ok))
