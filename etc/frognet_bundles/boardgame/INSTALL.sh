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
# INSTALL.sh — package install hook. Assumes the bundle tree is already extracted from /
# (this file lives at /etc/frognet_bundles/boardgame/INSTALL.sh). Stands up one service and
# makes the UI discoverable. Re-runnable.
set -eu
NAME="${1:-familygames}"; GID="${2:-table1}"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
echo "[boardgame] standing up network service '$NAME'"
/usr/local/bin/install_boardgamehost.sh --name "$NAME" --gid "$GID"
echo "[boardgame] UI module at /etc/frognet_bundles/boardgame/ui/boardgame_panel.py"
echo "[boardgame] manifest declares hub=games, tab=Games, group='Board Games'."
echo "[boardgame] The Communicator renders it data-driven on its next discovery cycle."
echo "[boardgame] done. Play via the Communicator Games tab, or:"
echo "  python3 /etc/frognet_bundles/boardgame/app/bg_play.py --name $NAME --gid $GID --who <you> --seat w"
