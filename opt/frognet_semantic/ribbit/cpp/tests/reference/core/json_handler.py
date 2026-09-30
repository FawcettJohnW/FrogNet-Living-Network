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
# core/json_handler.py
#
# JSON semantic handler:
#   - mode="json"
#   - Supports objects and top-level arrays (ATOMIC in v3)
#   - Key/path order is deterministic
#   - No blobs over the wire
#
# Array support (v3):
#   A) Top-level arrays:
#      - Treated as ATOMIC values (single field "value")
#      - type_map["value"] = "array"
#      - Value transmitted is the full list (codec compresses; dict/list preserved)
#
#   B) Nested arrays inside objects (e.g., jsonData.links):
#      - MUST be learned as arrays, BUT MUST NOT be flattened to scalar subpaths
#      - They are treated as ATOMIC values in field_order/type_map:
#           field_order includes "jsonData.links" (single entry)
#           type_map["jsonData.links"] = "array"
#      - Value transmitted is the full list (codec compresses)
#
# Null-safe typing:
#   - null never dominates type inference
#   - if first sample is null, field type becomes "any" unless later samples refine it
#
# Path-aware typing:
#   - Certain keys are NEVER "host" even if they look like identifiers (dev, kind, run_id, SensorType)
#   - Certain keys SHOULD be host when they are names (SensorName, NetworkName, peer_name, host_name)
#
# IMPORTANT:
#   - The SemanticCodec v3 guarantees dict/list survive encode+decode.
#   - Therefore, flattening top-level arrays into fixed item0..itemN slots is obsolete and harmful.
#   - This handler treats top-level arrays as atomic to preserve structural integrity.

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
import json
import math
import os

# ---- [NONFINITE_JSON_GUARD] -------------------------------------------------
# JSON has no representation for inf/-inf/nan.  Python's json.loads silently
# accepts the barewords Infinity/NaN (so a single bad float rides in unnoticed),
# and json.dumps re-emits them by default - invalid JSON that PHP json_decode
# and other strict parsers reject, which then poisons every downstream consumer
# and the template/diff machinery.  None is this codebase's existing "no value"
# sentinel, so non-finite floats are coerced to None at the handler boundary.
def _sanitize_nonfinite(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nonfinite(v) for v in obj]
    return obj

def _dumps(obj, **kw):
    kw.setdefault("separators", (",", ":"))
    return json.dumps(_sanitize_nonfinite(obj), **kw)
import re
import ast

from .template_utils import rebuild_json
from .blob_store import BlobStore

_DEBUG = os.environ.get("FROGNET_DEBUG", "0").strip() == "1"

DEFAULT_MAX_ITEMS = 64

_RE_V4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# A dict key is safe for dot-path decomposition only if it looks like an
# identifier: starts with a letter or underscore, contains only
# alphanumeric chars and underscores.  Anything else (dots, arrows,
# spaces, hyphens, etc.) means the dict should be treated as atomic JSON.
_SAFE_DICT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_ipv4(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not _RE_V4.match(s):
        return False
    try:
        parts = [int(p) for p in s.split(".")]
        return len(parts) == 4 and all(0 <= p <= 255 for p in parts)
    except Exception:
        return False


def _looks_like_interface(s: str) -> bool:
    """
    Common Linux ifname patterns. These must NEVER type as host.
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    if s in ("lo",):
        return True
    return bool(re.match(r"^(eth|wlan|wl|enp|wlp|br|docker|usb|veth)\w*$", s))


def _looks_like_enum(s: str) -> bool:
    """
    Uppercase semantic enums (FAST/SEMANTIC/LOCAL/DOWN/UNKNOWN/etc).
    Must NEVER type as host.
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    if s.upper() in ("FAST", "SEMANTIC", "LOCAL", "DOWN", "UNKNOWN", "GOOD", "OK", "WARN", "BAD"):
        return True
    if re.match(r"^[A-Z0-9_]{2,}$", s) and "." not in s:
        return True
    return False


def _is_plausible_host(s: str) -> bool:
    """
    Conservative host detector for template typing.
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or " " in s:
        return False
    if _is_ipv4(s):
        return False
    if _looks_like_interface(s):
        return False
    if _looks_like_enum(s):
        return False
    if not re.search(r"[A-Za-z]", s):
        return False
    if "." not in s and not s.startswith("FrogNetHost"):
        return False
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9\.\-]*[A-Za-z0-9]$", s))


def _force_string_for_path(path: str) -> bool:
    """
    Paths that are identifiers, enums, or interface labels must never be host.
    """
    if not path:
        return False

    p = path.lower()

    if p.endswith(".dev") or p == "dev":
        return True
    if p.endswith(".kind") or p == "kind":
        return True
    if p.endswith(".run_id") or p == "run_id":
        return True
    if p.endswith(".sensortype") or p == "sensortype":
        return True
    if p.endswith(".metricname") or p == "metricname":
        return True
    if p.endswith(".method") or p == "method":
        return True
    if p.endswith(".action") or p == "action":
        return True
    if p.endswith(".tags") or p == "tags":
        return True
    if p.endswith(".status") or p == "status":
        return True
    if p.endswith(".reason") or p == "reason":
        return True

    return False


def _force_host_for_path(path: str) -> bool:
    """
    Paths that SHOULD be host-typed if they look like names.
    """
    if not path:
        return False
    p = path.lower()

    if p.endswith("sensorname") or p.endswith(".sensorname"):
        return True
    if p.endswith("networkname") or p.endswith(".networkname"):
        return True
    if p.endswith("peer_name") or p.endswith(".peer_name"):
        return True
    if p.endswith("host_name") or p.endswith(".host_name"):
        return True

    return False


def _leaf_type(v: Any, path: str = "") -> str:
    """
    Path-aware, null-safe leaf typing.
    None => 'any' (caller may refine later).
    """
    if v is None:
        return "any"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        if _is_ipv4(v):
            return "ip"

        if _force_string_for_path(path):
            return "string"

        if _force_host_for_path(path):
            return "host" if _is_plausible_host(v) else "string"

        if _looks_like_interface(v) or _looks_like_enum(v):
            return "string"
        if _is_plausible_host(v):
            return "host"
        return "string"

    return "any"


def _merge_schema(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a

    if isinstance(a, dict) and "type" in a and len(a) == 1:
        if isinstance(b, dict) and "type" in b and len(b) == 1:
            ta = a["type"]
            tb = b["type"]
            if ta == "any":
                return {"type": tb}
            if tb == "any":
                return {"type": ta}
            if ta == tb:
                return {"type": ta}
            if (ta, tb) in (("int", "float"), ("float", "int")):
                return {"type": "float"}
            return {"type": "any"}
        return {"type": "any"}

    if isinstance(a, dict) and "_array" in a:
        if isinstance(b, dict) and "_array" in b:
            return {"_array": _merge_schema(a["_array"], b["_array"])}
        return {"type": "any"}

    if isinstance(a, dict) and isinstance(b, dict):
        out: Dict[str, Any] = {}
        keys = sorted(set(a.keys()) | set(b.keys()))
        for k in keys:
            out[k] = _merge_schema(a.get(k), b.get(k))
        return out

    return {"type": "any"}


def infer_schema_preserve_arrays(value: Any, path: str = "") -> Any:
    if value is None:
        return {"type": "any"}

    if isinstance(value, (bool, int, float, str)):
        return {"type": _leaf_type(value, path=path)}

    if isinstance(value, dict):
        # A dict can only be safely decomposed into dot-separated field
        # paths if EVERY key is a stable identifier (alphanumeric + _).
        #
        # Unsafe keys cause two classes of failure:
        #  1. Dots in keys ("10.x.y.1") collide with the path
        #     separator - flatten/rebuild splits on "." and creates
        #     phantom nested structure.
        #  2. Non-identifier keys ("REQ_REPEAT->RESP_SAME") signal a
        #     variable-key dict whose key set changes between requests
        #     (e.g. matrix entries that appear only when non-zero).
        #     Any change in the sorted key list shifts field_order
        #     positions, causing every subsequent value to land in
        #     the wrong field on decode.
        #
        # In both cases, treat the entire dict as an opaque JSON value
        # so it round-trips via TYPE_JSON encoding.
        if any(not _SAFE_DICT_KEY_RE.match(str(k)) for k in value.keys()):
            return {"type": "any"}

        out: Dict[str, Any] = {}
        for k in sorted(value.keys(), key=lambda x: str(x)):
            kk = str(k)
            child_path = f"{path}.{kk}" if path else kk
            out[kk] = infer_schema_preserve_arrays(value.get(k), path=child_path)
        return out

    if isinstance(value, list):
        if len(value) == 0:
            return {"_array": {"type": "any"}}

        item_schema: Optional[Any] = None
        for it in value:
            s = infer_schema_preserve_arrays(it, path=path)
            item_schema = _merge_schema(item_schema, s)
        if item_schema is None:
            item_schema = {"type": "any"}
        return {"_array": item_schema}

    return {"type": "any"}


def _build_field_order_and_type_map(schema: Any, prefix: str = "") -> Tuple[List[str], Dict[str, str]]:
    field_order: List[str] = []
    type_map: Dict[str, str] = {}

    def add_path(p: str, t: str) -> None:
        field_order.append(p)
        type_map[p] = t

    if isinstance(schema, dict) and "_array" in schema:
        add_path(prefix or "value", "array")
        return field_order, type_map

    if isinstance(schema, dict) and "type" in schema and len(schema) == 1:
        t = schema["type"]
        if t not in ("ip", "host", "string", "int", "float", "bool", "raw", "any", "array"):
            t = "any"
        add_path(prefix or "value", t)
        return field_order, type_map

    if isinstance(schema, dict):
        for k in sorted(schema.keys()):
            child = schema[k]
            child_path = f"{prefix}.{k}" if prefix else str(k)

            if isinstance(child, dict) and "_array" in child:
                add_path(child_path, "array")
                continue

            if isinstance(child, dict) and "type" in child and len(child) == 1:
                t = child["type"]
                if t not in ("ip", "host", "string", "int", "float", "bool", "raw", "any", "array"):
                    t = "any"
                add_path(child_path, t)
                continue

            fo2, tm2 = _build_field_order_and_type_map(child, child_path)
            field_order.extend(fo2)
            type_map.update(tm2)

        return field_order, type_map

    add_path(prefix or "value", "any")
    return field_order, type_map


def _get_by_path(obj: Any, path: str) -> Any:
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        if part not in cur:
            return None
        cur = cur.get(part)
    return cur


def flatten_json_preserve_arrays(obj: Dict[str, Any], field_order: List[str]) -> List[Any]:
    out: List[Any] = []
    for p in field_order:
        if p == "value":
            out.append(obj)
        else:
            out.append(_get_by_path(obj, p))
    return out


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class JsonFormatHandler(UnRESTHandler):
    def _max_items(self) -> int:
        try:
            return int(os.environ.get("FROGNET_JSON_MAX_ITEMS", str(DEFAULT_MAX_ITEMS)))
        except Exception:
            return DEFAULT_MAX_ITEMS

    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        return self._learn_template(body_text)

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        return self._learn_template(body_text)

    def _learn_template(self, body_text: str) -> Dict[str, Any]:
        body_text = (body_text or "").strip()
        if not body_text:
            return {
                "mode": "json",
                "schema": {},
                "field_order": [],
                "type_map": {},
                "baseline": {},
                "baseline_blobs": {},
                "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
            }

        try:
            obj = _sanitize_nonfinite(json.loads(body_text))
        except Exception:
            return {
                "mode": "json",
                "schema": {},
                "field_order": [],
                "type_map": {},
                "baseline": {},
                "baseline_blobs": {},
                "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
            }

        # Debug prints gated behind FROGNET_DEBUG=1
        if _DEBUG:
            print("JSON HANDLER RAW OBJ: ", json.dumps(obj, indent=2), flush=True)

        # v3 FIX: top-level arrays are ATOMIC JSON (single field "value")
        if isinstance(obj, list):
            return {
                "mode": "json",
                "schema": infer_schema_preserve_arrays(obj, path=""),
                "field_order": ["value"],
                "type_map": {"value": "array"},
                "baseline": {"value": obj},
                "baseline_blobs": {},
                "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
            }

        if isinstance(obj, dict):
            schema = infer_schema_preserve_arrays(obj, path="")
            field_order, type_map = _build_field_order_and_type_map(schema, prefix="")
            flat_vals = flatten_json_preserve_arrays(obj, field_order)
            baseline = {}
            baseline_blobs = {}
            for path, v in zip(field_order, flat_vals):
                # For large arrays, store in BlobStore and keep only the blob_id
                # in baseline_blobs. This keeps templateJSON under the size limit
                # while preserving the full value for reconstruction on RESP_SAME.
                if isinstance(v, list):
                    serialized = _dumps(v, ensure_ascii=False).encode("utf-8")
                    if len(serialized) > 8192:
                        blob_id = BlobStore.store(serialized)
                        baseline_blobs[path] = blob_id
                        baseline[path] = None  # placeholder; rebuild uses blob
                        continue
                baseline[path] = v

            out = {
                "mode": "json",
                "schema": schema,
                "field_order": field_order,
                "type_map": type_map,
                "baseline": baseline,
                "baseline_blobs": baseline_blobs,
                "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
            }

            if _DEBUG:
                print("jSON HANDLER EXTRACTED : ", out, flush=True)
            return out

        schema = {"type": _leaf_type(obj, path="value")}
        field_order = ["value"]
        type_map = {"value": schema["type"]}
        baseline = {"value": obj}
        out = {
            "mode": "json",
            "schema": schema,
            "field_order": field_order,
            "type_map": type_map,
            "baseline": baseline,
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }
        if _DEBUG:
            print("jSON HANDLER EXTRACTED (2) : ", out, flush=True)
        return out

    def extract_request_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract_dynamic(body_text, fragment)

    def extract_reply_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract_dynamic(body_text, fragment)

    def _extract_dynamic(self, body_text: str, fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        body_text = (body_text or "").strip()
        field_order: List[str] = fragment.get("field_order") or []
        if not body_text or not field_order:
            return []

        try:
            obj = _sanitize_nonfinite(json.loads(body_text))
        except Exception:
            return []

        # v3 FIX support: top-level arrays learned as ATOMIC under field_order=["value"]
        if isinstance(obj, list) and field_order == ["value"]:
            return [("value", obj)]

        if isinstance(obj, dict):
            flat_vals = flatten_json_preserve_arrays(obj, field_order)
            return [(path, val) for path, val in zip(field_order, flat_vals)]

        return []

    def rebuild_reply(self, fragment: Dict[str, Any], values: List[Any]) -> str:
        fragment = fragment or {}
        field_order: List[str] = fragment.get("field_order") or []

        if not field_order:
            return "{}"

        # Normalize incoming values into a mapping of field->value
        if values and isinstance(values[0], (tuple, list)) and len(values[0]) == 2:
            valmap = {str(k): v for (k, v) in values}
        else:
            valmap = {
                field_order[i]: values[i]
                for i in range(min(len(field_order), len(values)))
            }

        # v3 FIX: atomic top-level array rebuild
        if field_order == ["value"] and fragment.get("type_map", {}).get("value") == "array":
            arr_val = valmap.get("value")
            if arr_val is None:
                baseline = fragment.get("baseline") or {}
                arr_val = baseline.get("value", [])
            # Enforce list type if possible; otherwise attempt decode if string
            if isinstance(arr_val, list):
                return _dumps(arr_val)
            if isinstance(arr_val, str):
                s = arr_val.strip()
                if not s:
                    return "[]"
                try:
                    j = json.loads(s)
                    if isinstance(j, list):
                        return _dumps(j)
                except Exception:
                    pass
                try:
                    p = ast.literal_eval(s)
                    if isinstance(p, list):
                        return _dumps(p)
                except Exception:
                    pass
            # fail-closed safe default: empty list
            return "[]"

        # -------------------------------
        # OBJECT MODE (atomic arrays enforced)
        # -------------------------------
        type_map: Dict[str, str] = fragment.get("type_map") or {}

        def _coerce_atomic_array(v: Any) -> Any:
            """
            Enforce: atomic array fields must be Python lists (or None).
            If they arrive as JSON text or python-literal text, rehydrate.
            """
            if v is None:
                return None
            if isinstance(v, list):
                return v
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return []
                # Try JSON first
                try:
                    j = json.loads(s)
                    if isinstance(j, list):
                        return j
                except Exception:
                    pass
                # Try python literal as last resort
                try:
                    p = ast.literal_eval(s)
                    if isinstance(p, list):
                        return p
                except Exception:
                    pass
            # Anything else is a hard violation; fail closed by returning empty list
            return []

        # Split arrays vs scalars
        array_fields: Dict[str, Any] = {}
        scalar_field_order: List[str] = []
        baseline_blobs: Dict[str, str] = fragment.get("baseline_blobs") or {}

        for p in field_order:
            if type_map.get(p) == "array":
                arr_val = _coerce_atomic_array(valmap.get(p))
                # If value is None/empty and we have a blob for this field, load it
                if (arr_val is None or arr_val == []) and p in baseline_blobs:
                    try:
                        data = BlobStore.load(baseline_blobs[p])
                        arr_val = json.loads(data.decode("utf-8"))
                    except Exception:
                        arr_val = []
                array_fields[p] = arr_val
            else:
                scalar_field_order.append(p)

        scalar_values = [valmap.get(p) for p in scalar_field_order]
        obj = rebuild_json(scalar_values, scalar_field_order, type_map)

        # Inject atomic arrays LAST
        for path, arr_val in array_fields.items():
            if arr_val is None:
                continue
            cur = obj
            parts = path.split(".")
            for key in parts[:-1]:
                if key not in cur or not isinstance(cur[key], dict):
                    cur[key] = {}
                cur = cur[key]
            cur[parts[-1]] = arr_val

        return _dumps(obj)

# ---- DEPLOYMENT MARKER ----
print("[json_handler] BUILD=2026-06-03-nonfinite-guard", flush=True)
