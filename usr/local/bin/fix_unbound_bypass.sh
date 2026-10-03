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
set -e

CONF=/etc/dnsmasq.d/upstream_fallback.conf

echo "=== $(hostname) ==="

# Unbound is not needed by FrogNet - disable it permanently
systemctl stop unbound
systemctl disable unbound

# [NO_DNS_FALLBACK_V1] This wrote `no-resolv` plus Google's resolvers, which is
# two bugs in four lines.
#
#   1. It is a fallback. FrogNet forwards to the next hop on the default route
#      and to nobody else ([DNS_NEXTHOP_ONLY_V1]). A node with no upstream has
#      no external DNS -- FrogNet names still resolve locally -- and that is the
#      honest state, not a reason to silently ship every query to 8.8.8.8.
#   2. `no-resolv` disables resolv-file reading entirely. With it set, the
#      resolv-file below was ignored, so the merge maintained
#      /etc/sentinels/dnsmasq_upstream.conf for a dnsmasq that never read it.
#      frognet_install.sh:1119 says so explicitly; this script overwrote it.
#
# Now identical to what the installer writes. The filename is historical.
cat > "$CONF" << 'DNSEOF'
resolv-file=/etc/sentinels/dnsmasq_upstream.conf
strict-order
# Silence Belkin heartbeat noise
address=/heartbeat.belkin.com/0.0.0.0
DNSEOF

# A conf.d change is the one case that genuinely needs a restart: SIGHUP does
# not re-read /etc/dnsmasq.d. This is a one-shot repair script, not the merge.
systemctl restart dnsmasq
echo "Done on $(hostname)"
