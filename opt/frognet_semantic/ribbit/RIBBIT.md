# Introduction

A century ago, some schlubs got together and figured out how to make a
car work. Gas fills a cylinder and a spark ignites it, driving a piston.
That is the fundamental truth of the internal combustion engine, and
everything from a Rolls-Royce to a Kia follows that model.

We got really, really good at it. We added fuel injection and
turbochargers and sport suspension and learned how to squeeze
astonishing performance out of the same basic idea. There were
alternatives, but nobody seriously questioned what a car fundamentally
was until the technology around it had evolved enough to make something
else practical.

Electric motors weren't new. We had been driving toy cars with them for
years. What changed was that batteries, motors, electronics and
everything around them finally got good enough to make that model work
at scale. Once that happened, we could stop asking how to build a better
internal combustion engine and start asking how a car should work.

A new automotive paradigm was born.

Ribbit challenges the Internet in the same way. The question isn't
whether remote machines need to communicate. Of course they do. The
question, and the opportunity, is how. Networks are fast enough, memory
and compute are cheap enough, and permanent wide-area connections are
ordinary enough that we can finally ask whether the programming model
itself should change.

Ribbit looks at the technology available today and asks what happens if
we go back to first principles. What could we implement if we started
with the capabilities and programming practices we have now, things like
encapsulation and polymorphism, and applied them at a network level?
What would we build if we were designing the Internet's programming
model today instead of continuing to refine the one we inherited?

Ribbit was not born from naivety. Ribbit is the product of someone who
has decades of experience in networking. Enough, in fact, that he, like
the engineer who first realized they could build a practical
mass-production electric vehicle, is ready to help us make our own leap
to the next level.

**Welcome to Ribbit. Where the network is the computer.**

# Part I: What Is Ribbit, and Why Do I Care?

Ribbit creates what can be thought of as an **ephemeral shared-memory
island on the Internet**. The island is owned by the vendor that creates
it, and the vendor decides what the memory means, who gets access to it,
and how applications use it.

The definition of a vendor-supplied memory area includes the name or IP
address and port of the shared-memory host, which may be allocated
dynamically, the server-side definition and adaptation of the interface
API, and a convenience library supplied as a DLL or shared object. The
vendor owns those pieces and distributes the interface to the people who
are supposed to use it.

There doesn't have to be one island per vendor. A vendor might put
everybody into the same one, create one for each user, or create any
confederation of users that makes sense for what they're doing.
Everybody in the same confederation has access to the same real-time
information. The vendor decides what those groups look like and what
each of them is allowed to do.

Underneath all of this, I still need some way to address the memory. On
a normal machine, a variable eventually becomes an address. The machine
knows where the data starts and how to interpret what is there. I need
the network equivalent of that.

Ribbit uses three values to identify a memory location. Together, those
three values form a tuple, and the collection of addressable locations
is called a **tuple space**.\* The value at that location is a JSON bag,
so now I have the same basic pieces I need for memory: I have an
address, I have indirection, and I have something stored at that
address.

The three-part address also gives me something useful that an ordinary
memory address doesn't. I can search on parts of it. A sensor system
might use sensor type, sensor name and location, for example. I can
address one particular sensor, ask for all GPS sensors, or ask for all
sensors at a particular location.

That's the tuple space. But I don't expect applications to spend all day
doing tuple gets and puts. It's the substrate, not the programming
model.

None of these pieces is new. Shared memory isn't new. Tuple spaces
aren't new. Permanent connections aren't new. The question Ribbit asks
is what happens when I make shared state, rather than messages, the
programming surface of the network and use the machinery available today
to make that practical. The experiments in this document are attempts to
find out.

If I'm providing a weather service, for example, I can provide a call
that looks something like `get_weather("Seattle")`. My DLL turns that
into the appropriate operation against my memory region, and my
server-side API turns it into whatever tuple operations are actually
required. Maybe that ultimately becomes a lookup for something like
`forecast/current/Seattle`. The application doesn't care. It asked me
for the weather in Seattle and I gave it the weather in Seattle.

I can do the same thing with `evaluate_soil()`, or
`evaluate_position()`, or whatever else makes sense for the thing I'm
providing. I own the memory region, the schema, the API and the
convenience library, so I get to decide what my interface looks like. If
I change how I represent something underneath, I don't necessarily have
to change any of the applications that use it.

This is not SaaS, and it isn't even particularly close. In a SaaS model,
the vendor runs an application somewhere else and I send requests to it.
I am still programming against a remote service. Ribbit moves the
vendor's abstraction into my application's programming environment. The
vendor supplies a memory region and the code that defines how I interact
with it, and my application maps that shared state alongside everything
else it uses.

I can map a weather region from one vendor, a soil region from another,
GPS information from somebody else, and my own application's memory into
the same process. I'm not orchestrating four SaaS applications. I'm
programming against four independently owned shared-memory regions, each
presenting its own objects and operations, and I can use their real-time
state together. Each vendor is responsible for its own memory region,
schema, server-side API and convenience library.

That does not mean those vendors suddenly share one unprotected address
space. The regions are still independently owned trust boundaries. My
application can use information from several of them at once without
giving one vendor ownership of another vendor's memory. A confederation
means the owner has deliberately decided who belongs in the same region
and what they are allowed to do there. It does not mean everybody who
uses Ribbit gets to write everybody else's memory.

This is where things like encapsulation and polymorphism start moving
out onto the network. The weather service can inherit from the same
baseline Ribbit interface as the soil service, but the two can present
completely different interfaces because they do completely different
things. The application deals with weather and soil. Ribbit deals with
the memory underneath them.

So I can take that weather information and soil information, combine it
with the current state of my pumps and valves, and use all of it to
decide whether to irrigate a field. I don't have to build a special
protocol between all of those systems just because I want to use their
information together.

The programming rule I've been using for this is pretty simple:

**Determine your truth. Write your truth. Read the other truths. Go.**

If I'm a GPS receiver, my location is my truth, so I write it. If I'm a
temperature sensor, the temperature I just measured is my truth, so I
write that. If I'm the program deciding what to do next, I read the
location and the temperature, along with whatever other truths I need,
and make my decision.

Nobody has to ask the GPS receiver where it is. The GPS receiver doesn't
have to know who cares. It knows its own truth and keeps that truth
current.

Obviously information is still going over the network. That's not the
point. The point is that I don't have to program all of the
conversations required to move that information around.

In the conventional model, if machine A needs something machine B knows,
I have to figure out how A gets it from B. Frequently they are both
behind firewalls, so I can't just have A open a connection to B. Now I
need something they can both reach. I need connections to that thing,
some sort of request and response, and then all of the timeout, retry
and failure handling that goes with it. All I wanted to do was get
something B knew over to A, and suddenly I've got a little distributed
system to maintain.

With Ribbit, the vendor has already set up the common memory and both
sides already have a way to reach it. B writes what it knows. A reads
what it needs. All of the machinery underneath that still exists, of
course, but it isn't something every application developer has to invent
again.

The memory can also be as permanent or temporary as the application
needs. I can create a region for a session and throw it away when the
session ends. I can archive it and bring it back the next time. I can
pre-fill it before anybody connects.

A game is a good example. Before I start the game I can load the
players, their inventory, their attributes and everything else I already
know into the memory region. Then I start it. When everybody connects,
the world is already there. I don't need every client to come up and
immediately start exchanging a bunch of information so everybody can
reconstruct it.

If I need permanent storage behind some of that, fine. I can put
permanent storage behind my API. If I don't, I don't. RAM is RAM. Ribbit
doesn't force database semantics onto something just because it happens
to be shared across a network.

The same thing applies when a memory region moves. Ribbit doesn't
magically promise that the contents of the old region are copied to the
new one, reconciled with it, voted on, or anything else. If my
application needs that behavior, then it is my job as the vendor to
define it. If my application doesn't need it, I don't have to pay for
it.

So why do I care about any of this?

Because all of that code I was writing to get A and B to talk to each
other wasn't free. It had to be written, debugged and maintained. More
importantly, it made the application harder to reason about because now
I had all of these independent conversations happening at different
times and failing in different ways.

When I started replacing that model with shared memory, a lot of code
simply went away. That's not a theoretical claim. In the lispers.net
conversion, the audited implementation had **71% fewer lines of code and
47% less measured complexity** while preserving the behavior we were
testing. I've seen reductions in the same general range in the other
conversions, but lispers.net is the useful number here because the audit
ships with the implementation. You don't have to take my word for it.

The easiest example is chat. The client is 471 lines and took a couple
of hours to write. There is no chat server. I already had a generic
shared-memory server, so why would I write another server whose primary
purpose was taking information from one client and handing it to the
others?

And that question turned out to be a lot more interesting than the chat
program.

I've now asked it of Redis, PyTorch, lispers.net and several other
systems. Every time, the question is the same: **how much of this
architecture is actually required by the problem, and how much of it is
required because we decided that distributed programs should communicate
by sending messages to each other?**

That's why I care about Ribbit.

------------------------------------------------------------------------

\* The tuple-space model comes from David Gelernter's work on **Linda**,
developed with Nicholas Carriero at Yale. Linda introduced tuple spaces
as a coordination model in which processes communicate through a shared
collection of tuples. Readers interested in where the idea came from
should start there.

# Part II: How to Code Like a Frog

If you already know how to write an Internet application, you already
know most of the things Ribbit is intended to replace.

Take REST. I want something, so I `GET` it. I want to create something,
so I `POST` it. I want to replace it, so I `PUT` it. I want to change
part of it, so I `PATCH` it. I want it gone, so I `DELETE` it.

There is nothing wrong with REST. It solved a real problem very well.
The problem is that we have been doing this for so long that we tend to
think of those operations as part of the application instead of as the
machinery we built so one program could communicate with another one.

If I would have done a `GET`, what I probably wanted was the current
value of something. Read it. If I would have done a `PUT`, what I
probably wanted was to establish a new current value. If it's my truth,
write it. If I would have done a `PATCH`, I know something new about
state I own. Update it.

`DELETE` doesn't translate quite that simply. HTTP says to remove a
resource, but Ribbit makes me ask what removing it actually means. Maybe
my truth no longer exists, in which case I withdraw it. Maybe it has
been replaced by a new truth, so I write the new one. Maybe it was only
valid for a certain amount of time and I don't need to do anything at
all. I let it expire. The right answer comes from what the state means
to the application, not from the HTTP verb that used to carry the
operation.

`POST` doesn't translate quite as directly either. If what actually
happened is that I learned something, I write what I know. Anybody who
needs it can read it, and if they derive something new from it, they
write their own truth. If I really do need another service to do
something, that service can give me an input space I'm authorized to
write. I put the request there and it owns what happens next. I don't
reach into somebody else's state and change it for them.

That is the first rule of Ribbit: **you only write into your own space,
or into the authorized input space of another service.** The rule I've
been using from there is pretty simple: **Determine your truth. Write
your truth. Read the other truths. Go.**

The rule that you only write your own truth isn't something Ribbit tries
to enforce globally. The vendor owns the memory region and the interface
to it, and the vendor is responsible for deciding who may read or write
what. Ribbit supplies the programming model; the vendor supplies the
policy.

A GPS receiver writes its position. A temperature sensor writes its
temperature. If I need both to make a decision, I read both. If the
result of that decision is mine, I can write it. The GPS doesn't have to
know that I exist, and I don't have to know where the GPS is. I need its
position.

Polling is a good example. If I'm doing a `GET` over and over, what I
really want to know is when something changes. Ribbit can hold the read
until it does. That's what the chat client does with its own buffer.
When something changes, the read completes. There isn't an
application-specific chat server receiving messages and figuring out
where to send them. There is a generic memory service and a client.

Webhooks solve the same problem from the other direction. Instead of me
repeatedly asking whether something changed, you agree to tell me when
it does. That means you have to know where to find me, I have to provide
an endpoint, and we have to decide what happens when you call and I'm
not there. If what I really care about is a truth in shared memory, I
can watch it instead.

Service discovery changes for much the same reason. Usually I don't
actually care where a particular process is. I care about something it
knows or something it can do. In the lispers.net implementation, roles
register themselves as capabilities in memory. The Communicator can find
its media host by reading its tuple. If what I need is the capability, I
can look for the capability instead of finding a machine so I can ask it
whether it has the capability.

Sometimes I really do need to find the machine. Ribbit itself obviously
has to find things. The point is that the application doesn't have to
make location part of the problem when what it really wants is the
information or capability.

The same thing happens with barriers and rendezvous. PyTorch has ranks
that need to know when the other ranks have reached a particular point.
"Rank 2 is ready" is state. Rank 2 can write it. If rank 0 needs
everybody to be ready before it proceeds, it reads the other ranks and
waits until they are. The synchronization requirement is still there.
The separate conversation used to establish that everybody is ready
isn't.

The ownership rule also changes the way I deal with race conditions. If
I have ten users, I don't normally give all ten of them one object and
let everybody update it. I give them ten segments. User 1 writes user 1.
User 2 writes user 2. If somebody needs the state of all ten users, they
read all ten.

There isn't anything clever about this. If two programs never write the
same memory, they can't have a write/write race on it. Can I let two
programs write the same place? Of course. I can also take a pistol and
shoot myself in the foot. I'm not trying to prevent either one. I'm
saying that isn't the Froggish way to design it.

If the application really requires multiple writers, that's an
application-specific decision, and the application owns the
consequences. It may need locking, ordering, transactions or conflict
handling. It may even manage to deadlock itself. Ribbit isn't going to
save me from something I deliberately designed into the application. If
I find myself writing a distributed lock, I don't start by figuring out
how to implement the lock. I start by asking why the hell I have two
writers. Sometimes there will be a good answer. A lot of the time, I
modeled the data wrong.

This also means that several programs can know different things about
the same object without fighting over one master copy of it. Suppose
three participants each know something about Fred. One knows where Fred
is. One knows Fred's current status. One knows whether Fred has paid his
bill. I don't need a `Fred` record that all three of them update.

Each participant writes the thing it actually knows into its own space.
If I need to know everything about Fred, I read those truths and
assemble Fred. If I derive something new about Fred from them, that
result is my truth and I can write it into my space. There doesn't have
to be one master copy of Fred when there isn't one participant that
knows everything about Fred.

Leadership is another example. Sometimes somebody really is in charge.
The Communicator has cases where one party has standing to make a
decision about the call. That party writes the decision. I don't need a
leader-election protocol to figure out who gets to make the decision
when the application already knows who gets to make it. If the
application really does require an election, fine, have one. The point
is not to build one just because distributed applications traditionally
have one.

Liveness works the same way. Suppose an ETR says it is alive and that
assertion is good for a certain amount of time. It writes its liveness
and keeps refreshing it. If it dies, it stops refreshing it and
eventually the assertion isn't current anymore. I don't need another
participant to notice that the ETR died and then go around changing
everybody else's state to say so. The ETR owned the truth that it was
alive. When it stopped maintaining that truth, the truth expired.

There are also places where the answer really is coordination. Ribbit
doesn't claim coordination is unnecessary. It says not to build
coordination until the problem actually requires it.

A work queue is an obvious one. Suppose I have ten workers and one job
that must be performed exactly once. Giving every worker its own copy of
the job doesn't solve anything. Somebody has to claim it, and everybody
else has to agree that it has been claimed. In that case, contention
isn't something I accidentally introduced by modeling the data badly.
It's part of the problem I'm trying to solve.

I can still represent the problem as state. The job exists, a worker is
available, a worker has claimed the job, the job is running, the job
completed. But representing those things as truths doesn't magically
give me exactly-once execution. If two workers can claim the same job at
the same time, the application needs an atomic way to decide which claim
wins. If a worker claims a job and dies halfway through it, the
application has to decide whether the claim expires, whether somebody
else may take it, and whether doing the work twice is safe. Those are
application semantics and Ribbit shouldn't make them up.

**This is what the vendor operations are for.** A claim can be one
vendor operation that looks at the current state and changes it before
anybody else can get between the two. The operation runs in the RAM
host, against the memory itself, under the row locks. The vendor defines
what "claim" means because the vendor owns the semantics.

This is different from the race case above. If I have ten users
independently publishing their locations, making them contend over one
writable record would be stupid. Give them ten segments. If I have ten
workers competing for one job, there really is one thing they are
competing for. Splitting it into ten segments doesn't make that fact go
away.

Pub/sub looks different once the state is shared, but it doesn't
disappear either. If what I mean by publish is "this is my current
temperature," I don't need a message broker. I write the temperature and
anybody interested in it can watch it. If what I mean is "these
seventeen events happened, in this order, and every subscriber must be
able to consume all seventeen," that isn't current state anymore. I have
a stream with retention and delivery semantics. I can implement that
using Ribbit, but calling it shared memory doesn't make those
requirements disappear.

The same thing applies to caching. A lot of caching exists because
repeatedly going across the network to ask another service for the same
thing is expensive. Ribbit changes that equation because the application
is already programming against shared state, and the permanent
connection and semantic handling underneath it can make keeping that
state current much cheaper. That doesn't mean caches cease to be useful.
A local copy may still be faster, an expensive derived result may still
be worth keeping, and an application may deliberately accept stale data.
What changes is that I don't automatically add another copy of the truth
and then inherit a cache-invalidation problem without first establishing
that I need the cache.

Transactions are probably the clearest example of where I don't want
Ribbit pretending to be something it isn't. If my application requires
several independent changes to become visible atomically, then that's a
real requirement. Breaking the data into independently owned truths does
not satisfy it. Either I can change the model so there is one owner of
the transaction result, or I can make the change a vendor operation and
do the whole thing inside the RAM host.

lispers.net does exactly that. A Map-Register can contain several
records, but accepting half of the registration isn't useful. The
complete packet is validated first and the records are applied as one
operation, or they aren't applied. That's application-specific atomicity
implemented where it belongs, rather than a general transaction system
imposed on every Ribbit application.

What I don't do is call several ordinary writes a transaction and hope
nobody notices the difference.

Reads have the same issue. When I read Fred's location and then Fred's
account status, I have read two truths. Fred may have moved between
those reads. His account status may have changed too. Ribbit doesn't get
to tell the application that those two observations happened at the same
instant when they didn't.

Sometimes that doesn't matter. If I'm drawing Fred on a map, I probably
want the freshest information I can get and I don't care that two
unrelated fields were observed a few milliseconds apart. If I'm
transferring money, I may care very much. The application knows the
difference.

A watch doesn't change that. A watch tells me that the state I'm
interested in has changed according to the semantics of that operation.
It isn't a global clock, it doesn't freeze the rest of the memory while
I react, and it doesn't mean that everything I subsequently read
represents one atomic snapshot of the world. If I need those semantics,
I have to design for them.

This is why simply converting an existing protocol to Ribbit isn't
enough. If I take an application with a broker, a registry, a pile of
locks and a bunch of messages and reproduce all of those things in
shared memory, I haven't learned very much. I have the same architecture
implemented a different way.

I've done that. Once I could run it and see what it was actually doing,
I realized I had asked it to do the wrong thing and threw it away.

The question isn't how to implement the existing protocol in Ribbit. The
question is why the protocol exists. If it represents something the
application actually requires, keep it. If it exists so A can tell B
something A knows, write A's truth and let B read it. If B needs to know
when that truth changes, B watches it. If several programs are updating
one representation of an object, find out whether those are really
separate truths and give them separate places to write. If the
application already says who has authority to make a decision, let that
participant make the decision. If I need a distributed lock, find out
why I created multiple writers.

That is what the examples are for. Chat, Redis, PyTorch, lispers.net and
the Communicator are very different applications, which is precisely why
they are useful. Chat exercises held reads and independently owned
buffers. Redis gives me a familiar state interface to compare against.
PyTorch exercises rendezvous and distributed coordination. lispers.net
exercises capability discovery, ownership, liveness, derived views and
application-specific atomic operations. The Communicator puts the same
ideas into a live application with discovery, call state and media.

They aren't five demonstrations of the same trick. They're five
different attempts to find where the model stops working.

So far, the useful result hasn't been that Ribbit eliminates distributed
systems. Obviously it doesn't. It has been finding out how much
distributed-system machinery I didn't actually need.

That's how you code like a Frog.

**Determine your truth. Write your truth. Read the other truths. Go.**

# Part III: How Ribbit Optimizes Communications

So far I've mostly ignored the fact that the memory is on the other side
of a network. That's intentional. If Ribbit is doing its job, the
application programmer shouldn't have to spend much time thinking about
how the information gets there.

Unfortunately, somebody does.

Networks are fast, but they aren't free. Every byte still has to go
somewhere, latency still exists, links still fail, and some of the
places where I want Ribbit to work have terrible connections. Treating
something on the other side of the country as memory doesn't give me
permission to be stupid about what I put on the wire.

The first optimization is the one we've already spent two chapters
talking about: **don't communicate things you don't need to
communicate.** If I replace a request, response, acknowledgement and
notification sequence with one participant writing its current truth and
another participant reading it, I have eliminated communication before I
start trying to compress anything. If I don't need a discovery
conversation because I can read the capability I need, those packets
never exist. If I don't need a distributed lock because I only have one
writer, there is no lock traffic to optimize.

**The cheapest packet is the one I never send.**

After that, Ribbit gets more aggressive.

A conventional protocol tends to send complete representations over and
over. HTTP is an easy example. I send headers describing what I'm doing,
names identifying fields, formatting that lets the other side interpret
them, and frequently a bunch of values that haven't changed since the
last time we talked. Then I do it again on the next request.

Ribbit has a permanent relationship between the application and the
memory service. That means the two sides can remember what they already
know. If I've already told you what an object looks like, I don't need
to describe it again every time I change one value. If we already agree
on what a field means, I don't need to keep sending its name. If twelve
values are unchanged, I don't need to send twelve unchanged values just
so you can reconstruct the thirteenth one that changed.

Send the difference.

That's the basic idea behind Ribbit's semantic compression. It isn't
simply taking a conventional message and running a general-purpose
compressor over the bytes. Ribbit understands enough about the structure
and meaning of the interaction to avoid generating a lot of those bytes
in the first place.

Compressing `"temperature":72` into fewer bytes is useful. Reaching the
point where both sides already know that this value is the temperature
and I only need to send the change to `72` is better.

The permanent connection is what makes that practical. When the
connection comes up, the two sides establish a session and keep it. They
don't start from zero every time the application touches memory. What
one side has already taught the other side remains useful for the next
operation.

The first time Ribbit sees a request it doesn't know, there isn't any
magic. It has to learn it. The template, structure and dynamic pieces
have to be established, and that bootstrap traffic costs bytes. Once the
relationship is known, subsequent requests can refer to what both sides
already know and send the parts that changed.

That is why I separate bootstrap from steady state when I measure this.
Charging every request for the cost of teaching the first one would be
misleading. Pretending the first one was free would be just as
misleading.

I ran a pipeline workload through the proxy and measured the bytes
actually put on the wire against what the same traffic would have cost
without the semantic engine. The bootstrap request that teaches the
template was measured separately and excluded from the steady-state
numbers.

  Payload                         On the wire   Without the engine          Saved
  ----------------------------- ------------- -------------------- --------------
  Telemetry, 26 KB                  145,882 B          1,411,695 B   89.7% (9.7×)
  Chat window, 13 KB                  3,021 B            729,238 B   99.6% (241×)
  1 MB JSON, 6 dynamic fields        96,961 B         54,749,688 B   99.8% (565×)

Those numbers are wildly different for a reason. Semantic compression
works on what changes. The 1 MB JSON case has six dynamic fields in a
representation that is otherwise already known. There is almost nothing
new to send. A workload in which most of the representation changes
every time isn't going to get a 565× reduction, and I don't claim that
it will.

The roughly 90% number I've used elsewhere comes from workloads like the
telemetry test, not from assuming every application gets the best number
I've ever measured.

There are also cases where Ribbit costs more.

In one of the lispers.net measurements, a Ribbit Map-Register put about
1.7 KB on the wire at the systems host. The original lispers.net
implementation used 276 bytes. The original handled that packet locally.
Ribbit crossed the Internet to shared memory. Lookups used the same
number of bytes on both sides.

That's not a failure I need to hide. It tells me what I'm actually
buying. If I take an operation that was already tiny and local and move
it across the Internet, semantic compression doesn't repeal arithmetic.
The reason to make that change has to come from somewhere else in the
architecture.

Ribbit also avoids doing the same work twice when it can tell that the
work is the same. If a pile of identical requests arrive while the first
one is still being processed, I don't need to run the request a pile of
times. I can coalesce them, run it once and use the result to answer
everybody waiting for it.

That showed up very clearly in the burst tests. Four hundred
simultaneous requests could be carried with only a few kilobytes of
traffic because they weren't treated as four hundred unrelated
conversations.

Again, the optimization comes from knowing something about what the
communication means. A byte compressor can't know that 400 requests are
asking the same question. Ribbit can.

The same distinction matters when the payload itself gets large. Not
everything belongs in the semantic control path. Structured state is a
good fit because I can understand its shape, identify what changed and
encode the difference. A large opaque piece of data may not be.

That's why Ribbit separates the semantic work from the data path. The
permanent session gives me the relationship and the shared understanding
between the endpoints. Large data can move on the data socket instead of
being forced through an encoding designed for structured state.

That data path is segmented so one large transfer doesn't get to own the
connection. This matters when a slow link is carrying several kinds of
traffic at once. A large object can be broken into pieces and
interleaved fairly with other work instead of making everything behind
it wait for the entire object to finish.

The Communicator makes the reason fairly obvious. I may have video
moving while audio and control state are moving too. I don't want a
large video piece blocking the audio just because it got to the socket
first. The application cares about those things differently, so the
transport has to be capable of treating them differently.

BLDC handles the structured side of this. It isn't limited to one fixed
representation. Language handlers can be added so the engine can
understand additional structured formats, identify their meaningful
pieces and operate on them without changing the basic transport model.
The point isn't to make every possible payload semantic. The point is to
use semantic handling where knowing the structure buys me something, and
use the data plane when it doesn't.

There is another consequence of keeping a permanent semantic
relationship: sometimes one side thinks the other side knows something
that it doesn't.

Suppose I have already sent a complete response and later tell the other
side `SAME`. I'm saying, in effect, "you already have the answer."
Normally that's exactly what I want. There is no reason to send the
answer again.

But machines restart. Connections fail. State gets lost.

If the other side tells me it doesn't actually have the answer I just
referred to, I rebuild the full answer from the semantic cache and send
it. I do **not** run the original request again.

That distinction is important. The answer has already been produced.
Re-running the request could produce a different answer, repeat an
operation with side effects, or simply waste work. Recovery repairs the
shared semantic context. It doesn't pretend the original operation never
happened.

All of this sits below the programming model from the first two parts.
The application writes its truth and reads the truths it needs.
Underneath that, Ribbit can avoid conversations the application didn't
need, avoid repeating structure the other side already knows, send
differences instead of complete representations, coalesce identical
work, move large payloads on the appropriate data path, share the
connection fairly and recover when the two sides disagree about what
they remember.

None of those optimizations changes what the application thinks it is
doing.

That's important, because I don't want an application written around a
particular compression trick. If tomorrow I find a better way to encode
a delta, or decide that a particular payload should bypass the semantic
engine entirely, that shouldn't require rewriting the application. The
application owns its truths. Ribbit owns getting those truths where they
need to go.

The measurements also make it pretty clear why I don't describe this as
"90% compression." Sometimes I've measured less. Sometimes I've measured
vastly more. Sometimes Ribbit puts more bytes on the wire than the thing
I'm comparing it to.

The useful question is what information actually had to cross the
network to accomplish the application's job.

First, don't create communication the application doesn't need.

Then don't put information on the wire that the other side already
knows.

And don't do the same work twice.

# Part IV: Debugging and Logging

One of the first things I want to know when I'm debugging a distributed
application is what everybody thought was true.

That can be surprisingly hard to answer. I have logs from several
machines, requests going in both directions, retries, callbacks, queues
and timeouts. Something went wrong, and now I'm trying to reconstruct
the state of the system from the conversations the participants happened
to record.

With Ribbit, the participants are already publishing the state they own.
I can look at it while the application is running. If Fred's location is
wrong, I can look at the location Fred published. If a worker says it is
ready, I can look at its ready truth. If an ETR isn't resolving, I can
look at its registration, its liveness and the other truths involved in
making that decision.

That doesn't necessarily tell me why something is wrong. It tells me
where to start.

There is a difference between state and history. Ribbit memory normally
tells me what is true now. A log tells me what happened before now. If a
value changed from 10 to 11 to 12, the current truth may simply be 12.
If I need to know that it was 10 and then 11 before it became 12, I need
to record that history somewhere.

I can decide how much of that I need. I can watch a truth and log every
change, keep the last hundred values, record only changes I care about,
or send the whole thing to permanent storage. I can turn detailed
logging on while I'm chasing a problem and turn it back off when I'm
finished.

The application doesn't have to change because I want more
instrumentation.

That gets particularly useful when several truths contribute to a
result. Suppose my program reads three independently owned values and
produces a fourth. If the fourth one is wrong, I want to know which
inputs produced it. Recording the generations or identities of those
inputs gives me something much more useful than trying to line up
timestamps from four machines and guess what happened.

It also keeps me honest. As I said in Part II, reading A and then B does
not mean I observed A and B at exactly the same instant. If A changed
between the two reads, I shouldn't have a debugging system that quietly
tells me otherwise. "I calculated this from A generation 47 and B
generation 92" says what actually happened.

Logging can follow the same ownership rule as everything else. I don't
need every participant writing into one global log. Each participant can
publish its own diagnostics. A monitor can read them, combine them,
display them or archive them. The monitor doesn't have to be part of the
application at all.

Performance instrumentation works the same way. If I care about queue
depth, latency, bytes transferred, compression ratio, retries or cache
hits, the component that owns that measurement can publish it. A
dashboard can display it. Something else can archive it. I don't need to
build a diagnostic API into every application just so something can ask
how it's doing.

There is another level below that, because Ribbit knows things the
application doesn't. It knows what actually went over the wire. It knows
whether something went in full, as a difference, as `SAME`, or by
reference. It knows when requests were coalesced. It knows when semantic
state had to be recovered. It knows what happened on the data
connection.

That belongs in Ribbit's diagnostics, not in the application.

If the application says it wrote something and the reader didn't see it
when I expected, I should be able to work my way down. Did the writer
publish it? Did the RAM host get it? What generation did the reader see?
Was it waiting on a held read? What actually went over the wire? Did
semantic recovery happen? Was something else occupying the connection?

I don't want one enormous log containing all of that. I want enough
information at each level to follow the problem until I find the place
where reality stopped matching what I expected.

I also don't want to log everything. Logging everything is a wonderful
way to create several gigabytes of information that nobody can use, and
logging can change the timing of the thing I'm trying to debug. Decide
what I need to know and instrument that.

More importantly, instrument the thing I actually care about. If I'm
measuring semantic compression, count the bytes that went over the wire.
Don't calculate what I think probably went over the wire from the size
of an object somewhere else. If I'm testing a distributed algorithm,
record what the participants actually observed. Don't record what the
algorithm says they should have observed and call that a measurement.

And then write a test that tells me whether the result was right.

A log isn't an oracle. If I have to stare at the log after every run and
decide whether the test passed, I don't have a test suite. I have a
hobby.

The test says what must be true. The instrumentation tells me why it
wasn't true when the test fails.

That distinction matters beyond debugging. An oracle tells me whether I
preserved the behavior I care about. Instrumentation tells me what the
implementation actually did. Performance measurements tell me whether I
improved the thing I meant to improve.

Those are three different questions, and I want answers to all three.

That gives me something much better than a pile of logs. It gives me a
way to change the system and know what happened. That becomes important
when I build and deploy it, and even more important when I start
changing the architecture itself.

# Part V: Building and Deploying

Ribbit is intended to make distributed applications simpler, so
installing one shouldn't require recreating a distributed systems
laboratory on every machine.

There are really two things being deployed. The vendor deploys the
shared-memory service and defines the region. The application developer
uses the interface the vendor supplies.

The vendor's definition includes the host name or IP address and port of
the RAM host, the schema used in that region, the server-side API and
the convenience library applications use to get at it. The host can be
fixed or dynamically allocated. The vendor decides how many regions
exist, who belongs to each one, who can read and write what, how long
state lives, and what application-specific operations are available.

That is the boundary.

If I'm the application programmer using a weather service, I shouldn't
need to know how the weather vendor laid out its tuples. I use the
library the vendor supplied. If I'm the weather vendor, I own everything
behind that library and can change it as long as I preserve the contract
I've given my applications.

The same thing applies to language. Ribbit has C++ and Python
implementations, but the wire shouldn't care which one I used. A Python
participant and a C++ participant using the same schema are participants
in the same memory. The language library turns the programming interface
into Ribbit operations and the server-side API turns those operations
into whatever the vendor decided its memory means.

That separation is also why the examples build as standalone things.

Chat isn't supposed to require the rest of FrogNet. Neither is Redis,
PyTorch, lispers.net or the Communicator. Each example should be
something I can build, point at a Ribbit host and run over the Internet.
The example demonstrates the programming model; it shouldn't require
somebody to install an unrelated network just to find out whether the
idea works.

The repository follows the same rule. The Ribbit material belongs
together: the implementations, the default API and schema, the
documentation, and the examples. Somebody opening the tree should be
able to start at the README, understand what Ribbit is, build the
implementation they need, and then run a real example without having to
reverse-engineer how I developed it.

The defaults matter here. A vendor shouldn't have to design an API and
schema before it can write "hello world." Ribbit supplies a default
tuple-space contract that is enough to get started and enough for
applications that don't need anything more specialized. If the default
fits, use it.

If it doesn't, change it.

The API and schema are deliberately extensible because the vendor knows
the application domain and Ribbit doesn't. A weather vendor, a game
vendor and somebody implementing LISP should not be forced through the
same application API just because they happen to use the same
shared-memory substrate.

The vendor can add operations that run at the RAM host when the
application needs them. We already saw why in Part II. A queue claim may
need to inspect a value and change it atomically. A multi-record
Map-Register may need to validate everything before changing anything.
Those operations belong beside the memory because that's where the
required atomicity can actually be provided.

That doesn't mean I move the application into the RAM host. It means I
put the operations that have to be performed against the memory there.
The rest stays where it belongs.

Deployment follows from the same separation. The memory host needs to be
someplace the intended participants can reach. The vendor supplies its
API and client library. The application gets the address of the memory
region and whatever credentials and configuration the vendor requires,
establishes its permanent Ribbit connection, and starts using the
region.

From the application's point of view, moving the memory shouldn't be an
architectural event. If I move a Ribbit service from a machine on my
local network to a machine in a datacenter across the country, the
application should primarily care that the address changed. Obviously
the performance characteristics changed. Latency changed. Available
bandwidth may have changed. Failure modes may have changed. But I
shouldn't have to rewrite the application because the memory moved.

The same is true in the other direction. I can put a region close to the
applications using it when latency matters. I can put it somewhere
broadly reachable when geographic distribution matters more. I can
create one region for everybody, one per customer, one per session, or a
confederation of participants that exists for twenty minutes and
disappears when the job is finished.

The vendor decides because the vendor knows what the region means.

That also means deployment policy is not hidden inside Ribbit. If the
vendor requires persistence, the vendor supplies persistence. If a
region has to survive a host failure, the vendor defines how that
happens. If two regions split and later meet again and the application
requires reconciliation, the vendor defines the reconciliation
semantics. If the application requires a minimum number of participants
before a region is useful, that is part of the vendor's policy.

Ribbit doesn't get to invent those answers.

Security belongs in the same category. The vendor owns the island and
decides who gets onto it. The vendor decides who can read which
portions, who can write which portions, what input spaces a service
exposes and how participants authenticate. The first rule from Part II,
that I write only my own truth or an explicitly authorized service
input, becomes an enforceable deployment policy at that boundary.

I don't want every application implementing a different version of that
policy. Put it where the memory is owned and enforce it there.

Building the application should then be fairly boring. Link or import
the Ribbit library, configure the region, establish the connection and
use the interface. If I'm using a vendor-specific library, I should be
thinking in the vendor's vocabulary instead of Ribbit's.
`get_weather("Seattle")` is a better weather API than making every
weather application know the three coordinates the vendor happened to
use underneath it.

That is also why I care about keeping the examples buildable
independently. I don't want a paper full of architecture diagrams and
snippets that look plausible. I want something you can compile and run.

Build Chat. Run it against the RAM host.

Build Redis. Compare it with the interface it is replacing.

Run PyTorch against Gloo and against Ribbit.

Run the lispers.net oracle against the original implementation and the
Ribbit implementation.

Run the Communicator and watch a live application use the same
machinery.

The tests and measurements belong with the examples because they're part
of the example. "It compiled" isn't qualification. If I'm claiming
compatibility, preserve the incumbent behavior and run the oracle. If
I'm claiming performance, measure it. If I'm claiming fewer lines or
less complexity, count them and say what I counted.

This is especially important for something like Ribbit because it asks
people to question assumptions that have been sitting underneath network
programming for a long time. I don't expect somebody to accept those
claims because I wrote an enthusiastic README.

Run it.

Break it.

Change the schema. Write a vendor operation. Move the RAM host across
the country. Kill a participant and see what happens to its truth. Put
it on a bad link. Compare the bytes. Compare the code. Take one of the
examples and replace something I did with something you think is better.

That's why the source is there.

The goal isn't to make everybody build applications exactly the way I
built mine. The goal is to give them enough working machinery that they
can ask the same question I did without first spending months building
the experiment.

**How much of the distributed architecture I normally write is actually
required by my application?**

Build it and find out.

That used to be a much more expensive suggestion than it is now.
Building a serious alternative architecture used to mean committing
enough time and code that most of the alternatives were rejected before
anybody ever ran them.

That has changed.

# Part VI: Using AI for Maximum Code Velocity

AI changes the economics of the experiment.

It doesn't change what I need to know. I still need the oracle from Part
IV to tell me whether the behavior is right. I still need the
instrumentation to tell me what the implementation actually did. I still
need the measurements and the runnable system from Part V.

What AI changes is how cheaply I can ask the next question.

I don't use AI to decide what I should build. I use it to make building
what I want to test ridiculously cheap.

That distinction matters. Claude can write a ton of code very quickly.
So can the other current coding models. But if I'm not also writing and
running tests, measuring the result and documenting what I actually
built, it's probably all crap.

AI is a tool, just like any other. You wouldn't use a hammer to cut a
board, so why use AI for the parts it sucks at? I don't want it
supplying the imagination or understanding the bigger picture. Those are
the parts I need to own. What I want from the AI is the ability to turn
a sufficiently precise engineering idea into working code at a rate I
couldn't begin to approach by hand.

That changes what I'm willing to try.

In the past, rewriting a subsystem was expensive. If I already had
30,000 lines of working code wrapped around an architectural decision,
those 30,000 lines became an unstated argument for keeping the decision.
I might know there was another way to do it. I might even think the
other way was better. But somebody had to write it, compile it, debug
it, integrate it and test it before I could find out.

That tax was high enough that we normally paid it only when something
was badly broken.

AI changes the economics.

If I have a good behavioral oracle, I can hand an AI the existing
implementation, the contract it has to preserve, the tests that
establish that contract and the architecture I want to try. It can build
the alternative. Then I run the oracle.

If it passes, I have learned something.

If it fails, I find out why.

If I get it running and discover that the whole idea was stupid, I throw
it away.

That last one is more important than it sounds.

I've done it repeatedly while building Ribbit. I've dropped entire
architectures and replaced them wholesale because the cost of trying the
replacement was low enough that I didn't have to argue about whether it
might work. I could run it and find out.

Sometimes the replacement was better. Sometimes it exposed another
problem. Sometimes, once I could see what it was actually doing, I
realized that I had asked it to do the wrong thing and threw that one
away too.

That's not thrashing. Thrashing is changing things without knowing
whether the change helped. If I have an oracle that tells me whether I
preserved the required behavior, instrumentation that tells me what the
implementation is actually doing, and measurements that tell me whether
I improved the thing I was trying to improve, then rewriting an
architecture is an experiment.

That is where the code velocity comes from.

**Maximum code velocity comes from reducing the cost of asking the
machine another well-formed engineering question.**

The important word there is *well-formed*.

"Make this faster" is not a particularly useful engineering question.
Faster how? Throughput? Latency? CPU? Wall-clock time? Under what
workload? Compared with what? What behavior am I not allowed to change?

"Preserve these 42 externally observable behaviors, remove this
synchronization mechanism, and tell me whether throughput and latency
improve under this workload" is something I can test.

Now the AI has boundaries. More importantly, so do I.

I can build the red test first. I can run it against the incumbent and
establish what the incumbent actually does. I can make the new
implementation pass the same oracle. Then I can measure the result.

This is exactly why the debugging and instrumentation in Part IV matter.
AI code generation without fast feedback isn't maximum velocity. It is
just maximum code generation.

Those aren't the same thing.

The faster I can generate code, the less willing I should be to accept
code merely because it looks right. Compile it. Run it. Test it. Measure
it. If I'm replacing something, run the incumbent too. If I claim
compatibility, prove compatibility. If I claim performance, measure the
thing I actually claim improved.

I also document while I'm doing it.

That is partly because AI needs the documentation. A coding assistant
coming into a system without a clear statement of what the system is
supposed to do will happily infer one. Sometimes it will infer the right
one. Sometimes it will build an extremely competent implementation of
something I never wanted.

The documentation is part of the engineering contract. It says what the
architecture is, who owns what, what behavior must be preserved, what
the tests mean, what has already been tried and why something was
discarded. If the implementation and the documentation disagree, that's
something I want to discover, not quietly paper over.

The checkpoints matter for the same reason. When I get something green,
I keep it. Now I have a known point I can return to. The next experiment
starts from something that worked instead of from whatever happens to be
in the directory after three unsuccessful ideas.

That makes aggressive experimentation safe enough to be useful.

Ribbit-LISP is a good example. I didn't just tell an AI to "rewrite
lispers.net using Ribbit." The incumbent implementation was the
behavioral oracle. Changes were made in pieces. A failing test
established the next behavior before the implementation changed. When a
new architecture didn't survive the tests, it didn't survive. When
something went green, it became a checkpoint.

That process let me make changes that I would have considered absurdly
expensive a few years ago. Locks disappeared from the participant side.
The RAM host's single global mutex was replaced by per-row locks and a
pool of Engines. Once that was running I could see what I had actually
built: a pool of Engines that were clients of their own server, complete
with loopback sessions, copied views and their own mutexes. I threw the
pool out. The operations now run in the memory itself, under the row
locks. Registration ownership changed. Liveness changed. Multi-record
registration became an all-or-nothing operation. Whole pieces of
architecture were replaced while the externally required behavior stayed
pinned down by the oracle.

The AI made writing those alternatives cheap. The oracle made throwing
them away cheap.

Those are two different things, and I need both.

The same pattern shows up in the other Ribbit experiments. The chat
client took a couple of hours because the generic memory server already
existed and there was no chat server to write. Redis took four days. The
PyTorch work took five days from a standing start. The lispers.net
conversion took about six days end to end, or five from when I took over
the implementation work.

Those numbers aren't interesting because typing fast is impressive.
Typing isn't the bottleneck anymore.

They're interesting because I could move from question to running
experiment fast enough to ask the next question while I still cared
about the answer.

That changes architecture.

Before AI, I had to spend a lot more time deciding whether an idea was
worth implementing. There was no choice. Implementation was expensive. I
had to reject most alternatives before I had evidence because I couldn't
afford to build every plausible one.

Now I can be much less conservative.

What happens if I remove this layer?

Try it.

What happens if I replace the global lock?

Try it.

What happens if I stop reproducing the existing PyTorch choreography and
model the operation directly as shared state?

Try it.

What happens if the application doesn't have a server at all?

Try it.

The important part comes immediately afterward:

**Run it.**

That is the difference between using AI as an engineering accelerator
and using it as a code generator.

The design still matters enormously. In fact, I think it matters more.
If I can generate 10,000 lines of implementation before lunch, a bad
architectural decision can now become 10,000 lines of bad implementation
before lunch.

The AI doesn't fix that.

A flawless implementation of the wrong design is still wrong.

So I want to spend my time where I have the most leverage: deciding what
the system should do, figuring out who owns each truth, defining the
interfaces and invariants, designing the experiment, deciding what
constitutes success, and looking at the results. I let the machine pay
as much of the mechanical implementation tax as I can safely hand it.

Then I make it prove what it wrote.

This is also why I don't particularly care about arguments over whether
AI-generated code is "real programming." I care whether the system
works. I care whether the implementation satisfies its contract. I care
whether I can reproduce the result. I care whether the measurements are
honest. I care whether somebody else can open the tree, run the tests
and see the same thing.

The source doesn't get extra credit because I suffered while typing it.

The real opportunity is that architectures have become disposable in a
way they never were before.

That's a very big deal.

Software engineering has always accumulated sunk cost. Once enough code
depends on a decision, changing the decision becomes progressively
harder, even when everybody knows it wasn't a particularly good decision
to begin with. Eventually the cost of changing the architecture can be
larger than the cost of living with it.

AI attacks that cost directly.

But it only works if the rest of the engineering process gets more
rigorous at the same time. The faster I can rewrite something, the more
important the oracle becomes. The faster I can generate alternatives,
the more important measurement becomes. The easier it is to change the
architecture, the more important it is to know exactly what contract I'm
preserving.

Maximum code velocity isn't how many lines an AI can spit out in an
hour.

**Maximum code velocity comes from designing and instrumenting the
system so it's easy and fast to see if an algorithm is or is not
working, combined with a rigor that cuts off unproductive architectures
as soon as they can be identified, while simultaneously opening the door
for new, previously unknown or untried architectures.**

The AI makes the experiments cheap.

The engineering makes the experiments mean something.
