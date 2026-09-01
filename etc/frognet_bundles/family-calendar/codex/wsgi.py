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
# Minimal WSGI shim: bridges HTTP at the suffix to CalendarCodex verbs.
# Clients (web/Android) hit  http://<wellknown>/unrest/family-calendar/<verb>
# VERIFY: wire the TransientStore to api.php and PermStore to local Radicale here.
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from calendar_codex import CalendarCodex  # plus the box store implementations
# def application(env, start): ... (box wiring; see README)
