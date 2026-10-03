#!/usr/bin/env bash
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
# install_databasehost.sh - make THIS machine able to serve the databasehost role.
#
# A databasehost is NOT automatically THE databasehost - it becomes a CANDIDATE. It
# writes its capability to the transient under `databasehost/capability` and the
# merge-end election (live.py SERVICE_HOSTS_ELECTION_V1) picks the best candidate by
# merit. This installer just makes the machine ELIGIBLE and ADVERTISED.
#
# What it does:
#   1. Install mariadb/mysql server + PHP + Apache (the transient DB API stack).
#   2. Load the FrogNet schema and create the FrogUser account config.php expects.
#   3. Deploy the PHP API (api.php and friends) to the web root.
#   4. Install a systemd timer that writes databasehost/capability every 60s, so the
#      election can see and score this machine. mysql_running gates eligibility.
#
# Usage:  sudo ./install_databasehost.sh [--src <dir>]
#   --src   directory holding Create_Database.sql, schema_fixups.sql, config.php and
#           the *.php web set (default: ./dbhost_payload, then /var/www/html)
#
# Idempotent: safe to re-run. Does NOT drop an existing FrogNet database unless you
# pass --reset-db (Create_Database.sql DROPs - guarded behind that flag).
set -eu

SRC=""
RESET_DB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$2"; shift 2;;
    --reset-db) RESET_DB=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" = "0" ] || { echo "must run as root (sudo)" >&2; exit 1; }

# locate the payload (schema + php + config)
if [ -z "$SRC" ]; then
  # [ONE_WEB_ROOT_V1] /opt/frognet_semantic/web was a byte-identical stale mirror
  # of /var/www/html (55 files same, api.php a generation behind, plus a 23 MB tar
  # named .php). Removed 2026-08-29. It was also the middle of a three-way
  # try-then-try-again chain, so which copy of the schema a dbhost installed from
  # depended on what happened to exist. There is one web root.
  for c in ./dbhost_payload /var/www/html; do
    [ -f "$c/Create_Database.sql" ] && { SRC="$c"; break; }
  done
fi
[ -n "$SRC" ] && [ -f "$SRC/Create_Database.sql" ] || {
  echo "[dbhost] no payload found (need Create_Database.sql); pass --src <dir>" >&2; exit 1; }
echo "[dbhost] payload: $SRC"

# 1. packages
echo "[dbhost] installing mariadb + php + apache ..."
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  # php-mysqli is not an installable package name on modern apt; php-mysql provides
  # the mysqli + pdo_mysql extensions. Fall back to the versioned package if the
  # metapackage is unavailable.
  if ! apt-get install -y -qq mariadb-server php php-mysql libapache2-mod-php apache2 jq curl >/dev/null 2>&1; then
    PHPV="$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || true)"
    apt-get install -y -qq mariadb-server php libapache2-mod-php apache2 jq curl >/dev/null || true
    if [ -n "$PHPV" ]; then
      apt-get install -y -qq "php${PHPV}-mysql" >/dev/null 2>&1 || apt-get install -y -qq php-mysql >/dev/null 2>&1 || true
    else
      apt-get install -y -qq php-mysql >/dev/null 2>&1 || true
    fi
  fi
  command -v php >/dev/null 2>&1 && php -m 2>/dev/null | grep -qi mysqli \
    || echo "[dbhost] WARN: php mysqli extension not detected; api.php DB access may fail" >&2
else
  echo "[dbhost] non-apt system: install mariadb-server, php, php-mysql, apache2 yourself, then re-run with packages present" >&2
fi
systemctl enable --now mariadb >/dev/null 2>&1 || systemctl enable --now mysql >/dev/null 2>&1 || true

# 2. schema + user
# [NO_HARDCODED_CREDENTIAL_V1] DB_PASS defaulted to a live pond password when
# config.php could not be read or had no DB_PASS line. That is a credential in the
# source AND a silent wrong answer: the dbhost would be built with a password
# nobody chose, and only fail later at auth. The user and database names have safe
# conventional defaults; the password does not, so its absence is fatal.
DB_USER="$(grep -oP "define\('DB_USER',\s*'\K[^']+" "$SRC/config.php" 2>/dev/null || echo FrogUser)"
DB_NAME="$(grep -oP "define\('DB_NAME',\s*'\K[^']+" "$SRC/config.php" 2>/dev/null || echo FrogNet)"
DB_PASS="$(grep -oP "define\('DB_PASS',\s*'\K[^']+" "$SRC/config.php" 2>/dev/null || true)"
if [ -z "$DB_PASS" ]; then
  echo "[dbhost] no DB_PASS in $SRC/config.php - cannot create the database user without the pond password" >&2
  exit 1
fi
if [ "$DB_PASS" = "__FROGNET_DB_PASS__" ]; then
  echo "[dbhost] $SRC/config.php still holds the placeholder __FROGNET_DB_PASS__ - phase C1b has not injected the pond password yet. Run the installer, or point --src at an installed web root." >&2
  exit 1
fi

db_exists="$(mysql -N -B -e "SHOW DATABASES LIKE '$DB_NAME';" 2>/dev/null || true)"
if [ -n "$db_exists" ] && [ "$RESET_DB" != "1" ]; then
  echo "[dbhost] database $DB_NAME exists; leaving data intact (pass --reset-db to recreate)"
else
  echo "[dbhost] loading schema (Create_Database.sql)$([ "$RESET_DB" = 1 ] && echo ' [--reset-db: DROP+CREATE]')"
  mysql < "$SRC/Create_Database.sql"
  [ -f "$SRC/schema_fixups.sql" ] && mysql "$DB_NAME" < "$SRC/schema_fixups.sql" || true
fi

echo "[dbhost] ensuring account $DB_USER@localhost ..."
# [DBHOST_FAMILY_DB_V1] Create_Database.sql builds FrogNet only; the installer's D4
# always created FrogNetFamily alongside it (the Communicator's games/calendar/chat
# store). Granting a database that does not exist is a silent no-op, so create it
# here or the Communicator fails at first write on a freshly-built databasehost.
mysql -e "CREATE DATABASE IF NOT EXISTS \`FrogNetFamily\`
          CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

# [DBHOST_REMOTE_GRANT_V1] The '%' host is NOT optional and its absence is why the
# installer grew its own divergent copy of this block. databasehost.frognet is a
# FLOATING role: every other node in the pond connects to whichever machine holds it
# ACROSS 10/8 - monitors, api.php clients, the proxy/daemon metrics writers. Granting
# only localhost/127.0.0.1 makes this machine eligible for the role but unusable in
# it: local queries work, every remote node gets access-denied. Also grant the
# FrogNetFamily DB, which the Communicator (games/calendar/chat) uses.
mysql -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
          CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
          CREATE USER IF NOT EXISTS '$DB_USER'@'%'         IDENTIFIED BY '$DB_PASS';
          ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
          ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
          ALTER USER '$DB_USER'@'%'         IDENTIFIED BY '$DB_PASS';
          GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
          GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
          GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'%';
          GRANT ALL PRIVILEGES ON \`FrogNetFamily\`.* TO '$DB_USER'@'localhost';
          GRANT ALL PRIVILEGES ON \`FrogNetFamily\`.* TO '$DB_USER'@'127.0.0.1';
          GRANT ALL PRIVILEGES ON \`FrogNetFamily\`.* TO '$DB_USER'@'%';
          FLUSH PRIVILEGES;"

# 3. php web set
echo "[dbhost] deploying PHP API to /var/www/html ..."
mkdir -p /var/www/html
shopt -s nullglob
for f in "$SRC"/*.php; do
  d="/var/www/html/$(basename "$f")"
  [ -e "$d" ] && [ "$f" -ef "$d" ] && continue   # SRC may BE /var/www/html
  cp -f "$f" /var/www/html/
done
if [ -f "$SRC/config.php" ] && ! { [ -e /var/www/html/config.php ] && [ "$SRC/config.php" -ef /var/www/html/config.php ]; }; then
  cp -f "$SRC/config.php" /var/www/html/config.php
fi
chown -R www-data:www-data /var/www/html 2>/dev/null || true
systemctl enable --now apache2 >/dev/null 2>&1 || true

# verify the API answers locally
sleep 1
if curl -fsS "http://127.0.0.1/api.php?entity=sensors&action=values&SensorType=System" >/dev/null 2>&1; then
  echo "[dbhost] api.php responding locally - OK"
else
  echo "[dbhost] WARN: api.php did not answer on 127.0.0.1; check apache/php/config.php" >&2
fi

# 4. candidate registration (advertises databasehost/capability every 60s).
# Uses the shared, election-COMPATIBLE writer (frognet_tuples -> SD:capability.<scope>),
# the SAME tuple the merge-end election reads. mysql_running in the blob gates real
# eligibility. install_* only ensures the role SERVER is present; the advertiser and
# its timer are role-agnostic and set up here the same on every host.
for f in frognet_capability_probe.sh frognet_register_candidate.sh frognet_setup_advertisers.sh; do
  install -m 0755 "$f" /usr/local/bin/ 2>/dev/null || cp -f "$f" /usr/local/bin/ 2>/dev/null || true
done
if [ -x /usr/local/bin/frognet_setup_advertisers.sh ]; then
  /usr/local/bin/frognet_setup_advertisers.sh || true       # installs + enables BOTH timers
else
  # minimal fallback: just register databasehost now
  /usr/local/bin/frognet_register_candidate.sh databasehost || true
fi

echo "[dbhost] DONE. This machine is now a databasehost CANDIDATE."
echo "[dbhost]   - schema loaded, $DB_USER ready, api.php served by apache"
echo "[dbhost]   - capability advertised every 60s under databasehost/capability"
echo "[dbhost]   - the merge-end election will pick it if it scores best."
