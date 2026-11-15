<!-- Generated from the site page spec-15-applications.html (https://fawcettinnovations.com/spec-15-applications.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §15

# Reference applications.

Evidence for properties that are otherwise only asserted. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Watching the pond think

Insertion into a brownfield enterprise

## Current implementation clauses: October 12, 2026

## Communicator

Presence and call control live in application RAM; media uses comms-media. Desktop r3 and Android r5 have separate platform paths and interoperability records.

## Network construction

FD&F is an application of owned facts, held events and deterministic derivation. Its graph, hub and bridge gates exercise network semantics with real kernel packet paths.

## Existing software

FrogTorch, Redis, lispers.net and RINA conversions preserve specific external or numerical contracts. Deliberate semantic differences, errors and controls are recorded separately.

## Historical experiments

Earlier household apps, game integrations and Python mediahost work remain part of the project’s history. They are not represented as a supported current product suite merely because files remain.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; where they describe the broker path, that path was removed in the October 2026 cleanup (docs/cleanup-2026-10/).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Standing](#x1)
2.  [2The Communicator](#x2)
3.  [3The Monitor](#x3)
4.  [4The games](#x4)
5.  [5What a reference application is not](#x5)
6.  [6References](#x6)

## 1. Standing

Croakus: [§41](https://fawcettinnovations.com/magnum-croakus.html#41-measuring-it-on-your-own-box), [§41c](https://fawcettinnovations.com/magnum-croakus.html#41c-the-monitor-reading-it-back), [§42](https://fawcettinnovations.com/magnum-croakus.html#42-the-shape-of-the-job), [§45](https://fawcettinnovations.com/magnum-croakus.html#45-the-communicator), [§46](https://fawcettinnovations.com/magnum-croakus.html#46-the-wire-under-the-call)

The applications in this section are **normative only in one sense**: a conforming implementation of §§1–14 is sufficient to support them. They are not part of the conformance surface, and an implementation MUST NOT be judged by whether it ships them.

What they are for is evidence. Each demonstrates a property of the model that is otherwise only asserted, and each is small enough to read.

## 2. The Communicator

Video, audio, chat and file transfer between member nodes, with **no TURN server, no cloud service, and no infrastructure beyond the pond** — and, across sites, a region (RAM manager and hub). It exercises discovery, election, FrogNet Memory, FNWP-1 and BLDC-1, and the media ladder, which is what makes it the reference application rather than a demonstration: it is the smallest program that touches every layer.

Reference measurements, with their conditions:

``` code
1280x720 at 22 fps, ~850 Kbps
    Seattle to New York, 900 MHz bearer

starvation on camera: 987 -> 637 -> 450 Kbps
    picture thins, audio holds, climbs back without a redial

ladder floor demonstrated: 164 Kbps at 160x120

held under 150 Kbps
    read off diagnostics on camera, not an instrument log —
    published as a bound, not a point figure (§14.6)
```

### 2.1 What the Communicator proves about the model

Its state is the worked example of §10.6.1: **a fact belongs to whoever can know it**. One row per session carrying a members list, written by whichever participant wrote last, meant one node renewed other people's membership and a departing member was reinstated by somebody else's heartbeat. No care on the leaver's side could fix that, because the row was not the leaver's to write. Separate rows replaced it, each written by the single party with standing to know the fact \[FOUR_TUPLES_V1\]. There are now five, and the tag keeps its original name:

| Row                 | Scope | Written by, and what it states                                        |
|---------------------|-------|-----------------------------------------------------------------------|
| `CallsAnnounce`     | user  | the participant, stating what it offers                               |
| `CallsInProgress`   | host  | the media host, stating what it sees carrying traffic                 |
| `CallsJoined`       | user  | the user, stating what it is in                                       |
| `CallsInvitations`  | user  | the user, stating what it has been asked to join                      |
| `CallsMediaControl` | call  | the call’s own media settings \[MEDIACONTROL_BELONGS_TO_THE_CALL_V1\] |

Scope follows standing: host rows by host, user rows by name, call rows by id. **Identity is the name.** No per-launch identifier and no display marker may reach a key, or the same party writes a new row every time it starts.

A related requirement falls out of the same defect: **a row MUST declare only the fields it actually offers**. One shape carrying both a host field and a user field meant every host-scoped row declared a user it never supplied and every user-scoped row declared a host it never supplied, and each converged to nothing. The source records this as the third time that shape has bitten and the first time a test caught it.

Call state is scoped per session (§10.3), with separate scopes per concern — the session envelope, chat, links, per-viewer treatment, transcript lines — rather than one row per call. One convergence channel per concern is what lets each hold its own reference and freshness.

### 2.2 Two planes

The session envelope is an ordinary exchange: it reaches SAME against a learned template and collapses to the floor frame when nothing moves (§11). The media frames carry opaque payloads, never reach SAME, and are sent in full every time. An implementation MUST NOT run media frames through the semantic path expecting a saving; the saving there is the ladder, not the codec (§14.1).

## 3. The Monitor

A read-only display of pond state, and the cleanest demonstration of the consumer half of §10. It writes nothing, holds no state outliving a keystroke, and uses only the read operations — a variable across scopes, and every variable for a service (§10.3).

The property worth noticing is what it **cannot** do: each node publishes its own state under its own name (§8), and the Monitor is an ordinary reader and **does not know a value was written for it**. It cannot ask a node for its state; it reads what nodes published for their own reasons, and a value it displays would have been written whether the Monitor existed or not. That is §2e visible in a program.

**Membership arrives and departs without an event.** A node joining the network is not announced to the Monitor and a node leaving is not either. A value exists that did not before, or values stop being refreshed and age out (§2e). The Monitor renders arrival and departure with no membership service, no subscription, no registration step and no membership code — which is §10.4 in a running program: the category of code is absent, not delegated.

**Provenance is available and is not the same thing.** Every row carries the writing node’s own address as envelope information — `SensorAddress`, surfaced as `addr` (§9.2.1) — so a reader can report who reported a value. What it cannot do is make that node produce one.

It doubles as an end-to-end liveness check. A rendered display implies the local proxy, the local daemon, the elected database host, and the remote publishers all functioned; a blank column localises a failure to a layer. An independent reader written against the same two calls SHOULD agree with it, and disagreement indicates a defect in one of them.

## 4. The games

A shared board-game engine was built to demonstrate **governed shared state**, and later removed (below). Three rules, all normative for anyone building on this model (§10.6.4):

``` code
a participant writes ONLY its own object
    a player states intent in a value that is theirs; it never
    writes the board and never writes another player's object

the governor is the one writer of record
    it owns the dice, the turn gate, the seating and legality,
    and IGNORES any value it is not allowed to write — so a
    player cannot fake a roll or seat itself by writing memory

a reader that never writes is a spectator, for free
    watching is not a feature added to a game; it is the
    absence of a seat
```

Hold those and a multiplayer game is reads and writes of a shared board. Let go of either of the first two and what has been rebuilt is a request/response server with extra steps.

**Status: removed.** The game engine is not in the tree. The proxy’s `/game` origin went on 25 September 2026 (`proxy/proxy_main.py:271-272`: “\[GAME_ORIGIN_REMOVED_V1\] John, 2026-09-25: core/game_origin.py is outdated and removed”), the boardgame role with it (`core/role_registry.py` `ROLE_HANDLERS` has no game role; `frognet_prune_removed.sh:485-497`: `core/game_role.py`, `install_boardgamehost.sh`, `GAME_ORIGIN_INSTALL.md`), and the last remnants on 12 October 2026 (`docs/cleanup-2026-10/T2.md`; `frognet_prune_removed.sh:531,536`: `simulation/games`). The three rules above stand as doctrine for anyone building a governed game on this model (§10.6.4); no shipped game demonstrates them, and an implementation MUST NOT cite one.

## 5. What a reference application is not

None of these is a product, a supported deliverable, or a conformance test (§16). None is required for a node to function. An implementation that supports §§1–14 and ships none of them is conforming; an implementation that ships all of them and violates an invariant in §2 is not.

## 6. References

Specification [§2](spec-02-invariants.md), [§8](spec-08-election.md), [§9](spec-09-memory.md), [§10.6](spec-10-unrest.md#u6), [§11](spec-11-wire.md), [§14](spec-14-performance.md), [§16](spec-16-conformance.md), [§17](spec-17-limits.md). Source: `opt/frognet_semantic/ribbit/examples/communicator/` (the game sources were removed in 2026). Build manual: Croakus [§42](https://fawcettinnovations.com/magnum-croakus.html#42-the-shape-of-the-job), [§43](https://fawcettinnovations.com/magnum-croakus.html#43-the-first-handler-tic-tac-toe), [§44](https://fawcettinnovations.com/magnum-croakus.html#44-the-advanced-handler-backgammon-and-a-real-bug), [§45](https://fawcettinnovations.com/magnum-croakus.html#45-the-communicator), [§52](https://fawcettinnovations.com/magnum-croakus.html#52-the-communicator-is-a-hello-world). Worked code: [UnREST by example](https://fawcettinnovations.com/unrest-examples.html). Demonstrations: [on camera](https://fawcettinnovations.com/demonstrations.html).

[← §14 Performance and Adaptation](spec-14-performance.md) [Index](README.md) [§16 Simulator and Conformance →](spec-16-conformance.md)
