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
package_communicator.py — build installable FrogNet Communicator client packages
(Linux tar.gz + Windows zip) from a source communicator/ directory.

The file list is NOT hand-maintained. Starting from the client entrypoints, we walk
the import graph and pull in exactly the LOCAL modules they reach (a module is "local"
iff <src>/<name>.py exists). External pip deps (cv2, sounddevice, av, numpy, PIL,
tkinter) are NOT bundled — they're documented runtime requirements.

A closure ORACLE then re-scans the staged tree and FAILS LOUDLY if any local module
that something imports was left out. That's the guarantee the old hand-list couldn't give.

Usage:
  python3 package_communicator.py [--src .] [--out /mnt/user-data/outputs] [--version DATE]
"""
from __future__ import annotations
import argparse, ast, os, sys, shutil, tarfile, zipfile, datetime

# Client entrypoints the package must be able to run. Their transitive LOCAL imports
# define the closure. (fnav is imported by communicator_live; listing it is harmless.)
ENTRYPOINTS = ["communicator_live.py", "fnav.py"]

# pip-installed runtime deps — never bundled, listed in the README instead.
EXTERNAL_HINT = ("cv2", "sounddevice", "av", "numpy", "PIL", "tkinter", "serial")


def _imports(path):
    """Top-level module names imported by one .py file (import X / from X import …)."""
    out = set()
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.level == 0 and n.module:        # absolute import only (flat layout)
                out.add(n.module.split(".")[0])
    return out


def closure(src, entrypoints):
    """BFS the import graph over LOCAL modules only. Returns the set of .py filenames."""
    universe = {f[:-3] for f in os.listdir(src) if f.endswith(".py")}
    staged, queue = set(), list(entrypoints)
    while queue:
        fname = queue.pop()
        if fname in staged:
            continue
        path = os.path.join(src, fname)
        if not os.path.isfile(path):
            continue
        staged.add(fname)
        for name in _imports(path):
            if name in universe:                 # only follow local modules
                queue.append(name + ".py")
    return staged, universe


def ascii_gate(bundle):
    """[SOURCE_IS_ASCII_V1] Refuse to ship a bundle containing a non-ASCII byte.

    A Pi with LANG unset gets a latin-1 stdout. print() of a string holding an em dash
    raises UnicodeEncodeError there, and in fnav.run() that killed the call thread
    outright -- a call dropped because of a punctuation mark in a status message.
    ASCII-only is already the rule for this tree; this makes shipping a violation
    impossible instead of a thing discovered on somebody's console.
    """
    bad = []
    for f in sorted(os.listdir(bundle)):
        if not f.endswith(".py"):
            continue
        raw = open(os.path.join(bundle, f), "rb").read()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as e:
            line = raw[:e.start].count(b"\n") + 1
            bad.append("%s:%d %r" % (f, line, raw[e.start:e.start + 1]))
    if bad:
        raise SystemExit("ASCII GATE FAILED -- non-ASCII in a shipped module:\n  "
                         + "\n  ".join(bad))
    print("ASCII GATE: PASS (%d modules)"
          % len([f for f in os.listdir(bundle) if f.endswith(".py")]))


def oracle(src, staged, universe):
    """Closure completeness: every LOCAL import inside a staged file must also be staged.
    Raises AssertionError naming the gap. This is what makes the package trustworthy."""
    missing = []
    for fname in sorted(staged):
        for name in _imports(os.path.join(src, fname)):
            if name in universe and (name + ".py") not in staged:
                missing.append(f"{fname} imports local '{name}' which was NOT bundled")
    if missing:
        raise AssertionError("CLOSURE INCOMPLETE:\n  " + "\n  ".join(missing))


RUN_SH = """#!/usr/bin/env bash
# launch the FrogNet Communicator (live A/V client). Works both from the unpacked
# package (code under ./bundle) and from an install (code flat next to this script).
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
if [ -f "$DIR/communicator_live.py" ]; then cd "$DIR"; else cd "$DIR/bundle"; fi
exec python3 communicator_live.py "$@"
"""

RUN_BAT = """@echo off
REM launch the FrogNet Communicator (live A/V client)
if exist "%~dp0communicator_live.py" (cd /d "%~dp0") else (cd /d "%~dp0bundle")
python communicator_live.py %*
"""

INSTALL_SH = """#!/usr/bin/env bash
# install the client to ~/.local/share/FrogNetCommunicator and add a launcher on PATH.
# Layout mirrors Windows: client .py FLAT at the app root, game bundles under ./bundles/
# (so the launcher's bundles-root resolution finds them).
set -eu
APP="${XDG_DATA_HOME:-$HOME/.local/share}/FrogNetCommunicator"
SRC="$(cd "$(dirname "$0")" && pwd)"
echo "installing to $APP"
mkdir -p "$APP/bundles"
cp -f "$SRC"/bundle/*.py "$APP/"
cp -f "$SRC/run_communicator.sh" "$SRC/add_bundle.sh" "$APP/"
chmod +x "$APP/run_communicator.sh" "$APP/add_bundle.sh"
mkdir -p "$HOME/.local/bin"
ln -sf "$APP/run_communicator.sh" "$HOME/.local/bin/frognet-communicator"
echo "done. run: frognet-communicator --name <you>"
echo "add a game:  $APP/add_bundle.sh <bundle>.tar.gz"
"""

# add a game bundle (a from-/ tar of etc/frognet_bundles/<name>) into a client install.
ADD_BUNDLE_SH = """#!/usr/bin/env bash
# add_bundle.sh <bundle.tar.gz> [install_dir] — install a game bundle into the client.
set -eu
PKG="${1:?usage: add_bundle.sh <bundle.tar.gz> [install_dir]}"
APP="${2:-${XDG_DATA_HOME:-$HOME/.local/share}/FrogNetCommunicator}"
[ -d "$APP/bundles" ] || { echo "not a client install: $APP" >&2; exit 1; }
TMP="$(mktemp -d)"; tar xzf "$PKG" -C "$TMP"
for d in "$TMP"/etc/frognet_bundles/*/; do
  name="$(basename "$d")"; echo "installing $name"
  rm -rf "$APP/bundles/$name"; cp -r "$d" "$APP/bundles/$name"
done
rm -rf "$TMP"
echo "done - restart the Communicator."
"""

ADD_BUNDLE_BAT = """@echo off
REM add_bundle.bat <install_dir> <bundle.tar.gz> — install a game bundle into the client.
setlocal
set INSTALL=%~1
set PKG=%~2
if "%INSTALL%"=="" echo usage: add_bundle.bat ^<install_dir^> ^<bundle.tar.gz^> & exit /b 2
if not exist "%INSTALL%\\bundles" echo not a client install: %INSTALL% & exit /b 1
set TMP=%TEMP%\\fnbundle_%RANDOM%
mkdir "%TMP%"
tar -xzf "%PKG%" -C "%TMP%"
for /d %%D in ("%TMP%\\etc\\frognet_bundles\\*") do (
  echo installing %%~nxD
  xcopy /e /i /y "%%D" "%INSTALL%\\bundles\\%%~nxD" >nul
)
rmdir /s /q "%TMP%"
echo done - restart the Communicator to see it.
endlocal
"""

# Windows installer mirrors the proven communicator.old/install-optional.bat layout:
# flat .py at %APP%, games under %APP%\\bundles\\, Start Menu + Desktop shortcuts.
INSTALL_BAT = """@echo off
REM FrogNet Communicator - Windows installer. No admin needed. Requires Python 3 (python.org).
setlocal enabledelayedexpansion
set SRC=%~dp0
set APP=%LOCALAPPDATA%\\FrogNetCommunicator
where python >nul 2>nul || (echo Python 3 required on PATH ^(python.org installer^). & pause & exit /b 1)
echo Installing to %APP%
if not exist "%APP%" mkdir "%APP%"
copy /y "%SRC%bundle\\*.py" "%APP%\\" >nul
REM [WIN_INSTALL_ASSETS_V1] assets\\ too, not just *.py: the splash, the logo sizes
REM and the eight ladder doors live there, so copying only *.py shipped a Windows
REM client with no artwork at all -- comms_brand then falls back to the plain card.
if exist "%SRC%bundle\\assets" xcopy /e /i /y /q "%SRC%bundle\\assets" "%APP%\\assets" >nul
copy /y "%SRC%run_communicator.bat" "%APP%\\" >nul
if not exist "%APP%\\bundles" mkdir "%APP%\\bundles"
set SM=%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$s=$w.CreateShortcut('%SM%\\FrogNet Communicator.lnk');" ^
  "$s.TargetPath='%APP%\\run_communicator.bat';$s.WorkingDirectory='%APP%';$s.Save();" ^
  "$d=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\FrogNet Communicator.lnk');" ^
  "$d.TargetPath='%APP%\\run_communicator.bat';$d.WorkingDirectory='%APP%';$d.Save()"
echo Installed. Launch from Start Menu / Desktop, or run %APP%\\run_communicator.bat --name ^<you^>
pause
endlocal
"""

README = """FrogNet Communicator — client package

Requires: Python 3 with tkinter (use the python.org installer on Windows -- the
Microsoft Store build has a restricted %LOCALAPPDATA%).

A/V needs four pip modules. They are NOT in the bundle:

    pip install opencv-python av sounddevice samplerate

  opencv-python  camera capture        without it: no video
  av             video encode/decode   without it: no video
  sounddevice    audio devices         without it: no audio
  samplerate     48k->16k resampling   without it: no audio

Without all four the client still runs -- presence, chat and the transcript are the
text floor, a real rung of the ladder -- and Start a call says exactly what is
missing instead of failing. NOTE: no working MICROPHONE means no video either, at any
bandwidth: every video rung rides the audio bed, so the ceiling drops to L2 WHISPER.

Install:
  Linux:    bash install.sh
  Windows:  double-click install.bat   (or run it from cmd)

Run:
  frognet-communicator --name <you>            (Linux, after install)
  run_communicator.bat --name <you>            (Windows)

--name is the only required flag: the client resolves mediahost.frognet itself and
gathers the call there. There is no --host.

  --cam N            camera index, if the wrong one comes up
  --no-splash        skip the eight-door opening
  --no-video/--no-audio
  --relay HOST:PORT  dial a specific relay instead of the elected media host
  --serve [BIND:PORT] host the relay in this process
  Full list: run with --help.

First run: open "Camera and sound" in the lobby to pick the camera and microphone --
live preview, level meter, test tone. Saved to the pond, so once per machine.

NOTE: no microphone means NO VIDEO, at any bandwidth. Every video rung rides the
audio bed, so the ceiling drops to L2 WHISPER. The lobby says so in red.

Verify an install without making a call:
  cd bundle && python test_comms_ui_oracle.py

Add a game bundle later:
  Linux:    tar xzf <bundle>.tar.gz -C ~/.local/share/FrogNetCommunicator
  Windows:  add_bundle.bat "%LOCALAPPDATA%\\FrogNetCommunicator" <bundle>.tar.gz
"""


def build(src, out, version):
    staged, universe = closure(src, ENTRYPOINTS)
    oracle(src, staged, universe)               # FAILS here if anything local is missing

    work = os.path.join("/tmp", f"fnc_pkg_{version}")
    bundle = os.path.join(work, "bundle")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(bundle)
    for f in sorted(staged):
        shutil.copy2(os.path.join(src, f), os.path.join(bundle, f))

    # [SHIP_MODULES_NOT_SHIMS_V1] Some names next to the client are SHIMS that alias a
    # module living under /opt/frognet_semantic:
    #     _CORE = "/opt/frognet_semantic"; from core import frognet_tuples as _m
    # That resolves on a node and is an ImportError everywhere else. Shipped verbatim
    # it meant the Windows bundle could never start -- comms_control's first import
    # died on a Linux absolute path. A bundle has to be self-contained, so where a
    # staged file is a shim, ship what it points AT.
    core_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(src))),
                            "core")
    if not os.path.isdir(core_dir):
        core_dir = "/opt/frognet_semantic/core"
    for f in sorted(staged):
        dst = os.path.join(bundle, f)
        try:
            body = open(dst, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if "SHIM" not in body or "from core import" not in body:
            continue
        real = os.path.join(core_dir, f)
        if not os.path.exists(real):
            raise SystemExit("SHIM %s points at core/%s which is not present at %s -- "
                             "cannot build a self-contained bundle" % (f, f, core_dir))
        shutil.copy2(real, dst)
        print("  resolved shim -> %s (from %s)" % (f, core_dir))
    # assets/ ships WHOLE. The import closure only finds .py, so the logo the shell
    # loads at startup would otherwise be missing from every bundle and nobody would
    # notice until a node showed a splash with no mark on it. Copy the directory,
    # not a list of filenames, so a new size added to assets/ ships without anyone
    # remembering to add it here.
    # [SHIP_THE_ORACLES_V1] They import only what the bundle already carries, and an
    # install with no way to check itself gets checked by making a call in front of
    # somebody.
    for t in ("test_comms_ui_oracle.py", "test_shared_link_oracle.py",
              "test_quiet_mode_oracle.py"):
        tp = os.path.join(src, t)
        if os.path.exists(tp):
            shutil.copy2(tp, os.path.join(bundle, t))

    ascii_gate(bundle)      # FAILS here on a non-ASCII byte in a shipped module

    asset_src = os.path.join(src, "assets")
    if os.path.isdir(asset_src):
        shutil.copytree(asset_src, os.path.join(bundle, "assets"))
    # launcher + installer + readme at package root
    files = {
        "run_communicator.sh": RUN_SH, "run_communicator.bat": RUN_BAT,
        "install.sh": INSTALL_SH, "install.bat": INSTALL_BAT,
        "add_bundle.sh": ADD_BUNDLE_SH, "add_bundle.bat": ADD_BUNDLE_BAT,
        "README.txt": README,
    }
    for name, body in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)

    os.makedirs(out, exist_ok=True)
    tgz = os.path.join(out, f"frognet-communicator-linux-{version}.tar.gz")
    zfn = os.path.join(out, f"FrogNetCommunicator-windows-{version}.zip")
    with tarfile.open(tgz, "w:gz") as t:
        t.add(work, arcname="FrogNetCommunicator")
    # [ZIP_ARCNAME_MATCHES_TAR_V1] The tar uses arcname="FrogNetCommunicator"; the zip
    # walked with relpath against dirname(work), so it inherited the TEMP directory
    # name (fnc_pkg_<version>). Windows users therefore extracted a folder with a
    # different name from every instruction, every path in README.txt, and the tar.
    with zipfile.ZipFile(zfn, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, fs in os.walk(work):
            for f in fs:
                p = os.path.join(root, f)
                z.write(p, os.path.join("FrogNetCommunicator",
                                        os.path.relpath(p, work)))
    return staged, universe, tgz, zfn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default="/mnt/user-data/outputs")
    ap.add_argument("--version", default=datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    a = ap.parse_args()
    staged, universe, tgz, zfn = build(a.src, a.out, a.version)
    print(f"closure: {len(staged)} local modules (of {len(universe)} in source)")
    print(f"unbundled (external/unreached): not shipped — pip deps {', '.join(EXTERNAL_HINT)}")
    print(f"linux:   {tgz}")
    print(f"windows: {zfn}")
    print("CLOSURE ORACLE: PASS")


if __name__ == "__main__":
    main()
