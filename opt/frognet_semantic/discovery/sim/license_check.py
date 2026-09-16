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
sim/license_check.py - [ONE_LICENSE_HEADER_V1] every source file carries the
GPL-2.0-only notice, and the repository carries the license itself.

Also asserts GPL-2.0-ONLY specifically: an "or (at your option) any later
version" clause in a header silently converts the project to GPL-2.0-or-later,
a different license and irreversible once distributed.

Classification comes from frognet_apply_license.py so the gate and the applier
cannot disagree; the walk is bounded by frognet_world_manifest.sh so it does not
try to walk / on an installed node, where the tree root IS /.
"""
from __future__ import annotations
import os, sys, re, fnmatch, collections, importlib.util

FAILS = []
NOTICE_REGION = 2500


def _tree():
    d = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.dirname(os.path.dirname(d))


def _manifest(tree):
    """[TEST_THE_TREE_YOU_ARE_IN_V1] tree first, installed copy as fallback."""
    for c in (os.path.join(tree, "usr/local/lib/frognet_world_manifest.sh"),
              "/usr/local/lib/frognet_world_manifest.sh"):
        if os.path.exists(c):
            return c
    return None


def _array(man, name):
    """One bash array out of the manifest. [ONE_MANIFEST_V1] - ask it, don't
    keep a second copy of what the world contains."""
    if not man:
        return []
    out, inside = [], False
    for ln in open(man, encoding="utf-8", errors="replace").read().splitlines():
        if not inside:
            inside = bool(re.match(rf"\s*{name}=\(", ln))
            continue
        if re.match(r"\s*\)\s*$", ln):
            break
        for tok in ln.split("#", 1)[0].split():
            out.append(tok.strip("'\""))
    return out


def _sources(tree, roots, never, classify):
    """Candidate source files under the world roots.

    [A_SYMLINK_IS_NOT_A_SOURCE_FILE_V1] Symlinks and never-ship paths are not
    source. On FrogNetHost 2026-09-01, 45 of 129 reported gaps were systemd
    enable symlinks and the rest were generated node state - neither can carry
    a copyright notice, and never-ship files are excluded from the repo by
    definition.
    """
    skip_dirs = {"__pycache__", ".git", "python3.11", "venv",
                 "sites-enabled", "mods-enabled", "conf-enabled"}
    for r in roots:
        if os.path.isfile(r):
            rel = os.path.relpath(r, tree)
            if (not os.path.islink(r) and classify(r)
                    and not any(fnmatch.fnmatch(rel, p) for p in never)):
                yield r
            continue
        for dp, dns, fns in os.walk(r):
            dns[:] = [d for d in dns
                      if d not in skip_dirs and not d.endswith(".target.wants")]
            for fn in fns:
                f = os.path.join(dp, fn)
                if os.path.islink(f) or not classify(f):
                    continue
                # Manifest patterns are tree-relative. lstrip("/") only
                # produced that on an installed node, where the tree root IS
                # "/" - in a checkout it yielded an absolute path and no
                # never-ship pattern ever matched.
                rel = os.path.relpath(f, tree)
                if any(fnmatch.fnmatch(rel, p) for p in never):
                    continue
                yield f


def check(label, problems):
    if problems:
        FAILS.extend(problems)
        print(f"  [FAIL] {label}")
        for p in problems[:12]:
            print(f"         - {p}")
        if len(problems) > 12:
            print(f"         ... and {len(problems) - 12} more")
    else:
        print(f"  [PASS] {label}")


def _report_missing(missing):
    """[A_COUNT_IS_NOT_A_DIAGNOSIS_V1] A bare count says something is wrong and
    nothing about what. These arrive in clumps - a directory that was never in
    the tree, a file type the applier skips - so group before listing."""
    byd = collections.Counter(m.rsplit("/", 1)[0] for m in missing)
    bye = collections.Counter(m.rsplit(".", 1)[-1] if "." in os.path.basename(m)
                              else "<no extension>" for m in missing)
    print(f"  [FAIL] every source file carries the GPL-2.0-only notice "
          f"({len(missing)} without)")
    print("         by directory:")
    for d, n in byd.most_common(10):
        print(f"           {n:5d}  {d}")
    print("         by extension:")
    print("           " + "  ".join(f"{e}:{n}" for e, n in bye.most_common(8)))
    print("         first few:")
    for m in missing[:6]:
        print(f"           - {m}")


def run():
    tree = _tree()

    tool = os.path.join(tree, "usr/local/bin/frognet_apply_license.py")
    if not os.path.exists(tool):
        tool = "/usr/local/bin/frognet_apply_license.py"
    check("the license applier ships",
          [] if os.path.exists(tool) else
          [f"{tool} missing - nothing can refresh headers when the year rolls over"])
    if not os.path.exists(tool):
        return
    spec = importlib.util.spec_from_file_location("_fal", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # [LICENSE_TRAVELS_WITH_THE_WORK_V1] Two homes: the repo root for a reader of
    # the checkout, and usr/local/share/frognet inside the world so an installed
    # node has the text too. Checking only the repo root failed on a node.
    # [MATCH_THE_HOUSE_STYLE_V1] LICENSE is the GPLv2 text, COPYRIGHT is the
    # notice - the two names every generated header cites.
    def find(name):
        for c in (os.path.join(tree, name),
                  os.path.join(tree, "usr/local/share/frognet", name),
                  os.path.join("/usr/local/share/frognet", name)):
            if os.path.exists(c):
                return c
        return None

    probs = []
    for name, required in (("COPYRIGHT", ("GPL-2.0-only", "Copyright (C)")),
                           ("LICENSE", ("Version 2, June 1991", "TERMS AND CONDITIONS"))):
        p = find(name)
        if not p:
            probs.append(f"{name} missing")
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        probs += [f"{name} lacks {r!r}" for r in required if r not in text]
    check("LICENSE holds GPLv2 verbatim and COPYRIGHT states the grant", probs)

    man = _manifest(tree)
    roots = [os.path.join(tree, p) for p in _array(man, "FROGNET_WORLD_PATHS")]
    roots = [r for r in roots if os.path.exists(r)]
    never = _array(man, "FROGNET_NEVER_SHIP")
    # [THIRD_PARTY_IS_NOT_OURS_V1 - John 2026-09-14] Vendored code is excluded
    # from both checks below. It is not missing OUR notice -- it carries its
    # own, and applying ours would misstate its licence.
    third = _array(man, "FROGNET_THIRD_PARTY")

    def _is_third_party(rel):
        return any(rel == t or rel.startswith(t.rstrip("/") + "/")
                   for t in third)
    check("the walk is bounded by the world manifest",
          [] if roots else ["no world paths found - refusing to walk from the "
                            "filesystem root"])
    if not roots:
        return

    missing, orlater, apache = [], [], []
    for p in _sources(tree, roots, never, mod.classify):
        try:
            text = open(p, encoding="utf-8", errors="strict").read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(p, tree)
        if _is_third_party(rel):
            continue
        head = text[:NOTICE_REGION]
        if mod.SPDX not in text:
            missing.append(rel)
        # Only a file that CARRIES the notice can contradict it, and only in its
        # notice region. A whole-file scan matched this checker's own docstring,
        # which quotes the clause to explain why it must never appear in one.
        elif "(at your option) any later version" in head:
            orlater.append(f"{rel} is tagged GPL-2.0-only AND grants 'any later "
                           "version' - those contradict, and or-later is "
                           "irreversible once distributed")
        # The Apache GRANT line, not the words "Apache License", which appear in
        # this file for the same reason.
        if (p.endswith((".js", ".py", ".c", ".cpp", ".h"))
                and "Licensed under the Apache License" in head
                and mod.SPDX not in head):
            apache.append(f"{rel} looks Apache-2.0 - incompatible with "
                          "GPL-2.0-only; add it to THIRD_PARTY and reopen the "
                          "licensing question")

    if missing:
        _report_missing(missing)
        FAILS.extend(missing)
    else:
        check("every source file carries the GPL-2.0-only notice", [])
    check("no file grants 'any later version'", orlater)
    check("no Apache-2.0 code has been vendored in under the GPLv2-only umbrella",
          apache)
    # A declared third-party tree still has to be a real decision, not a
    # wildcard: a path listed here that is not on disk is a stale exclusion
    # quietly widening what the walk skips.
    check("every FROGNET_THIRD_PARTY path exists",
          [f"{t} is declared third-party but is not in the tree"
           for t in third if not os.path.exists(os.path.join(tree, t))])


def main():
    print("=== licensing: GPL-2.0-only, everywhere ===")
    run()
    print("\n" + ("ALL LICENSE CHECKS PASS" if not FAILS
                  else f"LICENSE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
