# outputs/ -- what is current (2026-09-27)

**Package:** `ribbit-lisp-v0.54-row-locks.zip` -- sha256 `baaca19f7f65129a6b385b333f95f686e30d6fa0897fa2690c4fdeeabd888a81` (also in the .sha256 file). No built binaries:
every machine builds its own (`tools/qualify.sh` build stage). Banner: `RIBBIT-LISP v0.54-row-locks`.

## Acceptance (lispers.net 0.643 vs Ribbit-LISP)
- `acceptance/ACCEPTANCE-REPORT.md` -- the latest full run: 76 tests, PARITY 54, A FAIL 12, KNOWN DIFFERENCE 4,
  OBSERVATION 6, B FAIL 0 (`acceptance/acc_run.txt` is its console log).
- `acceptance/acceptance.py` -- the harness (same copy as tools/acceptance.py in the package).
- `REAL-HARDWARE-TESTS.md` -- how to run on hardware: the DDT/peering/lig set (`acceptance/acceptance_hw.py`, configs from `acceptance/make_hw_configs.py`) and the two-machine L6 runs (`acceptance.py --agent-listen` / `--agent`).
- `acceptance/SURFACE-lispers.md` -- P5: lispers.net's map-server / map-resolver surface and what the suite ran.
- `acceptance/coverage/` -- per-line coverage, lispers.net (lispers-html/) and Ribbit (ribbit-coverage*.html), from the
  72-test coverage run (`acceptance/ACCEPTANCE-REPORT-coverage-run.md`).
- `ACCEPTANCE-TEST-PLAN.md` -- the plan, the recording scheme (section 6), rulings (7), the P5 closure table (8).
- `FINDINGS-FOR-DINO.md` -- lispers.net findings with reproductions.

## Engineering record
- `CHECKPOINT-BOUNDARY-NEVER-WAITS.md` -- every change this session, with its gates and numbers.
- `RAM-HOST-LOCKING-DESIGN.md` -- row locks, per-wait triggers, the host Engine pool (John's rulings).
- Older working folders (`lispers-compare-wip/`, `read-your-write-wip/`, `boundary-never-waits/`, `step4-pickup/`)
  are history; where they disagree with the files above, the files above are current.
