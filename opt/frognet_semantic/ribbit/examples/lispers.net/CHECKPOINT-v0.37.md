# v0.37 Map-server configuration as ETR-owned held truth — GREEN

Base: v0.36 GREEN. Red: CHECKPOINT-v0.37-red.md (48 tests, 1 error, against unchanged v0.36).

## Behaviour
- `etr_map_server.add / .get / .delete / .wait`: the ETR's map-server entries (control lisp_map_server_command /
  lisp_ms): address, ms-name (default "all"), alg sha1|sha256 ("sha2" accepted; a key without a type is
  SHA-256, as in the control), key-id, proxy-reply, merge, refresh, want-map-notify, site-id (integer).
- Published as cell `lisp / etr-map-server / <address>` with public policy only; withdrawal is inactive state.
  The password is kept in the configured ETR process (`ms_keys`) and never written.
- Held view: one lock-free snapshot, one watcher, applied cell `etr-map-server-applied`; `.wait` observes it
  (optional `timeout_s`).
- `wire.etr_register4 map_server=<address>` builds the register from held truth only: map-server policy from
  the held view, records from the held database mapping, password process-private. xTR-ID stays an argument —
  it is the ETR's identity, not map-server configuration (control: one random value reused for all
  map-servers). Refresh is set only when the caller says this is the periodic send AND the map-server asks
  for refresh (control). The explicit-argument form is unchanged.
- `resolver.stats` gains `map_server_reads` / `map_server_reads_total`.

## Qualification
- Local 47 PASS / 1 explicit SKIP; clean FNW1 47 PASS / 1 explicit SKIP.
- Held-config register == explicit-argument register, byte for byte; explicit form still byte-identical to the
  control (Dino oracle: all three PASS).
- Independent processes: map-server configured on the ETR; ETR builds from held truth with
  map_server_reads 0 and database_mapping_reads 0; a separate process observes and reads the config (no
  password); tools/check_ms_secret.cpp (prebuilt as tools/check-ms-secret, like check-site-secret; rebuild:
  g++ -std=c++17 -O2 -pthread tools/check_ms_secret.cpp ribbit_cpp/frogram.cpp -o tools/check-ms-secret), a raw FNW1 reader, confirms the published cell has the address and no
  password field. All other held tests and cross_process_ram PASS.
- Runtime lock-free probe YES; zero participant locks, atomic shared_ptr, Memory::remove, lines over 200;
  package_check PASS.

## Performance (artifacts/performance-v0.37.txt; 1-core sandbox, FNW1, warmed, JSON-lines interface cost)
- wire.etr_register4 from held map-server configuration: median 21.65 us/register (explicit arguments:
  27.30 — the difference is mostly fewer JSON arguments to parse per call, not the lookup).
- Request paths 21.30 / 25.91 / 26.10 us (v0.36: 21.02 / 25.47 / 25.82): noise. map_server_reads and
  database_mapping_reads 0 after all runs.

## Not claimed
- Periodic sending (next slice), DNS-named map-servers, ms-name to database-mapping selection (every entry goes
  to every map-server, which is the control's default "all"), encryption keys.
