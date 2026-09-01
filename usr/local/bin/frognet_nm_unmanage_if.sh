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
# /usr/local/bin/frognet_nm_unmanage_if.sh
#
# Make an interface unmanaged by NetworkManager so FrogNet can own it.
# FULL TRACE BUILD (additive logging).
#

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

DATE=/bin/date
ts(){ $DATE '+%Y-%m-%d %H:%M:%S.%3N'; }
log(){ echo "[frognet-nm-unmanage][$(ts)][pid=$$] $*"; }

DEV="${1:-}"
[[ -n "$DEV" ]] || { log "ERR missing dev arg"; exit 2; }

log "START dev=$DEV exec=$(readlink -f /proc/$$/exe 2>/dev/null) bash=$BASH_VERSION PATH=$PATH"

[[ -d "/sys/class/net/$DEV" ]] || { log "skip dev=$DEV reason=no_such_dev"; exit 0; }
[[ "$DEV" == "lo" ]] && { log "skip dev=$DEV reason=loopback"; exit 0; }

if [[ -d "/sys/class/net/$DEV/wireless" ]]; then
  log "skip dev=$DEV reason=wireless"
  exit 0
fi

CONF="/etc/NetworkManager/conf.d/99-frognet-unmanaged.conf"
mkdir -p /etc/NetworkManager/conf.d

if [[ ! -f "$CONF" ]]; then
  log "CREATE $CONF"
  cat >"$CONF" <<'EOF'
[keyfile]
unmanaged-devices=
EOF
fi

CUR="$(awk -F= '/^unmanaged-devices=/{print $2}' "$CONF" | head -n1)"
TOKEN="interface-name:${DEV}"

log "CONF current unmanaged-devices='$CUR' token='$TOKEN'"

echo "$CUR" | tr ',' '\n' | awk 'NF{print}' | grep -qx "$TOKEN"
if [[ "$?" != "0" ]]; then
  if [[ -z "$CUR" ]]; then
    NEW="$TOKEN"
  else
    NEW="${CUR},${TOKEN}"
  fi
  log "ACTION dev=$DEV persist_unmanaged new='$NEW'"
  awk -v new="$NEW" '
    BEGIN{done=0}
    /^unmanaged-devices=/{print "unmanaged-devices=" new; done=1; next}
    {print}
    END{if(!done){print "unmanaged-devices=" new}}
  ' "$CONF" > "${CONF}.tmp"
  mv -f "${CONF}.tmp" "$CONF"
else
  log "ok dev=$DEV persist_already_present token=$TOKEN"
fi

command -v nmcli >/dev/null 2>&1 || {
  log "ERR dev=$DEV reason=nmcli_missing (cannot enforce unmanaged live)"
  exit 3
}

if ! systemctl is-active --quiet NetworkManager; then
  log "WARN dev=$DEV NetworkManager_not_running (persisted only)"
  exit 0
fi

log "ACTION dev=$DEV nmcli device set managed no"
nmcli device set "$DEV" managed no 2>&1 | sed "s/^/[frognet-nm-unmanage][$(ts)][pid=$$][nmcli] /"
log "DONE dev=$DEV"
exit 0
