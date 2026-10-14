# Croakus normative-consistency audit — 2026-10-07

Test applied to every candidate sentence: could a competent engineer read it in isolation and infer a
requirement that contradicts Part 0 / Specification A (Ribbit), B (FrogNet) or C (FD&F)?

Method: every sentence outside Part 0, Spec A and FD&F (62-68) that touches the shared space's vocabulary,
consistency/history, locality, the store, notification, or the wire: 603 candidates, read in four groups.
Nothing in the generator was changed. Chapter numbers are the rebased ones.

## 1. Part 0 contradicts itself on what FNWP is  (decide; then one wording everywhere)

| Where | Says |
|---|---|
| 0.3 | FNWP "is the dedicated persistent socket between a machine and the RAM manager" |
| 1 (stack box) | "the persistent socket/session carrying Ribbit operations between a machine and the RAM manager" |
| Glossary, FNWP | "used between a participant and the service endpoint selected by the transport" |
| 13 | "FD&F ... each participant's FNWP connection terminates at the RAM manager rather than at another participant" |
| 27, 38, App F | FNWP-1 frames between a node and any peer node (getHosts, echo, sensor posts to a peer) |

Under FrogNet, semantic HTTP rides FNWP node-to-node, not only to the Region's host. Read alone, 0.3 makes
FrogNet's own wire non-conforming. Proposal: make the glossary wording the definition (0.3 and ch.1):
"the persistent framed session between a participant and the endpoint the transport selects; under FD&F
that endpoint is always the RAM manager."
Needs your ruling: your 2026-10-07 statement "FNWP is the dedicated socket to the RAM manager" was made about
FD&F. If it is meant for all transports, ch.13/27/38 and Appendix F need scoping instead.

## 2. High — Part XVIII (old Part XVII, restored verbatim) contradicts Part 0

| Ch | Sentence | Conflicts with | Proposed fix |
|---|---|---|---|
| 69 | "That is not an analogy for what FrogNet does. That is what FrogNet does." + table "one FrogNet, many nodes / shared address space = FrogNet Memory" | 0.4: FrogNet is not the owner of the programming model | "...what Ribbit does." Table column "one Region, many participants / a Ribbit Region" |
| 70 | Labelled ARCHITECTURAL: the surface "can be lifted off" FrogNet as a future possibility | 0.4/0.6 and Spec C: that separation is the architecture, run over FD&F | Relabel DEMONSTRATED; point at Spec A/C; keep the "what does not survive" and federation text |
| 70 | "if memory holds only current values and never history, a federation never has to reconcile a past" | R.1/R.5: history policy is per Region | "if a Region holds only current values (FrogNet's default policy) ..." |
| 71 | PARTIALLY DEMONSTRATED; "the proof would be a second backing store" | 0.5: the RAM manager persists to a plain disk file, not MySQL — the second substrate exists | Relabel DEMONSTRATED; one sentence naming the C++ RAM host's disk file |
| 71 | architectural list includes "current value only, no history" | R.1/R.5 | move that line to "FrogNet default Region policy" |
| 72 | "the reference implementation stays closed while the specification opens" | GPL-2.0 release; the Guild panel two paragraphs later says download it under GPL v2 | delete that clause |

## 3. Medium — FrogNet-Region facts that read as universal Ribbit rules

| Ch | Sentence | Conflicts with | Proposed fix |
|---|---|---|---|
| 16 | "The shared scratchpad lives at one address: databasehost.frognet." | R.4 multiple Regions; Spec C | "On a FrogNet, the default Region lives at one address: ..." |
| 16 | "A tuple holds one value." / "The store performs no consistency checking ..." | R.1, R.5 | "In FrogNet's default Region a tuple holds one value." / "This store ..." |
| 17 | "databasehost.frognet is the one your applications use ..." | Spec A: a Region is reached through the transport contract | "On a FrogNet, databasehost.frognet is the one applications use ..." |
| 29 | "a store designed to hold only the present" | R.1 | "FrogNet's default Region, designed to hold only the present" |
| 35f | "What put() gives you today is last-write-wins against the three-coordinate key ..." | R.2 (ownership stronger than last-writer-wins) | "What FrogNet's put() gives you ..." |
| 41 | "Memory holds current state and never history." / "'current state, never history'" / "No merge, no conflict, no reconciliation pass." | R.5 (a ledger may choose otherwise) | scope each to "the FrogNet Region the simulator models" |
| 41 | "The claim FrogNet actually makes is about shared memory ..." | 0.4 | "The claim this chapter tests is about the FrogNet-hosted Region ..." |
| 26 | "persisted so a power blink does not cost the network its shared memory" (about the template cache) | R.7: representation state is not Region state | "...does not cost the network its learned templates" |

## 4. Low — vocabulary or currency, no contradictory requirement

- 0.3 "Primed dictionaries may make the first exchange compact as well." FD&F measurement (7 Oct) found the
  first message of each instance carries all its values and priming made no measurable byte difference.
- 0.6 and 67 evidence predate the end of 7 October (flat send 87-88 bytes, -23%; C++ byte-identical to Python
  BLDC-1 on 605 sends, 79.8 bytes; move 3/3 in 73-182 ms; discovery/conformance/self-registration done;
  name-only joining still open). Not normative; stale as data.
- 0.7 "The next revision pass will continue moving those chapters ..." — a promise that should go once frozen.
- 1a and 35f use memset/memget/memwatch pseudocode; R.3 uses write/read/hold. Two vocabularies for one surface.
- Part XV1/2 uses "UnREST" unscoped (lead, and two rows of the 56 table) where the rest of the book now says Ribbit.
- WATCH panel titles say "FrogNet Memory" — they name existing videos; change with the re-recording.

## Read and left alone

Sentences already scoped by "historical", "FrogNet-hosted", "in FrogNet", "on a FrogNet node", or by being
worked examples of FrogNet machinery (discovery, the walk, elections, broker, tunnels, hostReset, port-80
interception, Monitor, Communicator, handlers). Notification statements are consistent: ch.61 states held reads
as state waits, matching R.3; "there is no notification protocol here" in ch.30 is scoped to hostReset.
