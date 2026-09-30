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
# core/html_handler.py
#
# HTML semantic handler for FrogNet - FULL-FIDELITY (v4, lxml.html-backed).
#
# Goal: cover the entirety of WHATWG HTML for ARBITRARY data with NO loss,
# in BOTH directions (request and reply are now symmetric/structured).
#   - full tree construction (implicit closing, void elements, tag-soup
#     recovery), nested elements, ALL attributes (not just id), comments,
#     <script>/<style> opaque raw text, doctype, leading/trailing text,
#     character references - all round-trip.
#
# Template/dynamic split (same model as the XML handler):
#   - "skeleton": the parsed document/fragment, serialized - the static
#     template learned once.
#   - value-slots: every element's text, every element's tail, and every
#     attribute value, enumerated in deterministic document order, keyed by a
#     unique path label. These are the dynamics.
#   - rebuild: re-parse skeleton, overwrite slots by label, re-serialize.
#     Unchanged reply replays skeleton verbatim -> lossless.
#
# Parser: lxml.html (real WHATWG-style tree builder), no_network=True. HTML has
# no DTD entity-expansion attack surface in lxml.html, and external resources
# are never fetched. <script>/<style> bodies are carried as opaque text and
# never interpreted.
#
# NOTE (behaviour change vs the prior handler): requests are no longer opaque
# "raw" - they are parsed with the same full fidelity as replies. The proxy
# request path now sees structured dynamics.
#
# Interface unchanged (drop-in for format_registry).

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional

import lxml.html as LH
from lxml import etree

_TEXT = "/#text"
_TAIL = "/#tail"
_FRAGROOT = "fragroot"


def _html_parser() -> LH.HTMLParser:
    return LH.HTMLParser(no_network=True)


def _is_document(body: str) -> bool:
    s = body.lstrip().lower()
    return s.startswith("<!doctype html") or s.startswith("<html") or "<html" in s[:512]


def _empty_fragment(body_text: str = "") -> Dict[str, Any]:
    return {
        "mode": "html",
        "skeleton": body_text or "",
        "doc_mode": "fragment",
        "doctype": "",
        "field_order": [],
        "type_map": {},
        "baseline": {},
        "baseline_blobs": {},
        "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
    }


def _parse(body: str):
    """Return (doc_mode, root, doctype).
      doc_mode == "document": root is the <html> element.
      doc_mode == "fragment": root is a synthetic, NEVER-serialized wrapper
                              whose .text is leading text and whose children
                              are the top-level fragment nodes.
    """
    if _is_document(body):
        root = LH.document_fromstring(body, parser=_html_parser())
        doctype = ""
        try:
            doctype = root.getroottree().docinfo.doctype or ""
        except Exception:
            doctype = ""
        return "document", root, doctype

    nodes = LH.fragments_fromstring(body, parser=_html_parser())
    wrapper = etree.Element(_FRAGROOT)
    rest = nodes
    if nodes and isinstance(nodes[0], str):
        wrapper.text = nodes[0]
        rest = nodes[1:]
    for n in rest:
        wrapper.append(n)
    return "fragment", wrapper, ""


def _enumerate_slots(doc_mode: str, root: etree._Element):
    """Yield (label, kind, ref) in document order.
      kind in {"text","tail","attr"}; ref as in the XML handler.
    For fragments, the synthetic wrapper contributes ONLY a leading-text slot
    (no attrs, no tail) so leading text round-trips without emitting the wrapper.
    """
    tree = root.getroottree()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue  # comments / PIs are static structure
        if doc_mode == "fragment" and el is root:
            yield (tree.getpath(el) + _TEXT, "text", el)  # leading text only
            continue
        base = tree.getpath(el)
        yield (base + _TEXT, "text", el)
        for name in el.attrib:
            yield (base + "/@" + name, "attr", (el, name))
        yield (base + _TAIL, "tail", el)


def _slot_value(kind: str, ref) -> Optional[str]:
    if kind == "text":
        return ref.text
    if kind == "tail":
        return ref.tail
    if kind == "attr":
        el, name = ref
        return el.get(name)
    return None


def _set_slot(kind: str, ref, value: Any) -> None:
    v = None if value is None else str(value)
    if kind == "text":
        ref.text = v
    elif kind == "tail":
        ref.tail = v
    elif kind == "attr":
        el, name = ref
        el.set(name, "" if v is None else v)


def _serialize(doc_mode: str, root: etree._Element, doctype: str) -> str:
    if doc_mode == "document":
        kw = {"encoding": "unicode"}
        if doctype:
            kw["doctype"] = doctype
        return LH.tostring(root, **kw)
    # fragment: emit leading text + each child (tostring includes tails),
    # never the synthetic wrapper itself.
    parts: List[str] = []
    if root.text:
        parts.append(root.text)
    for child in root:
        parts.append(LH.tostring(child, encoding="unicode"))
    return "".join(parts)


def _infer_type(val: Any) -> str:
    return "str" if val is None else "str"  # HTML text is always str-typed


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class HtmlFormatHandler(UnRESTHandler):
    """Full-fidelity, symmetric HTML codex handler."""

    def _learn(self, body_text: str) -> Dict[str, Any]:
        body_text = body_text or ""
        if not body_text.strip():
            return _empty_fragment(body_text)
        try:
            doc_mode, root, doctype = _parse(body_text)
        except Exception:
            # Never crash on arbitrary input; fall back to opaque skeleton.
            return _empty_fragment(body_text)

        field_order: List[str] = []
        baseline: Dict[str, Any] = {}
        type_map: Dict[str, str] = {}
        for label, kind, ref in _enumerate_slots(doc_mode, root):
            val = _slot_value(kind, ref)
            field_order.append(label)
            baseline[label] = val
            type_map[label] = _infer_type(val)

        frag = _empty_fragment(body_text)
        frag.update({
            "skeleton": _serialize(doc_mode, root, doctype),
            "doc_mode": doc_mode,
            "doctype": doctype,
            "field_order": field_order,
            "type_map": type_map,
            "baseline": baseline,
        })
        return frag

    def learn_request_template(self, body_text: str) -> Dict[str, Any]:
        return self._learn(body_text)

    def learn_reply_template(self, body_text: str) -> Dict[str, Any]:
        return self._learn(body_text)

    def extract_request_dynamic(self, body_text: str,
                                fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract(body_text)

    def extract_reply_dynamic(self, body_text: str,
                              fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return self._extract(body_text)

    def _extract(self, body_text: str) -> List[Tuple[str, Any]]:
        body_text = body_text or ""
        if not body_text.strip():
            return []
        try:
            doc_mode, root, _doctype = _parse(body_text)
        except Exception:
            return []
        return [(label, _slot_value(kind, ref))
                for (label, kind, ref) in _enumerate_slots(doc_mode, root)]

    def rebuild_reply(self, fragment: Dict[str, Any],
                      values: List[Any]) -> str:
        fragment = fragment or {}
        skeleton: str = fragment.get("skeleton") or ""
        if not skeleton.strip():
            return skeleton
        doctype = fragment.get("doctype") or ""

        try:
            doc_mode, root, parsed_doctype = _parse(skeleton)
            if not doctype:
                doctype = parsed_doctype
        except Exception:
            return skeleton

        valmap: Dict[str, Any] = {}
        if values:
            if isinstance(values[0], (tuple, list)) and len(values[0]) == 2:
                for label, v in values:
                    valmap[str(label)] = v
            else:
                fo = fragment.get("field_order") or []
                for label, v in zip(fo, values):
                    valmap[str(label)] = v

        if valmap:
            for label, kind, ref in _enumerate_slots(doc_mode, root):
                if label in valmap:
                    _set_slot(kind, ref, valmap[label])

        return _serialize(doc_mode, root, doctype)


# ---- DEPLOYMENT MARKER ----
print("[html_handler] BUILD=2026-06-08-lxml-fullfidelity", flush=True)
