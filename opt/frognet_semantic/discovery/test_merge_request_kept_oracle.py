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
test_merge_request_kept_oracle.py - an event that asked for a merge gets a merge.

Found 2026-11-28 installing five clean machines one after another (H4 - H2 - H1 - H3 - H5). H5 was
installed on H3's LAN, came up, merged, knew all four others, and told H3. H3 never learned of H5:

  10:10:22 dnsmasq-dhcp: DHCPACK(eth1) 10.73.73.40           H5's lease; dhcp_tracking.bash forks runMerge 38263
  10:10:55 systemd: Stopping dnsmasq.service ...              that merge's own `systemctl restart dnsmasq`
  10:11:27 GET /propogateNotification.php?event=f7ec...       H5: "I am here"
  10:11:58 [MERGE-ACCUM] fire decision=set_runAgain reason=merge_running
  10:12:25 systemd: dnsmasq.service: State 'final-sigterm' timed out. Killing.
           Killing process 38263 (runMerge.bash) with signal SIGKILL.

Two defects, each enough to lose a join:

PLANE 1 [LEASE_ASKS_THE_WATCHER_V1]  dhcp_tracking.bash started runMerge.bash itself. dnsmasq's dhcp-script runs
  inside dnsmasq.service's control group, and setsid does not leave it; so the merge was, to systemd, part of
  dnsmasq, and the merge's own restart of dnsmasq stopped (and 90 s later killed) the merge. The lease script now
  writes a request for the merge watcher, as the NetworkManager hook does. Checked on the script's text, with the
  line that stood there as the control (on the five nodes: the journal above, and no such kill after the change).

PLANE 2 [REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1]  the watcher consumed its batch and, finding the lock held, wrote
  runAgain and was done. A lock held by a merge that has already read runAgain (its last 1 to 2 s), or by a merge
  that will never finish (the one above), reads no flag: the event got no merge. This plane runs the REAL
  frognet-merge-watcher.sh ([SIM_ROOT_REDIRECT_V1]: its paths pointed at a scratch directory, a 1 s window) against
  a stand-in controller that takes the same lock and records when it starts.
  CONTROL: with no request there is no merge; with the lock free there is exactly one.

SKIP (said, not passed) where inotifywait is not installed: the watcher itself refuses to run without it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

PROBS = []
_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
BIN = "/usr/local/bin"


def check(ok, label):
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        PROBS.append(label)


def _script(name):
    # the checkout first (the tree under test), the installed copy when run on a node
    for p in (os.path.join(_TREE, "usr", "local", "bin", name), os.path.join(BIN, name)):
        if os.path.exists(p):
            return p
    return None


def _code_lines(text):
    """The script's lines that execute: comments and blank lines dropped."""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def starts_the_controller(text):
    """True if any executing line of a lease/interface hook runs runMerge.bash itself."""
    return any(re.search(r"(^|[\s;&|(])(setsid\s+|exec\s+|nohup\s+)?(/usr/local/bin/)?runMerge\.bash\b", ln)
               for ln in _code_lines(text))


def plane_lease():
    print("=== PLANE 1: a lease asks the watcher; it does not run the merge inside dnsmasq ===")
    was = 'setsid /usr/local/bin/runMerge.bash </dev/null >/dev/null 2>&1 &\ndisown 2>/dev/null || true\n'
    check(starts_the_controller(was),
          "CONTROL: the line that stood in dhcp_tracking.bash is seen as starting the merge itself")
    check(not starts_the_controller("# setsid /usr/local/bin/runMerge.bash </dev/null &\n: > \"$REQ_DIR/x\"\n"),
          "CONTROL: the same line inside a comment is not")
    p = _script("dhcp_tracking.bash")
    check(p is not None, "dhcp_tracking.bash found")
    if p:
        text = open(p, encoding="utf-8", errors="replace").read()
        check(not starts_the_controller(text),
              "dhcp_tracking.bash does not start runMerge.bash (it would run in dnsmasq.service's control group)")
        code = "\n".join(_code_lines(text))
        check('REQ_DIR="/run/frognet/merge_requests"' in code and re.search(r':\s*>\s*"\$\{REQ_DIR\}/dhcp-', code) is not None,
              "dhcp_tracking.bash writes its request into the watcher's queue, /run/frognet/merge_requests")
    hook = os.path.join(_TREE, "etc", "NetworkManager", "dispatcher.d", "90-frognet-merge")
    if not os.path.exists(hook):
        hook = "/etc/NetworkManager/dispatcher.d/90-frognet-merge"
    if os.path.exists(hook):
        check(not starts_the_controller(open(hook, encoding="utf-8", errors="replace").read()),
              "the NetworkManager hook does not start runMerge.bash either (one way to ask: the watcher)")


class Rig:
    """The real watcher over a scratch directory, with a stand-in controller on the same lock."""

    def __init__(self, watcher):
        self.d = tempfile.mkdtemp(prefix="frogsim_watcher_")
        self.req = os.path.join(self.d, "merge_requests")
        self.seen = os.path.join(self.d, "seen_notifications")
        self.lock = os.path.join(self.d, "runMerge.lock")
        self.started = os.path.join(self.d, "started")
        ctl = os.path.join(self.d, "controller.sh")
        with open(ctl, "w") as f:
            # what runMerge.bash does with the lock: take it or bail; hold it while "merging"
            f.write('#!/bin/bash\nexec 9>"%s"\nif ! flock -n 9; then echo "bail $(date +%%s.%%N)" >> "%s"; exit 0; fi\n'
                    'echo "start $(date +%%s.%%N)" >> "%s"\nsleep 0.3\n' % (self.lock, self.started, self.started))
        os.chmod(ctl, 0o755)
        env = dict(os.environ, FROGNET_MERGE_REQ_DIR=self.req, FROGNET_MERGE_SEEN_DIR=self.seen,
                   FROGNET_MERGE_LOCKFILE=self.lock, FROGNET_MERGE_CONTROLLER=ctl, FROGNET_MERGE_DEBOUNCE_SEC="1")
        self.log = open(os.path.join(self.d, "watcher.log"), "w")
        self.p = subprocess.Popen(["bash", watcher], env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        t0 = time.time()
        while not os.path.isdir(self.req) and time.time() - t0 < 5:
            time.sleep(0.05)
        time.sleep(0.3)                      # the watcher is in its first inotifywait

    def ask(self, name):
        open(os.path.join(self.req, name), "w").close()

    def starts(self):
        try:
            return [float(ln.split()[1]) for ln in open(self.started) if ln.startswith("start ")]
        except OSError:
            return []

    def queued(self):
        return sorted(os.listdir(self.req))

    def hold_lock(self, secs):
        """Something holds the merge lock for secs and reads no flag: a merge past its runAgain read, or a dead one."""
        return subprocess.Popen(["flock", self.lock, "sleep", str(secs)])

    def wait_starts(self, n, secs):
        t0 = time.time()
        while len(self.starts()) < n and time.time() - t0 < secs:
            time.sleep(0.1)
        return len(self.starts())

    def close(self):
        import signal
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except OSError:
            pass
        self.p.wait()
        self.log.close()
        shutil.rmtree(self.d, ignore_errors=True)


def plane_watcher():
    print("\n=== PLANE 2: a request stays queued until a merge is started for it (the real watcher) ===")
    w = _script("frognet-merge-watcher.sh")
    check(w is not None, "frognet-merge-watcher.sh found")
    if not w:
        return
    if not shutil.which("inotifywait") or not shutil.which("flock"):
        print("  SKIP the watcher cannot run here: inotifywait/flock not installed (a node has both: installer phase A2)")
        return

    r = Rig(w)
    try:
        time.sleep(2.5)
        check(len(r.starts()) == 0, "CONTROL: no request, no merge")

        r.ask("nm-eth1-up-1")
        check(r.wait_starts(1, 6) == 1, "CONTROL: lock free: one request, one merge")
        time.sleep(0.6)
        check(r.queued() == [], "              and the request is consumed")

        # the field case: the window expires while the lock is held by something that will read no flag
        n0 = len(r.starts())
        holder = r.hold_lock(5)
        time.sleep(0.3)
        t_ask = time.time()
        r.ask("f7ecf3f6-81b4-4386-913d-00f49fee1d93")       # the joining node's notification
        time.sleep(2.6)                                      # the 1 s window has expired; the lock is still held
        check(holder.poll() is None, "         (the lock is still held when the window expires)")
        check(len(r.starts()) == n0, "lock held: no second merge is started on top of the running one")
        check(r.queued() == ["f7ecf3f6-81b4-4386-913d-00f49fee1d93"],
              "lock held: the request is STILL QUEUED (it was consumed and turned into a runAgain nothing read)")
        r.ask("dhcp-add-10.73.73.40-77")                     # another event while waiting
        holder.wait()
        t_free = time.time()
        got = r.wait_starts(n0 + 1, 6)
        check(got == n0 + 1, "when the lock is released, ONE merge is started for the waiting requests")
        if got == n0 + 1:
            s = r.starts()[-1]
            check(s >= t_free - 0.05, "that merge starts after the lock was released (%.2f s after)" % (s - t_free))
            check(s > t_ask, "and after the event that asked for it")
        time.sleep(0.8)
        check(r.queued() == [], "both requests went with that one merge")
        time.sleep(2.5)
        check(len(r.starts()) == n0 + 1, "and nothing merges again with nothing asked")
    finally:
        r.close()


def main():
    plane_lease()
    plane_watcher()
    print()
    if PROBS:
        print("MERGE-REQUEST ORACLE FAILED")
        return 1
    print("ALL MERGE-REQUEST CHECKPOINTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
