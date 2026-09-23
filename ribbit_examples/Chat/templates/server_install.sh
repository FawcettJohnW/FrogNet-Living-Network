#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# install.sh -- install ONE service's FrogNet RAM on a server. Built by
# frogram_package.py; what it installs is described by package.env beside it.
#
#   sudo ./install.sh --port PORT --dir DIR [--api-port 8080] [--use-existing-db]
#
#   --port      the public port the FNW1 listener answers on
#   --dir       where the service is installed (listener, docroot, config)
#   --api-port  the LOOPBACK port the service's PHP API answers on (default 8080)
#
#     Internet --> DIR/ram_listener.py :PORT (FNW1 only) --> 127.0.0.1:API_PORT <api>.php --> MySQL/MariaDB
#
# This does not make the machine a FrogNet node and installs nothing from the
# FrogNet tree. It sets up: the application's database (named by the app, in
# package.env), a loopback-only database user that can touch only that
# database, a loopback-only Apache vhost serving the app's PHP API from
# DIR/www, and the listener as a systemd unit. It never touches /var/www/html
# or ports 80/443.  Debian/Ubuntu with apache2, mod_php, php-mysql, systemd.
#
# NO FALLBACKS. Every step either succeeds or the script stops and says why.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/package.env" ] || { echo "package.env not found beside install.sh" >&2; exit 1; }
# shellcheck disable=SC1091
. "$HERE/package.env"      # APP VERSION DB_NAME DB_USER DEFAULT_PORT PATH_PREFIX API_CONFIG

PORT=""; DIR=""; API_PORT=8080; USE_EXISTING=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --dir) DIR="$2"; shift 2;;
    --api-port) API_PORT="$2"; shift 2;;
    --use-existing-db) USE_EXISTING=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
say() { echo "[$APP] $*"; }
die() { echo "[$APP] FATAL: $*" >&2; exit 1; }
[ -n "$PORT" ] || die "--port is required (the package was built with default $DEFAULT_PORT)"
[ -n "$DIR" ]  || die "--dir is required"
case "$DIR" in /*) ;; *) die "--dir must be an absolute path";; esac
[ "$(id -u)" = "0" ] || die "must run as root"

# ---- the package is what the packager built ------------------------------------
( cd "$HERE" && sha256sum --quiet -c MANIFEST.sha256 ) || die "package contents do not match MANIFEST.sha256"
PY="$(command -v python3)" || die "python3 is not installed"
command -v apache2ctl >/dev/null || die "apache2 is not installed"
command -v mysql      >/dev/null || die "the mysql client is not installed"
PHPMODS="$(php -m 2>/dev/null)" || die "php is not installed"
grep -qi '^mysqli$' <<<"$PHPMODS" || die "php mysqli extension is not installed (php-mysql)"
mysql -e "SELECT 1" >/dev/null 2>&1 || die "cannot reach the local database as root (mysql -e 'SELECT 1')"
[ -d /run/systemd/system ] || NOSYSTEMD=1
SITE="$APP-ram"; UNIT="$APP-ram-listener"; WWW="$DIR/www"

# ---- the application's database --------------------------------------------------
if [ -n "$(mysql -N -B -e "SHOW DATABASES LIKE '$DB_NAME'")" ] && [ "$USE_EXISTING" != 1 ]; then
  die "a $DB_NAME database already exists on this machine. Re-run with --use-existing-db to use it as it is."
fi
mysql -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql "$DB_NAME" < "$HERE/server/schema.sql"
say "database $DB_NAME is present"

# ---- its PHP API, in DIR/www, on loopback ------------------------------------------
install -d -m 0755 "$DIR" "$WWW"
for f in "$HERE"/server/api/*.php; do install -m 0644 "$f" "$WWW/$(basename "$f")"; done
if [ -f "$WWW/$API_CONFIG" ]; then
  say "keeping the existing $WWW/$API_CONFIG and its password"
  DB_PASS="$(grep -oP "define\('RAM_DB_PASS',\s*'\K[^']+" "$WWW/$API_CONFIG")" || die "no RAM_DB_PASS in $WWW/$API_CONFIG"
else
  DB_PASS="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 28)"
  [ ${#DB_PASS} -ge 20 ] || die "could not generate a password"
  cat > "$WWW/$API_CONFIG" <<EOF
<?php
define('RAM_DB_HOST', '127.0.0.1');
define('RAM_DB_USER', '$DB_USER');
define('RAM_DB_PASS', '$DB_PASS');
define('RAM_DB_NAME', '$DB_NAME');
EOF
fi
chown root:www-data "$WWW/$API_CONFIG"; chmod 0640 "$WWW/$API_CONFIG"

say "ensuring $DB_USER@localhost (loopback only, $DB_NAME only)"
mysql -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
          CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
          ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
          ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
          GRANT SELECT, INSERT, UPDATE, DELETE ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
          GRANT SELECT, INSERT, UPDATE, DELETE ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
          FLUSH PRIVILEGES;"

cat > "/etc/apache2/sites-available/$SITE.conf" <<EOF
# $APP: one service's FrogNet RAM API. Loopback only. Written by install.sh ($VERSION).
Listen 127.0.0.1:$API_PORT
<VirtualHost 127.0.0.1:$API_PORT>
    DocumentRoot $WWW
    <Directory $WWW>
        Options -Indexes
        AllowOverride None
        Require all granted
    </Directory>
    <Files "$API_CONFIG">
        Require all denied
    </Files>
    ErrorLog \${APACHE_LOG_DIR}/$SITE-error.log
    CustomLog \${APACHE_LOG_DIR}/$SITE-access.log combined
</VirtualHost>
EOF
a2ensite -q "$SITE" >/dev/null
CFG="$(apache2ctl configtest 2>&1 || true)"
grep -q "Syntax OK" <<<"$CFG" || { echo "$CFG" >&2; die "apache config does not parse"; }
if [ -z "${NOSYSTEMD:-}" ]; then systemctl restart apache2; else service apache2 restart >/dev/null; fi
sleep 1
ANS="$(curl -fsS -m 10 "http://127.0.0.1:$API_PORT$PATH_PREFIX?op=read&service=ram.selftest")" \
  || die "the API does not answer on 127.0.0.1:$API_PORT$PATH_PREFIX"
grep -q '"ok":true' <<<"$ANS" || die "the API answered: $ANS"
say "the API answers on loopback $API_PORT"

# ---- the listener: the one public port -----------------------------------------
install -m 0755 "$HERE/server/ram_listener.py" "$DIR/ram_listener.py"
install -m 0755 "$HERE/probe_ram.py" "$DIR/probe_ram.py"
install -m 0644 "$HERE/package.env" "$DIR/package.env"
RUN="$PY $DIR/ram_listener.py --listen 0.0.0.0:$PORT --origin 127.0.0.1:$API_PORT --path-prefix $PATH_PREFIX"
cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=$APP FrogNet RAM listener (FNW1) on :$PORT
After=network-online.target apache2.service

[Service]
DynamicUser=yes
ExecStart=$RUN
Restart=always

[Install]
WantedBy=multi-user.target
EOF
[ -z "${NOSYSTEMD:-}" ] || die "everything is configured and the unit file is written, but this machine has no systemd to start it. Start the listener by hand:  $RUN   then verify:  $PY $DIR/probe_ram.py 127.0.0.1 $PORT --path-prefix $PATH_PREFIX"
systemctl daemon-reload
systemctl enable "$UNIT.service" >/dev/null
systemctl restart "$UNIT.service"
sleep 2
"$PY" "$DIR/probe_ram.py" 127.0.0.1 "$PORT" --path-prefix "$PATH_PREFIX" || die "the listener on :$PORT did not pass the FNW1 probe"
say "DONE ($VERSION). FNW1 on :$PORT; the API and $DB_NAME are loopback only; installed in $DIR."
say "Open TCP $PORT in the firewall, then from any other machine:  python3 probe_ram.py <this host> $PORT"
