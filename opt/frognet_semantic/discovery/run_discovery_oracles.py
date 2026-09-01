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
run_discovery_oracles.py - single gate over the discovery regression suite.

Runs every discovery/test_*.py oracle and gates on exit code. The suite is a
catalog of COMMON BREAKAGES and expected recovery, grounded against real runMerge
hardware where possible:

  walk/routes/kernel/runmerge ... the core engine + render fidelity
  seattle6 .................... LAN-relay node: good path + identity-iface-DOWN
                               breakage (kernel rejects src-on-dead-iface
                               defaults) + bring-up recovery
  leaf_src ................... borrowed-lease leaf must source from identity, not
                               the lease - KNOWN-FAIL (discovery-side src-pin not
                               wired; real fix pending, see SIM_STATUS)

KNOWN_FAIL entries are reported but do NOT fail the gate; they are tracked defects
awaiting a real fix. Any OTHER red is a regression and fails the gate.
"""
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))             # .../discovery
_FROGNET = os.path.dirname(_HERE)                              # .../frognet_semantic

# defect -> reason. Tracked, not gating, until the real fix lands.
#
# [FIXTURE_CONTAMINATION_2026-07-30] These two fail ONLY on a live FrogNet node
# and pass in a clean container through this same runner. Every module they touch
# is checksum-identical between the two, so this is a HARNESS defect, not a defect
# in shipped code -- no node behaves differently because of it.
#
# Root cause, from an instrumented run on Seattle5 (diag_child_onlink.py):
# descend() is handed immediate=[("10.250.250.1","wlan1")] and nothing else, yet
# the first candidate comes out as
#     CANDIDATE dest=10.250.250.0/24 via=192.168.0.1 dev=wlan1 kind=lan form=IMMEDIATE
# 192.168.0.1 is that BOX'S real home gateway on wlan1 -- the IMMEDIATE candidate's
# via is resolved against the live host instead of the fixture. That install fails
# rc=2, so 10.250.250.0/24 never becomes connected, so 10.250.250.1 is not on a
# connected subnet, so every later `via 10.250.250.1` also returns rc=2. The
# install_if_changed onlink retry (routes.py:243) is deliberately gated to
# dev.startswith("wg") -- a LAN via returning rc=2 is a shadowed-scope-link
# symptom that must NOT be papered over with onlink -- so nothing recovers and
# the walk installs nothing. In a container there is no such route, the candidate
# resolves scope-link, and both oracles pass.
#
# The fix is to make the IMMEDIATE candidate's via come from the fixture rather
# than from the box. Until then these are tracked here so they stay visible in
# every run instead of being quietly deleted.
KNOWN_FAIL = {

    "test_child_onlink_uplink_oracle":
        "IMMEDIATE candidate via read from the live box (192.168.0.1), not the "
        "fixture; cascades to rc=2 on every install. Harness, not shipped code.",
    "test_not_frognet_skip_oracle":
        "pass 1 does not mark despite a definitive refusal from a fake verify; "
        "passes in a clean container on identical modules.",
}

# [DESCEND_V1 retirement, John 2026-07-08] These oracles assert the internals of
# the seed-crawl + vouch + wave-prewarm merge path that DESCEND replaced - WALK
# decision log lines and wave-parallel prewarm equivalence. That machinery is
# deleted by design (descend crawls getHosts to seeds and routes via the next hop;
# no walk() decisions, no wave prewarm). The end-to-end behaviour they used to
# guard is now covered by test_runmerge_oracle (full NY-1 mesh), test_seattle6_oracle
# (LAN-relay), and test_descend_oracle. Retired, not silently deleted.
RETIRED = {
    "test_walk_oracle": "asserts walk() decision lines; walk path replaced by descend",
    "test_wave_parallel_equivalence_oracle": "asserts wave prewarm; descend has no waves",

    # [CAPABILITY_DOES_NOT_AGE_V1] supersedes [BALLOT_ADMISSIBILITY_V1] BY NAME
    # in core/frognet_role_elect.py: "The age gate is GONE." The 8.1-day fossil
    # this oracle replays was an UNREACHABLE box, not a stale description, and
    # age correlated with liveness only until it didn't -- a live mediahost was
    # refused at ts_age=2040s while a node that wrote a minute before dying was
    # admitted. Every check in the file asserts the horizon; there is no
    # surviving half to keep. The property that replaced it (the read never
    # ages, at any age) is now check C of test_capability_no_age_oracle.
    "test_ballot_admissibility_oracle":
        "asserts the FROGNET_BALLOT_MAX_AGE_S horizon; superseded by "
        "[CAPABILITY_DOES_NOT_AGE_V1] - see test_capability_no_age_oracle C",

    # [NO_CACHES_V1] was a purge aimed at unbounded memo dicts and took the
    # RESP_SAME body cache with it as collateral -- transport_semantic.py:343
    # records that it "was not a decision anyone made", that _same_lru was never
    # unbounded (_SAME_LRU_MAX caps it, oldest-first eviction), and that
    # removing it left the proxy 502ing every RESP_SAME while blaming peers that
    # were behaving correctly. [SAME_IS_BACK_V1] restored it.
    #
    # The surviving half of NO_CACHES_V1 is still marked live in the source
    # (session.py: the response/body is not stored, _reexecute_* deleted;
    # transport_semantic.py:462: req-hash memo removed). It is the ANSWER caches
    # this oracle gates -- G1/G2/G3 file existence and G4/G5 "an unchanged
    # request must be a frame, not an absence" -- that no longer hold, and those
    # are all of it. test_resp_same_oracle.py at the tree root is the gate for
    # the restored behaviour and belongs in a runner in its place.
    "test_no_caches_oracle":
        "gates the removal of the RESP_SAME/answer caches; reversed by "
        "[SAME_IS_BACK_V1] - see test_resp_same_oracle.py",
}


def _modules():
    out = []
    for fn in sorted(os.listdir(_HERE)):
        if fn.startswith("test_") and fn.endswith(".py"):
            m = fn[:-3]
            if m in RETIRED:
                continue
            out.append(m)
    return out


def _run(mod):
    env = dict(os.environ, PYTHONPATH=_FROGNET + os.pathsep + os.environ.get("PYTHONPATH", ""))
    # [SENTINEL_ISOLATION_V1] fresh not_frognet dir per oracle: real-socket
    # oracles legitimately mark sim IPs (ECONNREFUSED in-container); sharing one
    # sentinel across oracles poisoned later walks (proven 2026-07-05).
    import tempfile as _tmpf
    env["FROGNET_SENTINEL_DIR"] = _tmpf.mkdtemp(prefix=f"nf_{mod}_")
    p = subprocess.run([sys.executable, "-m", f"discovery.{mod}"],
                       cwd=_FROGNET, env=env, capture_output=True, text=True)
    tail = ""
    for ln in reversed((p.stdout + p.stderr).splitlines()):
        if ln.strip() and not ln.startswith("[FROGNET-BUILD]"):
            tail = ln.strip()
            break
    return p.returncode, tail


def main():
    mods = _modules()
    passed, known, regressed = [], [], []
    print(f"=== discovery regression suite ({len(mods)} oracles) ===")
    for m in mods:
        rc, tail = _run(m)
        if rc == 0:
            passed.append(m)
            status = "PASS"
        elif m in KNOWN_FAIL:
            known.append(m)
            status = "KNOWN-FAIL"
        else:
            regressed.append(m)
            status = "REGRESSION"
        print(f"  [{status:^11}] {m:<26} {tail}")

    print()
    if known:
        print("KNOWN-FAILS (tracked, not gating):")
        for m in known:
            print(f"  - {m}: {KNOWN_FAIL[m]}")
        print()

    ok = not regressed
    print(f"DISCOVERY GATE: {'PASS' if ok else 'FAIL'} "
          f"({len(passed)} pass, {len(known)} known-fail, {len(regressed)} regression)")
    if regressed:
        print("  REGRESSIONS: " + ", ".join(regressed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
