<!-- Generated from the site page spec-10-unrest.html (https://fawcettinnovations.com/spec-10-unrest.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §10

# UnREST — the programming model.

The programming model over [FrogNet Memory (§9)](spec-09-memory.md). Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\]. Worked code: [unrest-examples.html](https://fawcettinnovations.com/unrest-examples.html).

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

Registering your own wire protocol in two functions

## Current implementation clauses: October 12, 2026

## Participants

A participant holds the state it needs, performs its work and publishes the result. Give long participants clearer boundaries rather than recreating a private messaging framework.

## Held views

Read once, hold changes and swap a new snapshot into the query path. An applied cell lets tests and other participants observe progress without private wait flags or sleeps.

## Claims and Wants

Use separate contender claim cells and write-order arithmetic. A Want is published state whose result address can be held. Foreign state has one participant publishing it with provenance.

## Binding and host policy

The vendor can reshape schema and host operations for the domain. An API extension may perform a query or aggregate in the host. Its consistency, permissions and recovery remain explicit contracts.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the local path remains separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Model](#u1)
2.  [2The exchange, before and after](#u2)
3.  [3Operations](#u3)
4.  [3aObservation does not imply notification](#u3a)
5.  [3bDo not rebuild the model you left](#u3b)
6.  [4Coupling and lifetime](#u4)
7.  [5What the application no longer writes](#u5)
8.  [6Ownership rules](#u6)
9.  [7Handlers](#u7)
10. [7aThe reassignment callback](#u7a)
11. [8Concurrency and completion](#u8)
12. [9Non-goals](#u9)
13. [10References](#u10)

## 1. Model

Croakus: [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory), [§39a](https://fawcettinnovations.com/magnum-croakus.html#39a-one-program-many-threads)

FrogNet Memory is the architecture: one addressable store per connected pond, implemented by `api.php`, read and written by every node. **UnREST is the programming model over it.** The two are separately specified and MUST NOT be conflated: §9 states what the store does, §10 states what a program does with it.

**The store is reached over HTTP, and that is not a contradiction.** `api.php` is an HTTP endpoint; a `memset` or `memget` becomes a request to it. The claim of this specification is not that HTTP is absent from the machine — it is that **the application no longer writes the choreography**: no endpoint chosen, no retry policy, no reconnect, no serialisation contract with a peer, no failure branch for a peer that did not answer. What survives underneath is transport, and it is deliberately ordinary. The same relation holds one layer down: a C program does not stop producing machine instructions, and the claim for C was never that it did.

An implementation MAY carry the store interface over something other than HTTP. Nothing in §9 or §10 depends on the choice, and no application-visible semantics change with it.

The model is Linda — Gelernter and Carriero, Yale, 1985 — in which independent processes never address each other. A producer writes a value into an associative memory without knowing who will read it; a consumer reads by pattern without knowing who wrote it. Linda matches arbitrary tuples and says nothing about field count or meaning; the three coordinates FrogNet uses are FrogNet's indexing convention (§9.2), not a requirement of the model.

The operative mental frame is **one program with many threads over shared memory**, whose threads happen to be on different machines. Threads do not correspond. Each writes what only it can know; each reads all of it; the answer is a pure function of that state. An implementer who finds themselves asking how two parties *agree* has left the model.

## 2. The exchange, before and after

Croakus: [§34](https://fawcettinnovations.com/magnum-croakus.html#34-rest-and-unrest-side-by-side)

The whole of this section is the difference in step count, and the first thing to notice is the symmetry. **Under REST the producer and the consumer are the same sequence.** Both form a message, locate a target, transit, dispatch, execute, return, read, and tear down. Only the payload differs — the producer's carries a value, the consumer's carries a query. Publishing and observing cost the identical eleven steps because both are implemented as the same thing: a call to somebody.

``` code
OLD WAY   — producer and consumer, identically

    form REST message           the payload is the only difference:
    find the target             a producer carries a value,
    send the message            a consumer carries a query
    receive message
    find executable
    run
    get response
    return response
    read reply
    close socket
    continue

NEW WAY   — and here they finally differ

    Producer    push tuple to shared memory

    Consumer    read tuple space
```

Every step above the line exists to move a value between two processes that cannot see each other's memory. None of them describes the work the application is doing. The model's asymmetry appears only below the line: a write and a read are different operations, unconnected in time and unaware of each other, and neither is a call to anybody.

A conforming implementation MUST expose write and read as the whole of the coordination surface; an application MUST NOT be required to locate a peer, establish a session, or await a reply in order to publish or observe a value.

The steps do not move elsewhere — there is no equivalent sequence running underneath. Locating a peer is absent because nothing is addressed to a peer. Waiting for a reply is absent because a write has no reply and a read has no counterparty. What remains below the line is the substrate's own work: framing (§11), routing (§5), and the single serialization point (§9).

## 3. Operations

The surface is three operations. Their semantics are normative in §9 and are summarised here only as they appear to the caller.

| Operation        | Caller states                    | Returns                                                                                                                                                                                                                                                                             |
|------------------|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `put` (write)    | the three coordinates, the value | completion; no addressee, no acknowledgement from any reader                                                                                                                                                                                                                        |
| `get` (read)     | service and var                  | every instance of that variable across scopes — each node’s capability row, say — with timestamps                                                                                                                                                                                   |
| `get_all` (read) | service                          | every variable for that service, as rows carrying var, scope, name, address, value, envelope timestamp and age. A freshness bound is passed to the store and filtered there on the row’s own update time \[ENVELOPE_TS_V1\]; a timestamp inside the payload is not consulted (§9.4) |

A write MUST be a single atomic upsert against the tuple key and MUST NOT require a prior read (§9.3). A read MUST NOT block on a remote condition (§9.4). **`get_all` is architecturally load-bearing, not a convenience.** It is the operation that makes independent producers usable: where writers do not coordinate, a reader needs the whole set in order to interpret it, and a single-key read cannot express that. **It is not a snapshot.** The rows it returns were not read atomically and no cross-value atomicity exists (§9.3); two rows may reflect moments milliseconds apart, and a row may change between the read and the use of it. A consumer that requires a coherent instant across several values has a requirement this model does not meet and MUST NOT infer one from `get_all`. An implementation offering only `put` and a single-key `get` would leave applications no way to work across independent truths, and they would close the gap by coordinating writers instead (§10.3b). There is no fourth operation: there is no subscribe, no publish, no notification of a value changing, and no delivery. (A callback on *role reassignment* exists and is a topology event, not a data one — §10.7.) How a program observes change is its own choice (§10.3a); this specification defines no observation mechanism and requires none.

The three coordinates as the implementation names them are `service`, `var` and `scope`; the scope qualifies the variable name, and the service is the tuple's type. Scopes are constructed, not free-form:

| Scope   | Form               | For                                                                                                                                                                                                                                                                                                  |
|---------|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| host    | `host:<ip>:<pid>`  | A long-running service whose tuple should be reaped once that instance stops refreshing it. The pid makes it unique per instance and self-expiring: a new instance writes a new key and the dead one stops asserting.                                                                                |
| node    | `host:<ip>`        | A periodic refresh writer whose tuple must outlive any single process — one upserted row per host, not a fresh key every cycle.                                                                                                                                                                      |
| role    | `host:<ip>:<role>` | Capability tuples. A node writing capability under two roles MUST qualify the scope with the role: `SensorName` does not carry the type, so a bare node scope gives both roles one name; under the earlier name-only key that was one row whose role flip-flopped as each write overwrote the other. |
| session | `session:<id>`     | Per-call state                                                                                                                                                                                                                                                                                       |

**A write MUST declare whether the writing process owns the tuple.** An owned write is registered for cleanup at process exit, so a service's ephemeral coordination state — presence, call signalling — is normally removed when the service stops. **That cleanup is a courtesy, not the guarantee** — the rule and its rationale are §2e. For a caller it means one thing: **an owned tuple MUST NOT be treated as removed at the instant its writer stops.** An unowned write is fire-and-forget and ages out by staleness instead. A periodic or one-shot registrar MUST write unowned: arming cleanup on a short-lived process deletes the tuple milliseconds later, so its writes never persist — which is exactly the failure that left an election with no capability rows to read.

At the store, the tuple address is the unique key **Name + Type + Address** \[TUPLE_ADDRESS_V1\]. All three MUST be present in every write — empty where the caller supplies nothing — because the conflict clause that makes the write an upsert can only fire when the whole key is in the statement.

A write MUST be atomic resolve-or-create in one statement \[ATOMIC_UPSERT_V1\]. An implementation MUST NOT read to resolve and then insert: that races under concurrent writes from several peers and produces duplicate-key failures rather than the later write winning.

Underneath, the operations are ordinary HTTP against `api.php` on whichever host the address resolves to — the control host for coordination tuples, the elected host for durable data (§9.2.1). The wire form is stable and callable directly:

``` code
POST <host>.frognet/api.php?entity=sensor_data&action=upsert_by_name
  { "SensorName":"SD:move.host:10.1.1.1", "SensorType":"tictactoe",
    "SensorAddress":"10.1.1.1",
    "jsonData":{ "table":"kitchen-1", "op":"move", "seat":"X",
                  "cell":8, "intent_seq":4 } }
```

Verified in `var/www/html/api.php`. Both parameters are required; `entity` MUST be one of the eleven below and `action` one of the eleven.

| Entity             | Table         | Primary key        |
|--------------------|---------------|--------------------|
| `sensors`          | Sensor        | SensorID           |
| `sensor_data`      | SensorData    | SensorID           |
| `history`          | History       | HistoryID          |
| `known_frognets`   | KnownFrogNet  | NetworkName        |
| `actuators`        | Actuator      | ActuatorID         |
| `iahosts`          | AIHost        | AIHostID           |
| `well_known_sites` | WellKnownSite | SiteID             |
| `users`            | User          | CallSign           |
| `teams`            | Team          | TeamName           |
| `team_members`     | TeamMember    | TeamName, TeamUser |
| `messages`         | Message       | messageID          |

| Action           | Methods      | Notes                                                                                                                                                                   |
|------------------|--------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `values`         | GET          | `sensors` only. The read primitive.                                                                                                                                     |
| `list`           | GET          | Whole-entity read                                                                                                                                                       |
| `get`            | GET          | By primary key; every PK component required                                                                                                                             |
| `create`         | POST         | Idempotent for `sensors`, `known_frognets` and `sensor_data` only \[IDEMPOTENT_CREATE_V1\]; strict elsewhere — POST means expect a new row and conflict is a real error |
| `update`         | PUT, POST    | —                                                                                                                                                                       |
| `upsert`         | POST, PUT    | The write primitive                                                                                                                                                     |
| `upsert_by_name` | POST, PUT    | Resolve-or-create by `SensorName`; requires `SensorName`; `FrogID` is generated when absent                                                                             |
| `upsert_batch`   | POST, PUT    | Requires an `items` array. One round trip for N writes.                                                                                                                 |
| `delete`         | DELETE, POST | —                                                                                                                                                                       |
| `append`         | POST, PUT    | `history` only. Adds one row.                                                                                                                                           |
| `write_all`      | POST, PUT    | `history` only. Requires a `rows` array; replaces the table in one transaction unless `truncate` is false.                                                              |

Read filters on `sensors&action=values`: any whitelisted column as an equality filter, a `__like` suffix for pattern match, `order` (whitelisted columns only), `limit` (1–5000; `list` caps at 1000), `fresh_s=N` for rows written within N seconds, `wait_s=S` with `min_rows=N` to hold the read at the store until N rows match (at most 30 s), and `parse=1` to include decoded JSON as `data`. **`order`, `limit` and `parse` are excluded from the filter set** and, per §11.6, MUST NOT enter the template identity.

Errors are a JSON body `{"error", "detail"}` with the status in the HTTP code:

``` code
400  missing entity or action; unknown action; missing PK; missing
     SensorName for upsert_by_name; no valid fields; empty request
     body; invalid JSON; upsert_batch without an items array;
     write_all without a rows array
404  unknown entity
405  wrong method for the action
500  DB prepare/execute failed; SensorID resolution failed;
     upsert_batch or write_all failed
```

\[NO_EMPTY_BODY_FALLBACK_V1\] An empty body MUST be rejected as 400 and MUST NOT be treated as `{}`. The two are indistinguishable downstream, and every handler then runs with no data and reports success.

## 3a. Observation does not imply notification

FrogNet Memory exposes state. **It does not prescribe how an application decides to observe changes in that state.**

An application may poll, use inexpensive SAME/DIFF observation, install a trigger in the backing store, run a watcher, derive a change from several values, or use any other mechanism its requirements call for. **Change notification is application policy, not a responsibility of the fabric.**

The fabric MUST NOT need to know what applications exist or what changes matter to them. An implementation that adds a value-change primitive has moved application semantics into the substrate, and the coupling it removes from one place reappears in another: the fabric now holds a list of who cares about what, which must be maintained, replicated to wherever the role moves, and reconciled when it does not.

This is the same boundary drawn everywhere else in this specification, stated once in the programming model: **FrogNet provides the machine. Applications decide what to compute with it.**

The reassignment callback (§10.7a) is not an exception to this. It reports a change in the machine, not a change in a value, and topology is the fabric’s business precisely because it is not application semantics.

## 3b. Do not rebuild the model you left

An application can be written against this surface and still be a REST application. The operations permit it; the model is defeated by it.

**Programming-model note.** `put`, `get` and `get_all` are not replacements for POST, GET and request/reply. The intended model is **state publication**: a producer writes what it independently knows, under its own identity and scope, without knowing who will consume it. Consumers independently read what they require and apply application semantics to the values they find (§2b).

Each pattern below compiles and runs, and each returns the property it was adopted to avoid.

| The habit                                                                                                       | What it costs                                                                                                                                                                                                                                                                                                                                               |
|-----------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **One key, many writers.** A shared value several nodes update.                                                 | Every truth but the last. Two sensors reporting a temperature are two facts, not one variable; written to one key, one is lost. Written to their own keys and read together, a consumer has both readings, both timestamps and both reporters — and can select, average, weight, or discard, which is application knowledge a substrate could never supply. |
| **Request/reply over memory.** A writes a request tuple, B polls for it, B writes a response, A polls for that. | Two producers, two consumers and a correlation identifier — RPC with worse latency. The coupling §10.4 removes is reintroduced by hand, and A cannot proceed until B exists.                                                                                                                                                                                |
| **A status endpoint.** One node holds the authoritative view and others ask it.                                 | A coordinating server, which the model does not need (§2b), and a single point of failure the substrate had removed.                                                                                                                                                                                                                                        |
| **Tight polling as a substitute for events.**                                                                   | Observation is the reader’s to schedule (§10.3a) and cheap, but polling faster than the value changes spends the ceiling of §17.3 to buy latency the application usually did not need.                                                                                                                                                                      |
| **Write-then-read-back to confirm.**                                                                            | A read-modify-write cycle and its races (§9.3). The write reached the serialization point or failed; the read establishes nothing the return did not.                                                                                                                                                                                                       |
| **Sessions and connection state.** A handle, a lease, or a login held between operations.                       | Producer and consumer lifetimes are independent (§2e). A session couples them again and makes a restart a coordination problem.                                                                                                                                                                                                                             |

**The test: does the pattern require another program to exist, respond, or agree?** If so, the model has been given back what it removes. An application MAY do any of it — nothing prevents it — but it is then paying the substrate’s costs without taking its benefit.

The worked example is [UnREST by example](https://fawcettinnovations.com/unrest-examples.html): a participant writes only its own object, and one writer of record holds the shared result. **Break either and the message-passing world sneaks back in.**

## 4. Coupling and lifetime

**Producer and consumer are decoupled in time and in identity.** Neither learns that the other exists. A value survives the exit of its writer; a reader observes values written before it started. A conforming implementation MUST NOT make either operation contingent on the liveness, reachability, or prior registration of any other participant — which is what makes an offline node a whole network alone rather than a degraded participant.

**Because the memory is directly addressable, no coordinating or resolution server is required. There is no store-and-forward, and no consensus derivation from incomplete data. Each node writes its own truth in its own time, and consumers directly read that data when they require it.**

Each link forces the next. A program names the value, not a service holding it, so nothing stands between the program and the store to be located, connected to, or kept alive. Store-and-forward holds a message until a recipient takes it; nothing here is addressed to a recipient, and a write that cannot reach the store fails rather than queueing (§13.3). Consensus manufactures one answer from participants holding partial views; here every write goes to one store and every read comes from it (§2b, §8.6).

**Contention-free. A write replaces the named value at the store. In modern Ribbit, independent participants own separate addresses and use write-order ids; competing application truths are not reconciled by a last-write-wins merge. No contention.** No writer blocks on another, waits for another, retries after another, or is refused because of another; there is no lock to acquire and therefore nothing to contend for. Two writes to one description both succeed and the later stands. A design with two writers on one key loses *information*, not liveness — a modelling error (§10.3b), not contention.

**Integrity is checked twice, independently, and by neither of them.** Data integrity checking is performed first by the producer prior to commitment, and again independently by the consumer after the value is read. These operations occur asynchronously. The particular algorithm, if any, is the responsibility of the application. A conventional design puts a service in the middle to validate on everyone’s behalf, and that service must then know what every value means; here the producer validates what it measured and the consumer validates what it will act on. An implementation MUST NOT interpose validation of application values in the store or the transport (§2b).

**A whole category of application code is therefore absent rather than delegated:** the server, the coordination code, the discovery code, the registry, the connection management, the retry and reconnect logic, and the database to be provisioned. None of it is deferred to a provider. None of it exists.

**Participants may appear and disappear as the topology changes, with no notification to the application and no recognition by it**, unless the application wants that. A node that leaves stops writing and its values age out (§2e); a node that joins begins writing and its values become visible to any reader that looks. An implementation MUST NOT require an application to acknowledge a membership change in order to keep functioning. An application that does care sees it for free, because arrival and departure are already state: the reference Monitor renders nodes joining and leaving with no membership code in it (§15.3).

## 5. What the application no longer writes

Croakus: [§43](https://fawcettinnovations.com/magnum-croakus.html#43-the-shape-of-the-job)

The change is subtractive. An UnREST application makes four declarations: the shape of the data, the freshness of each field, where authority lives if anywhere, and what a received value means. The following are the substrate's and MUST NOT be reimplemented above it.

| Concern       | Under REST                                      | Under UnREST                                          |
|---------------|-------------------------------------------------|-------------------------------------------------------|
| Addressing    | endpoint per peer, resolved and configured      | three coordinates; no peer is named                   |
| Retry         | application-level, per call                     | substrate; a write is an upsert, a read is idempotent |
| Ordering      | sequence numbers, queues, or acceptance of none | one serialization point (§9.1)                        |
| Deduplication | idempotency keys                                | a key holds a value; a repeat write is the same write |
| Reconnect     | backoff, session re-establishment               | substrate; absence is a read result, not an error     |
| Versioning    | a shape contract owed to named consumers        | no named consumers; the template is the schema (§11)  |
| Compression   | optional, per response, application's job       | infrastructure, automatic, no application change      |

An application that has reimplemented any row of the right column has rebuilt request/response with extra steps, and SHOULD be treated as non-conforming to the model even where it functions.

## 6. Ownership rules

Croakus: [§39a](https://fawcettinnovations.com/magnum-croakus.html#39a-one-program-many-threads), [§43](https://fawcettinnovations.com/magnum-croakus.html#43-the-shape-of-the-job)

Three rules govern what a participant may write. Each was established by a defect that no amount of care on the wrong side could fix.

### 6.1 A fact belongs to whoever can know it \[FOUR_TUPLES_V1\]

A participant MUST write only values it alone stands in a position to know, scoped to itself, updated in place. A held keyframe is the viewer's fact; a capture rate is the sender's; a socket's state is the relay's. A participant MUST NOT write another participant's value, and MUST NOT write a shared aggregate that several participants would each overwrite. A single row carrying a membership list written by whichever participant wrote last is the canonical violation: a departing member was reinstated by another member's heartbeat, because the row was not the leaver's to write.

### 6.2 A derivation MUST NOT read its own previous output \[ONE_DERIVATION_V1\]

Where several participants must arrive at the same answer, that answer MUST be a pure function over shared rows — no clock, no instance state, no I/O. Every participant runs the same function over the same rows and lands on the same result without being told, which is what makes agreement unnecessary rather than merely cheap. A function that consults its own last answer is a controller with memory, and two controllers with memory diverge. Where a fact must persist across derivations it MUST be written to the store as a row, so that every participant derives the same value and none keeps a private history. \[A_RATE_THAT_FAILED_IS_NOT_A_CANDIDATE_V1\]

### 6.3 Absence is not a measurement

A reader MUST distinguish "the producer wrote nothing" from "this reader is slow". Treating absence as a measurement of the reader's own condition produces a ratchet: the producer sheds, the consumer reports worse, the derived rate drops, the producer sheds more. Absence MUST be reported as absence and MUST NOT be substituted into a derivation as a value.

### 6.4 Authority

Where a decision requires an order across values — legality, seating, turn-taking — that decision MUST be routed through an elected role (§8) rather than inferred from the store. The role is the one writer of record for the values it owns and MUST ignore writes to those values from any other participant. A participant states intent in a value that is its own; the role reads intent and writes outcome. A reader that never writes is a spectator by construction, not by permission.

## 7. Handlers

Croakus: [§28](https://fawcettinnovations.com/magnum-croakus.html#28-the-object-oriented-handler), [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory)

**There are three slots, and a handler fills only the ones its job requires.**

| Slot               | Question                                   | Specified in |
|--------------------|--------------------------------------------|--------------|
| **codec slot**     | how do you compress?                       | §11          |
| **election slot**  | are you fit to hold a role?                | **§8.3a**    |
| **lifecycle slot** | how do you announce yourself, and recover? | §6, §13      |

A content handler — JSON, XML, HTML, text — lives almost entirely in the codec slot, names no role, and leaves the other two inert at no cost. A role handler such as `databasehost` is the reverse: no codec work, and real election and lifecycle behaviour. A media handler would fill all three, overriding the codec slot to manage a live stream (§14.2a); the engine carries none today.

**Election belongs to the handler, not to the fabric.** The scoring function for a role is written by whoever defines that role, and roles differ deliberately (§8.3a).

Every exchange on a FrogNet is processed by a handler: an object conforming to one interface with a small number of virtual slots — how to compress, whether it holds a role, how to announce itself. UnREST Core is the default behaviour of that interface. Ordinary content handlers fill the compression slot and inherit do-nothing defaults for the rest, which is why every JSON reply and every sensor read receives SAME/DIFF/FULL treatment and the fanned transport without any subclassing.

``` code
class MyPathway(UnRESTHandler):
    ROLE_NAME = None                       # a content handler, no role
    def learn_request_template(self, req):  ...   # learn the shape
    def extract_request_dynamic(self, req): ...   # pull the values
    def rebuild_reply(self, tpl, vals):     ...   # rebuild far side
```

A handler is registered in one format registry and dispatched uniformly. A handler MUST NOT be called by the application: it is matched and triggered by the substrate. A handler declaring a `ROLE_NAME` participates in election (§8); a handler declaring none is a content handler.

The interface, verified in `core/unrest_handler.py`. A handler MAY override any slot; unoverridden slots inherit do-nothing defaults.

``` code
ROLE_NAME                          None for a content handler
mode                               the role name, or "" (property)
learn_request_template(body)       learn shape from a request
learn_reply_template(body)         learn shape from a reply
extract_request_dynamic(body, tpl) pull the varying values out
extract_reply_dynamic(body, tpl)
rebuild_reply(fragment, values)    reconstruct on the far side
decode_payload(rebuilt_body)       to bytes
score(candidate)                   role handlers: capability scoring
evaluate(hosts_list)               role handlers: election input
advertise(blob, ...)               publish capability; returns early
                                   if ROLE_NAME is unset
hostReset()                        calls advertise again (§6.6)
```

There is **one** implementation of `advertise` in the system and every role receives it identically. The difference between a handler with a lifecycle and one without is not an overridden method — it is a value on the object, and the inherited code returns immediately where `ROLE_NAME` is unset.

**Dispatch is by body inspection, in a fixed order, and Content-Type is a weak hint that never decides alone** (`core/format_registry.py`):

``` code
request    "json" in Content-Type            -> JSON
           "xml"  in Content-Type            -> XML
           "html" in Content-Type AND the body actually looks like HTML
                                             -> HTML
           otherwise: sniff the body         -> json | xml | html | text | raw

reply      application/json OR body starts { or [        -> JSON
           xml header OR  XML
           html hint AND real HTML evidence in the bytes  -> HTML
           any other text/*                               -> TEXT
           looks like readable text                       -> TEXT
           otherwise                                      -> RAW
```

The reply resolver MUST NOT trust `text/html` on its own: PHP endpoints default to that header while returning plain text, and classifying such a reply as HTML learns a template against a document that is not there. An empty or undecodable body classifies `raw`, and a raw body learns no template (§17.7).

## 7a. The reassignment callback

The handler interface carries one notification, and it is the only one in the programming model. An application MAY register a callback that fires after the database-host role has been reassigned.

**An operation in flight when the role moves fails.** It is not retried against the new host, not queued, and not silently completed. An implementation MUST NOT re-aim it: doing so would assert that the two stores are interchangeable, which §2a denies. The caller sees a failure, and that is the correct report.

**The callback carries no data.** It is not a diff, not a list of what was lost, and not an instruction. On receipt the application reasserts its own memory — re-publishing the values it owns under the single-writer discipline of §9.3 — and then reads to see what survived.

**There is no diff because there is nothing to diff against.** The old host held the old state; it is not the store any more, may be unreachable, and may never have existed as far as the new host is concerned. Describing what changed would require keeping a permanent shadow of the previous world against the possibility of a handoff — a second authoritative copy, maintained forever, to narrate a moment that has passed. Every invariant in §2 falls to it.

And it would answer the wrong question. A report can describe what changed at the store; it cannot describe what the application had in flight, what it was about to write, or which of its assumptions the change invalidated — the part that decides what happens next. **The application knows its own world. The store never did.**

So the callback is the smallest useful thing: *look again*. That is affordable because looking is cheap — an unchanged read collapses to twenty-one bytes (§11.10), so re-establishing a working set costs a handful of frames rather than a resynchronisation.

**This is a role event, not a data event.** It does not make FrogNet publish/subscribe and it is not a watch on a value; there is still no notification when a value changes (§10.3, §17.2), and how a program observes change remains its own choice (§10.3a). Topology is the infrastructure’s business, so the infrastructure reports it; data is the application’s business, so the infrastructure does not interpret it.

Source: `core/frognet_host_reset.py`. A process ticks a `HostResetWatcher(resolve_ip, get_modules, …)` in its loop; when the database host resolves to a new address, `tick()` runs `host_reset_all`, which calls `hostReset()` with no arguments on every module `get_modules` returns. The daemon runs one for role capability (`core/role_publish.py`), watching `databasehost_control.frognet`.

## 8. Concurrency and completion

Spec: [§11](README.md#s11)

Calls are not serial. Many requests MAY be in flight on one persistent socket, fanned in and interleaved, each matched home by sequence; replies wake their callers as they land, in any order. A conforming implementation MUST NOT head-of-line-block a completed reply behind an outstanding one.

``` code
writer -> seq 41  REQ_FULL     (dashboard poll)
writer -> seq 42  REQ_REPEAT   (sensor read)
writer -> seq 43  REQ_FULL     (file list)

reader <- seq 42  RESP_SAME    -> sensor caller   (first back)
reader <- seq 43  RESP_DIFF    -> file-list caller
reader <- seq 41  RESP_DIFF    -> dashboard caller
```

Frame layout, the opcode enumeration and sequence-matching rules are normative in §11 and are not restated here.

## 9. Non-goals

**Not pub/sub.** Nothing is published and nothing is delivered. There is no topic, no subscription, and no change notification of any kind — a boundary, not a gap (§10.3a).

**Not RPC.** No procedure is named, invoked, or awaited. A write has no return value from any reader.

**Not an ORM or a schema system.** The store imposes no schema; a key exists when it is first written (§9.2).

**Not transactional.** No atomicity across two keys and no consensus. Composition across keys is the application's problem by design (§9.3).

## 10. References

Specification [§9 (FrogNet Memory)](spec-09-memory.md), [§8 (Election)](README.md#s8), [§11 (FNWP-1 and BLDC-1)](README.md#s11), [§15 (Reference applications)](README.md#s15). Build manual: Croakus [§25](https://fawcettinnovations.com/magnum-croakus.html#25-from-messages-to-memory), [§34](https://fawcettinnovations.com/magnum-croakus.html#34-rest-and-unrest-side-by-side), [§39a](https://fawcettinnovations.com/magnum-croakus.html#39a-one-program-many-threads), [§43](https://fawcettinnovations.com/magnum-croakus.html#43-the-shape-of-the-job). Worked code: [UnREST by example](https://fawcettinnovations.com/unrest-examples.html). Lineage: Gelernter and Carriero, Linda, Yale, 1985.

[← §9 FrogNet Memory](spec-09-memory.md) [Index](README.md) [§11 FNWP-1 and BLDC-1 →](README.md#s11)
