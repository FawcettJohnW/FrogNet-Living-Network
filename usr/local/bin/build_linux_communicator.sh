#!/usr/bin/env bash
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
# build_linux_communicator.sh - assemble the Linux Communicator package.
#
# Produces:  FrogNetCommunicator-<VER>-linux.tar.gz
#
# The tarball IS the installer: unpack it anywhere and run ./install.sh.
# Contents sit at the ROOT of the archive -- unpacking gives you install.sh
# right where you are standing, not a folder to descend into first.
#
# Usage:
#     ./build_linux_communicator.sh [SRC_ROOT] [OUTDIR]
#     VER=2.9 ./build_linux_communicator.sh / /tmp
#
#     SRC_ROOT  tree containing BOTH etc/frognet_bundles and opt/frognet_semantic.
#               On a node that is "/". Default: current directory.
#     OUTDIR    where the tarball lands. Default: current directory.
#     CORE_ROOT override for core/ if it is not under SRC_ROOT.
#
# The client and core genuinely live in DIFFERENT trees on a node --
# /etc/frognet_bundles/communicator and /opt/frognet_semantic/core -- so this
# does not pretend one root covers both. CORE_ROOT exists for that reason.
set -euo pipefail

SRC_ROOT="${1:-.}"
OUTDIR="${2:-.}"
VER="${VER:-2.9}"
NAME="FrogNetCommunicator-${VER}-linux"

COMMS="${SRC_ROOT%/}/etc/frognet_bundles/communicator"
BUNDLES_SRC="${SRC_ROOT%/}/etc/frognet_bundles"
CORE_SRC="${CORE_ROOT:-${SRC_ROOT%/}/opt/frognet_semantic/core}"

die() { echo "FATAL: $*" >&2; exit 1; }

[[ -d "$COMMS" ]]       || die "no communicator dir at $COMMS (SRC_ROOT should be the tree root, e.g. /)"
[[ -d "$BUNDLES_SRC" ]] || die "no bundles dir at $BUNDLES_SRC"
[[ -d "$CORE_SRC" ]]    || die "no core/ at $CORE_SRC - frognet_tuples.py cannot resolve (set CORE_ROOT)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PAY="${STAGE}/payload"
mkdir -p "$PAY/bundles" "$PAY/core"

# ---- client -----------------------------------------------------------------
# EVERY non-test .py, not an import closure. A static closure from
# communicator_live.py reaches a fraction of the modules: the app launches
# bundle apps as SUBPROCESSES and imports others dynamically, so closure
# packaging silently drops working code and the failure only shows when a user
# presses a button. Shipping the whole bundle is what guarantees transitive
# deps. The test_ oracles are the one safe trim -- nothing in the runtime
# imports them.
n_py=0; n_skip=0
for f in "$COMMS"/*.py; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"
    case "$b" in test_*) n_skip=$((n_skip + 1)); continue ;; esac
    cp "$f" "$PAY/"
    n_py=$((n_py + 1))
done
(( n_py > 0 )) || die "no .py files in $COMMS"

for f in run_communicator.sh comms_functions.sh comms_web.html shell.json; do
    [[ -f "$COMMS/$f" ]] && cp "$COMMS/$f" "$PAY/"
done

# ---- entry point ------------------------------------------------------------
# communicator_live.py IS the Communicator -- the one with the splash, and the
# one the command line has always named:
#     python3 communicator_live.py --name <name>
# communicator.py is a different, older app that imports communicator_app and
# never reaches the live one. The launcher pointed at it for a long time.
[[ -f "$PAY/communicator_live.py" ]] || die "communicator_live.py missing - THE entry point"

# ---- core/ ------------------------------------------------------------------
# frognet_tuples.py is a SHIM: it inserts /opt/frognet_semantic on sys.path if
# that directory exists, then does `from core import frognet_tuples`. On a node
# that resolves against the node's core (shared state, which is correct there).
# On a machine with no other FrogNet component it must resolve against THIS
# package, so core/ ships alongside the client -- the payload directory is
# already on sys.path because communicator_live.py lives there.
n_core=0
for f in "$CORE_SRC"/*.py; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"
    case "$b" in test_*|*.bak) continue ;; esac
    cp "$f" "$PAY/core/"
    n_core=$((n_core + 1))
done
[[ -f "$PAY/core/__init__.py" ]]       || die "core/__init__.py missing - not a package"
[[ -f "$PAY/core/frognet_tuples.py" ]] || die "core/frognet_tuples.py missing - the shim target"

# ---- bundles ----------------------------------------------------------------
n_b=0
for d in "$BUNDLES_SRC"/*; do
    [[ -d "$d" ]] || continue
    b="$(basename "$d")"
    case "$b" in communicator|communicator.old|*.old|*.out_of_way) continue ;; esac
    cp -r "$d" "$PAY/bundles/$b"
    n_b=$((n_b + 1))
done

# ---- launcher ---------------------------------------------------------------
cat > "$PAY/run_communicator.sh" <<'LAUNCH'
#!/usr/bin/env bash
# FrogNet Communicator launcher. Resolves everything from its own location, so
# the install directory can be moved without editing anything.
HERE="$(cd "$(dirname "$0")" && pwd)"
export FROGNET_BUNDLES_ROOT="$HERE/bundles"
export FROGNET_COMMUNICATOR_HOME="$HERE"
exec python3 "$HERE/communicator_live.py" "$@"
LAUNCH
chmod +x "$PAY/run_communicator.sh"

# ---- installer --------------------------------------------------------------
cat > "$PAY/install.sh" <<'INSTALL'
#!/usr/bin/env bash
# FrogNet Communicator installer.
#
#   ./install.sh              install for this user   (~/.local/share)
#   ./install.sh --prefix DIR install somewhere else
#
# No root. Nothing is written outside your home unless you name a prefix.
# Re-runnable: bundles you added yourself are kept.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="${HOME}/.local/share/FrogNetCommunicator"
BIN="${HOME}/.local/bin"
DESKTOP="${HOME}/.local/share/applications"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) APP="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

command -v python3 >/dev/null || {
    echo "FATAL: python3 is not on PATH." >&2
    echo "       The Communicator needs Python 3 with tkinter." >&2
    exit 1; }
python3 -c 'import tkinter' 2>/dev/null || {
    echo "FATAL: python3 is present but tkinter is not." >&2
    echo "       Debian/Ubuntu: sudo apt install python3-tk" >&2
    echo "       Fedora:        sudo dnf install python3-tkinter" >&2
    exit 1; }

echo "installing to $APP"
mkdir -p "$APP" "$APP/bundles"

cp -f "$SRC"/*.py "$APP/"
cp -f "$SRC"/run_communicator.sh "$APP/"
[[ -f "$SRC/comms_web.html" ]] && cp -f "$SRC/comms_web.html" "$APP/"
chmod +x "$APP/run_communicator.sh"

# core/ must land NEXT TO the client: frognet_tuples.py resolves
# `from core import frognet_tuples` against the install directory.
rm -rf "$APP/core"
cp -r "$SRC/core" "$APP/core"

# merge ship-with bundles without clobbering ones the user added
for d in "$SRC"/bundles/*; do
    [[ -d "$d" ]] || continue
    b="$(basename "$d")"
    [[ -e "$APP/bundles/$b" ]] || cp -r "$d" "$APP/bundles/$b"
done

mkdir -p "$BIN"
ln -sf "$APP/run_communicator.sh" "$BIN/frognet-communicator"

mkdir -p "$DESKTOP"
cat > "$DESKTOP/frognet-communicator.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=FrogNet Communicator
Comment=Presence, calls and chat over the FrogNet mesh
Exec=$APP/run_communicator.sh
Path=$APP
Terminal=false
Categories=Network;InstantMessaging;
DESK
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$DESKTOP" 2>/dev/null || true

echo
echo "installed."
echo "  run:  frognet-communicator --name <you>"
echo "        (or $APP/run_communicator.sh --name <you>)"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo
       echo "  NOTE: $BIN is not on your PATH. Add it, or use the full path above." ;;
esac
INSTALL
chmod +x "$PAY/install.sh"

# ---- hygiene ----------------------------------------------------------------
find "$PAY" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAY" \( -name "*.pyc" -o -name "*.pyo" -o -name "*~" \) -delete 2>/dev/null || true

# ---- verify before shipping -------------------------------------------------
bad=0
while IFS= read -r f; do
    python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" \
        || { echo "SYNTAX FAIL: $f" >&2; bad=1; }
done < <(find "$PAY" -name "*.py")
(( bad == 0 )) || die "payload contains files that do not parse"
(( $(find "$PAY" -name '*.pyc' | wc -l) == 0 )) || die "bytecode in payload"
bash -n "$PAY/install.sh"          || die "generated install.sh does not parse"
bash -n "$PAY/run_communicator.sh" || die "generated launcher does not parse"
grep -q communicator_live.py "$PAY/run_communicator.sh" \
    || die "launcher does not start communicator_live.py"

# Prove every fnav attribute communicator_live.py reaches for actually exists in
# the fnav being shipped. Two communicator trees exist and their fnav.py differ;
# building from the wrong one produces a client that dies at startup.
python3 - "$PAY" <<'PYCHK' || die "payload fnav.py does not satisfy communicator_live.py"
import ast, os, sys
pay = sys.argv[1]
want = set()
for n in ast.walk(ast.parse(open(os.path.join(pay, "communicator_live.py"),
                                 encoding="utf-8").read())):
    if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "fnav"):
        want.add(n.attr)
have = set()
for n in ast.parse(open(os.path.join(pay, "fnav.py"), encoding="utf-8").read()).body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        have.add(n.name)
    elif isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                have.add(t.id)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names:
            have.add(a.asname or a.name.split(".")[0])
missing = sorted(want - have)
if missing:
    print("fnav.py is missing: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("  fnav.py satisfies communicator_live.py (%d attributes)" % len(want))
PYCHK

# ---- pack -------------------------------------------------------------------
mkdir -p "$OUTDIR"
OUT="$(cd "$OUTDIR" && pwd)/${NAME}.tar.gz"
rm -f "$OUT"
( cd "$PAY" && tar czf "$OUT" . )        # contents at the ROOT of the archive

echo "built ${OUT}"
echo "  client .py     ${n_py}  (skipped ${n_skip} test_ oracles)"
echo "  core modules   ${n_core}"
echo "  bundles        ${n_b}"
echo "  total files    $(find "$PAY" -type f | wc -l)"
echo "  size           $(du -h "$OUT" | cut -f1)"
echo
echo "Install:  tar xzf ${NAME}.tar.gz && ./install.sh"
echo "          (needs python3 with tkinter)"
