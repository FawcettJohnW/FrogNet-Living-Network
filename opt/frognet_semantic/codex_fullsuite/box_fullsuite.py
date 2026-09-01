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
box_fullsuite.py — SELF-CONTAINED second-pass corpus scorer for the FrogNet
codex handlers. Drops in WITHOUT overwriting any existing file: it lives in its
own directory and depends only on the deployed `core/*` handlers plus lxml
(and, optionally, html5lib for the WHATWG conformance %).

It measures WHATEVER handlers are currently deployed in core/ — it does not
ship or modify them. Run it on a NETWORKED host:

    bash fetch_corpora.sh                       # one-time, into ./corpora
    pip install --break-system-packages html5lib   # optional (HTML cert %)
    python3 box_fullsuite.py

Corpora root: ./corpora next to this file, or $FROGNET_CORPORA. Layout:
    <root>/JSONTestSuite/test_parsing/{y_,n_,i_}*.json
    <root>/html5lib-tests/tree-construction/*.dat
    <root>/xmlts/**/*.xml           (valid/ and not-wf/)   [optional]

Scoring:
  JSON  — y_ must round-trip; n_ must reject (empty fragment); i_ informational.
  XML   — valid/ must round-trip C14N-lossless; not-wf/ must reject.
  HTML  — every #data input must round-trip faithfully + idempotently; if
          html5lib is importable, ALSO compare our re-parsed tree to the
          html5lib reference tree (true WHATWG conformance %).

Informational only — does not gate.
"""
from __future__ import annotations

import os
import sys
import glob
import math
import json
import types
import zlib

# --- self-contained bootstrap: find the frognet_semantic root (the dir that
#     contains core/) by walking upward from this file, then stub mysql + lz4
#     so the handlers import in any environment. No other project files needed.
_HERE = os.path.dirname(os.path.abspath(__file__))
CORPORA = os.environ.get("FROGNET_CORPORA", os.path.join(_HERE, "corpora"))


def _find_root(start: str) -> str:
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "core")) and \
           os.path.isfile(os.path.join(d, "core", "json_handler.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return start


_ROOT = _find_root(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _stub(name, build):
    if name in sys.modules:
        return
    build()


def _install_stubs():
    if "mysql" not in sys.modules:
        m = types.ModuleType("mysql"); mc = types.ModuleType("mysql.connector")
        me = types.ModuleType("mysql.connector.errors")

        class Error(Exception):
            pass
        me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = Error
        mc.connect = lambda *a, **k: None
        mc.errors = me; mc.Error = Error
        pooling = types.ModuleType("mysql.connector.pooling")
        pooling.MySQLConnectionPool = object
        mc.pooling = pooling; m.connector = mc
        for n, mod in (("mysql", m), ("mysql.connector", mc),
                       ("mysql.connector.errors", me),
                       ("mysql.connector.pooling", pooling)):
            sys.modules[n] = mod
    if "lz4" not in sys.modules:
        lz4 = types.ModuleType("lz4"); frame = types.ModuleType("lz4.frame")
        frame.compress = lambda data: zlib.compress(data, 1)
        frame.decompress = lambda data: zlib.decompress(data)
        lz4.frame = frame
        sys.modules["lz4"] = lz4; sys.modules["lz4.frame"] = frame


_install_stubs()

import warnings as _warnings
_warnings.filterwarnings('ignore')

from core.json_handler import JsonFormatHandler          # noqa: E402
from core.xml_handler import XmlFormatHandler             # noqa: E402
from core.html_handler import HtmlFormatHandler           # noqa: E402


def _pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def _json_norm(x):
    if isinstance(x, float):
        return None if not math.isfinite(x) else x
    if isinstance(x, dict):
        return {k: _json_norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json_norm(v) for v in x]
    return x


def _json_eq(a_text, b_text):
    try:
        return _json_norm(json.loads(a_text)) == _json_norm(json.loads(b_text))
    except Exception:
        return False


# ---------------------------------------------------------------- JSON --------
def json_fullsuite():
    base = os.path.join(CORPORA, "JSONTestSuite", "test_parsing")
    files = sorted(glob.glob(os.path.join(base, "*.json")))
    if not files:
        return {"present": False}
    h = JsonFormatHandler()
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
            try:
                out = h.rebuild_reply(frag, h.extract_reply_dynamic(body, frag))
                ok = accepted and _json_eq(body, out)
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
def _xml_manifest_cases(root):
    """Read every W3C xmlts manifest (xmlconf.xml). Return {abspath: TYPE},
    TYPE in valid|invalid|not-wf|error, resolving xml:base down each TESTCASES
    chain. Authoritative classification — not path-guessing. Reference-output
    files (out/) and the manifests themselves are never cases."""
    import glob as _glob
    from lxml import etree
    XMLB = "{http://www.w3.org/XML/1998/namespace}base"
    cases = {}
    p = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                        recover=True, huge_tree=False)
    for mf in _glob.glob(os.path.join(root, "**", "xmlconf.xml"), recursive=True):
        mdir = os.path.dirname(mf)
        try:
            tree = etree.parse(mf, p)
        except Exception:
            continue

        def walk(node, base):
            b = node.get(XMLB) or ""
            base = os.path.normpath(os.path.join(base, b)) if b else base
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                tag = etree.QName(child).localname
                if tag == "TESTCASES":
                    walk(child, base)
                elif tag == "TEST":
                    uri, typ = child.get("URI"), child.get("TYPE")
                    if uri and typ:
                        cases[os.path.normpath(os.path.join(mdir, base, uri))] = typ
        walk(tree.getroot(), "")
    return cases


def _dtd_related(body):
    b = body.lstrip()[:4096].upper()
    return ("<!DOCTYPE" in b or "<!ENTITY" in b or "<!ATTLIST" in b
            or "<!ELEMENT" in b or "%" in body[:4096])


def xml_fullsuite():
    root = os.path.join(CORPORA, "xmlts")
    if not os.path.isdir(root):
        return {"present": False}
    cases = _xml_manifest_cases(root)
    if not cases:
        return {"present": False}
    from lxml import etree
    h = XmlFormatHandler()
    res = {"present": True,
           "wf_ok": 0, "wf_tot": 0, "wf_fails": [],
           "nwf_reject": 0, "nwf_tot": 0, "nwf_acc_dtd": 0, "nwf_acc_other": 0,
           "nwf_other_list": [], "err_tot": 0}
    for path, typ in cases.items():
        if not os.path.isfile(path):
            continue
        try:
            body = open(path, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        frag = h.learn_reply_template(body)
        accepted = bool(frag.get("field_order"))
        if typ in ("valid", "invalid"):     # both are WELL-FORMED -> must round-trip
            res["wf_tot"] += 1
            try:
                out = h.rebuild_reply(frag, h.extract_reply_dynamic(body, frag))
                ok = accepted and etree.canonicalize(body) == etree.canonicalize(out)
            except Exception:
                ok = False
            res["wf_ok"] += 1 if ok else 0
            if not ok and len(res["wf_fails"]) < 30:
                res["wf_fails"].append(os.path.basename(path))
        elif typ == "not-wf":               # ideally rejected
            res["nwf_tot"] += 1
            if not accepted:
                res["nwf_reject"] += 1
            elif _dtd_related(body):
                res["nwf_acc_dtd"] += 1     # accepted: needs DTD processing we disable
            else:
                res["nwf_acc_other"] += 1   # accepted: genuine miss
                if len(res["nwf_other_list"]) < 30:
                    res["nwf_other_list"].append(os.path.basename(path))
        else:                               # error: optional to detect
            res["err_tot"] += 1
    return res


# ---------------------------------------------------------------- HTML --------
def _parse_dat(path):
    blocks, cur, key = [], {"data": []}, None
    for line in open(path, "r", encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("#"):
            key = line[1:]
            if key == "data" and cur.get("data"):
                blocks.append(cur); cur = {"data": []}
            else:
                cur.setdefault(key, [])
            continue
        if key is not None:
            cur.setdefault(key, []).append(line)
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


def html_fullsuite():
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
                    from lxml import etree as ET
                    ref = html5lib.parse(data, treebuilder="lxml")
                    ours = html5lib.parse(out1, treebuilder="lxml")
                    cert_ok += 1 if ET.tostring(ref) == ET.tostring(ours) else 0
                except Exception:
                    pass
    if have_h5:
        res["html5lib_cert"] = (cert_ok, cert_tot)
    return res


# ---------------------------------------------------------------- main --------
def main():
    print("== FrogNet codex — FULL public-suite second pass (self-contained) ==")
    print(f"root: {_ROOT}\ncorpora: {CORPORA}\n")
    any_present = False

    j = json_fullsuite()
    if j.get("present"):
        any_present = True
        print(f"JSON (JSONTestSuite): y_ {j['y_ok']}/{j['y_tot']} "
              f"({_pct(j['y_ok'], j['y_tot'])}), n_ {j['n_ok']}/{j['n_tot']} "
              f"({_pct(j['n_ok'], j['n_tot'])}), i_ {j['i_tot']} informational")
        if j["fails"]:
            print("  JSON disagreements:", ", ".join(j["fails"][:20]),
                  ("…" if len(j["fails"]) > 20 else ""))
    else:
        print("JSON: corpus absent (run fetch_corpora.sh)")

    x = xml_fullsuite()
    if x.get("present"):
        any_present = True
        print(f"XML  (W3C xmlts, manifest-driven):")
        print(f"  well-formed (valid+invalid) round-trip: {x['wf_ok']}/{x['wf_tot']} "
              f"({_pct(x['wf_ok'], x['wf_tot'])})")
        acc = x['nwf_acc_dtd'] + x['nwf_acc_other']
        print(f"  not-wf rejected: {x['nwf_reject']}/{x['nwf_tot']} "
              f"({_pct(x['nwf_reject'], x['nwf_tot'])}); accepted {acc} "
              f"= {x['nwf_acc_dtd']} DTD-dependent (parser hardened: DTD/entity "
              f"processing OFF for XXE/bomb safety) + {x['nwf_acc_other']} other")
        print(f"  error cases (optional to detect): {x['err_tot']} informational")
        if x['wf_fails']:
            print("  WF round-trip misses:", ", ".join(x['wf_fails'][:20]),
                  ("…" if len(x['wf_fails']) > 20 else ""))
        if x['nwf_other_list']:
            print("  not-wf accepted (non-DTD, genuine misses):",
                  ", ".join(x['nwf_other_list'][:20]),
                  ("…" if len(x['nwf_other_list']) > 20 else ""))
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
            print("  (html5lib not importable — install for WHATWG conformance %)")
        if ht["fails"]:
            print(f"  HTML round-trip misses (first {len(ht['fails'])}):")
            for s in ht["fails"]:
                print("    ", s)
    else:
        print("HTML: corpus absent (run fetch_corpora.sh)")

    if not any_present:
        print("\nNo corpora found. Run:  bash fetch_corpora.sh   (next to this file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
