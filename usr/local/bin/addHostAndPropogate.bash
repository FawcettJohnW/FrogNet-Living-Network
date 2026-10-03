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
# addHostAndPropogate.bash
#
# Install the host locally (addHost.bash) and, unless the IP
# is still in discovery_pending grace, propogateNotification.
##############################################################

# [INSTRUMENTATION_V2_APPLIED_BASH]
# shellcheck source=/dev/null
[[ -f /usr/local/lib/frognet_trace.sh ]] && . /usr/local/lib/frognet_trace.sh
. /usr/local/bin/mapInterfaces
. /usr/local/lib/frognet_log.sh
flog_init "addHostAndPropogate"
trap 'rc=$?; flog_info "EXIT" rc=$rc; exit $rc' EXIT

HOSTNAME="$1"
DEVICE_IP="$2"
APPEND_DOMAIN="$3"
PING_HOST="$4"
NS_DEV="$5"

flog_info "ENTER" "hostname=$HOSTNAME" "device_ip=$DEVICE_IP" "ns_dev=$NS_DEV" "ping_host=$PING_HOST" "append_domain=$APPEND_DOMAIN"

SENT_DIR="/etc/sentinels"
FORWARDED="$SENT_DIR/forwarded"

mkdir -p "$SENT_DIR"
touch "$FORWARDED"

ENTRY_KEY="$HOSTNAME $DEVICE_IP $NS_DEV"

if grep -Fq "$ENTRY_KEY" "$FORWARDED"; then
    flog_info "DEDUP" "decision=skip" "reason=already_forwarded" "key=$(flog_kv _ "$ENTRY_KEY")"
    exit 0
fi
flog_info "DEDUP" "decision=proceed" "key=$(flog_kv _ "$ENTRY_KEY")"

# Perform local mutation (accumulator only)
flog_stage addHost_invoke
/usr/local/bin/addHost.bash "$HOSTNAME" "$DEVICE_IP" "$APPEND_DOMAIN" "$PING_HOST" "$NS_DEV"
_ah_rc=$?
flog_stage_end addHost_invoke "rc=$_ah_rc"

# Suppress propagation when the IP is still within its discovery
# grace window — avoids a flurry of churn-notifications for a peer
# we just saw for the first time.
if /usr/local/bin/discovery_pending.sh is_pending "$DEVICE_IP"; then
    flog_info "PROPAGATE" "decision=suppress" "reason=discovery_pending" "ip=$DEVICE_IP"
else
    flog_info "PROPAGATE" "decision=fire" "reason=not_pending" "ip=$DEVICE_IP"
fi

echo "$ENTRY_KEY" >> "$FORWARDED"
flog_info "FORWARDED_record" "entry=$(flog_kv _ "$ENTRY_KEY")"

flog_info "COMPLETE" "hostname=$HOSTNAME" "ip=$DEVICE_IP"
exit 0
