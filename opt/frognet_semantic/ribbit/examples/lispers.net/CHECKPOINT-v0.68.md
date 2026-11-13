# Ribbit-LISP v0.68-front-sanitizes

## What changed

- **The region decides every Map-Register; the front sanitizes first.**
  - [FRONT_SANITIZES_V1] Before a register goes anywhere, the front runs the structural part of the region's own
    `wire.register_notify`. That is the encryption bit, the instance-id / (S,G) LCAF, the records for the EID's
    address family, and each record's prefix. The check is `Engine::sanitize_register`.
  - A packet that fails is rejected on the front, with the same error the region would give, and never leaves the
    machine.
  - A packet that passes goes to the region whole. The region still decides everything: decryption, authentication,
    site policy, all-or-nothing, the registration and the Map-Notify.
  - An encrypted register can only be read with its key, which is in the RAM host. So only its header is checked on
    the front.
- **Registers are synchronous calls, many at once.**
  - [REGISTER_STRANDS_V1] Each sender has its own strand. Each register on a strand is ONE synchronous call to the
    region's operation, run completely.
  - Every sender's strand makes its calls at the same time over the one session: fan-out/fan-in on the wire.
  - v0.66's asynchronous send (`send_register_async`, "registers in flight") is removed. The caller waits for its own
    answer.
- **The front can be stopped while it waits for configuration.**
  - [STDIN_WITHOUT_STDIO_V1] `lisp-boundary` reads its configuration lines with `read(2)`, not `std::cin`.
  - A thread parked in a stdio read holds stdin's FILE lock, and the SIGTERM path's `exit()` flushes every FILE under
    its lock. The front could not stop: the acceptance run's L4.10 restart hung.
- **The acceptance harness** (`tools/acceptance.py` v0.69):
  - lispers.net's REST API port is chosen free at start. It was 8080, which is Apache on a FrogNet node.
  - RUN-LISP's own output goes into the evidence, with the list of lispers.net components running.
  - On Debian and Raspberry Pi OS, lispers.net runs in a mount namespace where `/bin/sh` is bash (F-DEBIAN-SH in
    FINDINGS-FOR-DINO.md).
  - The agent skips command cells whose writer has gone.
  - The harness and the agent print a line as each system starts.
  - `--ribbit-only` runs the tests on Ribbit alone, for a machine that cannot run lispers.net.

The RAM host's region code is unchanged since v0.65. `RIBBIT_LISP_REGION_COMPAT` accepts v0.65 through v0.68, so the
droplet needs no reinstall.

## Measured

Ribbit's side of the acceptance suite (`--ribbit-only`, 78 tests). lispers.net cannot run in the build container,
which has no IPv6.

| run | result | register rejections | rejected by the front | rejected at the RAM host |
|---|---|---|---|---|
| v0.67, Seattle7 → droplet (both systems) | B FAIL: L2.5, L3.3, L4.5, L4.9, L2.14.json | every malformed register went to the RAM host | 0 | all |
| v0.68, loopback | PARITY 67, KNOWN DIFFERENCE 4, OBSERVATION 7 | 600 | 355 | 245 (200 encrypted, no such key) |
| v0.68, RAM host behind an 80 ms round trip (`tools/delay_line.py --delay-ms 40`) | PARITY 67, KNOWN DIFFERENCE 4, OBSERVATION 7 | 599 | 363 | 236 (all encrypted) |

`tools/qualify.sh`: every stage passes.

## Not yet run

- The whole suite across the network with lispers.net: Seattle7 runs the systems, NY2 the harness, and the droplet is
  the RAM host.
