# v0.38 Periodic ETR registration as a participant — GREEN

Base: v0.37 GREEN. Red: CHECKPOINT-v0.38-red.md (`unsupported operation: etr_registrar.start`).

## Behaviour
- `etr_registrar.start xtr_id first_s interval_s udp_port` (defaults 5 s, 60 s, 4342 — the control's trigger
  timer, LISP_MAP_REGISTER_INTERVAL and the LISP control port); `etr_registrar.stop`; `etr_registrar.sent
  address`; `etr_registrar.wait address min_sends timeout_s`. FNW1 backend only.
- The registrar is a participant. Its clock is a bounded held read on `etr-map-server`: a timeout is the
  periodic send (refresh bit if the map-server asks for refresh); a wake carrying a published active
  map-server entry is the immediate, non-refresh register to that map-server (the control triggers a register
  whenever the map-server command is applied). Each send run starts at the control's nonce 0xaabbccdddfdfdf01.
- It builds each register from held truth (database snapshot, its own copy of the map-server configuration,
  the process-private key snapshot) and sends it as a UDP datagram to the map-server.
- It writes only its own cells: `etr-register-sent|<ms>` (sends, records, refresh, nonce, bytes — never a
  password) and `etr-registrar|state` ("running", then "stopped" when it has actually stopped). Stop is a flag
  honoured at the next wake; nothing is sent after it.
- Map-servers configured before `start` returns get the first periodic send; the baseline is read on the
  request thread before the participant starts, so there is no start/config race.
- Process-private map-server keys are now an immutable single-writer snapshot (the registrar thread reads them;
  previously a std::map read only by the request thread).

## Found while making it green
- A first trigger rule ("newly active since I last looked") missed a delete-then-add of the same map-server:
  memory holds current values, so a reader that wakes once sees only the add. Replaced with the control's
  rule: every published active entry is a trigger. The latency benchmark exposed it.

## Qualification
- tools/test_etr_registrar.py (independent processes, FNW1, shortened intervals): timer send with refresh and
  the control nonce; immediate non-refresh register to a newly configured map-server within 1.2 s; next period
  reaches both map-servers; every UDP datagram accepted and registered by an independent Map-Server process; a
  third process observes the published send truth (no password); no datagram in two intervals after stop.
  PASS on 10 consecutive runs of the final code (artifacts/etr-registrar-repeat-v0.38.txt).
- Local 47 PASS / 1 SKIP; clean FNW1 47 PASS / 1 SKIP; all other independent-process held tests and
  cross_process_ram PASS; Dino byte oracle 3 PASS; runtime lock-free probe YES; reclamation stress
  born=100001 dead=100001; zero participant locks/CVs, atomic shared_ptr, Memory::remove, lines over 200.

## Performance (artifacts/performance-v0.38.txt; 1-core sandbox, FNW1)
- Registrar trigger latency (etr_map_server.add returning -> UDP Map-Register received), N=200: median 394 us,
  p90 508 us, max 2412 us. Includes the FNW1 write, the held-read wake, build, HMAC and sendto.
- Request paths and register build unchanged within noise; map_server_reads and database_mapping_reads 0.

## Not claimed
- Map-Notify handling on the ETR side (want-map-notify is sent, the notify is not consumed); the 20-record/MTU
  split; IPv6/IID records; DNS-named map-servers; NAT/Info-Request; per-map-server sockets; the local backend.
