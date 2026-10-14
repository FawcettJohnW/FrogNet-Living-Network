import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
import re as _re
_sub = sub
def sub(o, n):
    # source escapes non-ASCII as \\uXXXX; hex case varies, so try both for OLD and mirror it in NEW
    o, n = o.replace("\\'", "'"), n.replace("\\'", "'")
    lo, ln = U(o), U(n)
    up = lambda x: _re.sub(r'\\u([0-9a-f]{4})', lambda m: '\\u' + m.group(1).upper(), x)
    if has(lo) == 1: _sub(lo, ln)
    else: _sub(up(lo), up(ln))

# 1  agentless claim
sub(r'What changed is the box their traffic already passed through. No per-host mesh can make that claim, and it is the claim worth leading with.',
    r'What changed is the box their traffic already passed through. Per-host meshes can reach a device that cannot take an agent through a subnet router, as an add-on. For FrogNet, insertion below egress is the default, not the exception, and that is the claim worth leading with.')

# 2  discovery calls the daemon
sub(r'b("You also have to answer the phone when discovery tries to contact the proxy."),',
    r'b("You also have to answer the phone when discovery calls the daemon on :9009."),')

# 3  negative cache worked example
sub(r'exLabel("Why only a refusal marks a host as not-FrogNet"),',
    r'exLabel("Why one failure never marks a host as not-FrogNet"),')
sub(r'P([ t("The negative cache has a rule that looks over-careful until you know where it came from: a "), b("timeout must never mark"), t(" a host as not-FrogNet. Only a definitive refusal does.") ]),',
    r'P([ t("The negative cache has a rule that looks over-careful until you know where it came from: "), b("one failure never marks a host"), t(". A timeout to a node address ("), mono(".1"), t(" or "), mono(".2"), t(") never marks it at all.") ]),')
sub(r'P([ t("One legitimate refusal, during a two-second restart, cost the network its memory. The rule that followed is narrow and it is the right width: a refusal is evidence, a timeout is not, and a slow real node had already been poisoned here once.") ]),',
    r'P([ t("One legitimate refusal, during a two-second restart, cost the network its memory. What followed: a mark now needs three consecutive definitive failures (refused, or no route) spread over at least 15 seconds, and every mark expires after 60 seconds so the host gets a fresh probe.") ]),')

# 4  roles that exist
sub(r'register for one — database, media, board game, whatever roles exist.',
    r'register for one — database or media, the two roles that exist.')

# 5  rhythm product not built
sub(r'passive sensors at an aging parent\'s house write the rhythm of a normal day into the shared layer (the sensor platform, just above), a local specialist reads that rhythm and flags only the break, and nothing — no camera, no recording, no company — ever leaves the pond. The watch is a tuple read on hardware the family owns.',
    r'passive sensors at an aging parent\'s house can write the rhythm of a normal day into the shared layer (the sensor platform, just above), a local specialist can read that rhythm and flag only the break, and nothing — no camera, no recording, no company — ever leaves the pond. The watch is a tuple read on hardware the family owns. That product is not built.')

# 6  both platforms
sub(r'...gld([ t("Both platforms work. Neither is yet tuned',
    r'...gld([ t("Both platforms have run. The sensor side ships in the tree; the AI host\'s wrapper ran in April 2026 and has not been brought forward to the current code. Neither is yet tuned')

# 7  spliced paragraph
sub(r'In the current architecture the ownership is precise: Ribbit is the programming model and Region One boundary is worth drawing here so it is never blurred later: Linda matches "), i("arbitrary"), t(" tuples by pattern, and says nothing about how many fields a tuple has or what they mean. The three coordinates this book uses — type, name, location — are FrogNet’s indexing convention, chosen because they fit the traffic, not something Linda requires. Borrow the model, own the convention. contract; BLDC-1 is semantic representation; FNWP is the wire; FrogNet or FD&F supplies transport. Two participants that share state need not re-send what both already hold. The wire can carry the difference and nothing else.") ]),',
    r'In the current architecture the ownership is precise: Ribbit is the programming model and Region contract; BLDC-1 is semantic representation; FNWP is the wire; FrogNet or FD&F supplies transport. Two participants that share state need not re-send what both already hold. The wire can carry the difference and nothing else.") ]),' + '\n      ' +
    r'P([ t("One boundary is worth drawing here so it is never blurred later: Linda matches "), i("arbitrary"), t(" tuples by pattern, and says nothing about how many fields a tuple has or what they mean. The three coordinates this book uses — type, name, location — are FrogNet’s indexing convention, chosen because they fit the traffic, not something Linda requires. Borrow the model, own the convention.") ]),')

# 8, 9  first exchange is RAW; changed structure goes RAW again
sub(r'The first exchange of a structure goes whole — FULL — and both sides learn its template: its shape, which parts are fixed and which vary. After that the sender says SAME and sends almost nothing when nothing changed, sends DIFF with just the changed values against the shared template, and falls back to FULL when the structure no longer matches the retained representation.',
    r'The first exchange of a structure goes raw — the whole HTTP request and answer — and both sides learn its template from it: its shape, which parts are fixed and which vary. The next exchange of that shape is the first FULL: every value, with no reference yet to diff against. After that the sender says SAME and sends almost nothing when nothing changed, and sends DIFF with just the changed values against the shared template. When the structure changes, the exchange goes raw again and a new template is learned from it.')

# 10  worked example on the wire
sub(r'exLabel("A sensor reading on the wire, three exchanges running"),',
    r'exLabel("A sensor reading on the wire, four exchanges running"),')
sub(r'...codeBlock(["exchange 1   FULL   { \"sensor\":\"barn.temp\", \"unit\":\"F\",", "                      \"value\":54.2, \"ts\":1719..., ... }   ~480 B", "             both sides now hold the template.", "exchange 2   DIFF   value->54.6  ts->...                    ~12  B", "exchange 3   SAME   (nothing changed)                       ~21 B"]),',
    r'...codeBlock(["exchange 1   RAW    the whole request and answer", "             both sides learn the template.", "exchange 2   FULL   the values, no reference yet", "exchange 3   DIFF   value->54.6  ts->...                    ~55 B", "exchange 4   SAME   (nothing changed)                        21 B"]),')

# 11
sub(r'That template "), i("is"), t(" the FULL from a moment ago; learning it and sending it are the same act.',
    r'That template is learned from the raw exchange a moment ago; nobody sends a template.')

# 12  template identity
sub(r'It is keyed by the "), b("stable parts"), t(" of the exchange — the operation and the fixed query keys — not by the values. A temperature reading and a humidity reading from the same kind of sensor share one template; the number is just a value slotted into it. This is why a brand-new sensor that has never been seen still rides an existing template the instant its readings match a known shape: the template was learned for the shape, not for the specific sensor.',
    r'It is keyed by the "), b("stable parts"), t(" of the exchange — the operation, the fixed query keys and the set of body field names; a sensor write is keyed by its sensor type and metric — never by the values. Two barns’ temperature readings share one template; a humidity reading has its own. A brand-new temperature sensor rides the existing template the instant it reports: the template was learned for the shape and the metric, not for the specific sensor.')
sub(r'"first reading of this shape  ->  FULL: learn the template",',
    r'"first exchange of this shape  ->  RAW: learn the template",')
sub(r'"every later reading of the shape  ->  just the values, diffed:",',
    r'"second exchange  ->  FULL: every value, no reference yet",' + '\n        ' + r'"every later reading of the shape  ->  just the values, diffed:",')

# 13, 14  references keyed per instance; SAME still runs the request
sub(r'for each peer and each kind of exchange, the proxy keeps the last canonical values it sent or received.',
    r'for each peer, each kind of exchange and each instance of it — the location the message names — the proxy keeps the last canonical values it sent or received.')
sub(r'so a SAME never touches the disk on either side.',
    r'so a SAME answer is never re-sent or re-stored; the far side still runs the request to know that nothing changed.')

# 15  what persists
sub(r'P([ t("And underneath, the references are written through to durable storage — the node\'s own database — so they survive a restart. A node that reboots does not relearn every template and reference from scratch; it loads what it knew and picks up where it left off. The cache is therefore both hot and durable: fast in memory for the steady state, persisted so a power blink does not cost the network its learned templates.',
    r'P([ t("Underneath, the learned templates are written to the node\'s own database and survive a restart. References and SAME answers live in memory only: after a reboot the first exchange of each kind goes FULL and rebuilds its reference. The cache is therefore split: templates durable, references hot.')
sub(r'per-node databases and write-through reference caches that survive a restart,',
    r'per-node databases and learned templates that survive a restart,')

# 16, 17  guild questions
sub(r'A list is held whole up to sixty-four items and then handled differently; why sixty-four? A payload is compressed only when compressing it would actually shrink it, which is obviously right, but the crossover was measured on one machine against one codec.',
    r'A list is carried whole as one value however long it is, so one changed item moves the whole list; is that right? A payload is compressed with LZ4 and the compressed form is kept only when it is smaller, which costs a compression on every payload; one codec, never compared against another.')

# 18
sub(r'It is what rides the established sockets between node proxies introduced in Part V.',
    r'It is what rides the established sockets from a node\'s proxy to each peer\'s daemon on :9009.')

# 19  socket pair
sub(r'"  4  FNWP-1 reader  -->  handler (BLDC-1)",',
    r'"  4  daemon :9009 reader  -->  handler (BLDC-1)",')
sub(r'"  8  FNWP-1 frame  -->  back over the same socket",',
    r'"  8  FNWP-1 frame  -->  back over the return socket",')
sub(r'and the reply home along the very same path.',
    r'and the reply home over the return socket of the same pair.')
sub(r'They "), b("fan in"), t(" to the peer\'s one established socket: the writer sends their frames one at a time down the single connection,',
    r'They "), b("fan in"), t(" to the peer\'s one established socket pair — requests out on one, replies home on the other: the writer sends their frames one at a time down the send socket,')
sub(r'Several requests are in flight on the one socket simultaneously,',
    r'Several requests are in flight on the one send socket simultaneously,')
sub(r'exLabel("Three requests in flight on one socket to ShopBox"),',
    r'exLabel("Three requests in flight on one send socket to ShopBox"),')
sub(r'"one established socket, three callers, sent back-to-back:",',
    r'"one established socket pair, three callers, sent back-to-back:",')
sub(r'The reader pulls each reply off the socket,',
    r'The reader pulls each reply off the return socket,')
sub(r'The frames went out fanned-in on one socket and come back fanned-out to the right callers,',
    r'The frames went out fanned-in on the send socket and come back fanned-out on the return socket to the right callers,')
sub(r'"FrogNet FNWP-1 (one socket, fanned + interleaved):",',
    r'"FrogNet FNWP-1 (one socket pair, fanned + interleaved):",')
sub(r'Owning a persistent socket to the peer,',
    r'Owning a persistent socket pair to the peer,')

# pathway paragraph: same RAW error as 9; 20 request hash
sub(r'The structure no longer fits the template, and it sends a FULL.',
    r'No reference yet, and it sends a FULL. The structure no longer fits the template, and the exchange goes raw again.')
sub(r'A short request hash on the frame tells the far side which reference this is measured against, so the two ends never disagree about what “last time” was.',
    r'A 16-byte request hash on the frame names the request, and the location fields every difference carries tell the far side which instance\'s reference to apply it to, so the two ends never disagree about what “last time” was.')
sub(r'it frames the SAME, the DIFF, or the FULL, tags it with a sequence number, and puts it on the socket.',
    r'it frames the SAME, the DIFF, or the FULL, files the waiting caller under a local sequence number, and puts the frame on the send socket.')

# 21, 22  the frame
sub(r'": four bytes of length, then a four-byte magic, an opcode, and the body. The opcodes',
    r'": four bytes of length, then the four-byte magic FNW1, a one-byte opcode, and the body. A reply puts a four-byte sequence number between the length and the magic. The opcodes')
sub(r'a HELLO to open a channel; an ERROR; a SEQ_RESET; and the round-trip probes. The length prefix',
    r'a HELLO to open a channel; an ERROR; a SEQ_RESET; and the round-trip probes. In the Ribbit C++ wire, a SAME-MISS: the client no longer holds the answer a SAME named, and asks the far side for it from its cache; the request is never run again. The probes are PING, PONG and LOOP — LOOP being the daemon\'s verdict that the connection came back to itself. The length prefix')

# 23  SEQ_RESET
sub(r'The two agree because they are counting the same events in the same order on the same socket — not because the number crossed the wire. That is why there is a SEQ_RESET opcode: two independent counters can drift apart, which would be impossible if the number rode the request, and SEQ_RESET is how they are put back in step.',
    r'The two agree because they count the same events in order on the same connection, and both restart from zero when a connection is made. A SEQ_RESET opcode is defined for putting them back in step without reconnecting; nothing sends it today.')
sub(r'and SEQ_RESET exists precisely because the two sides count independently and a disruption can pull them apart.',
    r'and the two sides count independently and are put back in step only by reconnecting (SEQ_RESET is defined and unused).')

# 24  channel sets
sub(r'So FNWP-1 lets a node open "), b("independent channel sets"), t(" to a peer — up to three, each with its own sockets, its own sequence space, and its own worker threads, so a loss on one cannot stall another. You ask for a set by naming the protocol and how latency-sensitive it is; all the ordinary traffic stays on the shared default set.") ]),',
    r'FrogNet\'s proxy driver — not FNWP, which never opens a socket — has an allocator that can give a peer up to three "), b("independent socket sets"), t(", each with its own sequence space and threads; set 0 is the default every ordinary call uses, and a fourth protocol overlays the least latency-sensitive set. The allocator is unit-tested; nothing opens a set yet. The Ribbit C++ wire takes a different route to the same end: a payload too large to diff goes, cut into segments, over a separate always-open data socket, so the semantic socket keeps moving.") ]),')
sub(r'exLabel("A call, a sync, and a chat to ShopBox"),',
    r'exLabel("What the allocator would give a call, a sync, and a chat to ShopBox"),')
sub(r'"three independent sets — no shared head-of-line domain:",',
    r'"three sets from the allocator (unit-tested; nothing opens one yet):",')
sub(r'Three is the budget per peer, and it is deliberate: past three, a new protocol overlays onto the least-sensitive existing set and shares its congestion window and its stalls. The cap is a guardrail against opening so many sockets that the node drowns in connection state. Within it the rule is simple — the things that must not stutter get their own set; everything else rides the shared one.',
    r'Three is the budget per peer: past three, a new protocol overlays onto the least-sensitive existing set and shares its congestion window and its stalls. The cap keeps the node from drowning in connection state. The allocator hands out set 0, the default set, first, so as written the first protocol to ask for a set shares it with the ordinary traffic.')
sub(r'Streams that must not interfere get their own channel set, so a bulk transfer and a live call share the link without fighting.',
    r'In the Ribbit C++ wire, a payload too large to diff rides its own data socket, so a bulk transfer does not hold up the small exchanges.')

# 25, 26
sub(r'— the figure measured on a narrowband, 4800-baud-class link is 44× — and why a real web application runs at all over a link slower than a 1990s modem.',
    r'— the figure measured, 100 concurrent requests over a 1,500 ms WireGuard round trip with an echo that answered SAME every time, is 44× the serial baseline — and why a real web application runs at all over a link slower than a 1990s modem. FrogNet has also run over Ethernet shaped to 4800 baud.')
sub(r'a single narrow satellite link at the command post feeds a working mesh of dozens instead of buckling under the first few.',
    r'a single narrow satellite link at the command post should feed a mesh of dozens instead of the first few; that is argued from the byte counts, not yet measured.')

# 27  threading
sub(r'The permanent socket is driven by a "), b("pool of worker threads"), t(", each carrying its own exchange, and what they produce is interleaved onto the one connection — so requests from many senders and replies to many receivers cross the wire together, genuinely at the same time, not taken in turns.',
    r'Each application request runs on its own thread; one writer per peer puts their frames on the send socket and one reader takes replies off the return socket, while on the far side a "), b("pool of threads"), t(" executes the requests at once. Requests from many senders and replies to many receivers cross the wire together, not taken in turns.')
sub(r'["Multiple cores and threads", "A worker-thread pool drives the permanent socket, interleaving many senders\' requests and many receivers\' replies across one wire at the same time."],',
    r'["Multiple cores and threads", "A thread per application request, one writer and one reader per peer, and a pool on the far side that executes requests at once, so many senders\' requests and many receivers\' replies cross the wire at the same time."],')

done()
