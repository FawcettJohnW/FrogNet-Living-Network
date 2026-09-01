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
no_mask_scan.py — fail-loud enforcement gate.
Walks a tree and reports every fail-MASKING site. Exit nonzero if any remain.
Masking = a fault is hidden, swallowed, downgraded to a tidy status, coerced,
or replaced with a fallback/backup instead of propagating. Legit try/finally
cleanup and try/except-that-reraises are NOT flagged.
"""
import ast, sys, os

MASK = []

class V(ast.NodeVisitor):
    def __init__(self, path): self.path = path
    def visit_ExceptHandler(self, node):
        body = node.body
        # re-raises somewhere in the handler => NOT masking
        reraises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
        only = body[0] if body else None
        kind = None
        if not reraises:
            if len(body) == 1 and isinstance(only, ast.Pass):
                kind = "swallow:pass"
            elif len(body) == 1 and isinstance(only, ast.Return):
                v = only.value
                if v is None or (isinstance(v, ast.Constant) and v.value in (None,"",0,False)) \
                   or (isinstance(v,(ast.List,ast.Dict,ast.Tuple)) and not getattr(v,'elts',getattr(v,'keys',[1]))):
                    kind = "swallow:return-default"
            elif len(body) == 1 and isinstance(only, ast.Continue):
                kind = "swallow:continue"
            elif not any(isinstance(n, (ast.Raise,)) for n in ast.walk(node)):
                # handler that logs/returns/falls through without re-raising
                kind = "swallow:no-reraise"
        if kind:
            MASK.append((self.path, node.lineno, kind))
        self.generic_visit(node)
    def visit_Call(self, node):
        # errors="replace"/"ignore"
        for kw in node.keywords or []:
            if kw.arg == "errors" and isinstance(kw.value, ast.Constant) and kw.value.value in ("replace","ignore"):
                MASK.append((self.path, node.lineno, f'coerce:errors={kw.value.value}'))
        self.generic_visit(node)

def scan(root):
    for dp,_,fs in os.walk(root):
        if "__pycache__" in dp: continue
        for f in fs:
            if not f.endswith(".py") or f.endswith((".bak",)): continue
            if ".old" in dp: continue
            p = os.path.join(dp,f)
            try:
                V(p).visit(ast.parse(open(p,encoding="utf-8",errors="strict").read()))
            except SyntaxError as e:
                MASK.append((p, e.lineno or 0, "UNPARSEABLE"))
    return MASK

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv)>1 else "."
    scan(root)
    from collections import Counter
    c = Counter(k for _,_,k in MASK)
    for kind,n in c.most_common(): print(f"{n:5d}  {kind}")
    print(f"----- {len(MASK)} masking sites -----")
    sys.exit(1 if MASK else 0)
