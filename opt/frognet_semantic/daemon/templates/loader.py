#!/opt/frognet_semantic/venv/bin/python3
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
"""
daemon/templates/loader.py

Thin adapter over core.store.TemplateStore.

Returns core.template.RequestTemplate / ReplyTemplate objects,
so semantic behavior matches the proven core library.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.store import TemplateStore
from core.template import RequestTemplate, ReplyTemplate


class TemplateLoader:
    def __init__(self) -> None:
        self._store = TemplateStore()

    def lookup_by_opcode(self, opcode: int) -> Tuple[Optional[RequestTemplate], Optional[ReplyTemplate]]:
        req_row, resp_row = self._store.lookup_by_opcode(int(opcode))
        if not req_row or not resp_row:
            return None, None
        return self._store.build_request_template(req_row), self._store.build_reply_template(resp_row)
