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
# /usr/local/bin/mergeHostsAndResolv.bash
#
# Performs merge work ONLY.  Propagates via propogateNotification
# (fire-and-forget).  mergePending / runAgain ownership lives in
# runMerge.bash.
#
# Parallelism model:
#
#   local identity + hosts preamble (serial)
#   tunnel reconcile (synchronous, broker-authoritative)
#     fetches broker list; if reachable, reconciles kernel wg ifaces
#     to match the response exactly (bring up missing, tear down
#     absent); if unreachable, tears down all wg ifaces; if node has
#     no own WAN uplink, tears down all wg ifaces.  Writes
#     /var/lib/frognet-tunnel/handshake_rtts.json.
#   sync_interfaces.sh (foreground)
#     wave 1 (LAN), reconcile (LAN-vs-WG RTT comparison + seeding
#     wave 2 with winning channels' peer .1s), wave 2 (probes
#     through wg ifaces + wave-1 getHosts children).  MAX_DEPTH=1,
#     wave 2 is a leaf.  No bringup barrier — reconcile already
#     completed.
#   /etc/hosts staging (with stable header — no timestamp)
#   dnsmasq forwarders staging
#   manageResolv (waits on fixDefaultRoute)
#   tunnel commit-only (foreground)
#     writes channel_map, runs committer (sole /24 installer),
#     reconciles daemon state with kernel.
#   propogateNotification if sync_required + emit_routes_snapshot
#
# [BROKER_AUTHORITATIVE_V1 2026-06-01] BRINGUP_READY/BRINGUP_DONE
# sentinels and the background bring-up-only fork are removed.
# Variables and cleanup remain only as harmless legacy: existing
# sentinel files (from older builds) are removed at startup, and
# the variables are defined but no longer signaled.

# [INSTRUMENTATION_V2_APPLIED_BASH]
# shellcheck source=/dev/null
[[ -f /usr/local/lib/frognet_trace.sh ]] && . /usr/local/lib/frognet_trace.sh
. /usr/local/lib/frognet_log.sh
flog_init "mergeHostsAndResolv"

trap 'rc=$?; flog_info "EXIT" rc=$rc; exit $rc' EXIT

# ------------------------------------------------------------
# Sentinel paths
# ------------------------------------------------------------
SENT_DIR="/etc/sentinels"
FROGNET_HOSTS="$SENT_DIR/frognet_hosts"
FROGNET_RESOLV="$SENT_DIR/frognet_resolv"
HOSTS_OUT="$SENT_DIR/frognet_hosts_out"
EXPECTED="$SENT_DIR/expected_routes"
FORWARDED="$SENT_DIR/forwarded"
SYNC_REQUIRED="$SENT_DIR/sync_required"
NEW_RESOLV="$SENT_DIR/new_resolv.conf"
BRINGUP_READY="$SENT_DIR/tunnel_bringup_ready"
BRINGUP_DONE="$SENT_DIR/tunnel_bringup_done"
FWD_FILE="/etc/dnsmasq.d/frognet_forwarders_auto.conf"
FWD_TMP="$SENT_DIR/frognet_forwarders_auto.conf.tmp"
DNS_RELOAD_NEEDED=0

MKDIR="/usr/bin/mkdir"
CP="/bin/cp"
CMP="/usr/bin/cmp"
AWK="/usr/bin/awk"
SORT="/usr/bin/sort"
MKTEMP="/usr/bin/mktemp"
RM="/usr/bin/rm"
ECHO="/bin/echo"
DATE="/bin/date"
CHMOD="/bin/chmod"

$MKDIR -p "$SENT_DIR" /etc/dnsmasq.d
flog_stage clear_working_files
$ECHO -n "" > "$FROGNET_HOSTS"
$ECHO -n "" > "$FROGNET_RESOLV"
$ECHO -n "" > "$FORWARDED"
$ECHO -n "" > "$NEW_RESOLV"
$RM -f "$SYNC_REQUIRED" "$BRINGUP_READY" "$BRINGUP_DONE"
$CHMOD 0644 "$FROGNET_HOSTS" "$FROGNET_RESOLV" "$NEW_RESOLV"
flog_stage_end clear_working_files

# ------------------------------------------------------------
# Local host entry
# ------------------------------------------------------------
flog_stage local_identity
ETH0_IP="$(/usr/local/bin/getEth0Address)"
DOMAIN="$(/usr/local/bin/getOurDomain)"
HOST_SHORT="$(/usr/bin/hostname -s)"
flog_info "LOCAL" "host_short=$HOST_SHORT" "domain=$DOMAIN" "eth0_ip=$ETH0_IP"

if [[ -n "$ETH0_IP" && -n "$DOMAIN" ]]; then
    ADMIN_IP="${ETH0_IP%.*}.2"
    # [HOSTS_FORMAT_V2] /etc/hosts entries are "<ip> FrogNetHost.<network>"
    # only — no bare short name, no extra aliases.  The self-line (this
    # machine) gets the bare "FrogNetHost" alias appended downstream
    # during /etc/hosts staging, where we already know which IP is self.
    echo "$ETH0_IP FrogNetHost.$DOMAIN" >> "$FROGNET_HOSTS"
    echo "$ADMIN_IP FrogNetAdmin.$DOMAIN" >> "$FROGNET_HOSTS"
    flog_info "LOCAL_appended" "host=$ETH0_IP" "admin=$ADMIN_IP"
else
    flog_warn "LOCAL_SKIP" "reason=missing_eth0_ip_or_domain" "eth0=$ETH0_IP" "domain=$DOMAIN"
fi
flog_stage_end local_identity

# ------------------------------------------------------------
# Tunnel reconcile (synchronous, broker-authoritative)
# ------------------------------------------------------------
# [BROKER_AUTHORITATIVE_V1 2026-06-01] Replaced background
# `bring-up-only` fork with a synchronous broker-reconcile call.
# Rules:
#   - Broker response IS the target state.  Kernel wg ifaces are
#     reconciled to match exactly: bring up missing, tear down
#     absent.
#   - Broker unreachable → tear down every wg iface and proceed.
#     No cached state is used.
#   - Node without own WAN uplink → target set is empty, all wg
#     ifaces torn down.
# This runs to completion BEFORE sync_interfaces starts, so by
# the time sync_interfaces probes, every wg iface either exists
# (broker said so) or doesn't (it didn't, or no broker).  No
# BRINGUP_READY barrier needed.
flog_stage reconcile_tunnels
PYTHONPATH=/opt/frognet_semantic /usr/bin/python3 -m internet_tunnels_v3 bring-up-only 2>&1 \
  | while IFS= read -r line; do flog_info "reconcile: $line"; done
_rc=${PIPESTATUS[0]}
flog_info "reconcile_rc" "rc=$_rc"
flog_stage_end reconcile_tunnels "rc=$_rc"

# ------------------------------------------------------------
# Discovery (foreground)
# ------------------------------------------------------------
flog_stage sync_interfaces
/usr/local/bin/sync_interfaces.sh
_si_rc=$?
flog_stage_end sync_interfaces "rc=$_si_rc"

# Snapshot state right after discovery
flog_dump_cmd "post_discovery_routes" ip -4 route show
flog_dump "post_discovery_observations" "/etc/sentinels/discovery_observations.tsv"
flog_dump "post_discovery_handshake_rtts" "/var/lib/frognet-tunnel/handshake_rtts.json"

# ------------------------------------------------------------
# /etc/hosts staging
# ------------------------------------------------------------
flog_stage stage_hosts
tmp="$($MKTEMP)"
{
  echo "################################################"
  echo "# AUTO-GENERATED BY mergeHostsAndResolv.bash   #"
  echo "################################################"
  echo "127.0.0.1 localhost FrogNetHost"

  # [HOSTS_FORMAT_V2] Normalize every 10.x line down to:
  #     <ip> FrogNetHost.<network>     (host lines)
  #     <ip> FrogNetAdmin.<network>    (admin alias lines)
  # Bare short names (Seattle1, BAMacBook, etc.) and bare "FrogNetHost"
  # aliases are stripped here.  The self-line then gets "FrogNetHost"
  # appended below — that's the one and only place the bare alias
  # belongs in /etc/hosts.
  $AWK -v self_ip="$ETH0_IP" '
    $1 ~ /^10\./ {
      ip = $1
      keep = ""
      for (i = 2; i <= NF; i++) {
        if ($i ~ /^FrogNetHost\./ || $i ~ /^FrogNetAdmin\./) {
          keep = $i
          break
        }
      }
      if (keep == "") next
      if (ip == self_ip && keep ~ /^FrogNetHost\./) {
        print ip, keep, "FrogNetHost"
      } else {
        print ip, keep
      }
    }
  ' "$FROGNET_HOSTS" | $SORT -u
} > "$tmp"

_hosts_changed=0
if ! $CMP -s "$tmp" /etc/hosts; then
    _hosts_changed=1
    flog_info "HOSTS_update" "changed=YES"
    # Save the old + new so you can diff them post-hoc
    flog_dump "hosts_old" /etc/hosts
    flog_dump "hosts_new" "$tmp"
    $CP "$tmp" /etc/hosts
    chmod 644 /etc/hosts
    DNS_RELOAD_NEEDED=1
    : > "$SYNC_REQUIRED"
    flog_info "sync_required_set" "reason=hosts_changed"
else
    flog_info "HOSTS_update" "changed=NO"
fi

$CP "$tmp" "$HOSTS_OUT" || true
chmod 644 "$HOSTS_OUT"
$RM -f "$tmp"
flog_stage_end stage_hosts "changed=$_hosts_changed"

# ------------------------------------------------------------
# dnsmasq forwarders
# ------------------------------------------------------------
flog_stage stage_dnsmasq
{
  echo "# AUTO-GENERATED"
  $AWK '
    $1 ~ /^10\./ {
      for(i=2;i<=NF;i++){
        if($i ~ /^FrogNetHost\./){
          dom=$i; sub(/^FrogNetHost\./,"",dom)
          print "server=/" dom "/" $1
          print "server=/." dom "/" $1
        }
      }
    }
  ' "$HOSTS_OUT" | $SORT -u
} > "$FWD_TMP"

OUR_IP="$(/usr/local/bin/getEth0Address)"
if [[ -n "$OUR_IP" ]]; then
    echo "listen-address=127.0.0.1,$OUR_IP" >> "$FWD_TMP"
    echo "bind-interfaces" >> "$FWD_TMP"
    flog_info "dnsmasq_listen_set" "ip=$OUR_IP"
else
    flog_warn "dnsmasq_SKIP_listen" "reason=no_our_ip"
fi

_dnsmasq_changed=0
if [[ ! -f "$FWD_FILE" ]] || ! $CMP -s "$FWD_TMP" "$FWD_FILE"; then
    _dnsmasq_changed=1
    flog_info "DNSMASQ_forwarders" "changed=YES" "file=$FWD_FILE"
    flog_dump "dnsmasq_old" "$FWD_FILE"
    flog_dump "dnsmasq_new" "$FWD_TMP"
    $CP "$FWD_TMP" "$FWD_FILE"
    DNS_RELOAD_NEEDED=1
    : > "$SYNC_REQUIRED"
    flog_info "sync_required_set" "reason=dnsmasq_changed"
else
    flog_info "DNSMASQ_forwarders" "changed=NO"
fi
$RM -f "$FWD_TMP"
flog_stage_end stage_dnsmasq "changed=$_dnsmasq_changed"

# ------------------------------------------------------------
# Post-discovery stage — two concurrent chains
# ------------------------------------------------------------
flog_stage post_discovery_parallel

/usr/local/bin/fixDefaultRoute &
_fdr_pid=$!
flog_info "fdr_forked" "pid=$_fdr_pid"

_mhj_pid=""
# [MAKEHOSTJSON_ALWAYS_REBUILD_V1] Rebuild the host JSON on EVERY merge, not only
# when /etc/hosts or the dnsmasq forwarders changed. makeHostJson's output embeds
# per-host ECHO/identity data, and that can change while /etc/hosts is byte-for-byte
# identical (e.g. an on-segment neighbour that now echoes as Seattle3/10.130.130.1).
# Its cache is keyed on the /etc/hosts SHA, so an echo-only change would otherwise
# leave getHosts.php serving stale JSON and discovery blind to the change. A forced
# rebuild reconciles echo every merge; the DNS reload itself still only fires when
# DNS_RELOAD_NEEDED (below).
flog_info "makeHostJson_fork" "reason=always_rebuild dns_reload_needed=$DNS_RELOAD_NEEDED"
/usr/local/bin/makeHostJson.bash rebuild &
_mhj_pid=$!

wait "$_fdr_pid"
_fdr_rc=$?
flog_info "fixDefaultRoute_done" "rc=$_fdr_rc"

flog_stage manageResolv
/usr/local/bin/manageResolv.bash
flog_stage_end manageResolv "rc=$?"

if [[ -n "$_mhj_pid" ]]; then
    wait "$_mhj_pid"
    flog_info "makeHostJson_done" "rc=$?"
fi
flog_stage_end post_discovery_parallel

# ------------------------------------------------------------
# Tunnel commit
# ------------------------------------------------------------
flog_stage commit_only
PYTHONPATH=/opt/frognet_semantic /usr/bin/python3 -m internet_tunnels_v3 commit-only 2>&1 \
    | while IFS= read -r line; do flog_info "commit: $line"; done
_commit_rc=${PIPESTATUS[0]}
flog_stage_end commit_only "rc=$_commit_rc"

# Post-commit route snapshot so you can see what committer did
flog_dump_cmd "post_commit_routes" ip -4 route show
flog_dump "post_commit_observations" "/etc/sentinels/discovery_observations.tsv"
flog_dump "post_commit_snapshot" "/etc/sentinels/route_snapshot.tsv"

$RM -f "$BRINGUP_READY" "$BRINGUP_DONE"

# Propagate to neighbors when this pass changed something (sync_required present).
# The chain-level 'announce only when converged' gate lives in runMerge.
# flag is present the pass converged with no change, so there is nothing to announce.
if [[ -f "$SYNC_REQUIRED" ]]; then
    flog_info "PROP" "sync_required=YES" "action=propogateNotification"
    /usr/local/bin/propogateNotification
else
    flog_info "PROP" "sync_required=NO"
fi

flog_stage emit_routes_snapshot
/usr/local/bin/emit_routes_snapshot.sh || true
flog_stage_end emit_routes_snapshot "rc=$?"

exit 0
