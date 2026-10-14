# Oct 8 audit, part 2 -- verbatim from chat a3c5615d turn 31 (Part XVIII, scoping, 0.3/0.6/67, 0.7, pseudocode, XV1/2)
p='build_magnum.js'; s=open(p,encoding='utf-8').read()
def sub(o,n,cnt=1):
    global s
    c=s.count(o); assert c==cnt,(o[:80],c); s=s.replace(o,n)
sub('That is not an analogy for what FrogNet does. That is what FrogNet does.','That is not an analogy for what Ribbit does. That is what Ribbit does.')
sub('"  one program, many threads      one FrogNet, many nodes", "  --------------------------     ------------------------", "  shared address space           FrogNet Memory", "  a thread writes, others see    a node writes, all see"',
    '"  one program, many threads      one Region, many participants", "  --------------------------     -----------------------------", "  shared address space           a Ribbit Region", "  a thread writes, others see    a participant writes, all see"')
sub('"  no thread addresses another    no node addresses another"','"  no thread addresses another    no participant addresses another"')
sub('"  a thread exits, memory stays   a node dies, memory stays"','"  a thread exits, memory stays   a participant dies, the Region stays"')
sub('H1("70.  FrogNet Memory Without FrogNet"),','H1("70.  Ribbit Without FrogNet"),')
sub('P([ b("ARCHITECTURAL."), t(" The programming surface and the living network are separable, and the interesting question is what survives the separation.") ]),',
    'P([ b("DEMONSTRATED."), t(" The programming surface and the living network are separable, and the interesting question is what survives the separation. Specification C answers the first half: FD&F runs Ribbit over a transport that shares no machinery with FrogNet.") ]),')
sub('A program written against FrogNet Memory names three coordinates','A program written against a Ribbit Region names three coordinates')
sub('What you get is UnREST semantics \\u2014','What you get is Ribbit semantics \\u2014')
sub('if memory holds only current values and never history, a federation never has to reconcile a past.','if a Region holds only current values and never history \\u2014 the policy FrogNet\\u2019s own Region uses \\u2014 a federation of such Regions never has to reconcile a past.')
sub('The honest state of this: FrogNet does not do it,','The honest state of this: Ribbit does not do it,')
sub('P([ b("PARTIALLY DEMONSTRATED."), t(" What is architectural and what is an implementation detail.") ]),','P([ b("DEMONSTRATED."), t(" What is architectural and what is an implementation detail.") ]),')
sub('P([ t("FrogNet Memory is implemented by "), mono("api.php"),','P([ t("FrogNet\\u2019s Region, historically FrogNet Memory, is implemented by "), mono("api.php"),')
sub('But careful language is not proof, and the proof would be a second backing store implementing the same contract."',
    'But careful language is not proof, and the proof was a second backing store implementing the same contract. It now exists: the C++ RAM host keeps a Region in memory and persists it to a plain disk file, with no MySQL, and FD&F\\u2019s RAM manager is that host."')
sub('"  architectural   three-coordinate addressing", "                  current value only, no history", "                  freshness bound on read", "                  the store is elected, not configured", "", "  implementation  MariaDB, api.php, HTTP as the interface,", "                  the table names, the JSON envelope"',
    '"  architectural   three-coordinate addressing", "                  freshness bound on read", "", "  FrogNet policy  current value only, no history", "                  the store is elected, not configured", "", "  implementation  MariaDB, api.php, HTTP as the interface,", "                  the table names, the JSON envelope"')
sub('A second substrate would finish that job.','The second substrate exists; a third would test the boundary again.')
sub('and constrains what can be shipped where, and the reference implementation stays closed while the specification opens \\u2014 partly for the reasons in Part I, and partly for this one. Reasonable',
    'and constrains what can be shipped where. Reasonable')
n=s.count('"70-frognet-memory-without-frognet"'); s=s.replace('"70-frognet-memory-without-frognet"','"70-ribbit-without-frognet"')
a=s.index('// [RENAMED_PART_ALIASES_V1]')
if '"70-frognet-memory-without-frognet":' not in s:
    s=s[:a]+'// [AUDIT_ALIASES_V1] ids retitled by the normative audit\n  "70-frognet-memory-without-frognet": "70-ribbit-without-frognet",\n  '+s[a:]
print("alias retargeted",n)
sub('"The shared scratchpad lives at one address: "','"On a FrogNet, the default Region\\u2019s shared scratchpad lives at one address: "')
sub('"A tuple holds one value. Not the newest of several,','"In FrogNet\\u2019s default Region a tuple holds one value. Not the newest of several,')
sub('"The store performs no consistency checking, and that is a decision rather than an omission.','"This store performs no consistency checking, and that is a decision rather than an omission.')
sub('t(" is the one your applications use: the elected, floating data host,','t(" is the one FrogNet applications use: the elected, floating data host,')
sub('keeping history in a store designed to hold only the present.','keeping history in FrogNet\\u2019s default Region, a store designed to hold only the present.')
sub('t(" What "), mono("put()"), t(" gives you today is last-write-wins against the three-coordinate key,','t(" What FrogNet\\u2019s "), mono("put()"), t(" gives you today is last-write-wins against the three-coordinate key,')
sub('"Memory holds current state and never history. The sensor name is unique','"The FrogNet Region this simulator models holds current state and never history. The sensor name is unique')
sub('No merge, no conflict, no reconciliation pass. That is what \\u201ccurrent state, never history\\u201d means when something goes wrong,','No merge, no conflict, no reconciliation pass. That is what \\u201ccurrent state, never history\\u201d means for this Region when something goes wrong,')
sub('The claim FrogNet actually makes is about shared memory, and the way to test that claim','The claim this simulator has to test is about the FrogNet-hosted Region, and the way to test that claim')
sub('persisted so a power blink does not cost the network its shared memory.','persisted so a power blink does not cost the network its learned templates.')
sub('Primed dictionaries may make the first exchange compact as well.',
    'A primed dictionary supplies the shapes in advance, but the first message of each instance still carries all of its values, because there is nothing earlier to diff against; on the FD&F data path the dictionary made no measurable difference to bytes.')
sub('BLDC-1 chaining on send produced one FULL followed by 300 DIFFs at roughly 108 bytes per data unit of framing. The current LISP mapping',
    "Machines announce themselves and list one another from the shared space alone, with no packets between machines; when the RAM manager moves, all three test machines follow it through the LISP mapping and reattach within 73 to 182 milliseconds. On the data path, a flat single-unit send cut the frame from 111\\u2013117 bytes to 87\\u201388 and the data-set bytes by 23 percent, and on the exact same 605 sends FD&F's C++ BLDC-1 is byte-identical to FrogNet's Python BLDC-1, 79.8 bytes on average. The current LISP mapping")
sub('BLDC-1 chaining on send produced one FULL followed by 300 DIFFs at about 108 bytes per data unit of framing. The LISP mapping',
    "Three machines announce themselves and appear in every participants query with name, IP and capability; one that leaves turns not-alive when its connection ends; no packet passes between machines. A conformance client that knows only the six definitions gets an answer to each. The RAM manager registers its own mapping at start and again after a move, and all three machines follow it within 73 to 182 milliseconds without reconfiguration. Removing the retired machinery took the per-machine agent from 784 lines to 370. A FrogNet-style dashboard and a chat page run over the same RAM manager, the chat through chat.inbox.<submitter>. On FD&F messages, a flat single-unit send cut the frame from 111\\u2013117 bytes to 87\\u201388 and the data-set bytes by 23 percent, and on the exact same 605 sends FD&F's C++ BLDC-1 is byte-identical to FrogNet's Python BLDC-1, 79.8 bytes on average. Finding the Map-Resolver from the area's name alone uses a deterministic rule: its address is derived from the name, hashed into a reserved prefix and bound, anycast, by whoever serves the area's mapping; the move test measured here configured one Map-Resolver address and printed it as configuration. The LISP mapping")
sub('"  memset(tuple, value);          write what you know", "  value = memget(tuple);         read what is currently true", "  memwatch(tuple);               be told when it changes"',
    '"  write(region, tuple, value);   write what you know", "  value = read(region, pattern); read what is currently true", "  hold(region, pattern, version); wait until it changes"',2)
sub('P([ t("The next revision pass will continue moving those chapters under their proper specification headings rather than preserving historical vocabulary indefinitely. The rule for every such edit is simple: preserve demonstrated behavior, preserve the data, and move only the responsibility that the evidence says was assigned to the wrong layer.") ]),',
    'P([ t("The rule applied to every such passage is simple: preserve demonstrated behavior, preserve the data, and move only the responsibility that the evidence says was assigned to the wrong layer.") ]),')
sub('It is the first serious application of UnREST written against an interface somebody else specified, by people who were not thinking about UnREST when they specified it.',
    'It is the first serious application of Ribbit (then called UnREST) written against an interface somebody else specified, by people who were not thinking about shared memory when they specified it.')
sub('"  UnREST, sliced                      7    2554   1880-2838",','"  Ribbit, sliced                      7    2554   1880-2838",')
sub('"  UnREST, whole-tensor                6    3312   2254-4683",','"  Ribbit, whole-tensor                6    3312   2254-4683",')
open(p,'w',encoding='utf-8').write(s); print("ok part b")
