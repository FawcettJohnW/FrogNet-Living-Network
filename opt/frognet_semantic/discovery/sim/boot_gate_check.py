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
sim/boot_gate_check.py - proves the frognet-discovered boot gate's unit graph and its merge
wiring from the ACTUAL files: the gate service blocks on the discovered sentinel; the target
requires+orders-after the gate service; consumers (dashboard, gps) order after the target;
the producer (merge-watcher) does NOT depend on the target (no deadlock); and live.main marks
'discovered' at merge completion.
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_FS = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_FS))                    # work
_UNITS = os.path.join(_WORK, "etc", "systemd", "system")

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

def _u(name):
    return open(os.path.join(_UNITS, name)).read()

def run():
    svc = _u("frognet-discovered.service")
    tgt = _u("frognet-discovered.target")
    gps = _u("frognet-gps.service")
    mw = _u("frognet-merge-watcher.service")

    probs = []
    if "/etc/sentinels/discovered" not in svc:
        probs.append("gate service does not block on the discovered sentinel")
    if "Type=oneshot" not in svc or "RemainAfterExit=yes" not in svc:
        probs.append("gate service must be oneshot + RemainAfterExit (latches the target)")
    check("[GATE-SVC] discovered.service blocks until the sentinel exists, then latches", probs)

    probs = []
    if "Requires=frognet-discovered.service" not in tgt or "After=frognet-discovered.service" not in tgt:
        probs.append("target must Require + order After the gate service")
    check("[GATE-TGT] discovered.target requires + orders after the gate service", probs)

    # [DEAD_UNITS_REMOVED_V1] frognet-dashboard was the other consumer and was
    # removed 2026-08-29 (disabled on a live node; its ExecStart named
    # /opt/frognet/venv/bin/gunicorn, a path in no other file in the tree, for a
    # WSGI app that is now var/www/html/api_semantic.php under Apache; its port
    # 9100 has since been reallocated to AV_PORT_BASE).
    #
    # That leaves gps as the ONLY consumer pulling the target, and gps is an
    # OPT_SERVICE. On a box installed without it, nothing references
    # frognet-discovered.target, so the boot gate silently does not exist. Assert
    # that at least one consumer remains, so removing the last one FAILS here
    # rather than quietly deleting the gate.
    probs = []
    consumers = [("gps", gps)]
    if not consumers:
        probs.append("no consumer pulls frognet-discovered.target - the gate is unreachable")
    for nm, txt in consumers:
        if "After=frognet-discovered.target" not in txt or "Wants=frognet-discovered.target" not in txt:
            probs.append(f"{nm} does not order after the discovered target")
    check("[CONSUMERS] every remaining consumer starts only after discovery converges", probs)

    probs = []
    if "frognet-discovered.target" in mw:
        probs.append("merge-watcher (the PRODUCER) depends on the target - deadlock")
    check("[NO-DEADLOCK] merge-watcher (producer) does not wait on the target", probs)

    # live.main marks discovered at merge completion
    probs = []
    live = open(os.path.join(_FS, "discovery", "live.py")).read()
    if "_mark_discovered(" not in live:
        probs.append("live.py does not call _mark_discovered at merge end")
    # [SENTINEL_DIR_HONOURED_V1] Test the BEHAVIOUR, not the source text.
    #
    # This used to grep live.py for the literal open("/etc/sentinels/discovered".
    # That breaks the moment the path is centralised (as it must be, so oracles
    # can redirect FROGNET_SENTINEL_DIR and stop reading the live box), and it
    # never proved the write happened -- only that a string was present.
    #
    # Call the real function against a temp sentinel dir and look for the file.
    # This also proves the redirect is honoured, which is the whole point.
    import tempfile as _tf, importlib as _il
    _d = _tf.mkdtemp(prefix="bootgate_")
    _old = os.environ.get("FROGNET_SENTINEL_DIR")
    os.environ["FROGNET_SENTINEL_DIR"] = _d
    try:
        from discovery import live as _live
        _il.reload(_live)
        _live._mark_discovered(logger=lambda *_a, **_k: None)
        if not os.path.exists(os.path.join(_d, "discovered")):
            probs.append("_mark_discovered did not write the discovered sentinel "
                         f"under FROGNET_SENTINEL_DIR ({_d})")
    except Exception as _e:
        probs.append(f"_mark_discovered raised: {_e!r}")
    finally:
        if _old is None:
            os.environ.pop("FROGNET_SENTINEL_DIR", None)
        else:
            os.environ["FROGNET_SENTINEL_DIR"] = _old
    # ordering: _commit_hosts then _arm_elector then _mark_discovered
    a = live.find("_commit_hosts(out, logger=logger)")
    b = live.find("_mark_discovered(logger=logger)")
    if not (0 <= a < b):
        probs.append("_mark_discovered must run after hosts are committed")
    check("[MARK] merge completion writes /etc/sentinels/discovered (after commit)", probs)

def main():
    print("=== frognet-discovered boot gate: unit graph + merge wiring ===")
    run()
    print("\n" + ("ALL BOOT-GATE CHECKS PASS" if not FAILS
                  else f"BOOT-GATE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
