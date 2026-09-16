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
# frognet_db_credential_sync.sh
#
# Put ONE FrogUser password into every file that carries it, after proving that
# password actually works.
#
# [ONE_CREDENTIAL_ONE_SOURCE_V1] Two files carry the secret now:
#     /var/www/html/config.php                  (api.php -> MySQL)
#     /opt/frognet_semantic/DB_CONFIG.json      (every Python reader)
# They are separate because one is PHP and one is JSON, not because they are
# allowed to differ. When they differ, api.php is refused while Python succeeds
# -- in the same second, on the same box, which is what an interleaved accept
# and refuse in the MariaDB log looks like.
#
# This script does NOT guess and does NOT write anything it has not verified.
#
#   sudo frognet_db_credential_sync.sh                 # prompt, verify, write
#   sudo frognet_db_credential_sync.sh --check         # report only, no writes
#   sudo frognet_db_credential_sync.sh --set-mysql     # also ALTER USER to match
#
set -u

CFG_PHP=/var/www/html/config.php
CFG_JSON=/opt/frognet_semantic/DB_CONFIG.json
DB_USER=FrogUser
DB_NAME=FrogNet

CHECK_ONLY=0
SET_MYSQL=0
for a in "$@"; do
    case "$a" in
        --check)      CHECK_ONLY=1 ;;
        --set-mysql)  SET_MYSQL=1 ;;
        -h|--help)    sed -n '18,40p' "$0"; exit 0 ;;
        *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done

say() { echo "[db-cred] $*"; }

# ---------------------------------------------------------------------------
# 1. What each file currently holds.
# ---------------------------------------------------------------------------
php_pass()  { grep -oP "define\('DB_PASS',\s*'\K([^'\\\\]|\\\\.)*" "$CFG_PHP" 2>/dev/null | head -1; }
json_pass() { python3 -c "import json,sys; print(json.load(open('$CFG_JSON')).get('password',''))" 2>/dev/null; }

P_PHP="$(php_pass)"
P_JSON="$(json_pass)"

say "config.php:      $( [[ -n "$P_PHP"  ]] && echo "set (${#P_PHP} chars)"  || echo MISSING )"
say "DB_CONFIG.json:  $( [[ -n "$P_JSON" ]] && echo "set (${#P_JSON} chars)" || echo MISSING )"
if [[ -n "$P_PHP" && -n "$P_JSON" ]]; then
    if [[ "$P_PHP" == "$P_JSON" ]]; then
        say "the two files AGREE"
    else
        say "the two files DISAGREE -- this is the accept-and-refuse-in-the-same-second bug"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Which accounts exist. Two FrogUser rows with different passwords is a real
#    way to get 'Access denied' from one client and success from another.
# ---------------------------------------------------------------------------
if mysql -N -B -e 'SELECT 1' >/dev/null 2>&1; then
    say "FrogUser accounts MySQL knows about:"
    mysql -N -B -e "SELECT CONCAT('  ', user, '@', host) FROM mysql.user WHERE user='$DB_USER'" \
        2>/dev/null | sed 's/^/[db-cred] /'
    _n="$(mysql -N -B -e "SELECT COUNT(*) FROM mysql.user WHERE user='$DB_USER'" 2>/dev/null)"
    if [[ "${_n:-0}" -gt 1 ]]; then
        say "  NOTE: more than one $DB_USER account. MySQL picks the most specific"
        say "        host match, so two rows with different passwords means some"
        say "        clients authenticate and others do not."
    fi
else
    say "cannot read mysql.user as this shell's user; skipping the account list"
fi

# ---------------------------------------------------------------------------
# 3. Test each candidate password against the real server.
# ---------------------------------------------------------------------------
try_pass() {   # $1 = password; 0 if it authenticates
    [[ -n "$1" ]] || return 1
    MYSQL_PWD="$1" mysql -u "$DB_USER" -h 127.0.0.1 -D "$DB_NAME" \
        -e 'SELECT 1' >/dev/null 2>&1
}

WORKING=""
if try_pass "$P_PHP";  then WORKING="$P_PHP";  say "config.php's password WORKS"; else
    [[ -n "$P_PHP"  ]] && say "config.php's password is REFUSED by MySQL"; fi
if [[ -z "$WORKING" ]] && try_pass "$P_JSON"; then WORKING="$P_JSON"; say "DB_CONFIG.json's password WORKS"; else
    [[ -z "$WORKING" && -n "$P_JSON" ]] && say "DB_CONFIG.json's password is REFUSED by MySQL"; fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    [[ -n "$WORKING" ]] && say "check only: a working password is on disk" \
                        || say "check only: NEITHER file holds a working password"
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. Get a password that works, then write it everywhere.
# ---------------------------------------------------------------------------
if [[ -z "$WORKING" ]]; then
    say "Neither file authenticates. Enter the FrogUser password MySQL holds"
    say "(the one that works with: mysql -u $DB_USER -p)"
    read -r -s -p "[db-cred] password: " P1; echo
    read -r -s -p "[db-cred] again:    " P2; echo
    [[ "$P1" == "$P2" ]] || { say "FATAL: the two entries differ"; exit 1; }
    [[ -n "$P1" ]]       || { say "FATAL: empty password"; exit 1; }
    if try_pass "$P1"; then
        WORKING="$P1"
        say "verified against MySQL"
    elif [[ "$SET_MYSQL" -eq 1 ]]; then
        say "MySQL refuses it; --set-mysql given, so setting the account to match"
        mysql -e "ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$(printf '%s' "$P1" | sed "s/'/''/g")'; FLUSH PRIVILEGES;" \
            || { say "FATAL: ALTER USER failed"; exit 1; }
        try_pass "$P1" || { say "FATAL: still refused after ALTER USER"; exit 1; }
        WORKING="$P1"
        say "MySQL updated and verified"
    else
        say "FATAL: MySQL refuses that password and --set-mysql was not given."
        say "  Nothing written. Re-run with --set-mysql to change the account"
        say "  instead, or find the password the account actually holds."
        exit 1
    fi
    unset P1 P2
fi

write_php() {
    python3 - "$CFG_PHP" <<'PY'
import os, re, sys
path, secret = sys.argv[1], os.environ["FN_SECRET"]
src = open(path, encoding="utf-8", errors="surrogateescape").read()
lit = secret.replace("\\", "\\\\").replace("'", "\\'")
out, n = re.subn(r"(define\(\s*'DB_PASS'\s*,\s*')(?:[^'\\]|\\.)*('\s*\))",
                 lambda m: m.group(1) + lit + m.group(2), src, count=1)
if n != 1:
    sys.exit("no DB_PASS define found in %s" % path)
tmp = path + ".new"
open(tmp, "w", encoding="utf-8", errors="surrogateescape").write(out)
os.replace(tmp, path)
PY
}

write_json() {
    python3 - "$CFG_JSON" <<'PY'
import json, os, sys
path, secret = sys.argv[1], os.environ["FN_SECRET"]
cfg = json.load(open(path))
cfg["password"] = secret
tmp = path + ".new"
json.dump(cfg, open(tmp, "w"), indent=2)
open(tmp, "a").write("\n")
os.replace(tmp, path)
PY
}

# Secret by ENV, never argv: argv is world-readable through ps.
FN_SECRET="$WORKING" write_php  || { say "FATAL: writing $CFG_PHP failed";  exit 1; }
FN_SECRET="$WORKING" write_json || { say "FATAL: writing $CFG_JSON failed"; exit 1; }
chown root:www-data "$CFG_PHP" 2>/dev/null || true
chmod 640 "$CFG_PHP"  2>/dev/null || true
chmod 600 "$CFG_JSON" 2>/dev/null || true
say "wrote the verified password to both files"

# ---------------------------------------------------------------------------
# 5. Prove it end to end, through Apache, the way the merge asks.
# ---------------------------------------------------------------------------
systemctl restart frognet-proxy frognet-daemon 2>/dev/null || true
sleep 1
if command -v curl >/dev/null 2>&1; then
    CODE="$(curl -s -o /tmp/.fn_cred_probe -w '%{http_code}' --max-time 20 \
        "http://127.0.0.1:8080/api.php?entity=sensors&action=values&SensorType=discovery&limit=1" \
        2>/dev/null || echo 000)"
    if [[ "$CODE" == "200" ]]; then
        say "api.php answers 200 -- the store is readable again"
    else
        say "api.php still returns HTTP $CODE"
        [[ -s /tmp/.fn_cred_probe ]] && say "  body: $(head -c 300 /tmp/.fn_cred_probe)"
    fi
    rm -f /tmp/.fn_cred_probe
fi
say "done"
exit 0
