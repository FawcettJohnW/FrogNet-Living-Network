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
# frognet_fix_db.sh -- make FrogUser work on this node, permanently.
#
#   sudo frognet_fix_db.sh                  # uses the pond password
#   sudo FROGNET_DB_PASS='other' frognet_fix_db.sh
#
# Idempotent. Run it on every node. Safe to re-run any time.
#
# Sets the MySQL account for every host form api.php and the Python readers can
# arrive as, writes the one password into the only two files that carry it, and
# restarts the two services that cache it. Ends by proving api.php answers 200.
set -u

PASS="${FROGNET_DB_PASS:-Act30n!}"
CFG_PHP=/var/www/html/config.php
CFG_JSON=/opt/frognet_semantic/DB_CONFIG.json

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

# ---- 1. the account -------------------------------------------------------
# localhost AND 127.0.0.1: with skip-name-resolve off a TCP connect to
# 127.0.0.1 matches 'localhost', with it on it matches '127.0.0.1'. Creating
# both means the answer does not depend on which way that is configured.
FN_SECRET="$PASS" python3 - <<'PY' > /tmp/.fn_grant.sql
import os
p = os.environ["FN_SECRET"].replace("\\", "\\\\").replace("'", "''")
for h in ("localhost", "127.0.0.1"):
    print(f"CREATE USER IF NOT EXISTS 'FrogUser'@'{h}' IDENTIFIED BY '{p}';")
    print(f"ALTER USER 'FrogUser'@'{h}' IDENTIFIED BY '{p}';")
    print(f"GRANT ALL PRIVILEGES ON FrogNet.* TO 'FrogUser'@'{h}';")
print("FLUSH PRIVILEGES;")
PY
mysql < /tmp/.fn_grant.sql || { echo "FATAL: could not update the MySQL account" >&2; rm -f /tmp/.fn_grant.sql; exit 1; }
rm -f /tmp/.fn_grant.sql

# ---- 2. the two files that carry it ---------------------------------------
FN_SECRET="$PASS" python3 - "$CFG_PHP" "$CFG_JSON" <<'PY' || { echo "FATAL: config write failed" >&2; exit 1; }
import json, os, re, sys
php, js = sys.argv[1], sys.argv[2]
secret = os.environ["FN_SECRET"]

if os.path.exists(php):
    src = open(php, encoding="utf-8", errors="surrogateescape").read()
    lit = secret.replace("\\", "\\\\").replace("'", "\\'")
    out, n = re.subn(r"(define\(\s*'DB_PASS'\s*,\s*')(?:[^'\\]|\\.)*('\s*\))",
                     lambda m: m.group(1) + lit + m.group(2), src, count=1)
    if n != 1:
        sys.exit("no DB_PASS define in %s" % php)
    open(php + ".new", "w", encoding="utf-8", errors="surrogateescape").write(out)
    os.replace(php + ".new", php)

if os.path.exists(js):
    cfg = json.load(open(js))
else:
    cfg = {"host": "127.0.0.1", "user": "FrogUser",
           "database": "FrogNet", "port": 3306}
cfg["password"] = secret
json.dump(cfg, open(js + ".new", "w"), indent=2)
open(js + ".new", "a").write("\n")
os.replace(js + ".new", js)
PY
chown root:www-data "$CFG_PHP" 2>/dev/null || true
chmod 640 "$CFG_PHP"  2>/dev/null || true
chmod 600 "$CFG_JSON" 2>/dev/null || true

# ---- 3. the services that cache it ----------------------------------------
systemctl restart frognet-proxy frognet-daemon 2>/dev/null || true

# ---- 4. prove it ----------------------------------------------------------
sleep 1
CODE="$(curl -s -o /tmp/.fn_probe -w '%{http_code}' --max-time 20 \
    'http://127.0.0.1:8080/api.php?entity=sensors&action=values&SensorType=discovery&limit=1' \
    2>/dev/null || echo 000)"
if [[ "$CODE" == "200" ]]; then
    echo "$(hostname): OK -- api.php answers 200"
    rm -f /tmp/.fn_probe
    exit 0
fi
echo "$(hostname): STILL BROKEN -- api.php returned HTTP $CODE"
[[ -s /tmp/.fn_probe ]] && head -c 400 /tmp/.fn_probe && echo
rm -f /tmp/.fn_probe
exit 1
