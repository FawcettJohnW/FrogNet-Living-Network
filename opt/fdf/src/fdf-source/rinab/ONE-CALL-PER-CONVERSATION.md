# Mode B on the Redis server model: every conversation is ONE call

The six rlite-ctl commands that took several requests are already one call each in the tree this builds from
(`rina.ctl`, `[CTL_AT_THE_MEMORY_V1]`): param-list 24 → 1, reset 20 → 1, ipcp-destroy 9 → 1, ipcp-register 5 → 1,
policy-mod 4 → 1, ipcp-create 2–3 → 1. They stay one call (`op=rina`, `ctl`).

The conversations that are still several calls, read from librina.cpp, rina_node.cpp and rlite_ctl.cpp:

| conversation | now | in the region |
|---|---|---|
| allocate a flow | `rina.alloc`, then a held read on `facc:<fid>` for the verdict, re-asked every 5 s | one `op=rina_block` `alloc`: returns when the acceptor answers (or refuses) |
| accept a flow | `rina.accept`, then a write of the verdict `facc:<fid>`; plus an inbox watcher re-asking `freq:<inbox>` every 5 s | one `op=rina_block` `accept`: takes the next request and answers it in the same call (`respond` is the same op when the program answers later) |
| unregister | read the registration, then write it empty | one `op=rina` `unregister` |
| send SDUs | one write per SDU into `pipe:<sender>:<receiver>` | one `op=rina` `send` carrying every SDU queued on the fd at that moment (batched); the region appends them to the flow's queue |
| receive SDUs | held read on each peer's pipe, re-asked every 5 s, one receiving thread per peer | one `op=rina_block` `recv` per program: returns every SDU waiting on any of its flows |
| machine agent | `rina.alive`, then `rina.ctl node-rejoin` | one `op=rina` `node`: state the devices and rejoin in the same call |
| liveness | each program and agent re-writes its record every 1 s (E1) | **none**: a participant is alive while its session is up; the server tells the region when a session ends (`[SESSION_IS_LIVENESS_V1]`) |

**No timed re-asks anywhere.** A call that waits returns when its answer exists, or when the session ends.
