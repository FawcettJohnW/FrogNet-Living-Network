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

tar -cvzf "/tmp/all_tar.tgz" \
    --exclude='*frognet_world*.tgz' \
    --exclude='*frognet_release_*.tgz' \
    --exclude='usr/local/bin/installer' \
    --exclude='usr/local/bin/frognet_install.sh' \
    --exclude='usr/local/bin/frognet_build_release.sh' \
    --exclude='usr/local/bin/ham_*.sh' \
    --exclude='usr/local/bin/frognet_radio_init.sh' \
    --exclude='usr/local/bin/setup_lillypad.bash' \
    --exclude='usr/local/bin/setup_lillypad_v3.bash' \
    --exclude='usr/local/bin/frognet-tunnel-setup.sh' \
    --exclude='opt/frognet_semantic/etc' \
    --exclude='opt/frognet_semantic/bin' \
    --exclude='opt/frognet_semantic/usr' \
    --exclude='opt/frognet_semantic/var' \
    --exclude='opt/frognet_semantic/blob_cache/*' \
    --exclude='opt/frognet_semantic/logs/*' \
    --exclude='opt/frognet_semantic/**/__pycache__' \
    --exclude='opt/frognet_semantic/**/*.pyc' \
    --exclude='opt/frognet_semantic/**.old' \
    --exclude='opt/frognet_semantic/daemon.old' \
    --exclude='opt/frognet_semantic/proxy.old' \
    --exclude='opt/frognet_semantic/core.old' \
    --exclude='opt/frognet_semantic/_v1' \
    --exclude='usr/local/bin/__pycache__' \
    --exclude='usr/local/bin/*.pyc' \
    --exclude='usr/local/sbin/__pycache__' \
    --exclude='usr/local/sbin/*.pyc' \
    --exclude='usr/local/lib/python3.11/**/__pycache__' \
    --exclude='usr/local/lib/python3.11/**/*.pyc' \
    --exclude='etc/frognet_bundles/**/__pycache__' \
    --exclude='etc/frognet_bundles/**/*.pyc' \
    --exclude='etc/wireguard/*.conf' \
    --exclude='etc/NetworkManager/system-connections' \
    --exclude='etc/frognet/tunnel.conf' \
    --exclude='etc/frognet/tunnel.conf.bak.*' \
    --exclude='etc/frognet/wifi.env' \
    --exclude='etc/frognet/sem_cache_secret' \
    --exclude='etc/frognet/broker.conf' \
    --exclude='etc/dnsmasq.d/opts_only.conf' \
    --exclude='etc/dnsmasq.d/forward_to_unbound.conf' \
    --exclude='etc/frognet/gateways.conf' \
    --exclude='etc/frognet/pond.conf' \
    --exclude='etc/frognet/active_interface' \
    --exclude='etc/frognet/interfaces_override.conf' \
    --exclude='etc/frognet/transit_exclude_interfaces' \
    --exclude='etc/frognet/ssid_projection.conf' \
    --exclude='var/www/html/config.php' \
    --exclude='opt/frognet_semantic/DB_CONFIG.json' \
    --exclude='opt/frognet_semantic/daemon/cache/semcache_db.py' \
    --exclude='opt/frognet_semantic/proxy/cache/semcache_db.py' \
    --exclude='opt/frognet_semantic/daemon/engine/data_cache.py' \
    --exclude='usr/local/bin/install_databasehost.sh' \
    --exclude='usr/local/bin/semantic_cache_schema.sql' \
    --exclude='*addExternalNameserver.php' \
    --exclude='etc/frognet/db.env' \
    --exclude='etc/frognet_bundles/games' \
    -C / \
    /usr/local/bin /opt/frognet_semantic/proxy /opt/frognet_semantic/daemon \
    /opt/frognet_semantic/discovery \
    /opt/frognet_semantic/core /opt/frognet_semantic/internet_tunnels_v3 /opt/frognet_semantic/frognet_route \
    /opt/frognet_semantic/simulation /etc/systemd/system/frognet-*.service /etc/sentinels/ /etc/cron.d/ /etc/cron.daily/ \
    /etc/cron.hourly/ /var/spool/cron/crontabs/ /etc/frognet/ /var/lib/frognet-tunnel/ /etc/wireguard/  /etc/logrotate.d/ \
