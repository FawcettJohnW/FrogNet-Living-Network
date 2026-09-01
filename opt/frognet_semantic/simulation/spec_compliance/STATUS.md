<!-- ============================================================== -->
<!-- AT FRONT PER USER REQUEST: engineering posture (read source,    -->
<!-- root-cause, instrument, prove). Canonical: docs/ENGINEERING_     -->
<!-- POSTURE.md. (Socket-sets transport API doc lives in docs/ too.) -->
<!-- ============================================================== -->

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


<!-- ================== END POSTURE FRONT-MATTER ================= -->

# spec_compliance/STATUS.md — codex language compliance (PRIMER 2)

State as of `frognet_FULLTREE_v25_7_*`. Read this FIRST when resuming. Every
in-sandbox behavior below was observed by running LIVE handlers; the box-only
second-pass harness was authored but NOT run in-sandbox (egress closed).

## Headline

XML + HTML handlers are full-fidelity (lxml). Compliance gate GREEN:
`json 51/0 · xml 44/0 · html 32/0 · text 45/0` → **172 pass, 0 fail**, rc=0.
Existing `xml_codex_tier`/`html_codex_tier` pass, incl. compression (>90% HTML
diff savings; 1 REQ_FULL + 14 REQ_DIFF; zero wire decode errors).

## Box-measured results (full public suites, run on FrogNetHost)

Real numbers from box_fullsuite on the W3C/JSONTestSuite/html5lib corpora
(box was running v25_6 handlers — no encoding fix — at measurement time):

- JSON (JSONTestSuite): accept y_ 85/95 (89.5%), reject n_ 185/188 (98.4%).
  All 13 disagreements map to existing findings: 3 nonfinite normalize-to-null
  (RFC says reject), 10 top-level-scalar / non-identifier-key atomic-wrap.
- XML (W3C xmlts): well-formed (valid) round-trip 728/744 (97.8%). Residual
  misses are encoding-declaration cases (FIXED in v25_7, not on box at run
  time) + a few DTD/conditional-section edges. not-wf rejection: the first run
  reported 62.9% but that was a SCORING ARTIFACT (path-guessing instead of the
  W3C manifest). box_fullsuite v2 rescoring is manifest-driven and splits
  accepted not-wf into DTD-dependent (intentionally not processed: DTD/entity
  OFF for XXE/billion-laughs safety) vs genuine misses. The genuine-miss count
  is the real not-wf number — re-run pending.
- HTML (html5lib-tests): round-trip faithful+idempotent 1629/1790 (91.0%).
  WHATWG conformance vs html5lib reference 1102/1790 (61.6%) — a FLOOR, not a
  clean score: depressed by (a) document-vs-fragment (handler is fragment-
  oriented; misses cluster on doctype variants, frameset, implied html/head/
  body, null-byte) and (b) the reference treebuilder itself being lossy
  (DataLossWarning on the inputs).
- text: lossless at handler by construction (no public spec).

Honest one-line "how close to full language support": well-formed/real-world
fidelity is HIGH (XML ~98%, HTML ~91%, JSON ~90% accept); strict full-spec
conformance over adversarial malformed + whole-document edge cases is lower and
uneven, and partly bounded BY DESIGN (DTD processing off for security). No
single blended percentage is honest; report the per-axis numbers above.

## Network reality (important)

The box network is ON, but the SANDBOX egress is still closed: pypi.org,
files.pythonhosted.org, github.com, api.github.com, codeload.github.com all
return `x-deny-reason: host_not_allowed`. So html5lib could not be installed
and the public corpora could not be fetched IN-SANDBOX. The second-pass
measurement must run ON THE BOX. lxml IS importable on the box (4.9.2 under
system python3; no venv — venv was removed deliberately).

## What changed (cumulative this task)

- `core/xml_handler.py` (lxml, full fidelity) BUILD=2026-06-08-lxml-fullfidelity.
  Hardened parser (resolve_entities=False/no_network/huge_tree=False/load_dtd=
  False/strip_cdata=False). NOW ALSO: `_normalize_decl_encoding()` rewrites a
  non-UTF-8 XML `encoding=` declaration to UTF-8 before parsing (we always feed
  lxml UTF-8 bytes), fixing the café→cafÃ© mojibake on ISO-8859-1/UTF-16/etc.
- `core/html_handler.py` (lxml.html, full fidelity, SYMMETRIC — requests now
  structured, not opaque) BUILD=2026-06-08-lxml-fullfidelity.
- `simulation/run_all.py` — TIER C wiring.
- `simulation/spec_compliance/` — _harness, run_compliance, 4 suites, STATUS.
  XML/HTML suites assert full-fidelity (C14N for XML; structural+idempotence
  for HTML) + arbitrary/deep/wide fuzz + (XML) encoding cases.
- `simulation/spec_compliance/box_fullsuite.py` + `fetch_corpora.sh` — BOX-ONLY
  second pass (see below). NOT wired into the gate. Authored, not run here.

## How close to full language support (measured + assessed)

XML — effectively complete for well-formed XML 1.0 AND 1.1. Round-trips
C14N-lossless: attributes (order), namespaces incl. default-ns redefinition,
mixed content, repeated siblings, comments, PIs (prolog+epilog), CDATA,
DOCTYPE internal subset, all predefined/numeric/astral char refs, xml:space,
xml:lang, non-UTF-8 encoding declarations (after the fix). Equivalent
normalizations (not data loss): CDATA→escaped text only when that slot is
rewritten; decl quote style; prolog whitespace. Call it ~complete; remaining
unknowns are whatever the W3C xmlts surfaces (run box_fullsuite).

HTML — complete in PRACTICE, not certified. Every WHATWG frontier case held
faithful+idempotent: inline SVG/MathML foreign content, <template>, raw-text
elements (<textarea>/<title>), custom elements, adoption-agency misnesting,
boolean attrs, <meta charset> documents. CAVEAT: engine is libxml2, not the
WHATWG reference (html5lib). True conformance % requires running box_fullsuite
with html5lib installed on the box.

Codex-contract note: handlers give full FIDELITY (lossless round-trip). The
compression surface is element text + tails + attribute values. STRUCTURAL
variation between frames (tag names, child counts, comment/PI/doctype content)
is correct but relearns the template (REQ_FULL) → no compression on that frame.
Inherent to the semantic codex, not a handler defect.

## Box second pass — get the real numbers

  bash simulation/spec_compliance/fetch_corpora.sh         # JSON + html5lib + xmlts
  pip install --break-system-packages html5lib             # optional: WHATWG cert
  python3 -m simulation.spec_compliance.box_fullsuite

Scoring: JSON y_/n_ accept/reject; XML valid/ round-trip + not-wf/ reject;
HTML round-trip+idempotence, plus tree-equality vs html5lib reference if
present. Informational only — does not gate. Feed any disagreements back here.

## CRITICAL deploy check

New handlers import lxml at load. Confirm on box (done: 4.9.2 present). If a
no-lxml host ever appears, the old stdlib(ET)/bs4(html.parser) handlers are the
fallback. Validate on the box interpreter after deploy:
  cd /opt/frognet_semantic && python3 -m simulation.spec_compliance.run_compliance; echo $?

## Behaviour change to flag downstream

HTML requests are now STRUCTURED, not opaque [("raw", body)]. Verify nothing
upstream assumed opaque HTML requests.

## Remaining findings (all OUTSIDE the XML/HTML scope)

JSON lone-surrogate accepted (RFC: reject; not a crash); JSON top-level
non-identifier keys wrap under "value"; codec TYPE_INT 32-bit overflow; codec
FINDING-1 (TYPE_RAW decoded utf-8/"replace" → binary mangled). Separate changes.

## Shebang note (venv removed)

Handler files still carry `#!/opt/frognet_semantic/venv/bin/python3` (cosmetic
for imported modules). The venv is gone on the box; anything EXEC'd by path
hits bad-interpreter. Not changed here (out of task scope); flip tree-wide to
`#!/usr/bin/env python3` as its own change if desired.
