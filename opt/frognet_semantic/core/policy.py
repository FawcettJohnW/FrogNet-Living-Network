#!/opt/frognet_semantic/venv/bin/python
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
# core/policy.py

class SemanticPolicy:
    """
    Given a request context, decides:
      - Phase 1 learning
      - Phase 2 semantic
      - Real upstream forwarding
      - Reject

    Context keys:
      - T_req
      - T_resp
      - slow_link
      - phase1_override
    """

    def decide(self, ctx):
        # Phase 1 override ALWAYS wins
        if ctx.get("phase1_override"):
            return "PHASE1"

        T_req = ctx.get("T_req", False)
        slow = ctx.get("slow_link", False)

        # If link fast -> real curl always
        if not slow:
            return "REAL"

        # Slow link:
        if T_req:
            return "SEMANTIC"

        # Slow link, no template -> try Phase 1
        return "PHASE1"
