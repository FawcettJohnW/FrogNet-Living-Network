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
# --exclude='*.tgz', which took source that ships as a bundle, and
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
    # usr/local/lib/frognet/ holds conf.sh, which frognet_prune_removed.sh's T3
    # section (installer phase C1c) sources. It was once never packaged: the list
    # named frognet_trace.sh and frognet_log.sh individually and stopped there, so
    # install phase C1c died with "FATAL: /usr/local/lib/frognet/conf.sh not found"
    # (2026-08-29). Anything else matching frognet* is picked up by the
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
    etc/apache2/sites-available/databasehost.conf
    etc/apache2/sites-available/databasehost-ssl.conf
    etc/apache2/sites-available/default-ssl.conf
    etc/apache2/sites-available/frognet-ssl.conf

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
    etc/NetworkManager/dispatcher.d/90-frognet-merge
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

    # Web files. (var/www/www_admin went in T5 with admin-site.conf: its vhost was on *:80, which Apache does not
    # listen on, and its two pages wrote Commands/netCommand.* files nothing reads.)
    var/www/html

    # [ONE_MONITOR_TRUTH_V1 - T5] www-data may read the kernel's WireGuard handshake times (getSnapshot.php's hub link
    # age). The installer (D5) and the FD&F .run set it 0440 and check it with visudo.
    etc/sudoers.d/frognet-snapshot

    # Python source. agent_workload/ -- ddpbench.py, the tuplespace collectives
    # and the DDP hooks -- is inside this and ships with it; it is deliberately
    # NOT listed separately, because a second entry under a parent already in
    # this list puts every one of its files into the archive twice.
    opt/frognet_semantic

    # [THE_HARNESS_IS_PART_OF_THE_WORLD_V1 - John 2026-09-15] The AI benchmark
    # harness: gate.py, hookrun.sh, netbench1.py, store_admin.py. It has never
    # been in this manifest, so no world tarball has ever carried it and it has
    # had to be moved by hand. The campaign results are only reproducible with
    # the code that produced them.
    usr/local/frognet-ai

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
# [THIRD_PARTY_IS_NOT_OURS_V1 - John 2026-09-14] Code we ship but did not
# write. It must NOT carry the Fawcett Innovations GPL-2.0-only notice --
# stamping our grant onto someone else's work misstates its licence, and the
# applier would do exactly that if these were merely "missing a header".
#
# license_check excludes these from the missing-notice walk AND from the
# Apache-2.0 check: Apache code inside a declared third-party tree is a
# vendored dependency with its own terms, not our grant contradicting itself.
# Its error message already told us to do this ("add it to THIRD_PARTY and
# reopen the licensing question") -- the array simply never existed, so the
# oracle failed on 341 files and stayed red long enough to be called known.
#
# Adding a path here is a licensing decision, not a way to quiet the check.
# GPL-2.0-only is incompatible with Apache-2.0, so anything listed here that
# is Apache-licensed still has to be resolved before distribution -- the two
# PHPMailer doc bundles below are the live example.
FROGNET_THIRD_PARTY=(
    # Dino Farinacci's lispers.net, Apache-2.0, vendored unmodified as the frozen oracle the lispers example is tested
    # against. It carries its own LICENSE and NOTICE; ours must never be stamped on it. Not shipped: see NEVER_SHIP.
    opt/frognet_semantic/ribbit/examples/lispers.net/third_party
    # [GPS_LEAFLET_LOCAL_V1] Leaflet 1.9.4 (BSD-2-Clause) and pointhi/leaflet-color-markers (BSD-2-Clause), unmodified,
    # each with its LICENSE, so gps_dashboard.html works on a network with no internet. Shipped with var/www/html.
    var/www/html/vendor/leaflet
)

FROGNET_NEVER_SHIP=(
    # [NODE_LOCAL_NEVER_SHIPS_V1 - 2026-10-02] On a node, used there, never FrogNet source: pip's console-script
    # launchers (pip writes them on install), the Redis front binary the Redis build installs for that machine's
    # architecture and a third-party service unit. full_tar from Seattle5 is the snapshot
    # that becomes every node and the repository, so these are kept out of it here rather than deleted from the node.
    usr/local/bin/bottle
    usr/local/bin/bottle.py
    usr/local/bin/cbor2
    usr/local/bin/cheroot
    usr/local/bin/dotenv
    usr/local/bin/f2py
    usr/local/bin/flask
    usr/local/bin/futurize
    usr/local/bin/gpsdclient
    usr/local/bin/gunicorn
    usr/local/bin/gunicornc
    usr/local/bin/httpx
    usr/local/bin/isympy
    usr/local/bin/markdown-it
    usr/local/bin/numpy-config
    usr/local/bin/pasteurize
    usr/local/bin/proton
    usr/local/bin/proton-viewer
    usr/local/bin/pyav
    usr/local/bin/torchfrtrace
    usr/local/bin/torchrun
    usr/local/bin/ribbit-redis-front
    etc/systemd/system/uptime-kuma.service
    # [TUNE_IS_NODE_STATE_V1 - 2026-10-03] Written at every boot by frognet-system-tune.sh from the board's own RAM and
    # CPU count (innodb_buffer_pool_size, sysctl, service limits): each node's are its own, and a shipped copy would
    # carry Seattle5's sizing onto a smaller board until the next boot.
    etc/mysql/mariadb.conf.d/99-frognet-tune.cnf
    etc/sysctl.d/99-frognet.conf
    'etc/systemd/system/*.service.d/99-frognet-tune.conf'
    # [NODE_IDENTITY_NEVER_SHIPS_V1 - 2026-10-01] Files every node generates for itself. A tree built on one node
    # carried them, and laying it over another node made that node Seattle5: dnsmasq served Seattle5's domain and
    # DHCP range, and `frognet_install.sh --preserve` then "recovered" the name Seattle5 from opts_only.conf.
    # Writers: frognet_install.sh, set_node_id.bash, frognet-netstart, frognet_nm_unmanage_if.sh.
    etc/dnsmasq.d/opts_only.conf
    etc/frognet/node-id
    etc/frognet/ssid_projection.conf
    'etc/NetworkManager/conf.d/*'
    'etc/iptables/rules.v*'
    etc/ssl/localCA
    # The machine's own OS state, not FrogNet: its CA links, openssl.cnf, and the systemd symlinks that enable or
    # mask services on that one machine (hostapd, display-manager, default.target, ...).
    etc/ssl/certs
    etc/ssl/openssl.cnf
    # A node's systemd aliases and masks (symlinks; full_tar does not skip links, which is how one node's copies reached
    # the repository). None of these is a FrogNet unit -- those are frognet-*. A mask shipped as hostapd.service turns
    # off another node's SSID; default.target and display-manager change how it boots.
    'etc/systemd/system/dbus-*'
    'etc/systemd/system/*.device.wants'
    'etc/systemd/system/*.service.wants'
    etc/systemd/system/default.target
    etc/systemd/system/display-manager.service
    etc/systemd/system/hostapd.service
    etc/systemd/system/sshd.service
    etc/systemd/system/chronyd.service
    etc/systemd/system/iptables.service
    etc/systemd/system/ip6tables.service
    etc/systemd/system/jool.service
    etc/systemd/system/systemd-resolved.service
    # [DO_NOT_SHIP_AN_INCOMPATIBLE_LICENCE_V1 - John 2026-09-14] PHPMailer's
    # generated phpdoc bundle carries Apache-2.0 JavaScript (bootstrap.js,
    # prettify/lang-clj.js). Apache-2.0 is incompatible with GPL-2.0-only, so
    # distributing it inside this world is a licensing problem that
    # FROGNET_THIRD_PARTY only makes the CHECKER stop reporting.
    #
    # It is documentation. Nothing at runtime reads it. Not shipping it
    # resolves the incompatibility instead of annotating it -- the library
    # itself stays, under its own licence, in THIRD_PARTY above.
    var/www/html/php/php/PHPMailer/docs
    # Same rule for Dino's lispers.net (Apache-2.0): the oracle the lispers example is tested against, never run on a
    # node. Not shipping it resolves the incompatibility; it stays in the repository under its own licence.
    opt/frognet_semantic/ribbit/examples/lispers.net/third_party
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

    # This node's region (RINA_RAM / RINA_AREA, when it has one) and its WiFi
    # passphrase. Each node's own.
    'etc/frognet/tunnel.conf'
    'etc/frognet/wifi.env'

    # Per-node RUNTIME STATE under etc/frognet. The DIRECTORY ships (the installer
    # and conf.sh expect it); this generated content does not. Found 2026-08-31 in
    # a build candidate:
    #   gateways.conf  held NETWORK_NAME=Seattle5 and GATEWAY_IP=10.250.250.1 -
    #                  another node's pond identity, generated by setup_lillypad
    #   active_interface / interfaces_override.conf  are AUTO-GENERATED from the
    #                  build host's NICs; D3 writes them for the target.
    'etc/frognet/gateways.conf'
    'etc/frognet/active_interface'
    'etc/frognet/interfaces_override.conf'
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
    'opt/frognet_semantic/ribbit/examples/communicator/shell.json'

    # Shared secrets.
    'etc/frognet/sem_cache_secret'
    'etc/frognet/*secret*'
    'etc/frognet/*.pem'

    # TLS. The directory ships for its structure; the material does not.
    # [EACH_NODE_MAKES_ITS_CERT_V1 - T4a] Nor the certificate: a certificate whose key does not ship is of no use to
    # the node that gets it (Apache will not start on a cert without its key), so the installer's E5 makes the pair
    # on every node that has no key, and nothing ships either half.
    'etc/ssl/private'
    'etc/ssl/*.key'
    'etc/ssl/*.pem'
    'etc/ssl/*.crt'
    'etc/ssl/*.csr'

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
# [NODE_FILE_SET_V1 - T4a] What a NODE is made of is FROGNET_WORLD_PATHS minus FROGNET_NEVER_SHIP minus this list: things
# the tree (and a build host) carries that no node runs. frognet_node_files below applies all three, and both ways a
# node is laid down use it: frognet_install.sh --from-repo copies exactly its output, and frognet_build_release.sh
# builds the world tar from the same paths with the same excludes. full_tar.bash (the repository itself) does NOT
# apply this list: the repository keeps the book and the retired names' history; a node does not get them.
# The patterns are tar --exclude patterns (matched against the path relative to /; '*' spans '/').
# -----------------------------------------------------------------------------
FROGNET_NODE_EXCLUDE=(
    # The book and its build (Magnum Croakus): read, not run.
    opt/frognet_semantic/docs
    # [HAM_OUT_V1] the amateur/AX.25 subsystem does not ship (FCC Part 97.113(a)(4)); frognet_radio_init.sh is its
    # orphan ham0 driver.
    'usr/local/bin/ham_*.sh'
    usr/local/bin/frognet_radio_init.sh
    # [RETIRED_VERSIONS_V1] only setup_lillypad_v4.bash runs; a build host that still has older ones must not ship them.
    usr/local/bin/setup_lillypad.bash
    usr/local/bin/setup_lillypad_v3.bash
    opt/frognet_semantic/internet_tunnels_v3/setup_lillypad.bash
    opt/frognet_semantic/internet_tunnels_v3/install/setup_lillypad_v3.bash
    usr/local/bin/frognet-tunnel-daemon.py
    # [NO_STAGING_DIRS_UNDER_OPT_V1] stray staging mirrors and the runtime dirs' contents.
    opt/frognet_semantic/etc
    opt/frognet_semantic/bin
    opt/frognet_semantic/usr
    opt/frognet_semantic/var
    'opt/frognet_semantic/blob_cache/*'
    'opt/frognet_semantic/logs/*'
    # [SHIP_NO_BYTECODE] (a top-level __pycache__ is not matched by '**/').
    '*/__pycache__'
    '*.pyc'
    'opt/frognet_semantic/*.old'
    opt/frognet_semantic/_v1
    # [DISTRO_FILES_ARE_NOT_OURS_V1 - 2026-10-14] Debian's apache2 default page: the distribution's file on every
    # node (000-default.conf serves index.php), not FrogNet's. A release built on a node picked it up from
    # /var/www/html, recorded it as FrogNet's, and test_license_oracle failed on it.
    var/www/html/index.html
    # Per-node state a build host has and a node makes for itself.
    etc/NetworkManager/system-connections
    etc/dnsmasq.d/forward_to_unbound.conf
    etc/frognet/db.env
    'etc/wireguard/*.conf'
    'etc/frognet/tunnel.conf.bak.*'
)

# frognet_node_files ROOT -- print the node file set under ROOT, one path per line, relative to ROOT, files and
# symlinks only (no directory entries: laying them down must not reset the mode or owner of /etc, /usr/local/bin or any
# other existing directory). ROOT is / on a build host, the checkout on --from-repo. The excludes are applied by GNU
# tar itself (it lists without reading file data when the archive is /dev/null), so they mean exactly what they mean in
# frognet_build_release.sh's world tar. A WORLD_PATHS entry absent under ROOT is reported by frognet_check_manifest.
frognet_node_files() {
    local _root="${1:?frognet_node_files ROOT}" _p _x
    local _paths=() _ex=()
    for _p in "${FROGNET_WORLD_PATHS[@]}"; do [ -e "${_root%/}/$_p" ] && _paths+=( "$_p" ); done
    for _p in "${_root%/}"/usr/local/lib/frognet*; do           # [LIB_MANIFEST_AUTODISCOVER_V1], under ROOT
        [ -e "$_p" ] || continue; _p="${_p#"${_root%/}"/}"
        case " ${_paths[*]} " in *" $_p "*) ;; *) _paths+=( "$_p" ) ;; esac
    done
    for _x in "${FROGNET_NEVER_SHIP[@]}" "${FROGNET_NODE_EXCLUDE[@]}"; do _ex+=( "--exclude=$_x" ); done
    tar -C "$_root" "${_ex[@]}" -cvf /dev/null "${_paths[@]}" | grep -v '/$'
}

# -----------------------------------------------------------------------------
# frognet_check_manifest <root>
#
# [REPORT_THE_HOLE_DO_NOT_REFUSE_THE_TAR_V1 - John 2026-09-15]
#
# This REPORTS. It does not refuse, and it always returns 0.
#
# It used to return 1 and its callers exited on it, so three absent paths --
# etc/apache2/sites-available/000-default.conf, default-ssl.conf,
# etc/setup_iptables -- meant AI-Host could not tar itself up at all. A node
# that is legitimately not an apache host is not a broken build host, and no
# tool of ours gets to decide that the operator may not have a tarball.
#
# The original reasoning was that a hole is invisible in a tarball listing.
# The answer to invisible is to PRINT IT, which is what this does, on stderr,
# naming every absent path. The answer is not to withhold the artifact.
#
# Returns 0 always. A caller that wants the count can read FROGNET_MANIFEST_MISSING.
# -----------------------------------------------------------------------------
frognet_check_manifest() {
    local _root="${1:-/}"
    local _missing=()
    local _p
    for _p in "${FROGNET_WORLD_PATHS[@]}"; do
        [ -e "${_root%/}/$_p" ] || _missing+=( "$_p" )
    done
    FROGNET_MANIFEST_MISSING="${#_missing[@]}"
    if [ "${#_missing[@]}" -gt 0 ]; then
        echo "NOTE: ${#_missing[@]} manifest path(s) absent under ${_root} and will not be in the tarball:" >&2
        for _p in "${_missing[@]}"; do echo "         $_p" >&2; done
        echo "       This host is missing them - it may simply not serve that role." >&2
        echo "       Building anyway. Unpacking this tarball will not create them." >&2
    fi
    return 0
}
