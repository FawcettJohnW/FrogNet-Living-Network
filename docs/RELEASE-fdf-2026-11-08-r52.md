# fdf-2026-11-08-r52: RangerTrak is OpenEOC

This is branch `openeoc-rename-20261108`, made from `roles-on-api-20261107` (r51 and its `[DIAG-REF]` logging).

## What changed

RangerTrak is renamed to OpenEOC everywhere (John, 2026-11-08: "Everything is changing to OpenEOC"):

| Was | Is |
|---|---|
| `opt/fdf/src/rangertrak-app` | `opt/fdf/src/openeoc-app` (Android Studio opens the renamed tree once) |
| packages and applicationIds `net.frognet.rangertrak.*` | `net.frognet.openeoc.*` (new apps on the phones; uninstall the old ones; their stored team and login do not carry over) |
| the app id `rangertrak`, its tuples `(fdf-app, *, rangertrak…)`, `rangertrakHost.frognet` | `openeoc`, `(fdf-app, *, openeoc…)`, `openeocHost.frognet` |
| the services `rt-*` (rt-team, rt-pos, rt-log, …) | `openeoc-*` |
| the pages `/rt/` (`/opt/fdf/www/rt`, rt.css, rt.js) | `/openeoc/` (`/opt/fdf/www/openeoc`, openeoc.css, openeoc.js) |
| `rt-sim`, `rt-post309`, `FDF_VAR/rt/` | `openeoc-sim`, `openeoc-post309`, `FDF_VAR/openeoc/` |
| the team RAM's vendor id `rangertrak-team` | `openeoc-team` (libteamram.so rebuilt for every ABI) |
| `ribbit/examples/rangertrak` | `ribbit/examples/openeoc` |

John Cornelison's RangerTrak (github.com/EOCOnline/rangertrak), the project this one began from, keeps its name where
the docs mention it. The `[RT_…]` tags are kept as they are, because they are the history's names.

Builds: `openeoc-app-2026-11-08-r55`, `openeoc-admin-2026-11-08-r15`, `openeoc-sim-2026-11-08-r10`,
`fdf-app-2026-11-08-r52`, base `fdf-2026-11-08-r52`.

## `fdf-app rename OLD NEW --admin USER:PASSWORD` (`[APP_RENAMED_V1]`)

This command carries an installed app over to its new name. It copies this machine's copy of OLD to NEW: the RAMs and
their ports, the roles, the categories, the users' verifiers, the teams-join verifier and what the machine serves. It
derives the admin key again for NEW from the admin's password. A password that does not open OLD's admin key renames
nothing.

The first machine renamed writes NEW's one configuration tuple. Every other machine adopts it. OLD's copy is moved to
`/opt/fdf/var/retired-OLD.json` before fdf-app restarts, so the service never serves both names.

## On every node, after the tarball

```
cd / && tar xvzf - < /tmp/fdf-node-aarch64-fdf-2026-11-08-r52.tgz && rm -rf /opt/fdf/www/rt /opt/fdf/bin/rt-sim /opt/fdf/bin/rt-post309 && { [ ! -d /opt/fdf/var/rt ] || mv /opt/fdf/var/rt /opt/fdf/var/openeoc; } && systemd-tmpfiles --create /etc/tmpfiles.d/frognet-held.conf && systemctl reload apache2 && systemctl daemon-reload && systemctl restart frognet-daemon frognet-proxy frognet-tunnel-daemon-v3 fdf-app frognet-host
```

Then, on every machine that has the app installed, the admin machine first:

```
/opt/fdf/bin/fdf-app rename rangertrak openeoc --admin <ADMIN>:<PASSWORD>
```

## You must re-enter

Teams that joined the EOC by its QR, the child-team entities `(fdf-app, child, rangertrak.<team>)`, join again. Their
entities are named by the app, and the rename does not move them.

The teams-join verifier and the users carry over with `fdf-app rename`.

## Gates (in the container)

- `run_all.py` passes, with 84/84 discovery oracles. The license oracle had failed on
  `etc/tmpfiles.d/frognet-held.conf`, which r51 shipped with no notice; the notice is added.
- `test_app` passes 31/31.
- `test_openeoc_pages` passes 28/28.
- These also pass: `test_team_host`, `test_frogqr`, `test_enroll_page`, `test_fdf_app`, `test_frognet_host`,
  `test_app_rename` (new, 4/4), `netns-enroll-test`, `netns-system-space-test` and the Android typecheck.
- `test/test_admin.py` was already failing on `integrate-20261031`. Its setup is now converted to the roles and to the
  one-configuration install. Its checks 2–8 still look for the admin page from before categories (`#roles`), so they
  fail.
