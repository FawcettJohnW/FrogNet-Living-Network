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
# build_windows_communicator.sh - assemble the Windows Communicator payload.
#
# Runs on the build box (Linux). Produces:
#     FrogNetCommunicator-windows-<date>.zip
#
# The zip IS the installer: unpack it anywhere and run install-optional.bat.
# It also doubles as the MSI payload directory -- FrogNetCommunicator.wxs
# harvests it with -bindpath, so the same tree feeds both paths and cannot
# drift between them.
#
# Usage:
#     ./build_windows_communicator.sh [SRC_ROOT] [OUTDIR]
#
#     SRC_ROOT  root of the extracted FrogNet source (contains opt/ and etc/)
#               default: the current directory
#     OUTDIR    where the zip lands, default: current directory
set -euo pipefail

SRC_ROOT="${1:-.}"
OUTDIR="${2:-.}"
STAMP="$(date +%Y%m%d)"
VER="${VER:-$(date +%Y%m%d)}"   # date-stamped: a version number must not lie
NAME="FrogNetCommunicator-${VER}-windows"

# [BUNDLE_TREE_IS_THE_CLIENT_V1] The Windows client is the BUNDLE tree.
#
# There are TWO communicator trees and they are NOT the same files:
#     etc/frognet_bundles/communicator/       <- what communicator_live.py imports
#     opt/frognet_semantic/etc/communicator/  <- the node-side copy
# fnav.py differs between them; the bundle copy has media_capability() and the
# node copy does not. Building the payload from the wrong one produces a client
# that dies at startup with AttributeError. Verified below, not assumed.
COMMS="${SRC_ROOT}/etc/frognet_bundles/communicator"
BUNDLES_SRC="${SRC_ROOT}/etc/frognet_bundles"

die() { echo "FATAL: $*" >&2; exit 1; }

[[ -d "$COMMS" ]]       || die "no communicator dir at $COMMS"
[[ -d "$BUNDLES_SRC" ]] || die "no bundles dir at $BUNDLES_SRC"

# The payload is the ZIP ROOT. Unzipping produces install.bat and the client
# right where the user is standing -- no nested folder to descend into first.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PAY="${STAGE}/payload"
mkdir -p "$PAY/bundles"

# ---- client -----------------------------------------------------------------
# EVERY non-test .py, not an import closure.
#
# A static closure from communicator.py reaches only 13 of 69 modules, because
# the app launches bundle apps as SUBPROCESSES and imports others dynamically.
# Closure-packaging would silently drop comms_control.py, fnav.py, call_av.py
# and 37 more, and the failure would not appear until a user pressed a button.
# Shipping the whole bundle is what guarantees transitive deps.
#
# [A_VERIFIER_THAT_CANNOT_RUN_IS_WORSE_THAN_NONE_V1] The test_ oracles SHIP.
#
# They were trimmed as the one safe saving. But checksite.py ships too, and five
# of its checks run those oracles -- so the installed client carried a verifier
# that could not pass, which is worse than carrying none: it teaches the
# operator to ignore a red result. They are a few hundred KB, and with ddnet
# gone there is nothing to save them from.
#
# Flat layout is the contract -- install.bat does `copy %SRC%*.py`.
n_py=0; n_skip=0
for f in "$COMMS"/*.py; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"
    case "$b" in *.bak|*.orig) n_skip=$((n_skip + 1)); continue ;; esac
    cp "$f" "$PAY/"
    n_py=$((n_py + 1))
done
(( n_py > 0 )) || die "no .py files found in $COMMS"

# ---- launchers and installers ----------------------------------------------
for f in add_bundle.bat comms_web.html shell.json; do
    [[ -f "$COMMS/$f" ]] && cp "$COMMS/$f" "$PAY/"
done

# [GENERATE_DONT_COPY_V1] The launcher and installer are GENERATED here, not
# copied from $COMMS.
#
# Copying them meant the package inherited whatever those two files happened to
# be on the build host. A node's run_communicator.bat still launched
# communicator.py -- a different, older app that imports communicator_app and
# never reaches the live one -- and its install-optional.bat copied only *.py,
# so core\ was left behind and the client died at startup with
# "ModuleNotFoundError: No module named 'core'". Both were fixed in a working
# copy that the node never had, so every build reintroduced them.
#
# These two files are packaging, not client code. They belong to the build.
cat > "$PAY/run_communicator.bat" <<'LAUNCH'
@echo off
REM communicator_live.py IS the Communicator -- the one with the splash, and the
REM one the command line has always named:
REM     python3 communicator_live.py --name <name>
REM communicator.py is a different, older app; it never reaches the live one.
set HERE=%~dp0
set FROGNET_BUNDLES_ROOT=%HERE%bundles
set FROGNET_COMMUNICATOR_HOME=%HERE%
python "%HERE%communicator_live.py" %*
LAUNCH

cat > "$PAY/install.bat" <<'INSTALL'
@echo off
REM FrogNet Communicator installer. Per-user, no admin. Re-runnable: bundles you
REM added yourself are kept.
setlocal
set SRC=%~dp0
set APP=%LOCALAPPDATA%\FrogNetCommunicator
echo Installing to %APP%

where python >nul 2>nul
if errorlevel 1 (
  echo FATAL: python is not on PATH. Install Python 3 from python.org
  echo        ^(the standard installer includes tkinter^), then run this again.
  pause
  exit /b 1
)

if /i "%SRC:~0,-1%"=="%APP%" (
  echo Already installed here -- nothing to copy.
  goto :shortcut
)

if not exist "%APP%" mkdir "%APP%"
copy /y "%SRC%*.py" "%APP%\" >nul
copy /y "%SRC%run_communicator.bat" "%APP%\" >nul
if exist "%SRC%comms_web.html" copy /y "%SRC%comms_web.html" "%APP%\" >nul

REM core\ must land NEXT TO the client: frognet_tuples.py resolves
REM "from core import frognet_tuples" against the install directory. Copying
REM only *.py left this behind and the client died at startup.
REM
REM robocopy, not xcopy: it skips files that are already identical, so a
REM re-install moves almost nothing, and it does not stop to ask about
REM overwriting. Exit codes 0-7 are success (8+ is a real failure), which is why
REM the errorlevel check reads the way it does.
if exist "%SRC%core" (
  robocopy "%SRC%core" "%APP%\core" /e /njh /njs /ndl /nc /ns /np >nul
  if errorlevel 8 echo WARNING: core\ did not copy cleanly -- the client may not start.
)

if not exist "%APP%\bundles" mkdir "%APP%\bundles"
REM Mirror every shipped bundle, but never delete one the user added.
for /d %%D in ("%SRC%bundles\*") do (
  robocopy "%%D" "%APP%\bundles\%%~nxD" /e /njh /njs /ndl /nc /ns /np >nul
)

:shortcut

powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "foreach($p in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){" ^
  "$s=$w.CreateShortcut((Join-Path $p 'FrogNet Communicator.lnk'));" ^
  "$s.TargetPath='%APP%\run_communicator.bat';$s.WorkingDirectory='%APP%';$s.Save()}" >nul 2>nul

echo Installed. Launch from the Start Menu / Desktop "FrogNet Communicator",
echo or run: "%APP%\run_communicator.bat" --name ^<you^>
pause
INSTALL

# install.bat is the name a user looks for after unzipping. The tree calls it
# install-optional.bat, which reads like something you can skip. Ship both:
# same bytes, and the obvious name is the one in the root of the zip.
[[ -f "$PAY/install.bat" ]] || die "install.bat was not generated"
[[ -f "$PAY/run_communicator.bat" ]]  || die "run_communicator.bat missing - the shortcut target"
# [LIVE_IS_THE_ENTRY_POINT_V1] communicator_live.py IS the Communicator -- the
# one with the splash, and the one the Linux command line has always named:
#   python3 communicator_live.py --name <name>
# communicator.py is a different, older app that imports communicator_app; it
# never reaches the live one. The launchers pointed at it, so install.bat's
# shortcut and run_communicator.bat both started the wrong program.
[[ -f "$PAY/communicator_live.py" ]] || die "communicator_live.py missing - THE entry point"
for _l in run_communicator.bat run_communicator.sh; do
    [[ -f "$PAY/$_l" ]] || continue
    grep -q "communicator_live.py" "$PAY/$_l" \
        || die "$_l does not launch communicator_live.py"
done

# [BUNDLE_TREE_IS_THE_CLIENT_V1] Prove every attribute communicator_live.py reaches
# for on fnav actually exists in the fnav being shipped. This is the check that would
# have caught the wrong-tree fnav.py before it was deployed instead of after.
if [[ -f "$PAY/communicator_live.py" && -f "$PAY/fnav.py" ]]; then
    python3 - "$PAY" <<'PYCHK' || die "payload fnav.py does not satisfy communicator_live.py"
import ast, sys, os
pay = sys.argv[1]
want = set()
tree = ast.parse(open(os.path.join(pay, "communicator_live.py"), encoding="utf-8").read())
for n in ast.walk(tree):
    if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "fnav"):
        want.add(n.attr)
have = set()
ft = ast.parse(open(os.path.join(pay, "fnav.py"), encoding="utf-8").read())
for n in ft.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        have.add(n.name)
    elif isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                have.add(t.id)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        # module aliases count: `import fnphone_pa as A` makes fnav.A real
        for a in n.names:
            have.add(a.asname or a.name.split(".")[0])
missing = sorted(want - have)
if missing:
    print("fnav.py is missing: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("  fnav.py satisfies communicator_live.py (%d attributes)" % len(want))
PYCHK
fi

# ---- core/ package ----------------------------------------------------------
# [CORE_PACKAGE_V1] The bundle's frognet_tuples.py is a SHIM:
#
#     _CORE = "/opt/frognet_semantic"
#     if os.path.isdir(_CORE): sys.path.insert(0, _CORE)
#     from core import frognet_tuples as _m
#
# That absolute path is a Linux node path. On Windows it does not exist, the
# isdir() guard skips it, and `from core import ...` then fails outright:
#
#     ModuleNotFoundError: No module named 'core'
#
# So core/ ships alongside the client, where the payload directory is already on
# sys.path because communicator.py lives there. The shim then resolves without
# needing the node layout, and the import stays shared-state-correct on a node.
CORE_SRC="${SRC_ROOT}/opt/frognet_semantic/core"
[[ -d "$CORE_SRC" ]] || die "no core/ at $CORE_SRC - frognet_tuples.py cannot resolve"
mkdir -p "$PAY/core"
n_core=0
for f in "$CORE_SRC"/*.py; do
    [[ -e "$f" ]] || continue
    b="$(basename "$f")"
    case "$b" in test_*|*.bak) continue ;; esac
    cp "$f" "$PAY/core/"
    n_core=$((n_core + 1))
done
[[ -f "$PAY/core/__init__.py" ]]      || die "core/__init__.py missing - not a package"
[[ -f "$PAY/core/frognet_tuples.py" ]] || die "core/frognet_tuples.py missing - the shim target"

# [MINIMUM_DIVERGENCE_V1] The payload ships the LIVE frognet_tuples.py verbatim.
# It was briefly rewritten here to "resolve against this package only". That was
# unnecessary: the live shim guards its /opt insert with os.path.isdir, which is
# false on Windows, and then `from core import frognet_tuples` resolves against
# the script directory, which is already on sys.path. Shipping core/ was the
# entire fix. Rewriting the shim on top of it made the Windows bundle a
# different code base from the one that works, for no gain.
#
# The rule: this package is the SAME code, plus core/, plus whatever is
# genuinely OS-specific. Every divergence has to earn itself.

# ---- bundles ----------------------------------------------------------------
# [THE_COMMUNICATOR_IS_THE_CLIENT_V1] No game bundles. None.
#
# This shipped every DIRECTORY under etc/frognet_bundles, which is how a C++
# source checkout ended up in a Windows Python client: games/ddnet is 58M and
# 1340 files of .cpp, .h, cmake, fonts and .map data, none of it runnable there
# and none of it buildable there. Measured 2026-08-11: an 862M zip for a 1.6M
# client, unpacked and then xcopied a second time into %LOCALAPPDATA% with
# Defender reading every file. That is the ten-minute install.
#
# The Windows client is the communicator and what the communicator imports.
# Games and the calendar are not that. They install afterwards, per bundle, with
# add_bundle.bat -- which is why that script exists.
#
# FROGNET_BUNDLES=a,b,c ships those bundles anyway, for building a demo image
# with something already in it. Named explicitly, never by "whatever is in the
# directory".
n_b=0
if [[ -n "${FROGNET_BUNDLES:-}" ]]; then
    IFS=',' read -ra _want <<< "$FROGNET_BUNDLES"
    for b in "${_want[@]}"; do
        b="$(echo "$b" | tr -d '[:space:]')"
        [[ -n "$b" ]] || continue
        [[ -d "$BUNDLES_SRC/$b" ]] || die "FROGNET_BUNDLES names '$b', which is not in $BUNDLES_SRC"
        cp -r "$BUNDLES_SRC/$b" "$PAY/bundles/$b"
        n_b=$((n_b + 1))
        echo "  bundle included by request: $b"
    done
fi

# ---- hygiene ----------------------------------------------------------------
# Ship no bytecode. Ship no editor droppings.
find "$PAY" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAY" \( -name "*.pyc" -o -name "*.pyo" -o -name "*~" -o -name ".DS_Store" \) \
     -delete 2>/dev/null || true

# CRLF for the batch files: cmd.exe tolerates LF but a user opening them in
# Notepad should not see one long line.
for f in "$PAY"/*.bat; do
    [[ -e "$f" ]] || continue
    sed -i 's/\r*$/\r/' "$f"
done

# ---- verify before shipping -------------------------------------------------
bad=0
while IFS= read -r f; do
    python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" \
        || { echo "SYNTAX FAIL: $f" >&2; bad=1; }
done < <(find "$PAY" -name "*.py")
(( bad == 0 )) || die "payload contains files that do not parse"

pyc=$(find "$PAY" -name "*.pyc" | wc -l)
(( pyc == 0 )) || die "bytecode in payload ($pyc files)"

# ---- pack -------------------------------------------------------------------
mkdir -p "$OUTDIR"
# [THE_CLIENT_IS_SMALL_V1] A chat client is a few megabytes. If the payload has
# grown past this, something that is not a bundle has been let in again --
# ddnet was 58M of C++ and 1340 files, and nothing said a word. Loud, and with
# the biggest offender named, so the next person does not have to go looking.
PAY_MB=$(du -sm "$PAY" | cut -f1)
MAX_MB="${FROGNET_MAX_PAYLOAD_MB:-8}"
if (( PAY_MB > MAX_MB )); then
    echo "WARNING: payload is ${PAY_MB}MB (expected under ${MAX_MB}MB)." >&2
    echo "         biggest things in it:" >&2
    du -sh "$PAY"/* "$PAY"/bundles/* 2>/dev/null | sort -rh | head -6 \
        | sed 's|^|           |' >&2
    echo "         set FROGNET_MAX_PAYLOAD_MB to raise the bar deliberately." >&2
fi

OUT="$(cd "$OUTDIR" && pwd)/${NAME}.zip"
rm -f "$OUT"
( cd "$PAY" && zip -qr "$OUT" . )        # contents at the ROOT of the zip

echo "built ${OUT}"
echo "  client .py     ${n_py}  (skipped ${n_skip} backup files)"
echo "  core modules   ${n_core}"
echo "  bundles        ${n_b}"
echo "  total files    $(find "$PAY" -type f | wc -l)"
echo "  size           $(du -h "$OUT" | cut -f1)"
echo
echo "Install on Windows: unpack the zip, double-click install.bat"
echo "  (needs Python 3 on PATH, with tkinter - the python.org installer has it)"
