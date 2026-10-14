import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *

# ---- Part XI, 35f ----
# 1 + 2
sub(r'''gives you today is last-write-wins against the three-coordinate key, a timestamp''',
    r'''gives you today is the default Region's policy: one current value per three-coordinate key, with no history, a timestamp''')
sub(r'''and section 54 carries it with the other honest edges.''',
    r'''and section 61 carries it with the other honest edges.''')
# 3
sub(r'''P([ b("A bundle"), t(" is the FrogNet deployment packaging for a complete application, conventionally under "), mono("/etc/frognet_bundles/"), t(". It is an implementation convention, not a Ribbit requirement. The Communicator is the worked FrogNet bundle in this book; the Ribbit programming model itself is transport- and packaging-independent.") ]),''',
    r'''P([ b("A bundle"), t(" is a complete application packaged to run on FrogNet. The Communicator ships as a self-contained example under "), mono("/opt/frognet_semantic/ribbit/examples/communicator"), t(". That is an implementation convention, not a Ribbit requirement. The Communicator is the worked example in this book; the Ribbit programming model itself is transport- and packaging-independent.") ]),''')

# ---- Part XII ----
# 4
sub(r'''t(" No discoverable application endpoints. No exposed application protocols. A scanner sweeping the address does not find an application to talk to, because the applications never needed a door. Read that''',
    r'''t(" Ribbit does not publish one endpoint per operation. Each vendor's memory is one listener, its RAM host, on one path ("), mono("/<vendor>-api"), t("), and only participants speak to it. Where that listener sits decides who can reach it: on a node behind its uplink, nobody off the mesh can; on a public host, such as the FD&F region's RAM manager on "), mono("StreamingFrog:51888"), t(", anyone on the internet can. Read that''')
# 14 (intro)
sub(r'''FrogNet implements no cryptography of its own. The bearer crypto is WireGuard\u2019s, and it is yours to supply and yours to configure.") ]),''',
    r'''FrogNet encrypts nothing of its own. It calls OpenSSL for SHA-256 and HMAC-SHA256 to name cache entries, which is integrity, not confidentiality. Confidentiality comes from WireGuard, which is the tunnel and is basic, or from TLS where you configure it.") ]),''')
# 18
sub(r'''...gld([ t("Everything you have just read about FNWP-1 and BLDC-1 is a description,''',
    r'''...gld([ t("Everything this book says about FNWP-1 and BLDC-1 (Part IX) is a description,''')
# 5
sub(r'''P([ t("Start with what a stranger on the internet can even find. On a FrogNet node, every service the node runs \u2014 Apache, the proxy, the daemon, discovery, both database planes \u2014 listens on the "), mono("10"), t("-net and only there. None binds a public address; none can be reached from off the mesh, because there is no route from off the mesh to them. The single thing on a node that ever faces the public side is a WireGuard bearer (Part VII), and WireGuard is "), b("silent by construction"), t(": a packet that does not carry a valid key gets no reply, no error, not even a closed-port refusal \u2014 it is dropped as though nothing were listening. A port scan of a node's public interface cannot tell the tunnel is there, because the tunnel never answers anything it does not already trust.") ]),''',
    r'''P([ t("Start with what a stranger on the internet can even find. "), b("Node services bind all interfaces."), t(" What keeps the internet off them is where the node sits, not what they bind to. A node is behind its uplink's NAT, its "), mono("10"), t(".x addresses are not routable from outside, and it opens no tunnel port: it dials the broker. Put a node's uplink on a public address and those ports can be reached unless you filter them.") ]),''')
sub(r''' This is not a firewall tightened until only the right ports are open; it is the absence of any listening service on the public side to begin with. The exposed''',
    r''' This is not a firewall tightened until only the right ports are open. The exposed''')
# 7 + 12 (first)
sub(r'''P([ t("One piece of the pond does face the internet, and the book will not pretend otherwise: the "), b("broker"), t(". It has two touchpoints out there''',
    r'''P([ t("Two things on the droplet face the internet. One is the FrogNet "), b("broker"), t(" (its service port and its WireGuard ports). The other is the FD&F region: its RAM manager on TCP "), mono("51888"), t(", reached directly over FNW1 and unencrypted, and its hub's WireGuard on UDP "), mono("50889"), t(". The broker has two touchpoints out there''')
sub(r''' And in the recommended split topology the broker sits "), i("outside"), t(" the mesh, so even this surface is not a path inward: taking it buys an attacker the rendezvous, not the network.") ]),''',
    r''' The broker routes the pond's whole "), mono("10/8"), t(", and every node accepts "), mono("10/8"), t(" from it, so root on the broker can reach node services. What it lacks is the semantic state those services hold, not a route to them.") ]),''')
# 8
sub(r'''It is the broker service answering for itself, and the only things that talk to it are "), mono("setup_lillypad"), t(" and "), mono("frognet-tunnel-setup-v3.sh"), t(", run on the node, from a shell. "), b("You do not visit a web page to register a machine."), t(" There is no enrollment form, no sign-up flow, no account. A node is enrolled by a script that redeems a passcode against that port, and the operator who minted the passcode did that from the broker's own command line.") ]),''',
    r'''It is the broker service answering for itself. A node enrolls by running a setup script ("), mono("frognet-make-gateway.sh"), t(", "), mono("setup_lillypad_v4.bash"), t(", "), mono("frognet-tunnel-setup-v3.sh"), t(") that registers it in a pond with the pond's password. Its tunnel daemon then polls "), mono("my-channels"), t(". "), b("There is no web form."), t(" The traffic is plain HTTP to the broker's port unless you give the broker TLS. A pond created without a password admits any node that knows its name, so the pond password and TLS on the broker port are the operator's to set.") ]),''')
sub(r'''"    operator, on the broker      mint a passcode",''',
    r'''"    operator, on the broker      create the pond (optional password)",''')
sub(r'''"    node -> broker service       redeem it",''',
    r'''"    node -> broker service       register (pond password)",''')
sub(r''' \u2014 and the one step that crosses the internet carries a secret that was minted for it. There is no step a stranger can start.") ]),''',
    r''' \u2014 and the one step that crosses the internet carries the pond's password, if the operator set one.") ]),''')
# 9
sub(r'''P([ t("Return to the broker service on "), mono("18257"), t(", because what a scanner learns from "), i("that"), t(" port is less than it looks like \u2014 and the same turns out to be true of the console, on its own port, for the same reason. HTTP is the "), i("application"), t(" interface on both ends of the exchange, not the representation on the wire: FNWP-1 carries it, a first contact costs a FULL because there is no template yet to diff against, and everything after settles to DIFF and SAME. A capture yields length-prefixed codec frames, and making sense of them takes a FrogNet endpoint rather than a protocol analyser. This is a statement about "), b("what the wire carries"), t(", not a confidentiality claim \u2014 the transport is not the thing protecting you, and the section on encryption says what is. But the honest description of the surface is: one port, answering, on a number somebody picked.") ]),''',
    r'''P([ t("Return to the broker service on "), mono("18257"), t(". "), b("The broker service is plain HTTP, FastAPI under uvicorn, on its own port."), t(" A capture of it is ordinary HTTP. FrogNet's port-80 interception applies only to traffic to "), mono("10/8"), t(", so traffic to the broker never enters the semantic wire. What protects the broker is that it is one small API behind a pond password and an admin token, not that its wire is hard to read.") ]),''')
sub(r'''P([ t("The reason that is true of the console too, and not just of enrollment, is that "), b("the broker's web server is itself a FrogNet semantic server"), t(". It understands templates and semantic compression like any other node, so once a template is established the wire carries what distinguishes "), i("this"), t(" transaction from state both ends already hold \u2014 not a fresh complete message each time. "), b("HTTP can be the application interface without full stateless HTTP being the wire representation."), t(" That is the same inversion the book makes everywhere else: above FrogNet, REST can look like memory; below REST, HTTP can look like semantic operations. In neither direction does REST disappear \u2014 it stays underneath doing its job, and what changes is the surface a developer or an operator has to think at. It is the \u201cREST is assembly\u201d point applied to management traffic.") ]),
''', '')
# 6
sub(r'''...codeBlock(["$ nmap -Pn   <node's public IP>", "   all scanned ports: filtered      nothing answers", "   the wg bearer drops unauthenticated packets in silence"]),''',
    r'''P([ t("A node normally has no public address of its own. Behind its router there is nothing for a scan to reach.") ]),''')
# 11
sub(r'''nmap fingerprinted it against eight different probes and could not name it.''',
    r'''nmap fingerprinted it against nine different probes and could not name it.''')
sub(r'''P([ t("Here is everything those eight probes returned.") ]),''',
    r'''P([ t("Here is everything those nine probes returned.") ]),''')
# 10 + 19
sub(r'''P([ t("The first run of this scan returned "), mono("server: uvicorn"), t(" on every response. That is a free fingerprint on the one service that cannot hide, and it was there because a systemd drop-in redefining the listen address had silently discarded the "), mono("--no-server-header"), t(" flag the main unit carried. Two files, one truth, and the wrong one was the one anybody read \u2014 the same failure shape as the docstring in Part VIII that described a flush nothing ever called. The scan above is the run after the fix, and the header is gone.") ]),''',
    r'''P([ t("The first run returned "), mono("server: uvicorn"), t(" on every response, because the listen drop-in that sets the real bind never passed "), mono("--no-server-header"), t(". That is a free fingerprint on the one service that cannot hide. The droplet was fixed by hand, and the shipped installer still omits the flag \u2014 the same failure shape as the docstring in Part VII\u00bd that described a flush nothing ever called. The scan above is the run after the hand fix.") ]),''')
# 12 (second)
sub(r''' \u2014 and the broker sits outside the mesh, holding no node identity and no route inward \u2014 so taking it gives you the transport path, not membership.") ]),''',
    r'''. The broker routes the pond's whole "), mono("10/8"), t(", and every node accepts "), mono("10/8"), t(" from it, so root on the broker can reach node services. What it lacks is the semantic state those services hold, not a route to them.") ]),''')
# 13
sub(r'''P([ t("Two things still hold, and one common claim does not. The broker terminates a separate tunnel to each site and forwards between them in the kernel, so inter-site traffic is in the clear at that seam -- it is inside the encryption boundary. It "), b("holds no node identity and no pond secret"), t(". What that does not mean is that the traffic is unreadable there: it terminates the tunnels and forwards between them, so inter-site traffic is in the clear at that seam. It runs "), b("none of the semantic stack"), t(" \u2014 no templates, no learned structure, no cache \u2014 so even decrypted, a frame is a difference against state it has never held, which makes it unreconstructable rather than merely unread. And it is "), b("not a member of any FrogNet"), t(": no node identity, no place in a pond, no route inward. Owning the transit point gives you the transit point.''',
    r'''P([ t("Start with what the broker does see. It terminates a separate tunnel to each site and forwards between them in the kernel, so inter-site traffic is in the clear at that seam: it is outside the WireGuard boundary. The broker holds pond password hashes and node GUIDs. Its tunnel private keys live only in its kernel interfaces and are never written to disk. Three things hold. It runs "), b("none of the semantic stack"), t(" \u2014 no templates, no learned structure, no cache \u2014 but a capture taken there from the start of a conversation learns the templates, exactly as section 38 says. It is "), b("not a member of any FrogNet"), t(". Owning the transit point gives you the transit point.''')
sub(r'''P([ t("There is a fourth property and it is the sharpest of the four, because''',
    r'''P([ t("The third is the sharpest of the three, because''')
# 21
sub(r'''P([ t("Which is also why the answer to a compromised broker is not incident response. It is a new droplet. Stand another one up, point the ponds at it, and the old one is a machine somebody owns that is no longer part of anything \u2014 the whole recovery being a config line, because there was never any state on it worth recovering.") ]),''',
    r'''P([ t("The answer to a compromised broker is a new droplet. Recreate the ponds and their passwords on it and point the nodes at it, and they re-register. The broker's database is a convenience, not a store of anything the network needs. If the same host carries an FD&F RAM manager, its region file goes with it.") ]),''')
# 12 (Oracle 1)
sub(r'''"    given    root on the broker; no node identity, no pond",''',
    r'''"    given    root on the broker, which routes the pond's 10/8",''')
# 16
sub(r''' Being near is necessary; it is not sufficient. A stranger who reaches the wire still meets the identity and membership gates \u2014 the GUID in "), mono("/etc/fnid"), t(", the redeemed group token \u2014 before it is anything more than noise on the segment. Proximity narrows the attacker's world to those who can physically reach your wire; the credentials narrow it again to those you admitted.") ]),''',
    r''' On a wired segment, being there is enough: the node hands you an address. On its Wi-Fi, the gate is the WPA2 passphrase. The GUID in "), mono("/etc/fnid"), t(" and the group token gate the broker, not the segment. That is deliberate. FrogNet does not force access security, and it does not prevent you from adding it.") ]),''')
sub(r'''physically near the wire and holding a token;''',
    r'''physically near the wire or holding the Wi-Fi passphrase;''')
# 17
sub(r''' It also matches no familiar signature. A capture does not look like web traffic, or messaging traffic, or any catalogued protocol; there is no envelope announcing what it carries, because the envelope was agreed once and left behind on both boxes.") ]),''',
    r''' Each frame carries a 4-byte "), mono("FNW1"), t(" marker and an opcode, so it is recognisable as FrogNet. What it does not carry is the structure: after the first exchange, which on a new path is a RAW frame holding the original HTTP, the frames are references and changed values.") ]),''')
# 15
sub(r'''it crosses a WireGuard bearer, and then the listener never gets the capture at all.''',
    r'''it crosses a WireGuard bearer, and then a listener on the wire gets nothing; the broker still does.''')
sub(r'''Put a WireGuard bearer under a link and that link is encrypted end to end;''',
    r'''Put a WireGuard bearer under a link and that hop is encrypted;''')
sub(r''' Between sites it is already there, because the cross-internet tunnels are WireGuard by default. On a local wire''',
    r''' Between sites, each site's hop to the broker is WireGuard. The broker decrypts, forwards in the kernel and re-encrypts, so the traffic is encrypted on the wire but not end to end. If you do not trust the broker host, layer your own cipher on the payload. On a local wire''')
# 14 (section 39)
sub(r'''b("FrogNet implements no cryptography of its own."), t(" The encryption in the system is WireGuard, and WireGuard is a "),''',
    r'''b("FrogNet encrypts nothing of its own."), t(" It calls OpenSSL for SHA-256 and HMAC-SHA256 to name cache entries, which is integrity, not confidentiality. Confidentiality comes from WireGuard, which is the tunnel and is basic, or from TLS where you configure it. WireGuard is a "),''')
# 20 (the one door, defined once, used by every FOR THE GUILD panel)
sub(r'''[" \u2014 free software under the GNU GPL v2, no licence to request and no form. What you build on it and distribute is GPL too; that is the deal.''',
    r'''[" \u2014 free software under the GNU GPL, version 2 only (GPL-2.0-only), no licence to request and no form. What you build on it and distribute is GPL-2.0-only too; that is the deal.''')

# ---- Part XII 1/2 ----
# 25
sub(r'''FrogNet bundles are one implementation of that model, not its definition.''',
    r'''The Ribbit C++ platform (a vendor's "), mono("RamHost"), t(" and "), mono("RamClient"), t(") and the examples built on it are one implementation of that model, not its definition.''')
# 22 (39a)
sub(r'''P([ t("The rate at which every participant in a call sends is one pure function \u2014 "), mono("derive_rate(state, ladder) -> geometry"), t(". No clock. No self. No I/O. Every participant runs the same function over the same rows and lands on the same answer without being told, which is what makes agreement unnecessary rather than merely cheap.") ]),''',
    r'''P([ t("In the August Communicator, the rate at which every participant in a call sent was one pure function \u2014 "), mono("derive_rate(state, ladder) -> geometry"), t(". No clock. No self. No I/O. Every participant ran the same function over the same rows and landed on the same answer without being told, which is what made agreement unnecessary rather than merely cheap. Today each viewer's rate is set by the media server from what that viewer's link drains and its download slider, and the server transcodes the one upload down to it.") ]),''')
# 24
sub(r'''The store returns 503 constantly and a half-succeeded read returns a "), i("subset"),''',
    r'''The store then returned 503 constantly, and a half-succeeded read returned a "), i("subset"),''')
# 23
sub(r''', and two hours went into reading the "), i("absence"),''',
    r''', and two turns went into reading the "), i("absence"),''')
sub(r'''"a GUI detaches from its console; two hours reading absence as non-arrival"''',
    r'''"a GUI detaches from its console; two turns reading absence as non-arrival"''')
# 22 (table intro)
sub(r'''P([ t("Every rule below is a doctrine tag that appears verbatim in the source and is checked mechanically, so this table and the code can be diffed against each other. They are grouped''',
    r'''P([ t("Every rule below was a doctrine tag in the August 2026 Python Communicator. That code has been replaced by the C++ Communicator ("), mono("comms-app"), t(", "), mono("comms-ram"), t(", "), mono("comms-media"), t("), where a few of the tags survive in the source ("), mono("FOUR_TUPLES"), t(", "), mono("ABSENCE_IS_NOT_A_MEASUREMENT"), t(", "), mono("READ_A_COUNTER_NOBODY_ELSE_DRAINS"), t(") and the rest are recorded in "), mono("docs/FNAV-SPEC.md"), t(". The rules are kept here as lessons, not as current source. They are grouped''')
# 22 (39b)
sub(r'''It reproduced the hunt before the fix, ninety cycles in five seconds, and it now asserts that''',
    r'''It reproduced the hunt before the fix, ninety cycles in five seconds, and it asserted that''')
sub(r'''Walk the syntax tree instead: one oracle now checks that every''',
    r'''Walk the syntax tree instead: one oracle checked that every''')
done()
