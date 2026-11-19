#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""The parts of the channel's setting that can be checked in one process: a registered driver's word, a registered
protection, a RAM's own setting, and the sealed message's bytes.

This is NOT the test that a running channel can be changed. That is simulation/channel_swap_tier.py, between machines,
through the real proxy and daemon. This is the quick check after touching core/channel_setting.py or
core/channel_protection.py.

  python3 core/test_channel_setting_oracle.py
"""
import os
import struct
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
    for name in ("A", "B"):
        with open(os.path.join(keys, name), "wb") as f:
            f.write(os.urandom(32))
    from core import channel_setting as C
    from core import channel_protection as P
    from core import homa_transport as HT
    C._started = True                                       # no held read: the settings are put here by hand
    C._me = "10.2.2.1"
    C.read_now = lambda: C._cur                             # and no read of the ControlRam at a refusal: there is none here

    # ---- a driver's word
    C._cur = {"driver": "softhoma"}
    check(C.driver() == "softhoma" and C.driver("10.9.9.1") == "softhoma", "the tuple names softhoma: that is the word for a new session")
    C._cur = {"driver": "quantum"}
    check(C.driver() is None, "the tuple names a word no driver registered: it is no word, and the node's own word decides")
    HT.register_driver("quantum", "homa")
    check(C.driver() == "quantum" and HT.DRIVERS["quantum"] == "homa", "a driver registers its word: the tuple may then name it")
    try:
        HT.register_driver("bad", "udp"); said = False
    except ValueError:
        said = True
    check(said, "a word is registered as one of the engine's two session kinds, and nothing else")

    # ---- one RAM's own setting
    C._cur = {"driver": "tcp"}
    C._rams = {"databasehost.frognet": {"driver": "softhoma", "key": "A", "accept": ["A"]}}
    C._at = {"databasehost.frognet": "10.2.2.1"}
    check(C.driver("10.2.2.1") == "softhoma" and C.sending_key("10.2.2.1") == "A" and C.driver("10.3.3.1") == "tcp" and C.sending_key("10.3.3.1") == "",
          "a RAM's own tuple: the connection to the machine that RAM is on is its setting, and every other connection is the channel's")
    check(C._accepted(C.here()) == ("A",), "on the RAM's machine, what arrives is under the RAM's setting")
    C._at = {"databasehost.frognet": "10.4.4.1"}
    check(C.driver("10.4.4.1") == "softhoma" and C.driver("10.2.2.1") == "tcp" and C._accepted(C.here()) == (),
          "the role moved: the setting is the new machine's connection, and the old machine's is the channel's again")

    # ---- the sealed message
    C._at = {"databasehost.frognet": "10.2.2.1"}
    frame = os.urandom(300001)
    b = C.protect(4, frame, "A", "10.2.2.1")
    got, name = C.unprotect(4, b, True, "10.9.9.1")
    check(got == frame and name == "A" and frame[:64] not in b, "a 300,001-byte message under key A: none of it in the clear, and it opens to exactly what was sealed")
    try:
        import cryptography.hazmat.primitives.ciphers.aead as A
        same = A.AESGCM(open(os.path.join(keys, "A"), "rb").read()).encrypt(b[2:14], frame, struct.pack("!Q", 4) + b"A") == b[14:]
        check(same, "the bytes are the bytes the Python module cryptography makes for the same key, nonce and associated data")
    except ImportError:
        print("  [SKIP] the Python module cryptography is not here to compare bytes with (it is not needed)")
    for what, n, blob in (("another message's number", 6, b), ("one bit changed", 4, b[:-1] + bytes([b[-1] ^ 1]))):
        try:
            C.unprotect(n, blob, True, "10.9.9.1"); said = False
        except C.KeyRefused:
            said = True
        check(said, "a sealed message with %s does not open, and is refused" % what)
    try:
        C.unprotect(4, C.protect(4, b"x", "B", "10.2.2.1"), True, "10.9.9.1"); said = False
    except C.KeyRefused as e:
        said = "accepts" in str(e)
    check(said, "a request under key B where only A is accepted: refused, and it says what is accepted")
    P.register_protection("xor-test", lambda k, n, a, f: bytes(x ^ 0x5A for x in f), lambda k, n, a, f: bytes(x ^ 0x5A for x in f))
    C._rams["databasehost.frognet"]["protection"] = "xor-test"
    b2 = C.protect(4, b"hello", "A", "10.2.2.1")
    check(C.unprotect(4, b2, True, "10.9.9.1")[0] == b"hello" and b2.endswith(bytes(x ^ 0x5A for x in b"hello")),
          "a protection registered by name is the one used when the tuple names it")
    C._rams["databasehost.frognet"]["protection"] = "rot13"
    try:
        C.protect(4, b"hello", "A", "10.2.2.1"); said = False
    except C.KeyRefused as e:
        said = "rot13" in str(e)
    check(said, "a protection nobody registered: refused, and it is named")

    print("\n%s" % ("ALL CHANNEL-SETTING ORACLE CHECKPOINTS PASS" if not FAILED else "CHANNEL-SETTING ORACLE FAILED (%d)" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = main(); sys.stdout.flush(); os._exit(rc)
