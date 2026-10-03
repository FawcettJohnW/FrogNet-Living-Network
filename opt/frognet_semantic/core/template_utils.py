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
# core/template_utils.py

import json
from typing import Any, Dict, List, Tuple, Optional
from .type_detect import detect_type


def learn_json_schema(obj: Any) -> Tuple[Dict, Dict[str, str], List[str]]:
    """
    Learn a JSON schema, type_map, and field_order from a nested JSON object.

    - schema: nested type structure (for reference / debugging)
    - type_map: { "path.to.field" -> "int"/"string"/"bool"/"ip"/"host" }
    - field_order: list of "path.to.field" in deterministic order

    No duplicates, stable ordering, supports nested dicts and arrays.
    Arrays are modeled as "first element" representative.
    """
    type_map: Dict[str, str] = {}
    field_order: List[str] = []

    def walk(prefix: str, value: Any) -> Dict:
        # prefix is dotted path to this value
        if isinstance(value, dict):
            node: Dict[str, Any] = {}
            for k in sorted(value.keys()):
                v = value[k]
                p = f"{prefix}.{k}" if prefix else k
                node[k] = walk(p, v)
            return node

        elif isinstance(value, list):
            # Represent array by the schema of its first element.
            # field paths under prefix will refer to e.g. "rows.NetworkName".
            if not value:
                # empty array -> treat as array of strings
                t = "string"
                type_map[prefix] = t
                field_order.append(prefix)
                return {"type": t, "is_array": True}
            first = value[0]
            elem_schema = walk(prefix, first)
            return {"_array": elem_schema}

        else:
            t = detect_type(value)
            # Only record once per unique path
            if prefix not in type_map:
                type_map[prefix] = t
                field_order.append(prefix)
            return {"type": t}

    schema = walk("", obj)
    return schema, type_map, field_order


def flatten_json(obj: Any, field_order: List[str]) -> List[Any]:
    """
    Given a JSON object and a list of field paths, return a list of values
    in that order.

    Supports prefix paths like:
       "ok"
       "SensorID"
       "jsonData.kind"
       "rows.NetworkName"  (takes first element of rows[])

    For array paths, uses the first element.
    Missing paths -> None.
    """
    out: List[Any] = []

    for path in field_order:
        parts = path.split(".")
        cur = obj
        for p in parts:
            if isinstance(cur, list):
                # use first element as representative
                if not cur:
                    cur = None
                    break
                cur = cur[0]
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(p, None)
            if cur is None:
                break
        out.append(cur)
    return out


def rebuild_json(values: List[Any], field_order: List[str], type_map: Optional[Dict[str, str]] = None) -> Dict:
    """
    Rebuild a nested JSON object from a list of values and the field_order.

    NOTE (Jan 2026):
      - Added optional third argument `type_map` for compatibility with callers
        that pass it (e.g., JSON handler enforcing atomic arrays).
      - This function does not need type_map for its legacy responsibilities,
        so it is accepted and ignored.

    - For paths without arrays, we create nested dicts as needed.
    - For paths under "rows.", we create a single-element rows[] array and
      assign row fields there.

    This is sufficient for:
        {"ok": true, "SensorID": 122, "FrogID": "..."}
    and:
        {"ok": true, "rows":[{"NetworkName": "...", ...}]}
    """
    root: Dict[str, Any] = {}

    for val, path in zip(values, field_order):
        parts = path.split(".")
        if not parts:
            continue

        # Special-case "rows.*" as first-element array
        if parts[0] == "rows":
            row_list = root.setdefault("rows", [])
            if not row_list:
                row_list.append({})
            row_obj = row_list[0]

            cur = row_obj
            for p in parts[1:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts[-1]] = val
            continue

        # General nested path
        cur = root
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = val

    return root
