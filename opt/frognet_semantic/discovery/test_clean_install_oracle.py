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
test_clean_install_oracle.py - a CLEAN machine, installed, then merged, in the sim.

Everything before the first merge used to be untestable here: the simulator always
started from an already-installed node, so an install that silently failed to create
the database user could only be found by building a real box. Field case 2026-07-23:
frognet_install.sh D8 holds correct `CREATE USER 'FrogUser'` SQL that a fresh machine
NEVER REACHES, because it sits below `if [[ -f /frognet_db.sql ]]; then ... else die`
- no restore dump on a clean box, so the phase dies first and the node comes up with
no FrogUser at all. api.php then cannot authenticate and every sensor write fails.

This oracle runs the two things that matter, from the REAL scripts:

  PLANE 1 (install)  clean machine -> databasehost postconditions:
                     both databases, FrogUser on localhost AND '%', grants on both.
                     Plus a REACHABILITY check: the user-creating statement must not
                     be guarded by a file a clean machine does not have.

  PLANE 2 (merge)    the installed node then converges, across every shape the
                     simulator builds, so "installs" and "actually forms a mesh" are
                     proven in one run instead of two disconnected ones.
"""
import os as _os
import re
import sys

_os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")

from discovery.sim.install_plane import (Machine, apply_script,
                                         guarded_by_missing_file, simulate_install,
                                         apply_filesystem)

PROBS = []
BIN = "/usr/local/bin"
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TREE = _os.path.abspath(_os.path.join(_HERE, "..", "..", ".."))   # tree root


def check(ok, label):
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        PROBS.append(label)


def _script(name):
    """Resolve a script, TREE FIRST.

    [TEST_THE_TREE_YOU_ARE_IN_V1] This used to prefer the installed path
    (/usr/local/bin/...) and fall back to the tree. That is backwards, and it made
    the oracle lie on a real node: running the simulator from a fresh checkout on a
    box with an older FrogNet installed, it read the INSTALLED installer and the
    INSTALLED manifest and reported a regression against code the checkout does not
    contain. Reproduced on BrokerHost 2026-09-01, where C1 had laid down an earlier
    release before the checkout was updated.

    An oracle run from a tree must test that tree. The installed copy is the
    fallback, for the container case where there is no tree above this file.

    frognet_install.sh lives in the installer/ subdir (release tooling kept off the
    flat node-tool namespace), so search that first.
    """
    for p in (_os.path.join(_TREE, "usr", "local", "bin", "installer", name),
              _os.path.join(_TREE, "usr", "local", "bin", name),
              _os.path.join(BIN, "installer", name), _os.path.join(BIN, name)):
        if _os.path.exists(p):
            return p
    return None


def _manifest():
    """The world manifest, TREE FIRST. Same reason as _script()."""
    for p in (_os.path.join(_TREE, "usr", "local", "lib", "frognet_world_manifest.sh"),
              "/usr/local/lib/frognet_world_manifest.sh"):
        if _os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------------- #
def plane_install():
    print("=== PLANE 1: clean machine -> databasehost ===")
    dbh = _script("install_databasehost.sh")
    if not dbh:
        check(False, "install_databasehost.sh found")
        return

    # A CLEAN machine: no restore dump, no databases, no users. Only the payload
    # the world tar lays down (config.php) is present.
    clean = Machine("clean", files={"/var/www/html/config.php":
                                    "define('DB_USER','FrogUser');"
                                    "define('DB_PASS','secret');"
                                    "define('DB_NAME','FrogNet');"})
    apply_script(clean, dbh, subs={"DB_USER": "FrogUser", "DB_PASS": "secret",
                                   "DB_NAME": "FrogNet"})
    m = clean.mysql

    check(m.has_user("FrogUser", "localhost"),
          "clean install creates FrogUser@localhost")
    check(m.has_user("FrogUser", "%"),
          "clean install creates FrogUser@'%' (remote nodes reach a floating dbhost)")
    check("FrogNetFamily" in m.databases,
          "clean install creates FrogNetFamily (Communicator store)")
    check(m.can_reach("FrogNet", "FrogUser", "%"),
          "FrogUser@'%' is granted on FrogNet")
    check(m.can_reach("FrogNetFamily", "FrogUser", "%"),
          "FrogUser@'%' is granted on FrogNetFamily")

    # REACHABILITY: the statement may be correct and still never run.
    inst = _script("frognet_install.sh")
    if inst:
        guard = guarded_by_missing_file(inst, "CREATE USER 'FrogUser'", clean)
        check(guard is None,
              "FrogUser creation is reachable on a clean machine "
              f"(not gated behind {guard or 'nothing'})")


# --------------------------------------------------------------------------- #
def plane_reinstall():
    print("\n=== PLANE 1b: reinstall lands on a CLEAN machine ===")
    inst = _script("frognet_install.sh")
    if not inst:
        # frognet_install.sh is RELEASE TOOLING: it lives in the release tar beside
        # frognet_world.tgz and is deliberately excluded from the world tar, so it is
        # absent both from a code-only tree and from an installed node. Its contract
        # is checked when building a release, not by the node gate. Reporting SKIP
        # here (rather than FAIL) keeps that boundary honest; the checks below on
        # frognet_reset.sh still run, because that one DOES ship.
        print("  SKIP installer contract - frognet_install.sh is release tooling, "
              "not part of the world tar")
    else:
        text = open(inst, encoding="utf-8", errors="replace").read()
        check("--preserve)" in text,
              "installer accepts --preserve (reinstall in place)")
        check("frognet_reset.sh" in text,
              "reinstall DELEGATES the wipe to frognet_reset.sh (not a second copy)")
        check(re.search(r'PRESERVE"?\s*-eq\s*1', text) is not None,
              "wipe is the DEFAULT; --preserve is what skips it")
        inline_nuke = re.findall(r"rm\s+-rf\s+/(opt/frognet_semantic|etc/frognet)\b", text)
        check(not inline_nuke,
              f"installer does not reimplement the wipe inline (found {len(inline_nuke)})")
        check("No prior FrogNet install detected" in text,
              "first install on a clean machine skips the reset entirely")
        check("--yes" in text or "-y" in text,
              "installer drives the reset non-interactively (--yes)")
        # [SKIP_SATISFIED_V1 / VENV_SURVIVES_RESET_V1] a reinstall must not redo
        # work that is already done. The venv is arch-specific BUILD OUTPUT, not
        # state: destroying it forces a pip self-upgrade plus ~24 wheels (several
        # of which compile on ARM) on every single install, for no gain.
        check("_VENV_STASH" in text,
              "reinstall CARRIES the venv across the reset (not rebuilt every run)")
        check("--rebuild-venv" in text,
              "--rebuild-venv exists to force a full venv rebuild when wanted")
        # [PRESERVE_IS_AN_UPGRADE_V1] --preserve is an UPGRADE, not just "skip the
        # reset": it must not restore the shipped dump over live data, and it MUST
        # migrate the schema or an upgraded node keeps yesterday's tables.
        _d8 = text[text.find('phase "D8'):text.find('phase "E1')] if 'phase "D8' in text else ""
        check("PRESERVE" in _d8,
              "--preserve is handled in the database phase (upgrade, not reset)")
        check(_d8.count("frognet_db.sql") > 0 and "no dump restore" in _d8,
              "--preserve does NOT restore the dump over live data")
        check("schema_fixups.sql" in _d8,
              "schema migrations are applied (a changed schema reaches an existing DB)")
        check("removed" in text or "Removes NOTHING" in text,
              "--preserve documented as removing nothing")
        check("dpkg-query" in text,
              "APT installs only MISSING packages (not the whole list every run)")
        check('pip3 show' in text,
              "pip installs only MISSING packages (not the whole list every run)")

    # [NO_MISSING_REQUIREMENTS_V1] The installer invokes frognet_reset.sh, so
    # frognet_reset.sh must SHIP. A release whose reinstall path depends on a file
    # that is not in the world tar is a broken release, and "it's probably already
    # on the box" is not a dependency. This check is unconditional.
    rst = _script("frognet_reset.sh")
    check(rst is not None,
          "frognet_reset.sh ships (installer's reinstall dependency is satisfied)")
    if rst:
        rtext = open(rst, encoding="utf-8", errors="replace").read()
        for flag in ("--yes", "--dry-run", "--keep-db", "--keep-identity"):
            check(flag in rtext, f"frognet_reset.sh supports {flag}")
        check("/opt/frognet_semantic" in rtext and "/etc/fnid" in rtext,
              "reset covers the FrogNet-owned tree and the node GUID")
        check("rm -rf /etc/apache2" not in rtext and "rm -rf /etc/mysql" not in rtext,
              "reset never nukes shared OS dirs wholesale")

    # [LIB_MANIFEST_AUTODISCOVER_V1] A hand-written file list under /usr/local/lib
    # drifts every time a lib is added (frognet_log.sh, then frognet_trace). The
    # builder must DISCOVER FrogNet libs, not depend on somebody remembering.
    bld = _script("frognet_build_release.sh")
    if not bld:
        print("  SKIP builder manifest - frognet_build_release.sh is release tooling")
    else:
        btext = open(bld, encoding="utf-8", errors="replace").read()
        check("LIB_MANIFEST_AUTODISCOVER_V1" in btext,
              "builder auto-discovers /usr/local/lib/frognet* (manifest cannot drift)")
        check("/usr/local/lib/frognet*" in btext,
              "auto-discovery globs the FrogNet lib namespace")
        check("refusing to build an incomplete world" in btext,
              "builder still fails LOUD on a missing required manifest path")



def _dirs():
    for b, w in ((BIN, "/var/www/html"),
                 (_os.path.join(_TREE, "usr", "local", "bin"),
                  _os.path.join(_TREE, "var", "www", "html"))):
        if _os.path.exists(_os.path.join(b, "install_databasehost.sh")):
            return b, w
    return None, None


def plane_three_modes():
    print("\n=== PLANE 1c: SIMULATED installs - clean / reset / preserve ===")
    bin_dir, web_dir = _dirs()
    if not bin_dir:
        check(False, "install scripts found for simulation")
        return
    cfg = {"/var/www/html/config.php": "define('DB_PASS','secret');"}

    # ---- CLEAN: virgin box ------------------------------------------------- #
    mc = Machine("clean", files=cfg)
    simulate_install(mc, "clean", bin_dir, web_dir, dump_present=False)
    check(mc.mysql.has_user("FrogUser", "%"), "[clean]    FrogUser@'%' created")
    check("FrogNetFamily" in mc.mysql.databases, "[clean]    FrogNetFamily created")
    check(mc.mysql.migrated, "[clean]    schema migrations applied")

    # ---- RESET: prior install, no --preserve ------------------------------- #
    mr = Machine("prior", files=cfg)
    mr.installed = True
    mr.venv = True
    mr.mysql.execute("CREATE DATABASE FrogNet; CREATE USER 'FrogUser'@'%';")
    mr.mysql.seed_live_data("FrogNet", "old-node-rows")
    simulate_install(mr, "reset", bin_dir, web_dir, dump_present=True)
    check(not mr.mysql.has_row("FrogNet", "old-node-rows"),
          "[reset]    old rows are GONE (a reset really resets)")
    check(mr.mysql.has_user("FrogUser", "%"),
          "[reset]    FrogUser@'%' recreated after the wipe")
    check(mr.venv, "[reset]    venv CARRIED across the wipe (not rebuilt)")

    # ---- PRESERVE: prior install, upgrade in place ------------------------- #
    mp = Machine("prior", files=cfg)
    mp.installed = True
    mp.venv = True
    mp.mysql.execute("CREATE DATABASE FrogNet; CREATE USER 'FrogUser'@'%';")
    mp.mysql.seed_live_data("FrogNet", "LIVE-USER-DATA")
    simulate_install(mp, "preserve", bin_dir, web_dir, dump_present=True)
    check(mp.mysql.has_row("FrogNet", "LIVE-USER-DATA"),
          "[preserve] LIVE DATA SURVIVES (shipped dump not restored over it)")
    check("FrogNet" in mp.mysql.databases,
          "[preserve] databases not dropped")
    check(mp.mysql.migrated,
          "[preserve] schema STILL migrated (a changed schema reaches the live DB)")
    check(mp.mysql.has_user("FrogUser", "%"),
          "[preserve] FrogUser still present after upgrade")
    check(mp.venv, "[preserve] venv untouched")



def plane_filesystem():
    print("\n=== PLANE 1d: SIMULATED filesystem - dirs the services need ===")
    inst = _script("frognet_install.sh")
    if not inst:
        print("  SKIP filesystem plane - frognet_install.sh is release tooling")
        return
    m = Machine("clean")
    apply_filesystem(m, inst)

    # apache2 will not start without its log dir; that takes api.php, getHosts.php
    # and frognet_echo.php down with it, which reads on-box as "the mesh is broken".
    check("/var/log/apache2" in m.dirs,
          "install creates /var/log/apache2 (apache cannot start without it)")
    check(m.dirs.get("/var/log/apache2") == "777",
          f"/var/log/apache2 mode is 777 (got {m.dirs.get('/var/log/apache2') or 'default'})")

    # the rest of the tree the daemons assume exists
    for d in ("/var/log/frognet", "/var/log/mysql", "/run/frognet",
              "/etc/frognet", "/etc/sentinels", "/var/lib/frognet-tunnel/active",
              "/var/www/html", "/etc/dnsmasq.d"):
        check(d in m.dirs, f"install creates {d}")

    # secrets must NOT be world-readable
    check(m.dirs.get("/etc/wireguard") == "700",
          "/etc/wireguard stays 700 (private keys)")



def plane_reset_release_symmetry():
    """[RESET_RELEASE_SYMMETRY_V1] Anything frognet_reset.sh DELETES, the release
    must be able to PUT BACK. /usr/local/lib/frognet_log.sh was deleted by the reset
    and never listed in WORLD_PATHS, so a reset+reinstall left every shell tool with
    `flog_init: command not found` and a merge that aborts. That asymmetry is the bug
    class; this is the check for it."""
    print("\n=== PLANE 1e: reset deletes nothing the release cannot restore ===")
    rst = _script("frognet_reset.sh")
    bld = _script("frognet_build_release.sh")
    if not rst:
        check(False, "frognet_reset.sh found")
        return
    rtext = open(rst, encoding="utf-8", errors="replace").read()
    # explicit FILE targets the reset removes (dirs are rebuilt by the installer)
    targets = set(re.findall(r"nuke\s+((?:/[^\s]+\s*)+)", rtext))
    files = set()
    for grp in targets:
        for tok in grp.split():
            if tok.startswith("/") and re.search(r"\.(sh|conf|sql|py)$", tok):
                files.add(tok)
    if not files:
        check(True, "reset removes no individual files needing restore")
        return
    # [ONE_MANIFEST_V1] The path list moved out of the builder into
    # frognet_world_manifest.sh. Read whichever holds it, so this plane does not
    # silently pass on an empty set the way it silently failed on one.
    wtext = ""
    _m = _manifest()
    if _m:
        wtext = open(_m, encoding="utf-8", errors="replace").read()
    if not wtext and bld:
        wtext = open(bld, encoding="utf-8", errors="replace").read()
    check(bool(wtext), "a world path list was found to check restorability against")
    inst = _script("frognet_install.sh")
    if not inst:
        print("  SKIP symmetry - needs frognet_install.sh (release tooling)")
        return
    itext = open(inst, encoding="utf-8", errors="replace").read()
    for f in sorted(files):
        rel = f.lstrip("/")
        # restorable two ways: carried in the world tar, OR written by the installer.
        # line-anchored: WORLD_PATHS entries are one per line. A loose substring
        # match made this check unfailable (usr/local/lib matched inside
        # usr/local/lib/python3.11), so it must be the exact path or exact parent.
        _entries = {ln.strip() for ln in wtext.splitlines()}
        shipped = rel in _entries or "/".join(rel.split("/")[:-1]) in _entries
        written = f in itext
        check(shipped or written,
              f"release can restore {f} (reset deletes it)"
              f"{'' if shipped else ' [installer writes it]' if written else ''}")


def plane_merge():
    print("\n=== PLANE 2: the installed node converges, every shape ===")
    from discovery.sim.system import System
    from discovery.sim import shapes

    built = []
    for name in ("snake", "ring", "star"):
        fn = getattr(shapes, name, None)
        if fn is None:
            continue
        for n in (5, 6):
            try:
                built.append((f"{name}({n})", fn(n)))
            except Exception:
                pass
    if not built:
        check(False, "simulator produced at least one topology")
        return

    for label, spec in built:
        try:
            s = System(spec)
            s.converge(8)
            ok = bool(getattr(s, "node", None))
            check(ok, f"{label}: clean-installed nodes converge")
        except Exception as e:
            check(False, f"{label}: converge raised {type(e).__name__}: {e}")


def plane_units_ship():
    """[UNITS_ARE_PART_OF_THE_WORLD_V1] Every unit the installer enables, or the
    boot gate reads, must be IN the release.

    Measured 2026-08-29 against a release tarball: frognet_build_release.sh scoped
    WORLD_PATHS to etc/systemd/system/dnsmasq.service.d -- the drop-in, not the
    directory -- so the world tar carried not one FrogNet unit while ten of them
    were referenced in 3 to 22 files each. A node installed from that release comes
    up with no proxy, no daemon, no tunnels and no merge watcher. Nothing in the
    build caught it: the builder's manifest check passed (the path it was given did
    exist), and the installer counted the misses into a variable it never read.

    The service names are not re-listed here. They are PARSED from the installer's
    own CORE_SERVICES array and from boot_gate_check's reads, so adding a service
    to the installer adds it to this gate. A hand-maintained second list is how the
    three disagreeing path manifests happened.
    """
    print("\n=== PLANE 1f: units the installer enables are IN the release ===")
    inst = _script("frognet_install.sh")
    if not inst:
        print("  SKIP unit-shipping plane - frognet_install.sh is release tooling")
        return
    text = open(inst, encoding="utf-8", errors="replace").read()

    m = re.search(r"CORE_SERVICES=\(([^)]*)\)", text, re.S)
    if not m:
        check(False, "installer declares CORE_SERVICES (cannot gate without it)")
        return
    core = [f"{s_}.service" for s_ in m.group(1).split()]
    check(bool(core), f"parsed {len(core)} core service(s) from the installer")

    # units boot_gate_check.py opens directly - the gate graph
    gate = _os.path.join(_HERE, "sim", "boot_gate_check.py")
    gate_units = []
    if _os.path.exists(gate):
        gate_units = sorted(set(re.findall(r'_u\("([^"]+)"\)',
                                           open(gate, encoding="utf-8").read())))
    # Timers are NOT lumped in with the core set. The installer enables every one
    # of them conditionally -- `[[ -f ... ]] && systemctl enable ... || true` -- so a
    # node without one is a node that opted out, not a broken release. Demanding
    # them here made this plane report two false absences on its first run.
    # Only timers whose .service the installer treats as core are required.
    timers = []
    for t in sorted(set(re.findall(r"(frognet-[a-z0-9-]+)\.timer", text))):
        if f"{t}.service" in core:
            timers.append(f"{t}.timer")

    units_dir = _os.path.join(_TREE, "etc", "systemd", "system")
    if not _os.path.isdir(units_dir):
        units_dir = "/etc/systemd/system"

    absent = []
    for u in sorted(set(core + gate_units + timers)):
        if not _os.path.isfile(_os.path.join(units_dir, u)):
            absent.append(u)
    check(not absent,
          f"all {len(set(core + gate_units + timers))} required unit(s) present in "
          f"{units_dir}" + (f" - ABSENT: {', '.join(absent)}" if absent else ""))

    # And the builder must package the DIRECTORY, not a drop-in inside it. A
    # release whose path list reaches only etc/systemd/system/<something>.d ships
    # that one subdirectory and silently drops every unit beside it.
    bld = _script("frognet_build_release.sh")
    # [ONE_MANIFEST_V1] The path list is no longer held in the builder -- it lives
    # in frognet_world_manifest.sh, which full_tar.bash also sources. So this reads
    # the MANIFEST, and separately asserts the builder does not keep a second copy.
    # (This check previously parsed WORLD_PATHS=( out of the builder and broke the
    # moment that became an expansion of the manifest. Assert on the source of
    # truth, not on whichever file happened to hold it last.)
    listed = []
    man = _manifest()
    check(man is not None, "frognet_world_manifest.sh ships (one list, two consumers)")
    if man:
        mtext = open(man, encoding="utf-8", errors="replace").read()
        _in = False
        for _ln in mtext.splitlines():
            if not _in:
                if re.match(r"\s*FROGNET_WORLD_PATHS=\(", _ln):
                    _in = True
                continue
            if re.match(r"\s*\)\s*$", _ln):
                break
            _ln = _ln.split("#", 1)[0].strip()
            if _ln:
                listed.extend(_ln.split())
        check("frognet_check_manifest" in mtext,
              "manifest defines frognet_check_manifest (refuses to build with a hole)")
        check("FROGNET_NEVER_SHIP" in mtext,
              "manifest defines FROGNET_NEVER_SHIP (keys cannot reach the public repo)")

    if bld:
        btext = open(bld, encoding="utf-8", errors="replace").read()
        check("frognet_world_manifest.sh" in btext,
              "builder SOURCES the shared manifest (does not hold its own path list)")
        # A literal path entry in the builder means a second list has grown back.
        _own = re.search(r"WORLD_PATHS=\(\s*\n\s*(?:etc|usr|var|opt)/", btext)
        check(_own is None,
              "builder has not regrown a local copy of the path list")
        check("LIB_MANIFEST_AUTODISCOVER_V1" in btext,
              "builder auto-discovers /usr/local/lib/frognet* (lib manifest cannot drift)")

        check("etc/systemd/system" in listed,
              "manifest packages etc/systemd/system as a DIRECTORY "
              "(not scoped to a drop-in inside it)")

    # [DEPS_ARE_INSTALLED_NOT_SHIPPED_V1] The release must not carry the build
    # host's third-party Python. It did: usr/local/lib/python3.11 was in
    # WORLD_PATHS and was 332 MB of a 396 MB release -- 44 package directories
    # against the 23 the installer declares, 143 compiled .so files, no FrogNet
    # code. And because C1 extracts AFTER A3 pip-installs, those aarch64 binaries
    # landed on top of whatever pip had just built for the target's architecture.
    #
    # Assert on the PATH LIST, not on a size or a filename pattern: the question is
    # whether the builder was told to package somebody else's dist-packages.
    if bld:
        deps = [p_ for p_ in listed if re.match(r"usr/local/lib/python3\.?[0-9]*$", p_)]
        check(not deps,
              "builder does not package the build host's dist-packages "
              f"(dependencies come from PIP_PACKAGES at A3/C2)"
              + (f" - found: {', '.join(deps)}" if deps else ""))
        # FrogNet's own shell libs under the same parent MUST still ship, or
        # runMerge dies at `source frognet_trace.sh`. Removing the tree wholesale
        # would take them; this proves the cut was scoped, not blanket.
        for _lib in ("usr/local/lib/frognet_trace.sh", "usr/local/lib/frognet_log.sh"):
            check(_lib in listed, f"builder still packages {_lib}")

    # The installer must REFUSE on a missing core unit, not count it and continue.
    check(re.search(r"MISSING=\(\s*\)", text) is not None
          and re.search(r'die "release is incomplete', text) is not None,
          "installer DIES on a missing core unit (does not warn and configure on)")


def plane_from_repo():
    """[CHECKOUT_IS_A_FIRST_NODE_V1] A repository checkout must reach a first node.

    Before --from-repo the installer's only accepted input was frognet_world.tgz,
    and the only thing that builds one is frognet_build_release.sh snapshotting a
    node that is ALREADY RUNNING. The graph had no edge from a checkout to a first
    node at all: a stranger who cloned the repo hit "frognet_world.tgz not found"
    and stopped. That is the exact failure the public repo exists to avoid.

    This plane asserts on the CODE, not on the comments that explain it -- the
    comments here quote the old error string, so a naive text check would match its
    own explanation. Anchors are the flag parse, the phase branch, and the copy.
    """
    print("\n=== PLANE 1g: a repository checkout reaches a first node ===")
    inst = _script("frognet_install.sh")
    if not inst:
        print("  SKIP from-repo plane - frognet_install.sh is release tooling")
        return
    text = open(inst, encoding="utf-8", errors="replace").read()

    check(re.search(r"^\s*--from-repo\)\s+FROM_REPO=1;", text, re.M) is not None,
          "installer parses --from-repo")

    c1 = text[text.find('phase "C1'):text.find('phase "C1b')]
    check(bool(c1), "C1 phase located")
    check(re.search(r"if\s*\(\(\s*FROM_REPO\s*\)\)", c1) is not None,
          "C1 branches on FROM_REPO (checkout is laid down, not unpacked)")
    check(re.search(r'cp -a "\$\{SCRIPT_DIR\}/\$\{_d\}/\." "/\$\{_d\}/"', c1) is not None,
          "C1 COPIES the checkout's payload roots")
    check(re.search(r"for _d in etc opt usr var; do", c1) is not None,
          "C1 copies etc/opt/usr/var BY NAME (not the repo root, so .git stays out)")
    check(re.search(r"tar --ignore-zeros -xzf", c1) is not None,
          "the release path still extracts the world tar (one installer, two inputs)")

    # Only C1 may differ. If a second phase learned about FROM_REPO, this stopped
    # being one branch and became a second installer growing inside the first.
    # The set of phases allowed to know about FROM_REPO is an ALLOWLIST, not a
    # count. Each entry needs a reason a release and a checkout genuinely differ;
    # anything else means a second installer is growing inside the first.
    #
    #   C1  a checkout IS the filesystem, so it is copied, not unpacked
    #   D8  a release carries mysqldump output to clone a node; a checkout makes a
    #       NEW node and starts with empty tables
    #
    # This started as "only C1" and D8 was added on evidence, not to make a test
    # pass: the install died at D8 requiring /frognet_db.sql. Adding a third entry
    # should take the same argument.
    ALLOWED = {"C1", "D8"}
    after_c1 = text[text.find('phase "C1b'):]
    leaked = []
    _cur = "?"
    for _ln in after_c1.splitlines():
        _m = re.match(r'phase "([A-F][0-9A-Za-z]*)', _ln)
        if _m:
            _cur = _m.group(1)
        elif "FROM_REPO" in _ln and _cur not in ALLOWED and _cur not in leaked:
            leaked.append(_cur)
    check(not leaked,
          f"FROM_REPO appears only in {'/'.join(sorted(ALLOWED))} - every other phase is identical"
          + (f" (leaked into: {', '.join(leaked)})" if leaked else ""))

    # Self-install guards.
    check(re.search(r'readlink -f "\$\{SCRIPT_DIR\}/\$\{_d\}"', text) is not None,
          "C1 refuses to copy a payload root onto itself")
    check("NO_SELF_INSTALL_V1" in text
          and re.search(r'readlink -f "\$\{_INST_DIR\}/\$\{_rel\}"', text) is not None,
          "installer does not install itself over itself when SCRIPT_DIR is the destination")

    # Preconditions must fail LOUD, before any phase runs.
    check(re.search(r'die_early "--from-repo: .*etc/systemd/system', text) is not None,
          "--from-repo refuses a checkout with no systemd units (would boot to nothing)")
    check(re.search(r'die_early "--from-repo: no directory holding all of', text) is not None,
          "--from-repo refuses when no payload root is found, naming where it looked")

    # [CHECKOUT_IS_A_FIRST_NODE_V1] A0 delegates the wipe to frognet_reset.sh. In a
    # checkout that file is at usr/local/bin/installer/, not the root, so looking
    # only in SCRIPT_DIR made --from-repo die at A0 on any box with a prior install
    # -- reporting an incomplete release when the file was right there. Measured on
    # BrokerHost 2026-08-29, after the reset had already run.
    check("SCRIPT_DIR}/usr/local/bin/installer/frognet_reset.sh" in text,
          "--from-repo finds frognet_reset.sh at its CHECKOUT path, not just the root")

    # [FROGNET_DOES_NOT_OWN_THE_OS_V1] Installing a node must not require upgrading
    # the operating system. A full `apt-get upgrade` makes every third-party repo on
    # the box a hard precondition; on BrokerHost a broken PHP repo killed the install
    # at A1, before any FrogNet file was written. A2 installs what FrogNet names.
    check(re.search(r"^\s*(DEBIAN_FRONTEND=\S+\s+)?apt-get upgrade", text, re.M) is None,
          "installer does not run a full-system apt-get upgrade")
    check(re.search(r"^\s*apt-get update", text, re.M) is not None,
          "installer still refreshes the package index (A2 cannot resolve without it)")

    # [A_CHECKOUT_MAKES_A_NEW_NODE_V1] D8 restores /frognet_db.sql - `mysqldump
    # FrogNet` from the build host, the whole live database. That is how a RELEASE
    # clones a node. A CHECKOUT makes a new one and must not require it: the
    # install died at D8 with "/frognet_db.sql missing - the release is
    # incomplete" on BrokerHost 2026-08-30, after C1c and all of D1-D7 had passed.
    d8 = text[text.find('phase "D8'):text.find('phase "D9')] or text[text.find('phase "D8'):]
    check(re.search(r"elif\s*\(\(\s*FROM_REPO\s*\)\)", d8) is not None,
          "D8 does not require a database dump under --from-repo")
    check(re.search(r'\[\[ -f /frognet_db\.sql \]\] \|\| die', d8) is not None,
          "D8 STILL requires the dump on the release path (a release clones a node)")

    # And the dump must never reach the public repo, whatever else changes.
    man_t = ""
    _m2 = _manifest()
    if _m2:
        man_t = open(_m2, encoding="utf-8").read()
    if man_t:
        _in, wp = False, []
        for _ln in man_t.splitlines():
            if not _in:
                if re.match(r"\s*FROGNET_WORLD_PATHS=\(", _ln):
                    _in = True
                continue
            if re.match(r"\s*\)\s*$", _ln):
                break
            _ln = _ln.split("#", 1)[0].strip()
            if _ln:
                wp.extend(_ln.split())
        check(not [p_ for p_ in wp if "frognet_db.sql" in p_],
              "the database dump is not in the world path list "
              "(a repo cannot carry a pond's users, messages and host table)")

    # [A_BROKEN_VENV_IS_NOT_A_WARNING_V1] C2 warned "Venv: 7 of 23 package(s)
    # failed" and continued into C3 and all of D (BrokerHost 2026-08-29). Those
    # packages are imported by the proxy data plane, the wire codec, the cache and
    # the tunnel daemon; missing them means the services cannot start, and the
    # next banner the operator sees says PASSED.
    c2 = text[text.find('phase "C2'):text.find('phase "C3')]
    check(bool(c2), "C2 phase located")
    check(re.search(r'die "venv build failed', c2) is not None,
          "C2 DIES on a failed venv package (does not warn and continue into C3)")
    check("VFAILED" in c2,
          "C2 names which packages failed (a count does not say PyYAML or NetfilterQueue)")

    # A missing reset script means a node that cannot reinstall itself. It must
    # not be reported as absent when C1 already put it in place, and must not be
    # shrugged off when it is genuinely gone.
    c1 = text[text.find('phase "C1'):text.find('phase "C1b')]
    check(re.search(r'die "\$\{_rel\} is in neither', c1) is not None,
          "a genuinely missing frognet_reset.sh is fatal, not a warning")
    check(re.search(r'elif \[\[ -f "\$\{_INST_DIR\}/\$\{_rel\}" \]\]', c1) is not None,
          "the installer checks the DESTINATION before reporting a file as missing")

    # [FIND_THE_ROOT_DO_NOT_ASSUME_IT_V1] SCRIPT_DIR is where the installer FILE
    # lives. In a checkout that is usr/local/bin/installer/, four levels below the
    # root, so treating it as the root refused the ordinary invocation -
    #   /repo# ./usr/local/bin/installer/frognet_install.sh --from-repo
    #   ERROR: /repo/usr/local/bin/installer/etc not found
    # telling an operator standing in the repo root they were not in it.
    check("REPO_ROOT=" in text and re.search(r'_c="\$\(dirname "\$_c"\)"', text) is not None,
          "--from-repo WALKS UP from the script to find the payload root")
    # And the walk must stop before "/", which on any Linux box has etc/opt/usr/var
    # and would be accepted as a checkout - C1 would then copy / onto /.
    check(re.search(r'\[\[ "\$_c" == "/" \]\] && break', text) is not None,
          'the upward walk refuses "/" as a repo root')
    check(re.search(r'"\$PWD" != "/"', text) is not None,
          '$PWD is not accepted as a repo root when it is "/"')

    # The usage block a stranger reads must mention it, or the edge exists and
    # nobody finds it.
    head = text[:text.find("set -eu")]
    check("--from-repo" in head, "--from-repo is documented in the usage header")


def _announce():
    """Name the files under test. [TEST_THE_TREE_YOU_ARE_IN_V1]

    When this oracle and the tree disagree, the first question is always "which
    copy did it read?" - and until it printed that, the answer took a round trip.
    """
    inst = _script("frognet_install.sh")
    man = _manifest()
    print("  reading:")
    print(f"    installer  {inst or '<not found>'}")
    print(f"    manifest   {man or '<not found>'}")
    for _p in (inst, man):
        if _p and not _p.startswith(_TREE):
            print(f"    NOTE: {_p} is the INSTALLED copy, not this tree - "
                  "results describe the installed node")
    print()


def main():
    _announce()
    plane_install()
    plane_reinstall()
    plane_three_modes()
    plane_filesystem()
    plane_units_ship()
    plane_from_repo()
    plane_reset_release_symmetry()
    plane_merge()
    print()
    if PROBS:
        # [A_COUNT_IS_NOT_A_DIAGNOSIS_V1] The runner surfaces only the LAST line of
        # an oracle. This printed "CLEAN-INSTALL ORACLE FAILED" and nothing else,
        # so a failure on a node showed up in the gate with no way to tell what
        # broke without re-running the oracle by hand. Recap the failures, and put
        # the first one on the summary line itself.
        print("FAILED CHECKS:")
        for _p in PROBS:
            print(f"  - {_p}")
        print()
        _first = PROBS[0] if PROBS else "?"
        print(f"CLEAN-INSTALL ORACLE FAILED ({len(PROBS)}): {_first}")
        return 1
    print("ALL CLEAN-INSTALL CHECKPOINTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
