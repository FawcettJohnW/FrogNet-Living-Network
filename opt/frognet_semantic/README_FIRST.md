# ENGINEERING POSTURE — read first

Operating rules for anyone (human or model) working this codebase. These are
non-negotiable working habits, not style preferences.

## Source of truth
- **Read the actual source first, then describe.** Never describe what code does
  from memory, inference, or the shape of a log line. Open the file. No exceptions.
- **Don't invent** paths, APIs, fields, configs, or log formats. If you don't have
  it, say so and ask — don't fabricate.
- **Don't assume you have the codebase.** Each session starts empty; go read it.
- When live code disagrees with the primer/spec, default to "code is wrong, primer
  is right" — implement the primer rule rather than rationalizing the deviation,
  unless explicitly told the primer is stale.

## Root cause means find the cause, wherever it lives — not dig deeper where the light is
1. **Enumerate layers and hosts before theorizing.** A distributed system is: app,
   threads, systemd, tunnel daemon, kernel (netfilter/conntrack/routing/ifaces),
   overlay (WireGuard), LAN, broker, every peer, and any scheduled jobs/hooks on all
   of the above.
2. **State what you have and what's missing.** One log from one host during one
   incident is almost never enough.
3. **Don't narrow until cross-layer / cross-host evidence rules out the rest.** The
   visible process is the victim until proven otherwise — ask "what happened *to* it"
   before "what's wrong *with* it."
4. **Symptom ≠ cause.** Name which one you're looking at.
5. Don't rank possibilities when the honest answer is "insufficient evidence." Say
   "I don't know" / "I need X" on turn one, not after speculation.

## When the log is insufficient: instrument for one-shot root cause
- Propose a specific file / function / line, matching existing tag conventions
  (`[DIAG-WRITER]`, etc.).
- **Log everything potentially relevant, not just the suspected cause** — all
  in-scope variables, all conditions checked, all state inspected, precise
  timestamps, thread/task IDs, inputs and outputs. Err toward too much: a second
  instrumented run costs hours and may not reproduce; a noisy log costs nothing.
- If you're selecting variables based on your current hypothesis, log all of them
  instead — the hypothesis is what might be wrong.

## Use external tools — match the tool to the layer the cause could live in
py-spy (dump/record/speedscope), strace (`-e network,poll,futex`), tcpdump (ring
buffer for post-hoc), ss, `/proc/$pid/task/*/stack`, conntrack,
`/proc/net/softnet_stat`, full unfiltered journalctl (not one unit), dmesg,
`systemctl list-timers`, cross-host correlation. Check constraint fit before
recommending (e.g. don't propose a realtime-only tool when stalls aren't noticed
live).

## Prove, don't assert
Use the simulator / a real run to prove claims. A green curated suite is not a
full-spec result; report measured numbers and name the caveats. Don't paper over a
real finding to make a gate go green.


---

## Other docs
- `docs/socket_sets_transport_API.md` — socket-sets / transport-modes consumer API (separate subsystem).
- `simulation/spec_compliance/STATUS.md` — codex language-compliance state + box-measured numbers.

## Codex compliance — quick run
- Gate: `python3 -m simulation.spec_compliance.run_compliance`
- Full public-suite second pass: `codex_fullsuite/box_fullsuite.py` (+ `fetch_corpora.sh`)
