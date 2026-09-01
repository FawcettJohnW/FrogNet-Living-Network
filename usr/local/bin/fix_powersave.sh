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
# fix_powersave.sh — Disable WiFi power save on this node

log() { echo "[$(hostname -s)] $*"; }

# Disable immediately on every wireless interface
for dev in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do
    iw dev "$dev" set power_save off 2>/dev/null && log "power_save off: $dev" || log "WARN: could not set $dev"
done

# Persist via NetworkManager
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-wifi-powersave.conf << 'NMEOF'
[connection]
wifi.powersave = 2
NMEOF
log "wrote 99-wifi-powersave.conf"

# Confirm
for dev in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do
    log "$dev: $(iw dev "$dev" get power_save 2>/dev/null || echo N/A)"
done
