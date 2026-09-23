#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""install.py -- install the client half of a FrogNet RAM application.

    python3 install.py --host HOST --port PORT --dir DIR [--client python|cpp]     (Linux)
    py -3   install.py --host HOST --port PORT --dir DIR [--client python|cpp]     (Windows)

    --client python   (default) the Python client; needs Python 3.8+ to run
    --client cpp      the native binary built for THIS platform, if the package
                      carries one; it needs nothing else to run. If the package
                      has no binary for this platform that is an error -- the
                      installer does not quietly install the other one.

    --host, --port   where this application's memory answers (the FNW1 listener)
    --dir            where to install the client

Copies the client into DIR, records where its memory is in DIR/store.json, and
writes a launcher (DIR/<app> on Linux, DIR\\<app>.cmd on Windows). Standard
library only; needs no administrator rights and touches nothing outside DIR.
Built by frogram_package.py; what it installs is described by package.json.

NO FALLBACKS: the installed client reads its memory's address from store.json
and nowhere else. If the package does not match its manifest, nothing is
installed.
"""
import argparse, hashlib, json, os, platform, shutil, stat, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg):
    print("FATAL: " + msg, file=sys.stderr); sys.exit(1)


def main():
    pkg = json.load(open(os.path.join(HERE, "package.json")))
    ap = argparse.ArgumentParser(description="install the %s client" % pkg["app"])
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--client", choices=("python", "cpp"), default="python")
    a = ap.parse_args()
    for line in open(os.path.join(HERE, "MANIFEST.sha256")):
        want, name = line.rstrip("\n").split("  ", 1)
        got = hashlib.sha256(open(os.path.join(HERE, name), "rb").read()).hexdigest()
        if got != want:
            die("package contents do not match MANIFEST.sha256: " + name)
    dest = os.path.abspath(a.dir)
    os.makedirs(dest, exist_ok=True)
    store = {"store": "%s:%d" % (a.host, a.port), "app": pkg["app"], "version": pkg["version"]}
    json.dump(store, open(os.path.join(dest, "store.json"), "w"), indent=1)
    if a.client == "cpp":
        plat = ("windows-" if os.name == "nt" else "linux-") + {"AMD64": "x86_64"}.get(platform.machine(), platform.machine())
        rel = pkg.get("native", {}).get(plat)
        if not rel:
            die("this package has no C++ client for %s (it has: %s)" % (plat, ", ".join(sorted(pkg.get("native", {}))) or "none"))
        launcher = os.path.join(dest, os.path.basename(rel))
        shutil.copyfile(os.path.join(HERE, rel), launcher)
        os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        if sys.version_info < (3, 8):
            die("the Python client needs Python 3.8 or later")
        for name in pkg["files"]:
            shutil.copyfile(os.path.join(HERE, "client", name), os.path.join(dest, name))
        entry = os.path.join(dest, pkg["entry"])
        if os.name == "nt":
            launcher = os.path.join(dest, pkg["app"] + ".cmd")
            open(launcher, "w").write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, entry))
        else:
            launcher = os.path.join(dest, pkg["app"])
            open(launcher, "w").write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, entry))
            os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("[%s] installed %s (%s client) in %s" % (pkg["app"], pkg["version"], a.client, dest))
    print("[%s] its memory is at %s:%d" % (pkg["app"], a.host, a.port))
    print("[%s] run:  %s --help" % (pkg["app"], launcher))


if __name__ == "__main__":
    main()
