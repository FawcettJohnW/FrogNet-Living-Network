# WorldShepherd on the memory: the acceptance study

This is the study Brandon Davis (WorldShepherd Research & Development) asked for on 2026-11-25 after reading the
published branch: continuity of SARA's read-ticket ledger when the memory is replaced, a link splits, an issuer is
absent or a writer is late, with the measurements he named; and the access-control negative controls, made through
the real RAM API. It is one program, `opt/frognet_semantic/simulation/worldshepherd_acceptance.py`. This file says
what it runs, what it measured on 2026-11-25, what it found, and what was changed because of it.

## The study on 0.11 (2026-11-26)
**This section is the current result. Everything after it, from "What is pinned" on, is the study as it was run on
0.9: its method, cases and injections are unchanged and still read true; its numbers, its access controls and its
storage finding are 0.9's.**

Pinned: FrogNet `bc7221b+uncommitted` on `worldshepherd-ribbit-20261124`; SARA `52b3764c`; `worldshepherd-ram worldshepherd-ram 0.11 2026-11-25i`; platform `ribbit-platform 0.72`; `fdf-app-2026-11-25-r56`; study `worldshepherd-acceptance 2026-11-25d`. Three repetitions
of each injection on each link; the 310-second case once, on the LAN link. Rows: `results/acceptance-0.11i-20261126.jsonl`.

From `opt/frognet_semantic`, with SARA's Python packages importable:

    python3 simulation/worldshepherd_acceptance.py --sara $SARA --link lan wan-100ms --skip-long --reps 3
    python3 simulation/worldshepherd_acceptance.py --sara $SARA --link lan --only long

**Result: PASS on both links: 49 rows PASS, 2 OPEN, 0 FAIL. No ticket was won twice and none was lost in any case.
The one control left OPEN is a restored winner whose issuer did not see the win.**

**Not done: nothing removes a ticket.** An expired or consumed ticket's record and its cell stay for the life of the
memory; the RAM host grows with every ticket ever issued. Removing them changes what a very late claim is answered
("not registered" where SARA's stores answer "expired" or "consumed"; his stores never delete a row), so it is not built.

### LAN (0.5 ms each way)
| case | tickets | claims | duplicate wins | lost tickets | claim p50 / p99 ms | recovery p50 / max ms | 0.9 recovery p50 / max ms |
|---|---|---|---|---|---|---|---|
| baseline | 240 | 720 | 0 | 0 | 2.4 / 12.0 | none injected | none injected |
| restart:before-claims | 720 | 2160 | 0 | 0 | 2.4 / 43.4 | 74.6 / 84.3 | 171.5 / 293.5 |
| restart:claims-in-flight | 720 | 2160 | 0 | 0 | 2.5 / 79.9 | 77.4 / 109.7 | 250.3 / 451.0 |
| restart:reassertion | 720 | 3240 | 0 | 0 | 2.5 / 126.8 | 180.2 / 215.0 | 595.6 / 650.4 |
| restart:completion | 720 | 4320 | 0 | 0 | 2.5 / 8.9 | 199.6 / 202.8 | 612.6 / 787.2 |
| split:issuer | 720 | 2160 | 0 | 0 | 2.2 / 9.0 | 13.0 / 15.9 | 20.1 / 26.6 |
| split:claimant-in-flight | 720 | 2160 | 0 | 0 | 2.1 / 9.3 | 2.5 / 2040.4 | 5.2 / 2405.0 |
| split:winner+restart | 720 | 2160 | 0 | 0 | 2.0 / 3037.3 | 3065.5 / 3095.4 | 3210.5 / 3252.3 |

### WAN (50 ms each way)
| case | tickets | claims | duplicate wins | lost tickets | claim p50 / p99 ms | recovery p50 / max ms | 0.9 recovery p50 / max ms |
|---|---|---|---|---|---|---|---|
| baseline | 24 | 72 | 0 | 0 | 107.4 / 111.4 | none injected | none injected |
| restart:before-claims | 72 | 216 | 0 | 0 | 107.4 / 651.8 | 653.0 / 669.9 | 670.3 / 678.9 |
| restart:claims-in-flight | 72 | 216 | 0 | 0 | 108.0 / 797.6 | 660.2 / 665.8 | 679.2 / 696.9 |
| restart:reassertion | 72 | 324 | 0 | 0 | 107.6 / 645.6 | 813.9 / 817.9 | 852.8 / 902.2 |
| restart:completion | 72 | 432 | 0 | 0 | 107.6 / 655.4 | 667.7 / 687.8 | 709.8 / 721.3 |
| split:issuer | 72 | 216 | 0 | 0 | 107.2 / 111.2 | 111.6 / 116.6 | 114.5 / 118.1 |
| split:claimant-in-flight | 72 | 216 | 0 | 0 | 107.4 / 2564.3 | 95.9 / 2547.6 | 98.1 / 2581.5 |
| split:winner+restart | 72 | 216 | 0 | 0 | 107.6 / 3545.2 | 3557.5 / 3558.0 | 3567.3 / 3579.3 |

Recovery is the time from the injection to the first answer after it. On 0.11 a SARA brings its issues and its won
claims back to a new memory in one call (`op=again`). The first build of 0.11 made one call per ticket, one after
another, and this study measured its recovery at 10 to 33 s on the WAN link; that is why `op=again` exists.

### The long case (LAN)
Two memories for 310 s: the issuer on one side, claimants on both, and one claimant's link down for longer than a
ticket can live (300 s). PASS: no ticket won twice, none lost. Side B (issuer absent) answered {'ticket not registered in this ledger': 12} after holding 0 to
300 s; d's claims, held 310 s past the ticket's life, were answered ['ticket expired', 'ticket expired'].

### The access controls (LAN; the WAN link gave the same verdicts)
On 0.11 a SARA writes one cell itself, `(worldshepherd, sara, <who>)`; everything about a ticket goes through the
ticket's operations, and the ticket writes how it stands in `(worldshepherd, tickets.<its issuer>, <ticket>)`. Each
control passes when the ticket stands afterwards exactly as it did before.

| control | result | what happened |
|---|---|---|
| access:other-name(claim) | PASS | refused: a-b61cd48b0299 is the SARA at 127.0.1.11; this is from 127.0.1.99; the ticket stands as it did |
| access:other-name(held-claim) | PASS | refused: a-b61cd48b0299 is the SARA at 127.0.1.11; this is from 127.0.1.99; the ticket stands as it did |
| access:other-name(here) | PASS | refused: a-b61cd48b0299 is the SARA at 127.0.1.11; this is from 127.0.1.99 |
| access:ticket-cell | PASS | refused: (worldshepherd, tickets.issuer-75bf3ce64536) is written by the ticket; a SARA writes (worldshepherd, sara, <who>) and calls issue and claim |
| access:not-a-family | PASS | refused: (worldshepherd, junk) is written by the ticket; a SARA writes (worldshepherd, sara, <who>) and calls issue and claim |
| access:claim-other-digest | PASS | answered digest; the ticket stands as it did |
| access:claim-past-expiry | PASS | answered expired; the ticket stands as it did |
| access:claim-on-consumed | PASS | answered consumed; the ticket stands as it did |
| access:held-claim-on-consumed | PASS | answered consumed; the ticket stands as it did |
| access:malformed-claim | PASS | refused: claim says "who", "ticket", "sha", "n" and "at"; the ticket stands as it did |
| access:issue-of-an-issued-ticket | PASS | answered ok; the ticket stands as it did |
| access:other-session | PASS | refused: a-b61cd48b0299 is session 5's, which is up; this is on session 25; the ticket stands as it did |
| access:no-session | PASS | refused: HTTP 403: {"ok":false,"error":"a WorldShepherd call is made on a session; this one has none"} |
| access:tickets-after | PASS |  |
| access:restored-winner(issuer-saw) | PASS | a member that said it held a ticket, in a new memory before the winner returned, was answered claimed for 0 of 4 tickets; afterwards 0 stand won by it and 4 by the true winner. the issuer had seen each ticket won before the memory was replaced, and said so when it issued them again |
| access:restored-winner(issuer-did-not-see) | OPEN | a member that said it held a ticket, in a new memory before the winner returned, was answered claimed for 4 of 4 tickets; afterwards 4 stand won by it and 0 by the true winner. the issuer's link was down while the tickets were won, so it could not say who won. The ticket cannot tell: proof is evidence from before |

### The provenance of a restored winner
In a new memory a ticket exists again when its issuer makes it again. `[THE_ISSUER_SAYS_WHO_WON_V1]`: the issuer holds
one read on its own tickets' family, sees each of them won, and says who won when it makes the ticket again. A ticket
made again that way comes back consumed by its winner, and a member that says it held it is answered consumed
(`access:restored-winner(issuer-saw)`, PASS on both links).

**Open:** a ticket won while its issuer could not see it (the issuer's link down, or the memory replaced before the
ticket's write reached the issuer's read, about one trip of the link). The issuer makes it again as issued and not
won, and the first valid claim takes it (`access:restored-winner(issuer-did-not-see)`: 4 of 4 taken on both links).
Nobody left in the new memory can say who won. Closing it needs evidence the winner carries from before; SARA's read
receipts are sealed with an unkeyed SHA-256 and are not signed, so that is WorldShepherd's to add.

### Storage
| | 0.9 | 0.11 |
|---|---|---|
| cells for 200 tickets after issue | 200 | 200 |
| after 3 SARAs claimed each | 800 | 200 |
| after they claimed each again | 1400 | 200 |
| cells a claim call adds | 1.0 | 0.0 |
| in a replaced memory | 400 | 200 |

On 0.9 every claim call added a cell and the finding was that nothing bounded them. On 0.11 a ticket is one cell
however often it is claimed. In this case (200 tickets, 1,200 claim calls, each ticket's status read by every SARA)
the RAM host grew 29.4 kB a ticket on the LAN link and 29.1 on the WAN link; most of that is the wire's reference for
each cell a session has been sent, kept by the platform's session code for the life of that session, not the ticket.
**Still true: a ticket's record and its cell stay for the life of the memory; nothing removes an expired ticket.**

### What this study found in 0.11, and what was changed
1. **Recovery of 10 to 33 s on the WAN link** (0.9: 0.7 to 0.9 s). A SARA made each of its issues and won claims again
   as its own call, one after another. Now one call, `op=again`.
2. **A SARA locked out of its own name.** When bringing its own back failed, the store opened a new session without
   closing the one it had just opened; the host saw the name as that session's, "which is up", and refused every
   later session. It tried 290 times in 10 s. The store now closes a session it gives up on.
3. **The study itself replaced the caller's `PYTHONPATH`**, so it could not find SARA's packages. It keeps it now.
4. **Bringing a SARA's own back wrote to the RAM host's disk, and failed where it could not.** The first `op=again`
   carried its issues and claims as JSON lists; the wire keeps a list out of line, as a file under
   `/opt/frognet_semantic/blob_cache`. Where that directory could not be made the call failed every time. It now
   carries one line per issue and claim, and nothing goes to disk (`harness/test_the_apphost_moves.py` passes with
   no such directory).
5. **A SARA that was refused by a live RAM host tried again at once, without end**, until Python ran out of stack.
   It now holds a read on the SARAs present and tries when that family changes.
6. **A restored winner could be taken by any member** (OPEN on 0.9 and on the first 0.11). The issuer now says who
   won. What is left open is above.

### aarch64
The study was also run on the LAN link, three repetitions, against the aarch64 RAM host under emulation (the SARAs'
session library x86_64): 24 PASS and the same one OPEN (access:restored-winner(issuer-did-not-see)). Rows:
`results/acceptance-0.11i-aarch64-emulated-20261126.jsonl`. The aarch64 session library is checked without Python by
`make session-check` (1,000 tickets, 7 sessions, 7,000 claims on 7,000 threads, under emulation: PASS).

### What was not run
The study has not been run on real nodes, and never with the aarch64 session library under Python (there is no
aarch64 Python here). The WAN link's long case was not run on 0.11.

# The study as run on 0.9

## What is pinned

| | |
|---|---|
| FrogNet | branch `worldshepherd-ribbit-20261124`; the commit that adds this file. The rows in `results/acceptance-0.9-20261125.jsonl` were measured on its tree before it was committed and say `7f91f30+uncommitted` |
| SARA | `Bdavis1390/Sara_pro`, branch `worldshepherd/connectors-distributed-claim-contract-v1-20260913`, commit `52b3764c` |
| the app's RAM host | `worldshepherd-ram 0.9 2026-11-25a` (it logs its build when it starts) |
| the store | `worldshepherd-ribbit 0.9 2026-11-25a` (each SARA logs it when it attaches) |
| fdf-app | `fdf-app-2026-11-25-r55` |
| the contract | SARA's `SharedClaimStore` API (register, claim, status) and its five `tests/test_connector_*.py` files, unchanged: 31 passed on `RibbitClaimStore`, and 31 on his `PostgresClaimStore` with its SQL done as cells (`results/his-tests-0.9.log`) |

Every row the study writes carries these.

## The smallest reproduction

Linux, g++ with C++20, `liblz4-dev`, `libssl-dev`, Python 3.10 or later with `pytest` and `fastapi`. No node, no root.

    git clone https://github.com/FawcettJohnW/FrogNet-Living-Network.git
    cd FrogNet-Living-Network && git checkout worldshepherd-ribbit-20261124
    (cd opt/frognet_semantic/ribbit/examples/worldshepherd && make)
    git clone https://github.com/Bdavis1390/Sara_pro.git
    (cd Sara_pro && git checkout worldshepherd/connectors-distributed-claim-contract-v1-20260913)
    cd opt/frognet_semantic
    python3 simulation/worldshepherd_acceptance.py --sara ../../../Sara_pro --link lan --skip-long

That is about one minute and prints one line a case and `RESULT`. Without `--skip-long` it also runs the case that
lasts the contract's whole `MAX_TTL_SECONDS` (310 s). `--link lan wan-100ms` runs both links (about 14 minutes with
the long case). `--only restart:reassertion split:winner` runs the two failure-injection cases named. `--reps N`
repeats each injection N times (the tables below are 5). Rows and every process's log go to the directory it names.

## What is real and what is simulated

Real: the app's RAM host, `RibbitClaimStore`, and SARA's contract. Each SARA is its own process. Simulated: the
network. Every SARA reaches the RAM through its own shaped link and arrives from its own address (`simulation/slowlink.py`).

- **The memory is replaced.** The RAM host is killed and a new one with an empty memory is started on the listening
  socket the study holds. That is what `fdf-app` does when the AppHost moves or its RAM host dies. The election
  itself, with `fdf-app` on three machines, is `harness/test_the_apphost_moves.py` (six checks, `results/apphost-moves-0.9.log`).
- **A split.** One SARA's link goes down. Its session ends, and its connections are not answered until the link is back.
- **Two memories.** A second RAM host with an empty memory, and some SARAs' links led to it and later back: an
  election on each side of a partition.

What the SARAs are asked and what they answer are cells in a second memory the study never stops, read by held reads.

## What is measured

For every case: claims answered and raised; tickets won twice (duplicate wins); tickets issued and claimed that
nobody won (lost); the claim's p50, p95 and p99 and the longest any call was held; and recovery. Recovery is, for
each call in flight at the injection (or, for a SARA with none in flight, its first call after), the time from the
injection until that call was answered. A case fails on any duplicate win, any lost ticket or any call that raises.

## Results, 2026-11-25, five repetitions of each injection

### LAN (0.5 ms each way)

| case | verdict | tickets | claims answered / raised | won twice | lost | claim p50 / p95 / p99 ms | longest held ms | recovery p50 / p95 / p99 / max ms (calls) |
|---|---|---|---|---|---|---|---|---|
| baseline | PASS | 240 | 720 / 0 | 0 | 0 | 2.6 / 4.6 / 6.1 | 8.3 | - |
| restart:before-claims | PASS | 1200 | 3600 / 0 | 0 | 0 | 3.2 / 7.6 / 131 | 277 | 172 / 294 / 294 / 294 (15) |
| restart:claims-in-flight | PASS | 1200 | 3600 / 0 | 0 | 0 | 3.0 / 7.6 / 325 | 453 | 250 / 451 / 451 / 451 (60) |
| restart:reassertion | PASS | 1200 | 5400 / 0 | 0 | 0 | 3.3 / 7.5 / 484 | 602 | 596 / 650 / 650 / 650 (15) |
| restart:completion | PASS | 1200 | 7200 / 0 | 0 | 0 | 3.1 / 6.5 / 15.9 | 757 | 613 / 787 / 787 / 787 (15) |
| split:issuer | PASS | 1200 | 3600 / 0 | 0 | 0 | 3.0 / 6.4 / 10.7 | 14.1 | 20.1 / 26.6 / 26.6 / 26.6 (15) |
| split:claimant-in-flight | PASS | 1200 | 3600 / 0 | 0 | 0 | 2.6 / 5.4 / 8.4 | 2405 | 5.2 / 2404 / 2405 / 2405 (60) |
| split:winner+restart | PASS | 1200 | 3600 / 0 | 0 | 0 | 2.3 / 4.2 / 3136 | 3229 | 3210 / 3252 / 3252 / 3252 (10) |
| access:tickets-after | PASS | 3 | 6 / 0 | 0 | 0 | 2.8 / 3.2 / 3.2 | 3.2 | - |
| long:two-memories | PASS | 8 | 45 / 0 | 0 | 0 | 14.2 / 299905 / 310120 | 310120 | 300022 / 310124 / 310124 / 310124 (4) |

### WAN (50 ms each way, the 100 ms round trip of NY2 to Seattle7)

| case | verdict | tickets | claims answered / raised | won twice | lost | claim p50 / p95 / p99 ms | longest held ms | recovery p50 / p95 / p99 / max ms (calls) |
|---|---|---|---|---|---|---|---|---|
| baseline | PASS | 24 | 72 / 0 | 0 | 0 | 108 / 111 / 112 | 112 | - |
| restart:before-claims | PASS | 120 | 360 / 0 | 0 | 0 | 109 / 656 / 660 | 662 | 670 / 679 / 679 / 679 (15) |
| restart:claims-in-flight | PASS | 120 | 360 / 0 | 0 | 0 | 108 / 702 / 707 | 816 | 679 / 697 / 697 / 697 (60) |
| restart:reassertion | PASS | 120 | 540 / 0 | 0 | 0 | 109 / 682 / 723 | 727 | 853 / 902 / 902 / 902 (15) |
| restart:completion | PASS | 120 | 720 / 0 | 0 | 0 | 108 / 692 / 701 | 706 | 710 / 721 / 721 / 721 (15) |
| split:issuer | PASS | 120 | 360 / 0 | 0 | 0 | 108 / 111 / 175 | 177 | 114 / 118 / 118 / 118 (15) |
| split:claimant-in-flight | PASS | 120 | 360 / 0 | 0 | 0 | 108 / 2570 / 2592 | 2594 | 98.1 / 2580 / 2582 / 2582 (60) |
| split:winner+restart | PASS | 120 | 360 / 0 | 0 | 0 | 108 / 3550 / 3556 | 3557 | 3567 / 3579 / 3579 / 3579 (10) |
| access:tickets-after | PASS | 3 | 6 / 0 | 0 | 0 | 109 / 111 / 111 | 111 | - |
| long:two-memories | PASS | 8 | 45 / 0 | 0 | 0 | 109 / 300105 / 310429 | 310429 | 300348 / 310435 / 310435 / 310435 (4) |

In `long:two-memories` the six tickets that were claimed in their life were each won once; the two that only the
late writer claimed expired unclaimed, as they must.

### What the cases are

| case | what is injected |
|---|---|
| baseline | nothing: what a claim costs on this link |
| restart:before-claims | the memory is replaced between issue and claim |
| restart:claims-in-flight | the memory is replaced with claims in flight |
| restart:reassertion | the memory is replaced, and replaced again while the SARAs are writing their own truth into the new one |
| restart:completion | the memory is replaced after every ticket is consumed; every SARA then claims every ticket again |
| split:issuer | the issuer's link is down for 2 s between issue and claim |
| split:claimant-in-flight | a claimant's link goes down for 2 s with its claims in flight |
| split:winner+restart | the winner's link goes down, the others see it leave, the memory is replaced, and the winner returns 3 s later |
| long:two-memories | for 310 s: the issuer and one claimant on one memory, two claimants on a second, empty one (the issuer is absent there), and a fifth SARA's link down with two claims held |

### The long case (the absent issuer, and the writer delayed past the 300-second maximum TTL)

- On the memory the issuer was absent from, the two claimants' twelve claims were held up to 299.9 s and then all
  refused as "ticket not registered in this ledger". Nothing was won there.
- On the issuer's memory the claimant won five of the six tickets at once; the sixth had been consumed before.
- The fifth SARA's two claims were held 310.1 s by its link and answered "ticket expired".
- After the two memories were one again every SARA claimed every ticket: no ticket was won twice. Status knew all
  eight tickets and showed six consumed.

## The access controls

Each is a write made through the RAM host's own API from a session that is not the owner's, and each passes only
if the write is refused and the cell is not in the memory afterwards.

| control | the RAM host's answer |
|---|---|
| a claim in another SARA's name saying won, from another address, answered write | refused: that name is the SARA at another address |
| the same as a posted write | refused (counted on the session) |
| won with a digest that is not the ticket's | refused |
| won at a time past the ticket's expiry | refused |
| won on a ticket another claim consumed in this memory | refused |
| a claim with no "at" | refused |
| a cell in a family that is not WorldShepherd's | refused |
| another SARA's name from its own address on a second session while its session is up | refused |
| a write with no session (plain HTTP) | refused, HTTP 403 |
| the tickets those were aimed at, claimed afterwards by two SARAs | each won once |

The rules are the RAM host's (`worldshepherd_ram.cpp`). Who may write a cell is the app's rule, and the app's RAM
host enforces it: the host is the platform's RAM host with WorldShepherd's rules about its own cells, and `fdf-app`
serves it in place of the general one (`fdf-app config worldshepherd general --host general=worldshepherd-ram`).

- **Writer identity.** A cell names its writer. A name belongs to the machine whose session first wrote a cell
  naming it, for the life of the memory, and while that session is up to that session alone. The machine is the
  address the session arrives from, which on a FrogNet network is the node's own address, admitted by key.
- **What a cell says.** An issue says a digest and an expiry; a claim says a digest and a time. Anything else in
  the service is refused, so a reader never meets a cell it cannot derive from.
- **Won is consistent with the ticket.** A claim that says won is refused when the memory shows it false. Every
  reader applies the same digest and expiry rule (`Ticket`), so the rule holds whichever of the issue and the claim
  arrives first.

## Open: the provenance of a restored winner

`access:restored-winner` is reported OPEN, not passed. After the memory was replaced and before the true winner had
returned, a member wrote a claim of its own saying won, with the right digest and a time inside the ticket's life.
It was taken for 4 of 4 tickets, and the true winner's claim was then refused as a second winner.

The RAM host cannot tell these apart. The memory in which the win happened is gone and kept no file. The proof has
to be evidence the winner carries from before the memory was lost, and SARA already has the means: its signed
receipts. A claim that says won would carry the receipt, and the RAM host or every reader would verify it. That is
WorldShepherd's to add, and this case is the test for it.

## Finding: storage is not bounded within a memory's life

200 tickets, three SARAs claiming each ticket twice:

| | cells for the 200 tickets |
|---|---|
| after issue | 200 |
| after each SARA claimed each ticket | 800 |
| after they claimed again | 1,400 |
| in a replaced memory (each issue and each won claim, written again) | 400 |

Every claim call adds one cell, and nothing removes a ticket's cells while its memory lives. The RAM host grew by
12.5 MB over the 1,200 claim cells (about 10.6 kB a cell, 85 kB a ticket with its seven cells; this build of the
host uses the system allocator). A SARA likewise keeps every ticket it issued and every claim it won for as long
as it runs, and writes all of them again each time the memory moves, so recovery grows with history: in the LAN
table it is 172 ms at the first replacement and 613 ms after about 4,000 more tickets.

A ticket lives at most 300 s, so the bound is available: a SARA removes its own cells, and forgets them, a
ticket's life after it wrote them. That is not built. It changes what a claim on an old ticket is told ("ticket
not registered" in place of "ticket expired"), which is WorldShepherd's decision.

## What the study found, and what was changed

Each of these failed in the study first and passes now. The rule names are in the code.

| found | measured before | change | after |
|---|---|---|---|
| A SARA ended ("nothing more is tried") when the memory was replaced again while it was writing its own truth into the new one | restart:reassertion: 1,800 of 2,160 claims raised, 360 tickets lost | `[A_MEMORY_GONE_WHILE_I_WRITE_MY_OWN_V1]`: the attach is done again, whole, in the memory that answers next | 5,400 claims, 0 raised, 0 lost |
| A session opening across a replacement waited 10 s for the half of a pair that went to the old host | recovery p50 10,120 ms | `[AN_OPENING_SESSION_WATCHES_ITS_OTHER_SOCKETS_V1]` in the platform's session library: an opening session notices at once that a connection it made has ended | recovery p50 596 ms (LAN), 853 ms (WAN) |
| A claim made again after a split wrote a second cell, while its first had already consumed the ticket: the ticket was consumed and its own SARA was told "already consumed" | split:claimant-in-flight: 17 of 2,880 tickets lost | `[A_CALL_HAS_ONE_CELL_V1]`: a call names its cell once; made again, it reads first and writes only if its cell is not there | 0 of 5,760 lost |
| A SARA whose link went down just before the memory was replaced was not waited for, and the tickets it had consumed were claimed again | split:winner+restart: 240 of 1,200 tickets won twice | `[A_SARA_THAT_LEFT_IS_WAITED_FOR_V1]`: a SARA that left the memory less than a ticket's life ago is waited for like one that was there | 0 of 7,200 won twice in 30 repetitions |
| A claim held 310 s by a link that was down was answered "claimed" 10 s after its ticket expired | long:two-memories: 2 of 2 | `[A_HELD_CLAIM_AGES_V1]`: a call that is made again is as old as it has been held | both "ticket expired" |
| Any session could write a claim in any name saying won, with any digest and time (Brandon Davis's finding) | accepted | the app's own RAM host and its three rules; `[WON_IS_CONSISTENT_WITH_THE_TICKET_V1]` in `Ticket` | the ten controls above |

The contract did not move: his 31 tests pass unchanged on both stores, and the 8-process race still has 400 of 400
tickets won once (3,785 claims a second on the memory store with the RAM host's rules, against 4,642 on the
general RAM host before them, which is also built with another allocator). On the WAN link the claim medians are as before: 107 ms for `RibbitClaimStore` and
214 ms for his `PostgresClaimStore` on the same memory.

## What the study does not cover

- **A winner no SARA present ever saw.** A SARA is waited for if some SARA that is present saw it in the memory
  within a ticket's life. A SARA that joined, won and left while every SARA now present was itself away is not
  known to any of them, and a ticket it consumed could be claimed again after a replacement. The signed receipt
  that closes the provenance case does not close this one. What closes it is knowing who was in a memory from
  something that outlives it. It is not built and not tested here.
- **Separate machines.** Every process is on one machine; the links are simulated.
- **The election under a real partition.** The two-memories case stands in for it. `fdf-app` electing on both
  sides of a real partition is not run.
- **The SQL adapter.** The study runs `RibbitClaimStore`. His `PostgresClaimStore` on the same cells is covered by
  his 31 tests and the race, not by the injections.
