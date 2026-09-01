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
proxy/templates.py

Template keying, fail-closed miss, and training helpers.

Invariant:
- Upsert_by_name templates are keyed by SensorType + MetricName (last segment of SensorName).
- Query-string keys in _STATIC_QUERY_KEYS are part of the template identity (keying).
- All other query-string keys are DYNAMIC - their values change per request and
  must be encoded as url_vals during semantic compression.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode

from proxy.constants import debug
from core.store import TemplateStore
from core.format_registry import detect_request_handler, detect_reply_handler


def get_template_store() -> TemplateStore:
    return TemplateStore()


def _parse_json_body(body: bytes) -> Optional[dict]:
    """[NO_FALLBACK_V1] None means "no body". A body that is present but does
    not parse is a different fact and is now reported.

    Both used to return None, and the caller widens either one to the coarse
    key /api.php?entity=sensor_data&action=upsert_by_name. So a client sending
    corrupt JSON produced a working, plausible, permanently coarse template key
    for that whole class of write, and nothing anywhere recorded that a body had
    failed to parse. The widening still happens - it is the correct key for an
    unkeyable write - but it no longer happens silently.
    """
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as e:
        print(f"[TEMPLATES] body present ({len(body)}B) but not JSON: {e!r} "
              f"- semantic key widened to the un-keyed form", flush=True)
        return None


def _extract_metric_name(sensor_name: Any) -> str:
    if not isinstance(sensor_name, str):
        return ""
    parts = [p for p in sensor_name.strip().split(".") if p]
    return parts[-1] if len(parts) >= 4 else ""


# -- Static vs Dynamic query keys ---------------------------------
#
# STATIC keys define template identity - they are part of the keyed path.
# DYNAMIC keys change per request - they are transmitted as url_vals in
# the semantic diff and reconstructed by the daemon via build_url().
#
# Single source of truth.  Used by normalize_path_for_semantics() and
# extract_dynamic_query_keys() alike.

_STATIC_QUERY_KEYS = frozenset({
    "entity",
    "action",
    "SensorType",
    "NetworkName",
    "FrogID",
    "Mode",
    "SensorName__like",
    "SensorName",
    "SensorID",
    # [DYNAMIC_LIMIT_ORDER_V1] "limit" and "order" removed from the
    # static set - they don't change the response shape (template still
    # matches the JSON structure), only the row count / row order.
    # Keeping them static produced one template per distinct limit/order
    # value, defeating compression.  Now both are sent as dynamic
    # url_vals: req_hash still differentiates them so cache rows don't
    # collide, but request and response templates are shared.
    "CallSign",
})


def normalize_path_for_semantics(path: str) -> Tuple[str, List[str]]:
    """
    Canonicalize URL for template keying and extract dynamic query keys.

    Returns:
      (semantic_path, dynamic_keys)

    semantic_path: URL with only static query keys (for template keying).
    dynamic_keys:  ordered list of query keys NOT in the static set.
                   These must be encoded as url_vals during compression.

    Examples:
      "/propogateNotification.php?event=abc"
        -> ("/propogateNotification.php", ["event"])

      "/api.php?entity=sensors&action=values&SensorName=X&parse=1"
        -> ("/api.php?entity=sensors&action=values", ["SensorName", "parse"])

      "/frognet_echo.php"
        -> ("/frognet_echo.php", [])
    """
    if not path:
        return "/", []

    # [SLASH_NORMALIZE_V1] Collapse runs of slashes BEFORE urlparse.
    # urllib.parse treats a leading `//` as a netloc-prefix
    # (`//host/path` per RFC 3986), so urlparse('//api.php?x=1') puts
    # `api.php` into .netloc and leaves .path empty - exactly the
    # mis-parse that lets `http://host//api.php` and
    # `http://host/api.php` fork the template cache.  Collapse leading
    # slash runs first so the parse sees a single-slashed path.
    while path.startswith("//"):
        path = path[1:]
    # Also collapse any internal double-slashes (rare but possible
    # via path traversal corner cases).
    parsed = urlparse(path)
    raw_p = parsed.path or "/"
    while "//" in raw_p:
        raw_p = raw_p.replace("//", "/")
    if not raw_p.startswith("/"):
        raw_p = "/" + raw_p

    qs = parse_qsl(parsed.query, keep_blank_values=True)

    stable = {}
    dynamic_keys = []
    seen_dynamic = set()

    for k, v in qs:
        if k in _STATIC_QUERY_KEYS:
            stable[k] = v
        elif k not in seen_dynamic:
            seen_dynamic.add(k)
            dynamic_keys.append(k)

    if stable:
        sem_path = raw_p + "?" + urlencode(stable, doseq=False)
    else:
        sem_path = raw_p

    return sem_path, dynamic_keys


def extract_dynamic_query_keys(raw_path: str) -> List[str]:
    """
    Convenience wrapper: return just the dynamic keys from a raw URL path.
    """
    _, dynamic = normalize_path_for_semantics(raw_path)
    return dynamic


def extract_dynamic_query_vals(raw_path: str, keys: List[str]) -> List[Tuple[str, str]]:
    """
    Extract the values for specific dynamic query keys from raw_path.

    Returns ordered (key, value) pairs matching the order of `keys`.
    Missing keys get empty string values.
    """
    if not keys or not raw_path:
        return []
    parsed = urlparse(raw_path)
    if not parsed.query:
        return [(k, "") for k in keys]
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query, keep_blank_values=True)
    result = []
    for k in keys:
        vals = qs.get(k, [])
        result.append((k, vals[0] if vals else ""))
    return result


def canonical_semantic_key(method: str, raw_path: str, body: bytes) -> str:
    base, _ = normalize_path_for_semantics(raw_path)

    if method.upper() != "POST":
        return base
    if not base.startswith("/api.php"):
        return base
    if "entity=sensor_data" not in base or "action=upsert_by_name" not in base:
        return base

    j = _parse_json_body(body)
    if not isinstance(j, dict):
        return "/api.php?entity=sensor_data&action=upsert_by_name"

    sensor_type = j.get("SensorType")
    metric_name = _extract_metric_name(j.get("SensorName"))

    if not isinstance(sensor_type, str) or not sensor_type.strip():
        return "/api.php?entity=sensor_data&action=upsert_by_name"
    sensor_type = sensor_type.strip()

    if not metric_name:
        return f"/api.php?entity=sensor_data&action=upsert_by_name&SensorType={sensor_type}"

    return (
        "/api.php?entity=sensor_data&action=upsert_by_name"
        f"&SensorType={sensor_type}"
        f"&MetricName={metric_name}"
    )


def fail_closed_template_missing(handler, *, corrid: str, method: str, semantic_key: str, target_ip: str, reason: str) -> None:
    msg = (
        "FROGNET TEMPLATE MISSING (FAIL-CLOSED)\n"
        f"corrid={corrid}\n"
        f"method={method}\n"
        f"semantic_key={semantic_key}\n"
        f"target_ip={target_ip}\n"
        f"reason={reason}\n"
        "action=refusing semantic execution\n"
    )
    debug("[Proxy] " + msg.replace("\n", " | "))
    body = msg.encode("utf-8", "replace")
    try:
        handler.send_response(503)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
    except BrokenPipeError:
        pass
    finally:
        handler.close_connection = True


def _first_row_only(obj):
    """Every array in a learned sample cut to its FIRST element.

    [ONE_ROW_IS_THE_SAMPLE_V1] A template exists to record what the fields ARE. One
    row carries every field name; the other hundred and thirty carry the same names
    again and inflate the baseline that gets stored and shipped. Measured on a real
    communicator reply: 1 row -> 1082 bytes, 5 rows -> 2686, and at 25+ the thing only
    stops growing because blobization starts pushing the excess into baseline_blobs.

    Learning-sample only. The response returned to the caller is untouched.
    """
    if isinstance(obj, list):
        return [_first_row_only(obj[0])] if obj else []
    if isinstance(obj, dict):
        return {k: _first_row_only(v) for k, v in obj.items()}
    return obj


def _empty_arrays_in(obj, path="") -> list:
    """Paths of every EMPTY array in a learned baseline, at any depth.

    [NO_TEMPLATE_FROM_AN_EMPTY_ARRAY_V1] An empty array tells you a key exists and
    nothing about what it contains. A baseline built from one reconstructs that
    emptiness for every subsequent reply on the opcode.
    """
    out = []
    if isinstance(obj, list):
        if not obj:
            out.append(path or "<root>")
        else:
            for i, v in enumerate(obj):
                out.extend(_empty_arrays_in(v, f"{path}[{i}]"))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_empty_arrays_in(v, f"{path}.{k}" if path else str(k)))
    return out


def learn_templates_from_real(
    *,
    method: str,
    semantic_path: str,
    req_headers: Dict[str, str],
    req_body: bytes,
    upstream: Dict[str, Any],
    store: TemplateStore,
    raw_path: str = "",
) -> None:
    try:
        resp_body = upstream.get("body") or b""
        if not isinstance(resp_body, (bytes, bytearray)):
            resp_body = str(resp_body).encode("utf-8", "replace")

        print(f"[Proxy TRAIN] learning {method} {semantic_path} resp_body={len(resp_body)}B", flush=True)

        req_handler = detect_request_handler(req_headers, req_body)
        req_text = req_body.decode("utf-8", "replace") if req_body else ""
        req_template = req_handler.learn_request_template(req_text)
        print(f"[Proxy TRAIN] req_handler={req_handler.__class__.__name__} req_template_mode={req_template.get('mode')}", flush=True)

        reply_handler = detect_reply_handler(upstream, resp_body)
        resp_text = resp_body.decode("utf-8", "replace") if resp_body else ""
        # [ONE_ROW_IS_THE_SAMPLE_V1] Learn the SHAPE from one row, not from the whole
        # result set. Field names are what a template is for, and one row has all of
        # them. This does not touch what the caller receives.
        try:
            _sample = json.loads(resp_text)
            _one = _first_row_only(_sample)
            resp_text_for_learning = json.dumps(_one)
        except Exception:
            resp_text_for_learning = resp_text
        resp_template = reply_handler.learn_reply_template(resp_text_for_learning)
        print(f"[Proxy TRAIN] reply_handler={reply_handler.__class__.__name__} resp_template_mode={resp_template.get('mode')} fields={len(resp_template.get('fields') or resp_template.get('field_order') or [])}", flush=True)

        # Inject dynamic URL query keys so the template knows which
        # query-string params to encode/decode as url_vals.
        if raw_path:
            dyn_keys = extract_dynamic_query_keys(raw_path)
            if dyn_keys:
                req_template["url_query_keys"] = dyn_keys
                print(f"[Proxy TRAIN] url_query_keys={dyn_keys} for {method} {semantic_path}", flush=True)

        # [NO_TEMPLATE_FROM_AN_EMPTY_ARRAY_V1] A reply whose array is EMPTY is not a
        # sample of that reply's shape. The baseline learned from
        #     {"ok":true,"rows":[]}
        # has the same field_order and type_map as a populated one -- the difference is
        # that its baseline array is empty -- so it passes every structural check and
        # then reconstructs an empty answer for every later request on that opcode.
        # ok:true, HTTP 200, no error anywhere.
        #
        # Seen on Seattle3: a database clear, then the first communicator read went out
        # while there were no communicator tuples, the template was learned from the
        # empty answer, and from then on every semantic read of that opcode returned
        # rows:[] no matter what the database held. Clearing the SemCache tables did not
        # help, because the poison is the TEMPLATE, not the cache.
        #
        # Refuse to learn from it. No template is stored, so the next request bootstraps
        # RAW again and learns when there is something to learn from. That costs one raw
        # round trip per attempt on an opcode that is genuinely empty, which is the
        # correct price: a template that lies is worse than no template.
        _empty = _empty_arrays_in(resp_template.get("baseline"))
        if _empty:
            print(f"[Proxy TRAIN] REFUSING to store template for {method} "
                  f"{semantic_path}: reply arrays are empty at {_empty} -- an empty "
                  f"array is not a sample of the shape. Will bootstrap RAW again.",
                  flush=True)
            return

        store.store_templates(method, semantic_path, req_template, resp_template)
        print(f"[Proxy TRAIN] stored template for {method} {semantic_path}", flush=True)
    except Exception as e:
        import traceback
        print(f"[Proxy TRAIN] learn FAILED for {method} {semantic_path}: {repr(e)}", flush=True)
        traceback.print_exc()

# ---- DEPLOYMENT MARKER ----
print("[templates] BUILD=2026-03-24-v1", flush=True)
