# v0.41 Multi-record Map-Register authorized once, from one snapshot — GREEN

Base: v0.40a (3fbac778b24c4cd3efe9f0bfd800d14ffd90f6e788b88eaba11779f635a6b5bd). Source of the fix: the evaluated
candidate (EVAL-v0.41-native-registration.md, Finding 1); nothing else from the candidate is included.

## Defect (present v0.25–v0.40a)
A multi-record Map-Register is validated, then applied. Each pass read the held site view. `registration.put` skips
authorization when no active site is visible ("open mode"). A site published between the passes let validation
authorize both records (no site yet -> open) and the apply pass reject record 2 after applying record 1: a partial
application of a register reported as rejected.

## Red (statistical — the defect is a race)
tools/stress_multirecord_atomic.py, 600 FNW1 rounds per run, against unchanged v0.40a, five runs:
partial applications 8, 0, 0, 0, 10 (18 in 3,000 rounds). artifacts/stress-multirecord-red-v0.41.txt.

## Green
- Authorization is decided once, in the validation pass; the apply pass does not re-read site policy
  (`apply_preauthorized`, request thread only, reset by scope guard, not settable from any operation).
- Five runs, 3,000 rounds: 0 partial applications (artifacts/stress-multirecord-green-v0.41.txt).

## Divergence from the control, recorded, NOT changed (decision needed)
The green runs show 1, 1, 13, 7, 14 registers accepted as "good" whose record 2 no site authorizes: validation saw no
active site and open mode accepted the whole register, consistently. The control never does this —
lisp_process_map_register (lisp.py ~10275-10330) skips any EID-record with no site ("Site not found"). Ribbit-LISP's
"no sites configured => accept everything" is a divergence carried since the early registration slices. Making
sites mandatory changes every fixture that registers without a site; it needs John's decision.

## Qualification
Local 48 PASS / 1 SKIP; clean FNW1 48 PASS / 1 SKIP; all independent-process held and registrar tests PASS;
Dino byte oracle 4 PASS; runtime lock-free probe YES; zero participant locks/CVs, atomic shared_ptr,
Memory::remove, lines over 200; package_check PASS.

## Performance (artifacts/performance-v0.41.txt)
Unchanged within noise: etr_request4 21.44, request4 25.16, etr_request6 25.79, etr_register4 26.84 / 21.48 (held
config), etr_notify4 84.15 us; registrar trigger 390 us median; notify UDP round trip 31 us median.
