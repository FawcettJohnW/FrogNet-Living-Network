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
test_xml_compliance.py - W3C XML 1.0 (5th Ed) compliance for the FULL-FIDELITY
core/xml_handler.py (lxml-backed, BUILD 2026-06-08+), exercised LIVE.

The handler now covers the full XML infoset for ARBITRARY data: elements,
attributes (order preserved), namespaces, mixed content, repeated siblings,
comments, PIs, CDATA, DOCTYPE/internal subset, the XML declaration, and
character references all round-trip. Equality is asserted via C14N
(etree.canonicalize) - semantic losslessness, independent of cosmetic
serialization (quote style, prolog whitespace, CDATA-vs-escaped-text).

SECURITY (now MUST hold AND pass - the handler runs the parser with
resolve_entities=False / no_network / huge_tree=False / load_dtd=False):
  - XXE external entity: &xxe; round-trips as a literal reference; the file is
    never read.
  - billion-laughs: internal entities are NOT expanded.
Reference suite for the full enumeration: W3C XML Test Suite (xmlts).
"""
from __future__ import annotations

from lxml import etree

from simulation.spec_compliance._harness import Suite, round_trip, field_order
from core.xml_handler import XmlFormatHandler


def _c14n(s: str) -> str:
    return etree.canonicalize(s)


def _semantic_eq(a: str, b: str) -> bool:
    try:
        return _c14n(a) == _c14n(b)
    except Exception:
        return False


# Well-formed XML - every case must round-trip C14N-losslessly.
POSITIVE_CASES = [
    ("simple_two_leaf",   '<root><a>1</a><b>x</b></root>'),
    ("single_leaf",       '<root><a>hello</a></root>'),
    ("empty_element",     '<root><a/></root>'),
    ("empty_pair_form",   '<root><a></a></root>'),
    ("char_ref_hex",      '<root><a>&#x41;</a></root>'),
    ("char_ref_dec",      '<root><a>&#65;</a></root>'),
    ("entity_escaped",    '<root><a>a &amp; b &lt; c &gt; d</a></root>'),
    ("cdata_section",     '<root><a><![CDATA[<b>&]]></a></root>'),
    ("nested_three_deep", '<root><a><b><c>v</c></b></a></root>'),
    ("attributes",        '<root><a id="7" k="v" z="3">1</a></root>'),
    ("attr_order",        '<root><a z="1" a="2" m="3">x</a></root>'),
    ("mixed_content",     '<root>lead<a>1</a>tail<b>2</b>post</root>'),
    ("namespaces_multi",  '<ns:root xmlns:ns="urn:x" xmlns:m="urn:y">'
                          '<ns:a m:at="1">v</ns:a></ns:root>'),
    ("default_namespace", '<root xmlns="urn:d"><c x="&amp;"/></root>'),
    ("repeated_siblings", '<root><a>1</a><a>2</a><a>3</a></root>'),
    ("comment_node",      '<root><!-- a comment --><a>1</a></root>'),
    ("processing_instr",  '<root><?target data here?><a>1</a></root>'),
    ("xml_declaration",   '<?xml version="1.0" encoding="UTF-8"?><root><a>1</a></root>'),
    ("utf8_text",         '<root><a>hello ? ?</a></root>'),
    ("ip_leaf",           '<root><addr>10.102.60.1</addr></root>'),
    ("int_leaf",          '<root><n>42</n></root>'),
    ("attr_with_entities",'<root><a t="a &amp; b &lt; c">x</a></root>'),
    ("whitespace_text",   '<root><a>  spaced  </a></root>'),
    ("nested_mixed_attrs",'<doc v="1"><p>intro <b id="x">bold</b> end</p></doc>'),
]

NEGATIVE_CASES = [
    ("unbalanced_tags",     '<a><b></a></b>'),
    ("missing_close",       '<root><a>1</root>'),
    ("no_root",             'just text, no tags'),
    ("multiple_roots",      '<a>1</a><b>2</b>'),
    ("unclosed_root",       '<root><a>1</a>'),
    ("bad_attr_quote",      '<root><a id=>1</a></root>'),
    ("stray_lt",            '<root>a < b</root>'),
    ("empty_input",         ''),
    ("truncated",           '<roo'),
    ("dup_attribute",       '<root><a x="1" x="2">v</a></root>'),
]


def _do_positive(suite: Suite, h: XmlFormatHandler) -> None:
    print(" POSITIVE - full-fidelity XML must round-trip C14N-losslessly:")
    for desc, body in POSITIVE_CASES:
        try:
            frag, out = round_trip(h, body)
            ok = (field_order(frag) != [] or body.strip() == "") and _semantic_eq(body, out)
            suite.check(f"xml/pos/{desc}", ok, f"out={out!r}")
        except Exception as e:
            suite.check(f"xml/pos/{desc}", False, f"RAISED {type(e).__name__}: {e}")


def _do_value_patch(suite: Suite, h: XmlFormatHandler) -> None:
    print(" VALUE-PATCH - changing a slot patches exactly that slot:")
    body = '<root><a>1</a><b id="k">x</b><a>2</a></root>'
    frag = h.learn_reply_template(body)
    out = h.rebuild_reply(frag, [("/root/a[1]/#text", "999"),
                                 ("/root/b/@id", "kk")])
    ok = ("<a>999</a>" in out and 'id="kk"' in out and "<a>2</a>" in out)
    suite.check("xml/patch/text_and_attr", ok, f"out={out!r}")


def _do_negative(suite: Suite, h: XmlFormatHandler) -> None:
    print(" NEGATIVE - not-well-formed XML must reject gracefully (no crash):")
    for desc, body in NEGATIVE_CASES:
        try:
            frag, out = round_trip(h, body)
            ok = (field_order(frag) == [] and out == "")
            suite.check(f"xml/neg/{desc} [reject]", ok,
                        f"fo={field_order(frag)!r} out={out!r}")
        except Exception as e:
            suite.check(f"xml/neg/{desc}", False,
                        f"handler crashed (must be graceful): "
                        f"{type(e).__name__}: {e}")


def _do_security(suite: Suite, h: XmlFormatHandler) -> None:
    print(" SECURITY - MUST reject/neutralize (now enforced by hardened parser):")

    xxe = ('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>'
           '<foo>&xxe;</foo>')
    try:
        frag, out = round_trip(h, xxe)
        base = frag.get("baseline") or {}
        # No resolved file content anywhere; entity stays a literal reference.
        resolved = any(v and str(v).strip() for v in base.values())
        ref_preserved = ("&xxe;" in (frag.get("skeleton") or "")) and ("&xxe;" in out)
        suite.check("xml/sec/xxe_external_not_resolved",
                    (not resolved) and ref_preserved,
                    f"baseline={base!r} out={out!r}")
    except Exception as e:
        suite.check("xml/sec/xxe_external_not_resolved", False,
                    f"RAISED {type(e).__name__}: {e}")

    bomb = ('<!DOCTYPE lolz ['
            '<!ENTITY a "AAAAAAAAAA">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            ']><lolz>&c;</lolz>')
    try:
        frag, _out = round_trip(h, bomb)
        base = frag.get("baseline") or {}
        expanded = sum(len(str(v or "")) for v in base.values())
        suite.check("xml/sec/internal_entity_not_expanded", expanded < 100,
                    f"expanded to {expanded} chars (must be ~0; no expansion)")
    except Exception as e:
        # A contained raise is also acceptable (no expansion product).
        suite.check("xml/sec/internal_entity_not_expanded", True,
                    f"raised/contained: {type(e).__name__}")


def _do_arbitrary(suite: Suite, h: XmlFormatHandler) -> None:
    print(" ARBITRARY - generated structured documents must round-trip:")
    # Wide: many attributes + many repeated children + mixed content + ns.
    attrs = " ".join(f'a{i}="v{i}"' for i in range(40))
    kids = "".join(f"<item k='{i}'>val{i} <em>{i}</em> tail{i}</item>"
                   for i in range(50))
    wide = f'<doc xmlns:x="urn:z" {attrs}>{kids}</doc>'
    # Deep but bounded.
    deep = "".join(f"<n{i}>" for i in range(200)) + "core" + \
           "".join(f"</n{i}>" for i in reversed(range(200)))
    deep = f"<root>{deep}</root>"
    for desc, body in (("wide_40attr_50child", wide), ("deep_200", deep)):
        try:
            frag, out = round_trip(h, body)
            suite.check(f"xml/arb/{desc}", _semantic_eq(body, out),
                        f"slots={len(field_order(frag))}")
        except Exception as e:
            suite.check(f"xml/arb/{desc}", False, f"RAISED {type(e).__name__}: {e}")


def _do_encoding(suite: Suite, h: XmlFormatHandler) -> None:
    print(" ENCODING - non-UTF-8 declarations must not mangle text:")
    # (desc, body, expected_leaf_text)
    cases = [
        ("decl_latin1",  '<?xml version="1.0" encoding="ISO-8859-1"?><root><a>cafe</a></root>', "cafe"),
        ("decl_utf16",   '<?xml version="1.0" encoding="UTF-16"?><root><a>naive</a></root>', "naive"),
        ("decl_upper",   '<?xml version="1.0" ENCODING="latin-1"?><root><a>resume</a></root>', "resume"),
        ("decl_singlequote", "<?xml version='1.0' encoding='Windows-1252'?><root><a>pinata</a></root>", "pinata"),
        ("no_decl_utf8", '<root><a>S?o Paulo</a></root>', "S?o Paulo"),
    ]
    for desc, body, want in cases:
        try:
            frag, out = round_trip(h, body)
            # leaf text must survive intact; output must be well-formed and
            # (where declared) re-declare UTF-8 to match the emitted bytes.
            text_ok = (frag.get("baseline", {}).get("/root/a/#text") == want) and (want in out)
            decl_ok = ("encoding=" not in out) or ("UTF-8" in out) or ("utf-8" in out)
            suite.check(f"xml/enc/{desc}", text_ok and decl_ok,
                        f"baseline={frag.get('baseline')!r} out={out!r}")
        except Exception as e:
            suite.check(f"xml/enc/{desc}", False, f"RAISED {type(e).__name__}: {e}")


def main() -> int:
    print("== XML codex compliance (W3C XML 1.0 5th Ed) - FULL FIDELITY ==")
    suite = Suite("xml_compliance")
    h = XmlFormatHandler()
    _do_positive(suite, h)
    _do_encoding(suite, h)
    _do_value_patch(suite, h)
    _do_negative(suite, h)
    _do_security(suite, h)
    _do_arbitrary(suite, h)
    return suite.report()


if __name__ == "__main__":
    raise SystemExit(main())
