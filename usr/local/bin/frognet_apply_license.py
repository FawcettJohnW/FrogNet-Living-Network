#!/usr/bin/env python3
"""
frognet_apply_license.py - [ONE_LICENSE_HEADER_V1]

Insert (or refresh) the GPL-2.0-only notice at the top of every FrogNet source
file, using the comment syntax that file type actually supports.

    frognet_apply_license.py --holder "..." --years "..." --check
    frognet_apply_license.py --holder "..." --years "..." --apply

WHY A TOOL AND NOT A ONE-LINER

  - The comment character is not the same in six of the file types here, and two
    of them (shebang scripts, PHP) have a first line the header MUST go AFTER.
    A sed one-liner that ignores that produces files that no longer execute.
  - JSON has no comment syntax at all. There is no way to put a header in one,
    so this does not try; the repository-level COPYING covers them.
  - It must be IDEMPOTENT. This will be re-run when the copyright year rolls
    over or the holder string changes, and a tool that appends a second header
    on the second run is worse than no tool.

WHAT IT WILL NOT TOUCH

  Third-party code. As of 2026-08-31 the tree contains none - the Apache-2.0
  WebRTC samples, the awrtc webpack bundle, the BSD adapter.js copies and
  XlsxWriter's vba_extract.py were all removed as dead, so there is nothing here
  that is not FrogNet's to license. THIRD_PARTY below is therefore empty, and is
  kept as the place to name anything vendored in later. Adding a vendored
  dependency means adding it there in the same commit.

  GPL-2.0-only cannot be combined with Apache-2.0 code, so this is not a
  bookkeeping detail: if THIRD_PARTY ever needs an Apache-2.0 entry, the
  licensing question has to be reopened, not worked around.
"""
from __future__ import annotations
import argparse
import os
import sys

SPDX = "SPDX-License-Identifier: GPL-2.0-only"
MARK = "FrogNet Living Network"

# Paths that are NOT ours to license. Empty by design - see the module docstring.
THIRD_PARTY: list[str] = []

# Never carries a header: no comment syntax, or not source.
SKIP_EXT = {
    ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".ivf", ".wav", ".mp4", ".zip", ".tgz", ".gz", ".crt", ".key", ".pem",
    ".pyc", ".map", ".min.js", ".load", ".sql.gz",
}
SKIP_NAME = {"COPYING", "LICENSE", "README.md", ".gitignore"}

# extension -> (line comment prefix, block open, block close)
HASH = ("#", None, None)
SYNTAX = {
    ".py": HASH, ".sh": HASH, ".bash": HASH, ".conf": HASH, ".cnf": HASH,
    ".service": HASH, ".timer": HASH, ".target": HASH, ".yml": HASH,
    ".yaml": HASH, ".toml": HASH,
    ".sql": ("--", None, None),
    ".php": (None, "/*", "*/"),
    ".js": (None, "/*", "*/"),
    ".css": (None, "/*", "*/"),
    ".cpp": (None, "/*", "*/"),
    ".h": (None, "/*", "*/"),
    ".hpp": (None, "/*", "*/"),
    ".cs": (None, "/*", "*/"),
    ".kt": (None, "/*", "*/"),
    ".html": (None, "<!--", "-->"),
}

# [MATCH_THE_HOUSE_STYLE_V1] This is the block the author had already begun
# applying by hand (14 files as of 2026-08-31, e.g. usr/local/bin/getOurDomain).
# It is reproduced exactly, with one correction: the copyright line measured 65
# characters where every other line in the box measured 64, so the right border
# did not line up. The box is generated to a fixed width here rather than typed,
# so it cannot drift again.
#
# The text cites COPYRIGHT and LICENSE at the tree root. That is the author's
# scheme and this tool does not invent a third name for the same thing.
BOX_W = 64
NOTICE_LINES = [
    "Copyright (C) {years} {holder}",
    "",
    "SPDX-License-Identifier: GPL-2.0-only",
    "",
    "This program is free software; you can redistribute it",
    "and/or modify it under the terms of the GNU General Public",
    "License as published by the Free Software Foundation;",
    "version 2 of the License, and no other version.",
    "",
    "This program is distributed in the hope that it will be",
    "useful, but WITHOUT ANY WARRANTY; without even the implied",
    "warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR",
    "PURPOSE.  See the GNU General Public License for details.",
    "",
    "See COPYRIGHT and LICENSE at the root of this tree.",
]


def render(ext: str, holder: str, years: str) -> str:
    """The boxed notice, in whatever comment syntax `ext` supports.

    A line too long for the box is not silently clipped - clipping a copyright
    line is how you end up distributing a notice that names the wrong party.
    """
    line, opn, cls = SYNTAX[ext]
    body = [l.format(holder=holder, years=years) for l in NOTICE_LINES]
    too_long = [l for l in body if len(l) > BOX_W - 6]
    if too_long:
        raise SystemExit(
            "notice line does not fit the %d-column box: %r\n"
            "Widen BOX_W rather than truncating a copyright line."
            % (BOX_W, too_long[0]))

    # Width math, explicitly. The first version wrote `line * BOX_W`, which for
    # SQL's two-character "--" prefix produced a 128-column rule instead of a
    # 64-column one, and mis-set the block-comment box by one. Repetition is not
    # padding when the prefix is longer than one character.
    if line is not None:
        rule = (line + "-" * BOX_W)[:BOX_W] if line == "--" else line * BOX_W
        rule = rule[:BOX_W]
        out = [rule]
        for l in body:
            out.append((f"{line}  {l}").ljust(BOX_W - len(line)) + line)
        out.append(rule)
        return "\n".join(out) + "\n"

    # Block-comment languages: same 64-column box, opened and closed properly.
    out = [opn + "*" * (BOX_W - len(opn))]
    for l in body:
        out.append((f" *  {l}").ljust(BOX_W - 1) + "*")
    out.append(" " + "*" * (BOX_W - 1 - len(cls)) + cls)
    return "\n".join(out) + "\n"


def insert_at(text: str, ext: str) -> int:
    """Byte offset the header must go at.

    A header before a shebang stops the file being executable, and a header
    before <?php is emitted as page output. Both are silent at write time and
    obvious only at run time.
    """
    if text.startswith("#!"):
        nl = text.find("\n")
        return len(text) if nl < 0 else nl + 1
    if ext == ".php":
        i = text.find("<?php")
        if i >= 0:
            nl = text.find("\n", i)
            return len(text) if nl < 0 else nl + 1
    if text.startswith("\ufeff"):
        return 1
    return 0


def classify(path: str) -> str | None:
    name = os.path.basename(path)
    if name in SKIP_NAME:
        return None
    ext = os.path.splitext(name)[1]
    if ext in SKIP_EXT:
        return None
    if ext in SYNTAX:
        return ext
    if "." not in name:
        try:
            with open(path, "rb") as fh:
                if fh.read(2) == b"#!":
                    return ".sh"
        except OSError:
            return None
    return None


def walk(root: str):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns
                  if d not in ("__pycache__", ".git", "python3.11", "venv")]
        rel_dp = os.path.relpath(dp, root)
        if any(rel_dp == t or rel_dp.startswith(t + os.sep) for t in THIRD_PARTY):
            dns[:] = []
            continue
        for fn in fns:
            yield os.path.join(dp, fn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holder", required=True,
                    help='Copyright holder, e.g. "John Fawcett"')
    ap.add_argument("--years", required=True, help='e.g. "2016-2026"')
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true",
                   help="report what is missing a header; change nothing")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    todo, have, skipped = [], [], 0
    for p in walk(a.root):
        ext = classify(p)
        if ext is None:
            skipped += 1
            continue
        try:
            text = open(p, encoding="utf-8", errors="strict").read()
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        (have if SPDX in text else todo).append((p, ext, text))

    if a.check:
        print(f"with header: {len(have)}\nneeding one: {len(todo)}\n"
              f"not source (no comment syntax / binary): {skipped}")
        by = {}
        for p, ext, _ in todo:
            by[ext] = by.get(ext, 0) + 1
        for ext, n in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"    {ext:<10} {n}")
        return 0

    n = 0
    for p, ext, text in todo:
        at = insert_at(text, ext)
        head = render(ext, a.holder, a.years)
        new = text[:at] + head + text[at:]
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(new)
        n += 1
    print(f"headers written: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
