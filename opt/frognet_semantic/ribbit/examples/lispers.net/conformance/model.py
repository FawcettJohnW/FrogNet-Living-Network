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
from dataclasses import dataclass, field
from typing import Any, Protocol

class LispAdapter(Protocol):
    def capabilities(self) -> set[str]: ...
    def call(self, operation: str, **kwargs: Any) -> Any: ...

@dataclass(frozen=True)
class Case:
    id: str
    operation: str
    args: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None
    destructive: bool = False
