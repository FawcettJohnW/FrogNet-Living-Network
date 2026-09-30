# lispers.net 0.643 vs Ribbit-LISP v0.63 -- the run of 2026-09-28

One complete run of `tools/acceptance.py` (behaviour, then performance), package v0.63-bounded:
a Raspberry Pi 5 load generator (FrogNetHost, 4 load processes) -> AI-Host running both systems side by side
(lispers.net 0.643 unmodified; Ribbit-LISP's front) -> Ribbit's state in FrogNet shared memory on streamingfrog.com,
a 2-core DigitalOcean droplet. Every performance run: 5 s warmup, 20 s measured. Files: ACCEPTANCE-REPORT.md,
PERF-REPORT.md, perf.json (every counter from all three machines), PERF-TIMELINE.html, performance-2026-09-28.png.

## Behaviour
78 tests: PARITY 53, lispers.net failures 14, KNOWN DIFFERENCE 4, OBSERVATION 7, Ribbit failures 0.

## Performance

| | lispers.net | Ribbit-LISP |
|---|---|---|
| lookups/s, 1 / 4 / 16 / 64 clients | 334 / 516 / 484 / 484 | 891 / 3,014 / 9,398 / 20,407 |
| lookup p50 at 64 clients | 130 ms | 2.9 ms |
| CPU per lookup at 64 clients | 2,586 µs | 29 µs (front); ~0 at the RAM server |
| lookups/s at 16 clients, 1 -> 10,000 registrations (host) | 938 -> 93 | 9,210 -> 9,123 |
| CPU per lookup, 1 -> 10,000 registrations (host) | 1,579 -> 11,373 µs | 44 -> 47 µs |
| registers/s, 1 / 4 / 16 / 64 clients | 357 / 793 / 769 / 718 | 39 / 161 / 591 / 1,565 |
| register p50, 1 / 64 clients | 2.8 / 85 ms | 25 / 38 ms |
| CPU per register at 64 clients | 1,650 µs | 680 µs front + 815 µs RAM server |
| mixed (80% lookups) ops/s, 64 clients | 495 | 5,125 |
| errors / unanswered, all runs | 0 / 0 | 0 / 1 (one request in the mixed 4-client run) |

## What the machines were doing (from perf.json)
- lispers.net's host never passed ~40% busy: it stops scaling while the machine has capacity left.
- Ribbit's lookups at 64 clients left AI-Host 82% idle and did not touch the RAM server; what bounds them further
  is not established by this run.
- Ribbit's registers at 64 clients had the 2-core droplet 69% busy: the shared memory's host is the next limit
  for registers.
- A Ribbit lookup is the UDP exchange only (~212 bytes per operation at AI-Host, the same as lispers.net); a Ribbit
  register also crosses to the shared memory and back (~1.7 KB per register at AI-Host, ~1.5 KB at the droplet).
