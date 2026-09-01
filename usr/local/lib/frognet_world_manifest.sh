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
# frognet_world_manifest.sh - [ONE_MANIFEST_V1]
#
# ONE list of what a FrogNet node is made of. Sourced by full_tar.bash (the
# public repo) and frognet_build_release.sh (the node clone), so the two cannot
# disagree about what a node consists of.
#
# Why one list: there used to be three. build_release had 23 paths,
# make_build_tar had 6 ("the installer regenerates /etc" - false), and full_tar
# had 5 plus an exclude list. The published source tarball shipped 5 of 23
# paths, so a checkout could not produce a working node. What made it invisible:
# make_build_tar logged "(absent, skipped)" for a missing path and carried on,
# so a tarball with eighteen holes looked like a successful build. Hence
# frognet_check_manifest below, which refuses.
#
# Cut by PATH, never by extension. The original full_tar used
# --exclude='*.tgz', which took the BROKER (it ships as a bundle), and
# --exclude='*.png|*.svg|*.ico', which took the web root's assets - the shipped
# var/www/html had zero images in it.
# =============================================================================

# -----------------------------------------------------------------------------
# What a node is made of. Relative to /.
# -----------------------------------------------------------------------------
FROGNET_WORLD_PATHS=(
    # Scripts and binaries
    usr/local/bin
    usr/local/sbin

    # FrogNet's own libraries under usr/local/lib. NOT the parent: that also holds
    # python3.11/dist-packages, 332 MB of somebody else's build output.
    # [DEPS_ARE_INSTALLED_NOT_SHIPPED_V1]
    #
    # These are the REQUIRED ones - absent, frognet_check_manifest fails the build.
    # usr/local/lib/frognet/ holds conf.sh, which frognet-conf-consolidate.sh,
    # frognet-make-lanonly.sh and frognet-unmake-gateway.sh all source and none of
    # them can run without. It was never packaged: the previous list named
    # frognet_trace.sh and frognet_log.sh individually and stopped there, so
    # install phase C1c died with "FATAL: /usr/local/lib/frognet/conf.sh not found"
    # on BrokerHost 2026-08-29. Anything else matching frognet* is picked up by the
    # autodiscovery below.
    usr/local/lib/frognet
    usr/local/lib/frognet_trace.sh
    usr/local/lib/frognet_log.sh

    # Configuration
    #
    # [WE_DO_NOT_OWN_THE_DISTRIBUTION_V1] NAMED FILES, not the directories. Scoping
    # etc/apache2 shipped Debian's entire apache config - ports.conf, envvars,
    # magic, every mods-available/*.load, conf-available/*, and a
    # sites-available.backup-2025-11-24 directory. etc/logrotate.d shipped
    # logrotate rules for cups, ppp, aptitude and asterisk.
    # etc/mysql/mariadb.conf.d shipped MariaDB's own 50-*.cnf.
    # None of that is ours to license, to distribute as ours, or to overwrite on
    # somebody else's machine - and it is how a GPLv2-only repository ends up
    # containing another project's files. Surfaced 2026-09-01 by the licensing
    # pass, which tried to put our copyright notice on all of it.
    #
    # Everything below is a file FrogNet writes. If the installer does not write
    # it, it does not belong here.
    etc/apache2/sites-available/000-default.conf
    etc/apache2/sites-available/admin-site.conf
    etc/apache2/sites-available/databasehost.conf
    etc/apache2/sites-available/databasehost-ssl.conf
    etc/apache2/sites-available/default-ssl.conf
    etc/apache2/sites-available/devices-site.conf
    etc/apache2/sites-available/frognet-ssl.conf
    etc/apache2/conf-available/unrest-family-calendar.conf

    etc/dnsmasq.d
    etc/frognet
    etc/iptables

    etc/logrotate.d/frognet

    etc/mysql/mariadb.conf.d/99-frognet.cnf
    etc/mysql/mariadb.conf.d/99-frognet-perf.cnf
    etc/mysql/mariadb.conf.d/99-frognet-tune.cnf

    # NetworkManager.conf ITSELF is Debian's, edited in place by the installer -
    # it is not ours and must not ship; the installer edits whatever is on the box.
    etc/NetworkManager/conf.d/10-frognet-unmanaged.conf
    etc/NetworkManager/conf.d/99-frognet-unmanaged.conf
    etc/NetworkManager/conf.d/frognet-unmanaged-ap.conf
    etc/NetworkManager/dispatcher.d/90-frognet-merge
    etc/NetworkManager/dispatcher.d/91-frognet-connectivity
    etc/ssl
    # [WE_DO_NOT_OWN_THE_DISTRIBUTION_V1] Named files, not the directory. Scoping
    # to etc/sysctl.d shipped Debian's and the Raspberry Pi foundation's stock
    # files - 98-rpi.conf, 99-sysctl.conf, README.sysctl, 99-ipv6-forwarding.conf -
    # inside a GPLv2-only work. They are not ours to license or to distribute as
    # ours, and the licensing pass surfaced them by trying to put our copyright
    # notice on them. The installer writes 90-frognet-forwarding.conf itself
    # (phase B1); both FrogNet files ship so a reset can restore them.
    etc/sysctl.d/90-frognet-forwarding.conf
    etc/sysctl.d/99-frognet.conf
    etc/setup_iptables

    # [UNITS_ARE_PART_OF_THE_WORLD_V1] The DIRECTORY, not a drop-in inside it.
    # This was scoped to etc/systemd/system/dnsmasq.service.d, so every world tar
    # ever built carried that one subdirectory and not one FrogNet unit. A node
    # installed from such a release boots with no proxy, no daemon, no tunnels.
    etc/systemd/system

    # NOT the *.target.wants symlink farms below it. Those are systemd's RECORD OF
    # WHAT IS ENABLED on one particular box, written by `systemctl enable`, and 45
    # of them showed up in the licensing pass as files with no copyright notice -
    # because they are not files, they are symlinks, several pointing at Debian's
    # own units. Shipping them would hand a new node another node's enable state.

    # WireGuard directory structure. The keys themselves are cut by
    # FROGNET_NEVER_SHIP below.
    etc/wireguard

    # Web files
    var/www/html
    var/www/www_admin

    # Application bundles: the Communicator and the app codices, including the
    # capability role-election code (frognet_role_elect, frognet_service_hosts,
    # frognet_tuples).
    etc/frognet_bundles

    # Python source
    opt/frognet_semantic

    # [LICENSE_TRAVELS_WITH_THE_WORK_V1] COPYING and LICENSE. The GPL requires the
    # license text accompany the work, and the work is what this list describes -
    # so the text has to be IN the list, not beside it. Living only at the repo
    # root would mean the world tar (which is built from /) shipped a GPL-licensed
    # node with no license on it. full_tar additionally emits copies at the archive
    # ROOT, where GitHub and every scanner look.
    usr/local/share/frognet
)

# [LIB_MANIFEST_AUTODISCOVER_V1] A hand-written file list under /usr/local/lib
# drifts every time a library is added - frognet_log.sh was added, then
# frognet_trace.sh, then frognet/, and each time the list was updated one release
# late. DISCOVER the FrogNet lib namespace instead of depending on somebody
# remembering. The required entries above still fail the build when absent; this
# only adds what is there and unlisted.
for _l in /usr/local/lib/frognet*; do
    [ -e "$_l" ] || continue
    _rel="${_l#/}"
    case " ${FROGNET_WORLD_PATHS[*]} " in
        *" $_rel "*) ;;
        *) FROGNET_WORLD_PATHS+=( "$_rel" ) ;;
    esac
done
unset _l _rel

# -----------------------------------------------------------------------------
# NEVER SHIP. Node identity and key material.
#
# tar --exclude patterns, matched against the archive member path. These are the
# difference between a public repository and a copy of somebody's node.
#
# The rule for adding: if a fresh install would REGENERATE it, or if it is unique
# to one box, it belongs here. Being secret is sufficient but not necessary -
# /etc/fnid is not a secret and still must never ship, because two nodes with the
# same GUID is a broken pond.
# -----------------------------------------------------------------------------
FROGNET_NEVER_SHIP=(
    # WireGuard. The directory ships (the installer expects it); the configs and
    # keys do not. Every wg*.conf holds a live PrivateKey.
    'etc/wireguard/wg*.conf'
    'etc/wireguard/*.key'
    'etc/wireguard/*.pub'
    'etc/wireguard/privatekey*'
    'etc/wireguard/publickey*'

    # Node identity. Regenerated at install by frognet-node-guid.sh --ensure.
    # Shipping it gives every cloned node the same identity.
    'etc/fnid'

    # Pond membership and broker credentials. tunnel.conf holds GROUP_TOKEN,
    # PASSCODE and the broker URL; broker.conf the broker's own. A node redeems
    # its own at join.
    'etc/frognet/tunnel.conf'
    'etc/frognet/broker.conf'
    'etc/frognet/wifi.env'

    # Per-node RUNTIME STATE under etc/frognet. The DIRECTORY ships (the installer
    # and conf.sh expect it); this generated content does not. Found 2026-08-31 in
    # a build candidate:
    #   gateways.conf  held NETWORK_NAME=Seattle5 and GATEWAY_IP=10.250.250.1 -
    #                  another node's pond identity, generated by setup_lillypad
    #   pond_bootstrap_done  is a SENTINEL. Shipping it makes a freshly installed
    #                  node believe deferred tunnel setup already ran, so
    #                  runMerge never calls frognet-pond-bootstrap.sh. A zero-byte
    #                  file that silently disables a boot step.
    #   active_interface / interfaces_override.conf  are AUTO-GENERATED from the
    #                  build host's NICs; D3 writes them for the target.
    'etc/frognet/gateways.conf'
    'etc/frognet/active_interface'
    'etc/frognet/interfaces_override.conf'
    'etc/frognet/pond_bootstrap_done'
    'etc/frognet/semantic_hosts'
    'etc/frognet/simulator_underlay'
    'etc/frognet/transit_exclude_interfaces'

    # Same class, different directory: the dnsmasq forwarder map is written by the
    # installer / frognet_fixup / setup_lillypad from whatever ponds THIS node has
    # seen. The shipping candidate carried server=/BABox/10.155.155.1 and
    # server=/Seattle5/10.250.250.1 - a map of somebody's private networks, and one
    # a new node would answer DNS from before it had discovered anything. Its own
    # first line says AUTO-GENERATED.
    'etc/dnsmasq.d/frognet_forwarders_auto.conf'
    'etc/dnsmasq.d/frognet_databasehost.conf'

    # systemd enable-state. `systemctl enable` writes these; the installer's F1
    # recreates them for the node it is installing.
    'etc/systemd/system/*.target.wants'
    'etc/systemd/system/*.target.wants/*'
    'etc/apache2/sites-enabled'
    'etc/apache2/mods-enabled'
    'etc/apache2/conf-enabled'

    # Communicator per-user state, written by communicator_app.py. The candidate
    # carried {"host": "10.250.250.1", "name": "Gorp", "id": "gorp",
    # "pref_camera": "/dev/video0", "pref_mic": "plughw:2,0"} - a person's display
    # name and their camera and microphone. Not source.
    'etc/frognet_bundles/communicator/shell.json'

    # Shared secrets.
    'etc/frognet/sem_cache_secret'
    'etc/frognet/*secret*'
    'etc/frognet/*.pem'

    # TLS. The directory ships for its structure; the material does not.
    'etc/ssl/private'
    'etc/ssl/*.key'
    'etc/ssl/*.pem'

    # Timestamped runtime backups of any of the above. Suffix-anchored ON
    # PURPOSE: a blanket '*backup*' would delete
    # /usr/local/bin/frognet_backup_world.bash, which is a real script.
    '*.lanonly-removed.*'
    '*.conf.20*'
    '*.bak.20*'

    # NOT here: var/www/html/config.php and opt/frognet_semantic/DB_CONFIG.json.
    # Excluding them was wrong - config.php is 100 lines of the FrogNetDb mysqli
    # wrapper and ONE line of password, so cutting the file to hide the string
    # threw out working code and left a --from-repo node with no config.php at
    # all. They ship TOKENIZED instead: the password is replaced with
    # __FROGNET_DB_PASS__, which is the placeholder phase C1b already injects into
    # (and C1b dies if any token survives). See the tokenize step in
    # full_tar.bash. [SHIP_THE_CODE_TOKENIZE_THE_SECRET_V1]
)

# -----------------------------------------------------------------------------
# frognet_check_manifest <root>
#
# [RELEASE_MANIFEST_FAILFAST_V1] The manifest IS the contract. A path listed here
# but absent on the build host means the build host is broken or the manifest is
# stale - either way the release must not ship with a silent hole, because a hole
# is invisible in a tarball listing and fatal on the node that unpacks it.
#
# Returns 0 if every path is present, 1 otherwise, naming each miss.
# -----------------------------------------------------------------------------
frognet_check_manifest() {
    local _root="${1:-/}"
    local _missing=()
    local _p
    for _p in "${FROGNET_WORLD_PATHS[@]}"; do
        [ -e "${_root%/}/$_p" ] || _missing+=( "$_p" )
    done
    if [ "${#_missing[@]}" -gt 0 ]; then
        echo "ERROR: refusing to build an incomplete world - ${#_missing[@]} manifest path(s) absent under ${_root}:" >&2
        for _p in "${_missing[@]}"; do echo "         $_p" >&2; done
        echo "       Either this build host is not a complete node, or the manifest is stale." >&2
        echo "       Do NOT 'fix' this by removing the path from the manifest." >&2
        return 1
    fi
    return 0
}
