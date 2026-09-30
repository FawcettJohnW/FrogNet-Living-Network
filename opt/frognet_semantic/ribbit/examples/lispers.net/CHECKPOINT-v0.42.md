# v0.42 No site, no registration — GREEN (bug fix)

Base: v0.41 GREEN. Red: CHECKPOINT-v0.42-red.md.

## The bug
`registration.put` skipped authorization entirely when the site table was empty (`if(!auth.empty())`). With no site
configured — or after the last site was deleted, or on a Map-Server whose sites had not been read yet — any
registration for any EID-prefix was accepted. It contradicted the project's own contract since v0.6 (CONTRACT.md:
"A registration for an unknown/unconfigured site is not accepted"; WORKLOG v0.6: "Unknown or unauthorized
registrations are rejected before state mutation") and the control (lisp_process_map_register skips a record with no
site, "Site not found"). It was not a design decision: nothing records one. It arose because registration (v0.4)
predates site authorization (v0.6); the authorization check was guarded so that the older tests, which never
configured a site, kept passing. v0.41 wrongly labelled it an open divergence pending a decision; it is a bug.

## The fix
Every record needs an authorizing site; an empty site table authorizes nothing.

## Tests that depended on the bug
22 conformance tests registered without configuring a site (10 in test_registration_contract.py, 12 in
test_wire_contract.py — including the basic Map-Register -> Map-Request -> Map-Reply path, most multi-record tests,
the IPv6 register tests and the ETR decoy registrations). None tested open acceptance; they predate sites. Each now
runs with a site covering exactly what it registers (per-test fixture, removal observed). Five tests that published
a site and used it without observing it now wait for the held site view first (the bug had hidden that race: with no
site visible, open mode accepted). Tools fixed the same way: test_control_wire_oracle.py, test_held_resolver.py,
bench_etr_request.py.

## Qualification
- Local 49 PASS / 1 SKIP; clean FNW1 49 PASS / 1 SKIP, 5/5 consecutive runs.
- Atomicity stress (v0.41's tool), three runs of 600 rounds: 600 x unauthorized, 0 partial applications, 0 wrongly
  accepted (v0.41 accepted 1-14 per run through open mode) — artifacts/stress-multirecord-v0.42.txt.
- All independent-process held and registrar tests PASS; Dino byte oracle 4 PASS; runtime lock-free probe YES;
  zero participant locks/CVs, atomic shared_ptr, Memory::remove, lines over 200; package_check PASS.

## Performance (artifacts/performance-v0.42.txt, performance-v0.42-run2.txt)
No path measured here is changed by the fix. Two runs on the 1-core sandbox, medians: etr_request4 21.33 / 23.11,
request4 25.51 / 26.85, etr_request6 25.87 / 28.63, etr_register4 32.59 / 27.02, held-config register 26.67 / 21.65,
etr_notify4 92.65 / 88.91 us; registrar trigger 464 / 413 us; notify UDP round trip 31 / 30 us. Run-to-run spread on
this host is up to ~20%; no attributable regression.
