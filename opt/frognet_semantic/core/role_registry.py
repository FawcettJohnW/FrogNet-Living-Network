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
# core/role_registry.py - role handlers ONLY, deliberately free of the lxml-backed
# XML/HTML FORMAT handlers. Discovery's mediahost/databasehost election needs
# ROLE_HANDLERS but NOT the format handlers (a proxy concern). Keeping these here
# lets a node elect service hosts on a box without lxml: a minimal/embedded node
# still self-forms and participates in the election even if it can't run the proxy's
# XML/HTML semantic compression. format_registry re-exports these for compatibility.
from .sotf_handler import SotFMediaHandler
from .database_handler import DatabaseRoleHandler
from .game_role import GameRoleHandler

SOTF_MEDIA_HANDLER = SotFMediaHandler()      # also the media-role handler
DATABASE_HANDLER   = DatabaseRoleHandler()
GAME_HANDLER       = GameRoleHandler()

ROLE_HANDLERS = {
    "mediahost":    SOTF_MEDIA_HANDLER,
    "databasehost": DATABASE_HANDLER,
    "boardgame":    GAME_HANDLER,
}


def role_handler(role_name):
    """role name -> the handler owning that role's election criteria. None if unknown."""
    return ROLE_HANDLERS.get(role_name)
