# v0.43 Native registration: ETR truth governed in place — GREEN

Base: v0.42 GREEN. Red: CHECKPOINT-v0.43-red.md. Design: TRANSPORT-ARCHITECTURE.md; shape from
EVAL-v0.41-native-registration.md (the candidate's code was not used).

## Behaviour
- `etr.identity name xtr_id`: the ETR publishes etr-identity/<name> {xtr_id}; its database mapping becomes
  database-mapping@<name>|iid|group — its own truth, one writer. (Without an identity: the untagged variable, as before.)
- `ms_governor.start iid group name`: a Map-Server participant. Its thread holds etr-identity; each ETR gets a child
  that holds that ETR's database variable directly with a held read (no claim copy). For each entry it applies site
  policy from the Map-Server's held site view and writes, as its own truth: the governed registration (merge
  instance <prefix>|<xtr>), the governance mode, and its decision etr-decision@<governor>|iid|group / <etr>|<prefix>
  (accepted / rejected / withdrawn / error). Rejected entries write nothing but the decision.
- `etr_decision.wait`: the ETR observes its own decisions (held read). Nothing is sent: no Map-Register, no
  Map-Notify, no UDP, no HMAC (authority is FrogNet Memory writer identity plus governance; TRANSPORT-ARCHITECTURE.md).
- `resolver.wait` gains `absent` (present without a given RLOC — one ETR's contribution withdrawn) and `timeout_s`.
- `ram.count variable`: observation, used to prove no copy exists.
- FINDINGS-FOR-DINO.md: lispers.net observations for the report, each with an offer to file an issue or pull request
  (per-record Map-Register processing vs Ribbit's all-or-nothing; Map-Notify-Ack record count 0; deregistration only
  for dynamic EIDs). His code is not changed.

## Qualification
- tools/test_native_registration.py: 10/10 runs of the final code (artifacts/native-registration-repeat-v0.43.txt).
- Local 49 PASS / 1 SKIP; clean FNW1 49 PASS / 1 SKIP, 3/3; all independent-process held, registrar and native tests
  PASS; atomicity stress 2 x 600: 0 partial, 0 wrongly accepted; Dino byte oracle 4 PASS; runtime lock-free probe
  YES; reclamation stress born=100001 dead=100001; zero participant locks/CVs, atomic shared_ptr, Memory::remove,
  lines over 200; package_check PASS.

## Bytes and time (artifacts/native-change-bytes-v0.43.txt, performance-v0.43.txt)
- One RLOC change, FNW1 frame bytes per process, three runs: ETR 593 / 593 / 593; Map-Server 2,055 / 1,252 / 1,428;
  ITR 1,498 / 1,115 / 1,116. The v0.41 candidate's claim copy cost the ETR 2,427 for the same change. A conventional
  ETR sends one full register (88 B for N=1) on every change and every 60 s. BLDC not in this tree (v0.40a).
- Database change -> governor -> ITR resolves the new RLOC, N=100: median 2.04 ms, p90 2.44 ms, max 3.14 ms.
- Request and wire paths unchanged within noise.

## Not claimed (next, each with a red first)
- Liveness: governed registrations carry no expiry (expires_at 0); a dead ETR's registrations remain.
  Planned: one liveness cell per ETR, fresh-by-time, held by the resolver and checked at resolution.
- Re-governance: a site-policy change does not re-govern entries already decided.
- One governor per (iid, group) is assumed; decision variables carry the governor's name so two governors never
  write the same cell, but two governors would both write registrations.
- publish_known_length is read-then-write from the request thread and the governor thread (same value both ways).
