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
test_names_oracle.py -- no function references a name nothing defines.

Written because a NameError shipped. `have_cam` is a local of Call._probe();
it was referenced inside Call.run(), which compiles clean, passes every unit
oracle, and dies the moment the branch is reached:

    File "/etc/frognet_bundles/communicator/fnav.py", line 4447, in run
      if self.want_video and have_cam and not self.no_audio:
    NameError: name 'have_cam' is not defined

py_compile cannot catch it -- it is valid syntax. The unit oracles cannot catch
it -- they drive the state machines and the encoder directly and never enter
run(). checksite compiled the file and ran the oracles and reported OK.

So the gap is structural, not an oversight: nothing in the bundle executes the
long-lived entry points, and nothing ever will in a test that has no camera, no
libav and no relay. A static name check is the thing that closes it, and it
closes the whole class rather than this one instance.

Walks every function in the named modules and reports any Name load that is not
a local, parameter, comprehension target, global, class attribute, builtin, or
import.
"""
import ast, builtins, os, sys

MODULES = ("fnav.py", "communicator_live.py", "comms_control.py", "comms_ui.py")
HERE = os.path.dirname(os.path.abspath(__file__)) or "."

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


class Scope:
    def __init__(self, parent=None, kind="module"):
        self.names = set()
        self.parent = parent
        self.kind = kind

    def defines(self, n):
        s = self
        while s is not None:
            if n in s.names:
                return True
            # a class body's names are NOT visible to nested functions
            s = s.parent
            while s is not None and s.kind == "class":
                s = s.parent
        return False


def bound_names(node, out):
    """Every name this statement binds IN THIS SCOPE.

    Deliberately does NOT descend into a nested function or class body. Walking
    the whole subtree put every function's locals into the module scope, so a
    local of Call._probe() looked like a global and a reference to it from
    Call.run() checked out clean -- which is exactly the bug that shipped. A
    checker that collects too much reports nothing and is worse than none.
    """
    STOP = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def walk(n, top=False):
        if isinstance(n, STOP) and hasattr(n, "name"):
            # a def binds its own name in the ENCLOSING scope, whether or not we
            # descend into it. Missing this made every top-level function
            # invisible to callers below it.
            out.add(n.name)
        if not top and isinstance(n, STOP):
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        for c in ast.iter_child_nodes(n):
            walk(c)

    walk(node, top=True)


def check_module(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    mod = Scope(None, "module")
    for st in tree.body:
        bound_names(st, mod.names)
    mod.names.update(dir(builtins))
    mod.names.add("__file__"); mod.names.add("__name__")
    bad = []

    def fn_scope(fn, parent):
        sc = Scope(parent, "function")
        args = fn.args
        for a in (list(getattr(args, "posonlyargs", [])) + list(args.args)
                  + list(args.kwonlyargs)):
            sc.names.add(a.arg)
        if args.vararg:
            sc.names.add(args.vararg.arg)
        if args.kwarg:
            sc.names.add(args.kwarg.arg)
        for st in getattr(fn, "body", []) if isinstance(fn.body, list) else []:
            bound_names(st, sc.names)
        return sc

    NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

    def walk_fn(fn, parent):
        """Check one function, recursing into nested defs with their own scope.

        Nested functions are handled separately rather than by ast.walk: a
        nested def's PARAMETERS are not in the outer scope, so scanning the
        outer function's whole subtree for Name loads reports every callback
        argument as undefined. That noise is what makes a checker get ignored.
        """
        sc = fn_scope(fn, parent)
        body = fn.body if isinstance(fn.body, list) else [fn.body]

        def handle(node):
            # Test the node ITSELF before descending. Testing only its children
            # meant a nested `def` appearing as a direct statement was never
            # recognised as nested, so its parameters were checked against the
            # OUTER scope and every callback argument came back undefined.
            if isinstance(node, NESTED):
                walk_fn(node, sc)
                return
            if isinstance(node, ast.ClassDef):
                cs = Scope(sc, "class")
                for st in node.body:
                    bound_names(st, cs.names)
                for st in node.body:
                    handle(st) if not isinstance(st, NESTED) else walk_fn(st, cs)
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if not sc.defines(node.id):
                    bad.append((os.path.basename(path), node.lineno,
                                fn_name(fn), node.id))
            for child in ast.iter_child_nodes(node):
                handle(child)

        for st in body:
            handle(st)

    def fn_name(fn):
        return getattr(fn, "name", "<lambda>")

    def walk(node, scope):
        for n in node.body:
            if isinstance(n, ast.ClassDef):
                cs = Scope(scope, "class")
                for st in n.body:
                    bound_names(st, cs.names)
                walk(n, cs)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk_fn(n, scope)
            elif hasattr(n, "body") and not isinstance(n, ast.Expr):
                walk(n, scope)

    walk(tree, mod)
    # de-dup: one report per (file, function, name)
    seen, uniq = set(), []
    for f, ln, fn, nm in bad:
        k = (f, fn, nm)
        if k not in seen:
            seen.add(k); uniq.append((f, ln, fn, nm))
    return uniq


for m in MODULES:
    p = os.path.join(HERE, m)
    if not os.path.isfile(p):
        ck("present: %s" % m, False, "missing")
        continue
    try:
        bad = check_module(p)
    except SyntaxError as e:
        ck("parses: %s" % m, False, str(e))
        continue
    ck("%s: no undefined names" % m, not bad,
       "; ".join("%s:%d %s() -> %s" % b for b in bad[:8]))

# the specific regression, named
src = open(os.path.join(HERE, "fnav.py"), encoding="utf-8").read()
ck("fnav.run() uses self.have_cam, not the bare local",
   "and self.have_cam and not self.no_audio" in src
   and "and have_cam and not self.no_audio" not in src)


# ---- [SELF_CALLS_MUST_EXIST_V1] --------------------------------------------
# The walker above catches bare names. `self._rung_stepped_down(...)` is an
# ATTRIBUTE reference, so it passed -- and the method it called had been removed
# in a rewrite. The send loop died on the first step down with an
# AttributeError, inside a thread, so the process carried on with no video and
# no exit code. Measured 2026-08-11.
#
# Every self.X(...) call in a class must resolve to something that class or a
# base defines. Attributes assigned anywhere in the class count; so do class
# attributes and anything inherited.
import ast as _ast

_missing = []
for _path in ("fnav.py", "comms_control.py", "comms_ui.py"):
    try:
        _tree = _ast.parse(open(_path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        continue
    for _cls in [n for n in _ast.walk(_tree) if isinstance(n, _ast.ClassDef)]:
        _defined = set()
        for _n in _ast.walk(_cls):
            if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                _defined.add(_n.name)
            elif isinstance(_n, _ast.Attribute) and isinstance(
                    _n.ctx, _ast.Store) and isinstance(_n.value, _ast.Name) \
                    and _n.value.id == "self":
                _defined.add(_n.attr)
            elif isinstance(_n, _ast.Assign):
                for _t in _n.targets:
                    if isinstance(_t, _ast.Name):
                        _defined.add(_t.id)
            elif isinstance(_n, _ast.AnnAssign) and isinstance(_n.target, _ast.Name):
                _defined.add(_n.target.id)
        # Inherited members are not visible here, so only flag classes with no
        # base beyond object -- otherwise this reports the parent's methods.
        _bases = [b for b in _cls.bases
                  if not (isinstance(b, _ast.Name) and b.id == "object")]
        if _bases:
            continue
        for _n in _ast.walk(_cls):
            if (isinstance(_n, _ast.Call)
                    and isinstance(_n.func, _ast.Attribute)
                    and isinstance(_n.func.value, _ast.Name)
                    and _n.func.value.id == "self"
                    and _n.func.attr not in _defined):
                _missing.append("%s:%d %s.self.%s()"
                                % (_path, _n.lineno, _cls.name, _n.func.attr))

ck("every self.X() call resolves to something the class defines",
   not _missing, _missing[:6])

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
