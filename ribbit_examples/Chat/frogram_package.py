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
################################################################
"""frogram_package.py -- build the deployment PAIR for a FrogNet RAM application.

Run on a FrogNet development host:

    python3 frogram_package.py --app apps/frogchat --out dist [--version V]

Reads <app>/frogram_app.json and writes two packages:

    dist/<app>-server-<V>.tgz    install.sh  --port PORT --dir DIR
    dist/<app>-client-<V>.tgz    install.py  --host HOST --port PORT --dir DIR [--client python|cpp]

If the app has a C++ client (manifest "client_cpp"), it is BUILT HERE, on the
development host, and the binaries ride in the client package under
bin/<platform>/ :
    --cpp-targets linux            (default) this host's architecture, e.g. linux-aarch64 on a Pi
    --cpp-targets linux,windows    also cross-build windows-x86_64 with mingw-w64
    --cpp-targets none             ship the Python client only
The build happens in a scratch directory; the source tree is not written to.

The server package sets up, on a machine that is NOT a FrogNet node: the
application's own database (named by the app's manifest, never "FrogNet"), a
loopback-only vhost serving the app's PHP API, and the FNW1 listener on the
public port. The client package installs the client and records where its
memory is. Neither package contains or needs anything else from the tree.

Packaging rules applied: no bytecode is shipped; every file is listed with its
SHA-256 in MANIFEST.sha256 and both installers refuse a package that does not
match; and each package is VERIFIED BY EXTRACTING IT into a scratch directory
and checking it there -- manifest, shell syntax, Python syntax, PHP syntax when
php is present -- before it is reported as built.

NO FALLBACKS. A manifest field that is missing, a file that is not there, or a
check that cannot pass stops the build.
"""
from __future__ import annotations

import argparse, hashlib, io, json, os, shutil, subprocess, sys, tarfile, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg: str):
    print("FATAL: " + msg, file=sys.stderr); sys.exit(1)


def need(d: dict, *path: str):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur or cur[k] in ("", None, []):
            die("frogram_app.json: missing %s" % ".".join(path))
        cur = cur[k]
    return cur


def put(stage: str, rel: str, src: str, mode: int = 0o644) -> None:
    if not os.path.isfile(src):
        die("file not found: " + src)
    dst = os.path.join(stage, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def write(stage: str, rel: str, text: str) -> None:
    dst = os.path.join(stage, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "w", newline="\n").write(text)


def seal(stage: str) -> None:
    lines = []
    for dp, dn, fn in os.walk(stage):
        dn.sort()
        if "__pycache__" in dp:
            die("bytecode directory in the stage: " + dp)
        for f in sorted(fn):
            if f.endswith((".pyc", ".pyo")):
                die("bytecode in the stage: " + f)
            rel = os.path.relpath(os.path.join(dp, f), stage).replace(os.sep, "/")
            lines.append("%s  %s" % (hashlib.sha256(open(os.path.join(dp, f), "rb").read()).hexdigest(), rel))
    write(stage, "MANIFEST.sha256", "\n".join(sorted(lines, key=lambda l: l.split("  ", 1)[1])) + "\n")


def tar(stage: str, top: str, out_path: str) -> None:
    def clean(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0; ti.uname = ti.gname = "root"
        return ti
    with tarfile.open(out_path, "w:gz") as t:
        t.add(stage, arcname=top, filter=clean)


def verify(pkg_path: str, top: str) -> None:
    """Extract the package the way its recipient will, and check it THERE."""
    scratch = tempfile.mkdtemp(prefix="frogram_verify_")
    try:
        with tarfile.open(pkg_path) as t:
            t.extractall(scratch)
        root = os.path.join(scratch, top)
        for line in open(os.path.join(root, "MANIFEST.sha256")):
            want, rel = line.rstrip("\n").split("  ", 1)
            if hashlib.sha256(open(os.path.join(root, rel), "rb").read()).hexdigest() != want:
                die("%s: %s does not match its manifest after extraction" % (pkg_path, rel))
        listed = {l.rstrip("\n").split("  ", 1)[1] for l in open(os.path.join(root, "MANIFEST.sha256"))}
        for dp, _, fn in os.walk(root):
            for f in fn:
                p = os.path.join(dp, f); rel = os.path.relpath(p, root).replace(os.sep, "/")
                if rel != "MANIFEST.sha256" and rel not in listed:
                    die("%s: %s is in the package but not in its manifest" % (pkg_path, rel))
                if f.endswith(".sh"):
                    r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
                    if r.returncode:
                        die("%s: %s: %s" % (pkg_path, rel, r.stderr.strip()))
                elif f.endswith(".py"):
                    try:
                        compile(open(p, encoding="utf-8").read(), rel, "exec")     # no bytecode written
                    except SyntaxError as e:
                        die("%s: %s: %s" % (pkg_path, rel, e))
                elif f.endswith(".php") and shutil.which("php"):
                    r = subprocess.run(["php", "-l", p], capture_output=True, text=True)
                    if r.returncode:
                        die("%s: %s: %s" % (pkg_path, rel, (r.stdout + r.stderr).strip()))
                elif f.endswith(".json"):
                    json.load(open(p))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def build_cpp(appdir: str, m: dict, targets: list, stage: str) -> dict:
    """Build the C++ client for each target into stage/bin/<platform>/. Returns
    {platform: relative path}. A target that cannot be built stops the package."""
    import platform as _pf
    cpp = m.get("client_cpp")
    if not cpp or not targets:
        return {}
    src = os.path.join(appdir, need(m, "client_cpp", "dir"))
    lib = os.path.join(HERE, "cpp")
    name = need(m, "client_cpp", "binary")
    if not os.path.isfile(os.path.join(src, "Makefile")):
        die("no Makefile in " + src)
    built = {}
    for t in targets:
        out = tempfile.mkdtemp(prefix="frogram_cpp_")
        if t == "linux":
            plat, goal, produced = "linux-" + _pf.machine(), os.path.join(out, name), name
            if not shutil.which("g++"):
                die("g++ is not installed (sudo apt-get install build-essential)")
        elif t == "windows":
            plat, goal, produced = "windows-x86_64", "windows", name + ".exe"
            if not shutil.which("x86_64-w64-mingw32-g++-posix"):
                die("the Windows cross compiler is not installed (sudo apt-get install g++-mingw-w64-x86-64-posix)")
        else:
            die("unknown --cpp-targets entry: %r (linux, windows, none)" % t)
        r = subprocess.run(["make", "-s", "-C", src, "OUT=" + out, "LIB=" + lib, goal], capture_output=True, text=True)
        if r.returncode or not os.path.isfile(os.path.join(out, produced)):
            die("C++ build for %s failed:\n%s" % (plat, (r.stdout + r.stderr).strip()))
        rel = "bin/%s/%s" % (plat, produced)
        put(stage, rel, os.path.join(out, produced), 0o755)
        shutil.rmtree(out, ignore_errors=True)
        built[plat] = rel
        print("built C++ client for %s" % plat)
    return built


def main() -> int:
    ap = argparse.ArgumentParser(description="build the server/client deployment pair for a FrogNet RAM app")
    ap.add_argument("--app", required=True, help="application directory holding frogram_app.json")
    ap.add_argument("--out", required=True, help="directory to write the two packages into")
    ap.add_argument("--version", default=time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
    ap.add_argument("--cpp-targets", default="linux", help="linux | linux,windows | none")
    a = ap.parse_args()
    appdir = os.path.abspath(a.app)
    mpath = os.path.join(appdir, "frogram_app.json")
    if not os.path.isfile(mpath):
        die("no frogram_app.json in " + appdir)
    m = json.load(open(mpath))
    app, db = need(m, "app"), need(m, "db_name")
    if db.lower() == "frognet":
        die('db_name is "%s": the application names its own database, and it is not FrogNet' % db)
    for v in (app, db, need(m, "db_user")):
        if not v.replace("_", "").replace("-", "").isalnum():
            die("app, db_name and db_user must be plain identifiers: %r" % v)
    os.makedirs(a.out, exist_ok=True)
    built = []

    # ---- server ---------------------------------------------------------------
    stage = tempfile.mkdtemp(prefix="frogram_server_")
    put(stage, "install.sh", os.path.join(HERE, "templates", "server_install.sh"), 0o755)
    put(stage, "server/ram_listener.py", os.path.join(HERE, "common", "ram_listener.py"), 0o755)
    put(stage, "probe_ram.py", os.path.join(HERE, "common", "probe_ram.py"), 0o755)
    put(stage, "server/schema.sql", os.path.join(appdir, need(m, "server", "schema")))
    for f in need(m, "server", "api"):
        put(stage, "server/api/" + os.path.basename(f), os.path.join(appdir, f))
    write(stage, "package.env", "".join("%s=%s\n" % kv for kv in (
        ("APP", app), ("VERSION", a.version), ("DB_NAME", db), ("DB_USER", m["db_user"]),
        ("DEFAULT_PORT", int(need(m, "default_port"))), ("PATH_PREFIX", need(m, "server", "path_prefix")),
        ("API_CONFIG", need(m, "server", "api_config")))))
    seal(stage)
    top = "%s-server-%s" % (app, a.version); out = os.path.join(a.out, top + ".tgz")
    tar(stage, top, out); shutil.rmtree(stage); verify(out, top); built.append(out)

    # ---- client ---------------------------------------------------------------
    stage = tempfile.mkdtemp(prefix="frogram_client_")
    put(stage, "install.py", os.path.join(HERE, "templates", "client_install.py"), 0o755)
    put(stage, "probe_ram.py", os.path.join(HERE, "common", "probe_ram.py"), 0o755)
    names = []
    for f in need(m, "client", "files"):
        put(stage, "client/" + os.path.basename(f), os.path.join(appdir, f)); names.append(os.path.basename(f))
    if need(m, "client", "entry") not in names:
        die("client.entry %r is not one of client.files" % m["client"]["entry"])
    if m.get("oracle"):
        put(stage, "oracle/" + os.path.basename(m["oracle"]), os.path.join(appdir, m["oracle"]))
    targets = [] if a.cpp_targets == "none" else [t.strip() for t in a.cpp_targets.split(",") if t.strip()]
    native = build_cpp(appdir, m, targets, stage)
    write(stage, "package.json", json.dumps({"app": app, "version": a.version, "files": names, "native": native,
                                             "entry": m["client"]["entry"],
                                             "default_port": int(m["default_port"])}, indent=1) + "\n")
    seal(stage)
    top = "%s-client-%s" % (app, a.version); out = os.path.join(a.out, top + ".tgz")
    tar(stage, top, out); shutil.rmtree(stage); verify(out, top); built.append(out)

    for p in built:
        print("built and verified  %s  (%d bytes)" % (p, os.path.getsize(p)))
    print("server:  tar xzf %s && sudo ./%s-server-%s/install.sh --port %d --dir /opt/%s"
          % (os.path.basename(built[0]), app, a.version, int(m["default_port"]), app))
    print("client:  tar xzf %s && python3 %s-client-%s/install.py --host <server> --port %d --dir <dir>%s"
          % (os.path.basename(built[1]), app, a.version, int(m["default_port"]),
             "   [--client cpp]" if native else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
