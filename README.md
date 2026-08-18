# FrogNet-Living-Network
# FrogNet — The Living Network

**Magnum Croakus** — the build manual, the architecture, the reasoning, and the limits.

This repository holds the book. It does not hold the engine.

---

## What FrogNet is

A network where programs share memory instead of calling each other.

A node discovers what is around it, organises itself, elects the roles it needs, adapts to whatever bearers exist, splits when the physical connection does, and merges when it comes back — with nobody administering any of it. On top of that lives **FrogNet Memory**: a live, network-addressable store every node reads and writes. A program writes a value where it computes it and reads it where it needs it. There is no peer to address, no reply to wait for, and no code to write for the case where the reply never comes.

REST is not gone. It is underneath, on the wire, where assembly went when programmers moved to C.

It runs today across nodes in Seattle, New York and Amsterdam.

---

## What is in this repository

| | |
|---|---|
| `Magnum Croakus.docx` | the book, 87,000 words, 59 chapters, 13 appendices |
| `Magnum Croakus.html` | the same book, web edition |
| `build_magnum.js` | the generator both editions come out of |
| `magnum-figures/` | 23 diagrams, drawn from code |
| `tools/make_figures.py` | the script that draws them |

**Edit the generator, never the output.** Both editions are produced by one `node build_magnum.js` run, and a hand-edit to a `.docx` or `.html` is gone the next time anybody builds.

```
npm install docx
pip install pillow
node build_magnum.js
```

---

## What is *not* in this repository, and why

The FrogNet engine is a **licensed reference implementation**. The source is not published, and this is not an open-source project. I would rather say that plainly than let a badge imply otherwise.

The licence is **free** for personal, community, educational, research and non-commercial use — including an individual wiring up a neighbourhood. Commercial use, meaning FrogNet as a core platform to make money, requires a paid licence from Fawcett Innovations LLC.

What *is* headed for the open is the **protocol**: FNWP-1's wire format, BLDC-1's state machine and template learning, the freshness classes and tuple semantics, and the election scoring — as a normative, versioned specification you can implement against without my code. This book is the seed that specification gets extracted from.

**Two doors.** To talk about it, use the Discussions here — no licence, no account with us, no permission. To build on it, ask for a licence at [fawcettinnovations.com/license](https://fawcettinnovations.com/license).

*The implementation is licensed. The argument is not.*

---

## Where to start

**If you want to know whether it works** — the videos. Someone at a terminal, from an empty box to a transcontinental call, saying out loud where the work is unfinished and where it went wrong on camera. The book carries 25 links to them at the chapters they belong to.

**If you want to understand it** — Part I, then Part IX. Chapter 1b if you already have a network and want to know what adopting this would cost you. Chapter 55 if you want the mental model before the mechanics.

**If you want to build it** — Appendix J, "From Clean to the Communicator." Three machines and a router, end to end. Budget about two days; the videos do not shorten that, they remove the guessing.

**If you want to break it** — the simulator, and Part XII.

---

## Some of this is wrong

Not *might be*. Is.

FrogNet is one person's work, and a system built from one perspective is guaranteed to have assumptions in it that only somebody else's environment will find. The book is written accordingly:

- **Appendix K, Known Limits** — the current release caps a pond near fifty-six nodes where the addressing plan allows sixty-three thousand. The arithmetic is published, the cause is named, and the fix is designed but not implemented.
- **Part XVI, What Is Not Built Yet** — no per-client rate adaptation, asymmetric inbound and outbound priority, partial store results under load, capture hardware that lies.
- **Chapter 53b, When It Goes Wrong on You** — the failure modes an operator will actually hit, including two nobody has explained.
- **Chapter 41, the ramp anomaly** — a measurement that dips, reproducibly, for reasons unknown. It is in the book rather than out of it because a measurement tool that only reports the flattering numbers is not a measurement tool.
- **Seven subsystems** were replaced wholesale, and the reversals are annotated in the source with the reasoning that caused them rather than quietly deleted.

If you think one of these is wrong, that is a contribution. If you think one is obviously *right*, that is a bigger one.

---

## Take the broker

The security argument in Part XII is not an assertion, it is a challenge with defined failure conditions.

The broker is the inter-site data path — every cross-site packet transits it. Assume it is owned. Three things would prove the architecture wrong:

```
ORACLE 1  broker pivot
  given   root on the broker; no endpoint keys; no node
  red if  a new application-level exchange can be originated
          with an internal FrogNet-only service

ORACLE 2  application recovery
  given   root on the broker — all cross-site traffic transits it,
          so capture is free
  red if  protected application content can be reconstructed
          without compromising an endpoint

ORACLE 3  credential manufacture
  given   root on the broker
  red if  sufficient authority can be manufactured to join as a
          legitimate member, without the endpoint secret material
```

**A red run is a finding. A hypothetical is not.** State which oracle you are attacking, run it, and show the result. A reproducible counterexample is a complete contribution and does not have to arrive with a fix.

---

## Contributing

**To the book** — issues and pull requests here. A passage that is wrong, unclear, or missing the case you actually have is worth raising; the correction lands in the next build for everybody rather than in one reply to one person.

**To the specification** — four normative documents need extracting from this manual. The reasoning is already written into the source at the point of decision, as 221 named rules, and the build fails if a load-bearing one is removed, so the specification and the implementation can be diffed by a script. Whoever writes each document is its editor. This needs no engine access and no licence.

**To the engine** — ten subsystems, each a seam somebody could own, named at [fawcettinnovations.com/community](https://fawcettinnovations.com/community). Everywhere the bar is the same: you got it running and you noticed something.

**Counsel.** Three live questions and rarely one practitioner: export classification of the reference implementation; the Foundation's formation and the IP assignment that makes the standard owned by its users rather than by me; and a licensing structure that has to hold a closed implementation, an open specification and a commercial company together without contradicting itself in five years. The structure is not set, so counsel arriving now shapes it rather than reviews it.

---

## Links

- **Site** — [fawcettinnovations.com](https://fawcettinnovations.com)
- **The whole argument in five minutes** — [fawcettinnovations.com/five-minutes](https://fawcettinnovations.com/five-minutes)
- **Videos** — [fawcettinnovations.com/watch](https://fawcettinnovations.com/watch)
- **Licence** — [fawcettinnovations.com/license](https://fawcettinnovations.com/license)

---

FrogNet™ and *Magnum Croakus* © 2026 Fawcett Innovations LLC. All rights reserved.
CAGE 1A5Y5 · Burien, Washington.

*FrogNet brings networks to life. The Guild brings FrogNet to life.*

*I gave FrogNet life. I am not its life.*
