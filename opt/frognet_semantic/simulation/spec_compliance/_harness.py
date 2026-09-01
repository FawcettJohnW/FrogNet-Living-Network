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
spec_compliance/_harness.py - shared scaffolding for the codex language
compliance suites (PRIMER 2).

WHAT THIS IS
  Exercises the REAL FrogNet codex handlers (core/json_handler.py,
  core/xml_handler.py, core/html_handler.py, core/text_handler.py) against
  hand-curated positive/negative corpora derived from the language specs.
  Mirrors the FAKE-HERE / REAL-ON-BOX posture of json_codex_tier.py: mysql
  and lz4 are stubbed so the handlers import in-container; the handlers
  themselves are LIVE.

WHAT "COMPLIANCE" MEANS HERE (PRIMER 2 Sec."What compliance means")
  1. Don't crash on well-formed input.
  2. Don't crash / hang / leak on malformed or adversarial input.
  3. Round-trip SEMANTIC content for well-formed input (whitespace and key
     ordering may change; values may not).
  4. Reject or normalize malformed input in a documented, deterministic way.

The bar is "safe and useful," not "byte-perfect." We record ACTUAL behavior,
not aspirational behavior. The one place we do NOT extend that grace is
security-relevant negatives (XXE, entity-expansion DoS, prototype pollution):
those MUST be rejected/neutralized, no documented-as-current exception
(PRIMER 2 Sec.Gotchas, Sec.Success criteria).
"""
from __future__ import annotations

import os
import sys
import json
import math
import types
import zlib
from typing import Any, Callable, List, Tuple

# ---------------------------------------------------------------------------
# Path + stubs - MUST run before importing any core.* module.
# spec_compliance -> simulation -> frognet_semantic (the import root).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SIM)
for _p in (_ROOT, _SIM):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def _install_mysql_stub() -> None:
    if "mysql" in sys.modules:
        return
    m = types.ModuleType("mysql")
    mc = types.ModuleType("mysql.connector")
    me = types.ModuleType("mysql.connector.errors")

    class Error(Exception):
        pass

    me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = Error
    mc.connect = lambda *a, **k: None
    mc.errors = me
    mc.Error = Error
    pooling = types.ModuleType("mysql.connector.pooling")
    pooling.MySQLConnectionPool = object
    mc.pooling = pooling
    m.connector = mc
    for n, mod in (("mysql", m), ("mysql.connector", mc),
                   ("mysql.connector.errors", me),
                   ("mysql.connector.pooling", pooling)):
        sys.modules[n] = mod


def _install_lz4_stub() -> None:
    if "lz4" in sys.modules:
        return
    lz4 = types.ModuleType("lz4")
    frame = types.ModuleType("lz4.frame")
    frame.compress = lambda data: zlib.compress(data, 1)
    frame.decompress = lambda data: zlib.decompress(data)
    lz4.frame = frame
    sys.modules["lz4"] = lz4
    sys.modules["lz4.frame"] = frame


_install_mysql_stub()
_install_lz4_stub()

# ---------------------------------------------------------------------------
# PASS/FAIL framework - same shape as json_codex_tier.check()
# ---------------------------------------------------------------------------


class Suite:
    """One language's compliance run. Tracks gating fails and informational
    (non-gating) findings separately, so a documented codec-layer finding
    doesn't fail a handler-isolation suite, while a security miss does."""

    def __init__(self, name: str):
        self.name = name
        self.fails: List[str] = []
        self.findings: List[str] = []
        self.npass = 0

    def check(self, name: str, cond: Any, detail: str = "") -> bool:
        ok = bool(cond)
        suffix = "" if ok else f"  - {detail}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        if ok:
            self.npass += 1
        else:
            self.fails.append(name)
        return ok

    def finding(self, name: str, detail: str = "") -> None:
        """Record a known, non-gating finding (e.g. a codec-layer bug that
        shadows handler-isolation tests). Reported, never fails the gate."""
        print(f"  [NOTE] {name}" + (f"  - {detail}" if detail else ""))
        self.findings.append(name)

    def report(self) -> int:
        print(f"\n  --- {self.name}: {self.npass} pass, "
              f"{len(self.fails)} fail, {len(self.findings)} finding(s) ---")
        if self.findings:
            print("  findings (non-gating):")
            for f in self.findings:
                print(f"    . {f}")
        if self.fails:
            print(f"  {self.name} FAILED: {self.fails}")
            return 1
        return 0


# ---------------------------------------------------------------------------
# Round-trip pipeline: learn -> extract -> rebuild, exactly as a reply flows.
# rebuild_reply returns a STRING for every handler (the PRIMER's "-> dict" is
# imprecise; JSON emits compact JSON text, XML emits an XML string, etc.).
# ---------------------------------------------------------------------------


def round_trip(handler, body: str) -> Tuple[dict, str]:
    """Returns (fragment, rebuilt_string). Caller asserts on both."""
    frag = handler.learn_reply_template(body)
    dyn = handler.extract_reply_dynamic(body, frag)
    out = handler.rebuild_reply(frag, dyn)
    return frag, out


def field_order(frag: dict) -> list:
    return (frag or {}).get("field_order") or []


# ---------------------------------------------------------------------------
# JSON semantic equality (order-insensitive, whitespace-insensitive).
# NaN/Infinity are intentionally NOT used in positive cases; the handler
# normalizes them to null and that is exercised as a negative/normalize case.
# ---------------------------------------------------------------------------


def _json_norm(x: Any) -> Any:
    if isinstance(x, float):
        # Should never see nan/inf in a positive case; guard anyway.
        return None if not math.isfinite(x) else x
    if isinstance(x, dict):
        return {k: _json_norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json_norm(v) for v in x]
    return x


def json_semantic_eq(a_text: str, b_text: str) -> bool:
    try:
        return _json_norm(json.loads(a_text)) == _json_norm(json.loads(b_text))
    except Exception:
        return False


def run_main(suite_mains: List[Callable[[], int]]) -> int:
    rc = 0
    for fn in suite_mains:
        rc |= fn()
    return 1 if rc else 0
