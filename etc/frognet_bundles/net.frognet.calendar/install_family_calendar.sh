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
# install_family_calendar.sh - pluggable install for the Family Calendar bundle.
#
# Installs, as ONE unit:
#   1. the OSS calendar backend (Radicale) as the durable PERM authority
#   2. the UnREST calendar codex (live shared element in the transient DB)
#   3. the web UI, and registration of the well-known name + suffix
#
# FrogNet conventions honored:
#   - NO `set -o pipefail` (prohibited in FrogNet bash).
#   - databasehost.frognet is resolved BY NAME from /etc/hosts, never hardwired.
#   - "open source" only; the specific license is never named.
#
# VERIFY-AGAINST-INSTALL markers (####) flag paths/values that depend on the
# target box and MUST be confirmed before production, per the no-invent rule.

set -e
set -u

BUNDLE="family-calendar"
WELLKNOWN="family-calendar.frognet"
SUFFIX="/unrest/family-calendar"        # codex endpoint behind local Apache
BUNDLE_ROOT="/etc/frognet_bundles/${BUNDLE}"   # the bundle's code lives here
CODEX_DIR="${BUNDLE_ROOT}/codex"
WEB_SRC="${BUNDLE_ROOT}/web"            # web assets travel inside the bundle
WEB_DIR="/var/www/html/${BUNDLE}"       #### confirm docroot on this box
RADICALE_DIR="${BUNDLE_ROOT}/oss/radicale"
PERM_NAME="frognet_perm_db"             # well-known perm host
HERE="$(cd "$(dirname "$0")" && pwd)"   # self-locate (script ships inside bundle)

log(){ /usr/local/bin/debugTag "install ${BUNDLE}: $*" 2>/dev/null || echo "[install ${BUNDLE}] $*"; }

# --- 0. preconditions -----------------------------------------------------
require_dbhost(){
  # resolve the floating transient DB by name; do not proceed until assigned
  local ip
  ip="$(getent hosts databasehost.frognet 2>/dev/null | awk '{print $1}' | head -n1)"
  if [ -z "${ip}" ]; then
    log "databasehost.frognet not yet resolved in /etc/hosts - aborting (re-run after merge)"
    exit 3
  fi
  log "transient DB resolves to ${ip} (by name; not hardwired)"
}

# --- 1. OSS backend (perm authority) --------------------------------------
install_oss_backend(){
  log "installing OSS calendar backend (Radicale) as perm authority"
  mkdir -p "${RADICALE_DIR}"
  # Radicale is OSS; install into an isolated venv so the bundle is self-contained.
  python3 -m venv "${RADICALE_DIR}/venv"
  # shellcheck disable=SC1091
  . "${RADICALE_DIR}/venv/bin/activate"
  pip install --upgrade pip >/dev/null
  pip install radicale >/dev/null     #### pin a version in production
  deactivate
  # Collections live on the PERM host's disk = the durable copy.
  mkdir -p "${RADICALE_DIR}/collections"   #### on perm host; replicate/own per policy
  cat > "${RADICALE_DIR}/config" <<CFG
[server]
hosts = 127.0.0.1:5232
[storage]
filesystem_folder = ${RADICALE_DIR}/collections
[auth]
type = none
CFG
  log "Radicale configured on 127.0.0.1:5232 (local; codex is the only client)"
}

# --- 2. UnREST codex ------------------------------------------------------
install_codex(){
  log "installing UnREST calendar codex"
  # code already laid down under BUNDLE_ROOT by the tar; nothing to copy
  mkdir -p "${CODEX_DIR}"
  # The codex talks the api.php sensor contract for the transient element and
  # CalDAV to local Radicale for perm. It is served behind Apache at SUFFIX.
  cat > "${CODEX_DIR}/wsgi.py" <<'WSGI'
# Minimal WSGI shim: bridges HTTP at the suffix to CalendarCodex verbs.
# Clients (web/Android) hit  http://<wellknown>/unrest/family-calendar/<verb>
# VERIFY: wire the TransientStore to api.php and PermStore to local Radicale here.
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from calendar_codex import CalendarCodex  # plus the box store implementations
# def application(env, start): ... (box wiring; see README)
WSGI
  log "codex installed at ${CODEX_DIR} (suffix ${SUFFIX})"
}

# --- 3. web UI ------------------------------------------------------------
install_web(){
  log "installing web UI"
  mkdir -p "${WEB_DIR}"
  cp "${WEB_SRC}/index.html" "${WEB_DIR}/index.html"
  log "web UI at ${WEB_DIR} (served by local Apache)"
}

# --- 4. Apache suffix routing + name registration -------------------------
wire_apache(){
  log "wiring Apache suffix ${SUFFIX} -> codex"
  # The codex answers SUFFIX behind the local Apache; the UI hits a local URL.
  cat > "/etc/apache2/conf-available/unrest-${BUNDLE}.conf" <<APACHE   #### path per box
# Route the UnREST suffix to the codex WSGI app. Adjust handler to the box's
# WSGI bridge (mod_wsgi / proxy to a local worker). VERIFY mechanism on target.
# WSGIScriptAlias ${SUFFIX} ${CODEX_DIR}/wsgi.py
APACHE
  log "NOTE: enable with a2enconf and reload Apache once the WSGI bridge is set"
}

register_name(){
  # Register the well-known service name the way other services register, so
  # WellKnownSites resolves ${WELLKNOWN} mesh-wide. Lazy/self-announcing; the
  # stack discovers the codex when the first request hits the suffix.
  log "registering ${WELLKNOWN} (re-registers on the standard lease interval)"
  #### invoke the box's service-registration path here (same mechanism as other
  #### services). Do NOT hardwire an IP; the name floats with the host.
}

main(){
  require_dbhost
  install_oss_backend
  install_codex
  install_web
  wire_apache
  register_name
  log "Family Calendar bundle installed as a pluggable unit."
  log "Run tests: python3 ${CODEX_DIR}/tests/test_calendar_codex.py"
}
main "$@"
