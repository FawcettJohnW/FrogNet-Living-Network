# FROGNET_NOW — mediahost prefers on-segment host (MEDIAHOST_ONSEGMENT_V1, 2026-06-20)

## Symptom (New-York-2 runMerge)
mediahost.frognet elected 10.130.130.1 (Seattle3) — a host reached across a
~206ms transit hop — over the local 10.28.28.1 (New-York-2) / 10.102.60.1
(New-York-1). For an A/V relay that is backwards: a beefy box 200ms away is the
wrong media host.

## Cause (read from source)
Capability tuples carry NO RTT/distance. The only locality signal is the lan/wan
split, and that flags ONLY wireguard-OVERLAY hosts as remote. Seattle3 is reached
via TRANSIT (eth1 -> New-York-1 relay), not an overlay, so it lands in bucket=lan
and wins on raw capability score (56.8 vs NY2 24.9 vs NY1 2.1). Pre-existing: the
old lan_list code picks it too (lan_list == hosts_list on this no-overlay island).
mediahost is the locality-sensitive role; databasehost is not.

## Fix
- discovery/live.py: compute the node's directly-connected /24s from
  _connected_prefixes(devs) and pass them as onsegment_subnets to the election.
- core/frognet_service_hosts.py: thread onsegment_subnets through
  apply_to_etc_hosts -> service_host_lines; tag each candidate c["_onseg"] =
  (its /24 is directly connected). Other roles ignore the tag.
- core/sotf_handler.py SotFMediaHandler.evaluate: elect among on-segment
  candidates when any exist; else fall back to the whole set (never empty).
  databasehost unchanged. Co-segment nodes share the on-segment set, so they
  agree — the earlier "single network-wide mediahost / no divergence" property
  holds within a segment.

## Proof
core/test_mediahost_onsegment_oracle.py drives the REAL SotFMediaHandler and
DatabaseRoleHandler with the NY-2 candidate set:
  - OLD pond-wide pick = Seattle3 (fail-on-old);
  - NEW mediahost = 10.28.28.1 (best on-segment), Seattle3 excluded;
  - databasehost still global -> Seattle3 (locality not applied);
  - fallback with no on-segment candidate -> best overall (never empty);
  - a weak on-segment host beats a strong off-segment one;
  - untagged candidates (older caller) -> pond-wide behavior (back-compat).
Regression: test_mediahost_pondwide_oracle still PASSES.

## NOT in this change (still pending)
- databasehost malformed-tuple crash: a capability tuple with
  mem_avail_kb={'1':..,'5':..,'15':..} (a loadavg dict) makes databasehost
  score() raise -> SERVICE_ELECT winner=none, saved only by DBHOST_FLOOR. Needs
  a malformed-field guard.
- Stale "Seattle5" identity / phantom 10.160.160.1 / 10.179.179 capability rows.
