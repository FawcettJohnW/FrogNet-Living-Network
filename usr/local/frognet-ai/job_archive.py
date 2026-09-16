#!/usr/bin/env python3
"""job_archive.py -- a job's data lives for the job. Archive it, then nuke it.

    python3 job_archive.py --runid mesh440 --store databasehost.frognet:80 \
                           --results /tmp/nb3 --out /var/lib/frognet-archive

    python3 job_archive.py --runid mesh440 ... --nuke      # and remove it

[A_JOB_S_DATA_LIVES_FOR_THE_JOB_V1]

Nothing is evicted while a job runs. Not the tensor plane's states, not the
store's rows, not the results. That is what lets a published state be a
pointer into memory that stays put rather than a copy taken in case someone
frees it, and it is what lets a reader that falls behind still find what it
needs -- the two problems that produced most of this project's failures.

The cost is that a job ends holding everything it ever produced, so the end
of the job is where it gets dealt with: export what is worth keeping, then
remove all of it. Re-running the job re-creates the data; that is the
recovery story, and it is a better one than partial eviction.

What is exported:
  results/    every rank's result JSON for this run id
  logs/       every rank's log
  sensors.json  the store rows this job created, whole
  MANIFEST    what was taken, when, and the sha256 of each file

--nuke removes the store rows and the local results ONLY after the archive
has been written and verified. An archive that did not land is a reason to
keep the data, not to proceed.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runid", required=True,
                    help="the job. Everything named nb1.<runid>.* is its data")
    ap.add_argument("--store", required=True)
    ap.add_argument("--results", default="/tmp/nb3")
    ap.add_argument("--out", default="/var/lib/frognet-archive")
    ap.add_argument("--tree", default="/opt/frognet_semantic")
    ap.add_argument("--pypath", default="/etc/frognet_bundles/communicator")
    ap.add_argument("--nuke", action="store_true",
                    help="remove the store rows and local results, AFTER the "
                         "archive is written and verified")
    a = ap.parse_args()

    for p in ([a.pypath] if os.path.isdir(a.pypath) else []) + [a.tree]:
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("FROGNET_DIAG", "0")
    os.environ.setdefault("FROGNET_SENTINEL_DIR", "/tmp/netbench1.sentinel")
    os.makedirs("/tmp/netbench1.sentinel", exist_ok=True)
    from core import frognet_tuples as T

    stamp = time.strftime("%Y%m%d-%H%M%S")
    stage = os.path.join(a.out, "%s.%s" % (a.runid, stamp))
    os.makedirs(os.path.join(stage, "results"), exist_ok=True)
    os.makedirs(os.path.join(stage, "logs"), exist_ok=True)

    # ---- the store rows this job created -------------------------------
    # One indexed prefix query. If this fails, nothing is removed: an
    # archive that did not read the data is not an archive.
    prefix = "nb1.%s." % a.runid
    rows = T._values_raw("", a.store, service_prefix=prefix)
    # [NEVER_DELETE_ON_SOMEONE_ELSE_S_FILTER_V1]
    #
    # Filter again here, on what came back. The prefix goes to the store as
    # SensorType__like, and a store that does not implement it returns
    # EVERY row rather than an error -- store_server.py did exactly that,
    # and this endpoint duly exported and deleted a live `presence` row
    # belonging to nothing to do with the job.
    #
    # A deletion is unrecoverable, so the set to delete is decided here,
    # from data in hand, and never on the assumption that a query was
    # understood.
    kept = [r for r in rows
            if (r.get("SensorType") or "").startswith(prefix)]
    if len(kept) != len(rows):
        print("  store returned %d rows, %d match %s -- the rest are not "
              "this job's and are left alone"
              % (len(rows), len(kept), prefix))
    rows = kept
    with open(os.path.join(stage, "sensors.json"), "w") as f:
        json.dump(rows, f, indent=1, default=str)
    print("  store rows exported: %d" % len(rows))

    # ---- results and logs ----------------------------------------------
    n_res = n_log = 0
    for src in sorted(glob.glob(os.path.join(a.results, "%s.*" % a.runid))):
        base = os.path.basename(src)
        if base.endswith(".json"):
            shutil.copy2(src, os.path.join(stage, "results", base)); n_res += 1
        elif base.endswith(".log"):
            shutil.copy2(src, os.path.join(stage, "logs", base)); n_log += 1
    print("  results: %d   logs: %d" % (n_res, n_log))

    # ---- manifest, so the archive can say what it is -------------------
    man = {"runid": a.runid, "archived": stamp, "store": a.store,
           "store_rows": len(rows), "results": n_res, "logs": n_log,
           "files": {}}
    for root, _, names in os.walk(stage):
        for nm in names:
            p = os.path.join(root, nm)
            man["files"][os.path.relpath(p, stage)] = sha(p)
    with open(os.path.join(stage, "MANIFEST.json"), "w") as f:
        json.dump(man, f, indent=1)

    tgz = stage + ".tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(stage, arcname=os.path.basename(stage))
    shutil.rmtree(stage)

    # Verify the archive is readable before anything is destroyed.
    with tarfile.open(tgz) as tf:
        names = tf.getnames()
    if not any(n.endswith("MANIFEST.json") for n in names):
        print("archive is missing its manifest -- nothing removed",
              file=sys.stderr)
        return 2
    print("  archive: %s  (%d entries, %.1f KB)"
          % (tgz, len(names), os.path.getsize(tgz) / 1024.0))

    if not a.nuke:
        print("\nnot removing anything. Re-run with --nuke to clear the job.")
        return 0

    # ---- and only now, remove it ---------------------------------------
    gone = failed = 0
    for r in rows:
        sid = r.get("SensorID")
        if sid is None:
            continue
        if T._delete_by_id(a.store, sid):
            gone += 1
        else:
            failed += 1
    print("  store rows removed: %d   refused: %d" % (gone, failed))
    if failed:
        print("some rows could not be removed; local results kept",
              file=sys.stderr)
        return 1
    for src in glob.glob(os.path.join(a.results, "%s.*" % a.runid)):
        os.remove(src)
    print("  local results removed")
    print("\n%s is archived and gone. Re-running the job re-creates it."
          % a.runid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
