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
# daemon/core/policy.py

class ExecutionPolicy:
    """
    Enforces hard semantic rules:
      - destination is static unless explicitly overridden
      - semantic links may not downgrade to fast
    """

    def allow_destination_override(self, has_override_flag: bool) -> bool:
        return bool(has_override_flag)

    def allow_fast_fallback(self, semantic_required: bool) -> bool:
        return not semantic_required
