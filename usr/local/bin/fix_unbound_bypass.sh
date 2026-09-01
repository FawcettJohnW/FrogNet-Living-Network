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

cat > "$CONF" << 'DNSEOF'
# Direct upstream DNS - unbound disabled
no-resolv
server=8.8.8.8
server=8.8.4.4
# Silence Belkin heartbeat noise
address=/heartbeat.belkin.com/0.0.0.0
DNSEOF

systemctl restart dnsmasq
echo "Done on $(hostname)"
