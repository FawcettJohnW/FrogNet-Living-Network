#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""app_elector.py -- for the tests where no tunnel daemon runs: a node's election of the AppHosts as the daemon runs
it (internet_tunnels_v3/poll.py, [APP_HOST_ELECTION_V1]): the real core/app_hosts.py over the node's block, on a held
read on the fdf-app family in databasehost-control and in the SystemRam, and when the block changes; when the block
names another databasehost-control, the merge's write of the configuration tuples from this node's copies
([CONFIG_WRITTEN_AGAIN_BY_THE_MERGE_V1]).

  app_elector.py HOSTS BLOCK APPS_DIR
      HOSTS   the node's /etc/hosts, written here
      BLOCK   the converged block without the app lines (the FrogNetHost lines, databasehost_control.frognet,
              databasehost.frognet), as the test has the view; written by the test, read again when it changes
      APPS_DIR  the node's FDF_APPS (its copies of the configuration tuples)
Environment: FDF_PY, FROGNET_CORE (frognet_held), FDF_HOSTS.
"""
import os, sys, threading, time

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("FDF_PY") or os.path.join(H, "..", "fdf", "lib", "python"))
sys.path.insert(0, os.path.join(H, "..", "..", "..", "..", "frognet_semantic"))
os.environ.setdefault("FROGNET_CORE", os.path.join(H, "..", "..", "..", "..", "frognet_semantic", "core"))
import fdfram, fdf_watch  # noqa: E402
from core import app_hosts  # noqa: E402

HOSTS, BLOCK, APPS = sys.argv[1], sys.argv[2], sys.argv[3]
lock = threading.Lock()


def log(m):
    print("[app-elector] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def block():
    try:
        return [l.rstrip("\n") for l in open(BLOCK) if l.strip()]
    except OSError:
        return []


def elect(why):
    with lock:
        base = block()
        try:
            was = [l.rstrip("\n") for l in open(HOSTS)]
        except OSError:
            was = []
        ctl = app_hosts.ip_of(base, app_hosts.CONTROL)
        if ctl and ctl != app_hosts.ip_of(was, app_hosts.CONTROL):
            app_hosts.APPS = APPS
            app_hosts.write_configs_again(ctl, logger=log)
        etc = app_hosts.commit(base, hosts_path=HOSTS, logger=lambda m: log("%s: %s" % (why, m)))
        if etc != was:
            open(HOSTS + ".tmp", "w").write("\n".join(etc) + "\n")
            os.replace(HOSTS + ".tmp", HOSTS)


def watch(name):
    # [ROLES_ON_API_V1] the role is its host's api.php on port 80: a held read on the fdf-app family there, as the
    # daemon holds it; the block naming another host for the role (or a read that fails) is read again on the block's
    # next change -- no polling
    rm, first, had = None, True, None
    while True:
        ip = app_hosts.ip_of(block(), name)
        if not ip:
            fdf_watch.Inotify(os.path.dirname(BLOCK) or ".").read(); continue
        if rm is None or rm.host != ip:
            rm, first = fdfram.RoleRam(ip), True
        try:
            cs = rm.read("fdf-app", "", "", after=0 if not first else -1, wait_s=fdfram.HOLD_S if not first else 0)
            now = sorted((c["id"], c["writer"], str(c["bag"])) for c in cs)
            if first or now != had:
                elect("app tuples (%s)" % name)
            first, had = False, now
        except fdfram.RamError as e:
            log("the read on %s (%s) ended: %s; read again when the block changes" % (name, ip, e))
            w, was = fdf_watch.Inotify(os.path.dirname(BLOCK) or "."), block()
            while block() == was:
                w.read()
            w.close(); rm = None


elect("start")
for n in (app_hosts.CONTROL, app_hosts.SYSTEM):
    threading.Thread(target=watch, args=(n,), daemon=True).start()
w = fdf_watch.Inotify(os.path.dirname(BLOCK) or ".")
seen = block()
while True:
    w.read()
    now = block()
    if now != seen:
        seen = now
        elect("the view")
