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
echo "=== Route Table ==="
ip route show
echo ""

ip route show | grep -oP '10\.\d+\.\d+\.1' | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n -u | while read IP; do
    echo "--- ${IP} ---"
    ping -c1 -W5 "$IP" 2>&1 | tail -1
    sleep 1
    echo curl -sS --connect-timeout 5 --max-time 5 "http://${IP}/frognet_echo.php" 2>&1
    curl -sS --connect-timeout 5 --max-time 5 "http://${IP}/frognet_echo.php" 2>&1
    echo ""
    sleep 1
    echo curl -sS --connect-timeout 5 --max-time 5 "http://${IP}/frognet_echo.php" 2>&1
    curl -sS --connect-timeout 5 --max-time 5 "http://${IP}/frognet_echo.php" 2>&1
    echo ""
    sleep 1
done
