# v0.40a Transport architecture record and registration bytes-on-the-wire — GREEN

Base: v0.40 GREEN (a8fd89c6cf7b6b911cedb63d7c3c00a56f8d03114f284bd64e1d16aa7ec9e484). Deliberately NOT numbered
v0.41: a separate session wrote a v0.41 red (native registration: etr_claimer / ms_governor) into the same working
tree while this was being done. This branch contains none of that work and was rebuilt from a clean v0.40 copy.

## Contents
- TRANSPORT-ARCHITECTURE.md: the three facilities (Memory/FNWP with BLDC, the high-speed connection, UDP LISP), the
  routing rule (structured -> BLDC; small unreducible -> control path; large unreducible -> high-speed), every LISP
  exchange classified, what replaces the Map-Register HMAC between Ribbit participants, and the measurement obligation.
- `transport.stats`: this participant's own FNW1 session counters (bytes out/in, raw/REPEAT/SAME/miss). Red first:
  TransportStatsContract against unchanged v0.40 -> `unsupported operation: transport.stats`
  (artifacts/ribbit-local-transport-stats-red.txt).
- `database_mapping.wait` accepts optional `rloc` and `timeout_s` (same extension as map_cache.wait in v0.35), so a
  holder's convergence on a replaced value can be observed instead of slept for.
- tools/measure_registration_bytes.py and its result (artifacts/registration-bytes.txt).

## Measurement (FNW1, this sandbox; NOT BLDC — BLDC is in the FrogNet proxy/daemon, not in this tree)
Native = FNW1 frame bytes (out+in) of each participant's own session, TCP/IP headers excluded: the ETR publishing its
database-mapping truth, and a Map-Server-side participant holding that truth with a held read.
UDP = full Map-Register(s) from the byte-identical encoder, split at 20 records as the control does.

| N entries | establish ETR / holder | refresh (re-publish unchanged) ETR / holder | one change ETR / holder | UDP register, per send (with IP/UDP headers) |
|---|---|---|---|---|
| 1 | 576 / 972 | 578 / 820 | 580 / 822 | 88 (116) |
| 10 | 5,771 / ~5,000–6,400 | 1,218 / ~5,500–6,400 | 581 / ~650–830 | 340 (368) |
| 100 | ~58,050 / ~54,000–56,000 | 7,710 / ~55,000–57,500 | 582 / ~650–830 | 3,100 in 5 packets (3,240) |

Holder figures vary run to run because a held read can wake with one cell or a batch; ranges are from three runs.

## What the numbers say
1. On FNW1 without BLDC, the native path is not smaller than UDP. Establishing 100 entries costs about 58 KB on the
   ETR's session against 3.1 KB of Map-Registers. The claimed advantage is not demonstrated here.
2. The dominant cost is our own schema: each database-mapping cell carries the full registration-shaped mapping JSON
   (iid, prefix, group, rlocs, ttl, last_registered, expires_at, use_register_ttl, registered, merge, xtr_id,
   site_id), roughly 580 bytes per write. Much of that is registration bookkeeping a database-mapping does not need.
   That is the vendor's schema to fix; it is not a property of FrogNet Memory.
3. REPEAT already helps the writer on unchanged refresh (7.7 KB versus 58 KB, about 77 bytes per unchanged cell), but
   the holder re-receives every re-published cell in full. Re-publishing every mapping to show liveness is the wrong
   shape; liveness belongs in one cell, whose cost does not grow with N.
4. A single change is where native is already competitive: about 580 bytes on the ETR, about 650–830 on the holder,
   independent of N, against a full register (3.1 KB at N=100) on every conventional refresh or change.
5. The BLDC measurement, the one that tests the architectural claim, has to run on a FrogNet node with the
   proxy/daemon. It stays open.

## Qualification
- Local 48 PASS / 1 SKIP; clean FNW1 48 PASS / 1 SKIP; all independent-process held and registrar tests PASS
  (artifacts/held-independent-v0.40a.txt); Dino byte oracle 4 PASS; zero participant locks, atomic shared_ptr,
  Memory::remove, lines over 200; package_check PASS.
- Environment note: stray ribbit-lisp processes left by an interrupted run made clean-FNW1 runs fail or hang, on v0.40
  as well. Every qualification run must start with no stray participants.
