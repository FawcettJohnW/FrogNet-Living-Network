#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# install_rendezvous.sh -- put the RAM-server rendezvous on the broker.
#
#   sudo ./install_rendezvous.sh --public-host fawcettinnovations.com \
#        [--api-port 8790] [--ports 22874-65535] [--idle 900] [--max 50] \
#        [--password WORD]...   (repeatable; defaults to the two demo words below)
#
#     GET http://<broker>:<api-port>/ram_rendezvous.php?tag=WORD  ->  a RAM server's port
#
# Installs: the endpoint on its own loopback-or-public vhost, the ram_server
# binary and the watchdog into /opt/frogram-rendezvous, and the config. Builds
# ram_server from source if a compiler is present, else expects it beside this
# script. Opens the port range in ufw if ufw is active. Touches nothing else on
# the broker -- not /var/www/html, not the tunnels, not 80/443.
set -euo pipefail
PUBLIC=""; APIPORT=8790; PORTS=22874-65535; IDLE=900; MAX=50
declare -a PASSWORDS=()
while [ $# -gt 0 ]; do case "$1" in
  --public-host) PUBLIC="$2"; shift 2;; --api-port) APIPORT="$2"; shift 2;;
  --ports) PORTS="$2"; shift 2;; --idle) IDLE="$2"; shift 2;; --max) MAX="$2"; shift 2;;
  --password) PASSWORDS+=("$2"); shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done
say() { echo "[rendezvous] $*"; }
die() { echo "[rendezvous] FATAL: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root"
[ -n "$PUBLIC" ] || die "--public-host is required (what clients dial)"
LOW="${PORTS%-*}"; HIGH="${PORTS#*-}"
HERE="$(cd "$(dirname "$0")" && pwd)"; APP=/opt/frogram-rendezvous
command -v apache2ctl >/dev/null || die "apache2 is not installed"
php -v >/dev/null 2>&1 || die "php-cli/mod_php is not installed"
php -m | grep -qi '^posix$' || die "php posix extension is missing (php-process): needed to manage the server processes"

install -d -m 0755 "$APP" "$APP/state"
# ram_server: build from the kit if we can, else take a prebuilt one beside us
if [ -f "$HERE/../server_cpp/ram_server.cpp" ] && command -v g++ >/dev/null; then
  say "building ram_server and reflector_server from source"
  g++ -std=c++11 -O2 -pthread -I"$HERE/../cpp" "$HERE/../server_cpp/ram_server.cpp" "$HERE/../cpp/frogram.cpp" -o "$APP/ram_server"
  g++ -std=c++11 -O2 -pthread -I"$HERE/../cpp" "$HERE/../reflector/reflector_server.cpp" "$HERE/../cpp/frogram.cpp" -o "$APP/reflector_server"
elif [ -f "$HERE/ram_server" ] && [ -f "$HERE/reflector_server" ]; then
  install -m 0755 "$HERE/ram_server" "$APP/ram_server"; install -m 0755 "$HERE/reflector_server" "$APP/reflector_server"
else
  die "no ram_server/reflector_server: build them (server_cpp/, reflector/) and put them beside this script, or install g++ and ship the kit's source"
fi
install -m 0755 "$HERE/ram_watchdog.sh" "$APP/ram_watchdog.sh"
command -v ss >/dev/null || die "ss is not installed (iproute2): the watchdog needs it to detect idle servers"

[ ${#PASSWORDS[@]} -gt 0 ] || PASSWORDS=('FrogNetD3mo!' 'ShrredR4M!')
PW_PHP=""; for w in "${PASSWORDS[@]}"; do e=${w//\\/\\\\}; e=${e//\'/\\\'}; PW_PHP+="'"$e"', "; done; PW_PHP=${PW_PHP%, }
DOCROOT="$APP/www"; install -d -m 0755 "$DOCROOT"
install -m 0644 "$HERE/ram_rendezvous.php" "$DOCROOT/ram_rendezvous.php"
cat > "$DOCROOT/rendezvous_config.php" <<EOF
<?php
define('RZ_PUBLIC_HOST',   '$PUBLIC');
define('RZ_BIND_HOST',     '0.0.0.0');
define('RZ_RAM_SERVER',    '$APP/ram_server');
define('RZ_REFLECTOR',     '$APP/reflector_server');
define('RZ_WATCHDOG',      '$APP/ram_watchdog.sh');
define('RZ_STATE_DIR',     '$APP/state');
define('RZ_PORT_LOW',      $LOW);
define('RZ_PORT_HIGH',     $HIGH);
define('RZ_MAX_SERVERS',   $MAX);
define('RZ_IDLE_TIMEOUT_S', $IDLE);
define('RZ_PASSWORDS', [$PW_PHP]);
EOF
chown -R www-data:www-data "$APP/state"; chmod 0640 "$DOCROOT/rendezvous_config.php"; chown root:www-data "$DOCROOT/rendezvous_config.php"

SITE=frogram-rendezvous
cat > "/etc/apache2/sites-available/$SITE.conf" <<EOF
# RAM-server rendezvous. Written by install_rendezvous.sh.
Listen $APIPORT
<VirtualHost *:$APIPORT>
    DocumentRoot $DOCROOT
    <Directory $DOCROOT>
        Options -Indexes
        AllowOverride None
        Require all granted
    </Directory>
    <Files "rendezvous_config.php">
        Require all denied
    </Files>
    ErrorLog \${APACHE_LOG_DIR}/$SITE-error.log
    CustomLog \${APACHE_LOG_DIR}/$SITE-access.log combined
</VirtualHost>
EOF
a2ensite -q "$SITE" >/dev/null
CFG="$(apache2ctl configtest 2>&1 || true)"; grep -q "Syntax OK" <<<"$CFG" || { echo "$CFG" >&2; die "apache config does not parse"; }
if [ -d /run/systemd/system ]; then systemctl restart apache2; else service apache2 restart; fi

# www-data launches setsid processes; make sure that is allowed (it is, by default).
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$APIPORT/tcp" comment 'frogram rendezvous API' >/dev/null
  ufw allow "$LOW:$HIGH/tcp" comment 'frogram rendezvous RAM servers' >/dev/null
  say "opened $APIPORT and $LOW:$HIGH in ufw"
else
  say "ufw not active; open TCP $APIPORT and $LOW-$HIGH yourself (and in the cloud firewall)"
fi
say "DONE. From anywhere:"
say "  curl 'http://$PUBLIC:$APIPORT/ram_rendezvous.php?pass=WORD&tag=demo'   # -> a port"
say "  frogbench --peer $PUBLIC:<that port>"
say "passwords: ${PASSWORDS[*]}  (edit RZ_PASSWORDS in $DOCROOT/rendezvous_config.php to change; no restart needed)"
