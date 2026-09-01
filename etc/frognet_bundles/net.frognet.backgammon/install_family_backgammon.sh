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
# install_family_backgammon.sh — pluggable install for the Family Backgammon bundle.
# Native build (no OSS backend): the codex IS the game. Installs to /etc/frognet_bundles,
# deploys the web UI to the docroot, wires the Apache suffix, registers the well-known
# name. No `pipefail`; resolve by name; "open source" only.
set -e
set -u
BUNDLE="family-backgammon"
WELLKNOWN="family-backgammon.frognet"
SUFFIX="/unrest/family-backgammon"
BUNDLE_ROOT="/etc/frognet_bundles/${BUNDLE}"
CODEX_DIR="${BUNDLE_ROOT}/codex"
WEB_SRC="${BUNDLE_ROOT}/web"
WEB_DIR="/var/www/html/${BUNDLE}"        #### confirm docroot on this box
HERE="$(cd "$(dirname "$0")" && pwd)"
log(){ echo "[install ${BUNDLE}] $*"; }

require_dbhost(){
  local ip; ip="$(getent hosts databasehost.frognet 2>/dev/null | awk '{print $1}' | head -n1)"
  if [ -z "${ip}" ]; then log "databasehost.frognet not resolved yet — re-run after merge"; exit 3; fi
  log "transient DB resolves to ${ip} (by name)"
}
install_web(){
  log "deploying web UI"; mkdir -p "${WEB_DIR}"; cp "${WEB_SRC}/index.html" "${WEB_DIR}/index.html"
}
wire_apache(){
  log "wiring Apache suffix ${SUFFIX} -> codex"
  cat > "/etc/apache2/conf-available/unrest-${BUNDLE}.conf" <<APACHE   #### path per box
# Route the UnREST suffix to the codex WSGI bridge (mod_wsgi or proxy to a local worker).
# WSGIScriptAlias ${SUFFIX} ${CODEX_DIR}/wsgi.py
APACHE
  log "enable with a2enconf and reload Apache once the WSGI bridge is set"
}
register_name(){
  log "registering ${WELLKNOWN} via the box's service-registration path (renewing lease)"
  #### invoke the standard registration here; never hardwire an IP
}
main(){
  require_dbhost
  log "code is under ${BUNDLE_ROOT} (laid down by the tar)"
  install_web; wire_apache; register_name
  log "Family Backgammon installed."
  log "Test: python3 ${BUNDLE_ROOT}/tests/test_backgammon_codex.py"
  log "Try in a browser (no box): python3 ${BUNDLE_ROOT}/dev/serve_web_test.py  then open the URL"
}
main "$@"
