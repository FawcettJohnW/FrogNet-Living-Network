#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""The parts of a channel's setting that can be checked in one process: whose setting a memory's name says it is, a
registered protocol's word, the two algorithms, and the setting proving itself ([A_SETTING_PROVES_ITSELF_V1]).

This is NOT the test that a running channel can be switched. That is simulation/roles_exercise_tier.py --run 4, between
machines, through the real proxy and daemon; and core/test_channel_frame_oracle.py for the frame itself. This is the
quick check after touching core/channel_setting.py or core/channel_protection.py.

  python3 core/test_channel_setting_oracle.py
"""
import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
FAILED = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg), flush=True)
    if not cond:
        FAILED.append(msg)


def main():
    keys = tempfile.mkdtemp(prefix="channel-keys-")
    os.environ["FROGNET_CHANNEL_KEYS"] = keys
    os.environ["FROGNET_SEM_TRANSPORT"] = "tcp"
    for role in ("units", "assessment"):
        os.makedirs(os.path.join(keys, role))
        for i in range(20):
            with open(os.path.join(keys, role, "%02d" % i), "wb") as f:
                f.write(hashlib.sha256(("%s %d" % (role, i)).encode()).digest())
    from core import channel_setting as C
    from core import channel_protection as P
    from core import homa_transport as HT
    C._started = True                                       # no held read: the settings are put here by hand
    said = []
    real_print = print

    # ---- whose setting a memory's name says it is
    names = {"unitsHost.exercise": "units", "exerciseHost.frognet": "exercise", "UNITSHOST.EXERCISE:80": "units",
             "databasehost.frognet": "", "databasehost_control.frognet": "", "10.1.2.3": "", "FrogNetHost.Seattle5": "", "": ""}
    check(all(C.role_of(n) == r for n, r in names.items()),
          "a Role's memory is an application memory, <team>Host.<app> or <app>Host.frognet, and the Role is the name before Host; "
          "the SystemRam, the ControlRam, a node's name and an address are no Role (%s)" % {n: C.role_of(n) for n in names})

    # ---- a setting proves itself
    def signed(role, **v):
        v = dict(v); v["check"] = C.check_of(role, v); return v
    first = signed("units", driver="tcp", switches=0)
    C._take(None, "the oracle", {"units": first})
    check(C.setting_of("units") == first and C.ram_of("unitsHost.exercise") == "units" and C.ram_of("commandHost.exercise") == "",
          "a setting with its check is taken, and a request for that Role's memory is that Role's; a Role with no setting is traffic of no Role")
    sealed = signed("units", driver="tcp", seed="s", protection="aes-256-gcm", switches=1)
    C._take(None, "the oracle", {"units": sealed})
    check(C.setting_of("units") == sealed, "the owner turns encryption on with a new write and a higher count: taken")
    forged = dict(sealed, seed="", switches=2)              # written by someone who does not hold the set: the old check, or none
    C._take(None, "the oracle", {"units": forged})
    kept1 = C.setting_of("units") == sealed
    C._take(None, "the oracle", {"units": {"driver": "tcp", "seed": "", "switches": 2}})
    kept2 = C.setting_of("units") == sealed
    C._take(None, "the oracle", {"units": dict(forged, check=C.check_of("assessment", {k: v for k, v in forged.items() if k != "check"}))})
    kept3 = C.setting_of("units") == sealed
    check(kept1 and kept2 and kept3, "a setting that turns the encryption off, with a check copied from another setting, with none, or "
          "made with another Role's set: ignored each time, and this end keeps running the setting it has")
    C._take(None, "the oracle", {"units": first})           # the Role's first, unsealed setting, written again: valid when it was made
    check(C.setting_of("units") == sealed, "an old valid setting written again (the Role's first, unsealed one, count 0): ignored, "
          "because its count is lower than the one this end is running")
    C.RUNNING = lambda: {"units": {"switches": 3}}
    late = signed("units", driver="chorus", seed="s", protection="aes-256-gcm", switches=2)
    C._take(None, "the oracle", {"units": late})
    check(C.setting_of("units") == sealed, "the count compared with is the one this end is RUNNING on its connections, when that is ahead of the setting it holds")
    C.RUNNING = None
    off = signed("units", driver="tcp", seed="", switches=2)
    C._take(None, "the oracle", {"units": off})
    check(C.setting_of("units") == off, "the owner turns encryption off: a new write, with its check, and a higher count: taken")
    C._take(None, "the oracle", {"units": off, "command": {"driver": "chorus", "seed": "x", "switches": 0}})
    check(C.setting_of("command") == {"driver": "chorus", "seed": "x", "switches": 0},
          "a Role this machine holds no set for: it has nothing to verify with and nothing to protect, and takes the setting as written")
    check(C.check_of("units", off) != C.check_of("assessment", off) and C.check_of("command", off) is None
          and C.check_text("units", {"switches": 2, "driver": "tcp", "seed": "", "check": "x", "ts": 9}) == b"units\ndriver=tcp\nseed=\nswitches=2",
          "the check is of the Role's name and the setting's other fields, as text any language can make the same, keyed from the Role's whole set")

    # ---- a registered protocol's word
    C._take(None, "the oracle", {"units": signed("units", driver="openqasm", switches=3), "assessment": signed("assessment", driver="no-such", switches=0)})
    check(C.driver(None, "units") == "openqasm" and C.driver(None, "assessment") is None,
          "the protocol a setting names is taken when a driver has registered that word (%s), and a word nobody registered is said and is no word"
          % ", ".join(sorted(HT.DRIVERS)))

    # ---- the two algorithms
    key, nonce, aad = os.urandom(32), os.urandom(12), b"\0" * 8
    ok = True
    for name in ("aes-256-gcm", "chacha20-poly1305"):
        seal, open_ = P.PROTECTIONS[name]
        blob = seal(key, nonce, aad, b"a frame")
        ok = ok and open_(key, nonce, aad, blob) == b"a frame" and len(blob) == len(b"a frame") + P.TAG
        for bad in ((os.urandom(32), nonce, aad), (key, nonce, b"\1" * 8)):
            try:
                open_(bad[0], bad[1], bad[2], blob); ok = False
            except ValueError:
                pass
    check(ok, "aes-256-gcm and chacha20-poly1305, from the machine's own libcrypto: each opens what it sealed, and not under another key or for another packet")
    print("ALL CHANNEL-SETTING ORACLE CHECKPOINTS PASS" if not FAILED else "CHANNEL-SETTING ORACLE: %d FAILED" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
