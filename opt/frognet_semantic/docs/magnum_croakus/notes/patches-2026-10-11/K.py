################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
import patchlib

# ---------------- Appendix G ----------------
# 1 content type
sub(r'''P([ t("Then it picks a handler. The content type is treated as a "), i("hint"), t(", not an instruction: the body is sniffed and the sniff wins. A body declared as HTML that does not look like HTML is not handled as HTML. Declared types are what a client asserts; sniffed types are what is actually there, and the second is the one worth trusting.") ]),''',
    r'''P([ t("Then it picks a handler. A declared JSON or XML type is taken as given. A declared HTML type is used only when the body starts like HTML. With no usable declared type, the body is sniffed: JSON, XML, HTML, text, else raw.") ]),''')
# 2 types row
sub(r'''"   types:  host,       enum,       float",''',
    r'''"   types:  host,       string,     float",''')

# ---------------- Appendix H ----------------
# 5 greeting, 4 pin failure
sub(r'''Open a connection, say who you are and where you want the answer sent back, send a ping stamped with the current clock, and wait.''',
    r'''Open a connection, send a greeting that names the address the answer should go back to, send a ping stamped with the current clock, and wait.''')
sub(r'''When the pin cannot be applied \u2014 an unprivileged process, a simulation \u2014 the code says so loudly rather than measuring the wrong thing quietly.") ]),''',
    r'''When the pin cannot be applied (an unprivileged process, a simulation), the code logs that the probe rides the installed route, not the candidate, and measures that route. It does it loudly, never quietly.") ]),''')
# 3 four answers (title changes; alias added below)
sub(r'''      H1("Three answers, not two"),
      P([ t("The probe returns one of three things, and the third is the one worth the appendix.") ]),
      ...codeBlock([
        "  a number   PONG came back      -> alive, and this is the rtt",
        "  nothing    no answer           -> not alive on THIS path",
        "  \"LOOP\"     the far daemon says -> this route comes back to me",
      ]),''',
    r'''      H1("Four answers, not two"),
      P([ t("The probe returns one of four things. LOOP is the one worth the appendix.") ]),
      ...codeBlock([
        "  a number    PONG came back       -> alive, and this is the rtt",
        "  nothing     no answer            -> asked again, up to three times this pass",
        "  \"REFUSED\"   refused, or no route -> something answered no: definitive absence",
        "  \"LOOP\"      the far daemon says  -> this route comes back to me",
      ]),''')
sub('''  // [AUDIT_ALIASES_V1] ids retitled by the normative audit\n''',
    '''  // [AUDIT_ALIASES_V1] ids retitled by the normative audit\n  "three-answers-not-two": "four-answers-not-two",\n''')
# 6 return address (states the code; fallback flagged in K-OPEN.md)
sub(r'''P([ t("It has to be the address that actually leaves by the interface under test \u2014 not the node's identity. A probe pinned''',
    r'''P([ t("It is the 10.x address on the interface under test. If that interface has none, the code uses the node's identity from /etc/hosts. A probe pinned''')
# 8 refused
sub(r'''A refused connection is different \u2014 something answered, and it said no \u2014 and that is a fact you may act on.''',
    r'''A refused connection, or no route to the host, is different. That is a fact you may act on.''')
# 7 stateless
sub(r'''P([ t("The probe is stateless. It remembers nothing between merges and carries no history forward. Every pass asks the question again and believes only this pass's answer. Nothing accumulates, so nothing can go stale, and a node that was wrong about the world a minute ago is not wrong about it now.") ]),''',
    r'''P([ t("The probe itself keeps nothing. One thing is remembered, and only until the next merge: a host that refused is marked not-FrogNet and not re-probed in later passes of the same merge. The mark is flushed at the top of every merge. A timeout is never marked.") ]),''')

# ---------------- Appendix I ----------------
# 9
sub(r'''Its encryption keys are replaced whenever a tunnel is rebuilt.''',
    r'''Its WireGuard key is kept across tunnel rebuilds, but it is replaced whenever the key files are removed, as a full reset does.''')
# 10
sub(r'''so that resetting a node's configuration, rebuilding its tunnels, renaming it or re-addressing it all leave the identity untouched. It is written read-only.''',
    r'''so that reinstalling over /etc/frognet, rebuilding its tunnels, renaming it or re-addressing it all leave the identity untouched. A full reset ("), mono("frognet_reset.sh"), t(") removes it unless given "), mono("--keep-identity"), t(". It is written read-only.''')
# 11
sub(r'''P([ t("There is exactly one path that creates it, and it runs at install. Everything else that needs the identity "), b("reads"), t(" \u2014 and if the file is missing, a reader refuses rather than inventing a replacement. That refusal is the whole design.''',
    r'''P([ t("One command creates it: "), mono("frognet-node-guid.sh --ensure"), t(". It runs at install, and "), mono("frognet-make-gateway.sh"), t(" runs it if the file is missing. A deliberate rotation is "), mono("frognet-node-guid.sh regenerate"), t(". Everything else "), b("reads"), t(", and a reader that finds the file missing refuses. That refusal is the whole design.''')
# 12
sub(r'''A new machine arriving under a name that is already taken is refused, and it should be. The refusal is the system telling you something true:''',
    r'''A new machine arriving under a name that is already taken is refused: the broker's unique (pond, name) index rejects the row, and today the node sees a server error rather than a message naming the conflict. The refusal is still the system telling you something true:''')
# 13
sub(r'''the record blocking its name is keyed by an identifier that no longer exists anywhere \u2014 and the one clean way to clear it is out of reach. The identity is not a secret and there is no harm in keeping a copy; the harm is in being unable to name the thing you need to retire.") ]),''',
    r'''the record blocking its name is keyed by an identifier that no longer exists anywhere, so the GUID retire ("), mono("POST /api/v4/retire-guid"), t(") cannot reach it. The operator clears it by key on the broker: "), mono("frognet-pond forget <pubkey>"), t(". The identity is not a secret. Keep a copy of "), mono("/etc/fnid"), t(".") ]),''')

# ---------------- Appendix J ----------------
# 14 duplicate opening paragraph
sub(r'''      P([ t("A build you can actually perform, end to end: two ordinary machines and a broker, from nothing installed to a live call that survives a starved link. Each step names what you should see, and what to check if you do not.") ]),
      // [RESTORE_APPJ_NEED_V1]''',
    r'''      // [RESTORE_APPJ_NEED_V1]''')
# 15
sub(r'''P([ b("Virtual machines are expected to work. Containers cannot be supported."), t(" This has not been confirmed. But FrogNet''',
    r'''P([ b("Virtual machines are expected to work; this has not been confirmed. Containers cannot be supported."), t(" FrogNet''')
# 16
sub(r'''The media host \u2014 the machine that mixes and fans the call \u2014 needs ffmpeg with libvpx, which its installer handles.") ]),''',
    r'''The machine that runs the media server ("), mono("comms-media"), t(") passes each sender's picture through, or transcodes it down per viewer. Each participant mixes its own audio. The Communicator's "), mono("./build.sh"), t(" installs the libraries it needs (FFmpeg, libvpx, Opus, PortAudio, Qt 6).") ]),''')
# 17, 18 Step 1
sub(r'''        "# no camera on this box and no wireless you want it to project?",
        "#   add  --no-ssid-projection",
      ]),
      P([ t("What you should see. The installer unpacks''',
    r'''        "# no wireless you want it to project?",
        "#   add  --no-ssid-projection",
      ]),
      P([ t("It asks for the database password (the same on every node in the pond) and whether this node is a gateway. Answer no for now; Step 4 adds the broker.") ]),
      P([ t("What you should see. The installer unpacks''')
sub(r'''A box that installs and then shows nothing in the monitor almost always has an address that does not end in .1.") ]),''',
    r'''A box that installs and then shows nothing in the monitor: check that the services came up (the installer's last phase names any that did not).") ]),''')
# 19 monitor name, Steps 1 and 2 (Step 4 is rewritten below)
sub(r'''        "FrogNet_monitor",
        "",
        "# one node, itself, healthy. that is a network.",''',
    r'''        "frognet_monitor",
        "",
        "# one node, itself, healthy. that is a network.",''')
sub(r'''        "FrogNet_monitor          # on HomeBase: Workshop is there, with RTT",''',
    r'''        "frognet_monitor          # on HomeBase: Workshop is there, with RTT",''')

# 21 REWRITE Steps 3-5
A = patchlib.s.find('      H1("Step 3 \\u2014 The broker"),')
B = patchlib.s.find('      H1("Step 6 \\u2014 Now starve it"),')
assert A > 0 and B > A and patchlib.s.count('H1("Step 3 \\u2014 The broker")') == 1
OLD35 = patchlib.s[A:B]
NEW35 = r'''      H1("Step 3 \u2014 The broker"),
      P([ t("On the third machine, from the extracted broker bundle:") ]),
      ...codeBlock([
        "sudo ./install_frognet_broker.sh            # --port 18257 is the default",
      ]),
      P([ t("It does not ask questions; everything is a flag. What you should see is the postflight:") ]),
      ...codeBlock([
        "service       active, <pid> pid",
        "listening     0.0.0.0:18257",
        "api           GET /api/v4/ponds -> 200",
        "data          0 pond(s) visible; database preserved at",
        "              /var/lib/frognet_broker_v4/broker.db",
        "installed: frognet_broker_<stamp>",
      ]),
      P([ t("Then make the pond the nodes will join:") ]),
      ...codeBlock([
        "frognet-pond create hometown [--password PW]   # on the broker",
        "frognet-pond list                              # hometown, with its entire_pond chorus",
      ]),
      P([ t("The broker URL you carry to the nodes is "), mono("http://<broker host>:18257"), t(".") ]),
      H1("Step 4 \u2014 A node across the internet"),
      P([ t("On each node (it needs a real non-10 uplink: a WAN lease, or Wi-Fi client upstream):") ]),
      ...codeBlock([
        "sudo frognet-make-gateway.sh --host your.broker.example \\",
        "     --port 18257 --pond hometown [--password PW]",
      ]),
      P([ t("It writes "), mono("BROKER_URL"), t(" and "), mono("POND_NAME"), t(" into "), mono("/etc/frognet/tunnel.conf"), t(", checks "), mono("/etc/fnid"), t(", and registers ("), mono("POST /api/v4/register"), t("). A node whose uplink is not up yet stays LAN-only; enrolment is retried from "), mono("runMerge"), t(" on the next network event.") ]),
      ...codeBlock([
        "frognet-pond nodes hometown   # on the broker: both, registered",
        "frognet_monitor               # on either node: the other one, with RTT",
        "ping 10.80.90.1               # from HomeBase, across the internet",
      ]),
      P([ t("If it does not work: check "), mono("frognet-pond nodes"), t(" first. A node missing there did not register; its tunnel-setup log names why.") ]),
      H1("Step 5 \u2014 The call"),
      P([ t("Build the Communicator on each machine from "), mono("ribbit/examples/communicator"), t(":") ]),
      ...codeBlock([
        "./build.sh            # installs the FFmpeg, libvpx, Opus, PortAudio and Qt 6 it needs",
      ]),
      P([ t("On the machine that will hold the call's memory and media server (open TCP 8800 and 8994 to the others):") ]),
      ...codeBlock([
        "build/comms-ram   --listen 0.0.0.0:8800",
        "build/comms-media --listen 0.0.0.0:8994 --ram 127.0.0.1:8800",
      ]),
      P([ t("Find your devices:") ]),
      ...codeBlock([
        "build/comms-app --list-devices",
        "",
        "#  cameras, each as  --camera \"<name>\"",
        "#  audio as  --mic N  /  --speaker N",
      ]),
      P([ t("On each machine a person uses (RAM and MEDIA are that machine's 10.x address):") ]),
      ...codeBlock([
        "build/comms-app --ram RAM:8800 --media MEDIA:8994 \\",
        "                --name Gorp --camera /dev/video0",
      ]),
      P([ t("With no camera, "), mono("comms-app"), t(" sends a synthetic picture ("), mono("--synthetic"), t("); "), mono("--no-audio"), t(" runs without sound.") ]),
      P([ t("On an FD&F pond node, "), mono("/opt/fdf/bin/fdf-comms.sh"), t(" runs the same two servers and writes the pond's where cell, so the phone's Communicator finds them with nothing typed.") ]),
'''
sub(OLD35, NEW35)

# 20 Step 6 levers and counter; 21 ladder caption
sub(r'''The Communicator carries the levers itself \u2014 a bandwidth ceiling and a jam control on the call bar \u2014 so you do not need''',
    r'''The Communicator carries the levers itself \u2014 a Bandwidth slider and a Jitter slider on the call \u2014 so you do not need''')
sub(r'''exLabel("The observed ladder, from the recorded run"),''',
    r'''exLabel("The observed ladder, recorded on the earlier Python Communicator"),''')
sub(r'''P([ t("If you want the compression figure too, the call bar carries a counter for it: on the recorded run roughly 89% of the bytes never had to be sent, which is a did-versus-would estimate against published figures for the same session rather than a packet capture, and is labelled that way everywhere it appears.") ]),''',
    r'''P([ t("On the 900 MHz field call (Part X), roughly 89% of the bytes never had to be sent. That is a did-versus-would estimate, not a packet capture.") ]),''')
# 22 empty heading gets its body
sub(r'''      H1("What you have just proved"),
''',
    r'''      H1("What you have just proved"),
      P([ t("Two machines on one wire became a network with nothing configured. A broker extended it across the internet, and no address changed. A call ran over it, and when the link was starved the picture gave way and the voice did not.") ]),
''')

# ---------------- Appendix K ----------------
# 25
sub(r'''and a replacement that removes the limit is in development.''',
    r'''and a replacement that removes the limit is designed and not yet built.''')
sub(r'''P([ t("The work in development moves allocation from "),''',
    r'''P([ t("The replacement design moves allocation from "),''')
sub(r'''lifts the ceiling to the addressing plan's own -- roughly fifty-nine thousand five hundred -- and it is being worked now.") ]),''',
    r'''lifts the ceiling to the addressing plan's own -- roughly fifty-nine thousand five hundred. It is designed and not yet built.") ]),''')
# 24
sub(r'''["Open files per process", "the operator", "unlimited. Set it in the unit files and it stops being a number anyone thinks about"],''',
    r'''["Open files per process", "the operator", "the shipped units do not set it yet; set LimitNOFILE=infinity in them"],''')
# 23
sub(r'''["Discovery depth", "the software", "children and grandchildren. Deeper tiers are not walked"],''',
    r'''["Discovery depth", "the software", "two levels per pass, from every node already routed; depth comes from re-merging, so a chain of any length is walked to its end"],''')

# ---------------- Afterword ----------------
# 26 (25 gld panels in the builder, this one included)
sub(r'''seventeen of them carry a panel like this one''',
    r'''twenty-four of them carry a panel like this one''')

# ---------------- Appendix L ----------------
# 27
sub(r'''P([ t("What that buys is unusual and worth stating plainly. "), mono("checksite.py"), t(" carries a MARKERS list and fails the build if a load-bearing rule is removed from the code. So the book and the implementation can be diffed by a script:''',
    r'''P([ t("What that buys is unusual and worth stating plainly. The book and the implementation can be diffed by a script:''')

# ---------------- Appendix M ----------------
# 29
sub(r'''gloss("Monitor", "FrogNet_monitor \u2014 the terminal program''',
    r'''gloss("Monitor", "frognet_monitor \u2014 the terminal program''')
# 30
sub(r'''Reachable by IP from every node; holds no authoritative state (its picture is rebuilt from node polls). It DOES carry the inter-site data path \u2014 cross-site traffic transits it \u2014 and inter-site traffic is in the clear at that seam. It holds no node identity and no pond secret, runs none of the semantic stack, and is not itself a member of any FrogNet. The broker host may also run the optional operator console \u2014 a supported web management interface on its own port, itself a FrogNet semantic server, with scripted equivalents in /usr/local/bin. That console is a separate listener from the broker service and is not required for enrollment, rendezvous, or tunnels."),''',
    r'''Reachable by IP from every node. It DOES carry the inter-site data path \u2014 cross-site traffic transits it \u2014 and inter-site traffic is in the clear at that seam. It holds the pond's registry: each node's install GUID (its matching key), public key and subnet, the tunnels and their address and port allocations, and any pond password hash. It holds no private keys, runs none of the semantic stack, and is not a member of any FrogNet. The optional console is a PHP page in the broker host's web root, with frognet-pond as its command-line equivalent. It is separate from the broker service and is not required for enrollment, rendezvous, or tunnels."),''')
# 31
sub(r'''as a JSON blob; each service folds in its own checks before publishing the result as a tuple the election scores (Appendix A)."),''',
    r'''as a JSON blob; each role's advertiser publishes it as its <role>/capability tuple, which the election scores (Appendix A)."),''')
# 32
sub(r'''It hosts codec and domain-handler machinery and is reached over a loopback socket.''',
    r'''It hosts codec and domain-handler machinery and is reached by peer proxies, and by the liveness probe, on TCP 9009.''')
# 33
sub(r'''gloss("databasehost_control", "A second, deterministic database plane \u2014 the highest-.1 node \u2014 used only for the mesh\u2019s internal discovery and election bookkeeping, notably as the place the databasehost election reads its candidates. Holds no application data; not something you address or configure."),''',
    r'''gloss("databasehost_control", "The deterministic control host (highest .1). It holds the coordination plane: the SD: tuples (capability, presence, beacons) and the Services table. It holds no durable sensor data; that stays on databasehost.frognet."),''')
# 34
sub(r'''with one CSV line: fully-qualified name and the node's wired and wireless addresses (Appendix B)."),''',
    r'''with one CSV line: the node name, its .1 identity, and up to two upstream addresses (0.0.0.0 when absent) (Appendix B)."),''')
# 35
sub(r'''gloss("GROUP_TOKEN", "A node's pond-membership credential, written to /etc/frognet/tunnel.conf when it redeems a passcode from the broker."),''',
    r'''gloss("GROUP_TOKEN", "A v2-era tunnel.conf field; unused by the v4 broker."),''')
# 36
sub(r'''gloss("media host", "The elected node that fans a live call\u2019s frames out to every viewer, chosen by measured real-time video-encoding capability (a working ffmpeg + libvpx encoder is required). Role name mediahost."),''',
    r'''gloss("media server", "comms-media (TCP 8994): it takes each participant's audio and video upload and serves each viewer at that viewer's own rate, passing through or transcoding down. It runs where it is started; on an FD&F pond node, fdf-comms.sh starts it and says where it is in the pond's where cell. (FrogNet's elected mediahost role, LAN-only and gated on ffmpeg+libvpx, still exists in core/sotf_handler.py; the service it starts points at a file no longer in the tree.)"),''')
# 39 pond
sub(r'''gloss("pond", "A whole FrogNet network \u2014 the set of nodes that belong together, as tracked by the broker."),''',
    r'''gloss("pond", "Under FrogNet, the nodes that register together at a broker. Under FD&F, the members of one pond in a region, held in the region's memory."),''')
# 40
sub(r'''gloss("scope", "FrogNet's nesting levels of reach: Self, Lillypad, Chorus, Pond."),''',
    r'''gloss("scope", "The book's names for reach: Self, Lillypad, Chorus, Pond. In code, a tuple's scope is host:<ip>[:pid|:role] or session:<id>, and chorus and pond are broker groupings."),''')
# 37
sub(r'''A node standing up its own Wi-Fi access point when it has no wired uplink, so clients can join it directly.''',
    r'''A node standing up its own Wi-Fi access point when its eth0 has no link, so clients can join it directly.''')
# 38 + 39 additions
sub(r'''rather than participant routing (Part XVII)."),''',
    r'''rather than participant routing (Specification C)."),
      gloss("RAM manager", "The FD&F memory every machine opens one outbound socket to; all data goes through it, never member to member."),
      gloss("hub", "The FD&F region's forwarding point on the droplet (StreamingFrog, 165.232.153.144:51888)."),
      gloss("bridge (fdf-tun)", "A phone or PC that joins as one TUN with 1:1 aliases in 100.64/10 and leaves via the hub."),
      gloss("where cell", "The pond tuple (pond, where, w:<guid>) that says where a node's services are."),
      gloss("comms-ram", "The Communicator's memory, TCP 8800."),''')

done()
