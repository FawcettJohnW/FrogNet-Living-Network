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
# A -- Part XV1/2 brought to 2026-11-15: the PyTorch work is in the GPL tree; the November architecture (references,
# the bits stay in each rank's memory); meshfeed and the five-node mesh campaign (329x / 5.5x).
# Sources: AI_READ_FIRST.md 10b; opt/frognet_semantic/agent_workload/PSYCHEDELIC-MESH-RESULTS-20261113.md (1, 3, 8, 9);
# opt/frognet_semantic/ribbit/examples/pytorch/README.md.
from patchlib import *

sub('lead([ b("A note on what this is. "), t("FrogNet is free software and always will be. The distributed-training work described here is not part of it: it is Freemium, a separate offering from Fawcett Innovations built "), i("on"), t(" FrogNet and kept out of the open tree. Nothing in this part is required to run a mesh, and nothing in the open tree depends on it.") ]),',
    'lead([ b("A note on what this is. "), t("The distributed-training work is in the same tree as everything else, under the same licence, GPL-2.0-only, since 10 November 2026 (agent_workload/ and usr/local/frognet-ai/). Use it for anything. A company whose own policy rules out GPL code can buy a commercial licence from Fawcett Innovations. Nothing in this part is required to run a mesh.") ]),')

sub('P([ t("The training product is where that question gets answered commercially. FrogNet itself remains what it has been throughout this book: a mesh, free, and complete without any of this.") ]),',
    U('''P([ t("That question got its first answer in November, and the answer was not a better reduction.") ]),

      H1("58a.  The Bits Stay Where They Are"),
      P([ t("The September backend moved tensor bytes through a store. In November that changed, and the change is the whole model in one sentence: "), b("you do not copy bits from one machine to another; you leave them where they were and use references."), t(" One RAM serves all PyTorch work, the frogtorch-ram region on the machine databasehost.frognet names. It holds membership, the rendezvous store, the start gate, the hook bags, and one cell per rank saying where that rank’s memory is. That is all it holds.") ]),
      P([ t("A rank’s tensors stay in its own process. A reader holds a read on the owner’s memory for a name and a generation; when the owner stores it, the bytes go once, from the owner’s memory over FrogNet’s own sessions straight into the reader’s tensor. Nothing is staged on any host. The shard reduction’s outgoing slices are the gradient bucket itself, not a copy of it. A rank stays until its peers are done, on one held read. There are no acks, no retries and no placement fallback, and nothing waits in the caller: all_reduce returns at once and runs on the backend’s own thread in call order.") ]),
      P([ t("On the five-node mesh of 12 and 13 November — Seattle, New York, Wi-Fi, WireGuard and home Internet links — this backend and gloo tied once one access point’s queue was changed to fq_codel: within 2% at batch 64 and 3% at batch 4096. Both sit near the floor the links set for any gradient exchange. A gradient here is 16.9 MB, and moving it through the slowest path costs about 18 seconds a step whatever the algorithm. Inside DDP’s arrangement there was nowhere left to go.") ]),

      H1("58b.  The Data Comes to the Compute"),
      P([ t("So the arrangement changed instead. DDP sends every machine its own data and then makes them agree on gradients. "), b("meshfeed"), t(" does the opposite. Each node keeps its minibatches on its own disk and publishes them through the tuple space. Every node measures its own capability, and the machine that measures fastest pulls the minibatches and does the arithmetic with ordinary, unmodified PyTorch. A minibatch at batch 64 is 4,352 bytes. The gradient it replaces is 16.9 MB.") ]),
      ...codeBlock([
        "same five nodes, same model, same held-out loss, total time",
        "",
        "  workload               stock DDP (gloo)   meshfeed     ",
        "  batch 64,   40 steps   1,018 s            3.09 s   329x",
        "  batch 4096, 10 steps     264 s            48.2 s    5.5x",
      ]),
      P([ t("Every single-machine run matches a one-thread oracle: exactly at batch 1024 and 4096, and to 6e-08 at batch 64. Waiting for data was 0.3 to 2.2 ms a step. Splitting the batch-4096 compute over two or three machines beat DDP by 2.6 and 2.2 times but lost to the fastest machine alone, because summing gradients among them cost more than the compute it saved. The planner ranks machines by compute speed; it does not yet measure links.") ]),
      P([ t("This is what the substrate was for. The mesh behaves as one machine: the data is reachable from every node, and the work goes where the capability is rather than where the data happens to sit. The full campaign, with every run identifier and the reproduction steps, is "), mono("agent_workload/PSYCHEDELIC-MESH-RESULTS-20261113.md"), t(". FrogNet itself remains what it has been throughout this book: a mesh, free, and complete without any of this.") ]),'''))
done()
