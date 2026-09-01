# CLAIMS.md — what is demonstrated, what is measured, what is argued

Written for a sceptical reader, human or machine, who wants the evidence status
of every substantive claim on this site without reading four pages to assemble
it. Nothing here is new; it is the same material sorted by how well it is
supported.

If a claim you care about is not in this file, treat it as **argued** until you
find it in the specification with a source citation.

Revision 2026-08-31. The specification is Draft 0.9 of the same date.

---

## Demonstrated on hardware, recorded live

These were captured from running systems. The recordings are unedited first-run
experiments, not reconstructions.

| Claim | Where | Note |
|---|---|---|
| A two-way HD video call sustains under 150 kbit/s over 900 MHz HaLow | `/demonstrations.html` §01 | Live capture. 22 fps measured |
| A call continues across a deliberate network partition | `/demonstrations.html` | The flagship capture |
| A value written in one city is read in another with no application messaging | `/index.html`, `/demonstrations.html` | The lamp: 2,400 miles each way |
| Route convergence over 20 distinct topologies | `/simulator.html`, spec §16.5 | Real kernel, network namespaces |
| The offline engine reproduces hardware route tables byte for byte | spec §17.5 | All 20 baselines stamped `source: hardware` |
| The media ladder steps down and recovers under contested RF | `/simulator.html` | Transport tier: latency, jitter, bandwidth, outage |

## Measured, reproducible by you

Figures produced by a tool that ships with the source. Run it and compare.

| Claim | Figure | How to check |
|---|---|---|
| Compression against ordinary traffic | 2.3× worst case, 9.6× on churning telemetry, 8,741× on a static blob | `pipe_workload.py <target> <payload>` — six payload shapes, three phases |
| An unchanged exchange | 21-byte FNWP-1 frame; 25 on the wire; 50 for the round trip | spec §11.10, and readable in `journalctl` |
| Did-vs-would bandwidth on a live node | ~89% reduction | `/demonstrations.html` |

**The published table leads with the worst case.** The large number is a static
payload with six changing fields; the honest number for traffic that actually
moves is roughly 10×. Both come from the same harness and the same command.

## Argued from the architecture, not measured

These follow from the design and are stated as reasoning. They are not
experimental results and should not be cited as such.

- That the programming model removes categories of application code — coordination,
  discovery, retry, session management. Argued in spec §10.4.
- That contention cannot arise where each writer owns its key. This is a property
  of the model, and **nothing in the fabric enforces it**: the store will accept a
  second writer to any key (spec §10.4).
- That an owned broker gains the inter-site bytes but not the templates they are
  diffed against, unless it observed from the first exchange of each structure
  (spec §12.4.1, §12.6).
- That a node runs correctly in a hypervisor guest. FrogNet restricts itself to
  standard kernel facilities, so a VM is expected to behave as bare metal, but
  nobody has tested it. Containers are a different case and are not supportable:
  a container shares the host kernel (spec §18.7).
- That the write ceiling moves with the machine holding the role (spec §17.3).
  The mechanism is specified; the ceiling itself has not been characterised with
  a published number.

## Not demonstrated, and stated as such

| | |
|---|---|
| **Prediction** | The simulator reproduces what hardware did. No run has yet predicted something a real box then confirmed. Agreement is not prediction |
| **Concurrency** | Merges run one at a time in a single process. Every race is unreachable by construction, so a green suite is not evidence about races |
| **Loss and jitter on a topology** | Modelled in the transport tier over a single link. A topology edge cannot carry them, so a degraded bearer cannot be attached to a named leg of a multi-site pond |
| **Independent verification** | Every claim on this site was produced and checked by one author. Nobody outside has reproduced any of it |
| **A second implementation** | None exists. A protocol only one implementation speaks has not been shown to be a protocol |

## Known limits, published

Not failures — consequences, each with its arithmetic in spec §17.

- **58 nodes** per pond on a single-pond broker; as few as **16** where one broker
  carries a second. A fix is planned; the allocator arithmetic is published.
- **One serialization point** is both the coherence guarantee and the write
  ceiling. The same fact stated twice.
- **No change notification.** This is a boundary, not a gap: deciding which
  changes matter is the application's job. How a program observes change is its
  own choice.
- **No cross-value atomicity.** `get_all` is not a snapshot. Two rows may reflect
  moments apart.
- **Four `PENDING SOURCE` values** the specification could not locate in source
  and declines to invent: discovery cadence (§6), the reassignment-callback
  registration API (§10), the template disuse interval (§11), association retry
  policy (§13).
- **Two of three falsification challenges carry no oracle** — credential
  manufacture, and what an owned broker can do (§18.5).

## How to disprove any of it

The specification names three falsification challenges (§18.5) and publishes the
tools. The simulator runs the real planner, committer, proxy and daemon rather
than a model of them, and `frognet_build_release.sh` builds an installable image
you can walk around a network you control.

If something here is wrong, it is wrong in a way that can be demonstrated. That
is the point of stating it this way.
