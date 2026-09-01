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

A repository that is "GPLv2-only" in its README and silent in 1,058 of its files
is not licensed, it is asserted. This drives the same classifier the applier uses
(frognet_apply_license.py), so the gate and the tool cannot disagree about which
files count as source - a second hand-maintained list of extensions is exactly
how the three disagreeing path manifests happened.

It also asserts GPL-2.0-ONLY specifically. "or (at your option) any later
version" in a header would silently convert the project to GPL-2.0-or-later,
which is a different license and an irreversible one once distributed.
"""
from __future__ import annotations
import os, sys, importlib.util

FAILS = []


def _tree():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.dirname(os.path.dirname(here))



def _never_ship(man):
    """FROGNET_NEVER_SHIP, parsed from the manifest. One list, not two."""
    import re as _re
    out, inside = [], False
    for ln in open(man, encoding="utf-8", errors="replace").read().splitlines():
        if not inside:
            if _re.match(r"\s*FROGNET_NEVER_SHIP=\(", ln):
                inside = True
            continue
        if _re.match(r"\s*\)\s*$", ln):
            break
        ln = ln.split("#", 1)[0].strip()
        m = _re.match(r"^'([^']+)'$", ln) or _re.match(r'^"([^"]+)"$', ln)
        if m:
            out.append(m.group(1))
    return out


def _world_roots(tree):
    """Directories to scan: the manifest's own path list, resolved under `tree`.

    Parsed from frognet_world_manifest.sh rather than restated here. A second
    hand-kept list of what the world contains is the bug this project already
    had three times over.
    """
    import re
    man = None
    # [TEST_THE_TREE_YOU_ARE_IN_V1] tree first; the installed copy is the fallback
    for c in (os.path.join(tree, "usr", "local", "lib", "frognet_world_manifest.sh"),
              "/usr/local/lib/frognet_world_manifest.sh"):
        if os.path.exists(c):
            man = c
            break
    if not man:
        return []
    out, inside = [], False
    for ln in open(man, encoding="utf-8", errors="replace").read().splitlines():
        if not inside:
            if re.match(r"\s*FROGNET_WORLD_PATHS=\(", ln):
                inside = True
            continue
        if re.match(r"\s*\)\s*$", ln):
            break
        ln = ln.split("#", 1)[0].strip()
        for tok in ln.split():
            full = os.path.join(tree, tok)
            if os.path.exists(full):
                out.append(full)
    return out


def _walk_roots(mod, roots):
    """Yield candidate SOURCE files under the world roots.

    [A_SYMLINK_IS_NOT_A_SOURCE_FILE_V1] Two whole categories were being asked for
    a copyright notice and could never carry one. Measured on FrogNetHost
    2026-09-01: of 129 reported gaps, 45 were systemd enable symlinks under
    *.target.wants and sites-enabled - not files, and several pointing at Debian's
    own units - and more were generated node state (tunnel.conf, wg*.conf,
    gateways.conf, the auto-generated dnsmasq forwarder map).

    A file the manifest says must NEVER SHIP is not source: it is this box's
    state, it is excluded from the repository by definition, and a copyright
    header on it would be meaningless. Ask the manifest rather than keeping a
    second list here.
    """
    import fnmatch
    never = list(getattr(mod, "NEVER_SHIP_PATTERNS", []))
    for r in roots:
        if os.path.isfile(r) and not os.path.islink(r):
            yield r
            continue
        for dp, dns, fns in os.walk(r):
            dns[:] = [d for d in dns
                      if d not in ("__pycache__", ".git", "python3.11", "venv")
                      and not d.endswith(".target.wants")
                      and d not in ("sites-enabled", "mods-enabled", "conf-enabled")]
            for fn in fns:
                f = os.path.join(dp, fn)
                if os.path.islink(f):
                    continue
                rel = f.lstrip("/")
                if any(fnmatch.fnmatch(rel, pat) for pat in never):
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


def run():
    tree = _tree()
    tool = os.path.join(tree, "usr", "local", "bin", "frognet_apply_license.py")
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

    # [LICENSE_TRAVELS_WITH_THE_WORK_V1] Two homes, on purpose: the repo root for
    # a reader/scanner of the checkout, and usr/local/share/frognet inside the
    # world so an INSTALLED node has the text too. Look in both - checking only
    # the repo root failed on a node, where there is no repo root.
    def _find(name):
        # [TEST_THE_TREE_YOU_ARE_IN_V1] tree first, installed last
        for c in (os.path.join(tree, name),
                  os.path.join(tree, "usr", "local", "share", "frognet", name),
                  os.path.join("/usr/local/share/frognet", name)):
            if os.path.exists(c):
                return c
        return None

    # [MATCH_THE_HOUSE_STYLE_V1] The author's scheme is LICENSE (the GPLv2 text)
    # and COPYRIGHT (the notice, the version rationale, third-party components and
    # the provenance of the license text). Every generated header cites those two
    # names. This checked for COPYING, a third name for the same thing that the
    # headers do not mention; it now checks what the tree actually uses.
    lic = _find("LICENSE")
    copying = _find("COPYRIGHT")
    probs = []
    if not copying:
        probs.append("COPYRIGHT missing - the notice every header cites")
    else:
        t = open(copying, encoding="utf-8", errors="replace").read()
        if "GPL-2.0-only" not in t:
            probs.append("COPYRIGHT does not state GPL-2.0-only")
        if "Copyright (C)" not in t:
            probs.append("COPYRIGHT names no copyright holder")
    if not lic:
        probs.append("LICENSE missing - the GPL requires the license text accompany the work")
    else:
        t = open(lic, encoding="utf-8", errors="replace").read()
        if "Version 2, June 1991" not in t:
            probs.append("LICENSE is not the GPLv2 text")
        if "TERMS AND CONDITIONS" not in t:
            probs.append("LICENSE is truncated - no TERMS AND CONDITIONS section")
    check("LICENSE holds GPLv2 verbatim and COPYRIGHT states the grant", probs)

    # [ONE_MANIFEST_V1] Bound the walk to the paths the manifest names. On an
    # INSTALLED node _tree() resolves to "/", and the first version of this walked
    # the entire filesystem - it did not finish. The world is what the manifest
    # says it is; ask it rather than guessing a root.
    roots = _world_roots(tree)
    _m = None
    for _c in (os.path.join(tree, "usr", "local", "lib", "frognet_world_manifest.sh"),
               "/usr/local/lib/frognet_world_manifest.sh"):
        if os.path.exists(_c):
            _m = _c
            break
    mod.NEVER_SHIP_PATTERNS = _never_ship(_m) if _m else []
    check("the walk is bounded by the world manifest",
          [] if roots else ["no world paths found - refusing to walk from the "
                            "filesystem root"])
    if not roots:
        return

    missing = []
    orlater = []
    for p in _walk_roots(mod, roots):
        ext = mod.classify(p)
        if ext is None:
            continue
        try:
            text = open(p, encoding="utf-8", errors="strict").read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(p, tree)
        if mod.SPDX not in text:
            missing.append(f"{rel} has no {mod.SPDX}")
        # Only a file that CARRIES the notice can contradict it. Searching every
        # file for the bare phrase matched this checker's own docstring and the
        # LICENSE file's explanation of why the clause is absent - a check that
        # fails on documents describing the rule is a check nobody will keep.
        # Look at the NOTICE REGION only. Checking the whole file matched this
        # checker's own docstring, which quotes the clause in order to explain why
        # it must never appear in one. Second time that bit: a rule stated in prose
        # inside the file that enforces it is not a violation of the rule.
        elif "(at your option) any later version" in text[:2500]:
            orlater.append(f"{rel} carries the GPL-2.0-only tag AND grants "
                           "'(at your option) any later version' - those contradict, "
                           "and or-later is irreversible once distributed")
    # [A_COUNT_IS_NOT_A_DIAGNOSIS_V1] This reported twelve names and "... and N
    # more". When N did not match what the operator expected -- 202 on a node
    # where a clean tree should have given 0, then 129 after a prune -- the count
    # said something was wrong and nothing about what. Group by directory first:
    # one line usually identifies the cause, because these arrive in clumps (a
    # directory that was never in the tree, a file type the applier skips).
    if missing:
        import collections
        byd = collections.Counter(m.rsplit("/", 1)[0].split(" has no")[0]
                                  for m in missing)
        bye = collections.Counter(
            (m.split(" has no")[0].rsplit(".", 1)[-1] if "." in m.split(" has no")[0]
             else "<no extension>") for m in missing)
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
        FAILS.extend(missing)
    else:
        check("every source file carries the GPL-2.0-only notice", [])
    check("no file grants 'any later version'", orlater)

    # A vendored dependency under a GPLv2-incompatible license is a licensing
    # decision, not a merge conflict. Make it fail here rather than surface in
    # somebody's audit.
    bad = []
    for p in _walk_roots(mod, roots):
        if not p.endswith((".js", ".py", ".c", ".cpp", ".h")):
            continue
        try:
            head = open(p, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        rel = os.path.relpath(p, tree)
        # The Apache GRANT line, not the words "Apache License" - which appear in
        # this file's own explanation of why they must not appear anywhere else.
        if "Licensed under the Apache License" in head and mod.SPDX not in head:
            bad.append(f"{rel} looks Apache-2.0 - incompatible with GPL-2.0-only; "
                       "add it to THIRD_PARTY and reopen the licensing question")
    check("no Apache-2.0 code has been vendored in under the GPLv2-only umbrella", bad)


def main():
    print("=== licensing: GPL-2.0-only, everywhere ===")
    run()
    print("\n" + ("ALL LICENSE CHECKS PASS" if not FAILS
                  else f"LICENSE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
