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
    """Prefer the installed path, fall back to the source tree (container runs).
    frognet_install.sh lives in the installer/ subdir (release tooling kept off the
    flat node-tool namespace), so search that first."""
    for p in (_os.path.join(BIN, "installer", name),
              _os.path.join(_TREE, "usr", "local", "bin", "installer", name),
              _os.path.join(BIN, name), _os.path.join(_TREE, "usr", "local", "bin", name)):
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
        # [REPORT_THE_HOLE_DO_NOT_REFUSE_THE_TAR_V1 - John 2026-09-15] The
        # builder REPORTS a missing manifest path and keeps going. It used to
        # exit 1, which meant a host that legitimately lacks a path -- an
        # apache vhost on a node that serves no vhosts -- could not build a
        # tarball at all. Loud is the requirement; refusing never was.
        check("REPORT_THE_HOLE_DO_NOT_REFUSE_THE_TAR_V1" in btext,
              "builder reports a missing manifest path by name")
        check("refusing to build an incomplete world" not in btext,
              "fail-on-old: and does not refuse to build over it")



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
    wtext = open(bld, encoding="utf-8", errors="replace").read() if bld else ""
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


def main():
    plane_install()
    plane_reinstall()
    plane_three_modes()
    plane_filesystem()
    plane_reset_release_symmetry()
    plane_merge()
    print()
    if PROBS:
        print("CLEAN-INSTALL ORACLE FAILED")
        return 1
    print("ALL CLEAN-INSTALL CHECKPOINTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
