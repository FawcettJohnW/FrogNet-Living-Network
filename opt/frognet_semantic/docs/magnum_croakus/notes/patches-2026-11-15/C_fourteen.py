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
# C -- a new Part XV3/4, "Fourteen Systems": every system built or converted on Ribbit, with Ribbit-LISP's 2026-11-14
# Internet run, OpenEOC and FrogTastic-RAM in their own chapters.
# Sources: opt/frognet_semantic/ribbit/examples/lispers.net/evidence/2026-11-14/RESULTS.md and RIBBIT-LISP-REPORT.md;
# ribbit/examples/frogtastic-ram/README.md and RESULTS.md; ribbit/examples/openeoc/PLAN.md and RESULTS.md;
# opt/fdf/docs/APP-SETUP-AND-OPERATION.md; the site's conversions.html record (Redis, Mosquitto, Sourri, rlite, chat).
from patchlib import *

sub('...partTitle("Part XVI", "What Is Not Built Yet"),',
    U('''...partTitle("Part XV¾", "Fourteen Systems"),
      lead([ t("Everything in this book was argued and then built. This part is the record of what the model has been used for: fourteen systems, some converted from an incumbent, some built new. The method is the same for every conversion. The incumbent’s API is the contract and its own tests are the judge; everything between those two is rebuilt on shared memory, usually by deleting most of it.") ]),

      H1("58c.  The Record"),
      ...codeBlock([
        "system                built / converted      judged by                        time / status",
        "chat                  built                  its own tests                    2 hours",
        "DHCPv6 failover       built                  RFC 8156, reference impl.        2 days",
        "Sourri chatbot API    converted              a 40-check oracle                3 days",
        "Redis 7.2.11          converted              Redis's own Tcl suite            4 days",
        "PyTorch distributed   converted              same-campaign gloo controls      5 days",
        "rlite (RINA)          converted              rlite's 30 integration tests     30 of 30",
        "Mosquitto 2.1.2       converted              Mosquitto's own suite            272 of 276, as stock",
        "lispers.net 0.643     converted              78 differential tests            6 days",
        "Meshtastic 2.8.2      converted              its suites and stock nodes       FrogTastic-RAM",
        "OpenEOC               built, measured        OpenEOC's own suite              in use",
        "SuperTuxKart          converted              a race on real hardware          playable",
        "the Communicator      built                  calls on the live mesh           in use",
        "FD&F                  built                  rlite's tests, LISP's oracle     CroakOS",
        "Comcast Redirector    examined               --                               set aside",
      ]),
      P([ t("Comcast’s Redirector was examined and set aside: too simple to prove anything. A conversion is worth doing when the original is real, mature and somebody else’s.") ]),
      P([ t("Three results from the record, in a sentence each. Redis passes its own suite with about a tenth of the server code, and a four-primary resharding that moves keys between primaries on stock moves none on the Ribbit front. Mosquitto at QoS 1 carried 101,483 messages a second against stock’s 27,485. Sourri’s developer built and ran the converted version without help.") ]),

      H1("58d.  lispers.net: Ribbit-LISP"),
      P([ t("Dino Farinacci’s lispers.net is the reference implementation of the LISP map-server and map-resolver. Ribbit-LISP does the same job against the same wire: the registrations, sites, keys and policies live in shared memory on a RAM host, and the front that receives LISP packets keeps no private copy of them and takes no locks. In v0.75 the front decides each Map-Register where it arrives and sends the Map-Notify without waiting on the RAM host; the rows it decides go to the RAM host in batches, through a window that backs off when the RAM host slows.") ]),
      P([ t("On 14 November it was measured over the Internet: load from New York, both map-servers in Seattle, Ribbit’s memory on a two-core droplet in San Francisco.") ]),
      ...codeBlock([
        "                              lispers.net 0.643   Ribbit-LISP v0.75",
        "78 behaviour tests            14 failures         0 failures",
        "lookups/s at peak             87                  1,468      17x",
        "registrations/s at peak       186                 1,474      7.9x",
        "256 clients and up            nothing answered    1,357-1,479/s",
        "CPU per million lookups       13,088 s            100 s      ~140x less",
        "application code (NLOC)       6,463               2,067      68% less",
        "total cyclomatic complexity   2,237               1,318      41% less",
      ]),
      P([ t("Every operation paid about 100 ms of network, so at one client both systems take the same time. The difference is what happens as clients are added: lispers.net stops answering at 256, and Ribbit-LISP runs on to the ceiling the path sets. At 10,000 registrations lispers.net still had sampled entries unresolved 85 seconds after loading them; Ribbit-LISP resolved all of them within 3 seconds, and its CPU per lookup stayed flat as the table grew. The report, the whole run and the code audit are in "), mono("ribbit/examples/lispers.net"), t(".") ]),

      H1("58e.  OpenEOC"),
      P([ t("OpenEOC is an emergency operations centre: a command centre, field teams, hospitals and ambulances, the radio log with its ICS-309 numbering, positions on a map, assignments, announcements and messages. It is FrogNet’s own application, written fresh and GPL-2.0-only, measured against the open-source OpenEOC’s screens, words, forms and test suite, whose stock run is recorded first.") ]),
      P([ t("It is as many RAMs as it needs. A general RAM that everyone in the app joins carries positions, the roster, the radio log, the mission and assignments, announcements and messages between teams. Each team has its own RAM for its own data and messages. Every one of those is a role: the host that serves it is chosen by OpenEOC’s selection over the capability tuples, the machine that best meets the criteria among the ones that can see each other. A team lead’s phone hosts the team when nobody else does. When the network splits, each side carries on with what it can see; when it merges, the owners reassert and the selection runs again. Nobody declares a primary, and nothing is configured per site. The pages are served by the nodes, and the Windows and Android devices open the same pages.") ]),

      H1("58f.  FrogTastic-RAM"),
      P([ t("A Meshtastic node, with its plumbing replaced by shared memory and nothing else changed. The client API stays exactly as it is — ToRadio and FromRadio over TCP, serial and Bluetooth — and so do Meshtastic’s modules. What goes is everything that moves packets between machines or repairs that movement, and every copy of another node’s data: a node writes its own facts into its own cells, and every other node reads them with held reads. Its NodeDB becomes a held view of the owners’ cells.") ]),
      P([ t("Meshtastic’s own end-to-end mesh test passes unmodified with multicast blackholed: zero UDP datagrams on the capture, the nodes talking only through the memory. The Python CLI passes 40 of 40, and the firmware’s 88 native suites stay green. The firmware links nothing of Ribbit; a separate FrogTastic-Bridge process carries its calls to the memory over a local shared segment, so Meshtastic’s GPL-3.0 and FrogNet’s GPL-2.0 never meet in one binary. A FrogNet node and a stock node talk on the same network, so nodes convert one at a time.") ]),

      ...partTitle("Part XVI", "What Is Not Built Yet"),'''))
done()
