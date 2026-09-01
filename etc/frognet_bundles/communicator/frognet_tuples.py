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
"""frognet_tuples.py -- SHIM. Moved into core/ so publish + election resolve in every context
(headless daemon/proxy, discovery) without the communicator bundle on path. This aliases
the bundle name to the one core module so existing `import frognet_tuples` callers keep working with
shared state. New code imports core.frognet_tuples.

[A_SHIM_MUST_BE_USABLE_BEFORE_IT_FINISHES_V1]

This did the aliasing with `sys.modules[__name__] = _m` alone. That swap only
takes effect when this module finishes executing -- so anything that imports
`frognet_tuples` DURING that window binds this half-built module object, which
has os, sys and _CORE and nothing else. The reference is permanent: the swap
does not reach back into a name somebody already bound.

Measured 2026-08-11 on the relay, every two seconds for the life of the process:

    [relay] MediaHold publish failed: AttributeError(
            "module 'frognet_tuples' has no attribute 'put'")
    [relay] call reap failed: AttributeError(
            "module 'frognet_tuples' has no attribute 'my_ip'")

The relay ran on with no MediaHold and no reaping, and said so in a line that
reads like a store problem rather than an import one.

So the names are copied into THIS module's namespace as well. The swap still
happens, for callers that come later and for shared state; the copy is what
makes an early binding work. Both, because either alone has a hole.
"""
import os, sys
_CORE = "/opt/frognet_semantic"
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from core import frognet_tuples as _m

# Copy first: a caller that bound this object mid-import gets a working module.
# Everything public, and the private helpers callers actually use -- _values_raw
# and _delete_by_id are reached by name from comms_control and fnav.
globals().update({k: v for k, v in vars(_m).items()
                  if not k.startswith("__")})

# Then alias, so later importers share the ONE module and its state.
sys.modules[__name__] = _m
