# HANDOFF: FD&F, 2026-10-08 — current state, and the next job (CLI tools + browser terminal)

Read this first. It replaces every earlier HANDOFF.md.

## Downloads you need (attach these to the new chat)

| File | What it is |
|---|---|
| `fdf-node.tgz` (sha256 82b5b663…) | The current node package: all binaries (static `rina-host`), scripts, kart demo, Windows files, and the full source as `opt/fdf/src/fdf-rinab-src.tgz`. Install: `cd / && tar xvzf - < /tmp/fdf-node.tgz` |
| `fdf-windows.zip` (sha256 fe24f4e8…) | `fdf-client.exe`, `kartdemo.py`, `ribbit.py`, README |
| `frognet-communicator-bundle (2).tgz` | John's Communicator (2026-10-06 version); `comms-app`, `comms-ram`, `comms-media` build from it |
| This file | |

Not needed: the older `fdf-modeb-*.tgz` and `fdf-restart-*.tgz` (superseded by `fdf-node.tgz`).

The reference document is the Claude Doc "FD&F: Everything Known" (history, architecture, implementation, every test,
the demo, lifecycle, security, open items).

## The architecture (John's rulings, binding)

- FD&F is a TRANSPORT of individual machines. Each machine connects OUTWARD to one RAM manager; all data goes through
  it, never machine to machine. No routing between machines.
- Layers: transport (FrogNet / FD&F / any) → FNWP (the wire) + BLDC-1 → Ribbit (RAM manager, API, schema) →
  applications, which talk only through shared memory.
- Participants come only from capability tuples in the shared space. Liveness is the session; no heartbeats.
- No fallbacks, no timed retries, no polling ("held, never polled"), no direct propagation between machines.
- The bootstrap (where the RAM manager is) is the only thing a machine needs; it should be encrypted (deferred: security
  is out of scope for the demo).
- Each RAM server is its own independent network (its own shared memory, its own members). Four ran at once on
  2026-10-07: FD&F (8840), race (8799), Communicator memory (8800), media (8994).
- "Generation" in John's vocabulary = a level of DIF nesting.
- Page input to a web service goes in as tuples `<App>.<QueueName>.<submitter>`.

## Deployment (live)

- Droplet StreamingFrog, 165.232.153.144, Ubuntu 22.04. `/usr/local/bin/fdf_service start|stop|status` (John's copy of
  `fdf-services.sh`) runs FD&F 8840, race 8799, Communicator 8800/8994, and publishes `fdf.where.race` and
  `fdf.where.comms` into the FD&F region. Communicator built at `/opt/communicator/build`.
- Players: `kartdemo.py --name X --fdf 165.232.153.144:8840 --comms-app <path> [--comms-args "..."]`. With `--fdf` the
  game reads locations from FD&F, shows only FD&F members, and starts (and restarts) the FD&F client itself.
- Machines: AI-Host (Linux x86, 10.250.250.100), DESKTOP-1UJHGLF (Windows laptop, behind NAT), FrogNetHost (Pi,
  arm64 — no FD&F member build for ARM yet).

## Tools that exist

- `fdf-client <host>:8840` (Linux; `fdf-client.exe` on Windows): connect, node record, announce, hold. Exits when the
  session ends.
- `fdf-participants` (reads `RINA_RAM` from `/opt/fdf/fdf.env`): prints the participants as JSON.
  `fdf-participants --wait GEN` holds until the set changes after generation GEN, prints `{"gen":N,"participants":[…]}`.
- Over the plain-HTTP front, from anywhere:
  `curl -s -X POST -d '{"op":"participants"}' "http://<host>:8840/FawcettInnovations.RINA.ram_interface.php?op=rina"`

## THE NEXT JOB (John, 2026-10-08)

"Create a FD&F connection to a network, then use the browser-based ssh to log into another machine. I need CLI tools for
things like listing the participants and setting up the browser."

Plan, built on what exists:

1. **`fdf-join <host>:8840`** — writes `RINA_RAM` into `/opt/fdf/fdf.env`, installs and starts a systemd service running
   `fdf-client` (restarted when its session ends). `fdf-leave` undoes it. Nothing in `/usr/local/bin`.
2. **`fdf-ls [--watch]`** — participants as a table (name, alive, IPs); `--watch` loops on `fdf-participants --wait`.
3. **`fdf-term-serve`** — on each machine that accepts logins (run as root, systemd): per browser session, a PTY running
   `/bin/login`; keystrokes read from tuples, output written to tuples. All through the RAM manager.
4. **`fdf-browser`** — serves the terminal page locally and opens it pointed at the area; the page lists targets from
   the participants and runs xterm.js (vendored, not from a CDN). The local server does the page's RAM get/send (the
   "PHP does RAM get/send" model), because the RAM manager's HTTP front sends no CORS headers.

### Verified in the sandbox before the handoff (static rina-host from fdf-node.tgz)

- **Held reads work over the HTTP front:**
  `GET …?op=read&service=S&variable=V&after=<id>&wait_s=<seconds>` holds and returns the moment a row with a higher id
  is written (measured: returned 1.2 s later, right on the write). Rows come back as `cells` with `id`, `instance`,
  `bag`. So input and output can both be held reads — no polling.
- **Writes:** `POST ?op=write` with `{"service","variable","instance","bag"}` → `{"ok":true,"id":N}`; ids are write order.
- **Remove:** `?op=remove` is refused on POST ("remove is DELETE"); `curl -X DELETE "…?op=remove&id=1"` returned an
  empty body and the row was still there. OPEN: find the remove call's exact form (read the platform's ram_server.cpp
  in the FrogNet repo, `opt/frognet_semantic/ribbit`), or reuse a bounded ring of instance names instead of deleting.

### Proposed tuple layout (follow `<App>.<QueueName>.<submitter>`)

- Keystrokes to target T from browser session S: `term.<T>.<S>|<seq>` → `{"d": base64}`; open
  `{"open":1,"cols","rows"}`, resize `{"r":[cols,rows]}`, close `{"x":1}`.
- Output to session S: `termout.<S>.<seq>` → `{"d": base64}`; the server holds a read on `term/<T>` after its last id,
  the page holds a read on `termout/<S>` after its last id.
- Each server advertises itself: `term.hosts.<T>` → `{"since": …}`; the page offers targets that are alive in FD&F AND
  advertise a terminal.

## Lost work (rebuild if needed)

The 2026-10-08 working tree was lost when the workspace reset. Not in any package:

- **`[NO_TOPOLOGY_BROADCAST_V1]`**: make `notify_agents()` in `src/rina_region.cpp` do nothing (it pushed `{"k":"t"}` to
  every agent on every join, so join cost grew with N). After it: rlite 30/30, rule/move/discovery checks PASS. The
  `rina-host` in fdf-node.tgz predates it.
- **Experiment harnesses** `tools/exp1-members.sh` (+ `tools/pcap_join_work.py`), `experiments/restart/exp2-app-every-member.sh`,
  `tools/exp3-topologies.py`, `tools/exp4-readdress.sh`. Results are recorded in the Claude Doc.
- Results: exp1 PASS (29 packets, ~1,085 bytes, 42–66 ms at N = 2…1,000); exp2 PASS in one area, FAIL across areas;
  exp3 FAIL 6/20; exp4 FAIL (stale locator after an address change).

## Building from the package

- Source: `tar xzf opt/fdf/src/fdf-rinab-src.tgz` → `rinab/`. `tools/build.sh` needs the Ribbit v0.63 platform from
  FrogNet's public repo (github.com/FawcettJohnW/FrogNet-Living-Network, commit in `experiments/FROGNET-COMMIT`);
  `tools/build_rlite.sh` needs rlite 6ac276b. `tools/build_static_host.sh` makes the static `rina-host` (needs
  libjemalloc-dev, liblz4-dev, libssl-dev). `tools/build_windows_client.sh` cross-builds `fdf-client.exe` (mingw-w64).

## Working rules (John)

- Small steps, artifacts after each; keep going until a real blocker; research before asking.
- Commands for John: no output redirected to log files; untar on nodes with `cd / && tar xvzf - < /tmp/<file>`; nothing
  in `/usr/local/bin`.
- Answer the question asked; lead with the better alternative, not paragraphs of objections; don't manufacture critique.
- `pkill -f` / `pgrep -f` match your own shell: kill by exact name (`pgrep -x`) or pid.
