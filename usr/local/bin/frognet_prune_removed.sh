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
# frognet_prune_removed.sh - delete what the cleanup removed.
#
# Untarring the node tree ADDS files; it cannot remove them. So a node that has
# been brought up to date still carries everything the cleanup deleted, and the
# oracles see it: on FrogNetHost 2026-09-01, test_license_oracle reported 202
# files with no GPL notice - files that are not in the tree and therefore never
# got one - and test_clean_install_oracle failed alongside it.
#
# Every path below was removed deliberately, with a reason. Nothing here is a
# guess: the list is the set difference between a node snapshot and the current
# tree, and every one of the 447 files matched a rule.
#
#   ./frognet_prune_removed.sh            # DRY RUN - prints, changes nothing
#   ./frognet_prune_removed.sh --apply    # actually delete
#
# Run it on the node AFTER untarring the current tree, then re-run the simulator.

set -u
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
[ "$APPLY" -eq 0 ] && echo "DRY RUN - nothing will be deleted. Pass --apply to do it." && echo

n=0; miss=0
gone() {   # gone <path> <why>
    local p="/$1"
    if [ -e "$p" ] || [ -L "$p" ]; then
        echo "  rm   $p"
        [ "$APPLY" -eq 1 ] && rm -rf -- "$p"
        n=$((n+1))
    else
        miss=$((miss+1))
    fi
}


echo "-- timestamped runtime backups carrying GROUP_TOKEN"
gone "etc/frognet/broker.conf.lanonly-removed.20260619T090222Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260619T090222Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260724T165319Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260725T154758Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260725T162342Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260725T165003Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260725T171739Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260726T202620Z"
gone "etc/frognet/tunnel.conf.lanonly-removed.20260726T223001Z"

echo "-- stash the build script already skips by name"
gone "etc/frognet_bundles/boardgame.out_of_way" 

echo "-- zero-byte shell accidents"
gone "etc/frognet_bundles/communicator/0"
gone "etc/frognet_bundles/communicator/0.3"
gone "etc/frognet_bundles/communicator/10.160.160.1"
gone "etc/frognet_bundles/communicator/="
gone "etc/frognet_bundles/communicator/None"
gone "etc/frognet_bundles/communicator/communicator"

echo "-- zero-byte shell accident"
gone "opt/frognet_semantic/daemon/engine/execution.pysystemctl"

echo "-- older variant of hosts.py"
gone "opt/frognet_semantic/discovery/hosts.py.2"

echo "-- empty stub"
gone "opt/frognet_semantic/discovery/live.pyi"

echo "-- older variant"
gone "opt/frognet_semantic/internet_tunnels_v3/__main__.py.v2"

echo "-- lost its .py - the STALE .py was importing; renamed in the tree"
gone "opt/frognet_semantic/proxy/proxy_metrics"

echo "-- backup copy"
gone "opt/frognet_semantic/proxy/transport_semantic.py.backup_20260417_055001"

echo "-- dead package - __INIT__.py is uppercase, it could never import"
gone "opt/frognet_semantic/proxy/util" 

echo "-- byte-identical stale mirror of the docroot + a 23 MB tar named .php"
gone "opt/frognet_semantic/web" 

echo "-- vendored ARM binaries"
gone "usr/local/bin/dhtnode"
gone "usr/local/bin/fbcp"
gone "usr/local/bin/py-spy"

echo "-- shadow copies - Apache never serves bin, and the module cannot import from there"
gone "usr/local/bin/discovery.py"
gone "usr/local/bin/getHosts.php"

echo "-- zero-byte / runtime lock"
gone "usr/local/bin/foo"
gone "usr/local/bin/frognet-roam.lock"
gone "usr/local/bin/gather_repo.sh"
gone "usr/local/bin/setRouteRoute"

echo "-- unreferenced; frognet-join held a hardcoded token, vba_extract is XlsxWriter's"
gone "usr/local/bin/frognet-join"
gone "usr/local/bin/frognet_simulator.bash"
gone "usr/local/bin/vba_extract.py"

echo "-- build output - the C++ source stays"
gone "usr/local/bin/frognet_monitor_cpp/frognet_dashboard_oracle"
gone "usr/local/bin/frognet_monitor_cpp/frognet_monitor"
gone "usr/local/bin/frognet_monitor_cpp/frognet_monitor_tui"
gone "usr/local/bin/frognet_monitor_cpp/frognet_parser_oracle"
gone "usr/local/bin/frognet_monitor_cpp/libfrognet_core.a"

echo "-- consolidated into opt/frognet_semantic/internet_tunnels_v3/"
gone "usr/local/bin/hostapd.conf.template"

echo "-- units for things that were removed, and stray unit-shaped files"
# dhtnode.service is the unit for /usr/local/bin/dhtnode, a vendored ARM binary
# deleted earlier; the unit outlived it. frognet_monitor in /etc/systemd/system is
# not a unit at all (no .service suffix) - systemd ignores it and it is invisible.
# Both surfaced on FrogNetHost 2026-09-01 as files with no copyright notice.
gone "etc/systemd/system/dhtnode.service"
gone "etc/systemd/system/frognet_monitor"
# Debian's console autologin override, swept into an earlier collection by a
# blanket *.d glob. Not FrogNet's, and it autologins the console as froguser.
gone "etc/systemd/system/getty@tty1.service.d"
# Older copies of the trace library. usr/local/lib/frognet_trace.sh is the one the
# shell tools source; these two python namesakes are unreferenced.
gone "opt/frognet_semantic/core/frognet_trace.py"
gone "opt/frognet_semantic/proxy/frognet_trace.py"
# Never deployed - the handoff records it as staged and never installed.
gone "usr/local/bin/frognet_clean.sh"
# Apache's own backup of its vhost dir, from a 2025 upgrade.
gone "etc/apache2/sites-available.backup-2025-11-24"

echo "-- units never existed anywhere"
gone "usr/local/sbin/frognet-connectivity-ui.py"
gone "usr/local/sbin/frognet_connectivity_watchdog.py"

echo "-- superseded dashboard lineage"
gone "var/www/html/LFN_Dashboard.html"
gone "var/www/html/LFN_Dashboard2.html"
gone "var/www/html/LFN_Dashboard3.html"

echo "-- awrtc webpack output - reached only from orphan pages"
gone "var/www/html/bundle" 

echo "-- orphan pages, nothing links them"
gone "var/www/html/examples.html"
gone "var/www/html/start_webrtc.html"
gone "var/www/html/testapp.html"

echo "-- superseded dashboard; 32 is live (oracle_dashboard32_views.js pins it)"
gone "var/www/html/fln_dashboard10.html"
gone "var/www/html/fln_dashboard11.html"
gone "var/www/html/fln_dashboard12.html"
gone "var/www/html/fln_dashboard13.html"
gone "var/www/html/fln_dashboard14.html"
gone "var/www/html/fln_dashboard15.html"
gone "var/www/html/fln_dashboard16.html"
gone "var/www/html/fln_dashboard17.html"
gone "var/www/html/fln_dashboard18.html"
gone "var/www/html/fln_dashboard19.html"
gone "var/www/html/fln_dashboard20.html"
gone "var/www/html/fln_dashboard21.html"
gone "var/www/html/fln_dashboard22.html"
gone "var/www/html/fln_dashboard23.html"
gone "var/www/html/fln_dashboard24.html"
gone "var/www/html/fln_dashboard25.html"
gone "var/www/html/fln_dashboard26.html"
gone "var/www/html/fln_dashboard27.html"
gone "var/www/html/fln_dashboard28.html"
gone "var/www/html/fln_dashboard29.html"
gone "var/www/html/fln_dashboard30.html"
gone "var/www/html/fln_dashboard31.html"
gone "var/www/html/fln_dashboard4.html"
gone "var/www/html/fln_dashboard5.html"
gone "var/www/html/fln_dashboard6.html"
gone "var/www/html/fln_dashboard7.html"
gone "var/www/html/fln_dashboard8.html"
gone "var/www/html/fln_dashboard9.html"

echo "-- the same undeployed API, deployed nowhere"
gone "var/www/html/frognet_api" 

echo "-- phpinfo - never on a public box"
gone "var/www/html/info.php"
gone "var/www/html/phpinfo.php"

echo "-- adapter.js (BSD) + plainwebrtc.js - orphans"
gone "var/www/html/js" 

echo "-- undeployed second PHP API (its README says "copy to your web root")"
gone "var/www/html/php" 

echo "-- superseded generation; the live wave hits propogateNotification.php"
gone "var/www/html/propagateNotification.php"
gone "var/www/html/propogatenotification.php"

echo "-- orphan - part of the browser-WebRTC branch"
gone "var/www/html/videoinput" 

echo "-- Apache-2.0 Google WebRTC samples - unreferenced"
gone "var/www/html/webrtc-web" 

echo
echo "-- python bytecode (regenerated on next import)"
for d in /opt/frognet_semantic /etc/frognet_bundles /usr/local/bin; do
    [ -d "$d" ] || continue
    if [ "$APPLY" -eq 1 ]; then
        find "$d" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
        find "$d" -name '*.pyc' -delete 2>/dev/null
    else
        c=$(find "$d" -name '__pycache__' -type d 2>/dev/null | wc -l)
        [ "$c" -gt 0 ] && echo "  rm   $d/**/__pycache__  ($c dirs)"
    fi
done

echo
echo "-- systemd: the removed units were disabled, but tell systemd anyway"
if [ "$APPLY" -eq 1 ]; then
    systemctl disable --now frognet-nat64 2>/dev/null
    systemctl daemon-reload
else
    echo "  systemctl disable --now frognet-nat64 ; systemctl daemon-reload"
fi

echo
if [ "$APPLY" -eq 1 ]; then
    echo "deleted $n path(s); $miss already absent."
else
    echo "$n path(s) would be deleted; $miss already absent."
    echo "Re-run with --apply."
fi

