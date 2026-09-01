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

/usr/local/lib/frognet/conf.sh is sourced by eight scripts and by installer phase
C1c. It was never packaged - the world manifest named frognet_trace.sh and
frognet_log.sh individually - so a clean install died with
"FATAL: /usr/local/lib/frognet/conf.sh not found" (BrokerHost 2026-08-29).

This drives the REAL library against a scratch config. The property that matters
is key-wise editing: frognet_setup_v4_helper.bash and change_pond.bash both state
that rewriting the file wholesale would take GROUP_TOKEN, PASSCODE, MAX_TUNNELS
and NODE_GUID with it. So the test is not "does it write a key" but "does
everything else survive".
"""
from __future__ import annotations
import os, re, subprocess, tempfile

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
          [] if lib else ["conf.sh not found - eight scripts source it and installer C1c dies without it"])
    if not lib:
        return

    # Every function the callers name must be defined.
    text = open(lib, encoding="utf-8", errors="replace").read()
    missing = [f for f in ("fn_conf_get", "fn_conf_set", "fn_conf_unset",
                           "fn_conf_backup", "fn_pond_set")
               if not re.search(rf"^{f}\s*\(\)", text, re.M)]
    check("[CONTRACT] every fn_conf_* / fn_pond_set the callers use is defined",
          [f"conf.sh does not define {m}" for m in missing])
    check("[DEFAULT] FROGNET_CONF defaults to the node config",
          [] if "/etc/frognet/tunnel.conf" in text else
          ["FROGNET_CONF has no default; frognet-conf-consolidate.sh takes dirname of it"])

    with tempfile.TemporaryDirectory() as d:
        conf = os.path.join(d, "tunnel.conf")
        script = f'''
set -e
export FROGNET_CONF="{conf}"
. "{lib}"
fn_conf_set GROUP_TOKEN membership
fn_conf_set PASSCODE secretpass
fn_conf_set MAX_TUNNELS 8
fn_conf_set BROKER_URL https://old/x
fn_pond_set hometown
echo "GET:$(fn_conf_get BROKER_URL)"
echo "ABSENT:[$(fn_conf_get NO_SUCH_KEY)]"
echo "BACKUP:$(fn_conf_backup)"
fn_conf_set BROKER_URL https://new/y
echo "RESET:$(fn_conf_get BROKER_URL)"
for k in BROKER_URL POND_NAME GROUP_NAME POND_PASSWORD CHORUSES; do fn_conf_unset "$k"; done
echo "SURVIVED:$(fn_conf_get GROUP_TOKEN)/$(fn_conf_get PASSCODE)/$(fn_conf_get MAX_TUNNELS)"
'''
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        out = r.stdout
        probs = []
        if r.returncode != 0:
            probs.append(f"conf.sh driver exited {r.returncode}: {r.stderr.strip()[:200]}")
        if "GET:https://old/x" not in out:
            probs.append("fn_conf_get did not read back what fn_conf_set wrote")
        if "ABSENT:[]" not in out:
            probs.append("an absent key must read as empty, not as an invented value")
        if "RESET:https://new/y" not in out:
            probs.append("fn_conf_set did not REPLACE an existing key")
        check("[ROUNDTRIP] set / get / replace", probs)

        probs = []
        if "SURVIVED:membership/secretpass/8" not in out:
            probs.append("unsetting the broker keys destroyed membership material - "
                         "fn_conf_unset must be key-wise, never a rewrite of the file "
                         f"(got: {[l for l in out.splitlines() if l.startswith('SURVIVED')]})")
        check("[KEY_WISE] clearing the broker leaves GROUP_TOKEN / PASSCODE / MAX_TUNNELS intact", probs)

        probs = []
        bak = next((l.split(":", 1)[1] for l in out.splitlines() if l.startswith("BACKUP:")), "")
        if not bak or not os.path.exists(bak):
            probs.append("fn_conf_backup did not write a file and print its path")
        elif not re.search(r"\.conf\.20\d{6}T\d{6}Z$", bak):
            probs.append(f"backup name {os.path.basename(bak)} does not match the "
                         "'*.conf.20*' FROGNET_NEVER_SHIP pattern - a backup carrying "
                         "GROUP_TOKEN would reach the public repo")
        check("[BACKUP] a backup is written, named so it can never ship", probs)

        probs = []
        pond = subprocess.run(["bash", "-c",
                               f'export FROGNET_CONF="{conf}"; . "{lib}"; '
                               f'fn_conf_set POND_NAME ""; fn_conf_set GROUP_NAME ""; '
                               f'fn_pond_set pondx; echo "$(fn_conf_get POND_NAME)/$(fn_conf_get GROUP_NAME)"'],
                              capture_output=True, text=True)
        if "pondx/pondx" not in pond.stdout:
            probs.append("fn_pond_set must write BOTH POND_NAME and GROUP_NAME "
                         "(frognet-conf-consolidate.sh's merge rule) so they cannot drift")
        check("[POND] fn_pond_set writes POND_NAME and GROUP_NAME together", probs)


def main():
    print("=== conf.sh: the one config library ===")
    run()
    print("\n" + ("ALL CONF-LIB CHECKS PASS" if not FAILS
                  else f"CONF-LIB CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
