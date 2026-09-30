# v0.45 Re-governance of native registrations, derived at read — GREEN

Base: v0.44 GREEN. Red: CHECKPOINT-v0.45-red.md.

## Behaviour
- A native registration (one with a liveness key) resolves only while a site in the Map-Server's held site policy
  authorizes its prefix — checked at resolution and in resolver.wait, from the same held snapshot that already sits
  in the resolver participant. Nothing is rewritten when a site changes: derived, not maintained (Programming Ribbit
  §5). A site change wakes waiters (the site watchers publish the resolver's applied cell), so observation is by event,
  not by time-out.
- Chosen over the evaluation's first idea (a governor that re-writes affected registrations on site change): that
  needs a second writer of governed cells or an in-process wake mechanism, and adds writes proportional to the number
  of affected entries; the derivation needs neither.

## Qualification
- tools/test_native_regovernance.py 10/10 runs of the final code.
- Through tools/qualify.sh: build PASS; local 49 PASS / 1 SKIP; clean FNW1 49 PASS / 1 SKIP; all ten
  independent-process tests PASS and cross_process_ram PASS; Dino byte oracle 4/4; atomicity stress 3 x 600: 0
  partial, 0 wrongly accepted; pointer_atomic_lock_free=YES; born=100001 dead=100001; source gates 0/0; no line over
  200; package_check PASS.

## Performance (artifacts/performance-v0.45.txt)
- Re-governance, N=50: site deleted -> ITR stops resolving median 524 us (p90 684); restored -> resolves median
  619 us (p90 789).
- Native change -> ITR 1.58 ms median; request paths ~21-26 us; registrar trigger 321 us; notify UDP round trip 29 us.

## Not claimed
- Decision cells are not rewritten on a site change: an ETR's "accepted" records the governance at the time of its
  entry's last change; what resolves is derived now. (A decision refresh would need its own single writer.)
- UDP-path registrations are not re-governed on site deletion (unchanged behaviour; the control removes a deleted
  site's registrations — recorded as a difference for a later slice).
- Multiple governors.
