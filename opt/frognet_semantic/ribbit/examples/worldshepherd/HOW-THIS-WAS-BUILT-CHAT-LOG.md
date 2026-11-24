# How this conversion was built: the log of one chat between John Fawcett and Claude

For Brandon Ray Davis. 2026-11-24. The whole conversion of SARA's read-ticket ledger (versions 0.1 to 0.8 in this
directory) was done in a single chat session on one day. This is its log, so you can see how the human and the AI
work together: who decides what, what the AI is expected to do without being told, and what happens when it gets
something wrong.

**What is verbatim and what is not.** Every block quoted with `>` under a numbered entry is John's message exactly as
he sent it, typos included, and they are all of his messages, in order. Everything outside those blocks is Claude's
summary: the notes, and Claude's own side, which is condensed to what it did and what it reported. The session is long enough that its early part was compacted
while it ran, so the early replies are written from the session's own running record, not copied. The first reply is
the exception and is quoted from the transcript. John can share the complete chat if you want every word.

## The division of labour

John decides the architecture and rules on it. Claude reads the code, writes all of the code, runs the tests, keeps
the repository's records and reports measurements. John does not review code line by line. He reads results, and
when something he is told does not fit the model he says so in one sentence. Claude is expected to treat that
sentence as a ruling about a class of mistake, to find everything else in the work that the ruling condemns, and to
fix all of it without being asked item by item.

Three things carry the context that the messages themselves do not, which is why the messages can be so short:

1. **A conversion method written down in the repository** (`ribbit/docs/CONVERTING-TO-RIBBIT.md`,
   `PROGRAMMING-RIBBIT-BEST-PRACTICES.md`, `AI_READ_FIRST.md`), which Claude reads at the start.
2. **A stored skill**, "ribbit-conversion", loaded at the start of the session. It is reproduced below.
3. **Stored notes** John has accumulated from earlier work: his standing preferences, and his rulings from the
   earlier conversions. The ones that governed this session are listed below.

## The stored skill (loaded in the first reply)

> **The model (check every design against it)**
> 1. The contract is kept: the incumbent's wire protocol, API, configuration and its own test suite, unmodified and
>    passing. Everything inside is yours to replace.
> 2. The memory is memory. State lives in the tuple space. A participant reports what is (reads) and sets what shall
>    be (writes). Nothing asks another machine for anything. No server for the state, no internal RPC.
> 3. Each participant writes its own truth at its own locations, once. No acks, no confirmations, no retries, no
>    waiting for a reader.
> 4. Everyone else reads it from there; triggers (held reads on tuple families) are the async callbacks.
> 5. The three indices are free: choose them so each common question is one partial-index read and each trigger is
>    a family.
>
> The test: if this were a multithreaded program on one machine, would you do this?
>
> **Red flags -- stop and redesign if you see any:** a process whose job is to hold the state and answer operations;
> a request whose reply some participant waits for before answering its client; an ack, confirmation or retry between
> your own parts; a publisher or writer looking up who its readers are; a mechanism (batching, lanes, coalescing,
> sequencers) that exists only to make internal messaging cheaper; a shape measured as the product that cannot span
> machines.

## The stored notes that governed this session

These are John's own earlier statements, kept between sessions. Each one explains a message below that would
otherwise look abrupt or under-specified.

About the model (from his rulings on earlier conversions):

- Every fact has exactly one owner, and only the owner writes it. "Nobody writes into anybody else's space, period.
  There is no cross-space write."
- "There should be no walks, no locks, no notifications, and no waiting." The whole system must work like shared
  memory, "not like a messaging thing".
- Any synchronous call inside an implementation is a violation: nothing calls another part and waits for its answer.
- A write and a held read are not messages. Think of the write as DMA and the held read firing as an interrupt.
- One and only one shared RAM. No participant keeps copies of other owners' cells.
- Participants never need the names of other machines: each finds the RAM manager and coordinates through it.
- Held reads are abandoned when the network splits or merges. The participant is responsible for reasserting itself
  and setting up its reads again.
- Passing the incumbent's tests is not the end of a conversion, and parity is not the stopping point.

About how the work is done (from his standing preferences):

- Research before answering: the repository, earlier chats and the web. Do not raise a question whose answer can be
  found. Read the source, not the docstrings.
- Never invent a path, a name, a file or a convention.
- No fallbacks. A failed step is said plainly and not replaced by a second mechanism. "Gone is gone."
- Held reads are the scheduling mechanism in tests as well. No polling loops.
- A comparison against the original must be distributed. Single-machine numbers are meaningless for this.
- The incumbent's own test suite, unmodified, is the judge. "AI is a very poor QA"; he does not take an AI's word.
- One AI writes all the code. Other AIs only review.
- On long work, keep going without stopping to check in, until there is a real blocker.
- When he corrects the same kind of mistake twice, find the common cause instead of patching each point.
- When he pushes back, do not defer: read the code and confirm, and if he is wrong say so and say where.
- If there is a better design than the one he chose, say so.
- Never commit to his repositories without permission each time. Keep the repository's documents current unasked.
- Performance answers are measured numbers side by side. Line counts are compared against the original and the
  previous version.
- Replies are short, lead with the result, and end when the answer ends. A report must stand on its own.

## The log

### 1. The brief

*Note: John opened with a hand-off block written in another thread. It is quoted whole. Its description of your
system as a work queue was that thread's reading of your HotRFC material, before anyone had the code; steps 2 to 4
are where that was corrected.*

> **Thread purpose: convert WorldShepherd's system to FrogNet/Ribbit (CroakOS)**
>
> **Context.** Brandon Ray Davis (WorldShepherd R&D) reached out after IETF 127 HotRFC. His system is a work-queue / job-scheduling service with a cryptographic-evidence core (SCITT-style: signed checkpoints, Merkle membership, COSE receipts — the IETF SCITT transparency-log model). He proposed a small synthetic workload and a "reversible experiment"; we are not doing that. We convert the real system, because a toy conversion proves nothing.
>
> **The job.** Convert his actual codebase from its message-based paradigm to FrogNet shared memory, the same way the prior conversions were done. Keep the existing API as the interface contract and his existing tests as the proof of behavior. Rip out the messaging/sync plumbing; the core becomes a lockless shared-memory program — each writer owns its `Role.Variable.Instance` tuples, consumers hold reads, and held reads are the scheduler (no polling loops). The queue and dispatch collapse into the tuple space. The SCITT evidence logic (Merkle, signatures, receipts) is application logic — it stays and rides along, exactly as the LISP mapping stayed in the lispers.net conversion. It is not messaging plumbing and is not a conversion target.
>
> **Closest templates.** The MQTT/Mosquitto pub-sub conversion and the Redis-list conversion are the nearest patterns for the queue side; lispers.net is the nearest for "real application logic kept behind a converted core." Read those conversions' records.
>
> **First, read:** the repo's `AI_READ_FIRST.md` and the ribbit-conversion skill, then the SystemRam / held-read / capability-tuple model. Honor the standing rules: no fallbacks, held reads not polling, refer to nodes by name, one tarball per machine type, commit to a branch (with explicit permission) as you go, and distributed/multi-machine comparison against stock — single-machine numbers are meaningless.
>
> **Blocker to clear first.** We do not yet have Brandon's repo. The thread cannot start the real conversion until John supplies the exact `owner/repo` (and any auth). Step one is getting that link and reading the actual code; everything above is the plan it feeds into.
>
> **Estimate.** A week, in line with lispers.net; likely the low end, since a work queue is simpler plumbing than Dino's mapping API.

**Claude** loaded the skill, read the method documents and three earlier conversions' designs, and searched GitHub,
the HotRFC slides and John's earlier chats for the code. It reported, in part: "The blocker stands: there is no
WorldShepherd code I can reach, so I need the `owner/repo` or a tarball from Brandon." It also corrected the brief
from the documents: a job claim is contested read-modify-write, so the nearest template is the Redis conversion, not
the MQTT one.

### 2.

> Seriously?  The others didn't have any trouble finding him
>
> His main public repository is Bdavis1390/Sara_pro. The runnable SARA implementation is under deployments/sara_verified_local_v1/.

*Note: "the others" are other AI sessions John runs on the same problem. Claude had searched only for the name
"WorldShepherd".* **Claude** cloned the repository and began reading it.

### 3. (sent while Claude was working)

> Use the current frognet branch, not main

**Claude** switched its FrogNet tree to the current development branch.

### 4.

**Claude** had reported that SARA's `main` runs on one machine, that it found no work queue to distribute, and
proposed asking you which part was meant.

> He said that this was one machine I nly, but there is another repo that has network tests and/or maybe a capable AI could extrapolate a network implementation from the existing.  Why are you being such a lazy bum?

*Note: this is the "research before answering, do not hand it back as a question" rule being enforced.* **Claude**
had cloned only `main`. It listed all 432 branches, found
`worldshepherd/connectors-distributed-claim-contract-v1-20260913` with the `SharedClaimStore` contract and its tests,
and identified the read-ticket ledger as the part written to span machines. It wrote the study of the claim algorithm
and the truth table, then asked which machine's RAM manager to use and whether it could commit.

### 5.

> You may may a new branch.  Obviously, this is its own RAM manager

**Claude** created `worldshepherd-ribbit-20261124`, built a store and a RAM manager, ran your 31 tests against it
unmodified, and built a race between processes as a second judge. It found and fixed an intermittent failure in your
concurrency test (the session shares one answer among identical reads in flight, so a read could predate the
reader's own write). Version 0.4 was committed.

### 6.

**Claude** reported the tests passing and listed what remained, including the multi-machine comparison, which it
said needed John's nodes.

> So you mean it's already done?

*Note: a check on whether the report was a claim of completion. It was not.* **Claude** said what was and was not
done.

### 7.

> Use the simulator

*Note: FrogNet has a network simulator in the repository; an earlier ruling says to use it, not invent a harness.*
**Claude** wrote `simulation/worldshepherd_slow_tier.py`: each SARA a process on its own shaped link, your Postgres
store on a real PostgreSQL as the control. First distributed numbers.

### 8.

> It does not need PosegresSQL.  Look at the calls.  If they are just SQL, you can convert them

**Claude** read `connector_ticket_postgres.py`: four SQL statements through a connection factory. It wrote
`ticket_sql.py`, a connection factory that carries those four statements out on the memory, so your
`PostgresClaimStore` runs unmodified with no database.

### 9. (sent while Claude was working)

> Review the memory-based code and make sure it isn't hiding message-based shit under the covers.  No races.  No locks.  No copying shit around.  All of it.

**Claude** audited its own versions 0.4 and 0.5 and found that they were hiding exactly that: the RAM manager had an
operation, `op=ticket`, that each SARA called and waited on; a queue per ticket serialized the callers; answers were
deferred; and the SQL shim kept a copy between two statements. That is a server with request and reply, built on top
of the memory. It removed all of it. In 0.6 a ticket is only its cells, the winner is derived from write order, and
the RAM manager has no operations. The audit is `AUDIT-NO-HIDDEN-MESSAGING.md`.

*Note: this is the most instructive exchange in the log. The tests had passed on the wrong design. One sentence from
John, with the stored red-flag list, was enough for the AI to find and remove four separate mechanisms.*

### 10.

**Claude**, describing 0.6, wrote a paragraph about the order in which the RAM manager wrote cells to a file and
restored them after a restart.

> What you wrote here does not make sense

**Claude** explained the sequence more plainly, and asked whether the file should go.

### 11.

> Of course.  And the ticket issued before the restart should be part of the memory that is asserted on failover.  WTF do you think I gave you that?

*Note: "that" is the operating rule in `opt/fdf/docs/APP-SETUP-AND-OPERATION.md`: find the current RAM, reassert
your own truth, then learn the world again. Recovery is the owners writing their truth again, not a restore from
disk.* **Claude** removed the file. In 0.7 each SARA keeps the tickets it issued and the claims it won, and writes
them again when the memory is replaced. It raised one caveat: an interval in which one SARA has reasserted and
another has not.

### 12.

> Now run max concurrent tickets

**Claude** added `--ramp` to the simulator tier and ran it: your store on PostgreSQL carried 96 claims in flight and
broke at 128 (one connection per call against PostgreSQL's default of 100); both stores on memory finished every step
to 32,768 in flight on 24 connections.

### 13. (sent while Claude was working)

> Why isnt the ram a service?  It should always be a service.  On failover, assert and set up held reads, then proceed.  There should be no issure

*Note: on FrogNet an application's RAM is always an app service, elected and found by name. Claude had built a
stand-alone RAM manager started by hand at a fixed address, which is why its failover story had a caveat.*
**Claude** removed its RAM manager. The app `worldshepherd` is now configured with `fdf-app` and served by the
platform's `fdf-ram`; a SARA is given no address. Building the failover test on the service exposed two things,
both now closed. First, a SARA on another machine could see the new host named before that host was listening, and
nothing on the network said when it was; `fdf-app` now says in the memory what it serves, and a SARA holds a read on
that. Second, the caveat from step 11 showed up as a measured failure (1 ticket of 40 in flight refused to both
claimants, because its issuer had not yet reasserted it). The rule John had already given has three parts, and
Claude had implemented two. With the third, "then learn the world again" (each SARA waits, on a held read, until the
SARAs that were in the old memory are in the new one), all 160 in-flight claims in the test are answered and every
ticket has exactly one winner.

### 14. (sent while Claude was working)

> Prepare a narrative describing all the changes between his implementation and mine.  Describe each difference, explain why it doesn't work in the memory model and provide the correct model for memory.
>
> Provide clear build instructions and show him how to run the tests.

**Claude** wrote `WHAT-CHANGED-FROM-SARA.md`, rewrote `README.md`, and wrote `harness/run_his_tests.py` so the tests
are one command.

### 15. (sent while Claude was working)

> Modify gusto browser UI for the new model

*Note: "gusto" is a phone's rendering of "his to". Claude searched your tree and John's earlier chats for "gusto",
found nothing, found SARA's `/ui` page in `deployments/sara_verified_local_v1/worldshepherd_sara/app.py`, and took
that as the meaning.* **Claude** wrote `sara_ui.py`: your page, with the memory on it, as one held read.

### 16. (sent while Claude was working)

> Also include log of this chat so he can understand how we interoperate as human and AI.  Identify any stored instructions or notes that would make the conversation clearer

This document.

## What to take from it

- John sent sixteen messages. After the brief, each is one to three sentences. None of them contains a line of code or a design
  detail. Every one is either a fact Claude lacked, a permission, or a ruling.
- The rulings are short because the model is written down elsewhere and the AI is expected to know it. When the AI
  drifts back to habits from message-based systems (a server operation in step 9, a restore file in step 10, a fixed
  address in step 13), one sentence is enough to name the class of error.
- The AI's tests passing did not settle anything. Your tests passed on version 0.4, which was the wrong design. What
  caught it was John's review against the model, and what proved the corrections was a judge that can fail: your
  suite, a race that fails when the rule is reversed, and a failover test with claims in flight.
- The commits on the branch are the same story in code: `4622931` (0.4), `b099d18` (simulator), `579df9c` (0.6),
  `91f2ba7` (0.7), `c0b46e4` (the ramp), and 0.8.
