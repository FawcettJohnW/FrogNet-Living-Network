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
# frognet_dhcp_scope.sh - renders /etc/dnsmasq.d/opts_only.conf FROM THE
# TEMPLATE at /etc/dnsmasq_conf_template, and nothing else.
#
# Sourced by setup_lillypad_v4.bash and frognet_fixup.sh.
#
# WHY THIS EXISTS
#
# setup_lillypad_v4.bash had stopped using the template and wrote its own
# 8-line heredoc. That dropped domain-needed, bogus-priv, every `interface=`
# line, clear-on-reload, cache-size, the dotted `local=/.Domain/`, the
# dhcp-script hook and dhcp-option=19,1, and invented option:router and
# option:dns-server that the template never had. frognet_fixup.sh then APPENDED
# exclusions to whatever was there, so repeat runs accumulated lines. Between
# them a node ended up with DHCP refused on its own AP interface, and clients
# associated to hostapd and timed out with nothing in any log.
#
# The template is the specification. This renders it verbatim, substitutes the
# two placeholders, and rewrites ONLY the interface block.
#
# THE RULES, and how the template already writes them down:
#
#   served      ->  interface=<dev>              (live, no exclusion)
#   not served  ->  # interface=<dev>            (commented)
#                   no-dhcp-interface=<dev>      (live)
#
#   1. eth0 present                                -> served
#   2. wlan0/wlan1 present AND hosting hostapd     -> served
#   3. wlan0/wlan1 present AND NOT hosting hostapd -> NOT served
#   4. everything else (frognet0, wg*, tun*, extra uplinks) -> NOT served
#
# Rule 1 is PRESENCE. Not carrier, not whether GATEWAY_IP is up: the old code
# keyed on GATEWAY_IP first, which resolves to empty whenever the address is
# not up yet, so which interface served DHCP depended on timing. A wired LAN
# with nothing plugged into it yet must still be ready for the first device.
#
# Serving is a SET, never a winner. The old code computed a single $SERVED_IF
# and excluded all others, which cannot serve eth0 AND the AP at once.
# =============================================================================

FROGNET_DNSMASQ_TEMPLATE="${FROGNET_DNSMASQ_TEMPLATE:-/etc/dnsmasq_conf_template}"

# frognet_hostapd_iface
#   Echoes the interface hostapd is hosting, empty if it hosts none.
#   Hosting = hostapd.conf names an interface AND the unit is not masked.
#   Wired mode masks hostapd (setup_lillypad_v4 "projection off"); a masked
#   hostapd hosts nothing, which is rule 3 rather than a special case.
frognet_hostapd_iface() {
    local conf="/etc/hostapd/hostapd.conf" iface="" state=""
    [ -f "$conf" ] || return 0
    iface="$(awk -F= '/^[[:space:]]*interface=/{print $2; exit}' "$conf" \
             | tr -d '[:space:]')"
    [ -n "$iface" ] || return 0
    if command -v systemctl >/dev/null; then
        state="$(systemctl is-enabled hostapd 2>&1)"
        case "$state" in masked*) return 0 ;; esac
    fi
    printf '%s\n' "$iface"
}

# frognet_dhcp_served_ifaces
#   Every interface that must serve DHCP, one per line.
frognet_dhcp_served_ifaces() {
    local ap="" served="" d=""
    if [ -z "${eth0Name:-}" ] || [ -z "${wlan0Name:-}" ]; then
        # shellcheck disable=SC1091
        . /usr/local/bin/mapInterfaces >/dev/null 2>&1 || true
    fi
    : "${eth0Name:=eth0}" "${wlan0Name:=wlan0}" "${wlan1Name:=wlan1}"

    ap="$(frognet_hostapd_iface)"

    [ -d "/sys/class/net/$eth0Name" ] && served="$served $eth0Name"

    for d in "$wlan0Name" "$wlan1Name"; do
        [ -n "$d" ] || continue
        [ -d "/sys/class/net/$d" ] || continue
        if [ -n "$ap" ] && [ "$d" = "$ap" ]; then served="$served $d"; fi
    done

    # hostapd on a renamed or USB radio: rule 2 is about HOSTING, not the name.
    if [ -n "$ap" ] && [ -d "/sys/class/net/$ap" ]; then
        case " $served " in *" $ap "*) : ;; *) served="$served $ap" ;; esac
    fi

    for d in $served; do printf '%s\n' "$d"; done
}

# frognet_render_dnsmasq_opts <outfile> <domain> <dhcp-range>
#   Renders the template to <outfile>. TRUNCATES: never appends, so repeat runs
#   cannot accumulate lines. Returns 1 and writes NOTHING if nothing is
#   servable - an unscoped dnsmasq answers DHCP on somebody else's LAN segment,
#   which is worse than absent.
frognet_render_dnsmasq_opts() {
    local out="$1" domain="$2" range="$3"
    local served="" all="" d="" tmp="" line="" emitted=0

    if [ ! -f "$FROGNET_DNSMASQ_TEMPLATE" ]; then
        printf 'dnsmasq: FATAL template missing: %s\n' "$FROGNET_DNSMASQ_TEMPLATE"
        return 1
    fi

    served="$(frognet_dhcp_served_ifaces | tr '\n' ' ')"
    if [ -z "$(printf %s "$served" | tr -d ' ')" ]; then
        printf 'dnsmasq: FATAL no DHCP-servable interface (no %s, hostapd hosting nothing)\n' \
               "${eth0Name:-eth0}"
        return 1
    fi

    # Present interfaces, PLUS any the template names even if currently absent,
    # so a template line is never silently dropped.
    all="$(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -v '^lo$')
$(sed -n 's/^#\{0,1\}[[:space:]]*\(no-dhcp-\)\{0,1\}interface=\([^[:space:]]*\).*/\2/p' \
      "$FROGNET_DNSMASQ_TEMPLATE")"
    all="$(printf '%s\n' $all | sort -u)"

    tmp="$(mktemp)" || return 1

    # Copy the template verbatim, substituting the two placeholders, and
    # replace the interface block in place at its first line, so the rest of
    # the file keeps the template's own order, spacing and comments.
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            \#*interface=*|interface=*|no-dhcp-interface=*)
                if [ "$emitted" -eq 0 ]; then
                    for d in $all; do
                        case " $served " in
                            *" $d "*) printf 'interface=%s\n' "$d" >> "$tmp" ;;
                            *) printf '# interface=%s\nno-dhcp-interface=%s\n' \
                                      "$d" "$d" >> "$tmp" ;;
                        esac
                    done
                    emitted=1
                fi
                continue
                ;;
        esac
        printf '%s\n' "$line" \
            | sed -e "s/DomainHere/$domain/g" -e "s|RangeHere|$range|g" >> "$tmp"
    done < "$FROGNET_DNSMASQ_TEMPLATE"

    cat "$tmp" > "$out"
    rm -f "$tmp"

    printf 'dnsmasq: rendered %s from %s; DHCP served on [%s]\n' \
           "$out" "$FROGNET_DNSMASQ_TEMPLATE" "$(printf %s "$served" | sed 's/ *$//')"
    return 0
}
