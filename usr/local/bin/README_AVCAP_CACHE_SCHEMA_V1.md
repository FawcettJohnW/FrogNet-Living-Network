# AVCAP_CACHE_SCHEMA_V1 — stop the capability probe republishing a stale cache

## What this fixes
`frognet_capability_probe.sh` computes the static capability block once per boot and
caches it to `/run/frognet_avcap.json`, then **returns that cache verbatim on every
later call** with no schema check. A node whose cache was written by an older probe
(when a field like memory was a nested object) therefore republishes that old shape
forever — emitting a dict where the election expects a scalar. On the consumer side
`database_handler.score`/`discovery/hosts._num` then hit `int({...})`; pre-guard that
crashed the merge, and even with the guard the candidate is silently excluded. This is
the NY1 "malformed capability / dict where a scalar belongs" case. (`install_mediahost.sh`
already carries a manual `rm -f /run/frognet_avcap.json` — acknowledging the staleness
but not preventing it.)

## The fix (publisher-side; ~59 lines, see the diff)
The probe now validates the cache against the CURRENT numeric schema before trusting it:
- required numeric fields (`cores, cpu_mhz, cpu_bench_total, mem_total_kb,
  mem_available_kb, disk_write_mbps, disk_fsync_ms, disk_free_gb, av_port`) must be
  present and scalar; the optional `mysql_innodb_pool_bytes` must be scalar if present
  (absent is fine — the consumer defaults it); `lan_ip` must be a non-empty string;
- a cache that fails is **dropped and recomputed** (with a loud stderr line), so a stale
  cache can never be republished;
- a final guard before emit refuses to publish silently if any numeric field is somehow
  non-scalar (should never fire after recompute; the consumer's `_num` guard remains the
  safety net, this is the loud signal);
- the cache path is overridable via `FROGNET_AVCAP_CACHE` (used by the oracle; default
  `/run/frognet_avcap.json`).

The numeric list is kept in sync with `database_handler.score` and `discovery/hosts._num`.
This is the contract those two read.

## Prove-don't-assert
```
bash oracle_avcap_cache.sh ./probe_orig.sh                 # FAIL / exit 1 (republishes the dict)
bash oracle_avcap_cache.sh ./frognet_capability_probe.sh   # PASS / exit 0 (rejects + recomputes)
```
The oracle seeds the real cache path with the exact incident poison (`mem_available_kb`
as a dict), runs the probe, and checks every election-numeric field in the emitted blob
is a scalar. Old probe emits the dict (FAIL); new probe recomputes a scalar (PASS).
Separately verified: a VALID cache is still returned verbatim (fast path intact), and a
cold start with no cache recomputes cleanly.

## Deploy (machines — every capability-publishing node)
1. Install over `/usr/local/bin/frognet_capability_probe.sh` on each candidate node
   (databasehost/mediahost — anything that publishes a capability tuple). The install
   scripts (`install_databasehost.sh`, `install_mediahost.sh`, `frognet_setup_advertisers.sh`)
   copy this file, so future installs carry it; for in-place, push the file directly.
2. No service restart needed — the capability timers invoke the probe fresh each publish.
   On the next publish the probe self-heals: it rejects the stale cache and recomputes.
   You do NOT need to manually `rm /run/frognet_avcap.json`; the probe now does it itself
   when the cache is wrong-schema (clearing it manually is harmless).
3. Verify on NY1:
   - `frognet_capability_probe.sh | python3 -c 'import sys,json;b=json.load(sys.stdin);print(type(b["mem_available_kb"]).__name__)'`
     → `int` (not `dict`).
   - On the next merge, NY1's databasehost candidate should score normally — no
     `SERVICE_CAND ... MALFORMED excluded` / `evaluate_failed` for NY1.

This fixes the publisher. The consumer-side `_num`/EVAL_ISOLATE guards stay — they're the
safety net for any future malformed publish, not a substitute for this.
