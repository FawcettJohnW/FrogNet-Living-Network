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
# Post-install for frognet-mergechurn-20260912.tar.gz
#
# Run as root, on every node, AFTER untarring the payload at /.
# The tarball only writes files. Everything that has to be REMOVED, edited
# in place, or restarted is here, because a tar cannot do any of it.
#
# Does NOT run a merge. The next one will happen on its own.
set -u

log() { echo "[postinstall] $*"; }

# ---------------------------------------------------------------------------
# 1. Remove the legacy tunnel daemon.
#
# Superseded by frognet-tunnel-daemon-v3. It hardcoded 10.101.* for identity
# detection, so it could not start on any current node anyway. The bare-name
# unit never shipped -- only a .service.d drop-in dir for it -- which is what
# let frognet-pond-bootstrap.sh "start" a unit that does not exist, fail
# is-active, never write its sentinel, and retry every merge and every 120s
# timer tick forever.
# ---------------------------------------------------------------------------
log "removing the legacy tunnel daemon"
systemctl disable --now frognet-tunnel-daemon.service 2>/dev/null || true
rm -f  /usr/local/bin/frognet-tunnel-daemon.py
rm -f  /usr/local/bin/frognet-tunnel-daemon
rm -f  /etc/systemd/system/frognet-tunnel-daemon.service
rm -rf /etc/systemd/system/frognet-tunnel-daemon.service.d
rm -f  /etc/systemd/system/multi-user.target.wants/frognet-tunnel-daemon.service

# ---------------------------------------------------------------------------
# 2. Remove deprecated and dead files.
# ---------------------------------------------------------------------------
log "removing deprecated / dead files"
rm -f /usr/local/bin/setup_lillypad.bash
rm -f /usr/local/bin/setup_lillypad_v3.bash
rm -f /opt/frognet_semantic/internet_tunnels_v3/setup_lillypad.bash
rm -f /opt/frognet_semantic/internet_tunnels_v3/install/setup_lillypad_v3.bash
rm -f /usr/local/bin/ham_concentrator_up.sh
rm -f /usr/local/sbin/dnsmasq_merge_trigger.sh
rm -f /etc/dnsmasq.d/forward_to_unbound.conf

# ---------------------------------------------------------------------------
# 3. Stop any leftover debug watcher.
#
# frognet_netdrop_watch.sh is a hand-launched install-time harness: `while :`
# with sleep 0.3, forking date + two ip + nmcli + ls + four greps per pass,
# roughly 37 processes a second, plus a journalctl -f and a tail -F. Nothing
# starts it and nothing stops it. Measured ~62 new kernel tasks/sec on a node
# where one had been left running.
# ---------------------------------------------------------------------------
if pgrep -f frognet_netdrop_watch >/dev/null 2>&1; then
    log "stopping a leftover frognet_netdrop_watch.sh"
    pkill -f frognet_netdrop_watch || true
    for P in /tmp/frognet_netdrop_nm.pid /tmp/frognet_netdrop_merge.pid; do
        [[ -f "$P" ]] && { kill "$(cat "$P")" 2>/dev/null || true; rm -f "$P"; }
    done
fi

# ---------------------------------------------------------------------------
# 4. Per-node dnsmasq DHCP scoping.
#
# opts_only.conf is NOT in the tarball: it carries this node's domain= and
# dhcp-range= and shipping one node's copy to the fleet would repoint every
# node's identity. The generator (setup_lillypad_v4.bash) is fixed for future
# writes; this patches the file already on disk.
#
# no-dhcp-interface is a blacklist, and it was built by enumerating the
# interfaces that existed when setup ran. Tunnels are created later, so a wg
# iface added afterwards gets DHCP served across it -- handing addresses out of
# THIS node's LAN pool to hosts on the far side of the mesh.
# ---------------------------------------------------------------------------
OPTS=/etc/dnsmasq.d/opts_only.conf
if [[ -f "$OPTS" ]]; then
    SERVED_IF="$(awk -F= '/^dhcp-range=/{split($2,a,","); print a[1]; exit}' "$OPTS" \
                 | xargs -r -I{} sh -c 'ip -4 -o addr show | awk -v p="$(echo {} | cut -d. -f1-3)." "\$4 ~ p {print \$2; exit}"')"
    ADDED=0
    {
        for N in $(seq 0 31); do echo "wg$N"; done
        echo "frognet0"
    } | while read -r IF; do
        [[ -n "${SERVED_IF:-}" && "$IF" == "$SERVED_IF" ]] && continue
        grep -qx "no-dhcp-interface=$IF" "$OPTS" && continue
        echo "no-dhcp-interface=$IF" >> "$OPTS"
        ADDED=1
    done
    log "dnsmasq: DHCP exclusions extended to wg0-wg31 + frognet0 in $OPTS"
else
    log "WARNING: $OPTS not found - DHCP scoping not patched on this node"
fi


# ---------------------------------------------------------------------------
# 4b. Verify the DB credential, do not repair it.
#
# [ONE_CREDENTIAL_ONE_SOURCE_V1] The FrogUser password used to live in five
# files; three of them were Python modules carrying it as a module-level
# default, and two of those shipped a real working pond password. Those three
# now read /opt/frognet_semantic/DB_CONFIG.json through core/db_credentials and
# hold no secret at all.
#
# This script does NOT write a password. It cannot know one, and guessing is how
# the problem started. It only says whether the one remaining source is usable,
# because the alternative symptom is an auth log filling with
#   Access denied for user 'FrogUser'@'localhost' (using password: YES)
# which names the wrong problem.
# ---------------------------------------------------------------------------
CFG=/opt/frognet_semantic/DB_CONFIG.json
if [[ ! -f "$CFG" ]]; then
    log "WARNING: $CFG missing. It is now the ONLY source of the FrogUser"
    log "  credential. Nothing on this node can reach MySQL until it exists."
elif grep -q '__FROGNET_DB_PASS__' "$CFG"; then
    log "WARNING: $CFG still holds the build placeholder. The installer's"
    log "  secret injection never ran here. Re-run frognet_install.sh --db-pass,"
    log "  or write the pond password into that file."
elif grep -q '__FROGNET_DB_PASS__' /var/www/html/config.php 2>/dev/null; then
    log "WARNING: /var/www/html/config.php still holds the build placeholder"
    log "  while $CFG does not. api.php will be refused by MySQL."
else
    # [VERIFY_THE_CREDENTIAL_WORKS_V1] "not the placeholder" is not the same as
    # "correct". Seattle/BAMacBook 2026-09-12: DB_CONFIG.json held a real-looking
    # password that MySQL refused, while config.php held one that worked -- so
    # api.php was fine and the Python readers were denied, in the same second.
    # A shape check would have passed. Connect and find out.
    _pw="$(python3 - "$CFG" <<'PYEOF'
import json, sys
print(json.load(open(sys.argv[1])).get("password", ""))
PYEOF
)"
    if [[ -z "$_pw" ]]; then
        log "WARNING: could not read a password out of $CFG"
    elif MYSQL_PWD="$_pw" mysql -u FrogUser -h 127.0.0.1 -D FrogNet \
            -e 'SELECT 1' >/dev/null 2>&1; then
        log "DB credential: $CFG verified against MySQL"
    else
        log "FATAL-ISH: $CFG has a password MySQL REFUSES."
        log "  Every reader now takes its credential from this one file, so this"
        log "  stops the proxy and the daemon rather than degrading a subset of"
        log "  them. Fix it before restarting:"
        log "    mysql -u FrogUser -p      # confirm the password you expect works"
        log "    vi $CFG                   # put that password here"
        log "  Then: systemctl restart frognet-proxy frognet-daemon"
    fi
    # [MYSQLI_THROWS_SINCE_PHP81_V1] api.php now REPORTS a bad credential instead
    # of dying on an uncaught mysqli_sql_exception. Confirm it answers at all --
    # before this fix the symptom was a bare 500 with an empty body and nothing
    # in the response to say the password was wrong.
    if command -v curl >/dev/null 2>&1; then
        _code="$(curl -s -o /tmp/.fn_api_probe -w '%{http_code}' --max-time 20 \
            'http://127.0.0.1:8080/api.php?entity=sensors&action=values&SensorType=discovery&limit=1' \
            2>/dev/null || echo 000)"
        if [[ "$_code" == "200" ]]; then
            log "api.php: answers 200 on the local Apache"
        else
            log "WARNING: api.php returned HTTP $_code on 127.0.0.1:8080"
            [[ -s /tmp/.fn_api_probe ]] && log "  body: $(head -c 300 /tmp/.fn_api_probe)"
            log "  A 500 naming 'DB connect failed' means config.php's password is"
            log "  wrong. Fix it with:  frognet_db_credential_sync.sh"
            log "  A 500 with an EMPTY body means this fix did not land."
        fi
        rm -f /tmp/.fn_api_probe
    fi

    # config.php must agree, or api.php is denied while Python succeeds.
    _php="$(grep -oP "define\('DB_PASS',\s*'\K[^']+" /var/www/html/config.php 2>/dev/null || true)"
    if [[ -n "$_php" && -n "$_pw" && "$_php" != "$_pw" ]]; then
        log "WARNING: /var/www/html/config.php and $CFG hold DIFFERENT passwords."
        log "  That is how one node logs Access-denied for some processes and"
        log "  serves fine for others, in the same second."
    fi
    unset _pw _php
fi

# ---------------------------------------------------------------------------
# 5. Stale bytecode.
#
# discovery/, core/ and frogsim/ all changed. runMerge purges __pycache__ every
# pass, but the proxy and daemon are long-lived and import core/ directly.
# ---------------------------------------------------------------------------
log "purging stale bytecode"
find /opt/frognet_semantic /usr/local/bin /etc/frognet_bundles \
     -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
find /opt/frognet_semantic /usr/local/bin /etc/frognet_bundles \
     -type f -name '*.pyc' -delete 2>/dev/null

# ---------------------------------------------------------------------------
# 6. Reload and restart what actually needs it.
#
#   systemd      - dnsmasq.service.d/override.conf changed ([Unit] After= was
#                  split across two lines; systemd was logging "Missing '='"
#                  on every reload and the dependency did not exist).
#   dnsmasq      - /etc/dnsmasq.d/upstream_fallback.conf changed. SIGHUP does
#                  NOT re-read the conf-dir, so this is the one case that
#                  genuinely needs a restart. One deliberate bounce, here.
#   proxy/daemon - both import core/frognet_tuples.py, which changed. They are
#                  long-lived; without a restart they keep running the old
#                  merge-trigger logic.
#
# Nothing else. discovery/ is re-execed by every merge. The NM dispatcher is
# re-read per event.
# ---------------------------------------------------------------------------
log "systemctl daemon-reload"
systemctl daemon-reload

log "restarting dnsmasq (conf.d changed; SIGHUP cannot pick it up)"
systemctl restart dnsmasq 2>/dev/null || true

log "restarting frognet-proxy and frognet-daemon (core/frognet_tuples.py changed)"
systemctl restart frognet-proxy 2>/dev/null || true
systemctl restart frognet-daemon 2>/dev/null || true

# ---------------------------------------------------------------------------
# 7. Report anything that still looks wrong, without changing it.
# ---------------------------------------------------------------------------
echo
log "--- state worth a look, not changed by this script ---"
if [[ -f /etc/frognet/pond_bootstrap_done ]]; then
    log "  pond_bootstrap_done: present (runMerge will not call the bootstrap)"
else
    log "  pond_bootstrap_done: MISSING - the bootstrap still runs every merge"
    log "    it no longer restarts the tunnel daemon, and it now writes the"
    log "    sentinel once enrolment completes, so this should clear itself"
    log "    on the next pass. If it does not: journalctl -u frognet-pond-bootstrap"
fi
if grep -q '^no-resolv' /etc/dnsmasq.d/*.conf 2>/dev/null; then
    log "  WARNING: no-resolv is still set somewhere in /etc/dnsmasq.d -"
    log "    resolv-file is being ignored and external DNS is not going to the"
    log "    next hop. grep -l '^no-resolv' /etc/dnsmasq.d/*.conf"
fi
if [[ -n "${FROGNET_RESTART_NETWORK:-}" ]] || grep -qs FROGNET_RESTART_NETWORK /etc/frognet/transit.conf; then
    log "  NOTE: FROGNET_RESTART_NETWORK is set. The block it drove is gone from"
    log "    frognet_up_clean.sh; the variable is now inert. Safe to remove."
fi
echo
log "done. No merge was run. Merge triggers now in effect:"
log "  NM interface event   - only when the routing table actually changed"
log "  store unreachable    - 3 consecutive failures, max 1 per 600s per node"
log "  store timeout        - no longer triggers at all (StoreSlow)"
log "  DHCP lease add/del   - now fires on the whole 10/8 plane (was 10.10x only)"
exit 0
