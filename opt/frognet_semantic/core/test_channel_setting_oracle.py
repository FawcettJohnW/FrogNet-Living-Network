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
    C._cur = {"driver": "chorus"}
    check(C.driver() == "chorus" and C.driver("10.9.9.1") == "chorus", "the tuple names chorus: that is the word for a new session")
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
    C._rams = {"databasehost.frognet": {"driver": "chorus", "key": "A", "accept": ["A"]}}
    # [THE_SETTING_IS_READ_BY_THE_MEMORYS_NAME_V1] John, 2026-11-21: "The setting in a tuple". It is read by the memory's
    # name, never by which memory is at an address.
    R = "databasehost.frognet"
    C._rams["otherram.frognet"] = {"driver": "tcp", "key": "B", "accept": ["B"]}
    check(C.driver("10.2.2.1", R) == "chorus" and C.sending_key("10.2.2.1", R) == "A" and C.driver("10.2.2.1") == "tcp" and C.sending_key("10.2.2.1") == "",
          "a memory's own tuple is the setting of the connection FOR that memory; a connection for no memory is the channel's, whatever is at its address")
    check(C.driver("10.2.2.1", "otherram.frognet") == "tcp" and C.sending_key("10.2.2.1", "otherram.frognet") == "B",
          "two memories on one machine each have their own setting")
    check(C._accepted(C.here(R)) == ("A",) and C._accepted(C.here("otherram.frognet")) == ("B",) and C._accepted(C.here()) == (),
          "what arrives for a memory is under that memory's setting; what arrives for none is under the channel's")
    check(C.driver("10.4.4.1", R) == "chorus", "the role moved: the memory's setting is still its own, at the new address")
    check(C.ram_of("DatabaseHost.frognet") == R and C.ram_of("databasehost.frognet:80") == R and C.ram_of("10.2.2.1") == ""
          and C.ram_of("nosuchram.frognet") == "" and C.ram_of(None) == "",
          "the memory a request is for is the name it gives, when that memory has a tuple of its own; an address or another name is none")
    del C._rams["otherram.frognet"]

    # ---- the sealed message
    frame = os.urandom(300001)
    b = C.protect(4, frame, "A", "10.2.2.1", R)
    got, name = C.unprotect(4, b, True, "10.9.9.1", R)
    check(got == frame and name == "A" and frame[:64] not in b, "a 300,001-byte message under key A: none of it in the clear, and it opens to exactly what was sealed")
    try:
        import cryptography.hazmat.primitives.ciphers.aead as A
        same = A.AESGCM(open(os.path.join(keys, "A"), "rb").read()).encrypt(b[3:15], frame, struct.pack("!Q", 4) + b[:3]) == b[15:]
        check(same, "the bytes are the bytes the Python module cryptography makes for the same key, nonce and associated data")
    except ImportError:
        print("  [SKIP] the Python module cryptography is not here to compare bytes with (it is not needed)")
    for what, n, blob in (("another message's number", 6, b), ("one bit changed", 4, b[:-1] + bytes([b[-1] ^ 1]))):
        try:
            C.unprotect(n, blob, True, "10.9.9.1", R); said = False
        except C.KeyRefused:
            said = True
        check(said, "a sealed message with %s does not open, and is refused" % what)
    try:
        C.unprotect(4, C.protect(4, b"x", "B", "10.2.2.1", R), True, "10.9.9.1", R); said = False
    except C.KeyRefused as e:
        said = "accepts" in str(e)
    check(said, "a request under key B where only A is accepted: refused, and it says what is accepted")
    P.register_protection("xor-test", lambda k, n, a, f: bytes(x ^ 0x5A for x in f), lambda k, n, a, f: bytes(x ^ 0x5A for x in f))
    C._rams["databasehost.frognet"]["protection"] = "xor-test"
    b2 = C.protect(4, b"hello", "A", "10.2.2.1", R)
    check(C.unprotect(4, b2, True, "10.9.9.1", R)[0] == b"hello" and b2.endswith(bytes(x ^ 0x5A for x in b"hello")),
          "a protection registered by name is the one used when the tuple names it")
    # [THE_ALGORITHM_IS_PART_OF_THE_SETTING_V1] the message names its algorithm; the setting says which are accepted
    C._rams[R].pop("protection")
    aes = C.protect(4, b"circuit", "A", "10.2.2.1", R)
    C._rams[R].update(protection="chacha20-poly1305", protections=["aes-256-gcm", "chacha20-poly1305"])
    cha = C.protect(4, b"circuit", "A", "10.2.2.1", R)
    check(aes[0] == 1 and cha[0] == 2 and aes[1:3] == cha[1:3] == b"\x01A" and C.opened(4, aes, True, None, R) == (b"circuit", "A", "aes-256-gcm")
          and C.opened(4, cha, True, None, R) == (b"circuit", "A", "chacha20-poly1305"),
          "a sealed message begins with its algorithm's number and its key's name; while both algorithms are accepted, a message sealed either way opens, under the same key")
    C._rams[R]["protections"] = ["chacha20-poly1305"]
    try:
        C.opened(4, aes, True, None, R); said = False
    except C.KeyRefused as e:
        said = "aes-256-gcm" in str(e) and "accepts" in str(e)
    check(said and C.opened(4, cha, True, None, R)[0] == b"circuit", "once only the new algorithm is accepted, a request sealed with the old one is refused, and it says so")
    check(C.opened(5, C.protect(5, b"reply", "A", None, R, "aes-256-gcm"), False, "10.2.2.1", R)[2] == "aes-256-gcm",
          "a reply is sealed as its request arrived, whatever the setting seals requests with now")
    try:
        C.opened(4, bytes([99]) + aes[1:], True, None, R); said = False
    except C.KeyRefused as e:
        said = "99" in str(e)
    check(said, "a message that names an algorithm this build does not have is refused, and the number is said")
    C._rams[R].pop("protections")
    C._rams["databasehost.frognet"]["protection"] = "rot13"
    try:
        C.protect(4, b"hello", "A", "10.2.2.1", R); said = False
    except C.KeyRefused as e:
        said = "rot13" in str(e)
    check(said, "a protection nobody registered: refused, and it is named")

    print("\n%s" % ("ALL CHANNEL-SETTING ORACLE CHECKPOINTS PASS" if not FAILED else "CHANNEL-SETTING ORACLE FAILED (%d)" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = main(); sys.stdout.flush(); os._exit(rc)
