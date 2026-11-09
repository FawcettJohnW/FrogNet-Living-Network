# fdf-2026-11-09-r53: roles on api.php, held reads in the core, on OpenEOC r57

Branch `pr-roles-on-api-20261109` merges `roles-on-api-20261107` (built as r50 and r51; r50 withdrawn) onto
`integrate-20261031` as of OpenEOC r57 (`cbf29daa`), and adds `fdf-app rename`. AI_READ_FIRST.md section 10a gives the
design.

## What changed

- **Roles are hosts** (`[ROLES_ON_API_V1]`). `databasehost.frognet` (the SystemRam) and `databasehost_control.frognet`
  (the ControlRam) are the default `api.php` on port 80 of the host the elections name. Gone:
  - the LAN RAM manager on 8841;
  - `ram_port`, `--ram-port` and `FDF_LAN_RAM_PORT`;
  - `ensure_lan_ram` and `lan-rams.json`.

  Services keep their ports: the region's RAM manager on 8840, and every AppRam.
- **Held reads are in api.php** (`[HELD_READ_IN_API_V1]`).
  - `entity=held&action=hold` answers with the current rows.
  - A write completes first. Then its holds are fired through each holder's port 80 (`held_fire.php`, then
    `/run/frognet/held/<hid>.sock`); a fire carries key, version and value.
  - Holds are one-shot, and the holder holds again.
  - The 50 ms wait loop is gone. There is no polling anywhere.
  - `/etc/hosts` rewritten ends every hold; the holder then holds again at the host it now names.
  - The FrogNetHost holds one read: its `(frognethost, config, <guid>)`.
  - A machine with no FrogNet 10/8 address cannot hold, and says so.
- **A machine leaving removes what it wrote** (`[WRITER_GONE_V1]`).
  - On the LAN, the proxy/daemon's last session to a peer ending is that machine gone. The daemon posts
    `remove_writer {subnet}` to its local api.php.
  - On the WAN, fdf-hub closes the member's tunnel (`[HUB_HAS_NO_MEMORY_V1]`).
- **Capability is announced** (`[CAPABILITY_ANNOUNCED_V1]`): fdf-app writes it plainly, holds a read on `can` to write
  it again, and withdraws it at stop. `ram hold` is removed from the `ram` tool.
- **A split is LAN-only** (`[SPLIT_IS_LAN_ONLY_V1]`, the answer to ORGANIZATIONS Q1).
  - When the region is lost, the daemon withdraws its routes, names and tunnels and runs `runMerge`.
  - It keeps standing sessions to the list, and the first entry that answers rejoins the region.
- **Each node bundle carries FrogNet's whole runtime from one tree** (`[FROGNET_RUNTIME_WHOLE_V1]`): core, daemon,
  proxy, discovery, internet_tunnels_v3, frognet_route, the web root, usr/local/bin and lib, the units, tmpfiles and
  sysctl. It never carries the node's config.php or DB_CONFIG.json.
  - r50 had shipped two daemon files without the `semcache_wire.py` they import. On Seattle3B, frognet-daemon then
    died on import and port 9009 refused every connection.
- **`[DIAG-REF]` logging** in the proxy and the daemon logs every move of a response reference on both ends. It is
  there for Seattle7's `DIFF decode failed: a splice without its reference string (row.UpdatedAt)`, 31 since boot,
  against 0 on Seattle5.
- **`fdf-app rename OLD NEW --admin USER:PASSWORD`** (`[APP_RENAMED_V1]`) carries an app installed as OLD over to NEW:
  - from this machine's copy: the RAMs and their ports, the roles, the categories, the users' verifiers, the teams-join
    verifier and what the machine serves;
  - the admin key derived again for NEW from the admin's password. A password that does not open OLD's key renames
    nothing.

  The first machine renamed writes NEW's one configuration tuple; the others adopt it. OLD's copy is moved to
  `/opt/fdf/var/retired-OLD.json` before fdf-app restarts.
  - Fixed along the way: a pre-r47 copy installed as the one tuple now keeps its ports and its teams-join verifier.
    Both had been dropped.
- `etc/tmpfiles.d/frognet-held.conf` has its GPL notice. Without it, r51 failed the license oracle.

Builds reported in logs: `fdf-2026-11-09-r53`, `fdf-app-2026-11-09-r53`, `api.php 20261107-held2`,
`frognet_held 20261107-held1`, `frognet-host-2026-11-07-roles1`, `dc-door-2026-11-07-roles1`, and the OpenEOC builds
from integrate (`openeoc-app` r57, `openeoc-admin` r16).

## On every node

The commands below are for aarch64 nodes (Pis); x86 nodes use `fdf-node-x86_64-fdf-2026-11-09-r53.tgz`.

```
cd / && tar xvzf - < /tmp/fdf-node-aarch64-fdf-2026-11-09-r53.tgz && systemd-tmpfiles --create /etc/tmpfiles.d/frognet-held.conf && mysql -u root FrogNet < /var/www/html/schema_fixups.sql && rm -f /etc/frognet/ram_port && systemctl reload apache2 && systemctl daemon-reload && systemctl restart frognet-daemon frognet-proxy frognet-tunnel-daemon-v3 fdf-app frognet-host
```

Then run this on every machine that has the app installed, the admin's machine first:

```
/opt/fdf/bin/fdf-app rename rangertrak openeoc --admin <ADMIN>:<PASSWORD>
```

## You must re-enter

The child-team entities `(fdf-app, child, <app>.<team>)` lived in the old ControlRam (8841) and under the old app name.
Teams that joined the EOC by its QR join again. `fdf-app rename` carries the teams-join verifier and the users over; a
machine that is set up again with `fdf-app config` has its teams-join verifier entered again (`--join`).

## Gates (in the container, on this merge)

**Pass:**
- `run_all.py` gate, discovery 84/84
- `test_writer_gone_oracle`
- `test_frognet_host`, `test_fdf_app`, `test_app_rename` (4/4)
- `test_rt_pages` 28/28
- `netns-enroll-test`, `netns-system-space-test`
- OpenEOC `test_app`, `test_team_host`

**Fails here as on `integrate-20261031`, run with the same binaries and the fail lines compared:**
- netns hub (2)
- netns egress (1)
- netns c-behind-ab (2)
- netns lan-only (4)
- netns leaf-tunnel (6)
- netns ponds-daemon (4)
- netns reach (27)

**netns placement:** 5 failures here against 15 on the base. These figures come from the r51 runs and were not run
again on this merge.

**OpenEOC `test/test_admin.py`:** integrate's r57 notes already record that its harness fails at `fdf-app config`.
That harness is now on the roles: an api.php role host and held_fire on the node's .1. a writes the one configuration,
b installs it with `--admin`, and c has a pre-r47 copy.
- Checks 1, 2 and 6 pass.
- Checks 3–5 and 7–11 still count one configuration tuple per member (`rm.read("fdf-app", "config")` with two
  entries). That is the model from before r47. Under r47 the members adopt the one tuple and answer in
  `(fdf-app, applied, openeoc.<guid>)`; the members' logs show them adopting each change. These checks should read the
  answers instead.
