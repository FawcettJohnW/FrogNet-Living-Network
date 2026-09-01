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
# core/type_detect.py
import re

def _is_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except Exception:
        return False

def detect_type(val):
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "float"

    if isinstance(val, str):
        v = val.strip()

        if _is_ipv4(v):
            return "ip"

        if "." in v and any(c.isalpha() for c in v) and re.match(r"^[A-Za-z0-9.\-]+$", v):
            return "host"

        if v.lower() in ("true", "false"):
            return "bool"

        if re.match(r"^[0-9]+$", v):
            return "int"

        if re.match(r"^[0-9]+\.[0-9]+$", v):
            return "float"

        return "string"

    return "string"
