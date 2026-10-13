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
#   ./frognet_prune_removed.sh                      # DRY RUN - prints, changes nothing
#   ./frognet_prune_removed.sh --apply              # actually delete
#
# Run it on the node AFTER untarring the current tree, then re-run the simulator.
# [PRUNE_ALL_ON_UPGRADE_V1 - T4c] The installer's C1c upgrade step and frognet_build_release.sh run
# `--apply`: every section, every install / build (there is no partial run). Every path below was
# checked absent from the tree it ships with (a fresh install loses nothing to it).
#
# Exit status: 0, or 1 if any removal failed (each failure is logged; the next run
# retries it).

set -u
APPLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        *) echo "usage: $0 [--apply]" >&2; exit 2 ;;
    esac
done
[ "$APPLY" -eq 0 ] && echo "DRY RUN - nothing will be deleted. Pass --apply to do it." && echo
# FROGNET_PRUNE_ROOT: run against a tree other than / (the oracle's scratch root). Empty = the node.
R="${FROGNET_PRUNE_ROOT:-}"

n=0; miss=0; fail=0
gone() {   # gone <path> <why>
    local p="$R/$1"
    if [ -e "$p" ] || [ -L "$p" ]; then
        echo "  rm   $p"
        if [ "$APPLY" -eq 1 ]; then
            rm -rf -- "$p" || { echo "  FAILED rm $p (retried on the next run)" >&2; fail=$((fail+1)); }
        fi
        n=$((n+1))
    else
        miss=$((miss+1))
    fi
}
gone_glob() {   # gone_glob <dir> <pattern>: every match of <pattern> in <dir> (one level)
    local f
    for f in "$R/$1"/$2; do
        [ -e "$f" ] || [ -L "$f" ] || continue
        gone "${f#"$R"/}"
    done
}
units_off() {   # stop + disable units while systemd still knows them by name
    if [ -n "$R" ]; then
        echo "  (root $R: systemctl not run for $*)"
    elif [ "$APPLY" -eq 1 ]; then
        echo "  systemctl disable --now $*"
        systemctl disable --now "$@" 2>/dev/null
    else
        echo "  systemctl disable --now $*"
    fi
}

t3_section() {
echo "-- [T3 2026-10-12] the broker, its enrolment and its node state, retired: two modes, LAN-only and region"
# Units first, while their files still exist.
units_off frognet-tunnel-setup.service frognet-pond-bootstrap.service frognet-pond-bootstrap.timer \
    frognet-broker-v4.service frognet-broker-v3.service frognet-metering.service \
    frognet-mediahost.service frognet-mediahost-advertise.timer frognet-mediahost-advertise.service frognet-reaper.service
# The old tunnel.conf keys. Nothing reads tunnel.conf any more: the region switch is /opt/fdf/var/join.env alone and the
# area site.env ([REGION_SWITCH_ONE_FILE_V1], T4c); the daemon names what is left here once, ignored. These are the keys
# the removed tools wrote, and the old RINA_RAM / RINA_AREA (a node whose region lived only here is LAN-only after the
# upgrade until fdf-region-join is run: gone is gone).
_tc="$R/etc/frognet/tunnel.conf"
if [ -f "$_tc" ]; then
    FROGNET_CONF="$_tc"
    . "$R/usr/local/lib/frognet/conf.sh" || { echo "  FAILED: cannot source $R/usr/local/lib/frognet/conf.sh" >&2; fail=$((fail+1)); }
    for _k in BROKER_URL GROUP_TOKEN GROUP_NAME PASSCODE POND_NAME POND_PASSWORD CHORUSES MAX_TUNNELS NODE_GUID RINA_RAM RINA_AREA; do
        grep -q "^${_k}=" "$_tc" || continue
        echo "  unset $_k in $_tc"
        n=$((n+1))
        [ "$APPLY" -eq 1 ] || continue
        fn_conf_unset "$_k" || { echo "  FAILED unset $_k in $_tc (retried on the next run)" >&2; fail=$((fail+1)); }
    done
fi
# Their files: the two old config files, and every backup of them or of tunnel.conf (they carry GROUP_TOKEN / PASSCODE).
gone "etc/frognet/broker.conf"
gone "etc/frognet/pond.conf"
gone "etc/frognet/tunnel.conf.broker"
gone_glob "etc/frognet" 'broker.conf.*'
gone_glob "etc/frognet" 'pond.conf.*'
gone_glob "etc/frognet" 'tunnel.conf.bak.*'
gone_glob "etc/frognet" 'tunnel.conf.20*'
# State nothing writes or reads any more.
gone "etc/frognet/pond_bootstrap_done"
gone "var/lib/frognet-tunnel/last_broker_state.json"
gone "var/lib/frognet-tunnel/broker_channels.json"
gone "var/lib/frognet-tunnel/channel_map.json"
gone "etc/sentinels/transit_subnets.tsv"
gone "etc/sentinels/internet_state"
gone "etc/sentinels/internet_state.lock"
# Units and hooks.
gone "etc/systemd/system/frognet-tunnel-setup.service"
gone "etc/systemd/system/frognet-pond-bootstrap.service"
gone "etc/systemd/system/frognet-pond-bootstrap.timer"
gone "etc/systemd/system/frognet-broker-v4.service"
gone "etc/systemd/system/frognet-broker-v4.service.d"
gone "etc/systemd/system/frognet-broker-v3.service"
gone "etc/systemd/system/frognet-broker-v3.service.d"
gone "etc/systemd/system/frognet-metering.service"
gone "etc/systemd/system/frognet-mediahost.service"
gone "etc/systemd/system/frognet-mediahost-advertise.service"
gone "etc/systemd/system/frognet-mediahost-advertise.timer"
gone "etc/systemd/system/frognet-reaper.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-tunnel-setup.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-broker-v4.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-mediahost.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-reaper.service"
gone "etc/systemd/system/timers.target.wants/frognet-pond-bootstrap.timer"
gone "etc/systemd/system/timers.target.wants/frognet-mediahost-advertise.timer"
gone "etc/NetworkManager/dispatcher.d/91-frognet-connectivity"
# The scripts and pages (T3b), the broker tree, and the T3a test files.
gone "usr/local/bin/change_pond.bash"
gone "usr/local/bin/frognet-broker-admin.sh"
gone "usr/local/bin/frognet-conf-consolidate.sh"
gone "usr/local/bin/frognet-config-v4"
gone "usr/local/bin/frognet-guid"
gone "usr/local/bin/frognet-make-gateway.sh"
gone "usr/local/bin/frognet-make-lanonly.sh"
gone "usr/local/bin/frognet-pond-bootstrap.sh"
gone "usr/local/bin/frognet-tunnel-setup-v3.sh"
gone "usr/local/bin/frognet-unmake-gateway.sh"
gone "usr/local/bin/frognet_internet_watch.sh"
gone "usr/local/bin/frognet_nuke_tunnels"
gone "var/www/html/frognet_chorus.php"
gone "var/www/html/pond_admin.html"
gone "opt/frognet_semantic/broker"
gone "opt/frognet_semantic/internet_tunnels_v3/frognet_chorus.php"
gone "opt/frognet_semantic/internet_tunnels_v3/test_register_on_404_oracle.py"
gone "opt/frognet_semantic/internet_tunnels_v3/test_transit_from_routes.py"
gone "opt/frognet_semantic/discovery/test_vouch_transit_gate_oracle.py"
gone "opt/frognet_semantic/discovery/sim/broker_world.py"
gone "opt/frognet_semantic/simulation/broker_topology.py"
gone "opt/frognet_semantic/simulation/test_transit_closure_repro.py"
echo "-- [T4b 2026-10-12] second mechanisms retired (one owner each)"
# frognet-eth0-fixup: a boot-time second owner of the LAN addresses (NetworkManager's profile is the one)
units_off frognet-eth0-fixup.service
gone "etc/systemd/system/frognet-eth0-fixup.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-eth0-fixup.service"
gone "usr/local/bin/frognet_eth0_fixup.sh"
# the installer's permanent "unmanaged" for the AP radio (frognet-netstart decides it per mode)
gone "etc/NetworkManager/conf.d/frognet-unmanaged-ap.conf"
# the cmp-identical second copy of the tuning script (frognet-system-tune.sh is the one)
gone "usr/local/bin/frognet_system_tune.sh"
# the merge hook's route signature moved to /run (gone at every boot: the boot merge is the hook's)
gone "etc/sentinels/nm_dispatch_sig"
if [ -z "$R" ] && [ "$APPLY" -eq 1 ]; then systemctl daemon-reload; fi
}

t1_section() {
echo "-- [T1..T4b, listed in T4c 2026-10-12] every file the cleanup deleted from usr/local/bin, usr/local/sbin,"
echo "--   etc/systemd/system, var/www/html and opt/frognet_semantic (git diff --name-status 42465dd 0db230f, D)"
echo "--   that no section above or below names already"
# Not here, on purpose: fdf-hub.service, fdf-ram-frognet.service, fdf-client-frognet.service. The tree once carried
# files by those names, but fdf-region-create makes fdf-ram-<AREA> / fdf-client-<AREA> and the droplet tarball
# installs fdf-hub.service: on a region holder (area "frognet") a file by that name is the live region, not a leftover.
units_off aiconnect.service frognet-post-merge-metrics.service
gone "etc/systemd/system/aiconnect.service"
gone "etc/systemd/system/frognet-nat64.service"
gone "etc/systemd/system/frognet-post-merge-metrics.service"
gone "opt/frognet_semantic/.netbench-backup-20260908-163735"
gone "opt/frognet_semantic/APPLY.txt"
gone "opt/frognet_semantic/_fix_meta"
gone "opt/frognet_semantic/cpre"
gone "opt/frognet_semantic/internet_tunnels_v3/broker.py"
gone "opt/frognet_semantic/internet_tunnels_v3/broker_auth.py"
gone "opt/frognet_semantic/internet_tunnels_v3/broker_pin.py"
gone "opt/frognet_semantic/internet_tunnels_v3/frognet-client-v3.tar.gz"
gone "opt/frognet_semantic/internet_tunnels_v3/gen_oracle.py"
gone "opt/frognet_semantic/internet_tunnels_v3/install"
gone "opt/frognet_semantic/internet_tunnels_v3/main.py"
gone "opt/frognet_semantic/internet_tunnels_v3/package_client.sh"
gone "opt/frognet_semantic/internet_tunnels_v3/peer.py"
gone "opt/frognet_semantic/internet_tunnels_v3/setup_lillypad.bash"
gone "opt/frognet_semantic/internet_tunnels_v3/test_transit_discover_v2.py"
gone "opt/frognet_semantic/internet_tunnels_v3/threads.py"
gone "opt/frognet_semantic/internet_tunnels_v3/websocket.py"
gone "usr/local/bin/'"
gone "usr/local/bin/ConnectFamily"
gone "usr/local/bin/addHost"
gone "usr/local/bin/addHostAndPropogate"
gone "usr/local/bin/daemon_main.py"
gone "usr/local/bin/fr"
gone "usr/local/bin/frognet-channel-advertise.py"
gone "usr/local/bin/frognet-channel-join.sh"
gone "usr/local/bin/frognet-create-passcode.sh"
gone "usr/local/bin/frognet-redeem-passcode.sh"
gone "usr/local/bin/frognet-tunnel"
gone "usr/local/bin/frognet-tunnel-daemon"
gone "usr/local/bin/frognet-tunnel-daemon.py"
gone "usr/local/bin/frognet-tunnel-host"
gone "usr/local/bin/frognet-tunnel-join"
gone "usr/local/bin/frognet-tunnel-refresh.sh"
gone "usr/local/bin/frognet-tunnel-register.sh"
gone "usr/local/bin/frognet-tunnel-setup.sh"
gone "usr/local/bin/frognet-tunnel-watcher.sh"
gone "usr/local/bin/frognet_netdrop_watch.sh"
gone "usr/local/bin/ham_concentrator_up.sh"
gone "usr/local/bin/probe_orig.sh"
gone "usr/local/bin/setup_log"
gone "usr/local/bin/x"
gone "usr/local/sbin/dnsmasq_merge_trigger.sh"
gone "var/www/html/dans_lamp.html"
gone "var/www/html/frognet_cli_bin_plus.zip"
gone "var/www/html/frognet_fuckme.php"
gone "var/www/html/frognet_test.php"
gone "var/www/html/getFrogNet2.php"
gone "var/www/html/getHosts2.php"
gone "var/www/html/mysql-apt-config_0.8.30-1_all.deb"
gone "var/www/html/setup_frognet2.html"
gone "var/www/html/sysperf.json"
gone "var/www/html/sysperf_2.html"
gone "var/www/html/sysperf_3.html"
gone "var/www/html/test_endpoint.php"
}

pip_console_section() {
echo "-- [T4c 2026-10-12] console scripts the old installer's A3 put in /usr/local/bin with the system pip"
# The services run on the venv since T4b; A3 installs nothing into the system python. These nine wrappers are what the
# old A3 left. A wrapper goes only when (1) it is pip's console-script wrapper (a python shebang and a
# `from <module>... import` line), (2) dpkg owns neither the wrapper nor its module as an OS package
# (/usr/lib/python3/dist-packages/<module>): a module the OS also packages keeps its wrapper. Nothing is pip-uninstalled.
local name f mod
for name in cbor2 dotenv gpsdclient gunicorn gunicornc httpx idna normalizer cffi-gen-src; do
    f="$R/usr/local/bin/$name"
    [ -f "$f" ] || { miss=$((miss+1)); continue; }
    if ! head -1 "$f" | grep -q '^#!.*python' ; then echo "  keep $f: not a python wrapper"; continue; fi
    mod=$(sed -n 's/^from \([A-Za-z_][A-Za-z0-9_]*\)[. ].*import .*/\1/p' "$f" | head -1)
    if [ -z "$mod" ]; then echo "  keep $f: no 'from <module> import' line (not pip's wrapper)"; continue; fi
    if dpkg -S "/usr/local/bin/$name" >/dev/null 2>&1; then echo "  keep $f: dpkg owns it"; continue; fi
    if dpkg -S "/usr/lib/python3/dist-packages/$mod" >/dev/null 2>&1; then
        echo "  keep $f: its module $mod is also an OS package ($(dpkg -S "/usr/lib/python3/dist-packages/$mod" 2>/dev/null | head -1 | cut -d: -f1))"
        continue
    fi
    gone "usr/local/bin/$name"
done
}

summary() {
    echo
    if [ "$APPLY" -eq 1 ]; then
        echo "deleted $n path(s); $miss already absent; $fail failed."
    else
        echo "$n path(s) would be deleted; $miss already absent."
        echo "Re-run with --apply."
    fi
    [ "$fail" -eq 0 ]
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

echo "-- the bundles directory, retired: the Communicator runs from /opt/frognet_semantic/ribbit/examples/communicator"
gone "etc/frognet_bundles"


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
echo "-- [PRUNE_NODE_JUNK_V1 2026-10-02] found on Seattle5; full_tar would carry each into the repository"
echo "-- copies of /usr/local/bin scripts left in the unit directory (systemd ignores them; the real ones are in /usr/local/bin)"
gone "etc/systemd/system/frognet-merge-watcher.sh"
gone "etc/systemd/system/frognet_alive.bash"
gone "etc/systemd/system/frognet_backup_world.bash"
gone "etc/systemd/system/frognet_bootstrap.sh"
gone "etc/systemd/system/frognet_cache_nuke.bash"
gone "etc/systemd/system/frognet_common.sh"
gone "etc/systemd/system/frognet_demo_traffic.py"
gone "etc/systemd/system/frognet_discovery_cache.sh"
gone "etc/systemd/system/frognet_down_clean.sh"
gone "etc/systemd/system/frognet_echo_cached.bash"
gone "etc/systemd/system/frognet_enable_on_boot.sh"
gone "etc/systemd/system/frognet_eth0_fixup.sh"
gone "etc/systemd/system/frognet_link_autoconfig.sh"
gone "etc/systemd/system/frognet_metrics_emit.sh"
gone "etc/systemd/system/frognet_nm_unmanage_if.sh"
gone "etc/systemd/system/frognet_radio_init.sh"
gone "etc/systemd/system/frognet_set_transit_baud.sh"
gone "etc/systemd/system/frognet_simulator.bash"
gone "etc/systemd/system/frognet_tc_apply.sh"
gone "etc/systemd/system/frognet_tc_clear.sh"
gone "etc/systemd/system/frognet_transit_boot.sh"
gone "etc/systemd/system/frognet_transit_profile.sh"
gone "etc/systemd/system/frognet_transit_watch.sh"
gone "etc/systemd/system/frognet_up_clean.sh"
gone "etc/systemd/system/~."
gone "etc/systemd/system/frognet-system-tune.service[Unit]"
echo "-- a stray copy of the proxy tree inside the monitor's directory (nothing imports it)"
gone "usr/local/bin/frognet_monitor_py/opt"
echo "-- a frog CLI tarball unpacked into the web root"
gone "var/www/html/etc"
gone "var/www/html/usr"
echo "-- the old monitor library: referenced by nothing; frognet_monitor runs frognet_monitor_py"
gone "usr/local/lib/frognet_monitor"
gone "usr/local/lib/frognet_trace"
echo "-- retired by af0aa9b (bundles and games) and 62b0fe7 (backups), still on nodes"
gone "etc/systemd/system/frognet-beacon.service"
gone "etc/systemd/system/frognet-roster.service"
gone "opt/frognet_semantic/.frognet-ai-backup-20260909-215304"
gone "opt/frognet_semantic/.frognet-ai-backup-20260909-223632"
gone "opt/frognet_semantic/.frognet-ai-backup-20260910-065022"
gone "opt/frognet_semantic/.frognet-ai-version"
gone "opt/frognet_semantic/core/game_role.py"
gone "opt/frognet_semantic/discovery/sim/bundle_float_check.py"
gone "opt/frognet_semantic/discovery/test_bundle_float_oracle.py"
gone "opt/frognet_semantic/docs/GAME_ORIGIN_INSTALL.md"
gone "opt/frognet_semantic/simulation/SIM_RESULTS_board_game.md"
gone "usr/local/bin/install_boardgamehost.sh"
gone "var/www/html/family-calendar"
gone "opt/frognet_semantic/foo.py"
gone "opt/frognet_semantic/test_loop_frame.py"
gone "opt/frognet_semantic/test_resp_same_oracle.py"
gone "opt/frognet_semantic/tier1_fallbacks.diff"
gone "usr/local/frognet-ai/files (41).zip"
gone "opt/frognet_semantic/broker/opt/frognet_broker_v4/TUNNEL_ID_KEY_V1.diff"
echo "-- retired oracles: they assert behaviour later ruled out (.1 discovery 2026-09-25, [NO_CACHES_V1], the July on-link change, the 2026-09-12 barrier)"
gone "opt/frognet_semantic/discovery/test_behind_relay_pingpong_oracle.py"
gone "opt/frognet_semantic/discovery/test_tunnel_peer_health_emit_oracle.py"
gone "opt/frognet_semantic/discovery/test_lan_neighbor_resolver_oracle.py"
gone "opt/frognet_semantic/discovery/test_no_caches_oracle.py"
gone "opt/frognet_semantic/proxy/test_onlink_tunnel_oracle.py"
gone "opt/frognet_semantic/core/test_role_election_uncoupled_oracle.py"
echo "-- [T2 2026-10-12] the mediahost role, the old Python Communicator's remnants and the game remnants, retired"
# Stopped and disabled BEFORE their files go, so systemd still knows them by name.
units_off frognet-mediahost.service frognet-mediahost-advertise.timer frognet-mediahost-advertise.service frognet-reaper.service
gone "etc/systemd/system/frognet-mediahost-advertise.service"
gone "etc/systemd/system/frognet-mediahost-advertise.timer"
gone "etc/systemd/system/frognet-mediahost.service"
gone "etc/systemd/system/frognet-reaper.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-mediahost.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-reaper.service"
gone "etc/systemd/system/timers.target.wants/frognet-mediahost-advertise.timer"
gone "opt/frognet_semantic/FROGNET_NOW_mediahost_onsegment.md"
gone "opt/frognet_semantic/core/live.py"
gone "opt/frognet_semantic/core/sotf_handler.py"
gone "opt/frognet_semantic/core/test_mediahost_onsegment_oracle.py"
gone "opt/frognet_semantic/core/test_mediahost_pondwide_oracle.py"
gone "opt/frognet_semantic/discovery/sim/role_election_check.py"
gone "opt/frognet_semantic/discovery/test_lan_untunnelled_oracle.py"
gone "opt/frognet_semantic/proxy/frognet_media_diag.py"
gone "opt/frognet_semantic/proxy/frognet_media_planes.py"
gone "opt/frognet_semantic/ribbit/cpp/tests/reference/core/game_role.py"
gone "opt/frognet_semantic/simulation/bakeoff_rest_vs_sotf.py"
gone "opt/frognet_semantic/simulation/compare_bandwidth.py"
gone "opt/frognet_semantic/simulation/compare_udp_vs_sotf.py"
gone "opt/frognet_semantic/simulation/degrade_sweep.py"
gone "opt/frognet_semantic/simulation/games"
gone "opt/frognet_semantic/simulation/media_assets"
gone "opt/frognet_semantic/simulation/role_registry_lxmlfree_check.py"
gone "opt/frognet_semantic/simulation/run_all_media_stream.py"
gone "opt/frognet_semantic/simulation/run_all_tests.py"
gone "opt/frognet_semantic/simulation/run_hardware_loop.py"
gone "opt/frognet_semantic/simulation/run_movies.py"
gone "opt/frognet_semantic/simulation/sim_mediahost.py"
gone "opt/frognet_semantic/simulation/sim_sotf_media_codex.py"
gone "opt/frognet_semantic/simulation/test_call_host_roster_oracle.py"
gone "opt/frognet_semantic/simulation/test_call_host_service_oracle.py"
gone "opt/frognet_semantic/simulation/test_call_media_oracle.py"
gone "opt/frognet_semantic/simulation/test_call_mediahost_oracle.py"
gone "opt/frognet_semantic/simulation/test_dl_leg_settle_oracle.py"
gone "opt/frognet_semantic/simulation/test_downlink_backpressure_oracle.py"
gone "opt/frognet_semantic/simulation/test_group_call_oracle.py"
gone "opt/frognet_semantic/simulation/test_ladder_rate_v2_oracle.py"
gone "opt/frognet_semantic/simulation/test_lan_untunnelled_netns_oracle.py"
gone "opt/frognet_semantic/simulation/test_media_engine_pipe_oracle.py"
gone "opt/frognet_semantic/simulation/test_media_planes.py"
gone "opt/frognet_semantic/simulation/test_media_stream.py"
gone "opt/frognet_semantic/simulation/test_media_stream_e2e.py"
gone "opt/frognet_semantic/simulation/test_media_stream_metrics.py"
gone "opt/frognet_semantic/simulation/test_media_stream_transport.py"
gone "opt/frognet_semantic/simulation/test_media_stream_watch.py"
gone "opt/frognet_semantic/simulation/test_media_stream_wire.py"
gone "opt/frognet_semantic/simulation/test_outplane_nonblocking_oracle.py"
gone "opt/frognet_semantic/simulation/test_plane_direction_oracle.py"
gone "opt/frognet_semantic/simulation/test_self_echo_oracle.py"
gone "opt/frognet_semantic/simulation/test_server_temporal_subset_oracle.py"
gone "opt/frognet_semantic/simulation/test_spatial_band_oracle.py"
gone "opt/frognet_semantic/simulation/test_stats_overlay_oracle.py"
gone "opt/frognet_semantic/simulation/test_temporal_decode_clean_oracle.py"
gone "opt/frognet_semantic/simulation/test_temporal_layer_oracle.py"
gone "opt/frognet_semantic/simulation/tests_topology.py"
gone "opt/frognet_semantic/tests/test_sotf_handler.py"
gone "usr/local/bin/AVCAP_CACHE_SCHEMA_V1.diff"
gone "usr/local/bin/README_AVCAP_CACHE_SCHEMA_V1.md"
gone "usr/local/bin/build_linux_communicator.sh"
gone "usr/local/bin/build_windows_communicator.sh"
gone "usr/local/bin/check_blob.py"
gone "usr/local/bin/frognet_candidate.sh"
gone "usr/local/bin/install_mediahost.sh"
gone "usr/local/bin/oracle_avcap_cache.sh"
gone "usr/local/bin/package_communicator.py"
gone "usr/local/bin/runCaptureHeadless"
gone "usr/local/bin/runClient"
gone "usr/local/bin/runServerHeadless"
gone "usr/local/share/frognet/docs/SOTF_MEDIA_README.md"
t5_section() {
echo "-- [T5 2026-10-12] the PHP endpoints nothing calls, the admin and devices vhosts, the dashboard unit"
# The vhosts: a2dissite's link first (a dangling sites-enabled link stops apache2's config test), then the file.
gone "etc/apache2/sites-enabled/admin-site.conf"
gone "etc/apache2/sites-available/admin-site.conf"
gone "etc/apache2/sites-enabled/devices-site.conf"
gone "etc/apache2/sites-available/devices-site.conf"
gone "var/www/www_admin"
units_off frognet-dashboard.service
gone "etc/systemd/system/frognet-dashboard.service"
gone "usr/local/bin/PropogateHost"
gone "usr/local/bin/propogateMergeGuid.bash"
for f in api_route.php api_semantic_hosts.php frognet_add_network.php frognet_intercept.php frognet_setup_admin_api.php \
         getAllUsers.php getFrogNet.php getFrogs.php getResolv.php get_available_wifi.php connect_network.php \
         getteamlocations.php insertGeoLocation.php loginuser.php registerAndForward.php registerHost.php registeruser.php \
         setindividuallocation.php somethingChanged.php storeFrogNet.php storeGpsLoc.php wg-route.php runMerge.php \
         frognet_templates.php frognet_gateway.py frognet_cache.php getHosts.php.example frognet_echo.php.example \
         getDbHost.php getPonds.php Commands frognet_monitor MAMP-PRO-Logo.png; do
    gone "var/www/html/$f"
done
# FrogNet's old index.html only (the "Admin BlackBox Portal foo" page): an index.html without it is apache2's own
# default page, the distribution's file, and stays (000-default.conf serves index.php)
grep -qs "Admin BlackBox Portal foo" "$R/var/www/html/index.html" && gone "var/www/html/index.html"
}

t6_section() {
echo "-- [T6a fixes 2026-10-13] the transit watcher that did nothing, the databasehost advertiser the merge repeats"
# Units first, while systemd still knows them by name. frognet-transit-watch ran a disabled copy of the old bash
# discovery (`exit 0` at the top) under Restart=always: a restart every 5 s, no work. The advertiser (60 s) wrote the
# same databasehost/capability row every merge's publish_all writes. NOT pruned: frognet-dbhost-elect.{service,timer},
# frognet_dbhost_elector.py, /etc/sentinels/last_merge and the elector's oracle -- the elector stays (the post-merge
# window on the control, [ELECTOR_ON_LAN_ONLY_V1]).
units_off frognet-transit-watch.service frognet-dbhost-advertise.timer frognet-dbhost-advertise.service
gone "etc/systemd/system/frognet-transit-watch.service"
gone "etc/systemd/system/multi-user.target.wants/frognet-transit-watch.service"
gone "etc/systemd/system/frognet-dbhost-advertise.service"
gone "etc/systemd/system/frognet-dbhost-advertise.timer"
gone "etc/systemd/system/timers.target.wants/frognet-dbhost-advertise.timer"
gone "usr/local/bin/frognet_transit_watch.sh"
gone "usr/local/bin/frognet_register_candidate.sh"
gone "usr/local/bin/frognet_setup_advertisers.sh"
}

t3_section
t1_section
pip_console_section
t5_section
t6_section
echo
echo "-- python bytecode (regenerated on next import)"
for d in "$R/opt/frognet_semantic" "$R/usr/local/bin"; do
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
units_off frognet-nat64.service
if [ -z "$R" ] && [ "$APPLY" -eq 1 ]; then systemctl daemon-reload; fi

summary
exit $?
