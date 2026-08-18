# Building the Magnum Croakus

    npm install docx
    node build_magnum.js

One authoring source, two outputs — `Magnum Croakus.html` and
`Magnum_Croakus.docx` — via `[HTML_EMITTER_V2]`.

## Everything is generated

**Figures.** `figures.js` checks `magnum-figures/` and shells out to
`tools/make_figures.py` for anything missing. Needs python3 with Pillow.
Twenty-two figures, all drawn from code, none produced by hand.

    node build_magnum.js                  # draw what is missing
    FIGURES_FORCE=1 node build_magnum.js  # redraw everything
    FIGURES_SKIP=1 node build_magnum.js   # trust what is on disk
    python3 tools/make_figures.py --list  # what it can draw

**The doctrine appendix** — `[DOCTRINE_INDEX_V1]`. Appendix L is read off the
source tree at build time. Point `FROGNET_SRC` at a communicator bundle:

    FROGNET_SRC=/etc/frognet_bundles/communicator node build_magnum.js

It scans `*.py` and `*.sh` for `[TAG_V1]` markers and emits every rule with the
files it lives in and a count. Against the 2026-08-12 tree: 221 rules.

**With no source tree it refuses.** The appendix prints "Not generated in this
build" and emits no table, because an index that quietly falls back to a
previous answer is precisely the failure it exists to prevent. Deliberate — do
not "fix" it by caching the last good scan.

## Changes, 2026-08-12

**Text**

- **Ch. 50** gains *The rule itself, in full* — `derive_rate` verbatim, with a
  reading of what is not in it (no clock, no self, no I/O) and the two lines
  carrying the reasoning.
- **Ch. 53** — the stale mechanism sentence is replaced. Not a misdirected
  report: one stream and one rate by design, with the struggling end naming
  itself.
- **Appendix L** is the Doctrine Index; the glossary moves to **Appendix M**.

**Figures — 6 placed before, 22 now**

Eleven were already drawn by `make_figures.py` and never placed in the book.
They are now, each in the chapter it names:

| Figure | Chapter |
|---|---|
| discovery_walk | 14 · The Walk |
| election | 15 · Selecting the Service Hosts |
| tuple_address | 16 · One Name, A Moving Target |
| wire_states | 26 · The Accelerant |
| dead_air | 27 · FNWP-1 — The Wire |
| airgap_broker | 35 · Nothing on the Internet Answers |
| scopes | Choruses — groups that span the pond |
| lamp_path | the 2,400-mile loop, April 17 2026 |
| monitor_read | 41 · Measuring It on Your Own Box |
| three_arms | Part XII½ opening |
| address_scaling | Appendix K · why a bigger number is not the fix |

Five are new, drawn for chapters that were long and had nothing:

| Figure | Chapter | What it shows |
|---|---|---|
| broker_rendezvous | 20 · Standing Up a Broker | the broker learns endpoints and gets out of the way; every byte crosses node-to-node |
| handler_object | 28 · The Object-Oriented Handler | one interface, a few virtual slots, Core as the default and Unleashed as more slots filled |
| shotgun | 31 · Song of the Frogs — The Same Problem | one encoder, three unlike receivers, and why shedding makes a slideshow rather than a smaller picture |
| derivation | 40f · The Surface Itself | three nodes write what they alone know, all three derive the same rate, not one message |
| hello_world | 45 · The Communicator | the layer stack, and the four platform bugs the video exposed |

`derivation.png` was drawn wrong first and corrected: with a struggling report
present, `derive_rate` steps DOWN, so the answer is 640x360 and not 854x480. A
figure that contradicts the function it illustrates is worse than no figure.

Three substantial chapters still have none, all correctly: the guild appeal is
prose, "Probes" is a reference table, and ch. 32 follows ch. 31's diagram
directly.

See `MARKERS_CROSSCHECK.md` for the book/source/MARKERS reconciliation and the
one decision it leaves open.

## Anchors — [BK_ALIAS_BARE_V1], added 2026-08-12

A third anchor form was found live on the site: its contents list links parts
as a BARE slug — `#discovery`, `#security`, `#glossary` — while the emitter
writes `id="<slug>-anchor"`. Thirty-one links in the shipped book resolved to
nothing, and they were the book's own table of contents.

Both forms are now aliased. The bare form is DERIVED at emit time, not listed,
so a new part needs no bookkeeping — which is how the first alias map ended up
needing two hand-pinned judgement calls.

One trap worth knowing: `slug()` reserves ids in `usedIds`, so calling it to
record an alias renames the real heading to `-2`. `bareSlug()` exists for that
reason — it computes the string without claiming it.

## 2026-08-12, second pass

**Ch. 1** gains a forward reference: the sentence *"if the network is memory,
then a distributed program is just a program with threads"* is named as a
promise and deliberately withheld, because it means nothing before the
machinery is under it. **Part XII½'s lead** now pays that promise off by name,
so the two ends of the book close a loop rather than repeating each other.

**Ch. 54 — three limits closed.** *The rung is a sender-side decision*, *a
headless publisher must originate*, and *backpressure is reported in aggregate*
were all still listed in the present tense. Each is now marked "Closed, 11
August 2026" with what replaced it, and what survives of it where anything
does. The site's §03 already did this; the book had not.

Marking a limit closed, with a date, reads better than deleting it — a record
of things closing is more persuasive than a shorter list.

## 2026-08-12, third pass — the arc runs through the book

The launch handoff was right that the shared-memory repositioning had gone one
step too far: the Living Network had started reading as plumbing for FrogNet
Memory. It is the first term, not the substrate.

**New front-matter chapter, `How This Book Is Shaped`**, before Part I. Names
all six terms and points at the parts that carry each: alive (II-VII), memory
(VI, IX), the programming surface (XI, XII½), learn (ch. 26), grow, evolve
(XVI, the guild). It also tells a reader they can skip it and come back.

**Part IX no longer opens "Everything to here has been plumbing."** That single
sentence demoted six parts of real machinery. It now says what those parts
actually built: a network that keeps itself alive, and a great deal of what
people build uses nothing else.

**Every part lead now closes on its term.** Fifteen leads, one sentence each,
and three parts that had no lead at all (XI, XV, XVI) now have one. Part XV
names every term at once, because the Communicator is every term at once.

Measured through the body: memory, learn, grow and evolve all run from 0.01 to
past 0.90. The living-network language runs to 0.63 and then stops, which is
correct — the last third is handler code, appendices and the doctrine index,
where the framing would be decoration rather than argument.

The book already closed on "FrogNet brings networks to life. The guild brings
FrogNet to life." Nothing needed adding at the end.

## 2026-08-12, fifth pass — names across ponds

**Ch. 16 gains "Names from other ponds."** The hosts file carries what one pond
converged on, so a name from another pond is not in it. Each node therefore
lists the machine at the far end of every tunnel it holds as a nameserver:
local resolver first so own-pond and .frognet names never leave the box, peers
next, WAN last.

Three things in that section were expensive to learn and are written down so
they are not re-learned:

- The list comes from the **walk**, which is the one place the answer exists
  without ambiguity — every peer the merge records carries the device it
  answered on and the name it gave for itself.
- The **kernel cannot answer it.** `AllowedIPs` is `10.0.0.0/8` on every
  FrogNet tunnel and names no pond; deriving from it produced `10.0.0.1`, which
  is nobody. And an interface's several `/24` routes are mostly *transit* —
  ponds reachable *through* the tunnel rather than the pond *at the end* of it
  — so a node with three tunnels produced one nameserver.
- The far side must be **willing to answer.** Debian's `--local-service` replies
  only to queries whose source is on a subnet it owns, a WireGuard interface
  carries a narrow address, so every cross-pond querier reads as non-local.
  dnsmasq logs the refusal once and goes quiet.

**New figure `resolv_chain`**, drawn by `make_figures.py`: the real BABox case,
`nslookup brokerhost.seattle5` falling through 127.0.0.1 to 10.250.250.1 and
answering 10.250.250.155. 23 figures now, all generated.

## 2026-08-12, sixth pass — the slider, and where the bottom is

**Ch. 50 gains "What it does when you drag it."** The call's one control is a
speed setting, and dragging it moves BOTH ends — which is the point, because
nothing is sent to the far side. The setting is a value in the call's memory,
every participant reads it and runs `derive_rate` over the same rows, and the
far end changes because it read rather than because it was told. There is
nothing to renegotiate; the only failure mode is a stale read and the next read
fixes it.

At the bottom the picture becomes a thumbnail and **the audio does not change
at all** — the floor doing its job. One run, a static scene and a software
encoder on one radio, sat at 160x120 around a hundred kilobits a second with
the voice indistinguishable from the top of the ladder.

The section says plainly not to take that as a specification: a static scene
compresses to almost nothing, a moving one does not, and a software encoder on
a small board runs out before the link does. What generalises is the shape —
the picture is what the ladder spends, the voice is what it protects — and
Appendix J is how a reader finds their own number.

## 2026-08-12, seventh pass — the rest of the day

**Ch. 50a gains "Reading shared state, and reading it wrong."** Three rules
that are not about media, sockets or cameras and transfer to anything on this
substrate:

- `[A_SHRINKING_READ_IS_NOT_NEWS_V1]` — a store under load half-answers, and a
  partial read looks exactly like participants leaving.
- `[A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1]` — promoting on current mood
  promotes back into a rate already shown not to work. The durable fact belongs
  in the STATE, not in a participant.
- `[A_CEILING_MUST_BE_ABLE_TO_LIFT_V1]` — and that ceiling has to be able to
  clear, or it is permanent by construction.

They share a shape: a program treating its own reading of shared memory as more
authoritative than it was. None was found by looking at one participant.

**`[A_MANIFEST_IS_ONLY_AS_GOOD_AS_ITS_GATE_V1]`** added to the packaging table:
a correct manifest did not stop a 439 MB broker tarball, 437 MB of it two demo
videos. Size ceiling that fails rather than warns, plus a media check by name,
because a small video is just as wrong as a large one.

**Ch. 16** now says that a node disables `--local-service` by CONFIGURATION
rather than by editing the distro's service file — the file carrying the flag
is under `/usr/share`, so a patched copy is restored by the next package
upgrade and the failure returns as one journal line and then silence. And why
it is not replaced with an interface list: a written-down answer to a question
whose answer moves.

## 2026-08-14 — saying the wrong true thing

**Ch. 50a gains "Saying the wrong true thing."** A sender watches its own uplink
buffer, which is a real and useful fact. It published that fact as a GETTING
row — a claim about what is arriving here — so a headless publisher reported
itself as a struggling consumer of a stream that does not exist, and the
derivation stepped the whole call down for it. From 1280x720 to 160x120 at 23
fps with zero drops, reporting "1 sender, 1 report" where the report was its
own.

Three rules: `[A_SENDER_IS_NOT_A_CONSUMER_OF_ITSELF_V1]`, `[ONE_STEP_PER_SIZE_V1]`
applied to the uplink (it stepped five times because it measured the new size
against the old size's backlog), and `capped` as a fact about a sender so
nothing climbs it back on the grounds that no consumer complained.

The shape: each was a program publishing something TRUE in a category that made
it mean something else, and shared memory cannot catch that because the value
was well-formed and the writer had every right to write it. **What a fact is
filed under is part of the fact.**

Doctrine index now reports 225 rules, picked up from the source automatically.
