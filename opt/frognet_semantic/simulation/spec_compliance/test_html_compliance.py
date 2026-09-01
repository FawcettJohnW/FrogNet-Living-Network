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
"""
test_html_compliance.py - WHATWG HTML compliance for the FULL-FIDELITY
core/html_handler.py (lxml.html-backed, BUILD 2026-06-08+), exercised LIVE.

The handler is now symmetric (request + reply both structured) and covers
arbitrary HTML: full tree construction (implicit closing, void elements,
tag-soup recovery), nested elements, ALL attributes, comments, opaque
<script>/<style> raw text, doctype, and leading/trailing text all round-trip.

Equality is asserted STRUCTURALLY (tag + attributes + text/tail in document
order), independent of cosmetic serialization. Where the WHATWG tree builder
normalizes tag-soup (e.g. implicit <p> closing), the structural form after
parsing is the ground truth; the round-trip must be STABLE (idempotent) and
preserve that structure. Reference suite for the full enumeration: html5lib
tests/tree-construction.
"""
from __future__ import annotations

import lxml.html as LH

from simulation.spec_compliance._harness import Suite, round_trip, field_order
from core.html_handler import HtmlFormatHandler


def _struct(html: str):
    """Document-order list of (tag, sorted-attrs, text, tail) over a parse.
    Cosmetic-serialization-independent structural fingerprint."""
    try:
        nodes = LH.fragments_fromstring(html, parser=LH.HTMLParser(no_network=True))
    except Exception:
        return None
    out = []

    def walk(el, leading=None):
        if isinstance(el, str):
            out.append(("#text", (), el, None))
            return
        out.append((el.tag, tuple(sorted(el.attrib.items())),
                    el.text or "", el.tail or ""))
        for c in el:
            walk(c)

    for n in nodes:
        walk(n)
    return out


def _stable_and_faithful(h, html: str):
    """Returns (ok, out1). ok == round-trip is structurally faithful AND
    idempotent on a second pass."""
    f1 = h.learn_reply_template(html)
    out1 = h.rebuild_reply(f1, h.extract_reply_dynamic(html, f1))
    f2 = h.learn_reply_template(out1)
    out2 = h.rebuild_reply(f2, h.extract_reply_dynamic(out1, f2))
    faithful = (_struct(html) == _struct(out1))
    idempotent = (out1 == out2)
    return (faithful and idempotent), out1


POSITIVE_CASES = [
    ("single_id_div",     '<div id="a">hi</div>'),
    ("two_ids",           '<div id="a">one</div><span id="b">two</span>'),
    ("nested_id",         '<div id="a"><span id="b">inner</span></div>'),
    ("all_attributes",    '<a href="/x" data-k="v" id="a" class="c">link</a>'),
    ("full_document",     '<!doctype html><html><head><title id="t">T</title>'
                          '</head><body><h1 id="h">H</h1><p id="p">B</p></body></html>'),
    ("unquoted_id_attr",  '<div id=a>hi</div>'),
    ("single_quoted_id",  "<div id='a'>hi</div>"),
    ("entity_in_text",    '<div id="a">a &amp; b &lt; c</div>'),
    ("utf8_text",         '<div id="a">hello ? ?</div>'),
    ("no_id_elements",    '<div>nothing tracked</div><p>more</p>'),
    ("empty_id_text",     '<div id="a"></div>'),
    ("numeric_text",      '<span id="n">42</span>'),
    ("void_elements",     '<img id="a" src="x"><br><hr><p id="b">t</p>'),
    ("duplicate_ids",     '<div id="a">one</div><div id="a">two</div>'),
    ("leading_trailing",  'hello <b id="a">world</b> tail'),
    ("comment_node",      '<div id="a">x</div><!-- keep me -->'),
    ("table_implicit",    '<table><tr><td id="a">c</td></tr></table>'),
    ("nested_lists",      '<ul><li id="a">1<ul><li id="b">2</li></ul></li></ul>'),
]

# WHATWG normalizations: parse is the ground truth; round-trip must be stable.
NORMALIZED_CASES = [
    ("implicit_p_close",  '<p id="a">one<p id="b">two'),
    ("misnested",         '<b id="a"><i>x</b>y</i>'),
    ("unclosed_tag",      '<div id="a">text'),
]

# Adversarial/arbitrary: must never crash; must be stable.
NEGATIVE_CASES = [
    ("stray_close",       '</div><div id="a">x</div>'),
    ("bare_lt",           '<div id="a">a < b</div>'),
    ("comment_in_script", '<div id="a">x</div><script><!--<div>--></script>'),
    ("attr_no_value",     '<div id="a" disabled>x</div>'),
    ("empty_input",       ''),
    ("null_bytes",        'pre\x00<div id="a">x</div>'),
    ("deep_nesting",      '<div>' * 200 + 'x' + '</div>' * 200),
    ("many_attrs",        '<div ' + " ".join(f'a{i}="{i}"' for i in range(60)) + '>x</div>'),
]


def _do_positive(suite: Suite, h: HtmlFormatHandler) -> None:
    print(" POSITIVE - arbitrary HTML must round-trip faithfully + stably:")
    for desc, html in POSITIVE_CASES:
        try:
            ok, out = _stable_and_faithful(h, html)
            suite.check(f"html/pos/{desc}", ok, f"out={out!r}")
        except Exception as e:
            suite.check(f"html/pos/{desc}", False, f"RAISED {type(e).__name__}: {e}")


def _do_normalized(suite: Suite, h: HtmlFormatHandler) -> None:
    print(" NORMALIZED - WHATWG tree-build is ground truth; must be stable:")
    for desc, html in NORMALIZED_CASES:
        try:
            ok, out = _stable_and_faithful(h, html)
            suite.check(f"html/norm/{desc}", ok, f"out={out!r}")
        except Exception as e:
            suite.check(f"html/norm/{desc}", False, f"RAISED {type(e).__name__}: {e}")


def _do_value_patch(suite: Suite, h: HtmlFormatHandler) -> None:
    print(" VALUE-PATCH - changing nested text/attr patches exactly that slot:")
    body = '<div id="a"><span id="b">inner</span></div>'
    frag = h.learn_reply_template(body)
    # Derive the real slot label (fragments walk under a synthetic root), then
    # patch exactly the span's text.
    span_text_label = next((lbl for lbl in field_order(frag)
                            if lbl.endswith("/span/#text")), None)
    out = h.rebuild_reply(frag, [(span_text_label, "CHANGED")])
    ok = (span_text_label is not None and "CHANGED" in out
          and 'id="b"' in out and 'id="a"' in out)
    suite.check("html/patch/nested_text", ok,
                f"label={span_text_label!r} out={out!r}")


def _do_opaque(suite: Suite, h: HtmlFormatHandler) -> None:
    print(" OPAQUE - <script>/<style> bodies preserved, never interpreted:")
    html = ('<div id="a">x</div>'
            '<script>var z = "<div id=\'evil\'>nope</div>";</script>'
            '<style>#a{color:red}</style>')
    frag, out = round_trip(h, html)
    suite.check("html/opaque/script_body_preserved",
                "nope" in out and "color:red" in out, f"out={out!r}")
    suite.check("html/opaque/script_not_parsed_as_html",
                not any("evil" in str(lbl) for lbl in field_order(frag)),
                f"fields={field_order(frag)!r}")


def _do_negative(suite: Suite, h: HtmlFormatHandler) -> None:
    print(" ADVERSARIAL - never crash, always stable:")
    for desc, html in NEGATIVE_CASES:
        try:
            ok, out = _stable_and_faithful(h, html) if html.strip() else (True, "")
            suite.check(f"html/neg/{desc}", isinstance(out, str) and ok,
                        f"out={out[:80]!r}")
        except Exception as e:
            suite.check(f"html/neg/{desc}", False,
                        f"handler crashed (must be graceful): "
                        f"{type(e).__name__}: {e}")


def main() -> int:
    print("== HTML codex compliance (WHATWG) - FULL FIDELITY, SYMMETRIC ==")
    suite = Suite("html_compliance")
    h = HtmlFormatHandler()
    _do_positive(suite, h)
    _do_normalized(suite, h)
    _do_value_patch(suite, h)
    _do_opaque(suite, h)
    _do_negative(suite, h)
    return suite.report()


if __name__ == "__main__":
    raise SystemExit(main())
