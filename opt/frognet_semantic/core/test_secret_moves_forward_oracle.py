#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] oracle. John, 22 Nov 2026 23:48, contract by contract:

  "Only for a Role with its own secret file. The default secret never moves."
  "Setting gains 'step', a count. At a key switch the owner's page draws a new number as always and raises step by
   one, in one write."
  "On seeing step go to s, a machine computes, in memory:
     secret(s) = HMAC-SHA384(secret(s-1), 'next', Role's name, s)
     key = first 32 bytes of HMAC-SHA384(secret(s), sender's FNID, Role's name, the number)
   A machine that missed switches repeats the first line once for each step it missed."
  "1. Verify the setting's check under secret(s). ... If it fails, ignore the setting, log it, write nothing.
   2. Write secret(s) with its step over the Role's file, whole (write new, rename). Erase secret(s-1).
   3. Keys in use stay in memory until each direction passes its switch call, as at any switch."
  "A setting whose step is lower than the file's is ignored and logged. Each end's report carries its step."

Two machines are two secrets directories. Every computation the contracts name is made here a second time,
independently of the code under test. (Contract 3 and the report are proved between machines, by the Roles exercise.)
"""
import hashlib
import hmac
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
A_DIR, B_DIR = tempfile.mkdtemp(prefix="secrets-a-"), tempfile.mkdtemp(prefix="secrets-b-")
os.environ["FROGNET_CHANNEL_SECRETS"] = A_DIR
from core import channel_frame as F  # noqa: E402
from core import channel_setting as C  # noqa: E402
from core import channel_admin as ADM  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + str(detail)[:300]))


def on(d):
    """Be the machine whose secrets directory is d (a process holds what it has verified; a machine's are apart)."""
    C.SECRETS = F.SECRETS = d
    C._secrets.clear()


def h(key, *parts):
    return hmac.new(key, b"\n".join(p if isinstance(p, bytes) else str(p).encode() for p in parts), hashlib.sha384).digest()


def signed(value):
    """A setting as an owner's page on machine A writes it: its check made under the secret at its step."""
    on(A_DIR)
    v = dict(value)
    v["check"] = C.check_of("units", v)
    return v


def _read_throughout(d, steps, top, stop, out):
    from core import channel_frame as F
    n = bad = 0
    while not stop.is_set():
        n += 1
        try:
            bad += F.secret_at("units", steps, d)[0] != top
        except Exception:
            bad += 1
    out.put((n, bad))


def main():
    placed = os.urandom(32)                               # the Role's own secret, as placed by hand: step 0
    default = os.urandom(32)
    for d in (A_DIR, B_DIR):
        open(os.path.join(d, "units"), "wb").write(placed)
        open(os.path.join(d, "default"), "wb").write(default)
    s1 = h(placed, b"next", b"units", 1)
    s2 = h(s1, b"next", b"units", 2)
    s3 = h(s2, b"next", b"units", 3)

    # ---- the default secret, and a Role's own
    check("a Role with its own file uses it, at step 0 as placed", F.read_secret("units", A_DIR) == (placed, 0, True))
    check("a Role with no file of its own uses the network's default secret", F.read_secret("warning", A_DIR) == (default, 0, False))
    check("the default secret has no steps: asked for step 5 it is still the default", F.secret_at("warning", 5, A_DIR)[0] == default)

    # ---- the owner's page: a key switch raises step by one, in the one write
    on(A_DIR)
    v0 = ADM.with_step("units", {"driver": "tcp", "number": 111, "protection": "aes-256-gcm"}, {})
    check("a key switch (the number changing) raises step by one: sealing a Role at step 0 writes step 1", v0.get("step") == 1, v0)
    v_same = ADM.with_step("units", {"driver": "chorus", "number": 111, "protection": "aes-256-gcm", "step": 99}, dict(v0))
    check("a protocol change is not a key switch: the step is carried unchanged", v_same.get("step") == 1, v_same)
    v_def = ADM.with_step("warning", {"driver": "tcp", "number": 5, "protection": "aes-256-gcm"}, {})
    check("a Role on the default secret gets no step", "step" not in v_def, v_def)

    # ---- on seeing step s: computed in memory, verified, then written
    set1 = signed(v0)
    check("the check is keyed with the secret at the setting's step (96 hex)",
          set1["check"] == hmac.new(s1, C.check_text("units", set1), hashlib.sha384).hexdigest() and len(set1["check"]) == 96)
    check("making the check wrote nothing: the owner's file is still as placed", F.read_secret("units", A_DIR) == (placed, 0, True))

    on(B_DIR)
    bad = dict(set1, check="0" * 96)
    why = C._proved("units", bad, None)
    check("1. a setting whose check fails under secret(s) is ignored, with the reason", why is not None and "does not verify" in why, why)
    check("1. ... and NOTHING is written: the file is as it was", F.read_secret("units", B_DIR) == (placed, 0, True))
    check("1. ... and no secret is taken into use", C.step_in_use("units") is None)

    why = C._proved("units", set1, None)
    got = F.read_secret("units", B_DIR)
    check("the setting at step 1 is taken", why is None, why)
    check("secret(1) = HMAC-SHA384(secret(0), 'next', the Role's name, 1)", got[0] == s1)
    check("2. it is written over the Role's file with its step", got == (s1, 1, True))
    check("2. the file is whole: one line saying the step, then the 48 bytes", open(os.path.join(B_DIR, "units"), "rb").read() == F.STEPPED + b"1\n" + s1)
    check("2. nothing is left beside it", sorted(os.listdir(B_DIR)) == ["default", "units"], os.listdir(B_DIR))
    check("the default secret did not move", open(os.path.join(B_DIR, "default"), "rb").read() == default)
    k = F.key_for(C.secret_in_use("units"), "fnid-a", "units", 111)
    check("key = first 32 bytes of HMAC-SHA384(secret(s), the sender's FNID, the Role's name, the number)",
          k == h(s1, b"frognet channel key", b"fnid-a", b"units", 111)[:32] and len(k) == 32)
    check("this end's step, for its report", C.step_in_use("units") == 1)

    # ---- 2. erase secret(s-1): the bytes of the file that was replaced are overwritten
    old = os.open(os.path.join(B_DIR, "units"), os.O_RDONLY)          # a handle on the file holding secret(1)
    on(A_DIR); C._proved("units", set1, None)                          # (the owner's machine takes its own setting)
    set2 = signed(ADM.with_step("units", {"driver": "tcp", "number": 222, "protection": "aes-256-gcm"}, set1))
    check("the next key switch writes step 2", set2.get("step") == 2, set2)
    on(B_DIR)
    check("step 2 is taken", C._proved("units", set2, set1) is None and F.read_secret("units", B_DIR) == (s2, 2, True))
    was = os.pread(old, 200, 0); os.close(old)
    check("2. secret(1) is erased: the replaced file's bytes are zeros", s1 not in was and set(was) <= {0}, was[:40])

    # ---- a setting whose step is lower than the file's
    why = C._proved("units", set1, set2)
    check("a setting whose step is lower than the file's is ignored, with the reason", why is not None and "lower than the step" in why, why)
    check("... and the file stays where it is", F.read_secret("units", B_DIR) == (s2, 2, True))

    # ---- a machine that missed switches
    third = tempfile.mkdtemp(prefix="secrets-c-")
    open(os.path.join(third, "units"), "wb").write(placed)
    on(A_DIR); C._proved("units", set2, None)
    set3 = signed(ADM.with_step("units", {"driver": "tcp", "number": 333, "protection": "aes-256-gcm"}, set2))
    on(third)
    check("a machine still at step 0 that sees step 3 repeats the first line once for each step it missed",
          C._proved("units", set3, None) is None and F.read_secret("units", third) == (s3, 3, True))

    # ---- two processes of one machine see the same step
    on(B_DIR)
    check("a second process of the machine that sees step 2 again finds it written, and takes it", C._proved("units", set2, None) is None
          and F.read_secret("units", B_DIR) == (s2, 2, True) and C.step_in_use("units") == 2)
    check("a process a step behind does not put an earlier secret back over a later one",
          F.keep_secret("units", s1, 1, B_DIR) is False and F.read_secret("units", B_DIR) == (s2, 2, True))

    # ---- [A_SECRET_IS_NOT_READ_WHILE_IT_IS_BEING_REPLACED_V1] one process moves the secret, others read it throughout
    import multiprocessing as mp
    race = tempfile.mkdtemp(prefix="secret-race-")
    first, steps = os.urandom(32), 800
    open(os.path.join(race, "units"), "wb").write(first)
    top = first
    for i in range(1, steps + 1):
        top = F.next_secret(top, "units", i)
    stop, out = mp.Event(), mp.Queue()
    readers = [mp.Process(target=_read_throughout, args=(race, steps, top, stop, out)) for _ in range(3)]
    [r.start() for r in readers]
    x = first
    for i in range(1, steps + 1):
        x = F.next_secret(x, "units", i)
        F.keep_secret("units", x, i, race)
    stop.set()
    got = [out.get() for _ in readers]
    [r.join() for r in readers]
    reads, wrong = sum(g[0] for g in got), sum(g[1] for g in got)
    check("a process that reads the Role's secret while another moves it %d steps always reads a whole secret (%d reads, %d wrong)"
          % (steps, reads, wrong), reads > 0 and wrong == 0)
    shutil.rmtree(race, ignore_errors=True)

    # ---- one way
    check("secret(s-1) is not in the file at step s, and is not secret(s) run again", s1 not in open(os.path.join(B_DIR, "units"), "rb").read()
          and F.next_secret(s2, "units", 1) != s1)
    for d in (A_DIR, B_DIR, third):
        shutil.rmtree(d, ignore_errors=True)
    print("\n%d passed, %d failed  (%s, %s)" % (passed, failed, F.BUILD, C.BUILD))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
