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

. /usr/local/bin/mapInterfaces

HAM_NET="10.99.0.0/24"
HAM_GW="10.99.0.1"

ip link add ham0 type dummy 2>/dev/null || true
ip addr flush dev ham0
ip addr add ${HAM_GW}/24 dev ham0
ip link set ham0 up

# dnsmasq DHCP on ham0 only
cat >/etc/dnsmasq.d/ham0.conf <<EOF
interface=ham0
bind-interfaces
dhcp-range=10.99.0.50,10.99.0.100,12h
EOF

systemctl restart dnsmasq

echo "[HAM] Concentrator up on ${HAM_GW}"
