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
# install_boardgamehost.sh — stand up a board-class game service on THIS machine.
#
# Ships in the standard source tree (every FrogNetHost is a service host) AND is
# self-contained so it can be copied to a non-FrogNetHost candidate box. It does NOT carry
# the engine payload — that lives at /etc/frognet_bundles/boardgame in the tree; if you copy
# this to a bare box, also copy that directory (and core/game_role.py) or pass --src.
#
# The SERVICE NAME is user-supplied and parameterizes everything: the SensorType region the
# engine governs, the <name>/capability tuple, the .frognet role name, and the systemd
# template instances frognet-boardgame@<name> / frognet-boardgame-advertise@<name>.
#
# Board class = python3-trivial gate (no GPU). Election ranks eligible boxes; a specialist
# wins on merit; need NOT be a .1. (Twitch class is a separate install with a GPU gate.)
#
# Usage: sudo install_boardgamehost.sh --name <service> [--gid <table>] [--hz N] [--src DIR]
set -eu
NAME=""; GID="table1"; HZ="5"; SRC="/etc/frognet_bundles/boardgame"
CORE="/opt/frognet_semantic/core"
while [ $# -gt 0 ]; do case "$1" in
  --name) NAME="$2"; shift 2;; --gid) GID="$2"; shift 2;;
  --hz) HZ="$2"; shift 2;; --src) SRC="$2"; shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ -n "$NAME" ] || { echo "--name <service> required (you name the service)" >&2; exit 2; }
case "$NAME" in *[!a-z0-9-]*) echo "name must be [a-z0-9-]" >&2; exit 2;; esac
[ -f "$SRC/bg_engine.py" ] || { echo "engine payload not at $SRC (copy the bundle or pass --src)" >&2; exit 1; }

echo "[$NAME] python3 gate"; command -v python3 >/dev/null || {
  export DEBIAN_FRONTEND=noninteractive
  command -v apt-get >/dev/null && apt-get update -qq && apt-get install -y -qq python3 >/dev/null \
    || { echo "install python3 and re-run" >&2; exit 1; }; }

# 1. ensure the engine bundle is in place (idempotent copy if --src differs from the tree)
if [ "$SRC" != "/etc/frognet_bundles/boardgame" ]; then
  echo "[$NAME] installing engine bundle -> /etc/frognet_bundles/boardgame"
  mkdir -p /etc/frognet_bundles/boardgame
  cp "$SRC"/bg_*.py "$SRC"/frognet_tuples.py /etc/frognet_bundles/boardgame/
fi

# 2. register the board-class handler under the USER'S NAME so the election can score it.
#    core/game_role.py (the criteria) ships in the tree; we add it to ROLE_HANDLERS keyed
#    by <name>. Idempotent + backup. (Discovery/election picks it up on its next restart.)
echo "[$NAME] registering handler in role_registry under '$NAME'"
python3 - "$CORE" "$NAME" <<'PY'
import sys, os, shutil, time, py_compile
core, name = sys.argv[1], sys.argv[2]
reg = os.path.join(core, "role_registry.py")
if not os.path.isfile(os.path.join(core, "game_role.py")):
    print("  WARNING: core/game_role.py missing in tree; copy it from the source tree", file=sys.stderr)
s = open(reg).read(); var = "GAME_HANDLER"
changed = False
if "from .game_role import GameRoleHandler" not in s:
    s = s.replace("from .database_handler import DatabaseRoleHandler",
                  "from .database_handler import DatabaseRoleHandler\nfrom .game_role import GameRoleHandler"); changed=True
if f"{var}" not in s:
    s = s.replace("DATABASE_HANDLER   = DatabaseRoleHandler()",
                  f"DATABASE_HANDLER   = DatabaseRoleHandler()\n{var}       = GameRoleHandler()"); changed=True
key = f'    "{name}":'
if key not in s:
    s = s.replace('    "databasehost": DATABASE_HANDLER,\n}',
                  f'    "databasehost": DATABASE_HANDLER,\n    "{name}":'+ " "*max(1,13-len(name)) + f'{var},\n}}'); changed=True
if changed:
    shutil.copy2(reg, f"{reg}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    open(reg,"w").write(s); py_compile.compile(reg, doraise=True)
    print(f"  registered '{name}' -> GameRoleHandler")
else:
    print(f"  '{name}' already registered")
PY

# 3. enable the template-unit instances for THIS service name
echo "[$NAME] enabling engine + advertiser (instances of the @ templates)"
# bake gid/hz into a drop-in so the template stays generic
mkdir -p "/etc/systemd/system/frognet-boardgame@${NAME}.service.d"
cat > "/etc/systemd/system/frognet-boardgame@${NAME}.service.d/args.conf" <<DROP
[Service]
ExecStart=
ExecStart=/usr/bin/env python3 /etc/frognet_bundles/boardgame/bg_engine.py --name %i --gid ${GID} --hz ${HZ}
DROP
systemctl daemon-reload
systemctl enable --now "frognet-boardgame@${NAME}.service"
systemctl enable --now "frognet-boardgame-advertise@${NAME}.timer"

[ -x /usr/local/bin/frognet_register_candidate.sh ] || \
  echo "[$NAME] WARNING: /usr/local/bin/frognet_register_candidate.sh missing (ships with install_databasehost/mediahost); <$NAME>/capability won't be written until present — engine still runs." >&2

cat <<DONE
[$NAME] done.
  engine:    systemctl status frognet-boardgame@${NAME}
  candidacy: systemctl list-timers | grep ${NAME}
  resolves:  ${NAME}.frognet  (once discovery/election restarts to load the handler)
  play:      python3 /etc/frognet_bundles/boardgame/bg_play.py --name ${NAME} --gid ${GID} --who <you> --seat w
DONE
