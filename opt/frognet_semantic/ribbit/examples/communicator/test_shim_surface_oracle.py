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
test_shim_surface_oracle.py -- [A_SHIM_MUST_BE_USABLE_BEFORE_IT_FINISHES_V1]

frognet_tuples.py in the bundle is a shim onto core.frognet_tuples. It aliased
itself with `sys.modules[__name__] = _m`, and that swap only takes effect when
the module finishes executing -- so anything importing frognet_tuples DURING
that window binds the half-built shim object, which has os, sys and _CORE and
nothing else. The swap does not reach back into a name already bound.

Measured 2026-08-11 on the relay, every two seconds for the life of the process:

    [relay] MediaHold publish failed: AttributeError(
            "module 'frognet_tuples' has no attribute 'put'")
    [relay] call reap failed: AttributeError(
            "module 'frognet_tuples' has no attribute 'my_ip'")

sim_tuple_roundtrip proved the round trip -- against core.frognet_tuples
directly. It never imported the shim, which is what every caller in the bundle
imports, so the one module standing between the code and the store was the one
module nothing tested.

  S1  the shim exposes the surface the bundle actually calls
  S2  a binding taken MID-IMPORT is usable, not just a later one
  S3  the alias still happens, so importers share one module and its state
  S4  every name the bundle reaches for by hand is present
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


# ---- S4: what the bundle actually calls on this module ---------------------
WANT = set()
_pat = re.compile(r"\b(?:_T|T|_tuples|frognet_tuples)\.([A-Za-z_][A-Za-z0-9_]*)")
for fn in sorted(os.listdir(HERE)):
    if not fn.endswith(".py") or fn.startswith("test_") or fn.startswith("sim_"):
        continue
    try:
        src = open(os.path.join(HERE, fn), encoding="utf-8").read()
    except OSError:
        continue
    if "frognet_tuples" not in src:
        continue
    WANT |= set(_pat.findall(src))
WANT -= {"my_ip"}          # checked explicitly below, and always wanted
# "frognet_tuples.py" in prose matches the attribute pattern. A file extension
# is not a module attribute.
WANT -= {"py"}
WANT = {w for w in WANT if not w.startswith("__")}

# ---- S2: a binding taken mid-import ----------------------------------------
spec = importlib.util.spec_from_file_location(
    "frognet_tuples", os.path.join(HERE, "frognet_tuples.py"))
early = importlib.util.module_from_spec(spec)
sys.modules["frognet_tuples"] = early       # what an in-flight importer sees
try:
    spec.loader.exec_module(early)
    loaded = True
except Exception as e:
    loaded = False
    print("  FAIL  the shim did not load: %r" % (e,))
    FAIL.append("shim load")

if loaded:
    ck("S1 the shim exposes put", hasattr(early, "put"))
    ck("S1 and get", hasattr(early, "get"))
    ck("S2 my_ip -- the one the reaper died on", hasattr(early, "my_ip"))
    ck("S2 _values_raw", hasattr(early, "_values_raw"))
    ck("S2 _delete_by_id", hasattr(early, "_delete_by_id"))

    missing = sorted(w for w in WANT if not hasattr(early, w))
    ck("S4 every name the bundle reaches for is present on a mid-import "
       "binding", not missing, missing)

    # ---- S3 -----------------------------------------------------------------
    import frognet_tuples as later
    ck("S3 the alias still happens -- one module, shared state",
       getattr(later, "__name__", "") == "core.frognet_tuples"
       or later is sys.modules.get("core.frognet_tuples"),
       getattr(later, "__name__", "?"))
    ck("S3 and the late binding has the surface too",
       hasattr(later, "put") and hasattr(later, "my_ip"), None)

    # Order checked on the CODE, not the text: the docstring quotes
    # `sys.modules[__name__] = _m` while explaining the defect, and a plain
    # index() finds the explanation before the statement. Third time prose has
    # broken one of my assertions today.
    import ast as _ast
    _mod = _ast.parse(open(os.path.join(HERE, "frognet_tuples.py"),
                           encoding="utf-8").read())
    _copy_at = _alias_at = None
    for _n in _ast.walk(_mod):
        if isinstance(_n, _ast.Call) and getattr(_n.func, "attr", "") == "update" \
                and getattr(getattr(_n.func, "value", None), "func", None) is not None \
                and getattr(_n.func.value.func, "id", "") == "globals":
            _copy_at = _n.lineno
        if isinstance(_n, _ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, _ast.Subscript) and \
                        getattr(getattr(_t.value, "attr", None), "__str__", None) \
                        and getattr(_t.value, "attr", "") == "modules":
                    _alias_at = _n.lineno
    ck("S2 the copy happens BEFORE the alias -- the alias alone has the hole",
       _copy_at is not None and _alias_at is not None and _copy_at < _alias_at,
       (_copy_at, _alias_at))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
