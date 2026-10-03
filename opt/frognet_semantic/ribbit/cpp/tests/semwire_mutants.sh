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
# Oracle sensitivity: each mutant is one deliberate bug in a copy of include/semwire.hpp; the S1 oracle must FAIL on
# every one. The real header is never modified. usage: tests/semwire_mutants.sh FROGNET_SEMANTIC_ROOT
set -u
cd "$(dirname "$0")/.."
ROOT=$1; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PY=$(command -v python3 || command -v python)
caught=0; total=0
mutant(){
  total=$((total+1)); mkdir -p "$T/m"; cp include/semwire.hpp "$T/m/semwire.hpp"
  $PY - "$T/m/semwire.hpp" "$2" "$3" <<'PYEOF' || { echo "MUTANT-SETUP-FAIL $1"; return; }
import sys; p, a, b = sys.argv[1:]; s = open(p).read()
if s.count(a) != 1: sys.exit('pattern not unique: ' + a)
open(p, 'w').write(s.replace(a, b))
PYEOF
  g++ -std=c++17 -O2 -I"$T/m" tests/semwire_driver.cpp -o "$T/drv" 2> "$T/cc.txt" || { echo "MUTANT-BUILD-FAIL $1"; return; }
  r=$($PY tests/test_semwire_oracle.py "$ROOT" --driver "$T/drv" --fuzz 20000 2>&1 | grep '^FAIL' | tr '\n' ';')
  if [ -n "$r" ]; then caught=$((caught+1)); echo "CAUGHT $1: $r"; else echo "MISSED $1"; fi
}
mutant "little-endian read" 'v = v << 8 | uint8_t(f[off + i]);' 'v = v | uint64_t(uint8_t(f[off + i])) << (8 * i);'
mutant "HELLO length limit 256" 'if (return_ip.size() > 255) throw' 'if (return_ip.size() > 256) throw'
mutant "RTT_PING payload None, not empty" 'm.payload = std::string_view();  // b"": the pad is not kept' 'm.payload.reset();'
mutant "RESP_RAW also sets payload" 'm.body = p.substr(4 + hl);' 'm.body = p.substr(4 + hl); m.payload = p;'
mutant "op_name zero-pads" 'if (op >= 16) s.push_back' 's.push_back(d[op >> 4]); if (0) s.push_back'
mutant "RTT_PONG ping_id unchecked" 'detail::fits(ping_id, 0xFFFFFFFFu, "ping_id");' ''
mutant "HELLO length before ASCII" 'if (uint8_t(return_ip[i]) >= 0x80)' 'if (return_ip.size() <= 255 && uint8_t(return_ip[i]) >= 0x80)'
mutant "ERROR short-message count off" 'std::to_string(f.size() - off - 4));' 'std::to_string(f.size() - off));'
echo "mutants caught: $caught of $total"
[ "$caught" = "$total" ]
