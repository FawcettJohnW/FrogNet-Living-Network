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
box_fullsuite.py - SECOND PASS: measure the handlers against the FULL public
language test suites. BOX-ONLY (needs network to fetch corpora once, and
optionally the html5lib package to certify HTML against the WHATWG reference).

This is deliberately NOT wired into run_all.py / the TIER C gate: it depends on
external corpora and an extra package that are not present in the sandbox. It
was authored but NOT executed in-sandbox (sandbox egress is closed); run it on
the box, where the network is on, to get real numbers.

Setup (on a networked host):
    bash simulation/spec_compliance/fetch_corpora.sh          # one-time
    pip install --break-system-packages html5lib              # optional, HTML cert
    python3 -m simulation.spec_compliance.box_fullsuite

Corpora layout (override root with FROGNET_CORPORA=/path):
    <root>/JSONTestSuite/test_parsing/{y_,n_,i_}*.json
    <root>/html5lib-tests/tree-construction/*.dat
    <root>/xmlts/xmlconf/...   (valid/ and not-wf/ *.xml)   [optional]

Scoring:
  JSON  - y_ must round-trip; n_ must reject (empty fragment); i_ informational.
  XML   - valid/ must round-trip C14N-lossless; not-wf/ must reject.
  HTML  - every #data input must round-trip faithfully + idempotently; if
          html5lib is importable, ALSO compare our parse tree to the html5lib
          reference tree (true WHATWG conformance %).
"""
from __future__ import annotations

import os
import sys
import glob

from simulation.spec_compliance import _harness  # path/stubs
from core.json_handler import JsonFormatHandler
from core.xml_handler import XmlFormatHandler
from core.html_handler import HtmlFormatHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
CORPORA = os.environ.get("FROGNET_CORPORA", os.path.join(_HERE, "corpora"))


def _pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


# ---------------------------------------------------------------- JSON --------
def json_fullsuite() -> dict:
    base = os.path.join(CORPORA, "JSONTestSuite", "test_parsing")
    files = sorted(glob.glob(os.path.join(base, "*.json")))
    if not files:
        return {"present": False}
    h = JsonFormatHandler()
    import json as _json
    res = {"y_ok": 0, "y_tot": 0, "n_ok": 0, "n_tot": 0, "i_tot": 0,
           "present": True, "fails": []}
    for f in files:
        name = os.path.basename(f)
        try:
            body = open(f, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        frag = h.learn_reply_template(body)
        accepted = bool(frag.get("field_order")) or body.strip() in ("{}", "[]")
        if name.startswith("y_"):
            res["y_tot"] += 1
            # accept AND round-trip semantically
            try:
                out = h.rebuild_reply(frag, h.extract_reply_dynamic(body, frag))
                ok = accepted and _harness.json_semantic_eq(body, out)
            except Exception:
                ok = False
            res["y_ok"] += 1 if ok else 0
            if not ok:
                res["fails"].append(name)
        elif name.startswith("n_"):
            res["n_tot"] += 1
            ok = not accepted
            res["n_ok"] += 1 if ok else 0
            if not ok:
                res["fails"].append(name)
        else:
            res["i_tot"] += 1
    return res


# ----------------------------------------------------------------- XML --------
def xml_fullsuite() -> dict:
    root = os.path.join(CORPORA, "xmlts")
    files = glob.glob(os.path.join(root, "**", "*.xml"), recursive=True)
    if not files:
        return {"present": False}
    from lxml import etree
    h = XmlFormatHandler()
    res = {"valid_ok": 0, "valid_tot": 0, "notwf_ok": 0, "notwf_tot": 0,
           "present": True, "fails": []}
    for f in files:
        low = f.lower()
        is_valid = "/valid/" in low
        is_notwf = "/not-wf/" in low or "/notwf/" in low
        if not (is_valid or is_notwf):
            continue
        try:
            body = open(f, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        frag = h.learn_reply_template(body)
        accepted = bool(frag.get("field_order"))
        if is_valid:
            res["valid_tot"] += 1
            try:
                out = h.rebuild_reply(frag, h.extract_reply_dynamic(body, frag))
                ok = accepted and etree.canonicalize(body) == etree.canonicalize(out)
            except Exception:
                ok = False
            res["valid_ok"] += 1 if ok else 0
            if not ok:
                res["fails"].append(os.path.basename(f))
        else:
            res["notwf_tot"] += 1
            ok = not accepted
            res["notwf_ok"] += 1 if ok else 0
            if not ok:
                res["fails"].append(os.path.basename(f))
    return res


# ---------------------------------------------------------------- HTML --------
def _parse_dat(path):
    """Yield #data blocks from an html5lib tree-construction .dat file."""
    blocks, cur, key = [], {}, None
    for line in open(path, "r", encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("#"):
            key = line[1:]
            cur.setdefault(key, [])
            if key == "data" and cur.get("data"):
                blocks.append(cur)
                cur = {"data": []}
            continue
        if key is not None:
            cur[key].append(line)
    if cur.get("data"):
        blocks.append(cur)
    for b in blocks:
        yield "\n".join(b.get("data", []))


def _struct(html, parser):
    nodes = parser(html)
    out = []

    def w(e):
        if isinstance(e, str):
            out.append(("#t", e)); return
        out.append((str(e.tag), tuple(sorted(e.attrib.items())),
                    e.text or "", e.tail or ""))
        for c in e:
            w(c)
    for n in nodes:
        w(n)
    return out


def html_fullsuite() -> dict:
    base = os.path.join(CORPORA, "html5lib-tests", "tree-construction")
    files = sorted(glob.glob(os.path.join(base, "*.dat")))
    if not files:
        return {"present": False}
    import lxml.html as LH
    h = HtmlFormatHandler()

    def lxml_parser(s):
        return LH.fragments_fromstring(s, parser=LH.HTMLParser(no_network=True))

    res = {"rt_ok": 0, "rt_tot": 0, "present": True, "fails": [],
           "html5lib_cert": None}

    try:
        import html5lib
        have_h5 = True
    except Exception:
        have_h5 = False

    cert_ok = cert_tot = 0
    for f in files:
        for data in _parse_dat(f):
            res["rt_tot"] += 1
            try:
                frag = h.learn_reply_template(data)
                out1 = h.rebuild_reply(frag, h.extract_reply_dynamic(data, frag))
                frag2 = h.learn_reply_template(out1)
                out2 = h.rebuild_reply(frag2, h.extract_reply_dynamic(out1, frag2))
                faithful = _struct(data, lxml_parser) == _struct(out1, lxml_parser)
                ok = faithful and (out1 == out2)
            except Exception:
                ok = False
            res["rt_ok"] += 1 if ok else 0
            if not ok and len(res["fails"]) < 25:
                res["fails"].append(repr(data[:60]))

            if have_h5:
                cert_tot += 1
                try:
                    # Reference WHATWG tree vs our serialization re-parsed.
                    ref = html5lib.parse(data, treebuilder="lxml")
                    ours = html5lib.parse(out1, treebuilder="lxml")
                    from lxml import etree as ET
                    cert_ok += 1 if ET.tostring(ref) == ET.tostring(ours) else 0
                except Exception:
                    pass
    if have_h5:
        res["html5lib_cert"] = (cert_ok, cert_tot)
    return res


# ---------------------------------------------------------------- main --------
def main() -> int:
    print("== FrogNet codex - FULL public-suite second pass (BOX-ONLY) ==")
    print(f"corpora root: {CORPORA}\n")
    any_present = False

    j = json_fullsuite()
    if j.get("present"):
        any_present = True
        print(f"JSON (JSONTestSuite): y_ {j['y_ok']}/{j['y_tot']} "
              f"({_pct(j['y_ok'], j['y_tot'])}), n_ {j['n_ok']}/{j['n_tot']} "
              f"({_pct(j['n_ok'], j['n_tot'])}), i_ {j['i_tot']} informational")
        if j["fails"]:
            print("  JSON disagreements:", ", ".join(j["fails"][:20]),
                  ("..." if len(j["fails"]) > 20 else ""))
    else:
        print("JSON: corpus absent (run fetch_corpora.sh)")

    x = xml_fullsuite()
    if x.get("present"):
        any_present = True
        print(f"XML  (W3C xmlts): valid {x['valid_ok']}/{x['valid_tot']} "
              f"({_pct(x['valid_ok'], x['valid_tot'])}), not-wf "
              f"{x['notwf_ok']}/{x['notwf_tot']} ({_pct(x['notwf_ok'], x['notwf_tot'])})")
        if x["fails"]:
            print("  XML disagreements:", ", ".join(x["fails"][:20]),
                  ("..." if len(x["fails"]) > 20 else ""))
    else:
        print("XML: corpus absent (run fetch_corpora.sh)")

    ht = html_fullsuite()
    if ht.get("present"):
        any_present = True
        print(f"HTML (html5lib-tests): round-trip {ht['rt_ok']}/{ht['rt_tot']} "
              f"({_pct(ht['rt_ok'], ht['rt_tot'])})")
        if ht.get("html5lib_cert"):
            co, ct = ht["html5lib_cert"]
            print(f"  WHATWG conformance vs html5lib reference: {co}/{ct} "
                  f"({_pct(co, ct)})")
        else:
            print("  (html5lib not importable - install for WHATWG conformance %)")
        if ht["fails"]:
            print(f"  HTML round-trip misses (first {len(ht['fails'])}):")
            for s in ht["fails"]:
                print("    ", s)
    else:
        print("HTML: corpus absent (run fetch_corpora.sh)")

    if not any_present:
        print("\nNo corpora found. On a networked host:\n"
              "  bash simulation/spec_compliance/fetch_corpora.sh\n"
              "then re-run. This harness is informational; it does not gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
