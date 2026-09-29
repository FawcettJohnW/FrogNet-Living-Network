#!/usr/bin/env python3
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
"""frognet_host_reset.py - SHIM. Moved into core/ so publish + election resolve in every context
(headless daemon/proxy, discovery) without the communicator bundle on path. This aliases
the bundle name to the one core module so existing `import frognet_host_reset` callers keep working with
shared state. New code imports core.frognet_host_reset."""
import os, sys
_CORE = "/opt/frognet_semantic"
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from core import frognet_host_reset as _m
sys.modules[__name__] = _m
