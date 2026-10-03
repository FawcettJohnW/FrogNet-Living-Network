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
tar  --exclude='venv' --exclude='__pycache__' -cvf - /usr/local/bin /opt/frognet_semantic/proxy /opt/frognet_semantic/daemon \
    /opt/frognet_semantic/core /opt/frognet_semantic/internet_tunnels_v3 /opt/frognet_semantic/frognet_route \
    /opt/frognet_semantic/simulation /etc/systemd/system/frognet-*.service /etc/sentinels/ /etc/cron.d/ /etc/cron.daily/ \
    /etc/cron.hourly/ /var/spool/cron/crontabs/ /etc/frognet/ /var/lib/frognet-tunnel/ /etc/wireguard/  /etc/logrotate.d/ \
    > /tmp/all_frognet.tar

