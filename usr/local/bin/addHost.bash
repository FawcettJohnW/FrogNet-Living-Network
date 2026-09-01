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
##############################################################
#  FrogNet addHost (hosts/resolv only — routes REMOVED)      #
#                                                            #
#  Resulting host entries (two lines per peer):              #
#      <DEVICE_IP> <DOMAIN> FrogNetHost.<DOMAIN>             #
#      <ADMIN_IP>  FrogNetAdmin.<DOMAIN>                     #
#  where ADMIN_IP = subnet_prefix(DEVICE_IP) + ".2".         #
#                                                            #
#  ROUTE POLICY:                                             #
#    addHost NO LONGER installs routes.  Route installation  #
#    is the sole responsibility of the frognet_route         #
#    committer (invoked by sync_interfaces.sh).  This script #
#    writes /etc/hosts fragments and /etc/resolv fragments   #
#    only.                                                   #
#                                                            #
#  The old route-install block (lines 114–141 of the pre-    #
#  committer version) has been deleted.  That block used to  #
#  `ip route replace $SUBNET via $PING_HOST dev $NS_DEV      #
#  metric 50` and was one of three places in the system that #
#  touched /24 routes — the exact problem the committer      #
#  refactor eliminates.                                      #
##############################################################

# [INSTRUMENTATION_V2_APPLIED_BASH]
# shellcheck source=/dev/null
[[ -f /usr/local/lib/frognet_trace.sh ]] && . /usr/local/lib/frognet_trace.sh
. /usr/local/bin/mapInterfaces
. /usr/local/lib/frognet_log.sh
flog_init "addHost"

HOSTNAME="$1"
DEVICE_IP="$2"
APPEND_DOMAIN="$3"
PING_HOST="$4"     # kept in argv for compatibility with callers
NS_DEV="$5"        # kept in argv for compatibility with callers

FROGNET_HOSTS="/etc/sentinels/frognet_hosts"
FROGNET_RESOLV="/etc/sentinels/frognet_resolv"

/usr/bin/mkdir -p /etc/sentinels
/usr/bin/touch "$FROGNET_HOSTS"
/usr/bin/touch "$FROGNET_RESOLV"

flog_info "ENTER" "hostname=$HOSTNAME" "device_ip=$DEVICE_IP" "ping_host=$PING_HOST" "ns_dev=$NS_DEV" "append_domain=$APPEND_DOMAIN"

is_ip()  { [[ "${1:-}" =~ ^([0-9]+\.){3}[0-9]+$ ]]; }
is_10x() { [[ "${1:-}" == 10.* ]]; }

NAME_PART="${HOSTNAME%%.*}"
DOMAIN_PART="${HOSTNAME#*.}"

if [[ "$DOMAIN_PART" == "$NAME_PART" ]]; then
    DOMAIN="${APPEND_DOMAIN:-$NAME_PART}"
    flog_info "domain_derived" "source=name_part_or_append_domain" "domain=$DOMAIN"
else
    DOMAIN="$DOMAIN_PART"
    flog_info "domain_derived" "source=hostname_suffix" "domain=$DOMAIN"
fi

ourIP="$(/usr/local/bin/getEth0Address 2>/dev/null || echo "")"
if [[ -n "$ourIP" && "$ourIP" == "$DEVICE_IP" ]]; then
    flog_info "DECISION" "action=bypass" "reason=self" "ip=$DEVICE_IP"
    exit 0
fi

HOSTNAME="$NAME_PART.$DOMAIN"
flog_info "canonical" "hostname=$HOSTNAME" "domain=$DOMAIN"

# Cleanup prior entries so we can rewrite them fresh.
ADMIN_IP="${DEVICE_IP%.*}.2"
/usr/bin/sed -i "\| FrogNetHost\.${DOMAIN}\b|d"  "$FROGNET_HOSTS" || true
/usr/bin/sed -i "\| FrogNetAdmin\.${DOMAIN}\b|d" "$FROGNET_HOSTS" || true
/usr/bin/sed -i "\|^${DEVICE_IP}[[:space:]]\b|d" "$FROGNET_HOSTS" || true
/usr/bin/sed -i "\|^${ADMIN_IP}[[:space:]]\b|d"  "$FROGNET_HOSTS" || true
flog_info "cleanup_done" "domain=$DOMAIN" "device_ip=$DEVICE_IP" "admin_ip=$ADMIN_IP"

# Canonical frognet_hosts entries
HOST_ENTRY="$DEVICE_IP $DOMAIN FrogNetHost.$DOMAIN"
if ! /usr/bin/grep -Fq "$HOST_ENTRY" "$FROGNET_HOSTS"; then
    echo "$HOST_ENTRY" >> "$FROGNET_HOSTS"
    flog_info "APPEND" "file=frognet_hosts" "entry=$(flog_kv _ "$HOST_ENTRY")"
else
    flog_info "APPEND_skip" "file=frognet_hosts" "reason=already_present" "entry=$(flog_kv _ "$HOST_ENTRY")"
fi
ADMIN_ENTRY="$ADMIN_IP FrogNetAdmin.$DOMAIN"
if ! /usr/bin/grep -Fq "$ADMIN_ENTRY" "$FROGNET_HOSTS"; then
    echo "$ADMIN_ENTRY" >> "$FROGNET_HOSTS"
    flog_info "APPEND" "file=frognet_hosts" "entry=$(flog_kv _ "$ADMIN_ENTRY")"
else
    flog_info "APPEND_skip" "file=frognet_hosts" "reason=already_present" "entry=$(flog_kv _ "$ADMIN_ENTRY")"
fi

echo "nameserver $DEVICE_IP" >> "$FROGNET_RESOLV" || true

flog_info "EXIT" "hostname=$HOSTNAME" "device_ip=$DEVICE_IP" "rc=0"
exit 0
