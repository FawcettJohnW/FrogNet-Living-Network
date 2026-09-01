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
# FrogNet Games installer/verifier. The tarball already lays files under / when
# extracted (tar xzf frognet_games.tar.gz -C /). This script verifies and reminds.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "[1/2] self-test (plays each game, proves replay/watcher/authority)"
python3 "$HERE/test_games.py"
echo
echo "[2/2] wire-up reminders:"
echo "  - Origin: ensure core/game_origin.py is hooked into the proxy (see GAME_ORIGIN_INSTALL.md)."
echo "  - Announce: python3 /etc/frognet_bundles/communicator/frognet_beacon_service.py \\"
echo "                --dbhost databasehost.frognet --interval 300   (use --once to test)"
echo "  - Play:    game_client.py --connect <host> --game hearts --table kitchen --who you"
echo "  - Watch:   ... --watch    Replay: ... --replay --cli"
echo "Done."
