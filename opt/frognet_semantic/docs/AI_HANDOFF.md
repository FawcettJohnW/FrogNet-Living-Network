# AI HANDOFF — pick up FrogNet discovery/loop work cold

You are an AI dropped into this codebase with no memory of prior sessions. This is
the map. Read it, then read source before asserting anything (see
`ENGINEERING_POSTURE.md` — non-negotiable).

## 0. Read order
1. `../FROGNET_PRIMER_load_me_first.md` — timeless architecture.
2. `../FROGNET_NOW.md` — volatile state; PROVEN / IN-FLIGHT / OPEN. Ships in every
   tarball. The top-dated section is the current truth.
3. `ENGINEERING_POSTURE.md` (this dir) — how to work: read source first, root-cause
   across layers/hosts, instrument for one-shot, prove-don't-assert, no fabrication.
4. This file — the discovery/loop specifics and the Magnum Croakus setup.

## 1. The loop-detection doctrine (get this right; it is easy to get wrong)
FrogNet is a self-forming mesh. **Every node knows about and can reach every other
node.** Multiple routes to a destination and dead-end avenues are NORMAL — they vary
with upstream/downstream position. This invariant is the key to not breaking things:

- **A loop is one bad AVENUE, not an unreachable destination.** If the route to X via
  next-hop N loops, reject that avenue; another avenue still reaches X. Reachability
  takes care of itself. DO NOT gate echo/reachability on "this looped" — that
  conflates a bad avenue with an unreachable node and strands nodes. (This mistake
  was made repeatedly; the whole 2026-07-20 session was spent recovering from it.)
- **Detection = the counter, and nothing more is needed.** Send a probe to the target
  via the SAME next hop real traffic uses; each hop increments a counter `c` before
  forwarding. If WE receive our own origin back with `c` neither 0 (our emission) nor
  1 (immediate) — i.e. `c >= 2` — it is a loop. This is the shipped reflect probe
  (`/reflect?o=<origin>&c=<counter>`, 508 = LOOP). Reaching the dest, or dead-ending
  somewhere else, is NOT a loop.
- **Prevention = a per-run memo.** When the counter says `(dest, via)` loops, remember
  it FOR THE DURATION OF THIS RUN, refuse that `(dest,via)`, and pick another avenue
  (guaranteed to exist). Clear the memo at the start of the next run. This breaks the
  count-to-infinity oscillation (detect → remove → the vouch re-forms it) that a bare
  detector suffers. See `[LOOP_MEMO_V1]` in `discovery.py` (`self.loop_memo`, promote
  refuses memoized `(dest,via)`) and `descend.py` (memoize on the counter's LOOP).
- **The back-vouch is the classic loop:** a downstream child learns a remote THROUGH
  its parent, then re-vouches it back; on an RTT tie the parent may pick the
  downstream avenue. The counter catches it (probe hairpins home); the memo holds it;
  the correct upstream/gateway avenue wins.

## 2. Simulator + how to prove things
The discovery engine runs against a real System simulator (cycle-by-cycle), not a toy
model. Prove-don't-assert with fail-on-old / pass-on-new oracles. Corrections are
class-level patterns, not instance fixes.

- Gate (must be green, 0 regression before shipping):
  `python3 discovery/run_discovery_oracles.py`  →  `DISCOVERY GATE: PASS (N pass, 0 …)`
- Loop audit (converges 6 topologies, reflect-traces every installed /24):
  `python3 -m discovery.sim.loop_scenarios`  →  `TOTAL back-vouch loops … : 0`
  (Filter the DBHOST election spam: `... 2>/dev/null | grep -v DBHOST_FLOOR`.)
- Sim faithfulness matters: the sim wires the reflect COUNTER (`_loop_counter` /
  `SimReflect` in `discovery/sim/system.py`) so the shipped `REFLECT_VOUCH_GATE_V2`
  and the loop memo actually fire in simulation. Historically the sim wired
  `reflect=None`, so it silently modeled every loop as fine — the counter never ran.

## 3. File map (discovery engine)
- `discovery/discovery.py` — orchestration, `promote()` (winner selection, incumbency
  hold, reflect vouch gate, `loop_memo`), `walk`/candidate emission.
- `discovery/descend.py` — flat gather→locate→route crawl; per-avenue probing;
  `IFACE_SKIP … reason=loop` + memoize.
- `discovery/routes.py` — kernel mutation chokepoint (`install_if_changed`,
  `route_matches`, `prune_dest_extras` with the proto-kernel guard, `sweep_probe_routes`).
- `discovery/neighbors.py` — direct-neighbor gossip set (incl. client-`.1` derivation).
- `discovery/real_backends.py` — RealVerify (`measure_or_loop`, ping-pong), RealReflect.
- `discovery/sim/` — the System simulator, `fabric.py` (`route_egress`), `lan_chain.py`
  (borrowed-lease model), `loop_scenarios.py` (standing loop detector).

### Fixes landed 2026-07-20 (each with an oracle; see FROGNET_NOW for tags)
- `[LOOP_MEMO_V1]` counter+memo (discovery.py + descend.py).
- fail-cache key `fkey=(x_ip, via or dev)` — `test_multitunnel_failcache_oracle`.
- `[TUNNEL_PEER_HEALTH_EMIT_V1]` — `test_tunnel_peer_health_emit_oracle`.
- prune proto-kernel guard — `test_prune_kernel_route_oracle`.
- `[NEIGHBOR_CLIENT_DOT1_V1]` — `test_neighbor_client_dot1_oracle`.
- sim fidelity: `route_egress` treats a bare host addr as `/32` (fabric.py) — the
  kernel stores `/32` probe routes bare, so route_egress used to skip them and the
  counter could never find its first hop. This one line is why the sim can test loops.

## 4. Magnum Croakus (the book) — build kit, NOT in this tree
*Magnum Croakus — How to Work Like a Frog* is the operational reference manual. It is
generated by a Node.js/docx script and is a SEPARATE artifact from this runtime tree.

- **Source of truth: `build_magnum.js`.** EDIT THE `.js`, NEVER THE `.docx` — the docx
  is generated output; hand-editing it creates drift.
- Build kit layout (ships as its own directory, convention
  `/opt/frognet_semantic/magnum_croakus/`):
  - `build_magnum.js` — the generator (Node + `docx`). ~37 sections, 6 appendices.
  - `make_magnum_figures.py` — regenerates `magnum-figures/*.png` (matplotlib).
  - `magnum-figures/` — embedded figures (PNG). NOTE: the figures are
    RECONSTRUCTIONS of lost originals — verify each against its section.
  - `Magnum_Croakus.docx` — last built output; rebuild to refresh.
- Build:
  ```
  python3 make_magnum_figures.py     # only if figures missing/changed
  npm install docx                   # once
  node build_magnum.js               # writes Magnum_Croakus.docx
  ```
- Known gotcha: docx-js emits paragraph border children top→bottom→left→right, but
  OOXML requires top→LEFT→bottom→right; a paragraph with both left+bottom borders
  makes invalid XML. The `codeBlock` helper was fixed by ordering/removing borders.
- Relevant sections if the discovery/loop code changes: §12 "Validate It in the
  Simulator", the "Proving It — the Simulator" part, and Appendix E "Advanced
  Simulation — The Disaster Area." These describe the sim and oracle discipline. The
  2026-07-20 fixes IMPLEMENT documented doctrine (they don't change it), so the book
  likely needs no edit — but if any of those sections print specific sim route
  outputs, re-check them against the `route_egress` `/32` fix.

**The builder IS now here: `docs/magnum_croakus/build_magnum.js`** (added
2026-07-20, verified: parses + runs). The kit is still incomplete — it needs
`make_magnum_figures.py` and `magnum-figures/*.png` to actually build (the generator
reads the figures at build time; without them it stops at ENOENT). Upload those to
finish the kit. Do not reconstruct anything from memory. Verified against the
2026-07-20 fixes: no book change needed (see `docs/magnum_croakus/README.md`).

## 5. Open items (design decisions, not bugs)
- `.2` metric-5 alias churn: `sweep_probe_routes` deletes live aliases every merge and
  promote re-lays them (unconditional replace of an unchanged route). `/24` traffic
  routes are NOT churned, so no user-flow disruption. Fix = keep a metric-5 alias
  whose `/24` has a winner; requires rewriting 5 oracles that assert the
  sweep-everything design. Awaiting a design call on whether aliases persist.
