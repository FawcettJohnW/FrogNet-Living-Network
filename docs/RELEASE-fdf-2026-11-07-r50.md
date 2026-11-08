# fdf-2026-11-07-r50: roles on api.php, held reads in the core

Branch `roles-on-api-20261107` (from `integrate-20261031`). Design: `AI_READ_FIRST.md` section 10a.

## What changed

- **Roles are hosts** (`[ROLES_ON_API_V1]`). `databasehost.frognet` (the SystemRam) and `databasehost_control.frognet`
  (the ControlRam) are the default `api.php` on port 80 of the host the elections name. The LAN RAM manager on 8841,
  `ram_port`, `--ram-port`, `FDF_LAN_RAM_PORT`, `ensure_lan_ram` and `lan-rams.json` are gone. Services keep their
  ports (the region RAM manager on 8840, every AppRam).
- **Held reads are in api.php** (`[HELD_READ_IN_API_V1]`). `entity=held&action=hold` answers with the rows now. A write
  completes first, then its holds are fired through each holder's port 80 (`held_fire.php`, then
  `/run/frognet/held/<hid>.sock`). Holds are one-shot and are held again. The 50 ms wait loop is gone. `/etc/hosts`
  rewritten ends every hold; the caller holds again at the host it now names. The FrogNetHost holds one read, its
  `(frognethost, config, <guid>)`.
- **A machine leaving removes what it wrote** (`[WRITER_GONE_V1]`). When the proxy/daemon's last session to a peer ends,
  it tells its local api.php `remove_writer {subnet}`. On the WAN, fdf-hub closes the member's tunnel.
- **Capability is announced** (`[CAPABILITY_ANNOUNCED_V1]`): plain writes, a held read on `can` to write them again, and
  a withdrawal at stop. `ram hold` is removed from the `ram` tool.
- **A split is LAN-only** (`[SPLIT_IS_LAN_ONLY_V1]`). When the region is lost, the daemon withdraws its routes, names
  and tunnels and runs `runMerge`. It keeps standing sessions to the list, and the first one that answers rejoins.

Builds reported in logs: `api.php 20261107-held2`, `frognet_held 20261107-held1`, `fdf-app-2026-11-07-roles1`,
`frognet-host-2026-11-07-roles1`, `dc-door-2026-11-07-roles1`, base `fdf-2026-11-07-r50`.

## You must re-enter after updating

The old ControlRam lived in the LAN RAM manager (8841). Its tuples are not moved into api.php:

1. **The teams-join verifier.** On the app's admin machine run `fdf-app config <app> ... --join <TEAMS_PASSWORD>`
   again. Only its verifier is stored.
2. **The child-team entities** `(fdf-app, child, <app>.<team name>)`. Each team that joined the EOC by its QR has to
   join again with the teams password.

The app's configuration tuple does not need re-entry. The merge writes it into the new databasehost-control again from
the installed machines' copies (`[CONFIG_WRITTEN_AGAIN_BY_THE_MERGE_V1]`).

## Gates (in the container)

`simulation/run_all.py` PASS, with 84/84 discovery oracles. Also: `test_frognet_host` 10/10, `test_fdf_app` 13/13,
`test_rt_pages` 28/28, `netns-system-space-test` 21/21, `netns-enroll-test` 12/12 and `test_writer_gone_oracle` 4/4.

These netns tests pass: admit-kt, graph, hub-addr, invisible, join-kt, org, region-loop, regions, space, spaces,
targets, member-death, latency and scale.

These netns tests fail here exactly as they fail on `integrate-20261031`, run with the same binaries (the fail lines
compared): hub (2), egress (1, E2), c-behind-ab (2), lan-only (4), leaf-tunnel (6), ponds-daemon (4, at the 570 s cap)
and reach (27, at the cap). placement fails 5 here and 15 on the base.

Bundles: `fdf-node-aarch64-fdf-2026-11-07-r50.tgz` (Pi nodes), `fdf-node-x86_64-fdf-2026-11-07-r50.tgz` and
`fdf-droplet-x86_64-fdf-2026-11-07-r50.tgz`. `opt/fdf/dist` holds the same build under the fixed names.
