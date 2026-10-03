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
# /usr/local/bin/frognet_enable_on_boot.sh
#
# Installs and enables a systemd unit that performs FrogNet clean bring-up at boot.
#

UNIT_PATH="/etc/systemd/system/frognet-bootstrap.service"
UP_SCRIPT="/usr/local/bin/frognet_up_clean.sh"
DOWN_SCRIPT="/usr/local/bin/frognet_down_clean.sh"

must_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Must run as root" >&2
    exit 1
  fi
}

have_systemd() {
  command -v systemctl >/dev/null 2>&1
}

main() {
  must_root
  if ! have_systemd; then
    echo "systemd not available; cannot install boot unit" >&2
    exit 1
  fi
  if [[ ! -x "$UP_SCRIPT" ]]; then
    echo "Missing or non-executable: $UP_SCRIPT" >&2
    exit 1
  fi
  if [[ ! -x "$DOWN_SCRIPT" ]]; then
    echo "Missing or non-executable: $DOWN_SCRIPT" >&2
    exit 1
  fi

  cat >"$UNIT_PATH" <<'EOF'
[Unit]
Description=FrogNet bootstrap (clean bring-up)
Wants=network-online.target
After=network-online.target NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/frognet_up_clean.sh
ExecStop=/usr/local/bin/frognet_down_clean.sh
TimeoutStartSec=180
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable frognet-bootstrap.service
  systemctl restart frognet-bootstrap.service

  echo "Installed and enabled frognet-bootstrap.service"
  systemctl status frognet-bootstrap.service --no-pager || true
}

main "$@"
