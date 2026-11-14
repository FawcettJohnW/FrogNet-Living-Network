# Integration, 2026-10-31: branch `integrate-20261031`

Every branch on origin with work main lacked, merged onto main `bf954af`, so the OpenEOC conversion and the
join library start from one tree that carries everything.

## What was merged

| branch | commits main lacked | what it carries | conflicts |
|---|---|---|---|
| `eoc-and-rangertrak-plans-20261031` | 1 (`79607e2`) | `docs/eoc/EOC-APP-AND-JOIN.md`, `ribbit/examples/openeoc/PLAN.md` | none |
| `croakus-claims-match-code-20261031` | 2 (`cbddd13`, `e868d3d`), pushed after PR #11 merged | OPERATING.md to r31 (invitations, RAM manager in the tunnel); F6's 28 checks | none |
| `persist-20261031` | 8 | IS-IS loop persistence on real FRR (Day 2026-10-30), its model, report, and the two `run_all` tier 4 gates | none |
| `senzmatica-ram-20261031` | 1 (`842ef8c`), off main at PR #9 | `examples/senzmatica` (the converted remote-configuration workflow, JNI binding with HeldRead, device, RAM host, probe, `senz` and `senz-hwtest` r8, docs and evidence), API++ tuple and capability changes, `_recovery/` (the reconstructed checkpoint's metadata) | none |
| `bundles-20261027` (r36) | 74 | `kart-autodrive-2026-10-17` (Android joins by invitation, `[ADMITTED_BY_INVITE_V1]`), `fdf-carry` (DisasterComm apps), the core, add-ins, `fdf-identity`, Windows bridge a19, the netns tests on invitations, the r36 acceptance record | 2, below |

Not merged:

- `ribbit-mqtt-v32-20261019` (1 commit, `bea85d0`, `full_tar.bash` with no manifest): already in. The merged
  `full_tar.bash` is that commit's file plus `[FULL_TAR_SAYS_ITS_RELEASE_V1]` (diff: 9 lines added, none removed).
- `meshtastic-20261030`: in progress in its own thread (last push 11:21 today). It already contains all of r36.
- Every other branch on origin is fully contained in main.

After the merge, `git rev-list --count HEAD..origin/<branch>` is 0 for each merged branch and for `kart-autodrive-2026-10-17`
and `fdf-carry`.

## Conflicts and how each was resolved

- `usr/local/lib/frognet_world_manifest.sh`, `FROGNET_THIRD_PARTY`: main added Leaflet (`[GPS_LEAFLET_LOCAL_V1]`), r36
  added Mosquitto (`mqtt/stock`, `mqtt/front`). Both kept. `bash -n` OK.
- `docs/OPERATING.md` §3, five hunks: main's side (written this morning against r31) and r36's side (written for r36)
  both rewrote the region section. The merged code is r36, so r36's text is the base, and the main-side statements
  the r36 code still bears out were kept, each checked in the merged tree:
  - the `modprobe -n` line installs wireguard-go where the kernel has no module;
  - allow ssh before `ufw default deny`;
  - `[NOTHING_LISTENS_OUTSIDE_V1]` (`fdf-hub`: 3 sites);
  - `--replace` (`fdf-region-create`: 6 sites);
  - the invitation is a secret; inviting a name again revokes its old key (`fdf-invite` line 141); a member never
    invited gets no peer (`[ADMITTED_BY_INVITE_V1]`).
  - r36's text is used for what r31's lacked: `fdf-identity`, the core, add-ins, `fdf-chat@51888`, and `fdf-leave`
    removing `hub.json` (`fdf-leave` line 27).

## Fixed during integration

- The SenzMatica example's 22 files of ours had no GPL-2.0-only notice (the licence oracle, 1b and tier D, failed 22).
  Each was checked to be ours: SenzMatica upstream (Apache-2.0) is pinned and patched, never vendored, and
  `native/device.cpp` is a synthetic device. Notice only: 374 lines added, 0 removed.
- `static-no-broker` allows `examples/senzmatica/*` for the reason it allows `examples/mqtt/*`: the OFF path runs an
  MQTT broker (Mosquitto) and the example converts it. 63 violations to 0.
- `frognet_apply_license.py --apply` wrote 448 headers where the check wanted 22: it walked the whole root while
  `license_check` walks `FROGNET_WORLD_PATHS`, so it stamped third-party code under `opt/fdf` (xterm.js, Go's x-sys, the
  OpenSSL headers in `rinab/src/win`). Those 426 writes were reverted, and the applier now walks the world paths only
  (`[APPLIER_WALKS_THE_WORLD_V1]`); `--check` after: 0 needing one.
- `docs/eoc/EOC-APP-AND-JOIN.md` and the OpenEOC `PLAN.md` Step 0 were written against main and said the member
  had never seen an invitation. On this tree it has (r36). Both now say what remains (door, registrar, `fdf-group`) and
  carry John's ordered list of Internet RAM managers.

## Gates on the merged tree (this sandbox, x86-64, not a node)

Built here for the gates: rinab (`tools/build.sh`, `build_static_host.sh`, `build_static_clients.sh`; liblz4-dev and
libjemalloc-dev installed), iproute2, Python lz4 4.4.5, and the tree's own `mapInterfaces` copied to
`/usr/local/bin` (where the installer puts it; `proxy/netutil.py` refuses to guess interface roles without it).

| gate | result |
|---|---|
| 3a static-no-broker | 9862 allowed, 0 violations |
| 3b static-no-fallback | 45 allowed, 0 violations |
| 3c check-docs | 0 problems. Before rinab was built here: 2, the same 2 on `origin/main` and on `origin/bundles-20261027` fresh (`README.md` names `rinab/build`, `opt/fdf/README.md` names `build/`, which exist only after a build) |
| 1a discovery selfcheck | PASS 14/14 |
| 1b discovery oracles | PASS 84, 0 known-fail, 0 regression (after the licence fix above) |
| 1e Python monitor oracles | parser vectors PASS, snapshot view PASS, merge ALL PASS |
| 1f dashboard oracle | PASS |
| 1g hub oracle | RESULT PASS |
| 1c simulator | GATE PASS: tiers 1, 2, 2b, R, 3 (S-scale with the built binaries), 4 (with both loop-persistence gates), 5, C, T, D (84 oracles), DAF |
| Ribbit cpp qualify | API++ PASS, capability API PASS, session/fan-in/memory/lockfree PASS. semcodec, fnwp e2e and ramrows FAIL: the same three r36's record names, attributed there to the Python reference's lz4 4.4.5 (installed here). Run on `origin/main` here too: identical counts on every line both runs show (enc_rep 39, enc_rep_diff 537, enc_req_diff 537, lz4smart 30 differ; fnwp e2e 1 fail per seed 1-3; ramrows seed 1). Main's dec_rep (242 differ) was not compared: that line fell outside the integrated run's captured tail |

Not run here: the netns gates (2a–2f, they need root namespaces and the WireGuard module), Gate 4 (Docker systemd
containers), Windows, the APK, hardware. r36 ran them on Oct 28; since then the code has changed only by main's
three days (Leaflet, GPS dashboard, Croakus, docs), the loop-persistence work, SenzMatica, and the applier fix.
