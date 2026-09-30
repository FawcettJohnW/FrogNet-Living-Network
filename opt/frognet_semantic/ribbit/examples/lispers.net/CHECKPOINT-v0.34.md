# v0.34 IPv6 ETR Map-Request -> Map-Reply from held database_mapping — GREEN

Base: v0.33 GREEN. Red: CHECKPOINT-v0.34-red.md (45 tests, 1 error, against unchanged v0.33).

## Behaviour
- `wire.etr_request6`: IPv6 counterpart of `wire.etr_request4`, same contract (held ETR database mapping only;
  one record; authoritative; No-Action; TTL 1440; R bit; database miss unestablished).
- The two ETR operations are one handler selecting codec by family; codec functions are unchanged.

## Qualification
- Local: 44 PASS / 1 explicit SKIP (artifacts/ribbit-local-etr6-green-v0.34.txt).
- Clean FNW1: 44 PASS / 1 explicit SKIP (artifacts/ribbit-fnw1-etr6-v0.34-clean.txt).
- Independent-process held tests all PASS; tools/test_held_etr_request.py now covers IPv4 and IPv6, including
  /49 -> /48 fallback on withdrawal with zero request-time reads (artifacts/held-independent-v0.34.txt).
- Runtime lock-free probe YES; Dino byte oracle register4/reply4 PASS; zero participant locks, atomic
  shared_ptr, Memory::remove, lines over 200; package_check PASS.

## Performance (artifacts/performance-v0.34.txt; 1-core sandbox, FNW1, warmed, 5 x 20,000 pipelined requests,
JSON-lines interface cost)
- wire.etr_request4: median 21.40 us/request (v0.33: 20.36 with a 2-entry database; now 4 entries)
- wire.request4 (Map-Server): median 25.50 us/request
- wire.etr_request6: median 26.22 us/request
- database_mapping_reads after runs: 0

## Finding recorded, not changed in this slice
- The held database-mapping view is one snapshot per (IID, group) scanned in full for longest-prefix
  (`database_mapping.get`), unlike registration/map-cache/DDT, which hold one slot per prefix length and walk
  from longest. Its lookup cost therefore grows with the number of ETR database entries (consistent with the
  +5% above, which is also within run-to-run noise). An ETR's database is typically small; the per-length
  shape is the doctrine form if it is ever not.
