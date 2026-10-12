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
sim/conf_lib_check.py - [ONE_CONF_V1] the conf library exists and honours its
contract.

/usr/local/lib/frognet/conf.sh holds fn_conf_unset, which frognet_prune_removed.sh's
T3 section (the installer's C1c upgrade step) uses to take the retired keys out of
/etc/frognet/tunnel.conf. It was once not packaged, so a clean install died with
"FATAL: /usr/local/lib/frognet/conf.sh not found" (2026-08-29).

This drives the REAL library against a scratch config. The property that matters
is key-wise editing: the file may also hold RINA_RAM and RINA_AREA, which the tunnel
daemon reads, so removing other keys must leave every other line - and the file's
mode - exactly as it was.
"""
from __future__ import annotations
import os, re, stat, subprocess, tempfile

FAILS = []


def check(label, problems):
    if problems:
        FAILS.extend(problems)
        print(f"  [FAIL] {label}")
        for p in problems:
            print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")


def _lib():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tree = os.path.dirname(os.path.dirname(here))
    # [TEST_THE_TREE_YOU_ARE_IN_V1] The tree under test comes first. Preferring
    # the installed copy made this assert against whatever FrogNet the box already
    # had, not the checkout being validated.
    for c in (os.path.join(tree, "usr", "local", "lib", "frognet", "conf.sh"),
              "/usr/local/lib/frognet/conf.sh"):
        if os.path.exists(c):
            return c
    return None


def run():
    lib = _lib()
    check("[EXISTS] /usr/local/lib/frognet/conf.sh ships",
          [] if lib else ["conf.sh not found - the T3 upgrade step sources it"])
    if not lib:
        return

    text = open(lib, encoding="utf-8", errors="replace").read()
    check("[CONTRACT] fn_conf_unset is defined",
          [] if re.search(r"^fn_conf_unset\s*\(\)", text, re.M) else ["conf.sh does not define fn_conf_unset"])
    check("[DEFAULT] FROGNET_CONF defaults to the node config",
          [] if "/etc/frognet/tunnel.conf" in text else ["FROGNET_CONF has no default"])

    with tempfile.TemporaryDirectory() as d:
        conf = os.path.join(d, "tunnel.conf")
        with open(conf, "w") as f:
            f.write("# a comment\nOLD_A=1\nRINA_RAM=10.77.0.1:8840\nOLD_B=two=parts\nRINA_AREA=site1\nOLD_A=again\n")
        os.chmod(conf, 0o600)
        script = f'''
set -e
export FROGNET_CONF="{conf}"
. "{lib}"
fn_conf_unset OLD_A
fn_conf_unset OLD_B
fn_conf_unset NO_SUCH_KEY
echo DONE
'''
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        probs = []
        if r.returncode != 0 or "DONE" not in r.stdout:
            probs.append(f"conf.sh driver exited {r.returncode}: {r.stderr.strip()[:200]}")
        left = open(conf).read()
        if left != "# a comment\nRINA_RAM=10.77.0.1:8840\nRINA_AREA=site1\n":
            probs.append(f"fn_conf_unset must remove exactly the named keys (every line of them) and nothing else; left {left!r}")
        if stat.S_IMODE(os.stat(conf).st_mode) != 0o600:
            probs.append(f"mode changed to {oct(stat.S_IMODE(os.stat(conf).st_mode))}")
        check("[KEY_WISE] unsetting keys leaves RINA_RAM / RINA_AREA, the comment and mode 600 intact", probs)

        probs = []
        r = subprocess.run(["bash", "-c", f'export FROGNET_CONF="{d}/absent.conf"; . "{lib}"; fn_conf_unset OLD_A; echo rc=$?'],
                           capture_output=True, text=True)
        if "rc=0" not in r.stdout or os.path.exists(os.path.join(d, "absent.conf")):
            probs.append("an absent file is the requested state already: rc 0, and no file is created")
        check("[ABSENT] no tunnel.conf: nothing to do, nothing created", probs)


def main():
    print("=== conf.sh: the one config library ===")
    run()
    print("\n" + ("ALL CONF-LIB CHECKS PASS" if not FAILS
                  else f"CONF-LIB CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
