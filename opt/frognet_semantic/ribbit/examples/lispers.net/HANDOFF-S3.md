# HANDOFF — semantic-engine port, resume at S3

Read in order: this file, PORT-LOG.md, S3-CHARACTERIZATION.md, SEMANTIC-ENGINE-PORT-PLAN.md (original plan S1-S7),
CHECKPOINT-S1.md, CHECKPOINT-S2.md, PROGRAMMING-RIBBIT-BEST-PRACTICES.md. Do not ask John to re-explain.

## Bundle contents (everything; nothing is left to re-upload)
- ribbit-lisp-v0.45.3-S2.zip (or the newest ribbit-lisp-*.zip): the package, S1 and S2 green inside.
- frognet-source-20260924.tgz (c3ae092674f551cd3c44f3291bc4fbc41e7bbe458b4ef8b2d214126ba393a3c7): John's Python, the
  oracle for every slice; unpack and point FROGNET_SEMANTIC_ROOT at opt/frognet_semantic. Supersedes happydog; S1 and
  S2 re-verified against it (artifacts/s1-s2-oracle-vs-source-20260924.txt). File hashes:
  artifacts/oracle-source-20260924.sha256. SEMANTIC-ENGINE-PORT-PLAN.md is inside the package.
- The previous handoff bundle's files (ram-server static build, FINDINGS-FOR-DINO.md, etc.) are inside the package or
  in HANDOFF-semantic-port-and-lispers.zip if John re-supplies it.

## Standing rules (John's)
Red first, checkpointed on its own; green; full qualification; performance measured, never estimated; raw output
recorded; package; verify from a clean extract; SHA-256 for every artifact. Never claim what was not produced. Read the
source before coding; never invent a protocol. No fallbacks, no sleeps standing in for observed state, no locks in
participants, no Memory::remove, no source line over 200 characters, no bytecode in packages. Lead with the answer.
WRITE ARTIFACTS AS YOU GO: copy the package to /mnt/user-data/outputs at every checkpoint, not at the end.

## State
- S1 GREEN: ribbit_cpp/semwire.hpp (FNW1 v4.1 framing).
- S2 GREEN: ribbit_cpp/semcodec.hpp + pyval.hpp + pyuni_tables.hpp (codec v5). See CHECKPOINT-S2.md.
- S3 RED checkpointed: CHECKPOINT-S3-red.md (ribbit_cpp/semtpl.hpp throws; oracle tools/test_semtpl_oracle.py). Next: S3 green.
- Oracle root is now the FIXED Python (no-fallbacks-python-20260924.tgz over the 20260924 tree); CHECKPOINT-NO-FALLBACKS.md.
  S3 red revised (artifacts/semtpl-red-S3-r2.txt). The 20260924 tree without that overlay is NOT the oracle any more.
- John's rulings this session: (1) ast.literal_eval in json_handler.rebuild_reply is DECLINED -- the C++ raises loudly
  when a str-typed array value is not JSON, and the S3 oracle exercises and reports that category. (2) Templates go in the
  local MySQL instance on both sides, same tables and row encoding as core/store.py (John, 2026-09-24).
  (3) A template-learning failure after a RAW bootstrap aborts loudly; neither Python swallow is ported (S4).

## Environment lessons
- Container: 1 core, 4 GB. Keep oracle corpora free of 64 KiB values in random pools (OOM); put them in explicit cases.
- Commands are cut at 300 s: run long jobs with setsid nohup, write results to files, poll.
- Needs: apt libssl-dev liblz4-dev; pip lz4 future; lispers.net clone for the Dino oracle.
- A qualify.sh run can be lost if the results dir is removed or the run restarted; copy SUMMARY.txt to artifacts/
  and outputs on completion (the /tmp/q3.sh pattern).

## Current state (2026-09-25, later): S3 GREEN
Package ribbit-lisp-v0.47-S3-green.zip. Oracle root = frognet-source-20260924 (running) + codec-exact-20260925.tgz.
Done: S1, S2 (exact), S3. Next in order: the RAM-answer handler (RAM-ANSWER-HANDLER-SPEC.md), S4 (engine in frogram),
S5 (engine in the RAM server, source: ram-server-urldec-fix-20260925.tgz), then the Python port of the RAM wire, S6, S7.
Withdrawn, never deploy: the 0x05/0x15 cells packages; no-fallbacks-python-20260924-r2.tgz (its hosts.py broke the mesh).
Background jobs in this environment do not survive the end of a turn: run qualify in stage batches.
