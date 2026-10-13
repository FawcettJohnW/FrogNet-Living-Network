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
# =============================================================================
# /usr/local/lib/frognet/conf.sh - [ONE_CONF_V1]
#
# Key-wise edits of the node's /etc/frognet/tunnel.conf. Nothing reads that file
# any more (T4c: the region switch is /opt/fdf/var/join.env alone); the tunnel
# daemon names what is left in it once.
#
# One caller: frognet_prune_removed.sh's T3 section (run by the installer's C1c
# upgrade step and by hand after an untar), which removes the retired keys with
# fn_conf_unset. Key-wise, never a rewrite: any line it was not asked for stays.
#
#   FROGNET_CONF        the file (default /etc/frognet/tunnel.conf)
#   fn_conf_unset KEY   remove every KEY= line; absent key or file is not an error
#
# [NO_FALLBACK_V1] A write that cannot happen fails (non-zero), it is not papered over.
# =============================================================================

FROGNET_CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"

# Remove one key. Absent key or absent file is not an error - the requested state
# already holds.
fn_conf_unset() {
    local _k="${1:?fn_conf_unset: KEY required}"
    [ -f "$FROGNET_CONF" ] || return 0
    grep -q "^${_k}=" "$FROGNET_CONF" || return 0
    local _tmp; _tmp="$(mktemp "${FROGNET_CONF}.XXXXXX")" || return 1
    # grep -v exits 1 when no line is left; that is a valid (empty) result.
    grep -v "^${_k}=" "$FROGNET_CONF" > "$_tmp"
    [ $? -le 1 ] || { rm -f "$_tmp"; return 1; }
    chmod --reference="$FROGNET_CONF" "$_tmp" || { rm -f "$_tmp"; return 1; }
    chown --reference="$FROGNET_CONF" "$_tmp" || { rm -f "$_tmp"; return 1; }
    mv -f "$_tmp" "$FROGNET_CONF"
}
